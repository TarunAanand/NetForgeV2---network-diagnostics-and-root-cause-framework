"""Controller orchestration without HTTP presentation concerns."""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from agent.models import ProbeRequest, ProbeType
from analysis.multivantage import MultiVantageReport, VantageObservation, localize_fault
from controller.dispatch import AgentDispatcher
from controller.models import (
    AgentRegistration,
    DiagnosisRequest,
    FanoutJob,
    FanoutJobRequest,
    RegisteredAgent,
)
from controller.monitoring import (
    Alert,
    AlertRule,
    AlertState,
    Incident,
    IncidentState,
    ScheduleEntry,
    advance_schedule,
    build_alert,
    is_due,
    is_healthy,
    matching_rules,
    max_severity,
)
from controller.correlation import (
    DeviceCorrelationRequest,
    HostPortBinding,
    augment_topology_with_bindings,
    correlate_host_ports as compute_bindings,
)
from controller.store import ControllerStore
from controller.topology import (
    NetworkTopology,
    ServiceEndpoint,
    ServiceInventory,
    ServiceProtocol,
)
from controller.vantage import select_vantages
from storage.baselines import record_and_compare
from storage.history import HistoryStore

logger = logging.getLogger("netforge.controller")


class UnknownAgentError(ValueError):
    pass


class UnknownTopologyError(ValueError):
    pass


class UnknownNodeError(ValueError):
    pass


class UnknownServiceError(ValueError):
    pass


class UnknownDiagnosisError(ValueError):
    pass


class UnknownScheduleError(ValueError):
    pass


class UnknownAlertRuleError(ValueError):
    pass


class UnknownIncidentError(ValueError):
    pass


class NoVantageAgentsError(RuntimeError):
    pass


class NoBindingsError(RuntimeError):
    pass


class ControllerService:
    def __init__(
        self,
        store: ControllerStore,
        dispatcher: AgentDispatcher,
        history: HistoryStore | None = None,
    ):
        self.store = store
        self.dispatcher = dispatcher
        self._history = history

    def register_agent(self, registration: AgentRegistration) -> RegisteredAgent:
        agent = RegisteredAgent(**registration.model_dump(), last_seen_at=None)
        self.store.upsert_agent(agent)
        return agent

    def list_agents(self) -> list[RegisteredAgent]:
        return self.store.list_agents()

    def dispatch_job(self, request: FanoutJobRequest) -> FanoutJob:
        agents: list[RegisteredAgent] = []
        for agent_id in dict.fromkeys(request.agent_ids):
            agent = self.store.get_agent(agent_id)
            if not agent or not agent.enabled:
                raise UnknownAgentError(f"unknown or disabled agent: {agent_id}")
            agents.append(agent)
        job = FanoutJob(job_id=str(uuid4()), created_at=time.time(), request=request, status="running")
        self.store.save_job(job)
        observations, errors = self.dispatcher.fanout(agents, request.probe)
        for agent_id in observations:
            self.store.mark_seen(agent_id)
        job.observations = observations
        job.errors = errors
        job.completed_at = time.time()
        job.status = "completed" if not errors else ("partial" if observations else "failed")
        self.store.save_job(job)
        return job

    def get_job(self, job_id: str) -> FanoutJob | None:
        return self.store.get_job(job_id)

    # --- Topology & service inventory (M3) ---
    def import_topology(self, topology: NetworkTopology) -> NetworkTopology:
        """Persist a validated topology graph (validation happens in the model)."""
        self.store.save_topology(topology)
        return topology

    def get_topology(self, name: str) -> NetworkTopology:
        topology = self.store.get_topology(name)
        if topology is None:
            raise UnknownTopologyError(f"unknown topology: {name}")
        return topology

    def list_topology_names(self) -> list[str]:
        return self.store.list_topology_names()

    def register_services(
        self,
        inventory: ServiceInventory,
        topology_name: str | None = None,
    ) -> list[ServiceEndpoint]:
        """Persist services, rejecting any node_id absent from the topology.

        When no topology_name is supplied and exactly one topology is stored it
        is used for validation; otherwise node references are left unchecked so
        a service inventory can be registered before its graph is imported.
        """
        topology = self._resolve_topology(topology_name)
        if topology is not None:
            for service in inventory.services:
                if service.node_id is not None and not topology.has_node(service.node_id):
                    raise UnknownNodeError(
                        f"service {service.service_id} references unknown node: {service.node_id}"
                    )
        for service in inventory.services:
            self.store.upsert_service(service)
        return list(inventory.services)

    def list_services(self) -> list[ServiceEndpoint]:
        return self.store.list_services()

    def correlate_agents(self, topology_name: str) -> dict[str, Any]:
        """Map topology agent-nodes onto the live agent registry.

        Establishes which graph vantage points are actually reachable, the
        prerequisite for M4 multi-vantage diagnosis.
        """
        topology = self.get_topology(topology_name)
        registered = {agent.agent_id: agent for agent in self.store.list_agents()}
        nodes: list[dict[str, Any]] = []
        for node in topology.agent_nodes():
            agent = registered.get(node.agent_id) if node.agent_id else None
            nodes.append(
                {
                    "node_id": node.node_id,
                    "agent_id": node.agent_id,
                    "registered": agent is not None,
                    "enabled": bool(agent.enabled) if agent else False,
                    "last_seen_at": agent.last_seen_at if agent else None,
                }
            )
        return {
            "topology": topology_name,
            "agent_node_count": len(nodes),
            "registered_count": sum(1 for n in nodes if n["registered"]),
            "nodes": nodes,
        }

    def _resolve_topology(self, name: str | None) -> NetworkTopology | None:
        if name is not None:
            return self.get_topology(name)
        names = self.store.list_topology_names()
        if len(names) == 1:
            return self.store.get_topology(names[0])
        return None

    # --- Two-sided / third-vantage diagnosis (M4) ---
    def diagnose_service(self, request: DiagnosisRequest) -> MultiVantageReport:
        """Localize a fault for a registered service using multiple vantage points.

        Selects source/destination/third vantage agents from the validated
        topology, fans out the same probe to all of them, correlates the results
        into a localized verdict, and persists the resulting evidence trail.
        """
        service_ep = self.store.get_service(request.service_id)
        if service_ep is None:
            raise UnknownServiceError(f"unknown service: {request.service_id}")

        topology = self._topology_for_service(service_ep, request.topology)
        correlation = self.correlate_agents(topology.name)
        plan = select_vantages(topology, service_ep, correlation, source_node_id=request.source_node_id)
        if not plan.assignments:
            raise NoVantageAgentsError(
                f"no registered agents available in topology '{topology.name}' to diagnose {service_ep.service_id}"
            )

        probe_type = request.probe_type or (
            ProbeType.TCP if service_ep.protocol == ServiceProtocol.TCP else ProbeType.ICMP
        )
        probe = ProbeRequest(
            probe_type=probe_type,
            target=service_ep.host,
            count=request.count,
            port=service_ep.port if probe_type == ProbeType.TCP else None,
        )

        agents = [
            agent
            for agent in (self.store.get_agent(a.agent_id) for a in plan.assignments)
            if agent is not None
        ]
        observations_by_agent, errors = self.dispatcher.fanout(agents, probe)

        vantage_observations: list[VantageObservation] = []
        for assignment in plan.assignments:
            for obs in observations_by_agent.get(assignment.agent_id, []):
                vantage_observations.append(
                    VantageObservation(
                        agent_id=assignment.agent_id,
                        node_id=assignment.node_id,
                        role=assignment.role,
                        target=obs.context.target or service_ep.host,
                        probe_type=obs.context.probe_type,
                        status=obs.result.status,
                        evidence_quality=obs.context.evidence_quality,
                        confidence=obs.context.confidence,
                        summary=obs.result.summary,
                        evidence=obs.result.evidence,
                    )
                )

        report = localize_fault(
            vantage_observations,
            target=service_ep.host,
            service_id=service_ep.service_id,
            topology=topology.name,
            agent_errors=errors,
        )
        self.store.save_diagnosis(report)
        return report

    def get_diagnosis(self, diagnosis_id: str) -> MultiVantageReport:
        report = self.store.get_diagnosis(diagnosis_id)
        if report is None:
            raise UnknownDiagnosisError(f"unknown diagnosis: {diagnosis_id}")
        return report

    def list_diagnoses(self, limit: int = 50) -> list[MultiVantageReport]:
        return self.store.list_diagnoses(limit=limit)

    def _topology_for_service(self, service_ep: ServiceEndpoint, name: str | None) -> NetworkTopology:
        if name is not None:
            return self.get_topology(name)
        for candidate in self.store.list_topology_names():
            topology = self.store.get_topology(candidate)
            if topology and service_ep.node_id and topology.has_node(service_ep.node_id):
                return topology
        names = self.store.list_topology_names()
        if len(names) == 1:
            return self.store.get_topology(names[0])
        raise UnknownTopologyError(
            "no imported topology contains the service node; import a topology first"
        )

    # --- Scheduling, baselines, alerts, incidents (M5) ---
    def create_schedule(self, schedule: ScheduleEntry) -> ScheduleEntry:
        """Register a periodic diagnosis schedule for a known service."""
        if self.store.get_service(schedule.service_id) is None:
            raise UnknownServiceError(f"unknown service: {schedule.service_id}")
        now = time.time()
        if not schedule.created_at:
            schedule.created_at = now
        if schedule.next_run_at is None:
            schedule.next_run_at = now  # due on the next scheduler tick
        self.store.upsert_schedule(schedule)
        return schedule

    def get_schedule(self, schedule_id: str) -> ScheduleEntry:
        schedule = self.store.get_schedule(schedule_id)
        if schedule is None:
            raise UnknownScheduleError(f"unknown schedule: {schedule_id}")
        return schedule

    def list_schedules(self, service_id: str | None = None) -> list[ScheduleEntry]:
        return self.store.list_schedules(service_id=service_id)

    def delete_schedule(self, schedule_id: str) -> bool:
        if not self.store.delete_schedule(schedule_id):
            raise UnknownScheduleError(f"unknown schedule: {schedule_id}")
        return True

    def register_alert_rule(self, rule: AlertRule) -> AlertRule:
        if rule.service_id is not None and self.store.get_service(rule.service_id) is None:
            raise UnknownServiceError(f"unknown service: {rule.service_id}")
        self.store.upsert_alert_rule(rule)
        return rule

    def list_alert_rules(self) -> list[AlertRule]:
        return self.store.list_alert_rules()

    def delete_alert_rule(self, rule_id: str) -> bool:
        if not self.store.delete_alert_rule(rule_id):
            raise UnknownAlertRuleError(f"unknown alert rule: {rule_id}")
        return True

    def run_due_schedules(self, now: float | None = None) -> list[MultiVantageReport]:
        """Run every schedule whose next_run_at has elapsed.

        Each run performs a multi-vantage diagnosis, feeds it through the
        baseline/alert/incident pipeline, and advances the schedule. Failures on
        one schedule are isolated so the remaining schedules still run.
        """
        now = time.time() if now is None else now
        reports: list[MultiVantageReport] = []
        for schedule in self.store.list_schedules():
            if not is_due(schedule, now):
                continue
            error: str | None = None
            try:
                request = DiagnosisRequest(
                    service_id=schedule.service_id,
                    probe_type=schedule.probe_type,
                    count=schedule.count,
                    source_node_id=schedule.source_node_id,
                    topology=schedule.topology,
                )
                report = self.diagnose_service(request)
                self.process_diagnosis(report, now=now)
                reports.append(report)
            except Exception as exc:
                # A schedule with no vantage agents / unknown service must not
                # stall the monitoring loop; it is recorded, advanced, retried.
                error = f"{type(exc).__name__}: {exc}"
                logger.exception(
                    "scheduled diagnosis failed (schedule=%s service=%s)",
                    schedule.schedule_id,
                    schedule.service_id,
                )
            finally:
                self.store.upsert_schedule(advance_schedule(schedule, now, error))
        return reports

    def process_diagnosis(
        self, report: MultiVantageReport, now: float | None = None
    ) -> dict[str, Any]:
        """Feed a diagnosis through baselines -> alerts -> incidents.

        Records a reachability baseline sample (flagging regressions), fires
        alert rules on unhealthy localizations, groups alerts into an incident,
        and auto-resolves the service's alerts/incidents when it is healthy.
        """
        now = time.time() if now is None else now
        regression = self._record_service_baseline(report)
        outcome: dict[str, Any] = {"regression": regression, "alerts": [], "incidents": []}

        if is_healthy(report):
            outcome["resolved_incidents"] = self._resolve_service(report.service_id, now)
            return outcome

        for rule in matching_rules(self.store.list_alert_rules(), report):
            alert = self.store.find_firing_alert(rule.rule_id, report.service_id)
            if alert is not None:
                alert.diagnosis_id = report.diagnosis_id
                alert.updated_at = now
                alert.regression = alert.regression or regression
                self.store.save_alert(alert)
            else:
                alert = build_alert(rule, report, now, regression=regression)
                self.store.save_alert(alert)
            outcome["alerts"].append(alert)
            outcome["incidents"].append(self._attach_to_incident(alert, report, now))
        return outcome

    def list_alerts(
        self, service_id: str | None = None, state: AlertState | None = None
    ) -> list[Alert]:
        return self.store.list_alerts(service_id=service_id, state=state)

    def list_incidents(
        self, service_id: str | None = None, state: IncidentState | None = None
    ) -> list[Incident]:
        return self.store.list_incidents(service_id=service_id, state=state)

    def get_incident(self, incident_id: str) -> Incident:
        incident = self.store.get_incident(incident_id)
        if incident is None:
            raise UnknownIncidentError(f"unknown incident: {incident_id}")
        return incident

    def acknowledge_incident(self, incident_id: str, now: float | None = None) -> Incident:
        incident = self.get_incident(incident_id)
        now = time.time() if now is None else now
        if incident.state != IncidentState.RESOLVED:
            incident.state = IncidentState.ACKNOWLEDGED
            incident.updated_at = now
            self.store.save_incident(incident)
        return incident

    def resolve_incident(self, incident_id: str, now: float | None = None) -> Incident:
        incident = self.get_incident(incident_id)
        now = time.time() if now is None else now
        if incident.state != IncidentState.RESOLVED:
            incident.state = IncidentState.RESOLVED
            incident.resolved_at = now
            incident.updated_at = now
            self.store.save_incident(incident)
        return incident

    def _attach_to_incident(
        self, alert: Alert, report: MultiVantageReport, now: float
    ) -> Incident:
        incident = self.store.find_active_incident(alert.service_id)
        if incident is None:
            incident = Incident(
                service_id=alert.service_id,
                target=report.target,
                state=IncidentState.OPEN,
                severity=alert.severity,
                summary=report.verdict,
                alert_ids=[alert.alert_id],
                opened_at=now,
                updated_at=now,
            )
        else:
            if alert.alert_id not in incident.alert_ids:
                incident.alert_ids.append(alert.alert_id)
            incident.severity = max_severity([incident.severity, alert.severity])
            incident.summary = report.verdict
            incident.updated_at = now
        self.store.save_incident(incident)
        return incident

    def _resolve_service(self, service_id: str | None, now: float) -> list[Incident]:
        for alert in self.store.list_alerts(service_id=service_id, state=AlertState.FIRING):
            alert.state = AlertState.RESOLVED
            alert.resolved_at = now
            alert.updated_at = now
            self.store.save_alert(alert)
        resolved: list[Incident] = []
        for incident in self.store.list_incidents(service_id=service_id):
            if incident.state in (IncidentState.OPEN, IncidentState.ACKNOWLEDGED):
                incident.state = IncidentState.RESOLVED
                incident.resolved_at = now
                incident.updated_at = now
                self.store.save_incident(incident)
                resolved.append(incident)
        return resolved

    def _record_service_baseline(self, report: MultiVantageReport) -> bool:
        """Store the reachable-vantage ratio and report whether it regressed."""
        ratio = (report.reachable_count / report.vantage_count) if report.vantage_count else 0.0
        key = report.service_id or report.target
        result = record_and_compare(
            "service",
            key,
            "reachable_ratio",
            ratio,
            higher_is_worse=False,
            store=self._history_store(),
        )
        return bool(result.metrics.get("deviated"))

    def _history_store(self) -> HistoryStore:
        if self._history is None:
            self._history = HistoryStore()
        return self._history

    # --- Host-to-switch-port correlation (M6) ---
    def correlate_host_ports(self, request: DeviceCorrelationRequest) -> list[HostPortBinding]:
        """Fuse LLDP + MAC-table telemetry into persisted host-port bindings."""
        topology = self._require_topology(request.topology)
        bindings = compute_bindings(topology, request.switches)
        self.store.replace_bindings(topology.name, bindings)
        return bindings

    def list_bindings(self, topology: str | None = None) -> list[HostPortBinding]:
        if topology is not None and self.store.get_topology(topology) is None:
            raise UnknownTopologyError(f"unknown topology: {topology}")
        return self.store.list_bindings(topology)

    def augment_topology(self, topology_name: str | None = None) -> NetworkTopology:
        """Add host->switch access edges from stored bindings and re-persist the graph."""
        topology = self._require_topology(topology_name)
        bindings = self.store.list_bindings(topology.name)
        if not bindings:
            raise NoBindingsError(
                f"no host-port bindings for topology '{topology.name}'; correlate device telemetry first"
            )
        augmented = augment_topology_with_bindings(topology, bindings)
        self.store.save_topology(augmented)
        return augmented

    def _require_topology(self, name: str | None) -> NetworkTopology:
        topology = self._resolve_topology(name)
        if topology is None:
            raise UnknownTopologyError(
                "no topology specified and none/multiple imported; pass an explicit topology name"
            )
        return topology

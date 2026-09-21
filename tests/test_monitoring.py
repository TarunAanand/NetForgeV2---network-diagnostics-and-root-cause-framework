"""Pure-logic and service-orchestration tests for M5 monitoring."""

import time
from uuid import uuid4

import pytest

from analysis.multivantage import FaultLocalization, MultiVantageReport
from controller.models import AgentRegistration, DiagnosisRequest
from controller.monitoring import (
    AlertRule,
    AlertState,
    IncidentState,
    ScheduleEntry,
    advance_schedule,
    build_alert,
    default_alert_rule,
    is_due,
    is_healthy,
    matching_rules,
    max_severity,
    rule_matches,
)
from controller.service import ControllerService, UnknownServiceError
from controller.store import ControllerStore
from controller.topology import (
    NetworkTopology,
    NodeRole,
    ServiceEndpoint,
    ServiceInventory,
    TopologyEdge,
    TopologyNode,
)
from core.observation import EvidenceQuality
from core.remote_observation import AgentObservation, RemoteObservationContext
from core.result import DiagnosticResult, DiagnosticStatus, Severity
from storage.history import HistoryStore


def _report(
    localization=FaultLocalization.SOURCE_SIDE,
    status=DiagnosticStatus.FAILED,
    confidence=0.9,
    service_id="db-postgres",
    target="10.0.0.21",
    vantage_count=3,
    reachable_count=2,
    failed_count=1,
):
    return MultiVantageReport(
        diagnosis_id=str(uuid4()),
        target=target,
        service_id=service_id,
        topology="lab",
        localization=localization,
        status=status,
        verdict=f"{localization.value} verdict",
        confidence=confidence,
        vantage_count=vantage_count,
        reachable_count=reachable_count,
        failed_count=failed_count,
    )


def _graph() -> NetworkTopology:
    return NetworkTopology(
        name="lab",
        nodes=[
            TopologyNode(node_id="db", role=NodeRole.SERVICE, agent_id="agent-db"),
            TopologyNode(node_id="app", role=NodeRole.AGENT, agent_id="agent-app"),
            TopologyNode(node_id="mon", role=NodeRole.AGENT, agent_id="agent-mon"),
        ],
        edges=[
            TopologyEdge(src="app", dst="db", kind="lan"),
            TopologyEdge(src="mon", dst="db", kind="lan"),
        ],
    )


def _service_ep() -> ServiceEndpoint:
    return ServiceEndpoint(
        service_id="db-postgres", name="Postgres", node_id="db", host="10.0.0.21", port=5432
    )


def _observation(agent_id, status, target="10.0.0.21"):
    return AgentObservation(
        context=RemoteObservationContext(
            agent_id=agent_id, hostname=agent_id, probe_type="tcp", target=target,
            evidence_quality=EvidenceQuality.SINGLE_SOURCE,
        ),
        result=DiagnosticResult(
            module="tcp", category="host", status=status, severity=Severity.INFO,
            summary=f"{status.value} from {agent_id}", evidence=[f"probe {agent_id}"],
        ),
    )


class ScriptedDispatcher:
    def __init__(self, status_by_agent):
        self.status_by_agent = status_by_agent
        self.calls = 0

    def fanout(self, agents, request):
        self.calls += 1
        obs = {
            a.agent_id: [_observation(a.agent_id, self.status_by_agent.get(a.agent_id, DiagnosticStatus.HEALTHY))]
            for a in agents
        }
        return obs, {}


def _service(tmp_path, dispatcher=None):
    disp = dispatcher or ScriptedDispatcher({})
    service = ControllerService(
        ControllerStore(tmp_path / "c.db"), disp, history=HistoryStore(tmp_path / "h.db")
    )
    service.import_topology(_graph())
    service.register_services(ServiceInventory(services=[_service_ep()]), topology_name="lab")
    return service


# --- Pure decision logic ---


def test_rule_matches_unhealthy_and_skips_healthy():
    rule = AlertRule(name="any", min_confidence=0.0)
    assert rule_matches(rule, _report(localization=FaultLocalization.SOURCE_SIDE))
    assert not rule_matches(rule, _report(localization=FaultLocalization.HEALTHY, status=DiagnosticStatus.HEALTHY))


def test_rule_respects_service_scope():
    rule = AlertRule(service_id="other", min_confidence=0.0)
    assert not rule_matches(rule, _report(service_id="db-postgres"))


def test_rule_respects_min_confidence():
    rule = AlertRule(min_confidence=0.95)
    assert not rule_matches(rule, _report(confidence=0.5))
    assert rule_matches(rule, _report(confidence=0.96))


def test_rule_respects_localization_filter():
    rule = AlertRule(fire_on_localizations=[FaultLocalization.TARGET_SIDE], min_confidence=0.0)
    assert rule_matches(rule, _report(localization=FaultLocalization.TARGET_SIDE))
    assert not rule_matches(rule, _report(localization=FaultLocalization.SOURCE_SIDE))


def test_disabled_rule_never_matches():
    rule = AlertRule(enabled=False, min_confidence=0.0)
    assert not rule_matches(rule, _report())


def test_matching_rules_filters_list():
    rules = [AlertRule(min_confidence=0.0), AlertRule(service_id="nope", min_confidence=0.0)]
    assert len(matching_rules(rules, _report())) == 1


def test_default_alert_rule_confidence_floor():
    rule = default_alert_rule()
    assert rule.min_confidence == 0.5
    assert not rule_matches(rule, _report(confidence=0.2))
    assert rule_matches(rule, _report(confidence=0.7))


def test_max_severity_orders_correctly():
    assert max_severity([Severity.LOW, Severity.CRITICAL, Severity.MEDIUM]) == Severity.CRITICAL
    assert max_severity([]) == Severity.INFO


def test_schedule_due_and_advance():
    schedule = ScheduleEntry(service_id="db-postgres", interval_seconds=60, next_run_at=100.0)
    assert is_due(schedule, 100.0)
    assert is_due(schedule, 150.0)
    assert not is_due(schedule, 50.0)
    advanced = advance_schedule(schedule, 100.0)
    assert advanced.last_run_at == 100.0
    assert advanced.next_run_at == 160.0
    assert not is_due(advanced, 150.0)


def test_disabled_schedule_never_due():
    schedule = ScheduleEntry(service_id="s", interval_seconds=60, enabled=False, next_run_at=0.0)
    assert not is_due(schedule, 1e9)


def test_build_alert_message_marks_regression():
    rule = AlertRule(name="db-down", severity=Severity.CRITICAL)
    alert = build_alert(rule, _report(), now=5.0, regression=True)
    assert alert.severity == Severity.CRITICAL
    assert alert.state == AlertState.FIRING
    assert "regression vs baseline" in alert.message


def test_is_healthy():
    assert is_healthy(_report(localization=FaultLocalization.HEALTHY))
    assert not is_healthy(_report(localization=FaultLocalization.PATH_SHARED))


# --- Service orchestration ---


def test_process_diagnosis_fires_alert_and_opens_incident(tmp_path):
    service = _service(tmp_path)
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0, severity=Severity.HIGH))
    outcome = service.process_diagnosis(_report(), now=1000.0)
    assert len(outcome["alerts"]) == 1
    assert len(outcome["incidents"]) == 1
    incident = outcome["incidents"][0]
    assert incident.state == IncidentState.OPEN
    assert incident.severity == Severity.HIGH
    assert service.list_alerts(state=AlertState.FIRING)[0].state == AlertState.FIRING
    assert service.list_incidents(state=IncidentState.OPEN)[0].incident_id == incident.incident_id


def test_process_diagnosis_dedups_firing_alert(tmp_path):
    service = _service(tmp_path)
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0))
    service.process_diagnosis(_report(), now=1000.0)
    service.process_diagnosis(_report(), now=1060.0)
    firing = service.list_alerts(state=AlertState.FIRING)
    assert len(firing) == 1
    incidents = service.list_incidents()
    assert len(incidents) == 1
    assert len(incidents[0].alert_ids) == 1


def test_process_diagnosis_auto_resolves_on_healthy(tmp_path):
    service = _service(tmp_path)
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0))
    service.process_diagnosis(_report(), now=1000.0)
    assert len(service.list_incidents(state=IncidentState.OPEN)) == 1

    healthy = _report(
        localization=FaultLocalization.HEALTHY,
        status=DiagnosticStatus.HEALTHY,
        reachable_count=3,
        failed_count=0,
    )
    outcome = service.process_diagnosis(healthy, now=1060.0)
    assert len(outcome["resolved_incidents"]) == 1
    assert service.list_alerts(state=AlertState.FIRING) == []
    assert service.list_alerts(state=AlertState.RESOLVED)[0].resolved_at == 1060.0
    assert service.list_incidents(state=IncidentState.RESOLVED)[0].state == IncidentState.RESOLVED


def test_process_diagnosis_flags_regression_against_baseline(tmp_path):
    service = _service(tmp_path)
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0))
    # Seed a healthy reachability baseline (reachable_ratio = 1.0).
    for i in range(3):
        service.process_diagnosis(
            _report(
                localization=FaultLocalization.HEALTHY,
                status=DiagnosticStatus.HEALTHY,
                reachable_count=3,
                failed_count=0,
            ),
            now=1000.0 + i,
        )
    # Sudden total outage -> reachable_ratio 0.0, far below the baseline.
    outcome = service.process_diagnosis(
        _report(
            localization=FaultLocalization.TARGET_SIDE,
            reachable_count=0,
            failed_count=3,
        ),
        now=2000.0,
    )
    assert outcome["regression"] is True
    assert outcome["alerts"][0].regression is True


def test_incident_acknowledge_and_resolve(tmp_path):
    service = _service(tmp_path)
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0))
    incident = service.process_diagnosis(_report(), now=1000.0)["incidents"][0]

    acked = service.acknowledge_incident(incident.incident_id)
    assert acked.state == IncidentState.ACKNOWLEDGED

    resolved = service.resolve_incident(incident.incident_id)
    assert resolved.state == IncidentState.RESOLVED
    assert resolved.resolved_at is not None
    # Idempotent: re-resolving keeps it resolved.
    assert service.resolve_incident(incident.incident_id).state == IncidentState.RESOLVED


def test_create_schedule_validates_service(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(UnknownServiceError):
        service.create_schedule(ScheduleEntry(service_id="missing", interval_seconds=60))


def test_run_due_schedules_runs_and_advances(tmp_path):
    dispatcher = ScriptedDispatcher(
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.HEALTHY, "agent-mon": DiagnosticStatus.HEALTHY}
    )
    service = _service(tmp_path, dispatcher)
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    service.create_schedule(ScheduleEntry(service_id="db-postgres", interval_seconds=60))

    now = time.time() + 1
    reports = service.run_due_schedules(now=now)
    assert len(reports) == 1
    assert dispatcher.calls == 1

    schedule = service.list_schedules()[0]
    assert schedule.last_run_at == now
    assert schedule.next_run_at == now + 60
    # Not due again immediately.
    assert service.run_due_schedules(now=now) == []
    assert dispatcher.calls == 1


def test_run_due_schedules_creates_incident_on_failure(tmp_path):
    dispatcher = ScriptedDispatcher(
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.HEALTHY}
    )
    service = _service(tmp_path, dispatcher)
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    service.register_alert_rule(AlertRule(name="any", min_confidence=0.0, severity=Severity.HIGH))
    service.create_schedule(ScheduleEntry(service_id="db-postgres", interval_seconds=60))

    reports = service.run_due_schedules(now=time.time())
    assert reports[0].localization == FaultLocalization.SOURCE_SIDE
    assert len(service.list_incidents(state=IncidentState.OPEN)) == 1


def test_run_due_schedules_skips_disabled(tmp_path):
    dispatcher = ScriptedDispatcher({})
    service = _service(tmp_path, dispatcher)
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    service.create_schedule(
        ScheduleEntry(service_id="db-postgres", interval_seconds=60, enabled=False)
    )
    assert service.run_due_schedules(now=time.time()) == []
    assert dispatcher.calls == 0


def test_delete_schedule(tmp_path):
    service = _service(tmp_path)
    schedule = service.create_schedule(ScheduleEntry(service_id="db-postgres", interval_seconds=30))
    assert service.delete_schedule(schedule.schedule_id) is True
    assert service.list_schedules() == []


def test_monitor_scheduler_tick_runs_due_schedules(tmp_path):
    from controller.scheduler import MonitorScheduler

    dispatcher = ScriptedDispatcher({})
    service = _service(tmp_path, dispatcher)
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    service.create_schedule(ScheduleEntry(service_id="db-postgres", interval_seconds=3600))

    scheduler = MonitorScheduler(service, poll_interval=0.01)
    scheduler.tick()
    assert dispatcher.calls == 1
    # A second immediate tick is not due (interval is an hour away).
    scheduler.tick()
    assert dispatcher.calls == 1


def test_monitor_scheduler_start_stop(tmp_path):
    from controller.scheduler import MonitorScheduler

    service = _service(tmp_path, ScriptedDispatcher({}))
    scheduler = MonitorScheduler(service, poll_interval=0.01)
    scheduler.start()
    assert scheduler.running
    scheduler.stop(timeout=2)
    assert not scheduler.running


class FailingDispatcher:
    def fanout(self, agents, request):
        raise RuntimeError("agent unreachable")


def test_run_due_schedules_records_failure_state(tmp_path):
    service = _service(tmp_path, FailingDispatcher())
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    service.create_schedule(ScheduleEntry(service_id="db-postgres", interval_seconds=60))

    now = time.time() + 1
    assert service.run_due_schedules(now=now) == []
    schedule = service.list_schedules()[0]
    assert schedule.consecutive_failures == 1
    assert "agent unreachable" in schedule.last_error
    assert schedule.next_run_at == now + 60

    assert service.run_due_schedules(now=now + 60) == []
    assert service.list_schedules()[0].consecutive_failures == 2


def test_advance_schedule_clears_failure_state_on_success():
    failed = advance_schedule(
        ScheduleEntry(service_id="db-postgres", interval_seconds=60), 100.0, "RuntimeError: boom"
    )
    assert failed.consecutive_failures == 1 and failed.last_error == "RuntimeError: boom"
    recovered = advance_schedule(failed, 200.0)
    assert recovered.consecutive_failures == 0 and recovered.last_error is None

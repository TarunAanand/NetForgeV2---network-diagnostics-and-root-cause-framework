"""HTTP endpoint tests for M5 scheduling, alerts, and incidents."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from controller.http_server import make_handler
from controller.models import AgentRegistration
from controller.service import ControllerService
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

    def fanout(self, agents, request):
        return (
            {
                a.agent_id: [
                    _observation(a.agent_id, self.status_by_agent.get(a.agent_id, DiagnosticStatus.HEALTHY))
                ]
                for a in agents
            },
            {},
        )


def _setup(tmp_path, status_by_agent):
    dispatcher = ScriptedDispatcher(status_by_agent)
    service = ControllerService(
        ControllerStore(tmp_path / "c.db"), dispatcher, history=HistoryStore(tmp_path / "h.db")
    )
    service.import_topology(
        NetworkTopology(
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
    )
    service.register_services(
        ServiceInventory(
            services=[
                ServiceEndpoint(
                    service_id="db-postgres", name="Postgres", node_id="db", host="10.0.0.21", port=5432
                )
            ]
        ),
        topology_name="lab",
    )
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    return service


def _start(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, "controller-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _call(url, method="GET", body=None, token="controller-secret"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        return response.status, json.loads(response.read())


def test_http_schedule_monitor_and_incident_lifecycle(tmp_path):
    service = _setup(
        tmp_path,
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.HEALTHY},
    )
    server, thread, base = _start(service)
    try:
        # Register an alert rule that fires on any unhealthy localization.
        status, rule = _call(base + "/v1/alert-rules", "POST", {"name": "any-unhealthy", "min_confidence": 0.0})
        assert status == 201 and rule["rule_id"]
        status, listing = _call(base + "/v1/alert-rules")
        assert len(listing["rules"]) == 1

        # Create a schedule for the service.
        status, schedule = _call(
            base + "/v1/schedules", "POST", {"service_id": "db-postgres", "interval_seconds": 60}
        )
        assert status == 201 and schedule["schedule_id"]
        status, listing = _call(base + "/v1/schedules")
        assert len(listing["schedules"]) == 1

        # Trigger the monitoring pass; the source-side fault should raise an alert.
        status, run = _call(base + "/v1/monitor/run", "POST", {})
        assert status == 200 and run["ran"] == 1
        assert run["reports"][0]["localization"] == "source_side"

        status, alerts = _call(base + "/v1/alerts", "GET")
        assert len(alerts["alerts"]) == 1
        assert alerts["alerts"][0]["state"] == "firing"

        status, incidents = _call(base + "/v1/incidents", "GET")
        assert len(incidents["incidents"]) == 1
        incident_id = incidents["incidents"][0]["incident_id"]
        assert incidents["incidents"][0]["state"] == "open"

        # Acknowledge then resolve.
        status, acked = _call(base + f"/v1/incidents/{incident_id}/ack", "POST", {})
        assert status == 200 and acked["state"] == "acknowledged"
        status, resolved = _call(base + f"/v1/incidents/{incident_id}/resolve", "POST", {})
        assert status == 200 and resolved["state"] == "resolved"

        status, fetched = _call(base + f"/v1/incidents/{incident_id}")
        assert status == 200 and fetched["state"] == "resolved"

        # Delete the schedule.
        status, deleted = _call(base + f"/v1/schedules/{schedule['schedule_id']}", "DELETE")
        assert status == 200 and deleted["deleted"] is True
        status, listing = _call(base + "/v1/schedules")
        assert listing["schedules"] == []
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_alerts_and_incidents_support_state_filter(tmp_path):
    service = _setup(
        tmp_path,
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.HEALTHY},
    )
    server, thread, base = _start(service)
    try:
        _call(base + "/v1/alert-rules", "POST", {"name": "any", "min_confidence": 0.0})
        _call(base + "/v1/schedules", "POST", {"service_id": "db-postgres", "interval_seconds": 60})
        _call(base + "/v1/monitor/run", "POST", {})

        status, firing = _call(base + "/v1/alerts?state=firing")
        assert len(firing["alerts"]) == 1
        status, resolved = _call(base + "/v1/alerts?state=resolved")
        assert resolved["alerts"] == []
        status, open_inc = _call(base + "/v1/incidents?state=open")
        assert len(open_inc["incidents"]) == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_unknown_incident_is_404(tmp_path):
    service = _setup(tmp_path, {})
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/incidents/nope")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("unknown incident returned a body")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_schedule_unknown_service_is_404(tmp_path):
    service = _setup(tmp_path, {})
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/schedules", "POST", {"service_id": "missing", "interval_seconds": 60})
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("schedule for unknown service was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)

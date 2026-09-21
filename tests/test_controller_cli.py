"""Tests for the controller HTTP client, CLI wiring, and rich renderers.

These exercise the CLI -> controller path over a real loopback ThreadingHTTPServer
(the same bootstrap used by the M5/M6 HTTP tests), so the client, typer command
group, and renderers are validated end to end without a live deployment.
"""

import json
import threading
from http.server import ThreadingHTTPServer

from typer.testing import CliRunner

from cli import app
from controller.client import ControllerAPIError, ControllerClient
from controller.correlation import CorrelationMethod
from controller.http_server import make_handler
from controller.models import AgentRegistration
from controller.monitoring import Alert, AlertState, Incident, IncidentState, ScheduleEntry
from controller.correlation import HostPortBinding
from controller.render import (
    render_alerts,
    render_bindings,
    render_incidents,
    render_schedules,
    render_topology,
)
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

TOKEN = "controller-secret"


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
    service = ControllerService(
        ControllerStore(tmp_path / "c.db"),
        ScriptedDispatcher(status_by_agent),
        history=HistoryStore(tmp_path / "h.db"),
    )
    service.import_topology(
        NetworkTopology(
            name="lab",
            nodes=[
                TopologyNode(node_id="core-sw", role=NodeRole.SWITCH),
                TopologyNode(
                    node_id="app-1", role=NodeRole.AGENT, agent_id="agent-app-1",
                    address="10.0.0.11", tags={"mac": "00:11:22:33:44:55"},
                ),
                TopologyNode(
                    node_id="db-1", role=NodeRole.SERVICE, agent_id="agent-db-1",
                    address="10.0.0.21", tags={"mac": "00:11:22:33:44:66"},
                ),
                TopologyNode(node_id="mon", role=NodeRole.AGENT, agent_id="agent-mon"),
            ],
            edges=[
                TopologyEdge(src="app-1", dst="db-1", kind="lan"),
                TopologyEdge(src="mon", dst="db-1", kind="lan"),
            ],
        )
    )
    service.register_services(
        ServiceInventory(
            services=[
                ServiceEndpoint(service_id="db-postgres", name="Postgres", node_id="db-1", host="10.0.0.21", port=5432)
            ]
        ),
        topology_name="lab",
    )
    for agent_id in ("agent-app-1", "agent-db-1", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    return service


def _start(service):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _switches():
    return [
        {
            "node_id": "core-sw",
            "lldp": [
                {"local_port": "Gi1/0/1", "neighbor_sys_name": "app-1", "neighbor_chassis_id": "00:11:22:33:44:55"},
                {"local_port": "Gi1/0/2", "neighbor_sys_name": "db-1"},
            ],
            "mac_table": [{"mac": "00:11:22:33:44:55", "bridge_port": 1, "if_index": 1}],
        }
    ]


def _norm(text: str) -> str:
    return " ".join(text.split())


# --- Client ---


def test_client_end_to_end(tmp_path):
    service = _setup(
        tmp_path,
        {"agent-app-1": DiagnosticStatus.FAILED, "agent-db-1": DiagnosticStatus.HEALTHY, "agent-mon": DiagnosticStatus.HEALTHY},
    )
    server, thread, base = _start(service)
    try:
        client = ControllerClient(base, TOKEN, timeout=3)
        assert client.health()["status"] == "healthy"
        assert {a["agent_id"] for a in client.list_agents()} == {"agent-app-1", "agent-db-1", "agent-mon"}
        assert client.list_topology_names() == ["lab"]
        assert [s.service_id for s in client.list_services()] == ["db-postgres"]

        # M4 diagnosis
        report = client.diagnose("db-postgres")
        assert report.localization.value == "source_side"
        assert client.list_diagnoses()[0].service_id == "db-postgres"

        # M6 correlation -> bindings -> augment
        bindings = client.correlate("lab", _switches())
        assert {b.host_node_id for b in bindings} == {"app-1", "db-1"}
        app = next(b for b in bindings if b.host_node_id == "app-1")
        assert app.method == CorrelationMethod.LLDP_AND_MAC
        assert len(client.list_bindings("lab")) == 2
        augmented = client.augment_topology("lab")
        assert ("app-1", "core-sw") in {(e.src, e.dst) for e in augmented.edges}

        # M5 monitoring -> alerts -> incidents
        client.create_alert_rule(name="any-unhealthy", min_confidence=0.0, severity="high")
        client.create_schedule("db-postgres", interval_seconds=60)
        reports = client.run_monitor()
        assert len(reports) == 1 and reports[0].localization.value == "source_side"
        alerts = client.list_alerts()
        assert len(alerts) == 1 and alerts[0].state == AlertState.FIRING
        incidents = client.list_incidents()
        assert len(incidents) == 1 and incidents[0].state == IncidentState.OPEN
        iid = incidents[0].incident_id
        assert client.ack_incident(iid).state == IncidentState.ACKNOWLEDGED
        assert client.resolve_incident(iid).state == IncidentState.RESOLVED
        assert client.get_incident(iid).state == IncidentState.RESOLVED
        assert client.delete_schedule(client.list_schedules()[0].schedule_id) is True
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_client_raises_on_bad_token(tmp_path):
    service = _setup(tmp_path, {})
    server, thread, base = _start(service)
    try:
        client = ControllerClient(base, "wrong-token", timeout=3)
        try:
            client.health()
        except ControllerAPIError as exc:
            assert exc.status == 401
        else:
            raise AssertionError("bad token was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)


# --- CLI (typer) ---


def test_cli_json_commands(tmp_path):
    service = _setup(
        tmp_path,
        {"agent-app-1": DiagnosticStatus.FAILED, "agent-db-1": DiagnosticStatus.HEALTHY, "agent-mon": DiagnosticStatus.HEALTHY},
    )
    server, thread, base = _start(service)
    runner = CliRunner()
    root = ["controller", "--url", base, "--token", TOKEN]
    try:
        # diagnose --json
        res = runner.invoke(app, root + ["diagnose", "db-postgres", "--json"])
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["localization"] == "source_side"

        # diagnose --strict exits 1 when unhealthy
        res = runner.invoke(app, root + ["diagnose", "db-postgres", "--strict", "--json"])
        assert res.exit_code == 1

        # correlate --json
        telemetry = tmp_path / "telemetry.json"
        telemetry.write_text(json.dumps({"switches": _switches()}), encoding="utf-8")
        res = runner.invoke(app, root + ["correlate", "lab", str(telemetry), "--json"])
        assert res.exit_code == 0, res.output
        assert len(json.loads(res.output)) == 2

        # augment --json
        res = runner.invoke(app, root + ["augment", "lab", "--json"])
        assert res.exit_code == 0, res.output
        edges = {(e["src"], e["dst"]) for e in json.loads(res.output)["edges"]}
        assert ("app-1", "core-sw") in edges

        # schedule add -> monitor run --json -> incident created
        res = runner.invoke(app, root + ["alert", "rule-add", "--name", "any", "--min-confidence", "0.0"])
        assert res.exit_code == 0, res.output
        res = runner.invoke(app, root + ["schedule", "add", "db-postgres", "--interval", "60"])
        assert res.exit_code == 0, res.output
        res = runner.invoke(app, root + ["monitor", "run", "--json"])
        assert res.exit_code == 0, res.output
        reports = json.loads(res.output)
        assert len(reports) == 1 and reports[0]["localization"] == "source_side"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_cli_table_commands(tmp_path):
    service = _setup(
        tmp_path,
        {"agent-app-1": DiagnosticStatus.FAILED, "agent-db-1": DiagnosticStatus.HEALTHY, "agent-mon": DiagnosticStatus.HEALTHY},
    )
    server, thread, base = _start(service)
    runner = CliRunner()
    root = ["controller", "--url", base, "--token", TOKEN]
    try:
        res = runner.invoke(app, root + ["health"])
        assert res.exit_code == 0 and "healthy" in _norm(res.output)

        res = runner.invoke(app, root + ["agent", "list"])
        assert res.exit_code == 0 and "agent-mon" in _norm(res.output)

        res = runner.invoke(app, root + ["topology", "list"])
        assert res.exit_code == 0 and "lab" in _norm(res.output)

        res = runner.invoke(app, root + ["service", "list"])
        assert res.exit_code == 0 and "db-postgres" in _norm(res.output)

        # Empty-state renderers produce stable messages.
        res = runner.invoke(app, root + ["incident", "list"])
        assert res.exit_code == 0 and "No incidents" in _norm(res.output)

        res = runner.invoke(app, root + ["alert", "list"])
        assert res.exit_code == 0 and "No alerts" in _norm(res.output)

        res = runner.invoke(app, root + ["schedule", "list"])
        assert res.exit_code == 0 and "No schedules" in _norm(res.output)

        res = runner.invoke(app, root + ["binding", "list", "lab"])
        assert res.exit_code == 0 and "No host-to-switch-port bindings" in _norm(res.output)
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_cli_bad_token_exits_nonzero(tmp_path):
    service = _setup(tmp_path, {})
    server, thread, base = _start(service)
    runner = CliRunner()
    try:
        res = runner.invoke(app, ["controller", "--url", base, "--token", "nope", "health"])
        assert res.exit_code == 1
        assert "controller API error" in _norm(res.output)
    finally:
        server.shutdown()
        thread.join(timeout=2)


# --- Renderers ---


def test_render_bindings_and_incidents():
    from io import StringIO

    from rich.console import Console

    binding = HostPortBinding(
        host_node_id="app-1", host_identity="app-1", switch_node_id="core-sw",
        switch_port="Gi1/0/1", if_index=1, method=CorrelationMethod.LLDP_AND_MAC,
        confidence=0.95, evidence_quality=EvidenceQuality.CORROBORATED,
    )
    out = StringIO()
    render_bindings([binding], console=Console(file=out, width=120))
    text = _norm(out.getvalue())
    assert "app-1" in text and "core-sw" in text and "lldp_and_mac" in text

    incident = Incident(
        incident_id="inc-1234abcd", service_id="db-postgres", target="10.0.0.21",
        state=IncidentState.OPEN, severity=Severity.HIGH, summary="source-side fault",
        alert_ids=["a1"], opened_at=1_700_000_000.0, updated_at=1_700_000_000.0,
    )
    out = StringIO()
    render_incidents([incident], console=Console(file=out, width=120))
    assert "db-postgres" in _norm(out.getvalue())


def test_render_alerts_schedules_topology():
    from io import StringIO

    from rich.console import Console

    alert = Alert(
        alert_id="alert-1234abcd", rule_id="r1", service_id="db-postgres", target="10.0.0.21",
        localization="source_side", status=DiagnosticStatus.FAILED, confidence=0.8,
        severity=Severity.HIGH, diagnosis_id="d1", message="boom", state=AlertState.FIRING,
        created_at=1_700_000_000.0, updated_at=1_700_000_000.0,
    )
    out = StringIO()
    render_alerts([alert], console=Console(file=out, width=140))
    assert "db-postgres" in _norm(out.getvalue())

    sched = ScheduleEntry(service_id="db-postgres", interval_seconds=60, count=3)
    out = StringIO()
    render_schedules([sched], console=Console(file=out, width=120))
    assert "db-postgres" in _norm(out.getvalue())

    topo = NetworkTopology(
        name="lab",
        nodes=[TopologyNode(node_id="core-sw", role=NodeRole.SWITCH), TopologyNode(node_id="app-1", role=NodeRole.HOST)],
    )
    out = StringIO()
    render_topology(topo, console=Console(file=out, width=120))
    text = _norm(out.getvalue())
    assert "lab" in text and "Nodes" in text

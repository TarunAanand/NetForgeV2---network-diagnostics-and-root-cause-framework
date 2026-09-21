"""Vantage selection, diagnosis orchestration, and HTTP tests for M4."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent.models import ProbeType
from analysis.multivantage import FaultLocalization
from controller.http_server import make_handler
from controller.models import AgentRegistration, DiagnosisRequest
from controller.service import (
    ControllerService,
    NoVantageAgentsError,
    UnknownServiceError,
)
from controller.store import ControllerStore
from controller.topology import (
    NetworkTopology,
    NodeRole,
    ServiceEndpoint,
    ServiceInventory,
    TopologyEdge,
    TopologyNode,
)
from controller.vantage import select_vantages
from core.observation import EvidenceQuality
from core.remote_observation import AgentObservation, RemoteObservationContext
from core.result import DiagnosticResult, DiagnosticStatus, Severity


def _correlation(*pairs):
    return {
        "nodes": [
            {"node_id": n, "agent_id": a, "registered": True, "enabled": True, "last_seen_at": None}
            for n, a in pairs
        ]
    }


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


# --- Vantage selection ---


def test_select_vantages_picks_destination_source_and_third():
    topo = _graph()
    corr = _correlation(("db", "agent-db"), ("app", "agent-app"), ("mon", "agent-mon"))
    plan = select_vantages(topo, _service_ep(), corr)
    roles = {a.role: a.node_id for a in plan.assignments}
    assert roles["destination"] == "db"
    assert roles["source"] == "app"
    assert roles["third"] == "mon"
    assert plan.target == "10.0.0.21"


def test_select_vantages_prefers_distant_third():
    # a1/a2 are adjacent to db (same access segment); b1 is topologically distant.
    # Alphabetically a2 < b1, so without the distance preference a2 would be picked.
    topo = NetworkTopology(
        name="lab",
        nodes=[
            TopologyNode(node_id="db", role=NodeRole.SERVICE, agent_id="agent-db"),
            TopologyNode(node_id="a1", role=NodeRole.AGENT, agent_id="agent-a1"),
            TopologyNode(node_id="a2", role=NodeRole.AGENT, agent_id="agent-a2"),
            TopologyNode(node_id="gw", role=NodeRole.GATEWAY),
            TopologyNode(node_id="b1", role=NodeRole.AGENT, agent_id="agent-b1"),
        ],
        edges=[
            TopologyEdge(src="a1", dst="db", kind="lan"),   # adjacent to db
            TopologyEdge(src="a2", dst="db", kind="lan"),   # adjacent to db
            TopologyEdge(src="db", dst="gw", kind="lan"),
            TopologyEdge(src="gw", dst="b1", kind="wan"),   # NOT adjacent to db
        ],
    )
    corr = _correlation(
        ("db", "agent-db"), ("a1", "agent-a1"), ("a2", "agent-a2"), ("b1", "agent-b1")
    )
    plan = select_vantages(topo, _service_ep(), corr, source_node_id="a1")
    roles = {a.role: a.node_id for a in plan.assignments}
    assert roles["destination"] == "db"
    assert roles["source"] == "a1"
    assert roles["third"] == "b1"  # distant node chosen over the adjacent a2


def test_select_vantages_empty_when_no_registered_agents():
    plan = select_vantages(_graph(), _service_ep(), _correlation())
    assert plan.assignments == []


def test_select_vantages_honours_explicit_source():
    corr = _correlation(("db", "agent-db"), ("app", "agent-app"), ("mon", "agent-mon"))
    plan = select_vantages(_graph(), _service_ep(), corr, source_node_id="mon")
    roles = {a.role: a.node_id for a in plan.assignments}
    assert roles["source"] == "mon"


# --- Service orchestration ---


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
        self.last_request = None
        self.last_agents = None

    def fanout(self, agents, request):
        self.last_request = request
        self.last_agents = [a.agent_id for a in agents]
        obs = {
            a.agent_id: [_observation(a.agent_id, self.status_by_agent.get(a.agent_id, DiagnosticStatus.HEALTHY))]
            for a in agents
        }
        return obs, {}


def _setup_service(tmp_path, dispatcher):
    service = ControllerService(ControllerStore(tmp_path / "c.db"), dispatcher)
    service.import_topology(_graph())
    service.register_services(ServiceInventory(services=[_service_ep()]), topology_name="lab")
    for agent_id in ("agent-db", "agent-app", "agent-mon"):
        service.register_agent(AgentRegistration(agent_id=agent_id, url="http://127.0.0.1:8081"))
    return service


def test_diagnose_service_localizes_source_side_and_persists(tmp_path):
    dispatcher = ScriptedDispatcher(
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.HEALTHY}
    )
    service = _setup_service(tmp_path, dispatcher)
    report = service.diagnose_service(DiagnosisRequest(service_id="db-postgres"))

    assert report.localization == FaultLocalization.SOURCE_SIDE
    assert report.topology == "lab"
    assert report.service_id == "db-postgres"
    assert report.vantage_count == 3
    # TCP service -> TCP probe carrying the service port
    assert dispatcher.last_request.probe_type == ProbeType.TCP
    assert dispatcher.last_request.port == 5432
    assert set(dispatcher.last_agents) == {"agent-db", "agent-app", "agent-mon"}
    # persisted evidence trail is retrievable
    assert service.get_diagnosis(report.diagnosis_id).localization == FaultLocalization.SOURCE_SIDE
    assert len(service.list_diagnoses()) == 1


def test_diagnose_service_target_side_when_destination_agent_fails(tmp_path):
    dispatcher = ScriptedDispatcher(
        {"agent-db": DiagnosticStatus.FAILED, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.FAILED}
    )
    service = _setup_service(tmp_path, dispatcher)
    report = service.diagnose_service(DiagnosisRequest(service_id="db-postgres"))
    assert report.localization == FaultLocalization.TARGET_SIDE


def test_diagnose_service_records_agent_errors(tmp_path):
    class PartialDispatcher:
        def fanout(self, agents, request):
            healthy = [a for a in agents if a.agent_id == "agent-db"]
            return (
                {a.agent_id: [_observation(a.agent_id, DiagnosticStatus.HEALTHY)] for a in healthy},
                {"agent-app": "timeout", "agent-mon": "refused"},
            )

    service = _setup_service(tmp_path, PartialDispatcher())
    report = service.diagnose_service(DiagnosisRequest(service_id="db-postgres"))
    assert report.agent_errors == {"agent-app": "timeout", "agent-mon": "refused"}


def test_diagnose_unknown_service_raises(tmp_path):
    service = _setup_service(tmp_path, ScriptedDispatcher({}))
    with pytest.raises(UnknownServiceError):
        service.diagnose_service(DiagnosisRequest(service_id="missing"))


def test_diagnose_without_agents_raises_no_vantage(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "c.db"), ScriptedDispatcher({}))
    service.import_topology(_graph())
    service.register_services(ServiceInventory(services=[_service_ep()]), topology_name="lab")
    with pytest.raises(NoVantageAgentsError):
        service.diagnose_service(DiagnosisRequest(service_id="db-postgres"))


# --- HTTP layer ---


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


def test_http_diagnose_and_fetch_trail(tmp_path):
    dispatcher = ScriptedDispatcher(
        {"agent-db": DiagnosticStatus.HEALTHY, "agent-app": DiagnosticStatus.FAILED, "agent-mon": DiagnosticStatus.HEALTHY}
    )
    service = _setup_service(tmp_path, dispatcher)
    server, thread, base = _start(service)
    try:
        status, report = _call(base + "/v1/diagnose", "POST", {"service_id": "db-postgres"})
        assert status == 200
        assert report["localization"] == "source_side"
        diagnosis_id = report["diagnosis_id"]

        status, listing = _call(base + "/v1/diagnoses")
        assert [d["diagnosis_id"] for d in listing["diagnoses"]] == [diagnosis_id]

        status, fetched = _call(base + f"/v1/diagnoses/{diagnosis_id}")
        assert status == 200 and fetched["verdict"] == report["verdict"]

        try:
            _call(base + "/v1/diagnoses/nope")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("unknown diagnosis returned a body")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_diagnose_unknown_service_is_404(tmp_path):
    service = _setup_service(tmp_path, ScriptedDispatcher({}))
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/diagnose", "POST", {"service_id": "missing"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("unknown service was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_diagnose_no_vantage_is_409(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "c.db"), ScriptedDispatcher({}))
    service.import_topology(_graph())
    service.register_services(ServiceInventory(services=[_service_ep()]), topology_name="lab")
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/diagnose", "POST", {"service_id": "db-postgres"})
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("diagnosis without vantage agents was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)

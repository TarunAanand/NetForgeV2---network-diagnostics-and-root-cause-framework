from agent.config import AgentConfig
from agent.models import ProbeRequest
from agent.service import AgentService, AuthorizationError, TargetNotAllowedError
from core.result import DiagnosticResult, DiagnosticStatus, Severity


def _service() -> AgentService:
    return AgentService(AgentConfig(agent_id="app-1", hostname="app-1.lab", bearer_token="secret", allowed_targets=frozenset({"db-1"})))


def test_agent_requires_bearer_token():
    service = _service()
    try:
        service.authorize("Bearer wrong")
    except AuthorizationError:
        return
    raise AssertionError("agent accepted an invalid token")


def test_agent_rejects_target_outside_allow_list():
    service = _service()
    try:
        service.probe(ProbeRequest(probe_type="icmp", target="unapproved"))
    except TargetNotAllowedError:
        return
    raise AssertionError("agent probed an unapproved target")


def test_agent_returns_controller_ready_observation(monkeypatch):
    service = _service()
    result = DiagnosticResult(module="tcp", category="host", status=DiagnosticStatus.HEALTHY, severity=Severity.INFO, summary="ok", target="db-1:5432", metrics={"port": 5432})
    monkeypatch.setattr(service, "_run_probe", lambda request: [result])
    observation = service.probe(ProbeRequest(probe_type="tcp", target="db-1", port=5432))[0]
    assert observation.context.agent_id == "app-1"
    assert observation.context.target == "db-1"
    assert observation.result.metrics["port"] == 5432

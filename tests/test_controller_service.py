from agent.models import ProbeRequest
from controller.models import AgentRegistration, FanoutJobRequest
from controller.service import ControllerService, UnknownAgentError
from controller.store import ControllerStore
from core.remote_observation import AgentObservation, RemoteObservationContext
from core.result import DiagnosticResult, DiagnosticStatus, Severity


class FakeDispatcher:
    def fanout(self, agents, request):
        observation = AgentObservation(
            context=RemoteObservationContext(agent_id="app-1", hostname="app-1", probe_type="icmp", target="db-1"),
            result=DiagnosticResult(module="packet_loss", category="host", status=DiagnosticStatus.HEALTHY, severity=Severity.INFO, summary="ok"),
        )
        return {agent.agent_id: [observation] for agent in agents}, {}


def test_controller_registers_agents_dispatches_and_persists_job(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "controller.db"), FakeDispatcher())
    service.register_agent(AgentRegistration(agent_id="app-1", url="http://127.0.0.1:8081"))
    job = service.dispatch_job(FanoutJobRequest(agent_ids=["app-1"], probe=ProbeRequest(probe_type="icmp", target="db-1")))
    assert job.status == "completed"
    assert job.observations["app-1"][0].context.target == "db-1"
    assert service.get_job(job.job_id) is not None


def test_controller_rejects_unknown_agents(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "controller.db"), FakeDispatcher())
    try:
        service.dispatch_job(FanoutJobRequest(agent_ids=["missing"], probe=ProbeRequest(probe_type="interfaces")))
    except UnknownAgentError:
        return
    raise AssertionError("controller dispatched to an unknown agent")

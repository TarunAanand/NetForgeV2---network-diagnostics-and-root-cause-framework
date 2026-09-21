import datetime

from core.observation import EvidenceQuality, ObservationContext


def test_observation_context_carries_required_remote_agent_context():
    observation = ObservationContext(
        timestamp=datetime.datetime(2026, 9, 20, tzinfo=datetime.timezone.utc),
        agent_id="app-1",
        hostname="app-1.lab",
        source_ip="10.10.10.11",
        source_interface="eth0",
        target="db-1",
        target_interface="eth0",
        probe_type="tcp_connect",
        sample_count=3,
        duration_ms=42.5,
        topology_tags={"site": "lab", "network": "backend", "vlan": "120"},
        raw_evidence={"socket_error": None},
        evidence_quality=EvidenceQuality.CORROBORATED,
    )
    assert observation.confidence == 0.80
    assert observation.raw_evidence == {"socket_error": None}
    assert observation.topology_tags["vlan"] == "120"


def test_observation_context_rejects_missing_agent_identity():
    try:
        ObservationContext(agent_id="", hostname="host", probe_type="icmp")
    except ValueError:
        return
    raise AssertionError("agent identity must be required")

"""Tests for host-to-switch-port correlation (M6)."""

import pytest

from controller.correlation import (
    CorrelationMethod,
    DeviceCorrelationRequest,
    HostPortBinding,
    SwitchTelemetry,
    augment_topology_with_bindings,
    correlate_host_ports,
    normalize_mac,
)
from controller.topology import NetworkTopology, NodeRole, TopologyEdge, TopologyNode
from core.observation import EvidenceQuality
from ingest.lldp import LldpNeighbor
from ingest.snmp_counters import MacTableEntry


def _topology() -> NetworkTopology:
    return NetworkTopology(
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
        ],
    )


def test_normalize_mac_variants():
    assert normalize_mac("00:11:22:33:44:55") == "001122334455"
    assert normalize_mac("00-11-22-33-44-55") == "001122334455"
    assert normalize_mac("0011.2233.4455") == "001122334455"
    assert normalize_mac("not-a-mac") is None
    assert normalize_mac(None) is None


def test_lldp_name_match_binds_host():
    switches = [
        SwitchTelemetry(
            node_id="core-sw",
            lldp=[LldpNeighbor(local_port="Gi1/0/1", neighbor_sys_name="app-1", neighbor_mgmt_addr="10.0.0.11")],
        )
    ]
    bindings = correlate_host_ports(_topology(), switches)
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.host_node_id == "app-1"
    assert binding.switch_node_id == "core-sw"
    assert binding.switch_port == "Gi1/0/1"
    assert binding.method == CorrelationMethod.LLDP
    assert binding.confidence == 0.82
    assert binding.evidence_quality == EvidenceQuality.SINGLE_SOURCE


def test_lldp_chassis_mac_match_outranks_name():
    switches = [
        SwitchTelemetry(
            node_id="core-sw",
            lldp=[LldpNeighbor(local_port="Gi1/0/1", neighbor_chassis_id="00:11:22:33:44:55")],
        )
    ]
    bindings = correlate_host_ports(_topology(), switches)
    assert bindings[0].confidence == 0.90
    assert bindings[0].host_identity == "001122334455"


def test_mac_table_only_binding():
    switches = [
        SwitchTelemetry(
            node_id="core-sw",
            mac_table=[MacTableEntry(mac="00:11:22:33:44:66", bridge_port=2, if_index=2)],
        )
    ]
    bindings = correlate_host_ports(_topology(), switches)
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.host_node_id == "db-1"
    assert binding.method == CorrelationMethod.MAC_TABLE
    assert binding.switch_port == "ifIndex 2"
    assert binding.if_index == 2
    assert binding.confidence == 0.75


def test_lldp_and_mac_corroborate():
    switches = [
        SwitchTelemetry(
            node_id="core-sw",
            lldp=[LldpNeighbor(local_port="Gi1/0/1", neighbor_sys_name="app-1", neighbor_chassis_id="00:11:22:33:44:55")],
            mac_table=[MacTableEntry(mac="00:11:22:33:44:55", bridge_port=1, if_index=1)],
        )
    ]
    bindings = correlate_host_ports(_topology(), switches)
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding.method == CorrelationMethod.LLDP_AND_MAC
    assert binding.confidence == 0.95
    assert binding.evidence_quality == EvidenceQuality.CORROBORATED
    assert binding.switch_port == "Gi1/0/1"  # LLDP port name preferred
    assert binding.if_index == 1            # ifIndex supplied by the MAC table
    assert any("Corroborated" in e for e in binding.evidence)


def test_no_match_yields_no_bindings():
    switches = [
        SwitchTelemetry(
            node_id="core-sw",
            lldp=[LldpNeighbor(local_port="Gi1/0/9", neighbor_sys_name="unknown-host")],
            mac_table=[MacTableEntry(mac="aa:bb:cc:dd:ee:ff", bridge_port=9, if_index=9)],
        )
    ]
    assert correlate_host_ports(_topology(), switches) == []


def test_bindings_sorted_and_multi_switch():
    switches = [
        SwitchTelemetry(node_id="core-sw", lldp=[
            LldpNeighbor(local_port="Gi1/0/2", neighbor_sys_name="db-1"),
            LldpNeighbor(local_port="Gi1/0/1", neighbor_sys_name="app-1"),
        ]),
    ]
    bindings = correlate_host_ports(_topology(), switches)
    assert [b.host_node_id for b in bindings] == ["app-1", "db-1"]


# --- Topology augmentation ---


def test_augment_adds_access_edges():
    topo = _topology()
    switches = [
        SwitchTelemetry(node_id="core-sw", lldp=[
            LldpNeighbor(local_port="Gi1/0/1", neighbor_sys_name="app-1"),
        ]),
    ]
    bindings = correlate_host_ports(topo, switches)
    augmented = augment_topology_with_bindings(topo, bindings)
    edge = next(e for e in augmented.edges if e.src == "app-1" and e.dst == "core-sw")
    assert edge.kind.value == "lan"
    assert edge.tags["switch_port"] == "Gi1/0/1"
    assert edge.tags["correlation"] == "lldp"


def test_augment_creates_missing_switch_node():
    topo = NetworkTopology(
        name="lab",
        nodes=[TopologyNode(node_id="app-1", role=NodeRole.AGENT)],
    )
    binding = HostPortBinding(
        host_node_id="app-1", host_identity="app-1", switch_node_id="new-sw",
        switch_port="Gi0/1", method=CorrelationMethod.LLDP, confidence=0.82,
    )
    augmented = augment_topology_with_bindings(topo, [binding])
    assert augmented.has_node("new-sw")
    assert augmented.node("new-sw").role == NodeRole.SWITCH
    assert any(e.src == "app-1" and e.dst == "new-sw" for e in augmented.edges)


def test_augment_refreshes_existing_edge_without_duplicating():
    topo = NetworkTopology(
        name="lab",
        nodes=[
            TopologyNode(node_id="core-sw", role=NodeRole.SWITCH),
            TopologyNode(node_id="app-1", role=NodeRole.AGENT),
        ],
        edges=[TopologyEdge(src="app-1", dst="core-sw", kind="lan", capacity_mbps=1000)],
    )
    binding = HostPortBinding(
        host_node_id="app-1", host_identity="app-1", switch_node_id="core-sw",
        switch_port="Gi1/0/1", method=CorrelationMethod.LLDP, confidence=0.82,
    )
    augmented = augment_topology_with_bindings(topo, [binding])
    matching = [e for e in augmented.edges if e.src == "app-1" and e.dst == "core-sw"]
    assert len(matching) == 1
    assert matching[0].capacity_mbps == 1000
    assert matching[0].tags["switch_port"] == "Gi1/0/1"


def test_device_correlation_request_validates():
    req = DeviceCorrelationRequest.model_validate(
        {"topology": "lab", "switches": [{"node_id": "core-sw", "lldp": [{"local_port": "1"}]}]}
    )
    assert req.switches[0].lldp[0].local_port == "1"
    with pytest.raises(Exception):
        DeviceCorrelationRequest.model_validate({"switches": [{"lldp": []}]})

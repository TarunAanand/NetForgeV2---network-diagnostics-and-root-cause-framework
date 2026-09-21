"""Host-to-switch-port correlation for M6.

Device-level telemetry (LLDP neighbors + the bridge MAC forwarding table) is
fused with the M3 validated topology to answer "which switch port is each host
on, and how sure are we?". The logic is pure so the controller can drive it from
an HTTP payload while tests feed it fixtures.

Two independent evidence sources are combined per (host, switch) pair:

- **LLDP** — the switch advertises the directly connected neighbor's system
  name / management address / chassis id on a specific local port.
- **MAC table** — the switch learned the host's MAC on a specific bridge port.

When both sources associate the same host with the same switch the binding is
*corroborated* and carries the highest confidence, mirroring the evidence-quality
model used across NetForge.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

from controller.topology import NetworkTopology, NodeRole, TopologyEdge, TopologyNode
from core.observation import EvidenceQuality
from ingest.lldp import LldpNeighbor
from ingest.snmp_counters import MacTableEntry

CONF_LLD_NAME = 0.82
CONF_LLD_MAC = 0.90
CONF_MAC_TABLE = 0.75
CONF_CORROBORATED = 0.95

_MAC_TAGS = {"mac", "mac_address", "chassis_id", "hardware", "hwaddr"}


class CorrelationMethod(str, Enum):
    LLDP = "lldp"
    MAC_TABLE = "mac_table"
    LLDP_AND_MAC = "lldp_and_mac"


class HostPortBinding(BaseModel):
    """A correlated host -> switch port association with provenance."""

    host_node_id: str
    host_identity: str
    switch_node_id: str
    switch_port: str
    if_index: int | None = None
    method: CorrelationMethod
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_quality: EvidenceQuality = EvidenceQuality.SINGLE_SOURCE
    evidence: list[str] = Field(default_factory=list)


class SwitchTelemetry(BaseModel):
    """LLDP + MAC-table capture reported for one switch node."""

    node_id: str = Field(min_length=1)
    lldp: list[LldpNeighbor] = Field(default_factory=list)
    mac_table: list[MacTableEntry] = Field(default_factory=list)


class DeviceCorrelationRequest(BaseModel):
    """HTTP payload that drives host-to-switch-port correlation."""

    topology: str | None = None
    switches: list[SwitchTelemetry] = Field(default_factory=list)


def normalize_mac(value: str | None) -> str | None:
    """Return a canonical 12-hex-digit lowercase MAC, or None if not MAC-shaped."""
    if not value:
        return None
    hexchars = re.sub(r"[^0-9a-fA-F]", "", str(value))
    return hexchars.lower() if len(hexchars) == 12 else None


def _host_identities(node: TopologyNode) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    macs: set[str] = set()
    for value in (node.node_id, node.label, node.address, node.agent_id):
        if value:
            names.add(str(value).strip().lower())
    for key, raw in node.tags.items():
        if not raw:
            continue
        text = str(raw).strip().lower()
        names.add(text)
        mac = normalize_mac(text)
        if mac and (key.lower() in _MAC_TAGS or mac):
            macs.add(mac)
    return names, macs


def _neighbor_identities(nb: LldpNeighbor) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    for value in (
        nb.neighbor_sys_name,
        nb.neighbor_mgmt_addr,
        nb.neighbor_port_id,
        nb.neighbor_chassis_id,
    ):
        if value:
            names.add(str(value).strip().lower())
    mac = normalize_mac(nb.neighbor_chassis_id)
    return names, ({mac} if mac else set())


def correlate_host_ports(
    topology: NetworkTopology, switches: list[SwitchTelemetry]
) -> list[HostPortBinding]:
    """Fuse LLDP + MAC-table telemetry into corroborated host-port bindings."""
    hosts = [
        node
        for node in topology.nodes
        if node.role in (NodeRole.HOST, NodeRole.AGENT, NodeRole.SERVICE) or node.agent_id
    ]
    host_index: list[tuple[TopologyNode, set[str], set[str]]] = [
        (node, *_host_identities(node)) for node in hosts
    ]

    # key: (host_node_id, switch_node_id) -> partial evidence per source
    partial: dict[tuple[str, str], dict] = {}

    for switch in switches:
        # --- LLDP evidence ---
        for nb in switch.lldp:
            nb_names, nb_macs = _neighbor_identities(nb)
            for node, host_names, host_macs in host_index:
                matched_identity = None
                confidence = 0.0
                if host_macs & nb_macs:
                    matched_identity = sorted(host_macs & nb_macs)[0]
                    confidence = CONF_LLD_MAC
                elif host_names & nb_names:
                    matched_identity = sorted(host_names & nb_names)[0]
                    confidence = CONF_LLD_NAME
                if matched_identity is None:
                    continue
                slot = partial.setdefault((node.node_id, switch.node_id), {})
                slot["lldp"] = {
                    "host_identity": matched_identity,
                    "switch_port": nb.local_port,
                    "confidence": confidence,
                    "evidence": (
                        f"LLDP on switch {switch.node_id} port {nb.local_port} reports neighbor "
                        f"'{matched_identity}' matching node {node.node_id}"
                    ),
                }

        # --- MAC-table evidence ---
        for entry in switch.mac_table:
            entry_mac = normalize_mac(entry.mac)
            if not entry_mac:
                continue
            for node, _host_names, host_macs in host_index:
                if entry_mac not in host_macs:
                    continue
                slot = partial.setdefault((node.node_id, switch.node_id), {})
                port = f"ifIndex {entry.if_index}" if entry.if_index is not None else f"bridge-port {entry.bridge_port}"
                slot["mac"] = {
                    "host_identity": entry_mac,
                    "switch_port": port,
                    "if_index": entry.if_index,
                    "confidence": CONF_MAC_TABLE,
                    "evidence": (
                        f"MAC table on {switch.node_id}: {entry_mac} learned on {port}"
                    ),
                }

    bindings: list[HostPortBinding] = []
    for (host_node_id, switch_node_id), slot in partial.items():
        lldp = slot.get("lldp")
        mac = slot.get("mac")
        if lldp and mac:
            evidence = [lldp["evidence"], mac["evidence"], "Corroborated by LLDP and MAC table"]
            confidence = CONF_CORROBORATED
            method = CorrelationMethod.LLDP_AND_MAC
            quality = EvidenceQuality.CORROBORATED
            switch_port = lldp["switch_port"]
            if_index = mac.get("if_index")
            host_identity = lldp["host_identity"]
        elif lldp:
            evidence = [lldp["evidence"]]
            confidence = lldp["confidence"]
            method = CorrelationMethod.LLDP
            quality = EvidenceQuality.SINGLE_SOURCE
            switch_port = lldp["switch_port"]
            if_index = None
            host_identity = lldp["host_identity"]
        else:
            evidence = [mac["evidence"]]
            confidence = mac["confidence"]
            method = CorrelationMethod.MAC_TABLE
            quality = EvidenceQuality.SINGLE_SOURCE
            switch_port = mac["switch_port"]
            if_index = mac.get("if_index")
            host_identity = mac["host_identity"]

        bindings.append(
            HostPortBinding(
                host_node_id=host_node_id,
                host_identity=host_identity,
                switch_node_id=switch_node_id,
                switch_port=switch_port,
                if_index=if_index,
                method=method,
                confidence=round(confidence, 2),
                evidence_quality=quality,
                evidence=evidence,
            )
        )

    bindings.sort(key=lambda b: (b.host_node_id, b.switch_node_id))
    return bindings


def augment_topology_with_bindings(
    topology: NetworkTopology, bindings: list[HostPortBinding]
) -> NetworkTopology:
    """Return a new topology with host->switch access edges added from bindings.

    Switch nodes referenced by a binding are created when absent. Existing
    host<->switch edges are refreshed in place (tagged with the port + method)
    rather than duplicated, so the result stays a valid, referentially-intact
    graph.
    """
    nodes = [node.model_copy(deep=True) for node in topology.nodes]
    known = {node.node_id for node in nodes}
    for binding in bindings:
        if binding.switch_node_id not in known:
            nodes.append(
                TopologyNode(
                    node_id=binding.switch_node_id,
                    role=NodeRole.SWITCH,
                    label=binding.switch_node_id,
                )
            )
            known.add(binding.switch_node_id)

    edge_tags = {
        (b.host_node_id, b.switch_node_id): {
            "switch_port": b.switch_port,
            "correlation": b.method.value,
            "confidence": f"{b.confidence:.2f}",
            **({"if_index": str(b.if_index)} if b.if_index is not None else {}),
        }
        for b in bindings
    }

    edges: list[TopologyEdge] = []
    seen: set[tuple[str, str]] = set()
    for edge in topology.edges:
        key = (edge.src, edge.dst)
        if key in edge_tags:
            merged = dict(edge.tags)
            merged.update(edge_tags[key])
            edges.append(TopologyEdge(src=edge.src, dst=edge.dst, kind=edge.kind, capacity_mbps=edge.capacity_mbps, tags=merged))
            seen.add(key)
        else:
            edges.append(edge.model_copy(deep=True))
    for binding in bindings:
        key = (binding.host_node_id, binding.switch_node_id)
        if key in seen:
            continue
        edges.append(
            TopologyEdge(src=binding.host_node_id, dst=binding.switch_node_id, kind="lan", tags=edge_tags[key])
        )

    return NetworkTopology(
        schema_version=topology.schema_version,
        name=topology.name,
        nodes=nodes,
        edges=edges,
    )

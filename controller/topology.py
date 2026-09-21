"""Validated network topology and service inventory models (M3).

The controller owns a *validated* view of the network graph: nodes (agents,
gateways, switches, hosts, targets), the edges that connect them, and a service
inventory that pins each monitored service to a topology node. Validation is
enforced declaratively by Pydantic so that downstream milestones (M4 vantage
selection) can trust referential integrity of the graph.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class NodeRole(str, Enum):
    """Coarse classification of a topology node."""

    AGENT = "agent"
    GATEWAY = "gateway"
    SWITCH = "switch"
    HOST = "host"
    TARGET = "target"
    SERVICE = "service"


class EdgeKind(str, Enum):
    """Link classification used later for path/last-mile reasoning."""

    LAN = "lan"
    WAN = "wan"
    LINK = "link"


class ServiceProtocol(str, Enum):
    TCP = "tcp"
    UDP = "udp"


class TopologyNode(BaseModel):
    """A single vertex in the validated network graph."""

    node_id: str = Field(min_length=1)
    label: str | None = None
    role: NodeRole = NodeRole.HOST
    agent_id: str | None = None
    address: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)


class TopologyEdge(BaseModel):
    """A directed edge between two known topology nodes."""

    src: str = Field(min_length=1)
    dst: str = Field(min_length=1)
    kind: EdgeKind = EdgeKind.LINK
    capacity_mbps: float | None = Field(default=None, gt=0)
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_self_loop(self) -> "TopologyEdge":
        if self.src == self.dst:
            raise ValueError("edge src and dst must reference different nodes")
        return self


class NetworkTopology(BaseModel):
    """A named, referentially-intact network graph.

    Validators guarantee unique node ids and that every edge endpoint resolves
    to a declared node, so consumers never observe dangling references.
    """

    schema_version: str = "1.0"
    name: str = Field(min_length=1)
    nodes: list[TopologyNode] = Field(default_factory=list)
    edges: list[TopologyEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> "NetworkTopology":
        node_ids = [node.node_id for node in self.nodes]
        duplicates = sorted({nid for nid in node_ids if node_ids.count(nid) > 1})
        if duplicates:
            raise ValueError(f"duplicate node_id(s): {', '.join(duplicates)}")

        known = set(node_ids)
        for edge in self.edges:
            if edge.src not in known:
                raise ValueError(f"edge references unknown src node: {edge.src}")
            if edge.dst not in known:
                raise ValueError(f"edge references unknown dst node: {edge.dst}")
        return self

    # --- Graph query helpers (used by M4 vantage selection) ---
    def has_node(self, node_id: str) -> bool:
        return any(node.node_id == node_id for node in self.nodes)

    def node(self, node_id: str) -> TopologyNode | None:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def neighbors(self, node_id: str) -> list[str]:
        """Undirected adjacency: nodes reachable from node_id in one hop."""
        result: list[str] = []
        for edge in self.edges:
            if edge.src == node_id and edge.dst not in result:
                result.append(edge.dst)
            elif edge.dst == node_id and edge.src not in result:
                result.append(edge.src)
        return result

    def nodes_with_role(self, role: NodeRole) -> list[TopologyNode]:
        return [node for node in self.nodes if node.role == role]

    def agent_nodes(self) -> list[TopologyNode]:
        """Nodes bound to a registered agent (explicit agent_id or agent role)."""
        return [
            node
            for node in self.nodes
            if node.agent_id or node.role == NodeRole.AGENT
        ]


class ServiceEndpoint(BaseModel):
    """A monitored service pinned to a topology node."""

    service_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    node_id: str | None = None
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    protocol: ServiceProtocol = ServiceProtocol.TCP
    tags: dict[str, str] = Field(default_factory=dict)


class ServiceInventory(BaseModel):
    """Bulk upload envelope for service endpoints with unique-id validation."""

    schema_version: str = "1.0"
    services: list[ServiceEndpoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ServiceInventory":
        ids = [svc.service_id for svc in self.services]
        duplicates = sorted({sid for sid in ids if ids.count(sid) > 1})
        if duplicates:
            raise ValueError(f"duplicate service_id(s): {', '.join(duplicates)}")
        return self


def topology_from_dict(data: dict[str, Any]) -> NetworkTopology:
    """Parse and validate a raw topology document (e.g. loaded from JSON)."""
    return NetworkTopology.model_validate(data)


def service_inventory_from_dict(data: dict[str, Any]) -> ServiceInventory:
    """Parse and validate a raw service inventory document."""
    return ServiceInventory.model_validate(data)

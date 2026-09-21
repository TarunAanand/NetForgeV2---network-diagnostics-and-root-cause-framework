"""Vantage-point selection for two-sided and third-vantage diagnosis (M4).

Selects which registered, topology-bound agents should probe a target service:

- *destination*: an agent co-located with the service's node (local ground truth);
- *source*: the primary client-side vantage;
- *third*: an independent tie-breaker, preferred to be topologically distant from
  the destination so it does not share the same access segment.

Together ``source`` + ``destination`` give a two-sided view; adding ``third``
enables disambiguation between a source-local fault, a shared-path fault, and a
target-side fault.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from analysis.multivantage import VantageRole
from controller.topology import NetworkTopology, ServiceEndpoint


class VantageAssignment(BaseModel):
    role: str
    node_id: str
    agent_id: str


class VantagePlan(BaseModel):
    target: str
    service_id: str | None = None
    topology: str | None = None
    assignments: list[VantageAssignment] = Field(default_factory=list)

    def agent_ids(self) -> list[str]:
        return [a.agent_id for a in self.assignments]

    def role_of(self, agent_id: str) -> str | None:
        for a in self.assignments:
            if a.agent_id == agent_id:
                return a.role
        return None

    def node_of(self, agent_id: str) -> str | None:
        for a in self.assignments:
            if a.agent_id == agent_id:
                return a.node_id
        return None


def _eligible_nodes(correlation: dict) -> dict[str, str]:
    """node_id -> agent_id for registered, enabled agent-bound nodes."""
    eligible: dict[str, str] = {}
    for node in correlation.get("nodes", []):
        if node.get("registered") and node.get("enabled") and node.get("agent_id"):
            eligible[node["node_id"]] = node["agent_id"]
    return eligible


def select_vantages(
    topology: NetworkTopology,
    service: ServiceEndpoint,
    correlation: dict,
    source_node_id: str | None = None,
) -> VantagePlan:
    """Build a deterministic vantage plan for diagnosing ``service``."""
    plan = VantagePlan(
        target=service.host,
        service_id=service.service_id,
        topology=topology.name,
    )
    eligible = _eligible_nodes(correlation)
    if not eligible:
        return plan

    dest_node = service.node_id
    assigned: set[str] = set()

    # 1. Destination-local vantage (two-sided ground truth), if the target node has an agent.
    if dest_node and dest_node in eligible:
        plan.assignments.append(
            VantageAssignment(role=VantageRole.DESTINATION.value, node_id=dest_node, agent_id=eligible[dest_node])
        )
        assigned.add(dest_node)

    # Candidates for external vantages, deterministically ordered.
    candidates = sorted(nid for nid in eligible if nid not in assigned)

    # 2. Source vantage: explicit request wins, else the first candidate.
    source_node: str | None = None
    if source_node_id and source_node_id in eligible and source_node_id not in assigned:
        source_node = source_node_id
    elif candidates:
        source_node = candidates[0]
    if source_node:
        plan.assignments.append(
            VantageAssignment(role=VantageRole.SOURCE.value, node_id=source_node, agent_id=eligible[source_node])
        )
        assigned.add(source_node)

    # 3. Third vantage: prefer a node NOT adjacent to the destination (topologically distant).
    remaining = [nid for nid in candidates if nid not in assigned]
    third_node: str | None = None
    if dest_node:
        dest_neighbors = set(topology.neighbors(dest_node))
        for nid in remaining:
            if nid not in dest_neighbors:
                third_node = nid
                break
    if third_node is None and remaining:
        third_node = remaining[0]
    if third_node:
        plan.assignments.append(
            VantageAssignment(role=VantageRole.THIRD.value, node_id=third_node, agent_id=eligible[third_node])
        )
        assigned.add(third_node)

    return plan

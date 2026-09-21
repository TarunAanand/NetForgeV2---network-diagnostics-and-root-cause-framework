"""Unit tests for the M3 validated topology and service inventory models."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from controller.topology import (
    NetworkTopology,
    NodeRole,
    ServiceInventory,
    TopologyEdge,
    TopologyNode,
    topology_from_dict,
)


def _topology() -> NetworkTopology:
    return NetworkTopology(
        name="lab",
        nodes=[
            TopologyNode(node_id="gw", role=NodeRole.GATEWAY),
            TopologyNode(node_id="app", role=NodeRole.AGENT, agent_id="agent-app"),
            TopologyNode(node_id="db", role=NodeRole.SERVICE, agent_id="agent-db"),
        ],
        edges=[
            TopologyEdge(src="app", dst="gw", kind="lan"),
            TopologyEdge(src="db", dst="gw", kind="lan"),
        ],
    )


def test_valid_topology_constructs():
    topo = _topology()
    assert topo.has_node("app")
    assert topo.node("gw").role == NodeRole.GATEWAY
    assert topo.node("missing") is None


def test_duplicate_node_ids_rejected():
    with pytest.raises(ValidationError) as exc:
        NetworkTopology(
            name="bad",
            nodes=[TopologyNode(node_id="a"), TopologyNode(node_id="a")],
        )
    assert "duplicate node_id" in str(exc.value)


def test_edge_referencing_unknown_node_rejected():
    with pytest.raises(ValidationError) as exc:
        NetworkTopology(
            name="bad",
            nodes=[TopologyNode(node_id="a")],
            edges=[TopologyEdge(src="a", dst="ghost")],
        )
    assert "unknown dst node" in str(exc.value)


def test_self_loop_edge_rejected():
    with pytest.raises(ValidationError) as exc:
        TopologyEdge(src="a", dst="a")
    assert "different nodes" in str(exc.value)


def test_neighbors_are_undirected():
    topo = _topology()
    assert sorted(topo.neighbors("gw")) == ["app", "db"]
    assert topo.neighbors("app") == ["gw"]
    assert topo.neighbors("lonely") == []


def test_agent_nodes_and_roles():
    topo = _topology()
    agent_ids = {n.node_id for n in topo.agent_nodes()}
    assert agent_ids == {"app", "db"}
    assert [n.node_id for n in topo.nodes_with_role(NodeRole.GATEWAY)] == ["gw"]


def test_service_inventory_rejects_duplicate_ids():
    with pytest.raises(ValidationError) as exc:
        ServiceInventory.model_validate(
            {
                "services": [
                    {"service_id": "s", "name": "a", "host": "h", "port": 1},
                    {"service_id": "s", "name": "b", "host": "h", "port": 2},
                ]
            }
        )
    assert "duplicate service_id" in str(exc.value)


def test_service_port_bounds_enforced():
    with pytest.raises(ValidationError):
        ServiceInventory.model_validate(
            {"services": [{"service_id": "s", "name": "a", "host": "h", "port": 70000}]}
        )


def test_example_topology_fixture_is_valid():
    data = json.loads(Path("examples/topology.json").read_text(encoding="utf-8"))
    topo = topology_from_dict(data)
    assert topo.name == "lab"
    # referential integrity holds and services pin to real nodes
    services = json.loads(Path("examples/services.json").read_text(encoding="utf-8"))
    for svc in services["services"]:
        assert topo.has_node(svc["node_id"])

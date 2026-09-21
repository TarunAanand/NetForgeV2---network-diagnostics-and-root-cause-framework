"""Service, store, and HTTP tests for the M3 topology & service inventory plane."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from controller.dispatch import AgentDispatcher
from controller.http_server import make_handler
from controller.models import AgentRegistration
from controller.service import ControllerService, UnknownNodeError, UnknownTopologyError
from controller.store import ControllerStore
from controller.topology import (
    NetworkTopology,
    NodeRole,
    ServiceEndpoint,
    ServiceInventory,
    TopologyEdge,
    TopologyNode,
)


class UnusedDispatcher:
    def fanout(self, agents, request):
        raise AssertionError("topology tests should not dispatch jobs")


def _service(tmp_path) -> ControllerService:
    return ControllerService(ControllerStore(tmp_path / "controller.db"), UnusedDispatcher())


def _topology() -> NetworkTopology:
    return NetworkTopology(
        name="lab",
        nodes=[
            TopologyNode(node_id="gw", role=NodeRole.GATEWAY),
            TopologyNode(node_id="app", role=NodeRole.AGENT, agent_id="agent-app"),
        ],
        edges=[TopologyEdge(src="app", dst="gw", kind="lan")],
    )


def test_import_and_get_topology_roundtrip(tmp_path):
    service = _service(tmp_path)
    service.import_topology(_topology())
    assert service.list_topology_names() == ["lab"]
    fetched = service.get_topology("lab")
    assert fetched.has_node("app")
    assert fetched.neighbors("gw") == ["app"]


def test_get_unknown_topology_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(UnknownTopologyError):
        service.get_topology("nope")


def test_register_services_validates_node_reference(tmp_path):
    service = _service(tmp_path)
    service.import_topology(_topology())
    good = ServiceInventory(
        services=[ServiceEndpoint(service_id="api", name="API", node_id="app", host="10.0.0.11", port=8080)]
    )
    service.register_services(good, topology_name="lab")
    assert [s.service_id for s in service.list_services()] == ["api"]

    bad = ServiceInventory(
        services=[ServiceEndpoint(service_id="ghost", name="G", node_id="missing", host="h", port=1)]
    )
    with pytest.raises(UnknownNodeError):
        service.register_services(bad, topology_name="lab")


def test_correlate_agents_maps_nodes_to_registry(tmp_path):
    service = _service(tmp_path)
    service.import_topology(_topology())
    service.register_agent(AgentRegistration(agent_id="agent-app", url="http://127.0.0.1:8081"))
    correlation = service.correlate_agents("lab")
    assert correlation["agent_node_count"] == 1
    assert correlation["registered_count"] == 1
    node = correlation["nodes"][0]
    assert node["node_id"] == "app" and node["registered"] is True


# --- HTTP layer ---


def _start_http(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "controller.db"), AgentDispatcher("agent-secret"))
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


def test_http_topology_and_services_flow(tmp_path):
    server, thread, base = _start_http(tmp_path)
    try:
        status, created = _call(base + "/v1/topology", "POST", json.loads(json.dumps(_topology().model_dump(mode="json"))))
        assert status == 201 and created["name"] == "lab"

        status, listing = _call(base + "/v1/topology")
        assert listing["topologies"] == ["lab"]

        status, fetched = _call(base + "/v1/topology/lab")
        assert fetched["name"] == "lab"

        inventory = {"services": [{"service_id": "api", "name": "API", "node_id": "app", "host": "10.0.0.11", "port": 8080}]}
        status, resp = _call(base + "/v1/services?topology=lab", "POST", inventory)
        assert status == 201 and resp["services"][0]["service_id"] == "api"

        status, resp = _call(base + "/v1/services")
        assert [s["service_id"] for s in resp["services"]] == ["api"]

        status, corr = _call(base + "/v1/topology/lab/agents")
        assert corr["topology"] == "lab" and corr["agent_node_count"] == 1
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_topology_validation_and_not_found(tmp_path):
    server, thread, base = _start_http(tmp_path)
    try:
        # invalid topology (dangling edge) -> 400
        bad = {"name": "bad", "nodes": [{"node_id": "a"}], "edges": [{"src": "a", "dst": "ghost"}]}
        try:
            _call(base + "/v1/topology", "POST", bad)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("invalid topology was accepted")

        # unknown topology name -> 404
        try:
            _call(base + "/v1/topology/missing")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("unknown topology returned a body")

        # service referencing unknown node -> 400
        _call(base + "/v1/topology", "POST", _topology().model_dump(mode="json"))
        inventory = {"services": [{"service_id": "x", "name": "X", "node_id": "ghost", "host": "h", "port": 1}]}
        try:
            _call(base + "/v1/services?topology=lab", "POST", inventory)
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
        else:
            raise AssertionError("service with unknown node was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_topology_requires_auth(tmp_path):
    server, thread, base = _start_http(tmp_path)
    try:
        try:
            _call(base + "/v1/topology", "GET", token="wrong")
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("topology endpoint accepted an unauthenticated request")
    finally:
        server.shutdown()
        thread.join(timeout=2)

"""Controller service and HTTP tests for M6 host-to-switch-port correlation."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from controller.correlation import CorrelationMethod, DeviceCorrelationRequest
from controller.http_server import make_handler
from controller.service import ControllerService, NoBindingsError, UnknownTopologyError
from controller.store import ControllerStore
from controller.topology import NetworkTopology, NodeRole, TopologyNode


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


def _correlation_payload() -> dict:
    return {
        "topology": "lab",
        "switches": [
            {
                "node_id": "core-sw",
                "lldp": [
                    {"local_port": "Gi1/0/1", "neighbor_sys_name": "app-1", "neighbor_chassis_id": "00:11:22:33:44:55"},
                    {"local_port": "Gi1/0/2", "neighbor_sys_name": "db-1"},
                ],
                "mac_table": [{"mac": "00:11:22:33:44:55", "bridge_port": 1, "if_index": 1}],
            }
        ],
    }


def _service(tmp_path) -> ControllerService:
    service = ControllerService(ControllerStore(tmp_path / "c.db"), dispatcher=object())
    service.import_topology(_topology())
    return service


# --- Service layer ---


def test_correlate_persists_bindings(tmp_path):
    service = _service(tmp_path)
    bindings = service.correlate_host_ports(DeviceCorrelationRequest.model_validate(_correlation_payload()))
    assert {b.host_node_id for b in bindings} == {"app-1", "db-1"}
    app = next(b for b in bindings if b.host_node_id == "app-1")
    assert app.method == CorrelationMethod.LLDP_AND_MAC  # corroborated
    db = next(b for b in bindings if b.host_node_id == "db-1")
    assert db.method == CorrelationMethod.LLDP
    # round-trip from the store
    stored = service.list_bindings("lab")
    assert len(stored) == 2


def test_correlate_replaces_previous_bindings(tmp_path):
    service = _service(tmp_path)
    service.correlate_host_ports(DeviceCorrelationRequest.model_validate(_correlation_payload()))
    assert len(service.list_bindings("lab")) == 2
    # A second, smaller correlation fully replaces the first.
    service.correlate_host_ports(
        DeviceCorrelationRequest.model_validate(
            {"topology": "lab", "switches": [{"node_id": "core-sw", "lldp": [{"local_port": "Gi1/0/1", "neighbor_sys_name": "app-1"}]}]}
        )
    )
    stored = service.list_bindings("lab")
    assert len(stored) == 1
    assert stored[0].host_node_id == "app-1"


def test_augment_topology_adds_edges_and_persists(tmp_path):
    service = _service(tmp_path)
    service.correlate_host_ports(DeviceCorrelationRequest.model_validate(_correlation_payload()))
    augmented = service.augment_topology("lab")
    assert any(e.src == "app-1" and e.dst == "core-sw" for e in augmented.edges)
    # The augmented graph is re-persisted.
    reloaded = service.get_topology("lab")
    assert any(e.src == "db-1" and e.dst == "core-sw" for e in reloaded.edges)


def test_augment_without_bindings_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(NoBindingsError):
        service.augment_topology("lab")


def test_correlate_unknown_topology_raises(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(UnknownTopologyError):
        service.correlate_host_ports(
            DeviceCorrelationRequest.model_validate({"topology": "missing", "switches": []})
        )


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


def test_http_correlate_bindings_and_augment(tmp_path):
    service = _service(tmp_path)
    server, thread, base = _start(service)
    try:
        payload = _correlation_payload()
        payload.pop("topology")  # topology comes from the path
        status, body = _call(base + "/v1/topology/lab/correlate", "POST", payload)
        assert status == 200 and body["count"] == 2

        status, listing = _call(base + "/v1/topology/lab/bindings")
        assert status == 200 and len(listing["bindings"]) == 2

        status, augmented = _call(base + "/v1/topology/lab/augment", "POST", {})
        assert status == 200
        edges = {(e["src"], e["dst"]) for e in augmented["edges"]}
        assert ("app-1", "core-sw") in edges
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_augment_without_bindings_is_409(tmp_path):
    service = _service(tmp_path)
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/topology/lab/augment", "POST", {})
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
        else:
            raise AssertionError("augment without bindings was accepted")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_bindings_unknown_topology_is_404(tmp_path):
    service = _service(tmp_path)
    server, thread, base = _start(service)
    try:
        try:
            _call(base + "/v1/topology/nope/bindings")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("bindings for unknown topology returned a body")
    finally:
        server.shutdown()
        thread.join(timeout=2)

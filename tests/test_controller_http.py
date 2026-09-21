import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from controller.http_server import make_handler
from controller.service import ControllerService
from controller.store import ControllerStore


class UnusedDispatcher:
    def fanout(self, agents, request):
        raise AssertionError("test should not dispatch a job")


def test_controller_http_auth_and_agent_registration(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "controller.db"), UnusedDispatcher())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, "controller-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps({"agent_id": "app-1", "url": "http://127.0.0.1:8081"}).encode()
        request = urllib.request.Request(base_url + "/v1/agents", data=body, method="POST", headers={"Authorization": "Bearer controller-secret", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=2) as response:
            registered = json.loads(response.read())
        assert registered["agent_id"] == "app-1"

        try:
            urllib.request.urlopen(base_url + "/v1/agents", timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("controller accepted an unauthenticated request")
    finally:
        server.shutdown()
        thread.join(timeout=2)

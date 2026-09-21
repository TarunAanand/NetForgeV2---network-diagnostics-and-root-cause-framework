import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from agent.config import AgentConfig
from agent.http_server import make_handler
from agent.service import AgentService


def test_health_endpoint_requires_authentication_and_returns_agent_identity():
    service = AgentService(AgentConfig(agent_id="app-1", hostname="app-1.lab", bearer_token="secret"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/v1/health"
    try:
        request = urllib.request.Request(url, headers={"Authorization": "Bearer secret"})
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["agent_id"] == "app-1"

        try:
            urllib.request.urlopen(url, timeout=2)
        except urllib.error.HTTPError as exc:
            assert exc.code == 401
        else:
            raise AssertionError("health endpoint accepted an unauthenticated request")
    finally:
        server.shutdown()
        thread.join(timeout=2)

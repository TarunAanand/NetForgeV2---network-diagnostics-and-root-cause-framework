"""Minimal standard-library HTTP transport for the NetForge demo agent."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from agent.models import ProbeRequest
from agent.service import AgentService, AuthorizationError, TargetNotAllowedError


def make_handler(service: AgentService):
    class AgentHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._dispatch(None)

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
            except json.JSONDecodeError:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "request body must be JSON"})
                return
            self._dispatch(body)

        def _dispatch(self, body: dict | None) -> None:
            try:
                service.authorize(self.headers.get("Authorization"))
                if self.command == "GET" and self.path == "/v1/health":
                    self._reply(HTTPStatus.OK, service.health())
                elif self.command == "GET" and self.path == "/v1/inventory":
                    self._reply(HTTPStatus.OK, service.inventory())
                elif self.command == "POST" and self.path == "/v1/probe":
                    request = ProbeRequest.model_validate(body or {})
                    observations = service.probe(request)
                    self._reply(HTTPStatus.OK, {"results": [item.model_dump(mode="json") for item in observations]})
                else:
                    self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            except AuthorizationError as exc:
                self._reply(HTTPStatus.UNAUTHORIZED, {"error": str(exc)})
            except (ValidationError, TargetNotAllowedError) as exc:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def _reply(self, status: HTTPStatus, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            return

    return AgentHandler


def serve(service: AgentService, host: str = "0.0.0.0", port: int = 8081) -> None:
    ThreadingHTTPServer((host, port), make_handler(service)).serve_forever()

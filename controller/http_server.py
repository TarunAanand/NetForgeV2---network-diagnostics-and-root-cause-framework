"""Authenticated HTTP API for the NetForge M2 controller."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from controller.models import AgentRegistration, DiagnosisRequest, FanoutJobRequest
from controller.correlation import DeviceCorrelationRequest
from controller.monitoring import AlertRule, AlertState, IncidentState, ScheduleEntry
from controller.service import (
    ControllerService,
    NoBindingsError,
    NoVantageAgentsError,
    UnknownAgentError,
    UnknownAlertRuleError,
    UnknownDiagnosisError,
    UnknownIncidentError,
    UnknownNodeError,
    UnknownScheduleError,
    UnknownServiceError,
    UnknownTopologyError,
)
from controller.topology import NetworkTopology, ServiceInventory


def make_handler(service: ControllerService, bearer_token: str):
    class ControllerHandler(BaseHTTPRequestHandler):
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

        def do_DELETE(self) -> None:
            self._dispatch(None)

        def _dispatch(self, body: dict | None) -> None:
            expected = f"Bearer {bearer_token}"
            if not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self._reply(HTTPStatus.UNAUTHORIZED, {"error": "valid bearer token required"})
                return
            parsed = urlparse(self.path)
            route = parsed.path
            query = parse_qs(parsed.query)
            try:
                if self.command == "GET" and route == "/v1/health":
                    self._reply(HTTPStatus.OK, {"status": "healthy", "service": "netforge-controller"})
                elif self.command == "GET" and route == "/v1/agents":
                    self._reply(HTTPStatus.OK, {"agents": [agent.model_dump(mode="json") for agent in service.list_agents()]})
                elif self.command == "POST" and route == "/v1/agents":
                    agent = service.register_agent(AgentRegistration.model_validate(body or {}))
                    self._reply(HTTPStatus.CREATED, agent.model_dump(mode="json"))
                elif self.command == "POST" and route == "/v1/jobs":
                    job = service.dispatch_job(FanoutJobRequest.model_validate(body or {}))
                    self._reply(HTTPStatus.OK, job.model_dump(mode="json"))
                elif self.command == "GET" and route.startswith("/v1/jobs/"):
                    job = service.get_job(route.rsplit("/", 1)[-1])
                    if job is None:
                        self._reply(HTTPStatus.NOT_FOUND, {"error": "job not found"})
                    else:
                        self._reply(HTTPStatus.OK, job.model_dump(mode="json"))
                elif self.command == "POST" and route == "/v1/topology":
                    topology = service.import_topology(NetworkTopology.model_validate(body or {}))
                    self._reply(HTTPStatus.CREATED, topology.model_dump(mode="json"))
                elif self.command == "GET" and route == "/v1/topology":
                    self._reply(HTTPStatus.OK, {"topologies": service.list_topology_names()})
                elif (
                    self.command == "GET"
                    and route.startswith("/v1/topology/")
                    and route.endswith("/agents")
                    and len(route.split("/")) == 5
                ):
                    name = route.split("/")[3]
                    self._reply(HTTPStatus.OK, service.correlate_agents(name))
                elif (
                    self.command == "POST"
                    and route.startswith("/v1/topology/")
                    and route.endswith("/correlate")
                    and len(route.split("/")) == 5
                ):
                    name = route.split("/")[3]
                    payload = dict(body or {})
                    payload["topology"] = name
                    bindings = service.correlate_host_ports(DeviceCorrelationRequest.model_validate(payload))
                    self._reply(
                        HTTPStatus.OK,
                        {"count": len(bindings), "bindings": [b.model_dump(mode="json") for b in bindings]},
                    )
                elif (
                    self.command == "GET"
                    and route.startswith("/v1/topology/")
                    and route.endswith("/bindings")
                    and len(route.split("/")) == 5
                ):
                    name = route.split("/")[3]
                    bindings = service.list_bindings(name)
                    self._reply(
                        HTTPStatus.OK,
                        {"bindings": [b.model_dump(mode="json") for b in bindings]},
                    )
                elif (
                    self.command == "POST"
                    and route.startswith("/v1/topology/")
                    and route.endswith("/augment")
                    and len(route.split("/")) == 5
                ):
                    name = route.split("/")[3]
                    topology = service.augment_topology(name)
                    self._reply(HTTPStatus.OK, topology.model_dump(mode="json"))
                elif self.command == "GET" and route.startswith("/v1/topology/"):
                    name = route.rsplit("/", 1)[-1]
                    topology = service.get_topology(name)
                    self._reply(HTTPStatus.OK, topology.model_dump(mode="json"))
                elif self.command == "POST" and route == "/v1/services":
                    topology_name = query.get("topology", [None])[0]
                    inventory = ServiceInventory.model_validate(body or {})
                    services = service.register_services(inventory, topology_name=topology_name)
                    self._reply(
                        HTTPStatus.CREATED,
                        {"services": [svc.model_dump(mode="json") for svc in services]},
                    )
                elif self.command == "GET" and route == "/v1/services":
                    self._reply(
                        HTTPStatus.OK,
                        {"services": [svc.model_dump(mode="json") for svc in service.list_services()]},
                    )
                elif self.command == "POST" and route == "/v1/diagnose":
                    report = service.diagnose_service(DiagnosisRequest.model_validate(body or {}))
                    self._reply(HTTPStatus.OK, report.model_dump(mode="json"))
                elif self.command == "GET" and route == "/v1/diagnoses":
                    self._reply(
                        HTTPStatus.OK,
                        {"diagnoses": [r.model_dump(mode="json") for r in service.list_diagnoses()]},
                    )
                elif self.command == "GET" and route.startswith("/v1/diagnoses/"):
                    report = service.get_diagnosis(route.rsplit("/", 1)[-1])
                    self._reply(HTTPStatus.OK, report.model_dump(mode="json"))
                elif self.command == "POST" and route == "/v1/schedules":
                    schedule = service.create_schedule(ScheduleEntry.model_validate(body or {}))
                    self._reply(HTTPStatus.CREATED, schedule.model_dump(mode="json"))
                elif self.command == "GET" and route == "/v1/schedules":
                    service_id = query.get("service_id", [None])[0]
                    self._reply(
                        HTTPStatus.OK,
                        {"schedules": [s.model_dump(mode="json") for s in service.list_schedules(service_id)]},
                    )
                elif self.command == "DELETE" and route.startswith("/v1/schedules/"):
                    service.delete_schedule(route.rsplit("/", 1)[-1])
                    self._reply(HTTPStatus.OK, {"deleted": True})
                elif self.command == "POST" and route == "/v1/monitor/run":
                    reports = service.run_due_schedules()
                    self._reply(
                        HTTPStatus.OK,
                        {"ran": len(reports), "reports": [r.model_dump(mode="json") for r in reports]},
                    )
                elif self.command == "POST" and route == "/v1/alert-rules":
                    rule = service.register_alert_rule(AlertRule.model_validate(body or {}))
                    self._reply(HTTPStatus.CREATED, rule.model_dump(mode="json"))
                elif self.command == "GET" and route == "/v1/alert-rules":
                    self._reply(
                        HTTPStatus.OK,
                        {"rules": [r.model_dump(mode="json") for r in service.list_alert_rules()]},
                    )
                elif self.command == "DELETE" and route.startswith("/v1/alert-rules/"):
                    service.delete_alert_rule(route.rsplit("/", 1)[-1])
                    self._reply(HTTPStatus.OK, {"deleted": True})
                elif self.command == "GET" and route == "/v1/alerts":
                    service_id = query.get("service_id", [None])[0]
                    state_str = query.get("state", [None])[0]
                    state = AlertState(state_str) if state_str else None
                    self._reply(
                        HTTPStatus.OK,
                        {"alerts": [a.model_dump(mode="json") for a in service.list_alerts(service_id, state)]},
                    )
                elif self.command == "GET" and route == "/v1/incidents":
                    service_id = query.get("service_id", [None])[0]
                    state_str = query.get("state", [None])[0]
                    state = IncidentState(state_str) if state_str else None
                    self._reply(
                        HTTPStatus.OK,
                        {"incidents": [i.model_dump(mode="json") for i in service.list_incidents(service_id, state)]},
                    )
                elif self.command == "GET" and route == "/v1/bindings":
                    topology_name = query.get("topology", [None])[0]
                    self._reply(
                        HTTPStatus.OK,
                        {"bindings": [b.model_dump(mode="json") for b in service.list_bindings(topology_name)]},
                    )
                elif self.command == "POST" and route.startswith("/v1/incidents/") and route.endswith("/ack"):
                    incident_id = route.split("/")[3]
                    incident = service.acknowledge_incident(incident_id)
                    self._reply(HTTPStatus.OK, incident.model_dump(mode="json"))
                elif self.command == "POST" and route.startswith("/v1/incidents/") and route.endswith("/resolve"):
                    incident_id = route.split("/")[3]
                    incident = service.resolve_incident(incident_id)
                    self._reply(HTTPStatus.OK, incident.model_dump(mode="json"))
                elif self.command == "GET" and route.startswith("/v1/incidents/"):
                    incident = service.get_incident(route.rsplit("/", 1)[-1])
                    self._reply(HTTPStatus.OK, incident.model_dump(mode="json"))
                else:
                    self._reply(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
            except (UnknownTopologyError, UnknownDiagnosisError) as exc:
                self._reply(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except (UnknownServiceError, UnknownScheduleError, UnknownAlertRuleError, UnknownIncidentError) as exc:
                self._reply(HTTPStatus.NOT_FOUND, {"error": str(exc)})
            except NoVantageAgentsError as exc:
                self._reply(HTTPStatus.CONFLICT, {"error": str(exc)})
            except NoBindingsError as exc:
                self._reply(HTTPStatus.CONFLICT, {"error": str(exc)})
            except (ValidationError, UnknownAgentError, UnknownNodeError, ValueError) as exc:
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

    return ControllerHandler


def serve(service: ControllerService, bearer_token: str, host: str = "0.0.0.0", port: int = 8080) -> None:
    ThreadingHTTPServer((host, port), make_handler(service, bearer_token)).serve_forever()

"""HTTP client for a running NetForge controller (CLI -> control plane).

A thin, dependency-free (stdlib ``urllib``) wrapper over the controller's ``/v1``
REST API. It mirrors the bearer-token transport used by
:class:`controller.dispatch.AgentDispatcher` and returns validated Pydantic
models so CLI commands can render them directly.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from analysis.multivantage import MultiVantageReport
from controller.correlation import HostPortBinding
from controller.monitoring import Alert, AlertRule, Incident, ScheduleEntry
from controller.topology import NetworkTopology, ServiceEndpoint


class ControllerAPIError(RuntimeError):
    """Raised for transport failures and non-2xx controller responses."""

    def __init__(self, status: int, message: str):
        super().__init__(f"controller API error ({status}): {message}")
        self.status = status
        self.message = message


class ControllerClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- Transport ---
    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
        url = self.base_url + path
        if query:
            clean = {k: v for k, v in query.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")
            try:
                detail = json.loads(detail).get("error", detail)
            except json.JSONDecodeError:
                pass
            raise ControllerAPIError(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise ControllerAPIError(0, str(exc.reason)) from exc

    # --- Health / registry ---
    def health(self) -> dict:
        return self._request("GET", "/v1/health")

    def list_agents(self) -> list[dict]:
        return self._request("GET", "/v1/agents").get("agents", [])

    def register_agent(self, agent_id: str, url: str, topology_tags: dict[str, str] | None = None) -> dict:
        body = {"agent_id": agent_id, "url": url, "topology_tags": topology_tags or {}}
        return self._request("POST", "/v1/agents", body)

    # --- Topology & services ---
    def list_topology_names(self) -> list[str]:
        return self._request("GET", "/v1/topology").get("topologies", [])

    def import_topology(self, topology: dict) -> NetworkTopology:
        return NetworkTopology.model_validate(self._request("POST", "/v1/topology", topology))

    def register_services(self, inventory: dict, topology: str | None = None) -> list[ServiceEndpoint]:
        resp = self._request("POST", "/v1/services", inventory, query={"topology": topology})
        return [ServiceEndpoint.model_validate(svc) for svc in resp.get("services", [])]

    def list_services(self) -> list[ServiceEndpoint]:
        resp = self._request("GET", "/v1/services")
        return [ServiceEndpoint.model_validate(svc) for svc in resp.get("services", [])]

    # --- Diagnosis (M4) ---
    def diagnose(
        self,
        service_id: str,
        probe_type: str | None = None,
        count: int = 3,
        source_node_id: str | None = None,
        topology: str | None = None,
    ) -> MultiVantageReport:
        body: dict[str, Any] = {"service_id": service_id, "count": count}
        if probe_type:
            body["probe_type"] = probe_type
        if source_node_id:
            body["source_node_id"] = source_node_id
        if topology:
            body["topology"] = topology
        return MultiVantageReport.model_validate(self._request("POST", "/v1/diagnose", body))

    def list_diagnoses(self) -> list[MultiVantageReport]:
        resp = self._request("GET", "/v1/diagnoses")
        return [MultiVantageReport.model_validate(r) for r in resp.get("diagnoses", [])]

    # --- Monitoring (M5) ---
    def run_monitor(self) -> list[MultiVantageReport]:
        resp = self._request("POST", "/v1/monitor/run", {})
        return [MultiVantageReport.model_validate(r) for r in resp.get("reports", [])]

    def list_schedules(self, service_id: str | None = None) -> list[ScheduleEntry]:
        resp = self._request("GET", "/v1/schedules", query={"service_id": service_id})
        return [ScheduleEntry.model_validate(s) for s in resp.get("schedules", [])]

    def create_schedule(
        self,
        service_id: str,
        interval_seconds: float,
        count: int = 3,
        probe_type: str | None = None,
        source_node_id: str | None = None,
        topology: str | None = None,
    ) -> ScheduleEntry:
        body: dict[str, Any] = {
            "service_id": service_id,
            "interval_seconds": interval_seconds,
            "count": count,
        }
        if probe_type:
            body["probe_type"] = probe_type
        if source_node_id:
            body["source_node_id"] = source_node_id
        if topology:
            body["topology"] = topology
        return ScheduleEntry.model_validate(self._request("POST", "/v1/schedules", body))

    def delete_schedule(self, schedule_id: str) -> bool:
        return bool(self._request("DELETE", f"/v1/schedules/{schedule_id}").get("deleted"))

    def list_alert_rules(self) -> list[AlertRule]:
        resp = self._request("GET", "/v1/alert-rules")
        return [AlertRule.model_validate(r) for r in resp.get("rules", [])]

    def create_alert_rule(
        self,
        name: str = "any-unhealthy",
        min_confidence: float = 0.0,
        severity: str = "high",
        service_id: str | None = None,
        fire_on_localizations: list[str] | None = None,
    ) -> AlertRule:
        body: dict[str, Any] = {"name": name, "min_confidence": min_confidence, "severity": severity}
        if service_id:
            body["service_id"] = service_id
        if fire_on_localizations:
            body["fire_on_localizations"] = fire_on_localizations
        return AlertRule.model_validate(self._request("POST", "/v1/alert-rules", body))

    def list_alerts(self, service_id: str | None = None, state: str | None = None) -> list[Alert]:
        resp = self._request("GET", "/v1/alerts", query={"service_id": service_id, "state": state})
        return [Alert.model_validate(a) for a in resp.get("alerts", [])]

    def list_incidents(self, service_id: str | None = None, state: str | None = None) -> list[Incident]:
        resp = self._request("GET", "/v1/incidents", query={"service_id": service_id, "state": state})
        return [Incident.model_validate(i) for i in resp.get("incidents", [])]

    def get_incident(self, incident_id: str) -> Incident:
        return Incident.model_validate(self._request("GET", f"/v1/incidents/{incident_id}"))

    def ack_incident(self, incident_id: str) -> Incident:
        return Incident.model_validate(self._request("POST", f"/v1/incidents/{incident_id}/ack", {}))

    def resolve_incident(self, incident_id: str) -> Incident:
        return Incident.model_validate(self._request("POST", f"/v1/incidents/{incident_id}/resolve", {}))

    # --- Device correlation (M6) ---
    def correlate(self, topology: str, switches: list[dict]) -> list[HostPortBinding]:
        resp = self._request("POST", f"/v1/topology/{topology}/correlate", {"switches": switches})
        return [HostPortBinding.model_validate(b) for b in resp.get("bindings", [])]

    def list_bindings(self, topology: str | None = None) -> list[HostPortBinding]:
        path = f"/v1/topology/{topology}/bindings" if topology else "/v1/bindings"
        resp = self._request("GET", path)
        return [HostPortBinding.model_validate(b) for b in resp.get("bindings", [])]

    def augment_topology(self, topology: str) -> NetworkTopology:
        return NetworkTopology.model_validate(self._request("POST", f"/v1/topology/{topology}/augment", {}))

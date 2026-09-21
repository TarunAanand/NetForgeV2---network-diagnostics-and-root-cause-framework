"""Agent business logic; HTTP transport is deliberately kept separate."""

from __future__ import annotations

import hmac
import time
from typing import Any

import psutil

from agent.config import AgentConfig
from agent.models import ProbeRequest, ProbeType, TARGETED_PROBES
from core.observation import EvidenceQuality
from core.remote_observation import AgentObservation, RemoteObservationContext
from core.result import DiagnosticResult
from diagnostics.host.dns import get_dns_servers, resolve_hostname
from diagnostics.host.gateway import check_gateway_reachability
from diagnostics.host.interface import inspect_interfaces
from diagnostics.host.packet_loss import ping_host
from diagnostics.host.routing import inspect_routing_table
from diagnostics.host.tcp_udp import test_tcp
from diagnostics.path.traceroute import traceroute_to_result


class AuthorizationError(PermissionError):
    pass


class TargetNotAllowedError(ValueError):
    pass


class AgentService:
    def __init__(self, config: AgentConfig):
        self.config = config

    def authorize(self, authorization: str | None) -> None:
        expected = f"Bearer {self.config.bearer_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise AuthorizationError("valid bearer token required")

    def health(self) -> dict[str, Any]:
        return {"status": "healthy", "agent_id": self.config.agent_id, "schema_version": "1.0"}

    def inventory(self) -> dict[str, Any]:
        stats = psutil.net_if_stats()
        return {
            "agent_id": self.config.agent_id,
            "hostname": self.config.hostname,
            "topology_tags": self.config.topology_tags,
            "interfaces": [
                {"name": name, "is_up": value.isup, "speed_mbps": value.speed, "mtu": value.mtu}
                for name, value in stats.items()
            ],
            "capabilities": [probe.value for probe in ProbeType],
        }

    def probe(self, request: ProbeRequest) -> list[AgentObservation]:
        self._authorize_target(request)
        started = time.perf_counter()
        results = self._run_probe(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        return [self._to_observation(result, request, duration_ms) for result in results]

    def _authorize_target(self, request: ProbeRequest) -> None:
        if request.probe_type not in TARGETED_PROBES:
            return
        if not self.config.allowed_targets:
            raise TargetNotAllowedError("agent has no allowed targets configured")
        if request.target not in self.config.allowed_targets:
            raise TargetNotAllowedError(f"target is not allowed: {request.target}")

    def _run_probe(self, request: ProbeRequest) -> list[DiagnosticResult]:
        if request.probe_type == ProbeType.ICMP:
            return [ping_host(request.target or "", count=request.count)]
        if request.probe_type == ProbeType.TCP:
            return [test_tcp(request.target or "", request.port or 0, timeout=request.timeout_seconds)]
        if request.probe_type == ProbeType.DNS:
            result = resolve_hostname(request.target or "")
            result.metrics["dns_servers"] = get_dns_servers()
            return [result]
        if request.probe_type == ProbeType.TRACEROUTE:
            return [traceroute_to_result(request.target or "", max_hops=request.max_hops)]
        if request.probe_type == ProbeType.INTERFACES:
            return inspect_interfaces()
        if request.probe_type == ProbeType.ROUTE:
            return [inspect_routing_table()]
        if request.probe_type == ProbeType.GATEWAY:
            return [check_gateway_reachability(count=request.count)]
        raise ValueError(f"unsupported probe: {request.probe_type}")

    def _to_observation(self, result: DiagnosticResult, request: ProbeRequest, duration_ms: float) -> AgentObservation:
        raw_output = result.metadata.get("raw_output")
        raw_evidence = {"raw_output": raw_output} if raw_output is not None else {}
        quality = EvidenceQuality.PARSED_EXTERNAL if raw_evidence else EvidenceQuality.SINGLE_SOURCE
        return AgentObservation(
            context=RemoteObservationContext(
                agent_id=self.config.agent_id,
                hostname=self.config.hostname,
                probe_type=request.probe_type.value,
                target=request.target or result.target,
                source_interface=request.source_interface,
                sample_count=request.count if request.probe_type in {ProbeType.ICMP, ProbeType.TRACEROUTE} else 1,
                duration_ms=duration_ms,
                topology_tags=self.config.topology_tags,
                raw_evidence=raw_evidence,
                evidence_quality=quality,
            ),
            result=result,
        )

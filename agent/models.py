"""Validated request types for the NetForge agent API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class ProbeType(str, Enum):
    ICMP = "icmp"
    TCP = "tcp"
    DNS = "dns"
    TRACEROUTE = "traceroute"
    INTERFACES = "interfaces"
    ROUTE = "route"
    GATEWAY = "gateway"


TARGETED_PROBES = {ProbeType.ICMP, ProbeType.TCP, ProbeType.DNS, ProbeType.TRACEROUTE}


class ProbeRequest(BaseModel):
    probe_type: ProbeType
    target: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    count: int = Field(default=3, ge=1, le=20)
    max_hops: int = Field(default=20, ge=1, le=64)
    timeout_seconds: float = Field(default=5, gt=0, le=30)
    source_interface: str | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "ProbeRequest":
        if self.probe_type in TARGETED_PROBES and not self.target:
            raise ValueError(f"target is required for {self.probe_type.value}")
        if self.probe_type == ProbeType.TCP and self.port is None:
            raise ValueError("port is required for tcp probes")
        if self.probe_type != ProbeType.TCP and self.port is not None:
            raise ValueError("port is only valid for tcp probes")
        return self

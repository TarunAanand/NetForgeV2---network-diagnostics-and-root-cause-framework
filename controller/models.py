"""Models used by the controller API and persistence layer."""

from __future__ import annotations

from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field

from agent.models import ProbeRequest, ProbeType
from core.remote_observation import AgentObservation


class AgentRegistration(BaseModel):
    agent_id: str = Field(min_length=1)
    url: AnyHttpUrl
    topology_tags: dict[str, str] = Field(default_factory=dict)


class RegisteredAgent(AgentRegistration):
    enabled: bool = True
    last_seen_at: float | None = None


class FanoutJobRequest(BaseModel):
    agent_ids: list[str] = Field(min_length=1, max_length=32)
    probe: ProbeRequest


class FanoutJob(BaseModel):
    job_id: str
    created_at: float
    completed_at: float | None = None
    request: FanoutJobRequest
    status: str
    observations: dict[str, list[AgentObservation]] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiagnosisRequest(BaseModel):
    """Multi-vantage diagnosis request for a registered service (M4).

    ``probe_type`` defaults to the service protocol when omitted (TCP services are
    probed on their port; everything else falls back to ICMP host reachability).
    """

    service_id: str = Field(min_length=1)
    probe_type: ProbeType | None = None
    count: int = Field(default=3, ge=1, le=20)
    source_node_id: str | None = None
    topology: str | None = None

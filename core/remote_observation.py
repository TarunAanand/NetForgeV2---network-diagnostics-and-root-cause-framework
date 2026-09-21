"""Strict observation envelope used at the remote agent/controller boundary."""

from __future__ import annotations

import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from core.observation import EVIDENCE_QUALITY_CONFIDENCE, EvidenceQuality
from core.result import DiagnosticResult


class RemoteObservationContext(BaseModel):
    """Required provenance for a remotely collected diagnostic result."""

    schema_version: str = "1.0"
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    agent_id: str = Field(min_length=1)
    hostname: str = Field(min_length=1)
    probe_type: str = Field(min_length=1)
    target: str | None = None
    source_ip: str | None = None
    source_interface: str | None = None
    target_interface: str | None = None
    sample_count: int = Field(default=1, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    topology_tags: dict[str, str] = Field(default_factory=dict)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime.datetime) -> datetime.datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(datetime.timezone.utc)

    @model_validator(mode="after")
    def set_evidence_quality_confidence(self) -> "RemoteObservationContext":
        expected = EVIDENCE_QUALITY_CONFIDENCE[self.evidence_quality]
        if self.confidence is None:
            self.confidence = expected
        elif self.confidence != expected:
            raise ValueError("confidence must match evidence_quality")
        return self


class AgentObservation(BaseModel):
    """Controller-ready observation with context and separately-derived metrics."""

    context: RemoteObservationContext
    result: DiagnosticResult

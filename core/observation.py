"""Versioned contract exchanged between NetForge agents and controller."""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class EvidenceQuality(str, Enum):
    """Reliability of the collection method, independent of probe status."""

    UNKNOWN = "unknown"
    SINGLE_SOURCE = "single_source"
    PARSED_EXTERNAL = "parsed_external"
    CORROBORATED = "corroborated"
    VERIFIED = "verified"


EVIDENCE_QUALITY_CONFIDENCE: dict[EvidenceQuality, float] = {
    EvidenceQuality.UNKNOWN: 0.0,
    EvidenceQuality.SINGLE_SOURCE: 0.45,
    EvidenceQuality.PARSED_EXTERNAL: 0.60,
    EvidenceQuality.CORROBORATED: 0.80,
    EvidenceQuality.VERIFIED: 0.95,
}


class ObservationContext(BaseModel):
    """Origin, scope, and evidence metadata required for remote observations.

    The controller will require this model in M2.  The local CLI is intentionally
    not forced to populate it until each legacy collector is migrated.
    """

    schema_version: str = "1.0"
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    agent_id: str = Field(min_length=1)
    hostname: str = Field(min_length=1)
    source_ip: str | None = None
    source_interface: str | None = None
    target: str | None = None
    target_interface: str | None = None
    probe_type: str = Field(min_length=1)
    sample_count: int = Field(default=1, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    topology_tags: dict[str, str] = Field(default_factory=dict)
    raw_evidence: dict[str, Any] = Field(default_factory=dict)
    evidence_quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def derive_confidence_from_evidence_quality(self) -> "ObservationContext":
        if self.confidence is None:
            self.confidence = EVIDENCE_QUALITY_CONFIDENCE[self.evidence_quality]
        return self

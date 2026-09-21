from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DiagnosticStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DiagnosticResult(BaseModel):
    """
    Standard result returned by every NetForge diagnostic module.
    """

    module: str
    category: str

    status: DiagnosticStatus
    severity: Severity

    summary: str

    target: str | None = None

    metrics: dict[str, Any] = Field(default_factory=dict)

    evidence: list[str] = Field(default_factory=list)

    warnings: list[str] = Field(default_factory=list)

    errors: list[str] = Field(default_factory=list)

    metadata: dict[str, Any] = Field(default_factory=dict)
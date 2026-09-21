"""Monitoring models and pure decision logic for M5.

Covers the four M5 concerns as composable, side-effect-free pieces that the
controller service orchestrates:

- **Scheduling** (:class:`ScheduleEntry`) — periodic multi-vantage diagnoses.
- **Baselines** — reachability history is tracked via ``storage.history``; a
  diagnosis that drops below its rolling baseline is flagged as a *regression*.
- **Alerts** (:class:`AlertRule` -> :class:`Alert`) — declarative rules that fire
  on unhealthy localizations above a confidence floor.
- **Incidents** (:class:`Incident`) — alerts for a service are grouped into an
  incident with an open/acknowledged/resolved lifecycle.
"""

from __future__ import annotations

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from agent.models import ProbeType
from analysis.multivantage import FaultLocalization, MultiVantageReport
from core.result import DiagnosticStatus, Severity

# Severity ordering used to compute an incident's aggregate severity.
_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}

# Localizations considered "not healthy" for default alerting.
UNHEALTHY_LOCALIZATIONS = [
    FaultLocalization.SOURCE_SIDE,
    FaultLocalization.VANTAGE_ISOLATED,
    FaultLocalization.TARGET_SIDE,
    FaultLocalization.PATH_SHARED,
    FaultLocalization.INCONCLUSIVE,
]


class AlertState(str, Enum):
    FIRING = "firing"
    RESOLVED = "resolved"


class IncidentState(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ScheduleEntry(BaseModel):
    """A periodic diagnosis schedule bound to one service."""

    schedule_id: str = Field(default_factory=lambda: str(uuid4()))
    service_id: str = Field(min_length=1)
    interval_seconds: float = Field(gt=0)
    enabled: bool = True
    count: int = Field(default=3, ge=1, le=20)
    probe_type: ProbeType | None = None
    source_node_id: str | None = None
    topology: str | None = None
    created_at: float = 0.0
    last_run_at: float | None = None
    next_run_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0


class AlertRule(BaseModel):
    """Declarative condition under which a diagnosis raises an alert."""

    rule_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Unhealthy localization"
    service_id: str | None = None  # None => applies to all services
    fire_on_localizations: list[FaultLocalization] = Field(default_factory=list)  # empty => any unhealthy
    min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    severity: Severity = Severity.HIGH
    enabled: bool = True


class Alert(BaseModel):
    """A fired (or resolved) alert instance tied to a diagnosis."""

    alert_id: str = Field(default_factory=lambda: str(uuid4()))
    rule_id: str
    service_id: str | None = None
    target: str
    localization: FaultLocalization
    status: DiagnosticStatus
    confidence: float
    severity: Severity
    diagnosis_id: str
    message: str
    regression: bool = False
    state: AlertState = AlertState.FIRING
    created_at: float = 0.0
    updated_at: float = 0.0
    resolved_at: float | None = None


class Incident(BaseModel):
    """A lifecycle-tracked grouping of alerts for one service."""

    incident_id: str = Field(default_factory=lambda: str(uuid4()))
    service_id: str | None = None
    target: str
    state: IncidentState = IncidentState.OPEN
    severity: Severity = Severity.HIGH
    summary: str = ""
    alert_ids: list[str] = Field(default_factory=list)
    opened_at: float = 0.0
    updated_at: float = 0.0
    resolved_at: float | None = None


def default_alert_rule(service_id: str | None = None, severity: Severity = Severity.HIGH) -> AlertRule:
    """A rule that fires on any unhealthy localization at or above 50% confidence."""
    return AlertRule(
        name="Any unhealthy localization",
        service_id=service_id,
        fire_on_localizations=list(UNHEALTHY_LOCALIZATIONS),
        min_confidence=0.5,
        severity=severity,
    )


def is_healthy(report: MultiVantageReport) -> bool:
    return report.localization == FaultLocalization.HEALTHY


def rule_matches(rule: AlertRule, report: MultiVantageReport) -> bool:
    """True when ``rule`` should fire for ``report`` (never fires when healthy)."""
    if not rule.enabled or is_healthy(report):
        return False
    if rule.service_id is not None and rule.service_id != report.service_id:
        return False
    if report.confidence < rule.min_confidence:
        return False
    if rule.fire_on_localizations and report.localization not in rule.fire_on_localizations:
        return False
    return True


def matching_rules(rules: list[AlertRule], report: MultiVantageReport) -> list[AlertRule]:
    return [rule for rule in rules if rule_matches(rule, report)]


def max_severity(severities: list[Severity]) -> Severity:
    if not severities:
        return Severity.INFO
    return max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0))


def is_due(schedule: ScheduleEntry, now: float) -> bool:
    """A schedule is due when enabled and its next_run_at is unset or reached."""
    if not schedule.enabled:
        return False
    return schedule.next_run_at is None or schedule.next_run_at <= now


def advance_schedule(
    schedule: ScheduleEntry, now: float, error: str | None = None
) -> ScheduleEntry:
    """Return a copy with last/next run timestamps and failure state advanced."""
    updated = schedule.model_copy(deep=True)
    updated.last_run_at = now
    updated.next_run_at = now + schedule.interval_seconds
    updated.last_error = error
    updated.consecutive_failures = schedule.consecutive_failures + 1 if error else 0
    return updated


def build_alert(
    rule: AlertRule,
    report: MultiVantageReport,
    now: float,
    regression: bool = False,
) -> Alert:
    message = f"[{rule.name}] {report.verdict}"
    if regression:
        message = f"{message} (regression vs baseline)"
    return Alert(
        rule_id=rule.rule_id,
        service_id=report.service_id,
        target=report.target,
        localization=report.localization,
        status=report.status,
        confidence=report.confidence,
        severity=rule.severity,
        diagnosis_id=report.diagnosis_id,
        message=message,
        regression=regression,
        state=AlertState.FIRING,
        created_at=now,
        updated_at=now,
    )

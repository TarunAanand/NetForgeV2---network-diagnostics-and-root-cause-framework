"""Two-sided and third-vantage fault localization with evidence trails (M4).

Given the same probe collected from several network vantage points, this module
localizes the fault (source-side, target-side, shared-path, or isolated-vantage)
and records an ordered, provenance-rich evidence trail. It builds on the M0
observation contract: every observation carries an ``EvidenceQuality`` that maps
to a numeric confidence, and the synthesized verdict confidence is scaled by the
quality of the evidence that actually drove the decision.

The logic is deliberately pure (depends only on ``core``), so the controller can
feed it observations from real fan-out jobs while tests feed it fixtures.
"""

from __future__ import annotations

import datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from core.observation import EVIDENCE_QUALITY_CONFIDENCE, EvidenceQuality
from core.result import DiagnosticStatus


class VantageRole(str, Enum):
    """Role a vantage point plays in a two-sided / third-vantage diagnosis."""

    DESTINATION = "destination"  # agent co-located with the target (local truth)
    SOURCE = "source"            # primary client-side vantage
    THIRD = "third"              # independent tie-breaker vantage
    PEER = "peer"                # any additional vantage


# Deterministic ordering so evidence trails are stable and testable.
_ROLE_ORDER = {"destination": 0, "source": 1, "third": 2, "peer": 3}


class FaultLocalization(str, Enum):
    HEALTHY = "healthy"
    SOURCE_SIDE = "source_side"
    VANTAGE_ISOLATED = "vantage_isolated"
    TARGET_SIDE = "target_side"
    PATH_SHARED = "path_shared"
    INCONCLUSIVE = "inconclusive"


class VantageObservation(BaseModel):
    """A single probe result attributed to one vantage point."""

    agent_id: str
    node_id: str | None = None
    role: str = VantageRole.PEER.value
    target: str
    probe_type: str
    status: DiagnosticStatus
    evidence_quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def derive_confidence(self) -> "VantageObservation":
        if self.confidence is None:
            self.confidence = EVIDENCE_QUALITY_CONFIDENCE[self.evidence_quality]
        return self

    @property
    def vantage(self) -> str:
        return self.node_id or self.agent_id


class EvidenceStep(BaseModel):
    """One link in the evidence trail leading to the verdict."""

    order: int
    vantage: str
    role: str
    observation: str
    status: DiagnosticStatus
    evidence_quality: EvidenceQuality
    confidence: float


class MultiVantageReport(BaseModel):
    """Localized verdict plus the full, ordered evidence trail."""

    diagnosis_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )
    target: str
    service_id: str | None = None
    topology: str | None = None
    localization: FaultLocalization
    status: DiagnosticStatus
    verdict: str
    confidence: float
    vantage_count: int
    reachable_count: int
    failed_count: int
    degraded_count: int = 0
    agent_errors: dict[str, str] = Field(default_factory=dict)
    observations: list[VantageObservation] = Field(default_factory=list)
    evidence_trail: list[EvidenceStep] = Field(default_factory=list)


def _clamp(value: float) -> float:
    return max(0.05, min(0.99, round(value, 2)))


def _confidence_of(obs: VantageObservation) -> float:
    return obs.confidence if obs.confidence is not None else EVIDENCE_QUALITY_CONFIDENCE[obs.evidence_quality]


def _verdict(
    localization: FaultLocalization,
    target: str,
    dest: VantageObservation | None,
    unreach: list[VantageObservation],
    vantage_count: int,
) -> str:
    failed_names = ", ".join(o.vantage for o in unreach)
    if localization == FaultLocalization.HEALTHY:
        return f"{target} is reachable from all {vantage_count} vantage point(s); no fault localized."
    if localization == FaultLocalization.TARGET_SIDE:
        if dest is not None and dest.status == DiagnosticStatus.FAILED:
            return (
                f"{target} is unreachable even from its own host agent ({dest.vantage}) and from "
                f"{len(unreach)} vantage(s) total — fault localized to the target/service itself."
            )
        return (
            f"{target} is unreachable from all {vantage_count} vantage points with no destination-local "
            f"agent to confirm; the target/service is the most likely common cause (a broad upstream "
            f"event cannot be ruled out without a control probe)."
        )
    if localization == FaultLocalization.SOURCE_SIDE:
        return (
            f"{target} is reachable from other vantage(s) but not from the source ({failed_names}) — "
            f"fault localized to the source host or its access path, not the target."
        )
    if localization == FaultLocalization.VANTAGE_ISOLATED:
        return (
            f"{target} is reachable from most vantage(s); only {failed_names} fails — isolated "
            f"vantage/access issue rather than a target or shared-path fault."
        )
    if localization == FaultLocalization.PATH_SHARED:
        return (
            f"{target} responds but is unreachable/degraded from multiple vantage(s) ({failed_names}) — "
            f"fault localized to the shared network path or target-side inbound filtering (ACL/firewall)."
        )
    return f"Insufficient or conflicting vantage evidence to localize a fault for {target}."


def localize_fault(
    observations: list[VantageObservation],
    target: str,
    service_id: str | None = None,
    topology: str | None = None,
    agent_errors: dict[str, str] | None = None,
) -> MultiVantageReport:
    """Correlate multi-vantage observations into a localized verdict + trail."""
    agent_errors = agent_errors or {}
    obs_sorted = sorted(
        observations,
        key=lambda o: (_ROLE_ORDER.get(o.role, 3), o.vantage),
    )
    vantage_count = len(obs_sorted)

    if vantage_count == 0:
        return MultiVantageReport(
            target=target,
            service_id=service_id,
            topology=topology,
            localization=FaultLocalization.INCONCLUSIVE,
            status=DiagnosticStatus.UNKNOWN,
            verdict=_verdict(FaultLocalization.INCONCLUSIVE, target, None, [], 0),
            confidence=0.05,
            vantage_count=0,
            reachable_count=0,
            failed_count=0,
            agent_errors=agent_errors,
            observations=[],
            evidence_trail=[
                EvidenceStep(
                    order=1,
                    vantage="controller",
                    role="verdict",
                    observation="No vantage observations were collected.",
                    status=DiagnosticStatus.UNKNOWN,
                    evidence_quality=EvidenceQuality.UNKNOWN,
                    confidence=0.05,
                )
            ],
        )

    dest = next((o for o in obs_sorted if o.role == VantageRole.DESTINATION.value), None)
    source = next((o for o in obs_sorted if o.role == VantageRole.SOURCE.value), None)
    third = next((o for o in obs_sorted if o.role == VantageRole.THIRD.value), None)

    unreach = [o for o in obs_sorted if o.status == DiagnosticStatus.FAILED]
    degrad = [o for o in obs_sorted if o.status == DiagnosticStatus.DEGRADED]
    reachable = [o for o in obs_sorted if o.status == DiagnosticStatus.HEALTHY]

    localization, base_conf, drivers = _decide(
        dest, source, third, unreach, degrad, reachable, obs_sorted, vantage_count
    )

    evidence_factor = (
        sum(_confidence_of(o) for o in drivers) / len(drivers) if drivers else 0.5
    )
    confidence = _clamp(base_conf * (0.6 + 0.4 * evidence_factor))

    status = _overall_status(localization, unreach, degrad)
    verdict_text = _verdict(localization, target, dest, unreach, vantage_count)

    trail = _build_trail(obs_sorted, verdict_text, status, confidence, vantage_count)

    return MultiVantageReport(
        target=target,
        service_id=service_id,
        topology=topology,
        localization=localization,
        status=status,
        verdict=verdict_text,
        confidence=confidence,
        vantage_count=vantage_count,
        reachable_count=len(reachable),
        failed_count=len(unreach),
        degraded_count=len(degrad),
        agent_errors=agent_errors,
        observations=obs_sorted,
        evidence_trail=trail,
    )


def _decide(
    dest: VantageObservation | None,
    source: VantageObservation | None,
    third: VantageObservation | None,
    unreach: list[VantageObservation],
    degrad: list[VantageObservation],
    reachable: list[VantageObservation],
    all_obs: list[VantageObservation],
    vantage_count: int,
) -> tuple[FaultLocalization, float, list[VantageObservation]]:
    """Return (localization, base_confidence, driving_observations)."""
    # No fault at all.
    if not unreach and not degrad:
        return FaultLocalization.HEALTHY, 0.9, all_obs

    # Degradation only (everything still reachable).
    if not unreach and degrad:
        if len(degrad) == 1:
            only = degrad[0]
            loc = (
                FaultLocalization.SOURCE_SIDE
                if only.role == VantageRole.SOURCE.value
                else FaultLocalization.VANTAGE_ISOLATED
            )
            return loc, 0.6, degrad
        return FaultLocalization.PATH_SHARED, 0.6, degrad

    # At least one vantage cannot reach the target.
    if dest is not None and dest.status == DiagnosticStatus.FAILED:
        return FaultLocalization.TARGET_SIDE, 0.9, [dest] + unreach

    if dest is not None and dest.status == DiagnosticStatus.HEALTHY:
        src_fail = source is not None and source.status == DiagnosticStatus.FAILED
        third_fail = third is not None and third.status == DiagnosticStatus.FAILED
        if src_fail and third_fail:
            return FaultLocalization.PATH_SHARED, 0.75, [dest, source, third]
        if src_fail:
            drivers = [dest, source] + ([third] if third else [])
            return FaultLocalization.SOURCE_SIDE, 0.82, drivers
        if third_fail:
            return FaultLocalization.VANTAGE_ISOLATED, 0.78, [dest, third]
        loc = FaultLocalization.PATH_SHARED if len(unreach) > 1 else FaultLocalization.VANTAGE_ISOLATED
        return loc, 0.7, [dest] + unreach

    # No destination-local vantage: reason purely from external vantages.
    if len(unreach) == vantage_count:
        return FaultLocalization.TARGET_SIDE, 0.6, unreach
    if len(unreach) == 1:
        only = unreach[0]
        loc = (
            FaultLocalization.SOURCE_SIDE
            if only.role == VantageRole.SOURCE.value
            else FaultLocalization.VANTAGE_ISOLATED
        )
        return loc, 0.72, all_obs
    return FaultLocalization.PATH_SHARED, 0.65, unreach + reachable


def _overall_status(
    localization: FaultLocalization,
    unreach: list[VantageObservation],
    degrad: list[VantageObservation],
) -> DiagnosticStatus:
    if localization == FaultLocalization.HEALTHY:
        return DiagnosticStatus.HEALTHY
    if localization == FaultLocalization.INCONCLUSIVE:
        return DiagnosticStatus.UNKNOWN
    if unreach:
        return DiagnosticStatus.FAILED
    if degrad:
        return DiagnosticStatus.DEGRADED
    return DiagnosticStatus.UNKNOWN


def _build_trail(
    obs_sorted: list[VantageObservation],
    verdict: str,
    status: DiagnosticStatus,
    confidence: float,
    vantage_count: int,
) -> list[EvidenceStep]:
    trail: list[EvidenceStep] = []
    for idx, obs in enumerate(obs_sorted, 1):
        detail = obs.summary
        if obs.evidence:
            detail = f"{detail} | {obs.evidence[0]}" if detail else obs.evidence[0]
        trail.append(
            EvidenceStep(
                order=idx,
                vantage=obs.vantage,
                role=obs.role,
                observation=detail,
                status=obs.status,
                evidence_quality=obs.evidence_quality,
                confidence=round(_confidence_of(obs), 2),
            )
        )
    trail.append(
        EvidenceStep(
            order=len(trail) + 1,
            vantage="controller",
            role="verdict",
            observation=verdict,
            status=status,
            evidence_quality=(
                EvidenceQuality.CORROBORATED if vantage_count >= 2 else EvidenceQuality.SINGLE_SOURCE
            ),
            confidence=confidence,
        )
    )
    return trail

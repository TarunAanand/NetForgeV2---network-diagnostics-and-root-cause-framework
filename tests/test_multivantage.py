"""Unit tests for M4 two-sided / third-vantage fault localization."""

from analysis.multivantage import (
    FaultLocalization,
    VantageObservation,
    localize_fault,
)
from core.observation import EvidenceQuality
from core.result import DiagnosticStatus

TARGET = "10.0.0.21"


def _obs(role, status, quality=EvidenceQuality.SINGLE_SOURCE, node=None):
    return VantageObservation(
        agent_id=f"agent-{role}",
        node_id=node or role,
        role=role,
        target=TARGET,
        probe_type="tcp",
        status=status,
        evidence_quality=quality,
        summary=f"{role} sees {status.value}",
        evidence=[f"{role} probe -> {status.value}"],
    )


H = DiagnosticStatus.HEALTHY
F = DiagnosticStatus.FAILED
D = DiagnosticStatus.DEGRADED


def test_all_reachable_is_healthy():
    report = localize_fault(
        [_obs("destination", H), _obs("source", H), _obs("third", H)], target=TARGET
    )
    assert report.localization == FaultLocalization.HEALTHY
    assert report.status == H
    assert report.reachable_count == 3 and report.failed_count == 0


def test_destination_local_failure_is_target_side():
    report = localize_fault(
        [_obs("destination", F), _obs("source", F), _obs("third", F)], target=TARGET
    )
    assert report.localization == FaultLocalization.TARGET_SIDE
    assert report.status == F
    assert "target/service itself" in report.verdict


def test_all_external_fail_without_destination_is_target_side():
    report = localize_fault([_obs("source", F), _obs("third", F)], target=TARGET)
    assert report.localization == FaultLocalization.TARGET_SIDE
    assert report.vantage_count == 2


def test_only_source_fails_is_source_side():
    report = localize_fault(
        [_obs("destination", H), _obs("source", F), _obs("third", H)], target=TARGET
    )
    assert report.localization == FaultLocalization.SOURCE_SIDE
    assert report.status == F


def test_source_and_third_fail_with_healthy_destination_is_shared_path():
    report = localize_fault(
        [_obs("destination", H), _obs("source", F), _obs("third", F)], target=TARGET
    )
    assert report.localization == FaultLocalization.PATH_SHARED
    assert "shared network path" in report.verdict


def test_only_third_fails_is_isolated_vantage():
    report = localize_fault(
        [_obs("destination", H), _obs("source", H), _obs("third", F)], target=TARGET
    )
    assert report.localization == FaultLocalization.VANTAGE_ISOLATED


def test_single_degradation_is_degraded_not_failed():
    report = localize_fault(
        [_obs("destination", H), _obs("source", D), _obs("third", H)], target=TARGET
    )
    assert report.status == D
    assert report.localization == FaultLocalization.SOURCE_SIDE
    assert report.degraded_count == 1


def test_multiple_degradation_is_shared_path():
    report = localize_fault(
        [_obs("destination", H), _obs("source", D), _obs("third", D)], target=TARGET
    )
    assert report.localization == FaultLocalization.PATH_SHARED
    assert report.status == D


def test_no_destination_single_source_failure_is_source_side():
    report = localize_fault([_obs("source", F), _obs("third", H)], target=TARGET)
    assert report.localization == FaultLocalization.SOURCE_SIDE


def test_no_destination_single_third_failure_is_isolated():
    report = localize_fault([_obs("source", H), _obs("third", F)], target=TARGET)
    assert report.localization == FaultLocalization.VANTAGE_ISOLATED


def test_empty_observations_are_inconclusive():
    report = localize_fault([], target=TARGET)
    assert report.localization == FaultLocalization.INCONCLUSIVE
    assert report.status == DiagnosticStatus.UNKNOWN
    assert report.vantage_count == 0
    assert len(report.evidence_trail) == 1


def test_evidence_trail_is_ordered_and_ends_with_verdict():
    report = localize_fault(
        [_obs("third", H), _obs("source", F), _obs("destination", H)], target=TARGET
    )
    trail = report.evidence_trail
    assert [s.order for s in trail] == [1, 2, 3, 4]
    # destination is ordered first regardless of input order
    assert [s.role for s in trail] == ["destination", "source", "third", "verdict"]
    assert trail[-1].vantage == "controller"
    assert trail[-1].observation == report.verdict


def test_higher_evidence_quality_yields_higher_confidence():
    low = localize_fault(
        [
            _obs("destination", H, EvidenceQuality.SINGLE_SOURCE),
            _obs("source", F, EvidenceQuality.SINGLE_SOURCE),
            _obs("third", H, EvidenceQuality.SINGLE_SOURCE),
        ],
        target=TARGET,
    )
    high = localize_fault(
        [
            _obs("destination", H, EvidenceQuality.VERIFIED),
            _obs("source", F, EvidenceQuality.VERIFIED),
            _obs("third", H, EvidenceQuality.VERIFIED),
        ],
        target=TARGET,
    )
    assert high.confidence > low.confidence
    assert 0.05 <= low.confidence <= 0.99
    assert 0.05 <= high.confidence <= 0.99


def test_agent_errors_are_recorded_on_report():
    report = localize_fault(
        [_obs("source", F), _obs("third", H)],
        target=TARGET,
        agent_errors={"agent-x": "connection refused"},
    )
    assert report.agent_errors == {"agent-x": "connection refused"}

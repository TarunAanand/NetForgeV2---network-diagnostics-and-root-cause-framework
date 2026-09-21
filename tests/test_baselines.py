from analysis.context import AnalysisContext
from analysis.rules.baseline_rules import SuddenDegradationRule
from analysis.rules.mesh_rules import MeshPartitionRule
from core.result import DiagnosticResult, DiagnosticStatus, Severity
from storage.baselines import record_and_compare
from storage.history import HistoryStore


def test_baseline_seeds_then_detects_deviation(tmp_path, monkeypatch):
    db = tmp_path / "b.db"
    store = HistoryStore(db)

    first = record_and_compare("host", "latency:x", "avg_ms", 10.0, store=store)
    assert first.metrics["deviated"] is False

    # Seed a few more normal samples so rolling mean stays ~10
    for _ in range(4):
        record_and_compare("host", "latency:x", "avg_ms", 10.0, store=store)

    bad = record_and_compare("host", "latency:x", "avg_ms", 40.0, store=store)
    assert bad.metrics["deviated"] is True
    assert bad.status in {DiagnosticStatus.DEGRADED, DiagnosticStatus.FAILED}


def test_sudden_degradation_rule():
    results = [
        DiagnosticResult(
            module="baseline_delta",
            category="host",
            status=DiagnosticStatus.DEGRADED,
            severity=Severity.MEDIUM,
            summary="avg_ms spiked",
            target="1.1.1.1",
            metrics={
                "deviated": True,
                "ratio": 3.0,
                "sample_count": 5,
                "metric": "avg_ms",
                "current": 30,
                "baseline": 10,
            },
        )
    ]
    issue = SuddenDegradationRule().evaluate(AnalysisContext(results))
    assert issue is not None
    assert issue.rule_id == "RULE_SUDDEN_DEGRADATION"


def test_mesh_partition_rule():
    results = [
        DiagnosticResult(
            module="mesh_summary",
            category="mesh",
            status=DiagnosticStatus.DEGRADED,
            severity=Severity.HIGH,
            summary="1/2 mesh targets failed",
            metrics={"failed_count": 1, "target_count": 2, "targets": ["a", "b"]},
        ),
        DiagnosticResult(
            module="mesh_ping",
            category="mesh",
            status=DiagnosticStatus.FAILED,
            severity=Severity.HIGH,
            summary="fail",
            target="a",
        ),
    ]
    issue = MeshPartitionRule().evaluate(AnalysisContext(results))
    assert issue is not None
    assert issue.rule_id == "RULE_MESH_PARTITION"

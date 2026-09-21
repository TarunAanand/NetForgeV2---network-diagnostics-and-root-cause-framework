from core.result import DiagnosticResult, DiagnosticStatus, Severity
from analysis.evidence import EvidenceCorrelator


def _result(**kwargs):
    defaults = dict(
        module="test",
        category="host",
        status=DiagnosticStatus.HEALTHY,
        severity=Severity.INFO,
        summary="ok",
    )
    defaults.update(kwargs)
    return DiagnosticResult(**defaults)


def test_evidence_correlator_treats_isolated_hop_loss_as_anomaly():
    results = [
        _result(module="connectivity", target="1.1.1.1", status=DiagnosticStatus.HEALTHY),
        _result(
            module="traceroute",
            target="1.1.1.1",
            metrics={
                "hops": [
                    {"hop": 1, "address": "192.168.1.1", "loss_percent": 0},
                    {"hop": 2, "address": "10.0.0.1", "loss_percent": 80},
                    {"hop": 3, "address": "1.1.1.1", "loss_percent": 0},
                ]
            },
        ),
        _result(module="packet_loss", metrics={"packet_loss_percent": 10.0}, status=DiagnosticStatus.HEALTHY),
    ]

    anomalies = EvidenceCorrelator.correlate(results)
    assert any(a.kind.value == "intermediate_hop_loss" for a in anomalies)
    assert all(a.confirmed is False for a in anomalies)


def test_evidence_correlator_keeps_path_change_as_unconfirmed_without_failure():
    results = [
        _result(module="connectivity", target="1.1.1.1", status=DiagnosticStatus.HEALTHY),
        _result(module="path_change", metrics={"changed": True}, status=DiagnosticStatus.DEGRADED, summary="path changed"),
    ]

    anomalies = EvidenceCorrelator.correlate(results)
    assert any(a.kind.value == "path_change" for a in anomalies)
    assert all(a.confirmed is False for a in anomalies)

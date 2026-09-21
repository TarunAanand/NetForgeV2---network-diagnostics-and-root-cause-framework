"""Baseline comparison helpers for latency, loss, and utilization."""

from __future__ import annotations

from core.result import DiagnosticResult, DiagnosticStatus, Severity
from storage.history import HistoryStore


def record_and_compare(
    domain: str,
    key: str,
    metric_name: str,
    current_value: float,
    *,
    higher_is_worse: bool = True,
    warn_ratio: float = 1.5,
    fail_ratio: float = 2.5,
    store: HistoryStore | None = None,
) -> DiagnosticResult:
    """
    Persist a metric sample and compare it to a robust rolling baseline.
    Emits a DiagnosticResult with module 'baseline_delta'.
    """
    store = store or HistoryStore()
    stats = store.rolling_stats(domain, key, metric_name, limit=20)
    baseline = float(stats["median"]) if stats else None
    sample_count = int(stats["sample_count"]) if stats else 0
    store.save_snapshot(
        domain=domain,
        key=key,
        payload={metric_name: current_value},
        fingerprint=None,
    )

    if baseline is None or baseline == 0:
        return DiagnosticResult(
            module="baseline_delta",
            category=domain,
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"Baseline seeded for {domain}/{key}.{metric_name}={current_value}",
            target=key,
            metrics={
                "metric": metric_name,
                "current": current_value,
                "baseline": None,
                "ratio": None,
                "deviated": False,
                "sample_count": sample_count,
            },
            evidence=[f"First samples stored for {metric_name}"],
        )

    ratio = current_value / baseline if baseline else None
    worsened = False
    if ratio is not None and sample_count >= 5:
        abs_delta = abs(current_value - baseline)
        abs_guard = 1.0 if "percent" in metric_name.lower() else 5.0
        mad = float(stats["mad"]) if stats else 0.0
        meaningful_delta = (
            abs_delta >= abs_guard
            or current_value >= 10.0
        ) and (
            current_value > float(stats["p95"])
            or (mad > 0.0 and abs_delta >= 3.0 * mad)
        )
        if higher_is_worse:
            worsened = ratio >= warn_ratio and meaningful_delta
        else:
            worsened = ratio <= (1.0 / warn_ratio) if warn_ratio else False

    if (
        ratio is not None
        and higher_is_worse
        and sample_count >= 5
        and ratio >= fail_ratio
        and (
            abs(current_value - baseline) >= (1.0 if "percent" in metric_name.lower() else 5.0)
            or current_value >= 10.0
        )
        and (
            current_value > float(stats["p95"])
            or (
                float(stats["mad"]) > 0.0
                and abs(current_value - baseline) >= 3.0 * float(stats["mad"])
            )
        )
    ):
        status, severity = DiagnosticStatus.FAILED, Severity.HIGH
    elif worsened:
        status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
    else:
        status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

    return DiagnosticResult(
        module="baseline_delta",
        category=domain,
        status=status,
        severity=severity,
        summary=(
            f"{metric_name} for {key}: current={current_value:.2f}, "
            f"baseline={baseline:.2f}, ratio={ratio:.2f}"
            if ratio is not None
            else f"{metric_name} for {key}: current={current_value:.2f}"
        ),
        target=key,
        metrics={
            "metric": metric_name,
            "current": current_value,
            "baseline": round(baseline, 4),
            "ratio": round(ratio, 3) if ratio is not None else None,
            "deviated": worsened,
            "higher_is_worse": higher_is_worse,
            "sample_count": sample_count,
            "baseline_mad": round(float(stats["mad"]), 4) if stats else None,
            "baseline_p95": round(float(stats["p95"]), 4) if stats else None,
        },
        evidence=[
            f"Rolling baseline median ({metric_name})={baseline:.4f}",
            f"Current value={current_value:.4f}",
        ],
        warnings=[f"{metric_name} significantly worse than baseline"] if worsened else [],
    )


def compare_probe_metrics(results: list[DiagnosticResult]) -> list[DiagnosticResult]:
    """
    For latency / packet_loss / link_utilization results, emit baseline_delta observations.
    """
    extras: list[DiagnosticResult] = []
    store = HistoryStore()

    for r in results:
        key = r.target or r.module
        if r.module == "latency" and r.metrics.get("avg_ms") is not None:
            extras.append(
                record_and_compare(
                    "host",
                    f"latency:{key}",
                    "avg_ms",
                    float(r.metrics["avg_ms"]),
                    higher_is_worse=True,
                    store=store,
                )
            )
        elif r.module == "packet_loss" and r.metrics.get("packet_loss_percent") is not None:
            extras.append(
                record_and_compare(
                    "host",
                    f"loss:{key}",
                    "packet_loss_percent",
                    float(r.metrics["packet_loss_percent"]),
                    higher_is_worse=True,
                    store=store,
                )
            )
        elif r.module == "link_utilization" and r.metrics.get("util_percent") is not None:
            extras.append(
                record_and_compare(
                    "link",
                    f"util:{key}",
                    "util_percent",
                    float(r.metrics["util_percent"]),
                    higher_is_worse=True,
                    store=store,
                )
            )
    return extras

from __future__ import annotations

import statistics
from dataclasses import dataclass


def calculate_rfc_jitter(latencies: list[float]) -> float:
    """
    Interarrival jitter / packet delay variation (RFC 3550 / RFC 1889).
    Mean absolute difference between consecutive latency samples.
    """
    if len(latencies) < 2:
        return 0.0
    diffs = [abs(latencies[i] - latencies[i - 1]) for i in range(1, len(latencies))]
    return sum(diffs) / len(diffs)


@dataclass(frozen=True)
class LatencySummary:
    min_ms: float
    avg_ms: float
    max_ms: float
    jitter_ms: float
    std_dev_ms: float
    samples: int


def summarize_latencies(latencies: list[float]) -> LatencySummary:
    """Compute min/avg/max/jitter/stddev for a latency sample list."""
    if not latencies:
        return LatencySummary(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    return LatencySummary(
        min_ms=round(min(latencies), 2),
        avg_ms=round(statistics.mean(latencies), 2),
        max_ms=round(max(latencies), 2),
        jitter_ms=round(calculate_rfc_jitter(latencies), 2),
        std_dev_ms=round(statistics.pstdev(latencies), 2) if len(latencies) > 1 else 0.0,
        samples=len(latencies),
    )

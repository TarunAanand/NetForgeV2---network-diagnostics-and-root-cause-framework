from __future__ import annotations

from core.result import DiagnosticResult
from diagnostics.path.traceroute import (
    detect_path_change,
    measure_hop_metrics,
    traceroute_to_result,
)


def collect_path_diagnostics(
    target: str = "1.1.1.1",
    max_hops: int = 30,
    hop_probes: int = 1,
) -> list[DiagnosticResult]:
    """Silent collection of path-layer DiagnosticResults."""
    results: list[DiagnosticResult] = []
    trace = traceroute_to_result(target, max_hops=max_hops)
    results.append(trace)
    results.append(detect_path_change(target, trace))
    if hop_probes > 1:
        results.append(measure_hop_metrics(target, probes=hop_probes, max_hops=max_hops))
    return results

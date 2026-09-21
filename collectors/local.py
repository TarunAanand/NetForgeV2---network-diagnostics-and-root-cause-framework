"""Local collector orchestration across diagnostic domains."""

from __future__ import annotations

from core.result import DiagnosticResult
from diagnostics.host.collector import collect_host_diagnostics
from diagnostics.link.collector import (
    measure_link_congestion,
    measure_link_errors,
    measure_link_utilization,
)
from diagnostics.path.collector import collect_path_diagnostics
from storage.baselines import compare_probe_metrics


def collect_local(
    domains: list[str] | None = None,
    target: str = "1.1.1.1",
) -> list[DiagnosticResult]:
    """
    Run silent collectors for the requested domains on this host.
    domains: subset of host|link|path (default: all three).
    """
    domains = domains or ["host", "link", "path"]
    results: list[DiagnosticResult] = []

    if "host" in domains:
        results.extend(collect_host_diagnostics(target_host=target))
    if "link" in domains:
        util = measure_link_utilization()
        errors = measure_link_errors()
        results.extend(util)
        results.extend(errors)
        results.extend(measure_link_congestion(util, errors))
        results.extend(compare_probe_metrics(util))
    if "path" in domains:
        results.extend(collect_path_diagnostics(target=target))

    return results

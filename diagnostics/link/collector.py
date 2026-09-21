"""Local interface / adjacent-link diagnostics."""

from __future__ import annotations

import time

import psutil
from rich.console import Console
from rich.table import Table

from core.metrics.bandwidth import link_capacity_bps
from core.metrics.congestion import congestion_score, congestion_state
from core.metrics.throughput import bps_from_byte_delta, utilization_percent
from core.result import DiagnosticResult, DiagnosticStatus, Severity

console = Console()


def _iface_speed_mbps(name: str) -> float | None:
    try:
        stats = psutil.net_if_stats().get(name)
        if stats and stats.speed and stats.speed > 0:
            return float(stats.speed)
    except Exception:
        pass
    return None


def measure_link_utilization(interval: float = 1.0) -> list[DiagnosticResult]:
    before = psutil.net_io_counters(pernic=True)
    time.sleep(interval)
    after = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()

    results: list[DiagnosticResult] = []
    for name, after_c in after.items():
        if name not in before:
            continue
        before_c = before[name]
        st = stats.get(name)
        if st and not st.isup:
            continue
        if "loopback" in name.lower() or name.lower().startswith("lo"):
            continue

        rx_bps = bps_from_byte_delta(after_c.bytes_recv - before_c.bytes_recv, interval)
        tx_bps = bps_from_byte_delta(after_c.bytes_sent - before_c.bytes_sent, interval)
        total_bps = rx_bps + tx_bps
        speed_mbps = _iface_speed_mbps(name)
        capacity = link_capacity_bps(speed_mbps)
        util = utilization_percent(total_bps, capacity)

        if util is not None and util >= 90:
            status, severity = DiagnosticStatus.DEGRADED, Severity.HIGH
        elif util is not None and util >= 75:
            status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        else:
            status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

        results.append(
            DiagnosticResult(
                module="link_utilization",
                category="link",
                status=status,
                severity=severity,
                summary=(
                    f"{name}: {util:.1f}% util"
                    if util is not None
                    else f"{name}: {total_bps/1e6:.2f} Mbps (speed unknown)"
                ),
                target=name,
                metrics={
                    "rx_bps": round(rx_bps, 1),
                    "tx_bps": round(tx_bps, 1),
                    "total_bps": round(total_bps, 1),
                    "util_percent": util,
                    "speed_mbps": speed_mbps,
                },
                evidence=[
                    f"RX {rx_bps/1e6:.2f} Mbps / TX {tx_bps/1e6:.2f} Mbps over {interval}s",
                ],
            )
        )
    return results


def measure_link_errors(interval: float = 1.0) -> list[DiagnosticResult]:
    before = psutil.net_io_counters(pernic=True)
    time.sleep(interval)
    after = psutil.net_io_counters(pernic=True)

    results: list[DiagnosticResult] = []
    for name, after_c in after.items():
        if name not in before:
            continue
        if "loopback" in name.lower() or name.lower().startswith("lo"):
            continue
        before_c = before[name]
        drops = (
            (after_c.dropin - before_c.dropin) + (after_c.dropout - before_c.dropout)
        ) / interval
        errors = (
            (after_c.errin - before_c.errin) + (after_c.errout - before_c.errout)
        ) / interval

        if drops >= 5 or errors >= 5:
            status, severity = DiagnosticStatus.FAILED, Severity.HIGH
        elif drops >= 0.5 or errors >= 0.5:
            status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        else:
            status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

        results.append(
            DiagnosticResult(
                module="link_errors",
                category="link",
                status=status,
                severity=severity,
                summary=f"{name}: {drops:.2f} drops/s, {errors:.2f} errs/s",
                target=name,
                metrics={
                    "drops_per_sec": round(drops, 3),
                    "errors_per_sec": round(errors, 3),
                },
                evidence=[f"Active drops={drops:.2f}/s errors={errors:.2f}/s"],
                warnings=["Active link-layer drops/errors"] if drops >= 0.5 or errors >= 0.5 else [],
            )
        )
    return results


def measure_link_congestion(
    util_results: list[DiagnosticResult] | None = None,
    error_results: list[DiagnosticResult] | None = None,
    latency_delta_ms: float = 0.0,
) -> list[DiagnosticResult]:
    util_results = util_results or measure_link_utilization()
    error_results = error_results or []
    errors_by_iface = {r.target: r for r in error_results}

    results: list[DiagnosticResult] = []
    for util in util_results:
        err = errors_by_iface.get(util.target)
        drop_rate = float(err.metrics.get("drops_per_sec", 0.0)) if err else 0.0
        error_rate = float(err.metrics.get("errors_per_sec", 0.0)) if err else 0.0
        score = congestion_score(
            util_percent=util.metrics.get("util_percent"),
            drop_rate=drop_rate,
            error_rate=error_rate,
            latency_delta_ms=latency_delta_ms,
        )
        state = congestion_state(score)

        if state == "severe":
            status, severity = DiagnosticStatus.FAILED, Severity.HIGH
        elif state in {"moderate", "mild"}:
            status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        else:
            status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

        results.append(
            DiagnosticResult(
                module="link_congestion",
                category="link",
                status=status,
                severity=severity,
                summary=f"{util.target}: congestion {state} (score {score})",
                target=util.target,
                metrics={
                    "congestion_score": score,
                    "congestion_state": state,
                    "util_percent": util.metrics.get("util_percent"),
                    "drops_per_sec": drop_rate,
                    "errors_per_sec": error_rate,
                },
                evidence=[f"Congestion score {score} → {state}"],
            )
        )
    return results


def run_link_diagnostics(interval: float = 1.0) -> list[DiagnosticResult]:
    # Single shared interval for util; errors use a second short sample
    util = measure_link_utilization(interval=interval)
    errors = measure_link_errors(interval=interval)
    congestion = measure_link_congestion(util, errors)

    table = Table(title="Link Diagnostics")
    table.add_column("Interface")
    table.add_column("Util %")
    table.add_column("Drops/s")
    table.add_column("Congestion")
    table.add_column("Status")

    err_map = {r.target: r for r in errors}
    cong_map = {r.target: r for r in congestion}
    for u in util:
        e = err_map.get(u.target)
        c = cong_map.get(u.target)
        util_pct = u.metrics.get("util_percent")
        table.add_row(
            u.target or "-",
            f"{util_pct:.1f}" if util_pct is not None else "-",
            f'{e.metrics.get("drops_per_sec", 0):.2f}' if e else "-",
            c.metrics.get("congestion_state", "-") if c else "-",
            (c or u).status.value,
        )
    console.print(table)
    return util + errors + congestion

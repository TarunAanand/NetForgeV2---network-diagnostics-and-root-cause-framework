"""Active bandwidth / speed estimation probes."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

from rich.console import Console
from rich.table import Table

from core.metrics.bandwidth import estimate_goodput_mbps
from core.metrics.latency_jitter import summarize_latencies
from core.result import DiagnosticResult, DiagnosticStatus, Severity
from diagnostics.host.icmp_utils import run_ping

console = Console()

DEFAULT_SPEED_URL = "https://speed.cloudflare.com/__down?bytes=5000000"


def measure_download_speed(
    url: str = DEFAULT_SPEED_URL,
    timeout: float = 30.0,
) -> DiagnosticResult:
    start = time.perf_counter()
    bytes_read = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "NetForge/0.2"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                bytes_read += len(chunk)
        elapsed = time.perf_counter() - start
        mbps = estimate_goodput_mbps(bytes_read, elapsed)

        if mbps < 1:
            status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        elif mbps < 5:
            status, severity = DiagnosticStatus.DEGRADED, Severity.LOW
        else:
            status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

        return DiagnosticResult(
            module="traffic_speed",
            category="traffic",
            status=status,
            severity=severity,
            summary=f"Download goodput ≈ {mbps:.2f} Mbps",
            target=url,
            metrics={
                "goodput_mbps": mbps,
                "bytes": bytes_read,
                "elapsed_seconds": round(elapsed, 3),
                "direction": "download",
            },
            evidence=[f"Transferred {bytes_read} bytes in {elapsed:.2f}s"],
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return DiagnosticResult(
            module="traffic_speed",
            category="traffic",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary="Download speed test failed",
            target=url,
            errors=[str(exc)],
        )


def measure_traffic_jitter(host: str = "1.1.1.1", count: int = 10) -> DiagnosticResult:
    data = run_ping(host, count=count, use_cache=False)
    if data.error or not data.latencies:
        return DiagnosticResult(
            module="traffic_jitter",
            category="traffic",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Jitter measurement failed for {host}",
            target=host,
            errors=[data.error] if data.error else ["No latency samples"],
        )

    summary = summarize_latencies(data.latencies)
    if summary.jitter_ms >= 50:
        status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
    elif summary.jitter_ms >= 20:
        status, severity = DiagnosticStatus.DEGRADED, Severity.LOW
    else:
        status, severity = DiagnosticStatus.HEALTHY, Severity.INFO

    return DiagnosticResult(
        module="traffic_jitter",
        category="traffic",
        status=status,
        severity=severity,
        summary=f"RFC 3550 jitter to {host}: {summary.jitter_ms:.2f} ms",
        target=host,
        metrics={
            "jitter_ms": summary.jitter_ms,
            "avg_ms": summary.avg_ms,
            "min_ms": summary.min_ms,
            "max_ms": summary.max_ms,
            "samples": summary.samples,
            "packet_loss_percent": data.packet_loss_percent,
        },
        evidence=[f"{summary.samples} samples, jitter {summary.jitter_ms:.2f} ms"],
    )


def collect_traffic_diagnostics(
    speed_url: str = DEFAULT_SPEED_URL,
    jitter_host: str = "1.1.1.1",
    include_speed: bool = True,
) -> list[DiagnosticResult]:
    results = [measure_traffic_jitter(jitter_host)]
    if include_speed:
        results.insert(0, measure_download_speed(speed_url))
    return results


def run_speed_diagnostics(url: str = DEFAULT_SPEED_URL) -> DiagnosticResult:
    result = measure_download_speed(url)
    table = Table(title="Traffic Speed")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Goodput", f'{result.metrics.get("goodput_mbps", 0):.2f} Mbps')
    table.add_row("Bytes", str(result.metrics.get("bytes", "-")))
    table.add_row("Elapsed", f'{result.metrics.get("elapsed_seconds", 0)} s')
    table.add_row("Status", result.status.value)
    console.print(table)
    return result


def run_jitter_diagnostics(host: str = "1.1.1.1", count: int = 10) -> DiagnosticResult:
    result = measure_traffic_jitter(host, count=count)
    table = Table(title="Traffic Jitter")
    table.add_column("Host")
    table.add_column("Jitter")
    table.add_column("Avg RTT")
    table.add_column("Status")
    table.add_row(
        result.target or "-",
        f'{result.metrics.get("jitter_ms", 0):.2f} ms',
        f'{result.metrics.get("avg_ms", 0):.2f} ms',
        result.status.value,
    )
    console.print(table)
    return result

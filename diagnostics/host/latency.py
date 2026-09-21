from rich.console import Console
from rich.table import Table

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)
from diagnostics.host.icmp_utils import run_ping

console = Console()


def measure_latency(
    host: str,
    count: int = 5,
) -> DiagnosticResult:

    data = run_ping(host, count=count)

    if data.error:
        return DiagnosticResult(
            module="latency",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Latency measurement failed for {host}",
            target=host,
            errors=[data.error],
        )

    if not data.latencies:
        return DiagnosticResult(
            module="latency",
            category="host",
            status=DiagnosticStatus.FAILED if data.packet_loss_percent == 100.0 else DiagnosticStatus.UNKNOWN,
            severity=Severity.HIGH if data.packet_loss_percent == 100.0 else Severity.MEDIUM,
            summary=f"No latency samples received from {host} (100% loss)" if data.packet_loss_percent == 100.0 else f"No latency samples received from {host}",
            target=host,
            metadata={"raw_output": data.raw_output},
        )

    average = data.avg_ms

    # Network latency evaluation thresholds
    if average < 100:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO
    elif average < 250:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO
    elif average < 400:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.LOW
    else:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.MEDIUM

    return DiagnosticResult(
        module="latency",
        category="host",
        status=status,
        severity=severity,
        summary=f"Average latency to {host}: {average:.2f} ms (RFC Jitter: {data.jitter_ms:.2f} ms)",
        target=host,
        metrics={
            "min_ms": data.min_ms,
            "avg_ms": data.avg_ms,
            "max_ms": data.max_ms,
            "jitter_ms": data.jitter_ms,       # RFC 3550 Interarrival Jitter
            "std_dev_ms": data.std_dev_ms,     # Statistical Standard Deviation
            "samples": len(data.latencies),
        },
        evidence=[
            f"{len(data.latencies)} latency samples collected",
            f"RFC 3550 PDV Jitter: {data.jitter_ms:.2f} ms",
        ],
        metadata={
            "raw_output": data.raw_output,
        },
    )


def run_latency_diagnostics(
    hosts: list[str] | None = None,
    count: int = 5,
):

    hosts = hosts or [
        "1.1.1.1",
        "8.8.8.8",
    ]

    results = [
        measure_latency(host, count)
        for host in hosts
    ]

    table = Table(
        title="Latency Diagnostics"
    )

    table.add_column("Host")
    table.add_column("Min")
    table.add_column("Avg")
    table.add_column("Max")
    table.add_column("Jitter")
    table.add_column("Status")

    for result in results:

        metrics = result.metrics

        table.add_row(
            result.target or "-",
            f'{metrics.get("min_ms", 0):.2f} ms',
            f'{metrics.get("avg_ms", 0):.2f} ms',
            f'{metrics.get("max_ms", 0):.2f} ms',
            f'{metrics.get("jitter_ms", 0):.2f} ms',
            result.status.value,
        )

    console.print(table)

    return results
from rich.console import Console
from rich.table import Table

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)
from diagnostics.host.icmp_utils import run_ping

console = Console()


def ping_host(
    host: str,
    count: int = 5,
) -> DiagnosticResult:

    data = run_ping(host, count=count)

    if data.error:
        return DiagnosticResult(
            module="packet_loss",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Unable to ping {host}",
            target=host,
            errors=[data.error],
        )

    loss = data.packet_loss_percent

    if loss is None:
        return DiagnosticResult(
            module="packet_loss",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Could not determine packet loss for {host}",
            target=host,
            metadata={
                "raw_output": data.raw_output,
            },
        )

    if loss == 0:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO
    elif loss < 5:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.LOW
    elif loss < 20:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.MEDIUM
    else:
        status = DiagnosticStatus.FAILED
        severity = Severity.HIGH

    return DiagnosticResult(
        module="packet_loss",
        category="host",
        status=status,
        severity=severity,
        summary=f"{loss}% packet loss to {host}",
        target=host,
        metrics={
            "packet_loss_percent": loss,
            "packets_sent": count,
        },
        evidence=[
            f"{loss}% packet loss observed"
        ],
        metadata={
            "raw_output": data.raw_output,
        },
    )


def run_packet_loss_diagnostics(
    hosts: list[str] | None = None,
    count: int = 5,
):

    hosts = hosts or [
        "1.1.1.1",
        "8.8.8.8",
    ]

    results = [
        ping_host(host, count)
        for host in hosts
    ]

    table = Table(
        title="Packet Loss Diagnostics"
    )

    table.add_column("Host")
    table.add_column("Loss")
    table.add_column("Status")

    for result in results:

        loss = result.metrics.get(
            "packet_loss_percent"
        )

        table.add_row(
            result.target or "-",
            (
                f"{loss}%"
                if loss is not None
                else "-"
            ),
            result.status.value,
        )

    console.print(table)

    return results
import socket
import time

from rich.console import Console
from rich.table import Table

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)

console = Console()


def check_host(
    host: str,
    port: int = 443,
    timeout: float = 3.0,
) -> DiagnosticResult:

    start = time.perf_counter()

    try:
        with socket.create_connection(
            (host, port),
            timeout=timeout,
        ):
            latency = (
                time.perf_counter() - start
            ) * 1000

        return DiagnosticResult(
            module="connectivity",
            category="host",
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"{host}:{port} is reachable",
            target=host,
            metrics={
                "port": port,
                "latency_ms": round(latency, 2),
            },
            evidence=[
                f"TCP connection to port {port} succeeded"
            ],
        )

    except (socket.timeout, OSError) as exc:

        return DiagnosticResult(
            module="connectivity",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.HIGH,
            summary=f"{host}:{port} is unreachable",
            target=host,
            metrics={
                "port": port,
            },
            evidence=[
                "TCP connection attempt failed"
            ],
            errors=[str(exc)],
        )


def run_connectivity_checks(
    hosts: list[str] | None = None,
) -> list[DiagnosticResult]:

    hosts = hosts or [
        "1.1.1.1",
        "8.8.8.8",
        "google.com",
    ]

    results = [
        check_host(host)
        for host in hosts
    ]

    table = Table(title="Connectivity Diagnostics")

    table.add_column("Host")
    table.add_column("Status")
    table.add_column("Latency")

    for result in results:

        status = (
            "[green]HEALTHY[/green]"
            if result.status == DiagnosticStatus.HEALTHY
            else "[red]FAILED[/red]"
        )

        latency = (
            f'{result.metrics["latency_ms"]} ms'
            if "latency_ms" in result.metrics
            else "-"
        )

        table.add_row(
            result.target or "-",
            status,
            latency,
        )

    console.print(table)

    return results
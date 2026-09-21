"""Gateway reachability probe for the host domain."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from core.result import DiagnosticResult, DiagnosticStatus, Severity
from diagnostics.host.icmp_utils import run_ping
from diagnostics.host.routing import inspect_routing_table

console = Console()


def check_gateway_reachability(
    gateway: str | None = None,
    count: int = 4,
) -> DiagnosticResult:
    """
    Ping the default gateway (or an explicit gateway address).
    Distinguishes local-gateway failure from upstream WAN outage.
    """
    if gateway is None:
        routing = inspect_routing_table()
        gateway = routing.metrics.get("default_gateway")

    if not gateway or gateway in {"None", "0.0.0.0"}:
        return DiagnosticResult(
            module="gateway",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.CRITICAL,
            summary="No default gateway configured to probe",
            evidence=["Routing table has no usable default gateway"],
        )

    data = run_ping(gateway, count=count, use_cache=False)

    if data.error:
        return DiagnosticResult(
            module="gateway",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Unable to probe gateway {gateway}",
            target=gateway,
            errors=[data.error],
        )

    loss = data.packet_loss_percent
    if loss is None:
        return DiagnosticResult(
            module="gateway",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Could not determine reachability of gateway {gateway}",
            target=gateway,
            metadata={"raw_output": data.raw_output},
        )

    if loss >= 99.0:
        status = DiagnosticStatus.FAILED
        severity = Severity.CRITICAL
        summary = f"Default gateway {gateway} is unreachable ({loss}% loss)"
    elif loss > 0:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.HIGH
        summary = f"Default gateway {gateway} is partially reachable ({loss}% loss)"
    else:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO
        summary = f"Default gateway {gateway} is reachable (RTT {data.avg_ms:.1f} ms)"

    return DiagnosticResult(
        module="gateway",
        category="host",
        status=status,
        severity=severity,
        summary=summary,
        target=gateway,
        metrics={
            "packet_loss_percent": loss,
            "avg_ms": data.avg_ms,
            "min_ms": data.min_ms,
            "max_ms": data.max_ms,
            "jitter_ms": data.jitter_ms,
            "packets_sent": count,
        },
        evidence=[
            f"ICMP probe to gateway {gateway}: {loss}% loss, avg RTT {data.avg_ms:.1f} ms",
        ],
        metadata={"raw_output": data.raw_output},
    )


def run_gateway_diagnostics(count: int = 4) -> DiagnosticResult:
    result = check_gateway_reachability(count=count)
    table = Table(title="Gateway Reachability")
    table.add_column("Gateway")
    table.add_column("Loss")
    table.add_column("Avg RTT")
    table.add_column("Status")
    loss = result.metrics.get("packet_loss_percent")
    table.add_row(
        result.target or "-",
        f"{loss}%" if loss is not None else "-",
        f'{result.metrics.get("avg_ms", 0):.1f} ms' if result.metrics else "-",
        result.status.value,
    )
    console.print(table)
    return result

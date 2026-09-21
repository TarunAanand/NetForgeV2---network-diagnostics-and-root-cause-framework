import time

import psutil
from rich.console import Console
from rich.table import Table

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)

console = Console()


def inspect_resources_and_activity(
    interval: float = 1.0,
) -> tuple[DiagnosticResult, DiagnosticResult]:
    """
    Simultaneously measure host CPU, memory, and network throughput deltas
    over a single sampling interval (reducing diagnostic runtime).
    """
    before_net = psutil.net_io_counters()
    cpu = psutil.cpu_percent(interval=interval)
    after_net = psutil.net_io_counters()
    memory = psutil.virtual_memory()

    rx_bytes = (after_net.bytes_recv - before_net.bytes_recv) / interval
    tx_bytes = (after_net.bytes_sent - before_net.bytes_sent) / interval
    rx_packets = (after_net.packets_recv - before_net.packets_recv) / interval
    tx_packets = (after_net.packets_sent - before_net.packets_sent) / interval

    rx_errors_sec = (after_net.errin - before_net.errin) / interval
    tx_errors_sec = (after_net.errout - before_net.errout) / interval
    rx_drops_sec = (after_net.dropin - before_net.dropin) / interval
    tx_drops_sec = (after_net.dropout - before_net.dropout) / interval

    active_errors = rx_errors_sec + tx_errors_sec
    active_drops = rx_drops_sec + tx_drops_sec

    # Evaluate host resource health
    warnings = []
    errors = []

    if active_errors > 0 or active_drops > 0:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.HIGH
        summary = f"Active network packet drops/errors detected ({active_drops:.1f} drops/s, {active_errors:.1f} errs/s)"
        warnings.append(f"Interface is actively dropping or corrupting packets: {active_drops:.1f} drops/s")
    elif cpu >= 95 or memory.percent >= 95:
        status = DiagnosticStatus.FAILED
        severity = Severity.HIGH
        summary = f"Host resources saturated (CPU: {cpu:.1f}%, RAM: {memory.percent:.1f}%)"
        errors.append("Host CPU or memory under extreme pressure")
    elif cpu >= 85 or memory.percent >= 90:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.MEDIUM
        summary = f"High host resource utilization (CPU: {cpu:.1f}%, RAM: {memory.percent:.1f}%)"
        warnings.append("High CPU or RAM usage detected")
    else:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO
        summary = f"Host resources healthy (CPU: {cpu:.1f}%, RAM: {memory.percent:.1f}%)"

    resource_res = DiagnosticResult(
        module="resource_network",
        category="host",
        status=status,
        severity=severity,
        summary=summary,
        metrics={
            "cpu_percent": cpu,
            "memory_percent": memory.percent,
            "memory_available_mb": round(memory.available / 1024 / 1024, 2),
            "boot_cumulative_errors": after_net.errin + after_net.errout,
            "boot_cumulative_drops": after_net.dropin + after_net.dropout,
            "active_errors_per_sec": active_errors,
            "active_drops_per_sec": active_drops,
        },
        evidence=[
            f"CPU: {cpu:.1f}%, RAM: {memory.percent:.1f}%",
            f"Active drop rate: {active_drops:.2f}/s, error rate: {active_errors:.2f}/s",
        ],
        warnings=warnings,
        errors=errors,
    )

    activity_res = DiagnosticResult(
        module="network_activity",
        category="host",
        status=DiagnosticStatus.HEALTHY,
        severity=Severity.INFO,
        summary="Network activity measured",
        metrics={
            "rx_bytes_per_sec": rx_bytes,
            "tx_bytes_per_sec": tx_bytes,
            "rx_packets_per_sec": rx_packets,
            "tx_packets_per_sec": tx_packets,
            "rx_errors_per_sec": rx_errors_sec,
            "tx_errors_per_sec": tx_errors_sec,
            "rx_drops_per_sec": rx_drops_sec,
            "tx_drops_per_sec": tx_drops_sec,
        },
    )

    return resource_res, activity_res


def inspect_resources() -> DiagnosticResult:
    resource_res, _ = inspect_resources_and_activity(interval=1.0)
    return resource_res


def measure_network_activity(interval: float = 1.0) -> DiagnosticResult:
    _, activity_res = inspect_resources_and_activity(interval=interval)
    return activity_res


def run_resource_network_diagnostics():

    resources, activity = inspect_resources_and_activity(interval=1.0)

    table = Table(
        title="Resource / Network Interaction"
    )

    table.add_column("Metric")
    table.add_column("Value")

    table.add_row(
        "CPU",
        f'{resources.metrics["cpu_percent"]:.1f}%',
    )

    table.add_row(
        "Memory",
        f'{resources.metrics["memory_percent"]:.1f}%',
    )

    table.add_row(
        "RX Throughput",
        f'{activity.metrics["rx_bytes_per_sec"] / 1024:.2f} KB/s',
    )

    table.add_row(
        "TX Throughput",
        f'{activity.metrics["tx_bytes_per_sec"] / 1024:.2f} KB/s',
    )

    table.add_row(
        "RX Packets",
        f'{activity.metrics["rx_packets_per_sec"]:.2f}/s',
    )

    table.add_row(
        "TX Packets",
        f'{activity.metrics["tx_packets_per_sec"]:.2f}/s',
    )

    active_drops = resources.metrics.get("active_drops_per_sec", 0.0)
    active_errs = resources.metrics.get("active_errors_per_sec", 0.0)
    drop_color = "[red]" if active_drops > 0 else "[green]"
    err_color = "[red]" if active_errs > 0 else "[green]"

    table.add_row(
        "Active Drops/s",
        f"{drop_color}{active_drops:.2f}/s[/]",
    )

    table.add_row(
        "Active Errors/s",
        f"{err_color}{active_errs:.2f}/s[/]",
    )

    table.add_row(
        "Boot Cumulative Drops",
        str(resources.metrics.get("boot_cumulative_drops", 0)),
    )

    table.add_row(
        "Boot Cumulative Errors",
        str(resources.metrics.get("boot_cumulative_errors", 0)),
    )

    console.print(table)

    return [
        resources,
        activity,
    ]
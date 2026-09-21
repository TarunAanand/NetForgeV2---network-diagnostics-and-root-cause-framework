import socket

import psutil
from rich.console import Console
from rich.table import Table

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)

console = Console()


def inspect_interfaces() -> list[DiagnosticResult]:

    addresses = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    results = []

    # Determine if any non-loopback interface is operational on this host
    has_active_interface = any(
        s.isup
        for n, s in stats.items()
        if s.isup and "loopback" not in n.lower() and not n.startswith("lo")
    )

    for name, addr_list in addresses.items():

        interface_stats = stats.get(name)

        is_up = (
            interface_stats.isup
            if interface_stats
            else False
        )

        speed = (
            interface_stats.speed
            if interface_stats
            else 0
        )

        mtu = (
            interface_stats.mtu
            if interface_stats
            else None
        )

        ipv4 = []
        ipv6 = []
        mac = None

        af_link = getattr(psutil, "AF_LINK", None)
        af_packet = getattr(socket, "AF_PACKET", None)

        for addr in addr_list:

            if addr.family == socket.AF_INET:
                ipv4.append(addr.address)

            elif addr.family == socket.AF_INET6:
                ipv6.append(addr.address)

            elif (af_link is not None and addr.family == af_link) or (
                af_packet is not None and addr.family == af_packet
            ):
                mac = addr.address

        if is_up:
            status = DiagnosticStatus.HEALTHY
            severity = Severity.INFO
            summary = f"Interface {name} is operational"
        elif has_active_interface:
            # Other network interfaces are operational; inactive adapter is normal
            status = DiagnosticStatus.HEALTHY
            severity = Severity.INFO
            summary = f"Interface {name} is inactive/disconnected"
        else:
            # No network interface is operational on the entire system
            status = DiagnosticStatus.FAILED
            severity = Severity.HIGH
            summary = f"Interface {name} is down (no active network)"

        results.append(
            DiagnosticResult(
                module="interface",
                category="host",
                status=status,
                severity=severity,
                summary=summary,
                target=name,
                metrics={
                    "is_up": is_up,
                    "speed_mbps": speed,
                    "mtu": mtu,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "mac": mac,
                },
                evidence=[
                    f"Interface state: {'UP' if is_up else 'DOWN'}"
                ],
            )
        )

    return results


def run_interface_diagnostics():

    results = inspect_interfaces()

    table = Table(title="Network Interfaces")

    table.add_column("Interface")
    table.add_column("State")
    table.add_column("IPv4")
    table.add_column("MAC")
    table.add_column("Speed")
    table.add_column("MTU")

    for result in results:

        is_up = result.metrics.get("is_up", False)
        state = (
            "[green]UP[/green]"
            if is_up
            else "[dim]DOWN[/dim]"
        )

        table.add_row(
            result.target or "-",
            state,
            ", ".join(
                result.metrics.get("ipv4", [])
            ) or "-",
            result.metrics.get("mac") or "-",
            f'{result.metrics.get("speed_mbps", 0)} Mbps',
            str(result.metrics.get("mtu") or "-"),
        )

    console.print(table)

    return results
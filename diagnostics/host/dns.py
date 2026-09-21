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


import platform
import re
import subprocess


def get_dns_servers() -> list[str]:

    system = platform.system().lower()
    servers = []

    if system == "windows":
        try:
            res = subprocess.run(
                ["netsh", "interface", "ip", "show", "dnsservers"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                ip_pattern = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
                for line in res.stdout.splitlines():
                    if "DNS servers" in line or "Statically Configured" in line or line.startswith(" "):
                        found = ip_pattern.findall(line)
                        for ip in found:
                            if not ip.startswith("127.") and not ip.startswith("169.254.") and ip not in servers:
                                servers.append(ip)
        except Exception:
            pass

        if not servers:
            try:
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "(Get-DnsClientServerAddress -AddressFamily IPv4).ServerAddresses"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if res.returncode == 0:
                    for ip in res.stdout.split():
                        if ip and ip not in servers:
                            servers.append(ip)
            except Exception:
                pass

    elif system == "darwin":
        try:
            res = subprocess.run(
                ["scutil", "--dns"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                matches = re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", res.stdout)
                for s in matches:
                    if s not in servers:
                        servers.append(s)
        except Exception:
            pass

    # Linux or fallback
    if not servers:
        try:
            with open("/etc/resolv.conf", encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if line.startswith("nameserver"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1] not in servers:
                            servers.append(parts[1])
        except (FileNotFoundError, PermissionError):
            pass

    return servers


def resolve_hostname(
    hostname: str,
) -> DiagnosticResult:

    start = time.perf_counter()

    try:

        addresses = socket.getaddrinfo(
            hostname,
            None,
            socket.AF_UNSPEC,
        )

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        ips = sorted(
            {
                result[4][0]
                for result in addresses
            }
        )

        return DiagnosticResult(
            module="dns",
            category="host",
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"DNS resolution succeeded for {hostname}",
            target=hostname,
            metrics={
                "resolution_time_ms": round(
                    elapsed,
                    2,
                ),
                "address_count": len(ips),
                "addresses": ips,
            },
            evidence=[
                f"Resolved {hostname} successfully"
            ],
        )

    except socket.gaierror as exc:

        return DiagnosticResult(
            module="dns",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.HIGH,
            summary=f"DNS resolution failed for {hostname}",
            target=hostname,
            errors=[str(exc)],
        )


def run_dns_diagnostics(
    hostname: str = "google.com",
):

    result = resolve_hostname(hostname)
    servers = get_dns_servers()

    result.metrics["dns_servers"] = servers
    if servers:
        result.evidence.append(f"Configured DNS servers: {', '.join(servers)}")

    table = Table(title="DNS Diagnostics")

    table.add_column("Property")
    table.add_column("Value")

    table.add_row(
        "Hostname",
        hostname,
    )

    table.add_row(
        "DNS Servers",
        ", ".join(servers) or "Unknown",
    )

    table.add_row(
        "Status",
        result.status.value,
    )

    table.add_row(
        "Resolution Time",
        (
            f'{result.metrics["resolution_time_ms"]} ms'
            if "resolution_time_ms"
            in result.metrics
            else "-"
        ),
    )

    table.add_row(
        "Addresses",
        ", ".join(
            result.metrics.get(
                "addresses",
                [],
            )
        ) or "-",
    )

    console.print(table)

    return result
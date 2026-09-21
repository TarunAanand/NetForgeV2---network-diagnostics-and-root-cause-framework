import platform
import subprocess

from rich.console import Console

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)

console = Console()


def parse_routing_table(system: str, output: str) -> tuple[str | None, str | None, int]:
    default_gateway = None
    default_interface = None
    route_count = 0

    lines = output.splitlines()

    if system == "windows":
        in_active_routes = False
        for line in lines:
            line_str = line.strip()
            if "Active Routes:" in line_str:
                in_active_routes = True
                continue
            if in_active_routes:
                if line_str.startswith("=") or "Persistent Routes:" in line_str or "IPv6 Route Table" in line_str:
                    in_active_routes = False
                    continue
                parts = line_str.split()
                if len(parts) >= 5 and parts[0] != "Network":
                    dest, netmask, gateway, iface = parts[0], parts[1], parts[2], parts[3]
                    if dest == "0.0.0.0" and netmask == "0.0.0.0":
                        if default_gateway is None:
                            default_gateway = gateway
                            default_interface = iface
                    route_count += 1

    elif system == "linux":
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            parts = line_str.split()
            if not parts:
                continue
            route_count += 1
            if parts[0] == "default":
                if "via" in parts:
                    idx = parts.index("via")
                    if idx + 1 < len(parts):
                        default_gateway = parts[idx + 1]
                if "dev" in parts:
                    idx = parts.index("dev")
                    if idx + 1 < len(parts):
                        default_interface = parts[idx + 1]

    elif system == "darwin":
        in_ipv4 = False
        for line in lines:
            line_str = line.strip()
            if "Internet:" in line_str:
                in_ipv4 = True
                continue
            if in_ipv4:
                if "Internet6:" in line_str:
                    break
                parts = line_str.split()
                if len(parts) >= 3 and not parts[0].startswith("Destination"):
                    route_count += 1
                    if parts[0] == "default":
                        default_gateway = parts[1]
                        default_interface = parts[-1]

    return default_gateway, default_interface, route_count


def inspect_routing_table() -> DiagnosticResult:

    system = platform.system().lower()

    if system == "windows":
        command = ["route", "print"]

    elif system == "linux":
        command = ["ip", "route"]

    elif system == "darwin":
        command = ["netstat", "-rn"]

    else:
        return DiagnosticResult(
            module="routing",
            category="host",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary="Unsupported operating system",
            errors=[
                f"Unsupported OS: {system}"
            ],
        )

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:

            return DiagnosticResult(
                module="routing",
                category="host",
                status=DiagnosticStatus.FAILED,
                severity=Severity.HIGH,
                summary="Failed to read routing table",
                errors=[
                    result.stderr.strip()
                ],
            )

        output = result.stdout.strip()
        default_gw, default_iface, route_count = parse_routing_table(system, output)

        if default_gw:
            status = DiagnosticStatus.HEALTHY
            severity = Severity.INFO
            summary = f"Default gateway {default_gw} operational ({route_count} active routes)"
        else:
            status = DiagnosticStatus.FAILED
            severity = Severity.HIGH
            summary = "No default gateway found in routing table"

        return DiagnosticResult(
            module="routing",
            category="host",
            status=status,
            severity=severity,
            summary=summary,
            target=default_gw,
            metrics={
                "os": system,
                "route_count": route_count,
                "default_gateway": default_gw,
                "default_interface": default_iface,
            },
            evidence=[
                f"Default gateway: {default_gw or 'None'}",
                f"Default interface: {default_iface or 'None'}",
                f"Active routes counted: {route_count}",
            ],
            metadata={
                "raw_output": output,
            },
        )

    except (
        subprocess.SubprocessError,
        OSError,
    ) as exc:

        return DiagnosticResult(
            module="routing",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.HIGH,
            summary="Unable to inspect routing table",
            errors=[str(exc)],
        )


def run_routing_diagnostics(verbose: bool = False):

    result = inspect_routing_table()

    from rich.table import Table
    table = Table(title="Routing Diagnostics")
    table.add_column("Property")
    table.add_column("Value")

    table.add_row("Status", result.status.value)
    table.add_row("Default Gateway", str(result.metrics.get("default_gateway") or "Not found"))
    table.add_row("Default Interface", str(result.metrics.get("default_interface") or "Not found"))
    table.add_row("Active Routes", str(result.metrics.get("route_count", 0)))
    table.add_row("Operating System", str(result.metrics.get("os") or "-"))

    console.print(table)

    if verbose and result.metadata.get("raw_output"):
        console.print("\n[dim]Raw Routing Table:[/dim]")
        console.print(result.metadata["raw_output"])

    return result
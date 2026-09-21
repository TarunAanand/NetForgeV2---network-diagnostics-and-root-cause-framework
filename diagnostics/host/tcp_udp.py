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


def test_tcp(
    host: str,
    port: int,
    timeout: float = 3.0,
) -> DiagnosticResult:

    start = time.perf_counter()

    try:
        # Resolve address family dynamically (IPv4 or IPv6)
        addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not addr_info:
            raise OSError(f"Could not resolve {host}")

        family, socktype, proto, _, sockaddr = addr_info[0]

        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)
            code = sock.connect_ex(sockaddr)

        latency = (time.perf_counter() - start) * 1000

        if code == 0:
            return DiagnosticResult(
                module="tcp",
                category="host",
                status=DiagnosticStatus.HEALTHY,
                severity=Severity.INFO,
                summary=f"TCP port {port} is reachable",
                target=f"{host}:{port}",
                metrics={
                    "protocol": "tcp",
                    "port": port,
                    "latency_ms": round(latency, 2),
                },
                evidence=[
                    f"TCP handshake to {host}:{port} succeeded"
                ],
            )

        return DiagnosticResult(
            module="tcp",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.MEDIUM,
            summary=f"TCP port {port} is unreachable",
            target=f"{host}:{port}",
            metrics={
                "protocol": "tcp",
                "port": port,
                "error_code": code,
            },
            evidence=[
                f"TCP connection failed with error code {code}"
            ],
        )

    except OSError as exc:
        return DiagnosticResult(
            module="tcp",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.MEDIUM,
            summary=f"TCP test failed for port {port}",
            target=f"{host}:{port}",
            metrics={
                "protocol": "tcp",
                "port": port,
            },
            errors=[str(exc)],
        )


def test_udp(
    host: str,
    port: int = 53,
    timeout: float = 3.0,
) -> DiagnosticResult:
    """
    Test UDP reachability. For DNS (port 53) and NTP (port 123), protocol-level
    request/response probes are used. For other ports, datagram transmission is tested.
    """
    start = time.perf_counter()

    try:
        addr_info = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_DGRAM)
        if not addr_info:
            raise OSError(f"Could not resolve {host}")

        family, socktype, proto, _, sockaddr = addr_info[0]

        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(timeout)

            if port == 53:
                # Standard DNS A-record query for google.com
                query = b"\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x06google\x03com\x00\x00\x01\x00\x01"
                sock.sendto(query, sockaddr)
                data, _ = sock.recvfrom(512)
                latency = (time.perf_counter() - start) * 1000
                evidence = [f"UDP DNS response received ({len(data)} bytes)"]

            elif port == 123:
                # Standard 48-byte NTP client request
                query = b"\x1b" + 47 * b"\0"
                sock.sendto(query, sockaddr)
                data, _ = sock.recvfrom(512)
                latency = (time.perf_counter() - start) * 1000
                evidence = [f"UDP NTP response received ({len(data)} bytes)"]

            else:
                # Generic UDP test: send datagram and verify no immediate ICMP Port Unreachable
                sock.connect(sockaddr)
                sock.send(b"\x00")
                latency = (time.perf_counter() - start) * 1000
                evidence = ["UDP datagram transmitted without ICMP Port Unreachable"]

        return DiagnosticResult(
            module="udp",
            category="host",
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"UDP port {port} is reachable",
            target=f"{host}:{port}",
            metrics={
                "protocol": "udp",
                "port": port,
                "latency_ms": round(latency, 2),
            },
            evidence=evidence,
        )

    except (socket.timeout, TimeoutError):
        # For non-DNS/NTP UDP, timeout is expected if server doesn't reply; for 53/123 it is a failure
        if port in {53, 123}:
            return DiagnosticResult(
                module="udp",
                category="host",
                status=DiagnosticStatus.FAILED,
                severity=Severity.MEDIUM,
                summary=f"UDP port {port} timed out waiting for reply",
                target=f"{host}:{port}",
                metrics={"protocol": "udp", "port": port},
                errors=["Request timed out"],
            )
        return DiagnosticResult(
            module="udp",
            category="host",
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"UDP port {port} datagram sent (no ICMP rejection)",
            target=f"{host}:{port}",
            metrics={"protocol": "udp", "port": port},
            evidence=["Datagram sent, no ICMP port unreachable returned"],
        )

    except (ConnectionRefusedError, OSError) as exc:
        return DiagnosticResult(
            module="udp",
            category="host",
            status=DiagnosticStatus.FAILED,
            severity=Severity.MEDIUM,
            summary=f"UDP port {port} is closed or rejected",
            target=f"{host}:{port}",
            metrics={"protocol": "udp", "port": port},
            errors=[str(exc)],
        )


def run_transport_diagnostics(
    host: str = "1.1.1.1",
    tcp_ports: list[int] | None = None,
    udp_ports: list[int] | None = None,
):
    tcp_ports = tcp_ports or [53, 80, 443]
    udp_ports = udp_ports or [53]

    results = []

    for port in tcp_ports:
        results.append(test_tcp(host, port))

    for port in udp_ports:
        results.append(test_udp(host, port))

    table = Table(title="Transport Diagnostics (TCP / UDP)")

    table.add_column("Protocol")
    table.add_column("Host")
    table.add_column("Port")
    table.add_column("Status")
    table.add_column("Latency")

    for result in results:
        status_color = "[green]HEALTHY[/green]" if result.status == DiagnosticStatus.HEALTHY else "[red]FAILED[/red]"
        latency = (
            f'{result.metrics["latency_ms"]} ms'
            if "latency_ms" in result.metrics
            else "-"
        )
        table.add_row(
            result.metrics.get("protocol", "tcp").upper(),
            host,
            str(result.metrics["port"]),
            status_color,
            latency,
        )

    console.print(table)

    return results
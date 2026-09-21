from __future__ import annotations

from core.result import DiagnosticResult
from diagnostics.host.connectivity import check_host
from diagnostics.host.dns import get_dns_servers, resolve_hostname
from diagnostics.host.gateway import check_gateway_reachability
from diagnostics.host.interface import inspect_interfaces
from diagnostics.host.latency import measure_latency
from diagnostics.host.packet_loss import ping_host
from diagnostics.host.resource_network import inspect_resources_and_activity
from diagnostics.host.routing import inspect_routing_table
from diagnostics.host.tcp_udp import test_tcp, test_udp
from storage.baselines import compare_probe_metrics


def collect_host_diagnostics(
    target_host: str = "google.com",
    connectivity_hosts: list[str] | None = None,
    ping_count: int = 5,
    include_baselines: bool = True,
) -> list[DiagnosticResult]:
    """
    Orchestrates and collects DiagnosticResult observations across host diagnostic probes.
    Pure data collection without printing, ideal for RuleEngine or API integrations.
    """
    results: list[DiagnosticResult] = []

    conn_hosts = connectivity_hosts or ["1.1.1.1", "8.8.8.8", target_host]
    for h in conn_hosts:
        results.append(check_host(h))

    results.extend(inspect_interfaces())
    results.append(inspect_routing_table())
    results.append(check_gateway_reachability(count=min(4, ping_count)))

    dns_result = resolve_hostname(target_host)
    servers = get_dns_servers()
    dns_result.metrics["dns_servers"] = servers
    if servers:
        dns_result.evidence.append(f"Configured DNS servers: {', '.join(servers)}")
    results.append(dns_result)

    for port in [53, 80, 443]:
        results.append(test_tcp("1.1.1.1", port))
    results.append(test_udp("1.1.1.1", 53))

    for h in ["1.1.1.1", "8.8.8.8"]:
        results.append(ping_host(h, count=ping_count))

    for h in ["1.1.1.1", "8.8.8.8"]:
        results.append(measure_latency(h, count=ping_count))

    resources, activity = inspect_resources_and_activity(interval=1.0)
    results.append(resources)
    results.append(activity)

    if include_baselines:
        results.extend(compare_probe_metrics(results))

    return results

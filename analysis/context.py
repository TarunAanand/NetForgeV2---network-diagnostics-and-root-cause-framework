from __future__ import annotations

from typing import Any
from core.result import DiagnosticResult, DiagnosticStatus


class AnalysisContext:
    """
    Indexed cache and query helper over a collection of DiagnosticResult observations.
    Simplifies multi-layer evidence correlation across rule definitions.
    """

    def __init__(self, results: list[DiagnosticResult]):
        self.results = results
        self._by_module: dict[str, list[DiagnosticResult]] = {}
        for r in results:
            self._by_module.setdefault(r.module, []).append(r)

    def by_module(self, module: str) -> list[DiagnosticResult]:
        return self._by_module.get(module, [])

    def first_by_module(self, module: str) -> DiagnosticResult | None:
        items = self.by_module(module)
        return items[0] if items else None

    # --- Interface Layer ---
    def has_active_interface(self) -> bool:
        """Returns True if at least one non-loopback interface is marked UP."""
        ifaces = self.by_module("interface")
        for iface in ifaces:
            if iface.metrics.get("is_up") and "loopback" not in (iface.target or "").lower():
                return True
        return False

    def all_interfaces_down(self) -> bool:
        ifaces = self.by_module("interface")
        if not ifaces:
            return False
        return not self.has_active_interface()

    # --- Routing & Gateway Layer ---
    def default_gateway(self) -> str | None:
        routing = self.first_by_module("routing")
        if routing and routing.metrics:
            return routing.metrics.get("default_gateway")
        return None

    def has_default_gateway(self) -> bool:
        gw = self.default_gateway()
        return bool(gw and gw != "None" and gw != "0.0.0.0")

    def is_gateway_reachable(self) -> bool | None:
        """
        True if gateway probe succeeded, False if failed, None if not probed.
        """
        gw = self.first_by_module("gateway")
        if not gw:
            return None
        if gw.status == DiagnosticStatus.HEALTHY:
            return True
        if gw.status in {DiagnosticStatus.FAILED, DiagnosticStatus.DEGRADED}:
            loss = gw.metrics.get("packet_loss_percent")
            if loss is not None and loss >= 99.0:
                return False
            if gw.status == DiagnosticStatus.FAILED:
                return False
            return True
        return None

    # --- IP Connectivity Layer ---
    def is_ip_connectivity_working(self) -> bool:
        """Checks if connectivity to public IPs (e.g. 1.1.1.1 or 8.8.8.8) succeeded."""
        conn_results = self.by_module("connectivity")
        for r in conn_results:
            if r.target in {"1.1.1.1", "8.8.8.8"} and r.status == DiagnosticStatus.HEALTHY:
                return True
        # Check ICMP ping as fallback
        loss_results = self.by_module("packet_loss")
        for r in loss_results:
            loss = r.metrics.get("packet_loss_percent")
            if loss is not None and loss < 100.0:
                return True
        return False

    # --- DNS Layer ---
    def is_dns_resolution_working(self) -> bool:
        dns_res = self.first_by_module("dns")
        if not dns_res:
            return False
        return dns_res.status == DiagnosticStatus.HEALTHY

    def get_dns_servers(self) -> list[str]:
        dns_res = self.first_by_module("dns")
        if dns_res and "dns_servers" in dns_res.metrics:
            return dns_res.metrics["dns_servers"]
        return []

    # --- Latency & Loss Metrics ---
    def get_max_packet_loss(self) -> float:
        losses = [
            r.metrics.get("packet_loss_percent", 0.0)
            for r in self.by_module("packet_loss")
            if r.metrics.get("packet_loss_percent") is not None
        ]
        return max(losses) if losses else 0.0

    def get_avg_packet_loss(self) -> float:
        losses = [
            r.metrics.get("packet_loss_percent", 0.0)
            for r in self.by_module("packet_loss")
            if r.metrics.get("packet_loss_percent") is not None
        ]
        return sum(losses) / len(losses) if losses else 0.0

    def get_avg_latency(self) -> float:
        latencies = [
            r.metrics.get("avg_ms", 0.0)
            for r in self.results
            if r.module in {"latency", "traffic_jitter", "gateway"}
            if r.metrics.get("avg_ms") is not None
        ]
        return sum(latencies) / len(latencies) if latencies else 0.0

    def get_max_jitter(self) -> float:
        jitters = [
            r.metrics.get("jitter_ms", 0.0)
            for r in self.by_module("latency")
            if r.metrics.get("jitter_ms") is not None
        ]
        return max(jitters) if jitters else 0.0

    def get_jitter(self, module: str) -> float | None:
        """Return jitter for a specific measurement scope."""
        values = [
            float(r.metrics["jitter_ms"])
            for r in self.by_module(module)
            if r.metrics.get("jitter_ms") is not None
        ]
        return max(values) if values else None

    def has_confirmed_jitter_pattern(self, threshold_ms: float = 40.0) -> bool:
        """Require local and end-to-end corroboration before calling bufferbloat."""
        gateway = self.get_jitter("gateway")
        endpoint = self.get_jitter("latency") or self.get_jitter("traffic_jitter")
        if gateway is None or endpoint is None:
            return False
        if gateway < threshold_ms or endpoint < threshold_ms:
            return False

        under_load = any(
            r.metrics.get("under_load") is True
            or r.metrics.get("load_phase") == "under_load"
            for r in self.results
        )
        return under_load or self.get_max_link_utilization() >= 70.0

    # --- Resource & Activity Layer ---
    def get_active_drop_rate(self) -> float:
        res = self.first_by_module("resource_network")
        if res and "active_drops_per_sec" in res.metrics:
            return float(res.metrics["active_drops_per_sec"])
        return 0.0

    def get_active_error_rate(self) -> float:
        res = self.first_by_module("resource_network")
        if res and "active_errors_per_sec" in res.metrics:
            return float(res.metrics["active_errors_per_sec"])
        return 0.0

    def get_cpu_percent(self) -> float:
        res = self.first_by_module("resource_network")
        if res and "cpu_percent" in res.metrics:
            return float(res.metrics["cpu_percent"])
        return 0.0

    def get_memory_percent(self) -> float:
        res = self.first_by_module("resource_network")
        if res and "memory_percent" in res.metrics:
            return float(res.metrics["memory_percent"])
        return 0.0

    # --- Transport Layer Query ---
    def get_transport_result(self, port: int, protocol: str = "tcp") -> DiagnosticResult | None:
        target_mod = protocol.lower()
        for r in self.by_module(target_mod):
            if r.metrics.get("port") == port:
                return r
        return None

    # --- Link Layer ---
    def get_max_link_utilization(self) -> float:
        utils = [
            float(r.metrics.get("util_percent", 0.0))
            for r in self.by_module("link_utilization")
            if r.metrics.get("util_percent") is not None
        ]
        return max(utils) if utils else 0.0

    def get_max_link_drop_rate(self) -> float:
        rates = [
            float(r.metrics.get("drops_per_sec", 0.0))
            for r in self.by_module("link_errors")
            if r.metrics.get("drops_per_sec") is not None
        ]
        return max(rates) if rates else self.get_active_drop_rate()

    # --- Path Layer ---
    def get_path_hops(self) -> list[dict]:
        path = self.first_by_module("traceroute")
        if path and isinstance(path.metrics.get("hops"), list):
            return path.metrics["hops"]
        return []

    def get_path_fingerprint(self) -> str | None:
        path = self.first_by_module("traceroute")
        if path:
            return path.metrics.get("path_fingerprint")
        return None

    def path_changed(self) -> bool:
        change = self.first_by_module("path_change")
        if not change:
            return False
        return bool(change.metrics.get("changed"))

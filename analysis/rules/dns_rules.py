from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import DiagnosticStatus, Severity


class DNSFailureWithHealthyIPRule(DiagnosticRule):
    rule_id = "RULE_DNS_FAIL_IP_HEALTHY"
    name = "DNS Resolution Failure with Functional IP Transit"
    category = "DNS"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        dns_res = ctx.first_by_module("dns")
        if not dns_res or dns_res.status == DiagnosticStatus.HEALTHY:
            return None

        # Check if underlying IP connectivity is functional
        if not ctx.is_ip_connectivity_working():
            return None  # Total outage handles this

        dns_servers = ctx.get_dns_servers()
        evidence = [
            f"DNS resolution failed: {dns_res.summary}",
            "Direct IP transport handshakes and ICMP ping to public addresses (1.1.1.1, 8.8.8.8) succeeded",
        ]
        if dns_servers:
            evidence.append(f"Configured upstream DNS servers: {', '.join(dns_servers)}")
        if dns_res.errors:
            evidence.append(f"Resolver error: {'; '.join(dns_res.errors)}")

        confidence = 0.95 if dns_servers else 0.90

        recs = [
            Recommendation(
                action="Flush local operating system DNS cache",
                command="ipconfig /flushdns",
                rationale="Clears stale, negative, or corrupted resolver cache entries.",
                priority=1,
            ),
            Recommendation(
                action="Test resolution directly against an Anycast public resolver (Cloudflare 1.1.1.1)",
                command="nslookup google.com 1.1.1.1",
                rationale="Determines whether your current upstream resolver is down or if outbound port 53 is blocked.",
                priority=1,
            ),
            Recommendation(
                action="Configure fallback public DNS servers (1.1.1.1 or 8.8.8.8) in network adapter settings",
                rationale="Bypasses unreliable ISP or local router DNS forwarders.",
                priority=2,
            ),
        ]

        return self.build_issue(
            title="DNS Resolution Failure with Functional IP Transit",
            severity=Severity.HIGH,
            confidence=confidence,
            root_cause="The host has full Layer 3/4 Internet connectivity, but domain name resolution is failing. Your configured DNS servers are unresponsive, returning SERVFAIL, or outbound UDP/TCP port 53 is filtered.",
            correlated_evidence=evidence,
            recommendations=recs,
        )


class SlowDNSResolutionRule(DiagnosticRule):
    rule_id = "RULE_SLOW_DNS_RESOLUTION"
    name = "High DNS Resolution Latency"
    category = "DNS"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        dns_res = ctx.first_by_module("dns")
        if not dns_res or dns_res.status != DiagnosticStatus.HEALTHY:
            return None

        res_time = dns_res.metrics.get("resolution_time_ms")
        if res_time is None or res_time < 180.0:
            return None

        avg_latency = ctx.get_avg_latency()
        # If IP latency is also huge (>250ms), it's a general link issue
        if avg_latency > 200.0:
            return None

        evidence = [
            f"DNS resolution time was {res_time:.1f} ms",
            f"Host IP round-trip latency is only {avg_latency:.1f} ms",
        ]
        dns_servers = ctx.get_dns_servers()
        if dns_servers:
            evidence.append(f"Configured DNS servers: {', '.join(dns_servers)}")

        confidence = 0.85

        recs = [
            Recommendation(
                action="Switch to high-performance recursive DNS resolvers (Cloudflare 1.1.1.1 or Google 8.8.8.8)",
                command="nslookup -type=A google.com 1.1.1.1",
                rationale="Distant or overburdened ISP resolvers add 150-500ms of overhead to every new domain connection.",
                priority=1,
            ),
        ]

        return self.build_issue(
            title="Sluggish DNS Resolution Latency",
            severity=Severity.MEDIUM,
            confidence=confidence,
            root_cause=f"DNS resolution took {res_time:.1f} ms despite low network round-trip latency ({avg_latency:.1f} ms), indicating an overloaded or geographically distant recursive resolver.",
            correlated_evidence=evidence,
            recommendations=recs,
        )


class DNSTimeoutRule(DiagnosticRule):
    rule_id = "RULE_DNS_TIMEOUT"
    name = "DNS Resolution Timeout"
    category = "DNS"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        dns_res = ctx.first_by_module("dns")
        if not dns_res:
            return None
        if dns_res.status not in {DiagnosticStatus.FAILED, DiagnosticStatus.UNKNOWN}:
            return None

        err_blob = " ".join(dns_res.errors + [dns_res.summary]).lower()
        if "timeout" not in err_blob and "timed out" not in err_blob:
            # Still treat hard DNS failure with working IP as timeout-class if no other DNS rule fired
            if not ctx.is_ip_connectivity_working():
                return None
            # Let DNSFailureWithHealthyIPRule own non-timeout failures
            return None

        if not ctx.is_ip_connectivity_working():
            return None

        evidence = [
            f"DNS probe timed out: {dns_res.summary}",
            "IP connectivity to public targets is working",
        ]
        servers = ctx.get_dns_servers()
        if servers:
            evidence.append(f"Configured DNS servers: {', '.join(servers)}")

        recs = [
            Recommendation(
                action="Query a public resolver directly to isolate local DNS timeout",
                command="nslookup google.com 1.1.1.1",
                rationale="Confirms whether the timeout is specific to your configured resolvers.",
                priority=1,
            ),
        ]
        return self.build_issue(
            title="DNS Resolution Timeout",
            severity=Severity.HIGH,
            confidence=0.92,
            root_cause="DNS queries are timing out while IP transit still works, indicating blocked/unresponsive resolvers or filtered DNS ports.",
            correlated_evidence=evidence,
            recommendations=recs,
        )

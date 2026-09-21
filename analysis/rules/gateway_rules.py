from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import DiagnosticStatus, Severity


class NoDefaultGatewayRule(DiagnosticRule):
    rule_id = "RULE_NO_DEFAULT_GATEWAY"
    name = "Missing Default Gateway Route"
    category = "Routing & Gateway"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if ctx.all_interfaces_down():
            return None

        if ctx.has_default_gateway():
            return None

        evidence = [
            "Routing table does not contain a default gateway route (0.0.0.0/0)",
            "Host has active interface but cannot determine next-hop gateway for non-local traffic",
        ]
        recs = [
            Recommendation(
                action="Renew IP address and default gateway via DHCP",
                command="ipconfig /renew",
                rationale="Requesting a fresh DHCP lease will pull the router default gateway option from your network.",
                priority=1,
            ),
            Recommendation(
                action="Inspect static IP routing table configuration",
                command="route print",
                rationale="Verify if a static default gateway needs to be manually assigned.",
                priority=2,
            ),
        ]
        return self.build_issue(
            title="Missing Default Gateway Route",
            severity=Severity.CRITICAL,
            confidence=0.96,
            root_cause="No default route exists in the host routing table. The operating system cannot route packets destined for external networks.",
            correlated_evidence=evidence,
            recommendations=recs,
            suppressed_rules=["RULE_TOTAL_INTERNET_OUTAGE", "RULE_DNS_FAIL_IP_HEALTHY"],
        )


class GatewayUnreachableRule(DiagnosticRule):
    rule_id = "RULE_GATEWAY_UNREACHABLE"
    name = "Default Gateway Unreachable"
    category = "Routing & Gateway"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if not ctx.has_default_gateway():
            return None
        reachable = ctx.is_gateway_reachable()
        if reachable is not False:
            return None

        gw = ctx.default_gateway()
        evidence = [
            f"Default gateway configured: {gw}",
            "ICMP probes to the default gateway timed out or reported ~100% loss",
            "Local interface may be up, but the next-hop router is not responding",
        ]
        recs = [
            Recommendation(
                action="Verify the gateway address and local subnet mask",
                rationale="An incorrect gateway IP or subnet makes the next hop unreachable.",
                priority=1,
            ),
            Recommendation(
                action="Check the local router/AP power and LAN switch port",
                command=f"ping -n 4 {gw}" if gw else None,
                rationale="Confirm the CPE/router is online on the LAN before blaming WAN/ISP.",
                priority=2,
            ),
        ]
        return self.build_issue(
            title="Default Gateway Unreachable",
            severity=Severity.CRITICAL,
            confidence=0.95,
            root_cause=f"The configured default gateway {gw} does not respond to probes. Local next-hop failure prevents Internet access.",
            correlated_evidence=evidence,
            recommendations=recs,
            suppressed_rules=[
                "RULE_GATEWAY_HEALTHY_WAN_OUTAGE",
                "RULE_TOTAL_INTERNET_OUTAGE",
                "RULE_DNS_FAIL_IP_HEALTHY",
                "RULE_DNS_TIMEOUT",
            ],
        )


class GatewayHealthyWANOutageRule(DiagnosticRule):
    rule_id = "RULE_GATEWAY_HEALTHY_WAN_OUTAGE"
    name = "Local Gateway Healthy but Upstream WAN Outage"
    category = "Routing & Gateway"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if not ctx.has_default_gateway():
            return None

        reachable = ctx.is_gateway_reachable()
        # Prefer explicit gateway probe; if missing, fall back to interface+route intact assumption
        if reachable is False:
            return None
        if reachable is None and ctx.all_interfaces_down():
            return None

        ip_working = ctx.is_ip_connectivity_working()
        max_loss = ctx.get_max_packet_loss()

        if not ip_working and max_loss >= 99.0:
            gw = ctx.default_gateway()
            evidence = [
                f"Default gateway configured: {gw}",
                "Outbound TCP handshakes and ICMP pings to public IPs (1.1.1.1, 8.8.8.8) timed out completely",
            ]
            if reachable is True:
                evidence.append(f"Local gateway {gw} responds to ICMP — failure is upstream of the LAN")
            else:
                evidence.append("Local host interface and routing table are intact")

            recs = [
                Recommendation(
                    action="Inspect modem / ISP WAN optical or coax link status",
                    rationale="Your host communicates with the local router, but the router cannot route out to the Internet.",
                    priority=1,
                ),
                Recommendation(
                    action=f"Test local gateway reachability directly at {gw}",
                    command=f"ping -n 4 {gw}",
                    rationale="Verify if the local router management interface is responsive.",
                    priority=2,
                ),
            ]
            return self.build_issue(
                title="Upstream WAN / ISP Outage (Local Gateway Reachable)",
                severity=Severity.CRITICAL,
                confidence=0.94 if reachable is True else 0.88,
                root_cause=f"The local host is connected to default gateway {gw}, but the upstream Internet connection (WAN link / ISP) is completely down.",
                correlated_evidence=evidence,
                recommendations=recs,
                suppressed_rules=["RULE_DNS_FAIL_IP_HEALTHY", "RULE_DNS_TIMEOUT"],
            )

        return None


class TotalInternetOutageRule(DiagnosticRule):
    rule_id = "RULE_TOTAL_INTERNET_OUTAGE"
    name = "Total Internet Outage"
    category = "Routing & Gateway"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if ctx.all_interfaces_down() or not ctx.has_default_gateway():
            return None
        if ctx.is_gateway_reachable() is False:
            return None
        if ctx.is_ip_connectivity_working():
            return None
        if ctx.get_max_packet_loss() < 99.0:
            return None

        # Prefer more specific WAN-outage rule when gateway is known healthy
        if ctx.is_gateway_reachable() is True:
            return None

        evidence = [
            "No successful TCP or ICMP connectivity to public Internet targets",
            f"Default gateway present: {ctx.default_gateway()}",
            "Gateway reachability was not confirmed; treating as broad Internet outage",
        ]
        recs = [
            Recommendation(
                action="Ping the default gateway, then an upstream IP (1.1.1.1)",
                rationale="Separates LAN next-hop failure from ISP/WAN failure.",
                priority=1,
            ),
            Recommendation(
                action="Check ISP outage status and CPE WAN lights",
                rationale="Broad Internet failure often originates at the modem or provider.",
                priority=2,
            ),
        ]
        return self.build_issue(
            title="Total Internet Connectivity Outage",
            severity=Severity.CRITICAL,
            confidence=0.90,
            root_cause="The host cannot reach any public Internet targets despite having a local interface and default route.",
            correlated_evidence=evidence,
            recommendations=recs,
            suppressed_rules=["RULE_DNS_FAIL_IP_HEALTHY", "RULE_DNS_TIMEOUT"],
        )

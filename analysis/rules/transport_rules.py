from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import DiagnosticStatus, Severity


class PortBlockedByFirewallRule(DiagnosticRule):
    rule_id = "RULE_PORT_BLOCKED_FIREWALL"
    name = "Port-Specific Firewall or Security Group Blocking"
    category = "Firewall & Transport"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if not ctx.is_ip_connectivity_working():
            return None

        # Look for failed transport ports while other transport ports succeeded
        tcp_results = ctx.by_module("tcp")
        if not tcp_results:
            return None

        failed_ports = [r for r in tcp_results if r.status == DiagnosticStatus.FAILED]
        healthy_ports = [r for r in tcp_results if r.status == DiagnosticStatus.HEALTHY]

        if failed_ports and healthy_ports:
            failed_port_list = [str(r.metrics.get("port")) for r in failed_ports]
            healthy_port_list = [str(r.metrics.get("port")) for r in healthy_ports]
            target_host = failed_ports[0].target or "target"

            evidence = [
                f"Failed connection on TCP port(s): {', '.join(failed_port_list)}",
                f"Successful connection on TCP port(s): {', '.join(healthy_port_list)}",
                "Basic ICMP Layer 3 reachability is verified",
            ]

            recs = [
                Recommendation(
                    action=f"Inspect local outbound firewall rules for blocked port(s) {', '.join(failed_port_list)}",
                    command="Get-NetFirewallRule -Direction Outbound -Action Block",
                    rationale="Host-level firewalls or antivirus endpoint protection often filter non-standard outbound ports.",
                    priority=1,
                ),
                Recommendation(
                    action="Check corporate proxy, UTM appliance, or cloud security group ACLs",
                    rationale="Perimeter firewalls or captive portals frequently drop egress traffic on restricted ports.",
                    priority=2,
                ),
            ]

            return self.build_issue(
                title=f"Port Filtering Detected (TCP {', '.join(failed_port_list)} Blocked)",
                severity=Severity.HIGH,
                confidence=0.88,
                root_cause=f"Host-level or perimeter firewall is selectively filtering TCP port(s) {', '.join(failed_port_list)} to {target_host} while other transport ports remain open.",
                correlated_evidence=evidence,
                recommendations=recs,
            )

        return None


class TargetSpecificFailureRule(DiagnosticRule):
    rule_id = "RULE_TARGET_SPECIFIC_FAILURE"
    name = "Remote Destination Outage"
    category = "Firewall & Transport"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        conn_results = ctx.by_module("connectivity")
        if not conn_results:
            return None

        failed_targets = [r for r in conn_results if r.status == DiagnosticStatus.FAILED]
        healthy_targets = [r for r in conn_results if r.status == DiagnosticStatus.HEALTHY]

        # General internet works (1.1.1.1 or 8.8.8.8 healthy), but one target failed
        public_healthy = any(r.target in {"1.1.1.1", "8.8.8.8"} for r in healthy_targets)

        if public_healthy and failed_targets:
            failed_names = [r.target for r in failed_targets if r.target]
            evidence = [
                f"Connection failed specifically to: {', '.join(failed_names)}",
                f"Public baseline hosts (1.1.1.1 / 8.8.8.8) are fully reachable",
                "Local network and default gateway are healthy",
            ]
            recs = [
                Recommendation(
                    action=f"Verify service status and uptime for {', '.join(failed_names)}",
                    rationale="General connectivity is functional; the issue is isolated to the remote service provider.",
                    priority=1,
                ),
                Recommendation(
                    action=f"Perform traceroute to isolate remote network hop drops",
                    command=f"tracert {failed_names[0]}",
                    rationale="Pinpoints whether routing drops occur inside the destination AS or hosting provider.",
                    priority=2,
                ),
            ]
            return self.build_issue(
                title=f"Remote Destination Outage ({', '.join(failed_names)})",
                severity=Severity.HIGH,
                confidence=0.91,
                root_cause=f"Public Internet transit is operational, but {', '.join(failed_names)} is down, rejecting connections, or experiencing remote routing failure.",
                correlated_evidence=evidence,
                recommendations=recs,
            )

        return None

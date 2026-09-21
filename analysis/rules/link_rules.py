from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class AllInterfacesDownRule(DiagnosticRule):
    rule_id = "RULE_ALL_INTERFACES_DOWN"
    name = "All Host Interfaces Down"
    category = "Physical Link"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if not ctx.all_interfaces_down():
            return None

        evidence = [
            "Every network interface on the host is reported as DOWN",
            "No non-loopback network adapter has an active operational link",
        ]
        recs = [
            Recommendation(
                action="Verify physical Ethernet cable or toggle Wi-Fi adapter ON",
                command="netsh interface show interface",
                rationale="No network traffic can egress the host without at least one operational link layer.",
                priority=1,
            ),
            Recommendation(
                action="Check network adapter driver status in device manager / kernel logs",
                command="Get-NetAdapter",
                rationale="Ensure the network interface card is recognized and enabled by the operating system.",
                priority=2,
            ),
        ]
        return self.build_issue(
            title="Complete Physical Link Disconnection (All Interfaces Down)",
            severity=Severity.CRITICAL,
            confidence=0.98,
            root_cause="All physical and virtual network adapters are down. The host is completely isolated from the local network.",
            correlated_evidence=evidence,
            recommendations=recs,
            suppressed_rules=[
                "RULE_TOTAL_INTERNET_OUTAGE",
                "RULE_NO_DEFAULT_GATEWAY",
                "RULE_DNS_FAIL_IP_HEALTHY",
                "RULE_GATEWAY_UNREACHABLE",
            ],
        )


class ActivePacketDropsRule(DiagnosticRule):
    rule_id = "RULE_ACTIVE_PACKET_DROPS"
    name = "Active Interface Packet Drops and Errors"
    category = "Physical Link"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        drop_rate = ctx.get_active_drop_rate()
        err_rate = ctx.get_active_error_rate()

        if drop_rate < 0.5 and err_rate < 0.5:
            return None

        evidence = []
        if drop_rate >= 0.5:
            evidence.append(f"Network interface actively dropping {drop_rate:.1f} packets/second")
        if err_rate >= 0.5:
            evidence.append(f"Network interface actively logging {err_rate:.1f} transmission/reception errors/second")

        confidence = 0.85
        if drop_rate > 5.0 or err_rate > 5.0:
            confidence = 0.95

        recs = [
            Recommendation(
                action="Inspect physical Ethernet cabling, patch panel, or switch port for CRC/FCS errors",
                rationale="Active frame errors and drops at the NIC level typically signify bad wiring, bad terminations, or duplex mismatches.",
                priority=1,
            ),
            Recommendation(
                action="Check wireless signal-to-noise ratio (SNR) and Wi-Fi channel congestion",
                command="netsh wlan show interfaces",
                rationale="High RF interference causes packet corruption and retransmission drops.",
                priority=2,
            ),
        ]

        return self.build_issue(
            title="Active Hardware/Link-Layer Packet Drops Detected",
            severity=Severity.HIGH,
            confidence=confidence,
            root_cause="The network interface is actively dropping or corrupting frames at the link layer, indicating hardware degradation, cabling issues, or RF interference.",
            correlated_evidence=evidence,
            recommendations=recs,
        )

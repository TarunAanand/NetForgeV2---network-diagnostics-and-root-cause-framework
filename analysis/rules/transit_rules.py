from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class SeverePacketLossRule(DiagnosticRule):
    rule_id = "RULE_SEVERE_PACKET_LOSS"
    name = "Intermittent WAN Packet Loss"
    category = "Internet Transit"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        max_loss = ctx.get_max_packet_loss()
        avg_loss = ctx.get_avg_packet_loss()

        if avg_loss < 10.0 or max_loss >= 100.0:
            return None

        evidence = [
            f"Observed average packet loss of {avg_loss:.1f}% (peak: {max_loss:.1f}%)",
        ]
        local_drops = ctx.get_active_drop_rate()
        if local_drops == 0.0:
            evidence.append("Local network interface reports zero frame drops (isolating loss to WAN / router transit)")
            cause_detail = "Upstream transit congestion, buffer overflow on the edge router/modem, or packet policing."
        else:
            evidence.append(f"Local network interface also recorded {local_drops:.1f} drops/second")
            cause_detail = "Local Wi-Fi signal interference or interface buffer exhaustion combined with transit loss."

        severity = Severity.HIGH if avg_loss >= 20.0 else Severity.MEDIUM
        confidence = 0.92 if avg_loss >= 20.0 else 0.80

        recs = [
            Recommendation(
                action="Run continuous ping to default gateway to isolate LAN vs WAN drop source",
                command=f"ping -n 20 {ctx.default_gateway() or '192.168.0.1'}",
                rationale="If 0% loss to gateway but 20% loss to 1.1.1.1, the problem is confirmed upstream on the ISP link.",
                priority=1,
            ),
            Recommendation(
                action="Power cycle cable modem / optical ONT to reset carrier line synchronization",
                rationale="Resolves transient line noise and upstream channel bonding errors.",
                priority=2,
            ),
        ]

        return self.build_issue(
            title=f"Severe Packet Loss Observed ({avg_loss:.1f}%)",
            severity=severity,
            confidence=confidence,
            root_cause=f"High packet loss is degrading transport reliability. {cause_detail}",
            correlated_evidence=evidence,
            recommendations=recs,
        )


class HighJitterLatencySpikeRule(DiagnosticRule):
    rule_id = "RULE_HIGH_JITTER_LATENCY"
    name = "High Network Jitter and Bufferbloat"
    category = "Internet Transit"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        jitter = ctx.get_max_jitter()
        if jitter < 40.0:
            return None

        # A single endpoint jitter sample is not enough to identify bufferbloat.
        # Require gateway and endpoint corroboration plus evidence of load.
        if not ctx.has_confirmed_jitter_pattern():
            return None

        # If host CPU is saturated (>85%), host bufferbloat rule handles that
        if ctx.get_cpu_percent() >= 85.0:
            return None

        evidence = [
            f"RFC 3550 Interarrival Jitter measured at {jitter:.2f} ms",
            f"Host CPU utilization is low ({ctx.get_cpu_percent():.1f}%), indicating delay variance is purely network-induced",
        ]

        recs = [
            Recommendation(
                action="Check for heavy background uploads/downloads on local network (Bufferbloat)",
                rationale="Large buffer queues in home routers cause packet queuing delay spikes during concurrent transfers.",
                priority=1,
            ),
            Recommendation(
                action="Enable Smart Queue Management (SQM / FQ-CoDel / CAKE) on your router",
                rationale="SQM prevents network buffers from bloating during active traffic surges.",
                priority=2,
            ),
        ]

        return self.build_issue(
            title="Elevated Network Jitter / Bufferbloat Detected",
            severity=Severity.MEDIUM,
            confidence=0.85,
            root_cause=f"High packet delay variation ({jitter:.2f} ms jitter) detected on healthy host CPU, indicating bufferbloat on the local gateway or congested ISP intermediate hops.",
            correlated_evidence=evidence,
            recommendations=recs,
        )

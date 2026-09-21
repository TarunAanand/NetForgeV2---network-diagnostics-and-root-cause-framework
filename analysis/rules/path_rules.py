from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class PathChangeRule(DiagnosticRule):
    rule_id = "RULE_PATH_CHANGE"
    name = "Forwarding Path Changed"
    category = "Path"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        if not ctx.path_changed():
            return None
        change = ctx.first_by_module("path_change")
        evidence = list(change.evidence) if change else ["Path fingerprint differs from baseline"]
        return self.build_issue(
            title="Forwarding Path Change Detected",
            severity=Severity.MEDIUM,
            confidence=0.88,
            root_cause="The hop sequence toward the target differs from the previously stored baseline, indicating routing change, failover, or load-balancing shift.",
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Compare current traceroute hops with the previous baseline",
                    rationale="Identifies whether the change is local (ISP peering) or remote.",
                    priority=1,
                ),
                Recommendation(
                    action="Re-run path diagnostics after a few minutes to confirm stability",
                    rationale="Transient ECMP hashing can look like a path change.",
                    priority=2,
                ),
            ],
        )


class HighHopLossRule(DiagnosticRule):
    rule_id = "RULE_HIGH_HOP_LOSS"
    name = "High Per-Hop Packet Loss"
    category = "Path"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        hops = ctx.get_path_hops()
        if not hops:
            hop_metrics = ctx.first_by_module("hop_metrics")
            hops = hop_metrics.metrics.get("hops", []) if hop_metrics else []

        bad = [h for h in hops if (h.get("loss_percent") or 0) >= 50 and h.get("address")]
        # Ignore trailing all-star patterns alone; need at least one addressed hop with high loss
        if not bad:
            return None

        evidence = [
            f"Hop {h.get('hop')} ({h.get('address')}): {h.get('loss_percent')}% loss"
            for h in bad[:5]
        ]
        return self.build_issue(
            title="Elevated Packet Loss on Path Hops",
            severity=Severity.HIGH,
            confidence=0.82,
            root_cause="One or more intermediate hops show high probe loss, which may indicate congestion, ICMP rate-limiting, or a failing router.",
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Repeat traceroute and compare ICMP vs TCP traceroute if available",
                    rationale="Some routers rate-limit ICMP TTL-exceeded replies, causing false loss.",
                    priority=1,
                ),
                Recommendation(
                    action="Correlate with end-to-end packet_loss probes to the same target",
                    rationale="True loss usually appears in end-to-end ICMP as well.",
                    priority=2,
                ),
            ],
        )


class LastMileVsCoreRule(DiagnosticRule):
    rule_id = "RULE_LAST_MILE_VS_CORE"
    name = "Last-Mile vs Core Path Degradation"
    category = "Path"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        hops = ctx.get_path_hops()
        if len(hops) < 3:
            return None

        early = hops[:2]
        late = hops[2:]
        early_loss = max((h.get("loss_percent") or 0) for h in early)
        late_loss = max((h.get("loss_percent") or 0) for h in late)
        early_rtt = max((h.get("avg_rtt_ms") or 0) for h in early)
        late_rtt = max((h.get("avg_rtt_ms") or 0) for h in late)

        # Last-mile: early hops bad, later ok-ish
        if early_loss >= 50 and late_loss < 30:
            return self.build_issue(
                title="Likely Last-Mile / Access Path Issue",
                severity=Severity.HIGH,
                confidence=0.78,
                root_cause="High loss or instability appears on the first hops while deeper hops look healthier, pointing at LAN/CPE/access network.",
                correlated_evidence=[
                    f"Early-hop max loss {early_loss}%",
                    f"Later-hop max loss {late_loss}%",
                ],
                recommendations=[
                    Recommendation(
                        action="Check local gateway, Wi-Fi, and ISP modem/ONT",
                        rationale="Access-segment faults dominate early traceroute hops.",
                        priority=1,
                    ),
                ],
            )

        # Core: late hops much worse RTT/loss
        if late_loss >= 50 and early_loss < 20 and late_rtt > early_rtt + 80:
            return self.build_issue(
                title="Likely Core / Transit Path Degradation",
                severity=Severity.HIGH,
                confidence=0.75,
                root_cause="Degradation appears deeper in the path after a healthy access segment, suggesting ISP transit or remote network issues.",
                correlated_evidence=[
                    f"Early-hop max loss {early_loss}%, RTT {early_rtt} ms",
                    f"Later-hop max loss {late_loss}%, RTT {late_rtt} ms",
                ],
                recommendations=[
                    Recommendation(
                        action="Capture path fingerprints and open a ticket with your ISP including hop list",
                        rationale="Core issues are outside local remediation.",
                        priority=1,
                    ),
                ],
            )
        return None

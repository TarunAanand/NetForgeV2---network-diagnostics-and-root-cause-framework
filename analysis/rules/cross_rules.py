from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class LinkCongestionWithPathLossRule(DiagnosticRule):
    rule_id = "RULE_LINK_CONGESTION_WITH_PATH_LOSS"
    name = "Link Congestion Correlated with Path Loss"
    category = "Cross-Domain"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        util = ctx.get_max_link_utilization()
        cong = ctx.by_module("link_congestion")
        severe = any(
            (r.metrics.get("congestion_score") or 0) >= 45 for r in cong
        )
        if util < 75 and not severe:
            return None

        hops = ctx.get_path_hops()
        hop_loss = max((h.get("loss_percent") or 0) for h in hops) if hops else 0.0
        e2e_loss = ctx.get_max_packet_loss()
        if hop_loss < 30 and e2e_loss < 5:
            return None

        evidence = [
            f"Peak link utilization {util:.1f}%",
            f"Max path hop loss {hop_loss}%",
            f"Max end-to-end loss {e2e_loss}%",
        ]
        return self.build_issue(
            title="Local Link Congestion Correlated with Path Loss",
            severity=Severity.HIGH,
            confidence=0.8,
            root_cause="High local link utilization coincides with elevated path or end-to-end loss, suggesting congestion-induced drops rather than a pure remote failure.",
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Reduce local traffic load or upgrade the saturated interface",
                    rationale="Congestion at the edge explains both util spikes and loss signals.",
                    priority=1,
                ),
            ],
            suppressed_rules=["RULE_HIGH_HOP_LOSS"],
        )

from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class ElephantFlowRule(DiagnosticRule):
    rule_id = "RULE_ELEPHANT_FLOW"
    name = "Elephant Flow Dominating Traffic"
    category = "Flow"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        flow = ctx.first_by_module("flow_analysis")
        if not flow:
            return None
        elephants = flow.metrics.get("elephant_flows") or []
        if not elephants:
            return None

        evidence = [
            f"{e.get('src')} → {e.get('dst')}: {float(e.get('share', 0))*100:.1f}% of observed bytes"
            for e in elephants[:5]
        ]
        return self.build_issue(
            title="Elephant Flow Dominating Observed Traffic",
            severity=Severity.MEDIUM,
            confidence=0.84,
            root_cause="One or a few conversations account for a large share of observed bytes, which can saturate links and inflate latency for other flows.",
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Rate-limit, reschedule, or move the elephant flow to an off-peak window",
                    rationale="Reducing a dominant flow restores headroom for interactive traffic.",
                    priority=1,
                ),
                Recommendation(
                    action="Correlate with link utilization diagnostics on the same segment",
                    rationale="Confirms whether the elephant flow is saturating a specific interface.",
                    priority=2,
                ),
            ],
        )

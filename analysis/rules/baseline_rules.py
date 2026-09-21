from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class SuddenDegradationRule(DiagnosticRule):
    rule_id = "RULE_SUDDEN_DEGRADATION"
    name = "Sudden Metric Degradation vs Baseline"
    category = "Baseline"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        bad = [
            r
            for r in ctx.by_module("baseline_delta")
            if r.metrics.get("deviated") and (r.metrics.get("ratio") or 0) >= 1.5
        ]
        if not bad:
            return None

        evidence = [r.summary for r in bad[:5]]
        worst = max(bad, key=lambda r: r.metrics.get("ratio") or 0)
        ratio = worst.metrics.get("ratio") or 0
        return self.build_issue(
            title="Sudden Performance Degradation Relative to Baseline",
            severity=Severity.HIGH if ratio >= 2.5 else Severity.MEDIUM,
            confidence=0.8,
            root_cause=(
                "One or more latency, loss, or utilization metrics are significantly worse "
                "than their recent rolling baseline, suggesting a recent change rather than "
                "a long-standing condition."
            ),
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Identify what changed recently (config, load, path, or interface state)",
                    rationale="Baseline deviations imply a delta from the recent healthy window.",
                    priority=1,
                ),
                Recommendation(
                    action="Re-run host, link, and path diagnostics against the affected target",
                    rationale="Localizes whether the regression is endpoint, link, or path scoped.",
                    priority=2,
                ),
            ],
        )

from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class LinkSaturationRule(DiagnosticRule):
    rule_id = "RULE_LINK_SATURATION"
    name = "Link Utilization Saturation"
    category = "Link"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        util = ctx.get_max_link_utilization()
        if util < 75:
            return None

        evidence = [f"Peak observed interface utilization: {util:.1f}%"]
        for r in ctx.by_module("link_utilization"):
            u = r.metrics.get("util_percent")
            if u is not None and u >= 75:
                evidence.append(f"{r.target}: {u:.1f}% util")

        severity = Severity.HIGH if util >= 90 else Severity.MEDIUM
        return self.build_issue(
            title="Network Link Near or At Capacity",
            severity=severity,
            confidence=0.86 if util >= 90 else 0.78,
            root_cause="One or more local interfaces are carrying traffic close to their negotiated capacity, which causes queueing delay and drops.",
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Identify top talkers or background uploads/downloads on the saturated interface",
                    rationale="Saturation is often a single elephant flow or backup job.",
                    priority=1,
                ),
                Recommendation(
                    action="Upgrade link speed or enable QoS for latency-sensitive traffic",
                    rationale="Persistent high utilization needs capacity or prioritization.",
                    priority=2,
                ),
            ],
        )


class InterfaceErrorsRule(DiagnosticRule):
    rule_id = "RULE_INTERFACE_ERRORS"
    name = "Interface Errors and Drops"
    category = "Link"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        drop_rate = ctx.get_max_link_drop_rate()
        err_results = ctx.by_module("link_errors")
        max_err = 0.0
        evidence = []
        for r in err_results:
            d = float(r.metrics.get("drops_per_sec", 0.0))
            e = float(r.metrics.get("errors_per_sec", 0.0))
            max_err = max(max_err, e)
            if d >= 0.5 or e >= 0.5:
                evidence.append(f"{r.target}: {d:.2f} drops/s, {e:.2f} errs/s")

        if drop_rate < 0.5 and max_err < 0.5:
            return None

        return self.build_issue(
            title="Active Interface Errors or Drops",
            severity=Severity.HIGH,
            confidence=0.9,
            root_cause="NIC counters show active drops or errors, typically from cabling, duplex mismatch, driver issues, or RF interference.",
            correlated_evidence=evidence or [f"Drop rate {drop_rate:.2f}/s"],
            recommendations=[
                Recommendation(
                    action="Inspect cabling, switch port, and NIC driver/firmware",
                    rationale="Link-layer errors rarely self-heal without physical or driver remediation.",
                    priority=1,
                ),
            ],
            suppressed_rules=["RULE_ACTIVE_PACKET_DROPS"],
        )

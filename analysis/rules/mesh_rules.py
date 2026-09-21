from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import DiagnosticStatus, Severity


class MeshPartitionRule(DiagnosticRule):
    rule_id = "RULE_MESH_PARTITION"
    name = "Mesh Target Partition or Widespread Failure"
    category = "Mesh"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        summary = ctx.first_by_module("mesh_summary")
        if not summary:
            return None

        failed = int(summary.metrics.get("failed_count") or 0)
        total = int(summary.metrics.get("target_count") or 0)
        if total == 0 or failed == 0:
            return None

        # Partial vs total mesh failure
        if failed == total:
            title = "Total Mesh Reachability Failure"
            severity = Severity.CRITICAL
            confidence = 0.9
            cause = (
                "All configured mesh targets are unreachable from this vantage point, "
                "indicating a local uplink failure or a broad network outage."
            )
        else:
            title = "Partial Mesh Partition"
            severity = Severity.HIGH
            confidence = 0.82
            cause = (
                f"{failed}/{total} mesh targets failed while others remain reachable, "
                "suggesting a regional partition, selective ACL, or destination-specific outage."
            )

        failed_targets = [
            r.target
            for r in ctx.by_module("mesh_ping")
            if r.status == DiagnosticStatus.FAILED and r.target
        ]
        evidence = [summary.summary]
        if failed_targets:
            evidence.append("Failed targets: " + ", ".join(failed_targets[:8]))

        return self.build_issue(
            title=title,
            severity=severity,
            confidence=confidence,
            root_cause=cause,
            correlated_evidence=evidence,
            recommendations=[
                Recommendation(
                    action="Compare failed vs healthy mesh targets for shared subnet/VLAN/path",
                    rationale="Shared attributes among failures localize the partition boundary.",
                    priority=1,
                ),
                Recommendation(
                    action="Verify local gateway and uplink before blaming remote destinations",
                    rationale="Total mesh failure is often local; partial failure is often path/ACL.",
                    priority=2,
                ),
            ],
        )

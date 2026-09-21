from __future__ import annotations

from analysis.context import AnalysisContext
from analysis.models import DiagnosedIssue, Recommendation
from analysis.rule import DiagnosticRule
from core.result import Severity


class HostSaturationBufferbloatRule(DiagnosticRule):
    rule_id = "RULE_HOST_RESOURCE_SATURATION"
    name = "Host Resource Exhaustion Degrading Network Stack"
    category = "Host Resources"

    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        cpu = ctx.get_cpu_percent()
        ram = ctx.get_memory_percent()

        if cpu < 85.0 and ram < 90.0:
            return None

        evidence = []
        if cpu >= 85.0:
            evidence.append(f"Host CPU utilization is critical at {cpu:.1f}%")
        if ram >= 90.0:
            evidence.append(f"Host Memory utilization is critical at {ram:.1f}%")

        jitter = ctx.get_max_jitter()
        if jitter > 20.0:
            evidence.append(f"Concurrent elevated network jitter: {jitter:.2f} ms")

        severity = Severity.HIGH if (cpu >= 95.0 or ram >= 95.0) else Severity.MEDIUM
        confidence = 0.92 if (cpu >= 90.0 and ram >= 90.0) else 0.85

        recs = [
            Recommendation(
                action="Identify and terminate runaway host processes consuming CPU or memory",
                command="Get-Process | Sort-Object CPU -Descending | Select-Object -First 10",
                rationale="CPU starvation delays network driver interrupt handling (DPC/ISR) and causes packet buffer drops.",
                priority=1,
            ),
            Recommendation(
                action="Inspect system swap/pagefile thrashing",
                rationale="Memory paging freezes userspace networking threads and inflates socket processing latency.",
                priority=2,
            ),
        ]

        return self.build_issue(
            title="Host Resource Exhaustion Impacting Network Performance",
            severity=severity,
            confidence=confidence,
            root_cause="Severe host CPU or memory saturation is preventing timely processing of network packets, masquerading as or compounding network latency.",
            correlated_evidence=evidence,
            recommendations=recs,
        )

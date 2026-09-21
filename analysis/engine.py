from __future__ import annotations

import datetime
import logging

from analysis.context import AnalysisContext
from analysis.evidence import EvidenceCorrelator
from analysis.models import ConfidenceLevel, DiagnosedIssue, DiagnosisReport, Recommendation
from analysis.rule import DiagnosticRule
from analysis.rules import DEFAULT_RULES
from core.result import DiagnosticResult, DiagnosticStatus, Severity

logger = logging.getLogger(__name__)


class RuleEngine:
    """
    Diagnostic Rule Engine.
    Executes modular rules against diagnostic results, correlates multi-layer evidence,
    resolves issue conflicts/subsumption, and synthesizes root-cause verdicts.
    """

    def __init__(self, rules: list[DiagnosticRule] | None = None):
        self.rules = rules if rules is not None else list(DEFAULT_RULES)
        self.rule_errors: list[dict[str, str]] = []

    def add_rule(self, rule: DiagnosticRule) -> None:
        self.rules.append(rule)

    def analyze(
        self,
        results: list[DiagnosticResult],
        target_host: str = "google.com",
    ) -> DiagnosisReport:
        ctx = AnalysisContext(results)
        raw_issues: list[DiagnosedIssue] = []
        self.rule_errors = []

        anomalies = EvidenceCorrelator.correlate(results)
        if anomalies:
            raw_issues.extend(
                [
                    DiagnosedIssue(
                        rule_id=f"EVIDENCE_ANOMALY_{idx}",
                        title=anomaly.title,
                        category="Evidence",
                        severity=Severity.LOW,
                        confidence=anomaly.confidence,
                        confidence_level=ConfidenceLevel.from_score(anomaly.confidence),
                        root_cause=anomaly.summary,
                        correlated_evidence=anomaly.evidence,
                        recommendations=[
                            Recommendation(
                                action="Collect a second probe window before escalating",
                                rationale="This is a low-confidence anomaly; corroborating evidence should be collected before treating it as a fault.",
                                priority=1,
                            )
                        ],
                        suppressed_rules=[],
                    )
                    for idx, anomaly in enumerate(anomalies, start=1)
                ]
            )

        # 1. Run all registered rules
        for rule in self.rules:
            try:
                issue = rule.evaluate(ctx)
                if issue is not None:
                    raw_issues.append(issue)
            except Exception as exc:
                # Rules are isolated; one rule failure never crashes the engine
                err = {"rule_id": rule.rule_id, "error": str(exc)}
                self.rule_errors.append(err)
                logger.exception("Rule %s failed during evaluation", rule.rule_id)

        # 2. Conflict & Subsumption Resolution
        # If a higher-order issue suppressed child rules, remove them
        suppressed_ids = set()
        for issue in raw_issues:
            suppressed_ids.update(issue.suppressed_rules)

        active_issues = [
            issue for issue in raw_issues
            if issue.rule_id not in suppressed_ids
        ]

        # 3. Sort issues by Severity and Confidence
        severity_order = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }
        active_issues.sort(
            key=lambda x: (severity_order.get(x.severity, 99), -x.confidence)
        )

        # 4. Count probe results
        healthy_count = sum(1 for r in results if r.status == DiagnosticStatus.HEALTHY)
        degraded_count = sum(1 for r in results if r.status == DiagnosticStatus.DEGRADED)
        failed_count = sum(1 for r in results if r.status == DiagnosticStatus.FAILED)
        substantive_degraded_count = sum(
            1
            for r in results
            if r.status == DiagnosticStatus.DEGRADED
            and r.module not in {"path_change", "traceroute"}
        )

        # 5. Determine Overall Report Status and Verdict
        confirmed_failures = [
            i for i in active_issues
            if i.severity in {Severity.CRITICAL, Severity.HIGH} and i.confidence >= 0.75
        ]
        if confirmed_failures:
            status = DiagnosticStatus.FAILED
        elif substantive_degraded_count > 0 or any(
            issue.severity not in {Severity.LOW, Severity.INFO}
            for issue in active_issues
        ):
            status = DiagnosticStatus.DEGRADED
        else:
            status = DiagnosticStatus.HEALTHY

        verdict = self._synthesize_verdict(active_issues, status)

        # 6. Extract key positive observations
        observations = []
        if ctx.has_active_interface():
            observations.append("Network interface link is UP and assigned an IP address.")
        if ctx.has_default_gateway():
            observations.append(f"Default gateway route ({ctx.default_gateway()}) is active.")
        if ctx.is_dns_resolution_working():
            observations.append(f"DNS resolution succeeded for {target_host}.")
        if ctx.is_ip_connectivity_working():
            observations.append("Raw Layer 3/4 Internet connectivity is operational.")

        return DiagnosisReport(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            target_host=target_host,
            total_probes=len(results),
            healthy_probes=healthy_count,
            degraded_probes=degraded_count,
            failed_probes=failed_count,
            status=status,
            verdict=verdict,
            issues=active_issues,
            key_observations=observations,
            metadata={"rule_errors": self.rule_errors} if self.rule_errors else {},
        )

    def _synthesize_verdict(
        self,
        issues: list[DiagnosedIssue],
        status: DiagnosticStatus,
    ) -> str:
        if not issues:
            return "All diagnostic probes passed. Host network stack, local gateway, DNS, and transit appear fully operational."

        if all(issue.severity in {Severity.LOW, Severity.INFO} for issue in issues):
            return (
                "No confirmed network fault. Minor observations were recorded and "
                "require corroboration before escalation."
            )

        primary = issues[0]
        if len(issues) == 1:
            return f"Primary root cause identified: {primary.title} (Confidence: {int(primary.confidence * 100)}%)."

        secondary_titles = ", ".join(f"'{i.title}'" for i in issues[1:3])
        return (
            f"Multiple network issues detected. Primary root cause: '{primary.title}' "
            f"({int(primary.confidence * 100)}% confidence). Secondary contributing factors: {secondary_titles}."
        )

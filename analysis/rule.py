from __future__ import annotations

from abc import ABC, abstractmethod
from analysis.context import AnalysisContext
from analysis.models import ConfidenceLevel, DiagnosedIssue, Recommendation
from core.result import Severity


class DiagnosticRule(ABC):
    """
    Abstract base class for all NetForge diagnostic rules.
    Each rule implements pattern matching, cross-module evidence correlation,
    confidence scoring, and prioritized remediation actions.
    """

    rule_id: str = "BASE_RULE"
    name: str = "Base Diagnostic Rule"
    category: str = "General"

    @abstractmethod
    def evaluate(self, ctx: AnalysisContext) -> DiagnosedIssue | None:
        """
        Inspect the diagnostic observations in AnalysisContext.
        Returns a DiagnosedIssue if the failure pattern matches, or None.
        """
        raise NotImplementedError

    def build_issue(
        self,
        title: str,
        severity: Severity,
        confidence: float,
        root_cause: str,
        correlated_evidence: list[str],
        recommendations: list[Recommendation],
        suppressed_rules: list[str] | None = None,
    ) -> DiagnosedIssue:
        bounded_conf = max(0.05, min(0.99, round(confidence, 2)))
        return DiagnosedIssue(
            rule_id=self.rule_id,
            title=title,
            category=self.category,
            severity=severity,
            confidence=bounded_conf,
            confidence_level=ConfidenceLevel.from_score(bounded_conf),
            root_cause=root_cause,
            correlated_evidence=correlated_evidence,
            recommendations=recommendations,
            suppressed_rules=suppressed_rules or [],
        )

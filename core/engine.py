from collections import Counter

from core.result import (
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
)


class DiagnosticEngine:

    def __init__(
        self,
        results: list[DiagnosticResult],
    ):
        self.results = results

    def summarize(self) -> dict:

        status_counts = Counter(
            result.status
            for result in self.results
        )

        severity_counts = Counter(
            result.severity
            for result in self.results
        )

        return {
            "total_checks": len(self.results),
            "healthy": status_counts[
                DiagnosticStatus.HEALTHY
            ],
            "degraded": status_counts[
                DiagnosticStatus.DEGRADED
            ],
            "failed": status_counts[
                DiagnosticStatus.FAILED
            ],
            "unknown": status_counts[
                DiagnosticStatus.UNKNOWN
            ],
            "critical": severity_counts[
                Severity.CRITICAL
            ],
            "high": severity_counts[
                Severity.HIGH
            ],
            "medium": severity_counts[
                Severity.MEDIUM
            ],
        }

    def find_failures(
        self,
    ) -> list[DiagnosticResult]:

        return [
            result
            for result in self.results
            if result.status
            in {
                DiagnosticStatus.FAILED,
                DiagnosticStatus.DEGRADED,
            }
        ]

    def generate_findings(self) -> list[str]:

        findings = []

        failures = self.find_failures()

        for result in failures:
            msg = f"{result.module}: {result.summary}"
            if result.errors:
                msg += f" (Details: {'; '.join(result.errors)})"
            elif result.warnings:
                msg += f" (Warning: {'; '.join(result.warnings)})"
            findings.append(msg)

        return findings
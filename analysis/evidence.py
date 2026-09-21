from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from core.result import DiagnosticResult, DiagnosticStatus


class EvidenceAnomalyKind(str, Enum):
    INTERMEDIATE_HOP_LOSS = "intermediate_hop_loss"
    PATH_CHANGE = "path_change"
    LATENCY_WIGGLE = "latency_wiggle"


@dataclass
class EvidenceAnomaly:
    kind: EvidenceAnomalyKind
    title: str
    summary: str
    confidence: float
    severity: str = "low"
    confirmed: bool = False
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "summary": self.summary,
            "confidence": round(float(self.confidence), 2),
            "severity": self.severity,
            "confirmed": self.confirmed,
            "evidence": list(self.evidence),
        }


class EvidenceCorrelator:
    """Convert raw diagnostics into low-confidence anomaly observations.

    The goal is to separate minor, unconfirmed signals from true end-to-end faults:
    path churn or isolated intermediate-hop loss should remain visible as anomalies,
    but they should not be reported as hard failures unless a corroborating end-to-end
    signal also appears.
    """

    @staticmethod
    def correlate(results: list[DiagnosticResult]) -> list[EvidenceAnomaly]:
        anomalies: list[EvidenceAnomaly] = []

        connectivity_healthy = any(
            r.module == "connectivity" and r.status == DiagnosticStatus.HEALTHY
            for r in results
        )
        packet_loss = max(
            (
                float(r.metrics.get("packet_loss_percent"))
                for r in results
                if r.module == "packet_loss"
                and r.metrics.get("packet_loss_percent") is not None
            ),
            default=100.0,
        )
        end_to_end_failed = any(
            r.module in {"connectivity", "packet_loss"}
            and r.status in {DiagnosticStatus.FAILED, DiagnosticStatus.DEGRADED}
            for r in results
        )

        traceroute = next((r for r in results if r.module == "traceroute"), None)
        if traceroute and isinstance(traceroute.metrics.get("hops"), list):
            hops = traceroute.metrics["hops"]
            bad_hops = [
                h for h in hops
                if (h.get("loss_percent") or 0) >= 50 and h.get("address")
            ]
            if bad_hops and connectivity_healthy and packet_loss < 30 and not end_to_end_failed:
                anomalies.append(
                    EvidenceAnomaly(
                        kind=EvidenceAnomalyKind.INTERMEDIATE_HOP_LOSS,
                        title="Likely ICMP suppression or transient ECMP churn",
                        summary="Intermediate hops show elevated loss without end-to-end connectivity failure.",
                        confidence=0.62,
                        severity="low",
                        confirmed=False,
                        evidence=[
                            f"Hop {h.get('hop')} ({h.get('address')}): {h.get('loss_percent')}% loss"
                            for h in bad_hops[:3]
                        ],
                    )
                )

        path_change = next(
            (r for r in results if r.module == "path_change" and r.metrics.get("changed")),
            None,
        )
        if path_change and not end_to_end_failed:
            anomalies.append(
                EvidenceAnomaly(
                    kind=EvidenceAnomalyKind.PATH_CHANGE,
                    title="Path change without confirmed service failure",
                    summary="The forwarding path differs from the stored baseline, but no end-to-end failure is confirmed.",
                    confidence=0.58,
                    severity="low",
                    confirmed=False,
                    evidence=[
                        "Path fingerprint changed from the prior baseline",
                        path_change.summary,
                    ],
                )
            )

        latency = next((r for r in results if r.module == "latency"), None)
        if latency and latency.metrics.get("jitter_ms") is not None:
            jitter = float(latency.metrics["jitter_ms"])
            if jitter > 0 and packet_loss < 30 and not end_to_end_failed:
                anomalies.append(
                    EvidenceAnomaly(
                        kind=EvidenceAnomalyKind.LATENCY_WIGGLE,
                        title="Statistically weak latency wiggle",
                        summary="Latency jitter is elevated but does not cross a confirmed-failure threshold.",
                        confidence=0.55,
                        severity="low",
                        confirmed=False,
                        evidence=[
                            f"Observed jitter {jitter:.2f} ms without corresponding packet loss or reachability failure",
                        ],
                    )
                )

        return anomalies

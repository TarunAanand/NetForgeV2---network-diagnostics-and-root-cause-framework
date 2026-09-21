"""Flow record analysis over passive telemetry."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from rich.console import Console
from rich.table import Table

from core.result import DiagnosticResult, DiagnosticStatus, Severity
from ingest.sflow import load_sflow_records

console = Console()

# Flows larger than this share of total bytes are "elephants"
ELEPHANT_SHARE = 0.25


def analyze_flows(records: list[dict[str, Any]]) -> DiagnosticResult:
    if not records:
        return DiagnosticResult(
            module="flow_analysis",
            category="flow",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.INFO,
            summary="No flow records to analyze",
            metrics={"flow_count": 0},
        )

    by_pair: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"bytes": 0.0, "packets": 0.0}
    )
    total_bytes = 0.0
    for rec in records:
        src = str(rec.get("src") or rec.get("source") or "unknown")
        dst = str(rec.get("dst") or rec.get("destination") or "unknown")
        nbytes = float(rec.get("bytes") or rec.get("octetDeltaCount") or 0)
        pkts = float(rec.get("packets") or rec.get("packetDeltaCount") or 0)
        by_pair[(src, dst)]["bytes"] += nbytes
        by_pair[(src, dst)]["packets"] += pkts
        total_bytes += nbytes

    ranked = sorted(by_pair.items(), key=lambda kv: kv[1]["bytes"], reverse=True)
    top = [
        {
            "src": pair[0],
            "dst": pair[1],
            "bytes": stats["bytes"],
            "packets": stats["packets"],
            "share": round(stats["bytes"] / total_bytes, 4) if total_bytes else 0.0,
        }
        for pair, stats in ranked[:10]
    ]

    elephants = [t for t in top if t["share"] >= ELEPHANT_SHARE]
    if elephants:
        status, severity = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        summary = f"{len(elephants)} elephant flow(s) dominate traffic"
    else:
        status, severity = DiagnosticStatus.HEALTHY, Severity.INFO
        summary = f"Analyzed {len(records)} flow records; no elephant flows"

    return DiagnosticResult(
        module="flow_analysis",
        category="flow",
        status=status,
        severity=severity,
        summary=summary,
        metrics={
            "flow_count": len(records),
            "total_bytes": total_bytes,
            "top_talkers": top,
            "elephant_flows": elephants,
            "elephant_count": len(elephants),
        },
        evidence=[
            f"Total bytes observed: {int(total_bytes)}",
            f"Unique conversations: {len(by_pair)}",
        ],
        warnings=[f"Elephant flow {e['src']} → {e['dst']} ({e['share']*100:.1f}%)" for e in elephants],
    )


def collect_flow_diagnostics(path: str) -> list[DiagnosticResult]:
    records = load_sflow_records(path)
    return [analyze_flows(records)]


def run_flow_top(path: str) -> DiagnosticResult:
    result = analyze_flows(load_sflow_records(path))
    table = Table(title="Top Talkers")
    table.add_column("Src")
    table.add_column("Dst")
    table.add_column("Bytes")
    table.add_column("Share")
    for row in result.metrics.get("top_talkers", [])[:10]:
        table.add_row(
            row["src"],
            row["dst"],
            str(int(row["bytes"])),
            f'{row["share"]*100:.1f}%',
        )
    console.print(table)
    return result

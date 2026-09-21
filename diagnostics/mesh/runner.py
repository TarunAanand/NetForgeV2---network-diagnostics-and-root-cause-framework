"""Ping-mesh topology loading and local multi-target runner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from core.result import DiagnosticResult, DiagnosticStatus, Severity
from diagnostics.host.packet_loss import ping_host

console = Console()


def load_topology(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if "targets" not in data and "edges" not in data:
        raise ValueError("Mesh topology requires 'targets' list and/or 'edges' list")
    return data


def run_local_mesh(
    targets: list[str] | None = None,
    topology_path: str | Path | None = None,
    count: int = 3,
) -> list[DiagnosticResult]:
    """
    Run ICMP probes from this host to many targets (single-vantage mesh).
    Remote multi-agent mesh uses collectors.agent_api later.
    """
    if topology_path:
        topo = load_topology(topology_path)
        targets = list(topo.get("targets") or [])
        for edge in topo.get("edges") or []:
            if isinstance(edge, dict) and edge.get("dst"):
                targets.append(str(edge["dst"]))
            elif isinstance(edge, (list, tuple)) and len(edge) >= 2:
                targets.append(str(edge[1]))

    targets = list(dict.fromkeys(targets or ["1.1.1.1", "8.8.8.8"]))
    results: list[DiagnosticResult] = []
    failed = 0
    for t in targets:
        r = ping_host(t, count=count)
        # Retag as mesh observation
        results.append(
            DiagnosticResult(
                module="mesh_ping",
                category="mesh",
                status=r.status,
                severity=r.severity,
                summary=r.summary,
                target=t,
                metrics=r.metrics,
                evidence=r.evidence,
                errors=r.errors,
                warnings=r.warnings,
                metadata={**r.metadata, "mesh": True},
            )
        )
        if r.status == DiagnosticStatus.FAILED:
            failed += 1

    # Aggregate mesh health
    if failed == len(targets) and targets:
        agg_status, agg_sev = DiagnosticStatus.FAILED, Severity.CRITICAL
        summary = "All mesh targets unreachable"
    elif failed > 0:
        agg_status, agg_sev = DiagnosticStatus.DEGRADED, Severity.HIGH
        summary = f"{failed}/{len(targets)} mesh targets failed"
    else:
        agg_status, agg_sev = DiagnosticStatus.HEALTHY, Severity.INFO
        summary = f"All {len(targets)} mesh targets reachable"

    results.append(
        DiagnosticResult(
            module="mesh_summary",
            category="mesh",
            status=agg_status,
            severity=agg_sev,
            summary=summary,
            metrics={
                "target_count": len(targets),
                "failed_count": failed,
                "targets": targets,
            },
            evidence=[summary],
        )
    )
    return results


def run_mesh_diagnostics(
    targets: list[str] | None = None,
    topology_path: str | Path | None = None,
    count: int = 3,
) -> list[DiagnosticResult]:
    results = run_local_mesh(targets=targets, topology_path=topology_path, count=count)
    table = Table(title="Ping Mesh")
    table.add_column("Target")
    table.add_column("Loss")
    table.add_column("Status")
    for r in results:
        if r.module != "mesh_ping":
            continue
        loss = r.metrics.get("packet_loss_percent")
        table.add_row(r.target or "-", f"{loss}%" if loss is not None else "-", r.status.value)
    console.print(table)
    return results

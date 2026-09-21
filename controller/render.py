"""Rich terminal renderers for controller-plane objects (M4-M6).

Keeps presentation of controller-owned models (bindings, incidents, alerts,
schedules, augmented topology) next to the controller rather than in
``analysis.formatter`` so the low-level analysis package stays free of a
dependency on the control plane. Multi-vantage reports are delegated to the
existing :func:`analysis.formatter.render_multivantage_report`.
"""

from __future__ import annotations

import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from analysis.formatter import render_multivantage_report
from analysis.multivantage import MultiVantageReport
from controller.correlation import HostPortBinding
from controller.monitoring import Alert, AlertState, Incident, IncidentState, ScheduleEntry
from controller.topology import NetworkTopology
from core.observation import EvidenceQuality
from core.result import Severity

_SEVERITY_COLOR = {
    Severity.CRITICAL: "bold red",
    Severity.HIGH: "red",
    Severity.MEDIUM: "yellow",
    Severity.LOW: "blue",
    Severity.INFO: "dim",
}

_INCIDENT_STATE_COLOR = {
    IncidentState.OPEN: "red",
    IncidentState.ACKNOWLEDGED: "yellow",
    IncidentState.RESOLVED: "green",
}

_ALERT_STATE_COLOR = {
    AlertState.FIRING: "red",
    AlertState.RESOLVED: "green",
}

_QUALITY_COLOR = {
    EvidenceQuality.CORROBORATED: "bold green",
    EvidenceQuality.VERIFIED: "green",
    EvidenceQuality.PARSED_EXTERNAL: "cyan",
    EvidenceQuality.SINGLE_SOURCE: "yellow",
    EvidenceQuality.UNKNOWN: "dim",
}


def _fmt_ts(value: float | None) -> str:
    if not value:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def _conf_color(confidence: float) -> str:
    if confidence >= 0.9:
        return "green"
    if confidence >= 0.75:
        return "yellow"
    return "red"


def render_bindings(bindings: list[HostPortBinding], console: Console | None = None) -> None:
    """Render host-to-switch-port correlation bindings."""
    console = console or Console()
    if not bindings:
        console.print("[yellow]No host-to-switch-port bindings.[/yellow]")
        return
    table = Table(title=f"Host-to-Switch-Port Bindings ({len(bindings)})")
    table.add_column("Host")
    table.add_column("Switch")
    table.add_column("Port")
    table.add_column("ifIndex")
    table.add_column("Method")
    table.add_column("Conf")
    table.add_column("Quality")
    for b in bindings:
        conf = f"{int(b.confidence * 100)}%"
        table.add_row(
            b.host_node_id,
            b.switch_node_id,
            b.switch_port or "-",
            str(b.if_index) if b.if_index is not None else "-",
            b.method.value,
            f"[{_conf_color(b.confidence)}]{conf}[/{_conf_color(b.confidence)}]",
            f"[{_QUALITY_COLOR.get(b.evidence_quality, 'white')}]{b.evidence_quality.value}[/{_QUALITY_COLOR.get(b.evidence_quality, 'white')}]",
        )
    console.print(table)


def render_incidents(incidents: list[Incident], console: Console | None = None) -> None:
    """Render the incident list."""
    console = console or Console()
    if not incidents:
        console.print("[green]No incidents.[/green]")
        return
    table = Table(title=f"Incidents ({len(incidents)})")
    table.add_column("Incident")
    table.add_column("Service")
    table.add_column("Target")
    table.add_column("State")
    table.add_column("Severity")
    table.add_column("Alerts")
    table.add_column("Opened")
    for inc in incidents:
        state_color = _INCIDENT_STATE_COLOR.get(inc.state, "white")
        sev_color = _SEVERITY_COLOR.get(inc.severity, "white")
        table.add_row(
            inc.incident_id[:8],
            inc.service_id,
            inc.target or "-",
            f"[{state_color}]{inc.state.value}[/{state_color}]",
            f"[{sev_color}]{inc.severity.value}[/{sev_color}]",
            str(len(inc.alert_ids)),
            _fmt_ts(inc.opened_at),
        )
    console.print(table)


def render_alerts(alerts: list[Alert], console: Console | None = None) -> None:
    """Render the alert list."""
    console = console or Console()
    if not alerts:
        console.print("[green]No alerts.[/green]")
        return
    table = Table(title=f"Alerts ({len(alerts)})")
    table.add_column("Alert")
    table.add_column("Service")
    table.add_column("Localization")
    table.add_column("State")
    table.add_column("Severity")
    table.add_column("Conf")
    table.add_column("Regr")
    table.add_column("Message")
    for a in alerts:
        state_color = _ALERT_STATE_COLOR.get(a.state, "white")
        sev_color = _SEVERITY_COLOR.get(a.severity, "white")
        conf = f"{int(a.confidence * 100)}%"
        table.add_row(
            a.alert_id[:8],
            a.service_id,
            a.localization.value if a.localization else "-",
            f"[{state_color}]{a.state.value}[/{state_color}]",
            f"[{sev_color}]{a.severity.value}[/{sev_color}]",
            f"[{_conf_color(a.confidence)}]{conf}[/{_conf_color(a.confidence)}]",
            "[red]yes[/red]" if a.regression else "no",
            a.message,
        )
    console.print(table)


def render_schedules(schedules: list[ScheduleEntry], console: Console | None = None) -> None:
    """Render the schedule list."""
    console = console or Console()
    if not schedules:
        console.print("[yellow]No schedules configured.[/yellow]")
        return
    table = Table(title=f"Monitor Schedules ({len(schedules)})")
    table.add_column("Schedule")
    table.add_column("Service")
    table.add_column("Interval")
    table.add_column("Count")
    table.add_column("Enabled")
    table.add_column("Last run")
    table.add_column("Next run")
    for s in schedules:
        enabled = "[green]yes[/green]" if s.enabled else "[dim]no[/dim]"
        table.add_row(
            s.schedule_id[:8],
            s.service_id,
            f"{s.interval_seconds:g}s",
            str(s.count),
            enabled,
            _fmt_ts(s.last_run_at),
            _fmt_ts(s.next_run_at),
        )
    console.print(table)


def render_topology(topology: NetworkTopology, console: Console | None = None) -> None:
    """Render a compact topology summary (nodes/edges by role)."""
    console = console or Console()
    roles: dict[str, int] = {}
    for node in topology.nodes:
        key = node.role.value if hasattr(node.role, "value") else str(node.role)
        roles[key] = roles.get(key, 0) + 1
    role_lines = "\n".join(f"  [bold]{role}:[/bold] {count}" for role, count in sorted(roles.items()))
    console.print(
        Panel(
            f"[bold]Topology:[/bold] {topology.name}\n"
            f"[bold]Nodes:[/bold] {len(topology.nodes)}    [bold]Edges:[/bold] {len(topology.edges)}\n\n"
            f"{role_lines or '  (none)'}",
            title="[bold cyan]NetForge Topology[/bold cyan]",
        )
    )


def render_reports(reports: list[MultiVantageReport], console: Console | None = None) -> None:
    """Render a batch of multi-vantage reports using the shared renderer."""
    console = console or Console()
    if not reports:
        console.print("[yellow]No reports produced (nothing was due).[/yellow]")
        return
    for report in reports:
        render_multivantage_report(report, console=console)

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from analysis.models import DiagnosisReport
from analysis.multivantage import FaultLocalization, MultiVantageReport
from core.result import DiagnosticStatus, Severity


def render_diagnosis_report(report: DiagnosisReport, console: Console | None = None) -> None:
    console = console or Console()

    # Determine status color
    if report.status == DiagnosticStatus.HEALTHY:
        status_color = "green"
        status_text = "HEALTHY (No Active Pathologies)"
    elif report.status == DiagnosticStatus.DEGRADED:
        status_color = "yellow"
        status_text = "DEGRADED (Performance Warning)"
    else:
        status_color = "red"
        status_text = "FAILURE (Root-Cause Issues Identified)"

    # Header Panel
    console.print()
    header_content = (
        f"[bold]Target Host:[/bold] {report.target_host}    "
        f"[bold]Probes Evaluated:[/bold] {report.total_probes}    "
        f"[bold]Status:[/bold] [{status_color}]{status_text}[/{status_color}]\n\n"
        f"[bold]Executive Verdict:[/bold]\n[{status_color}]{report.verdict}[/{status_color}]"
    )
    console.print(
        Panel(
            header_content,
            title="[bold cyan]NetForge Root-Cause Diagnostic Analysis[/bold cyan]",
            border_style=status_color,
        )
    )

    # Positive Highlights / Key Observations
    if report.key_observations:
        console.print("\n[bold]Baseline System State:[/bold]")
        for obs in report.key_observations:
            console.print(f"  [green][OK][/green] {obs}")

    # Diagnosed Issues
    if not report.issues:
        console.print(
            "\n[green]All network layers evaluated healthy. No actionable anomalies found.[/green]\n"
        )
        return

    console.print(f"\n[bold yellow]Diagnosed Root Causes ({len(report.issues)}):[/bold yellow]\n")

    severity_colors = {
        Severity.CRITICAL: "bold red",
        Severity.HIGH: "red",
        Severity.MEDIUM: "yellow",
        Severity.LOW: "blue",
        Severity.INFO: "dim",
    }

    confidence_colors = {
        "VERY HIGH": "bold green",
        "HIGH": "green",
        "MEDIUM": "yellow",
        "LOW": "red",
    }

    for idx, issue in enumerate(report.issues, 1):
        sev_color = severity_colors.get(issue.severity, "white")
        conf_color = confidence_colors.get(issue.confidence_level.value, "white")
        conf_pct = int(issue.confidence * 100)

        card_title = (
            f"[{sev_color}][{issue.severity.value.upper()}][/{sev_color}] "
            f"[bold]{idx}. {issue.title}[/bold] "
            f"([cyan]{issue.category}[/cyan]) -- "
            f"[{conf_color}]{conf_pct}% Confidence ({issue.confidence_level.value})[/{conf_color}]"
        )

        issue_body = [
            f"[bold]Root Cause Analysis:[/bold]\n{issue.root_cause}",
        ]

        if issue.correlated_evidence:
            evidence_lines = "\n".join(f"  * {e}" for e in issue.correlated_evidence)
            issue_body.append(f"\n[bold]Correlated Evidence:[/bold]\n{evidence_lines}")

        if issue.recommendations:
            rec_lines = []
            for r_idx, rec in enumerate(issue.recommendations, 1):
                prio_tag = "[red][Urgent][/red] " if rec.priority == 1 else "[dim][Secondary][/dim] "
                cmd_line = f"\n     Command: [bold cyan]{rec.command}[/bold cyan]" if rec.command else ""
                rec_lines.append(
                    f"  {r_idx}. {prio_tag}[bold]{rec.action}[/bold]{cmd_line}\n"
                    f"     [dim]Why: {rec.rationale}[/dim]"
                )
            issue_body.append(f"\n[bold green]Recommended Actions:[/bold green]\n" + "\n".join(rec_lines))

        console.print(
            Panel(
                "\n".join(issue_body),
                title=card_title,
                title_align="left",
                border_style="yellow" if issue.severity in {Severity.MEDIUM, Severity.LOW} else "red",
            )
        )
        console.print()


_LOCALIZATION_STYLE = {
    FaultLocalization.HEALTHY: ("green", "HEALTHY"),
    FaultLocalization.TARGET_SIDE: ("bold red", "TARGET-SIDE"),
    FaultLocalization.PATH_SHARED: ("red", "SHARED-PATH"),
    FaultLocalization.SOURCE_SIDE: ("yellow", "SOURCE-SIDE"),
    FaultLocalization.VANTAGE_ISOLATED: ("yellow", "ISOLATED-VANTAGE"),
    FaultLocalization.INCONCLUSIVE: ("dim", "INCONCLUSIVE"),
}

_STATUS_STYLE = {
    DiagnosticStatus.HEALTHY: "green",
    DiagnosticStatus.DEGRADED: "yellow",
    DiagnosticStatus.FAILED: "red",
    DiagnosticStatus.UNKNOWN: "dim",
}


def render_multivantage_report(report: MultiVantageReport, console: Console | None = None) -> None:
    """Render a two-sided / third-vantage diagnosis with its evidence trail."""
    console = console or Console()
    color, label = _LOCALIZATION_STYLE.get(report.localization, ("white", report.localization.value.upper()))
    conf_pct = int(report.confidence * 100)

    console.print()
    console.print(
        Panel(
            f"[bold]Target:[/bold] {report.target}    "
            f"[bold]Service:[/bold] {report.service_id or '-'}    "
            f"[bold]Topology:[/bold] {report.topology or '-'}\n"
            f"[bold]Vantages:[/bold] {report.vantage_count}    "
            f"[green]Reachable:[/green] {report.reachable_count}    "
            f"[red]Failed:[/red] {report.failed_count}    "
            f"[yellow]Degraded:[/yellow] {report.degraded_count}\n\n"
            f"[bold]Localization:[/bold] [{color}]{label}[/{color}] ({conf_pct}% confidence)\n"
            f"[bold]Verdict:[/bold] [{color}]{report.verdict}[/{color}]",
            title="[bold cyan]NetForge Multi-Vantage Diagnosis[/bold cyan]",
            border_style=color,
        )
    )

    if report.agent_errors:
        console.print("\n[bold yellow]Vantage dispatch errors:[/bold yellow]")
        for agent_id, err in report.agent_errors.items():
            console.print(f"  [yellow]• {agent_id}: {err}[/yellow]")

    trail = Table(title="Evidence Trail")
    trail.add_column("#", justify="right")
    trail.add_column("Vantage")
    trail.add_column("Role")
    trail.add_column("Status")
    trail.add_column("Quality")
    trail.add_column("Conf")
    trail.add_column("Observation")
    for step in report.evidence_trail:
        status_color = _STATUS_STYLE.get(step.status, "white")
        trail.add_row(
            str(step.order),
            step.vantage,
            step.role,
            f"[{status_color}]{step.status.value}[/{status_color}]",
            step.evidence_quality.value,
            f"{int(step.confidence * 100)}%",
            step.observation,
        )
    console.print()
    console.print(trail)
    console.print()

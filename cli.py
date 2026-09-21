import typer
from rich.console import Console
from rich.panel import Panel

from core.engine import DiagnosticEngine
from core.result import DiagnosticStatus

from diagnostics.host.connectivity import run_connectivity_checks
from diagnostics.host.interface import run_interface_diagnostics
from diagnostics.host.routing import run_routing_diagnostics
from diagnostics.host.dns import run_dns_diagnostics
from diagnostics.host.tcp_udp import run_transport_diagnostics
from diagnostics.host.packet_loss import run_packet_loss_diagnostics
from diagnostics.host.latency import run_latency_diagnostics
from diagnostics.host.resource_network import run_resource_network_diagnostics
from diagnostics.host.gateway import run_gateway_diagnostics

app = typer.Typer(
    name="netforge",
    help="NetForge Network Diagnostics Framework",
    no_args_is_help=True,
)

host_app = typer.Typer(name="host", help="Host-level (node/endpoint) diagnostics")
link_app = typer.Typer(name="link", help="Link-level interface diagnostics")
path_app = typer.Typer(name="path", help="Path-level traceroute diagnostics")
traffic_app = typer.Typer(name="traffic", help="Traffic / bandwidth / jitter probes")
flow_app = typer.Typer(name="flow", help="Flow analysis over passive telemetry")
mesh_app = typer.Typer(name="mesh", help="Ping-mesh / distributed observations")
diagnose_app = typer.Typer(name="diagnose", help="Intelligent root-cause diagnosis")

controller_app = typer.Typer(
    name="controller",
    help="Distributed controller: diagnose, monitor, and correlate via a running controller (HTTP)",
)
ctrl_agent_app = typer.Typer(name="agent", help="Register and list remote agents")
ctrl_topology_app = typer.Typer(name="topology", help="Import and list topologies")
ctrl_service_app = typer.Typer(name="service", help="Register and list services")
ctrl_monitor_app = typer.Typer(name="monitor", help="Run the scheduled monitoring loop on demand")
ctrl_schedule_app = typer.Typer(name="schedule", help="Manage periodic diagnosis schedules")
ctrl_alert_app = typer.Typer(name="alert", help="Alert rules and firing alerts")
ctrl_incident_app = typer.Typer(name="incident", help="Incident lifecycle")
ctrl_binding_app = typer.Typer(name="binding", help="Host-to-switch-port bindings")

app.add_typer(host_app, name="host")
app.add_typer(link_app, name="link")
app.add_typer(path_app, name="path")
app.add_typer(traffic_app, name="traffic")
app.add_typer(flow_app, name="flow")
app.add_typer(mesh_app, name="mesh")
app.add_typer(diagnose_app, name="diagnose")
app.add_typer(controller_app, name="controller")

controller_app.add_typer(ctrl_agent_app, name="agent")
controller_app.add_typer(ctrl_topology_app, name="topology")
controller_app.add_typer(ctrl_service_app, name="service")
controller_app.add_typer(ctrl_monitor_app, name="monitor")
controller_app.add_typer(ctrl_schedule_app, name="schedule")
controller_app.add_typer(ctrl_alert_app, name="alert")
controller_app.add_typer(ctrl_incident_app, name="incident")
controller_app.add_typer(ctrl_binding_app, name="binding")


# ---------------------------------------------------------------------------
# Host commands
# ---------------------------------------------------------------------------


@host_app.command()
def connectivity():
    """Check basic network connectivity."""
    run_connectivity_checks()


@host_app.command()
def interface():
    """Inspect network interfaces."""
    run_interface_diagnostics()


@host_app.command()
def routing(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print raw routing table"),
):
    """Inspect the routing table and default gateway."""
    run_routing_diagnostics(verbose=verbose)


@host_app.command()
def gateway(
    count: int = typer.Option(4, "--count", "-c"),
):
    """Probe default gateway reachability."""
    run_gateway_diagnostics(count=count)


@host_app.command()
def dns(
    hostname: str = typer.Option("google.com", "--hostname", "-h"),
):
    """Resolve a hostname and list configured DNS servers."""
    run_dns_diagnostics(hostname=hostname)


@host_app.command()
def transport(
    host: str = typer.Option("1.1.1.1", "--host"),
):
    """Test TCP/UDP transport to common ports."""
    run_transport_diagnostics(host=host)


@host_app.command("packet-loss")
def packet_loss(
    count: int = typer.Option(5, "--count", "-c"),
):
    """Measure packet loss."""
    run_packet_loss_diagnostics(count=count)


@host_app.command()
def latency(
    count: int = typer.Option(5, "--count", "-c"),
):
    """Measure latency and jitter."""
    run_latency_diagnostics(count=count)


@host_app.command()
def resources():
    """Analyze host resources and network activity."""
    run_resource_network_diagnostics()


@host_app.command("all")
def host_all(
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Exit with code 1 if any diagnostics are degraded or failed",
    ),
    diagnose: bool = typer.Option(
        False,
        "--diagnose",
        help="Also run the RuleEngine for root-cause analysis",
    ),
    target: str = typer.Option("google.com", "--target", "-t"),
):
    """Run the full host diagnostic suite."""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]NetForge Host Diagnostics[/bold cyan]",
            subtitle="Comprehensive Host Analysis",
        )
    )

    results = []
    console.print("\n[bold]1. Connectivity[/bold]")
    results.extend(run_connectivity_checks())
    console.print("\n[bold]2. Interfaces[/bold]")
    results.extend(run_interface_diagnostics())
    console.print("\n[bold]3. Routing[/bold]")
    results.append(run_routing_diagnostics())
    console.print("\n[bold]4. Gateway[/bold]")
    results.append(run_gateway_diagnostics())
    console.print("\n[bold]5. DNS[/bold]")
    results.append(run_dns_diagnostics())
    console.print("\n[bold]6. Transport (TCP / UDP)[/bold]")
    results.extend(run_transport_diagnostics(host="1.1.1.1"))
    console.print("\n[bold]7. Packet Loss[/bold]")
    results.extend(run_packet_loss_diagnostics())
    console.print("\n[bold]8. Latency & Jitter[/bold]")
    results.extend(run_latency_diagnostics())
    console.print("\n[bold]9. Resource / Network Interaction[/bold]")
    results.extend(run_resource_network_diagnostics())

    engine = DiagnosticEngine(results)
    summary = engine.summarize()
    console.print("\n")
    console.print(
        Panel(
            f"""
[bold]Checks:[/bold] {summary["total_checks"]}

[green]Healthy:[/green] {summary["healthy"]}
[yellow]Degraded:[/yellow] {summary["degraded"]}
[red]Failed:[/red] {summary["failed"]}
[dim]Unknown:[/dim] {summary["unknown"]}
""",
            title="NetForge Diagnostic Summary",
        )
    )

    findings = engine.generate_findings()
    if findings:
        console.print("\n[bold yellow]Findings[/bold yellow]")
        for finding in findings:
            console.print(f"  • {finding}")
    else:
        console.print("\n[green]No degraded or failed diagnostics detected.[/green]")

    if diagnose:
        from analysis.engine import RuleEngine
        from analysis.formatter import render_diagnosis_report

        report = RuleEngine().analyze(results, target_host=target)
        render_diagnosis_report(report, console=console)
        if strict and report.status != DiagnosticStatus.HEALTHY:
            raise typer.Exit(code=1)
        if report.status == DiagnosticStatus.FAILED:
            raise typer.Exit(code=1)
        return

    if strict and (summary["failed"] > 0 or summary["degraded"] > 0):
        raise typer.Exit(code=1)
    if summary["failed"] > 0:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Link commands
# ---------------------------------------------------------------------------


@link_app.command("util")
def link_util(
    interval: float = typer.Option(1.0, "--interval", "-i"),
):
    """Measure per-interface utilization."""
    from diagnostics.link.collector import measure_link_utilization
    from rich.table import Table

    results = measure_link_utilization(interval=interval)
    table = Table(title="Link Utilization")
    table.add_column("Interface")
    table.add_column("Util %")
    table.add_column("RX Mbps")
    table.add_column("TX Mbps")
    table.add_column("Status")
    for r in results:
        util = r.metrics.get("util_percent")
        table.add_row(
            r.target or "-",
            f"{util:.1f}" if util is not None else "-",
            f'{r.metrics.get("rx_bps", 0)/1e6:.2f}',
            f'{r.metrics.get("tx_bps", 0)/1e6:.2f}',
            r.status.value,
        )
    Console().print(table)


@link_app.command("errors")
def link_errors(
    interval: float = typer.Option(1.0, "--interval", "-i"),
):
    """Measure per-interface drops and errors."""
    from diagnostics.link.collector import measure_link_errors
    from rich.table import Table

    results = measure_link_errors(interval=interval)
    table = Table(title="Link Errors")
    table.add_column("Interface")
    table.add_column("Drops/s")
    table.add_column("Errors/s")
    table.add_column("Status")
    for r in results:
        table.add_row(
            r.target or "-",
            f'{r.metrics.get("drops_per_sec", 0):.2f}',
            f'{r.metrics.get("errors_per_sec", 0):.2f}',
            r.status.value,
        )
    Console().print(table)


@link_app.command("all")
def link_all(
    interval: float = typer.Option(1.0, "--interval", "-i"),
):
    """Run full link diagnostics (util, errors, congestion)."""
    from diagnostics.link.collector import run_link_diagnostics

    run_link_diagnostics(interval=interval)


# ---------------------------------------------------------------------------
# Path commands
# ---------------------------------------------------------------------------


@path_app.command("trace")
def path_trace(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
    max_hops: int = typer.Option(30, "--max-hops", "-m"),
):
    """Run traceroute to a target."""
    from diagnostics.path.traceroute import run_traceroute_diagnostics

    run_traceroute_diagnostics(target=target, max_hops=max_hops)


@path_app.command("hops")
def path_hops(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
    probes: int = typer.Option(2, "--probes", "-p"),
):
    """Aggregate per-hop latency and loss."""
    from diagnostics.path.traceroute import measure_hop_metrics
    from rich.table import Table

    result = measure_hop_metrics(target, probes=probes)
    table = Table(title=f"Hop Metrics → {target}")
    table.add_column("Hop")
    table.add_column("Address")
    table.add_column("Avg RTT")
    table.add_column("Loss")
    for hop in result.metrics.get("hops", []):
        avg = hop.get("avg_rtt_ms")
        table.add_row(
            str(hop.get("hop")),
            hop.get("address") or "*",
            f"{avg} ms" if avg is not None else "*",
            f'{hop.get("loss_percent", 0)}%',
        )
    Console().print(table)


@path_app.command("diff")
def path_diff(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
):
    """Detect path change vs stored baseline."""
    from diagnostics.path.traceroute import detect_path_change, traceroute_to_result

    trace = traceroute_to_result(target)
    change = detect_path_change(target, trace)
    Console().print(f"[bold]{change.summary}[/bold] ({change.status.value})")
    for ev in change.evidence:
        Console().print(f"  • {ev}")


@path_app.command("all")
def path_all(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
):
    """Traceroute + path-change detection."""
    from diagnostics.path.traceroute import (
        detect_path_change,
        run_traceroute_diagnostics,
    )

    trace = run_traceroute_diagnostics(target=target)
    change = detect_path_change(target, trace)
    Console().print(f"\n[bold]Path change:[/bold] {change.summary}")


# ---------------------------------------------------------------------------
# Traffic commands
# ---------------------------------------------------------------------------


@traffic_app.command("speed")
def traffic_speed(
    url: str = typer.Option(
        "https://speed.cloudflare.com/__down?bytes=5000000",
        "--url",
        "-u",
    ),
):
    """Estimate download goodput."""
    from diagnostics.traffic.speed import run_speed_diagnostics

    run_speed_diagnostics(url=url)


@traffic_app.command("jitter")
def traffic_jitter(
    host: str = typer.Option("1.1.1.1", "--host", "-h"),
    count: int = typer.Option(10, "--count", "-c"),
):
    """Measure RFC 3550 jitter."""
    from diagnostics.traffic.speed import run_jitter_diagnostics

    run_jitter_diagnostics(host=host, count=count)


@traffic_app.command("bandwidth")
def traffic_bandwidth(
    interval: float = typer.Option(1.0, "--interval", "-i"),
):
    """Show current interface bandwidth (bytes/sec sample)."""
    from diagnostics.link.collector import measure_link_utilization
    from rich.table import Table

    results = measure_link_utilization(interval=interval)
    table = Table(title="Interface Bandwidth Sample")
    table.add_column("Interface")
    table.add_column("Total Mbps")
    table.add_column("Util %")
    for r in results:
        table.add_row(
            r.target or "-",
            f'{r.metrics.get("total_bps", 0)/1e6:.2f}',
            (
                f'{r.metrics.get("util_percent"):.1f}'
                if r.metrics.get("util_percent") is not None
                else "-"
            ),
        )
    Console().print(table)


# ---------------------------------------------------------------------------
# Flow commands
# ---------------------------------------------------------------------------


@flow_app.command("top")
def flow_top(
    path: str = typer.Argument(..., help="JSON or JSONL flow export path"),
):
    """Show top talkers from an offline flow export."""
    from diagnostics.flow.analysis import run_flow_top

    run_flow_top(path)


@flow_app.command("analyze")
def flow_analyze(
    path: str = typer.Argument(..., help="JSON or JSONL flow export path"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Analyze flow records (elephants, top talkers)."""
    from diagnostics.flow.analysis import collect_flow_diagnostics

    results = collect_flow_diagnostics(path)
    result = results[0]
    if json_output:
        print(result.model_dump_json(indent=2))
    else:
        Console().print(f"[bold]{result.summary}[/bold]")
        for w in result.warnings:
            Console().print(f"  [yellow]• {w}[/yellow]")


# ---------------------------------------------------------------------------
# Mesh commands
# ---------------------------------------------------------------------------


@mesh_app.command("run")
def mesh_run(
    targets: str = typer.Option(
        "1.1.1.1,8.8.8.8",
        "--targets",
        "-t",
        help="Comma-separated targets",
    ),
    topology: str = typer.Option(None, "--topology", help="JSON topology file"),
    count: int = typer.Option(3, "--count", "-c"),
):
    """Run a local ping mesh to many targets."""
    from diagnostics.mesh.runner import run_mesh_diagnostics

    target_list = [t.strip() for t in targets.split(",") if t.strip()] if not topology else None
    run_mesh_diagnostics(targets=target_list, topology_path=topology, count=count)


@mesh_app.command("status")
def mesh_status():
    """Show mesh/agent capability status."""
    from collectors.agent_api import AGENT_API_SCHEMA

    console = Console()
    console.print("[bold]Local mesh:[/bold] available via `netforge mesh run`")
    console.print("[bold]Remote agents:[/bold] stubbed (not implemented)")
    console.print(f"Agent API schema: {AGENT_API_SCHEMA}")


# ---------------------------------------------------------------------------
# Diagnose commands
# ---------------------------------------------------------------------------


def _exit_from_report(report, strict: bool) -> None:
    if strict and report.status != DiagnosticStatus.HEALTHY:
        raise typer.Exit(code=1)
    if report.status == DiagnosticStatus.FAILED:
        raise typer.Exit(code=1)


@diagnose_app.command("host")
def diagnose_host(
    target: str = typer.Option("google.com", "--target", "-t"),
    json_output: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
):
    """Diagnose host-layer issues via the Rule Engine."""
    from analysis.engine import RuleEngine
    from analysis.formatter import render_diagnosis_report
    from diagnostics.host.collector import collect_host_diagnostics

    console = Console()
    if not json_output:
        console.print(
            f"[bold cyan]NetForge[/bold cyan]: collecting host observations for [bold]{target}[/bold]..."
        )
    results = collect_host_diagnostics(target_host=target)
    report = RuleEngine().analyze(results, target_host=target)
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        render_diagnosis_report(report, console=console)
    _exit_from_report(report, strict)


@diagnose_app.command("path")
def diagnose_path(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
    json_output: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
):
    """Diagnose path-layer issues."""
    from analysis.engine import RuleEngine
    from analysis.formatter import render_diagnosis_report
    from diagnostics.path.collector import collect_path_diagnostics

    console = Console()
    if not json_output:
        console.print(
            f"[bold cyan]NetForge[/bold cyan]: collecting path observations for [bold]{target}[/bold]..."
        )
    results = collect_path_diagnostics(target=target)
    report = RuleEngine().analyze(results, target_host=target)
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        render_diagnosis_report(report, console=console)
    _exit_from_report(report, strict)


@diagnose_app.command("link")
def diagnose_link(
    json_output: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
    interval: float = typer.Option(1.0, "--interval", "-i"),
):
    """Diagnose link-layer issues."""
    from analysis.engine import RuleEngine
    from analysis.formatter import render_diagnosis_report
    from diagnostics.link.collector import (
        measure_link_congestion,
        measure_link_errors,
        measure_link_utilization,
    )
    from storage.baselines import compare_probe_metrics

    console = Console()
    if not json_output:
        console.print("[bold cyan]NetForge[/bold cyan]: collecting link observations...")
    util = measure_link_utilization(interval=interval)
    errors = measure_link_errors(interval=interval)
    results = util + errors + measure_link_congestion(util, errors)
    results.extend(compare_probe_metrics(util))
    report = RuleEngine().analyze(results, target_host="local-links")
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        render_diagnosis_report(report, console=console)
    _exit_from_report(report, strict)


@diagnose_app.command("all")
def diagnose_all(
    target: str = typer.Option("1.1.1.1", "--target", "-t"),
    json_output: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict"),
):
    """Merge host + link + path observations into one diagnosis."""
    from analysis.engine import RuleEngine
    from analysis.formatter import render_diagnosis_report
    from collectors.local import collect_local

    console = Console()
    if not json_output:
        console.print(
            f"[bold cyan]NetForge[/bold cyan]: collecting host+link+path for [bold]{target}[/bold]..."
        )
    results = collect_local(domains=["host", "link", "path"], target=target)
    report = RuleEngine().analyze(results, target_host=target)
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        render_diagnosis_report(report, console=console)
    _exit_from_report(report, strict)


# ---------------------------------------------------------------------------
# Controller commands (M4-M6: diagnose / monitor / correlate over HTTP)
# ---------------------------------------------------------------------------


def _controller_client(ctx: typer.Context):
    from controller.client import ControllerClient

    return ControllerClient(ctx.obj["url"], ctx.obj["token"])


def _run(call, *args):
    """Invoke a client call, converting API/transport errors into a CLI exit."""
    from controller.client import ControllerAPIError

    try:
        return call(*args)
    except ControllerAPIError as exc:
        Console().print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


def _load_json(path: str):
    import json
    from pathlib import Path

    return json.loads(Path(path).read_text(encoding="utf-8"))


@controller_app.callback()
def _controller_group(
    ctx: typer.Context,
    url: str = typer.Option(
        "http://127.0.0.1:8080",
        "--url",
        "-u",
        envvar="NETFORGE_CONTROLLER_URL",
        help="Controller base URL",
    ),
    token: str = typer.Option(
        "",
        "--token",
        "-t",
        envvar="NETFORGE_CONTROLLER_TOKEN",
        help="Controller bearer token",
    ),
):
    """Talk to a running NetForge controller over HTTP."""
    ctx.ensure_object(dict)
    ctx.obj["url"] = url
    ctx.obj["token"] = token


@controller_app.command("health")
def controller_health(ctx: typer.Context):
    """Check controller reachability."""
    data = _run(_controller_client(ctx).health)
    Console().print(f"[green]{data.get('status')}[/green] — {data.get('service')}")


# --- agents ---


@ctrl_agent_app.command("register")
def controller_agent_register(
    ctx: typer.Context,
    agent_id: str = typer.Argument(..., help="Unique agent id"),
    url: str = typer.Argument(..., help="Agent base URL, e.g. http://10.0.0.5:8081"),
    tag: list[str] = typer.Option(None, "--tag", help="topology tag k=v (repeatable)"),
):
    """Register a remote agent with the controller."""
    tags = dict(t.split("=", 1) for t in tag) if tag else {}
    agent = _run(_controller_client(ctx).register_agent, agent_id, url, tags)
    Console().print(f"[green]Registered agent[/green] {agent.get('agent_id')} -> {agent.get('url')}")


@ctrl_agent_app.command("list")
def controller_agent_list(ctx: typer.Context):
    """List registered agents."""
    from rich.table import Table

    agents = _run(_controller_client(ctx).list_agents)
    table = Table(title=f"Agents ({len(agents)})")
    table.add_column("Agent")
    table.add_column("URL")
    table.add_column("Enabled")
    table.add_column("Tags")
    for a in agents:
        tags = ", ".join(f"{k}={v}" for k, v in (a.get("topology_tags") or {}).items())
        enabled = "[green]yes[/green]" if a.get("enabled") else "[dim]no[/dim]"
        table.add_row(a.get("agent_id", "-"), str(a.get("url", "-")), enabled, tags or "-")
    Console().print(table)


# --- topology ---


@ctrl_topology_app.command("import")
def controller_topology_import(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="JSON topology file"),
):
    """Import (and validate) a topology document."""
    from controller.render import render_topology

    topology = _run(_controller_client(ctx).import_topology, _load_json(path))
    render_topology(topology)


@ctrl_topology_app.command("list")
def controller_topology_list(ctx: typer.Context):
    """List known topology names."""
    names = _run(_controller_client(ctx).list_topology_names)
    console = Console()
    if not names:
        console.print("[yellow]No topologies imported.[/yellow]")
        return
    for name in names:
        console.print(f"  • [bold]{name}[/bold]")


# --- services ---


@ctrl_service_app.command("register")
def controller_service_register(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="JSON service inventory file"),
    topology: str = typer.Option(None, "--topology", help="Attach services to a topology"),
):
    """Register a service inventory."""
    services = _run(_controller_client(ctx).register_services, _load_json(path), topology)
    Console().print(f"[green]Registered[/green] {len(services)} service(s)")


@ctrl_service_app.command("list")
def controller_service_list(ctx: typer.Context):
    """List registered services."""
    from rich.table import Table

    services = _run(_controller_client(ctx).list_services)
    table = Table(title=f"Services ({len(services)})")
    table.add_column("Service")
    table.add_column("Name")
    table.add_column("Host")
    table.add_column("Port")
    table.add_column("Proto")
    table.add_column("Node")
    for svc in services:
        table.add_row(
            svc.service_id,
            svc.name,
            svc.host,
            str(svc.port),
            svc.protocol.value,
            svc.node_id or "-",
        )
    Console().print(table)


# --- diagnose (M4) ---


@controller_app.command("diagnose")
def controller_diagnose(
    ctx: typer.Context,
    service_id: str = typer.Argument(..., help="Registered service id"),
    probe_type: str = typer.Option(None, "--probe-type", "-p", help="icmp|tcp|udp|dns|http"),
    count: int = typer.Option(3, "--count", "-c", help="Probes per vantage"),
    source_node: str = typer.Option(None, "--source-node", help="Pin the source vantage node id"),
    topology: str = typer.Option(None, "--topology", help="Topology name"),
    json_output: bool = typer.Option(False, "--json"),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 unless localization is healthy"),
):
    """Run a multi-vantage diagnosis for a service."""
    from analysis.multivantage import FaultLocalization

    report = _run(
        _controller_client(ctx).diagnose,
        service_id,
        probe_type,
        count,
        source_node,
        topology,
    )
    if json_output:
        print(report.model_dump_json(indent=2))
    else:
        from analysis.formatter import render_multivantage_report

        render_multivantage_report(report)
    if strict and report.localization != FaultLocalization.HEALTHY:
        raise typer.Exit(code=1)


@controller_app.command("diagnoses")
def controller_diagnoses(ctx: typer.Context):
    """List stored multi-vantage diagnoses."""
    from rich.table import Table

    reports = _run(_controller_client(ctx).list_diagnoses)
    table = Table(title=f"Diagnoses ({len(reports)})")
    table.add_column("Service")
    table.add_column("Target")
    table.add_column("Localization")
    table.add_column("Conf")
    table.add_column("Vantages")
    for r in reports:
        table.add_row(
            r.service_id or "-",
            r.target,
            r.localization.value,
            f"{int(r.confidence * 100)}%",
            str(r.vantage_count),
        )
    Console().print(table)


# --- monitor (M5) ---


@ctrl_monitor_app.command("run")
def controller_monitor_run(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json"),
):
    """Run all due schedules now and render the resulting reports."""
    reports = _run(_controller_client(ctx).run_monitor)
    if json_output:
        import json

        print(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
    else:
        from controller.render import render_reports

        Console().print(f"[bold cyan]NetForge[/bold cyan]: ran [bold]{len(reports)}[/bold] due schedule(s)")
        render_reports(reports)


@ctrl_schedule_app.command("list")
def controller_schedule_list(
    ctx: typer.Context,
    service_id: str = typer.Option(None, "--service", "-s"),
):
    """List diagnosis schedules."""
    from controller.render import render_schedules

    render_schedules(_run(_controller_client(ctx).list_schedules, service_id))


@ctrl_schedule_app.command("add")
def controller_schedule_add(
    ctx: typer.Context,
    service_id: str = typer.Argument(..., help="Registered service id"),
    interval: float = typer.Option(..., "--interval", "-i", help="Seconds between runs"),
    count: int = typer.Option(3, "--count", "-c"),
    probe_type: str = typer.Option(None, "--probe-type", "-p"),
    source_node: str = typer.Option(None, "--source-node"),
    topology: str = typer.Option(None, "--topology"),
):
    """Create a periodic diagnosis schedule."""
    sched = _run(
        _controller_client(ctx).create_schedule,
        service_id,
        interval,
        count,
        probe_type,
        source_node,
        topology,
    )
    Console().print(
        f"[green]Scheduled[/green] {sched.schedule_id} every {sched.interval_seconds:g}s for {sched.service_id}"
    )


@ctrl_schedule_app.command("remove")
def controller_schedule_remove(
    ctx: typer.Context,
    schedule_id: str = typer.Argument(...),
):
    """Delete a schedule."""
    ok = _run(_controller_client(ctx).delete_schedule, schedule_id)
    Console().print(f"[green]Deleted[/green] {schedule_id}" if ok else f"[red]Not deleted[/red] {schedule_id}")


@ctrl_alert_app.command("list")
def controller_alert_list(
    ctx: typer.Context,
    service_id: str = typer.Option(None, "--service", "-s"),
    state: str = typer.Option(None, "--state", help="firing|resolved"),
):
    """List alerts."""
    from controller.render import render_alerts

    render_alerts(_run(_controller_client(ctx).list_alerts, service_id, state))


@ctrl_alert_app.command("rule-list")
def controller_alert_rule_list(ctx: typer.Context):
    """List alert rules."""
    from rich.table import Table

    rules = _run(_controller_client(ctx).list_alert_rules)
    table = Table(title=f"Alert Rules ({len(rules)})")
    table.add_column("Rule")
    table.add_column("Name")
    table.add_column("Service")
    table.add_column("Severity")
    table.add_column("Min conf")
    table.add_column("Enabled")
    for r in rules:
        table.add_row(
            r.rule_id[:8],
            r.name,
            r.service_id or "*",
            r.severity.value,
            f"{r.min_confidence:.2f}",
            "[green]yes[/green]" if r.enabled else "[dim]no[/dim]",
        )
    Console().print(table)


@ctrl_alert_app.command("rule-add")
def controller_alert_rule_add(
    ctx: typer.Context,
    name: str = typer.Option("any-unhealthy", "--name", "-n"),
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
    severity: str = typer.Option("high", "--severity"),
    service_id: str = typer.Option(None, "--service", "-s", help="Scope to one service"),
    fire_on: str = typer.Option(None, "--fire-on", help="Comma-separated localizations"),
):
    """Register an alert rule."""
    localizations = [x.strip() for x in fire_on.split(",") if x.strip()] if fire_on else None
    rule = _run(
        _controller_client(ctx).create_alert_rule,
        name,
        min_confidence,
        severity,
        service_id,
        localizations,
    )
    Console().print(f"[green]Rule registered[/green] {rule.rule_id} ({rule.name})")


@ctrl_incident_app.command("list")
def controller_incident_list(
    ctx: typer.Context,
    service_id: str = typer.Option(None, "--service", "-s"),
    state: str = typer.Option(None, "--state", help="open|acknowledged|resolved"),
):
    """List incidents."""
    from controller.render import render_incidents

    render_incidents(_run(_controller_client(ctx).list_incidents, service_id, state))


@ctrl_incident_app.command("show")
def controller_incident_show(
    ctx: typer.Context,
    incident_id: str = typer.Argument(...),
):
    """Show a single incident."""
    from controller.render import render_incidents

    incident = _run(_controller_client(ctx).get_incident, incident_id)
    render_incidents([incident])
    Console().print(f"[bold]Summary:[/bold] {incident.summary}")


@ctrl_incident_app.command("ack")
def controller_incident_ack(
    ctx: typer.Context,
    incident_id: str = typer.Argument(...),
):
    """Acknowledge an incident."""
    incident = _run(_controller_client(ctx).ack_incident, incident_id)
    Console().print(f"[yellow]Acknowledged[/yellow] {incident.incident_id} -> {incident.state.value}")


@ctrl_incident_app.command("resolve")
def controller_incident_resolve(
    ctx: typer.Context,
    incident_id: str = typer.Argument(...),
):
    """Resolve an incident."""
    incident = _run(_controller_client(ctx).resolve_incident, incident_id)
    Console().print(f"[green]Resolved[/green] {incident.incident_id} -> {incident.state.value}")


# --- correlate (M6) ---


@controller_app.command("correlate")
def controller_correlate(
    ctx: typer.Context,
    topology: str = typer.Argument(..., help="Topology name to correlate against"),
    telemetry: str = typer.Argument(..., help="JSON file with switch telemetry (LLDP + MAC table)"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Correlate hosts to switch ports from device telemetry."""
    data = _load_json(telemetry)
    switches = data.get("switches", []) if isinstance(data, dict) else data
    bindings = _run(_controller_client(ctx).correlate, topology, switches)
    if json_output:
        import json

        print(json.dumps([b.model_dump(mode="json") for b in bindings], indent=2))
    else:
        from controller.render import render_bindings

        render_bindings(bindings)


@ctrl_binding_app.command("list")
def controller_binding_list(
    ctx: typer.Context,
    topology: str = typer.Argument(None, help="Optional topology name"),
):
    """List stored host-to-switch-port bindings."""
    from controller.render import render_bindings

    render_bindings(_run(_controller_client(ctx).list_bindings, topology))


@controller_app.command("augment")
def controller_augment(
    ctx: typer.Context,
    topology: str = typer.Argument(..., help="Topology name to augment with bindings"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Add host->switch LAN edges into a topology from stored bindings."""
    augmented = _run(_controller_client(ctx).augment_topology, topology)
    if json_output:
        print(augmented.model_dump_json(indent=2))
    else:
        from controller.render import render_topology

        render_topology(augmented)


if __name__ == "__main__":
    app()

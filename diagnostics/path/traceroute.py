"""Traceroute / path discovery probes."""

from __future__ import annotations

import hashlib
import platform
import re
import subprocess
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from core.result import DiagnosticResult, DiagnosticStatus, Severity

console = Console()


@dataclass
class Hop:
    hop: int
    address: str | None = None
    hostname: str | None = None
    rtts_ms: list[float] = field(default_factory=list)
    loss_percent: float | None = None
    timed_out: bool = False

    @property
    def avg_rtt(self) -> float | None:
        return round(sum(self.rtts_ms) / len(self.rtts_ms), 2) if self.rtts_ms else None


def _path_fingerprint(hops: list[Hop]) -> str:
    parts = []
    for h in hops:
        addr = h.address or "*"
        parts.append(f"{h.hop}:{addr}")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return digest[:16]


def parse_traceroute_output(output: str, system: str) -> list[Hop]:
    hops: dict[int, Hop] = {}
    lines = output.splitlines()

    if system == "windows":
        #  1    <1 ms    <1 ms    <1 ms  192.168.1.1
        #  2     *        *        *     Request timed out.
        hop_re = re.compile(r"^\s*(\d+)\s+(.*)$")
        for line in lines:
            m = hop_re.match(line)
            if not m:
                continue
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            hop = hops.setdefault(hop_num, Hop(hop=hop_num))
            if "Request timed out" in rest or rest.replace("*", "").strip() == "":
                # count stars as timeouts
                stars = rest.count("*")
                if stars or "timed out" in rest.lower():
                    hop.timed_out = True
                continue
            rtts = re.findall(r"(?:<)?(\d+(?:\.\d+)?)\s*ms", rest, re.IGNORECASE)
            hop.rtts_ms.extend(float(r) for r in rtts)
            # trailing address / hostname
            addr_match = re.search(
                r"((?:\d{1,3}\.){3}\d{1,3}|[0-9a-fA-F:]+)\s*$",
                rest,
            )
            name_match = re.search(r"([A-Za-z0-9._-]+)\s+\[([^\]]+)\]", rest)
            if name_match:
                hop.hostname = name_match.group(1)
                hop.address = name_match.group(2)
            elif addr_match:
                hop.address = addr_match.group(1)
    else:
        # 1  gateway (192.168.1.1)  1.234 ms  1.111 ms  1.000 ms
        # 2  * * *
        hop_re = re.compile(r"^\s*(\d+)\s+(.*)$")
        for line in lines:
            m = hop_re.match(line)
            if not m:
                continue
            hop_num = int(m.group(1))
            rest = m.group(2).strip()
            hop = hops.setdefault(hop_num, Hop(hop=hop_num))
            if rest.replace("*", "").strip() == "" or rest.startswith("*"):
                hop.timed_out = True
                continue
            rtts = re.findall(r"(\d+(?:\.\d+)?)\s*ms", rest, re.IGNORECASE)
            hop.rtts_ms.extend(float(r) for r in rtts)
            name_ip = re.search(r"([^\s]+)\s+\(([^)]+)\)", rest)
            if name_ip:
                hop.hostname = name_ip.group(1)
                hop.address = name_ip.group(2)
            else:
                ip_only = re.search(r"((?:\d{1,3}\.){3}\d{1,3})", rest)
                if ip_only:
                    hop.address = ip_only.group(1)

    # derive per-hop loss from probe count expectation (3 probes typical)
    result = []
    for num in sorted(hops):
        hop = hops[num]
        expected = 3
        received = len(hop.rtts_ms)
        if hop.timed_out and received == 0:
            hop.loss_percent = 100.0
        else:
            hop.loss_percent = round(max(0.0, (expected - received) / expected * 100.0), 1)
        result.append(hop)
    return result


def run_traceroute(target: str, max_hops: int = 30, timeout_sec: int = 60) -> tuple[list[Hop], str, str | None]:
    system = platform.system().lower()
    if system == "windows":
        cmd = ["tracert", "-d", "-h", str(max_hops), target]
    elif system == "darwin":
        cmd = ["traceroute", "-n", "-m", str(max_hops), target]
    else:
        cmd = ["traceroute", "-n", "-m", str(max_hops), target]

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        output = res.stdout + res.stderr
        hops = parse_traceroute_output(output, "windows" if system == "windows" else system)
        return hops, output, None
    except Exception as exc:
        return [], "", str(exc)


def traceroute_to_result(target: str, max_hops: int = 30) -> DiagnosticResult:
    hops, raw, error = run_traceroute(target, max_hops=max_hops)
    if error:
        return DiagnosticResult(
            module="traceroute",
            category="path",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Traceroute to {target} failed",
            target=target,
            errors=[error],
        )

    if not hops:
        return DiagnosticResult(
            module="traceroute",
            category="path",
            status=DiagnosticStatus.FAILED,
            severity=Severity.HIGH,
            summary=f"No hops discovered toward {target}",
            target=target,
            metadata={"raw_output": raw},
        )

    hop_dicts = []
    high_loss_hops = 0
    for h in hops:
        hop_dicts.append(
            {
                "hop": h.hop,
                "address": h.address,
                "hostname": h.hostname,
                "avg_rtt_ms": h.avg_rtt,
                "loss_percent": h.loss_percent,
                "timed_out": h.timed_out,
                "rtts_ms": h.rtts_ms,
            }
        )
        if (h.loss_percent or 0) >= 50:
            high_loss_hops += 1

    reached = any(h.address and target in (h.address or "") for h in hops) or (
        hops and hops[-1].address and not hops[-1].timed_out
    )
    fingerprint = _path_fingerprint(hops)

    if high_loss_hops >= 3 or (hops and hops[-1].timed_out and not reached):
        status = DiagnosticStatus.DEGRADED
        severity = Severity.MEDIUM
    elif high_loss_hops > 0:
        status = DiagnosticStatus.DEGRADED
        severity = Severity.LOW
    else:
        status = DiagnosticStatus.HEALTHY
        severity = Severity.INFO

    return DiagnosticResult(
        module="traceroute",
        category="path",
        status=status,
        severity=severity,
        summary=f"Path to {target}: {len(hops)} hops (fingerprint {fingerprint})",
        target=target,
        metrics={
            "hop_count": len(hops),
            "hops": hop_dicts,
            "path_fingerprint": fingerprint,
            "high_loss_hops": high_loss_hops,
            "max_hop_loss_percent": max((h.loss_percent or 0) for h in hops),
        },
        evidence=[
            f"Discovered {len(hops)} hops toward {target}",
            f"Path fingerprint: {fingerprint}",
        ],
        metadata={"raw_output": raw},
    )


def measure_hop_metrics(target: str, probes: int = 2, max_hops: int = 20) -> DiagnosticResult:
    """Run multiple traceroutes and aggregate per-hop loss/latency."""
    aggregates: dict[int, Hop] = {}
    errors: list[str] = []

    for _ in range(probes):
        hops, _, err = run_traceroute(target, max_hops=max_hops)
        if err:
            errors.append(err)
            continue
        for h in hops:
            agg = aggregates.setdefault(h.hop, Hop(hop=h.hop, address=h.address, hostname=h.hostname))
            if h.address:
                agg.address = h.address
            agg.rtts_ms.extend(h.rtts_ms)
            if h.timed_out and not h.rtts_ms:
                # mark a missed probe batch
                pass

    if not aggregates and errors:
        return DiagnosticResult(
            module="hop_metrics",
            category="path",
            status=DiagnosticStatus.UNKNOWN,
            severity=Severity.MEDIUM,
            summary=f"Hop metrics failed for {target}",
            target=target,
            errors=errors,
        )

    hop_list = []
    for num in sorted(aggregates):
        h = aggregates[num]
        expected = probes * 3
        received = len(h.rtts_ms)
        loss = round(max(0.0, (expected - received) / expected * 100.0), 1) if expected else 0.0
        hop_list.append(
            {
                "hop": h.hop,
                "address": h.address,
                "avg_rtt_ms": h.avg_rtt,
                "loss_percent": loss,
                "samples": received,
            }
        )

    max_loss = max((h["loss_percent"] for h in hop_list), default=0.0)
    status = DiagnosticStatus.HEALTHY if max_loss < 30 else DiagnosticStatus.DEGRADED
    severity = Severity.INFO if max_loss < 30 else Severity.MEDIUM

    return DiagnosticResult(
        module="hop_metrics",
        category="path",
        status=status,
        severity=severity,
        summary=f"Aggregated hop metrics to {target} over {probes} traces",
        target=target,
        metrics={"hops": hop_list, "probes": probes, "max_hop_loss_percent": max_loss},
        evidence=[f"Max per-hop loss: {max_loss}%"],
        errors=errors,
    )


def detect_path_change(target: str, current: DiagnosticResult) -> DiagnosticResult:
    from storage.history import HistoryStore

    store = HistoryStore()
    fingerprint = current.metrics.get("path_fingerprint")
    hops = current.metrics.get("hops", [])
    store.save_snapshot(
        domain="path",
        key=target,
        payload={"hops": hops, "fingerprint": fingerprint},
        fingerprint=fingerprint,
    )
    previous = store.previous_snapshot("path", target)

    if previous is None:
        return DiagnosticResult(
            module="path_change",
            category="path",
            status=DiagnosticStatus.HEALTHY,
            severity=Severity.INFO,
            summary=f"No prior path baseline for {target}; baseline stored",
            target=target,
            metrics={"changed": False, "baseline_created": True, "path_fingerprint": fingerprint},
            evidence=["First path observation stored as baseline"],
        )

    changed = previous.get("fingerprint") != fingerprint
    if changed:
        return DiagnosticResult(
            module="path_change",
            category="path",
            status=DiagnosticStatus.DEGRADED,
            severity=Severity.MEDIUM,
            summary=f"Forwarding path to {target} changed",
            target=target,
            metrics={
                "changed": True,
                "previous_fingerprint": previous.get("fingerprint"),
                "path_fingerprint": fingerprint,
            },
            evidence=[
                f"Previous fingerprint: {previous.get('fingerprint')}",
                f"Current fingerprint: {fingerprint}",
            ],
            warnings=["Routing path differs from the last observed baseline"],
        )

    return DiagnosticResult(
        module="path_change",
        category="path",
        status=DiagnosticStatus.HEALTHY,
        severity=Severity.INFO,
        summary=f"Path to {target} unchanged",
        target=target,
        metrics={
            "changed": False,
            "path_fingerprint": fingerprint,
            "previous_fingerprint": previous.get("fingerprint"),
        },
        evidence=["Path fingerprint matches previous baseline"],
    )


def run_traceroute_diagnostics(target: str = "1.1.1.1", max_hops: int = 30) -> DiagnosticResult:
    result = traceroute_to_result(target, max_hops=max_hops)
    table = Table(title=f"Traceroute to {target}")
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
    console.print(table)
    return result

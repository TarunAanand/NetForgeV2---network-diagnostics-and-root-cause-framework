import platform
import re
import subprocess
import time
from dataclasses import dataclass, field

from core.metrics.latency_jitter import calculate_rfc_jitter, summarize_latencies


@dataclass
class PingData:
    host: str
    count: int
    packet_loss_percent: float | None = None
    latencies: list[float] = field(default_factory=list)
    min_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    jitter_ms: float = 0.0  # RFC 3550 Packet Delay Variation
    std_dev_ms: float = 0.0
    raw_output: str = ""
    error: str | None = None


_PING_CACHE: dict[tuple[str, int], tuple[float, PingData]] = {}
CACHE_TTL = 15.0  # seconds


def parse_ping_output(output: str, count: int) -> tuple[float | None, list[float]]:
    # 1. Parse packet loss percentage
    loss = None
    match_loss = re.search(r"(\d+(?:\.\d+)?)%\s*(?:packet\s+)?loss", output, re.IGNORECASE)
    if match_loss:
        loss = float(match_loss.group(1))
    else:
        pct_matches = re.findall(r"(\d+(?:\.\d+)?)%", output)
        if pct_matches:
            loss = float(pct_matches[-1])

    # 2. Parse individual latency samples
    time_matches = re.findall(
        r"(?:time|zeit|temps|tempo|[=<])\s*(\d+(?:\.\d+)?)\s*ms",
        output,
        re.IGNORECASE,
    )
    latencies = [float(v) for v in time_matches]

    if not latencies:
        summary_match = re.search(
            r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
            r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)",
            output,
            re.IGNORECASE,
        )
        if summary_match:
            latencies = [
                float(summary_match.group(1)),
                float(summary_match.group(2)),
                float(summary_match.group(3)),
            ]

    if loss == 100.0:
        latencies = []

    return loss, latencies


def run_ping(host: str, count: int = 5, use_cache: bool = True) -> PingData:
    now = time.time()
    cache_key = (host, count)

    if use_cache and cache_key in _PING_CACHE:
        cached_time, cached_data = _PING_CACHE[cache_key]
        if now - cached_time < CACHE_TTL:
            return cached_data

    system = platform.system().lower()
    command = (
        ["ping", "-n", str(count), host]
        if system == "windows"
        else ["ping", "-c", str(count), host]
    )

    try:
        res = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=count * 3 + 5,
        )
        output = res.stdout + res.stderr
        loss, latencies = parse_ping_output(output, count)
        summary = summarize_latencies(latencies)

        data = PingData(
            host=host,
            count=count,
            packet_loss_percent=loss,
            latencies=latencies,
            min_ms=summary.min_ms,
            avg_ms=summary.avg_ms,
            max_ms=summary.max_ms,
            jitter_ms=summary.jitter_ms,
            std_dev_ms=summary.std_dev_ms,
            raw_output=output,
        )
        _PING_CACHE[cache_key] = (now, data)
        return data

    except Exception as exc:
        return PingData(
            host=host,
            count=count,
            error=str(exc),
        )


# Re-export for callers that imported calculate_rfc_jitter from this module
__all__ = [
    "CACHE_TTL",
    "PingData",
    "calculate_rfc_jitter",
    "parse_ping_output",
    "run_ping",
]

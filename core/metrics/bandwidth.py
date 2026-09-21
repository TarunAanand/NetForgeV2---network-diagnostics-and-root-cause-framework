from __future__ import annotations


def link_capacity_bps(speed_mbps: float | int | None) -> float | None:
    """Convert an interface speed in Mbps to bits/sec."""
    if speed_mbps is None or speed_mbps <= 0:
        return None
    return float(speed_mbps) * 1_000_000.0


def estimate_goodput_mbps(bytes_transferred: int | float, elapsed_seconds: float) -> float:
    """Estimate application goodput in Mbps from transferred bytes and elapsed time."""
    if elapsed_seconds <= 0:
        return 0.0
    bits = float(bytes_transferred) * 8.0
    return round(bits / elapsed_seconds / 1_000_000.0, 3)

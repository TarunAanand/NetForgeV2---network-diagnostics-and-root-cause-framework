from __future__ import annotations


def bytes_per_sec_from_delta(byte_delta: int | float, seconds: float) -> float:
    """Convert a byte counter delta over an interval into bytes/sec."""
    if seconds <= 0:
        return 0.0
    return float(byte_delta) / seconds


def bps_from_byte_delta(byte_delta: int | float, seconds: float) -> float:
    """Convert a byte counter delta over an interval into bits/sec."""
    return bytes_per_sec_from_delta(byte_delta, seconds) * 8.0


def utilization_percent(bps: float, link_speed_bps: float | None) -> float | None:
    """
    Link utilization as a percentage of negotiated/capacity speed.
    Returns None when link speed is unknown or zero.
    """
    if not link_speed_bps or link_speed_bps <= 0:
        return None
    return round(min(100.0, (bps / link_speed_bps) * 100.0), 2)

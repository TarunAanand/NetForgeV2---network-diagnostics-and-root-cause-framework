from __future__ import annotations


def congestion_score(
    util_percent: float | None = None,
    drop_rate: float = 0.0,
    error_rate: float = 0.0,
    latency_delta_ms: float = 0.0,
) -> float:
    """
    Heuristic congestion score in [0, 100].
    Combines link utilization, drop/error rates, and latency rise.
    """
    score = 0.0

    if util_percent is not None:
        if util_percent >= 90:
            score += 45
        elif util_percent >= 75:
            score += 30
        elif util_percent >= 60:
            score += 15

    if drop_rate >= 5.0:
        score += 30
    elif drop_rate >= 0.5:
        score += 15

    if error_rate >= 5.0:
        score += 15
    elif error_rate >= 0.5:
        score += 8

    if latency_delta_ms >= 100:
        score += 20
    elif latency_delta_ms >= 40:
        score += 10

    return round(min(100.0, score), 1)


def congestion_state(score: float) -> str:
    """Map congestion score to a coarse state label."""
    if score < 20:
        return "clear"
    if score < 45:
        return "mild"
    if score < 70:
        return "moderate"
    return "severe"

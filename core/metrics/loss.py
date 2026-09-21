from __future__ import annotations


def loss_percent(sent: int, received: int) -> float:
    """Return packet loss percentage for a probe batch."""
    if sent <= 0:
        return 0.0
    lost = max(0, sent - received)
    return round((lost / sent) * 100.0, 2)


def classify_loss_severity(loss: float) -> str:
    """
    Map loss % to a coarse severity label used by probes and rules.
    Returns one of: healthy, low, medium, high.
    """
    if loss <= 0:
        return "healthy"
    if loss < 5:
        return "low"
    if loss < 20:
        return "medium"
    return "high"

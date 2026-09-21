from core.metrics.bandwidth import estimate_goodput_mbps, link_capacity_bps
from core.metrics.congestion import congestion_score, congestion_state
from core.metrics.latency_jitter import calculate_rfc_jitter, summarize_latencies
from core.metrics.loss import classify_loss_severity, loss_percent
from core.metrics.throughput import bps_from_byte_delta, utilization_percent


def test_loss_percent():
    assert loss_percent(10, 10) == 0.0
    assert loss_percent(10, 8) == 20.0
    assert loss_percent(0, 0) == 0.0


def test_classify_loss_severity():
    assert classify_loss_severity(0) == "healthy"
    assert classify_loss_severity(3) == "low"
    assert classify_loss_severity(10) == "medium"
    assert classify_loss_severity(50) == "high"


def test_rfc_jitter():
    assert calculate_rfc_jitter([]) == 0.0
    assert calculate_rfc_jitter([10.0]) == 0.0
    assert calculate_rfc_jitter([10.0, 20.0, 10.0]) == 10.0


def test_summarize_latencies():
    s = summarize_latencies([10.0, 20.0, 30.0])
    assert s.min_ms == 10.0
    assert s.max_ms == 30.0
    assert s.avg_ms == 20.0
    assert s.samples == 3


def test_throughput_and_util():
    assert bps_from_byte_delta(125_000_000, 1.0) == 1_000_000_000.0
    assert utilization_percent(500_000_000, 1_000_000_000) == 50.0
    assert utilization_percent(100, None) is None


def test_bandwidth_helpers():
    assert link_capacity_bps(1000) == 1_000_000_000.0
    assert estimate_goodput_mbps(1_000_000, 1.0) == 8.0


def test_congestion_score():
    score = congestion_score(util_percent=95, drop_rate=6, latency_delta_ms=120)
    assert score >= 70
    assert congestion_state(score) == "severe"
    assert congestion_state(10) == "clear"

from diagnostics.flow.analysis import analyze_flows
from diagnostics.path.traceroute import parse_traceroute_output
from storage.history import HistoryStore


def test_parse_windows_tracert():
    sample = """
Tracing route to 1.1.1.1 over a maximum of 30 hops

  1    <1 ms    <1 ms    <1 ms  192.168.1.1
  2    10 ms    11 ms    12 ms  10.0.0.1
  3     *        *        *     Request timed out.
"""
    hops = parse_traceroute_output(sample, "windows")
    assert hops[0].address == "192.168.1.1"
    assert hops[1].avg_rtt is not None
    assert hops[2].timed_out


def test_flow_elephant_detection():
    records = [
        {"src": "10.0.0.1", "dst": "1.1.1.1", "bytes": 9000, "packets": 10},
        {"src": "10.0.0.2", "dst": "8.8.8.8", "bytes": 1000, "packets": 5},
    ]
    result = analyze_flows(records)
    assert result.metrics["elephant_count"] >= 1
    assert result.status.value in {"degraded", "healthy"}


def test_history_store(tmp_path):
    store = HistoryStore(tmp_path / "hist.db")
    store.save_snapshot("path", "1.1.1.1", {"hops": []}, fingerprint="aaa")
    store.save_snapshot("path", "1.1.1.1", {"hops": []}, fingerprint="bbb")
    prev = store.previous_snapshot("path", "1.1.1.1")
    assert prev is not None
    assert prev["fingerprint"] == "aaa"
    latest = store.latest_snapshot("path", "1.1.1.1")
    assert latest["fingerprint"] == "bbb"

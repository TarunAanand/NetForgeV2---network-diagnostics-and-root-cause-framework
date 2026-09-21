"""Tests for SNMPv3 counter / MAC-table ingest (M6)."""

from pathlib import Path

import pytest

from core.result import DiagnosticStatus
from ingest.snmp_counters import (
    InterfaceCounters,
    LiveSnmpV3Transport,
    ReplaySnmpTransport,
    SecurityLevel,
    SnmpV3Credentials,
    evaluate_interface_delta,
    load_snmp_replay,
    mac_from_oid_suffix,
    poll_interface_counters,
    poll_mac_table,
    replay_transport,
)

EXAMPLE_WALK = Path(__file__).resolve().parent.parent / "examples" / "snmp_walk.json"


# --- Credentials ---


def test_credentials_auth_no_priv_requires_auth_key():
    creds = SnmpV3Credentials(host="10.0.0.1", username="netforge", security_level=SecurityLevel.AUTH_NO_PRIV, auth_key="secret")
    assert creds.username == "netforge"
    with pytest.raises(ValueError):
        SnmpV3Credentials(host="10.0.0.1", username="x", security_level=SecurityLevel.AUTH_NO_PRIV)


def test_credentials_auth_priv_requires_priv_key():
    with pytest.raises(ValueError):
        SnmpV3Credentials(
            host="10.0.0.1", username="x", security_level=SecurityLevel.AUTH_PRIV, auth_key="a"
        )
    creds = SnmpV3Credentials(
        host="10.0.0.1", username="x", security_level=SecurityLevel.AUTH_PRIV, auth_key="a", priv_key="p"
    )
    assert creds.security_level == SecurityLevel.AUTH_PRIV


def test_credentials_no_auth_no_priv_needs_no_keys():
    creds = SnmpV3Credentials(host="h", username="u", security_level=SecurityLevel.NO_AUTH_NO_PRIV)
    assert creds.auth_key == ""


# --- Live transport is wired to the SNMPv3 engine ---


def test_live_transport_walk_unreachable_raises():
    """A live walk against a device that never answers surfaces SnmpError."""
    from ingest.snmp_engine import SnmpError

    class _TimeoutSocket:
        def settimeout(self, _t):
            return None

        def sendto(self, data, _addr):
            return len(data)

        def recvfrom(self, _n):
            raise TimeoutError("timed out")

        def close(self):
            return None

    creds = SnmpV3Credentials(host="203.0.113.1", username="u", security_level=SecurityLevel.NO_AUTH_NO_PRIV)
    transport = LiveSnmpV3Transport(creds, timeout=0.01, socket_factory=lambda: _TimeoutSocket())
    with pytest.raises(SnmpError):
        transport.walk("1.3.6.1.2.1.2.2.1.2")


# --- Replay transport + parsing ---


def test_load_replay_and_walk():
    data = load_snmp_replay(EXAMPLE_WALK)
    transport = ReplaySnmpTransport(data)
    assert transport.walk("1.3.6.1.2.1.2.2.1.2") == {"1": "GigabitEthernet1/0/1", "2": "GigabitEthernet1/0/2"}
    assert transport.walk("9.9.9.9") == {}


def test_replay_transport_helper():
    transport = replay_transport(EXAMPLE_WALK)
    assert isinstance(transport, ReplaySnmpTransport)


def test_poll_interface_counters():
    transport = replay_transport(EXAMPLE_WALK)
    counters = poll_interface_counters(transport, collected_at=42.0)
    assert len(counters) == 2
    by_index = {c.if_index: c for c in counters}
    assert by_index[1].name == "GigabitEthernet1/0/1"
    assert by_index[1].alias == "to-app-1"
    assert by_index[1].oper_status == "up"
    assert by_index[1].speed_mbps == 1000.0
    assert by_index[1].in_octets == 1_000_000
    assert by_index[2].in_errors == 3
    assert all(c.collected_at == 42.0 for c in counters)


def test_mac_from_oid_suffix():
    assert mac_from_oid_suffix("0.17.34.51.68.85") == "00:11:22:33:44:55"
    assert mac_from_oid_suffix("0.17.34.51.68.102") == "00:11:22:33:44:66"


def test_poll_mac_table():
    entries = poll_mac_table(replay_transport(EXAMPLE_WALK))
    macs = {e.mac: e for e in entries}
    assert set(macs) == {"00:11:22:33:44:55", "00:11:22:33:44:66"}
    assert macs["00:11:22:33:44:55"].if_index == 1
    assert macs["00:11:22:33:44:66"].bridge_port == 2


# --- Counter delta -> DiagnosticResult ---


def _snap(if_index, name, in_octets, out_octets, in_errors=0, oper="up", speed=1000.0):
    return InterfaceCounters(
        if_index=if_index, name=name, oper_status=oper, speed_mbps=speed,
        in_octets=in_octets, out_octets=out_octets, in_errors=in_errors,
    )


def test_evaluate_interface_delta_utilization_and_errors():
    before = [_snap(1, "Gi1/0/1", 0, 0)]
    after = [_snap(1, "Gi1/0/1", 60_000_000, 60_000_000)]
    results = evaluate_interface_delta(before, after, interval=1.0, device="core-sw")
    by_module = {r.module: r for r in results}
    util = by_module["snmp_link_utilization"]
    # 960 Mbps over a 1000 Mbps link => 96% => degraded/high
    assert util.metrics["util_percent"] == 96.0
    assert util.status == DiagnosticStatus.DEGRADED
    assert util.metrics["device"] == "core-sw"
    assert "snmp_link_errors" in by_module


def test_evaluate_interface_delta_error_rate_fails():
    before = [_snap(2, "Gi1/0/2", 0, 0, in_errors=0)]
    after = [_snap(2, "Gi1/0/2", 0, 0, in_errors=10)]
    results = evaluate_interface_delta(before, after, interval=1.0)
    err = next(r for r in results if r.module == "snmp_link_errors")
    assert err.metrics["errors_per_sec"] == 10.0
    assert err.status == DiagnosticStatus.FAILED


def test_evaluate_interface_delta_skips_down_and_new_interfaces():
    before = [_snap(1, "up-iface", 0, 0), _snap(3, "gone", 0, 0)]
    after = [
        _snap(1, "up-iface", 1000, 1000),
        _snap(2, "down-iface", 1000, 1000, oper="down"),
        # ifIndex 3 absent from 'after'; ifIndex 2 absent from 'before'
    ]
    results = evaluate_interface_delta(before, after, interval=1.0)
    targets = {r.target for r in results}
    assert "up-iface" in targets
    assert "down-iface" not in targets


def test_evaluate_interface_delta_rejects_nonpositive_interval():
    with pytest.raises(ValueError):
        evaluate_interface_delta([], [], interval=0)

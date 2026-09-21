"""Tests for LLDP neighbor ingest (M6)."""

import json
from pathlib import Path

import pytest

from ingest.lldp import (
    OID_LLDP_REM_CHASSIS_ID,
    OID_LLDP_REM_PORT_ID,
    OID_LLDP_REM_SYS_DESC,
    OID_LLDP_REM_SYS_NAME,
    LldpNeighbor,
    lldp_neighbors_from_walk,
    load_lldp_neighbors,
    start_lldp_collector,
)

EXAMPLE_LLDP = Path(__file__).resolve().parent.parent / "examples" / "lldp_neighbors.json"


def test_load_lldp_neighbors_from_json_array():
    neighbors = load_lldp_neighbors(EXAMPLE_LLDP)
    assert len(neighbors) == 2
    first = neighbors[0]
    assert first.local_port == "Gi1/0/1"
    assert first.neighbor_sys_name == "app-1"
    assert first.neighbor_mgmt_addr == "10.0.0.11"


def test_load_lldp_neighbors_from_jsonl(tmp_path):
    path = tmp_path / "lldp.jsonl"
    rows = [
        {"local_port": "1", "neighbor_sys_name": "host-a"},
        {"local_port": "2", "neighbor_sys_name": "host-b"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    neighbors = load_lldp_neighbors(path)
    assert [n.neighbor_sys_name for n in neighbors] == ["host-a", "host-b"]


def test_load_lldp_neighbors_empty_file(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("   ", encoding="utf-8")
    assert load_lldp_neighbors(path) == []


def test_load_lldp_neighbors_rejects_malformed_line(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(ValueError):
        load_lldp_neighbors(path)


def test_identities_are_lowercased_and_non_empty():
    nb = LldpNeighbor(local_port="1", neighbor_sys_name="Host-A", neighbor_mgmt_addr="10.0.0.11")
    identities = nb.identities()
    assert "host-a" in identities
    assert "10.0.0.11" in identities
    assert "" not in identities


def test_lldp_neighbors_from_walk():
    walk = {
        OID_LLDP_REM_SYS_NAME: {"1": "host-a", "2.0": "host-b"},
        OID_LLDP_REM_CHASSIS_ID: {"1": "00:11:22:33:44:55"},
        OID_LLDP_REM_PORT_ID: {"1": "eth0"},
        OID_LLDP_REM_SYS_DESC: {},
    }
    neighbors = lldp_neighbors_from_walk(walk)
    by_port = {n.local_port: n for n in neighbors}
    # suffix "2.0" collapses to local port "2"
    assert set(by_port) == {"1", "2"}
    assert by_port["1"].neighbor_sys_name == "host-a"
    assert by_port["1"].neighbor_chassis_id == "00:11:22:33:44:55"
    assert by_port["2"].neighbor_sys_name == "host-b"


def test_lldp_neighbors_from_walk_decodes_bytes():
    walk = {OID_LLDP_REM_SYS_NAME: {"1": b"host-bytes"}}
    neighbors = lldp_neighbors_from_walk(walk)
    assert neighbors[0].neighbor_sys_name == "host-bytes"


def test_start_lldp_collector_is_stubbed():
    with pytest.raises(NotImplementedError):
        start_lldp_collector()

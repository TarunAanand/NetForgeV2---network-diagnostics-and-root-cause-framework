"""LLDP neighbor ingest for M6 host-to-switch-port correlation.

Like the other passive-telemetry modules, LLDP is delivered offline-first: a
captured LLDP-MIB ``lldpRemTable`` (or a hand-authored JSON list) is replayed
into typed :class:`LldpNeighbor` records. Live LLDP collection is intentionally
stubbed until an SNMP engine is bundled.

A walk is expressed in the same ``{base_oid: {suffix: value}}`` shape used by
:mod:`ingest.snmp_counters`, where ``suffix`` identifies the local switch port on
which the neighbor was learned (the leading LLDP rem-table port component).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

# --- LLDP-MIB lldpRemTable columns ---
OID_LLDP_REM_CHASSIS_ID = "1.0.8802.1.1.2.1.4.1.1.5"
OID_LLDP_REM_PORT_ID = "1.0.8802.1.1.2.1.4.1.1.7"
OID_LLDP_REM_SYS_NAME = "1.0.8802.1.1.2.1.4.1.1.9"
OID_LLDP_REM_SYS_DESC = "1.0.8802.1.1.2.1.4.1.1.10"


class LldpNeighbor(BaseModel):
    """A directly-connected neighbor as seen on one local switch port."""

    local_port: str
    neighbor_chassis_id: str | None = None
    neighbor_port_id: str | None = None
    neighbor_sys_name: str | None = None
    neighbor_sys_desc: str | None = None
    neighbor_mgmt_addr: str | None = None
    ttl: int | None = None

    def identities(self) -> set[str]:
        """All lowercase identifiers this neighbor could be matched on."""
        candidates = {
            self.neighbor_chassis_id,
            self.neighbor_sys_name,
            self.neighbor_mgmt_addr,
            self.neighbor_port_id,
        }
        return {c.strip().lower() for c in candidates if c}


def load_lldp_neighbors(path: str | Path) -> list[LldpNeighbor]:
    """Load neighbors from a JSON array (or JSON-lines) capture file."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("LLDP JSON root must be a list")
        return [LldpNeighbor.model_validate(row) for row in data]
    return [LldpNeighbor.model_validate(json.loads(line)) for line in text.splitlines() if line.strip()]


def lldp_neighbors_from_walk(walk: dict[str, dict[str, Any]]) -> list[LldpNeighbor]:
    """Assemble neighbors from raw LLDP-MIB column walks keyed by local port."""
    chassis = walk.get(OID_LLDP_REM_CHASSIS_ID, {})
    port_id = walk.get(OID_LLDP_REM_PORT_ID, {})
    sys_name = walk.get(OID_LLDP_REM_SYS_NAME, {})
    sys_desc = walk.get(OID_LLDP_REM_SYS_DESC, {})

    local_ports = sorted(set(chassis) | set(port_id) | set(sys_name) | set(sys_desc))
    neighbors: list[LldpNeighbor] = []
    for suffix in local_ports:
        local_port = str(suffix).split(".")[0]
        neighbor = LldpNeighbor(
            local_port=local_port,
            neighbor_chassis_id=_str_or_none(chassis.get(suffix)),
            neighbor_port_id=_str_or_none(port_id.get(suffix)),
            neighbor_sys_name=_str_or_none(sys_name.get(suffix)),
            neighbor_sys_desc=_str_or_none(sys_desc.get(suffix)),
        )
        if neighbor.identities():
            neighbors.append(neighbor)
    return neighbors


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = value.decode("utf-8", "ignore") if isinstance(value, (bytes, bytearray)) else str(value)
    return text or None


def start_lldp_collector(host: str = "0.0.0.0", port: int = 161) -> None:
    raise NotImplementedError(
        f"Live LLDP collection from {host}:{port} is not implemented yet; "
        "capture the LLDP-MIB lldpRemTable to JSON and use load_lldp_neighbors()."
    )

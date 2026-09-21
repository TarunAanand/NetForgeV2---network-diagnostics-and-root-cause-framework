"""SNMPv3 interface counter and bridge (MAC) table ingest for M6.

NetForge keeps its "no heavy native dependencies" stance, so live SNMPv3 is
implemented with a self-contained pure-stdlib engine (:mod:`ingest.snmp_engine`,
:mod:`ingest.asn1`, :mod:`ingest.aes`) rather than a third-party ASN.1/SNMP stack.
This module provides:

- A typed :class:`SnmpV3Credentials` config (USM security levels / auth+priv).
- The canonical IF-MIB and BRIDGE-MIB OIDs used for device-level telemetry.
- An :class:`SnmpTransport` seam with two implementations:
  * :class:`ReplaySnmpTransport` — offline replay from a JSON walk capture, the
    default deterministic path (mirrors the sFlow/IPFIX replay convention).
  * :class:`LiveSnmpV3Transport` — live polling over UDP via the bundled SNMPv3
    USM engine (noAuthNoPriv / authNoPriv / authPriv with AES-128-CFB).
- Parsing into :class:`InterfaceCounters` and :class:`MacTableEntry` models plus
  ``DiagnosticResult`` builders that turn two counter snapshots into true
  switch/router link utilization and error observations.

A walk is represented uniformly as ``{index_suffix: value}`` where
``index_suffix`` is the OID remainder after the base column OID (the ifIndex for
interface columns, or the six dotted MAC octets for bridge FDB addresses).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from core.metrics.throughput import bps_from_byte_delta, utilization_percent
from core.result import DiagnosticResult, DiagnosticStatus, Severity


class SecurityLevel(str, Enum):
    NO_AUTH_NO_PRIV = "noAuthNoPriv"
    AUTH_NO_PRIV = "authNoPriv"
    AUTH_PRIV = "authPriv"


@dataclass(frozen=True)
class SnmpV3Credentials:
    """USM credentials for an SNMPv3 managed device."""

    host: str
    username: str
    security_level: SecurityLevel = SecurityLevel.AUTH_NO_PRIV
    auth_protocol: str = "sha"      # sha | md5 | none
    auth_key: str = ""
    priv_protocol: str = "aes"      # aes | des | none
    priv_key: str = ""
    context_name: str = ""
    port: int = 161

    def __post_init__(self) -> None:
        if self.security_level != SecurityLevel.NO_AUTH_NO_PRIV and not self.auth_key:
            raise ValueError("auth_key is required for security level " + self.security_level.value)
        if self.security_level == SecurityLevel.AUTH_PRIV and not self.priv_key:
            raise ValueError("priv_key is required for security level authPriv")


# --- IF-MIB / IF-MIB2 interface columns ---
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"       # Mbps
OID_IF_HC_IN_OCTETS = "1.3.6.1.2.1.31.1.1.1.6"       # 64-bit
OID_IF_HC_OUT_OCTETS = "1.3.6.1.2.1.31.1.1.1.10"     # 64-bit
OID_IF_IN_ERRORS = "1.3.6.1.2.1.2.2.1.14"
OID_IF_OUT_ERRORS = "1.3.6.1.2.1.2.2.1.20"
OID_IF_IN_DISCARDS = "1.3.6.1.2.1.2.2.1.13"
OID_IF_OUT_DISCARDS = "1.3.6.1.2.1.2.2.1.19"

# --- BRIDGE-MIB forwarding database (MAC -> bridge port) ---
OID_DOT1D_TP_FDB_ADDRESS = "1.3.6.1.2.1.17.4.3.1.1"  # index = 6 MAC octets
OID_DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
OID_DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"

# ifOperStatus enumeration (RFC 2863).
OPER_STATUS_NAMES = {
    1: "up", 2: "down", 3: "testing", 4: "unknown",
    5: "dormant", 6: "notPresent", 7: "lowerLayerDown",
}


class SnmpTransport(Protocol):
    """A minimal SNMP walk/get seam so replay and live engines are swappable."""

    def walk(self, base_oid: str) -> dict[str, Any]:
        """Return ``{index_suffix: value}`` for every instance under base_oid."""
        ...

    def get(self, oid: str) -> Any:
        ...


class ReplaySnmpTransport:
    """Offline transport backed by a captured ``{base_oid: {suffix: value}}`` map."""

    def __init__(self, data: dict[str, dict[str, Any]]):
        self.data = data

    def walk(self, base_oid: str) -> dict[str, Any]:
        return dict(self.data.get(base_oid, {}))

    def get(self, oid: str) -> Any:
        base, _, suffix = oid.rpartition(".")
        column = self.data.get(base, {})
        if suffix in column:
            return column[suffix]
        # Fall back to a scalar stored under the full OID.
        return self.data.get(oid, {}).get("")


class LiveSnmpV3Transport:
    """SNMPv3 transport backed by the self-contained :mod:`ingest.snmp_engine`.

    Speaks the real SNMPv3 wire protocol (USM) over UDP with no third-party SNMP
    dependency: ``noAuthNoPriv``, ``authNoPriv`` (HMAC-SHA/MD5) and ``authPriv``
    (AES-128-CFB, RFC 3826). ``socket_factory`` may be injected to drive the
    engine against a fake UDP responder in tests.
    """

    def __init__(self, credentials: SnmpV3Credentials, timeout: float = 2.0, socket_factory=None):
        from ingest.snmp_engine import SnmpV3Engine

        self.credentials = credentials
        self.timeout = timeout
        self.engine = SnmpV3Engine(
            host=credentials.host,
            port=credentials.port,
            username=credentials.username,
            security_level=credentials.security_level.value,
            auth_protocol=credentials.auth_protocol,
            auth_key=credentials.auth_key,
            priv_protocol=credentials.priv_protocol,
            priv_key=credentials.priv_key,
            context_name=credentials.context_name,
            timeout=timeout,
            socket_factory=socket_factory,
        )

    def walk(self, base_oid: str) -> dict[str, Any]:
        return self.engine.walk(base_oid)

    def get(self, oid: str) -> Any:
        return self.engine.get(oid)


def load_snmp_replay(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load a captured SNMP walk document (JSON object of ``{oid: {suffix: value}}``)."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("SNMP replay JSON root must be an object keyed by base OID")
    return data


def replay_transport(path: str | Path) -> ReplaySnmpTransport:
    return ReplaySnmpTransport(load_snmp_replay(path))


class InterfaceCounters(BaseModel):
    """A point-in-time snapshot of one interface's counters."""

    if_index: int
    name: str
    alias: str | None = None
    oper_status: str = "unknown"
    speed_mbps: float | None = Field(default=None, ge=0)
    in_octets: int = 0
    out_octets: int = 0
    in_errors: int = 0
    out_errors: int = 0
    in_discards: int = 0
    out_discards: int = 0
    collected_at: float = 0.0


class MacTableEntry(BaseModel):
    """A learned MAC -> switch port mapping from the bridge forwarding DB."""

    mac: str
    bridge_port: int
    if_index: int | None = None


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def poll_interface_counters(transport: SnmpTransport, collected_at: float = 0.0) -> list[InterfaceCounters]:
    """Walk the IF-MIB columns and assemble one :class:`InterfaceCounters` per ifIndex."""
    descr = transport.walk(OID_IF_DESCR)
    alias = transport.walk(OID_IF_ALIAS)
    oper = transport.walk(OID_IF_OPER_STATUS)
    speed = transport.walk(OID_IF_HIGH_SPEED)
    hc_in = transport.walk(OID_IF_HC_IN_OCTETS)
    hc_out = transport.walk(OID_IF_HC_OUT_OCTETS)
    in_err = transport.walk(OID_IF_IN_ERRORS)
    out_err = transport.walk(OID_IF_OUT_ERRORS)
    in_disc = transport.walk(OID_IF_IN_DISCARDS)
    out_disc = transport.walk(OID_IF_OUT_DISCARDS)

    indices = sorted(
        {int(idx) for idx in descr.keys() if str(idx).lstrip("-").isdigit()},
    )
    counters: list[InterfaceCounters] = []
    for idx in indices:
        key = str(idx)
        oper_raw = _to_int(oper.get(key))
        speed_raw = speed.get(key)
        counters.append(
            InterfaceCounters(
                if_index=idx,
                name=str(descr.get(key, f"if{idx}")),
                alias=str(alias[key]) if key in alias and alias[key] not in (None, "") else None,
                oper_status=OPER_STATUS_NAMES.get(oper_raw, "unknown"),
                speed_mbps=float(speed_raw) if speed_raw not in (None, "") else None,
                in_octets=_to_int(hc_in.get(key)),
                out_octets=_to_int(hc_out.get(key)),
                in_errors=_to_int(in_err.get(key)),
                out_errors=_to_int(out_err.get(key)),
                in_discards=_to_int(in_disc.get(key)),
                out_discards=_to_int(out_disc.get(key)),
                collected_at=collected_at,
            )
        )
    return counters


def mac_from_oid_suffix(suffix: str) -> str:
    """Convert a bridge FDB OID suffix (six decimal octets) to a MAC string."""
    parts = [p for p in suffix.split(".") if p != ""]
    octets = parts[-6:] if len(parts) >= 6 else parts
    return ":".join(f"{int(o) & 0xFF:02x}" for o in octets)


def poll_mac_table(transport: SnmpTransport) -> list[MacTableEntry]:
    """Walk the bridge forwarding database into :class:`MacTableEntry` rows."""
    fdb_port = transport.walk(OID_DOT1D_TP_FDB_PORT)
    base_port_ifindex = transport.walk(OID_DOT1D_BASE_PORT_IFINDEX)
    entries: list[MacTableEntry] = []
    for suffix, _value in transport.walk(OID_DOT1D_TP_FDB_ADDRESS).items():
        bridge_port = _to_int(fdb_port.get(suffix))
        if bridge_port == 0:
            continue
        if_index_raw = base_port_ifindex.get(str(bridge_port))
        entries.append(
            MacTableEntry(
                mac=mac_from_oid_suffix(suffix),
                bridge_port=bridge_port,
                if_index=_to_int(if_index_raw) if if_index_raw not in (None, "") else None,
            )
        )
    return entries


def evaluate_interface_delta(
    before: list[InterfaceCounters],
    after: list[InterfaceCounters],
    interval: float,
    device: str = "switch",
) -> list[DiagnosticResult]:
    """Turn two counter snapshots into utilization + error ``DiagnosticResult``s.

    Counter deltas are computed per ifIndex and translated with the same
    throughput math used by the local psutil link collector, so device-level and
    host-level link observations share thresholds and metric names.
    """
    if interval <= 0:
        raise ValueError("interval must be positive")
    before_by_index = {c.if_index: c for c in before}
    results: list[DiagnosticResult] = []
    for cur in after:
        prev = before_by_index.get(cur.if_index)
        if prev is None:
            continue
        if cur.oper_status != "up":
            continue

        rx_bps = bps_from_byte_delta(cur.in_octets - prev.in_octets, interval)
        tx_bps = bps_from_byte_delta(cur.out_octets - prev.out_octets, interval)
        total_bps = rx_bps + tx_bps
        capacity = (cur.speed_mbps * 1_000_000) if cur.speed_mbps else None
        util = utilization_percent(total_bps, capacity)

        if util is not None and util >= 90:
            util_status, util_sev = DiagnosticStatus.DEGRADED, Severity.HIGH
        elif util is not None and util >= 75:
            util_status, util_sev = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        else:
            util_status, util_sev = DiagnosticStatus.HEALTHY, Severity.INFO

        label = cur.alias or cur.name
        results.append(
            DiagnosticResult(
                module="snmp_link_utilization",
                category="link",
                status=util_status,
                severity=util_sev,
                summary=(
                    f"{device} {label}: {util:.1f}% util"
                    if util is not None
                    else f"{device} {label}: {total_bps / 1e6:.2f} Mbps (speed unknown)"
                ),
                target=label,
                metrics={
                    "device": device,
                    "if_index": cur.if_index,
                    "rx_bps": round(rx_bps, 1),
                    "tx_bps": round(tx_bps, 1),
                    "total_bps": round(total_bps, 1),
                    "util_percent": util,
                    "speed_mbps": cur.speed_mbps,
                },
                evidence=[
                    f"SNMP ifHCInOctets/ifHCOutOctets delta over {interval}s on {device}",
                    f"RX {rx_bps / 1e6:.2f} Mbps / TX {tx_bps / 1e6:.2f} Mbps",
                ],
            )
        )

        err_delta = (cur.in_errors - prev.in_errors) + (cur.out_errors - prev.out_errors)
        disc_delta = (cur.in_discards - prev.in_discards) + (cur.out_discards - prev.out_discards)
        err_rate = err_delta / interval
        disc_rate = disc_delta / interval
        if err_rate >= 5 or disc_rate >= 5:
            err_status, err_sev = DiagnosticStatus.FAILED, Severity.HIGH
        elif err_rate >= 0.5 or disc_rate >= 0.5:
            err_status, err_sev = DiagnosticStatus.DEGRADED, Severity.MEDIUM
        else:
            err_status, err_sev = DiagnosticStatus.HEALTHY, Severity.INFO

        results.append(
            DiagnosticResult(
                module="snmp_link_errors",
                category="link",
                status=err_status,
                severity=err_sev,
                summary=f"{device} {label}: {err_rate:.2f} errs/s, {disc_rate:.2f} discards/s",
                target=label,
                metrics={
                    "device": device,
                    "if_index": cur.if_index,
                    "errors_per_sec": round(err_rate, 3),
                    "discards_per_sec": round(disc_rate, 3),
                },
                evidence=[f"SNMP ifInErrors/ifOutErrors/discards delta over {interval}s on {device}"],
                warnings=["Device-level interface errors/discards rising"]
                if err_rate >= 0.5 or disc_rate >= 0.5
                else [],
            )
        )
    return results

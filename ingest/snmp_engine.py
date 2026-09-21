"""Self-contained SNMPv3 (USM) engine — no third-party SNMP/ASN.1 stack.

Implements just enough of RFC 3412 (message processing), RFC 3414 (USM security)
and RFC 3826 (AES privacy) to poll a device with ``get`` / ``getnext`` / ``walk``
over UDP, using the pure-stdlib :mod:`ingest.asn1` codec and :mod:`ingest.aes`
cipher. Supported security:

- ``noAuthNoPriv``
- ``authNoPriv`` with HMAC-SHA1/SHA2 (and MD5) authentication
- ``authPriv`` with AES-128-CFB privacy (RFC 3826)

DES privacy is intentionally not implemented. Engine discovery, key localization
and message authentication/encryption follow the RFCs; the byte-level auth digest
placement is centralized in :func:`auth_params_offset` so both the client and any
test responder share one code path.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import socket
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable

from ingest import asn1
from ingest.aes import aes128_cfb_decrypt, aes128_cfb_encrypt

NO_AUTH_NO_PRIV = "noAuthNoPriv"
AUTH_NO_PRIV = "authNoPriv"
AUTH_PRIV = "authPriv"

_AUTH_HASHES = {
    "sha": "sha1", "sha1": "sha1", "md5": "md5",
    "sha224": "sha224", "sha256": "sha256",
    "sha384": "sha384", "sha512": "sha512",
}

AUTH_PARAM_LEN = 12
PRIV_PARAM_LEN = 8
# RFC 3414 3.2.7 acceptable clock skew against the authoritative engine.
TIME_WINDOW_SECONDS = 150
MAX_WALK_OIDS = 10_000
MAX_MESSAGE_SIZE = 65507
USM_SECURITY_MODEL = 3
SNMP_VERSION_3 = 3
SYS_DESCR = "1.3.6.1.2.1.1.1.0"

FLAG_AUTH = 0x01
FLAG_PRIV = 0x02
FLAG_REPORTABLE = 0x04

_EXCEPTION_VALUES = ("noSuchObject", "noSuchInstance", "endOfMibView")


class SnmpError(RuntimeError):
    """Raised for protocol, security, or transport failures."""


def _default_socket() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _hash_name(protocol: str) -> str:
    name = _AUTH_HASHES.get((protocol or "").lower())
    if name is None:
        raise SnmpError(f"unsupported auth protocol: {protocol!r}")
    return name


def peek_msg_id(data: bytes) -> int:
    """Read msgID from the plaintext header, without keys or decryption."""
    _tag, msg_body, _ = asn1.decode(data, 0)
    children = asn1.decode_children(msg_body)
    return asn1.parse_int(asn1.decode_children(children[1][1])[0][1])


def _as_bytes(value: str | bytes) -> bytes:
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def password_to_key(password: str | bytes, engine_id: bytes, hash_name: str = "sha1") -> bytes:
    """RFC 3414 A.2 password-to-key: 1 MiB fold, then localize with the engineID."""
    pw = _as_bytes(password)
    if not pw:
        raise SnmpError("password must be non-empty")
    running = hashlib.new(hash_name)
    index = 0
    buf = bytearray(64)
    consumed = 0
    while consumed < 1048576:
        for i in range(64):
            buf[i] = pw[index % len(pw)]
            index += 1
        running.update(buf)
        consumed += 64
    ku = running.digest()
    localized = hashlib.new(hash_name)
    localized.update(ku + bytes(engine_id) + ku)
    return localized.digest()


def auth_params_offset(data: bytes) -> int:
    """Absolute offset of the 12-byte msgAuthenticationParameters value in ``data``.

    Walks the message structure and uses each TLV's value length to recover the
    value start (``next_offset - len(value)``), so it works for both messages we
    build and messages we receive.
    """
    _tag, msg_body, off = asn1.decode(data, 0)
    body_start = off - len(msg_body)
    o = body_start
    _t, _v, o = asn1.decode(data, o)      # msgVersion
    _t, _v, o = asn1.decode(data, o)      # msgGlobalData
    _t, sec_value, o = asn1.decode(data, o)  # msgSecurityParameters OCTET STRING
    sp_value_start = o - len(sec_value)
    # sec_value is the OCTET STRING payload, i.e. the UsmSecurityParameters
    # SEQUENCE TLV; unwrap it to reach the six USM fields.
    _st, seq_body, seq_off = asn1.decode(sec_value, 0)
    seq_body_start = seq_off - len(seq_body)
    oo = 0
    ap_in_seq = 0
    for i in range(5):
        _t, val, oo = asn1.decode(seq_body, oo)
        if i == 4:
            ap_in_seq = oo - len(val)
            break
    return sp_value_start + seq_body_start + ap_in_seq


def encode_scoped_pdu(context_engine_id: bytes | str, context_name: str | bytes, pdu: bytes) -> bytes:
    return asn1.enc_seq([asn1.enc_oct(_as_bytes(context_engine_id) or b""), asn1.enc_oct(_as_bytes(context_name)), pdu])


def encode_message(
    *,
    msg_id: int,
    engine_id: bytes,
    boots: int,
    engine_time: int,
    username: str | bytes,
    flags: int,
    scoped_pdu: bytes,
    auth_key: bytes | None = None,
    auth_hash: str = "sha1",
    priv_key: bytes | None = None,
    max_size: int = MAX_MESSAGE_SIZE,
    sec_model: int = USM_SECURITY_MODEL,
) -> bytes:
    """Assemble (and optionally encrypt + authenticate) an SNMPv3 message."""
    if flags & FLAG_PRIV:
        if priv_key is None:
            raise SnmpError("priv_key required when privFlag is set")
        salt = os.urandom(PRIV_PARAM_LEN)
        iv = boots.to_bytes(4, "big") + engine_time.to_bytes(4, "big") + salt
        msg_data = asn1.enc_oct(aes128_cfb_encrypt(priv_key[:16], iv, scoped_pdu))
        priv_params = salt
    else:
        msg_data = scoped_pdu
        priv_params = b""

    auth_params = b"\x00" * AUTH_PARAM_LEN if (flags & FLAG_AUTH) else b""
    sec_body = asn1.enc_seq(
        [
            asn1.enc_oct(bytes(engine_id)),
            asn1.enc_int(boots),
            asn1.enc_int(engine_time),
            asn1.enc_oct(_as_bytes(username)),
            asn1.enc_oct(auth_params),
            asn1.enc_oct(priv_params),
        ]
    )
    global_data = asn1.enc_seq(
        [asn1.enc_int(msg_id), asn1.enc_int(max_size), asn1.enc_oct(bytes([flags])), asn1.enc_int(sec_model)]
    )
    message = asn1.enc_seq(
        [asn1.enc_int(SNMP_VERSION_3), global_data, asn1.enc_oct(sec_body), msg_data]
    )
    if flags & FLAG_AUTH:
        if auth_key is None:
            raise SnmpError("auth_key required when authFlag is set")
        offset = auth_params_offset(message)
        digest = hmac.new(auth_key, message, auth_hash).digest()[:AUTH_PARAM_LEN]
        message = message[:offset] + digest + message[offset + AUTH_PARAM_LEN :]
    return message


@dataclass
class ParsedMessage:
    version: int
    msg_id: int
    flags: int
    engine_id: bytes
    boots: int
    engine_time: int
    username: bytes
    auth_params: bytes
    priv_params: bytes
    context_engine_id: bytes
    context_name: bytes
    pdu_tag: int
    request_id: int
    error_status: int
    error_index: int
    varbinds: list[tuple[str, Any]] = field(default_factory=list)


def decode_message(
    data: bytes,
    *,
    priv_key: bytes | None = None,
    auth_key: bytes | None = None,
    auth_hash: str = "sha1",
    verify_auth: bool = False,
) -> ParsedMessage:
    """Parse an SNMPv3 message, decrypting the scoped PDU and verifying auth."""
    tag, msg_body, _ = asn1.decode(data, 0)
    if tag != asn1.SEQUENCE:
        raise SnmpError("SNMPv3 message must be a SEQUENCE")
    ch = asn1.decode_children(msg_body)
    version = asn1.parse_int(ch[0][1])
    gd = asn1.decode_children(ch[1][1])
    msg_id = asn1.parse_int(gd[0][1])
    flags = gd[2][1][0] if gd[2][1] else 0
    _sec_tag, sec_body, _ = asn1.decode(ch[2][1], 0)  # unwrap UsmSecurityParameters SEQUENCE
    sec = asn1.decode_children(sec_body)
    engine_id, boots_b, time_b, username, auth_params, priv_params = (sec[i][1] for i in range(6))
    boots = asn1.parse_int(boots_b)
    engine_time = asn1.parse_int(time_b)

    if (flags & FLAG_AUTH) and verify_auth:
        if auth_key is None:
            raise SnmpError("auth_key required to verify an authenticated message")
        offset = auth_params_offset(data)
        zeroed = data[:offset] + b"\x00" * AUTH_PARAM_LEN + data[offset + AUTH_PARAM_LEN :]
        expected = hmac.new(auth_key, zeroed, auth_hash).digest()[:AUTH_PARAM_LEN]
        if not hmac.compare_digest(expected, auth_params):
            raise SnmpError("authentication failure: digest mismatch")

    md_tag, md_val = ch[3]
    if (flags & FLAG_PRIV) and md_tag == asn1.OCTET_STRING:
        if priv_key is None:
            raise SnmpError("priv_key required to decrypt a private message")
        iv = boots.to_bytes(4, "big") + engine_time.to_bytes(4, "big") + priv_params
        decrypted = aes128_cfb_decrypt(priv_key[:16], iv, md_val)
        # Decrypted payload is the full ScopedPDU SEQUENCE TLV; unwrap it.
        _sc_tag, scoped_body, _ = asn1.decode(decrypted, 0)
    else:
        # Plaintext msgData: decode already stripped the ScopedPDU SEQUENCE tag/len.
        scoped_body = md_val

    sch = asn1.decode_children(scoped_body)
    context_engine_id = sch[0][1]
    context_name = sch[1][1]
    pdu_tag, pdu_body = sch[2]
    pch = asn1.decode_children(pdu_body)
    request_id = asn1.parse_int(pch[0][1])
    error_status = asn1.parse_int(pch[1][1])
    error_index = asn1.parse_int(pch[2][1])
    varbinds: list[tuple[str, Any]] = []
    for _vb_tag, vb_val in asn1.decode_children(pch[3][1]):
        parts = asn1.decode_children(vb_val)
        oid = asn1.parse_oid(parts[0][1])
        value = asn1.decode_value(parts[1][0], parts[1][1])
        varbinds.append((oid, value))
    return ParsedMessage(
        version=version, msg_id=msg_id, flags=flags, engine_id=engine_id, boots=boots,
        engine_time=engine_time, username=username, auth_params=auth_params,
        priv_params=priv_params, context_engine_id=context_engine_id, context_name=context_name,
        pdu_tag=pdu_tag, request_id=request_id, error_status=error_status,
        error_index=error_index, varbinds=varbinds,
    )


class SnmpV3Engine:
    """A minimal SNMPv3 USM engine supporting get/getnext/walk over UDP."""

    def __init__(
        self,
        host: str,
        port: int = 161,
        username: str = "",
        security_level: str = AUTH_NO_PRIV,
        auth_protocol: str = "sha",
        auth_key: str = "",
        priv_protocol: str = "aes",
        priv_key: str = "",
        context_name: str = "",
        context_engine_id: bytes = b"",
        timeout: float = 2.0,
        retries: int = 1,
        socket_factory: Callable[[], Any] | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.level = security_level.value if hasattr(security_level, "value") else str(security_level)
        if self.level not in (NO_AUTH_NO_PRIV, AUTH_NO_PRIV, AUTH_PRIV):
            raise SnmpError(f"unknown security level: {self.level!r}")
        self.auth_protocol = auth_protocol
        self.auth_key = auth_key
        self.priv_protocol = (priv_protocol or "").lower()
        self.priv_key = priv_key
        self.context_name = context_name
        self.timeout = timeout
        self.retries = retries
        self._socket_factory = socket_factory or _default_socket

        if self.level != NO_AUTH_NO_PRIV and auth_protocol.lower() in ("", "none"):
            raise SnmpError("auth_protocol is required for authenticated levels")
        if self.level == AUTH_PRIV and self.priv_protocol not in ("aes", "aes128"):
            raise SnmpError("only AES privacy (RFC 3826) is supported; DES is not implemented")

        self._auth_hash = _hash_name(auth_protocol) if self.level != NO_AUTH_NO_PRIV else "sha1"
        self._context_engine_id = context_engine_id
        self._engine_id = b""
        self._boots = 0
        self._engine_time = 0
        self._ref: float | None = None
        self._localized_auth: bytes | None = None
        self._localized_priv: bytes | None = None
        self._msg_id = int.from_bytes(os.urandom(2), "big")
        self._req_id = int.from_bytes(os.urandom(2), "big")
        self._discovered = False

    # --- helpers ---
    @property
    def use_auth(self) -> bool:
        return self.level in (AUTH_NO_PRIV, AUTH_PRIV)

    @property
    def use_priv(self) -> bool:
        return self.level == AUTH_PRIV

    def _next_id(self, attr: str) -> int:
        value = (getattr(self, attr) + 1) & 0x7FFFFFFF
        setattr(self, attr, value)
        return value

    def _flags(self) -> int:
        flags = FLAG_REPORTABLE
        if self.use_auth:
            flags |= FLAG_AUTH
        if self.use_priv:
            flags |= FLAG_PRIV
        return flags

    def _send_recv(self, message: bytes, expected_msg_id: int) -> bytes:
        """Send one message and return the first reply carrying ``expected_msg_id``.

        UDP datagrams that do not match the outstanding request are discarded
        rather than parsed, so an off-path injector cannot answer on behalf of
        the device by racing the real response. Discarding never extends the
        attempt: the socket timeout shrinks to the remaining deadline, so a
        stream of foreign datagrams cannot keep the poll alive.
        """
        last: Exception | None = None
        for _ in range(self.retries + 1):
            sock = self._socket_factory()
            try:
                sock.settimeout(self.timeout)
                sock.sendto(message, (self.host, self.port))
                deadline = _time.monotonic() + self.timeout
                while True:
                    remaining = deadline - _time.monotonic()
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
                    data, _addr = sock.recvfrom(MAX_MESSAGE_SIZE)
                    data = bytes(data)
                    try:
                        if peek_msg_id(data) == expected_msg_id:
                            return data
                    except (SnmpError, ValueError, IndexError):
                        continue
                last = SnmpError("no reply matched the outstanding msgID")
            except (socket.timeout, TimeoutError, OSError) as exc:
                last = exc
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        raise SnmpError(f"no SNMP response from {self.host}:{self.port} ({last})")

    def _localize_keys(self) -> None:
        if self.use_auth:
            self._localized_auth = password_to_key(self.auth_key, self._engine_id, self._auth_hash)
        if self.use_priv:
            self._localized_priv = password_to_key(self.priv_key, self._engine_id, self._auth_hash)[:16]

    def _current_time(self) -> int:
        if self._ref is None:
            return self._engine_time
        elapsed = int(_time.monotonic() - self._ref)
        return min(self._engine_time + elapsed, 2147483647)

    # --- protocol ---
    def discover(self) -> None:
        """Learn the authoritative engineID/boots/time (RFC 3414 discovery)."""
        if self._discovered:
            return
        req_id = self._next_id("_req_id")
        msg_id = self._next_id("_msg_id")
        pdu = asn1.enc_request_pdu(asn1.GET_REQUEST, req_id, [SYS_DESCR])
        scoped = encode_scoped_pdu(b"", "", pdu)
        message = encode_message(
            msg_id=msg_id, engine_id=b"", boots=0, engine_time=0,
            username="", flags=FLAG_REPORTABLE, scoped_pdu=scoped,
        )
        parsed = decode_message(self._send_recv(message, msg_id))
        if not parsed.engine_id:
            raise SnmpError("engine discovery returned no authoritative engineID")
        self._engine_id = parsed.engine_id
        self._boots = parsed.boots
        self._engine_time = parsed.engine_time
        self._ref = _time.monotonic()
        if not self._context_engine_id:
            self._context_engine_id = self._engine_id
        self._localize_keys()
        self._discovered = True

    def _exchange(self, pdu_tag: int, oids: list[str]) -> ParsedMessage:
        self.discover()
        req_id = self._next_id("_req_id")
        msg_id = self._next_id("_msg_id")
        pdu = asn1.enc_request_pdu(pdu_tag, req_id, oids)
        scoped = encode_scoped_pdu(self._context_engine_id, self.context_name, pdu)
        message = encode_message(
            msg_id=msg_id, engine_id=self._engine_id, boots=self._boots,
            engine_time=self._current_time(), username=self.username, flags=self._flags(),
            scoped_pdu=scoped, auth_key=self._localized_auth, auth_hash=self._auth_hash,
            priv_key=self._localized_priv,
        )
        parsed = decode_message(
            self._send_recv(message, msg_id), priv_key=self._localized_priv,
            auth_key=self._localized_auth, auth_hash=self._auth_hash, verify_auth=self.use_auth,
        )
        if parsed.request_id != req_id:
            raise SnmpError("response request-id does not match the request")
        if parsed.engine_id and parsed.engine_id != self._engine_id:
            raise SnmpError("response came from a different authoritative engineID")
        self._check_time_window(parsed)
        if parsed.boots > self._boots or (parsed.boots == self._boots and parsed.engine_time > self._engine_time):
            self._boots = parsed.boots
            self._engine_time = parsed.engine_time
            self._ref = _time.monotonic()
        if parsed.error_status != 0:
            raise SnmpError(f"SNMP error-status {parsed.error_status} at index {parsed.error_index}")
        return parsed

    def get(self, oid: str) -> Any:
        parsed = self._exchange(asn1.GET_REQUEST, [oid])
        if not parsed.varbinds:
            raise SnmpError("empty GET response")
        _oid, value = parsed.varbinds[0]
        return None if value in _EXCEPTION_VALUES else value

    def getnext(self, oid: str) -> tuple[str, Any]:
        parsed = self._exchange(asn1.GET_NEXT_REQUEST, [oid])
        if not parsed.varbinds:
            raise SnmpError("empty GETNEXT response")
        return parsed.varbinds[0]

    def _check_time_window(self, parsed: ParsedMessage) -> None:
        """RFC 3414 3.2.7 replay window for authenticated responses."""
        if not self.use_auth:
            return
        if parsed.boots < self._boots:
            raise SnmpError("response is outside the time window (stale engine boots)")
        if parsed.boots == self._boots and abs(parsed.engine_time - self._current_time()) > TIME_WINDOW_SECONDS:
            raise SnmpError("response is outside the time window (clock skew)")

    def walk(self, base_oid: str, max_oids: int = MAX_WALK_OIDS) -> dict[str, Any]:
        """GETNEXT-loop a column, returning ``{index_suffix: value}``.

        Raises :class:`SnmpError` when ``max_oids`` is exhausted, so a caller
        never mistakes a truncated table for a complete one.
        """
        base = base_oid.strip(".")
        prefix = base + "."
        out: dict[str, Any] = {}
        current = base
        for _ in range(max_oids):
            oid, value = self.getnext(current)
            if value == "endOfMibView" or not oid.startswith(prefix) or oid == current:
                return out
            if value not in ("noSuchObject", "noSuchInstance"):
                out[oid[len(prefix) :]] = value
            current = oid
        raise SnmpError(f"{base_oid} walk exceeded {max_oids} OIDs; the table would be truncated")

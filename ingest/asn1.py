"""Minimal ASN.1 BER codec for the SNMPv3 message subset (RFC 3416 / 3412).

Pure-stdlib encoder/decoder covering exactly the types NetForge needs to poll a
device: INTEGER, OCTET STRING, NULL, OBJECT IDENTIFIER, SEQUENCE, the SNMP
application types (IpAddress, Counter32/64, Gauge32, TimeTicks, Opaque), the
exception types (noSuchObject, noSuchInstance, endOfMibView), and the request /
response PDUs. This avoids bundling a third-party ASN.1 stack.
"""

from __future__ import annotations

from typing import Any

# Universal + SNMP tags.
INTEGER = 0x02
OCTET_STRING = 0x04
NULL = 0x05
OBJECT_IDENTIFIER = 0x06
SEQUENCE = 0x30

IP_ADDRESS = 0x40
COUNTER32 = 0x41
GAUGE32 = 0x42
TIME_TICKS = 0x43
OPAQUE = 0x44
COUNTER64 = 0x46

NO_SUCH_OBJECT = 0x80
NO_SUCH_INSTANCE = 0x81
END_OF_MIB_VIEW = 0x82

GET_REQUEST = 0xA0
GET_NEXT_REQUEST = 0xA1
GET_RESPONSE = 0xA2
SET_REQUEST = 0xA3

_EXCEPTION_TAGS = {NO_SUCH_OBJECT, NO_SUCH_INSTANCE, END_OF_MIB_VIEW}


class BERError(ValueError):
    """Raised when a buffer cannot be decoded as expected."""


def encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(body)) + body


def _int_body(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    if value > 0:
        body = value.to_bytes((value.bit_length() + 7) // 8, "big")
        if body[0] & 0x80:
            body = b"\x00" + body
        return body
    length = (value.bit_length() + 8) // 8
    return value.to_bytes(length, "big", signed=True)


def enc_int(value: int) -> bytes:
    return tlv(INTEGER, _int_body(value))


def enc_oct(value: bytes) -> bytes:
    return tlv(OCTET_STRING, value)


def enc_null() -> bytes:
    return tlv(NULL, b"")


def _base128(value: int) -> list[int]:
    if value == 0:
        return [0]
    out: list[int] = []
    while value:
        out.append(value & 0x7F)
        value >>= 7
    out.reverse()
    for i in range(len(out) - 1):
        out[i] |= 0x80
    return out


def enc_oid(oid: str) -> bytes:
    parts = [int(p) for p in str(oid).strip(".").split(".") if p != ""]
    if len(parts) < 2:
        raise BERError(f"OID needs at least two arcs: {oid}")
    body = bytearray(_base128(40 * parts[0] + parts[1]))
    for p in parts[2:]:
        body.extend(_base128(p))
    return tlv(OBJECT_IDENTIFIER, bytes(body))


def enc_seq(items: list[bytes]) -> bytes:
    return tlv(SEQUENCE, b"".join(items))


def enc_request_pdu(tag: int, request_id: int, oids: list[str]) -> bytes:
    """Encode a GET/GETNEXT PDU (all varbind values are NULL)."""
    varbinds = enc_seq([enc_seq([enc_oid(o), enc_null()]) for o in oids])
    return tlv(tag, enc_int(request_id) + enc_int(0) + enc_int(0) + varbinds)


def decode(buf: bytes, offset: int = 0) -> tuple[int, bytes, int]:
    """Decode one TLV, returning ``(tag, value_bytes, next_offset)``."""
    if offset >= len(buf):
        raise BERError("unexpected end of buffer")
    tag = buf[offset]
    offset += 1
    length = buf[offset]
    offset += 1
    if length & 0x80:
        n = length & 0x7F
        if n == 0:
            raise BERError("indefinite length not supported")
        length = int.from_bytes(buf[offset : offset + n], "big")
        offset += n
    end = offset + length
    if end > len(buf):
        raise BERError("truncated TLV value")
    return tag, buf[offset:end], end


def decode_children(value: bytes) -> list[tuple[int, bytes]]:
    out: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(value):
        tag, val, offset = decode(value, offset)
        out.append((tag, val))
    return out


def parse_int(value: bytes) -> int:
    if not value:
        return 0
    n = int.from_bytes(value, "big", signed=False)
    if value[0] & 0x80:
        n -= 1 << (8 * len(value))
    return n


def parse_uint(value: bytes) -> int:
    return int.from_bytes(value, "big") if value else 0


def parse_oid(value: bytes) -> str:
    groups: list[int] = []
    acc = 0
    for byte in value:
        acc = (acc << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            groups.append(acc)
            acc = 0
    if not groups:
        return ""
    first = groups[0]
    a = min(first // 40, 2)
    parts = [a, first - 40 * a, *groups[1:]]
    return ".".join(str(p) for p in parts)


def decode_value(tag: int, value: bytes) -> Any:
    """Decode a varbind value TLV into a Python value."""
    if tag in _EXCEPTION_TAGS:
        return {
            NO_SUCH_OBJECT: "noSuchObject",
            NO_SUCH_INSTANCE: "noSuchInstance",
            END_OF_MIB_VIEW: "endOfMibView",
        }[tag]
    if tag in (INTEGER, COUNTER32, GAUGE32, TIME_TICKS, COUNTER64):
        return parse_int(value) if tag == INTEGER else parse_uint(value)
    if tag == OBJECT_IDENTIFIER:
        return parse_oid(value)
    if tag == IP_ADDRESS:
        return ".".join(str(b) for b in value)
    if tag in (OCTET_STRING, OPAQUE):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if tag == NULL:
        return None
    # Unknown application/context type: surface raw bytes as hex.
    return value.hex()

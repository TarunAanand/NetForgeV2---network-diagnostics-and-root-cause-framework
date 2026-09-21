"""Tests for the self-contained SNMPv3 engine, BER codec, and AES core.

Covers: FIPS-197 AES vectors + CFB round-trip, BER integer/OID/long-length
round-trips, RFC 3414 password-to-key, the auth-parameters offset, and full
SNMPv3 message encode/decode across all three USM security levels. A fake UDP
device (built on the same codec) independently verifies the request's auth digest
and encrypts/decrypts the scoped PDU, so ``SnmpV3Engine.get``/``walk`` and the
``LiveSnmpV3Transport`` wiring are exercised end to end without a live device.
"""

import hmac

import pytest

from ingest import asn1
from ingest.aes import aes128_cfb_decrypt, aes128_cfb_encrypt, aes128_encrypt_block
from ingest.snmp_counters import (
    OID_IF_DESCR,
    LiveSnmpV3Transport,
    SecurityLevel,
    SnmpV3Credentials,
)
from ingest.snmp_engine import (
    AUTH_NO_PRIV,
    AUTH_PRIV,
    FLAG_AUTH,
    FLAG_PRIV,
    FLAG_REPORTABLE,
    NO_AUTH_NO_PRIV,
    SYS_DESCR,
    SnmpError,
    SnmpV3Engine,
    auth_params_offset,
    decode_message,
    encode_message,
    encode_scoped_pdu,
    password_to_key,
)

ENGINE_ID = bytes.fromhex("80001f8880e4aabbccdd")
AUTH_PASS = "authpass123"
PRIV_PASS = "privpass123"
REPORT_PDU = 0xA8
USM_STATS_UNKNOWN_ENGINE_IDS = "1.3.6.1.6.3.15.1.1.3.0"


def _counter(tag: int, value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    return asn1.tlv(tag, body)


MIB = {
    "1.3.6.1.2.1.1.1.0": asn1.enc_oct(b"NetForge fake device"),  # sysDescr
    "1.3.6.1.2.1.1.5.0": asn1.enc_oct(b"fake-switch"),          # sysName
    "1.3.6.1.2.1.2.2.1.2.1": asn1.enc_oct(b"eth0"),             # ifDescr.1
    "1.3.6.1.2.1.2.2.1.2.2": asn1.enc_oct(b"eth1"),             # ifDescr.2
    "1.3.6.1.2.1.31.1.1.1.6.1": _counter(asn1.COUNTER64, 1000),  # ifHCInOctets.1
    "1.3.6.1.2.1.31.1.1.1.6.2": _counter(asn1.COUNTER64, 2000),  # ifHCInOctets.2
}


def _oid_tuple(oid: str) -> tuple:
    return tuple(int(x) for x in oid.strip(".").split("."))


def _next_oid(oid: str):
    t = _oid_tuple(oid)
    candidates = [k for k in MIB if _oid_tuple(k) > t]
    return min(candidates, key=_oid_tuple) if candidates else None


def _response_pdu(request_id: int, varbinds: list) -> bytes:
    vblist = asn1.enc_seq([asn1.enc_seq([asn1.enc_oid(o), v]) for (o, v) in varbinds])
    return asn1.tlv(
        asn1.GET_RESPONSE, asn1.enc_int(request_id) + asn1.enc_int(0) + asn1.enc_int(0) + vblist
    )


class FakeAgent:
    """A minimal SNMPv3 responder that verifies auth and applies AES privacy."""

    def __init__(self, level: str, auth_pass: str = AUTH_PASS, priv_pass: str = PRIV_PASS):
        self.level = level
        self.auth_hash = "sha1"
        self.engine_id = ENGINE_ID
        self.boots = 3
        self.engine_time = 1234
        self.auth_key = password_to_key(auth_pass, ENGINE_ID, self.auth_hash) if level != NO_AUTH_NO_PRIV else None
        self.priv_key = (
            password_to_key(priv_pass, ENGINE_ID, self.auth_hash)[:16] if level == AUTH_PRIV else None
        )

    def _report(self, parsed) -> bytes:
        varbind = asn1.enc_seq(
            [asn1.enc_oid(USM_STATS_UNKNOWN_ENGINE_IDS), _counter(asn1.COUNTER32, 1)]
        )
        pdu = asn1.tlv(
            REPORT_PDU,
            asn1.enc_int(parsed.request_id) + asn1.enc_int(0) + asn1.enc_int(0) + asn1.enc_seq([varbind]),
        )
        scoped = encode_scoped_pdu(self.engine_id, b"", pdu)
        return encode_message(
            msg_id=parsed.msg_id, engine_id=self.engine_id, boots=self.boots, engine_time=self.engine_time,
            username=parsed.username, flags=FLAG_REPORTABLE, scoped_pdu=scoped,
        )

    def respond(self, data: bytes) -> bytes:
        parsed = decode_message(
            data, priv_key=self.priv_key, auth_key=self.auth_key,
            auth_hash=self.auth_hash, verify_auth=bool(self.auth_key),
        )
        if parsed.engine_id != self.engine_id:
            return self._report(parsed)

        varbinds = []
        for oid, _value in parsed.varbinds:
            if parsed.pdu_tag == asn1.GET_REQUEST:
                varbinds.append((oid, MIB.get(oid, asn1.tlv(asn1.NO_SUCH_OBJECT, b""))))
            else:  # GETNEXT
                nxt = _next_oid(oid)
                if nxt is None:
                    varbinds.append((oid, asn1.tlv(asn1.END_OF_MIB_VIEW, b"")))
                else:
                    varbinds.append((nxt, MIB[nxt]))

        scoped = encode_scoped_pdu(
            parsed.context_engine_id or self.engine_id, parsed.context_name,
            _response_pdu(parsed.request_id, varbinds),
        )
        return encode_message(
            msg_id=parsed.msg_id, engine_id=self.engine_id, boots=self.boots, engine_time=self.engine_time,
            username=parsed.username, flags=parsed.flags, scoped_pdu=scoped,
            auth_key=self.auth_key, auth_hash=self.auth_hash, priv_key=self.priv_key,
        )


class FakeSocket:
    def __init__(self, agent: FakeAgent):
        self.agent = agent
        self._pending = None

    def settimeout(self, _t):
        return None

    def sendto(self, data, _addr):
        self._pending = self.agent.respond(bytes(data))
        return len(data)

    def recvfrom(self, _n):
        if self._pending is None:
            raise OSError("recvfrom before sendto")
        payload, self._pending = self._pending, None
        return payload, ("127.0.0.1", 161)

    def close(self):
        return None


def _engine(level: str, agent: FakeAgent, **kwargs) -> SnmpV3Engine:
    return SnmpV3Engine(
        host="10.0.0.9", port=161, username="netforge", security_level=level,
        auth_protocol="sha", auth_key=AUTH_PASS if level != NO_AUTH_NO_PRIV else "",
        priv_protocol="aes", priv_key=PRIV_PASS if level == AUTH_PRIV else "",
        timeout=2.0, socket_factory=lambda: FakeSocket(agent), **kwargs,
    )


# --- AES core ---


def test_aes128_known_answer_vectors():
    key = bytes(range(16))
    pt = bytes([0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    assert aes128_encrypt_block(key, pt).hex() == "69c4e0d86a7b0430d8cdb78070b4c55a"  # FIPS-197 B.1
    assert aes128_encrypt_block(bytes(16), bytes(16)).hex() == "66e94bd4ef8a2c3b884cfa59ca342b2e"  # C.1


def test_aes_cfb_roundtrip_multiblock():
    key = bytes(range(16))
    iv = bytes(reversed(range(16)))
    data = b"the quick brown fox jumps over the lazy dog" * 3  # > 16 bytes, non-multiple
    cipher = aes128_cfb_encrypt(key, iv, data)
    assert cipher != data
    assert aes128_cfb_decrypt(key, iv, cipher) == data


# --- BER codec ---


@pytest.mark.parametrize("value", [0, 1, 127, 128, 255, 256, 65535, -1, -128, -129, 2147483647])
def test_ber_integer_roundtrip(value):
    _tag, body, _ = asn1.decode(asn1.enc_int(value))
    assert asn1.parse_int(body) == value


@pytest.mark.parametrize(
    "oid",
    ["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.31.1.1.1.6.10", "1.3.6.1.2.1.17.4.3.1.1.0.17.34.51.68.85"],
)
def test_ber_oid_roundtrip(oid):
    _tag, body, _ = asn1.decode(asn1.enc_oid(oid))
    assert asn1.parse_oid(body) == oid


def test_ber_long_form_length_roundtrip():
    assert asn1.encode_length(200) == b"\x81\xc8"
    payload = bytes(range(256)) * 2  # 512 bytes -> long-form length
    _tag, body, _ = asn1.decode(asn1.enc_oct(payload))
    assert body == payload


def test_decode_value_types():
    assert asn1.decode_value(asn1.OCTET_STRING, b"eth0") == "eth0"
    assert asn1.decode_value(asn1.COUNTER64, (1000).to_bytes(8, "big")) == 1000
    assert asn1.decode_value(asn1.IP_ADDRESS, b"\x0a\x00\x00\x01") == "10.0.0.1"
    assert asn1.decode_value(asn1.END_OF_MIB_VIEW, b"") == "endOfMibView"


# --- USM key + message framing ---


def test_password_to_key_is_deterministic_and_hash_sized():
    k1 = password_to_key(AUTH_PASS, ENGINE_ID, "sha1")
    k2 = password_to_key(AUTH_PASS, ENGINE_ID, "sha1")
    assert k1 == k2 and len(k1) == 20
    assert len(password_to_key(AUTH_PASS, ENGINE_ID, "sha256")) == 32
    # A different engineID localizes to a different key.
    assert password_to_key(AUTH_PASS, b"\x01\x02\x03", "sha1") != k1


def test_message_roundtrip_all_levels_and_tamper_detection():
    for level, flags in (
        (NO_AUTH_NO_PRIV, FLAG_REPORTABLE),
        (AUTH_NO_PRIV, FLAG_REPORTABLE | FLAG_AUTH),
        (AUTH_PRIV, FLAG_REPORTABLE | FLAG_AUTH | FLAG_PRIV),
    ):
        auth_key = password_to_key(AUTH_PASS, ENGINE_ID, "sha1") if level != NO_AUTH_NO_PRIV else None
        priv_key = password_to_key(PRIV_PASS, ENGINE_ID, "sha1")[:16] if level == AUTH_PRIV else None
        scoped = encode_scoped_pdu(ENGINE_ID, "", asn1.enc_request_pdu(asn1.GET_REQUEST, 5, [SYS_DESCR]))
        msg = encode_message(
            msg_id=11, engine_id=ENGINE_ID, boots=2, engine_time=50, username="netforge",
            flags=flags, scoped_pdu=scoped, auth_key=auth_key, auth_hash="sha1", priv_key=priv_key,
        )
        parsed = decode_message(msg, priv_key=priv_key, auth_key=auth_key, auth_hash="sha1", verify_auth=bool(auth_key))
        assert parsed.flags == flags and parsed.varbinds[0][0] == SYS_DESCR

        if auth_key is not None:
            tampered = bytearray(msg)
            tampered[-4] ^= 0xFF
            with pytest.raises(SnmpError):
                decode_message(bytes(tampered), priv_key=priv_key, auth_key=auth_key, auth_hash="sha1", verify_auth=True)


def test_auth_params_offset_targets_the_zeroed_field():
    auth_key = password_to_key(AUTH_PASS, ENGINE_ID, "sha1")
    scoped = encode_scoped_pdu(ENGINE_ID, "", asn1.enc_request_pdu(asn1.GET_REQUEST, 1, [SYS_DESCR]))
    unsigned = encode_message(
        msg_id=3, engine_id=ENGINE_ID, boots=1, engine_time=1, username="netforge",
        flags=FLAG_REPORTABLE, scoped_pdu=scoped,  # no auth -> 12 zero bytes present only if flag set
    )
    # Without the auth flag there is no 12-byte field; build one with auth to inspect.
    signed = encode_message(
        msg_id=3, engine_id=ENGINE_ID, boots=1, engine_time=1, username="netforge",
        flags=FLAG_REPORTABLE | FLAG_AUTH, scoped_pdu=scoped, auth_key=auth_key, auth_hash="sha1",
    )
    offset = auth_params_offset(signed)
    digest = hmac.new(auth_key, signed[:offset] + b"\x00" * 12 + signed[offset + 12 :], "sha1").digest()[:12]
    assert signed[offset : offset + 12] == digest
    assert len(unsigned) < len(signed)


# --- Engine over the fake UDP device ---


@pytest.mark.parametrize("level", [NO_AUTH_NO_PRIV, AUTH_NO_PRIV, AUTH_PRIV])
def test_engine_get_and_walk(level):
    agent = FakeAgent(level)
    engine = _engine(level, agent)
    assert engine.get(SYS_DESCR) == "NetForge fake device"
    assert engine.walk(OID_IF_DESCR) == {"1": "eth0", "2": "eth1"}
    assert engine._engine_id == ENGINE_ID  # discovery populated the authoritative engineID


def test_engine_walk_counter64_values():
    agent = FakeAgent(AUTH_PRIV)
    engine = _engine(AUTH_PRIV, agent)
    assert engine.walk("1.3.6.1.2.1.31.1.1.1.6") == {"1": 1000, "2": 2000}


def test_engine_get_missing_oid_returns_none():
    agent = FakeAgent(AUTH_NO_PRIV)
    engine = _engine(AUTH_NO_PRIV, agent)
    assert engine.get("1.3.6.1.2.1.99.99.0") is None


def test_engine_auth_failure_raises():
    # Device localizes keys with a different passphrase -> digest mismatch.
    agent = FakeAgent(AUTH_NO_PRIV, auth_pass="wrongpass999")
    engine = _engine(AUTH_NO_PRIV, agent)
    with pytest.raises(SnmpError):
        engine.get(SYS_DESCR)


# --- LiveSnmpV3Transport wiring ---


def test_live_transport_walk_and_get():
    agent = FakeAgent(AUTH_PRIV)
    creds = SnmpV3Credentials(
        host="10.0.0.9", username="netforge", security_level=SecurityLevel.AUTH_PRIV,
        auth_protocol="sha", auth_key=AUTH_PASS, priv_protocol="aes", priv_key=PRIV_PASS,
    )
    transport = LiveSnmpV3Transport(creds, timeout=2.0, socket_factory=lambda: FakeSocket(agent))
    assert transport.walk(OID_IF_DESCR) == {"1": "eth0", "2": "eth1"}
    assert transport.get(SYS_DESCR) == "NetForge fake device"


def test_live_transport_rejects_des_privacy():
    creds = SnmpV3Credentials(
        host="10.0.0.9", username="netforge", security_level=SecurityLevel.AUTH_PRIV,
        auth_protocol="sha", auth_key=AUTH_PASS, priv_protocol="des", priv_key=PRIV_PASS,
    )
    with pytest.raises(SnmpError):
        LiveSnmpV3Transport(creds, timeout=2.0, socket_factory=lambda: FakeSocket(FakeAgent(AUTH_PRIV)))

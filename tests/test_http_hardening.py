"""Transport hardening: bounded request bodies and TLS wiring."""

import json
import ssl
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from controller.http_server import make_handler
from controller.service import ControllerService
from controller.store import ControllerStore
from core.http_utils import (
    MAX_BODY_BYTES,
    BodyTooLarge,
    MalformedBody,
    build_ssl_context,
    read_json_body,
)


class _FakeHandler:
    def __init__(self, headers: dict[str, str], body: bytes = b""):
        self.headers = headers

        class _Reader:
            def __init__(self, payload: bytes):
                self.payload = payload

            def read(self, size: int) -> bytes:
                return self.payload[:size]

        self.rfile = _Reader(body)


class UnusedDispatcher:
    def fanout(self, agents, request):
        raise AssertionError("test should not dispatch a job")


def test_read_json_body_rejects_oversized_content_length():
    handler = _FakeHandler({"Content-Length": str(MAX_BODY_BYTES + 1)})
    with pytest.raises(BodyTooLarge):
        read_json_body(handler)


def test_read_json_body_rejects_malformed_length_and_payload():
    with pytest.raises(MalformedBody):
        read_json_body(_FakeHandler({"Content-Length": "not-a-number"}))
    with pytest.raises(MalformedBody):
        read_json_body(_FakeHandler({"Content-Length": "-1"}))
    with pytest.raises(MalformedBody):
        read_json_body(_FakeHandler({"Content-Length": "2"}, b"{{"))
    with pytest.raises(MalformedBody):
        read_json_body(_FakeHandler({"Content-Length": "2"}, b"[]"))
    with pytest.raises(MalformedBody):
        read_json_body(_FakeHandler({"Content-Length": "4"}, b'"\xff\xfe"'))


def test_read_json_body_defaults_to_empty_object():
    assert read_json_body(_FakeHandler({"Content-Length": "0"})) == {}


def test_controller_rejects_oversized_body(tmp_path):
    service = ControllerService(ControllerStore(tmp_path / "controller.db"), UnusedDispatcher())
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, "controller-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        payload = json.dumps({"agent_id": "a" * (MAX_BODY_BYTES + 16), "url": "http://x"}).encode()
        request = urllib.request.Request(
            base_url + "/v1/agents",
            data=payload,
            method="POST",
            headers={"Authorization": "Bearer controller-secret", "Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=5)
        assert exc.value.code == 413
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_build_ssl_context_requires_tls12(tmp_path):
    pytest.importorskip("cryptography")
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = tmp_path / "server.pem"
    pem.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    context = build_ssl_context(str(pem))
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2

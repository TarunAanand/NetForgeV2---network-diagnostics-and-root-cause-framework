"""Shared transport helpers for the stdlib HTTP servers (agent and controller).

Centralizes the two things both servers must get right: bounding request bodies
so a single ``Content-Length`` header cannot exhaust memory, and wrapping the
listening socket in TLS so bearer tokens are not transmitted in cleartext.
"""

from __future__ import annotations

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY_BYTES = 1 << 20


class BodyTooLarge(ValueError):
    """Raised when Content-Length exceeds :data:`MAX_BODY_BYTES`."""


class MalformedBody(ValueError):
    """Raised for an unparsable Content-Length or a non-JSON body."""


def read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int = MAX_BODY_BYTES) -> dict:
    """Read and decode a bounded JSON request body."""
    raw_length = handler.headers.get("Content-Length", "0")
    try:
        size = int(raw_length)
    except (TypeError, ValueError):
        raise MalformedBody("Content-Length must be an integer") from None
    if size < 0:
        raise MalformedBody("Content-Length must not be negative")
    if size > max_bytes:
        raise BodyTooLarge(f"request body exceeds {max_bytes} bytes")
    body = handler.rfile.read(size) or b"{}"
    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise MalformedBody("request body must be JSON") from None
    if not isinstance(decoded, dict):
        raise MalformedBody("request body must be a JSON object")
    return decoded


def build_ssl_context(certfile: str, keyfile: str | None = None) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def serve_http(
    handler_class: type[BaseHTTPRequestHandler],
    host: str,
    port: int,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> None:
    """Serve forever, over TLS when ``certfile`` is supplied."""
    server = ThreadingHTTPServer((host, port), handler_class)
    if certfile:
        server.socket = build_ssl_context(certfile, keyfile).wrap_socket(server.socket, server_side=True)
    server.serve_forever()

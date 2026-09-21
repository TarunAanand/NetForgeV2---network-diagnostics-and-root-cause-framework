"""Remote agent contract for distributed mesh observations (stub)."""

from __future__ import annotations

from typing import Any

from core.result import DiagnosticResult


def agent_probe_request(
    agent_url: str,
    probe: str,
    target: str,
    options: dict[str, Any] | None = None,
) -> list[DiagnosticResult]:
    """
    HTTP contract placeholder:
      POST {agent_url}/v1/probe
      body: {"probe": "...", "target": "...", "options": {...}}
      response: {"results": [DiagnosticResult, ...]}

    Not implemented in this phase — local mesh runner covers single-vantage use.
    """
    raise NotImplementedError(
        f"Remote agent API at {agent_url} is not implemented yet "
        f"(probe={probe}, target={target}, options={options or {}})."
    )


AGENT_API_SCHEMA = {
    "endpoint": "/v1/probe",
    "method": "POST",
    "request": {
        "probe": "string (ping|traceroute|connectivity)",
        "target": "string",
        "options": "object",
    },
    "response": {"results": "DiagnosticResult[]"},
}

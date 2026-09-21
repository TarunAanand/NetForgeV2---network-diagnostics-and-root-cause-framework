"""IPFIX / NetFlow ingest stub."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ingest.sflow import load_sflow_records


def load_ipfix_records(path: str | Path) -> list[dict[str, Any]]:
    """
    Offline IPFIX/NetFlow records use the same JSON schema as sFlow replay for MVP.
    """
    return load_sflow_records(path)


def start_ipfix_collector(bind: str = "0.0.0.0", port: int = 4739) -> None:
    raise NotImplementedError(
        f"Live IPFIX collector on {bind}:{port} is not implemented yet; "
        "use load_ipfix_records() with a JSON export."
    )

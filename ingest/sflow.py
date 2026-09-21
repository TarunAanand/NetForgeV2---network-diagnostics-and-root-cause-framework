"""sFlow ingest — file replay first; live UDP collector stub."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_sflow_records(path: str | Path) -> list[dict[str, Any]]:
    """
    Load flow records from a JSON lines or JSON array file for offline analysis.
    Expected keys per record: src, dst, bytes, packets, proto (optional).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("sFlow JSON root must be a list")
        return data
    records = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def start_sflow_collector(bind: str = "0.0.0.0", port: int = 6343) -> None:
    """
    Placeholder for a live UDP sFlow collector.
    Phase 6 delivers offline replay; live socket collection is intentionally stubbed.
    """
    raise NotImplementedError(
        f"Live sFlow collector on {bind}:{port} is not implemented yet; "
        "use load_sflow_records() with a JSON export for offline analysis."
    )

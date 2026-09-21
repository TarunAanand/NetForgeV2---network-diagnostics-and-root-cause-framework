"""SQLite-backed probe history for path fingerprints and baselines."""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_DB_PATH = Path(os.environ.get("NETFORGE_HISTORY_DB", ".netforge_history.db"))
BUSY_TIMEOUT_SECONDS = 5.0


class HistoryStore:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_SECONDS * 1000)}")
        return conn

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection that is always closed, not just committed."""
        with closing(self._connect()) as conn, conn:
            yield conn

    def _ensure_schema(self) -> None:
        with self._session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    key TEXT NOT NULL,
                    fingerprint TEXT,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_domain_key "
                "ON snapshots(domain, key, created_at DESC)"
            )
            conn.commit()

    def save_snapshot(
        self,
        domain: str,
        key: str,
        payload: dict,
        fingerprint: str | None = None,
    ) -> None:
        with self._session() as conn:
            conn.execute(
                "INSERT INTO snapshots(domain, key, fingerprint, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (domain, key, fingerprint, json.dumps(payload), time.time()),
            )
            conn.commit()

    def latest_snapshot(self, domain: str, key: str) -> dict | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT fingerprint, payload, created_at FROM snapshots "
                "WHERE domain = ? AND key = ? ORDER BY created_at DESC LIMIT 1",
                (domain, key),
            ).fetchone()
        if not row:
            return None
        return {
            "fingerprint": row["fingerprint"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    def previous_snapshot(self, domain: str, key: str) -> dict | None:
        """Return the second-most-recent snapshot (previous baseline)."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT fingerprint, payload, created_at FROM snapshots "
                "WHERE domain = ? AND key = ? ORDER BY created_at DESC LIMIT 2",
                (domain, key),
            ).fetchall()
        if len(rows) < 2:
            return None
        row = rows[1]
        return {
            "fingerprint": row["fingerprint"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    def rolling_baseline(
        self,
        domain: str,
        key: str,
        metric: str,
        limit: int = 20,
    ) -> float | None:
        """Mean of a numeric metric across recent snapshots."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT payload FROM snapshots "
                "WHERE domain = ? AND key = ? ORDER BY created_at DESC LIMIT ?",
                (domain, key, limit),
            ).fetchall()
        values: list[float] = []
        for row in rows:
            payload = json.loads(row["payload"])
            val = payload.get(metric)
            if isinstance(val, (int, float)):
                values.append(float(val))
        if not values:
            return None
        return statistics.median(values)

    def rolling_stats(
        self,
        domain: str,
        key: str,
        metric: str,
        limit: int = 20,
    ) -> dict[str, float | int] | None:
        """Return robust baseline statistics for recent numeric samples."""
        with self._session() as conn:
            rows = conn.execute(
                "SELECT payload FROM snapshots "
                "WHERE domain = ? AND key = ? ORDER BY created_at DESC LIMIT ?",
                (domain, key, limit),
            ).fetchall()
        values: list[float] = []
        for row in rows:
            payload = json.loads(row["payload"])
            value = payload.get(metric)
            if isinstance(value, (int, float)):
                values.append(float(value))
        if not values:
            return None

        median = statistics.median(values)
        deviations = [abs(value - median) for value in values]
        mad = statistics.median(deviations)
        return {
            "sample_count": len(values),
            "median": median,
            "p95": (
                max(values)
                if len(values) < 2
                else statistics.quantiles(values, n=20, method="inclusive")[-1]
            ),
            "mad": mad,
        }

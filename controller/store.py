"""SQLite persistence for controller agents and fan-out jobs."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from controller.models import FanoutJob, RegisteredAgent
from controller.topology import NetworkTopology, ServiceEndpoint
from controller.monitoring import (
    Alert,
    AlertRule,
    AlertState,
    Incident,
    IncidentState,
    ScheduleEntry,
)
from controller.correlation import HostPortBinding
from analysis.multivantage import MultiVantageReport


class ControllerStore:
    def __init__(self, db_path: str | Path = ".netforge_controller.db"):
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY, url TEXT NOT NULL, tags TEXT NOT NULL,
                    enabled INTEGER NOT NULL, last_seen_at REAL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
                    completed_at REAL, status TEXT NOT NULL, payload TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS topology (
                    name TEXT PRIMARY KEY, schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS services (
                    service_id TEXT PRIMARY KEY, node_id TEXT,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS diagnoses (
                    diagnosis_id TEXT PRIMARY KEY, target TEXT NOT NULL,
                    service_id TEXT, localization TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, payload TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schedules (
                    schedule_id TEXT PRIMARY KEY, service_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL, next_run_at REAL,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS alert_rules (
                    rule_id TEXT PRIMARY KEY, service_id TEXT,
                    severity TEXT NOT NULL, enabled INTEGER NOT NULL,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY, service_id TEXT, rule_id TEXT NOT NULL,
                    state TEXT NOT NULL, severity TEXT NOT NULL, diagnosis_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY, service_id TEXT,
                    state TEXT NOT NULL, severity TEXT NOT NULL,
                    opened_at REAL NOT NULL, updated_at REAL NOT NULL, payload TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS bindings (
                    binding_id TEXT PRIMARY KEY, topology TEXT NOT NULL,
                    host_node_id TEXT NOT NULL, switch_node_id TEXT NOT NULL,
                    method TEXT NOT NULL, confidence REAL NOT NULL,
                    payload TEXT NOT NULL, updated_at REAL NOT NULL
                )"""
            )

    def upsert_agent(self, agent: RegisteredAgent) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO agents(agent_id, url, tags, enabled, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET url=excluded.url, tags=excluded.tags,
                enabled=excluded.enabled, last_seen_at=excluded.last_seen_at""",
                (agent.agent_id, str(agent.url), json.dumps(agent.topology_tags), int(agent.enabled), agent.last_seen_at),
            )

    def get_agent(self, agent_id: str) -> RegisteredAgent | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
        if not row:
            return None
        return RegisteredAgent(agent_id=row["agent_id"], url=row["url"], topology_tags=json.loads(row["tags"]), enabled=bool(row["enabled"]), last_seen_at=row["last_seen_at"])

    def list_agents(self) -> list[RegisteredAgent]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()
        return [RegisteredAgent(agent_id=row["agent_id"], url=row["url"], topology_tags=json.loads(row["tags"]), enabled=bool(row["enabled"]), last_seen_at=row["last_seen_at"]) for row in rows]

    def mark_seen(self, agent_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE agents SET last_seen_at = ? WHERE agent_id = ?", (time.time(), agent_id))

    def save_job(self, job: FanoutJob) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO jobs(job_id, created_at, completed_at, status, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET completed_at=excluded.completed_at,
                status=excluded.status, payload=excluded.payload""",
                (job.job_id, job.created_at, job.completed_at, job.status, job.model_dump_json()),
            )

    def get_job(self, job_id: str) -> FanoutJob | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return FanoutJob.model_validate_json(row["payload"]) if row else None

    # --- Topology persistence (M3) ---
    def save_topology(self, topology: NetworkTopology) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO topology(name, schema_version, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET schema_version=excluded.schema_version,
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (topology.name, topology.schema_version, topology.model_dump_json(), time.time()),
            )

    def get_topology(self, name: str) -> NetworkTopology | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM topology WHERE name = ?", (name,)).fetchone()
        return NetworkTopology.model_validate_json(row["payload"]) if row else None

    def list_topology_names(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT name FROM topology ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    # --- Service inventory persistence (M3) ---
    def upsert_service(self, service: ServiceEndpoint) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO services(service_id, node_id, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET node_id=excluded.node_id,
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (service.service_id, service.node_id, service.model_dump_json(), time.time()),
            )

    def get_service(self, service_id: str) -> ServiceEndpoint | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM services WHERE service_id = ?", (service_id,)).fetchone()
        return ServiceEndpoint.model_validate_json(row["payload"]) if row else None

    def list_services(self) -> list[ServiceEndpoint]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM services ORDER BY service_id").fetchall()
        return [ServiceEndpoint.model_validate_json(row["payload"]) for row in rows]

    def delete_service(self, service_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM services WHERE service_id = ?", (service_id,))
        return cursor.rowcount > 0

    # --- Multi-vantage diagnosis persistence (M4) ---
    def save_diagnosis(self, report: MultiVantageReport) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO diagnoses(diagnosis_id, target, service_id, localization,
                    status, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(diagnosis_id) DO UPDATE SET localization=excluded.localization,
                status=excluded.status, payload=excluded.payload""",
                (
                    report.diagnosis_id,
                    report.target,
                    report.service_id,
                    report.localization.value,
                    report.status.value,
                    time.time(),
                    report.model_dump_json(),
                ),
            )

    def get_diagnosis(self, diagnosis_id: str) -> MultiVantageReport | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM diagnoses WHERE diagnosis_id = ?", (diagnosis_id,)
            ).fetchone()
        return MultiVantageReport.model_validate_json(row["payload"]) if row else None

    def list_diagnoses(self, limit: int = 50) -> list[MultiVantageReport]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM diagnoses ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [MultiVantageReport.model_validate_json(row["payload"]) for row in rows]

    # --- Schedule persistence (M5) ---
    def upsert_schedule(self, schedule: ScheduleEntry) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO schedules(schedule_id, service_id, enabled, next_run_at,
                    payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET service_id=excluded.service_id,
                enabled=excluded.enabled, next_run_at=excluded.next_run_at,
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    schedule.schedule_id,
                    schedule.service_id,
                    int(schedule.enabled),
                    schedule.next_run_at,
                    schedule.model_dump_json(),
                    time.time(),
                ),
            )

    def get_schedule(self, schedule_id: str) -> ScheduleEntry | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM schedules WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
        return ScheduleEntry.model_validate_json(row["payload"]) if row else None

    def list_schedules(self, service_id: str | None = None) -> list[ScheduleEntry]:
        with self._connect() as connection:
            if service_id is None:
                rows = connection.execute(
                    "SELECT payload FROM schedules ORDER BY service_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM schedules WHERE service_id = ? ORDER BY service_id",
                    (service_id,),
                ).fetchall()
        return [ScheduleEntry.model_validate_json(row["payload"]) for row in rows]

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,))
        return cursor.rowcount > 0

    # --- Alert rule persistence (M5) ---
    def upsert_alert_rule(self, rule: AlertRule) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO alert_rules(rule_id, service_id, severity, enabled, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET service_id=excluded.service_id,
                severity=excluded.severity, enabled=excluded.enabled,
                payload=excluded.payload, updated_at=excluded.updated_at""",
                (
                    rule.rule_id,
                    rule.service_id,
                    rule.severity.value,
                    int(rule.enabled),
                    rule.model_dump_json(),
                    time.time(),
                ),
            )

    def get_alert_rule(self, rule_id: str) -> AlertRule | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM alert_rules WHERE rule_id = ?", (rule_id,)
            ).fetchone()
        return AlertRule.model_validate_json(row["payload"]) if row else None

    def list_alert_rules(self) -> list[AlertRule]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM alert_rules ORDER BY rule_id").fetchall()
        return [AlertRule.model_validate_json(row["payload"]) for row in rows]

    def delete_alert_rule(self, rule_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM alert_rules WHERE rule_id = ?", (rule_id,))
        return cursor.rowcount > 0

    # --- Alert persistence (M5) ---
    def save_alert(self, alert: Alert) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO alerts(alert_id, service_id, rule_id, state, severity,
                    diagnosis_id, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO UPDATE SET state=excluded.state,
                severity=excluded.severity, updated_at=excluded.updated_at,
                payload=excluded.payload""",
                (
                    alert.alert_id,
                    alert.service_id,
                    alert.rule_id,
                    alert.state.value,
                    alert.severity.value,
                    alert.diagnosis_id,
                    alert.created_at,
                    alert.updated_at,
                    alert.model_dump_json(),
                ),
            )

    def get_alert(self, alert_id: str) -> Alert | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM alerts WHERE alert_id = ?", (alert_id,)
            ).fetchone()
        return Alert.model_validate_json(row["payload"]) if row else None

    def list_alerts(
        self, service_id: str | None = None, state: AlertState | None = None, limit: int = 100
    ) -> list[Alert]:
        clauses: list[str] = []
        params: list[object] = []
        if service_id is not None:
            clauses.append("service_id = ?")
            params.append(service_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM alerts{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [Alert.model_validate_json(row["payload"]) for row in rows]

    def find_firing_alert(self, rule_id: str, service_id: str | None) -> Alert | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM alerts WHERE rule_id = ? AND service_id IS ? AND state = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (rule_id, service_id, AlertState.FIRING.value),
            ).fetchone()
        return Alert.model_validate_json(row["payload"]) if row else None

    # --- Incident persistence (M5) ---
    def save_incident(self, incident: Incident) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO incidents(incident_id, service_id, state, severity,
                    opened_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET state=excluded.state,
                severity=excluded.severity, updated_at=excluded.updated_at,
                payload=excluded.payload""",
                (
                    incident.incident_id,
                    incident.service_id,
                    incident.state.value,
                    incident.severity.value,
                    incident.opened_at,
                    incident.updated_at,
                    incident.model_dump_json(),
                ),
            )

    def get_incident(self, incident_id: str) -> Incident | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return Incident.model_validate_json(row["payload"]) if row else None

    def list_incidents(
        self, service_id: str | None = None, state: IncidentState | None = None, limit: int = 100
    ) -> list[Incident]:
        clauses: list[str] = []
        params: list[object] = []
        if service_id is not None:
            clauses.append("service_id = ?")
            params.append(service_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM incidents{where} ORDER BY opened_at DESC LIMIT ?", params
            ).fetchall()
        return [Incident.model_validate_json(row["payload"]) for row in rows]

    def find_active_incident(self, service_id: str | None) -> Incident | None:
        """Return the OPEN or ACKNOWLEDGED incident for a service, if any."""
        active = (IncidentState.OPEN.value, IncidentState.ACKNOWLEDGED.value)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM incidents WHERE service_id IS ? AND state IN (?, ?) "
                "ORDER BY opened_at DESC LIMIT 1",
                (service_id, active[0], active[1]),
            ).fetchone()
        return Incident.model_validate_json(row["payload"]) if row else None

    # --- Host-to-switch-port binding persistence (M6) ---
    def replace_bindings(self, topology: str, bindings: list[HostPortBinding]) -> None:
        """Atomically replace all stored bindings for a topology."""
        now = time.time()
        with self._connect() as connection:
            connection.execute("DELETE FROM bindings WHERE topology = ?", (topology,))
            for binding in bindings:
                binding_id = f"{topology}:{binding.host_node_id}:{binding.switch_node_id}"
                connection.execute(
                    """INSERT INTO bindings(binding_id, topology, host_node_id, switch_node_id,
                        method, confidence, payload, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(binding_id) DO UPDATE SET method=excluded.method,
                    confidence=excluded.confidence, payload=excluded.payload,
                    updated_at=excluded.updated_at""",
                    (
                        binding_id,
                        topology,
                        binding.host_node_id,
                        binding.switch_node_id,
                        binding.method.value,
                        binding.confidence,
                        binding.model_dump_json(),
                        now,
                    ),
                )

    def list_bindings(self, topology: str | None = None) -> list[HostPortBinding]:
        with self._connect() as connection:
            if topology is None:
                rows = connection.execute(
                    "SELECT payload FROM bindings ORDER BY host_node_id, switch_node_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM bindings WHERE topology = ? "
                    "ORDER BY host_node_id, switch_node_id",
                    (topology,),
                ).fetchall()
        return [HostPortBinding.model_validate_json(row["payload"]) for row in rows]

    def delete_bindings(self, topology: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM bindings WHERE topology = ?", (topology,))
        return cursor.rowcount

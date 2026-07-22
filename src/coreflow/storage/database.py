"""SQLite database initialization and connection handling."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 6


class Database:
    """Small sqlite3 wrapper for CoreFlow Studio storage."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            current_version = _current_schema_version(connection)
            if current_version is not None and current_version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than "
                    f"supported version {SCHEMA_VERSION}"
                )

            connection.execute("BEGIN")
            for statement in SCHEMA_STATEMENTS:
                connection.execute(statement)
            _ensure_columns(
                connection,
                "modbus_trial_records",
                {
                    "k_factor_parameter": "TEXT",
                    "original_k_factor": "REAL",
                },
            )
            _ensure_columns(
                connection,
                "filling_advance_profiles",
                {"retired_at": "TEXT"},
            )
            _ensure_columns(
                connection,
                "modbus_device_profiles",
                {
                    "register_map_id": "TEXT",
                    "register_map_version": "TEXT",
                },
            )
            _migrate_modbus_register_map_catalog(connection)
            _backfill_modbus_profile_devices(connection)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES (?, datetime('now'))
                """,
                (SCHEMA_VERSION,),
            )


SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        device_type TEXT NOT NULL,
        serial_number TEXT,
        model TEXT,
        firmware_version TEXT,
        hardware_version TEXT,
        protocol_address TEXT,
        connection_metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_sessions (
        run_id TEXT PRIMARY KEY,
        run_type TEXT NOT NULL,
        workflow_name TEXT NOT NULL,
        workflow_version TEXT NOT NULL,
        device_id TEXT NOT NULL,
        operator TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        configuration_snapshot_json TEXT NOT NULL DEFAULT '{}',
        software_version TEXT,
        notes TEXT,
        FOREIGN KEY(device_id) REFERENCES devices(device_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        step_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        name TEXT NOT NULL,
        step_type TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        input_configuration_json TEXT NOT NULL DEFAULT '{}',
        output_summary_json TEXT NOT NULL DEFAULT '{}',
        error_message TEXT,
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analysis_results (
        result_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT,
        result_type TEXT NOT NULL,
        algorithm_name TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,
        input_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
        configuration_snapshot_json TEXT NOT NULL DEFAULT '{}',
        summary_metrics_json TEXT NOT NULL DEFAULT '{}',
        pass_fail_decision TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id),
        FOREIGN KEY(step_id) REFERENCES workflow_steps(step_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        artifact_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        step_id TEXT,
        artifact_type TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_format TEXT NOT NULL,
        size_bytes INTEGER,
        checksum TEXT,
        created_at TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id),
        FOREIGN KEY(step_id) REFERENCES workflow_steps(step_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS audit_logs (
        audit_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        actor TEXT NOT NULL,
        action_type TEXT NOT NULL,
        workflow_state TEXT,
        device_id TEXT,
        run_id TEXT,
        target TEXT,
        previous_value_json TEXT,
        new_value_json TEXT,
        dry_run INTEGER NOT NULL DEFAULT 0,
        validation_result TEXT,
        protocol_request_ref TEXT,
        result TEXT,
        error_message TEXT,
        FOREIGN KEY(device_id) REFERENCES devices(device_id),
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS variable_samples (
        sample_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        run_id TEXT,
        step_id TEXT,
        variable_name TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        value_json TEXT NOT NULL,
        unit TEXT,
        source_channel TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(device_id) REFERENCES devices(device_id),
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id),
        FOREIGN KEY(step_id) REFERENCES workflow_steps(step_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS modbus_device_profiles (
        profile_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL UNIQUE,
        display_name TEXT,
        device_model TEXT,
        tube_model TEXT,
        transmitter_model TEXT,
        connection_settings_json TEXT NOT NULL DEFAULT '{}',
        register_map_json TEXT NOT NULL DEFAULT '{}',
        register_map_id TEXT,
        register_map_version TEXT,
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS modbus_register_maps (
        register_map_id TEXT NOT NULL,
        version TEXT NOT NULL,
        display_name TEXT NOT NULL,
        source TEXT NOT NULL,
        checksum TEXT NOT NULL,
        register_map_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(register_map_id, version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_modbus_register_maps_checksum
    ON modbus_register_maps(checksum)
    """,
    """
    CREATE TABLE IF NOT EXISTS modbus_test_sessions (
        session_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        profile_id TEXT,
        operator TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        device_metadata_json TEXT NOT NULL DEFAULT '{}',
        register_map_snapshot_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT,
        FOREIGN KEY(profile_id) REFERENCES modbus_device_profiles(profile_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS modbus_operation_attempts (
        attempt_id TEXT PRIMARY KEY,
        session_id TEXT,
        run_id TEXT,
        device_id TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        status TEXT NOT NULL,
        started_at TEXT,
        ended_at TEXT,
        operator TEXT NOT NULL,
        device_metadata_json TEXT NOT NULL DEFAULT '{}',
        register_map_snapshot_json TEXT NOT NULL DEFAULT '{}',
        raw_artifact_id TEXT,
        summary_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT,
        FOREIGN KEY(session_id) REFERENCES modbus_test_sessions(session_id),
        FOREIGN KEY(raw_artifact_id) REFERENCES artifacts(artifact_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS modbus_trial_records (
        trial_id TEXT PRIMARY KEY,
        session_id TEXT,
        attempt_id TEXT,
        run_id TEXT,
        device_id TEXT NOT NULL,
        flow_point REAL NOT NULL,
        trial_index INTEGER NOT NULL,
        trial_status TEXT NOT NULL,
        k_factor_parameter TEXT,
        original_k_factor REAL,
        mass_acc_before REAL,
        mass_acc_after REAL,
        measured_mass_delta REAL,
        standard_mass REAL,
        percent_error REAL,
        mean_flow REAL,
        instant_flow REAL,
        flow_started_at TEXT,
        flow_instant_at TEXT,
        flow_ended_at TEXT,
        raw_artifact_id TEXT,
        device_metadata_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT,
        FOREIGN KEY(session_id) REFERENCES modbus_test_sessions(session_id),
        FOREIGN KEY(attempt_id) REFERENCES modbus_operation_attempts(attempt_id),
        FOREIGN KEY(raw_artifact_id) REFERENCES artifacts(artifact_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_run_sessions_run_device
    ON run_sessions(run_id, device_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS filling_trial_records (
        trial_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        trial_index INTEGER NOT NULL,
        trial_status TEXT NOT NULL,
        mode TEXT NOT NULL,
        control_valve_label TEXT NOT NULL,
        pulse_frequency_switch_point_hz REAL NOT NULL,
        mass_per_pulse REAL NOT NULL,
        mass_unit TEXT NOT NULL,
        flow_point_g_per_s REAL NOT NULL,
        specified_mass REAL NOT NULL,
        target_mass REAL NOT NULL,
        standard_mass REAL NOT NULL,
        percent_error REAL NOT NULL,
        configuration_snapshot_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT,
        calculated_at TEXT,
        notes TEXT,
        UNIQUE(run_id, trial_index),
        FOREIGN KEY(run_id) REFERENCES run_sessions(run_id),
        FOREIGN KEY(device_id) REFERENCES devices(device_id),
        FOREIGN KEY(run_id, device_id)
            REFERENCES run_sessions(run_id, device_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_filling_trials_device_calculated
    ON filling_trial_records(device_id, calculated_at DESC, trial_id DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS filling_advance_profiles (
        profile_id TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        source_result_id TEXT NOT NULL,
        control_valve_label TEXT NOT NULL,
        pulse_frequency_switch_point_hz REAL NOT NULL,
        mass_per_pulse REAL NOT NULL,
        mass_unit TEXT NOT NULL,
        flow_point_g_per_s REAL NOT NULL,
        specified_mass REAL NOT NULL,
        advance_mass REAL NOT NULL,
        corrected_target_mass REAL NOT NULL,
        source_trial_ids_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        retired_at TEXT,
        configuration_snapshot_json TEXT NOT NULL DEFAULT '{}',
        notes TEXT,
        FOREIGN KEY(device_id) REFERENCES devices(device_id),
        FOREIGN KEY(source_result_id) REFERENCES analysis_results(result_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_filling_advance_profiles_device_created
    ON filling_advance_profiles(device_id, created_at DESC, profile_id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_filling_advance_profiles_source_result
    ON filling_advance_profiles(source_result_id)
    """,
)


def _current_schema_version(connection: sqlite3.Connection) -> int | None:
    table_exists = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_migrations'
        """
    ).fetchone()
    if table_exists is None:
        return None
    row = connection.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()
    if row is None or row["version"] is None:
        return None
    return int(row["version"])


def _backfill_modbus_profile_devices(connection: sqlite3.Connection) -> None:
    profiles = connection.execute(
        """
        SELECT
            profile.device_id,
            profile.created_at,
            profile.updated_at
        FROM modbus_device_profiles AS profile
        WHERE NOT EXISTS (
            SELECT 1
            FROM devices AS device
            WHERE device.device_id = profile.device_id
        )
        """
    ).fetchall()
    connection.executemany(
        """
        INSERT INTO devices (
            device_id, device_type, connection_metadata_json,
            created_at, updated_at
        )
        VALUES (?, 'modbus_rtu', '{}', ?, ?)
        """,
        (
            (
                profile["device_id"],
                _legacy_timestamp_as_utc(profile["created_at"]),
                _legacy_timestamp_as_utc(profile["updated_at"]),
            )
            for profile in profiles
        ),
    )


def _migrate_modbus_register_map_catalog(connection: sqlite3.Connection) -> None:
    """Bind legacy inline profile maps without altering their saved snapshots."""

    profiles = connection.execute(
        """
        SELECT profile_id, register_map_json, register_map_id, register_map_version
        FROM modbus_device_profiles
        WHERE register_map_id IS NULL OR register_map_version IS NULL
        """
    ).fetchall()
    now = datetime.now(UTC).isoformat()
    for profile in profiles:
        try:
            payload = json.loads(profile["register_map_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        registers = payload.get("registers")
        if not isinstance(registers, list) or not registers:
            continue
        canonical_registers = json.dumps(
            registers,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        checksum = hashlib.sha256(canonical_registers.encode("utf-8")).hexdigest()
        register_map_id = f"legacy-{checksum}"
        version = "1.0.0"
        map_name = str(payload.get("name") or "register map").strip()
        normalized_payload = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO modbus_register_maps (
                register_map_id, version, display_name, source, checksum,
                register_map_json, created_at, updated_at
            ) VALUES (?, ?, ?, 'legacy', ?, ?, ?, ?)
            """,
            (
                register_map_id,
                version,
                f"Imported {map_name}",
                checksum,
                normalized_payload,
                now,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE modbus_device_profiles
            SET register_map_id = ?, register_map_version = ?
            WHERE profile_id = ?
            """,
            (register_map_id, version, profile["profile_id"]),
        )


def _legacy_timestamp_as_utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column_name, declaration in columns.items():
        if column_name not in existing:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {declaration}"
            )

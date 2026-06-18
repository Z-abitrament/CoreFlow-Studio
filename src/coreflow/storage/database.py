"""SQLite database initialization and connection handling."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3


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
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
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
)


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

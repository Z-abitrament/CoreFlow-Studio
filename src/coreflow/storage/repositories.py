"""SQLite repositories for CoreFlow Studio metadata."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import PurePath, PureWindowsPath
from typing import Any

from coreflow.storage.database import Database
from coreflow.storage.models import (
    AnalysisResultRecord,
    Artifact,
    ArtifactType,
    AuditLogRecord,
    DeviceRecord,
    RunSummary,
)
from coreflow.workflows.models import RunSession, WorkflowStep


class StorageRepository:
    """Repository facade for M4 metadata persistence."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save_device(self, record: DeviceRecord) -> None:
        now = _utc_now()
        created_at = record.created_at or now
        updated_at = record.updated_at or now
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO devices (
                    device_id, device_type, serial_number, model, firmware_version,
                    hardware_version, protocol_address, connection_metadata_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    device_type=excluded.device_type,
                    serial_number=excluded.serial_number,
                    model=excluded.model,
                    firmware_version=excluded.firmware_version,
                    hardware_version=excluded.hardware_version,
                    protocol_address=excluded.protocol_address,
                    connection_metadata_json=excluded.connection_metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.device_id,
                    record.device_type,
                    record.serial_number,
                    record.model,
                    record.firmware_version,
                    record.hardware_version,
                    record.protocol_address,
                    _to_json(record.connection_metadata),
                    _dt(created_at),
                    _dt(updated_at),
                ),
            )

    def get_device(self, device_id: str) -> DeviceRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return DeviceRecord(
            device_id=row["device_id"],
            device_type=row["device_type"],
            serial_number=row["serial_number"],
            model=row["model"],
            firmware_version=row["firmware_version"],
            hardware_version=row["hardware_version"],
            protocol_address=row["protocol_address"],
            connection_metadata=_from_json(row["connection_metadata_json"], {}),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def save_run(self, run: RunSession) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO run_sessions (
                    run_id, run_type, workflow_name, workflow_version, device_id,
                    operator, status, started_at, ended_at,
                    configuration_snapshot_json, software_version, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.run_type.value,
                    run.workflow_name,
                    run.workflow_version,
                    run.device_id,
                    run.operator,
                    run.status.value,
                    _dt(run.started_at),
                    _dt(run.ended_at),
                    _to_json(run.configuration_snapshot),
                    run.software_version,
                    run.notes,
                ),
            )

    def get_run_status(self, run_id: str) -> str | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM run_sessions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return str(row["status"])

    def get_run(self, run_id: str) -> RunSession | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM run_sessions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _run_session_from_row(row)

    def list_runs(self, limit: int | None = None) -> tuple[RunSummary, ...]:
        query = """
            SELECT run_id, run_type, workflow_name, status, device_id, operator,
                   started_at, ended_at, software_version
            FROM run_sessions
            ORDER BY COALESCE(started_at, '') DESC, run_id DESC
        """
        parameters: tuple[Any, ...] = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        with self._database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_run_summary_from_row(row) for row in rows)

    def save_step(self, step: WorkflowStep) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO workflow_steps (
                    step_id, run_id, name, step_type, status, started_at, ended_at,
                    input_configuration_json, output_summary_json, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    step.run_id,
                    step.name,
                    step.step_type.value,
                    step.status.value,
                    _dt(step.started_at),
                    _dt(step.ended_at),
                    _to_json(step.input_configuration),
                    _to_json(step.output_summary),
                    step.error_message,
                ),
            )

    def list_step_statuses(self, run_id: str) -> tuple[tuple[str, str], ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT name, status
                FROM workflow_steps
                WHERE run_id = ?
                ORDER BY step_id
                """,
                (run_id,),
            ).fetchall()
        return tuple((str(row["name"]), str(row["status"])) for row in rows)

    def list_steps(self, run_id: str) -> tuple[WorkflowStep, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM workflow_steps
                WHERE run_id = ?
                ORDER BY step_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(_workflow_step_from_row(row) for row in rows)

    def save_analysis_result(self, record: AnalysisResultRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO analysis_results (
                    result_id, run_id, step_id, result_type, algorithm_name,
                    algorithm_version, input_artifact_ids_json,
                    configuration_snapshot_json, summary_metrics_json,
                    pass_fail_decision, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.result_id,
                    record.run_id,
                    record.step_id,
                    record.result_type,
                    record.algorithm_name,
                    record.algorithm_version,
                    _to_json(record.input_artifact_ids),
                    _to_json(record.configuration_snapshot),
                    _to_json(record.summary_metrics),
                    record.pass_fail_decision,
                    _dt(record.created_at or _utc_now()),
                ),
            )

    def list_analysis_results(self, run_id: str) -> tuple[AnalysisResultRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM analysis_results
                WHERE run_id = ?
                ORDER BY result_id
                """,
                (run_id,),
            ).fetchall()
        return tuple(_analysis_result_from_row(row) for row in rows)

    def save_artifact(self, artifact: Artifact) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO artifacts (
                    artifact_id, run_id, step_id, artifact_type, file_path,
                    file_format, size_bytes, checksum, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.run_id,
                    artifact.step_id,
                    artifact.artifact_type.value,
                    str(artifact.file_path),
                    artifact.file_format,
                    artifact.size_bytes,
                    artifact.checksum,
                    _dt(artifact.created_at or _utc_now()),
                    _to_json(artifact.metadata),
                ),
            )

    def list_artifacts(self, run_id: str | None = None) -> tuple[Artifact, ...]:
        query = "SELECT * FROM artifacts"
        parameters: tuple[Any, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            parameters = (run_id,)
        query += " ORDER BY artifact_id"
        with self._database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_artifact_from_row(row) for row in rows)

    def save_audit_log(self, record: AuditLogRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs (
                    audit_id, timestamp, actor, action_type, workflow_state,
                    device_id, run_id, target, previous_value_json, new_value_json,
                    dry_run, validation_result, protocol_request_ref, result,
                    error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.audit_id,
                    _dt(record.timestamp),
                    record.actor,
                    record.action_type,
                    record.workflow_state,
                    record.device_id,
                    record.run_id,
                    record.target,
                    _to_json(record.previous_value),
                    _to_json(record.new_value),
                    1 if record.dry_run else 0,
                    record.validation_result,
                    record.protocol_request_ref,
                    record.result,
                    record.error_message,
                ),
            )

    def count_rows(self, table_name: str) -> int:
        if table_name not in {
            "devices",
            "run_sessions",
            "workflow_steps",
            "analysis_results",
            "artifacts",
            "audit_logs",
            "schema_migrations",
        }:
            raise ValueError(f"Unsupported table: {table_name}")
        with self._database.connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS count FROM {table_name}").fetchone()
        return int(row["count"])


def _artifact_from_row(row: sqlite3.Row) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        artifact_type=ArtifactType(row["artifact_type"]),
        file_path=PureWindowsPath(row["file_path"]),
        file_format=row["file_format"],
        size_bytes=row["size_bytes"],
        checksum=row["checksum"],
        created_at=_parse_dt(row["created_at"]),
        metadata=_from_json(row["metadata_json"], {}),
    )


def _analysis_result_from_row(row: sqlite3.Row) -> AnalysisResultRecord:
    return AnalysisResultRecord(
        result_id=row["result_id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        result_type=row["result_type"],
        algorithm_name=row["algorithm_name"],
        algorithm_version=row["algorithm_version"],
        input_artifact_ids=tuple(_from_json(row["input_artifact_ids_json"], [])),
        configuration_snapshot=_from_json(row["configuration_snapshot_json"], {}),
        summary_metrics=_from_json(row["summary_metrics_json"], {}),
        pass_fail_decision=row["pass_fail_decision"],
        created_at=_parse_dt(row["created_at"]),
    )


def _run_session_from_row(row: sqlite3.Row) -> RunSession:
    from coreflow.workflows.models import RunStatus, RunType

    return RunSession(
        run_id=row["run_id"],
        run_type=RunType(row["run_type"]),
        workflow_name=row["workflow_name"],
        workflow_version=row["workflow_version"],
        device_id=row["device_id"],
        operator=row["operator"],
        status=RunStatus(row["status"]),
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        configuration_snapshot=_from_json(row["configuration_snapshot_json"], {}),
        software_version=row["software_version"],
        notes=row["notes"],
    )


def _run_summary_from_row(row: sqlite3.Row) -> RunSummary:
    return RunSummary(
        run_id=row["run_id"],
        run_type=row["run_type"],
        workflow_name=row["workflow_name"],
        status=row["status"],
        device_id=row["device_id"],
        operator=row["operator"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        software_version=row["software_version"],
    )


def _workflow_step_from_row(row: sqlite3.Row) -> WorkflowStep:
    from coreflow.workflows.models import WorkflowStepStatus, WorkflowStepType

    return WorkflowStep(
        step_id=row["step_id"],
        run_id=row["run_id"],
        name=row["name"],
        step_type=WorkflowStepType(row["step_type"]),
        status=WorkflowStepStatus(row["status"]),
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        input_configuration=_from_json(row["input_configuration_json"], {}),
        output_summary=_from_json(row["output_summary_json"], {}),
        error_message=row["error_message"],
    )


def _to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _from_json(value: str, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)

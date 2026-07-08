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
    DeviceHistoryRecord,
    ModbusDeviceProfileRecord,
    ModbusOperationAttemptRecord,
    ModbusTestSessionRecord,
    ModbusTrialRecord,
    PulseDeviceProfileRecord,
    PulseOperationAttemptRecord,
    PulseTrialRecord,
    RunSummary,
    VariableSampleRecord,
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

    def save_modbus_device_profile(
        self,
        record: ModbusDeviceProfileRecord,
    ) -> None:
        now = _utc_now()
        created_at = record.created_at or now
        updated_at = record.updated_at or now
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO modbus_device_profiles (
                    profile_id, device_id, display_name, device_model, tube_model,
                    transmitter_model, connection_settings_json, register_map_json,
                    notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    device_id=excluded.device_id,
                    display_name=excluded.display_name,
                    device_model=excluded.device_model,
                    tube_model=excluded.tube_model,
                    transmitter_model=excluded.transmitter_model,
                    connection_settings_json=excluded.connection_settings_json,
                    register_map_json=excluded.register_map_json,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    record.profile_id,
                    record.device_id,
                    record.display_name,
                    record.device_model,
                    record.tube_model,
                    record.transmitter_model,
                    _to_json(record.connection_settings),
                    _to_json(record.register_map),
                    record.notes,
                    _dt(created_at),
                    _dt(updated_at),
                ),
            )

    def get_modbus_device_profile(
        self,
        device_id: str,
    ) -> ModbusDeviceProfileRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM modbus_device_profiles
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return _modbus_device_profile_from_row(row)

    def list_modbus_device_profiles(self) -> tuple[ModbusDeviceProfileRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM modbus_device_profiles
                ORDER BY device_id
                """
            ).fetchall()
        return tuple(_modbus_device_profile_from_row(row) for row in rows)

    def delete_modbus_device_profile(self, device_id: str) -> bool:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT profile_id
                FROM modbus_device_profiles
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
            if row is None:
                return False
            profile_id = row["profile_id"]
            connection.execute(
                """
                UPDATE modbus_test_sessions
                SET profile_id = NULL
                WHERE profile_id = ?
                """,
                (profile_id,),
            )
            cursor = connection.execute(
                """
                DELETE FROM modbus_device_profiles
                WHERE device_id = ?
                """,
                (device_id,),
            )
        return cursor.rowcount > 0

    def delete_legacy_modbus_device_profiles(self) -> int:
        """Remove old port-derived profiles while keeping device-linked records."""

        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT profile_id
                FROM modbus_device_profiles
                WHERE lower(device_id) LIKE 'modbus:%'
                """
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE modbus_test_sessions
                    SET profile_id = NULL
                    WHERE profile_id = ?
                    """,
                    (row["profile_id"],),
                )
            cursor = connection.execute(
                """
                DELETE FROM modbus_device_profiles
                WHERE lower(device_id) LIKE 'modbus:%'
                """
            )
        return cursor.rowcount

    def save_pulse_device_profile(
        self,
        record: PulseDeviceProfileRecord,
    ) -> None:
        now = _utc_now()
        created_at = record.created_at or now
        updated_at = record.updated_at or now
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO pulse_device_profiles (
                    profile_id, device_id, display_name, channel, edge,
                    pulse_value, unit, switch_frequency_hz, boundary_tolerance_s,
                    notes, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_id) DO UPDATE SET
                    device_id=excluded.device_id,
                    display_name=excluded.display_name,
                    channel=excluded.channel,
                    edge=excluded.edge,
                    pulse_value=excluded.pulse_value,
                    unit=excluded.unit,
                    switch_frequency_hz=excluded.switch_frequency_hz,
                    boundary_tolerance_s=excluded.boundary_tolerance_s,
                    notes=excluded.notes,
                    updated_at=excluded.updated_at
                """,
                (
                    record.profile_id,
                    record.device_id,
                    record.display_name,
                    record.channel,
                    record.edge,
                    record.pulse_value,
                    record.unit,
                    record.switch_frequency_hz,
                    record.boundary_tolerance_s,
                    record.notes,
                    _dt(created_at),
                    _dt(updated_at),
                ),
            )

    def get_pulse_device_profile(
        self,
        device_id: str,
    ) -> PulseDeviceProfileRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM pulse_device_profiles
                WHERE device_id = ?
                """,
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        return _pulse_device_profile_from_row(row)

    def list_pulse_device_profiles(self) -> tuple[PulseDeviceProfileRecord, ...]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM pulse_device_profiles
                ORDER BY device_id
                """
            ).fetchall()
        return tuple(_pulse_device_profile_from_row(row) for row in rows)

    def save_modbus_test_session(self, record: ModbusTestSessionRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO modbus_test_sessions (
                    session_id, device_id, profile_id, operator, status,
                    started_at, ended_at, device_metadata_json,
                    register_map_snapshot_json, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.device_id,
                    record.profile_id,
                    record.operator,
                    record.status,
                    _dt(record.started_at),
                    _dt(record.ended_at),
                    _to_json(record.device_metadata),
                    _to_json(record.register_map_snapshot),
                    record.notes,
                ),
            )

    def list_modbus_test_sessions(
        self,
        *,
        device_id: str | None = None,
    ) -> tuple[ModbusTestSessionRecord, ...]:
        query = "SELECT * FROM modbus_test_sessions"
        parameters: tuple[Any, ...] = ()
        if device_id is not None:
            query += " WHERE device_id = ?"
            parameters = (device_id,)
        query += " ORDER BY started_at DESC, session_id DESC"
        with self._database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(_modbus_test_session_from_row(row) for row in rows)

    def save_modbus_operation_attempt(
        self,
        record: ModbusOperationAttemptRecord,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO modbus_operation_attempts (
                    attempt_id, session_id, run_id, device_id, operation_type,
                    status, started_at, ended_at, operator, device_metadata_json,
                    register_map_snapshot_json, raw_artifact_id, summary_json,
                    notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.attempt_id,
                    record.session_id,
                    record.run_id,
                    record.device_id,
                    record.operation_type,
                    record.status,
                    _dt(record.started_at),
                    _dt(record.ended_at),
                    record.operator,
                    _to_json(record.device_metadata),
                    _to_json(record.register_map_snapshot),
                    record.raw_artifact_id,
                    _to_json(record.summary),
                    record.notes,
                ),
            )

    def list_modbus_operation_attempts(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        operation_type: str | None = None,
        status: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        device_model: str | None = None,
        tube_model: str | None = None,
        transmitter_model: str | None = None,
    ) -> tuple[ModbusOperationAttemptRecord, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if operation_type is not None and operation_type not in ("", "all"):
            clauses.append("operation_type = ?")
            parameters.append(operation_type)
        if status is not None and status not in ("", "all"):
            clauses.append("status = ?")
            parameters.append(status)
        if started_from is not None:
            clauses.append("started_at >= ?")
            parameters.append(_dt(started_from))
        if started_to is not None:
            clauses.append("started_at <= ?")
            parameters.append(_dt(started_to))
        query = "SELECT * FROM modbus_operation_attempts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(started_at, '') DESC, attempt_id DESC"
        with self._database.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        records = tuple(_modbus_operation_attempt_from_row(row) for row in rows)
        return tuple(
            record
            for record in records
            if _metadata_filter_matches(
                record.device_metadata,
                device_model=device_model,
                tube_model=tube_model,
                transmitter_model=transmitter_model,
            )
        )

    def save_modbus_trial_record(self, record: ModbusTrialRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO modbus_trial_records (
                    trial_id, session_id, attempt_id, run_id, device_id,
                    flow_point, trial_index, trial_status, k_factor_parameter,
                    original_k_factor, mass_acc_before, mass_acc_after,
                    measured_mass_delta, standard_mass, percent_error,
                    mean_flow, instant_flow, flow_started_at, flow_instant_at,
                    flow_ended_at, raw_artifact_id, device_metadata_json, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trial_id,
                    record.session_id,
                    record.attempt_id,
                    record.run_id,
                    record.device_id,
                    record.flow_point,
                    record.trial_index,
                    record.trial_status,
                    record.k_factor_parameter,
                    record.original_k_factor,
                    record.mass_acc_before,
                    record.mass_acc_after,
                    record.measured_mass_delta,
                    record.standard_mass,
                    record.percent_error,
                    record.mean_flow,
                    record.instant_flow,
                    _dt(record.flow_started_at),
                    _dt(record.flow_instant_at),
                    _dt(record.flow_ended_at),
                    record.raw_artifact_id,
                    _to_json(record.device_metadata),
                    record.notes,
                ),
            )

    def list_modbus_trial_records(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        trial_status: str | None = None,
        device_model: str | None = None,
        tube_model: str | None = None,
        transmitter_model: str | None = None,
    ) -> tuple[ModbusTrialRecord, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if trial_status is not None and trial_status not in ("", "all"):
            clauses.append("trial_status = ?")
            parameters.append(trial_status)
        query = "SELECT * FROM modbus_trial_records"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY flow_point, trial_index, trial_id"
        with self._database.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        records = tuple(_modbus_trial_from_row(row) for row in rows)
        return tuple(
            record
            for record in records
            if _metadata_filter_matches(
                record.device_metadata,
                device_model=device_model,
                tube_model=tube_model,
                transmitter_model=transmitter_model,
            )
        )

    def save_pulse_operation_attempt(
        self,
        record: PulseOperationAttemptRecord,
    ) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO pulse_operation_attempts (
                    attempt_id, device_id, operation_type, status, started_at,
                    ended_at, operator, source_path, raw_artifact_id,
                    summary_json, configuration_snapshot_json, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.attempt_id,
                    record.device_id,
                    record.operation_type,
                    record.status,
                    _dt(record.started_at),
                    _dt(record.ended_at),
                    record.operator,
                    record.source_path,
                    record.raw_artifact_id,
                    _to_json(record.summary),
                    _to_json(record.configuration_snapshot),
                    record.notes,
                ),
            )

    def list_pulse_operation_attempts(
        self,
        *,
        device_id: str | None = None,
        operation_type: str | None = None,
        status: str | None = None,
    ) -> tuple[PulseOperationAttemptRecord, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if operation_type is not None and operation_type not in ("", "all"):
            clauses.append("operation_type = ?")
            parameters.append(operation_type)
        if status is not None and status not in ("", "all"):
            clauses.append("status = ?")
            parameters.append(status)
        query = "SELECT * FROM pulse_operation_attempts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY COALESCE(started_at, '') DESC, attempt_id DESC"
        with self._database.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return tuple(_pulse_operation_attempt_from_row(row) for row in rows)

    def save_pulse_trial_record(self, record: PulseTrialRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO pulse_trial_records (
                    trial_id, attempt_id, device_id, flow_point, trial_index,
                    trial_status, pulse_count, measured_quantity,
                    standard_quantity, percent_error, mean_rate, started_at,
                    ended_at, boundary_pulse_count, raw_artifact_id, notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.trial_id,
                    record.attempt_id,
                    record.device_id,
                    record.flow_point,
                    record.trial_index,
                    record.trial_status,
                    record.pulse_count,
                    record.measured_quantity,
                    record.standard_quantity,
                    record.percent_error,
                    record.mean_rate,
                    _dt(record.started_at),
                    _dt(record.ended_at),
                    record.boundary_pulse_count,
                    record.raw_artifact_id,
                    record.notes,
                ),
            )

    def list_pulse_trial_records(
        self,
        *,
        device_id: str | None = None,
        trial_status: str | None = None,
    ) -> tuple[PulseTrialRecord, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if trial_status is not None and trial_status not in ("", "all"):
            clauses.append("trial_status = ?")
            parameters.append(trial_status)
        query = "SELECT * FROM pulse_trial_records"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY flow_point, trial_index, trial_id"
        with self._database.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return tuple(_pulse_trial_record_from_row(row) for row in rows)

    def list_device_history_records(
        self,
        *,
        device_id: str,
        module: str | None = None,
    ) -> tuple[DeviceHistoryRecord, ...]:
        normalized_module = None if module in (None, "", "All") else module
        records: list[DeviceHistoryRecord] = []
        if normalized_module in (None, "Modbus"):
            records.extend(
                DeviceHistoryRecord(
                    module="Modbus",
                    record_id=attempt.attempt_id,
                    device_id=attempt.device_id,
                    operation_type=attempt.operation_type,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    ended_at=attempt.ended_at,
                    summary=attempt.summary,
                    notes=attempt.notes,
                )
                for attempt in self.list_modbus_operation_attempts(device_id=device_id)
            )
        if normalized_module in (None, "Pulse"):
            records.extend(
                DeviceHistoryRecord(
                    module="Pulse",
                    record_id=attempt.attempt_id,
                    device_id=attempt.device_id,
                    operation_type=attempt.operation_type,
                    status=attempt.status,
                    started_at=attempt.started_at,
                    ended_at=attempt.ended_at,
                    summary=attempt.summary,
                    notes=attempt.notes,
                )
                for attempt in self.list_pulse_operation_attempts(device_id=device_id)
            )
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    record.started_at or datetime.min.replace(tzinfo=UTC),
                    record.record_id,
                ),
                reverse=True,
            )
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

    def update_run_notes(self, run_id: str, notes: str) -> None:
        with self._database.connect() as connection:
            cursor = connection.execute(
                "UPDATE run_sessions SET notes = ? WHERE run_id = ?",
                (notes, run_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown run: {run_id}")

    def list_runs(self, limit: int | None = None) -> tuple[RunSummary, ...]:
        query = """
            SELECT run_id, run_type, workflow_name, status, device_id, operator,
                   started_at, ended_at, software_version, notes
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

    def save_variable_sample(self, record: VariableSampleRecord) -> None:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO variable_samples (
                    sample_id, device_id, run_id, step_id, variable_name,
                    captured_at, value_json, unit, source_channel, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.sample_id,
                    record.device_id,
                    record.run_id,
                    record.step_id,
                    record.variable_name,
                    _dt(record.captured_at),
                    _to_json(record.value),
                    record.unit,
                    record.source_channel,
                    _to_json(record.metadata),
                ),
            )

    def list_variable_samples(
        self,
        *,
        run_id: str | None = None,
        device_id: str | None = None,
        variable_name: str | None = None,
    ) -> tuple[VariableSampleRecord, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if device_id is not None:
            clauses.append("device_id = ?")
            parameters.append(device_id)
        if variable_name is not None:
            clauses.append("variable_name = ?")
            parameters.append(variable_name)
        query = "SELECT * FROM variable_samples"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY captured_at, sample_id"
        with self._database.connect() as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        return tuple(_variable_sample_from_row(row) for row in rows)

    def count_rows(self, table_name: str) -> int:
        if table_name not in {
            "devices",
            "run_sessions",
            "workflow_steps",
            "analysis_results",
            "artifacts",
            "audit_logs",
            "schema_migrations",
            "variable_samples",
            "modbus_device_profiles",
            "modbus_test_sessions",
            "modbus_operation_attempts",
            "modbus_trial_records",
            "pulse_device_profiles",
            "pulse_operation_attempts",
            "pulse_trial_records",
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


def _modbus_device_profile_from_row(
    row: sqlite3.Row,
) -> ModbusDeviceProfileRecord:
    return ModbusDeviceProfileRecord(
        profile_id=row["profile_id"],
        device_id=row["device_id"],
        display_name=row["display_name"],
        device_model=row["device_model"],
        tube_model=row["tube_model"],
        transmitter_model=row["transmitter_model"],
        connection_settings=_from_json(row["connection_settings_json"], {}),
        register_map=_from_json(row["register_map_json"], {}),
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _pulse_device_profile_from_row(row: sqlite3.Row) -> PulseDeviceProfileRecord:
    return PulseDeviceProfileRecord(
        profile_id=row["profile_id"],
        device_id=row["device_id"],
        display_name=row["display_name"],
        channel=row["channel"],
        edge=row["edge"],
        pulse_value=float(row["pulse_value"]),
        unit=row["unit"],
        switch_frequency_hz=float(row["switch_frequency_hz"]),
        boundary_tolerance_s=row["boundary_tolerance_s"],
        notes=row["notes"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _modbus_test_session_from_row(row: sqlite3.Row) -> ModbusTestSessionRecord:
    return ModbusTestSessionRecord(
        session_id=row["session_id"],
        device_id=row["device_id"],
        profile_id=row["profile_id"],
        operator=row["operator"],
        status=row["status"],
        started_at=_parse_dt(row["started_at"]) or _utc_now(),
        ended_at=_parse_dt(row["ended_at"]),
        device_metadata=_from_json(row["device_metadata_json"], {}),
        register_map_snapshot=_from_json(row["register_map_snapshot_json"], {}),
        notes=row["notes"],
    )


def _modbus_operation_attempt_from_row(
    row: sqlite3.Row,
) -> ModbusOperationAttemptRecord:
    return ModbusOperationAttemptRecord(
        attempt_id=row["attempt_id"],
        session_id=row["session_id"],
        run_id=row["run_id"],
        device_id=row["device_id"],
        operation_type=row["operation_type"],
        status=row["status"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        operator=row["operator"],
        device_metadata=_from_json(row["device_metadata_json"], {}),
        register_map_snapshot=_from_json(row["register_map_snapshot_json"], {}),
        raw_artifact_id=row["raw_artifact_id"],
        summary=_from_json(row["summary_json"], {}),
        notes=row["notes"],
    )


def _modbus_trial_from_row(row: sqlite3.Row) -> ModbusTrialRecord:
    return ModbusTrialRecord(
        trial_id=row["trial_id"],
        session_id=row["session_id"],
        attempt_id=row["attempt_id"],
        run_id=row["run_id"],
        device_id=row["device_id"],
        flow_point=float(row["flow_point"]),
        trial_index=int(row["trial_index"]),
        trial_status=row["trial_status"],
        k_factor_parameter=row["k_factor_parameter"],
        original_k_factor=row["original_k_factor"],
        mass_acc_before=row["mass_acc_before"],
        mass_acc_after=row["mass_acc_after"],
        measured_mass_delta=row["measured_mass_delta"],
        standard_mass=row["standard_mass"],
        percent_error=row["percent_error"],
        mean_flow=row["mean_flow"],
        instant_flow=row["instant_flow"],
        flow_started_at=_parse_dt(row["flow_started_at"]),
        flow_instant_at=_parse_dt(row["flow_instant_at"]),
        flow_ended_at=_parse_dt(row["flow_ended_at"]),
        raw_artifact_id=row["raw_artifact_id"],
        device_metadata=_from_json(row["device_metadata_json"], {}),
        notes=row["notes"],
    )


def _pulse_operation_attempt_from_row(
    row: sqlite3.Row,
) -> PulseOperationAttemptRecord:
    return PulseOperationAttemptRecord(
        attempt_id=row["attempt_id"],
        device_id=row["device_id"],
        operation_type=row["operation_type"],
        status=row["status"],
        operator=row["operator"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        source_path=row["source_path"],
        raw_artifact_id=row["raw_artifact_id"],
        summary=_from_json(row["summary_json"], {}),
        configuration_snapshot=_from_json(row["configuration_snapshot_json"], {}),
        notes=row["notes"],
    )


def _pulse_trial_record_from_row(row: sqlite3.Row) -> PulseTrialRecord:
    return PulseTrialRecord(
        trial_id=row["trial_id"],
        attempt_id=row["attempt_id"],
        device_id=row["device_id"],
        flow_point=float(row["flow_point"]),
        trial_index=int(row["trial_index"]),
        trial_status=row["trial_status"],
        pulse_count=int(row["pulse_count"]),
        measured_quantity=float(row["measured_quantity"]),
        standard_quantity=float(row["standard_quantity"]),
        percent_error=float(row["percent_error"]),
        mean_rate=row["mean_rate"],
        started_at=_parse_dt(row["started_at"]),
        ended_at=_parse_dt(row["ended_at"]),
        boundary_pulse_count=int(row["boundary_pulse_count"]),
        raw_artifact_id=row["raw_artifact_id"],
        notes=row["notes"],
    )


def _metadata_filter_matches(
    metadata: dict[str, Any],
    *,
    device_model: str | None = None,
    tube_model: str | None = None,
    transmitter_model: str | None = None,
) -> bool:
    expected = {
        "device_model": device_model,
        "tube_model": tube_model,
        "transmitter_model": transmitter_model,
    }
    for key, value in expected.items():
        if value not in (None, "") and str(metadata.get(key, "")) != value:
            return False
    return True


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
        notes=row["notes"],
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


def _variable_sample_from_row(row: sqlite3.Row) -> VariableSampleRecord:
    return VariableSampleRecord(
        sample_id=row["sample_id"],
        device_id=row["device_id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        variable_name=row["variable_name"],
        captured_at=_parse_dt(row["captured_at"]) or _utc_now(),
        value=_from_json(row["value_json"], None),
        unit=row["unit"],
        source_channel=row["source_channel"],
        metadata=_from_json(row["metadata_json"], {}),
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

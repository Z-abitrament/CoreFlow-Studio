"""Storage value objects for metadata and linked files."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any


class ArtifactType(StrEnum):
    """File artifact categories tracked by SQLite metadata."""

    RAW = "raw"
    PROCESSED = "processed"
    EXPORT = "export"
    REPORT = "report"
    LOG = "log"
    REPLAY = "replay"
    CONFIG_SNAPSHOT = "config_snapshot"


@dataclass(frozen=True, slots=True)
class Artifact:
    """A file linked to a run, step, result, or configuration snapshot."""

    artifact_id: str
    run_id: str
    artifact_type: ArtifactType
    file_path: PurePath
    file_format: str
    step_id: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    """Persisted device metadata."""

    device_id: str
    device_type: str
    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    protocol_address: str | None = None
    connection_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunSummary:
    """Compact run-session row for history and inspection views."""

    run_id: str
    run_type: str
    workflow_name: str
    status: str
    device_id: str
    operator: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    software_version: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisResultRecord:
    """Persisted analysis output summary."""

    result_id: str
    run_id: str
    step_id: str | None
    result_type: str
    algorithm_name: str
    algorithm_version: str
    input_artifact_ids: tuple[str, ...] = ()
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)
    summary_metrics: dict[str, Any] = field(default_factory=dict)
    pass_fail_decision: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditLogRecord:
    """Append-only safety-sensitive action record."""

    audit_id: str
    timestamp: datetime
    actor: str
    action_type: str
    device_id: str | None = None
    run_id: str | None = None
    workflow_state: str | None = None
    target: str | None = None
    previous_value: Any | None = None
    new_value: Any | None = None
    dry_run: bool = False
    validation_result: str | None = None
    protocol_request_ref: str | None = None
    result: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class VariableSampleRecord:
    """Timestamped low-rate variable value persisted in SQLite."""

    sample_id: str
    device_id: str
    variable_name: str
    captured_at: datetime
    value: Any
    run_id: str | None = None
    step_id: str | None = None
    unit: str | None = None
    source_channel: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModbusDeviceProfileRecord:
    """Operator-maintained Modbus device profile."""

    profile_id: str
    device_id: str
    display_name: str | None = None
    device_model: str | None = None
    tube_model: str | None = None
    transmitter_model: str | None = None
    connection_settings: dict[str, Any] = field(default_factory=dict)
    register_map: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ModbusTestSessionRecord:
    """One flexible Modbus test session for a device profile."""

    session_id: str
    device_id: str
    operator: str
    status: str
    started_at: datetime
    profile_id: str | None = None
    ended_at: datetime | None = None
    device_metadata: dict[str, Any] = field(default_factory=dict)
    register_map_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ModbusOperationAttemptRecord:
    """One Modbus operation attempt saved for traceability."""

    attempt_id: str
    device_id: str
    operation_type: str
    status: str
    operator: str
    session_id: str | None = None
    run_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    device_metadata: dict[str, Any] = field(default_factory=dict)
    register_map_snapshot: dict[str, Any] = field(default_factory=dict)
    raw_artifact_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ModbusTrialRecord:
    """One saved Modbus error/repeatability trial."""

    trial_id: str
    device_id: str
    flow_point: float
    trial_index: int
    trial_status: str
    k_factor_parameter: str | None = None
    original_k_factor: float | None = None
    session_id: str | None = None
    attempt_id: str | None = None
    run_id: str | None = None
    mass_acc_before: float | None = None
    mass_acc_after: float | None = None
    measured_mass_delta: float | None = None
    standard_mass: float | None = None
    percent_error: float | None = None
    mean_flow: float | None = None
    instant_flow: float | None = None
    flow_started_at: datetime | None = None
    flow_instant_at: datetime | None = None
    flow_ended_at: datetime | None = None
    raw_artifact_id: str | None = None
    device_metadata: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None

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

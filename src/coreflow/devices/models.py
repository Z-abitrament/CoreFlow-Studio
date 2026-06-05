"""Device-domain value objects for CoreFlow Studio."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class DeviceType(StrEnum):
    """Supported device adapter categories."""

    SIMULATED = "simulated"
    MODBUS_RTU = "modbus_rtu"
    FUTURE_ADAPTER = "future_adapter"


class CommunicationState(StrEnum):
    """Connection state visible to services and UI."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAULTED = "faulted"


class WriteMode(StrEnum):
    """Safety mode for parameter write requests."""

    PREVIEW = "preview"
    DRY_RUN = "dry_run"
    ARMED = "armed"


class WriteResultStatus(StrEnum):
    """Result of a parameter write request."""

    PREVIEWED = "previewed"
    DRY_RUN = "dry_run"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Stable identity fields for a transmitter or virtual device."""

    device_id: str
    device_type: DeviceType
    serial_number: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    hardware_version: str | None = None
    protocol_address: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeviceHealth:
    """Device health and status snapshot."""

    state: CommunicationState
    status_flags: tuple[str, ...] = ()
    alarm_flags: tuple[str, ...] = ()
    message: str | None = None
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Measurement:
    """Low-rate live measurement snapshot."""

    captured_at: datetime
    mass_flow: float | None = None
    volume_flow: float | None = None
    density: float | None = None
    temperature: float | None = None
    status_flags: tuple[str, ...] = ()
    source_channel: str | None = None
    raw_values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfigurationParameter:
    """Readable or writable device configuration parameter."""

    name: str
    value: Any
    unit: str | None = None
    writable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParameterWriteRequest:
    """Application-level request for preview, dry-run, or armed writes."""

    parameter_name: str
    new_value: Any
    mode: WriteMode
    actor: str
    workflow_state: str
    run_id: str | None = None
    expected_previous_value: Any | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParameterWriteResult:
    """Outcome of a guarded parameter write request."""

    parameter_name: str
    status: WriteResultStatus
    previous_value: Any | None = None
    new_value: Any | None = None
    audit_id: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CommunicationDiagnostic:
    """Communication counters and timing diagnostics for one device channel."""

    state: CommunicationState
    request_count: int = 0
    successful_response_count: int = 0
    timeout_count: int = 0
    frame_error_count: int = 0
    exception_response_count: int = 0
    last_error: str | None = None
    last_success_at: datetime | None = None
    average_response_ms: float | None = None

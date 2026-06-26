"""Standalone Modbus module runtime services."""

from __future__ import annotations

import json
import csv
import io
import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

from coreflow import __version__
from coreflow.analysis.calibration import (
    KFactorCalibrationInput,
    RepeatabilityTestResult,
    RepeatabilityTrial,
    ZeroCalibrationRecord,
    analyze_repeatability,
    calculate_k_factor,
)
from coreflow.app.variable_sampling import VariableSample
from coreflow.app.write_guard import WriteGuardService
from coreflow.devices import (
    CommunicationDiagnostic,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    DeviceType,
    FlowmeterDevice,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
)
from coreflow.hardware import build_placeholder_register_map
from coreflow.hardware.register_map import register_map_from_json, register_map_to_json
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    ModbusRtuFlowmeterDevice,
    ModbusTransport,
    PymodbusSerialTransport,
    RegisterKind,
    SerialConfig,
    TransportResponse,
)
from coreflow.storage import ArtifactStore, ArtifactType, StorageRepository
from coreflow.storage.models import (
    AnalysisResultRecord,
    Artifact,
    DeviceRecord,
    ModbusDeviceProfileRecord,
    ModbusOperationAttemptRecord,
    ModbusTestSessionRecord,
    ModbusTrialRecord,
    VariableSampleRecord,
)
from coreflow.workflows.calibration import (
    FlowSegmentCaptureConfig,
    FlowSegmentCaptureResult,
    KFactorCalibrationConfig,
    KFactorCalibrationWorkflow,
    RepeatabilityTestConfig,
    RepeatabilityTestWorkflow,
    ZeroCalibrationConfig,
    ZeroCalibrationWorkflow,
    capture_flow_segment,
)
from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


@dataclass(frozen=True, slots=True)
class ModbusConnectionSettings:
    """Connection settings owned by the standalone Modbus module."""

    port: str
    unit_id: int
    baudrate: int = 19200
    parity: str = "N"
    stop_bits: int = 1
    order: str = "ABCD"
    read_timeout_s: float = 3.0
    write_timeout_s: float = 3.0
    retry_count: int = 1

    def serial_config(self) -> SerialConfig:
        return SerialConfig(
            port=self.port,
            unit_id=self.unit_id,
            baudrate=self.baudrate,
            parity=self.parity,
            stop_bits=self.stop_bits,
            read_timeout_s=self.read_timeout_s,
            write_timeout_s=self.write_timeout_s,
            retry_count=self.retry_count,
        )


@dataclass(frozen=True, slots=True)
class ModbusModuleStatus:
    """UI-ready state for the independent Modbus module."""

    connected: bool
    device_id: str | None = None
    message: str = "Disconnected"


@dataclass(frozen=True, slots=True)
class ModbusVariableSampleResult:
    """Partial-success result for one variable sampling click."""

    samples: tuple[VariableSample, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModbusVariableSamplingRunResult:
    """Saved multi-sample variable polling operation."""

    run_id: str
    variable_names: tuple[str, ...]
    units: dict[str, str]
    samples: tuple[ModbusTrialSamplePoint, ...]
    poll_interval_s: float
    started_at: datetime
    ended_at: datetime
    flow_samples_artifact_id: str | None = None
    raw_artifact_id: str | None = None
    test_session_id: str | None = None
    errors: tuple[str, ...] = ()
    status: str = "passed"
    notes: str = ""

    @property
    def sample_count(self) -> int:
        return len(self.samples)


@dataclass(frozen=True, slots=True)
class ModbusZeroCalibrationResult:
    """UI-ready zero calibration result."""

    run_id: str
    record: ZeroCalibrationRecord
    audit_id: str
    pre_snapshot: dict[str, object] = field(default_factory=dict)
    pre_snapshot_captured_at: datetime | None = None
    raw_artifact_id: str | None = None
    test_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModbusKFactorSimpleCapture:
    """Captured device data before operator standard-mass entry."""

    run_id: str
    flow_rate_parameter: str
    flow_acc_parameter: str
    k_factor_parameter: str
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    mass_acc_before: float
    mass_acc_after: float
    current_k_factor: float
    segment: FlowSegmentCaptureResult
    poll_interval_s: float
    raw_curve: tuple[dict[str, object], ...] = ()
    raw_artifact_id: str | None = None
    test_session_id: str | None = None

    @property
    def measured_mass_delta(self) -> float:
        return self.mass_acc_after - self.mass_acc_before


@dataclass(frozen=True, slots=True)
class ModbusKFactorSimpleResult:
    """UI-ready result for one simple K factor calibration trial."""

    run_id: str
    flow_rate_parameter: str
    flow_acc_parameter: str
    k_factor_parameter: str
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    mass_acc_before: float
    mass_acc_after: float
    standard_mass: float
    current_k_factor: float
    corrected_k_factor: float
    measured_mass_delta: float
    mean_flow: float
    instant_flow: float
    flow_started_at: datetime
    flow_instant_at: datetime
    flow_ended_at: datetime
    poll_interval_s: float
    flow_rate_source: str
    history_saved: bool
    write_requested: bool = False
    write_status: str = "not_requested"
    write_verified: bool = False
    readback_k_factor: float | None = None
    audit_id: str | None = None
    operation_metadata: ModbusOperationMetadata | None = None
    raw_artifact_id: str | None = None
    test_session_id: str | None = None

    @property
    def duration_s(self) -> float:
        return (self.flow_ended_at - self.flow_started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class ModbusFlowSamplePoint:
    """One flow-rate sample captured during a manual flow segment."""

    captured_at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class ModbusTrialSamplePoint:
    """One multi-variable sample captured during a manual trial segment."""

    captured_at: datetime
    values: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModbusFlowSampleSeries:
    """Trial samples loaded from a saved repeatability artifact."""

    artifact_id: str
    run_id: str
    flow_rate_parameter: str
    unit: str
    samples: tuple[ModbusFlowSamplePoint, ...]
    variable_names: tuple[str, ...] = ()
    units: dict[str, str] = field(default_factory=dict)
    points: tuple[ModbusTrialSamplePoint, ...] = ()

    def __post_init__(self) -> None:
        variable_names = self.variable_names
        if not variable_names and self.flow_rate_parameter:
            variable_names = (self.flow_rate_parameter,)
        elif (
            self.flow_rate_parameter
            and self.flow_rate_parameter not in variable_names
        ):
            variable_names = (self.flow_rate_parameter, *variable_names)
        object.__setattr__(self, "variable_names", _unique_names(variable_names))
        units = dict(self.units)
        if self.flow_rate_parameter and self.unit:
            units.setdefault(self.flow_rate_parameter, self.unit)
        object.__setattr__(self, "units", units)
        if not self.points and self.samples and self.flow_rate_parameter:
            object.__setattr__(
                self,
                "points",
                tuple(
                    ModbusTrialSamplePoint(
                        captured_at=sample.captured_at,
                        values={self.flow_rate_parameter: sample.value},
                    )
                    for sample in self.samples
                ),
            )

    def values_for(self, variable_name: str) -> tuple[float | None, ...]:
        if self.points:
            return tuple(
                _optional_float(point.values.get(variable_name))
                for point in self.points
            )
        if variable_name == self.flow_rate_parameter:
            return tuple(sample.value for sample in self.samples)
        return tuple(None for _sample in self.samples)


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilitySimpleCapture:
    """Captured device data before one repeatability standard-mass entry."""

    run_id: str
    flow_point: float
    trial_index: int
    flow_rate_parameter: str
    flow_acc_parameter: str
    k_factor_parameter: str
    original_k_factor: float
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    mass_acc_before: float
    mass_acc_after: float
    segment: FlowSegmentCaptureResult
    poll_interval_s: float
    post_snapshot: dict[str, object] = field(default_factory=dict)
    post_snapshot_captured_at: datetime | None = None
    raw_curve: tuple[dict[str, object], ...] = ()
    raw_artifact_id: str | None = None
    test_session_id: str | None = None
    capture_started_at: datetime | None = None
    flow_samples: tuple[ModbusFlowSamplePoint, ...] = ()
    trial_samples: tuple[ModbusTrialSamplePoint, ...] = ()
    trial_sample_variable_names: tuple[str, ...] = ()
    flow_samples_artifact_id: str | None = None

    @property
    def measured_mass_delta(self) -> float:
        return self.mass_acc_after - self.mass_acc_before

    @property
    def mean_flow(self) -> float:
        if self.segment.duration_s <= 0:
            return 0.0
        return self.measured_mass_delta / self.segment.duration_s


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilitySimpleTrialResult:
    """UI-ready result for one repeatability trial."""

    run_id: str
    flow_point: float
    trial_index: int
    flow_rate_parameter: str
    flow_acc_parameter: str
    k_factor_parameter: str
    original_k_factor: float
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    mass_acc_before: float
    mass_acc_after: float
    measured_mass_delta: float
    standard_mass: float
    percent_error: float
    mean_flow: float
    instant_flow: float
    flow_started_at: datetime
    flow_instant_at: datetime
    flow_ended_at: datetime
    poll_interval_s: float
    post_snapshot: dict[str, object] = field(default_factory=dict)
    post_snapshot_captured_at: datetime | None = None
    flow_rate_source: str = "device"
    raw_artifact_id: str | None = None
    test_session_id: str | None = None
    trial_status: str = "accepted"
    notes: str = ""
    capture_started_at: datetime | None = None
    flow_samples: tuple[ModbusFlowSamplePoint, ...] = ()
    trial_samples: tuple[ModbusTrialSamplePoint, ...] = ()
    trial_sample_variable_names: tuple[str, ...] = ()
    flow_samples_artifact_id: str | None = None
    recorded_flow_sample_count: int = 0

    @property
    def flow_sample_count(self) -> int:
        return len(self.flow_samples) if self.flow_samples else self.recorded_flow_sample_count

    @property
    def duration_s(self) -> float:
        return (self.flow_ended_at - self.flow_started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilityHistoryTrial:
    """One repeatability trial reconstructed from saved history records."""

    trial: ModbusRepeatabilitySimpleTrialResult
    attempt_id: str | None
    pre_snapshot: dict[str, object]
    device_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilityFlowSummary:
    """Operator-facing summary for the currently completed trials at one flow point."""

    flow_point: float
    trial_count: int
    mean_percent_error: float
    max_abs_percent_error: float
    repeatability_stddev_percent: float
    trial_errors: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ModbusDeviceAnalysis:
    """Simple history-derived analysis for one Modbus device ID."""

    device_id: str
    generated_at: datetime
    profile: ModbusDeviceProfile | None
    record_count: int
    session_count: int
    operation_counts: dict[str, int]
    trial_count: int
    accepted_trial_count: int
    diagnostic_trial_count: int
    rejected_trial_count: int
    overall_mean_error_percent: float | None
    overall_stddev_error_percent: float | None
    overall_max_abs_error_percent: float | None
    flow_summaries: tuple[ModbusRepeatabilityFlowSummary, ...]
    latest_final_k: dict[str, object] | None
    latest_k_factor: dict[str, object] | None
    latest_zero_calibration: dict[str, object] | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModbusDeviceAnalysisReportResult:
    """Saved text report produced from device analysis trial selection."""

    run_id: str
    metrics: dict[str, object]
    report_text: str
    report_artifact_id: str | None = None


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilitySimpleResult:
    """UI-ready result for one saved repeatability test summary."""

    run_id: str
    flow_rate_parameter: str
    flow_acc_parameter: str
    poll_interval_s: float
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...]
    analysis: RepeatabilityTestResult
    history_saved: bool
    mode: str = "three_point"
    expected_trials_per_point: int = 3
    test_session_id: str | None = None
    notes: str = ""

    @property
    def started_at(self) -> datetime:
        return min(trial.flow_started_at for trial in self.trials)

    @property
    def ended_at(self) -> datetime:
        return max(trial.flow_ended_at for trial in self.trials)


@dataclass(frozen=True, slots=True)
class ModbusCalibrationHistoryEntry:
    """One historical Modbus calibration operation."""

    run_id: str
    operation: str
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    device_id: str
    operator: str
    metrics: dict[str, Any]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class ModbusOperationMetadata:
    """Operator-supplied device context attached to Modbus operation history."""

    device_model: str = ""
    tube_model: str = ""
    transmitter_model: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "device_model": self.device_model,
            "tube_model": self.tube_model,
            "transmitter_model": self.transmitter_model,
        }


@dataclass(frozen=True, slots=True)
class ModbusDeviceProfile:
    """UI-ready Modbus device profile with stable device identity."""

    device_id: str
    device_model: str = ""
    tube_model: str = ""
    transmitter_model: str = ""
    display_name: str = ""
    connection_settings: dict[str, Any] = field(default_factory=dict)
    register_map: ModbusRegisterMap | None = None
    notes: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def label(self) -> str:
        if self.display_name and self.display_name != self.device_id:
            return f"{self.device_id} - {self.display_name}"
        return self.device_id

    @property
    def metadata(self) -> ModbusOperationMetadata:
        return ModbusOperationMetadata(
            device_model=self.device_model,
            tube_model=self.tube_model,
            transmitter_model=self.transmitter_model,
        )


@dataclass(frozen=True, slots=True)
class ModbusCalibrationHistoryExportResult:
    """Summary of one Modbus calibration-history export file."""

    path: Path
    run_count: int
    analysis_result_count: int
    workflow_step_count: int
    format_version: int


@dataclass(frozen=True, slots=True)
class ModbusCalibrationHistoryImportResult:
    """Summary of one Modbus calibration-history import operation."""

    path: Path
    imported_runs: int
    skipped_runs: int
    renamed_runs: int
    imported_analysis_results: int
    imported_workflow_steps: int
    retargeted_runs: int = 0
    errors: tuple[str, ...] = ()


TransportFactory = Callable[[SerialConfig], ModbusTransport | None]
FrameLogger = Callable[[str, str, str], None]

_CALIBRATION_HISTORY_EXPORT_FORMAT = "coreflow.modbus.calibration_history"
_CALIBRATION_HISTORY_EXPORT_VERSION = 1
_CALIBRATION_HISTORY_WORKFLOWS = {
    "zero_calibration",
    "k_factor_calibration",
    "k_factor_calibration_capture",
    "modbus_variable_sampling",
    "manual_error_repeatability",
    "manual_error_repeatability_final_k",
    "manual_error_repeatability_trial",
}
_CALIBRATION_HISTORY_STATUSES = {"passed", "captured", "failed", "canceled", "error"}
_RAW_CAPTURE_IMPORT_STATUS_FALLBACKS = {
    "accepted": RunStatus.PASSED,
    "captured": RunStatus.PASSED,
    "passed": RunStatus.PASSED,
    "calculated": RunStatus.PASSED,
    "rejected": RunStatus.FAILED,
    "diagnostic": RunStatus.PASSED,
    "failed": RunStatus.FAILED,
    "canceled": RunStatus.CANCELED,
    "error": RunStatus.ERROR,
}


class ModbusModuleRuntime:
    """Coordinates standalone Modbus master operations without simulator channels."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        register_map: ModbusRegisterMap | None = None,
        transport_factory: TransportFactory | None = None,
        operator: str = "operator",
        data_root: Path | None = None,
        zero_calibration_wait_s: float = 3.0,
        k_factor_post_start_sample_s: float = 3.0,
        k_factor_post_stop_delay_s: float = 3.0,
    ) -> None:
        self._repository = repository
        default_data_root = getattr(
            getattr(repository, "_database", None),
            "path",
            None,
        )
        self._artifact_store = ArtifactStore(
            data_root
            or (Path(default_data_root).parent if default_data_root is not None else Path.cwd())
        )
        self._register_map = register_map or build_placeholder_register_map()
        self._transport_factory = transport_factory
        self._frame_logger: FrameLogger | None = None
        self._operator = operator
        self._operation_metadata = ModbusOperationMetadata()
        self._zero_calibration_wait_s = zero_calibration_wait_s
        self._k_factor_post_start_sample_s = k_factor_post_start_sample_s
        self._k_factor_post_stop_delay_s = k_factor_post_stop_delay_s
        self._device: FlowmeterDevice | None = None
        self._device_id: str | None = None
        self._identity: DeviceIdentity | None = None
        self._test_session_id: str | None = None
        self._profile_id: str | None = None
        self._selected_profile: ModbusDeviceProfile | None = None
        self._sequence = repository.count_rows("run_sessions")

    @property
    def status(self) -> ModbusModuleStatus:
        if self._device is None:
            return ModbusModuleStatus(connected=False)
        return ModbusModuleStatus(
            connected=True,
            device_id=self._device_id,
            message=f"Connected {self._device_id}",
        )

    @property
    def register_map(self) -> ModbusRegisterMap:
        return self._register_map

    def configure_register_map(self, register_map: ModbusRegisterMap) -> None:
        if self._device is not None:
            raise RuntimeError("Disconnect before changing the Modbus variable map.")
        self._register_map = register_map

    @property
    def operation_metadata(self) -> ModbusOperationMetadata:
        return self._operation_metadata

    def configure_operation_metadata(
        self,
        metadata: ModbusOperationMetadata,
    ) -> None:
        self._operation_metadata = metadata

    def list_device_profiles(self) -> tuple[ModbusDeviceProfile, ...]:
        return tuple(
            _profile_from_record(record)
            for record in self._repository.list_modbus_device_profiles()
        )

    def delete_legacy_port_profiles(self) -> int:
        if self._device is not None:
            return 0
        deleted = self._repository.delete_legacy_modbus_device_profiles()
        if (
            self._selected_profile is not None
            and self._selected_profile.device_id.lower().startswith("modbus:")
        ):
            self._selected_profile = None
            self._profile_id = None
        return deleted

    def get_device_profile(self, device_id: str) -> ModbusDeviceProfile | None:
        record = self._repository.get_modbus_device_profile(device_id)
        if record is None:
            return None
        return _profile_from_record(record)

    def select_device_profile(self, device_id: str) -> ModbusDeviceProfile:
        if self._device is not None:
            raise RuntimeError("Disconnect before changing the device profile.")
        profile = self.get_device_profile(device_id)
        if profile is None:
            raise ValueError(f"Unknown Modbus device profile: {device_id}")
        self._selected_profile = profile
        self._profile_id = f"profile:{profile.device_id}"
        self._operation_metadata = profile.metadata
        if profile.register_map is not None:
            self._register_map = profile.register_map
        return profile

    def save_device_profile(
        self,
        *,
        device_id: str,
        metadata: ModbusOperationMetadata | None = None,
        register_map: ModbusRegisterMap | None = None,
        connection_settings: ModbusConnectionSettings | None = None,
        display_name: str | None = None,
        notes: str | None = None,
        select: bool = True,
    ) -> ModbusDeviceProfile:
        if self._device is not None:
            raise RuntimeError("Disconnect before saving a device profile.")
        normalized_device_id = _validate_modbus_device_id(device_id)
        existing = self._repository.get_modbus_device_profile(normalized_device_id)
        metadata = metadata or self._operation_metadata
        register_map = register_map or self._register_map
        connection_payload = (
            _connection_settings_to_payload(connection_settings)
            if connection_settings is not None
            else (
                dict(existing.connection_settings)
                if existing is not None
                else {}
            )
        )
        self._repository.save_device(
            DeviceRecord(
                device_id=normalized_device_id,
                device_type=DeviceType.MODBUS_RTU.value,
                model=metadata.device_model or None,
                connection_metadata={
                    "tube_model": metadata.tube_model,
                    "transmitter_model": metadata.transmitter_model,
                    "profile_created_from": "modbus_module",
                },
            )
        )
        self._repository.save_modbus_device_profile(
            ModbusDeviceProfileRecord(
                profile_id=(
                    existing.profile_id
                    if existing is not None
                    else f"profile:{normalized_device_id}"
                ),
                device_id=normalized_device_id,
                display_name=display_name
                if display_name not in (None, "")
                else (existing.display_name if existing is not None else None),
                device_model=metadata.device_model or None,
                tube_model=metadata.tube_model or None,
                transmitter_model=metadata.transmitter_model or None,
                connection_settings=connection_payload,
                register_map=_register_map_payload(register_map),
                notes=notes if notes is not None else (existing.notes if existing else None),
                created_at=existing.created_at if existing is not None else None,
            )
        )
        saved = self.get_device_profile(normalized_device_id)
        if saved is None:
            raise RuntimeError(f"Failed to save Modbus device profile: {normalized_device_id}")
        if select:
            self._selected_profile = saved
            self._profile_id = f"profile:{saved.device_id}"
            self._operation_metadata = saved.metadata
            if saved.register_map is not None:
                self._register_map = saved.register_map
        return saved

    def delete_device_profile(self, device_id: str) -> bool:
        if self._device is not None:
            raise RuntimeError("Disconnect before deleting a device profile.")
        normalized_device_id = str(device_id).strip()
        if not normalized_device_id:
            raise ValueError("Device ID is required.")
        deleted = self._repository.delete_modbus_device_profile(normalized_device_id)
        if (
            self._selected_profile is not None
            and self._selected_profile.device_id == normalized_device_id
        ):
            self._selected_profile = None
            self._profile_id = None
        return deleted

    def set_frame_logger(self, logger: FrameLogger | None) -> None:
        self._frame_logger = logger

    def connect(self, settings: ModbusConnectionSettings) -> ModbusModuleStatus:
        if self._selected_profile is None:
            raise RuntimeError("Create or select a device profile before connecting.")
        serial_config = settings.serial_config()
        transport = (
            self._transport_factory(serial_config)
            if self._transport_factory is not None
            else PymodbusSerialTransport(serial_config)
        )
        if self._frame_logger is not None and transport is not None:
            transport = _FrameLoggingTransport(transport, self._frame_logger)
        device = ModbusRtuFlowmeterDevice(
            serial_config,
            self._register_map,
            transport=transport,
        )
        device.connect()
        self._device = device
        self._device_id = self._selected_profile.device_id
        self._identity = DeviceIdentity(
            device_id=self._device_id,
            device_type=DeviceType.MODBUS_RTU,
            model=self._selected_profile.device_model or None,
            protocol_address=str(settings.unit_id),
            metadata={
                "port": settings.port,
                "unit_id": settings.unit_id,
                "register_map": self._register_map.name,
                "register_map_version": self._register_map.version,
                "order": settings.order,
            },
        )
        self._ensure_modbus_device_profile(settings)
        self._start_modbus_test_session()
        return self.status

    def disconnect(self) -> ModbusModuleStatus:
        self._finish_modbus_test_session(status="closed")
        if self._device is not None:
            self._device.disconnect()
        self._device = None
        self._device_id = None
        self._identity = None
        self._test_session_id = None
        self._profile_id = None
        return self.status

    def sample_variables(
        self,
        variable_names: tuple[str, ...] = (
            "mass_acc",
            "delta_t",
            "zero_offset",
            "k_factor",
            "low_threshold",
        ),
    ) -> ModbusVariableSampleResult:
        device = self._require_device()
        identity = self._require_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )
        samples: list[VariableSample] = []
        errors: list[str] = []
        for variable_name in variable_names:
            try:
                sample = _sample_one_variable(
                    device,
                    repository=self._repository,
                    identity=identity,
                    variable_name=variable_name,
                )
            except Exception as exc:
                errors.append(f"{variable_name}: {exc}")
                continue
            samples.append(sample)
        return ModbusVariableSampleResult(samples=tuple(samples), errors=tuple(errors))

    def read_variables(
        self,
        variable_names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
    ) -> ModbusVariableSampleResult:
        """Read selected variables for live display."""

        device = self._require_device()
        identity = self._require_identity()
        if merge_adjacent:
            try:
                parameters = _read_selected_parameters(
                    device,
                    variable_names,
                    merge_adjacent=True,
                )
                return _samples_from_parameters(parameters, identity, variable_names)
            except Exception as exc:
                return ModbusVariableSampleResult(
                    samples=(),
                    errors=(f"{', '.join(variable_names)}: {exc}",),
                )

        samples: list[VariableSample] = []
        errors: list[str] = []
        for variable_name in variable_names:
            try:
                parameters = _read_selected_parameters(
                    device,
                    (variable_name,),
                    merge_adjacent=False,
                )
                result = _samples_from_parameters(
                    parameters,
                    identity,
                    (variable_name,),
                )
            except Exception as exc:
                errors.append(f"{variable_name}: {exc}")
                continue
            samples.extend(result.samples)
            errors.extend(result.errors)
        return ModbusVariableSampleResult(samples=tuple(samples), errors=tuple(errors))

    def run_variable_sampling(
        self,
        variable_names: tuple[str, ...],
        *,
        poll_interval_s: float = 1.0,
        max_samples: int | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        status_callback: Callable[[str], None] | None = None,
        sample_callback: Callable[[datetime, dict[str, object]], None] | None = None,
        operation_metadata: ModbusOperationMetadata | None = None,
        notes: str = "",
    ) -> ModbusVariableSamplingRunResult:
        """Poll selected variables, save a wide sample CSV, and record test history."""

        variable_names = _unique_names(variable_names)
        if not variable_names:
            raise ValueError("Select at least one variable to sample.")
        if poll_interval_s <= 0:
            raise ValueError("Variable sampling poll interval must be positive.")
        if max_samples is not None and max_samples < 1:
            raise ValueError("Variable sampling max samples must be at least 1.")
        for variable_name in variable_names:
            self._register_map.by_name(variable_name)

        run_id = self._next_run_id()
        started_at = datetime.now(UTC)
        device = self._require_device()
        recording_device = _RawCurveRecordingDevice(
            device,
            register_map=self._register_map,
            default_phase="variable_sampling",
        )
        identity = self._require_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )

        samples: list[ModbusTrialSamplePoint] = []
        errors: list[str] = []
        seen_errors: set[str] = set()
        poll_count = 0
        _emit_status(
            status_callback,
            f"Sampling {len(variable_names)} variable(s)...",
        )
        while True:
            if cancel_requested is not None and cancel_requested() and samples:
                break
            poll_count += 1
            parameters, read_errors = _read_sampling_parameters(
                recording_device,
                variable_names,
            )
            for error in read_errors:
                if error not in seen_errors:
                    seen_errors.add(error)
                    errors.append(error)
                    _emit_status(
                        status_callback,
                        f"Variable sampling warning: {error}",
                    )

            captured_at = datetime.now(UTC)
            values: dict[str, object] = {}
            for variable_name in variable_names:
                try:
                    parameter = _find_parameter(parameters, variable_name)
                except Exception as exc:
                    errors.append(f"{variable_name}: {exc}")
                    continue
                values[variable_name] = _json_metric_value(parameter.value)
            if values:
                point = ModbusTrialSamplePoint(
                    captured_at=captured_at,
                    values=values,
                )
                samples.append(point)
                if sample_callback is not None:
                    sample_callback(point.captured_at, dict(point.values))
                _emit_status(
                    status_callback,
                    f"Sampled {len(samples)} point(s).",
                )
            if max_samples is not None and (
                len(samples) >= max_samples
                or (not values and poll_count >= max_samples)
            ):
                break
            if cancel_requested is not None and cancel_requested():
                break
            _sleep_poll_interval(
                poll_interval_s,
                cancel_requested=cancel_requested,
            )

        ended_at = datetime.now(UTC)
        sample_points = tuple(samples)
        if not sample_points:
            raise RuntimeError("Variable sampling stopped before any samples were recorded.")
        raw_curve = tuple(recording_device.points)
        raw_artifact_id = self._save_raw_curve_artifact(
            run_id=run_id,
            operation_type="modbus_variable_sampling",
            points=raw_curve,
            created_at=started_at,
        )
        flow_samples_artifact_id = self._save_flow_samples_artifact(
            run_id=run_id,
            operation_type="modbus_variable_sampling",
            flow_rate_parameter=variable_names[0],
            samples=sample_points,
            variable_names=variable_names,
            created_at=started_at,
            curve_type="variable_samples",
        )
        metadata = self._operation_metadata_snapshot(operation_metadata)
        units = {
            name: _register_unit(self._register_map, name)
            for name in variable_names
        }
        summary: dict[str, object] = {
            **metadata,
            "variable_names": list(variable_names),
            "sample_variable_names": list(variable_names),
            "flow_rate_parameter": variable_names[0],
            "poll_interval_s": poll_interval_s,
            "sample_count": len(sample_points),
            "flow_sample_count": len(sample_points),
            "flow_samples_artifact_id": flow_samples_artifact_id,
            "raw_artifact_id": raw_artifact_id,
            "units": units,
            "errors": errors,
            "notes": notes.strip(),
            "started_at": started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
        }
        status = "passed" if not errors else "captured"
        self._save_modbus_operation_attempt(
            attempt_id=f"{run_id}-VARIABLE-SAMPLING",
            operation_type="modbus_variable_sampling",
            status=status,
            run_id=run_id,
            started_at=started_at,
            ended_at=ended_at,
            raw_artifact_id=raw_artifact_id,
            summary=summary,
            metadata=operation_metadata,
            notes=notes.strip() or None,
        )
        return ModbusVariableSamplingRunResult(
            run_id=run_id,
            variable_names=variable_names,
            units=units,
            samples=sample_points,
            poll_interval_s=poll_interval_s,
            started_at=started_at,
            ended_at=ended_at,
            flow_samples_artifact_id=flow_samples_artifact_id,
            raw_artifact_id=raw_artifact_id,
            test_session_id=self._test_session_id,
            errors=tuple(errors),
            status=status,
            notes=notes.strip(),
        )

    def write_variable(self, variable_name: str, value: str) -> ParameterWriteResult:
        """Apply one operator-requested variable write through write guard."""

        device = self._require_device()
        register = self._register_map.by_name(variable_name)
        parameter = ConfigurationParameter(
            name=register.name,
            value=None,
            unit=register.unit,
            writable=register.writable,
            minimum=register.minimum,
            maximum=register.maximum,
            metadata={
                "register_kind": register.kind.value,
                "address": register.address,
                "word_count": register.word_count,
                "data_type": register.data_type.value,
                "source": "modbus_module_ui_map",
            },
        )
        write_method = getattr(device, "write_configuration_without_pre_read", None)
        write_device = (
            _NoPreReadWriteDevice(device)
            if callable(write_method)
            else device
        )
        decision = WriteGuardService(
            self._repository,
            write_capable_states=(
                "calibration_write_armed",
                "manual_modbus_write",
            ),
        ).evaluate_known_parameter(
            write_device,
            ParameterWriteRequest(
                parameter_name=variable_name,
                new_value=_coerce_write_value(register, value),
                mode=WriteMode.ARMED,
                actor=self._operator,
                workflow_state="manual_modbus_write",
                metadata={"source": "modbus_module_ui"},
            ),
            parameter,
        )
        return decision.result

    def run_zero_calibration(
        self,
        *,
        snapshot_variable_names: tuple[str, ...] = (),
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> ModbusZeroCalibrationResult:
        metadata = operation_metadata or self._operation_metadata
        run_id = self._next_run_id()
        parameter_names = _unique_names(
            (
                "zero_calibration_start",
                "zero_offset",
                "delta_t",
                *snapshot_variable_names,
            )
        )
        result = ZeroCalibrationWorkflow(self._repository).run(
            _SelectedParameterDevice(
                self._require_device(),
                identity=self._require_identity(),
                parameter_names=parameter_names,
            ),
            ZeroCalibrationConfig(
                run_id=run_id,
                operator=self._operator,
                snapshot_parameter_names=_unique_names(snapshot_variable_names),
                completion_wait_s=self._zero_calibration_wait_s,
                software_version=__version__,
            ),
        )
        self._attach_operation_metadata_to_run(result.run_id, metadata)
        raw_points = _zero_calibration_raw_points(result.record, result.pre_snapshot)
        raw_artifact_id = self._save_raw_curve_artifact(
            run_id=result.run_id,
            operation_type="zero_calibration",
            points=raw_points,
            created_at=result.record.before.captured_at,
        )
        summary = {
            "zero_offset_before": result.record.before.zero_offset,
            "zero_offset_after": result.record.after.zero_offset,
            "zero_offset_change": result.record.zero_offset_change,
            "delta_t_before": result.record.before.delta_t,
            "delta_t_after": result.record.after.delta_t,
            "delta_t_change": result.record.delta_t_change,
            "completed": result.record.completed,
            "audit_id": result.audit_id,
            "pre_snapshot": result.pre_snapshot,
            "raw_artifact_id": raw_artifact_id,
        }
        self._save_modbus_operation_attempt(
            attempt_id=f"{result.run_id}-ZERO-ATTEMPT",
            operation_type="zero_calibration",
            status="passed" if result.record.completed else "failed",
            run_id=result.run_id,
            started_at=result.record.before.captured_at,
            ended_at=result.record.after.captured_at,
            raw_artifact_id=raw_artifact_id,
            summary=summary,
            metadata=metadata,
        )
        return ModbusZeroCalibrationResult(
            run_id=run_id,
            record=result.record,
            audit_id=result.audit_id,
            pre_snapshot=result.pre_snapshot,
            pre_snapshot_captured_at=result.pre_snapshot_captured_at,
            raw_artifact_id=raw_artifact_id,
            test_session_id=self._test_session_id,
        )

    def capture_k_factor_simple_trial(
        self,
        *,
        snapshot_variable_names: tuple[str, ...] = (),
        flow_rate_parameter: str = "mass_rate",
        flow_acc_parameter: str = "mass_acc",
        k_factor_parameter: str = "k_factor",
        poll_interval_s: float = 1.0,
        nonzero_threshold: float = 0.0,
        post_start_sample_s: float | None = None,
        post_stop_delay_s: float | None = None,
        max_wait_start_polls: int = 600,
        max_wait_stop_polls: int = 600,
        cancel_requested: Callable[[], bool] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> ModbusKFactorSimpleCapture:
        run_id = self._next_run_id()
        device = self._require_device()
        recording_device = _RawCurveRecordingDevice(
            device,
            register_map=self._register_map,
            default_phase="k_factor_capture",
        )
        identity = self._require_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )
        _emit_status(status_callback, "Reading selected variables before this trial...")
        recording_device.set_phase("pre_snapshot")
        pre_snapshot, pre_snapshot_captured_at = _pre_calibration_snapshot(
            recording_device,
            _unique_names(snapshot_variable_names),
        )
        recording_device.set_phase("before_segment")
        before_parameters = _read_selected_parameters(
            recording_device,
            (flow_acc_parameter, k_factor_parameter),
            merge_adjacent=False,
        )
        mass_acc_before = float(_find_parameter(before_parameters, flow_acc_parameter).value)
        current_k_factor = float(_find_parameter(before_parameters, k_factor_parameter).value)
        _emit_status(
            status_callback,
            "Selected variables read. Start this trial when the device flow is ready.",
        )
        recording_device.set_phase("flow_segment")
        segment = capture_flow_segment(
            recording_device,
            FlowSegmentCaptureConfig(
                flow_rate_parameter=flow_rate_parameter,
                poll_interval_s=poll_interval_s,
                nonzero_threshold=nonzero_threshold,
                post_start_sample_s=self._k_factor_post_start_sample_s
                if post_start_sample_s is None
                else post_start_sample_s,
                post_stop_delay_s=self._k_factor_post_stop_delay_s
                if post_stop_delay_s is None
                else post_stop_delay_s,
                max_wait_start_polls=max_wait_start_polls,
                max_wait_stop_polls=max_wait_stop_polls,
                cancel_message="K factor capture canceled.",
                cancel_requested=cancel_requested,
            ),
        )
        _emit_status(status_callback, "Flow segment captured. Reading ending variables...")
        recording_device.set_phase("after_segment")
        after_parameters = _read_selected_parameters(
            recording_device,
            (flow_acc_parameter,),
            merge_adjacent=False,
        )
        mass_acc_after = float(_find_parameter(after_parameters, flow_acc_parameter).value)
        raw_curve = tuple(recording_device.points)
        raw_artifact_id = self._save_raw_curve_artifact(
            run_id=run_id,
            operation_type="k_factor_calibration",
            points=raw_curve,
            created_at=segment.started_at,
        )
        self._save_modbus_operation_attempt(
            attempt_id=f"{run_id}-KFACTOR-CAPTURE",
            operation_type="k_factor_calibration_capture",
            status="captured",
            run_id=run_id,
            started_at=segment.started_at,
            ended_at=segment.ended_at,
            raw_artifact_id=raw_artifact_id,
            summary={
                "flow_rate_parameter": flow_rate_parameter,
                "flow_acc_parameter": flow_acc_parameter,
                "k_factor_parameter": k_factor_parameter,
                "mass_acc_before": mass_acc_before,
                "mass_acc_after": mass_acc_after,
                "measured_mass_delta": mass_acc_after - mass_acc_before,
                "current_k_factor": current_k_factor,
                "instant_flow": segment.instant_flow,
                "duration_s": segment.duration_s,
                "raw_artifact_id": raw_artifact_id,
            },
        )
        return ModbusKFactorSimpleCapture(
            run_id=run_id,
            flow_rate_parameter=flow_rate_parameter,
            flow_acc_parameter=flow_acc_parameter,
            k_factor_parameter=k_factor_parameter,
            pre_snapshot=pre_snapshot,
            pre_snapshot_captured_at=pre_snapshot_captured_at,
            mass_acc_before=mass_acc_before,
            mass_acc_after=mass_acc_after,
            current_k_factor=current_k_factor,
            segment=segment,
            poll_interval_s=poll_interval_s,
            raw_curve=raw_curve,
            raw_artifact_id=raw_artifact_id,
            test_session_id=self._start_modbus_test_session(),
        )

    def calculate_k_factor_simple_result(
        self,
        capture: ModbusKFactorSimpleCapture,
        *,
        standard_mass: float,
        save_history: bool = True,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> ModbusKFactorSimpleResult:
        if standard_mass <= 0:
            raise ValueError("K factor calibration requires positive standard mass.")
        calibration = calculate_k_factor(
            KFactorCalibrationInput(
                mass_acc_before=capture.mass_acc_before,
                mass_acc_after=capture.mass_acc_after,
                standard_mass=standard_mass,
                current_k_factor=capture.current_k_factor,
            )
        )
        mean_flow = (
            calibration.measured_mass_delta / capture.segment.duration_s
            if capture.segment.duration_s > 0
            else 0.0
        )
        result = ModbusKFactorSimpleResult(
            run_id=capture.run_id,
            flow_rate_parameter=capture.flow_rate_parameter,
            flow_acc_parameter=capture.flow_acc_parameter,
            k_factor_parameter=capture.k_factor_parameter,
            pre_snapshot=capture.pre_snapshot,
            pre_snapshot_captured_at=capture.pre_snapshot_captured_at,
            mass_acc_before=capture.mass_acc_before,
            mass_acc_after=capture.mass_acc_after,
            standard_mass=standard_mass,
            current_k_factor=capture.current_k_factor,
            corrected_k_factor=calibration.corrected_k_factor,
            measured_mass_delta=calibration.measured_mass_delta,
            mean_flow=mean_flow,
            instant_flow=capture.segment.instant_flow,
            flow_started_at=capture.segment.started_at,
            flow_instant_at=capture.segment.instant_flow_at,
            flow_ended_at=capture.segment.ended_at,
            poll_interval_s=capture.poll_interval_s,
            flow_rate_source=capture.segment.flow_rate_source,
            history_saved=save_history,
            operation_metadata=operation_metadata or self._operation_metadata,
            raw_artifact_id=capture.raw_artifact_id,
            test_session_id=capture.test_session_id,
        )
        if save_history:
            self._save_k_factor_simple_history(
                result,
                status=RunStatus.PASSED,
                operation_metadata=operation_metadata,
            )
        return result

    def apply_k_factor_simple_result(
        self,
        result: ModbusKFactorSimpleResult,
    ) -> ModbusKFactorSimpleResult:
        device = self._require_device()
        register = self._register_map.by_name(result.k_factor_parameter)
        parameter = ConfigurationParameter(
            name=register.name,
            value=result.current_k_factor,
            unit=register.unit,
            writable=register.writable,
            minimum=register.minimum,
            maximum=register.maximum,
            metadata={
                "register_kind": register.kind.value,
                "address": register.address,
                "word_count": register.word_count,
                "data_type": register.data_type.value,
                "source": "k_factor_simple_calibration",
            },
        )
        write_method = getattr(device, "write_configuration_without_pre_read", None)
        write_device = (
            _NoPreReadWriteDevice(device)
            if callable(write_method)
            else device
        )
        decision = WriteGuardService(
            self._repository,
            write_capable_states=("calibration_write_armed",),
        ).evaluate_known_parameter(
            write_device,
            ParameterWriteRequest(
                parameter_name=result.k_factor_parameter,
                new_value=result.corrected_k_factor,
                mode=WriteMode.ARMED,
                actor=self._operator,
                workflow_state="calibration_write_armed",
                run_id=result.run_id if result.history_saved else None,
                metadata={"calibration": "k_factor_simple"},
            ),
            parameter,
        )
        readback = None
        verified = False
        if decision.result.status.value == "applied":
            parameters = _read_selected_parameters(
                device,
                (result.k_factor_parameter,),
                merge_adjacent=False,
            )
            readback = float(_find_parameter(parameters, result.k_factor_parameter).value)
            verified = abs(readback - result.corrected_k_factor) <= max(
                1e-9,
                abs(result.corrected_k_factor) * 1e-6,
            )
        updated = ModbusKFactorSimpleResult(
            run_id=result.run_id,
            flow_rate_parameter=result.flow_rate_parameter,
            flow_acc_parameter=result.flow_acc_parameter,
            k_factor_parameter=result.k_factor_parameter,
            pre_snapshot=result.pre_snapshot,
            pre_snapshot_captured_at=result.pre_snapshot_captured_at,
            mass_acc_before=result.mass_acc_before,
            mass_acc_after=result.mass_acc_after,
            standard_mass=result.standard_mass,
            current_k_factor=result.current_k_factor,
            corrected_k_factor=result.corrected_k_factor,
            measured_mass_delta=result.measured_mass_delta,
            mean_flow=result.mean_flow,
            instant_flow=result.instant_flow,
            flow_started_at=result.flow_started_at,
            flow_instant_at=result.flow_instant_at,
            flow_ended_at=result.flow_ended_at,
            poll_interval_s=result.poll_interval_s,
            flow_rate_source=result.flow_rate_source,
            history_saved=result.history_saved,
            write_requested=True,
            write_status=decision.result.status.value,
            write_verified=verified,
            readback_k_factor=readback,
            audit_id=decision.audit_id,
            operation_metadata=result.operation_metadata,
            raw_artifact_id=result.raw_artifact_id,
            test_session_id=result.test_session_id,
        )
        if updated.history_saved:
            self._save_k_factor_simple_history(
                updated,
                status=RunStatus.PASSED if verified else RunStatus.FAILED,
                operation_metadata=updated.operation_metadata,
            )
        return updated

    def capture_repeatability_simple_trial(
        self,
        *,
        run_id: str | None = None,
        capture_started_at: datetime | None = None,
        flow_point: float,
        trial_index: int,
        snapshot_variable_names: tuple[str, ...] = (),
        post_snapshot_variable_names: tuple[str, ...] = (),
        flow_rate_parameter: str = "mass_rate",
        flow_acc_parameter: str = "mass_acc",
        k_factor_parameter: str = "k_factor",
        poll_interval_s: float = 1.0,
        nonzero_threshold: float = 0.0,
        post_start_sample_s: float | None = None,
        post_stop_delay_s: float | None = None,
        max_wait_start_polls: int = 600,
        max_wait_stop_polls: int = 600,
        capture_snapshot: bool = True,
        cancel_requested: Callable[[], bool] | None = None,
        status_callback: Callable[[str], None] | None = None,
        flow_sample_callback: Callable[[datetime, float], None] | None = None,
        sample_callback: Callable[[datetime, dict[str, object]], None] | None = None,
        sample_variable_names: tuple[str, ...] = (),
        record_flow_samples: bool = False,
    ) -> ModbusRepeatabilitySimpleCapture:
        if trial_index < 1:
            raise ValueError("Repeatability trial index must be at least 1.")
        if poll_interval_s <= 0:
            raise ValueError("Repeatability poll interval must be positive.")
        capture_started_at = capture_started_at or datetime.now(UTC)
        capture_run_id = run_id or self._next_run_id()
        device = self._require_device()
        recording_device = _RawCurveRecordingDevice(
            device,
            register_map=self._register_map,
            default_phase="repeatability_trial",
        )
        identity = self._require_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )
        _emit_status(status_callback, "Reading selected variables before this trial...")
        snapshot_names = _unique_names(
            snapshot_variable_names or post_snapshot_variable_names
        )
        if capture_snapshot:
            recording_device.set_phase("pre_snapshot")
            pre_snapshot, pre_snapshot_captured_at = _pre_calibration_snapshot(
                recording_device,
                snapshot_names,
            )
        else:
            pre_snapshot, pre_snapshot_captured_at = {}, None
        recording_device.set_phase("before_segment")
        before_parameters = _read_selected_parameters(
            recording_device,
            _unique_names((flow_acc_parameter, k_factor_parameter)),
            merge_adjacent=False,
        )
        mass_acc_before = float(_find_parameter(before_parameters, flow_acc_parameter).value)
        original_k_factor = float(_find_parameter(before_parameters, k_factor_parameter).value)
        _emit_status(
            status_callback,
            "Selected variables read. Start this trial when the device flow is ready.",
        )
        flow_samples: list[ModbusFlowSamplePoint] = []
        trial_samples: list[ModbusTrialSamplePoint] = []
        trial_sample_names = _unique_names(
            (flow_rate_parameter, *sample_variable_names)
        )
        should_record_flow_samples = (
            record_flow_samples
            or flow_sample_callback is not None
            or sample_callback is not None
        )

        def record_flow_sample(
            captured_at: datetime,
            values: dict[str, object],
        ) -> None:
            flow_value = float(values[flow_rate_parameter])
            point = ModbusFlowSamplePoint(
                captured_at=captured_at,
                value=flow_value,
            )
            flow_samples.append(point)
            trial_point = ModbusTrialSamplePoint(
                captured_at=captured_at,
                values={
                    name: _json_metric_value(values[name])
                    for name in trial_sample_names
                    if name in values
                },
            )
            trial_samples.append(trial_point)
            if flow_sample_callback is not None:
                flow_sample_callback(point.captured_at, point.value)
            if sample_callback is not None:
                sample_callback(trial_point.captured_at, dict(trial_point.values))

        recording_device.set_phase("flow_segment")
        segment = capture_flow_segment(
            recording_device,
            FlowSegmentCaptureConfig(
                flow_rate_parameter=flow_rate_parameter,
                poll_interval_s=poll_interval_s,
                nonzero_threshold=nonzero_threshold,
                post_start_sample_s=self._k_factor_post_start_sample_s
                if post_start_sample_s is None
                else post_start_sample_s,
                post_stop_delay_s=self._k_factor_post_stop_delay_s
                if post_stop_delay_s is None
                else post_stop_delay_s,
                max_wait_start_polls=max_wait_start_polls,
                max_wait_stop_polls=max_wait_stop_polls,
                cancel_message="Repeatability capture canceled.",
                cancel_requested=cancel_requested,
                sample_variables=tuple(
                    name for name in trial_sample_names if name != flow_rate_parameter
                )
                if should_record_flow_samples
                else (),
                multi_sample_callback=record_flow_sample
                if should_record_flow_samples
                else None,
            ),
        )
        _emit_status(status_callback, "Flow segment captured. Reading ending variables...")
        recording_device.set_phase("after_segment")
        after_parameters = _read_selected_parameters(
            recording_device,
            (flow_acc_parameter,),
            merge_adjacent=False,
        )
        mass_acc_after = float(_find_parameter(after_parameters, flow_acc_parameter).value)
        if snapshot_names:
            recording_device.set_phase("post_snapshot")
            post_snapshot, post_snapshot_captured_at = _pre_calibration_snapshot(
                recording_device,
                snapshot_names,
            )
        else:
            post_snapshot, post_snapshot_captured_at = {}, None
        raw_curve = tuple(recording_device.points)
        raw_artifact_id = self._save_raw_curve_artifact(
            run_id=capture_run_id,
            operation_type="manual_error_repeatability_trial",
            points=raw_curve,
            created_at=segment.started_at,
        )
        flow_sample_points = tuple(flow_samples)
        trial_sample_points = tuple(trial_samples)
        flow_samples_artifact_id = (
            self._save_flow_samples_artifact(
                run_id=capture_run_id,
                operation_type="manual_error_repeatability_trial",
                flow_rate_parameter=flow_rate_parameter,
                samples=trial_sample_points,
                variable_names=trial_sample_names,
                created_at=capture_started_at,
            )
            if record_flow_samples
            else None
        )
        return ModbusRepeatabilitySimpleCapture(
            run_id=capture_run_id,
            capture_started_at=capture_started_at,
            flow_point=flow_point,
            trial_index=trial_index,
            flow_rate_parameter=flow_rate_parameter,
            flow_acc_parameter=flow_acc_parameter,
            k_factor_parameter=k_factor_parameter,
            original_k_factor=original_k_factor,
            pre_snapshot=pre_snapshot,
            pre_snapshot_captured_at=pre_snapshot_captured_at,
            mass_acc_before=mass_acc_before,
            mass_acc_after=mass_acc_after,
            segment=segment,
            poll_interval_s=poll_interval_s,
            post_snapshot=post_snapshot,
            post_snapshot_captured_at=post_snapshot_captured_at,
            raw_curve=raw_curve,
            raw_artifact_id=raw_artifact_id,
            test_session_id=self._start_modbus_test_session(),
            flow_samples=flow_sample_points,
            trial_samples=trial_sample_points,
            trial_sample_variable_names=trial_sample_names,
            flow_samples_artifact_id=flow_samples_artifact_id,
        )

    def calculate_repeatability_simple_trial(
        self,
        capture: ModbusRepeatabilitySimpleCapture,
        *,
        standard_mass: float,
        notes: str = "",
    ) -> ModbusRepeatabilitySimpleTrialResult:
        if standard_mass <= 0:
            raise ValueError("Repeatability test requires positive standard mass.")
        measured_mass_delta = capture.measured_mass_delta
        percent_error = (measured_mass_delta - standard_mass) / standard_mass * 100.0
        trial_notes = notes.strip()
        trial = ModbusRepeatabilitySimpleTrialResult(
            run_id=capture.run_id,
            capture_started_at=capture.capture_started_at,
            flow_point=capture.flow_point,
            trial_index=capture.trial_index,
            flow_rate_parameter=capture.flow_rate_parameter,
            flow_acc_parameter=capture.flow_acc_parameter,
            k_factor_parameter=capture.k_factor_parameter,
            original_k_factor=capture.original_k_factor,
            pre_snapshot=capture.pre_snapshot,
            pre_snapshot_captured_at=capture.pre_snapshot_captured_at,
            post_snapshot=capture.post_snapshot,
            post_snapshot_captured_at=capture.post_snapshot_captured_at,
            mass_acc_before=capture.mass_acc_before,
            mass_acc_after=capture.mass_acc_after,
            measured_mass_delta=measured_mass_delta,
            standard_mass=standard_mass,
            percent_error=percent_error,
            mean_flow=capture.mean_flow,
            instant_flow=capture.segment.instant_flow,
            flow_started_at=capture.segment.started_at,
            flow_instant_at=capture.segment.instant_flow_at,
            flow_ended_at=capture.segment.ended_at,
            poll_interval_s=capture.poll_interval_s,
            flow_rate_source=capture.segment.flow_rate_source,
            raw_artifact_id=capture.raw_artifact_id,
            test_session_id=capture.test_session_id,
            trial_status="accepted",
            notes=trial_notes,
            flow_samples=capture.flow_samples,
            trial_samples=capture.trial_samples,
            trial_sample_variable_names=capture.trial_sample_variable_names,
            flow_samples_artifact_id=capture.flow_samples_artifact_id,
            recorded_flow_sample_count=len(capture.flow_samples),
        )
        self._save_modbus_trial_record(trial)
        return trial

    def summarize_repeatability_flow_point(
        self,
        trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        *,
        flow_point: float | None = None,
    ) -> ModbusRepeatabilityFlowSummary:
        if not trials:
            raise ValueError("Repeatability summary requires at least one trial.")
        selected = tuple(
            trial
            for trial in trials
            if flow_point is None or trial.flow_point == flow_point
        )
        if not selected:
            raise ValueError("Repeatability summary requires trials for the flow point.")
        errors = tuple(trial.percent_error for trial in selected)
        return ModbusRepeatabilityFlowSummary(
            flow_point=selected[0].flow_point,
            trial_count=len(selected),
            mean_percent_error=sum(errors) / len(errors),
            max_abs_percent_error=max(abs(error) for error in errors),
            repeatability_stddev_percent=_sample_stddev(errors),
            trial_errors=errors,
        )

    def calculate_repeatability_simple_result(
        self,
        trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        *,
        save_history: bool = True,
        mode: str = "three_point",
        expected_flow_point_count: int = 3,
        expected_trials_per_point: int = 3,
        require_complete: bool = True,
        operation_metadata: ModbusOperationMetadata | None = None,
        notes: str = "",
    ) -> ModbusRepeatabilitySimpleResult:
        if not trials:
            raise ValueError("Repeatability test requires at least one trial.")
        if expected_flow_point_count < 1:
            raise ValueError("Repeatability test requires at least one flow point.")
        if expected_trials_per_point < 1:
            raise ValueError("Repeatability test requires at least one trial per point.")
        flow_points = {trial.flow_point for trial in trials}
        expected_total = expected_flow_point_count * expected_trials_per_point
        if require_complete and len(trials) < expected_total:
            raise ValueError(
                "Repeatability test requires "
                f"at least {expected_total} trials for {expected_flow_point_count} flow point(s)."
            )
        if len(flow_points) != expected_flow_point_count:
            raise ValueError(
                "Repeatability test requires "
                f"{expected_flow_point_count} flow point(s)."
            )
        if require_complete:
            for flow_point in flow_points:
                trial_count = sum(
                    1 for trial in trials if trial.flow_point == flow_point
                )
                if trial_count < expected_trials_per_point:
                    raise ValueError(
                        "Repeatability test requires at least "
                        f"{expected_trials_per_point} trials for flow point {flow_point:g}."
                    )
        run_ids = {trial.run_id for trial in trials}
        if len(run_ids) != 1:
            raise ValueError("Repeatability trials must belong to one run.")
        flow_rate_parameters = {trial.flow_rate_parameter for trial in trials}
        flow_acc_parameters = {trial.flow_acc_parameter for trial in trials}
        if len(flow_rate_parameters) != 1 or len(flow_acc_parameters) != 1:
            raise ValueError("Repeatability trials must use one variable configuration.")
        repeatability_trials = tuple(
            RepeatabilityTrial(
                flow_point=trial.flow_point,
                trial_index=trial.trial_index,
                mass_acc_before=trial.mass_acc_before,
                mass_acc_after=trial.mass_acc_after,
                standard_mass=trial.standard_mass,
            )
            for trial in trials
        )
        analysis = analyze_repeatability(
            repeatability_trials,
            expected_trials_per_point=expected_trials_per_point,
        )
        first_snapshot_trial = next((trial for trial in trials if trial.pre_snapshot), None)
        result_notes = notes.strip() or next(
            (trial.notes for trial in trials if trial.notes),
            "",
        )
        result = ModbusRepeatabilitySimpleResult(
            run_id=trials[0].run_id,
            flow_rate_parameter=trials[0].flow_rate_parameter,
            flow_acc_parameter=trials[0].flow_acc_parameter,
            poll_interval_s=trials[0].poll_interval_s,
            pre_snapshot=first_snapshot_trial.pre_snapshot
            if first_snapshot_trial is not None
            else {},
            pre_snapshot_captured_at=first_snapshot_trial.pre_snapshot_captured_at
            if first_snapshot_trial is not None
            else None,
            trials=trials,
            analysis=analysis,
            history_saved=save_history,
            mode=mode,
            expected_trials_per_point=expected_trials_per_point,
            test_session_id=trials[0].test_session_id,
            notes=result_notes,
        )
        if save_history:
            self._save_repeatability_simple_history(
                result,
                status=RunStatus.PASSED,
                operation_metadata=operation_metadata,
            )
        return result

    def save_repeatability_flow_summary_history(
        self,
        trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        *,
        flow_point: float,
        mode: str = "three_point",
        save_history: bool = True,
        operation_metadata: ModbusOperationMetadata | None = None,
        notes: str = "",
    ) -> ModbusRepeatabilitySimpleResult:
        """Persist one operator-selected repeatability/error calculation."""

        selected = tuple(trial for trial in trials if trial.flow_point == flow_point)
        if not selected:
            raise ValueError("Repeatability summary requires trials for the flow point.")
        return self.calculate_repeatability_simple_result(
            selected,
            save_history=save_history,
            mode=mode,
            expected_flow_point_count=1,
            expected_trials_per_point=len(selected),
            require_complete=False,
            operation_metadata=operation_metadata,
            notes=notes,
        )

    def calculate_repeatability_final_k(
        self,
        selected_trials_by_flow: dict[
            float,
            tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        ],
        *,
        original_k_factor: float | None = None,
        run_id: str | None = None,
        allow_new_run_id: bool = False,
        require_single_operation: bool = True,
        save_history: bool = True,
        operation_metadata: ModbusOperationMetadata | None = None,
        notes: str = "",
    ) -> dict[str, object]:
        """Calculate the operator-selected final K preview from three flow points."""

        if len(selected_trials_by_flow) != 3:
            raise ValueError("Final K calculation requires three selected flow points.")
        selected_groups: list[tuple[float, tuple[ModbusRepeatabilitySimpleTrialResult, ...]]] = []
        for flow_point in sorted(selected_trials_by_flow):
            trials = tuple(selected_trials_by_flow[flow_point])
            if len(trials) != 3:
                raise ValueError(
                    "Final K calculation requires three trials for "
                    f"flow point {flow_point:g}."
                )
            if any(trial.flow_point != flow_point for trial in trials):
                raise ValueError("Selected repeatability trials do not match their flow point.")
            trial_indexes = tuple(trial.trial_index for trial in trials)
            expected_indexes = tuple(range(trial_indexes[0], trial_indexes[0] + 3))
            if trial_indexes != expected_indexes:
                raise ValueError("Selected repeatability trials must be consecutive.")
            selected_groups.append((flow_point, trials))

        all_trials = tuple(
            trial
            for _flow_point, trials in selected_groups
            for trial in trials
        )
        run_ids = {trial.run_id for trial in all_trials}
        if require_single_operation and len(run_ids) != 1:
            raise ValueError("Final K calculation requires trials from one operation.")
        resolved_run_id = run_id or next(iter(run_ids))
        if resolved_run_id not in run_ids and not allow_new_run_id:
            raise ValueError("Final K run ID must match the selected trials.")
        flow_rate_parameters = {trial.flow_rate_parameter for trial in all_trials}
        flow_acc_parameters = {trial.flow_acc_parameter for trial in all_trials}
        if len(flow_rate_parameters) != 1 or len(flow_acc_parameters) != 1:
            raise ValueError("Final K calculation requires one variable configuration.")
        k_factor_parameters = {trial.k_factor_parameter for trial in all_trials}
        if len(k_factor_parameters) != 1:
            raise ValueError("Final K calculation requires one K factor variable.")
        original_k_values = {trial.original_k_factor for trial in all_trials}
        if len(original_k_values) != 1:
            raise ValueError("Final K calculation requires one original K value.")
        captured_original_k = next(iter(original_k_values))
        if original_k_factor is None:
            original_k_factor = captured_original_k
        elif original_k_factor != captured_original_k:
            raise ValueError("Final K original K must match the selected trials.")
        if original_k_factor <= 0:
            raise ValueError("Final K calculation requires a positive original K factor.")

        flow_rows: list[dict[str, object]] = []
        measurement_errors = []
        for flow_point, trials in selected_groups:
            errors = tuple(trial.percent_error for trial in trials)
            measurement_error = sum(errors) / len(errors)
            measurement_errors.append(measurement_error)
            summary = self.summarize_repeatability_flow_point(
                trials,
                flow_point=flow_point,
            )
            flow_rows.append(
                {
                    "flow_point": flow_point,
                    "trial_indexes": [trial.trial_index for trial in trials],
                    "trial_errors_percent": list(errors),
                    "measurement_error_percent": measurement_error,
                    "repeatability_stddev_percent": (
                        summary.repeatability_stddev_percent
                    ),
                    "repeatability_mean_percent_error": (
                        summary.mean_percent_error
                    ),
                    "raw_artifact_ids": [
                        trial.raw_artifact_id
                        for trial in trials
                        if trial.raw_artifact_id is not None
                    ],
                    "flow_samples_artifact_ids": [
                        trial.flow_samples_artifact_id
                        for trial in trials
                        if trial.flow_samples_artifact_id is not None
                    ],
                }
            )

        average_error = (max(measurement_errors) + min(measurement_errors)) / 2.0
        intermediate_k_values = []
        for row in flow_rows:
            measurement_error = float(row["measurement_error_percent"])
            adjusted_error = measurement_error - average_error
            denominator = 1.0 + measurement_error / 100.0
            if denominator == 0:
                raise ValueError("Final K calculation produced a zero denominator.")
            intermediate_k = original_k_factor / denominator
            row["adjusted_error_percent"] = adjusted_error
            row["intermediate_k_factor"] = intermediate_k
            intermediate_k_values.append(intermediate_k)

        new_k_factor = (
            max(intermediate_k_values) + min(intermediate_k_values)
        ) / 2.0
        delta_k_factor = new_k_factor - original_k_factor
        source_started_at = min(trial.flow_started_at for trial in all_trials)
        source_ended_at = max(trial.flow_ended_at for trial in all_trials)
        calculated_at = datetime.now(UTC)
        metadata = self._operation_metadata_snapshot(operation_metadata)
        result_notes = notes.strip() or next(
            (trial.notes for trial in all_trials if trial.notes),
            "",
        )
        metrics: dict[str, object] = {
            **metadata,
            "run_id": resolved_run_id,
            "original_k_factor": original_k_factor,
            "average_error": average_error,
            "average_error_percent": average_error,
            "new_k_factor": new_k_factor,
            "delta_k_factor": delta_k_factor,
            "notes": result_notes,
            "flow_rate_parameter": all_trials[0].flow_rate_parameter,
            "flow_acc_parameter": all_trials[0].flow_acc_parameter,
            "k_factor_parameter": all_trials[0].k_factor_parameter,
            "selected_flow_point_count": len(flow_rows),
            "selected_trial_count": len(all_trials),
            "history_saved": save_history,
            "write_requested": False,
            "write_status": "not_requested",
            "write_verified": False,
            "readback_k_factor": None,
            "audit_id": None,
            "started_at": calculated_at.isoformat(),
            "ended_at": calculated_at.isoformat(),
            "source_trial_started_at": source_started_at.isoformat(),
            "source_trial_ended_at": source_ended_at.isoformat(),
            "flow_points": flow_rows,
            "trials": [
                {
                    "flow_point": trial.flow_point,
                    "trial_index": trial.trial_index,
                    "k_factor_parameter": trial.k_factor_parameter,
                    "original_k_factor": trial.original_k_factor,
                    "mass_acc_before": trial.mass_acc_before,
                    "mass_acc_after": trial.mass_acc_after,
                    "measured_mass_delta": trial.measured_mass_delta,
                    "standard_mass": trial.standard_mass,
                    "percent_error": trial.percent_error,
                    "mean_flow": trial.mean_flow,
                    "instant_flow": trial.instant_flow,
                    "trial_status": trial.trial_status,
                    "raw_artifact_id": trial.raw_artifact_id,
                    "flow_samples_artifact_id": trial.flow_samples_artifact_id,
                    "flow_sample_count": trial.flow_sample_count,
                    "trial_sample_variable_names": list(
                        trial.trial_sample_variable_names
                    ),
                    "test_session_id": trial.test_session_id,
                    "duration_s": trial.duration_s,
                    "flow_started_at": trial.flow_started_at.isoformat(),
                    "flow_instant_at": trial.flow_instant_at.isoformat(),
                    "flow_ended_at": trial.flow_ended_at.isoformat(),
                    "notes": trial.notes,
                }
                for trial in all_trials
            ],
        }
        for row in flow_rows:
            label = _flow_point_metric_label(float(row["flow_point"]))
            metrics[f"{label}_measurement_error_percent"] = row[
                "measurement_error_percent"
            ]
            metrics[f"{label}_adjusted_error_percent"] = row[
                "adjusted_error_percent"
            ]
            metrics[f"{label}_repeatability_stddev_percent"] = row[
                "repeatability_stddev_percent"
            ]
            metrics[f"{label}_intermediate_k_factor"] = row[
                "intermediate_k_factor"
            ]

        if save_history:
            self._save_repeatability_final_k_history(
                run_id=resolved_run_id,
                started_at=calculated_at,
                ended_at=calculated_at,
                metrics=metrics,
                input_artifact_ids=_repeatability_trial_artifact_ids(all_trials),
                operation_metadata=operation_metadata,
                notes=result_notes,
            )
        return metrics

    def list_repeatability_history_trials(
        self,
        device_id: str,
        *,
        accepted_only: bool = True,
    ) -> tuple[ModbusRepeatabilityHistoryTrial, ...]:
        """Return saved repeatability trials with their operation-attempt snapshots."""

        normalized_device_id = str(device_id).strip()
        if not normalized_device_id:
            raise ValueError("Device ID is required.")
        records = self._repository.list_modbus_trial_records(
            device_id=normalized_device_id,
            trial_status="accepted" if accepted_only else None,
        )
        attempts = {
            attempt.attempt_id: attempt
            for attempt in self._repository.list_modbus_operation_attempts(
                device_id=normalized_device_id,
                operation_type="manual_error_repeatability_trial",
            )
        }
        history_trials: list[ModbusRepeatabilityHistoryTrial] = []
        for record in records:
            attempt = attempts.get(record.attempt_id or "")
            pre_snapshot = (
                _history_summary_pre_snapshot(attempt.summary)
                if attempt is not None
                else {}
            )
            flow_started_at = record.flow_started_at or record.flow_instant_at
            flow_ended_at = record.flow_ended_at or record.flow_instant_at
            flow_instant_at = (
                record.flow_instant_at
                or flow_started_at
                or flow_ended_at
                or datetime.now(UTC)
            )
            if flow_started_at is None:
                flow_started_at = flow_instant_at
            if flow_ended_at is None:
                flow_ended_at = flow_instant_at
            summary = attempt.summary if attempt is not None else {}
            history_trials.append(
                ModbusRepeatabilityHistoryTrial(
                    trial=ModbusRepeatabilitySimpleTrialResult(
                        run_id=record.run_id or record.trial_id,
                        flow_point=record.flow_point,
                        trial_index=record.trial_index,
                        flow_rate_parameter=str(
                            pre_snapshot.get("flow_rate_parameter")
                            or summary.get("flow_rate_parameter")
                            or "mass_rate"
                        ),
                        flow_acc_parameter=str(
                            pre_snapshot.get("flow_acc_parameter")
                            or summary.get("flow_acc_parameter")
                            or "mass_acc"
                        ),
                        k_factor_parameter=(
                            record.k_factor_parameter
                            or str(summary.get("k_factor_parameter") or "")
                            or "k_factor"
                        ),
                        original_k_factor=_history_trial_original_k_factor(
                            record,
                            summary,
                        ),
                        pre_snapshot=pre_snapshot,
                        pre_snapshot_captured_at=_history_datetime(
                            summary.get("pre_snapshot_captured_at")
                        ),
                        post_snapshot=_history_dict(summary, "post_snapshot"),
                        post_snapshot_captured_at=_history_datetime(
                            summary.get("post_snapshot_captured_at")
                        ),
                        mass_acc_before=record.mass_acc_before or 0.0,
                        mass_acc_after=record.mass_acc_after or 0.0,
                        measured_mass_delta=record.measured_mass_delta or 0.0,
                        standard_mass=record.standard_mass or 0.0,
                        percent_error=record.percent_error or 0.0,
                        mean_flow=record.mean_flow or 0.0,
                        instant_flow=record.instant_flow or 0.0,
                        flow_started_at=flow_started_at,
                        flow_instant_at=flow_instant_at,
                        flow_ended_at=flow_ended_at,
                        poll_interval_s=float(summary.get("poll_interval_s", 0.0)),
                        raw_artifact_id=record.raw_artifact_id,
                        test_session_id=record.session_id,
                        trial_status=record.trial_status,
                        notes=record.notes or "",
                        capture_started_at=_history_datetime(
                            summary.get("capture_started_at")
                        ),
                        flow_samples_artifact_id=_history_str(
                            summary,
                            "flow_samples_artifact_id",
                        ),
                        trial_sample_variable_names=_history_str_tuple(
                            summary.get("trial_sample_variable_names")
                        ),
                        recorded_flow_sample_count=_history_optional_int(
                            summary.get("flow_sample_count")
                        )
                        or 0,
                    ),
                    attempt_id=record.attempt_id,
                    pre_snapshot=pre_snapshot,
                    device_metadata=dict(record.device_metadata),
                )
            )
        return tuple(
            sorted(
                history_trials,
                key=lambda item: (
                    item.trial.flow_point,
                    item.trial.trial_index,
                    item.trial.flow_started_at,
                    item.attempt_id or "",
                ),
            )
        )

    def validate_repeatability_analysis_snapshot_consistency(
        self,
        selected_trials_by_flow: dict[
            float,
            tuple[ModbusRepeatabilityHistoryTrial, ...],
        ],
        *,
        variable_names: tuple[str, ...] = ("zero_offset", "low_threshold"),
    ) -> None:
        """Validate old K and required pre-calibration snapshot variables match."""

        history_trials = tuple(
            history_trial
            for trials in selected_trials_by_flow.values()
            for history_trial in trials
        )
        if len(history_trials) != 9:
            raise ValueError("Device analysis requires exactly 9 selected trials.")
        _ensure_selected_history_trials_have_consistent_snapshot(
            history_trials,
            variable_names=variable_names,
        )

    def calculate_device_analysis_repeatability_preview(
        self,
        selected_trials_by_flow: dict[
            float,
            tuple[ModbusRepeatabilityHistoryTrial, ...],
        ],
        *,
        run_id: str | None = None,
    ) -> dict[str, object]:
        """Calculate current-device final-K preview without saving history."""

        self.validate_repeatability_analysis_snapshot_consistency(
            selected_trials_by_flow,
            variable_names=("zero_offset", "low_threshold"),
        )
        trial_groups = {
            flow_point: tuple(item.trial for item in trials)
            for flow_point, trials in selected_trials_by_flow.items()
        }
        metrics = self.calculate_repeatability_final_k(
            trial_groups,
            run_id=run_id or "PREVIEW-DEVICE-ANALYSIS",
            allow_new_run_id=True,
            require_single_operation=False,
            save_history=False,
            operation_metadata=self._operation_metadata,
        )
        source_trial_started_at = _datetime_from_metric(
            metrics.get("source_trial_started_at")
        )
        source_trial_ended_at = _datetime_from_metric(
            metrics.get("source_trial_ended_at")
        )
        source_run_ids = sorted(
            {
                history_trial.trial.run_id
                for trials in selected_trials_by_flow.values()
                for history_trial in trials
            }
        )
        return {
            **metrics,
            "source_repeatability_run_ids": source_run_ids,
            "source_trial_started_at": source_trial_started_at.isoformat()
            if source_trial_started_at is not None
            else None,
            "source_trial_ended_at": source_trial_ended_at.isoformat()
            if source_trial_ended_at is not None
            else None,
        }

    def calculate_device_analysis_repeatability_report(
        self,
        device_id: str,
        selected_trials_by_flow: dict[
            float,
            tuple[ModbusRepeatabilityHistoryTrial, ...],
        ],
        *,
        comparison_variable_names: tuple[str, ...] = (),
        save_history: bool = True,
    ) -> ModbusDeviceAnalysisReportResult:
        normalized_device_id = _validate_modbus_device_id(device_id)
        operation_started_at = datetime.now(UTC)
        comparison_names = _unique_names(
            (
                "zero_offset",
                "low_threshold",
                *comparison_variable_names,
            )
        )
        self.validate_repeatability_analysis_snapshot_consistency(
            selected_trials_by_flow,
            variable_names=("zero_offset", "low_threshold"),
        )
        run_id = self._next_run_id()
        metrics = self.calculate_device_analysis_repeatability_preview(
            selected_trials_by_flow,
            run_id=run_id,
        )
        source_run_ids = list(metrics.get("source_repeatability_run_ids", []))
        report_text = _device_analysis_repeatability_report_text(
            metrics=metrics,
            selected_trials_by_flow=selected_trials_by_flow,
            comparison_variable_names=comparison_names,
        )
        operation_ended_at = datetime.now(UTC)
        metrics = {
            **metrics,
            "started_at": operation_started_at.isoformat(),
            "ended_at": operation_ended_at.isoformat(),
        }
        report_artifact_id = None
        if save_history:
            metrics = dict(metrics)
            metrics.update(
                {
                    "run_id": run_id,
                    "device_id": normalized_device_id,
                    "analysis_source": "current_device_analysis",
                    "source_repeatability_run_ids": source_run_ids,
                    "report_text": report_text,
                    "comparison_variable_names": list(comparison_names),
                }
            )
            report_artifact_id = self._save_repeatability_final_k_history(
                run_id=run_id,
                started_at=operation_started_at,
                ended_at=operation_ended_at,
                metrics=metrics,
                input_artifact_ids=tuple(
                    str(value) for value in _final_k_artifact_ids(metrics)
                ),
                operation_metadata=self._operation_metadata,
                device_id=normalized_device_id,
                report_text=report_text,
            )
            metrics["report_artifact_id"] = report_artifact_id
        return ModbusDeviceAnalysisReportResult(
            run_id=run_id,
            metrics=metrics,
            report_text=report_text,
            report_artifact_id=report_artifact_id,
        )

    def apply_repeatability_final_k_result(
        self,
        metrics: dict[str, object],
        *,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> dict[str, object]:
        device = self._require_device()
        run_id = str(metrics.get("run_id") or "")
        if not run_id:
            raise ValueError("Final K write requires a saved final-K result.")
        k_factor_parameter = str(metrics.get("k_factor_parameter") or "")
        if not k_factor_parameter:
            raise ValueError("Final K write requires a K factor variable.")
        new_k_factor = float(metrics["new_k_factor"])
        original_k_factor = float(metrics["original_k_factor"])
        register = self._register_map.by_name(k_factor_parameter)
        parameter = ConfigurationParameter(
            name=register.name,
            value=original_k_factor,
            unit=register.unit,
            writable=register.writable,
            minimum=register.minimum,
            maximum=register.maximum,
            metadata={
                "register_kind": register.kind.value,
                "address": register.address,
                "word_count": register.word_count,
                "data_type": register.data_type.value,
                "source": "repeatability_final_k",
            },
        )
        write_method = getattr(device, "write_configuration_without_pre_read", None)
        write_device = (
            _NoPreReadWriteDevice(device)
            if callable(write_method)
            else device
        )
        decision = WriteGuardService(
            self._repository,
            write_capable_states=("calibration_write_armed",),
        ).evaluate_known_parameter(
            write_device,
            ParameterWriteRequest(
                parameter_name=k_factor_parameter,
                new_value=new_k_factor,
                mode=WriteMode.ARMED,
                actor=self._operator,
                workflow_state="calibration_write_armed",
                run_id=run_id,
                expected_previous_value=original_k_factor,
                metadata={"calibration": "repeatability_final_k"},
            ),
            parameter,
        )
        readback = None
        verified = False
        if decision.result.status.value == "applied":
            parameters = _read_selected_parameters(
                device,
                (k_factor_parameter,),
                merge_adjacent=False,
            )
            readback = float(_find_parameter(parameters, k_factor_parameter).value)
            verified = abs(readback - new_k_factor) <= max(
                1e-9,
                abs(new_k_factor) * 1e-6,
            )
        updated = dict(metrics)
        updated.update(
            {
                "write_requested": True,
                "write_status": decision.result.status.value,
                "write_verified": verified,
                "readback_k_factor": readback,
                "audit_id": decision.audit_id,
            }
        )
        if bool(metrics.get("history_saved", True)):
            started_at = _datetime_from_metric(updated.get("started_at"))
            ended_at = _datetime_from_metric(updated.get("ended_at"))
            self._save_repeatability_final_k_history(
                run_id=run_id,
                started_at=started_at,
                ended_at=ended_at,
                metrics=updated,
                input_artifact_ids=tuple(
                    str(artifact_id)
                    for artifact_id in _final_k_artifact_ids(updated)
                ),
                operation_metadata=operation_metadata,
                status=RunStatus.PASSED if verified else RunStatus.FAILED,
                attempt_status=decision.result.status.value,
                notes=str(updated.get("notes") or "") or None,
            )
        return updated

    def run_k_factor_calibration(
        self,
        *,
        mass_acc_before: float,
        mass_acc_after: float,
        standard_mass: float,
        current_k_factor: float,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> str:
        metadata = operation_metadata or self._operation_metadata
        run_id = self._next_run_id()
        KFactorCalibrationWorkflow(self._repository).run(
            _SelectedParameterDevice(
                self._require_device(),
                identity=self._require_identity(),
                parameter_names=("k_factor",),
            ),
            KFactorCalibrationConfig(
                run_id=run_id,
                operator=self._operator,
                mass_acc_before=mass_acc_before,
                mass_acc_after=mass_acc_after,
                standard_mass=standard_mass,
                current_k_factor=current_k_factor,
                software_version=__version__,
            ),
        )
        self._attach_operation_metadata_to_run(run_id, metadata)
        return run_id

    def run_repeatability_test(
        self,
        trials: tuple[RepeatabilityTrial, ...],
        *,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> str:
        metadata = operation_metadata or self._operation_metadata
        run_id = self._next_run_id()
        RepeatabilityTestWorkflow(self._repository).run(
            _SelectedParameterDevice(
                self._require_device(),
                identity=self._require_identity(),
                parameter_names=(),
            ),
            RepeatabilityTestConfig(
                run_id=run_id,
                operator=self._operator,
                trials=trials,
                software_version=__version__,
            ),
        )
        self._attach_operation_metadata_to_run(run_id, metadata)
        return run_id

    def list_calibration_history(
        self,
        *,
        operation: str | None = None,
    ) -> tuple[ModbusCalibrationHistoryEntry, ...]:
        operation_filter = None if operation in (None, "", "all") else operation
        entries: list[ModbusCalibrationHistoryEntry] = []
        accepted_workflows: set[str] | None = None
        if operation_filter == "manual_error_repeatability":
            accepted_workflows = {
                "manual_error_repeatability",
                "manual_error_repeatability_final_k",
            }
        elif operation_filter is not None:
            accepted_workflows = {operation_filter}
        for run_summary in self._repository.list_runs():
            if run_summary.workflow_name not in _CALIBRATION_HISTORY_WORKFLOWS:
                continue
            status = getattr(run_summary.status, "value", str(run_summary.status))
            if status not in _CALIBRATION_HISTORY_STATUSES:
                continue
            if (
                accepted_workflows is not None
                and run_summary.workflow_name not in accepted_workflows
            ):
                continue
            run = self._repository.get_run(run_summary.run_id)
            metrics: dict[str, Any] = {}
            analysis_results = self._repository.list_analysis_results(run_summary.run_id)
            if analysis_results:
                metrics = dict(analysis_results[-1].summary_metrics)
            if run is not None:
                metrics.update(
                    _operation_metadata_from_configuration(
                        run.configuration_snapshot
                    )
                )
            summary_notes = _history_str(metrics, "notes") or ""
            entries.append(
                ModbusCalibrationHistoryEntry(
                    run_id=run_summary.run_id,
                    operation=run_summary.workflow_name,
                    status=run_summary.status,
                    started_at=run_summary.started_at,
                    ended_at=run_summary.ended_at,
                    device_id=run_summary.device_id,
                    operator=run_summary.operator,
                    metrics=metrics,
                    notes=run_summary.notes or summary_notes,
                )
            )
        return tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.started_at or datetime.min.replace(tzinfo=UTC),
                    item.run_id,
                ),
                reverse=True,
            )
        )

    def list_test_records(
        self,
        *,
        device_id: str | None = None,
        session_id: str | None = None,
        operation: str | None = None,
        status: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
        device_model: str | None = None,
        tube_model: str | None = None,
        transmitter_model: str | None = None,
    ) -> tuple[ModbusCalibrationHistoryEntry, ...]:
        """List Modbus test records from operation attempts."""

        operation_filter = None if operation in (None, "", "all") else operation
        attempts = self._repository.list_modbus_operation_attempts(
            device_id=device_id,
            session_id=session_id,
            operation_type=operation_filter,
            status=None if status in (None, "", "all") else status,
            started_from=started_from,
            started_to=started_to,
            device_model=device_model,
            tube_model=tube_model,
            transmitter_model=transmitter_model,
        )
        if operation_filter == "manual_error_repeatability":
            for related_operation in (
                "manual_error_repeatability_trial",
                "manual_error_repeatability_final_k",
            ):
                attempts = attempts + self._repository.list_modbus_operation_attempts(
                    device_id=device_id,
                    session_id=session_id,
                    operation_type=related_operation,
                    status=None if status in (None, "", "all") else status,
                    started_from=started_from,
                    started_to=started_to,
                    device_model=device_model,
                    tube_model=tube_model,
                    transmitter_model=transmitter_model,
                )
        elif operation_filter == "k_factor_calibration":
            attempts = attempts + self._repository.list_modbus_operation_attempts(
                device_id=device_id,
                session_id=session_id,
                operation_type="k_factor_calibration_capture",
                status=None if status in (None, "", "all") else status,
                started_from=started_from,
                started_to=started_to,
                device_model=device_model,
                tube_model=tube_model,
                transmitter_model=transmitter_model,
            )
        attempts = tuple(
            sorted(
                attempts,
                key=lambda item: (
                    item.started_at or datetime.min.replace(tzinfo=UTC),
                    item.attempt_id,
                ),
                reverse=True,
            )
        )
        entries: list[ModbusCalibrationHistoryEntry] = []
        for attempt in attempts:
            run = self._repository.get_run(attempt.run_id) if attempt.run_id else None
            summary_notes = _history_str(attempt.summary, "notes") or ""
            notes = (
                attempt.notes
                or summary_notes
                or (run.notes if run is not None else "")
                or ""
            )
            prefer_attempt_time = attempt.operation_type in {
                "manual_error_repeatability_trial",
            }
            entries.append(
                ModbusCalibrationHistoryEntry(
                    run_id=attempt.run_id or attempt.attempt_id,
                    operation=attempt.operation_type,
                    status=attempt.status,
                    started_at=(
                        attempt.started_at
                        if prefer_attempt_time and attempt.started_at is not None
                        else (
                            run.started_at
                            if run is not None and run.started_at is not None
                            else attempt.started_at
                        )
                    ),
                    ended_at=(
                        attempt.ended_at
                        if prefer_attempt_time and attempt.ended_at is not None
                        else (
                            run.ended_at
                            if run is not None and run.ended_at is not None
                            else attempt.ended_at
                        )
                    ),
                    device_id=attempt.device_id,
                    operator=attempt.operator,
                    metrics={
                        **attempt.summary,
                        **attempt.device_metadata,
                        "register_map_snapshot": attempt.register_map_snapshot,
                        "attempt_id": attempt.attempt_id,
                        "session_id": attempt.session_id,
                        "raw_artifact_id": attempt.raw_artifact_id,
                    },
                    notes=notes,
                )
            )
        covered_run_ids = {
            attempt.run_id for attempt in attempts if attempt.run_id is not None
        }
        for legacy in self.list_calibration_history(operation=operation_filter):
            if session_id not in (None, ""):
                continue
            if legacy.run_id in covered_run_ids:
                continue
            if device_id is not None and legacy.device_id != device_id:
                continue
            if status not in (None, "", "all") and legacy.status != status:
                continue
            if not _history_entry_in_time_range(
                legacy,
                started_from=started_from,
                started_to=started_to,
            ):
                continue
            if not _metadata_filter_matches(
                legacy.metrics,
                device_model=device_model,
                tube_model=tube_model,
                transmitter_model=transmitter_model,
            ):
                continue
            entries.append(legacy)
        return tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.started_at or datetime.min.replace(tzinfo=UTC),
                    item.run_id,
                ),
                reverse=True,
            )
        )

    def analyze_device_history(self, device_id: str) -> ModbusDeviceAnalysis:
        """Build a lightweight, threshold-free analysis summary for one device."""

        normalized_device_id = str(device_id).strip()
        if not normalized_device_id:
            raise ValueError("Device ID is required for device analysis.")
        records = self.list_test_records(device_id=normalized_device_id)
        sessions = self._repository.list_modbus_test_sessions(
            device_id=normalized_device_id,
        )
        trials = self._repository.list_modbus_trial_records(
            device_id=normalized_device_id,
        )
        operation_counts: dict[str, int] = {}
        for record in records:
            operation_counts[record.operation] = (
                operation_counts.get(record.operation, 0) + 1
            )

        accepted_trials = tuple(
            trial for trial in trials if trial.trial_status == "accepted"
        )
        error_values = tuple(
            float(trial.percent_error)
            for trial in accepted_trials
            if trial.percent_error is not None
        )
        flow_summaries = tuple(
            _flow_summary_from_trial_records(
                flow_point,
                tuple(
                    trial
                    for trial in accepted_trials
                    if trial.flow_point == flow_point
                ),
            )
            for flow_point in sorted({trial.flow_point for trial in accepted_trials})
        )
        notes = _device_analysis_notes(
            records=records,
            trials=trials,
            accepted_trials=accepted_trials,
            flow_summaries=flow_summaries,
        )
        return ModbusDeviceAnalysis(
            device_id=normalized_device_id,
            generated_at=datetime.now(UTC),
            profile=self.get_device_profile(normalized_device_id),
            record_count=len(records),
            session_count=len(sessions),
            operation_counts=operation_counts,
            trial_count=len(trials),
            accepted_trial_count=len(accepted_trials),
            diagnostic_trial_count=sum(
                1 for trial in trials if trial.trial_status == "diagnostic"
            ),
            rejected_trial_count=sum(
                1 for trial in trials if trial.trial_status == "rejected"
            ),
            overall_mean_error_percent=(
                sum(error_values) / len(error_values) if error_values else None
            ),
            overall_stddev_error_percent=(
                _sample_stddev(error_values) if error_values else None
            ),
            overall_max_abs_error_percent=(
                max(abs(error) for error in error_values) if error_values else None
            ),
            flow_summaries=flow_summaries,
            latest_final_k=_latest_record_metrics(
                records,
                "manual_error_repeatability_final_k",
            ),
            latest_k_factor=_latest_record_metrics(
                records,
                "k_factor_calibration",
            ),
            latest_zero_calibration=_latest_record_metrics(
                records,
                "zero_calibration",
            ),
            notes=notes,
        )

    def update_calibration_history_note(self, run_id: str, notes: str) -> None:
        self._repository.update_run_notes(run_id, notes)

    def _artifact_to_history_payload(self, artifact: Artifact) -> dict[str, object]:
        payload = _artifact_to_history_payload(artifact)
        path = self._artifact_store.resolve(artifact.file_path)
        try:
            content = path.read_bytes()
        except FileNotFoundError:
            payload["content_missing"] = True
            return payload
        payload["content_base64"] = base64.b64encode(content).decode("ascii")
        payload["content_encoding"] = "base64"
        return payload

    def load_flow_sample_series(self, artifact_id: str) -> ModbusFlowSampleSeries:
        """Load one saved wide variable-sample CSV artifact."""

        normalized_artifact_id = str(artifact_id).strip()
        if not normalized_artifact_id:
            raise ValueError("Flow-sample artifact ID is required.")
        artifact = next(
            (
                item
                for item in self._repository.list_artifacts()
                if item.artifact_id == normalized_artifact_id
            ),
            None,
        )
        if artifact is None:
            raise ValueError(f"Flow-sample artifact not found: {normalized_artifact_id}")
        if artifact.metadata.get("curve_type") not in {
            "flow_rate_samples",
            "variable_samples",
        }:
            raise ValueError(
                f"Artifact is not a flow-sample curve: {normalized_artifact_id}"
            )
        path = self._artifact_store.resolve(artifact.file_path)
        if not path.exists():
            raise FileNotFoundError(f"Flow-sample artifact file not found: {path}")
        points = _flow_samples_from_csv(path.read_text(encoding="utf-8"))
        if not points:
            raise ValueError(f"Flow-sample artifact is empty: {normalized_artifact_id}")
        flow_rate_parameter = str(artifact.metadata.get("flow_rate_parameter") or "")
        variable_names = tuple(
            str(name)
            for name in artifact.metadata.get("variable_names", ())
            if str(name)
        )
        if not variable_names:
            variable_names = _trial_sample_variable_names(points, flow_rate_parameter)
        flow_samples = tuple(
            ModbusFlowSamplePoint(
                captured_at=point.captured_at,
                value=float(point.values[flow_rate_parameter]),
            )
            for point in points
            if flow_rate_parameter in point.values
            and _optional_float(point.values.get(flow_rate_parameter)) is not None
        )
        units = {
            str(name): str(unit)
            for name, unit in dict(artifact.metadata.get("units") or {}).items()
        }
        unit = str(artifact.metadata.get("unit") or units.get(flow_rate_parameter, ""))
        return ModbusFlowSampleSeries(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            flow_rate_parameter=flow_rate_parameter,
            unit=unit,
            samples=flow_samples,
            variable_names=variable_names,
            units=units,
            points=points,
        )

    def export_calibration_history(
        self,
        path: str | Path,
        *,
        operation: str | None = None,
        device_id: str | None = None,
        started_from: datetime | None = None,
        started_to: datetime | None = None,
    ) -> ModbusCalibrationHistoryExportResult:
        """Export Modbus calibration history as a portable JSON package."""

        export_path = Path(path)
        suffix = export_path.suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            raise ValueError(
                "Excel export is reserved for a future release; choose JSON for now."
            )
        if suffix != ".json":
            export_path = export_path.with_suffix(".json")

        entries: list[dict[str, object]] = []
        analysis_count = 0
        step_count = 0
        exported_run_ids: set[str] = set()
        for history_entry in self.list_test_records(
            operation=operation,
            device_id=device_id,
        ):
            if not _history_entry_in_time_range(
                history_entry,
                started_from=started_from,
                started_to=started_to,
            ):
                continue
            run = self._repository.get_run(history_entry.run_id)
            if run is not None and run.run_id in exported_run_ids:
                continue
            if run is not None:
                exported_run_ids.add(run.run_id)
            entry_device_id = (
                run.device_id if run is not None else history_entry.device_id
            )
            device = self._repository.get_device(entry_device_id)
            steps = self._repository.list_steps(run.run_id) if run is not None else ()
            analysis_results = (
                self._repository.list_analysis_results(run.run_id)
                if run is not None
                else ()
            )
            artifacts = (
                self._repository.list_artifacts(run.run_id)
                if run is not None
                else ()
            )
            attempts = self._repository.list_modbus_operation_attempts(
                device_id=entry_device_id,
            )
            run_attempts = tuple(
                attempt
                for attempt in attempts
                if (
                    (run is not None and attempt.run_id == run.run_id)
                    or attempt.attempt_id == history_entry.metrics.get("attempt_id")
                )
            )
            trials = tuple(
                trial
                for trial in self._repository.list_modbus_trial_records(
                    device_id=entry_device_id,
                )
                if (
                    (run is not None and trial.run_id == run.run_id)
                    or trial.attempt_id == history_entry.metrics.get("attempt_id")
                )
            )
            session_ids = {
                attempt.session_id
                for attempt in run_attempts
                if attempt.session_id is not None
            }
            session_ids.update(
                trial.session_id for trial in trials if trial.session_id is not None
            )
            test_sessions = tuple(
                session
                for session in self._repository.list_modbus_test_sessions(
                    device_id=entry_device_id,
                )
                if session.session_id in session_ids
            )
            analysis_count += len(analysis_results)
            step_count += len(steps)
            entries.append(
                {
                    "device": _device_record_to_history_payload(device)
                    if device is not None
                    else None,
                    "run": _run_session_to_history_payload(run)
                    if run is not None
                    else None,
                    "workflow_steps": [
                        _workflow_step_to_history_payload(step) for step in steps
                    ],
                    "analysis_results": [
                        _analysis_result_to_history_payload(result)
                        for result in analysis_results
                    ],
                    "artifacts": [
                        self._artifact_to_history_payload(artifact)
                        for artifact in artifacts
                    ],
                    "test_sessions": [
                        _test_session_to_history_payload(session)
                        for session in test_sessions
                    ],
                    "operation_attempts": [
                        _operation_attempt_to_history_payload(attempt)
                        for attempt in run_attempts
                    ],
                    "trial_records": [
                        _trial_record_to_history_payload(trial)
                        for trial in trials
                    ],
                }
            )

        payload = {
            "format": _CALIBRATION_HISTORY_EXPORT_FORMAT,
            "format_version": _CALIBRATION_HISTORY_EXPORT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "software_version": __version__,
            "operation_filter": operation or "all",
            "device_id_filter": device_id,
            "started_from": started_from.isoformat()
            if started_from is not None
            else None,
            "started_to": started_to.isoformat()
            if started_to is not None
            else None,
            "future_exports": {
                "excel": "reserved",
            },
            "entries": entries,
        }
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return ModbusCalibrationHistoryExportResult(
            path=export_path,
            run_count=len(entries),
            analysis_result_count=analysis_count,
            workflow_step_count=step_count,
            format_version=_CALIBRATION_HISTORY_EXPORT_VERSION,
        )

    def import_calibration_history(
        self,
        path: str | Path,
        *,
        target_device_id: str | None = None,
    ) -> ModbusCalibrationHistoryImportResult:
        """Import a portable Modbus calibration-history JSON package."""

        import_path = Path(path)
        normalized_target_device_id = (
            _validate_modbus_device_id(target_device_id)
            if target_device_id not in (None, "")
            else None
        )
        payload = json.loads(import_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Calibration history import file must contain an object.")
        if payload.get("format") != _CALIBRATION_HISTORY_EXPORT_FORMAT:
            raise ValueError("Unsupported calibration history import format.")
        if payload.get("format_version") != _CALIBRATION_HISTORY_EXPORT_VERSION:
            raise ValueError(
                "Unsupported calibration history import format version."
            )
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list):
            raise ValueError("Calibration history import file has no entries list.")

        imported_runs = 0
        skipped_runs = 0
        renamed_runs = 0
        retargeted_runs = 0
        imported_analysis_results = 0
        imported_workflow_steps = 0
        errors: list[str] = []
        for index, raw_entry in enumerate(raw_entries, start=1):
            try:
                if not isinstance(raw_entry, dict):
                    raise ValueError("entry must be an object")
                raw_run = raw_entry.get("run")
                if raw_run is None:
                    continue
                run = _run_session_from_history_payload(raw_run)
                if run.workflow_name not in _CALIBRATION_HISTORY_WORKFLOWS:
                    raise ValueError(f"unsupported workflow: {run.workflow_name}")
                status = getattr(run.status, "value", str(run.status))
                if status not in _CALIBRATION_HISTORY_STATUSES:
                    run = _normalize_imported_raw_capture_run_status(
                        run,
                        raw_entry,
                    )
                    status = getattr(run.status, "value", str(run.status))
                if status not in _CALIBRATION_HISTORY_STATUSES:
                    if status == RunStatus.RUNNING.value:
                        skipped_runs += 1
                        continue
                    raise ValueError(f"unsupported status: {status}")
                original_device_id = run.device_id
                source_run = run
                if normalized_target_device_id is not None:
                    run = _retarget_imported_run(
                        run,
                        target_device_id=normalized_target_device_id,
                    )
                existing_run = self._repository.get_run(run.run_id)
                if existing_run is not None:
                    source_analysis_results = _analysis_results_from_history_payload(
                        raw_entry.get("analysis_results")
                    )
                    analysis_results = source_analysis_results
                    artifacts = _artifacts_from_history_payload(
                        raw_entry.get("artifacts")
                    )
                    test_sessions = _test_sessions_from_history_payload(
                        raw_entry.get("test_sessions")
                    )
                    source_operation_attempts = (
                        _operation_attempts_from_history_payload(
                            raw_entry.get("operation_attempts")
                        )
                    )
                    source_trial_records = _trial_records_from_history_payload(
                        raw_entry.get("trial_records")
                    )
                    operation_attempts = source_operation_attempts
                    trial_records = source_trial_records
                    if normalized_target_device_id is not None:
                        analysis_results = tuple(
                            _retarget_imported_analysis_result(
                                result,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for result in analysis_results
                        )
                        artifacts = tuple(
                            _retarget_imported_artifact(
                                artifact,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for artifact in artifacts
                        )
                        test_sessions = tuple(
                            _retarget_imported_test_session(
                                session,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for session in test_sessions
                        )
                        operation_attempts = tuple(
                            _retarget_imported_operation_attempt(
                                attempt,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for attempt in operation_attempts
                        )
                        trial_records = tuple(
                            _retarget_imported_trial_record(
                                trial,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for trial in trial_records
                        )
                    already_imported = _history_run_already_imported(
                        self._repository,
                        run,
                        analysis_results,
                    )
                    if (
                        not already_imported
                        and normalized_target_device_id is not None
                    ):
                        already_imported = _history_run_already_imported(
                            self._repository,
                            source_run,
                            source_analysis_results,
                        )
                    if already_imported:
                        if normalized_target_device_id is not None:
                            device = _device_record_from_history_payload(
                                raw_entry.get("device")
                            )
                            if device is None:
                                device = DeviceRecord(
                                    device_id=run.device_id,
                                    device_type=DeviceType.MODBUS_RTU.value,
                                )
                            device = _retarget_imported_device_record(
                                device,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            if self._repository.get_device(device.device_id) is None:
                                self._repository.save_device(device)
                            if existing_run.device_id != normalized_target_device_id:
                                self._repository.save_run(run)
                                for result in analysis_results:
                                    self._repository.save_analysis_result(result)
                                imported_analysis_results += len(analysis_results)
                            self._save_imported_artifacts(
                                artifacts,
                                raw_entry.get("artifacts"),
                            )
                            test_sessions = _ensure_import_test_sessions(
                                test_sessions,
                                operation_attempts,
                                trial_records,
                                run,
                            )
                            for session in test_sessions:
                                self._repository.save_modbus_test_session(session)
                            for attempt in operation_attempts:
                                self._repository.save_modbus_operation_attempt(
                                    attempt
                                )
                            for trial in trial_records:
                                self._repository.save_modbus_trial_record(trial)
                            retargeted_runs += 1
                            continue
                        skipped_runs += 1
                        continue
                    original_run_id = run.run_id
                    new_run_id = self._next_imported_run_id(original_run_id)
                    imported_at = datetime.now(UTC).isoformat()
                    run = _remap_imported_run(
                        run,
                        original_run_id=original_run_id,
                        new_run_id=new_run_id,
                        imported_at=imported_at,
                    )
                    steps = _workflow_steps_from_history_payload(
                        raw_entry.get("workflow_steps")
                    )
                    test_sessions = _test_sessions_from_history_payload(
                        raw_entry.get("test_sessions")
                    )
                    operation_attempts = _operation_attempts_from_history_payload(
                        raw_entry.get("operation_attempts")
                    )
                    trial_records = _trial_records_from_history_payload(
                        raw_entry.get("trial_records")
                    )
                    if normalized_target_device_id is not None:
                        steps = tuple(
                            _retarget_imported_workflow_step(
                                step,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for step in steps
                        )
                        artifacts = tuple(
                            _retarget_imported_artifact(
                                artifact,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for artifact in artifacts
                        )
                        test_sessions = tuple(
                            _retarget_imported_test_session(
                                session,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for session in test_sessions
                        )
                        operation_attempts = tuple(
                            _retarget_imported_operation_attempt(
                                attempt,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for attempt in operation_attempts
                        )
                        trial_records = tuple(
                            _retarget_imported_trial_record(
                                trial,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for trial in trial_records
                        )
                    step_id_map = _import_step_id_map(
                        steps,
                        original_run_id=original_run_id,
                        new_run_id=new_run_id,
                    )
                    artifact_id_map = _import_artifact_id_map(
                        artifacts,
                        original_run_id=original_run_id,
                        new_run_id=new_run_id,
                    )
                    steps = tuple(
                        _remap_imported_workflow_step(
                            step,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            step_id_map=step_id_map,
                            imported_at=imported_at,
                        )
                        for step in steps
                    )
                    artifacts = tuple(
                        _remap_imported_artifact(
                            artifact,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            step_id_map=step_id_map,
                            artifact_id_map=artifact_id_map,
                            imported_at=imported_at,
                        )
                        for artifact in artifacts
                    )
                    analysis_results = tuple(
                        _remap_imported_analysis_result(
                            result,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            step_id_map=step_id_map,
                            artifact_id_map=artifact_id_map,
                            imported_at=imported_at,
                        )
                        for result in analysis_results
                    )
                    operation_attempts = tuple(
                        _remap_imported_operation_attempt(
                            attempt,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            artifact_id_map=artifact_id_map,
                            imported_at=imported_at,
                        )
                        for attempt in operation_attempts
                    )
                    trial_records = tuple(
                        _remap_imported_trial_record(
                            trial,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            artifact_id_map=artifact_id_map,
                            imported_at=imported_at,
                        )
                        for trial in trial_records
                    )
                    renamed_runs += 1
                else:
                    steps = _workflow_steps_from_history_payload(
                        raw_entry.get("workflow_steps")
                    )
                    analysis_results = _analysis_results_from_history_payload(
                        raw_entry.get("analysis_results")
                    )
                    artifacts = _artifacts_from_history_payload(
                        raw_entry.get("artifacts")
                    )
                    test_sessions = _test_sessions_from_history_payload(
                        raw_entry.get("test_sessions")
                    )
                    operation_attempts = _operation_attempts_from_history_payload(
                        raw_entry.get("operation_attempts")
                    )
                    trial_records = _trial_records_from_history_payload(
                        raw_entry.get("trial_records")
                    )
                    if normalized_target_device_id is not None:
                        steps = tuple(
                            _retarget_imported_workflow_step(
                                step,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for step in steps
                        )
                        analysis_results = tuple(
                            _retarget_imported_analysis_result(
                                result,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for result in analysis_results
                        )
                        artifacts = tuple(
                            _retarget_imported_artifact(
                                artifact,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for artifact in artifacts
                        )
                        test_sessions = tuple(
                            _retarget_imported_test_session(
                                session,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for session in test_sessions
                        )
                        operation_attempts = tuple(
                            _retarget_imported_operation_attempt(
                                attempt,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for attempt in operation_attempts
                        )
                        trial_records = tuple(
                            _retarget_imported_trial_record(
                                trial,
                                target_device_id=normalized_target_device_id,
                                original_device_id=original_device_id,
                            )
                            for trial in trial_records
                        )

                device = _device_record_from_history_payload(raw_entry.get("device"))
                if device is None:
                    device = DeviceRecord(
                        device_id=run.device_id,
                        device_type=DeviceType.MODBUS_RTU.value,
                    )
                if normalized_target_device_id is not None:
                    device = _retarget_imported_device_record(
                        device,
                        target_device_id=normalized_target_device_id,
                        original_device_id=original_device_id,
                    )
                if self._repository.get_device(device.device_id) is None:
                    self._repository.save_device(device)
                self._repository.save_run(run)

                for step in steps:
                    self._repository.save_step(step)
                imported_workflow_steps += len(steps)

                self._save_imported_artifacts(
                    artifacts,
                    raw_entry.get("artifacts"),
                )

                for result in analysis_results:
                    self._repository.save_analysis_result(result)
                imported_analysis_results += len(analysis_results)

                test_sessions = _ensure_import_test_sessions(
                    test_sessions,
                    operation_attempts,
                    trial_records,
                    run,
                )
                for session in test_sessions:
                    self._repository.save_modbus_test_session(session)

                for attempt in operation_attempts:
                    self._repository.save_modbus_operation_attempt(attempt)

                for trial in trial_records:
                    self._repository.save_modbus_trial_record(trial)
                imported_runs += 1
            except Exception as exc:
                errors.append(f"entry {index}: {exc}")

        return ModbusCalibrationHistoryImportResult(
            path=import_path,
            imported_runs=imported_runs,
            skipped_runs=skipped_runs,
            renamed_runs=renamed_runs,
            imported_analysis_results=imported_analysis_results,
            imported_workflow_steps=imported_workflow_steps,
            retargeted_runs=retargeted_runs,
            errors=tuple(errors),
        )

    def _save_imported_artifacts(
        self,
        artifacts: tuple[Artifact, ...],
        raw_artifacts: object,
    ) -> None:
        raw_payloads = _artifact_payloads_by_id(raw_artifacts)
        for artifact in artifacts:
            raw_payload = raw_payloads.get(
                str(
                    artifact.metadata.get(
                        "imported_from_artifact_id",
                        artifact.artifact_id,
                    )
                )
            )
            if raw_payload is None:
                raw_payload = raw_payloads.get(artifact.artifact_id)
            if raw_payload is not None:
                _restore_imported_artifact_content(
                    self._artifact_store,
                    artifact,
                    raw_payload,
                )
            self._repository.save_artifact(artifact)

    def _save_k_factor_simple_history(
        self,
        result: ModbusKFactorSimpleResult,
        *,
        status: RunStatus,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> None:
        identity = self._require_identity()
        metadata = self._operation_metadata_snapshot(operation_metadata)
        metrics = _k_factor_simple_metrics(result)
        metrics.update(metadata)
        self._repository.save_run(
            RunSession(
                run_id=result.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="k_factor_calibration",
                workflow_version="0.2-simple",
                device_id=identity.device_id,
                operator=self._operator,
                status=status,
                started_at=result.flow_started_at,
                ended_at=result.flow_ended_at,
                configuration_snapshot={
                    **metadata,
                    "mode": "simple",
                    "flow_rate_parameter": result.flow_rate_parameter,
                    "flow_acc_parameter": result.flow_acc_parameter,
                    "k_factor_parameter": result.k_factor_parameter,
                    "poll_interval_s": result.poll_interval_s,
                    "pre_snapshot_variable_count": len(result.pre_snapshot),
                },
                software_version=__version__,
            )
        )
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{result.run_id}-KFACTOR",
                run_id=result.run_id,
                step_id=None,
                result_type="k_factor_calibration",
                algorithm_name="simple_flow_segment_k_factor",
                algorithm_version="0.2",
                input_artifact_ids=(result.raw_artifact_id,)
                if result.raw_artifact_id is not None
                else (),
                configuration_snapshot={
                    **metadata,
                    "formula": "K1 = K0 / (m2 - m1) * standard_mass",
                    "mean_flow_formula": "(m2 - m1) / (t2 - t1)",
                },
                summary_metrics=metrics,
                pass_fail_decision="passed" if status is RunStatus.PASSED else "failed",
                created_at=datetime.now(UTC),
            )
        )
        self._save_modbus_operation_attempt(
            attempt_id=(
                f"{result.run_id}-KFACTOR-APPLY"
                if result.write_requested
                else f"{result.run_id}-KFACTOR-CALCULATE"
            ),
            operation_type="k_factor_calibration",
            status=status.value,
            run_id=result.run_id,
            started_at=result.flow_started_at,
            ended_at=result.flow_ended_at,
            raw_artifact_id=result.raw_artifact_id,
            summary=metrics,
            metadata=operation_metadata,
        )

    def _save_repeatability_simple_history(
        self,
        result: ModbusRepeatabilitySimpleResult,
        *,
        status: RunStatus,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> None:
        identity = self._require_identity()
        metadata = self._operation_metadata_snapshot(operation_metadata)
        calculated_at = datetime.now(UTC)
        metrics = _repeatability_simple_metrics(
            result,
            calculated_at=calculated_at,
        )
        metrics.update(metadata)
        source_started_at = result.started_at
        source_ended_at = result.ended_at
        self._repository.save_run(
            RunSession(
                run_id=result.run_id,
                run_type=RunType.ERROR_ANALYSIS,
                workflow_name="manual_error_repeatability",
                workflow_version="0.2-simple",
                device_id=identity.device_id,
                operator=self._operator,
                status=status,
                started_at=calculated_at,
                ended_at=calculated_at,
                configuration_snapshot={
                    **metadata,
                    "mode": result.mode,
                    "flow_rate_parameter": result.flow_rate_parameter,
                    "flow_acc_parameter": result.flow_acc_parameter,
                    "poll_interval_s": result.poll_interval_s,
                    "flow_point_count": result.analysis.summary_metrics.get(
                        "flow_point_count"
                    ),
                    "trials_per_point": result.expected_trials_per_point,
                    "pre_snapshot_variable_count": len(result.pre_snapshot),
                },
                software_version=__version__,
                notes=result.notes or None,
            )
        )
        step = WorkflowStep(
            step_id=f"{result.run_id}-STEP-001",
            run_id=result.run_id,
            name="Capture and analyze Modbus repeatability trials",
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.PASSED
            if status is RunStatus.PASSED
            else WorkflowStepStatus.FAILED,
            started_at=calculated_at,
            ended_at=calculated_at,
            input_configuration={
                **metadata,
                "mode": result.mode,
                "flow_rate_parameter": result.flow_rate_parameter,
                "flow_acc_parameter": result.flow_acc_parameter,
                "poll_interval_s": result.poll_interval_s,
                "source_trial_started_at": source_started_at.isoformat(),
                "source_trial_ended_at": source_ended_at.isoformat(),
            },
            output_summary={
                "flow_point_count": result.analysis.summary_metrics.get(
                    "flow_point_count"
                ),
                "trial_count": result.analysis.summary_metrics.get("trial_count"),
                "max_abs_percent_error": result.analysis.summary_metrics.get(
                    "max_abs_percent_error"
                ),
                "max_repeatability_stddev_percent": result.analysis.summary_metrics.get(
                    "max_repeatability_stddev_percent"
                ),
            },
        )
        self._repository.save_step(step)
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{result.run_id}-REPEATABILITY",
                run_id=result.run_id,
                step_id=step.step_id,
                result_type="manual_error_repeatability",
                algorithm_name="modbus_simple_mass_total_repeatability",
                algorithm_version="0.2",
                input_artifact_ids=_repeatability_trial_artifact_ids(result.trials),
                configuration_snapshot={
                    **metadata,
                    "formula": "e = (delta_m - standard_mass) / standard_mass * 100%",
                    "repeatability": "sample standard deviation of percent errors per flow point",
                    "mode": result.mode,
                    "trial_count": len(result.trials),
                    "flow_point_count": result.analysis.summary_metrics.get(
                        "flow_point_count"
                    ),
                    "trials_per_point": result.expected_trials_per_point,
                },
                summary_metrics=metrics,
                pass_fail_decision="passed" if status is RunStatus.PASSED else "failed",
                created_at=datetime.now(UTC),
            )
        )
        if len(result.analysis.flow_points) == 1:
            summary_attempt_id = (
                f"{result.run_id}-REPEATABILITY-"
                f"{_flow_point_metric_label(result.analysis.flow_points[0].flow_point).upper()}"
            )
        else:
            summary_attempt_id = f"{result.run_id}-REPEATABILITY-SUMMARY"
        self._save_modbus_operation_attempt(
            attempt_id=summary_attempt_id,
            operation_type="manual_error_repeatability",
            status=status.value,
            run_id=result.run_id,
            started_at=calculated_at,
            ended_at=calculated_at,
            raw_artifact_id=None,
            summary=metrics,
            metadata=operation_metadata,
            notes=result.notes or None,
        )

    def _save_repeatability_final_k_history(
        self,
        *,
        run_id: str,
        started_at: datetime,
        ended_at: datetime,
        metrics: dict[str, object],
        input_artifact_ids: tuple[str, ...],
        operation_metadata: ModbusOperationMetadata | None = None,
        status: RunStatus = RunStatus.PASSED,
        attempt_status: str = "calculated",
        device_id: str | None = None,
        report_text: str | None = None,
        notes: str | None = None,
    ) -> str | None:
        resolved_device_id = device_id or self._device_id
        if not resolved_device_id and self._identity is not None:
            resolved_device_id = self._identity.device_id
        if not resolved_device_id:
            raise ValueError("Device ID is required to save final K history.")
        if self._repository.get_device(resolved_device_id) is None:
            self._repository.save_device(
                DeviceRecord(
                    device_id=resolved_device_id,
                    device_type=DeviceType.MODBUS_RTU.value,
                )
            )
        metadata = self._operation_metadata_snapshot(operation_metadata)
        report_artifact_id = None
        effective_input_artifact_ids = input_artifact_ids
        self._repository.save_run(
            RunSession(
                run_id=run_id,
                run_type=RunType.ERROR_ANALYSIS,
                workflow_name="manual_error_repeatability_final_k",
                workflow_version="0.4-final-k",
                device_id=resolved_device_id,
                operator=self._operator,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                configuration_snapshot={
                    **metadata,
                    "flow_rate_parameter": metrics.get("flow_rate_parameter"),
                    "flow_acc_parameter": metrics.get("flow_acc_parameter"),
                    "k_factor_parameter": metrics.get("k_factor_parameter"),
                    "original_k_factor": metrics.get("original_k_factor"),
                    "selected_flow_point_count": metrics.get(
                        "selected_flow_point_count"
                    ),
                    "selected_trial_count": metrics.get("selected_trial_count"),
                    "write_requested": metrics.get("write_requested"),
                    "write_status": metrics.get("write_status"),
                    "write_verified": metrics.get("write_verified"),
                    "test_session_id": self._test_session_id,
                },
                software_version=__version__,
                notes=notes,
            )
        )
        if report_text is not None:
            report_artifact_id = self._save_text_report_artifact(
                run_id=run_id,
                operation_type="manual_error_repeatability_final_k",
                report_text=report_text,
                created_at=ended_at,
            )
            if report_artifact_id is not None:
                effective_input_artifact_ids = (
                    *effective_input_artifact_ids,
                    report_artifact_id,
                )
                metrics = {
                    **metrics,
                    "report_artifact_id": report_artifact_id,
                }
        step = WorkflowStep(
            step_id=f"{run_id}-FINAL-K-STEP",
            run_id=run_id,
            name="Calculate final K from selected repeatability trials",
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.PASSED
            if status is RunStatus.PASSED
            else WorkflowStepStatus.FAILED,
            started_at=started_at,
            ended_at=ended_at,
            input_configuration={
                **metadata,
                "formula_average_error": "(max(measurement_errors) + min(measurement_errors)) / 2",
                "formula_adjusted_error": "measurement_error - average_error",
                "formula_intermediate_k": "original_k / (1 + measurement_error_percent / 100)",
                "formula_new_k": "(max(intermediate_k) + min(intermediate_k)) / 2",
            },
            output_summary={
                "average_error": metrics.get("average_error"),
                "new_k_factor": metrics.get("new_k_factor"),
                "delta_k_factor": metrics.get("delta_k_factor"),
                "selected_trial_count": metrics.get("selected_trial_count"),
                "write_status": metrics.get("write_status"),
                "write_verified": metrics.get("write_verified"),
                "readback_k_factor": metrics.get("readback_k_factor"),
            },
        )
        self._repository.save_step(step)
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{run_id}-FINAL-K",
                run_id=run_id,
                step_id=step.step_id,
                result_type="manual_error_repeatability_final_k",
                algorithm_name="modbus_repeatability_final_k",
                algorithm_version="0.4",
                input_artifact_ids=effective_input_artifact_ids,
                configuration_snapshot=step.input_configuration,
                summary_metrics=metrics,
                pass_fail_decision=(
                    "calculated"
                    if not metrics.get("write_requested")
                    else (
                        "write_verified"
                        if metrics.get("write_verified")
                        else "write_failed"
                    )
                ),
                created_at=datetime.now(UTC),
            )
        )
        if self._identity is not None and self._identity.device_id == resolved_device_id:
            self._save_modbus_operation_attempt(
                attempt_id=f"{run_id}-FINAL-K",
                operation_type="manual_error_repeatability_final_k",
                status=attempt_status,
                run_id=run_id,
                started_at=started_at,
                ended_at=ended_at,
                raw_artifact_id=None,
                summary=metrics,
                metadata=operation_metadata,
                notes=notes,
            )
        else:
            device_metadata = {
                "device_id": resolved_device_id,
                "device_type": DeviceType.MODBUS_RTU.value,
                **metadata,
            }
            self._repository.save_modbus_operation_attempt(
                ModbusOperationAttemptRecord(
                    attempt_id=f"{run_id}-FINAL-K",
                    session_id=None,
                    run_id=run_id,
                    device_id=resolved_device_id,
                    operation_type="manual_error_repeatability_final_k",
                    status=attempt_status,
                    started_at=started_at,
                    ended_at=ended_at,
                    operator=self._operator,
                    device_metadata=device_metadata,
                    raw_artifact_id=None,
                    summary={**metrics, **device_metadata},
                    notes=notes,
                )
            )
        return report_artifact_id

    def _require_device(self) -> FlowmeterDevice:
        if self._device is None:
            raise ConnectionError("Connect the Modbus module first.")
        return self._device

    def _require_identity(self) -> DeviceIdentity:
        if self._identity is None:
            raise ConnectionError("Connect the Modbus module first.")
        return self._identity

    def _ensure_modbus_device_profile(
        self,
        settings: ModbusConnectionSettings,
    ) -> None:
        identity = self._require_identity()
        metadata = self._operation_metadata_snapshot()
        existing = self._repository.get_modbus_device_profile(identity.device_id)
        if existing is None:
            raise RuntimeError(
                f"Modbus device profile no longer exists: {identity.device_id}"
            )
        self._profile_id = existing.profile_id
        self._repository.save_modbus_device_profile(
            ModbusDeviceProfileRecord(
                profile_id=self._profile_id,
                device_id=identity.device_id,
                display_name=existing.display_name or identity.device_id,
                device_model=metadata.get("device_model") or None,
                tube_model=metadata.get("tube_model") or None,
                transmitter_model=metadata.get("transmitter_model") or None,
                connection_settings=_connection_settings_to_payload(settings),
                register_map=_register_map_payload(self._register_map),
                notes=existing.notes if existing is not None else None,
                created_at=existing.created_at if existing is not None else None,
            )
        )

    def _start_modbus_test_session(self) -> str:
        identity = self._require_identity()
        if self._test_session_id is None:
            session_id = f"MODBUS-SESSION-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid4().hex[:8]}"
            self._test_session_id = session_id
            self._repository.save_modbus_test_session(
                ModbusTestSessionRecord(
                    session_id=session_id,
                    device_id=identity.device_id,
                    profile_id=self._profile_id,
                    operator=self._operator,
                    status="running",
                    started_at=datetime.now(UTC),
                    device_metadata=self._device_metadata_snapshot(),
                    register_map_snapshot=self._register_map_snapshot(),
                )
            )
        return self._test_session_id

    def _finish_modbus_test_session(self, *, status: str) -> None:
        if self._test_session_id is None or self._device_id is None:
            return
        sessions = self._repository.list_modbus_test_sessions(
            device_id=self._device_id,
        )
        session = next(
            (
                item
                for item in sessions
                if item.session_id == self._test_session_id
            ),
            None,
        )
        if session is None:
            return
        self._repository.save_modbus_test_session(
            replace(session, status=status, ended_at=datetime.now(UTC))
        )

    def _device_metadata_snapshot(
        self,
        metadata: ModbusOperationMetadata | None = None,
    ) -> dict[str, str]:
        identity = self._require_identity()
        snapshot = self._operation_metadata_snapshot(metadata)
        return {
            "device_id": identity.device_id,
            "device_type": identity.device_type.value,
            "protocol_address": identity.protocol_address or "",
            **snapshot,
        }

    def _register_map_snapshot(self) -> dict[str, object]:
        return {
            "name": self._register_map.name,
            "version": self._register_map.version,
            "registers": [
                {
                    "name": register.name,
                    "kind": register.kind.value,
                    "address": register.address,
                    "word_count": register.word_count,
                    "data_type": register.data_type.value,
                    "scale": register.scale,
                    "unit": register.unit,
                    "writable": register.writable,
                    "minimum": register.minimum,
                    "maximum": register.maximum,
                    "byte_order": register.byte_order.value,
                    "word_order": register.word_order.value,
                }
                for register in self._register_map.registers
            ],
        }

    def _save_modbus_operation_attempt(
        self,
        *,
        attempt_id: str,
        operation_type: str,
        status: str,
        run_id: str | None,
        started_at: datetime | None,
        ended_at: datetime | None,
        raw_artifact_id: str | None,
        summary: dict[str, Any],
        metadata: ModbusOperationMetadata | None = None,
        notes: str | None = None,
    ) -> None:
        identity = self._require_identity()
        session_id = self._start_modbus_test_session()
        self._repository.save_modbus_operation_attempt(
            ModbusOperationAttemptRecord(
                attempt_id=attempt_id,
                session_id=session_id,
                run_id=run_id,
                device_id=identity.device_id,
                operation_type=operation_type,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                operator=self._operator,
                device_metadata=self._device_metadata_snapshot(metadata),
                register_map_snapshot=self._register_map_snapshot(),
                raw_artifact_id=raw_artifact_id,
                summary=summary,
                notes=notes,
            )
        )
        self._complete_raw_capture_run(
            run_id=run_id,
            status=status,
            ended_at=ended_at,
            summary=summary,
        )

    def _save_modbus_trial_record(
        self,
        trial: ModbusRepeatabilitySimpleTrialResult,
    ) -> None:
        identity = self._require_identity()
        calculated_at = datetime.now(UTC)
        record_started_at = trial.capture_started_at or calculated_at
        attempt_id = (
            f"{trial.run_id}-TRIAL-{trial.trial_index:03d}"
            f"-{uuid4().hex[:6]}"
        )
        self._save_modbus_operation_attempt(
            attempt_id=attempt_id,
            operation_type="manual_error_repeatability_trial",
            status=trial.trial_status,
            run_id=trial.run_id,
            started_at=record_started_at,
            ended_at=calculated_at,
            raw_artifact_id=trial.raw_artifact_id,
            summary={
                "flow_point": trial.flow_point,
                "trial_index": trial.trial_index,
                "flow_rate_parameter": trial.flow_rate_parameter,
                "flow_acc_parameter": trial.flow_acc_parameter,
                "k_factor_parameter": trial.k_factor_parameter,
                "original_k_factor": trial.original_k_factor,
                "mass_acc_before": trial.mass_acc_before,
                "mass_acc_after": trial.mass_acc_after,
                "measured_mass_delta": trial.measured_mass_delta,
                "standard_mass": trial.standard_mass,
                "percent_error": trial.percent_error,
                "mean_flow": trial.mean_flow,
                "instant_flow": trial.instant_flow,
                "duration_s": trial.duration_s,
                "capture_started_at": record_started_at.isoformat(),
                "calculated_at": calculated_at.isoformat(),
                "flow_started_at": trial.flow_started_at.isoformat(),
                "flow_instant_at": trial.flow_instant_at.isoformat(),
                "flow_ended_at": trial.flow_ended_at.isoformat(),
                "started_at": record_started_at.isoformat(),
                "ended_at": calculated_at.isoformat(),
                "poll_interval_s": trial.poll_interval_s,
                "trial_status": trial.trial_status,
                "raw_artifact_id": trial.raw_artifact_id,
                "flow_samples_artifact_id": trial.flow_samples_artifact_id,
                "flow_sample_count": trial.flow_sample_count,
                "trial_sample_variable_names": list(trial.trial_sample_variable_names),
                "test_session_id": trial.test_session_id,
                "notes": trial.notes,
                "pre_snapshot": trial.pre_snapshot,
                "pre_snapshot_captured_at": (
                    trial.pre_snapshot_captured_at.isoformat()
                    if trial.pre_snapshot_captured_at is not None
                    else None
                ),
                "post_snapshot": trial.post_snapshot,
                "post_snapshot_captured_at": (
                    trial.post_snapshot_captured_at.isoformat()
                    if trial.post_snapshot_captured_at is not None
                    else None
                ),
            },
            notes=trial.notes,
        )
        self._repository.save_modbus_trial_record(
            ModbusTrialRecord(
                trial_id=(
                    f"{trial.run_id}-FLOW-{_flow_point_metric_label(trial.flow_point)}"
                    f"-TRIAL-{trial.trial_index:03d}-{uuid4().hex[:6]}"
                ),
                session_id=trial.test_session_id or self._test_session_id,
                attempt_id=attempt_id,
                run_id=trial.run_id,
                device_id=identity.device_id,
                flow_point=trial.flow_point,
                trial_index=trial.trial_index,
                trial_status=trial.trial_status,
                k_factor_parameter=trial.k_factor_parameter,
                original_k_factor=trial.original_k_factor,
                mass_acc_before=trial.mass_acc_before,
                mass_acc_after=trial.mass_acc_after,
                measured_mass_delta=trial.measured_mass_delta,
                standard_mass=trial.standard_mass,
                percent_error=trial.percent_error,
                mean_flow=trial.mean_flow,
                instant_flow=trial.instant_flow,
                flow_started_at=trial.flow_started_at,
                flow_instant_at=trial.flow_instant_at,
                flow_ended_at=trial.flow_ended_at,
                raw_artifact_id=trial.raw_artifact_id,
                device_metadata=self._device_metadata_snapshot(),
                notes=trial.notes,
            )
        )

    def _save_raw_curve_artifact(
        self,
        *,
        run_id: str,
        operation_type: str,
        points: tuple[dict[str, object], ...],
        created_at: datetime | None = None,
    ) -> str | None:
        if not points:
            return None
        self._ensure_raw_artifact_run(run_id, operation_type, created_at)
        artifact_id = f"{run_id}-{operation_type.upper()}-RAW-{uuid4().hex[:8]}"
        created = created_at or datetime.now(UTC)
        artifact = self._artifact_store.write_artifact(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=ArtifactType.RAW,
            file_name=f"{artifact_id}_raw_curve.csv",
            content=_raw_curve_csv(points),
            created_at=created,
            file_format="csv",
        )
        artifact = replace(
            artifact,
            metadata={
                "source": "modbus_module",
                "operation_type": operation_type,
                "curve_type": "modbus_polling",
                "point_count": len(points),
            },
        )
        self._repository.save_artifact(artifact)
        return artifact.artifact_id

    def _save_flow_samples_artifact(
        self,
        *,
        run_id: str,
        operation_type: str,
        flow_rate_parameter: str,
        samples: tuple[ModbusTrialSamplePoint, ...],
        variable_names: tuple[str, ...],
        created_at: datetime | None = None,
        curve_type: str = "flow_rate_samples",
    ) -> str | None:
        if not samples:
            return None
        self._ensure_raw_artifact_run(run_id, operation_type, created_at)
        artifact_id = f"{run_id}-{operation_type.upper()}-FLOW-SAMPLES-{uuid4().hex[:8]}"
        created = created_at or samples[0].captured_at
        variable_names = _unique_names(variable_names)
        units = {
            name: _register_unit(self._register_map, name)
            for name in variable_names
        }
        artifact = self._artifact_store.write_artifact(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=ArtifactType.RAW,
            file_name=f"{artifact_id}_flow_samples.csv",
            content=_flow_samples_csv(samples, variable_names),
            created_at=created,
            file_format="csv",
        )
        artifact = replace(
            artifact,
            metadata={
                "source": "modbus_module",
                "operation_type": operation_type,
                "curve_type": curve_type,
                "flow_rate_parameter": flow_rate_parameter,
                "unit": units.get(flow_rate_parameter, ""),
                "units": units,
                "variable_names": list(variable_names),
                "sample_count": len(samples),
            },
        )
        self._repository.save_artifact(artifact)
        return artifact.artifact_id

    def _save_text_report_artifact(
        self,
        *,
        run_id: str,
        operation_type: str,
        report_text: str,
        created_at: datetime | None = None,
    ) -> str | None:
        if not report_text:
            return None
        created = created_at or datetime.now(UTC)
        artifact_id = f"{run_id}-{operation_type.upper()}-REPORT-{uuid4().hex[:8]}"
        artifact = self._artifact_store.write_artifact(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=ArtifactType.REPORT,
            file_name=f"{operation_type}_report.txt",
            content=report_text.encode("utf-8"),
            created_at=created,
            file_format="txt",
        )
        artifact = replace(
            artifact,
            metadata={
                "source": "modbus_module",
                "operation_type": operation_type,
                "report_type": "device_analysis_repeatability_final_k",
            },
        )
        self._repository.save_artifact(artifact)
        return artifact.artifact_id

    def _ensure_raw_artifact_run(
        self,
        run_id: str,
        operation_type: str,
        started_at: datetime | None,
    ) -> None:
        if self._repository.get_run(run_id) is not None:
            return
        identity = self._require_identity()
        run_type = (
            RunType.ERROR_ANALYSIS
            if operation_type.startswith("manual_error_repeatability")
            else RunType.CALIBRATION
        )
        self._repository.save_run(
            RunSession(
                run_id=run_id,
                run_type=run_type,
                workflow_name=operation_type,
                workflow_version="0.2-raw-capture",
                device_id=identity.device_id,
                operator=self._operator,
                status=RunStatus.RUNNING,
                started_at=started_at or datetime.now(UTC),
                configuration_snapshot={
                    **self._device_metadata_snapshot(),
                    "test_session_id": self._test_session_id,
                    "register_map": self._register_map_snapshot(),
                    "raw_capture_only": True,
                },
                software_version=__version__,
            )
        )

    def _complete_raw_capture_run(
        self,
        *,
        run_id: str | None,
        status: str,
        ended_at: datetime | None,
        summary: dict[str, Any],
    ) -> None:
        if run_id is None:
            return
        run = self._repository.get_run(run_id)
        if run is None or not run.configuration_snapshot.get("raw_capture_only"):
            return
        run_status = _raw_capture_run_status_from_attempt_status(status)
        configuration = dict(run.configuration_snapshot)
        configuration["raw_capture_completed"] = True
        configuration["raw_capture_attempt_status"] = status
        if "raw_artifact_id" in summary:
            configuration["raw_artifact_id"] = summary["raw_artifact_id"]
        self._repository.save_run(
            replace(
                run,
                status=run_status,
                ended_at=ended_at or run.ended_at or datetime.now(UTC),
                configuration_snapshot=configuration,
            )
        )

    def _next_run_id(self) -> str:
        self._sequence += 1
        return f"RUN-{datetime.now(UTC):%Y%m%d}-{self._sequence:06d}"

    def _next_imported_run_id(self, original_run_id: str) -> str:
        for _attempt in range(100):
            candidate = f"IMPORTED-{original_run_id}-{uuid4().hex[:8]}"
            if self._repository.get_run(candidate) is None:
                return candidate
        raise RuntimeError(f"Unable to create imported run ID for {original_run_id}.")

    def _operation_metadata_snapshot(
        self,
        metadata: ModbusOperationMetadata | None = None,
    ) -> dict[str, str]:
        return (metadata or self._operation_metadata).to_dict()

    def _attach_operation_metadata_to_run(
        self,
        run_id: str,
        metadata: ModbusOperationMetadata,
    ) -> None:
        metadata_snapshot = self._operation_metadata_snapshot(metadata)
        run = self._repository.get_run(run_id)
        if run is not None:
            configuration = dict(run.configuration_snapshot)
            configuration.update(metadata_snapshot)
            self._repository.save_run(replace(run, configuration_snapshot=configuration))
        results = self._repository.list_analysis_results(run_id)
        for result in results:
            configuration = dict(result.configuration_snapshot)
            configuration.update(metadata_snapshot)
            metrics = dict(result.summary_metrics)
            metrics.update(metadata_snapshot)
            self._repository.save_analysis_result(
                replace(
                    result,
                    configuration_snapshot=configuration,
                    summary_metrics=metrics,
                )
            )


class _SelectedParameterDevice(FlowmeterDevice):
    """Limits generic workflow parameter reads to the Modbus registers in use."""

    def __init__(
        self,
        device: FlowmeterDevice,
        *,
        identity: DeviceIdentity,
        parameter_names: tuple[str, ...],
    ) -> None:
        self._device = device
        self._identity = identity
        self._parameter_names = parameter_names

    def connect(self) -> None:
        self._device.connect()

    def disconnect(self) -> None:
        self._device.disconnect()

    def read_identity(self) -> DeviceIdentity:
        return self._identity

    def read_health(self) -> DeviceHealth:
        return self._device.read_health()

    def read_measurement(self) -> Measurement:
        return self._device.read_measurement()

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        return self.read_configuration_parameters(self._parameter_names)

    def read_configuration_parameters(
        self,
        names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
    ) -> tuple[ConfigurationParameter, ...]:
        requested = tuple(
            name for name in names if name in set(self._parameter_names)
        )
        if not requested:
            return ()
        reader = getattr(self._device, "read_configuration_parameters", None)
        if callable(reader):
            try:
                return reader(requested, merge_adjacent=merge_adjacent)
            except TypeError:
                return reader(requested)
        parameters = self._device.read_configuration()
        allowed = set(requested)
        return tuple(parameter for parameter in parameters if parameter.name in allowed)

    def write_configuration(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        return self._device.write_configuration(request)

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return self._device.communication_diagnostics()


class _RawCurveRecordingDevice(FlowmeterDevice):
    """Records Modbus logical-variable reads made during one operation."""

    def __init__(
        self,
        device: FlowmeterDevice,
        *,
        register_map: ModbusRegisterMap,
        default_phase: str,
    ) -> None:
        self._device = device
        self._register_map = register_map
        self._phase = default_phase
        self.points: list[dict[str, object]] = []

    def set_phase(self, phase: str) -> None:
        self._phase = phase

    def connect(self) -> None:
        self._device.connect()

    def disconnect(self) -> None:
        self._device.disconnect()

    def read_identity(self) -> DeviceIdentity:
        return self._device.read_identity()

    def read_health(self) -> DeviceHealth:
        return self._device.read_health()

    def read_measurement(self) -> Measurement:
        return self._device.read_measurement()

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        parameters = self._device.read_configuration()
        self._record_parameters(parameters)
        return parameters

    def read_configuration_parameters(
        self,
        names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
    ) -> tuple[ConfigurationParameter, ...]:
        reader = getattr(self._device, "read_configuration_parameters", None)
        if callable(reader):
            try:
                parameters = reader(names, merge_adjacent=merge_adjacent)
            except TypeError:
                parameters = reader(names)
        else:
            allowed = set(names)
            parameters = tuple(
                parameter
                for parameter in self._device.read_configuration()
                if parameter.name in allowed
            )
        self._record_parameters(parameters)
        return parameters

    def write_configuration(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        return self._device.write_configuration(request)

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return self._device.communication_diagnostics()

    def _record_parameters(
        self,
        parameters: tuple[ConfigurationParameter, ...],
    ) -> None:
        captured_at = datetime.now(UTC).isoformat()
        for parameter in parameters:
            try:
                register = self._register_map.by_name(parameter.name)
                register_metadata: dict[str, object] = {
                    "register_kind": register.kind.value,
                    "address": register.address,
                    "word_count": register.word_count,
                    "data_type": register.data_type.value,
                }
            except KeyError:
                register_metadata = dict(parameter.metadata)
            self.points.append(
                {
                    "captured_at": captured_at,
                    "phase": self._phase,
                    "variable_name": parameter.name,
                    "value": _json_metric_value(parameter.value),
                    "unit": parameter.unit or "",
                    **register_metadata,
                }
            )


class _NoPreReadWriteDevice(FlowmeterDevice):
    """Routes writes to the Modbus no-pre-read method while preserving the interface."""

    def __init__(self, device: FlowmeterDevice) -> None:
        self._device = device

    def connect(self) -> None:
        self._device.connect()

    def disconnect(self) -> None:
        self._device.disconnect()

    def read_identity(self) -> DeviceIdentity:
        return self._device.read_identity()

    def read_health(self) -> DeviceHealth:
        return self._device.read_health()

    def read_measurement(self) -> Measurement:
        return self._device.read_measurement()

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        return self._device.read_configuration()

    def write_configuration(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        writer = getattr(self._device, "write_configuration_without_pre_read", None)
        if callable(writer):
            return writer(request)
        return self._device.write_configuration(request)

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return self._device.communication_diagnostics()


class _FrameLoggingTransport:
    """Wraps a Modbus transport and emits operator-visible TX/RX frames."""

    def __init__(self, transport: ModbusTransport, logger: FrameLogger) -> None:
        self._transport = transport
        self._logger = logger

    def connect(self) -> bool:
        return self._transport.connect()

    def close(self) -> None:
        self._transport.close()

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
        function = _read_function_code(kind)
        self._logger("TX", "read", _hex_frame([unit_id, function, * _u16(address), * _u16(count)]))
        response = self._transport.read_registers(kind, address, count, unit_id)
        if response.ok and response.values is not None:
            payload = (
                _bits_to_response_bytes(response.values, count)
                if kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT)
                else _words_to_bytes(response.values)
            )
            self._logger("RX", "read", _hex_frame([unit_id, function, len(payload), *payload]))
        else:
            self._logger("RX", "read", response.error or "ERROR")
        return response

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
        payload = _words_to_bytes(values)
        function = 0x10
        self._logger(
            "TX",
            "write_registers",
            _hex_frame([unit_id, function, *_u16(address), *_u16(len(values)), len(payload), *payload]),
        )
        response = self._transport.write_registers(address, values, unit_id)
        if response.ok:
            self._logger("RX", "write_registers", _hex_frame([unit_id, function, *_u16(address), *_u16(len(values))]))
        else:
            self._logger("RX", "write_registers", response.error or "ERROR")
        return response

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse:
        function = 0x05
        coil_value = [0xFF, 0x00] if value else [0x00, 0x00]
        self._logger("TX", "write_coil", _hex_frame([unit_id, function, *_u16(address), *coil_value]))
        response = self._transport.write_coil(address, value, unit_id)
        if response.ok:
            self._logger("RX", "write_coil", _hex_frame([unit_id, function, *_u16(address), *coil_value]))
        else:
            self._logger("RX", "write_coil", response.error or "ERROR")
        return response


def _read_function_code(kind: RegisterKind) -> int:
    if kind is RegisterKind.COIL:
        return 0x01
    if kind is RegisterKind.DISCRETE_INPUT:
        return 0x02
    if kind is RegisterKind.HOLDING:
        return 0x03
    if kind is RegisterKind.INPUT:
        return 0x04
    raise ValueError(f"Unsupported register kind: {kind}")


def _u16(value: int) -> list[int]:
    return [(value >> 8) & 0xFF, value & 0xFF]


def _words_to_bytes(values: list[int]) -> list[int]:
    bytes_out: list[int] = []
    for value in values:
        bytes_out.extend(_u16(value))
    return bytes_out


def _bits_to_response_bytes(values: list[int], count: int) -> list[int]:
    byte_count = (count + 7) // 8
    payload = [0] * byte_count
    for index, value in enumerate(values[:count]):
        if value:
            payload[index // 8] |= 1 << (index % 8)
    return payload


def _hex_frame(payload: list[int]) -> str:
    frame = [value & 0xFF for value in payload]
    crc = _modbus_crc(frame)
    frame.extend([crc & 0xFF, (crc >> 8) & 0xFF])
    return " ".join(f"{value:02X}" for value in frame)


def _modbus_crc(values: list[int]) -> int:
    crc = 0xFFFF
    for value in values:
        crc ^= value
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _sample_one_variable(
    device: FlowmeterDevice,
    *,
    repository: StorageRepository,
    identity: DeviceIdentity,
    variable_name: str,
) -> VariableSample:
    reader = getattr(device, "read_configuration_parameters", None)
    if callable(reader):
        parameters = reader((variable_name,))
    else:
        parameters = device.read_configuration()
    parameter = _find_parameter(parameters, variable_name)
    captured_at = datetime.now(UTC)
    sample = VariableSample(
        sample_id=f"VAR-{uuid4().hex}",
        device_id=identity.device_id,
        variable_name=parameter.name,
        captured_at=captured_at,
        value=parameter.value,
        unit=parameter.unit,
        source_channel=identity.protocol_address or identity.device_id,
    )
    repository.save_variable_sample(
        VariableSampleRecord(
            sample_id=sample.sample_id,
            device_id=sample.device_id,
            run_id=sample.run_id,
            step_id=sample.step_id,
            variable_name=sample.variable_name,
            captured_at=sample.captured_at,
            value=sample.value,
            unit=sample.unit,
            source_channel=sample.source_channel,
            metadata=parameter.metadata,
        )
    )
    return sample


def _pre_calibration_snapshot(
    device: FlowmeterDevice,
    names: tuple[str, ...],
) -> tuple[dict[str, object], datetime | None]:
    unique_names = _unique_names(names)
    if not unique_names:
        return {}, None
    parameters = _read_selected_parameters(
        device,
        unique_names,
        merge_adjacent=False,
    )
    captured_at = datetime.now(UTC)
    return (
        {
            name: _json_metric_value(_find_parameter(parameters, name).value)
            for name in unique_names
        },
        captured_at,
    )


def _k_factor_simple_metrics(result: ModbusKFactorSimpleResult) -> dict[str, object]:
    metrics: dict[str, object] = {
        "mode": "simple",
        "flow_rate_parameter": result.flow_rate_parameter,
        "flow_acc_parameter": result.flow_acc_parameter,
        "k_factor_parameter": result.k_factor_parameter,
        "mass_acc_before": result.mass_acc_before,
        "mass_acc_after": result.mass_acc_after,
        "measured_mass_delta": result.measured_mass_delta,
        "standard_mass": result.standard_mass,
        "current_k_factor": result.current_k_factor,
        "corrected_k_factor": result.corrected_k_factor,
        "mean_flow": result.mean_flow,
        "instant_flow": result.instant_flow,
        "flow_rate_source": result.flow_rate_source,
        "duration_s": result.duration_s,
        "flow_started_at": result.flow_started_at.isoformat(),
        "flow_instant_at": result.flow_instant_at.isoformat(),
        "flow_ended_at": result.flow_ended_at.isoformat(),
        "poll_interval_s": result.poll_interval_s,
        "write_requested": result.write_requested,
        "write_status": result.write_status,
        "write_verified": result.write_verified,
        "history_saved": result.history_saved,
    }
    if result.raw_artifact_id is not None:
        metrics["raw_artifact_id"] = result.raw_artifact_id
    if result.test_session_id is not None:
        metrics["test_session_id"] = result.test_session_id
    if result.readback_k_factor is not None:
        metrics["readback_k_factor"] = result.readback_k_factor
    if result.audit_id:
        metrics["audit_id"] = result.audit_id
    if result.pre_snapshot:
        metrics["pre_snapshot"] = result.pre_snapshot
    if result.pre_snapshot_captured_at is not None:
        metrics["pre_snapshot_captured_at"] = result.pre_snapshot_captured_at.isoformat()
    return metrics


def _repeatability_simple_metrics(
    result: ModbusRepeatabilitySimpleResult,
    *,
    calculated_at: datetime | None = None,
) -> dict[str, object]:
    record_started_at = calculated_at or datetime.now(UTC)
    metrics: dict[str, object] = {
        "mode": result.mode,
        "flow_rate_parameter": result.flow_rate_parameter,
        "flow_acc_parameter": result.flow_acc_parameter,
        "poll_interval_s": result.poll_interval_s,
        "expected_trials_per_point": result.expected_trials_per_point,
        "history_saved": result.history_saved,
        "started_at": record_started_at.isoformat(),
        "ended_at": record_started_at.isoformat(),
        "source_trial_started_at": result.started_at.isoformat(),
        "source_trial_ended_at": result.ended_at.isoformat(),
        **result.analysis.summary_metrics,
        "trials": [
            {
                "flow_point": trial.flow_point,
                "trial_index": trial.trial_index,
                "k_factor_parameter": trial.k_factor_parameter,
                "original_k_factor": trial.original_k_factor,
                "mass_acc_before": trial.mass_acc_before,
                "mass_acc_after": trial.mass_acc_after,
                "measured_mass_delta": trial.measured_mass_delta,
                "standard_mass": trial.standard_mass,
                "percent_error": trial.percent_error,
                "mean_flow": trial.mean_flow,
                "instant_flow": trial.instant_flow,
                "flow_rate_source": trial.flow_rate_source,
                "trial_status": trial.trial_status,
                "raw_artifact_id": trial.raw_artifact_id,
                "flow_samples_artifact_id": trial.flow_samples_artifact_id,
                "flow_sample_count": trial.flow_sample_count,
                "trial_sample_variable_names": list(trial.trial_sample_variable_names),
                "test_session_id": trial.test_session_id,
                "duration_s": trial.duration_s,
                "flow_started_at": trial.flow_started_at.isoformat(),
                "flow_instant_at": trial.flow_instant_at.isoformat(),
                "flow_ended_at": trial.flow_ended_at.isoformat(),
                "pre_snapshot": trial.pre_snapshot,
                "pre_snapshot_captured_at": (
                    trial.pre_snapshot_captured_at.isoformat()
                    if trial.pre_snapshot_captured_at is not None
                    else None
                ),
                "post_snapshot": trial.post_snapshot,
                "post_snapshot_captured_at": (
                    trial.post_snapshot_captured_at.isoformat()
                    if trial.post_snapshot_captured_at is not None
                    else None
                ),
                "notes": trial.notes,
            }
            for trial in result.trials
        ],
        "flow_points": [
            {
                "flow_point": point.flow_point,
                "repeatability_stddev_percent": point.repeatability_stddev_percent,
                "trial_errors": [
                    trial.percent_error
                    for trial in point.trials
                ],
            }
            for point in result.analysis.flow_points
        ],
    }
    for point in result.analysis.flow_points:
        label = _flow_point_metric_label(point.flow_point)
        metrics[f"{label}_repeatability_stddev_percent"] = (
            point.repeatability_stddev_percent
        )
        for trial in point.trials:
            metrics[f"{label}_trial_{trial.trial_index}_percent_error"] = (
                trial.percent_error
            )
    if result.pre_snapshot:
        metrics["pre_snapshot"] = result.pre_snapshot
    if result.pre_snapshot_captured_at is not None:
        metrics["pre_snapshot_captured_at"] = (
            result.pre_snapshot_captured_at.isoformat()
        )
    return metrics


def _repeatability_trial_artifact_ids(
    trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for trial in trials:
        if trial.raw_artifact_id is not None:
            values.append(trial.raw_artifact_id)
        if trial.flow_samples_artifact_id is not None:
            values.append(trial.flow_samples_artifact_id)
    return tuple(dict.fromkeys(values))


def _flow_point_metric_label(flow_point: float) -> str:
    raw = f"{flow_point:.12g}".replace("-", "neg_")
    return "flow_point_" + raw.replace(".", "_")


def _datetime_from_metric(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return datetime.now(UTC)


def _final_k_artifact_ids(metrics: dict[str, object]) -> tuple[str, ...]:
    values: list[str] = []
    trials = metrics.get("trials")
    if isinstance(trials, list):
        for trial in trials:
            if not isinstance(trial, dict):
                continue
            artifact_id = trial.get("raw_artifact_id")
            if artifact_id:
                values.append(str(artifact_id))
            flow_samples_artifact_id = trial.get("flow_samples_artifact_id")
            if flow_samples_artifact_id:
                values.append(str(flow_samples_artifact_id))
    if not values:
        flow_points = metrics.get("flow_points")
        if isinstance(flow_points, list):
            for flow_point in flow_points:
                if not isinstance(flow_point, dict):
                    continue
                artifact_ids = flow_point.get("raw_artifact_ids")
                if isinstance(artifact_ids, list):
                    values.extend(str(value) for value in artifact_ids if value)
                flow_sample_artifact_ids = flow_point.get("flow_samples_artifact_ids")
                if isinstance(flow_sample_artifact_ids, list):
                    values.extend(
                        str(value) for value in flow_sample_artifact_ids if value
                    )
    return tuple(dict.fromkeys(values))


def _sample_stddev(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)


def _flow_summary_from_trial_records(
    flow_point: float,
    trials: tuple[ModbusTrialRecord, ...],
) -> ModbusRepeatabilityFlowSummary:
    errors = tuple(
        float(trial.percent_error)
        for trial in trials
        if trial.percent_error is not None
    )
    if not errors:
        return ModbusRepeatabilityFlowSummary(
            flow_point=flow_point,
            trial_count=len(trials),
            mean_percent_error=0.0,
            max_abs_percent_error=0.0,
            repeatability_stddev_percent=0.0,
            trial_errors=(),
        )
    return ModbusRepeatabilityFlowSummary(
        flow_point=flow_point,
        trial_count=len(errors),
        mean_percent_error=sum(errors) / len(errors),
        max_abs_percent_error=max(abs(error) for error in errors),
        repeatability_stddev_percent=_sample_stddev(errors),
        trial_errors=errors,
    )


def _latest_record_metrics(
    records: tuple[ModbusCalibrationHistoryEntry, ...],
    operation: str,
) -> dict[str, object] | None:
    matches = [record for record in records if record.operation == operation]
    if not matches:
        return None
    latest = max(
        matches,
        key=lambda record: (
            record.started_at or datetime.min.replace(tzinfo=UTC),
            record.run_id,
        ),
    )
    return {
        "run_id": latest.run_id,
        "operation": latest.operation,
        "status": latest.status,
        "started_at": latest.started_at.isoformat()
        if latest.started_at is not None
        else None,
        **latest.metrics,
    }


def _device_analysis_notes(
    *,
    records: tuple[ModbusCalibrationHistoryEntry, ...],
    trials: tuple[ModbusTrialRecord, ...],
    accepted_trials: tuple[ModbusTrialRecord, ...],
    flow_summaries: tuple[ModbusRepeatabilityFlowSummary, ...],
) -> tuple[str, ...]:
    notes: list[str] = []
    if not records:
        notes.append("No test records found for this device ID.")
    if trials and not accepted_trials:
        notes.append("Trials exist, but none are currently marked accepted.")
    if accepted_trials and len(flow_summaries) < 3:
        notes.append(
            "Accepted repeatability data covers fewer than three flow points."
        )
    for summary in flow_summaries:
        if summary.trial_count < 3:
            notes.append(
                f"Flow {summary.flow_point:g} has fewer than three accepted trials."
            )
    if not any(
        record.operation == "manual_error_repeatability_final_k"
        for record in records
    ):
        notes.append("No final K preview record found for this device yet.")
    return tuple(notes)


def _history_summary_pre_snapshot(summary: dict[str, Any]) -> dict[str, object]:
    value = summary.get("pre_snapshot")
    if isinstance(value, dict):
        return dict(value)
    return {}


def _history_trial_original_k_factor(
    record: ModbusTrialRecord,
    summary: dict[str, Any],
) -> float:
    if record.original_k_factor is not None:
        return float(record.original_k_factor)
    value = summary.get("original_k_factor")
    if value is not None:
        return float(value)
    pre_snapshot = _history_summary_pre_snapshot(summary)
    value = pre_snapshot.get("original_k_factor")
    if value is not None:
        return float(value)
    value = pre_snapshot.get("k_factor")
    if value is not None:
        return float(value)
    return 0.0


def _ensure_selected_history_trials_have_consistent_snapshot(
    history_trials: tuple[ModbusRepeatabilityHistoryTrial, ...],
    *,
    variable_names: tuple[str, ...],
) -> None:
    if not history_trials:
        raise ValueError("No trials selected.")
    expected_k = history_trials[0].trial.original_k_factor
    mismatches: list[str] = []
    for trial in history_trials:
        if trial.trial.original_k_factor != expected_k:
            mismatches.append(
                "old K mismatch: "
                f"trial {trial.trial.flow_point:g}/#{trial.trial.trial_index} "
                f"has {_format_history_value(trial.trial.original_k_factor)}, "
                f"expected {_format_history_value(expected_k)}"
            )
    for variable_name in variable_names:
        expected_missing = variable_name not in history_trials[0].pre_snapshot
        expected = history_trials[0].pre_snapshot.get(variable_name)
        for trial in history_trials:
            if variable_name not in trial.pre_snapshot:
                mismatches.append(
                    f"{variable_name} missing: "
                    f"trial {trial.trial.flow_point:g}/#{trial.trial.trial_index}"
                )
                continue
            value = trial.pre_snapshot.get(variable_name)
            if expected_missing:
                mismatches.append(
                    f"{variable_name} mismatch: "
                    f"trial {trial.trial.flow_point:g}/#{trial.trial.trial_index} "
                    f"has {_format_history_value(value)}, "
                    "expected a captured value on all selected trials"
                )
            elif value != expected:
                mismatches.append(
                    f"{variable_name} mismatch: "
                    f"trial {trial.trial.flow_point:g}/#{trial.trial.trial_index} "
                    f"has {_format_history_value(value)}, "
                    f"expected {_format_history_value(expected)}"
                )
    if mismatches:
        raise ValueError(
            "Selected trials do not share the same pre-calibration values:\n"
            + "\n".join(mismatches)
        )


def _device_analysis_repeatability_report_text(
    *,
    metrics: dict[str, object],
    selected_trials_by_flow: dict[
        float,
        tuple[ModbusRepeatabilityHistoryTrial, ...],
    ],
    comparison_variable_names: tuple[str, ...],
) -> str:
    history_trials = tuple(
        history_trial
        for trials in selected_trials_by_flow.values()
        for history_trial in trials
    )
    snapshot = history_trials[0].pre_snapshot if history_trials else {}
    lines = [
        "Selected Trial Final K Report",
        "",
        "Pre-Calibration Consistency Values:",
        f"old_k: {_format_k_value(metrics.get('original_k_factor'))}",
    ]
    for variable_name in comparison_variable_names:
        lines.append(
            f"{variable_name}: {_format_history_value(snapshot.get(variable_name))}"
        )
    lines.extend(
        [
            "",
            "Selected Trials:",
            "flow_point\ttrial\tstarted_at\terror_percent\tstandard_mass\tmeasured_delta\tv1\tv_mean\told_k\tcompare_values",
        ]
    )
    for flow_point in sorted(selected_trials_by_flow):
        trials = selected_trials_by_flow[flow_point]
        for history_trial in trials:
            trial = history_trial.trial
            compare_values = "; ".join(
                f"{variable_name}={_format_history_value(history_trial.pre_snapshot.get(variable_name))}"
                for variable_name in comparison_variable_names
            )
            lines.append(
                "\t".join(
                    (
                        _format_history_value(flow_point),
                        str(trial.trial_index),
                        trial.flow_started_at.isoformat(),
                        _format_history_value(trial.percent_error),
                        _format_history_value(trial.standard_mass),
                        _format_history_value(trial.measured_mass_delta),
                        _format_history_value(trial.instant_flow),
                        _format_history_value(trial.mean_flow),
                        _format_k_value(trial.original_k_factor),
                        compare_values,
                    )
                )
            )
    flow_rows = metrics.get("flow_points")
    if isinstance(flow_rows, list):
        lines.append("")
        lines.append("Per-Flow Calculations:")
        lines.append(
            "flow_point\ttrial_indexes\tmeasurement_error_percent\trepeatability_stddev_percent\tadjusted_error_percent\tintermediate_k"
        )
        for row in flow_rows:
            if not isinstance(row, dict):
                continue
            trial_indexes = row.get("trial_indexes")
            if isinstance(trial_indexes, list):
                trial_indexes_text = ",".join(str(value) for value in trial_indexes)
            else:
                trial_indexes_text = _format_history_value(trial_indexes)
            lines.append(
                "\t".join(
                    (
                        _format_history_value(row.get("flow_point")),
                        trial_indexes_text,
                        _format_history_value(row.get("measurement_error_percent")),
                        _format_history_value(row.get("repeatability_stddev_percent")),
                        _format_history_value(row.get("adjusted_error_percent")),
                        _format_k_value(row.get("intermediate_k_factor")),
                    )
                )
            )
    lines.extend(
        [
            "",
            "Final K Calculation:",
            f"average_error_percent: {_format_history_value(metrics.get('average_error'))}",
            f"old_k_factor: {_format_k_value(metrics.get('original_k_factor'))}",
            f"new_k_factor: {_format_k_value(metrics.get('new_k_factor'))}",
            f"delta_k_factor: {_format_k_value(metrics.get('delta_k_factor'))}",
            f"selected_flow_point_count: {_format_history_value(metrics.get('selected_flow_point_count'))}",
            f"selected_trial_count: {_format_history_value(metrics.get('selected_trial_count'))}",
        ]
    )
    return "\n".join(lines)


def _format_history_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _format_k_value(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.12f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _emit_status(
    status_callback: Callable[[str], None] | None,
    message: str,
) -> None:
    if status_callback is None:
        return
    try:
        status_callback(message)
    except Exception:
        return


def _sleep_poll_interval(
    poll_interval_s: float,
    *,
    cancel_requested: Callable[[], bool] | None,
) -> None:
    deadline = monotonic() + poll_interval_s
    while True:
        if cancel_requested is not None and cancel_requested():
            return
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(remaining, 0.05))


def _json_metric_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _csv_metric_value(value: object) -> object:
    value = _json_metric_value(value)
    if value is None:
        return ""
    return value


def _parse_csv_metric_value(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trial_sample_variable_names(
    samples: tuple[ModbusTrialSamplePoint, ...],
    flow_rate_parameter: str,
) -> tuple[str, ...]:
    names: list[str] = []
    for sample in samples:
        for name in sample.values:
            names.append(name)
    if flow_rate_parameter and flow_rate_parameter not in names:
        names.insert(0, flow_rate_parameter)
    return _unique_names(tuple(names))


def _raw_curve_csv(points: tuple[dict[str, object], ...]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "captured_at",
            "phase",
            "variable_name",
            "value",
            "unit",
            "register_kind",
            "address",
            "word_count",
            "data_type",
        ]
    )
    for point in points:
        writer.writerow(
            [
                point.get("captured_at", ""),
                point.get("phase", ""),
                point.get("variable_name", ""),
                point.get("value", ""),
                point.get("unit", ""),
                point.get("register_kind", ""),
                point.get("address", ""),
                point.get("word_count", ""),
                point.get("data_type", ""),
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _flow_samples_csv(
    samples: tuple[ModbusTrialSamplePoint, ...],
    variable_names: tuple[str, ...],
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    variable_names = _unique_names(variable_names)
    writer.writerow(["captured_at", "elapsed_s", "sample_index", *variable_names])
    first_at = samples[0].captured_at if samples else None
    for index, sample in enumerate(samples, start=1):
        elapsed = (
            (sample.captured_at - first_at).total_seconds()
            if first_at is not None
            else 0.0
        )
        writer.writerow(
            [
                sample.captured_at.isoformat(),
                elapsed,
                index,
                *[
                    _csv_metric_value(sample.values.get(variable_name))
                    for variable_name in variable_names
                ],
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _flow_samples_from_csv(content: str) -> tuple[ModbusTrialSamplePoint, ...]:
    reader = csv.DictReader(io.StringIO(content))
    samples: list[ModbusTrialSamplePoint] = []
    metadata_columns = {"captured_at", "elapsed_s", "sample_index"}
    variable_names = tuple(
        column
        for column in (reader.fieldnames or ())
        if column not in metadata_columns and column
    )
    for row_number, row in enumerate(reader, start=2):
        captured_at_text = (row.get("captured_at") or "").strip()
        if not captured_at_text:
            raise ValueError(f"Invalid flow-sample CSV row {row_number}.")
        try:
            captured_at = datetime.fromisoformat(captured_at_text)
        except ValueError as exc:
            raise ValueError(f"Invalid flow-sample CSV row {row_number}.") from exc
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=UTC)
        values: dict[str, object] = {}
        for variable_name in variable_names:
            value_text = (row.get(variable_name) or "").strip()
            if not value_text:
                continue
            values[variable_name] = _parse_csv_metric_value(value_text)
        if not values:
            raise ValueError(f"Invalid flow-sample CSV row {row_number}.")
        if "flow_rate" in values and len(variable_names) == 1:
            values["mass_rate"] = values.pop("flow_rate")
        samples.append(ModbusTrialSamplePoint(captured_at=captured_at, values=values))
    return tuple(samples)


def _register_unit(register_map: ModbusRegisterMap, variable_name: str) -> str:
    try:
        return register_map.by_name(variable_name).unit or ""
    except KeyError:
        return ""


def _zero_calibration_raw_points(
    record: ZeroCalibrationRecord,
    pre_snapshot: dict[str, object],
) -> tuple[dict[str, object], ...]:
    points: list[dict[str, object]] = []
    for variable_name, value in pre_snapshot.items():
        points.append(
            {
                "captured_at": record.before.captured_at.isoformat(),
                "phase": "pre_snapshot",
                "variable_name": variable_name,
                "value": value,
                "unit": "",
            }
        )
    points.extend(
        [
            {
                "captured_at": record.before.captured_at.isoformat(),
                "phase": "before_zero",
                "variable_name": "zero_offset",
                "value": record.before.zero_offset,
                "unit": "",
            },
            {
                "captured_at": record.before.captured_at.isoformat(),
                "phase": "before_zero",
                "variable_name": "delta_t",
                "value": record.before.delta_t,
                "unit": "",
            },
            {
                "captured_at": record.after.captured_at.isoformat(),
                "phase": "after_zero",
                "variable_name": "zero_offset",
                "value": record.after.zero_offset,
                "unit": "",
            },
            {
                "captured_at": record.after.captured_at.isoformat(),
                "phase": "after_zero",
                "variable_name": "delta_t",
                "value": record.after.delta_t,
                "unit": "",
            },
            {
                "captured_at": record.after.captured_at.isoformat(),
                "phase": "completion",
                "variable_name": record.control_parameter,
                "value": record.completed,
                "unit": "",
            },
        ]
    )
    return tuple(points)


def _operation_metadata_from_configuration(
    configuration: dict[str, object],
) -> dict[str, str]:
    return {
        key: str(value)
        for key in (
            "device_model",
            "tube_model",
            "transmitter_model",
        )
        if (value := configuration.get(key)) not in (None, "")
    }


def _metadata_filter_matches(
    metadata: dict[str, object],
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


def _validate_modbus_device_id(device_id: str) -> str:
    value = device_id.strip()
    if not value:
        raise ValueError("Device ID is required.")
    if value.isdigit():
        raise ValueError("Device ID must be a stable asset ID, not a numeric Modbus unit ID.")
    if value.lower().startswith("modbus:"):
        raise ValueError("Device ID must not be derived from port and Modbus unit ID.")
    return value


def _connection_settings_to_payload(
    settings: ModbusConnectionSettings,
) -> dict[str, object]:
    return {
        "port": settings.port,
        "unit_id": settings.unit_id,
        "baudrate": settings.baudrate,
        "parity": settings.parity,
        "stop_bits": settings.stop_bits,
        "order": settings.order,
        "read_timeout_s": settings.read_timeout_s,
        "write_timeout_s": settings.write_timeout_s,
        "retry_count": settings.retry_count,
    }


def _register_map_payload(register_map: ModbusRegisterMap) -> dict[str, object]:
    return json.loads(register_map_to_json(register_map))


def _register_map_from_payload(
    payload: dict[str, Any],
) -> ModbusRegisterMap | None:
    if not payload:
        return None
    try:
        return register_map_from_json(json.dumps(payload))
    except Exception:
        return None


def _profile_from_record(record: ModbusDeviceProfileRecord) -> ModbusDeviceProfile:
    return ModbusDeviceProfile(
        device_id=record.device_id,
        display_name=record.display_name or "",
        device_model=record.device_model or "",
        tube_model=record.tube_model or "",
        transmitter_model=record.transmitter_model or "",
        connection_settings=dict(record.connection_settings),
        register_map=_register_map_from_payload(record.register_map),
        notes=record.notes or "",
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _device_record_to_history_payload(record: DeviceRecord) -> dict[str, object]:
    return {
        "device_id": record.device_id,
        "device_type": record.device_type,
        "serial_number": record.serial_number,
        "model": record.model,
        "firmware_version": record.firmware_version,
        "hardware_version": record.hardware_version,
        "protocol_address": record.protocol_address,
        "connection_metadata": record.connection_metadata,
        "created_at": _datetime_to_history_payload(record.created_at),
        "updated_at": _datetime_to_history_payload(record.updated_at),
    }


def _device_record_from_history_payload(value: object) -> DeviceRecord | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("device must be an object")
    device_id = _required_history_str(value, "device_id")
    device_type = _history_str(value, "device_type") or DeviceType.MODBUS_RTU.value
    return DeviceRecord(
        device_id=device_id,
        device_type=device_type,
        serial_number=_history_str(value, "serial_number"),
        model=_history_str(value, "model"),
        firmware_version=_history_str(value, "firmware_version"),
        hardware_version=_history_str(value, "hardware_version"),
        protocol_address=_history_str(value, "protocol_address"),
        connection_metadata=_history_dict(value, "connection_metadata"),
        created_at=_history_datetime(value.get("created_at")),
        updated_at=_history_datetime(value.get("updated_at")),
    )


def _run_session_to_history_payload(run: RunSession) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "run_type": run.run_type.value,
        "workflow_name": run.workflow_name,
        "workflow_version": run.workflow_version,
        "device_id": run.device_id,
        "operator": run.operator,
        "status": run.status.value,
        "started_at": _datetime_to_history_payload(run.started_at),
        "ended_at": _datetime_to_history_payload(run.ended_at),
        "configuration_snapshot": run.configuration_snapshot,
        "software_version": run.software_version,
        "notes": run.notes,
    }


def _run_session_from_history_payload(value: object) -> RunSession:
    if not isinstance(value, dict):
        raise ValueError("run must be an object")
    return RunSession(
        run_id=_required_history_str(value, "run_id"),
        run_type=RunType(_required_history_str(value, "run_type")),
        workflow_name=_required_history_str(value, "workflow_name"),
        workflow_version=_required_history_str(value, "workflow_version"),
        device_id=_required_history_str(value, "device_id"),
        operator=_required_history_str(value, "operator"),
        status=RunStatus(_required_history_str(value, "status")),
        started_at=_history_datetime(value.get("started_at")),
        ended_at=_history_datetime(value.get("ended_at")),
        configuration_snapshot=_history_dict(value, "configuration_snapshot"),
        software_version=_history_str(value, "software_version"),
        notes=_history_str(value, "notes"),
    )


def _workflow_step_to_history_payload(step: WorkflowStep) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "run_id": step.run_id,
        "name": step.name,
        "step_type": step.step_type.value,
        "status": step.status.value,
        "started_at": _datetime_to_history_payload(step.started_at),
        "ended_at": _datetime_to_history_payload(step.ended_at),
        "input_configuration": step.input_configuration,
        "output_summary": step.output_summary,
        "error_message": step.error_message,
    }


def _workflow_steps_from_history_payload(value: object) -> tuple[WorkflowStep, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("workflow_steps must be a list")
    return tuple(_workflow_step_from_history_payload(item) for item in value)


def _workflow_step_from_history_payload(value: object) -> WorkflowStep:
    if not isinstance(value, dict):
        raise ValueError("workflow step must be an object")
    return WorkflowStep(
        step_id=_required_history_str(value, "step_id"),
        run_id=_required_history_str(value, "run_id"),
        name=_required_history_str(value, "name"),
        step_type=WorkflowStepType(_required_history_str(value, "step_type")),
        status=WorkflowStepStatus(_required_history_str(value, "status")),
        started_at=_history_datetime(value.get("started_at")),
        ended_at=_history_datetime(value.get("ended_at")),
        input_configuration=_history_dict(value, "input_configuration"),
        output_summary=_history_dict(value, "output_summary"),
        error_message=_history_str(value, "error_message"),
    )


def _analysis_result_to_history_payload(
    result: AnalysisResultRecord,
) -> dict[str, object]:
    return {
        "result_id": result.result_id,
        "run_id": result.run_id,
        "step_id": result.step_id,
        "result_type": result.result_type,
        "algorithm_name": result.algorithm_name,
        "algorithm_version": result.algorithm_version,
        "input_artifact_ids": list(result.input_artifact_ids),
        "configuration_snapshot": result.configuration_snapshot,
        "summary_metrics": result.summary_metrics,
        "pass_fail_decision": result.pass_fail_decision,
        "created_at": _datetime_to_history_payload(result.created_at),
    }


def _artifact_to_history_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "run_id": artifact.run_id,
        "step_id": artifact.step_id,
        "artifact_type": artifact.artifact_type.value,
        "file_path": str(artifact.file_path),
        "file_format": artifact.file_format,
        "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum,
        "created_at": _datetime_to_history_payload(artifact.created_at),
        "metadata": artifact.metadata,
    }


def _artifacts_from_history_payload(value: object) -> tuple[Artifact, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("artifacts must be a list")
    return tuple(_artifact_from_history_payload(item) for item in value)


def _artifact_from_history_payload(value: object) -> Artifact:
    if not isinstance(value, dict):
        raise ValueError("artifact must be an object")
    return Artifact(
        artifact_id=_required_history_str(value, "artifact_id"),
        run_id=_required_history_str(value, "run_id"),
        step_id=_history_str(value, "step_id"),
        artifact_type=ArtifactType(_required_history_str(value, "artifact_type")),
        file_path=Path(_required_history_str(value, "file_path")),
        file_format=_required_history_str(value, "file_format"),
        size_bytes=_history_optional_int(value.get("size_bytes")),
        checksum=_history_str(value, "checksum"),
        created_at=_history_datetime(value.get("created_at")),
        metadata=_history_dict(value, "metadata"),
    )


def _artifact_payloads_by_id(value: object) -> dict[str, dict[str, object]]:
    if not isinstance(value, list):
        return {}
    payloads: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        artifact_id = item.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            payloads[artifact_id] = item
    return payloads


def _restore_imported_artifact_content(
    artifact_store: ArtifactStore,
    artifact: Artifact,
    raw_payload: dict[str, object],
) -> None:
    content_value = raw_payload.get("content_base64")
    if not isinstance(content_value, str) or not content_value:
        return
    if raw_payload.get("content_encoding", "base64") != "base64":
        raise ValueError(f"unsupported artifact content encoding: {artifact.artifact_id}")
    try:
        content = base64.b64decode(content_value.encode("ascii"), validate=True)
    except Exception as exc:
        raise ValueError(f"invalid artifact content for {artifact.artifact_id}: {exc}") from exc
    if artifact.checksum and _history_content_sha256(content) != artifact.checksum:
        raise ValueError(f"artifact content checksum mismatch: {artifact.artifact_id}")
    target = artifact_store.resolve(artifact.file_path)
    data_root = artifact_store.data_root.resolve()
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(data_root)
    except ValueError as exc:
        raise ValueError(
            f"artifact path escapes data root: {artifact.file_path}"
        ) from exc
    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    resolved_target.write_bytes(content)


def _history_content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _test_session_to_history_payload(
    session: ModbusTestSessionRecord,
) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "device_id": session.device_id,
        "profile_id": session.profile_id,
        "operator": session.operator,
        "status": session.status,
        "started_at": _datetime_to_history_payload(session.started_at),
        "ended_at": _datetime_to_history_payload(session.ended_at),
        "device_metadata": session.device_metadata,
        "register_map_snapshot": session.register_map_snapshot,
        "notes": session.notes,
    }


def _test_sessions_from_history_payload(
    value: object,
) -> tuple[ModbusTestSessionRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("test_sessions must be a list")
    return tuple(_test_session_from_history_payload(item) for item in value)


def _test_session_from_history_payload(value: object) -> ModbusTestSessionRecord:
    if not isinstance(value, dict):
        raise ValueError("test session must be an object")
    started_at = _history_datetime(value.get("started_at"))
    if started_at is None:
        raise ValueError("missing started_at")
    return ModbusTestSessionRecord(
        session_id=_required_history_str(value, "session_id"),
        device_id=_required_history_str(value, "device_id"),
        profile_id=None,
        operator=_required_history_str(value, "operator"),
        status=_required_history_str(value, "status"),
        started_at=started_at,
        ended_at=_history_datetime(value.get("ended_at")),
        device_metadata=_history_dict(value, "device_metadata"),
        register_map_snapshot=_history_dict(value, "register_map_snapshot"),
        notes=_history_str(value, "notes"),
    )


def _operation_attempt_to_history_payload(
    attempt: ModbusOperationAttemptRecord,
) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "session_id": attempt.session_id,
        "run_id": attempt.run_id,
        "device_id": attempt.device_id,
        "operation_type": attempt.operation_type,
        "status": attempt.status,
        "started_at": _datetime_to_history_payload(attempt.started_at),
        "ended_at": _datetime_to_history_payload(attempt.ended_at),
        "operator": attempt.operator,
        "device_metadata": attempt.device_metadata,
        "register_map_snapshot": attempt.register_map_snapshot,
        "raw_artifact_id": attempt.raw_artifact_id,
        "summary": attempt.summary,
        "notes": attempt.notes,
    }


def _trial_record_to_history_payload(trial: ModbusTrialRecord) -> dict[str, object]:
    return {
        "trial_id": trial.trial_id,
        "session_id": trial.session_id,
        "attempt_id": trial.attempt_id,
        "run_id": trial.run_id,
        "device_id": trial.device_id,
        "flow_point": trial.flow_point,
        "trial_index": trial.trial_index,
        "trial_status": trial.trial_status,
        "k_factor_parameter": trial.k_factor_parameter,
        "original_k_factor": trial.original_k_factor,
        "mass_acc_before": trial.mass_acc_before,
        "mass_acc_after": trial.mass_acc_after,
        "measured_mass_delta": trial.measured_mass_delta,
        "standard_mass": trial.standard_mass,
        "percent_error": trial.percent_error,
        "mean_flow": trial.mean_flow,
        "instant_flow": trial.instant_flow,
        "flow_started_at": _datetime_to_history_payload(trial.flow_started_at),
        "flow_instant_at": _datetime_to_history_payload(trial.flow_instant_at),
        "flow_ended_at": _datetime_to_history_payload(trial.flow_ended_at),
        "raw_artifact_id": trial.raw_artifact_id,
        "device_metadata": trial.device_metadata,
        "notes": trial.notes,
    }


def _operation_attempts_from_history_payload(
    value: object,
) -> tuple[ModbusOperationAttemptRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("operation_attempts must be a list")
    return tuple(_operation_attempt_from_history_payload(item) for item in value)


def _operation_attempt_from_history_payload(
    value: object,
) -> ModbusOperationAttemptRecord:
    if not isinstance(value, dict):
        raise ValueError("operation attempt must be an object")
    return ModbusOperationAttemptRecord(
        attempt_id=_required_history_str(value, "attempt_id"),
        session_id=_history_str(value, "session_id"),
        run_id=_history_str(value, "run_id"),
        device_id=_required_history_str(value, "device_id"),
        operation_type=_required_history_str(value, "operation_type"),
        status=_required_history_str(value, "status"),
        started_at=_history_datetime(value.get("started_at")),
        ended_at=_history_datetime(value.get("ended_at")),
        operator=_required_history_str(value, "operator"),
        device_metadata=_history_dict(value, "device_metadata"),
        register_map_snapshot=_history_dict(value, "register_map_snapshot"),
        raw_artifact_id=_history_str(value, "raw_artifact_id"),
        summary=_history_dict(value, "summary"),
        notes=_history_str(value, "notes"),
    )


def _trial_records_from_history_payload(
    value: object,
) -> tuple[ModbusTrialRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("trial_records must be a list")
    return tuple(_trial_record_from_history_payload(item) for item in value)


def _trial_record_from_history_payload(value: object) -> ModbusTrialRecord:
    if not isinstance(value, dict):
        raise ValueError("trial record must be an object")
    return ModbusTrialRecord(
        trial_id=_required_history_str(value, "trial_id"),
        session_id=_history_str(value, "session_id"),
        attempt_id=_history_str(value, "attempt_id"),
        run_id=_history_str(value, "run_id"),
        device_id=_required_history_str(value, "device_id"),
        flow_point=_required_history_float(value, "flow_point"),
        trial_index=_required_history_int(value, "trial_index"),
        trial_status=_required_history_str(value, "trial_status"),
        k_factor_parameter=_history_str(value, "k_factor_parameter"),
        original_k_factor=_history_optional_float(value.get("original_k_factor")),
        mass_acc_before=_history_optional_float(value.get("mass_acc_before")),
        mass_acc_after=_history_optional_float(value.get("mass_acc_after")),
        measured_mass_delta=_history_optional_float(value.get("measured_mass_delta")),
        standard_mass=_history_optional_float(value.get("standard_mass")),
        percent_error=_history_optional_float(value.get("percent_error")),
        mean_flow=_history_optional_float(value.get("mean_flow")),
        instant_flow=_history_optional_float(value.get("instant_flow")),
        flow_started_at=_history_datetime(value.get("flow_started_at")),
        flow_instant_at=_history_datetime(value.get("flow_instant_at")),
        flow_ended_at=_history_datetime(value.get("flow_ended_at")),
        raw_artifact_id=_history_str(value, "raw_artifact_id"),
        device_metadata=_history_dict(value, "device_metadata"),
        notes=_history_str(value, "notes"),
    )


def _analysis_results_from_history_payload(
    value: object,
) -> tuple[AnalysisResultRecord, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("analysis_results must be a list")
    return tuple(_analysis_result_from_history_payload(item) for item in value)


def _analysis_result_from_history_payload(value: object) -> AnalysisResultRecord:
    if not isinstance(value, dict):
        raise ValueError("analysis result must be an object")
    input_artifact_ids = value.get("input_artifact_ids", [])
    if not isinstance(input_artifact_ids, list):
        raise ValueError("input_artifact_ids must be a list")
    return AnalysisResultRecord(
        result_id=_required_history_str(value, "result_id"),
        run_id=_required_history_str(value, "run_id"),
        step_id=_history_str(value, "step_id"),
        result_type=_required_history_str(value, "result_type"),
        algorithm_name=_required_history_str(value, "algorithm_name"),
        algorithm_version=_required_history_str(value, "algorithm_version"),
        input_artifact_ids=tuple(str(item) for item in input_artifact_ids),
        configuration_snapshot=_history_dict(value, "configuration_snapshot"),
        summary_metrics=_history_dict(value, "summary_metrics"),
        pass_fail_decision=_history_str(value, "pass_fail_decision"),
        created_at=_history_datetime(value.get("created_at")),
    )


def _history_run_already_imported(
    repository: StorageRepository,
    run: RunSession,
    analysis_results: tuple[AnalysisResultRecord, ...],
) -> bool:
    existing = repository.get_run(run.run_id)
    if existing is None:
        return False
    existing_results = repository.list_analysis_results(run.run_id)
    return (
        existing.workflow_name == run.workflow_name
        and getattr(existing.status, "value", str(existing.status))
        == getattr(run.status, "value", str(run.status))
        and existing.started_at == run.started_at
        and [result.summary_metrics for result in existing_results]
        == [result.summary_metrics for result in analysis_results]
    )


def _normalize_imported_raw_capture_run_status(
    run: RunSession,
    raw_entry: dict[str, object],
) -> RunSession:
    if getattr(run.status, "value", str(run.status)) != RunStatus.RUNNING.value:
        return run
    if not run.configuration_snapshot.get("raw_capture_only"):
        return run
    attempts = _operation_attempts_from_history_payload(
        raw_entry.get("operation_attempts")
    )
    relevant_attempts = tuple(
        attempt for attempt in attempts if attempt.run_id == run.run_id
    )
    if not relevant_attempts:
        return run
    latest_attempt = sorted(
        relevant_attempts,
        key=lambda attempt: (
            attempt.ended_at
            or attempt.started_at
            or datetime.min.replace(tzinfo=UTC),
            attempt.attempt_id,
        ),
    )[-1]
    run_status = _raw_capture_run_status_from_attempt_status(latest_attempt.status)
    configuration = dict(run.configuration_snapshot)
    configuration["raw_capture_completed"] = True
    configuration["raw_capture_attempt_status"] = latest_attempt.status
    if latest_attempt.raw_artifact_id is not None:
        configuration["raw_artifact_id"] = latest_attempt.raw_artifact_id
    return replace(
        run,
        status=run_status,
        ended_at=latest_attempt.ended_at or run.ended_at or latest_attempt.started_at,
        configuration_snapshot=configuration,
    )


def _raw_capture_run_status_from_attempt_status(status: str) -> RunStatus:
    return _RAW_CAPTURE_IMPORT_STATUS_FALLBACKS.get(str(status), RunStatus.ERROR)


def _ensure_import_test_sessions(
    test_sessions: tuple[ModbusTestSessionRecord, ...],
    operation_attempts: tuple[ModbusOperationAttemptRecord, ...],
    trial_records: tuple[ModbusTrialRecord, ...],
    run: RunSession,
) -> tuple[ModbusTestSessionRecord, ...]:
    """Backfill sessions for older history exports that predate session payloads."""

    sessions_by_id = {session.session_id: session for session in test_sessions}
    referenced_session_ids: set[str] = set()
    referenced_session_ids.update(
        attempt.session_id
        for attempt in operation_attempts
        if attempt.session_id is not None
    )
    referenced_session_ids.update(
        trial.session_id for trial in trial_records if trial.session_id is not None
    )
    metadata = dict(run.configuration_snapshot)
    if "device_id" not in metadata:
        metadata["device_id"] = run.device_id
    for session_id in referenced_session_ids:
        if session_id in sessions_by_id:
            continue
        sessions_by_id[session_id] = ModbusTestSessionRecord(
            session_id=session_id,
            device_id=run.device_id,
            profile_id=None,
            operator=run.operator,
            status=getattr(run.status, "value", str(run.status)),
            started_at=run.started_at or datetime.now(UTC),
            ended_at=run.ended_at,
            device_metadata=metadata,
            notes=f"Backfilled during history import for run {run.run_id}.",
        )
    return tuple(sessions_by_id.values())


def _retarget_imported_device_record(
    device: DeviceRecord,
    *,
    target_device_id: str,
    original_device_id: str,
) -> DeviceRecord:
    metadata = dict(device.connection_metadata)
    if original_device_id != target_device_id:
        metadata.setdefault("imported_from_device_id", original_device_id)
    return replace(
        device,
        device_id=target_device_id,
        connection_metadata=metadata,
    )


def _retarget_imported_run(
    run: RunSession,
    *,
    target_device_id: str,
) -> RunSession:
    original_device_id = run.device_id
    configuration = _retarget_device_metadata_dict(
        run.configuration_snapshot,
        target_device_id=target_device_id,
        original_device_id=original_device_id,
    )
    notes = run.notes or ""
    if original_device_id != target_device_id:
        import_note = f"Imported for {target_device_id} from {original_device_id}"
        notes = f"{notes}\n{import_note}" if notes else import_note
    return replace(
        run,
        device_id=target_device_id,
        configuration_snapshot=configuration,
        notes=notes,
    )


def _retarget_imported_workflow_step(
    step: WorkflowStep,
    *,
    target_device_id: str,
    original_device_id: str,
) -> WorkflowStep:
    return replace(
        step,
        input_configuration=_retarget_device_metadata_dict(
            step.input_configuration,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
        output_summary=_retarget_device_metadata_dict(
            step.output_summary,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
    )


def _retarget_imported_analysis_result(
    result: AnalysisResultRecord,
    *,
    target_device_id: str,
    original_device_id: str,
) -> AnalysisResultRecord:
    return replace(
        result,
        configuration_snapshot=_retarget_device_metadata_dict(
            result.configuration_snapshot,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
        summary_metrics=_retarget_device_metadata_dict(
            result.summary_metrics,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
    )


def _retarget_imported_artifact(
    artifact: Artifact,
    *,
    target_device_id: str,
    original_device_id: str,
) -> Artifact:
    return replace(
        artifact,
        metadata=_retarget_device_metadata_dict(
            artifact.metadata,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
    )


def _retarget_imported_test_session(
    session: ModbusTestSessionRecord,
    *,
    target_device_id: str,
    original_device_id: str,
) -> ModbusTestSessionRecord:
    return replace(
        session,
        device_id=target_device_id,
        profile_id=None,
        device_metadata=_retarget_device_metadata_dict(
            session.device_metadata,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
    )


def _retarget_imported_operation_attempt(
    attempt: ModbusOperationAttemptRecord,
    *,
    target_device_id: str,
    original_device_id: str,
) -> ModbusOperationAttemptRecord:
    device_metadata = _retarget_device_metadata_dict(
        attempt.device_metadata,
        target_device_id=target_device_id,
        original_device_id=original_device_id,
    )
    summary = _retarget_device_metadata_dict(
        attempt.summary,
        target_device_id=target_device_id,
        original_device_id=original_device_id,
    )
    summary.update(device_metadata)
    return replace(
        attempt,
        device_id=target_device_id,
        device_metadata=device_metadata,
        summary=summary,
    )


def _retarget_imported_trial_record(
    trial: ModbusTrialRecord,
    *,
    target_device_id: str,
    original_device_id: str,
) -> ModbusTrialRecord:
    return replace(
        trial,
        device_id=target_device_id,
        device_metadata=_retarget_device_metadata_dict(
            trial.device_metadata,
            target_device_id=target_device_id,
            original_device_id=original_device_id,
        ),
    )


def _retarget_device_metadata_dict(
    values: dict[str, Any],
    *,
    target_device_id: str,
    original_device_id: str,
) -> dict[str, Any]:
    updated = dict(values)
    if original_device_id != target_device_id:
        updated.setdefault("imported_from_device_id", original_device_id)
    if "device_id" in updated or original_device_id != target_device_id:
        updated["device_id"] = target_device_id
    return updated


def _history_entry_in_time_range(
    entry: ModbusCalibrationHistoryEntry,
    *,
    started_from: datetime | None,
    started_to: datetime | None,
) -> bool:
    if entry.started_at is None:
        return started_from is None and started_to is None
    started_at = _datetime_as_utc(entry.started_at)
    if started_from is not None and started_at < _datetime_as_utc(started_from):
        return False
    if started_to is not None and started_at > _datetime_as_utc(started_to):
        return False
    return True


def _remap_imported_run(
    run: RunSession,
    *,
    original_run_id: str,
    new_run_id: str,
    imported_at: str,
) -> RunSession:
    configuration = dict(run.configuration_snapshot)
    configuration["imported_from_run_id"] = original_run_id
    configuration["imported_at"] = imported_at
    notes = run.notes or ""
    import_note = f"Imported from {original_run_id}"
    notes = f"{notes}\n{import_note}" if notes else import_note
    return replace(
        run,
        run_id=new_run_id,
        configuration_snapshot=configuration,
        notes=notes,
    )


def _import_step_id_map(
    steps: tuple[WorkflowStep, ...],
    *,
    original_run_id: str,
    new_run_id: str,
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for index, step in enumerate(steps, start=1):
        if step.step_id.startswith(original_run_id):
            mapping[step.step_id] = step.step_id.replace(original_run_id, new_run_id, 1)
        else:
            mapping[step.step_id] = f"{new_run_id}-STEP-{index:03d}"
    return mapping


def _import_artifact_id_map(
    artifacts: tuple[Artifact, ...],
    *,
    original_run_id: str,
    new_run_id: str,
) -> dict[str, str]:
    return {
        artifact.artifact_id: _remap_imported_record_id(
            artifact.artifact_id,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        )
        for artifact in artifacts
    }


def _remap_imported_workflow_step(
    step: WorkflowStep,
    *,
    original_run_id: str,
    new_run_id: str,
    step_id_map: dict[str, str],
    imported_at: str,
) -> WorkflowStep:
    input_configuration = dict(step.input_configuration)
    input_configuration["imported_from_run_id"] = original_run_id
    input_configuration["imported_at"] = imported_at
    return replace(
        step,
        step_id=step_id_map.get(step.step_id, f"{new_run_id}-{uuid4().hex[:8]}"),
        run_id=new_run_id,
        input_configuration=input_configuration,
    )


def _remap_imported_artifact(
    artifact: Artifact,
    *,
    original_run_id: str,
    new_run_id: str,
    step_id_map: dict[str, str],
    artifact_id_map: dict[str, str],
    imported_at: str,
) -> Artifact:
    metadata = dict(artifact.metadata)
    metadata["imported_from_run_id"] = original_run_id
    metadata["imported_at"] = imported_at
    metadata["imported_from_artifact_id"] = artifact.artifact_id
    return replace(
        artifact,
        artifact_id=artifact_id_map.get(
            artifact.artifact_id,
            _remap_imported_record_id(
                artifact.artifact_id,
                original_run_id=original_run_id,
                new_run_id=new_run_id,
            ),
        ),
        run_id=new_run_id if artifact.run_id == original_run_id else artifact.run_id,
        step_id=step_id_map.get(artifact.step_id)
        if artifact.step_id is not None
        else None,
        file_path=_remap_imported_artifact_path(
            artifact.file_path,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        ),
        metadata=metadata,
    )


def _remap_imported_artifact_path(
    file_path: Path | object,
    *,
    original_run_id: str,
    new_run_id: str,
) -> Path:
    path = Path(str(file_path))
    parts = tuple(new_run_id if part == original_run_id else part for part in path.parts)
    return Path(*parts)


def _remap_imported_analysis_result(
    result: AnalysisResultRecord,
    *,
    original_run_id: str,
    new_run_id: str,
    step_id_map: dict[str, str],
    artifact_id_map: dict[str, str],
    imported_at: str,
) -> AnalysisResultRecord:
    configuration = dict(result.configuration_snapshot)
    configuration["imported_from_run_id"] = original_run_id
    configuration["imported_at"] = imported_at
    metrics = _remap_metric_artifact_ids(result.summary_metrics, artifact_id_map)
    metrics["imported_from_run_id"] = original_run_id
    return replace(
        result,
        result_id=_remap_imported_result_id(
            result.result_id,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        ),
        run_id=new_run_id,
        step_id=step_id_map.get(result.step_id) if result.step_id is not None else None,
        input_artifact_ids=tuple(
            artifact_id_map.get(artifact_id, artifact_id)
            for artifact_id in result.input_artifact_ids
        ),
        configuration_snapshot=configuration,
        summary_metrics=metrics,
    )


def _remap_imported_operation_attempt(
    attempt: ModbusOperationAttemptRecord,
    *,
    original_run_id: str,
    new_run_id: str,
    artifact_id_map: dict[str, str],
    imported_at: str,
) -> ModbusOperationAttemptRecord:
    summary = _remap_metric_artifact_ids(attempt.summary, artifact_id_map)
    summary["imported_from_run_id"] = original_run_id
    summary["imported_at"] = imported_at
    raw_artifact_id = (
        artifact_id_map.get(attempt.raw_artifact_id, attempt.raw_artifact_id)
        if attempt.raw_artifact_id is not None
        else None
    )
    if attempt.raw_artifact_id is not None:
        summary["raw_artifact_id"] = raw_artifact_id
    return replace(
        attempt,
        attempt_id=_remap_imported_record_id(
            attempt.attempt_id,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        ),
        run_id=new_run_id if attempt.run_id == original_run_id else attempt.run_id,
        raw_artifact_id=raw_artifact_id,
        summary=summary,
    )


def _remap_imported_trial_record(
    trial: ModbusTrialRecord,
    *,
    original_run_id: str,
    new_run_id: str,
    artifact_id_map: dict[str, str],
    imported_at: str,
) -> ModbusTrialRecord:
    metadata = dict(trial.device_metadata)
    metadata["imported_from_run_id"] = original_run_id
    metadata["imported_at"] = imported_at
    raw_artifact_id = (
        artifact_id_map.get(trial.raw_artifact_id, trial.raw_artifact_id)
        if trial.raw_artifact_id is not None
        else None
    )
    return replace(
        trial,
        trial_id=_remap_imported_record_id(
            trial.trial_id,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        ),
        attempt_id=_remap_imported_record_id(
            trial.attempt_id,
            original_run_id=original_run_id,
            new_run_id=new_run_id,
        )
        if trial.attempt_id is not None
        else None,
        run_id=new_run_id if trial.run_id == original_run_id else trial.run_id,
        raw_artifact_id=raw_artifact_id,
        device_metadata=metadata,
    )


def _remap_metric_artifact_ids(
    value: dict[str, Any],
    artifact_id_map: dict[str, str],
) -> dict[str, Any]:
    return {
        key: _remap_metric_artifact_value(key, item, artifact_id_map)
        for key, item in value.items()
    }


def _remap_metric_artifact_value(
    key: str,
    value: Any,
    artifact_id_map: dict[str, str],
) -> Any:
    if key in {
        "raw_artifact_id",
        "flow_samples_artifact_id",
        "report_artifact_id",
    }:
        return artifact_id_map.get(value, value) if value is not None else None
    if key in {
        "raw_artifact_ids",
        "flow_samples_artifact_ids",
        "input_artifact_ids",
    } and isinstance(value, list):
        return [artifact_id_map.get(item, item) for item in value]
    if isinstance(value, dict):
        return _remap_metric_artifact_ids(value, artifact_id_map)
    if isinstance(value, list):
        return [
            _remap_metric_artifact_value("", item, artifact_id_map)
            for item in value
        ]
    return value


def _remap_imported_result_id(
    result_id: str,
    *,
    original_run_id: str,
    new_run_id: str,
) -> str:
    if result_id.startswith(original_run_id):
        return result_id.replace(original_run_id, new_run_id, 1)
    return f"{new_run_id}-{result_id}"


def _remap_imported_record_id(
    value: str,
    *,
    original_run_id: str,
    new_run_id: str,
) -> str:
    if value.startswith(original_run_id):
        return value.replace(original_run_id, new_run_id, 1)
    return f"{new_run_id}-{value}"


def _datetime_to_history_payload(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _history_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("datetime value must be a string")
    return datetime.fromisoformat(value)


def _history_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _history_str_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    if isinstance(value, str) and value:
        return (value,)
    return ()


def _required_history_str(payload: dict[str, object], key: str) -> str:
    value = _history_str(payload, key)
    if not value:
        raise ValueError(f"missing {key}")
    return value


def _required_history_float(payload: dict[str, object], key: str) -> float:
    if key not in payload:
        raise ValueError(f"missing {key}")
    value = _history_optional_float(payload.get(key))
    if value is None:
        raise ValueError(f"missing {key}")
    return value


def _history_optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _history_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _required_history_int(payload: dict[str, object], key: str) -> int:
    if key not in payload:
        raise ValueError(f"missing {key}")
    value = payload.get(key)
    if value in (None, ""):
        raise ValueError(f"missing {key}")
    return int(value)


def _history_dict(payload: dict[str, object], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _unique_names(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in names if name))


def _read_selected_parameters(
    device: FlowmeterDevice,
    variable_names: tuple[str, ...],
    *,
    merge_adjacent: bool,
) -> tuple[ConfigurationParameter, ...]:
    reader = getattr(device, "read_configuration_parameters", None)
    if callable(reader):
        try:
            return reader(variable_names, merge_adjacent=merge_adjacent)
        except TypeError:
            return reader(variable_names)
    parameters = device.read_configuration()
    names = set(variable_names)
    return tuple(parameter for parameter in parameters if parameter.name in names)


def _read_sampling_parameters(
    device: FlowmeterDevice,
    variable_names: tuple[str, ...],
) -> tuple[tuple[ConfigurationParameter, ...], tuple[str, ...]]:
    parameters: list[ConfigurationParameter] = []
    errors: list[str] = []
    for variable_name in variable_names:
        try:
            parameters.extend(
                _read_selected_parameters(
                    device,
                    (variable_name,),
                    merge_adjacent=False,
                )
            )
        except Exception as exc:
            errors.append(f"{variable_name}: {exc}")
    return tuple(parameters), tuple(errors)


def _samples_from_parameters(
    parameters: tuple[ConfigurationParameter, ...],
    identity: DeviceIdentity,
    variable_names: tuple[str, ...],
) -> ModbusVariableSampleResult:
    samples: list[VariableSample] = []
    errors: list[str] = []
    for variable_name in variable_names:
        try:
            parameter = _find_parameter(parameters, variable_name)
        except Exception as exc:
            errors.append(f"{variable_name}: {exc}")
            continue
        samples.append(_sample_from_parameter(parameter, identity))
    return ModbusVariableSampleResult(samples=tuple(samples), errors=tuple(errors))


def _sample_from_parameter(
    parameter: ConfigurationParameter,
    identity: DeviceIdentity,
) -> VariableSample:
    return VariableSample(
        sample_id=f"VAR-{uuid4().hex}",
        device_id=identity.device_id,
        variable_name=parameter.name,
        captured_at=datetime.now(UTC),
        value=parameter.value,
        unit=parameter.unit,
        source_channel=identity.protocol_address or identity.device_id,
    )


def _save_variable_sample(
    repository: StorageRepository,
    sample: VariableSample,
    *,
    metadata: dict[str, Any],
) -> None:
    repository.save_variable_sample(
        VariableSampleRecord(
            sample_id=sample.sample_id,
            device_id=sample.device_id,
            run_id=sample.run_id,
            step_id=sample.step_id,
            variable_name=sample.variable_name,
            captured_at=sample.captured_at,
            value=sample.value,
            unit=sample.unit,
            source_channel=sample.source_channel,
            metadata=metadata,
        )
    )


def _coerce_write_value(register: ModbusRegister, value: str) -> Any:
    text = value.strip()
    if not text:
        raise ValueError(f"Write value is empty for {register.name}.")
    if register.data_type is ModbusDataType.BOOL:
        lowered = text.lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"Boolean write value must be true/false for {register.name}.")
    if register.data_type in {
        ModbusDataType.UINT16,
        ModbusDataType.INT16,
        ModbusDataType.UINT32,
        ModbusDataType.INT32,
    }:
        return int(float(text))
    return float(text)


def _find_parameter(
    parameters: tuple[ConfigurationParameter, ...],
    variable_name: str,
) -> ConfigurationParameter:
    for parameter in parameters:
        if parameter.name == variable_name:
            return parameter
    raise ValueError(f"Missing required parameter: {variable_name}")

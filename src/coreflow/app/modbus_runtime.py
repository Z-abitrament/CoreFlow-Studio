"""Standalone Modbus module runtime services."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
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
from coreflow.storage import StorageRepository
from coreflow.storage.models import AnalysisResultRecord, DeviceRecord, VariableSampleRecord
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
class ModbusZeroCalibrationResult:
    """UI-ready zero calibration result."""

    run_id: str
    record: ZeroCalibrationRecord
    audit_id: str
    pre_snapshot: dict[str, object] = field(default_factory=dict)
    pre_snapshot_captured_at: datetime | None = None


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

    @property
    def duration_s(self) -> float:
        return (self.flow_ended_at - self.flow_started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class ModbusRepeatabilitySimpleCapture:
    """Captured device data before one repeatability standard-mass entry."""

    run_id: str
    flow_point: float
    trial_index: int
    flow_rate_parameter: str
    flow_acc_parameter: str
    pre_snapshot: dict[str, object]
    pre_snapshot_captured_at: datetime | None
    mass_acc_before: float
    mass_acc_after: float
    segment: FlowSegmentCaptureResult
    poll_interval_s: float

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
    flow_rate_source: str = "device"

    @property
    def duration_s(self) -> float:
        return (self.flow_ended_at - self.flow_started_at).total_seconds()


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
    errors: tuple[str, ...] = ()


TransportFactory = Callable[[SerialConfig], ModbusTransport | None]
FrameLogger = Callable[[str, str, str], None]

_CALIBRATION_HISTORY_EXPORT_FORMAT = "coreflow.modbus.calibration_history"
_CALIBRATION_HISTORY_EXPORT_VERSION = 1
_CALIBRATION_HISTORY_WORKFLOWS = {
    "zero_calibration",
    "k_factor_calibration",
    "manual_error_repeatability",
}
_CALIBRATION_HISTORY_STATUSES = {"passed", "failed", "canceled", "error"}


class ModbusModuleRuntime:
    """Coordinates standalone Modbus master operations without simulator channels."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        register_map: ModbusRegisterMap | None = None,
        transport_factory: TransportFactory | None = None,
        operator: str = "operator",
        zero_calibration_wait_s: float = 3.0,
        k_factor_post_start_sample_s: float = 3.0,
        k_factor_post_stop_delay_s: float = 3.0,
    ) -> None:
        self._repository = repository
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

    def set_frame_logger(self, logger: FrameLogger | None) -> None:
        self._frame_logger = logger

    def connect(self, settings: ModbusConnectionSettings) -> ModbusModuleStatus:
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
        self._device_id = f"modbus:{settings.port}:{settings.unit_id}"
        self._identity = DeviceIdentity(
            device_id=self._device_id,
            device_type=DeviceType.MODBUS_RTU,
            protocol_address=str(settings.unit_id),
            metadata={
                "port": settings.port,
                "register_map": self._register_map.name,
                "register_map_version": self._register_map.version,
                "order": settings.order,
            },
        )
        return self.status

    def disconnect(self) -> ModbusModuleStatus:
        if self._device is not None:
            self._device.disconnect()
        self._device = None
        self._device_id = None
        self._identity = None
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
        return ModbusZeroCalibrationResult(
            run_id=run_id,
            record=result.record,
            audit_id=result.audit_id,
            pre_snapshot=result.pre_snapshot,
            pre_snapshot_captured_at=result.pre_snapshot_captured_at,
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
    ) -> ModbusKFactorSimpleCapture:
        run_id = self._next_run_id()
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
        pre_snapshot, pre_snapshot_captured_at = _pre_calibration_snapshot(
            device,
            _unique_names(snapshot_variable_names),
        )
        before_parameters = _read_selected_parameters(
            device,
            (flow_acc_parameter, k_factor_parameter),
            merge_adjacent=False,
        )
        mass_acc_before = float(_find_parameter(before_parameters, flow_acc_parameter).value)
        current_k_factor = float(_find_parameter(before_parameters, k_factor_parameter).value)
        segment = capture_flow_segment(
            device,
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
        after_parameters = _read_selected_parameters(
            device,
            (flow_acc_parameter,),
            merge_adjacent=False,
        )
        mass_acc_after = float(_find_parameter(after_parameters, flow_acc_parameter).value)
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
        flow_point: float,
        trial_index: int,
        snapshot_variable_names: tuple[str, ...] = (),
        flow_rate_parameter: str = "mass_rate",
        flow_acc_parameter: str = "mass_acc",
        poll_interval_s: float = 1.0,
        nonzero_threshold: float = 0.0,
        post_start_sample_s: float | None = None,
        post_stop_delay_s: float | None = None,
        max_wait_start_polls: int = 600,
        max_wait_stop_polls: int = 600,
        capture_snapshot: bool = True,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ModbusRepeatabilitySimpleCapture:
        if trial_index < 1:
            raise ValueError("Repeatability trial index must be at least 1.")
        if poll_interval_s <= 0:
            raise ValueError("Repeatability poll interval must be positive.")
        capture_run_id = run_id or self._next_run_id()
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
        if capture_snapshot:
            pre_snapshot, pre_snapshot_captured_at = _pre_calibration_snapshot(
                device,
                _unique_names(snapshot_variable_names),
            )
        else:
            pre_snapshot, pre_snapshot_captured_at = {}, None
        before_parameters = _read_selected_parameters(
            device,
            (flow_acc_parameter,),
            merge_adjacent=False,
        )
        mass_acc_before = float(_find_parameter(before_parameters, flow_acc_parameter).value)
        segment = capture_flow_segment(
            device,
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
            ),
        )
        after_parameters = _read_selected_parameters(
            device,
            (flow_acc_parameter,),
            merge_adjacent=False,
        )
        mass_acc_after = float(_find_parameter(after_parameters, flow_acc_parameter).value)
        return ModbusRepeatabilitySimpleCapture(
            run_id=capture_run_id,
            flow_point=flow_point,
            trial_index=trial_index,
            flow_rate_parameter=flow_rate_parameter,
            flow_acc_parameter=flow_acc_parameter,
            pre_snapshot=pre_snapshot,
            pre_snapshot_captured_at=pre_snapshot_captured_at,
            mass_acc_before=mass_acc_before,
            mass_acc_after=mass_acc_after,
            segment=segment,
            poll_interval_s=poll_interval_s,
        )

    def calculate_repeatability_simple_trial(
        self,
        capture: ModbusRepeatabilitySimpleCapture,
        *,
        standard_mass: float,
    ) -> ModbusRepeatabilitySimpleTrialResult:
        if standard_mass <= 0:
            raise ValueError("Repeatability test requires positive standard mass.")
        measured_mass_delta = capture.measured_mass_delta
        percent_error = (measured_mass_delta - standard_mass) / standard_mass * 100.0
        return ModbusRepeatabilitySimpleTrialResult(
            run_id=capture.run_id,
            flow_point=capture.flow_point,
            trial_index=capture.trial_index,
            flow_rate_parameter=capture.flow_rate_parameter,
            flow_acc_parameter=capture.flow_acc_parameter,
            pre_snapshot=capture.pre_snapshot,
            pre_snapshot_captured_at=capture.pre_snapshot_captured_at,
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
        )

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
    ) -> ModbusRepeatabilitySimpleResult:
        if not trials:
            raise ValueError("Repeatability test requires at least one trial.")
        if expected_flow_point_count < 1:
            raise ValueError("Repeatability test requires at least one flow point.")
        if expected_trials_per_point < 1:
            raise ValueError("Repeatability test requires at least one trial per point.")
        flow_points = {trial.flow_point for trial in trials}
        expected_total = expected_flow_point_count * expected_trials_per_point
        if require_complete and len(trials) != expected_total:
            raise ValueError(
                "Repeatability test requires "
                f"{expected_total} trials for {expected_flow_point_count} flow point(s)."
            )
        if len(flow_points) != expected_flow_point_count:
            raise ValueError(
                "Repeatability test requires "
                f"{expected_flow_point_count} flow point(s)."
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
        )
        if save_history:
            self._save_repeatability_simple_history(
                result,
                status=RunStatus.PASSED,
                operation_metadata=operation_metadata,
            )
        return result

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
        for run_summary in self._repository.list_runs():
            if run_summary.workflow_name not in _CALIBRATION_HISTORY_WORKFLOWS:
                continue
            status = getattr(run_summary.status, "value", str(run_summary.status))
            if status not in _CALIBRATION_HISTORY_STATUSES:
                continue
            if (
                operation_filter is not None
                and run_summary.workflow_name != operation_filter
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
                    notes=run_summary.notes or "",
                )
            )
        return tuple(entries)

    def update_calibration_history_note(self, run_id: str, notes: str) -> None:
        self._repository.update_run_notes(run_id, notes)

    def export_calibration_history(
        self,
        path: str | Path,
        *,
        operation: str | None = None,
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
        for history_entry in self.list_calibration_history(operation=operation):
            if not _history_entry_in_time_range(
                history_entry,
                started_from=started_from,
                started_to=started_to,
            ):
                continue
            run = self._repository.get_run(history_entry.run_id)
            if run is None:
                continue
            device = self._repository.get_device(run.device_id)
            steps = self._repository.list_steps(run.run_id)
            analysis_results = self._repository.list_analysis_results(run.run_id)
            analysis_count += len(analysis_results)
            step_count += len(steps)
            entries.append(
                {
                    "device": _device_record_to_history_payload(device)
                    if device is not None
                    else None,
                    "run": _run_session_to_history_payload(run),
                    "workflow_steps": [
                        _workflow_step_to_history_payload(step) for step in steps
                    ],
                    "analysis_results": [
                        _analysis_result_to_history_payload(result)
                        for result in analysis_results
                    ],
                }
            )

        payload = {
            "format": _CALIBRATION_HISTORY_EXPORT_FORMAT,
            "format_version": _CALIBRATION_HISTORY_EXPORT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "software_version": __version__,
            "operation_filter": operation or "all",
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
    ) -> ModbusCalibrationHistoryImportResult:
        """Import a portable Modbus calibration-history JSON package."""

        import_path = Path(path)
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
        imported_analysis_results = 0
        imported_workflow_steps = 0
        errors: list[str] = []
        for index, raw_entry in enumerate(raw_entries, start=1):
            try:
                if not isinstance(raw_entry, dict):
                    raise ValueError("entry must be an object")
                run = _run_session_from_history_payload(raw_entry.get("run"))
                if run.workflow_name not in _CALIBRATION_HISTORY_WORKFLOWS:
                    raise ValueError(f"unsupported workflow: {run.workflow_name}")
                status = getattr(run.status, "value", str(run.status))
                if status not in _CALIBRATION_HISTORY_STATUSES:
                    raise ValueError(f"unsupported status: {status}")
                if self._repository.get_run(run.run_id) is not None:
                    analysis_results = _analysis_results_from_history_payload(
                        raw_entry.get("analysis_results")
                    )
                    if _history_run_already_imported(
                        self._repository,
                        run,
                        analysis_results,
                    ):
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
                    step_id_map = _import_step_id_map(
                        steps,
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
                    analysis_results = tuple(
                        _remap_imported_analysis_result(
                            result,
                            original_run_id=original_run_id,
                            new_run_id=new_run_id,
                            step_id_map=step_id_map,
                            imported_at=imported_at,
                        )
                        for result in analysis_results
                    )
                    renamed_runs += 1
                else:
                    steps = _workflow_steps_from_history_payload(
                        raw_entry.get("workflow_steps")
                    )
                    analysis_results = _analysis_results_from_history_payload(
                        raw_entry.get("analysis_results")
                    )

                device = _device_record_from_history_payload(raw_entry.get("device"))
                if device is None:
                    device = DeviceRecord(
                        device_id=run.device_id,
                        device_type=DeviceType.MODBUS_RTU.value,
                    )
                if self._repository.get_device(device.device_id) is None:
                    self._repository.save_device(device)
                self._repository.save_run(run)

                for step in steps:
                    self._repository.save_step(step)
                imported_workflow_steps += len(steps)

                for result in analysis_results:
                    self._repository.save_analysis_result(result)
                imported_analysis_results += len(analysis_results)
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
            errors=tuple(errors),
        )

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

    def _save_repeatability_simple_history(
        self,
        result: ModbusRepeatabilitySimpleResult,
        *,
        status: RunStatus,
        operation_metadata: ModbusOperationMetadata | None = None,
    ) -> None:
        identity = self._require_identity()
        metadata = self._operation_metadata_snapshot(operation_metadata)
        metrics = _repeatability_simple_metrics(result)
        metrics.update(metadata)
        started_at = result.started_at
        ended_at = result.ended_at
        self._repository.save_run(
            RunSession(
                run_id=result.run_id,
                run_type=RunType.ERROR_ANALYSIS,
                workflow_name="manual_error_repeatability",
                workflow_version="0.2-simple",
                device_id=identity.device_id,
                operator=self._operator,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
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
            started_at=started_at,
            ended_at=ended_at,
            input_configuration={
                **metadata,
                "mode": result.mode,
                "flow_rate_parameter": result.flow_rate_parameter,
                "flow_acc_parameter": result.flow_acc_parameter,
                "poll_interval_s": result.poll_interval_s,
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

    def _require_device(self) -> FlowmeterDevice:
        if self._device is None:
            raise ConnectionError("Connect the Modbus module first.")
        return self._device

    def _require_identity(self) -> DeviceIdentity:
        if self._identity is None:
            raise ConnectionError("Connect the Modbus module first.")
        return self._identity

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
) -> dict[str, object]:
    metrics: dict[str, object] = {
        "mode": result.mode,
        "flow_rate_parameter": result.flow_rate_parameter,
        "flow_acc_parameter": result.flow_acc_parameter,
        "poll_interval_s": result.poll_interval_s,
        "expected_trials_per_point": result.expected_trials_per_point,
        "history_saved": result.history_saved,
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        **result.analysis.summary_metrics,
        "trials": [
            {
                "flow_point": trial.flow_point,
                "trial_index": trial.trial_index,
                "mass_acc_before": trial.mass_acc_before,
                "mass_acc_after": trial.mass_acc_after,
                "measured_mass_delta": trial.measured_mass_delta,
                "standard_mass": trial.standard_mass,
                "percent_error": trial.percent_error,
                "mean_flow": trial.mean_flow,
                "instant_flow": trial.instant_flow,
                "flow_rate_source": trial.flow_rate_source,
                "duration_s": trial.duration_s,
                "flow_started_at": trial.flow_started_at.isoformat(),
                "flow_instant_at": trial.flow_instant_at.isoformat(),
                "flow_ended_at": trial.flow_ended_at.isoformat(),
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


def _flow_point_metric_label(flow_point: float) -> str:
    raw = f"{flow_point:.12g}".replace("-", "neg_")
    return "flow_point_" + raw.replace(".", "_")


def _sample_stddev(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)


def _json_metric_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


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


def _remap_imported_analysis_result(
    result: AnalysisResultRecord,
    *,
    original_run_id: str,
    new_run_id: str,
    step_id_map: dict[str, str],
    imported_at: str,
) -> AnalysisResultRecord:
    configuration = dict(result.configuration_snapshot)
    configuration["imported_from_run_id"] = original_run_id
    configuration["imported_at"] = imported_at
    metrics = dict(result.summary_metrics)
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
        configuration_snapshot=configuration,
        summary_metrics=metrics,
    )


def _remap_imported_result_id(
    result_id: str,
    *,
    original_run_id: str,
    new_run_id: str,
) -> str:
    if result_id.startswith(original_run_id):
        return result_id.replace(original_run_id, new_run_id, 1)
    return f"{new_run_id}-{result_id}"


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


def _required_history_str(payload: dict[str, object], key: str) -> str:
    value = _history_str(payload, key)
    if not value:
        raise ValueError(f"missing {key}")
    return value


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

"""Standalone Modbus module runtime services."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from coreflow import __version__
from coreflow.analysis.calibration import RepeatabilityTrial, ZeroCalibrationRecord
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
from coreflow.storage.models import DeviceRecord, VariableSampleRecord
from coreflow.workflows.calibration import (
    KFactorCalibrationConfig,
    KFactorCalibrationWorkflow,
    RepeatabilityTestConfig,
    RepeatabilityTestWorkflow,
    ZeroCalibrationConfig,
    ZeroCalibrationWorkflow,
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


TransportFactory = Callable[[SerialConfig], ModbusTransport | None]
FrameLogger = Callable[[str, str, str], None]

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
    ) -> None:
        self._repository = repository
        self._register_map = register_map or build_placeholder_register_map()
        self._transport_factory = transport_factory
        self._frame_logger: FrameLogger | None = None
        self._operator = operator
        self._zero_calibration_wait_s = zero_calibration_wait_s
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
    ) -> ModbusZeroCalibrationResult:
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
        return ModbusZeroCalibrationResult(
            run_id=run_id,
            record=result.record,
            audit_id=result.audit_id,
            pre_snapshot=result.pre_snapshot,
            pre_snapshot_captured_at=result.pre_snapshot_captured_at,
        )

    def run_k_factor_calibration(
        self,
        *,
        mass_acc_before: float,
        mass_acc_after: float,
        standard_mass: float,
        current_k_factor: float,
    ) -> str:
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
        return run_id

    def run_repeatability_test(
        self,
        trials: tuple[RepeatabilityTrial, ...],
    ) -> str:
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
        return run_id

    def list_calibration_history(
        self,
        *,
        operation: str | None = None,
    ) -> tuple[ModbusCalibrationHistoryEntry, ...]:
        operation_filter = None if operation in (None, "", "all") else operation
        entries: list[ModbusCalibrationHistoryEntry] = []
        for run in self._repository.list_runs():
            if run.workflow_name not in _CALIBRATION_HISTORY_WORKFLOWS:
                continue
            status = getattr(run.status, "value", str(run.status))
            if status not in _CALIBRATION_HISTORY_STATUSES:
                continue
            if operation_filter is not None and run.workflow_name != operation_filter:
                continue
            metrics: dict[str, Any] = {}
            analysis_results = self._repository.list_analysis_results(run.run_id)
            if analysis_results:
                metrics = dict(analysis_results[-1].summary_metrics)
            entries.append(
                ModbusCalibrationHistoryEntry(
                    run_id=run.run_id,
                    operation=run.workflow_name,
                    status=run.status,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    device_id=run.device_id,
                    operator=run.operator,
                    metrics=metrics,
                    notes=run.notes or "",
                )
            )
        return tuple(entries)

    def update_calibration_history_note(self, run_id: str, notes: str) -> None:
        self._repository.update_run_notes(run_id, notes)

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

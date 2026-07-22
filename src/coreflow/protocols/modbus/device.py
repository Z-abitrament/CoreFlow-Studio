"""FlowmeterDevice implementation for Modbus RTU transmitters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from coreflow.devices import (
    CommunicationDiagnostic,
    CommunicationState,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    DeviceType,
    FlowmeterDevice,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
    WriteResultStatus,
)
from coreflow.protocols.modbus.encoding import decode_registers, encode_registers
from coreflow.protocols.modbus.models import (
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    SerialConfig,
)
from coreflow.protocols.modbus.transport import (
    ModbusTransport,
    PymodbusSerialTransport,
)


class ModbusRtuFlowmeterDevice(FlowmeterDevice):
    """Modbus RTU adapter behind the application-level device interface."""

    def __init__(
        self,
        config: SerialConfig,
        register_map: ModbusRegisterMap,
        transport: ModbusTransport | None = None,
    ) -> None:
        self._config = config
        self._register_map = register_map
        self._transport = transport or PymodbusSerialTransport(config)
        self._state = CommunicationState.DISCONNECTED
        self._request_count = 0
        self._success_count = 0
        self._timeout_count = 0
        self._frame_error_count = 0
        self._exception_count = 0
        self._last_error: str | None = None
        self._last_success_at: datetime | None = None
        self._average_response_ms: float | None = None

    def connect(self) -> None:
        if self._state is CommunicationState.CONNECTED:
            return
        self._request_count += 1
        self._state = CommunicationState.CONNECTING
        if self._transport.connect():
            self._state = CommunicationState.CONNECTED
            self._record_success()
        else:
            self._state = CommunicationState.FAULTED
            last_error = getattr(self._transport, "last_error", None)
            self._last_error = (
                last_error
                if isinstance(last_error, str) and last_error
                else (
                    "Unable to open Modbus RTU transport "
                    f"on {self._config.port} "
                    f"({self._config.baudrate} baud, "
                    f"{self._config.data_bits}{self._config.parity}{self._config.stop_bits}, "
                    f"timeout={self._config.read_timeout_s:g}s)."
                )
            )
            raise ConnectionError(self._last_error)

    def disconnect(self) -> None:
        self._request_count += 1
        self._transport.close()
        self._state = CommunicationState.DISCONNECTED
        self._record_success()

    def read_identity(self) -> DeviceIdentity:
        values = {
            name: self._read_optional(name)
            for name in (
                "serial_number",
                "model",
                "firmware_version",
                "hardware_version",
            )
        }
        return DeviceIdentity(
            device_id=f"modbus:{self._config.port}:{self._config.unit_id}",
            device_type=DeviceType.MODBUS_RTU,
            serial_number=_string_or_none(values["serial_number"]),
            model=_string_or_none(values["model"]),
            firmware_version=_string_or_none(values["firmware_version"]),
            hardware_version=_string_or_none(values["hardware_version"]),
            protocol_address=str(self._config.unit_id),
            metadata={
                "port": self._config.port,
                "register_map": self._register_map.name,
                "register_map_version": self._register_map.version,
            },
        )

    def read_health(self) -> DeviceHealth:
        status = self._read_optional("device_status")
        alarm = self._read_optional("alarm_flags")
        flags = () if status is None else (f"status:{status}",)
        alarms = () if alarm is None else (f"alarm:{alarm}",)
        return DeviceHealth(
            state=self._state,
            status_flags=flags,
            alarm_flags=alarms,
            captured_at=datetime.now(UTC),
        )

    def read_measurement(self) -> Measurement:
        return Measurement(
            captured_at=datetime.now(UTC),
            mass_flow=self._read_optional("mass_flow"),
            volume_flow=self._read_optional("volume_flow"),
            density=self._read_optional("density"),
            temperature=self._read_optional("temperature"),
            source_channel=f"{self._config.port}:{self._config.unit_id}",
        )

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        parameters: list[ConfigurationParameter] = []
        for register in self._register_map.registers:
            if register.kind not in (
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
            ):
                continue
            parameters.append(self._configuration_parameter(register))
        return tuple(parameters)

    def read_configuration_parameters(
        self,
        parameter_names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
        transport_retry_count: int | None = None,
    ) -> tuple[ConfigurationParameter, ...]:
        """Read only selected configured parameters."""

        if merge_adjacent:
            return self._read_configuration_parameters_merged(
                parameter_names,
                transport_retry_count=transport_retry_count,
            )
        if transport_retry_count is not None:
            return tuple(
                self._configuration_parameter_with_retry(
                    self._register_map.by_name(name),
                    transport_retry_count=transport_retry_count,
                )
                for name in parameter_names
            )
        return tuple(
            self._configuration_parameter(self._register_map.by_name(name))
            for name in parameter_names
        )

    def write_configuration(
        self, request: ParameterWriteRequest
    ) -> ParameterWriteResult:
        try:
            register = self._register_map.by_name(request.parameter_name)
        except KeyError as exc:
            self._exception_count += 1
            self._last_error = str(exc)
            return _write_result(request, WriteResultStatus.REJECTED, message=str(exc))

        previous_value = self._read_register(register)
        validation_error = self._validate_write(register, request.new_value)
        if validation_error is not None:
            self._exception_count += 1
            self._last_error = validation_error
            return _write_result(
                request,
                WriteResultStatus.REJECTED,
                previous_value=previous_value,
                message=validation_error,
            )

        if request.mode is WriteMode.PREVIEW:
            self._record_success()
            return _write_result(
                request,
                WriteResultStatus.PREVIEWED,
                previous_value=previous_value,
                new_value=request.new_value,
            )
        if request.mode is WriteMode.DRY_RUN:
            self._record_success()
            return _write_result(
                request,
                WriteResultStatus.DRY_RUN,
                previous_value=previous_value,
                new_value=request.new_value,
            )

        if register.kind is RegisterKind.COIL:
            response = self._send_coil_write(register, bool(request.new_value))
        else:
            encoded = encode_registers(register, request.new_value)
            response = self._send_write(register, encoded)
        if not response:
            return _write_result(
                request,
                WriteResultStatus.FAILED,
                previous_value=previous_value,
                message=self._last_error,
            )

        return _write_result(
            request,
            WriteResultStatus.APPLIED,
            previous_value=previous_value,
            new_value=request.new_value,
        )

    def write_configuration_without_pre_read(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        """Apply a validated write without issuing a read before the write frame."""

        try:
            register = self._register_map.by_name(request.parameter_name)
        except KeyError as exc:
            self._exception_count += 1
            self._last_error = str(exc)
            return _write_result(request, WriteResultStatus.REJECTED, message=str(exc))

        validation_error = self._validate_write(register, request.new_value)
        if validation_error is not None:
            self._exception_count += 1
            self._last_error = validation_error
            return _write_result(request, WriteResultStatus.REJECTED, message=validation_error)

        if request.mode is WriteMode.PREVIEW:
            self._record_success()
            return _write_result(
                request,
                WriteResultStatus.PREVIEWED,
                new_value=request.new_value,
            )
        if request.mode is WriteMode.DRY_RUN:
            self._record_success()
            return _write_result(
                request,
                WriteResultStatus.DRY_RUN,
                new_value=request.new_value,
            )

        if register.kind is RegisterKind.COIL:
            response = self._send_coil_write(register, bool(request.new_value))
        else:
            encoded = encode_registers(register, request.new_value)
            response = self._send_write(register, encoded)
        if not response:
            return _write_result(
                request,
                WriteResultStatus.FAILED,
                message=self._last_error,
            )

        return _write_result(
            request,
            WriteResultStatus.APPLIED,
            new_value=request.new_value,
        )

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return CommunicationDiagnostic(
            state=self._state,
            request_count=self._request_count,
            successful_response_count=self._success_count,
            timeout_count=self._timeout_count,
            frame_error_count=self._frame_error_count,
            exception_response_count=self._exception_count,
            last_error=self._last_error,
            last_success_at=self._last_success_at,
            average_response_ms=self._average_response_ms,
        )

    def _read_optional(self, name: str) -> Any | None:
        try:
            register = self._register_map.by_name(name)
        except KeyError:
            return None
        return self._read_register(register)

    def _read_register(self, register: ModbusRegister) -> Any:
        last_error: str | None = None
        for _attempt in range(self._config.retry_count + 1):
            self._request_count += 1
            response = self._transport.read_registers(
                register.kind,
                register.address,
                register.word_count,
                self._config.unit_id,
            )
            if response.ok:
                self._record_success()
                return decode_registers(register, response.values or [])
            last_error = response.error
            self._record_transport_error(last_error)
        raise TimeoutError(last_error or "Modbus read failed.")

    def _configuration_parameter(
        self,
        register: ModbusRegister,
    ) -> ConfigurationParameter:
        value = self._read_register(register)
        return self._configuration_parameter_from_value(register, value)

    def _configuration_parameter_with_retry(
        self,
        register: ModbusRegister,
        *,
        transport_retry_count: int,
    ) -> ConfigurationParameter:
        words = self._read_words(
            register.kind,
            register.address,
            register.word_count,
            transport_retry_count=transport_retry_count,
        )
        return self._configuration_parameter_from_value(
            register,
            decode_registers(register, words),
            raw_words=words,
        )

    def _configuration_parameter_from_value(
        self,
        register: ModbusRegister,
        value: Any,
        *,
        raw_words: list[int] | None = None,
    ) -> ConfigurationParameter:
        metadata: dict[str, Any] = {
            "register_kind": register.kind.value,
            "address": register.address,
            "word_count": register.word_count,
            "data_type": register.data_type.value,
        }
        if raw_words is not None:
            metadata["raw_words"] = list(raw_words)
        return ConfigurationParameter(
            name=register.name,
            value=value,
            unit=register.unit,
            writable=register.writable,
            minimum=register.minimum,
            maximum=register.maximum,
            metadata=metadata,
        )

    def _read_configuration_parameters_merged(
        self,
        parameter_names: tuple[str, ...],
        *,
        transport_retry_count: int | None = None,
    ) -> tuple[ConfigurationParameter, ...]:
        registers = [self._register_map.by_name(name) for name in parameter_names]
        values: dict[str, Any] = {}
        raw_values: dict[str, list[int]] = {}
        for group in _contiguous_register_groups(registers):
            start = group[0].address
            end = max(register.address + register.word_count for register in group)
            words = self._read_words(
                group[0].kind,
                start,
                end - start,
                transport_retry_count=transport_retry_count,
            )
            for register in group:
                offset = register.address - start
                raw_words = words[offset : offset + register.word_count]
                values[register.name] = decode_registers(register, raw_words)
                raw_values[register.name] = raw_words
        return tuple(
            self._configuration_parameter_from_value(
                self._register_map.by_name(name),
                values[name],
                raw_words=raw_values[name],
            )
            for name in parameter_names
        )

    def _read_words(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        *,
        transport_retry_count: int | None = None,
    ) -> list[int]:
        last_error: str | None = None
        retry_count = (
            self._config.retry_count
            if transport_retry_count is None
            else transport_retry_count
        )
        if retry_count < 0:
            raise ValueError("transport_retry_count must be nonnegative.")
        for _attempt in range(retry_count + 1):
            self._request_count += 1
            response = self._transport.read_registers(
                kind,
                address,
                count,
                self._config.unit_id,
            )
            if response.ok:
                self._record_success()
                return response.values or []
            last_error = response.error
            self._record_transport_error(last_error)
        raise TimeoutError(last_error or "Modbus read failed.")

    def _send_write(self, register: ModbusRegister, values: list[int]) -> bool:
        for _attempt in range(self._config.retry_count + 1):
            self._request_count += 1
            response = self._transport.write_registers(
                register.address,
                values,
                self._config.unit_id,
            )
            if response.ok:
                self._record_success()
                return True
            self._record_transport_error(response.error)
        return False

    def _send_coil_write(self, register: ModbusRegister, value: bool) -> bool:
        for _attempt in range(self._config.retry_count + 1):
            self._request_count += 1
            response = self._transport.write_coil(
                register.address,
                value,
                self._config.unit_id,
            )
            if response.ok:
                self._record_success()
                return True
            self._record_transport_error(response.error)
        return False

    def _validate_write(self, register: ModbusRegister, value: Any) -> str | None:
        if not register.writable:
            return f"Register is not writable: {register.name}"
        if register.kind not in (RegisterKind.HOLDING, RegisterKind.COIL):
            return f"Only holding registers and coils are writable: {register.name}"
        if register.kind is RegisterKind.COIL and not isinstance(value, bool | int):
            return f"Coil value must be boolean-like: {register.name}"
        if isinstance(value, int | float):
            if register.minimum is not None and value < register.minimum:
                return f"Value below minimum for {register.name}: {value}"
            if register.maximum is not None and value > register.maximum:
                return f"Value above maximum for {register.name}: {value}"
        return None

    def _record_success(self) -> None:
        self._success_count += 1
        self._state = (
            CommunicationState.CONNECTED
            if self._state is not CommunicationState.DISCONNECTED
            else self._state
        )
        self._last_error = None
        self._last_success_at = datetime.now(UTC)

    def _record_transport_error(self, error: str | None) -> None:
        self._timeout_count += 1
        self._last_error = error or "Modbus transport error."
        self._state = CommunicationState.FAULTED


def _write_result(
    request: ParameterWriteRequest,
    status: WriteResultStatus,
    previous_value: Any | None = None,
    new_value: Any | None = None,
    message: str | None = None,
) -> ParameterWriteResult:
    return ParameterWriteResult(
        parameter_name=request.parameter_name,
        status=status,
        previous_value=previous_value,
        new_value=new_value,
        message=message,
    )


def _string_or_none(value: Any | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _contiguous_register_groups(
    registers: list[ModbusRegister],
) -> tuple[tuple[ModbusRegister, ...], ...]:
    groups: list[list[ModbusRegister]] = []
    for register in sorted(registers, key=lambda item: (item.kind.value, item.address)):
        if not groups:
            groups.append([register])
            continue
        current = groups[-1]
        current_end = max(item.address + item.word_count for item in current)
        if register.kind is current[-1].kind and register.address <= current_end:
            current.append(register)
            continue
        groups.append([register])
    return tuple(tuple(group) for group in groups)

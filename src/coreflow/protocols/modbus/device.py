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
        self._request_count += 1
        self._state = CommunicationState.CONNECTING
        if self._transport.connect():
            self._state = CommunicationState.CONNECTED
            self._record_success()
        else:
            self._state = CommunicationState.FAULTED
            self._last_error = "Unable to open Modbus RTU transport."
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
            if register.kind not in (RegisterKind.HOLDING, RegisterKind.INPUT):
                continue
            value = self._read_register(register)
            parameters.append(
                ConfigurationParameter(
                    name=register.name,
                    value=value,
                    unit=register.unit,
                    writable=register.writable,
                    minimum=register.minimum,
                    maximum=register.maximum,
                    metadata={
                        "register_kind": register.kind.value,
                        "address": register.address,
                        "word_count": register.word_count,
                        "data_type": register.data_type.value,
                    },
                )
            )
        return tuple(parameters)

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

    def _validate_write(self, register: ModbusRegister, value: Any) -> str | None:
        if not register.writable:
            return f"Register is not writable: {register.name}"
        if register.kind is not RegisterKind.HOLDING:
            return f"Only holding registers are writable in M3: {register.name}"
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

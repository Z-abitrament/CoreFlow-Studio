from __future__ import annotations

from dataclasses import dataclass, field

from coreflow.devices import (
    CommunicationState,
    DeviceType,
    ParameterWriteRequest,
    WriteMode,
    WriteResultStatus,
)
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    ModbusRtuFlowmeterDevice,
    RegisterKind,
    SerialConfig,
    TransportResponse,
)


@dataclass
class FakeModbusTransport:
    registers: dict[int, list[int]]
    connected: bool = False
    read_errors: dict[int, str] = field(default_factory=dict)
    transient_read_errors: dict[int, list[str]] = field(default_factory=dict)
    write_errors: dict[int, str] = field(default_factory=dict)
    writes: list[tuple[int, list[int], int]] = field(default_factory=list)
    coil_writes: list[tuple[int, bool, int]] = field(default_factory=list)

    def connect(self) -> bool:
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
        if address in self.transient_read_errors and self.transient_read_errors[address]:
            return TransportResponse(error=self.transient_read_errors[address].pop(0))
        if address in self.read_errors:
            return TransportResponse(error=self.read_errors[address])
        values = self.registers[address][:count]
        return TransportResponse(values=values)

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
        if address in self.write_errors:
            return TransportResponse(error=self.write_errors[address])
        self.writes.append((address, list(values), unit_id))
        self.registers[address] = list(values)
        return TransportResponse(values=values)

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse:
        self.coil_writes.append((address, value, unit_id))
        self.registers[address] = [1 if value else 0]
        return TransportResponse(values=[1 if value else 0])


class FailingConnectTransport(FakeModbusTransport):
    last_error = "Unable to open COM42: Access is denied."

    def connect(self) -> bool:
        return False


def _register_map() -> ModbusRegisterMap:
    return ModbusRegisterMap(
        name="test-map",
        version="0.1",
        registers=(
            ModbusRegister(
                name="mass_flow",
                kind=RegisterKind.INPUT,
                address=0,
                word_count=1,
                data_type=ModbusDataType.INT16,
                scale=0.1,
                unit="kg/s",
            ),
            ModbusRegister(
                name="density",
                kind=RegisterKind.INPUT,
                address=1,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                scale=0.1,
                unit="kg/m3",
            ),
            ModbusRegister(
                name="temperature",
                kind=RegisterKind.INPUT,
                address=2,
                word_count=1,
                data_type=ModbusDataType.INT16,
                scale=0.1,
                unit="C",
            ),
            ModbusRegister(
                name="device_status",
                kind=RegisterKind.INPUT,
                address=3,
                word_count=1,
                data_type=ModbusDataType.UINT16,
            ),
            ModbusRegister(
                name="serial_number",
                kind=RegisterKind.HOLDING,
                address=10,
                word_count=1,
                data_type=ModbusDataType.UINT16,
            ),
            ModbusRegister(
                name="zero_offset",
                kind=RegisterKind.HOLDING,
                address=20,
                word_count=1,
                data_type=ModbusDataType.INT16,
                writable=True,
                scale=0.01,
                minimum=-10.0,
                maximum=10.0,
            ),
            ModbusRegister(
                name="read_only_setting",
                kind=RegisterKind.HOLDING,
                address=21,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                writable=False,
            ),
            ModbusRegister(
                name="zero_calibration_start",
                kind=RegisterKind.COIL,
                address=30,
                word_count=1,
                data_type=ModbusDataType.BOOL,
                writable=True,
            ),
        ),
    )


def _device(
    transport: FakeModbusTransport,
) -> ModbusRtuFlowmeterDevice:
    return ModbusRtuFlowmeterDevice(
        SerialConfig(port="COM9", unit_id=7, retry_count=1),
        _register_map(),
        transport=transport,
    )


def _transport() -> FakeModbusTransport:
    return FakeModbusTransport(
        registers={
            0: [123],
            1: [9982],
            2: [215],
            3: [1],
            10: [12345],
            20: [0],
            21: [1],
            30: [0],
        }
    )


def test_modbus_device_reads_identity_health_and_measurements() -> None:
    device = _device(_transport())

    device.connect()
    identity = device.read_identity()
    health = device.read_health()
    measurement = device.read_measurement()

    assert identity.device_type is DeviceType.MODBUS_RTU
    assert identity.serial_number == "12345"
    assert identity.protocol_address == "7"
    assert health.status_flags == ("status:1",)
    assert measurement.mass_flow == 12.3
    assert measurement.density == 998.2
    assert measurement.temperature == 21.5


def test_modbus_device_reads_configuration_parameters() -> None:
    device = _device(_transport())
    device.connect()

    parameters = {parameter.name: parameter for parameter in device.read_configuration()}

    assert parameters["zero_offset"].writable is True
    assert parameters["zero_offset"].value == 0.0
    assert parameters["read_only_setting"].writable is False


def test_modbus_device_write_preview_dry_run_apply_and_reject() -> None:
    transport = _transport()
    device = _device(transport)
    device.connect()

    preview = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=1.25,
            mode=WriteMode.PREVIEW,
            actor="pytest",
            workflow_state="preview",
        )
    )
    dry_run = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=1.25,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="dry_run",
        )
    )
    applied = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=1.25,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )
    rejected_read_only = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="read_only_setting",
            new_value=2,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )
    rejected_range = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=99.0,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )

    assert preview.status is WriteResultStatus.PREVIEWED
    assert dry_run.status is WriteResultStatus.DRY_RUN
    assert applied.status is WriteResultStatus.APPLIED
    assert transport.writes == [(20, [125], 7)]
    assert rejected_read_only.status is WriteResultStatus.REJECTED
    assert rejected_range.status is WriteResultStatus.REJECTED


def test_modbus_device_writes_coils() -> None:
    transport = _transport()
    device = _device(transport)
    device.connect()

    result = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_calibration_start",
            new_value=True,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="calibration_write_armed",
        )
    )

    assert result.status is WriteResultStatus.APPLIED
    assert transport.coil_writes == [(30, True, 7)]


def test_modbus_device_records_transport_errors() -> None:
    transport = _transport()
    transport.read_errors[0] = "timeout"
    device = _device(transport)
    device.connect()

    try:
        device.read_measurement()
    except TimeoutError:
        pass

    diagnostics = device.communication_diagnostics()

    assert diagnostics.state is CommunicationState.FAULTED
    assert diagnostics.timeout_count == 2
    assert diagnostics.last_error == "timeout"


def test_modbus_device_preserves_transport_connect_failure_details() -> None:
    device = ModbusRtuFlowmeterDevice(
        SerialConfig(port="COM42", unit_id=7, baudrate=9600),
        _register_map(),
        transport=FailingConnectTransport({}),
    )

    try:
        device.connect()
    except ConnectionError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected connect failure")

    assert "COM42" in message
    assert "Access is denied." in message
    diagnostics = device.communication_diagnostics()
    assert diagnostics.state is CommunicationState.FAULTED
    assert diagnostics.last_error == "Unable to open COM42: Access is denied."


def test_modbus_device_retries_transient_read_errors() -> None:
    transport = _transport()
    transport.transient_read_errors[0] = ["timeout"]
    device = ModbusRtuFlowmeterDevice(
        SerialConfig(port="COM9", unit_id=7, retry_count=1),
        _register_map(),
        transport=transport,
    )
    device.connect()

    measurement = device.read_measurement()
    diagnostics = device.communication_diagnostics()

    assert measurement.mass_flow == 12.3
    assert diagnostics.timeout_count == 1
    assert diagnostics.successful_response_count >= 2

from __future__ import annotations

from datetime import UTC, datetime

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


class StubFlowmeterDevice(FlowmeterDevice):
    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def read_identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            device_id="STUB-001",
            device_type=DeviceType.SIMULATED,
            serial_number="STUB-SN",
        )

    def read_health(self) -> DeviceHealth:
        state = (
            CommunicationState.CONNECTED
            if self.connected
            else CommunicationState.DISCONNECTED
        )
        return DeviceHealth(state=state)

    def read_measurement(self) -> Measurement:
        return Measurement(
            captured_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
            mass_flow=1.23,
        )

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        return (
            ConfigurationParameter(
                name="zero_offset",
                value=0.0,
                writable=True,
                minimum=-1.0,
                maximum=1.0,
            ),
        )

    def write_configuration(
        self, request: ParameterWriteRequest
    ) -> ParameterWriteResult:
        return ParameterWriteResult(
            parameter_name=request.parameter_name,
            status=WriteResultStatus.DRY_RUN
            if request.mode is WriteMode.DRY_RUN
            else WriteResultStatus.PREVIEWED,
            new_value=request.new_value,
        )

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return CommunicationDiagnostic(
            state=CommunicationState.CONNECTED
            if self.connected
            else CommunicationState.DISCONNECTED,
            request_count=1,
        )


def test_flowmeter_device_interface_can_be_implemented_without_adapters() -> None:
    device = StubFlowmeterDevice()

    device.connect()
    identity = device.read_identity()
    health = device.read_health()
    measurement = device.read_measurement()
    parameters = device.read_configuration()
    write_result = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.1,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="m1_interface_test",
        )
    )
    diagnostics = device.communication_diagnostics()

    assert identity.device_id == "STUB-001"
    assert health.state is CommunicationState.CONNECTED
    assert measurement.mass_flow == 1.23
    assert parameters[0].writable is True
    assert write_result.status is WriteResultStatus.DRY_RUN
    assert diagnostics.request_count == 1


def test_flowmeter_device_disconnect_updates_stub_state() -> None:
    device = StubFlowmeterDevice()
    device.connect()
    device.disconnect()

    assert device.read_health().state is CommunicationState.DISCONNECTED

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from coreflow.devices import (
    CommunicationDiagnostic,
    CommunicationState,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    DeviceType,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
    WriteResultStatus,
)


def test_device_identity_captures_stable_fields() -> None:
    identity = DeviceIdentity(
        device_id="SIM-001",
        device_type=DeviceType.SIMULATED,
        serial_number="SN-001",
        model="virtual-transmitter",
        firmware_version="sim-1",
        protocol_address="unit-1",
    )

    assert identity.device_id == "SIM-001"
    assert identity.device_type is DeviceType.SIMULATED
    assert identity.serial_number == "SN-001"


def test_device_models_are_frozen_value_objects() -> None:
    identity = DeviceIdentity(device_id="SIM-001", device_type=DeviceType.SIMULATED)

    with pytest.raises(FrozenInstanceError):
        identity.device_id = "SIM-002"


def test_health_measurement_and_diagnostics_snapshots() -> None:
    captured_at = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)

    health = DeviceHealth(
        state=CommunicationState.CONNECTED,
        status_flags=("measuring",),
        alarm_flags=(),
        captured_at=captured_at,
    )
    measurement = Measurement(
        captured_at=captured_at,
        mass_flow=12.5,
        density=998.2,
        source_channel="sim-1",
    )
    diagnostic = CommunicationDiagnostic(
        state=CommunicationState.CONNECTED,
        request_count=10,
        successful_response_count=9,
        timeout_count=1,
        average_response_ms=12.2,
    )

    assert health.state is CommunicationState.CONNECTED
    assert measurement.mass_flow == 12.5
    assert measurement.status_flags == ()
    assert diagnostic.timeout_count == 1


def test_configuration_parameter_and_write_models() -> None:
    parameter = ConfigurationParameter(
        name="zero_offset",
        value=0.01,
        unit="kg/s",
        writable=True,
        minimum=-1.0,
        maximum=1.0,
    )
    request = ParameterWriteRequest(
        parameter_name=parameter.name,
        new_value=0.02,
        mode=WriteMode.DRY_RUN,
        actor="pytest",
        workflow_state="calibration_preview",
        run_id="RUN-001",
        expected_previous_value=parameter.value,
    )
    result = ParameterWriteResult(
        parameter_name=parameter.name,
        status=WriteResultStatus.DRY_RUN,
        previous_value=parameter.value,
        new_value=request.new_value,
        audit_id="AUD-001",
    )

    assert parameter.writable is True
    assert request.mode is WriteMode.DRY_RUN
    assert result.status is WriteResultStatus.DRY_RUN
    assert result.audit_id == "AUD-001"

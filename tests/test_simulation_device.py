from __future__ import annotations

import math

import pytest

from coreflow.devices import (
    CommunicationState,
    ParameterWriteRequest,
    WriteMode,
    WriteResultStatus,
)
from coreflow.simulation import (
    FaultKind,
    FaultRule,
    FlowProfile,
    FlowProfileKind,
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)


def _connected_device(scenario: SimulatorScenario) -> SimulatedFlowmeterDevice:
    device = SimulatedFlowmeterDevice(scenario)
    device.connect()
    return device


def test_same_seed_and_scenario_produce_identical_readings() -> None:
    scenario = SimulatorScenario(
        name="nominal",
        device_id="SIM-001",
        seed=42,
        flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=10.0),
        noise_std=0.1,
    )
    first = _connected_device(scenario)
    second = _connected_device(scenario)

    first_readings = [first.read_measurement().mass_flow for _ in range(5)]
    second_readings = [second.read_measurement().mass_flow for _ in range(5)]

    assert first_readings == second_readings


def test_different_scenario_configuration_changes_readings() -> None:
    low_flow = _connected_device(
        SimulatorScenario(
            name="low",
            device_id="SIM-LOW",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=1.0),
        )
    )
    high_flow = _connected_device(
        SimulatorScenario(
            name="high",
            device_id="SIM-HIGH",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=5.0),
        )
    )

    assert low_flow.read_measurement().mass_flow == 1.0
    assert high_flow.read_measurement().mass_flow == 5.0


def test_drift_step_ramp_and_sine_profiles_are_deterministic() -> None:
    ramp = _connected_device(
        SimulatorScenario(
            name="ramp",
            device_id="SIM-RAMP",
            seed=1,
            flow_profile=FlowProfile(
                kind=FlowProfileKind.RAMP,
                start=1.0,
                slope_per_sample=0.5,
            ),
            drift_per_sample=0.1,
        )
    )
    step = _connected_device(
        SimulatorScenario(
            name="step",
            device_id="SIM-STEP",
            seed=1,
            flow_profile=FlowProfile(
                kind=FlowProfileKind.STEP,
                value=1.0,
                steps=((2, 3.0),),
            ),
        )
    )
    sine = _connected_device(
        SimulatorScenario(
            name="sine",
            device_id="SIM-SINE",
            seed=1,
            flow_profile=FlowProfile(
                kind=FlowProfileKind.SINE,
                value=10.0,
                amplitude=2.0,
                period_samples=4,
            ),
        )
    )

    assert [ramp.read_measurement().mass_flow for _ in range(3)] == [
        1.0,
        1.6,
        2.2,
    ]
    assert [step.read_measurement().mass_flow for _ in range(4)] == [
        1.0,
        1.0,
        3.0,
        3.0,
    ]
    assert [sine.read_measurement().mass_flow for _ in range(4)] == [
        10.0,
        12.0,
        10.0,
        8.0,
    ]


def test_invalid_timeout_alarm_and_delay_faults() -> None:
    device = _connected_device(
        SimulatorScenario(
            name="faults",
            device_id="SIM-FAULT",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=2.0),
            fault_rules=(
                FaultRule(kind=FaultKind.INVALID_VALUE, start_sample=0, end_sample=0),
                FaultRule(kind=FaultKind.TIMEOUT, start_sample=1, end_sample=1),
                FaultRule(
                    kind=FaultKind.ALARM_FLAG,
                    start_sample=1,
                    end_sample=1,
                    value="alarm_test",
                ),
                FaultRule(
                    kind=FaultKind.DELAY,
                    action="read_identity",
                    value=25.0,
                ),
            ),
        )
    )

    invalid = device.read_measurement()
    assert math.isnan(invalid.mass_flow)
    assert invalid.status_flags == ("invalid_value",)
    assert device.read_health().alarm_flags == ("alarm_test",)

    with pytest.raises(TimeoutError):
        device.read_measurement()

    assert device.read_identity().device_id == "SIM-FAULT"
    assert device.communication_diagnostics().average_response_ms == 25.0
    assert device.communication_diagnostics().timeout_count == 1


def test_disconnection_fault_updates_device_state() -> None:
    device = _connected_device(
        SimulatorScenario(
            name="disconnect",
            device_id="SIM-DISCONNECT",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=2.0),
            fault_rules=(
                FaultRule(kind=FaultKind.DISCONNECTION, start_sample=0, end_sample=0),
            ),
        )
    )

    with pytest.raises(ConnectionError):
        device.read_measurement()

    assert device.read_health().state is CommunicationState.DISCONNECTED


def test_simulated_write_modes_validation_and_failures() -> None:
    device = _connected_device(
        SimulatorScenario(
            name="writes",
            device_id="SIM-WRITE",
            seed=1,
            parameters=(
                ScenarioParameter(
                    name="zero_offset",
                    value=0.0,
                    writable=True,
                    minimum=-1.0,
                    maximum=1.0,
                ),
                ScenarioParameter(name="read_only", value=5.0, writable=False),
            ),
            fault_rules=(
                FaultRule(
                    kind=FaultKind.WRITE_FAILURE,
                    action="zero_offset",
                ),
            ),
        )
    )

    preview = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.2,
            mode=WriteMode.PREVIEW,
            actor="pytest",
            workflow_state="preview",
        )
    )
    dry_run = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.2,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="dry_run",
        )
    )
    rejected_read_only = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="read_only",
            new_value=6.0,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )
    rejected_range = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=2.0,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )
    failed = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.3,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )

    assert preview.status is WriteResultStatus.PREVIEWED
    assert dry_run.status is WriteResultStatus.DRY_RUN
    assert rejected_read_only.status is WriteResultStatus.REJECTED
    assert rejected_range.status is WriteResultStatus.REJECTED
    assert failed.status is WriteResultStatus.FAILED


def test_successful_armed_write_updates_virtual_parameter() -> None:
    device = _connected_device(
        SimulatorScenario(
            name="write_success",
            device_id="SIM-WRITE-OK",
            seed=1,
            parameters=(
                ScenarioParameter(
                    name="zero_offset",
                    value=0.0,
                    writable=True,
                    minimum=-1.0,
                    maximum=1.0,
                ),
            ),
        )
    )

    result = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.5,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="armed",
        )
    )

    assert result.status is WriteResultStatus.APPLIED
    assert result.previous_value == 0.0
    assert result.new_value == 0.5
    assert device.read_configuration()[0].value == 0.5

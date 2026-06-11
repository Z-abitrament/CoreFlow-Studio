"""Deterministic simulated flowmeter transmitter."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from coreflow.devices import (
    CommunicationDiagnostic,
    CommunicationState,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    FlowmeterDevice,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
    WriteResultStatus,
)
from coreflow.simulation.scenario import (
    FaultKind,
    FlowProfile,
    FlowProfileKind,
    ScenarioParameter,
    SimulatorScenario,
)


class SimulatedFlowmeterDevice(FlowmeterDevice):
    """Virtual transmitter implementing the application-level device interface."""

    def __init__(self, scenario: SimulatorScenario) -> None:
        self._scenario = scenario
        self._identity = scenario.build_identity()
        self._rng = random.Random(scenario.seed)
        self._sample_index = 0
        self._connected = False
        self._state = CommunicationState.DISCONNECTED
        self._request_count = 0
        self._success_count = 0
        self._timeout_count = 0
        self._frame_error_count = 0
        self._exception_count = 0
        self._last_error: str | None = None
        self._last_success_at: datetime | None = None
        self._average_response_ms: float | None = None
        self._parameters = self._build_parameters(scenario.parameters)
        self._started_at = datetime(2026, 1, 1, tzinfo=UTC)

    @property
    def scenario(self) -> SimulatorScenario:
        return self._scenario

    @property
    def sample_index(self) -> int:
        return self._sample_index

    def connect(self) -> None:
        self._request_count += 1
        self._connected = True
        self._state = CommunicationState.CONNECTED
        self._record_success()

    def disconnect(self) -> None:
        self._request_count += 1
        self._connected = False
        self._state = CommunicationState.DISCONNECTED
        self._record_success()

    def read_identity(self) -> DeviceIdentity:
        self._begin_request("read_identity")
        self._require_connected()
        self._record_success()
        return self._identity

    def read_health(self) -> DeviceHealth:
        self._begin_request("read_health")
        now = self._current_timestamp()
        alarm_flags = self._active_alarm_flags()
        if not self._connected:
            return DeviceHealth(
                state=CommunicationState.DISCONNECTED,
                message="Simulated device is disconnected.",
                captured_at=now,
            )
        self._record_success()
        return DeviceHealth(
            state=self._state,
            status_flags=("simulated", "measuring"),
            alarm_flags=alarm_flags,
            captured_at=now,
        )

    def read_measurement(self) -> Measurement:
        self._begin_request("read_measurement")
        self._require_connected()

        sample_index = self._sample_index
        timeout = self._scenario.fault_applies(FaultKind.TIMEOUT, sample_index)
        if timeout is not None:
            self._timeout_count += 1
            self._last_error = "Simulated timeout."
            raise TimeoutError(self._last_error)

        disconnection = self._scenario.fault_applies(
            FaultKind.DISCONNECTION, sample_index
        )
        if disconnection is not None:
            self._connected = False
            self._state = CommunicationState.DISCONNECTED
            self._last_error = "Simulated disconnection."
            raise ConnectionError(self._last_error)

        invalid_value = self._scenario.fault_applies(
            FaultKind.INVALID_VALUE, sample_index
        )
        timestamp = self._current_timestamp()
        mass_flow = self._mass_flow(sample_index)
        if invalid_value is not None:
            mass_flow = math.nan
            status_flags = ("invalid_value",)
        else:
            status_flags = ()

        self._sample_index += 1
        self._record_success()
        return Measurement(
            captured_at=timestamp,
            mass_flow=mass_flow,
            volume_flow=self._volume_flow(mass_flow),
            density=self._scenario.density,
            temperature=self._scenario.temperature,
            status_flags=status_flags,
            source_channel=self._identity.device_id,
            raw_values={
                "sample_index": sample_index,
                "scenario": self._scenario.name,
            },
        )

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        self._begin_request("read_configuration")
        self._require_connected()
        self._record_success()
        return tuple(self._parameters.values())

    def write_configuration(
        self, request: ParameterWriteRequest
    ) -> ParameterWriteResult:
        self._begin_request("write_configuration")
        self._require_connected()

        failure = self._scenario.action_fault(
            FaultKind.WRITE_FAILURE, request.parameter_name
        )
        parameter = self._parameters.get(request.parameter_name)
        if parameter is None:
            self._exception_count += 1
            self._last_error = f"Unknown parameter: {request.parameter_name}"
            return self._write_result(
                request,
                WriteResultStatus.REJECTED,
                message=self._last_error,
            )
        if not parameter.writable:
            self._exception_count += 1
            self._last_error = f"Parameter is not writable: {request.parameter_name}"
            return self._write_result(
                request,
                WriteResultStatus.REJECTED,
                previous_value=parameter.value,
                message=self._last_error,
            )
        if not self._in_range(parameter, request.new_value):
            self._exception_count += 1
            self._last_error = f"Parameter value out of range: {request.parameter_name}"
            return self._write_result(
                request,
                WriteResultStatus.REJECTED,
                previous_value=parameter.value,
                message=self._last_error,
            )
        if failure is not None and request.mode is WriteMode.ARMED:
            self._exception_count += 1
            self._last_error = f"Simulated write failure: {request.parameter_name}"
            return self._write_result(
                request,
                WriteResultStatus.FAILED,
                previous_value=parameter.value,
                message=self._last_error,
            )

        if request.mode is WriteMode.PREVIEW:
            status = WriteResultStatus.PREVIEWED
        elif request.mode is WriteMode.DRY_RUN:
            status = WriteResultStatus.DRY_RUN
        else:
            status = WriteResultStatus.APPLIED
            self._apply_parameter_value(parameter, request.new_value)

        self._record_success()
        return self._write_result(
            request,
            status,
            previous_value=parameter.value,
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

    def _begin_request(self, action: str) -> None:
        self._request_count += 1
        delay = self._scenario.action_fault(FaultKind.DELAY, action)
        if delay is not None and delay.value is not None:
            self._average_response_ms = float(delay.value)
        elif self._scenario.response_delay_ms:
            self._average_response_ms = self._scenario.response_delay_ms

    def _require_connected(self) -> None:
        if not self._connected:
            self._state = CommunicationState.DISCONNECTED
            self._last_error = "Simulated device is disconnected."
            raise ConnectionError(self._last_error)

    def _record_success(self) -> None:
        self._success_count += 1
        self._last_success_at = self._current_timestamp()
        self._last_error = None

    def _current_timestamp(self) -> datetime:
        return self._started_at + timedelta(milliseconds=self._sample_index * 100)

    def _mass_flow(self, sample_index: int) -> float:
        profile_value = self._profile_value(self._scenario.flow_profile, sample_index)
        noise = self._rng.gauss(0.0, self._scenario.noise_std)
        drift = self._scenario.drift_per_sample * sample_index
        return profile_value + self._scenario.zero_offset + drift + noise

    def _profile_value(self, profile: FlowProfile, sample_index: int) -> float:
        if profile.kind is FlowProfileKind.CONSTANT:
            return profile.value
        if profile.kind is FlowProfileKind.STEP:
            value = profile.value
            for start, step_value in sorted(profile.steps):
                if sample_index >= start:
                    value = step_value
            return value
        if profile.kind is FlowProfileKind.RAMP:
            return profile.start + profile.slope_per_sample * sample_index
        if profile.kind is FlowProfileKind.SINE:
            period = max(profile.period_samples, 1)
            angle = 2.0 * math.pi * sample_index / period
            return profile.value + profile.amplitude * math.sin(angle)
        raise ValueError(f"Unsupported flow profile: {profile.kind}")

    def _volume_flow(self, mass_flow: float) -> float:
        if math.isnan(mass_flow):
            return math.nan
        if self._scenario.volume_flow_scale:
            return mass_flow * self._scenario.volume_flow_scale
        if self._scenario.density:
            return mass_flow / self._scenario.density
        return mass_flow

    def _active_alarm_flags(self) -> tuple[str, ...]:
        rule = self._scenario.fault_applies(FaultKind.ALARM_FLAG, self._sample_index)
        if rule is None:
            return ()
        if rule.value is None:
            return ("simulated_alarm",)
        if isinstance(rule.value, str):
            return (rule.value,)
        return tuple(str(value) for value in rule.value)

    def _write_result(
        self,
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
            audit_id=f"SIM-AUDIT-{self._request_count:06d}",
            message=message,
        )

    def _build_parameters(
        self, parameters: tuple[ScenarioParameter, ...]
    ) -> dict[str, ConfigurationParameter]:
        if not parameters:
            parameters = (
                ScenarioParameter(
                    name="zero_offset",
                    value=self._scenario.zero_offset,
                    unit="kg/s",
                    writable=True,
                    minimum=-10.0,
                    maximum=10.0,
                ),
            )
        return {
            parameter.name: ConfigurationParameter(
                name=parameter.name,
                value=parameter.value,
                unit=parameter.unit,
                writable=parameter.writable,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                metadata=parameter.metadata,
            )
            for parameter in parameters
        }

    def _apply_parameter_value(
        self,
        parameter: ConfigurationParameter,
        value: Any,
    ) -> None:
        if parameter.metadata.get("simulated_zero_calibration_control") and bool(value):
            self._parameters[parameter.name] = ConfigurationParameter(
                name=parameter.name,
                value=False,
                unit=parameter.unit,
                writable=parameter.writable,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                metadata=parameter.metadata,
            )
            for target_name, target_value in (
                ("zero_offset", parameter.metadata.get("completed_zero_offset", 0.0)),
                ("delta_t", parameter.metadata.get("completed_delta_t", 0.0)),
            ):
                target = self._parameters.get(target_name)
                if target is not None:
                    self._parameters[target_name] = ConfigurationParameter(
                        name=target.name,
                        value=target_value,
                        unit=target.unit,
                        writable=target.writable,
                        minimum=target.minimum,
                        maximum=target.maximum,
                        metadata=target.metadata,
                    )
            return
        self._parameters[parameter.name] = ConfigurationParameter(
            name=parameter.name,
            value=value,
            unit=parameter.unit,
            writable=parameter.writable,
            minimum=parameter.minimum,
            maximum=parameter.maximum,
            metadata=parameter.metadata,
        )

    def _in_range(self, parameter: ConfigurationParameter, value: Any) -> bool:
        if not isinstance(value, int | float):
            return True
        if parameter.minimum is not None and value < parameter.minimum:
            return False
        if parameter.maximum is not None and value > parameter.maximum:
            return False
        return True

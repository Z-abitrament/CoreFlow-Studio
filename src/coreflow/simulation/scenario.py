"""Scenario models for deterministic virtual flowmeter transmitters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from coreflow.devices import DeviceIdentity, DeviceType


class FlowProfileKind(StrEnum):
    """Initial flow profile shapes supported by M2."""

    CONSTANT = "constant"
    STEP = "step"
    RAMP = "ramp"
    SINE = "sine"


class FaultKind(StrEnum):
    """Faults that can be injected by a simulator scenario."""

    TIMEOUT = "timeout"
    DISCONNECTION = "disconnection"
    INVALID_VALUE = "invalid_value"
    DELAY = "delay"
    WRITE_FAILURE = "write_failure"
    ALARM_FLAG = "alarm_flag"


@dataclass(frozen=True, slots=True)
class FlowProfile:
    """Configurable flow profile used to generate mass-flow values."""

    kind: FlowProfileKind = FlowProfileKind.CONSTANT
    value: float = 0.0
    steps: tuple[tuple[int, float], ...] = ()
    start: float = 0.0
    slope_per_sample: float = 0.0
    amplitude: float = 0.0
    period_samples: int = 1


@dataclass(frozen=True, slots=True)
class FaultRule:
    """A scheduled fault triggered by sample index or action."""

    kind: FaultKind
    start_sample: int = 0
    end_sample: int | None = None
    action: str | None = None
    value: Any | None = None

    def applies_to_sample(self, sample_index: int) -> bool:
        if sample_index < self.start_sample:
            return False
        return self.end_sample is None or sample_index <= self.end_sample

    def applies_to_action(self, action: str) -> bool:
        return self.action == action


@dataclass(frozen=True, slots=True)
class ScenarioParameter:
    """Initial simulator parameter definition."""

    name: str
    value: Any
    unit: str | None = None
    writable: bool = False
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True, slots=True)
class SimulatorScenario:
    """Complete deterministic scenario for one virtual transmitter."""

    name: str
    device_id: str
    seed: int
    flow_profile: FlowProfile = field(default_factory=FlowProfile)
    density: float = 998.2
    temperature: float = 20.0
    volume_flow_scale: float = 1.0
    zero_offset: float = 0.0
    noise_std: float = 0.0
    drift_per_sample: float = 0.0
    response_delay_ms: float = 0.0
    identity: DeviceIdentity | None = None
    parameters: tuple[ScenarioParameter, ...] = ()
    fault_rules: tuple[FaultRule, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def build_identity(self) -> DeviceIdentity:
        if self.identity is not None:
            return self.identity
        return DeviceIdentity(
            device_id=self.device_id,
            device_type=DeviceType.SIMULATED,
            serial_number=self.device_id,
            model="Simulated Coriolis Transmitter",
            firmware_version="sim-m2",
            hardware_version="virtual",
            protocol_address=self.device_id,
            metadata={
                "scenario": self.name,
                "seed": self.seed,
                **self.metadata,
            },
        )

    def fault_applies(self, kind: FaultKind, sample_index: int) -> FaultRule | None:
        for rule in self.fault_rules:
            if rule.kind is kind and rule.applies_to_sample(sample_index):
                return rule
        return None

    def action_fault(self, kind: FaultKind, action: str) -> FaultRule | None:
        for rule in self.fault_rules:
            if rule.kind is kind and rule.applies_to_action(action):
                return rule
        return None

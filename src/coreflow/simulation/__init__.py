"""Simulator devices and scenario support."""

from coreflow.simulation.device import SimulatedFlowmeterDevice
from coreflow.simulation.manager import SimulatorManager
from coreflow.simulation.scenario import (
    FaultKind,
    FaultRule,
    FlowProfile,
    FlowProfileKind,
    ScenarioParameter,
    SimulatorScenario,
)

__all__ = [
    "FaultKind",
    "FaultRule",
    "FlowProfile",
    "FlowProfileKind",
    "ScenarioParameter",
    "SimulatedFlowmeterDevice",
    "SimulatorManager",
    "SimulatorScenario",
]

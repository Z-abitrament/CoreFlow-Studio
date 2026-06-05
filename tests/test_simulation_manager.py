from __future__ import annotations

from coreflow.devices import Measurement
from coreflow.simulation import (
    FaultKind,
    FaultRule,
    FlowProfile,
    FlowProfileKind,
    SimulatorManager,
    SimulatorScenario,
)


def _scenario(index: int) -> SimulatorScenario:
    return SimulatorScenario(
        name=f"device-{index}",
        device_id=f"SIM-{index:03d}",
        seed=index,
        flow_profile=FlowProfile(
            kind=FlowProfileKind.CONSTANT,
            value=float(index),
        ),
    )


def test_manager_runs_eight_virtual_devices_concurrently() -> None:
    manager = SimulatorManager(_scenario(index) for index in range(8))

    manager.connect_all()
    results = manager.read_all_measurements()

    assert len(results) == 8
    assert all(isinstance(result, Measurement) for result in results.values())
    assert results["SIM-007"].mass_flow == 7.0


def test_one_faulted_virtual_device_does_not_stop_others() -> None:
    scenarios = [_scenario(index) for index in range(4)]
    scenarios.append(
        SimulatorScenario(
            name="faulted",
            device_id="SIM-FAULTED",
            seed=99,
            flow_profile=FlowProfile(
                kind=FlowProfileKind.CONSTANT,
                value=99.0,
            ),
            fault_rules=(FaultRule(kind=FaultKind.TIMEOUT, start_sample=0),),
        )
    )
    manager = SimulatorManager(scenarios)

    manager.connect_all()
    results = manager.read_all_measurements()

    assert isinstance(results["SIM-FAULTED"], TimeoutError)
    assert isinstance(results["SIM-000"], Measurement)
    assert isinstance(results["SIM-003"], Measurement)

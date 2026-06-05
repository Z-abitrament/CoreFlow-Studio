"""Multi-device simulator manager."""

from __future__ import annotations

from collections.abc import Iterable

from coreflow.simulation.device import SimulatedFlowmeterDevice
from coreflow.simulation.scenario import SimulatorScenario


class SimulatorManager:
    """Creates and owns multiple independent virtual devices."""

    def __init__(self, scenarios: Iterable[SimulatorScenario]) -> None:
        self._devices = {
            scenario.device_id: SimulatedFlowmeterDevice(scenario)
            for scenario in scenarios
        }

    @property
    def devices(self) -> tuple[SimulatedFlowmeterDevice, ...]:
        return tuple(self._devices.values())

    def get(self, device_id: str) -> SimulatedFlowmeterDevice:
        return self._devices[device_id]

    def connect_all(self) -> None:
        for device in self._devices.values():
            device.connect()

    def disconnect_all(self) -> None:
        for device in self._devices.values():
            device.disconnect()

    def read_all_measurements(self) -> dict[str, object]:
        results: dict[str, object] = {}
        for device_id, device in self._devices.items():
            try:
                results[device_id] = device.read_measurement()
            except (ConnectionError, TimeoutError) as exc:
                results[device_id] = exc
        return results

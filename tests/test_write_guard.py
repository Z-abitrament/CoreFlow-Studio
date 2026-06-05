from __future__ import annotations

from coreflow.app import WriteGuardService
from coreflow.devices import ParameterWriteRequest, WriteMode, WriteResultStatus
from coreflow.simulation import (
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import Database, DeviceRecord, StorageRepository


def _device() -> SimulatedFlowmeterDevice:
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="write_guard",
            device_id="SIM-GUARD",
            seed=1,
            parameters=(
                ScenarioParameter(
                    name="zero_offset",
                    value=0.0,
                    writable=True,
                    minimum=-1.0,
                    maximum=1.0,
                ),
                ScenarioParameter(name="read_only", value=0.0, writable=False),
            ),
        )
    )
    device.connect()
    return device


def _repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    repository.save_device(DeviceRecord(device_id="SIM-GUARD", device_type="simulated"))
    return repository


def test_write_guard_rejects_armed_write_outside_write_state(tmp_path) -> None:
    repository = _repository(tmp_path)
    guard = WriteGuardService(repository)
    device = _device()

    decision = guard.evaluate(
        device,
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.5,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="calibration_preview",
            run_id=None,
        ),
    )

    assert decision.allowed is False
    assert decision.result.status is WriteResultStatus.REJECTED
    assert repository.count_rows("audit_logs") == 1
    assert device.read_configuration()[0].value == 0.0


def test_write_guard_allows_dry_run_without_changing_device(tmp_path) -> None:
    repository = _repository(tmp_path)
    guard = WriteGuardService(repository)
    device = _device()

    decision = guard.evaluate(
        device,
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.5,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="calibration_preview",
        ),
    )

    assert decision.allowed is True
    assert decision.result.status is WriteResultStatus.DRY_RUN
    assert repository.count_rows("audit_logs") == 1
    assert device.read_configuration()[0].value == 0.0


def test_write_guard_rejects_read_only_and_out_of_range_values(tmp_path) -> None:
    repository = _repository(tmp_path)
    guard = WriteGuardService(repository)
    device = _device()

    read_only = guard.evaluate(
        device,
        ParameterWriteRequest(
            parameter_name="read_only",
            new_value=1.0,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="calibration_preview",
        ),
    )
    out_of_range = guard.evaluate(
        device,
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=2.0,
            mode=WriteMode.DRY_RUN,
            actor="pytest",
            workflow_state="calibration_preview",
        ),
    )

    assert read_only.allowed is False
    assert out_of_range.allowed is False
    assert repository.count_rows("audit_logs") == 2

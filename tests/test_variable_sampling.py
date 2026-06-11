from __future__ import annotations

from coreflow.app.variable_sampling import VariableSamplingService
from coreflow.simulation import (
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import Database, StorageRepository
from coreflow.storage.models import DeviceRecord
from coreflow.workflows import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


def test_variable_sampling_service_persists_selected_configuration_values(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="sampling",
            device_id="SIM-SAMPLE",
            seed=1,
            parameters=(
                ScenarioParameter(
                    name="mass_acc",
                    value=12.5,
                    unit="kg",
                    metadata={"logical_group": "measurement"},
                ),
                ScenarioParameter(name="k_factor", value=42.0, writable=True),
            ),
        )
    )
    device.connect()
    repository.save_device(DeviceRecord(device_id="SIM-SAMPLE", device_type="simulated"))
    repository.save_run(
        RunSession(
            run_id="RUN-SAMPLE",
            run_type=RunType.EXPERIMENT,
            workflow_name="variable_sampling",
            workflow_version="0.1",
            device_id="SIM-SAMPLE",
            operator="pytest",
            status=RunStatus.RUNNING,
        )
    )
    repository.save_step(
        WorkflowStep(
            step_id="STEP-SAMPLE",
            run_id="RUN-SAMPLE",
            name="Sample configured variables",
            step_type=WorkflowStepType.DEVICE_READ,
            status=WorkflowStepStatus.RUNNING,
        )
    )
    service = VariableSamplingService(repository)

    samples = service.sample_configuration(
        device,
        variable_names=("mass_acc",),
        run_id="RUN-SAMPLE",
        step_id="STEP-SAMPLE",
    )
    stored = repository.list_variable_samples(
        device_id="SIM-SAMPLE",
        variable_name="mass_acc",
    )

    assert len(samples) == 1
    assert samples[0].value == 12.5
    assert stored[0].unit == "kg"
    assert stored[0].metadata["logical_group"] == "measurement"

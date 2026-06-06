from __future__ import annotations

from coreflow.simulation import (
    FlowProfile,
    FlowProfileKind,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import (
    ArtifactStore,
    Database,
    StorageRepository,
    check_artifact_integrity,
)
from coreflow.workflows.factory_test import (
    FactoryMeasurementCheck,
    FactoryStabilityCheck,
    FactoryTestConfig,
    FactoryTestWorkflow,
)


def _storage(tmp_path):
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return StorageRepository(database), ArtifactStore(tmp_path)


def _workflow(tmp_path) -> FactoryTestWorkflow:
    repository, artifact_store = _storage(tmp_path)
    return FactoryTestWorkflow(repository, artifact_store)


def _passing_config(run_id: str) -> FactoryTestConfig:
    return FactoryTestConfig(
        run_id=run_id,
        operator="pytest",
        measurement_check=FactoryMeasurementCheck(
            reference_mass_flow=10.0,
            sample_count=3,
            max_abs_error=0.25,
        ),
        stability_check=FactoryStabilityCheck(
            sample_count=4,
            max_range=0.1,
            max_stddev=0.1,
        ),
    )


def test_factory_test_workflow_passes_against_nominal_simulator(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    workflow = FactoryTestWorkflow(repository, artifact_store)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="factory_pass",
            device_id="SIM-FACTORY-PASS",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=10.0),
        )
    )

    result = workflow.run(device, _passing_config("RUN-20260606-000200"))
    statuses = dict(repository.list_step_statuses("RUN-20260606-000200"))
    analysis = repository.list_analysis_results("RUN-20260606-000200")

    assert result.passed is True
    assert repository.get_run_status("RUN-20260606-000200") == "passed"
    assert statuses == {
        "Communication health": "passed",
        "Identity and configuration capture": "passed",
        "Measurement check": "passed",
        "Stability segment": "passed",
    }
    assert repository.count_rows("artifacts") == 2
    assert analysis[0].pass_fail_decision == "passed"
    assert analysis[0].summary_metrics["measurement_abs_error"] == 0.0
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_factory_test_workflow_records_failed_measurement_check(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    workflow = FactoryTestWorkflow(repository, artifact_store)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="factory_fail",
            device_id="SIM-FACTORY-FAIL",
            seed=1,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=11.0),
        )
    )

    result = workflow.run(device, _passing_config("RUN-20260606-000201"))
    statuses = dict(repository.list_step_statuses("RUN-20260606-000201"))
    analysis = repository.list_analysis_results("RUN-20260606-000201")

    assert result.passed is False
    assert repository.get_run_status("RUN-20260606-000201") == "failed"
    assert statuses["Measurement check"] == "failed"
    assert statuses["Stability segment"] == "passed"
    assert analysis[0].pass_fail_decision == "failed"
    assert result.summary_metrics["measurement_abs_error"] == 1.0


def test_factory_test_workflows_isolate_faulted_device(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    devices = [
        SimulatedFlowmeterDevice(
            SimulatorScenario(
                name=f"factory-{index}",
                device_id=f"SIM-FACTORY-{index}",
                seed=index,
                flow_profile=FlowProfile(
                    kind=FlowProfileKind.CONSTANT,
                    value=10.0 if index != 3 else 12.0,
                ),
            )
        )
        for index in range(8)
    ]

    results = []
    for index, device in enumerate(devices):
        workflow = FactoryTestWorkflow(repository, artifact_store)
        results.append(
            workflow.run(
                device,
                _passing_config(f"RUN-20260606-0003{index:02d}"),
            )
        )

    assert len(results) == 8
    assert results[3].passed is False
    assert all(result.passed for index, result in enumerate(results) if index != 3)
    assert repository.count_rows("run_sessions") == 8
    assert repository.count_rows("analysis_results") == 8

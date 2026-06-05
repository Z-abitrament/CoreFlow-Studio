from __future__ import annotations

from coreflow.analysis.calibration import CalibrationReferencePoint
from coreflow.simulation import (
    FlowProfile,
    FlowProfileKind,
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import (
    ArtifactStore,
    Database,
    StorageRepository,
    check_artifact_integrity,
)
from coreflow.workflows.calibration import (
    CalibrationPreviewConfig,
    CalibrationPreviewWorkflow,
)


def _storage(tmp_path):
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    return repository, ArtifactStore(tmp_path)


def test_calibration_preview_runs_end_to_end_against_simulator(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="calibration",
            device_id="SIM-CAL",
            seed=10,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=10.5),
            parameters=(
                ScenarioParameter(
                    name="zero_offset",
                    value=0.0,
                    writable=True,
                    minimum=-10.0,
                    maximum=10.0,
                ),
            ),
        )
    )
    workflow = CalibrationPreviewWorkflow(repository, artifact_store)

    result = workflow.run(
        device,
        CalibrationPreviewConfig(
            run_id="RUN-20260605-000100",
            operator="pytest",
            reference_points=(
                CalibrationReferencePoint(reference_mass_flow=10.0, sample_count=3),
            ),
        ),
    )

    assert result.run_id == "RUN-20260605-000100"
    assert result.preview.summary_metrics["mean_error"] == 0.5
    assert result.preview.proposed_writes[0].new_value == -0.5
    assert result.raw_artifact_ids == ("RUN-20260605-000100-RAW-001",)
    assert len(result.proposed_audit_ids) == 1
    assert repository.count_rows("devices") == 1
    assert repository.count_rows("run_sessions") == 1
    assert repository.count_rows("workflow_steps") == 1
    assert repository.count_rows("artifacts") == 1
    assert repository.count_rows("analysis_results") == 1
    assert repository.count_rows("audit_logs") == 1
    assert device.read_configuration()[0].value == 0.0
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_calibration_preview_requires_reference_points(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="calibration",
            device_id="SIM-CAL",
            seed=10,
        )
    )
    workflow = CalibrationPreviewWorkflow(repository, artifact_store)

    try:
        workflow.run(
            device,
            CalibrationPreviewConfig(
                run_id="RUN-20260605-000101",
                operator="pytest",
                reference_points=(),
            ),
        )
    except ValueError as exc:
        assert "reference points" in str(exc)
    else:
        raise AssertionError("Expected missing reference points to fail")

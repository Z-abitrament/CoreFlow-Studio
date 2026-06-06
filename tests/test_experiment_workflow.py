from __future__ import annotations

import csv

from coreflow.experiments import (
    CapturePlan,
    ExperimentDefinition,
    FixtureAction,
    MLInferenceConfig,
    ProcessingModuleConfig,
)
from coreflow.simulation import (
    FlowProfile,
    FlowProfileKind,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import (
    ArtifactStore,
    ArtifactType,
    Database,
    StorageRepository,
    check_artifact_integrity,
)
from coreflow.workflows.experiment import (
    ExperimentWorkflow,
    ExperimentWorkflowConfig,
)


def _storage(tmp_path):
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    return repository, ArtifactStore(tmp_path)


def _device() -> SimulatedFlowmeterDevice:
    return SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="experiment",
            device_id="SIM-EXP",
            seed=8,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=12.0),
        )
    )


def _definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id="EXP-PYTEST",
        name="pytest basic stats",
        version="0.1",
        capture_plan=CapturePlan(sample_count=5, label="pytest_capture"),
        processing=(ProcessingModuleConfig(module_name="basic_signal_stats"),),
        fixture_actions=(
            FixtureAction(
                action_name="future_reference_pump",
                parameters={"setpoint": 12.0},
                required=False,
            ),
        ),
        ml_inference=(
            MLInferenceConfig(
                model_name="placeholder_quality_model",
                enabled=True,
            ),
        ),
        metadata={"purpose": "extension_test"},
    )


def test_experiment_workflow_runs_simulated_capture_and_processing(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    workflow = ExperimentWorkflow(repository, artifact_store)

    result = workflow.run(
        _device(),
        ExperimentWorkflowConfig(
            run_id="RUN-20260606-001000",
            operator="pytest",
            definition=_definition(),
        ),
    )
    artifacts = {artifact.artifact_id: artifact for artifact in repository.list_artifacts()}
    analysis = repository.list_analysis_results("RUN-20260606-001000")
    steps = {step.name: step for step in repository.list_steps("RUN-20260606-001000")}
    processed_rows = _read_csv(
        tmp_path / artifacts[result.processed_artifact_ids[0]].file_path
    )

    assert result.raw_artifact_id == "RUN-20260606-001000-EXPERIMENT-RAW"
    assert result.processed_artifact_ids == ("RUN-20260606-001000-PROCESSED-001",)
    assert result.analysis_result_ids == (
        "RUN-20260606-001000-EXPERIMENT-RESULT-001",
    )
    assert artifacts[result.raw_artifact_id].artifact_type is ArtifactType.RAW
    assert artifacts[result.processed_artifact_ids[0]].artifact_type is ArtifactType.PROCESSED
    assert repository.get_run_status("RUN-20260606-001000") == "passed"
    assert analysis[0].result_type == "experiment_signal_processing"
    assert analysis[0].summary_metrics["sample_count"] == 5.0
    assert analysis[0].summary_metrics["mass_flow_mean"] == 12.0
    assert len(processed_rows) == 5
    assert steps["Experiment capture"].status.value == "passed"
    assert steps["Process basic_signal_stats"].status.value == "passed"
    assert steps["Fixture action future_reference_pump"].status.value == "skipped"
    assert steps["ML inference placeholder_quality_model"].status.value == "skipped"
    assert "not configured" in result.fixture_messages[0]
    assert "placeholder" in result.ml_messages[0]
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_experiment_workflow_rejects_missing_processing(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    workflow = ExperimentWorkflow(repository, artifact_store)
    definition = ExperimentDefinition(
        experiment_id="EXP-NO-PROCESSING",
        name="invalid",
        version="0.1",
        capture_plan=CapturePlan(sample_count=1),
        processing=(),
    )

    try:
        workflow.run(
            _device(),
            ExperimentWorkflowConfig(
                run_id="RUN-20260606-001001",
                operator="pytest",
                definition=definition,
            ),
        )
    except ValueError as exc:
        assert "processing module" in str(exc)
    else:
        raise AssertionError("Expected missing processing to fail")


def test_required_fixture_placeholder_fails_before_hardware_use(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    workflow = ExperimentWorkflow(repository, artifact_store)
    definition = ExperimentDefinition(
        experiment_id="EXP-REQUIRED-FIXTURE",
        name="fixture gated",
        version="0.1",
        capture_plan=CapturePlan(sample_count=1),
        processing=(ProcessingModuleConfig(module_name="basic_signal_stats"),),
        fixture_actions=(
            FixtureAction(action_name="reference_valve", required=True),
        ),
    )

    try:
        workflow.run(
            _device(),
            ExperimentWorkflowConfig(
                run_id="RUN-20260606-001002",
                operator="pytest",
                definition=definition,
            ),
        )
    except ValueError as exc:
        assert "Required fixture action" in str(exc)
    else:
        raise AssertionError("Expected required fixture placeholder to fail")
    assert repository.get_run_status("RUN-20260606-001002") == "error"


def _read_csv(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))

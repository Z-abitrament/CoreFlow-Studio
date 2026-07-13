from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PureWindowsPath

from coreflow.storage import Artifact, ArtifactType
from coreflow.workflows import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


def test_run_session_model_records_traceable_metadata() -> None:
    run = RunSession(
        run_id="RUN-20260605-000001",
        run_type=RunType.CALIBRATION,
        workflow_name="calibration_preview",
        workflow_version="0.1",
        device_id="SIM-001",
        operator="pytest",
        status=RunStatus.RUNNING,
        started_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
        configuration_snapshot={"scenario": "nominal"},
        software_version="0.1.0",
    )

    assert run.run_type is RunType.CALIBRATION
    assert run.configuration_snapshot["scenario"] == "nominal"
    assert run.status is RunStatus.RUNNING


def test_workflow_step_model_records_inputs_outputs_and_errors() -> None:
    step = WorkflowStep(
        step_id="STEP-001",
        run_id="RUN-001",
        name="Read identity",
        step_type=WorkflowStepType.DEVICE_READ,
        status=WorkflowStepStatus.PASSED,
        input_configuration={"fields": ["identity"]},
        output_summary={"serial_number": "SN-001"},
    )

    assert step.step_type is WorkflowStepType.DEVICE_READ
    assert step.output_summary["serial_number"] == "SN-001"
    assert step.error_message is None


def test_filling_workflow_enums_use_neutral_completion_values() -> None:
    assert RunType.FILLING_TRIAL.value == "filling_trial"
    assert RunStatus.COMPLETED.value == "completed"
    assert WorkflowStepStatus.COMPLETED.value == "completed"


def test_artifact_model_links_files_to_runs() -> None:
    artifact = Artifact(
        artifact_id="ART-001",
        run_id="RUN-001",
        step_id="STEP-001",
        artifact_type=ArtifactType.RAW,
        file_path=PureWindowsPath("artifacts/runs/RUN-001/raw/samples.csv"),
        file_format="csv",
        size_bytes=1024,
        checksum="sha256:test",
        created_at=datetime(2026, 6, 5, 8, 0, tzinfo=UTC),
    )

    assert artifact.artifact_type is ArtifactType.RAW
    assert artifact.file_path.name == "samples.csv"
    assert artifact.checksum == "sha256:test"

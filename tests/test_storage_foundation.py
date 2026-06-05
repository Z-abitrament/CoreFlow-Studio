from __future__ import annotations

from datetime import UTC, datetime

from coreflow.storage import (
    AnalysisResultRecord,
    ArtifactStore,
    ArtifactType,
    AuditLogRecord,
    Database,
    DeviceRecord,
    StorageRepository,
    check_artifact_integrity,
)
from coreflow.workflows import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


def test_database_initialization_creates_schema(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)

    assert repository.count_rows("schema_migrations") == 1
    assert (tmp_path / "coreflow.sqlite").exists()


def test_repository_persists_run_metadata_and_artifacts(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    store = ArtifactStore(tmp_path)
    created_at = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)

    repository.save_device(
        DeviceRecord(
            device_id="SIM-001",
            device_type="simulated",
            serial_number="SIM-001",
            model="virtual",
            connection_metadata={"scenario": "nominal"},
        )
    )
    repository.save_run(
        RunSession(
            run_id="RUN-20260605-000001",
            run_type=RunType.CALIBRATION,
            workflow_name="calibration_preview",
            workflow_version="0.1",
            device_id="SIM-001",
            operator="pytest",
            status=RunStatus.RUNNING,
            started_at=created_at,
            configuration_snapshot={"scenario": "nominal"},
            software_version="0.1.0",
        )
    )
    repository.save_step(
        WorkflowStep(
            step_id="STEP-001",
            run_id="RUN-20260605-000001",
            name="Capture raw samples",
            step_type=WorkflowStepType.CAPTURE,
            status=WorkflowStepStatus.PASSED,
            started_at=created_at,
            ended_at=created_at,
            output_summary={"samples": 2},
        )
    )
    artifact = store.write_artifact(
        run_id="RUN-20260605-000001",
        artifact_id="ART-001",
        artifact_type=ArtifactType.RAW,
        file_name="samples.csv",
        content=b"t,mass_flow\n0,1.0\n",
        created_at=created_at,
        step_id="STEP-001",
    )
    repository.save_artifact(artifact)
    repository.save_analysis_result(
        AnalysisResultRecord(
            result_id="RES-001",
            run_id="RUN-20260605-000001",
            step_id="STEP-001",
            result_type="stability",
            algorithm_name="example",
            algorithm_version="0.1",
            input_artifact_ids=("ART-001",),
            summary_metrics={"mean": 1.0},
            pass_fail_decision="passed",
            created_at=created_at,
        )
    )
    repository.save_audit_log(
        AuditLogRecord(
            audit_id="AUD-001",
            timestamp=created_at,
            actor="pytest",
            action_type="parameter_write",
            device_id="SIM-001",
            run_id="RUN-20260605-000001",
            workflow_state="dry_run",
            target="zero_offset",
            previous_value=0,
            new_value=1,
            dry_run=True,
            validation_result="accepted",
            result="dry_run",
        )
    )

    stored_device = repository.get_device("SIM-001")
    artifacts = repository.list_artifacts("RUN-20260605-000001")
    issues = check_artifact_integrity(repository, tmp_path)

    assert stored_device is not None
    assert stored_device.connection_metadata["scenario"] == "nominal"
    assert repository.count_rows("run_sessions") == 1
    assert repository.count_rows("workflow_steps") == 1
    assert repository.count_rows("analysis_results") == 1
    assert repository.count_rows("audit_logs") == 1
    assert artifacts[0].artifact_id == "ART-001"
    assert artifacts[0].file_path.as_posix().endswith("raw/samples.csv")
    assert (tmp_path / artifacts[0].file_path).exists()
    assert issues == ()


def test_artifact_store_uses_human_sortable_run_directory(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    created_at = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)
    run_id = store.next_run_id(created_at, sequence=7)

    artifact = store.write_artifact(
        run_id=run_id,
        artifact_id="ART-007",
        artifact_type=ArtifactType.REPORT,
        file_name="report.txt",
        content=b"report",
        created_at=created_at,
    )

    assert run_id == "RUN-20260605-000007"
    assert artifact.file_path.as_posix() == (
        "artifacts/runs/2026/06/RUN-20260605-000007/report/report.txt"
    )
    assert artifact.size_bytes == 6


def test_integrity_check_reports_missing_artifacts(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    store = ArtifactStore(tmp_path)
    created_at = datetime(2026, 6, 5, 8, 0, tzinfo=UTC)

    repository.save_device(DeviceRecord(device_id="SIM-001", device_type="simulated"))
    repository.save_run(
        RunSession(
            run_id="RUN-20260605-000001",
            run_type=RunType.EXPERIMENT,
            workflow_name="experiment",
            workflow_version="0.1",
            device_id="SIM-001",
            operator="pytest",
        )
    )
    artifact = store.write_artifact(
        run_id="RUN-20260605-000001",
        artifact_id="ART-MISSING",
        artifact_type=ArtifactType.LOG,
        file_name="diagnostic.log",
        content=b"log",
        created_at=created_at,
    )
    repository.save_artifact(artifact)
    (tmp_path / artifact.file_path).unlink()

    issues = check_artifact_integrity(repository, tmp_path)

    assert len(issues) == 1
    assert issues[0].artifact_id == "ART-MISSING"
    assert "Missing artifact file" in issues[0].message

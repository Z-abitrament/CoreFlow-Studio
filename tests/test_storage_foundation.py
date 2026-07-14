from __future__ import annotations

from datetime import UTC, datetime

from coreflow.storage import (
    AnalysisResultRecord,
    ArtifactStore,
    ArtifactType,
    AuditLogRecord,
    Database,
    DeviceRecord,
    ModbusDeviceProfileRecord,
    ModbusOperationAttemptRecord,
    ModbusTestSessionRecord,
    ModbusTrialRecord,
    StorageRepository,
    VariableSampleRecord,
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
    database.initialize()
    repository = StorageRepository(database)

    assert repository.count_rows("schema_migrations") == 1
    with database.connect() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
    assert versions == [5]
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
    repository.save_variable_sample(
        VariableSampleRecord(
            sample_id="VAR-001",
            device_id="SIM-001",
            run_id="RUN-20260605-000001",
            step_id="STEP-001",
            variable_name="mass_acc",
            captured_at=created_at,
            value=123.45,
            unit="kg",
            source_channel="SIM-001",
            metadata={"register_kind": "input"},
        )
    )

    stored_device = repository.get_device("SIM-001")
    artifacts = repository.list_artifacts("RUN-20260605-000001")
    samples = repository.list_variable_samples(run_id="RUN-20260605-000001")
    issues = check_artifact_integrity(repository, tmp_path)

    assert stored_device is not None
    assert stored_device.connection_metadata["scenario"] == "nominal"
    assert repository.count_rows("run_sessions") == 1
    assert repository.count_rows("workflow_steps") == 1
    assert repository.count_rows("analysis_results") == 1
    assert repository.count_rows("audit_logs") == 1
    assert repository.count_rows("variable_samples") == 1
    assert samples[0].variable_name == "mass_acc"
    assert samples[0].value == 123.45
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


def test_repository_persists_modbus_test_records(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    captured_at = datetime(2026, 6, 12, 8, 0, tzinfo=UTC)

    repository.save_modbus_device_profile(
        ModbusDeviceProfileRecord(
            profile_id="profile:DEV-1",
            device_id="DEV-1",
            device_model="CFM-100",
            tube_model="T-25",
            transmitter_model="TX-9",
            connection_settings={"port": "COM9", "unit_id": 1},
            register_map={"name": "map-a"},
        )
    )
    repository.save_modbus_test_session(
        ModbusTestSessionRecord(
            session_id="SESSION-1",
            device_id="DEV-1",
            profile_id="profile:DEV-1",
            operator="pytest",
            status="running",
            started_at=captured_at,
            device_metadata={
                "device_model": "CFM-100",
                "tube_model": "T-25",
                "transmitter_model": "TX-9",
            },
        )
    )
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="ATTEMPT-1",
            session_id="SESSION-1",
            device_id="DEV-1",
            operation_type="manual_error_repeatability_trial",
            status="accepted",
            started_at=captured_at,
            ended_at=captured_at,
            operator="pytest",
            device_metadata={
                "device_model": "CFM-100",
                "tube_model": "T-25",
                "transmitter_model": "TX-9",
            },
            summary={"percent_error": 0.12},
        )
    )
    repository.save_modbus_trial_record(
        ModbusTrialRecord(
            trial_id="TRIAL-1",
            session_id="SESSION-1",
            attempt_id="ATTEMPT-1",
            device_id="DEV-1",
            flow_point=100.0,
            trial_index=1,
            trial_status="accepted",
            k_factor_parameter="k_factor",
            original_k_factor=500.0,
            mass_acc_before=0.0,
            mass_acc_after=10.0,
            measured_mass_delta=10.0,
            standard_mass=9.99,
            percent_error=0.10010010010010009,
            mean_flow=1.0,
            instant_flow=1.1,
            flow_started_at=captured_at,
            flow_instant_at=captured_at,
            flow_ended_at=captured_at,
            device_metadata={
                "device_model": "CFM-100",
                "tube_model": "T-25",
                "transmitter_model": "TX-9",
            },
        )
    )

    profiles = repository.list_modbus_device_profiles()
    attempts = repository.list_modbus_operation_attempts(tube_model="T-25")
    trials = repository.list_modbus_trial_records(transmitter_model="TX-9")

    assert profiles[0].device_id == "DEV-1"
    assert attempts[0].summary["percent_error"] == 0.12
    assert trials[0].trial_status == "accepted"
    assert trials[0].k_factor_parameter == "k_factor"
    assert trials[0].original_k_factor == 500.0
    assert repository.count_rows("modbus_trial_records") == 1


def test_repository_deletes_modbus_profile_without_deleting_test_records(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    captured_at = datetime(2026, 6, 12, 8, 0, tzinfo=UTC)

    repository.save_modbus_device_profile(
        ModbusDeviceProfileRecord(
            profile_id="profile:DEV-KEEP-HISTORY",
            device_id="DEV-KEEP-HISTORY",
        )
    )
    repository.save_modbus_test_session(
        ModbusTestSessionRecord(
            session_id="SESSION-KEEP-HISTORY",
            device_id="DEV-KEEP-HISTORY",
            profile_id="profile:DEV-KEEP-HISTORY",
            operator="pytest",
            status="closed",
            started_at=captured_at,
        )
    )

    assert repository.delete_modbus_device_profile("DEV-KEEP-HISTORY") is True

    assert repository.get_modbus_device_profile("DEV-KEEP-HISTORY") is None
    sessions = repository.list_modbus_test_sessions(device_id="DEV-KEEP-HISTORY")
    assert len(sessions) == 1
    assert sessions[0].device_id == "DEV-KEEP-HISTORY"
    assert sessions[0].profile_id is None

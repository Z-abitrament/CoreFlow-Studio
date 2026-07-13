from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from coreflow.storage import (
    AnalysisResultRecord,
    Database,
    DeviceRecord,
    FillingAdvanceProfileRecord,
    FillingTrialRecord,
    StorageRepository,
)
from coreflow.workflows import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


STARTED_AT = datetime(2026, 7, 13, 1, 0, tzinfo=UTC)
CALCULATED_AT = datetime(2026, 7, 13, 1, 5, tzinfo=UTC)


def _create_v3_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE devices (
                device_id TEXT PRIMARY KEY,
                device_type TEXT NOT NULL,
                serial_number TEXT,
                model TEXT,
                firmware_version TEXT,
                hardware_version TEXT,
                protocol_address TEXT,
                connection_metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE modbus_device_profiles (
                profile_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL UNIQUE,
                display_name TEXT,
                device_model TEXT,
                tube_model TEXT,
                transmitter_model TEXT,
                connection_settings_json TEXT NOT NULL DEFAULT '{}',
                register_map_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (3, '2026-07-13T00:00:00+00:00')"
        )
        connection.execute(
            """
            INSERT INTO devices (
                device_id, device_type, created_at, updated_at
            )
            VALUES ('CFM-1', 'modbus_rtu', '2026-07-13', '2026-07-13')
            """
        )
        connection.execute(
            """
            INSERT INTO modbus_device_profiles (
                profile_id, device_id, created_at, updated_at
            )
            VALUES ('profile:ORPHAN', 'ORPHAN', '2026-07-13', '2026-07-13')
            """
        )


def _repository(tmp_path) -> tuple[Database, StorageRepository]:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return database, StorageRepository(database)


def _device(
    device_id: str = "CFM-1",
    *,
    created_at: datetime = STARTED_AT,
) -> DeviceRecord:
    return DeviceRecord(
        device_id=device_id,
        device_type="modbus_rtu",
        serial_number=f"SERIAL-{device_id}",
        model="CFM-200",
        connection_metadata={"source": "filling", "nested": {"channel": 2}},
        created_at=created_at,
        updated_at=created_at,
    )


def _run(
    run_id: str = "RUN-FILL-1",
    *,
    device_id: str = "CFM-1",
    run_type: RunType = RunType.FILLING_TRIAL,
    status: RunStatus = RunStatus.RUNNING,
    started_at: datetime = STARTED_AT,
) -> RunSession:
    return RunSession(
        run_id=run_id,
        run_type=run_type,
        workflow_name="filling_trial_group",
        workflow_version="1",
        device_id=device_id,
        operator="pytest",
        status=status,
        started_at=started_at,
        configuration_snapshot={"mode": "regular", "labels": ["CTRL-A", "VALVE-2"]},
        software_version="0.7.0",
        notes=f"notes for {run_id}",
    )


def _step(
    step_id: str = "STEP-FILL-1",
    *,
    run_id: str = "RUN-FILL-1",
    name: str = "Calculate filling trial 1",
) -> WorkflowStep:
    return WorkflowStep(
        step_id=step_id,
        run_id=run_id,
        name=name,
        step_type=WorkflowStepType.ANALYSIS,
        status=WorkflowStepStatus.COMPLETED,
        started_at=STARTED_AT,
        ended_at=CALCULATED_AT,
        input_configuration={"trial_index": 1},
        output_summary={"percent_error": 0.5},
    )


def _trial(
    trial_id: str = "TRIAL-FILL-1",
    *,
    run_id: str = "RUN-FILL-1",
    device_id: str = "CFM-1",
    trial_index: int = 1,
    calculated_at: datetime | None = CALCULATED_AT,
) -> FillingTrialRecord:
    return FillingTrialRecord(
        trial_id=trial_id,
        run_id=run_id,
        device_id=device_id,
        trial_index=trial_index,
        trial_status="calculated",
        mode="regular",
        control_valve_label="CTRL-A + VALVE-2",
        pulse_frequency_switch_point_hz=125.0,
        mass_per_pulse=0.1,
        mass_unit="g",
        flow_point_g_per_s=100.0,
        specified_mass=1000.0,
        target_mass=995.0,
        standard_mass=1005.0,
        percent_error=0.5,
        configuration_snapshot={
            "mode": "regular",
            "labels": ["CTRL-A", "VALVE-2"],
            "nested": {"pulse": 125.0},
        },
        started_at=STARTED_AT,
        calculated_at=calculated_at,
        notes=f"notes for {trial_id}",
    )


def _result(
    result_id: str = "RESULT-FILL-1",
    *,
    run_id: str = "RUN-FILL-1",
    step_id: str = "STEP-FILL-1",
    created_at: datetime = CALCULATED_AT,
) -> AnalysisResultRecord:
    return AnalysisResultRecord(
        result_id=result_id,
        run_id=run_id,
        step_id=step_id,
        result_type="filling_advance",
        algorithm_name="filling_advance",
        algorithm_version="1",
        input_artifact_ids=(),
        configuration_snapshot={"selection": ["TRIAL-FILL-1"]},
        summary_metrics={"advance_mass": 5.0, "source_trial_ids": ["TRIAL-FILL-1"]},
        created_at=created_at,
    )


def _profile(
    profile_id: str = "PROFILE-FILL-1",
    *,
    device_id: str = "CFM-1",
    source_result_id: str = "RESULT-FILL-1",
    created_at: datetime = CALCULATED_AT,
) -> FillingAdvanceProfileRecord:
    return FillingAdvanceProfileRecord(
        profile_id=profile_id,
        device_id=device_id,
        source_result_id=source_result_id,
        control_valve_label="CTRL-A + VALVE-2",
        pulse_frequency_switch_point_hz=125.0,
        mass_per_pulse=0.1,
        mass_unit="g",
        flow_point_g_per_s=100.0,
        specified_mass=1000.0,
        advance_mass=5.0,
        corrected_target_mass=995.0,
        source_trial_ids=("TRIAL-FILL-1", "TRIAL-FILL-2", "TRIAL-FILL-3"),
        created_at=created_at,
        configuration_snapshot={"mode": "advance", "nested": {"enabled": True}},
        notes=f"notes for {profile_id}",
    )


def _seed_run(repository: StorageRepository, run: RunSession | None = None) -> RunSession:
    selected = run or _run()
    repository.save_run(selected)
    return selected


def _seed_result(
    repository: StorageRepository,
    *,
    result: AnalysisResultRecord | None = None,
) -> AnalysisResultRecord:
    selected = result or _result()
    repository.save_step(_step(step_id=selected.step_id or "STEP-FILL-1", run_id=selected.run_id))
    repository.save_analysis_result(selected)
    return selected


def test_schema_v4_preserves_v3_data_and_backfills_orphan_profiles(tmp_path) -> None:
    path = tmp_path / "coreflow.sqlite"
    _create_v3_database(path)
    database = Database(path)

    database.initialize()
    database.initialize()
    repository = StorageRepository(database)

    assert repository.get_device("CFM-1") is not None
    orphan = repository.get_device("ORPHAN")
    assert orphan is not None
    assert orphan.device_type == "modbus_rtu"
    assert [record.device_id for record in repository.list_devices()] == ["CFM-1", "ORPHAN"]
    assert repository.list_modbus_device_profiles()[0].profile_id == "profile:ORPHAN"
    assert repository.count_rows("filling_trial_records") == 0
    assert repository.count_rows("filling_advance_profiles") == 0
    with database.connect() as connection:
        versions = {
            int(row["version"])
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
    assert versions == {3, 4}


def test_fresh_schema_v4_is_idempotent_and_has_expected_indexes(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")

    database.initialize()
    database.initialize()

    with database.connect() as connection:
        versions = [
            int(row["version"])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        indexes = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert versions == [4]
    assert {
        "idx_filling_trials_device_calculated",
        "idx_filling_advance_profiles_device_created",
        "idx_filling_advance_profiles_source_result",
    } <= indexes


def test_database_rejects_future_schema_version(tmp_path) -> None:
    path = tmp_path / "coreflow.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (5, '2026-07-13T00:00:00+00:00')"
        )

    with pytest.raises(RuntimeError, match=r"schema version 5.*supported version 4"):
        Database(path).initialize()


def test_filling_records_are_frozen_and_slotted() -> None:
    trial = _trial()
    profile = _profile()

    assert not hasattr(trial, "__dict__")
    assert not hasattr(profile, "__dict__")
    with pytest.raises(FrozenInstanceError):
        trial.notes = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.notes = "changed"  # type: ignore[misc]


def test_create_and_list_devices_rejects_duplicate_ids_without_overwrite(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    first = _device("CFM-2", created_at=STARTED_AT + timedelta(minutes=2))
    second = _device("CFM-1")
    repository.create_device(first)
    repository.create_device(second)

    with pytest.raises(sqlite3.IntegrityError):
        repository.create_device(replace(second, model="replacement"))

    assert repository.get_device("CFM-1") == second
    assert repository.list_devices() == (second, first)


def test_filling_trial_json_timestamp_round_trip_and_queries(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    _seed_run(repository, _run("RUN-FILL-1"))
    _seed_run(
        repository,
        _run("RUN-FILL-2", started_at=STARTED_AT + timedelta(minutes=1)),
    )
    _seed_run(
        repository,
        _run(
            "RUN-FILL-3",
            device_id="CFM-2",
            started_at=STARTED_AT + timedelta(minutes=2),
        ),
    )
    trial_2 = _trial(
        "TRIAL-FILL-2",
        trial_index=2,
        calculated_at=CALCULATED_AT + timedelta(minutes=1),
    )
    trial_1 = _trial("TRIAL-FILL-1", trial_index=1)
    latest_a = _trial(
        "TRIAL-FILL-3A",
        run_id="RUN-FILL-2",
        trial_index=1,
        calculated_at=CALCULATED_AT + timedelta(minutes=2),
    )
    latest_b = _trial(
        "TRIAL-FILL-3B",
        run_id="RUN-FILL-2",
        trial_index=2,
        calculated_at=CALCULATED_AT + timedelta(minutes=2),
    )
    other_device = _trial(
        "TRIAL-FILL-4",
        run_id="RUN-FILL-3",
        device_id="CFM-2",
        trial_index=1,
        calculated_at=CALCULATED_AT + timedelta(minutes=3),
    )
    for trial in (trial_2, trial_1, latest_a, latest_b, other_device):
        repository.save_filling_trial(trial)

    assert repository.get_filling_trial(trial_1.trial_id) == trial_1
    assert repository.get_filling_trial("missing") is None
    assert repository.list_filling_trials(run_id="RUN-FILL-1") == (trial_1, trial_2)
    assert repository.list_filling_trials(
        run_id="RUN-FILL-1", device_id="CFM-1"
    ) == (trial_1, trial_2)
    assert repository.list_filling_trials(device_id="CFM-1") == (
        latest_b,
        latest_a,
        trial_2,
        trial_1,
    )
    assert repository.latest_filling_trial("CFM-1") == latest_b
    assert repository.latest_filling_trial("missing") is None


def test_filling_trial_constraints_reject_duplicates_foreign_keys_and_mismatch(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    _seed_run(repository)
    original = _trial()
    repository.save_filling_trial(original)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_trial(
            _trial("TRIAL-OTHER-ID", run_id=original.run_id, trial_index=1)
        )
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_trial(
            _trial("TRIAL-UNKNOWN-RUN", run_id="RUN-MISSING", trial_index=2)
        )
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_trial(
            _trial("TRIAL-UNKNOWN-DEVICE", device_id="CFM-MISSING", trial_index=2)
        )
    with pytest.raises(ValueError, match="does not belong to device CFM-2"):
        repository.save_filling_trial(
            _trial("TRIAL-MISMATCH", device_id="CFM-2", trial_index=2)
        )

    assert repository.list_filling_trials(run_id=original.run_id) == (original,)


def test_profiles_and_analysis_results_round_trip_without_overwrite(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    _seed_run(repository)
    result = _seed_result(repository)
    older = _profile("PROFILE-A", created_at=CALCULATED_AT)
    newer_a = _profile(
        "PROFILE-B",
        created_at=CALCULATED_AT + timedelta(minutes=1),
    )
    newer_b = _profile(
        "PROFILE-C",
        created_at=CALCULATED_AT + timedelta(minutes=1),
    )
    for profile in (older, newer_a, newer_b):
        repository.save_filling_advance_profile(profile)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_advance_profile(replace(older, notes="replacement"))
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_advance_profile(
            _profile("PROFILE-MISSING-RESULT", source_result_id="RESULT-MISSING")
        )
    with pytest.raises(ValueError, match="does not belong to device CFM-2"):
        repository.save_filling_advance_profile(
            _profile("PROFILE-MISMATCH", device_id="CFM-2")
        )

    assert repository.get_analysis_result(result.result_id) == result
    assert repository.get_analysis_result("missing") is None
    assert repository.list_filling_advance_profiles("CFM-1") == (
        newer_b,
        newer_a,
        older,
    )
    assert repository.list_filling_advance_profiles("CFM-2") == ()


def test_generic_analysis_save_protects_profile_provenance(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    _seed_run(repository)
    original = _seed_result(repository)
    updated = replace(
        original,
        algorithm_version="2",
        summary_metrics={"advance_mass": 4.5},
    )

    repository.save_analysis_result(updated)
    assert repository.get_analysis_result(updated.result_id) == updated

    profile = _profile(source_result_id=updated.result_id)
    repository.save_filling_advance_profile(profile)
    replacement = replace(
        updated,
        algorithm_version="3",
        summary_metrics={"advance_mass": 99.0},
    )
    with pytest.raises(ValueError, match="referenced by a filling advance profile"):
        repository.save_analysis_result(replacement)

    assert repository.get_analysis_result(updated.result_id) == updated
    assert repository.list_filling_advance_profiles("CFM-1") == (profile,)


def test_repository_normalizes_aware_timestamps_before_history_sorting(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    early_with_late_wall_clock = datetime(
        2026,
        7,
        13,
        23,
        30,
        tzinfo=timezone(timedelta(hours=14)),
    )
    later_in_utc = datetime(2026, 7, 13, 10, 0, tzinfo=UTC)
    early_run = _run("RUN-OFFSET-EARLY", started_at=early_with_late_wall_clock)
    late_run = _run("RUN-OFFSET-LATE", started_at=later_in_utc)
    repository.save_run(early_run)
    repository.save_run(late_run)
    early_trial = _trial(
        "TRIAL-OFFSET-EARLY",
        run_id=early_run.run_id,
        calculated_at=early_with_late_wall_clock,
    )
    late_trial = _trial(
        "TRIAL-OFFSET-LATE",
        run_id=late_run.run_id,
        calculated_at=later_in_utc,
    )
    repository.save_filling_trial(early_trial)
    repository.save_filling_trial(late_trial)
    result = _result(
        "RESULT-OFFSET",
        run_id=late_run.run_id,
        step_id="STEP-OFFSET",
        created_at=later_in_utc,
    )
    _seed_result(repository, result=result)
    early_profile = _profile(
        "PROFILE-OFFSET-EARLY",
        source_result_id=result.result_id,
        created_at=early_with_late_wall_clock,
    )
    late_profile = _profile(
        "PROFILE-OFFSET-LATE",
        source_result_id=result.result_id,
        created_at=later_in_utc,
    )
    repository.save_filling_advance_profile(early_profile)
    repository.save_filling_advance_profile(late_profile)

    assert [record.run_id for record in repository.list_runs()] == [
        late_run.run_id,
        early_run.run_id,
    ]
    assert repository.list_filling_trials(device_id="CFM-1") == (
        late_trial,
        early_trial,
    )
    assert repository.latest_filling_trial("CFM-1") == late_trial
    assert repository.list_filling_advance_profiles("CFM-1") == (
        late_profile,
        early_profile,
    )
    stored_early = repository.get_filling_trial(early_trial.trial_id)
    assert stored_early is not None
    assert stored_early.calculated_at is not None
    assert stored_early.calculated_at.utcoffset() == timedelta(0)


def test_repository_rejects_naive_timestamps_without_writing(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    naive = datetime(2026, 7, 13, 10, 0)

    with pytest.raises(ValueError, match="timezone-aware"):
        repository.create_device(_device(created_at=naive))

    assert repository.get_device("CFM-1") is None


def test_list_runs_keeps_positional_limit_and_adds_device_and_type_filters(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    runs = (
        _run("RUN-1", started_at=STARTED_AT),
        _run(
            "RUN-2",
            run_type=RunType.CALIBRATION,
            started_at=STARTED_AT + timedelta(minutes=1),
        ),
        _run(
            "RUN-3",
            device_id="CFM-2",
            started_at=STARTED_AT + timedelta(minutes=2),
        ),
    )
    for run in runs:
        repository.save_run(run)

    assert [record.run_id for record in repository.list_runs()] == ["RUN-3", "RUN-2", "RUN-1"]
    assert [record.run_id for record in repository.list_runs(2)] == ["RUN-3", "RUN-2"]
    assert [record.run_id for record in repository.list_runs(device_id="CFM-1")] == [
        "RUN-2",
        "RUN-1",
    ]
    assert [
        record.run_id
        for record in repository.list_runs(run_type=RunType.FILLING_TRIAL.value)
    ] == ["RUN-3", "RUN-1"]
    assert [
        record.run_id
        for record in repository.list_runs(
            1,
            device_id="CFM-1",
            run_type=RunType.FILLING_TRIAL.value,
        )
    ] == ["RUN-1"]


def test_filling_transition_methods_persist_complete_state(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    running = _seed_run(repository)
    completed = replace(running, status=RunStatus.COMPLETED, ended_at=CALCULATED_AT)
    step = _step()
    trial = _trial()

    repository.save_filling_trial_transition(run=completed, step=step, trial=trial)

    assert repository.get_run(completed.run_id) == completed
    assert repository.list_steps(completed.run_id) == (step,)
    assert repository.get_filling_trial(trial.trial_id) == trial

    analysis_step = _step(
        "STEP-ANALYSIS",
        name="Calculate filling advance",
    )
    result = _result(step_id=analysis_step.step_id)
    repository.save_filling_analysis(step=analysis_step, result=result)
    assert repository.list_steps(completed.run_id) == (analysis_step, step)
    assert repository.get_analysis_result(result.result_id) == result

    profile = _profile(source_result_id=result.result_id)
    new_run = _run(
        "RUN-FILL-2",
        status=RunStatus.RUNNING,
        started_at=CALCULATED_AT + timedelta(minutes=1),
    )
    repository.save_filling_advance_transition(
        profile=profile,
        completed_run=completed,
        new_run=new_run,
    )
    assert repository.get_run(completed.run_id) == completed
    assert repository.get_run(new_run.run_id) == new_run
    assert repository.list_filling_advance_profiles("CFM-1") == (profile,)


def test_filling_trial_transition_rolls_back_run_and_step_on_duplicate_trial(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    running = _seed_run(repository)
    existing = _trial()
    repository.save_filling_trial(existing)
    completed = replace(running, status=RunStatus.COMPLETED, ended_at=CALCULATED_AT)
    new_step = _step("STEP-ROLLBACK", name="Must roll back")
    duplicate = replace(existing, trial_index=2, standard_mass=1006.0)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_trial_transition(
            run=completed,
            step=new_step,
            trial=duplicate,
        )

    assert repository.get_run(running.run_id) == running
    assert repository.list_steps(running.run_id) == ()
    assert repository.get_filling_trial(existing.trial_id) == existing


def test_filling_trial_transition_rejects_rehoming_an_existing_run(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    original = _seed_run(repository)
    rehomed = replace(original, device_id="CFM-2")
    step = _step("STEP-REHOME")
    trial = _trial("TRIAL-REHOME", device_id="CFM-2")

    with pytest.raises(ValueError, match="already belongs to device CFM-1"):
        repository.save_filling_trial_transition(
            run=rehomed,
            step=step,
            trial=trial,
        )

    assert repository.get_run(original.run_id) == original
    assert repository.list_steps(original.run_id) == ()
    assert repository.get_filling_trial(trial.trial_id) is None


def test_filling_analysis_rolls_back_step_on_duplicate_result(tmp_path) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    _seed_run(repository)
    existing = _seed_result(repository)
    new_step = _step("STEP-ROLLBACK", name="Must roll back")
    duplicate = replace(existing, step_id=new_step.step_id, summary_metrics={"changed": True})

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_analysis(step=new_step, result=duplicate)

    assert [step.step_id for step in repository.list_steps(existing.run_id)] == [
        existing.step_id
    ]
    assert repository.get_analysis_result(existing.result_id) == existing


def test_filling_advance_transition_rolls_back_profile_and_completed_run(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    running = _seed_run(repository)
    result = _seed_result(repository)
    existing_new_run = _run(
        "RUN-DUPLICATE",
        status=RunStatus.PENDING,
        started_at=CALCULATED_AT + timedelta(minutes=1),
    )
    repository.save_run(existing_new_run)
    completed = replace(running, status=RunStatus.COMPLETED, ended_at=CALCULATED_AT)
    duplicate_new_run = replace(
        existing_new_run,
        status=RunStatus.RUNNING,
        notes="must not overwrite",
    )
    profile = _profile(source_result_id=result.result_id)

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_filling_advance_transition(
            profile=profile,
            completed_run=completed,
            new_run=duplicate_new_run,
        )

    assert repository.get_run(running.run_id) == running
    assert repository.get_run(existing_new_run.run_id) == existing_new_run
    assert repository.list_filling_advance_profiles("CFM-1") == ()


def test_filling_advance_transition_rejects_rehoming_the_completed_run(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device("CFM-1"))
    repository.create_device(_device("CFM-2"))
    original = _seed_run(repository)
    source_run = _run("RUN-CFM-2", device_id="CFM-2")
    repository.save_run(source_run)
    result = _result(
        "RESULT-CFM-2",
        run_id=source_run.run_id,
        step_id="STEP-CFM-2",
    )
    _seed_result(repository, result=result)
    profile = _profile(
        "PROFILE-REHOME",
        device_id="CFM-2",
        source_result_id=result.result_id,
    )
    rehomed = replace(
        original,
        device_id="CFM-2",
        status=RunStatus.COMPLETED,
        ended_at=CALCULATED_AT,
    )
    new_run = _run("RUN-NEW-CFM-2", device_id="CFM-2")

    with pytest.raises(ValueError, match="already belongs to device CFM-1"):
        repository.save_filling_advance_transition(
            profile=profile,
            completed_run=rehomed,
            new_run=new_run,
        )

    assert repository.get_run(original.run_id) == original
    assert repository.get_run(new_run.run_id) is None
    assert repository.list_filling_advance_profiles("CFM-2") == ()


def test_filling_advance_transition_requires_result_from_completed_run(
    tmp_path,
) -> None:
    _, repository = _repository(tmp_path)
    repository.create_device(_device())
    completed_source = _seed_run(repository)
    other_run = _run("RUN-OTHER")
    repository.save_run(other_run)
    other_result = _result(
        "RESULT-OTHER",
        run_id=other_run.run_id,
        step_id="STEP-OTHER",
    )
    _seed_result(repository, result=other_result)
    profile = _profile(
        "PROFILE-OTHER",
        source_result_id=other_result.result_id,
    )
    completed = replace(
        completed_source,
        status=RunStatus.COMPLETED,
        ended_at=CALCULATED_AT,
    )
    new_run = _run("RUN-NEW")

    with pytest.raises(ValueError, match="belongs to run RUN-OTHER, not RUN-FILL-1"):
        repository.save_filling_advance_transition(
            profile=profile,
            completed_run=completed,
            new_run=new_run,
        )

    assert repository.get_run(completed_source.run_id) == completed_source
    assert repository.get_run(new_run.run_id) is None
    assert repository.list_filling_advance_profiles("CFM-1") == ()

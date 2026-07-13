from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from math import inf, nan

import pytest

from coreflow.app import (
    FillingAnalysisRecord,
    FillingConfiguration,
    FillingGroupSnapshot,
    FillingHistoryEntry,
    FillingMode,
    FillingTrialService,
)
from coreflow.storage import Database, DeviceRecord, StorageRepository
from coreflow.workflows import RunStatus, RunType, WorkflowStepStatus


START = datetime(2026, 7, 13, 1, 0, tzinfo=UTC)


class TickingClock:
    def __init__(self, value: datetime = START) -> None:
        self.value = value
        self.returned: list[datetime] = []

    def __call__(self) -> datetime:
        value = self.value
        self.returned.append(value)
        self.value += timedelta(minutes=1)
        return value


class TokenFactory:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> str:
        self.count += 1
        return f"token-{self.count}"


@pytest.fixture
def repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    for device_id in ("CFM-1", "CFM-2"):
        repository.create_device(
            DeviceRecord(
                device_id=device_id,
                device_type="modbus_rtu",
                model=f"MODEL-{device_id}",
                created_at=START,
                updated_at=START,
            )
        )
    return repository


def _config(
    mode: FillingMode = FillingMode.REGULAR,
    *,
    label: str = "CTRL-A + VALVE-2",
    specified_mass: float = 1000.0,
    target_mass: float | None = None,
    flow_point: float = 100.0,
) -> FillingConfiguration:
    return FillingConfiguration(
        mode=mode,
        control_valve_label=label,
        pulse_frequency_switch_point_hz=125.0,
        mass_per_pulse=0.1,
        mass_unit="g",
        flow_point_g_per_s=flow_point,
        specified_mass=specified_mass,
        target_mass=(
            specified_mass
            if mode is FillingMode.ADVANCE
            else 995.0 if target_mass is None else target_mass
        ),
    )


def _service(
    repository: StorageRepository,
    *,
    device_id: str | None = "CFM-1",
    clock: TickingClock | None = None,
) -> tuple[FillingTrialService, TickingClock]:
    selected_clock = clock or TickingClock()
    service = FillingTrialService(
        repository,
        operator="pytest",
        software_version="9.8.7",
        clock=selected_clock,
        token_factory=TokenFactory(),
    )
    if device_id is not None:
        service.select_device(device_id)
    return service, selected_clock


def _calculate_trials(
    service: FillingTrialService,
    masses: tuple[float, ...],
    *,
    note_prefix: str = "trial",
) -> tuple[str, ...]:
    trial_ids: list[str] = []
    for index, mass in enumerate(masses, start=1):
        trial = service.calculate_current_trial(
            mass,
            notes=f"{note_prefix} {index}",
        )
        trial_ids.append(trial.trial_id)
        if index < len(masses):
            service.add_trial()
    return tuple(trial_ids)


def test_public_service_models_are_frozen_slotted_and_snapshot_complete() -> None:
    configuration = _config()
    assert configuration.snapshot() == {
        "mode": "regular",
        "control_valve_label": "CTRL-A + VALVE-2",
        "pulse_frequency_switch_point_hz": 125.0,
        "mass_per_pulse": 0.1,
        "mass_unit": "g",
        "flow_point_g_per_s": 100.0,
        "specified_mass": 1000.0,
        "target_mass": 995.0,
    }
    assert not hasattr(configuration, "__dict__")
    with pytest.raises(FrozenInstanceError):
        configuration.target_mass = 1.0  # type: ignore[misc]

    assert FillingGroupSnapshot.__dataclass_params__.frozen is True
    assert FillingAnalysisRecord.__dataclass_params__.frozen is True
    assert FillingHistoryEntry.__dataclass_params__.frozen is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pulse_frequency_switch_point_hz", 0.0),
        ("pulse_frequency_switch_point_hz", -1.0),
        ("pulse_frequency_switch_point_hz", inf),
        ("mass_per_pulse", nan),
        ("mass_per_pulse", True),
        ("flow_point_g_per_s", False),
        ("specified_mass", 0.0),
        ("target_mass", -1.0),
    ],
)
def test_configuration_rejects_nonpositive_nonfinite_and_boolean_numbers(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        replace(_config(), **{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [("control_valve_label", "  "), ("mass_unit", "")],
)
def test_configuration_rejects_blank_text(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        replace(_config(), **{field: value})


def test_advance_configuration_forces_target_to_equal_specified_mass() -> None:
    assert _config(FillingMode.ADVANCE).target_mass == 1000.0
    with pytest.raises(ValueError, match="equal specified mass"):
        FillingConfiguration(
            **{
                **_config(FillingMode.ADVANCE).snapshot(),
                "mode": FillingMode.ADVANCE,
                "target_mass": 999.0,
            }
        )
    assert _config(target_mass=997.0).target_mass == 997.0


def test_device_creation_is_trimmed_insert_only_and_selection_requires_existing(
    repository: StorageRepository,
) -> None:
    service, clock = _service(repository, device_id=None)

    created = service.create_device(device_id="  CFM-NEW  ", model="M-NEW")
    assert created.device_id == "CFM-NEW"
    assert created.device_type == "future_adapter"
    assert created.created_at == clock.returned[0]
    assert repository.get_device("CFM-NEW") == created

    with pytest.raises(ValueError, match="already exists"):
        service.create_device(device_id="CFM-NEW", model="replacement")
    assert repository.get_device("CFM-NEW") == created
    with pytest.raises(ValueError, match="non-empty"):
        service.create_device(device_id="   ")
    with pytest.raises(ValueError, match="Unknown device"):
        service.select_device("missing")

    assert [record.device_id for record in service.list_devices()] == [
        "CFM-1",
        "CFM-2",
        "CFM-NEW",
    ]


def test_select_device_restores_only_last_calculated_configuration_per_device(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    first = _config(label="FIRST", target_mass=991.0)
    service.start_group(first)
    service.calculate_current_trial(1001.0)
    service.end_group()

    assert service.select_device("CFM-2") is None
    draft = _config(label="UNSAVED", target_mass=992.0)
    service.start_group(draft)
    service.update_pending_configuration(replace(draft, target_mass=993.0))
    service.end_group()

    second = _config(label="SECOND", target_mass=994.0, flow_point=200.0)
    service.start_group(second)
    service.calculate_current_trial(1002.0)
    service.end_group()

    assert service.select_device("CFM-1") == first
    restored = service.select_device("CFM-2")
    assert restored == second
    assert "standard_mass" not in restored.snapshot()


def test_group_start_and_pending_configuration_update_are_persisted(
    repository: StorageRepository,
) -> None:
    service, clock = _service(repository)
    original = _config()
    snapshot = service.start_group(original)

    assert snapshot.status is RunStatus.PENDING
    assert snapshot.pending_trial_index == 1
    assert snapshot.configuration_locked is False
    stored = repository.get_run(snapshot.run_id)
    assert stored is not None
    assert stored.run_type is RunType.FILLING_TRIAL
    assert stored.workflow_name == "filling_trial_group"
    assert stored.workflow_version == "1"
    assert stored.operator == "pytest"
    assert stored.software_version == "9.8.7"
    assert stored.status is RunStatus.PENDING
    assert stored.started_at == clock.returned[0]
    assert stored.configuration_snapshot == original.snapshot()

    updated = replace(original, target_mass=990.0)
    snapshot = service.update_pending_configuration(updated)
    assert snapshot.configuration == updated
    assert repository.get_run(snapshot.run_id).configuration_snapshot == updated.snapshot()

    service.calculate_current_trial(1005.0)
    with pytest.raises(ValueError, match="locked"):
        service.update_pending_configuration(original)


def test_start_group_requires_selection_and_only_one_active_group(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository, device_id=None)
    with pytest.raises(ValueError, match="Select a device"):
        service.start_group(_config())
    service.select_device("CFM-1")
    service.start_group(_config())
    with pytest.raises(ValueError, match="active"):
        service.start_group(_config())
    with pytest.raises(ValueError, match="End the active group"):
        service.select_device("CFM-2")


def test_trial_save_failure_keeps_retryable_memory_and_pending_run(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository)
    before = service.start_group(_config())
    original_save = repository.save_filling_trial_transition

    def fail_save(**_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected trial failure")

    monkeypatch.setattr(repository, "save_filling_trial_transition", fail_save)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        service.calculate_current_trial(1005.0, notes="retry me")

    assert service.snapshot() == before
    assert repository.list_filling_trials(run_id=before.run_id) == ()
    assert repository.list_steps(before.run_id) == ()
    assert repository.get_run(before.run_id).status is RunStatus.PENDING

    monkeypatch.setattr(repository, "save_filling_trial_transition", original_save)
    trial = service.calculate_current_trial(1006.0, notes="retried")
    assert trial.standard_mass == 1006.0
    assert trial.notes == "retried"
    assert trial.started_at == repository.get_run(before.run_id).started_at
    assert repository.get_run(before.run_id).status is RunStatus.RUNNING
    assert repository.list_steps(before.run_id)[0].status is WorkflowStepStatus.COMPLETED


def test_trials_require_manual_add_and_never_cache_standard_mass(
    repository: StorageRepository,
) -> None:
    service, clock = _service(repository)
    service.start_group(_config())
    first = service.calculate_current_trial(1005.0)
    assert first.percent_error == pytest.approx(0.5)
    assert service.snapshot().has_pending_trial is False
    with pytest.raises(ValueError, match="pending trial"):
        service.calculate_current_trial(1005.0)

    add_time = clock.value
    snapshot = service.add_trial()
    assert snapshot.pending_trial_index == 2
    second = service.calculate_current_trial(997.0)
    assert second.started_at == add_time
    assert second.standard_mass == 997.0
    assert [trial.standard_mass for trial in service.snapshot().trials] == [
        1005.0,
        997.0,
    ]

    service.add_trial()
    with pytest.raises(ValueError, match="already pending"):
        service.add_trial()


def test_standard_mass_rejects_boolean_nonfinite_and_nonpositive_without_error_run(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    snapshot = service.start_group(_config())
    for value in (True, nan, inf, 0.0, -1.0):
        with pytest.raises(ValueError):
            service.calculate_current_trial(value)  # type: ignore[arg-type]
        assert service.snapshot() == snapshot
        assert repository.get_run(snapshot.run_id).status is RunStatus.PENDING


def test_repeatability_uses_three_consecutive_trials_and_each_save_is_unique(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    service.start_group(_config())
    trial_ids = _calculate_trials(service, (1001.0, 1002.0, 999.0, 1004.0))

    first = service.calculate_repeatability(trial_ids[1:])
    second = service.calculate_repeatability(trial_ids[1:])
    assert first.result_id != second.result_id
    assert first.result_type == "filling_repeatability"
    assert first.metrics["source_trial_ids"] == list(trial_ids[1:])
    assert first.metrics["source_trial_indexes"] == [2, 3, 4]
    assert first.metrics["mean_error_percent"] == pytest.approx(1.0 / 6.0)
    assert first.metrics["repeatability_stddev_percent"] == pytest.approx(
        0.2516611478423583
    )
    assert first.metrics["source_trial_started_at"]
    assert first.metrics["source_trial_ended_at"]
    assert len(repository.list_analysis_results(first.run_id)) == 2
    assert len(repository.list_steps(first.run_id)) == 6

    with pytest.raises(ValueError, match="consecutive"):
        service.calculate_repeatability((trial_ids[0], trial_ids[2], trial_ids[3]))
    with pytest.raises(ValueError, match="unique"):
        service.calculate_repeatability((trial_ids[0], trial_ids[0], trial_ids[1]))


def test_analysis_rejects_wrong_mode_external_and_mismatched_trials_without_writes(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    group = service.start_group(_config())
    current_ids = _calculate_trials(service, (1001.0, 1002.0, 1003.0))
    baseline_steps = len(repository.list_steps(group.run_id))

    other_run_service, _ = _service(repository)
    other_run_service.start_group(_config())
    external_run_id = _calculate_trials(
        other_run_service,
        (1004.0,),
        note_prefix="external-run",
    )[0]

    other_device_service, _ = _service(repository, device_id="CFM-2")
    other_device_service.start_group(_config())
    external_device_id = _calculate_trials(
        other_device_service,
        (1005.0,),
        note_prefix="external-device",
    )[0]

    mismatched = replace(
        repository.get_filling_trial(current_ids[2]),
        trial_id="filling-trial:mismatch",
        trial_index=4,
        configuration_snapshot={**_config().snapshot(), "target_mass": 900.0},
    )
    repository.save_filling_trial(mismatched)

    invalid_selections = (
        (current_ids[0], current_ids[1], external_run_id),
        (current_ids[0], current_ids[1], external_device_id),
        (current_ids[0], current_ids[1], mismatched.trial_id),
    )
    for selected in invalid_selections:
        with pytest.raises(ValueError):
            service.calculate_repeatability(selected)

    assert repository.list_analysis_results(group.run_id) == ()
    assert len(repository.list_steps(group.run_id)) == baseline_steps
    with pytest.raises(ValueError, match="Advance"):
        service.calculate_advance(current_ids)


def test_analysis_save_failure_has_no_half_write(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository)
    group = service.start_group(_config())
    trial_ids = _calculate_trials(service, (1001.0, 1002.0, 1003.0))
    steps_before = repository.list_steps(group.run_id)

    def fail_save(**_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected analysis failure")

    monkeypatch.setattr(repository, "save_filling_analysis", fail_save)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        service.calculate_repeatability(trial_ids)
    assert repository.list_analysis_results(group.run_id) == ()
    assert repository.list_steps(group.run_id) == steps_before


@pytest.mark.parametrize(
    ("masses", "advance", "corrected"),
    [
        ((1005.0, 1006.0, 1007.0, 1004.0), 16.0 / 3.0, 994.6666666666666),
        ((995.0, 994.0, 993.0, 996.0), -16.0 / 3.0, 1005.3333333333334),
    ],
)
def test_advance_allows_nonconsecutive_trials_and_positive_or_negative_results(
    repository: StorageRepository,
    masses: tuple[float, ...],
    advance: float,
    corrected: float,
) -> None:
    service, _ = _service(repository)
    service.start_group(_config(FillingMode.ADVANCE))
    trial_ids = _calculate_trials(service, masses)
    selected = (trial_ids[0], trial_ids[2], trial_ids[3])

    first = service.calculate_advance(selected)
    second = service.calculate_advance(selected)
    assert first.result_id != second.result_id
    assert first.result_type == "filling_advance"
    assert first.metrics["source_trial_ids"] == list(selected)
    assert first.metrics["selected_trial_count"] == 3
    assert first.metrics["advance_mass"] == pytest.approx(advance)
    assert first.metrics["corrected_target_mass"] == pytest.approx(corrected)
    assert repository.get_analysis_result(first.result_id).pass_fail_decision is None

    with pytest.raises(ValueError, match="at least 3"):
        service.calculate_advance(selected[:2])
    with pytest.raises(ValueError, match="Regular"):
        service.calculate_repeatability(selected)


def test_set_advance_atomically_creates_profile_and_corrected_regular_group(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    advance_configuration = _config(FillingMode.ADVANCE)
    old_group = service.start_group(advance_configuration)
    trial_ids = _calculate_trials(service, (1005.0, 1006.0, 1004.0))
    calculation = service.calculate_advance(trial_ids)

    profile = service.set_advance(calculation.result_id)
    snapshot = service.snapshot()
    assert profile.source_result_id == calculation.result_id
    assert profile.source_trial_ids == trial_ids
    assert profile.advance_mass == pytest.approx(5.0)
    assert profile.corrected_target_mass == pytest.approx(995.0)
    assert profile.configuration_snapshot == advance_configuration.snapshot()
    assert snapshot.run_id != old_group.run_id
    assert snapshot.status is RunStatus.PENDING
    assert snapshot.configuration.mode is FillingMode.REGULAR
    assert snapshot.configuration.target_mass == pytest.approx(995.0)
    assert snapshot.pending_trial_index == 1
    assert snapshot.trials == ()
    assert repository.get_run(old_group.run_id).status is RunStatus.COMPLETED
    assert repository.get_run(old_group.run_id).ended_at is not None
    assert repository.get_run(snapshot.run_id).status is RunStatus.PENDING

    trial = service.calculate_current_trial(1000.0)
    assert trial.started_at == repository.get_run(snapshot.run_id).started_at


def test_set_advance_failure_does_not_switch_memory_or_complete_old_run(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository)
    group = service.start_group(_config(FillingMode.ADVANCE))
    trial_ids = _calculate_trials(service, (1005.0, 1006.0, 1004.0))
    calculation = service.calculate_advance(trial_ids)
    before = service.snapshot()

    def fail_save(**_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected transition failure")

    monkeypatch.setattr(repository, "save_filling_advance_transition", fail_save)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        service.set_advance(calculation.result_id)

    assert service.snapshot() == before
    assert repository.get_run(group.run_id).status is RunStatus.RUNNING
    assert repository.list_filling_advance_profiles("CFM-1") == ()
    assert len(repository.list_runs(device_id="CFM-1")) == 1


def test_set_advance_rejects_unknown_repeatability_and_other_run_results(
    repository: StorageRepository,
) -> None:
    regular, _ = _service(repository)
    regular.start_group(_config())
    regular_ids = _calculate_trials(regular, (1001.0, 1002.0, 1003.0))
    repeatability = regular.calculate_repeatability(regular_ids)
    with pytest.raises(ValueError):
        regular.set_advance(repeatability.result_id)

    advance, _ = _service(repository)
    advance.start_group(_config(FillingMode.ADVANCE))
    advance_ids = _calculate_trials(advance, (1005.0, 1006.0, 1004.0))
    other_result = advance.calculate_advance(advance_ids)

    second_advance, _ = _service(repository)
    second_advance.start_group(_config(FillingMode.ADVANCE))
    _calculate_trials(second_advance, (1005.0, 1006.0, 1004.0))
    with pytest.raises(ValueError):
        second_advance.set_advance(other_result.result_id)
    with pytest.raises(ValueError, match="Unknown"):
        second_advance.set_advance("missing-result")


def test_multiple_advance_profiles_for_same_condition_are_preserved(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    source_result_ids: list[str] = []
    for masses in ((1005.0, 1006.0, 1004.0), (1007.0, 1005.0, 1006.0)):
        service.start_group(_config(FillingMode.ADVANCE))
        trial_ids = _calculate_trials(service, masses)
        calculation = service.calculate_advance(trial_ids)
        source_result_ids.append(calculation.result_id)
        service.set_advance(calculation.result_id)
        service.end_group()

    profiles = service.list_advance_profiles()
    assert len(profiles) == 2
    assert len({profile.profile_id for profile in profiles}) == 2
    assert {profile.source_result_id for profile in profiles} == set(source_result_ids)


def test_end_group_cancels_empty_and_completes_nonempty_while_retaining_selection(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    empty = service.start_group(_config())
    service.end_group()
    assert repository.get_run(empty.run_id).status is RunStatus.CANCELED
    assert repository.get_run(empty.run_id).ended_at is not None
    assert service.snapshot().device_id == "CFM-1"
    assert service.snapshot().run_id is None

    nonempty = service.start_group(_config())
    service.calculate_current_trial(1005.0)
    service.end_group()
    assert repository.get_run(nonempty.run_id).status is RunStatus.COMPLETED
    assert repository.get_run(nonempty.run_id).ended_at is not None
    assert service.snapshot().device_id == "CFM-1"


def test_history_merges_four_record_types_with_sources_configuration_and_notes(
    repository: StorageRepository,
) -> None:
    service, _ = _service(repository)
    regular_configuration = _config(label="REGULAR")
    service.start_group(regular_configuration)
    regular_ids = _calculate_trials(
        service,
        (1001.0, 1002.0, 1003.0),
        note_prefix="regular note",
    )
    repeatability = service.calculate_repeatability(regular_ids)
    service.end_group()

    advance_configuration = _config(FillingMode.ADVANCE, label="ADVANCE")
    service.start_group(advance_configuration)
    advance_ids = _calculate_trials(
        service,
        (1005.0, 1006.0, 1004.0),
        note_prefix="advance note",
    )
    advance = service.calculate_advance(advance_ids)
    profile = service.set_advance(advance.result_id)

    entries = service.list_history()
    record_types = {entry.record_type for entry in entries}
    assert record_types == {
        "trial",
        "repeatability",
        "advance_calculation",
        "advance_profile",
    }
    assert all(entry.device_id == "CFM-1" for entry in entries)
    assert [entry.created_at for entry in entries] == sorted(
        (entry.created_at for entry in entries),
        reverse=True,
    )

    by_id = {entry.record_id: entry for entry in entries}
    assert by_id[regular_ids[0]].details["configuration_snapshot"] == (
        regular_configuration.snapshot()
    )
    assert by_id[regular_ids[0]].details["notes"] == "regular note 1"
    assert by_id[repeatability.result_id].details["source_trial_ids"] == list(
        regular_ids
    )
    assert by_id[advance.result_id].details["source_trial_ids"] == list(advance_ids)
    assert by_id[profile.profile_id].details["source_trial_ids"] == list(advance_ids)
    assert by_id[profile.profile_id].details["configuration_snapshot"] == (
        advance_configuration.snapshot()
    )

    service.end_group()
    service.select_device("CFM-2")
    assert service.list_history() == ()
    assert service.list_advance_profiles() == ()


def test_selected_device_queries_require_selection_and_propagate_query_failures(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository, device_id=None)
    with pytest.raises(ValueError, match="Select a device"):
        service.list_history()
    with pytest.raises(ValueError, match="Select a device"):
        service.list_advance_profiles()

    service.select_device("CFM-1")

    def fail_query(**_kwargs: object) -> tuple[()]:
        raise sqlite3.OperationalError("history query failure")

    monkeypatch.setattr(repository, "list_filling_trials", fail_query)
    with pytest.raises(sqlite3.OperationalError, match="history query failure"):
        service.list_history()


def test_naive_clock_is_rejected_before_any_persistence(
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(
        repository,
        clock=lambda: datetime(2026, 7, 13, 1, 0),
        token_factory=TokenFactory(),
    )
    with pytest.raises(ValueError, match="aware UTC"):
        service.create_device(device_id="NAIVE")
    assert repository.get_device("NAIVE") is None

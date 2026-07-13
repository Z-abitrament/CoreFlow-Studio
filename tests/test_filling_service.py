from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from math import inf, nan
from threading import Event, Lock, Thread

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
from coreflow.workflows import (
    RunStatus,
    RunType,
    WorkflowStepStatus,
    WorkflowStepType,
)


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


def _advance_calculation(
    service: FillingTrialService,
) -> tuple[tuple[str, ...], FillingAnalysisRecord]:
    service.start_group(_config(FillingMode.ADVANCE))
    trial_ids = _calculate_trials(service, (1005.0, 1006.0, 1004.0))
    return trial_ids, service.calculate_advance(trial_ids)


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


@pytest.mark.parametrize(
    "tamper",
    [
        "result_type",
        "algorithm_name",
        "algorithm_version",
        "input_artifact_ids",
        "pass_fail_decision",
        "configuration_snapshot",
        "source_trial_indexes",
        "source_trial_time_range",
        "created_at",
    ],
)
def test_set_advance_rejects_tampered_result_provenance(
    repository: StorageRepository,
    tamper: str,
) -> None:
    service, _ = _service(repository)
    _, calculation = _advance_calculation(service)
    stored = repository.get_analysis_result(calculation.result_id)
    assert stored is not None

    changes: dict[str, object]
    if tamper == "result_type":
        changes = {"result_type": "filling_repeatability"}
    elif tamper == "algorithm_name":
        changes = {"algorithm_name": "tampered_algorithm"}
    elif tamper == "algorithm_version":
        changes = {"algorithm_version": "2"}
    elif tamper == "input_artifact_ids":
        changes = {"input_artifact_ids": ("ARTIFACT-TAMPERED",)}
    elif tamper == "pass_fail_decision":
        changes = {"pass_fail_decision": "passed"}
    elif tamper == "configuration_snapshot":
        changes = {
            "configuration_snapshot": {
                **stored.configuration_snapshot,
                "source_trial_indexes": [99, 100, 101],
            }
        }
    elif tamper == "source_trial_indexes":
        changes = {
            "summary_metrics": {
                **stored.summary_metrics,
                "source_trial_indexes": [99, 100, 101],
            }
        }
    elif tamper == "source_trial_time_range":
        changes = {
            "summary_metrics": {
                **stored.summary_metrics,
                "source_trial_ended_at": START.isoformat(),
            }
        }
    else:
        changes = {"created_at": START}
    repository.save_analysis_result(replace(stored, **changes))
    before = service.snapshot()

    with pytest.raises(ValueError, match="provenance"):
        service.set_advance(calculation.result_id)

    assert service.snapshot() == before
    assert repository.list_filling_advance_profiles("CFM-1") == ()
    assert len(repository.list_runs(device_id="CFM-1")) == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "name",
        "step_type",
        "status",
        "input_configuration",
        "output_summary",
        "started_at",
        "ended_at",
        "error_message",
    ],
)
def test_set_advance_rejects_tampered_step_provenance(
    repository: StorageRepository,
    tamper: str,
) -> None:
    service, _ = _service(repository)
    _, calculation = _advance_calculation(service)
    stored_result = repository.get_analysis_result(calculation.result_id)
    assert stored_result is not None
    matching = [
        step
        for step in repository.list_steps(calculation.run_id)
        if step.step_id == stored_result.step_id
    ]
    assert len(matching) == 1
    step = matching[0]

    changes: dict[str, object]
    if tamper == "name":
        changes = {"name": "Tampered advance step"}
    elif tamper == "step_type":
        changes = {"step_type": WorkflowStepType.CAPTURE}
    elif tamper == "status":
        changes = {"status": WorkflowStepStatus.FAILED}
    elif tamper == "input_configuration":
        changes = {
            "input_configuration": {
                **step.input_configuration,
                "source_trial_indexes": [99, 100, 101],
            }
        }
    elif tamper == "output_summary":
        changes = {
            "output_summary": {
                **step.output_summary,
                "advance_mass": 999.0,
            }
        }
    elif tamper == "started_at":
        changes = {"started_at": step.started_at - timedelta(seconds=1)}
    elif tamper == "ended_at":
        changes = {"ended_at": step.ended_at + timedelta(seconds=1)}
    else:
        changes = {"error_message": "tampered error"}
    repository.save_step(replace(step, **changes))
    before = service.snapshot()

    with pytest.raises(ValueError, match="provenance"):
        service.set_advance(calculation.result_id)

    assert service.snapshot() == before
    assert repository.list_filling_advance_profiles("CFM-1") == ()
    assert len(repository.list_runs(device_id="CFM-1")) == 1


def test_calculate_and_end_group_are_serialized_at_trial_persistence(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository)
    group = service.start_group(_config())
    save_entered = Event()
    allow_save = Event()
    end_attempted = Event()
    end_finished = Event()
    original_save = repository.save_filling_trial_transition
    results: list[object] = []
    errors: list[BaseException] = []

    def blocking_save(**kwargs: object) -> None:
        save_entered.set()
        if not allow_save.wait(5.0):
            raise RuntimeError("Timed out waiting to release trial save")
        original_save(**kwargs)

    def calculate() -> None:
        try:
            results.append(service.calculate_current_trial(1005.0))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def end() -> None:
        end_attempted.set()
        try:
            service.end_group()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            end_finished.set()

    monkeypatch.setattr(repository, "save_filling_trial_transition", blocking_save)
    calculate_thread = Thread(target=calculate)
    end_thread = Thread(target=end)
    calculate_thread.start()
    assert save_entered.wait(5.0)
    end_thread.start()
    assert end_attempted.wait(5.0)
    end_was_blocked = not end_finished.wait(0.1)
    allow_save.set()
    calculate_thread.join(5.0)
    end_thread.join(5.0)

    assert end_was_blocked
    assert not calculate_thread.is_alive()
    assert not end_thread.is_alive()
    assert errors == []
    assert len(results) == 1
    assert repository.get_run(group.run_id).status is RunStatus.COMPLETED
    assert len(repository.list_filling_trials(run_id=group.run_id)) == 1
    assert service.snapshot().run_id is None


def test_concurrent_set_advance_creates_only_one_profile_and_new_group(
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _ = _service(repository)
    _, calculation = _advance_calculation(service)
    first_save_entered = Event()
    second_call_attempted = Event()
    second_save_entered = Event()
    allow_save = Event()
    call_guard = Lock()
    save_calls = 0
    original_save = repository.save_filling_advance_transition
    results: list[object] = []
    errors: list[BaseException] = []

    def blocking_save(**kwargs: object) -> None:
        nonlocal save_calls
        with call_guard:
            save_calls += 1
            call_number = save_calls
        if call_number == 1:
            first_save_entered.set()
        else:
            second_save_entered.set()
        if not allow_save.wait(5.0):
            raise RuntimeError("Timed out waiting to release advance save")
        original_save(**kwargs)

    def set_advance(*, second: bool = False) -> None:
        if second:
            second_call_attempted.set()
        try:
            results.append(service.set_advance(calculation.result_id))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(repository, "save_filling_advance_transition", blocking_save)
    first = Thread(target=set_advance)
    second = Thread(target=set_advance, kwargs={"second": True})
    first.start()
    assert first_save_entered.wait(5.0)
    second.start()
    assert second_call_attempted.wait(5.0)
    second_was_blocked = not second_save_entered.wait(0.1)
    allow_save.set()
    first.join(5.0)
    second.join(5.0)

    assert second_was_blocked
    assert not first.is_alive()
    assert not second.is_alive()
    assert save_calls == 1
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert len(repository.list_filling_advance_profiles("CFM-1")) == 1
    assert len(repository.list_runs(device_id="CFM-1")) == 2
    assert service.snapshot().configuration.mode is FillingMode.REGULAR


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


@pytest.mark.parametrize(
    "operation",
    [
        "calculate_trial",
        "add_trial",
        "calculate_analysis",
        "set_advance",
        "end_pending_group",
        "end_running_group",
    ],
)
def test_clock_rejects_event_time_regressions_without_state_changes(
    repository: StorageRepository,
    operation: str,
) -> None:
    clock = TickingClock()
    service, _ = _service(repository, clock=clock)
    calculation: FillingAnalysisRecord | None = None

    if operation == "calculate_trial":
        service.start_group(_config())
    elif operation == "add_trial":
        service.start_group(_config())
        service.calculate_current_trial(1005.0)
    elif operation == "calculate_analysis":
        service.start_group(_config(FillingMode.ADVANCE))
        trial_ids = _calculate_trials(service, (1005.0, 1006.0, 1004.0))
    elif operation == "set_advance":
        trial_ids, calculation = _advance_calculation(service)
    elif operation == "end_pending_group":
        service.start_group(_config())
    else:
        service.start_group(_config())
        service.calculate_current_trial(1005.0)

    before = service.snapshot()
    runs_before = repository.list_runs(device_id="CFM-1")
    trials_before = repository.list_filling_trials(device_id="CFM-1")
    steps_before = tuple(
        step
        for run in runs_before
        for step in repository.list_steps(run.run_id)
    )
    results_before = tuple(
        result
        for run in runs_before
        for result in repository.list_analysis_results(run.run_id)
    )
    profiles_before = repository.list_filling_advance_profiles("CFM-1")
    clock.value = START - timedelta(days=1)

    with pytest.raises(ValueError, match="earlier"):
        if operation == "calculate_trial":
            service.calculate_current_trial(1005.0)
        elif operation == "add_trial":
            service.add_trial()
        elif operation == "calculate_analysis":
            service.calculate_advance(trial_ids)
        elif operation == "set_advance":
            assert calculation is not None
            service.set_advance(calculation.result_id)
        else:
            service.end_group()

    runs_after = repository.list_runs(device_id="CFM-1")
    assert service.snapshot() == before
    assert runs_after == runs_before
    assert repository.list_filling_trials(device_id="CFM-1") == trials_before
    assert tuple(
        step
        for run in runs_after
        for step in repository.list_steps(run.run_id)
    ) == steps_before
    assert tuple(
        result
        for run in runs_after
        for result in repository.list_analysis_results(run.run_id)
    ) == results_before
    assert repository.list_filling_advance_profiles("CFM-1") == profiles_before


@pytest.mark.parametrize(
    "operation",
    ["calculate_trial", "add_trial", "calculate_analysis", "set_advance"],
)
def test_clock_uses_latest_active_event_as_lower_bound(
    repository: StorageRepository,
    operation: str,
) -> None:
    clock = TickingClock()
    service, _ = _service(repository, clock=clock)

    if operation == "set_advance":
        service.start_group(_config(FillingMode.ADVANCE))
        trial_ids = _calculate_trials(service, (1005.0, 1006.0, 1004.0))
        first = service.calculate_advance(trial_ids)
        latest = service.calculate_advance(trial_ids)
        clock.value = first.created_at + timedelta(seconds=30)
        action = lambda: service.set_advance(first.result_id)
    else:
        service.start_group(_config())
        trial_ids = _calculate_trials(service, (1001.0, 1002.0, 1003.0))
        if operation == "calculate_trial":
            service.add_trial()
        latest = service.calculate_repeatability(trial_ids)
        latest_trial = repository.get_filling_trial(trial_ids[-1])
        assert latest_trial is not None and latest_trial.calculated_at is not None
        if operation == "calculate_trial":
            clock.value = latest.created_at - timedelta(seconds=30)
            action = lambda: service.calculate_current_trial(1004.0)
        elif operation == "add_trial":
            clock.value = latest_trial.calculated_at + timedelta(seconds=30)
            action = service.add_trial
        else:
            clock.value = latest_trial.calculated_at + timedelta(seconds=30)
            action = lambda: service.calculate_repeatability(trial_ids)

    before = service.snapshot()
    with pytest.raises(ValueError, match="earlier"):
        action()
    assert service.snapshot() == before
    assert max(
        result.created_at
        for result in repository.list_analysis_results(before.run_id)
        if result.created_at is not None
    ) == latest.created_at

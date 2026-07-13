"""Headless orchestration for manually recorded filling trials."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import wraps
from math import isfinite
from numbers import Real
from threading import RLock
from typing import Any, Callable, Sequence
from uuid import uuid4

from coreflow import __version__
from coreflow.analysis.filling import (
    FillingAdvanceResult,
    FillingRepeatabilityResult,
    FillingTrialValue,
    calculate_advance,
    calculate_repeatability,
    calculate_trial_error,
)
from coreflow.storage.models import (
    AnalysisResultRecord,
    DeviceRecord,
    FillingAdvanceProfileRecord,
    FillingTrialRecord,
)
from coreflow.storage.repositories import StorageRepository
from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


Clock = Callable[[], datetime]
TokenFactory = Callable[[], str]


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


def new_token() -> str:
    """Return an opaque token suitable for a persisted identifier."""

    return uuid4().hex


class FillingMode(StrEnum):
    """Supported manual filling trial modes."""

    REGULAR = "regular"
    ADVANCE = "advance"


@dataclass(frozen=True, slots=True)
class FillingConfiguration:
    """Locked configuration shared by every trial in one group."""

    mode: FillingMode
    control_valve_label: str
    pulse_frequency_switch_point_hz: float
    mass_per_pulse: float
    mass_unit: str
    flow_point_g_per_s: float
    specified_mass: float
    target_mass: float

    def __post_init__(self) -> None:
        if not isinstance(self.mode, FillingMode):
            raise ValueError("Filling mode must be a FillingMode value.")
        object.__setattr__(
            self,
            "control_valve_label",
            _nonempty_text("Control/valve label", self.control_valve_label),
        )
        object.__setattr__(
            self,
            "mass_unit",
            _nonempty_text("Mass unit", self.mass_unit),
        )
        for field_name, label in (
            ("pulse_frequency_switch_point_hz", "Pulse frequency switch point"),
            ("mass_per_pulse", "Mass per pulse"),
            ("flow_point_g_per_s", "Flow point"),
            ("specified_mass", "Specified mass"),
            ("target_mass", "Target mass"),
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_number(label, getattr(self, field_name)),
            )
        if (
            self.mode is FillingMode.ADVANCE
            and self.target_mass != self.specified_mass
        ):
            raise ValueError(
                "Advance target mass must equal specified mass."
            )

    def snapshot(self) -> dict[str, object]:
        """Return the complete persistable group configuration."""

        return {
            "mode": self.mode.value,
            "control_valve_label": self.control_valve_label,
            "pulse_frequency_switch_point_hz": (
                self.pulse_frequency_switch_point_hz
            ),
            "mass_per_pulse": self.mass_per_pulse,
            "mass_unit": self.mass_unit,
            "flow_point_g_per_s": self.flow_point_g_per_s,
            "specified_mass": self.specified_mass,
            "target_mass": self.target_mass,
        }


@dataclass(frozen=True, slots=True)
class FillingGroupSnapshot:
    """Read-only view of the selected device and active group."""

    device_id: str | None
    run_id: str | None
    status: RunStatus | None
    configuration: FillingConfiguration | None
    configuration_locked: bool
    has_pending_trial: bool
    pending_trial_index: int | None
    trials: tuple[FillingTrialRecord, ...]


@dataclass(frozen=True, slots=True)
class FillingAnalysisRecord:
    """Application-facing view of one stored filling analysis."""

    result_id: str
    run_id: str
    result_type: str
    created_at: datetime
    metrics: dict[str, object]


@dataclass(frozen=True, slots=True)
class FillingHistoryEntry:
    """One device-filtered filling history row with complete details."""

    record_id: str
    record_type: str
    run_id: str
    device_id: str
    created_at: datetime | None
    summary: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class _PendingTrial:
    trial_index: int
    started_at: datetime


def _synchronized(method: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize every public operation that observes service state."""

    @wraps(method)
    def wrapper(self: FillingTrialService, *args: Any, **kwargs: Any) -> Any:
        with self._state_lock:
            return method(self, *args, **kwargs)

    return wrapper


class FillingTrialService:
    """Coordinate one in-memory filling group and its atomic persistence."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        operator: str = "operator",
        software_version: str = __version__,
        clock: Clock = utc_now,
        token_factory: TokenFactory = new_token,
    ) -> None:
        self._state_lock = RLock()
        self._repository = repository
        self._operator = _nonempty_text("Operator", operator)
        self._software_version = _nonempty_text(
            "Software version", software_version
        )
        self._clock = clock
        self._token_factory = token_factory
        self._issued_ids: set[str] = set()
        self._selected_device_id: str | None = None
        self._selected_configuration: FillingConfiguration | None = None
        self._run: RunSession | None = None
        self._configuration: FillingConfiguration | None = None
        self._pending_trial: _PendingTrial | None = None
        self._trials: tuple[FillingTrialRecord, ...] = ()

    @_synchronized
    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self._repository.list_devices()

    @_synchronized
    def create_device(
        self,
        *,
        device_id: str,
        model: str | None = None,
    ) -> DeviceRecord:
        normalized_id = _nonempty_text("Device ID", device_id)
        if self._repository.get_device(normalized_id) is not None:
            raise ValueError(f"Device ID already exists: {normalized_id}")
        created_at = self._now()
        record = DeviceRecord(
            device_id=normalized_id,
            device_type="future_adapter",
            model=model.strip() if isinstance(model, str) else model,
            created_at=created_at,
            updated_at=created_at,
        )
        try:
            self._repository.create_device(record)
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"Device ID already exists: {normalized_id}"
            ) from exc
        return record

    @_synchronized
    def select_device(self, device_id: str) -> FillingConfiguration | None:
        normalized_id = _nonempty_text("Device ID", device_id)
        if (
            self._run is not None
            and normalized_id != self._selected_device_id
        ):
            raise ValueError("End the active group before changing devices.")
        if self._repository.get_device(normalized_id) is None:
            raise ValueError(f"Unknown device: {normalized_id}")

        latest = self._repository.latest_filling_trial(normalized_id)
        restored = (
            _configuration_from_trial(latest) if latest is not None else None
        )
        self._selected_device_id = normalized_id
        self._selected_configuration = restored
        return restored

    @_synchronized
    def start_group(
        self,
        configuration: FillingConfiguration,
    ) -> FillingGroupSnapshot:
        self._require_configuration(configuration)
        device_id = self._require_selected_device()
        if self._run is not None:
            raise ValueError("A filling group is already active.")
        if self._repository.get_device(device_id) is None:
            raise ValueError(f"Unknown device: {device_id}")

        started_at = self._now()
        run = RunSession(
            run_id=self._new_id("filling-run"),
            run_type=RunType.FILLING_TRIAL,
            workflow_name="filling_trial_group",
            workflow_version="1",
            device_id=device_id,
            operator=self._operator,
            status=RunStatus.PENDING,
            started_at=started_at,
            configuration_snapshot=configuration.snapshot(),
            software_version=self._software_version,
        )
        self._repository.save_run(run)

        self._run = run
        self._configuration = configuration
        self._pending_trial = _PendingTrial(1, started_at)
        self._trials = ()
        return self.snapshot()

    @_synchronized
    def update_pending_configuration(
        self,
        configuration: FillingConfiguration,
    ) -> FillingGroupSnapshot:
        self._require_configuration(configuration)
        run, _ = self._require_active_group()
        if self._trials or self._pending_trial is None:
            raise ValueError(
                "Filling configuration is locked after the first trial."
            )
        updated_run = replace(
            run,
            configuration_snapshot=configuration.snapshot(),
        )
        self._repository.save_run(updated_run)
        self._run = updated_run
        self._configuration = configuration
        return self.snapshot()

    @_synchronized
    def calculate_current_trial(
        self,
        standard_mass: float,
        *,
        notes: str | None = None,
    ) -> FillingTrialRecord:
        run, configuration = self._require_active_group()
        pending = self._pending_trial
        if pending is None:
            raise ValueError("There is no pending trial to calculate.")
        normalized_mass = _positive_number("Standard mass", standard_mass)
        calculated_at = self._event_time(
            "Trial calculation",
            pending.started_at,
        )
        percent_error = calculate_trial_error(
            configuration.specified_mass,
            normalized_mass,
        )
        configuration_snapshot = configuration.snapshot()
        trial = FillingTrialRecord(
            trial_id=self._new_id("filling-trial"),
            run_id=run.run_id,
            device_id=run.device_id,
            trial_index=pending.trial_index,
            trial_status="calculated",
            mode=configuration.mode.value,
            control_valve_label=configuration.control_valve_label,
            pulse_frequency_switch_point_hz=(
                configuration.pulse_frequency_switch_point_hz
            ),
            mass_per_pulse=configuration.mass_per_pulse,
            mass_unit=configuration.mass_unit,
            flow_point_g_per_s=configuration.flow_point_g_per_s,
            specified_mass=configuration.specified_mass,
            target_mass=configuration.target_mass,
            standard_mass=normalized_mass,
            percent_error=percent_error,
            configuration_snapshot=configuration_snapshot,
            started_at=pending.started_at,
            calculated_at=calculated_at,
            notes=notes,
        )
        step = WorkflowStep(
            step_id=self._new_id("filling-step"),
            run_id=run.run_id,
            name=f"Calculate filling trial {pending.trial_index}",
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.COMPLETED,
            started_at=pending.started_at,
            ended_at=calculated_at,
            input_configuration={
                **configuration_snapshot,
                "trial_index": pending.trial_index,
                "standard_mass": normalized_mass,
                "notes": notes,
            },
            output_summary={
                "trial_id": trial.trial_id,
                "trial_index": pending.trial_index,
                "percent_error": percent_error,
            },
        )
        running = replace(run, status=RunStatus.RUNNING)
        self._repository.save_filling_trial_transition(
            run=running,
            step=step,
            trial=trial,
        )

        self._run = running
        self._trials = (*self._trials, trial)
        self._pending_trial = None
        self._selected_configuration = configuration
        return trial

    @_synchronized
    def add_trial(self) -> FillingGroupSnapshot:
        self._require_active_group()
        if not self._trials:
            raise ValueError("Calculate the first trial before adding another.")
        if self._pending_trial is not None:
            raise ValueError("A trial is already pending.")
        last_calculated_at = max(
            _aware_utc("Trial calculated_at", trial.calculated_at)
            for trial in self._trials
        )
        started_at = self._event_time("Add Trial", last_calculated_at)
        self._pending_trial = _PendingTrial(
            max(trial.trial_index for trial in self._trials) + 1,
            started_at,
        )
        return self.snapshot()

    @_synchronized
    def calculate_repeatability(
        self,
        trial_ids: Sequence[str],
    ) -> FillingAnalysisRecord:
        _, configuration = self._require_active_group()
        if configuration.mode is not FillingMode.REGULAR:
            raise ValueError("Repeatability requires a Regular filling group.")
        trials = tuple(
            sorted(
                self._load_selected_trials(trial_ids),
                key=lambda trial: trial.trial_index,
            )
        )
        calculation = calculate_repeatability(
            tuple(_trial_value(trial) for trial in trials)
        )
        metrics = self._repeatability_metrics(calculation, trials)
        return self._persist_analysis(
            result_type="filling_repeatability",
            algorithm_name="filling_repeatability",
            source_trials=trials,
            metrics=metrics,
        )

    @_synchronized
    def calculate_advance(
        self,
        trial_ids: Sequence[str],
    ) -> FillingAnalysisRecord:
        _, configuration = self._require_active_group()
        if configuration.mode is not FillingMode.ADVANCE:
            raise ValueError("Advance calculation requires an Advance filling group.")
        trials = self._load_selected_trials(trial_ids)
        calculation = calculate_advance(
            tuple(_trial_value(trial) for trial in trials)
        )
        metrics = self._advance_metrics(calculation, trials)
        return self._persist_analysis(
            result_type="filling_advance",
            algorithm_name="filling_advance",
            source_trials=trials,
            metrics=metrics,
        )

    @_synchronized
    def set_advance(self, result_id: str) -> FillingAdvanceProfileRecord:
        run, configuration = self._require_active_group()
        if configuration.mode is not FillingMode.ADVANCE:
            raise ValueError("Set Advance requires an Advance filling group.")
        result = self._repository.get_analysis_result(
            _nonempty_text("Result ID", result_id)
        )
        if result is None:
            raise ValueError(f"Unknown filling analysis result: {result_id}")
        try:
            if result.run_id != run.run_id:
                raise ValueError("result belongs to another run")
            source_ids = _string_list_metric(
                result.summary_metrics,
                "source_trial_ids",
            )
            source_trials = self._load_selected_trials(source_ids)
            recalculated = calculate_advance(
                tuple(_trial_value(trial) for trial in source_trials)
            )
            source_started_at, source_ended_at = _source_time_range(
                source_trials
            )
            canonical_configuration = _analysis_configuration(
                configuration,
                source_trials,
                source_started_at=source_started_at,
                source_ended_at=source_ended_at,
            )
            canonical_metrics = self._advance_metrics(
                recalculated,
                source_trials,
            )
            matching_steps = tuple(
                step
                for step in self._repository.list_steps(run.run_id)
                if step.step_id == result.step_id
            )
            result_created_at = self._validate_advance_result(
                result,
                run=run,
                canonical_configuration=canonical_configuration,
                canonical_metrics=canonical_metrics,
                source_ended_at=source_ended_at,
                matching_steps=matching_steps,
            )
        except ValueError as exc:
            raise ValueError(
                f"Advance provenance validation failed: {exc}"
            ) from exc

        transition_at = self._event_time(
            "Set Advance",
            max(
                _aware_utc("Run started_at", run.started_at),
                source_ended_at,
                result_created_at,
            ),
        )
        notes = _combined_notes(source_trials)
        profile = FillingAdvanceProfileRecord(
            profile_id=self._new_id("filling-profile"),
            device_id=run.device_id,
            source_result_id=result.result_id,
            control_valve_label=configuration.control_valve_label,
            pulse_frequency_switch_point_hz=(
                configuration.pulse_frequency_switch_point_hz
            ),
            mass_per_pulse=configuration.mass_per_pulse,
            mass_unit=configuration.mass_unit,
            flow_point_g_per_s=configuration.flow_point_g_per_s,
            specified_mass=configuration.specified_mass,
            advance_mass=recalculated.advance_mass,
            corrected_target_mass=recalculated.corrected_target_mass,
            source_trial_ids=recalculated.trial_ids,
            created_at=transition_at,
            configuration_snapshot=configuration.snapshot(),
            notes=notes,
        )
        completed_run = replace(
            run,
            status=RunStatus.COMPLETED,
            ended_at=transition_at,
        )
        regular_configuration = replace(
            configuration,
            mode=FillingMode.REGULAR,
            target_mass=recalculated.corrected_target_mass,
        )
        new_run = RunSession(
            run_id=self._new_id("filling-run"),
            run_type=RunType.FILLING_TRIAL,
            workflow_name="filling_trial_group",
            workflow_version="1",
            device_id=run.device_id,
            operator=self._operator,
            status=RunStatus.PENDING,
            started_at=transition_at,
            configuration_snapshot=regular_configuration.snapshot(),
            software_version=self._software_version,
            notes=notes,
        )
        self._repository.save_filling_advance_transition(
            profile=profile,
            completed_run=completed_run,
            new_run=new_run,
        )

        self._run = new_run
        self._configuration = regular_configuration
        self._pending_trial = _PendingTrial(1, transition_at)
        self._trials = ()
        return profile

    @_synchronized
    def end_group(self) -> None:
        if self._run is None:
            return
        minimum_end_time = max(self._active_event_times())
        ended_at = self._event_time("End Group", minimum_end_time)
        final_status = (
            RunStatus.COMPLETED if self._trials else RunStatus.CANCELED
        )
        ended_run = replace(
            self._run,
            status=final_status,
            ended_at=ended_at,
        )
        self._repository.save_run(ended_run)
        self._run = None
        self._configuration = None
        self._pending_trial = None
        self._trials = ()

    @_synchronized
    def list_advance_profiles(
        self,
    ) -> tuple[FillingAdvanceProfileRecord, ...]:
        return self._repository.list_filling_advance_profiles(
            self._require_selected_device()
        )

    @_synchronized
    def list_history(self) -> tuple[FillingHistoryEntry, ...]:
        device_id = self._require_selected_device()
        trials = self._repository.list_filling_trials(device_id=device_id)
        profiles = self._repository.list_filling_advance_profiles(device_id)
        profile_by_result: dict[str, list[FillingAdvanceProfileRecord]] = {}
        for profile in profiles:
            profile_by_result.setdefault(profile.source_result_id, []).append(profile)

        entries = [self._trial_history_entry(trial) for trial in trials]
        result_by_id: dict[str, AnalysisResultRecord] = {}
        run_notes: dict[str, str | None] = {}
        for summary in self._repository.list_runs(
            device_id=device_id,
            run_type=RunType.FILLING_TRIAL.value,
        ):
            run_notes[summary.run_id] = summary.notes
            for result in self._repository.list_analysis_results(summary.run_id):
                if result.result_type not in {
                    "filling_repeatability",
                    "filling_advance",
                }:
                    continue
                result_by_id[result.result_id] = result
                entries.append(
                    self._analysis_history_entry(
                        result,
                        device_id=device_id,
                        notes=summary.notes,
                        linked_profiles=profile_by_result.get(result.result_id, []),
                    )
                )

        for profile in profiles:
            source = result_by_id.get(profile.source_result_id)
            if source is None:
                source = self._repository.get_analysis_result(
                    profile.source_result_id
                )
            if source is None:
                raise ValueError(
                    "Advance profile references a missing analysis result: "
                    f"{profile.source_result_id}"
                )
            entries.append(
                self._profile_history_entry(
                    profile,
                    run_id=source.run_id,
                    run_notes=run_notes.get(source.run_id),
                )
            )

        minimum = datetime.min.replace(tzinfo=UTC)
        return tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.created_at or minimum,
                    entry.record_type,
                    entry.record_id,
                ),
                reverse=True,
            )
        )

    @_synchronized
    def snapshot(self) -> FillingGroupSnapshot:
        if self._run is None:
            return FillingGroupSnapshot(
                device_id=self._selected_device_id,
                run_id=None,
                status=None,
                configuration=self._selected_configuration,
                configuration_locked=False,
                has_pending_trial=False,
                pending_trial_index=None,
                trials=(),
            )
        return FillingGroupSnapshot(
            device_id=self._selected_device_id,
            run_id=self._run.run_id,
            status=self._run.status,
            configuration=self._configuration,
            configuration_locked=bool(self._trials),
            has_pending_trial=self._pending_trial is not None,
            pending_trial_index=(
                self._pending_trial.trial_index
                if self._pending_trial is not None
                else None
            ),
            trials=self._trials,
        )

    def _persist_analysis(
        self,
        *,
        result_type: str,
        algorithm_name: str,
        source_trials: tuple[FillingTrialRecord, ...],
        metrics: dict[str, object],
    ) -> FillingAnalysisRecord:
        run, configuration = self._require_active_group()
        source_started_at, source_ended_at = _source_time_range(source_trials)
        created_at = self._event_time("Filling analysis", source_ended_at)
        step_id = self._new_id("filling-step")
        result_id = self._new_id("filling-result")
        analysis_configuration = _analysis_configuration(
            configuration,
            source_trials,
            source_started_at=source_started_at,
            source_ended_at=source_ended_at,
        )
        step = WorkflowStep(
            step_id=step_id,
            run_id=run.run_id,
            name=(
                "Calculate filling repeatability"
                if result_type == "filling_repeatability"
                else "Calculate filling advance"
            ),
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.COMPLETED,
            started_at=created_at,
            ended_at=created_at,
            input_configuration=analysis_configuration,
            output_summary=dict(metrics),
        )
        result = AnalysisResultRecord(
            result_id=result_id,
            run_id=run.run_id,
            step_id=step_id,
            result_type=result_type,
            algorithm_name=algorithm_name,
            algorithm_version="1",
            configuration_snapshot=analysis_configuration,
            summary_metrics=dict(metrics),
            pass_fail_decision=None,
            created_at=created_at,
        )
        self._repository.save_filling_analysis(step=step, result=result)
        return FillingAnalysisRecord(
            result_id=result_id,
            run_id=run.run_id,
            result_type=result_type,
            created_at=created_at,
            metrics=dict(metrics),
        )

    def _repeatability_metrics(
        self,
        calculation: FillingRepeatabilityResult,
        trials: tuple[FillingTrialRecord, ...],
    ) -> dict[str, object]:
        started_at, ended_at = _source_time_range(trials)
        _, configuration = self._require_active_group()
        return {
            "source_trial_ids": list(calculation.trial_ids),
            "source_trial_indexes": list(calculation.trial_indexes),
            "source_trial_started_at": started_at.isoformat(),
            "source_trial_ended_at": ended_at.isoformat(),
            "trial_errors_percent": list(calculation.errors_percent),
            "mean_error_percent": calculation.mean_error_percent,
            "repeatability_stddev_percent": (
                calculation.repeatability_stddev_percent
            ),
            "configuration_snapshot": configuration.snapshot(),
        }

    def _advance_metrics(
        self,
        calculation: FillingAdvanceResult,
        trials: tuple[FillingTrialRecord, ...],
    ) -> dict[str, object]:
        started_at, ended_at = _source_time_range(trials)
        _, configuration = self._require_active_group()
        return {
            "source_trial_ids": list(calculation.trial_ids),
            "source_trial_indexes": list(calculation.trial_indexes),
            "source_trial_started_at": started_at.isoformat(),
            "source_trial_ended_at": ended_at.isoformat(),
            "source_standard_masses": list(calculation.standard_masses),
            "selected_trial_count": len(calculation.trial_ids),
            "mean_standard_mass": calculation.mean_standard_mass,
            "specified_mass": calculation.specified_mass,
            "advance_mass": calculation.advance_mass,
            "corrected_target_mass": calculation.corrected_target_mass,
            "configuration_snapshot": configuration.snapshot(),
        }

    def _load_selected_trials(
        self,
        trial_ids: Sequence[str],
    ) -> tuple[FillingTrialRecord, ...]:
        run, configuration = self._require_active_group()
        if isinstance(trial_ids, (str, bytes)):
            raise ValueError("Trial IDs must be a sequence of unique IDs.")
        selected_ids = tuple(trial_ids)
        if not selected_ids:
            raise ValueError("Select at least one trial.")
        if any(not isinstance(trial_id, str) or not trial_id.strip() for trial_id in selected_ids):
            raise ValueError("Trial IDs must be non-empty strings.")
        if len(set(selected_ids)) != len(selected_ids):
            raise ValueError("Trial IDs must be unique.")

        expected_snapshot = configuration.snapshot()
        selected: list[FillingTrialRecord] = []
        for trial_id in selected_ids:
            trial = self._repository.get_filling_trial(trial_id)
            if trial is None:
                raise ValueError(f"Unknown filling trial: {trial_id}")
            if trial.run_id != run.run_id:
                raise ValueError("All trials must belong to the active run.")
            if trial.device_id != run.device_id:
                raise ValueError("All trials must belong to the selected device.")
            if trial.trial_status != "calculated":
                raise ValueError("Only calculated trials can be analyzed.")
            if trial.configuration_snapshot != expected_snapshot:
                raise ValueError("All trials must share the active configuration snapshot.")
            if not _trial_fields_match_configuration(trial, configuration):
                raise ValueError("Trial fields do not match the active configuration.")
            _aware_utc("Trial started_at", trial.started_at)
            _aware_utc("Trial calculated_at", trial.calculated_at)
            selected.append(trial)
        return tuple(selected)

    def _validate_advance_result(
        self,
        result: AnalysisResultRecord,
        *,
        run: RunSession,
        canonical_configuration: dict[str, object],
        canonical_metrics: dict[str, object],
        source_ended_at: datetime,
        matching_steps: tuple[WorkflowStep, ...],
    ) -> datetime:
        if result.run_id != run.run_id:
            raise ValueError("result run ID is inconsistent")
        if result.result_type != "filling_advance":
            raise ValueError("result type is inconsistent")
        if result.algorithm_name != "filling_advance":
            raise ValueError("algorithm name is inconsistent")
        if result.algorithm_version != "1":
            raise ValueError("algorithm version is inconsistent")
        if result.input_artifact_ids != ():
            raise ValueError("input artifact IDs must be empty")
        if result.pass_fail_decision is not None:
            raise ValueError("pass/fail decision must be unset")
        if result.configuration_snapshot != canonical_configuration:
            raise ValueError("result configuration snapshot is inconsistent")
        if result.summary_metrics != canonical_metrics:
            raise ValueError("result metrics are inconsistent")
        result_created_at = _aware_utc(
            "Advance result created_at",
            result.created_at,
        )
        if result_created_at < source_ended_at:
            raise ValueError("result predates its source trials")
        if (
            not isinstance(result.step_id, str)
            or not result.step_id.strip()
            or len(matching_steps) != 1
        ):
            raise ValueError("result must reference one unique workflow step")

        step = matching_steps[0]
        if step.step_id != result.step_id or step.run_id != run.run_id:
            raise ValueError("workflow step references are inconsistent")
        if step.name != "Calculate filling advance":
            raise ValueError("workflow step name is inconsistent")
        if step.step_type is not WorkflowStepType.ANALYSIS:
            raise ValueError("workflow step type is inconsistent")
        if step.status is not WorkflowStepStatus.COMPLETED:
            raise ValueError("workflow step status is inconsistent")
        if step.input_configuration != canonical_configuration:
            raise ValueError("workflow step input is inconsistent")
        if step.output_summary != canonical_metrics:
            raise ValueError("workflow step output is inconsistent")
        if step.error_message is not None:
            raise ValueError("workflow step error must be unset")
        if (
            _aware_utc("Advance step started_at", step.started_at)
            != result_created_at
            or _aware_utc("Advance step ended_at", step.ended_at)
            != result_created_at
        ):
            raise ValueError("workflow step timestamps are inconsistent")
        return result_created_at

    def _trial_history_entry(
        self,
        trial: FillingTrialRecord,
    ) -> FillingHistoryEntry:
        return FillingHistoryEntry(
            record_id=trial.trial_id,
            record_type="trial",
            run_id=trial.run_id,
            device_id=trial.device_id,
            created_at=trial.calculated_at or trial.started_at,
            summary=(
                f"Trial {trial.trial_index}: error={trial.percent_error:g}%"
            ),
            details={
                "trial_id": trial.trial_id,
                "trial_index": trial.trial_index,
                "trial_status": trial.trial_status,
                "mode": trial.mode,
                "control_valve_label": trial.control_valve_label,
                "pulse_frequency_switch_point_hz": (
                    trial.pulse_frequency_switch_point_hz
                ),
                "mass_per_pulse": trial.mass_per_pulse,
                "mass_unit": trial.mass_unit,
                "flow_point_g_per_s": trial.flow_point_g_per_s,
                "specified_mass": trial.specified_mass,
                "target_mass": trial.target_mass,
                "standard_mass": trial.standard_mass,
                "percent_error": trial.percent_error,
                "configuration_snapshot": dict(trial.configuration_snapshot),
                "source_trial_ids": [trial.trial_id],
                "started_at": _iso_or_none(trial.started_at),
                "calculated_at": _iso_or_none(trial.calculated_at),
                "notes": trial.notes,
            },
        )

    def _analysis_history_entry(
        self,
        result: AnalysisResultRecord,
        *,
        device_id: str,
        notes: str | None,
        linked_profiles: Sequence[FillingAdvanceProfileRecord],
    ) -> FillingHistoryEntry:
        repeatability = result.result_type == "filling_repeatability"
        record_type = "repeatability" if repeatability else "advance_calculation"
        summary = (
            "Repeatability: "
            f"stddev={result.summary_metrics.get('repeatability_stddev_percent')}%"
            if repeatability
            else "Advance: "
            f"mass={result.summary_metrics.get('advance_mass')}"
        )
        return FillingHistoryEntry(
            record_id=result.result_id,
            record_type=record_type,
            run_id=result.run_id,
            device_id=device_id,
            created_at=result.created_at,
            summary=summary,
            details={
                "result_id": result.result_id,
                "result_type": result.result_type,
                "algorithm_name": result.algorithm_name,
                "algorithm_version": result.algorithm_version,
                "configuration_snapshot": dict(result.configuration_snapshot),
                "metrics": dict(result.summary_metrics),
                "source_trial_ids": list(
                    result.summary_metrics.get("source_trial_ids", [])
                ),
                "pass_fail_decision": result.pass_fail_decision,
                "created_at": _iso_or_none(result.created_at),
                "linked_advance_profile_ids": [
                    profile.profile_id for profile in linked_profiles
                ],
                "notes": notes,
            },
        )

    def _profile_history_entry(
        self,
        profile: FillingAdvanceProfileRecord,
        *,
        run_id: str,
        run_notes: str | None,
    ) -> FillingHistoryEntry:
        return FillingHistoryEntry(
            record_id=profile.profile_id,
            record_type="advance_profile",
            run_id=run_id,
            device_id=profile.device_id,
            created_at=profile.created_at,
            summary=(
                f"Advance profile: advance={profile.advance_mass:g}, "
                f"target={profile.corrected_target_mass:g} {profile.mass_unit}"
            ),
            details={
                "profile_id": profile.profile_id,
                "source_result_id": profile.source_result_id,
                "control_valve_label": profile.control_valve_label,
                "pulse_frequency_switch_point_hz": (
                    profile.pulse_frequency_switch_point_hz
                ),
                "mass_per_pulse": profile.mass_per_pulse,
                "mass_unit": profile.mass_unit,
                "flow_point_g_per_s": profile.flow_point_g_per_s,
                "specified_mass": profile.specified_mass,
                "advance_mass": profile.advance_mass,
                "corrected_target_mass": profile.corrected_target_mass,
                "source_trial_ids": list(profile.source_trial_ids),
                "configuration_snapshot": dict(profile.configuration_snapshot),
                "created_at": _iso_or_none(profile.created_at),
                "notes": profile.notes,
                "run_notes": run_notes,
            },
        )

    def _require_active_group(
        self,
    ) -> tuple[RunSession, FillingConfiguration]:
        if self._run is None or self._configuration is None:
            raise ValueError("There is no active filling group.")
        return self._run, self._configuration

    def _require_selected_device(self) -> str:
        if self._selected_device_id is None:
            raise ValueError("Select a device before using the Filling Module.")
        return self._selected_device_id

    @staticmethod
    def _require_configuration(configuration: FillingConfiguration) -> None:
        if not isinstance(configuration, FillingConfiguration):
            raise ValueError("A FillingConfiguration is required.")

    def _now(self) -> datetime:
        return _aware_utc("Clock value", self._clock())

    def _event_time(self, label: str, minimum: datetime) -> datetime:
        lower_bound = _aware_utc(f"{label} lower bound", minimum)
        value = self._now()
        if value < lower_bound:
            raise ValueError(
                f"{label} time cannot be earlier than {lower_bound.isoformat()}."
            )
        return value

    def _active_event_times(self) -> tuple[datetime, ...]:
        run, _ = self._require_active_group()
        event_times = [
            _aware_utc("Run started_at", run.started_at),
        ]
        if self._pending_trial is not None:
            event_times.append(self._pending_trial.started_at)
        for trial in self._trials:
            event_times.extend(
                (
                    _aware_utc("Trial started_at", trial.started_at),
                    _aware_utc("Trial calculated_at", trial.calculated_at),
                )
            )
        for result in self._repository.list_analysis_results(run.run_id):
            event_times.append(
                _aware_utc("Analysis created_at", result.created_at)
            )
        return tuple(event_times)

    def _new_id(self, prefix: str) -> str:
        token = _nonempty_text("Generated token", self._token_factory())
        candidate = f"{prefix}:{token}"
        suffix = 2
        while candidate in self._issued_ids or self._persisted_id_exists(
            prefix,
            candidate,
        ):
            candidate = f"{prefix}:{token}:{suffix}"
            suffix += 1
        self._issued_ids.add(candidate)
        return candidate

    def _persisted_id_exists(self, prefix: str, candidate: str) -> bool:
        if prefix == "filling-run":
            return self._repository.get_run(candidate) is not None
        if prefix == "filling-trial":
            return self._repository.get_filling_trial(candidate) is not None
        if prefix == "filling-result":
            return self._repository.get_analysis_result(candidate) is not None
        if prefix == "filling-step":
            return any(
                step.step_id == candidate
                for run in self._repository.list_runs()
                for step in self._repository.list_steps(run.run_id)
            )
        if prefix == "filling-profile":
            return any(
                profile.profile_id == candidate
                for device in self._repository.list_devices()
                for profile in self._repository.list_filling_advance_profiles(
                    device.device_id
                )
            )
        raise ValueError(f"Unknown filling ID prefix: {prefix}")


def _configuration_from_trial(
    trial: FillingTrialRecord,
) -> FillingConfiguration:
    return FillingConfiguration(
        mode=FillingMode(trial.mode),
        control_valve_label=trial.control_valve_label,
        pulse_frequency_switch_point_hz=(
            trial.pulse_frequency_switch_point_hz
        ),
        mass_per_pulse=trial.mass_per_pulse,
        mass_unit=trial.mass_unit,
        flow_point_g_per_s=trial.flow_point_g_per_s,
        specified_mass=trial.specified_mass,
        target_mass=trial.target_mass,
    )


def _trial_value(trial: FillingTrialRecord) -> FillingTrialValue:
    return FillingTrialValue(
        trial_id=trial.trial_id,
        trial_index=trial.trial_index,
        specified_mass=trial.specified_mass,
        standard_mass=trial.standard_mass,
        error_percent=trial.percent_error,
    )


def _trial_fields_match_configuration(
    trial: FillingTrialRecord,
    configuration: FillingConfiguration,
) -> bool:
    return (
        trial.mode == configuration.mode.value
        and trial.control_valve_label == configuration.control_valve_label
        and trial.pulse_frequency_switch_point_hz
        == configuration.pulse_frequency_switch_point_hz
        and trial.mass_per_pulse == configuration.mass_per_pulse
        and trial.mass_unit == configuration.mass_unit
        and trial.flow_point_g_per_s == configuration.flow_point_g_per_s
        and trial.specified_mass == configuration.specified_mass
        and trial.target_mass == configuration.target_mass
    )


def _source_time_range(
    trials: Sequence[FillingTrialRecord],
) -> tuple[datetime, datetime]:
    started = tuple(
        _aware_utc("Trial started_at", trial.started_at) for trial in trials
    )
    ended = tuple(
        _aware_utc("Trial calculated_at", trial.calculated_at)
        for trial in trials
    )
    return min(started), max(ended)


def _analysis_configuration(
    configuration: FillingConfiguration,
    trials: Sequence[FillingTrialRecord],
    *,
    source_started_at: datetime | None = None,
    source_ended_at: datetime | None = None,
) -> dict[str, object]:
    if source_started_at is None or source_ended_at is None:
        source_started_at, source_ended_at = _source_time_range(trials)
    return {
        **configuration.snapshot(),
        "source_trial_ids": [trial.trial_id for trial in trials],
        "source_trial_indexes": [trial.trial_index for trial in trials],
        "source_trial_started_at": source_started_at.isoformat(),
        "source_trial_ended_at": source_ended_at.isoformat(),
    }


def _combined_notes(trials: Sequence[FillingTrialRecord]) -> str | None:
    notes = tuple(trial.notes.strip() for trial in trials if trial.notes and trial.notes.strip())
    return "\n".join(notes) if notes else None


def _string_list_metric(metrics: dict[str, object], key: str) -> tuple[str, ...]:
    value = metrics.get(key)
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"Advance result is missing {key}.")
    values = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"Advance result contains invalid {key}.")
    if len(set(values)) != len(values):
        raise ValueError(f"Advance result contains duplicate {key}.")
    return values


def _positive_number(label: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number greater than zero.")
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{label} must be a finite number greater than zero.")
    return normalized


def _nonempty_text(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string.")
    return value.strip()


def _aware_utc(label: str, value: datetime | None) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be an aware UTC datetime.")
    offset = value.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ValueError(f"{label} must be an aware UTC datetime.")
    return value.astimezone(UTC)


def _iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


__all__ = [
    "FillingAnalysisRecord",
    "FillingConfiguration",
    "FillingGroupSnapshot",
    "FillingHistoryEntry",
    "FillingMode",
    "FillingTrialService",
]

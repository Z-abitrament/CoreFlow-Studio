"""Application service for the independent Pulse Counter module."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path

from coreflow.pulse_counter import (
    PulseAnalysisConfig,
    analyze_dsview_csv,
    calculate_measured_pulse_error,
)
from coreflow.pulse_counter.models import PulseAnalysisResult
from coreflow.storage import (
    DeviceRecord,
    PulseDeviceProfileRecord,
    PulseOperationAttemptRecord,
    PulseTrialRecord,
    StorageRepository,
)


@dataclass(frozen=True, slots=True)
class PulseProfile:
    """Resolved Pulse Counter profile and analysis configuration."""

    record: PulseDeviceProfileRecord
    config: PulseAnalysisConfig


@dataclass(frozen=True, slots=True)
class PulseTrialCalculationResult:
    """Result of one pulse-derived mass trial calculation."""

    attempt: PulseOperationAttemptRecord
    trial: PulseTrialRecord


@dataclass(frozen=True, slots=True)
class PulseRepeatabilityResult:
    """Repeatability summary for selected Pulse Counter trials."""

    device_id: str
    flow_point: float
    trial_count: int
    mean_percent_error: float
    repeatability_stddev_percent: float
    trials: tuple[PulseTrialRecord, ...]
    attempt: PulseOperationAttemptRecord | None = None


class PulseCounterRuntime:
    """Headless Pulse Counter operations independent from Modbus state."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        operator: str = "operator",
    ) -> None:
        self._repository = repository
        self._operator = operator

    def save_profile(
        self,
        *,
        device_id: str,
        channel: str = "0",
        edge: str = "rising",
        pulse_value: float = 0.05,
        unit: str = "g",
        switch_frequency_hz: float = 100.0,
        boundary_tolerance_s: float | None = None,
        display_name: str | None = None,
        notes: str | None = None,
    ) -> PulseProfile:
        """Persist Pulse configuration for one stable Device ID."""

        _validate_device_id(device_id)
        config = PulseAnalysisConfig(
            channel=channel,
            edge=edge,  # type: ignore[arg-type]
            pulse_value=pulse_value,
            unit=unit,
            switch_frequency_hz=switch_frequency_hz,
            boundary_tolerance_s=boundary_tolerance_s,
        )
        record = PulseDeviceProfileRecord(
            profile_id=f"pulse-profile:{device_id}",
            device_id=device_id,
            display_name=display_name or device_id,
            channel=config.channel,
            edge=config.edge,
            pulse_value=config.pulse_value,
            unit=config.unit,
            switch_frequency_hz=config.switch_frequency_hz,
            boundary_tolerance_s=config.boundary_tolerance_s,
            notes=notes,
        )
        self._repository.save_device(
            DeviceRecord(
                device_id=device_id,
                device_type="flowmeter",
                connection_metadata={"pulse_counter_profile": record.profile_id},
            )
        )
        self._repository.save_pulse_device_profile(record)
        return PulseProfile(record=record, config=config)

    def load_profile(self, device_id: str) -> PulseProfile:
        """Load a device-scoped Pulse profile, returning defaults when absent."""

        record = self._repository.get_pulse_device_profile(device_id)
        if record is None:
            record = PulseDeviceProfileRecord(
                profile_id=f"pulse-profile:{device_id}",
                device_id=device_id,
            )
        config = PulseAnalysisConfig(
            channel=record.channel,
            edge=record.edge,  # type: ignore[arg-type]
            pulse_value=record.pulse_value,
            unit=record.unit,
            switch_frequency_hz=record.switch_frequency_hz,
            boundary_tolerance_s=record.boundary_tolerance_s,
        )
        return PulseProfile(record=record, config=config)

    def analyze_csv(
        self,
        *,
        device_id: str,
        csv_path: str | Path,
    ) -> PulseAnalysisResult:
        """Analyze a DSView CSV with the current device-scoped configuration."""

        profile = self.load_profile(device_id)
        return analyze_dsview_csv(csv_path, profile.config)

    def calculate_trial_from_analysis(
        self,
        *,
        device_id: str,
        analysis: PulseAnalysisResult,
        standard_quantity: float,
        flow_point: float,
        trial_index: int,
        source_path: str | None = None,
        notes: str | None = None,
    ) -> PulseTrialCalculationResult:
        """Persist one trial from parsed pulse analysis and operator standard mass."""

        return self.calculate_trial_from_counts(
            device_id=device_id,
            flow_point=flow_point,
            trial_index=trial_index,
            pulse_count=analysis.pulse_count,
            standard_quantity=standard_quantity,
            mean_rate=analysis.mean_rate,
            boundary_pulse_count=analysis.boundary_pulse_count,
            source_path=source_path
            or (
                str(analysis.metadata.source_path)
                if analysis.metadata.source_path is not None
                else None
            ),
            notes=notes,
        )

    def calculate_trial_from_counts(
        self,
        *,
        device_id: str,
        flow_point: float,
        trial_index: int,
        pulse_count: int,
        standard_quantity: float,
        mean_rate: float | None = None,
        boundary_pulse_count: int = 0,
        source_path: str | None = None,
        notes: str | None = None,
    ) -> PulseTrialCalculationResult:
        """Persist one trial from pulse count and operator-entered standard mass."""

        if pulse_count < 0:
            raise ValueError("Pulse count cannot be negative.")
        profile = self.load_profile(device_id)
        measured_quantity = pulse_count * profile.config.pulse_value
        error = calculate_measured_pulse_error(
            measured_quantity=measured_quantity,
            standard_quantity=standard_quantity,
        )
        now = datetime.now(UTC)
        attempt_id = f"PULSE-ATTEMPT-{self._repository.count_rows('pulse_operation_attempts') + 1:06d}"
        trial_id = f"PULSE-TRIAL-{self._repository.count_rows('pulse_trial_records') + 1:06d}"
        summary = {
            "flow_point": flow_point,
            "trial_index": trial_index,
            "pulse_count": pulse_count,
            "measured_quantity": error.measured_quantity,
            "standard_quantity": error.standard_quantity,
            "percent_error": error.percent_error,
            "mean_rate": mean_rate,
            "boundary_pulse_count": boundary_pulse_count,
            "unit": profile.config.unit,
        }
        attempt = PulseOperationAttemptRecord(
            attempt_id=attempt_id,
            device_id=device_id,
            operation_type="pulse_csv_trial",
            status="calculated",
            operator=self._operator,
            started_at=now,
            ended_at=now,
            source_path=source_path,
            summary=summary,
            configuration_snapshot=_config_snapshot(profile.config),
            notes=notes,
        )
        trial = PulseTrialRecord(
            trial_id=trial_id,
            attempt_id=attempt_id,
            device_id=device_id,
            flow_point=flow_point,
            trial_index=trial_index,
            trial_status="accepted",
            pulse_count=pulse_count,
            measured_quantity=error.measured_quantity,
            standard_quantity=error.standard_quantity,
            percent_error=error.percent_error,
            mean_rate=mean_rate,
            started_at=now,
            ended_at=now,
            boundary_pulse_count=boundary_pulse_count,
            notes=notes,
        )
        self._repository.save_pulse_operation_attempt(attempt)
        self._repository.save_pulse_trial_record(trial)
        return PulseTrialCalculationResult(attempt=attempt, trial=trial)

    def calculate_repeatability(
        self,
        device_id: str,
        *,
        trial_ids: tuple[str, ...],
    ) -> PulseRepeatabilityResult:
        """Calculate repeatability from selected pulse trial errors."""

        trials_by_id = {
            trial.trial_id: trial
            for trial in self._repository.list_pulse_trial_records(device_id=device_id)
        }
        trials = tuple(trials_by_id[trial_id] for trial_id in trial_ids)
        if len(trials) < 2:
            raise ValueError("Repeatability requires at least two pulse trials.")
        errors = [trial.percent_error for trial in trials]
        mean_error = sum(errors) / len(errors)
        variance = sum((error - mean_error) ** 2 for error in errors) / (len(errors) - 1)
        return PulseRepeatabilityResult(
            device_id=device_id,
            flow_point=trials[0].flow_point,
            trial_count=len(trials),
            mean_percent_error=mean_error,
            repeatability_stddev_percent=sqrt(variance),
            trials=trials,
        )

    def save_repeatability_selection(
        self,
        device_id: str,
        *,
        trial_ids: tuple[str, ...],
        notes: str | None = None,
    ) -> PulseRepeatabilityResult:
        """Persist a user-selected three-trial Pulse repeatability result."""

        trials = self._selected_repeatability_trials(device_id, trial_ids)
        errors = [trial.percent_error for trial in trials]
        mean_error = sum(errors) / len(errors)
        variance = sum((error - mean_error) ** 2 for error in errors) / (len(errors) - 1)
        repeatability = sqrt(variance)
        started_values = [trial.started_at for trial in trials if trial.started_at is not None]
        ended_values = [trial.ended_at for trial in trials if trial.ended_at is not None]
        profile = self.load_profile(device_id)
        now = datetime.now(UTC)
        attempt_id = f"PULSE-ATTEMPT-{self._repository.count_rows('pulse_operation_attempts') + 1:06d}"
        summary = {
            "flow_point": trials[0].flow_point,
            "selected_trial_ids": [trial.trial_id for trial in trials],
            "selected_trial_indexes": [trial.trial_index for trial in trials],
            "trial_count": len(trials),
            "mean_percent_error": mean_error,
            "repeatability_stddev_percent": repeatability,
            "source_trial_started_at": min(started_values).isoformat()
            if started_values
            else None,
            "source_trial_ended_at": max(ended_values).isoformat()
            if ended_values
            else None,
            "unit": profile.config.unit,
        }
        attempt = PulseOperationAttemptRecord(
            attempt_id=attempt_id,
            device_id=device_id,
            operation_type="pulse_repeatability",
            status="calculated",
            operator=self._operator,
            started_at=now,
            ended_at=now,
            summary=summary,
            configuration_snapshot=_config_snapshot(profile.config),
            notes=notes,
        )
        self._repository.save_pulse_operation_attempt(attempt)
        return PulseRepeatabilityResult(
            device_id=device_id,
            flow_point=trials[0].flow_point,
            trial_count=len(trials),
            mean_percent_error=mean_error,
            repeatability_stddev_percent=repeatability,
            trials=trials,
            attempt=attempt,
        )

    def list_history(self, device_id: str) -> tuple[PulseOperationAttemptRecord, ...]:
        """List Pulse operation records for one Device ID."""

        return self._repository.list_pulse_operation_attempts(device_id=device_id)

    def list_trials(self, device_id: str) -> tuple[PulseTrialRecord, ...]:
        """List Pulse trial records for one Device ID."""

        return self._repository.list_pulse_trial_records(device_id=device_id)

    def _selected_repeatability_trials(
        self,
        device_id: str,
        trial_ids: tuple[str, ...],
    ) -> tuple[PulseTrialRecord, ...]:
        if len(trial_ids) != 3:
            raise ValueError("Pulse repeatability requires exactly three selected trials.")
        trials_by_id = {
            trial.trial_id: trial
            for trial in self._repository.list_pulse_trial_records(device_id=device_id)
        }
        try:
            trials = tuple(trials_by_id[trial_id] for trial_id in trial_ids)
        except KeyError as exc:
            raise ValueError(f"Selected pulse trial does not exist: {exc.args[0]}") from exc
        flow_points = {trial.flow_point for trial in trials}
        if len(flow_points) != 1:
            raise ValueError("Pulse repeatability trials must use the same flow point.")
        indexes = [trial.trial_index for trial in sorted(trials, key=lambda item: item.trial_index)]
        expected = list(range(indexes[0], indexes[0] + 3))
        if indexes != expected:
            raise ValueError("Pulse repeatability trial indexes must be consecutive.")
        return tuple(sorted(trials, key=lambda item: item.trial_index))


def _config_snapshot(config: PulseAnalysisConfig) -> dict[str, object]:
    return {
        "channel": config.channel,
        "edge": config.edge,
        "pulse_value": config.pulse_value,
        "unit": config.unit,
        "switch_frequency_hz": config.switch_frequency_hz,
        "boundary_tolerance_s": config.boundary_tolerance_s,
    }


def _validate_device_id(device_id: str) -> None:
    if not device_id.strip():
        raise ValueError("Device ID is required.")
    if device_id.strip().isdigit():
        raise ValueError("Device ID must be a stable asset ID, not a numeric unit ID.")

"""Pure calculations for manually recorded filling trials."""

from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite, sqrt
from typing import Sequence


@dataclass(frozen=True, slots=True)
class FillingTrialValue:
    """Calculation inputs from one persisted filling trial."""

    trial_id: str
    trial_index: int
    specified_mass: float
    standard_mass: float
    error_percent: float


@dataclass(frozen=True, slots=True)
class FillingRepeatabilityResult:
    """Sample repeatability calculated from three consecutive trials."""

    trial_ids: tuple[str, ...]
    trial_indexes: tuple[int, ...]
    errors_percent: tuple[float, ...]
    mean_error_percent: float
    repeatability_stddev_percent: float


@dataclass(frozen=True, slots=True)
class FillingAdvanceResult:
    """Valve-closing advance calculated from selected filling trials."""

    trial_ids: tuple[str, ...]
    trial_indexes: tuple[int, ...]
    standard_masses: tuple[float, ...]
    specified_mass: float
    mean_standard_mass: float
    advance_mass: float
    corrected_target_mass: float


def calculate_trial_error(specified_mass: float, standard_mass: float) -> float:
    """Calculate final-mass error relative to the specified mass."""

    _positive("specified mass", specified_mass)
    _positive("standard mass", standard_mass)
    error_percent = (standard_mass - specified_mass) / specified_mass * 100.0
    _finite("trial error percent", error_percent)
    return error_percent


def calculate_repeatability(
    trials: Sequence[FillingTrialValue],
) -> FillingRepeatabilityResult:
    """Calculate sample standard deviation for three consecutive trials."""

    selected = tuple(trials)
    if len(selected) != 3:
        raise ValueError("Repeatability requires exactly 3 trials.")
    _validate_trials(selected)

    ordered = tuple(sorted(selected, key=lambda trial: trial.trial_index))
    indexes = tuple(trial.trial_index for trial in ordered)
    expected_indexes = tuple(range(indexes[0], indexes[0] + 3))
    if indexes != expected_indexes:
        raise ValueError("Repeatability trials must be consecutive.")

    errors = tuple(trial.error_percent for trial in ordered)
    mean_error, stddev = _mean_and_sample_stddev(errors)
    return FillingRepeatabilityResult(
        trial_ids=tuple(trial.trial_id for trial in ordered),
        trial_indexes=indexes,
        errors_percent=errors,
        mean_error_percent=mean_error,
        repeatability_stddev_percent=stddev,
    )


def calculate_advance(
    trials: Sequence[FillingTrialValue],
) -> FillingAdvanceResult:
    """Calculate advance mass and the corresponding corrected target mass."""

    selected = tuple(trials)
    if len(selected) < 3:
        raise ValueError("Advance calculation requires at least 3 trials.")
    _validate_trials(selected)

    specified_mass = selected[0].specified_mass
    if any(trial.specified_mass != specified_mass for trial in selected[1:]):
        raise ValueError("Advance trials must share one specified mass.")

    standard_masses = tuple(trial.standard_mass for trial in selected)
    mean_standard_mass = _stable_mean(standard_masses)
    advance_mass = mean_standard_mass - specified_mass
    _finite("advance mass", advance_mass)
    corrected_target_mass = specified_mass - advance_mass
    _positive("corrected target mass", corrected_target_mass)

    return FillingAdvanceResult(
        trial_ids=tuple(trial.trial_id for trial in selected),
        trial_indexes=tuple(trial.trial_index for trial in selected),
        standard_masses=standard_masses,
        specified_mass=specified_mass,
        mean_standard_mass=mean_standard_mass,
        advance_mass=advance_mass,
        corrected_target_mass=corrected_target_mass,
    )


def _validate_trials(trials: tuple[FillingTrialValue, ...]) -> None:
    trial_ids: set[str] = set()
    trial_indexes: set[int] = set()
    for trial in trials:
        if not isinstance(trial.trial_id, str) or not trial.trial_id.strip():
            raise ValueError("Trial ID must be a non-empty string.")
        if trial.trial_id in trial_ids:
            raise ValueError("Trial IDs must be unique.")
        trial_ids.add(trial.trial_id)

        if (
            isinstance(trial.trial_index, bool)
            or not isinstance(trial.trial_index, int)
            or trial.trial_index <= 0
        ):
            raise ValueError("Trial index must be a positive integer.")
        if trial.trial_index in trial_indexes:
            raise ValueError("Trial indexes must be unique.")
        trial_indexes.add(trial.trial_index)

        _positive("specified mass", trial.specified_mass)
        _positive("standard mass", trial.standard_mass)
        _finite("error percent", trial.error_percent)


def _mean_and_sample_stddev(values: tuple[float, ...]) -> tuple[float, float]:
    scale = max(abs(value) for value in values)
    if scale == 0.0:
        return 0.0, 0.0

    scaled_values = tuple(value / scale for value in values)
    scaled_mean = fsum(scaled_values) / len(scaled_values)
    mean = scale * scaled_mean
    scaled_variance = fsum(
        (value - scaled_mean) ** 2 for value in scaled_values
    ) / (len(scaled_values) - 1)
    stddev = scale * sqrt(scaled_variance)
    _finite("mean error percent", mean)
    _finite("repeatability standard deviation percent", stddev)
    return mean, stddev


def _stable_mean(values: tuple[float, ...]) -> float:
    try:
        mean = fsum(values) / len(values)
    except OverflowError:
        scale = max(values)
        mean = scale * (fsum(value / scale for value in values) / len(values))
    _finite("mean standard mass", mean)
    return mean


def _positive(label: str, value: float) -> None:
    _finite(label, value)
    if value <= 0:
        raise ValueError(f"{label.capitalize()} must be greater than zero.")


def _finite(label: str, value: float) -> None:
    try:
        finite = isfinite(value)
    except TypeError as exc:
        raise ValueError(f"{label.capitalize()} must be finite.") from exc
    if not finite:
        raise ValueError(f"{label.capitalize()} must be finite.")

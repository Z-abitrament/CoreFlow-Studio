from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from coreflow.analysis import (
    FillingAdvanceResult,
    FillingRepeatabilityResult,
    FillingTrialValue,
    calculate_advance,
    calculate_repeatability,
    calculate_trial_error,
)


def _trial(
    trial_id: str,
    trial_index: int,
    *,
    specified_mass: float = 1000.0,
    standard_mass: float = 1000.0,
    error_percent: float = 0.0,
) -> FillingTrialValue:
    return FillingTrialValue(
        trial_id=trial_id,
        trial_index=trial_index,
        specified_mass=specified_mass,
        standard_mass=standard_mass,
        error_percent=error_percent,
    )


def _repeatability_trials() -> tuple[FillingTrialValue, ...]:
    return (
        _trial("T-3", 3, standard_mass=1006.0, error_percent=0.6),
        _trial("T-1", 1, standard_mass=1004.0, error_percent=0.4),
        _trial("T-2", 2, standard_mass=1005.0, error_percent=0.5),
    )


@pytest.mark.parametrize(
    ("specified_mass", "standard_mass", "expected"),
    [
        (1000.0, 1005.0, 0.5),
        (1000.0, 995.0, -0.5),
        (1000.0, 1000.0, 0.0),
    ],
)
def test_trial_error_uses_specified_mass(
    specified_mass: float,
    standard_mass: float,
    expected: float,
) -> None:
    assert calculate_trial_error(specified_mass, standard_mass) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    "specified_mass",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_trial_error_rejects_invalid_specified_mass(specified_mass: float) -> None:
    with pytest.raises(ValueError, match="(?i)specified mass"):
        calculate_trial_error(specified_mass, 1.0)


@pytest.mark.parametrize(
    "standard_mass",
    [0.0, -1.0, float("nan"), float("inf"), float("-inf")],
)
def test_trial_error_rejects_invalid_standard_mass(standard_mass: float) -> None:
    with pytest.raises(ValueError, match="(?i)standard mass"):
        calculate_trial_error(1.0, standard_mass)


def test_filling_trial_values_are_frozen_and_slotted() -> None:
    trial = _trial("T-1", 1)

    assert not hasattr(trial, "__dict__")
    with pytest.raises(FrozenInstanceError):
        trial.standard_mass = 1001.0  # type: ignore[misc]


def test_repeatability_sorts_consecutive_trials_and_uses_sample_stddev() -> None:
    result = calculate_repeatability(_repeatability_trials())

    assert isinstance(result, FillingRepeatabilityResult)
    assert result.trial_ids == ("T-1", "T-2", "T-3")
    assert result.trial_indexes == (1, 2, 3)
    assert result.errors_percent == (0.4, 0.5, 0.6)
    assert result.mean_error_percent == pytest.approx(0.5)
    assert result.repeatability_stddev_percent == pytest.approx(0.1)


@pytest.mark.parametrize(
    "trials",
    [
        (_trial("T-1", 1), _trial("T-2", 2)),
        (
            _trial("T-1", 1),
            _trial("T-2", 2),
            _trial("T-3", 3),
            _trial("T-4", 4),
        ),
    ],
)
def test_repeatability_requires_exactly_three_trials(
    trials: tuple[FillingTrialValue, ...],
) -> None:
    with pytest.raises(ValueError, match="(?i)exactly 3"):
        calculate_repeatability(trials)


def test_repeatability_requires_consecutive_trial_indexes() -> None:
    trials = (_trial("T-1", 1), _trial("T-3", 3), _trial("T-4", 4))

    with pytest.raises(ValueError, match="(?i)consecutive"):
        calculate_repeatability(trials)


def test_advance_calculates_positive_advance_for_nonconsecutive_trials() -> None:
    trials = (
        _trial("T-1", 1, standard_mass=1002.0, error_percent=0.2),
        _trial("T-3", 3, standard_mass=1004.0, error_percent=0.4),
        _trial("T-5", 5, standard_mass=1006.0, error_percent=0.6),
    )

    result = calculate_advance(trials)

    assert isinstance(result, FillingAdvanceResult)
    assert result.trial_ids == ("T-1", "T-3", "T-5")
    assert result.trial_indexes == (1, 3, 5)
    assert result.standard_masses == (1002.0, 1004.0, 1006.0)
    assert result.specified_mass == 1000.0
    assert result.mean_standard_mass == pytest.approx(1004.0)
    assert result.advance_mass == pytest.approx(4.0)
    assert result.corrected_target_mass == pytest.approx(996.0)


def test_advance_allows_negative_advance() -> None:
    trials = (
        _trial("T-1", 1, standard_mass=998.0, error_percent=-0.2),
        _trial("T-2", 2, standard_mass=997.0, error_percent=-0.3),
        _trial("T-3", 3, standard_mass=999.0, error_percent=-0.1),
    )

    result = calculate_advance(trials)

    assert result.mean_standard_mass == pytest.approx(998.0)
    assert result.advance_mass == pytest.approx(-2.0)
    assert result.corrected_target_mass == pytest.approx(1002.0)


def test_advance_requires_at_least_three_trials() -> None:
    with pytest.raises(ValueError, match="(?i)at least 3"):
        calculate_advance((_trial("T-1", 1), _trial("T-2", 2)))


def test_advance_requires_one_specified_mass() -> None:
    trials = (
        _trial("T-1", 1, specified_mass=1000.0),
        _trial("T-2", 2, specified_mass=1001.0),
        _trial("T-3", 3, specified_mass=1000.0),
    )

    with pytest.raises(ValueError, match="(?i)share one specified mass"):
        calculate_advance(trials)


@pytest.mark.parametrize("standard_mass", [200.0, 201.0])
def test_advance_rejects_nonpositive_corrected_target_mass(
    standard_mass: float,
) -> None:
    trials = tuple(
        _trial(
            f"T-{index}",
            index,
            specified_mass=100.0,
            standard_mass=standard_mass,
            error_percent=100.0,
        )
        for index in range(1, 4)
    )

    with pytest.raises(ValueError, match="(?i)corrected target mass"):
        calculate_advance(trials)


@pytest.mark.parametrize(
    ("field_name", "invalid_value", "message"),
    [
        ("specified_mass", 0.0, "specified mass"),
        ("specified_mass", -1.0, "specified mass"),
        ("specified_mass", float("nan"), "specified mass"),
        ("specified_mass", float("inf"), "specified mass"),
        ("standard_mass", 0.0, "standard mass"),
        ("standard_mass", -1.0, "standard mass"),
        ("standard_mass", float("nan"), "standard mass"),
        ("standard_mass", float("inf"), "standard mass"),
    ],
)
def test_trial_collections_reject_invalid_masses(
    field_name: str,
    invalid_value: float,
    message: str,
) -> None:
    trials = list(_repeatability_trials())
    trials[0] = replace(trials[0], **{field_name: invalid_value})

    with pytest.raises(ValueError, match=f"(?i){message}"):
        calculate_repeatability(trials)
    with pytest.raises(ValueError, match=f"(?i){message}"):
        calculate_advance(trials)


@pytest.mark.parametrize("error_percent", [float("nan"), float("inf"), float("-inf")])
def test_trial_collections_reject_nonfinite_error_percent(
    error_percent: float,
) -> None:
    trials = list(_repeatability_trials())
    trials[0] = replace(trials[0], error_percent=error_percent)

    with pytest.raises(ValueError, match="(?i)error percent"):
        calculate_repeatability(trials)
    with pytest.raises(ValueError, match="(?i)error percent"):
        calculate_advance(trials)


@pytest.mark.parametrize("trial_index", [0, -1, 1.5, True])
def test_trial_collections_reject_invalid_trial_index(
    trial_index: object,
) -> None:
    trials = list(_repeatability_trials())
    trials[0] = replace(trials[0], trial_index=trial_index)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="(?i)trial index"):
        calculate_repeatability(trials)
    with pytest.raises(ValueError, match="(?i)trial index"):
        calculate_advance(trials)


def test_trial_collections_reject_duplicate_trial_ids() -> None:
    trials = list(_repeatability_trials())
    trials[0] = replace(trials[0], trial_id=trials[1].trial_id)

    with pytest.raises(ValueError, match="(?i)trial ID"):
        calculate_repeatability(trials)
    with pytest.raises(ValueError, match="(?i)trial ID"):
        calculate_advance(trials)

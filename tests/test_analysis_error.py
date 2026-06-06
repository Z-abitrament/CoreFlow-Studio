from __future__ import annotations

import pytest

from coreflow.analysis import (
    ErrorAnalysisConfig,
    ErrorPoint,
    NearZeroPolicy,
    analyze_error,
)


def test_error_analysis_calculates_point_and_summary_metrics() -> None:
    result = analyze_error(
        [
            ErrorPoint(reference=10.0, measured=10.2, label="p1"),
            ErrorPoint(reference=20.0, measured=19.8, label="p2"),
        ]
    )

    assert result.points[0].absolute_error == pytest.approx(0.2)
    assert result.points[0].relative_error == pytest.approx(0.02)
    assert result.points[1].absolute_error == pytest.approx(-0.2)
    assert result.summary_metrics["point_count"] == 2.0
    assert result.summary_metrics["mean_absolute_error"] == pytest.approx(0.0)
    assert result.summary_metrics["max_absolute_error"] == pytest.approx(0.2)
    assert result.summary_metrics["max_abs_relative_error"] == pytest.approx(0.02)


def test_error_analysis_near_zero_absolute_only_policy() -> None:
    result = analyze_error(
        [ErrorPoint(reference=0.0, measured=0.1)],
        ErrorAnalysisConfig(near_zero_policy=NearZeroPolicy.ABSOLUTE_ONLY),
    )

    assert result.points[0].absolute_error == pytest.approx(0.1)
    assert result.points[0].relative_error is None
    assert result.summary_metrics["mean_relative_error"] is None


def test_error_analysis_near_zero_epsilon_policy() -> None:
    result = analyze_error(
        [ErrorPoint(reference=0.0, measured=0.1)],
        ErrorAnalysisConfig(
            near_zero_policy=NearZeroPolicy.EPSILON,
            near_zero_epsilon=0.01,
        ),
    )

    assert result.points[0].relative_error == pytest.approx(10.0)


def test_error_analysis_near_zero_raise_policy() -> None:
    with pytest.raises(ZeroDivisionError):
        analyze_error(
            [ErrorPoint(reference=0.0, measured=0.1, label="zero")],
            ErrorAnalysisConfig(near_zero_policy=NearZeroPolicy.RAISE),
        )


def test_error_analysis_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        analyze_error([])

"""Error analysis calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NearZeroPolicy(StrEnum):
    """Policy for relative error when reference is zero or near zero."""

    RAISE = "raise"
    ABSOLUTE_ONLY = "absolute_only"
    EPSILON = "epsilon"


@dataclass(frozen=True, slots=True)
class ErrorAnalysisConfig:
    """Configuration for reference-vs-measured error calculations."""

    near_zero_policy: NearZeroPolicy = NearZeroPolicy.ABSOLUTE_ONLY
    near_zero_epsilon: float = 1e-9


@dataclass(frozen=True, slots=True)
class ErrorPoint:
    """One reference and measured value pair."""

    reference: float
    measured: float
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ErrorPointResult:
    """Calculated error metrics for one point."""

    reference: float
    measured: float
    absolute_error: float
    relative_error: float | None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ErrorAnalysisResult:
    """Summary and point-level error metrics."""

    points: tuple[ErrorPointResult, ...]
    summary_metrics: dict[str, float | None] = field(default_factory=dict)


def analyze_error(
    points: tuple[ErrorPoint, ...] | list[ErrorPoint],
    config: ErrorAnalysisConfig | None = None,
) -> ErrorAnalysisResult:
    """Calculate absolute, relative, and summary error metrics."""

    if not points:
        raise ValueError("Error analysis requires at least one point.")
    config = config or ErrorAnalysisConfig()
    results = tuple(_analyze_point(point, config) for point in points)
    absolute_errors = tuple(result.absolute_error for result in results)
    relative_errors = tuple(
        result.relative_error
        for result in results
        if result.relative_error is not None
    )
    summary: dict[str, float | None] = {
        "point_count": float(len(results)),
        "mean_absolute_error": sum(absolute_errors) / len(absolute_errors),
        "max_absolute_error": max(abs(error) for error in absolute_errors),
        "mean_relative_error": (
            sum(relative_errors) / len(relative_errors) if relative_errors else None
        ),
        "max_abs_relative_error": (
            max(abs(error) for error in relative_errors) if relative_errors else None
        ),
    }
    return ErrorAnalysisResult(points=results, summary_metrics=summary)


def _analyze_point(
    point: ErrorPoint,
    config: ErrorAnalysisConfig,
) -> ErrorPointResult:
    absolute_error = point.measured - point.reference
    denominator = point.reference
    if abs(denominator) <= config.near_zero_epsilon:
        if config.near_zero_policy is NearZeroPolicy.RAISE:
            raise ZeroDivisionError(
                f"Reference value near zero for point {point.label or ''}".strip()
            )
        if config.near_zero_policy is NearZeroPolicy.ABSOLUTE_ONLY:
            relative_error = None
        else:
            denominator = config.near_zero_epsilon
            relative_error = absolute_error / denominator
    else:
        relative_error = absolute_error / denominator
    return ErrorPointResult(
        reference=point.reference,
        measured=point.measured,
        absolute_error=absolute_error,
        relative_error=relative_error,
        label=point.label,
    )

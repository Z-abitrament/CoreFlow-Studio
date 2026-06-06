"""Stability analysis calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean, pstdev

from coreflow.analysis.timeseries import TimeSeriesSample


@dataclass(frozen=True, slots=True)
class StabilityAnalysisConfig:
    """Thresholds for stability pass/fail decisions."""

    max_range: float | None = None
    max_stddev: float | None = None
    max_dropout_count: int | None = None


@dataclass(frozen=True, slots=True)
class StabilityAnalysisResult:
    """Short-term stability, drift, noise, and dropout metrics."""

    sample_count: int
    valid_sample_count: int
    dropout_count: int
    mean: float | None
    stddev: float | None
    value_range: float | None
    drift_estimate: float | None
    pass_fail_decision: str | None
    summary_metrics: dict[str, float | int | None] = field(default_factory=dict)


def analyze_stability(
    samples: tuple[TimeSeriesSample, ...] | list[TimeSeriesSample],
    config: StabilityAnalysisConfig | None = None,
) -> StabilityAnalysisResult:
    """Calculate stability metrics from persisted or in-memory samples."""

    if not samples:
        raise ValueError("Stability analysis requires at least one sample.")
    config = config or StabilityAnalysisConfig()
    values = [sample.value for sample in samples if sample.value is not None]
    dropout_count = len(samples) - len(values)
    if not values:
        return StabilityAnalysisResult(
            sample_count=len(samples),
            valid_sample_count=0,
            dropout_count=dropout_count,
            mean=None,
            stddev=None,
            value_range=None,
            drift_estimate=None,
            pass_fail_decision="failed",
            summary_metrics={
                "sample_count": len(samples),
                "valid_sample_count": 0,
                "dropout_count": dropout_count,
            },
        )

    calculated_mean = mean(values)
    calculated_stddev = pstdev(values) if len(values) > 1 else 0.0
    value_range = max(values) - min(values)
    drift_estimate = values[-1] - values[0] if len(values) > 1 else 0.0
    decision = _pass_fail(config, value_range, calculated_stddev, dropout_count)
    summary = {
        "sample_count": len(samples),
        "valid_sample_count": len(values),
        "dropout_count": dropout_count,
        "mean": calculated_mean,
        "stddev": calculated_stddev,
        "range": value_range,
        "drift_estimate": drift_estimate,
    }
    return StabilityAnalysisResult(
        sample_count=len(samples),
        valid_sample_count=len(values),
        dropout_count=dropout_count,
        mean=calculated_mean,
        stddev=calculated_stddev,
        value_range=value_range,
        drift_estimate=drift_estimate,
        pass_fail_decision=decision,
        summary_metrics=summary,
    )


def _pass_fail(
    config: StabilityAnalysisConfig,
    value_range: float,
    stddev: float,
    dropout_count: int,
) -> str | None:
    failed = False
    if config.max_range is not None and value_range > config.max_range:
        failed = True
    if config.max_stddev is not None and stddev > config.max_stddev:
        failed = True
    if (
        config.max_dropout_count is not None
        and dropout_count > config.max_dropout_count
    ):
        failed = True
    if (
        config.max_range is None
        and config.max_stddev is None
        and config.max_dropout_count is None
    ):
        return None
    return "failed" if failed else "passed"

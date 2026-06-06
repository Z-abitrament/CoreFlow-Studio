from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coreflow.analysis import (
    StabilityAnalysisConfig,
    TimeSeriesSample,
    analyze_stability,
    load_mass_flow_csv,
)


def _samples(values: list[float | None]) -> tuple[TimeSeriesSample, ...]:
    start = datetime(2026, 6, 6, 8, 0, tzinfo=UTC)
    return tuple(
        TimeSeriesSample(
            timestamp=start + timedelta(seconds=index),
            value=value,
        )
        for index, value in enumerate(values)
    )


def test_stability_analysis_calculates_summary_metrics() -> None:
    result = analyze_stability(
        _samples([10.0, 10.1, 10.2, 10.3]),
        StabilityAnalysisConfig(max_range=0.5, max_stddev=0.2),
    )

    assert result.sample_count == 4
    assert result.valid_sample_count == 4
    assert result.dropout_count == 0
    assert result.mean == pytest.approx(10.15)
    assert result.value_range == pytest.approx(0.3)
    assert result.drift_estimate == pytest.approx(0.3)
    assert result.pass_fail_decision == "passed"
    assert result.summary_metrics["stddev"] == pytest.approx(0.1118033989)


def test_stability_analysis_detects_drift_and_dropouts() -> None:
    result = analyze_stability(
        _samples([1.0, None, 1.2, 1.4, None, 1.8]),
        StabilityAnalysisConfig(max_range=0.5, max_dropout_count=0),
    )

    assert result.dropout_count == 2
    assert result.value_range == pytest.approx(0.8)
    assert result.drift_estimate == pytest.approx(0.8)
    assert result.pass_fail_decision == "failed"


def test_stability_analysis_handles_all_dropouts() -> None:
    result = analyze_stability(
        _samples([None, None]),
        StabilityAnalysisConfig(max_dropout_count=0),
    )

    assert result.valid_sample_count == 0
    assert result.dropout_count == 2
    assert result.mean is None
    assert result.pass_fail_decision == "failed"


def test_stability_analysis_recomputes_from_persisted_csv(tmp_path) -> None:
    csv_path = tmp_path / "samples.csv"
    csv_path.write_text(
        "\n".join(
            [
                "captured_at,mass_flow,volume_flow,density,temperature,status_flags,source_channel",
                "2026-06-06T08:00:00+00:00,10.0,,,,,SIM",
                "2026-06-06T08:00:01+00:00,10.2,,,,,SIM",
                "2026-06-06T08:00:02+00:00,10.4,,,,,SIM",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_mass_flow_csv(csv_path)
    result = analyze_stability(
        loaded,
        StabilityAnalysisConfig(max_range=0.5),
    )

    assert result.valid_sample_count == 3
    assert result.mean == pytest.approx(10.2)
    assert result.value_range == pytest.approx(0.4)
    assert result.pass_fail_decision == "passed"


def test_stability_analysis_rejects_empty_input() -> None:
    with pytest.raises(ValueError):
        analyze_stability([])

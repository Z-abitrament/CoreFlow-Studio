from __future__ import annotations

import math

import pytest

from coreflow.analysis.zero_monitor import (
    ZERO_MONITOR_CRITERIA,
    IndependentCandidateSelector,
    ZeroMonitorAnalysisConfig,
    ZeroMonitorAnalyzer,
    ZeroMonitorCandidate,
    ZeroMonitorState,
    ZeroMonitorThreshold,
)


def _candidate(index: int, value: float = 1.0, *, segment: int = 1) -> ZeroMonitorCandidate:
    return ZeroMonitorCandidate(
        sequence=(100 + index * 6) & 0xFFFF,
        device_tick_ms=index * 600,
        continuous_segment=segment,
        live_zero_600ms=value,
        trim_std_600ms=0.1,
        trim_range_600ms=0.2,
        raw_p2p_600ms=0.3,
    )


def _test_config(*, long_window_s: float = 12.0, stable_s: float = 0.0):
    thresholds = {
        name: ZeroMonitorThreshold(
            enabled=True,
            limit=10.0,
            source="deterministic test",
            unit="us",
            test_only=True,
        )
        for name in ZERO_MONITOR_CRITERIA
    }
    return ZeroMonitorAnalysisConfig(
        long_window_s=long_window_s,
        minimum_stable_duration_s=stable_s,
        thresholds=thresholds,
    )


def test_production_defaults_are_diagnostic_only() -> None:
    config = ZeroMonitorAnalysisConfig.production_default()

    assert set(config.thresholds) == set(ZERO_MONITOR_CRITERIA)
    assert all(item.enabled and item.limit is None for item in config.thresholds.values())
    assert config.minimum_stable_duration_s is None
    assert config.status == "pending_bench_approval"


@pytest.mark.parametrize("value", [11.999, 86400.001, math.inf, math.nan])
def test_long_window_rejects_values_outside_inclusive_bounds(value: float) -> None:
    with pytest.raises(ValueError):
        ZeroMonitorAnalysisConfig(long_window_s=value)


@pytest.mark.parametrize("value", [12.0, 86400.0])
def test_long_window_accepts_inclusive_bounds(value: float) -> None:
    assert ZeroMonitorAnalysisConfig(long_window_s=value).long_window_s == value


def test_candidate_selector_uses_sequence_based_non_overlapping_windows() -> None:
    selector = IndependentCandidateSelector(publications_per_candidate=6)

    selected = [
        sequence
        for sequence in (100, 101, 105, 106, 112, 118)
        if selector.accept(sequence)
    ]

    assert selected == [100, 106, 112, 118]
    selector.reset()
    assert selector.accept(400)


def test_analyzer_requires_window_span_and_twenty_candidates() -> None:
    analyzer = ZeroMonitorAnalyzer(_test_config(long_window_s=12.0))

    result = None
    for index in range(20):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )

    assert result is not None
    assert result.state is ZeroMonitorState.NOT_READY
    result = analyzer.add_candidate(
        _candidate(20),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    assert result.state is ZeroMonitorState.STABLE
    assert result.metrics.candidate_count == 21
    assert result.metrics.window_span_s == pytest.approx(12.0)


def test_analyzer_uses_ddof_one_linear_percentiles_and_centered_slope() -> None:
    analyzer = ZeroMonitorAnalyzer(_test_config(long_window_s=12.0))
    result = None
    for index in range(21):
        result = analyzer.add_candidate(
            _candidate(index, value=float(index)),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )

    assert result is not None
    assert result.metrics.long_mean == pytest.approx(10.0)
    assert result.metrics.repeat_std == pytest.approx(6.2048368229954285)
    assert result.metrics.long_range == pytest.approx(20.0)
    assert result.metrics.long_p95_p5 == pytest.approx(18.0)
    assert result.metrics.long_slope == pytest.approx(1.0 / 0.6)
    assert result.metrics.trend_span == pytest.approx(20.0)
    assert result.metrics.max_step == pytest.approx(1.0)
    assert result.metrics.adjacent_difference_rms == pytest.approx(math.sqrt(0.5))


def test_threshold_equality_passes_without_hidden_epsilon() -> None:
    config = _test_config(long_window_s=12.0)
    thresholds = dict(config.thresholds)
    thresholds["short_std"] = ZeroMonitorThreshold(
        limit=0.1,
        source="boundary",
        unit="us",
        test_only=True,
    )
    analyzer = ZeroMonitorAnalyzer(
        ZeroMonitorAnalysisConfig(
            long_window_s=12.0,
            minimum_stable_duration_s=0.0,
            thresholds=thresholds,
        )
    )
    result = None
    for index in range(21):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )
    assert result is not None
    assert result.state is ZeroMonitorState.STABLE


def test_missing_threshold_zero_context_and_unverified_order_block_stable() -> None:
    analyzer = ZeroMonitorAnalyzer(ZeroMonitorAnalysisConfig.production_default())
    result = None
    for index in range(51):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=False,
            byte_order_verified=False,
        )

    assert result is not None
    assert result.state is ZeroMonitorState.EVALUATING
    assert "THRESHOLD_CONFIG_INCOMPLETE" in result.reason_codes
    assert "ZERO_FLOW_UNCONFIRMED" in result.reason_codes
    assert "BYTE_ORDER_UNVERIFIED" in result.reason_codes


def test_unit_mismatch_blocks_decision() -> None:
    config = _test_config()
    thresholds = dict(config.thresholds)
    thresholds["max_step"] = ZeroMonitorThreshold(
        limit=1.0,
        source="bad unit",
        unit="ms",
        test_only=True,
    )
    analyzer = ZeroMonitorAnalyzer(
        ZeroMonitorAnalysisConfig(
            long_window_s=12.0,
            minimum_stable_duration_s=0.0,
            thresholds=thresholds,
        )
    )
    result = None
    for index in range(21):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )
    assert result is not None
    assert result.state is ZeroMonitorState.EVALUATING
    assert "THRESHOLD_UNIT_MISMATCH:max_step" in result.reason_codes


def test_stable_duration_uses_device_time_and_resets_after_violation() -> None:
    analyzer = ZeroMonitorAnalyzer(_test_config(long_window_s=12.0, stable_s=1.2))
    result = None
    for index in range(23):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )
    assert result is not None
    assert result.state is ZeroMonitorState.STABLE
    assert result.stable_duration_s == pytest.approx(1.2)

    result = analyzer.add_candidate(
        _candidate(23, value=100.0),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    assert result.state is ZeroMonitorState.UNSTABLE
    assert result.stable_duration_s == 0.0


def test_offset_is_advisory_and_does_not_block_stable() -> None:
    config = _test_config()
    config = ZeroMonitorAnalysisConfig(
        long_window_s=config.long_window_s,
        minimum_stable_duration_s=0.0,
        thresholds=config.thresholds,
        offset_limit=0.5,
        offset_limit_source="test",
    )
    analyzer = ZeroMonitorAnalyzer(config)
    result = None
    for index in range(21):
        result = analyzer.add_candidate(
            _candidate(index, value=1.0),
            zero_flow_confirmed=True,
            byte_order_verified=True,
            official_zero_offset=0.0,
        )
    assert result is not None
    assert result.state is ZeroMonitorState.STABLE
    assert "OFFSET_EXCEEDED" in result.advisory_codes


def test_non_candidate_snapshot_still_checks_current_short_metrics() -> None:
    config = _test_config()
    thresholds = dict(config.thresholds)
    thresholds["short_std"] = ZeroMonitorThreshold(
        limit=0.5,
        source="test",
        unit="us",
        test_only=True,
    )
    analyzer = ZeroMonitorAnalyzer(
        ZeroMonitorAnalysisConfig(
            long_window_s=12.0,
            minimum_stable_duration_s=0.0,
            thresholds=thresholds,
        )
    )
    result = None
    for index in range(21):
        result = analyzer.add_candidate(
            _candidate(index),
            zero_flow_confirmed=True,
            byte_order_verified=True,
        )
    assert result is not None and result.state is ZeroMonitorState.STABLE

    result = analyzer.evaluate_snapshot(
        ZeroMonitorCandidate(
            sequence=221,
            device_tick_ms=12100,
            continuous_segment=1,
            live_zero_600ms=1.0,
            trim_std_600ms=0.6,
            trim_range_600ms=0.2,
            raw_p2p_600ms=0.3,
        ),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )

    assert result.state is ZeroMonitorState.UNSTABLE
    assert "CRITERION_EXCEEDED:short_std" in result.reason_codes

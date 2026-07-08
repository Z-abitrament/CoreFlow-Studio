"""Pulse counting, switch-period windowing, and calibration helpers."""

from __future__ import annotations

from pathlib import Path
from math import floor
from statistics import mean

from coreflow.pulse_counter.dsview_csv import read_dsview_csv
from coreflow.pulse_counter.models import (
    DsviewCsvCapture,
    MeasuredPulseErrorResult,
    MeasuredPulseKFactorResult,
    PulseAnalysisConfig,
    PulseAnalysisResult,
    PulseEdge,
    PulseFrequencySegment,
    PulseWindow,
)


def analyze_dsview_csv(
    path: str | Path,
    config: PulseAnalysisConfig | None = None,
) -> PulseAnalysisResult:
    """Read and analyze one DSView CSV export."""

    capture = read_dsview_csv(path)
    return analyze_capture(capture, config or PulseAnalysisConfig())


def analyze_capture(
    capture: DsviewCsvCapture,
    config: PulseAnalysisConfig | None = None,
) -> PulseAnalysisResult:
    """Analyze pulse edges from an already parsed DSView capture."""

    resolved = config or PulseAnalysisConfig()
    if resolved.channel not in capture.channels:
        raise ValueError(f"Channel {resolved.channel} is not present in capture.")
    if resolved.pulse_value <= 0:
        raise ValueError("Pulse value must be positive.")
    if resolved.min_segment_pulses < 1:
        raise ValueError("Minimum segment pulse count must be at least one.")
    edges = extract_edges(capture, resolved)
    windows = build_rate_windows(edges, resolved)
    frequency_segments = detect_frequency_segments(edges, resolved)
    pulse_count = len(edges)
    total_quantity = pulse_count * resolved.pulse_value
    started_at_s = edges[0].time_s if edges else None
    ended_at_s = edges[-1].time_s if edges else None
    duration_s = (
        (ended_at_s - started_at_s) if started_at_s is not None and ended_at_s is not None else 0.0
    )
    mean_rate = total_quantity / duration_s if duration_s > 0 else 0.0
    return PulseAnalysisResult(
        metadata=capture.metadata,
        config=resolved,
        edges=edges,
        windows=windows,
        frequency_segments=frequency_segments,
        pulse_count=pulse_count,
        total_quantity=total_quantity,
        started_at_s=started_at_s,
        ended_at_s=ended_at_s,
        duration_s=duration_s,
        mean_rate=mean_rate,
        boundary_pulse_count=sum(window.boundary_pulse_count for window in windows),
    )


def extract_edges(
    capture: DsviewCsvCapture,
    config: PulseAnalysisConfig,
) -> tuple[PulseEdge, ...]:
    """Extract configured pulse edges from a DSView change-point capture."""

    previous: int | None = None
    edges: list[PulseEdge] = []
    for sample in capture.samples:
        value = sample.states[config.channel]
        if previous is None:
            previous = value
            continue
        if previous == 0 and value == 1 and config.edge in ("rising", "both"):
            edges.append(PulseEdge(sample.time_s, config.channel, "rising"))
        elif previous == 1 and value == 0 and config.edge in ("falling", "both"):
            edges.append(PulseEdge(sample.time_s, config.channel, "falling"))
        previous = value
    return tuple(edges)


def build_rate_windows(
    edges: tuple[PulseEdge, ...],
    config: PulseAnalysisConfig,
) -> tuple[PulseWindow, ...]:
    """Aggregate pulses into fixed switch-period windows for plotting."""

    if not edges:
        return ()
    period_s = config.switch_period_s
    tolerance_s = config.resolved_boundary_tolerance_s
    start_index = floor((edges[0].time_s - config.window_origin_s) / period_s)
    start_s = config.window_origin_s + start_index * period_s
    end_s = edges[-1].time_s
    window_count = int((end_s - start_s) // period_s) + 1
    windows: list[PulseWindow] = []
    cumulative = 0.0
    for index in range(window_count):
        window_start = start_s + index * period_s
        window_end = window_start + period_s
        selected = [
            edge
            for edge in edges
            if window_start <= edge.time_s < window_end
            or (index == window_count - 1 and edge.time_s == end_s)
        ]
        pulse_count = len(selected)
        quantity = pulse_count * config.pulse_value
        cumulative += quantity
        boundary_count = sum(
            1
            for edge in selected
            if _is_near_window_boundary(
                edge.time_s,
                start_s=start_s,
                period_s=period_s,
                tolerance_s=tolerance_s,
            )
        )
        windows.append(
            PulseWindow(
                index=index,
                start_s=window_start,
                end_s=window_end,
                pulse_count=pulse_count,
                quantity=quantity,
                rate=quantity / period_s,
                cumulative_quantity=cumulative,
                boundary_pulse_count=boundary_count,
            )
        )
    return tuple(windows)


def detect_frequency_segments(
    edges: tuple[PulseEdge, ...],
    config: PulseAnalysisConfig,
) -> tuple[PulseFrequencySegment, ...]:
    """Infer frequency segments inside fixed switch-period windows."""

    if not edges:
        return ()
    period_s = config.switch_period_s
    result: list[PulseFrequencySegment] = []
    start_s = config.window_origin_s
    segment_index = 0
    current_window = int((edges[0].time_s - start_s) // period_s)
    current_edges: list[PulseEdge] = []
    for edge in edges:
        window = int((edge.time_s - start_s) // period_s)
        if window != current_window:
            _append_segment_if_long_enough(
                result,
                segment_index=segment_index,
                edges=current_edges,
                config=config,
            )
            if len(current_edges) >= config.min_segment_pulses:
                segment_index += 1
            current_edges = []
            current_window = window
        current_edges.append(edge)
    _append_segment_if_long_enough(
        result,
        segment_index=segment_index,
        edges=current_edges,
        config=config,
    )
    return tuple(result)


def calculate_measured_pulse_error(
    *,
    measured_quantity: float,
    standard_quantity: float,
) -> MeasuredPulseErrorResult:
    """Calculate percent error when pulse quantity is the measured value."""

    if standard_quantity <= 0:
        raise ValueError("Standard quantity must be positive.")
    return MeasuredPulseErrorResult(
        measured_quantity=measured_quantity,
        standard_quantity=standard_quantity,
        percent_error=(measured_quantity - standard_quantity) / standard_quantity * 100.0,
    )


def calculate_measured_pulse_k_factor(
    *,
    current_k_factor: float,
    measured_quantity: float,
    standard_quantity: float,
) -> MeasuredPulseKFactorResult:
    """Preview corrected K factor when pulse quantity is measured output."""

    if measured_quantity == 0:
        raise ValueError("Measured pulse quantity must be non-zero.")
    if standard_quantity <= 0:
        raise ValueError("Standard quantity must be positive.")
    return MeasuredPulseKFactorResult(
        current_k_factor=current_k_factor,
        measured_quantity=measured_quantity,
        standard_quantity=standard_quantity,
        corrected_k_factor=current_k_factor / measured_quantity * standard_quantity,
    )


def _append_segment_if_long_enough(
    result: list[PulseFrequencySegment],
    *,
    segment_index: int,
    edges: list[PulseEdge],
    config: PulseAnalysisConfig,
) -> None:
    if len(edges) < config.min_segment_pulses:
        return
    periods = [
        current.time_s - previous.time_s
        for previous, current in zip(edges, edges[1:])
        if current.time_s > previous.time_s
    ]
    mean_period_s = mean(periods) if periods else None
    mean_frequency_hz = 1.0 / mean_period_s if mean_period_s and mean_period_s > 0 else None
    quantity = len(edges) * config.pulse_value
    mean_rate = (
        mean_frequency_hz * config.pulse_value
        if mean_frequency_hz is not None
        else None
    )
    result.append(
        PulseFrequencySegment(
            index=segment_index,
            start_s=edges[0].time_s,
            end_s=edges[-1].time_s,
            pulse_count=len(edges),
            quantity=quantity,
            mean_period_s=mean_period_s,
            mean_frequency_hz=mean_frequency_hz,
            mean_rate=mean_rate,
            boundary_pulse_count=0,
        )
    )


def _is_near_window_boundary(
    time_s: float,
    *,
    start_s: float,
    period_s: float,
    tolerance_s: float,
) -> bool:
    if tolerance_s == 0:
        return False
    offset = (time_s - start_s) % period_s
    return offset <= tolerance_s or period_s - offset <= tolerance_s

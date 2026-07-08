"""Pulse-counter parsing and analysis helpers."""

from coreflow.pulse_counter.analysis import (
    analyze_capture,
    analyze_dsview_csv,
    build_rate_windows,
    calculate_measured_pulse_error,
    calculate_measured_pulse_k_factor,
    detect_frequency_segments,
    extract_edges,
)
from coreflow.pulse_counter.dsview_csv import read_dsview_csv
from coreflow.pulse_counter.models import (
    DsviewCsvCapture,
    DsviewCsvMetadata,
    DsviewCsvSample,
    MeasuredPulseErrorResult,
    MeasuredPulseKFactorResult,
    PulseAnalysisConfig,
    PulseAnalysisResult,
    PulseEdge,
    PulseFrequencySegment,
    PulseWindow,
)

__all__ = [
    "DsviewCsvCapture",
    "DsviewCsvMetadata",
    "DsviewCsvSample",
    "MeasuredPulseErrorResult",
    "MeasuredPulseKFactorResult",
    "PulseAnalysisConfig",
    "PulseAnalysisResult",
    "PulseEdge",
    "PulseFrequencySegment",
    "PulseWindow",
    "analyze_capture",
    "analyze_dsview_csv",
    "build_rate_windows",
    "calculate_measured_pulse_error",
    "calculate_measured_pulse_k_factor",
    "detect_frequency_segments",
    "extract_edges",
    "read_dsview_csv",
]

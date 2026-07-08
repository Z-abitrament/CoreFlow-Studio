"""Pulse-counter data models for exported and future live edge streams."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


PulseEdgeKind = Literal["rising", "falling"]
PulseEdgeSelection = Literal["rising", "falling", "both"]
PulseSourceRole = Literal["measured", "reference"]


@dataclass(frozen=True, slots=True)
class PulseAnalysisConfig:
    """Configurable pulse counting and windowing assumptions."""

    channel: str = "0"
    edge: PulseEdgeSelection = "rising"
    pulse_value: float = 0.05
    unit: str = "g"
    switch_frequency_hz: float = 100.0
    window_origin_s: float = 0.0
    boundary_tolerance_s: float | None = None
    source_role: PulseSourceRole = "measured"
    min_segment_pulses: int = 3

    @property
    def switch_period_s(self) -> float:
        if self.switch_frequency_hz <= 0:
            raise ValueError("Switch frequency must be positive.")
        return 1.0 / self.switch_frequency_hz

    @property
    def resolved_boundary_tolerance_s(self) -> float:
        if self.boundary_tolerance_s is not None:
            if self.boundary_tolerance_s < 0:
                raise ValueError("Boundary tolerance cannot be negative.")
            return self.boundary_tolerance_s
        return self.switch_period_s * 0.02


@dataclass(frozen=True, slots=True)
class DsviewCsvMetadata:
    """Metadata parsed from DSView/libsigrok4DSL CSV comments."""

    source_path: Path | None = None
    generator: str | None = None
    channel_count: int | None = None
    enabled_channel_count: int | None = None
    sample_rate_hz: float | None = None
    sample_count: str | None = None
    comments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DsviewCsvSample:
    """One exported DSView change-point row."""

    time_s: float
    states: dict[str, int]


@dataclass(frozen=True, slots=True)
class DsviewCsvCapture:
    """DSView change-point capture with parsed metadata and channel states."""

    metadata: DsviewCsvMetadata
    channels: tuple[str, ...]
    samples: tuple[DsviewCsvSample, ...]


@dataclass(frozen=True, slots=True)
class PulseEdge:
    """One selected pulse edge."""

    time_s: float
    channel: str
    edge: PulseEdgeKind


@dataclass(frozen=True, slots=True)
class PulseWindow:
    """Pulse count and rate within one configured switch period."""

    index: int
    start_s: float
    end_s: float
    pulse_count: int
    quantity: float
    rate: float
    cumulative_quantity: float
    boundary_pulse_count: int = 0


@dataclass(frozen=True, slots=True)
class PulseFrequencySegment:
    """One contiguous frequency segment inferred from pulse periods."""

    index: int
    start_s: float
    end_s: float
    pulse_count: int
    quantity: float
    mean_period_s: float | None
    mean_frequency_hz: float | None
    mean_rate: float | None
    boundary_pulse_count: int = 0


@dataclass(frozen=True, slots=True)
class PulseAnalysisResult:
    """Complete pulse analysis output for plotting and calibration preview."""

    metadata: DsviewCsvMetadata
    config: PulseAnalysisConfig
    edges: tuple[PulseEdge, ...]
    windows: tuple[PulseWindow, ...]
    frequency_segments: tuple[PulseFrequencySegment, ...]
    pulse_count: int
    total_quantity: float
    started_at_s: float | None = None
    ended_at_s: float | None = None
    duration_s: float = 0.0
    mean_rate: float = 0.0
    boundary_pulse_count: int = 0


@dataclass(frozen=True, slots=True)
class MeasuredPulseErrorResult:
    """Error result when pulse quantity is the measured device output."""

    measured_quantity: float
    standard_quantity: float
    percent_error: float


@dataclass(frozen=True, slots=True)
class MeasuredPulseKFactorResult:
    """K-factor preview when pulse quantity is the measured device output."""

    current_k_factor: float
    measured_quantity: float
    standard_quantity: float
    corrected_k_factor: float


@dataclass(frozen=True, slots=True)
class _MutableSegment:
    """Internal helper while accumulating frequency segments."""

    start_index: int
    periods: list[float] = field(default_factory=list)

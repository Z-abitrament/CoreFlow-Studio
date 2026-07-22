"""Pure calculations for the Modbus DSP zero-monitor workflow."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite, sqrt
from typing import Any, Mapping

import numpy as np


ZERO_MONITOR_CRITERIA = (
    "short_std",
    "short_range",
    "raw_p2p",
    "repeat_std",
    "long_range",
    "trend_span",
    "max_step",
)
ZERO_MONITOR_VALUE_UNIT = "us"
ZERO_MONITOR_SLOPE_UNIT = "us/s"
MIN_LONG_WINDOW_S = 12.0
MAX_LONG_WINDOW_S = 86400.0
MIN_INDEPENDENT_CANDIDATES = 20


class ZeroMonitorState(StrEnum):
    """Operator-facing zero-monitor decision state."""

    NOT_READY = "NOT_READY"
    DATA_GAP = "DATA_GAP"
    EVALUATING = "EVALUATING"
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE"


@dataclass(frozen=True, slots=True)
class ZeroMonitorThreshold:
    """One explicitly enabled or disabled stability criterion."""

    enabled: bool = True
    limit: float | None = None
    source: str = ""
    unit: str = ZERO_MONITOR_VALUE_UNIT
    test_only: bool = False

    def configuration_error(self, name: str) -> str | None:
        if not self.enabled:
            return None
        if self.limit is None or not isfinite(self.limit) or self.limit < 0:
            return f"THRESHOLD_CONFIG_INCOMPLETE:{name}"
        if not self.source.strip():
            return f"THRESHOLD_CONFIG_INCOMPLETE:{name}"
        if self.unit != ZERO_MONITOR_VALUE_UNIT:
            return f"THRESHOLD_UNIT_MISMATCH:{name}"
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "limit": self.limit,
            "source": self.source,
            "unit": self.unit,
            "test_only": self.test_only,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ZeroMonitorThreshold:
        limit_value = value.get("limit")
        return cls(
            enabled=bool(value.get("enabled", True)),
            limit=None if limit_value in (None, "") else float(limit_value),
            source=str(value.get("source") or ""),
            unit=str(value.get("unit") or ZERO_MONITOR_VALUE_UNIT),
            test_only=bool(value.get("test_only", False)),
        )


def _default_thresholds() -> dict[str, ZeroMonitorThreshold]:
    return {name: ZeroMonitorThreshold() for name in ZERO_MONITOR_CRITERIA}


@dataclass(frozen=True, slots=True)
class ZeroMonitorAnalysisConfig:
    """Rolling analysis configuration with intentionally blank production limits."""

    long_window_s: float = 30.0
    minimum_stable_duration_s: float | None = None
    thresholds: Mapping[str, ZeroMonitorThreshold] = field(default_factory=_default_thresholds)
    offset_limit: float | None = None
    offset_limit_source: str = ""
    status: str = "pending_bench_approval"

    def __post_init__(self) -> None:
        if (
            not isfinite(self.long_window_s)
            or not MIN_LONG_WINDOW_S <= self.long_window_s <= MAX_LONG_WINDOW_S
        ):
            raise ValueError(
                f"long_window_s must be within {MIN_LONG_WINDOW_S:g}.."
                f"{MAX_LONG_WINDOW_S:g} seconds."
            )
        if self.minimum_stable_duration_s is not None and (
            not isfinite(self.minimum_stable_duration_s)
            or self.minimum_stable_duration_s < 0
        ):
            raise ValueError("minimum_stable_duration_s must be finite and nonnegative.")
        if self.offset_limit is not None and (
            not isfinite(self.offset_limit) or self.offset_limit < 0
        ):
            raise ValueError("offset_limit must be finite and nonnegative.")
        normalized = {
            name: self.thresholds.get(name, ZeroMonitorThreshold())
            for name in ZERO_MONITOR_CRITERIA
        }
        object.__setattr__(self, "thresholds", normalized)

    @classmethod
    def production_default(cls) -> ZeroMonitorAnalysisConfig:
        return cls()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ZeroMonitorAnalysisConfig:
        raw_thresholds = value.get("thresholds")
        thresholds = _default_thresholds()
        if isinstance(raw_thresholds, Mapping):
            for name in ZERO_MONITOR_CRITERIA:
                raw = raw_thresholds.get(name)
                if isinstance(raw, Mapping):
                    thresholds[name] = ZeroMonitorThreshold.from_dict(raw)
        stable_value = value.get("minimum_stable_duration_s")
        offset_value = value.get("offset_limit")
        return cls(
            long_window_s=float(value.get("long_window_s", 30.0)),
            minimum_stable_duration_s=(
                None if stable_value in (None, "") else float(stable_value)
            ),
            thresholds=thresholds,
            offset_limit=None if offset_value in (None, "") else float(offset_value),
            offset_limit_source=str(value.get("offset_limit_source") or ""),
            status=str(value.get("status") or "pending_bench_approval"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "long_window_s": self.long_window_s,
            "minimum_stable_duration_s": self.minimum_stable_duration_s,
            "thresholds": {
                name: self.thresholds[name].to_dict()
                for name in ZERO_MONITOR_CRITERIA
            },
            "offset_limit": self.offset_limit,
            "offset_limit_source": self.offset_limit_source,
            "status": self.status,
        }

    def configuration_errors(self) -> tuple[str, ...]:
        enabled = [name for name, item in self.thresholds.items() if item.enabled]
        errors: list[str] = []
        if not enabled:
            errors.append("NO_ENABLED_CRITERIA")
        for name in enabled:
            error = self.thresholds[name].configuration_error(name)
            if error is not None:
                errors.append(error)
        if self.minimum_stable_duration_s is None:
            errors.append("MINIMUM_STABLE_DURATION_NOT_CONFIGURED")
        if any(
            error.startswith("THRESHOLD_CONFIG_INCOMPLETE:")
            or error == "MINIMUM_STABLE_DURATION_NOT_CONFIGURED"
            for error in errors
        ):
            errors.insert(0, "THRESHOLD_CONFIG_INCOMPLETE")
        return tuple(dict.fromkeys(errors))


@dataclass(frozen=True, slots=True)
class ZeroMonitorCandidate:
    """One independent 600 ms DSP zero candidate."""

    sequence: int
    device_tick_ms: int
    continuous_segment: int
    live_zero_600ms: float
    trim_std_600ms: float
    trim_range_600ms: float
    raw_p2p_600ms: float


@dataclass(frozen=True, slots=True)
class ZeroMonitorMetrics:
    """Short- and long-window values used for an explainable decision."""

    candidate_count: int = 0
    window_span_s: float = 0.0
    short_std: float | None = None
    short_range: float | None = None
    raw_p2p: float | None = None
    long_mean: float | None = None
    repeat_std: float | None = None
    long_range: float | None = None
    long_p95_p5: float | None = None
    long_slope: float | None = None
    trend_span: float | None = None
    max_step: float | None = None
    adjacent_difference_rms: float | None = None
    zero_drift_from_cal: float | None = None

    def criterion_values(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in ZERO_MONITOR_CRITERIA}

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "candidate_count": self.candidate_count,
            "window_span_s": self.window_span_s,
            "short_std": self.short_std,
            "short_range": self.short_range,
            "raw_p2p": self.raw_p2p,
            "long_mean": self.long_mean,
            "repeat_std": self.repeat_std,
            "long_range": self.long_range,
            "long_p95_p5": self.long_p95_p5,
            "long_slope": self.long_slope,
            "trend_span": self.trend_span,
            "max_step": self.max_step,
            "adjacent_difference_rms": self.adjacent_difference_rms,
            "zero_drift_from_cal": self.zero_drift_from_cal,
        }


@dataclass(frozen=True, slots=True)
class ZeroMonitorEvaluation:
    state: ZeroMonitorState
    metrics: ZeroMonitorMetrics = field(default_factory=ZeroMonitorMetrics)
    reason_codes: tuple[str, ...] = ()
    advisory_codes: tuple[str, ...] = ()
    stable_duration_s: float = 0.0


class IndependentCandidateSelector:
    """Select a sequence-aligned candidate without filling missing publications."""

    def __init__(self, publications_per_candidate: int = 6) -> None:
        if publications_per_candidate <= 0:
            raise ValueError("publications_per_candidate must be positive.")
        self.publications_per_candidate = publications_per_candidate
        self._anchor: int | None = None

    def reset(self) -> None:
        self._anchor = None

    def accept(self, sequence: int) -> bool:
        sequence &= 0xFFFF
        if self._anchor is None:
            self._anchor = sequence
            return True
        delta = (sequence - self._anchor) & 0xFFFF
        return delta < 0x8000 and delta % self.publications_per_candidate == 0


class ZeroMonitorAnalyzer:
    """Memory-bounded rolling evaluator driven only by accepted candidates."""

    def __init__(self, config: ZeroMonitorAnalysisConfig) -> None:
        self.config = config
        self._candidates: deque[ZeroMonitorCandidate] = deque()
        self._segment: int | None = None
        self._stable_since_tick_ms: int | None = None

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    def reset(self) -> None:
        self._candidates.clear()
        self._segment = None
        self._stable_since_tick_ms = None

    def break_stability(self) -> None:
        self._stable_since_tick_ms = None

    def add_candidate(
        self,
        candidate: ZeroMonitorCandidate,
        *,
        zero_flow_confirmed: bool,
        byte_order_verified: bool,
        official_zero_offset: float | None = None,
    ) -> ZeroMonitorEvaluation:
        if self._segment != candidate.continuous_segment:
            self._candidates.clear()
            self._stable_since_tick_ms = None
            self._segment = candidate.continuous_segment
        self._candidates.append(candidate)
        maximum_age_ms = self.config.long_window_s * 1000.0
        while (
            self._candidates
            and candidate.device_tick_ms - self._candidates[0].device_tick_ms
            > maximum_age_ms
        ):
            self._candidates.popleft()

        metrics = _calculate_metrics(
            tuple(self._candidates),
            current=candidate,
            long_window_s=self.config.long_window_s,
            official_zero_offset=official_zero_offset,
        )
        return self._evaluate(
            candidate,
            metrics,
            zero_flow_confirmed=zero_flow_confirmed,
            byte_order_verified=byte_order_verified,
        )

    def evaluate_snapshot(
        self,
        snapshot: ZeroMonitorCandidate,
        *,
        zero_flow_confirmed: bool,
        byte_order_verified: bool,
        official_zero_offset: float | None = None,
    ) -> ZeroMonitorEvaluation:
        """Evaluate current 100 ms short metrics without adding a long candidate."""

        if self._segment != snapshot.continuous_segment:
            self._candidates.clear()
            self._stable_since_tick_ms = None
            self._segment = snapshot.continuous_segment
        metrics = _calculate_metrics(
            tuple(self._candidates),
            current=snapshot,
            long_window_s=self.config.long_window_s,
            official_zero_offset=official_zero_offset,
        )
        return self._evaluate(
            snapshot,
            metrics,
            zero_flow_confirmed=zero_flow_confirmed,
            byte_order_verified=byte_order_verified,
        )

    def _evaluate(
        self,
        current: ZeroMonitorCandidate,
        metrics: ZeroMonitorMetrics,
        *,
        zero_flow_confirmed: bool,
        byte_order_verified: bool,
    ) -> ZeroMonitorEvaluation:
        if (
            metrics.candidate_count < MIN_INDEPENDENT_CANDIDATES
            or metrics.window_span_s < self.config.long_window_s
        ):
            self._stable_since_tick_ms = None
            return ZeroMonitorEvaluation(
                state=ZeroMonitorState.NOT_READY,
                metrics=metrics,
                reason_codes=("LONG_WINDOW_NOT_READY",),
                advisory_codes=self._offset_advisories(metrics),
            )

        reasons = list(self.config.configuration_errors())
        if not zero_flow_confirmed:
            reasons.append("ZERO_FLOW_UNCONFIRMED")
        if not byte_order_verified:
            reasons.append("BYTE_ORDER_UNVERIFIED")
        if reasons:
            self._stable_since_tick_ms = None
            return ZeroMonitorEvaluation(
                state=ZeroMonitorState.EVALUATING,
                metrics=metrics,
                reason_codes=tuple(dict.fromkeys(reasons)),
                advisory_codes=self._offset_advisories(metrics),
            )

        exceeded = []
        values = metrics.criterion_values()
        for name, threshold in self.config.thresholds.items():
            if not threshold.enabled:
                continue
            value = values[name]
            if value is None or value > float(threshold.limit):
                exceeded.append(f"CRITERION_EXCEEDED:{name}")
        if exceeded:
            self._stable_since_tick_ms = None
            return ZeroMonitorEvaluation(
                state=ZeroMonitorState.UNSTABLE,
                metrics=metrics,
                reason_codes=tuple(exceeded),
                advisory_codes=self._offset_advisories(metrics),
            )

        if self._stable_since_tick_ms is None:
            self._stable_since_tick_ms = current.device_tick_ms
        stable_duration_s = max(
            0.0,
            (current.device_tick_ms - self._stable_since_tick_ms) / 1000.0,
        )
        required = float(self.config.minimum_stable_duration_s)
        state = (
            ZeroMonitorState.STABLE
            if stable_duration_s >= required
            else ZeroMonitorState.EVALUATING
        )
        reasons = () if state is ZeroMonitorState.STABLE else ("STABLE_DURATION_PENDING",)
        return ZeroMonitorEvaluation(
            state=state,
            metrics=metrics,
            reason_codes=reasons,
            advisory_codes=self._offset_advisories(metrics),
            stable_duration_s=stable_duration_s,
        )

    def _offset_advisories(self, metrics: ZeroMonitorMetrics) -> tuple[str, ...]:
        if self.config.offset_limit is None:
            return ("OFFSET_LIMIT_UNAVAILABLE",)
        if metrics.zero_drift_from_cal is None:
            return ("OFFSET_VALUE_UNAVAILABLE",)
        if abs(metrics.zero_drift_from_cal) > self.config.offset_limit:
            return ("OFFSET_EXCEEDED",)
        return ()


def _calculate_metrics(
    candidates: tuple[ZeroMonitorCandidate, ...],
    *,
    current: ZeroMonitorCandidate | None = None,
    long_window_s: float,
    official_zero_offset: float | None,
) -> ZeroMonitorMetrics:
    if not candidates:
        if current is None:
            return ZeroMonitorMetrics()
        return ZeroMonitorMetrics(
            short_std=current.trim_std_600ms,
            short_range=current.trim_range_600ms,
            raw_p2p=current.raw_p2p_600ms,
            zero_drift_from_cal=(
                None
                if official_zero_offset is None
                else current.live_zero_600ms - official_zero_offset
            ),
        )
    latest = current or candidates[-1]
    values = np.asarray([item.live_zero_600ms for item in candidates], dtype=float)
    times_s = np.asarray([item.device_tick_ms for item in candidates], dtype=float) / 1000.0
    span_s = float(times_s[-1] - times_s[0])
    long_mean = float(np.mean(values))
    repeat_std = float(np.std(values, ddof=1)) if len(values) >= 2 else None
    long_range = float(np.max(values) - np.min(values)) if len(values) >= 2 else None
    long_p95_p5 = (
        float(np.percentile(values, 95, method="linear") - np.percentile(values, 5, method="linear"))
        if len(values) >= MIN_INDEPENDENT_CANDIDATES
        else None
    )
    slope = None
    if len(values) >= 2:
        centered_time = times_s - float(np.mean(times_s))
        denominator = float(np.dot(centered_time, centered_time))
        if denominator > 0:
            centered_values = values - float(np.mean(values))
            slope = float(np.dot(centered_time, centered_values) / denominator)
    differences = np.diff(values)
    maximum_step = float(np.max(np.abs(differences))) if differences.size else None
    adjacent_rms = (
        float(sqrt(0.5 * float(np.mean(np.square(differences)))))
        if differences.size
        else None
    )
    drift = (
        None
        if official_zero_offset is None
        else float(latest.live_zero_600ms - official_zero_offset)
    )
    return ZeroMonitorMetrics(
        candidate_count=len(candidates),
        window_span_s=span_s,
        short_std=latest.trim_std_600ms,
        short_range=latest.trim_range_600ms,
        raw_p2p=latest.raw_p2p_600ms,
        long_mean=long_mean,
        repeat_std=repeat_std,
        long_range=long_range,
        long_p95_p5=long_p95_p5,
        long_slope=slope,
        trend_span=None if slope is None else abs(slope) * long_window_s,
        max_step=maximum_step,
        adjacent_difference_rms=adjacent_rms,
        zero_drift_from_cal=drift,
    )

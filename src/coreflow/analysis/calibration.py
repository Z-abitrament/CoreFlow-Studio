"""Calibration calculation interfaces and placeholder implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from math import sqrt
from typing import Protocol

from coreflow.devices import ParameterWriteRequest, WriteMode


@dataclass(frozen=True, slots=True)
class CalibrationReferencePoint:
    """One reference point used by calibration preview."""

    reference_mass_flow: float
    sample_count: int
    tolerance: float | None = None


@dataclass(frozen=True, slots=True)
class CalibrationMeasurement:
    """Measured value collected at one reference point."""

    reference_mass_flow: float
    measured_mass_flow: float
    sample_count: int
    raw_artifact_id: str


@dataclass(frozen=True, slots=True)
class CalibrationPreviewResult:
    """Preview result with metrics and proposed write requests."""

    measurements: tuple[CalibrationMeasurement, ...]
    summary_metrics: dict[str, float]
    proposed_writes: tuple[ParameterWriteRequest, ...] = ()
    algorithm_name: str = "placeholder_zero_offset"
    algorithm_version: str = "0.1"
    notes: str = "Placeholder calculation; replace with approved production formula."


class CalibrationCalculator(Protocol):
    """Calculation interface for calibration preview modules."""

    def preview(
        self,
        measurements: tuple[CalibrationMeasurement, ...],
        actor: str,
        workflow_state: str,
        run_id: str,
    ) -> CalibrationPreviewResult: ...


@dataclass(frozen=True, slots=True)
class PlaceholderCalibrationCalculator:
    """Simple preview calculator that does not represent production calibration."""

    target_parameter: str = "zero_offset"
    metadata: dict[str, str] = field(default_factory=dict)

    def preview(
        self,
        measurements: tuple[CalibrationMeasurement, ...],
        actor: str,
        workflow_state: str,
        run_id: str,
    ) -> CalibrationPreviewResult:
        if not measurements:
            raise ValueError("Calibration preview requires at least one measurement.")

        errors = tuple(
            measurement.measured_mass_flow - measurement.reference_mass_flow
            for measurement in measurements
        )
        mean_error = sum(errors) / len(errors)
        max_abs_error = max(abs(error) for error in errors)
        proposed_offset = -mean_error
        proposed_write = ParameterWriteRequest(
            parameter_name=self.target_parameter,
            new_value=proposed_offset,
            mode=WriteMode.PREVIEW,
            actor=actor,
            workflow_state=workflow_state,
            run_id=run_id,
            metadata={
                "algorithm": "placeholder_zero_offset",
                "formula_status": "placeholder",
                **self.metadata,
            },
        )
        return CalibrationPreviewResult(
            measurements=measurements,
            summary_metrics={
                "mean_error": mean_error,
                "max_abs_error": max_abs_error,
                "reference_point_count": float(len(measurements)),
            },
            proposed_writes=(proposed_write,),
        )


@dataclass(frozen=True, slots=True)
class ZeroCalibrationSnapshot:
    """Values captured before or after a zero calibration action."""

    zero_offset: float
    delta_t: float
    captured_at: datetime


@dataclass(frozen=True, slots=True)
class ZeroCalibrationRecord:
    """Traceable result for a zero calibration cycle."""

    before: ZeroCalibrationSnapshot
    after: ZeroCalibrationSnapshot
    control_parameter: str
    completed: bool

    @property
    def zero_offset_change(self) -> float:
        return self.after.zero_offset - self.before.zero_offset

    @property
    def delta_t_change(self) -> float:
        return self.after.delta_t - self.before.delta_t


@dataclass(frozen=True, slots=True)
class KFactorCalibrationInput:
    """Inputs captured during manual K factor calibration."""

    mass_acc_before: float
    mass_acc_after: float
    standard_mass: float
    current_k_factor: float

    @property
    def measured_mass_delta(self) -> float:
        return self.mass_acc_after - self.mass_acc_before


@dataclass(frozen=True, slots=True)
class KFactorCalibrationResult:
    """Calculated K factor update and supporting values."""

    mass_acc_before: float
    mass_acc_after: float
    measured_mass_delta: float
    standard_mass: float
    current_k_factor: float
    corrected_k_factor: float


@dataclass(frozen=True, slots=True)
class RepeatabilityTrial:
    """One manual mass-total trial at a configured flow point."""

    flow_point: float
    trial_index: int
    mass_acc_before: float
    mass_acc_after: float
    standard_mass: float

    @property
    def measured_mass_delta(self) -> float:
        return self.mass_acc_after - self.mass_acc_before


@dataclass(frozen=True, slots=True)
class RepeatabilityTrialResult:
    """Calculated error for one repeatability trial."""

    flow_point: float
    trial_index: int
    measured_mass_delta: float
    standard_mass: float
    percent_error: float


@dataclass(frozen=True, slots=True)
class FlowPointRepeatabilityResult:
    """Error and repeatability summary for one flow point."""

    flow_point: float
    trials: tuple[RepeatabilityTrialResult, ...]
    repeatability_stddev_percent: float


@dataclass(frozen=True, slots=True)
class RepeatabilityTestResult:
    """Complete error and repeatability result set."""

    flow_points: tuple[FlowPointRepeatabilityResult, ...]
    summary_metrics: dict[str, float]


def calculate_k_factor(
    calibration_input: KFactorCalibrationInput,
) -> KFactorCalibrationResult:
    """Calculate corrected K factor from manual standard-mass input."""

    measured_mass_delta = calibration_input.measured_mass_delta
    if measured_mass_delta == 0:
        raise ValueError("K factor calibration requires non-zero measured mass delta.")
    if calibration_input.standard_mass <= 0:
        raise ValueError("K factor calibration requires positive standard mass.")
    corrected = (
        calibration_input.current_k_factor
        / measured_mass_delta
        * calibration_input.standard_mass
    )
    return KFactorCalibrationResult(
        mass_acc_before=calibration_input.mass_acc_before,
        mass_acc_after=calibration_input.mass_acc_after,
        measured_mass_delta=measured_mass_delta,
        standard_mass=calibration_input.standard_mass,
        current_k_factor=calibration_input.current_k_factor,
        corrected_k_factor=corrected,
    )


def analyze_repeatability(
    trials: tuple[RepeatabilityTrial, ...] | list[RepeatabilityTrial],
    *,
    expected_trials_per_point: int = 3,
) -> RepeatabilityTestResult:
    """Calculate percent error and repeatability per flow point."""

    if not trials:
        raise ValueError("Repeatability analysis requires at least one trial.")
    grouped: dict[float, list[RepeatabilityTrialResult]] = {}
    for trial in trials:
        if trial.standard_mass <= 0:
            raise ValueError("Repeatability analysis requires positive standard mass.")
        percent_error = (
            (trial.measured_mass_delta - trial.standard_mass)
            / trial.standard_mass
            * 100.0
        )
        grouped.setdefault(trial.flow_point, []).append(
            RepeatabilityTrialResult(
                flow_point=trial.flow_point,
                trial_index=trial.trial_index,
                measured_mass_delta=trial.measured_mass_delta,
                standard_mass=trial.standard_mass,
                percent_error=percent_error,
            )
        )

    point_results: list[FlowPointRepeatabilityResult] = []
    all_errors: list[float] = []
    for flow_point in sorted(grouped):
        trial_results = tuple(sorted(grouped[flow_point], key=lambda item: item.trial_index))
        if len(trial_results) != expected_trials_per_point:
            raise ValueError(
                f"Flow point {flow_point} requires {expected_trials_per_point} trials."
            )
        errors = [trial.percent_error for trial in trial_results]
        all_errors.extend(errors)
        point_results.append(
            FlowPointRepeatabilityResult(
                flow_point=flow_point,
                trials=trial_results,
                repeatability_stddev_percent=_sample_stddev(errors),
            )
        )

    return RepeatabilityTestResult(
        flow_points=tuple(point_results),
        summary_metrics={
            "flow_point_count": float(len(point_results)),
            "trial_count": float(len(all_errors)),
            "max_abs_percent_error": max(abs(error) for error in all_errors),
            "mean_percent_error": sum(all_errors) / len(all_errors),
            "max_repeatability_stddev_percent": max(
                point.repeatability_stddev_percent for point in point_results
            ),
        },
    )


def _sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)

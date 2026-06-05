"""Calibration calculation interfaces and placeholder implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
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

"""Calibration preview workflow foundation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from coreflow.analysis.calibration import (
    CalibrationCalculator,
    CalibrationMeasurement,
    CalibrationPreviewResult,
    CalibrationReferencePoint,
    PlaceholderCalibrationCalculator,
)
from coreflow.app.write_guard import WriteGuardService
from coreflow.devices import FlowmeterDevice
from coreflow.storage import (
    AnalysisResultRecord,
    ArtifactStore,
    ArtifactType,
    DeviceRecord,
    StorageRepository,
)
from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


@dataclass(frozen=True, slots=True)
class CalibrationPreviewConfig:
    """Inputs required to run calibration preview."""

    run_id: str
    operator: str
    reference_points: tuple[CalibrationReferencePoint, ...]
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class CalibrationPreviewWorkflowResult:
    """Stored calibration preview workflow output."""

    run_id: str
    preview: CalibrationPreviewResult
    raw_artifact_ids: tuple[str, ...]
    proposed_audit_ids: tuple[str, ...]


class CalibrationPreviewWorkflow:
    """Headless simulator-capable calibration preview workflow."""

    def __init__(
        self,
        repository: StorageRepository,
        artifact_store: ArtifactStore,
        calculator: CalibrationCalculator | None = None,
        write_guard: WriteGuardService | None = None,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._calculator = calculator or PlaceholderCalibrationCalculator()
        self._write_guard = write_guard or WriteGuardService(repository)

    def run(
        self,
        device: FlowmeterDevice,
        config: CalibrationPreviewConfig,
    ) -> CalibrationPreviewWorkflowResult:
        if not config.reference_points:
            raise ValueError("Calibration preview requires reference points.")

        started_at = datetime.now(UTC)
        device.connect()
        identity = device.read_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="calibration_preview",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot={
                    "reference_points": [
                        {
                            "reference_mass_flow": point.reference_mass_flow,
                            "sample_count": point.sample_count,
                            "tolerance": point.tolerance,
                        }
                        for point in config.reference_points
                    ],
                    "calculation": "placeholder_zero_offset",
                },
                software_version=config.software_version,
            )
        )

        measurements: list[CalibrationMeasurement] = []
        raw_artifact_ids: list[str] = []
        for index, point in enumerate(config.reference_points, start=1):
            step = WorkflowStep(
                step_id=f"{config.run_id}-STEP-{index:03d}",
                run_id=config.run_id,
                name=f"Collect reference point {index}",
                step_type=WorkflowStepType.CAPTURE,
                status=WorkflowStepStatus.RUNNING,
                started_at=datetime.now(UTC),
                input_configuration={
                    "reference_mass_flow": point.reference_mass_flow,
                    "sample_count": point.sample_count,
                },
            )
            self._repository.save_step(step)
            collected = [device.read_measurement() for _ in range(point.sample_count)]
            measured_values = [
                measurement.mass_flow
                for measurement in collected
                if measurement.mass_flow is not None
            ]
            if not measured_values:
                failed_step = WorkflowStep(
                    step_id=step.step_id,
                    run_id=step.run_id,
                    name=step.name,
                    step_type=step.step_type,
                    status=WorkflowStepStatus.FAILED,
                    started_at=step.started_at,
                    ended_at=datetime.now(UTC),
                    input_configuration=step.input_configuration,
                    error_message="No mass-flow measurements collected.",
                )
                self._repository.save_step(failed_step)
                raise ValueError("No mass-flow measurements collected.")

            artifact_id = f"{config.run_id}-RAW-{index:03d}"
            artifact = self._artifact_store.write_artifact(
                run_id=config.run_id,
                artifact_id=artifact_id,
                artifact_type=ArtifactType.RAW,
                file_name=f"reference_point_{index:03d}.csv",
                content=_samples_csv(collected),
                created_at=started_at,
                step_id=step.step_id,
                file_format="csv",
            )
            self._repository.save_artifact(artifact)
            raw_artifact_ids.append(artifact_id)
            measured_mean = sum(measured_values) / len(measured_values)
            measurements.append(
                CalibrationMeasurement(
                    reference_mass_flow=point.reference_mass_flow,
                    measured_mass_flow=measured_mean,
                    sample_count=len(measured_values),
                    raw_artifact_id=artifact_id,
                )
            )
            self._repository.save_step(
                WorkflowStep(
                    step_id=step.step_id,
                    run_id=step.run_id,
                    name=step.name,
                    step_type=step.step_type,
                    status=WorkflowStepStatus.PASSED,
                    started_at=step.started_at,
                    ended_at=datetime.now(UTC),
                    input_configuration=step.input_configuration,
                    output_summary={
                        "measured_mean": measured_mean,
                        "raw_artifact_id": artifact_id,
                    },
                )
            )

        preview = self._calculator.preview(
            tuple(measurements),
            actor=config.operator,
            workflow_state="calibration_preview",
            run_id=config.run_id,
        )
        proposed_audit_ids: list[str] = []
        for write in preview.proposed_writes:
            decision = self._write_guard.preview(device, write)
            proposed_audit_ids.append(decision.audit_id)

        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{config.run_id}-CAL-PREVIEW",
                run_id=config.run_id,
                step_id=None,
                result_type="calibration_preview",
                algorithm_name=preview.algorithm_name,
                algorithm_version=preview.algorithm_version,
                input_artifact_ids=tuple(raw_artifact_ids),
                configuration_snapshot={"notes": preview.notes},
                summary_metrics=preview.summary_metrics,
                pass_fail_decision="preview",
                created_at=datetime.now(UTC),
            )
        )
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="calibration_preview",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.PASSED,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                configuration_snapshot={
                    "reference_points": [
                        {
                            "reference_mass_flow": point.reference_mass_flow,
                            "sample_count": point.sample_count,
                            "tolerance": point.tolerance,
                        }
                        for point in config.reference_points
                    ],
                    "calculation": "placeholder_zero_offset",
                },
                software_version=config.software_version,
            )
        )
        return CalibrationPreviewWorkflowResult(
            run_id=config.run_id,
            preview=preview,
            raw_artifact_ids=tuple(raw_artifact_ids),
            proposed_audit_ids=tuple(proposed_audit_ids),
        )


def _samples_csv(samples: list[object]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "captured_at",
            "mass_flow",
            "volume_flow",
            "density",
            "temperature",
            "status_flags",
            "source_channel",
        ]
    )
    for sample in samples:
        writer.writerow(
            [
                getattr(sample, "captured_at"),
                getattr(sample, "mass_flow"),
                getattr(sample, "volume_flow"),
                getattr(sample, "density"),
                getattr(sample, "temperature"),
                "|".join(getattr(sample, "status_flags")),
                getattr(sample, "source_channel"),
            ]
        )
    return buffer.getvalue().encode("utf-8")

"""Automated factory test workflow foundation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev

from coreflow.devices import DeviceHealth, FlowmeterDevice, Measurement
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
class FactoryMeasurementCheck:
    """Reference point and acceptance limit for a factory measurement check."""

    reference_mass_flow: float
    sample_count: int
    max_abs_error: float


@dataclass(frozen=True, slots=True)
class FactoryStabilityCheck:
    """Short stability segment settings."""

    sample_count: int
    max_range: float
    max_stddev: float | None = None


@dataclass(frozen=True, slots=True)
class FactoryTestConfig:
    """Inputs for the automated factory test workflow."""

    run_id: str
    operator: str
    measurement_check: FactoryMeasurementCheck
    stability_check: FactoryStabilityCheck
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class FactoryTestWorkflowResult:
    """Summary returned after a factory test workflow run."""

    run_id: str
    passed: bool
    measurement_artifact_id: str | None
    stability_artifact_id: str | None
    summary_metrics: dict[str, float]


class FactoryTestWorkflow:
    """Headless automated factory test workflow."""

    def __init__(
        self,
        repository: StorageRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store

    def run(
        self,
        device: FlowmeterDevice,
        config: FactoryTestConfig,
    ) -> FactoryTestWorkflowResult:
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
        self._save_run(config, identity.device_id, RunStatus.RUNNING, started_at)

        summary_metrics: dict[str, float] = {}
        measurement_artifact_id: str | None = None
        stability_artifact_id: str | None = None
        failed = False

        health = device.read_health()
        health_passed = health.state.value == "connected" and not health.alarm_flags
        self._save_step(
            config.run_id,
            "STEP-001",
            "Communication health",
            WorkflowStepType.DEVICE_READ,
            health_passed,
            input_configuration={},
            output_summary={
                "state": health.state.value,
                "status_flags": list(health.status_flags),
                "alarm_flags": list(health.alarm_flags),
            },
            error_message=None if health_passed else _health_error(health),
        )
        failed = failed or not health_passed

        configuration = device.read_configuration()
        self._save_step(
            config.run_id,
            "STEP-002",
            "Identity and configuration capture",
            WorkflowStepType.DEVICE_READ,
            True,
            input_configuration={"fields": ["identity", "configuration"]},
            output_summary={
                "device_id": identity.device_id,
                "serial_number": identity.serial_number,
                "configuration_count": len(configuration),
            },
        )

        measurement_samples = [
            device.read_measurement()
            for _ in range(config.measurement_check.sample_count)
        ]
        measurement_step_id = f"{config.run_id}-STEP-003"
        self._repository.save_step(
            WorkflowStep(
                step_id=measurement_step_id,
                run_id=config.run_id,
                name="Measurement check",
                step_type=WorkflowStepType.PASS_FAIL_CHECK,
                status=WorkflowStepStatus.RUNNING,
                started_at=datetime.now(UTC),
                input_configuration={
                    "reference_mass_flow": config.measurement_check.reference_mass_flow,
                    "sample_count": config.measurement_check.sample_count,
                    "max_abs_error": config.measurement_check.max_abs_error,
                },
            )
        )
        measurement_values = _mass_flow_values(measurement_samples)
        measurement_mean = mean(measurement_values)
        abs_error = abs(
            measurement_mean - config.measurement_check.reference_mass_flow
        )
        measurement_passed = abs_error <= config.measurement_check.max_abs_error
        measurement_artifact_id = f"{config.run_id}-FACTORY-MEASUREMENT"
        measurement_artifact = self._artifact_store.write_artifact(
            run_id=config.run_id,
            artifact_id=measurement_artifact_id,
            artifact_type=ArtifactType.RAW,
            file_name="factory_measurement_check.csv",
            content=_samples_csv(measurement_samples),
            created_at=started_at,
            step_id=measurement_step_id,
            file_format="csv",
        )
        self._repository.save_artifact(measurement_artifact)
        summary_metrics.update(
            {
                "measurement_mean": measurement_mean,
                "measurement_abs_error": abs_error,
            }
        )
        self._save_step(
            config.run_id,
            "STEP-003",
            "Measurement check",
            WorkflowStepType.PASS_FAIL_CHECK,
            measurement_passed,
            input_configuration={
                "reference_mass_flow": config.measurement_check.reference_mass_flow,
                "sample_count": config.measurement_check.sample_count,
                "max_abs_error": config.measurement_check.max_abs_error,
            },
            output_summary={
                "measurement_mean": measurement_mean,
                "abs_error": abs_error,
                "raw_artifact_id": measurement_artifact_id,
            },
            error_message=None
            if measurement_passed
            else f"Measurement error {abs_error} exceeded limit.",
        )
        failed = failed or not measurement_passed

        stability_samples = [
            device.read_measurement()
            for _ in range(config.stability_check.sample_count)
        ]
        stability_step_id = f"{config.run_id}-STEP-004"
        self._repository.save_step(
            WorkflowStep(
                step_id=stability_step_id,
                run_id=config.run_id,
                name="Stability segment",
                step_type=WorkflowStepType.PASS_FAIL_CHECK,
                status=WorkflowStepStatus.RUNNING,
                started_at=datetime.now(UTC),
                input_configuration={
                    "sample_count": config.stability_check.sample_count,
                    "max_range": config.stability_check.max_range,
                    "max_stddev": config.stability_check.max_stddev,
                },
            )
        )
        stability_values = _mass_flow_values(stability_samples)
        stability_range = max(stability_values) - min(stability_values)
        stability_stddev = pstdev(stability_values) if len(stability_values) > 1 else 0.0
        stability_passed = stability_range <= config.stability_check.max_range
        if config.stability_check.max_stddev is not None:
            stability_passed = (
                stability_passed
                and stability_stddev <= config.stability_check.max_stddev
            )
        stability_artifact_id = f"{config.run_id}-FACTORY-STABILITY"
        stability_artifact = self._artifact_store.write_artifact(
            run_id=config.run_id,
            artifact_id=stability_artifact_id,
            artifact_type=ArtifactType.RAW,
            file_name="factory_stability_segment.csv",
            content=_samples_csv(stability_samples),
            created_at=started_at,
            step_id=stability_step_id,
            file_format="csv",
        )
        self._repository.save_artifact(stability_artifact)
        summary_metrics.update(
            {
                "stability_range": stability_range,
                "stability_stddev": stability_stddev,
            }
        )
        self._save_step(
            config.run_id,
            "STEP-004",
            "Stability segment",
            WorkflowStepType.PASS_FAIL_CHECK,
            stability_passed,
            input_configuration={
                "sample_count": config.stability_check.sample_count,
                "max_range": config.stability_check.max_range,
                "max_stddev": config.stability_check.max_stddev,
            },
            output_summary={
                "range": stability_range,
                "stddev": stability_stddev,
                "raw_artifact_id": stability_artifact_id,
            },
            error_message=None
            if stability_passed
            else "Stability metrics exceeded configured limits.",
        )
        failed = failed or not stability_passed

        passed = not failed
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{config.run_id}-FACTORY-SUMMARY",
                run_id=config.run_id,
                step_id=None,
                result_type="factory_test_summary",
                algorithm_name="factory_test_basic_checks",
                algorithm_version="0.1",
                input_artifact_ids=tuple(
                    artifact_id
                    for artifact_id in (
                        measurement_artifact_id,
                        stability_artifact_id,
                    )
                    if artifact_id is not None
                ),
                configuration_snapshot={
                    "measurement_check": {
                        "reference_mass_flow": config.measurement_check.reference_mass_flow,
                        "sample_count": config.measurement_check.sample_count,
                        "max_abs_error": config.measurement_check.max_abs_error,
                    },
                    "stability_check": {
                        "sample_count": config.stability_check.sample_count,
                        "max_range": config.stability_check.max_range,
                        "max_stddev": config.stability_check.max_stddev,
                    },
                },
                summary_metrics=summary_metrics,
                pass_fail_decision="passed" if passed else "failed",
                created_at=datetime.now(UTC),
            )
        )
        self._save_run(
            config,
            identity.device_id,
            RunStatus.PASSED if passed else RunStatus.FAILED,
            started_at,
            ended_at=datetime.now(UTC),
        )
        return FactoryTestWorkflowResult(
            run_id=config.run_id,
            passed=passed,
            measurement_artifact_id=measurement_artifact_id,
            stability_artifact_id=stability_artifact_id,
            summary_metrics=summary_metrics,
        )

    def _save_run(
        self,
        config: FactoryTestConfig,
        device_id: str,
        status: RunStatus,
        started_at: datetime,
        ended_at: datetime | None = None,
    ) -> None:
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.FACTORY_TEST,
                workflow_name="automated_factory_test",
                workflow_version=config.workflow_version,
                device_id=device_id,
                operator=config.operator,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                configuration_snapshot={
                    "measurement_check": {
                        "reference_mass_flow": config.measurement_check.reference_mass_flow,
                        "sample_count": config.measurement_check.sample_count,
                        "max_abs_error": config.measurement_check.max_abs_error,
                    },
                    "stability_check": {
                        "sample_count": config.stability_check.sample_count,
                        "max_range": config.stability_check.max_range,
                        "max_stddev": config.stability_check.max_stddev,
                    },
                },
                software_version=config.software_version,
            )
        )

    def _save_step(
        self,
        run_id: str,
        step_suffix: str,
        name: str,
        step_type: WorkflowStepType,
        passed: bool,
        input_configuration: dict[str, object],
        output_summary: dict[str, object],
        error_message: str | None = None,
    ) -> None:
        self._repository.save_step(
            WorkflowStep(
                step_id=f"{run_id}-{step_suffix}",
                run_id=run_id,
                name=name,
                step_type=step_type,
                status=WorkflowStepStatus.PASSED
                if passed
                else WorkflowStepStatus.FAILED,
                started_at=datetime.now(UTC),
                ended_at=datetime.now(UTC),
                input_configuration=input_configuration,
                output_summary=output_summary,
                error_message=error_message,
            )
        )


def _health_error(health: DeviceHealth) -> str:
    if health.alarm_flags:
        return f"Device has active alarms: {', '.join(health.alarm_flags)}"
    return f"Device health state is {health.state.value}"


def _mass_flow_values(samples: list[Measurement]) -> list[float]:
    values = [sample.mass_flow for sample in samples if sample.mass_flow is not None]
    if not values:
        raise ValueError("No mass-flow measurements collected.")
    return values


def _samples_csv(samples: list[Measurement]) -> bytes:
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
                sample.captured_at,
                sample.mass_flow,
                sample.volume_flow,
                sample.density,
                sample.temperature,
                "|".join(sample.status_flags),
                sample.source_channel,
            ]
        )
    return buffer.getvalue().encode("utf-8")

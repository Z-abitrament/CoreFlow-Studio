"""Calibration preview workflow foundation."""

from __future__ import annotations

from collections.abc import Callable
import csv
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import sleep

from coreflow.analysis.calibration import (
    CalibrationCalculator,
    CalibrationMeasurement,
    CalibrationPreviewResult,
    CalibrationReferencePoint,
    KFactorCalibrationInput,
    KFactorCalibrationResult,
    PlaceholderCalibrationCalculator,
    RepeatabilityTestResult,
    RepeatabilityTrial,
    ZeroCalibrationRecord,
    ZeroCalibrationSnapshot,
    analyze_repeatability,
    calculate_k_factor,
)
from coreflow.app.write_guard import WriteGuardService
from coreflow.devices import (
    ConfigurationParameter,
    DeviceIdentity,
    FlowmeterDevice,
    ParameterWriteRequest,
    WriteMode,
)
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


@dataclass(frozen=True, slots=True)
class ZeroCalibrationConfig:
    """Inputs for a Modbus-style zero calibration coil workflow."""

    run_id: str
    operator: str
    start_parameter: str = "zero_calibration_start"
    zero_offset_parameter: str = "zero_offset"
    delta_t_parameter: str = "delta_t"
    snapshot_parameter_names: tuple[str, ...] = ()
    completion_wait_s: float = 3.0
    max_poll_count: int = 30
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class ZeroCalibrationWorkflowResult:
    """Stored zero calibration workflow output."""

    run_id: str
    record: ZeroCalibrationRecord
    audit_id: str
    pre_snapshot: dict[str, object] = field(default_factory=dict)
    pre_snapshot_captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class KFactorCalibrationConfig:
    """Inputs for the manual K factor apply workflow."""

    run_id: str
    operator: str
    mass_acc_before: float
    mass_acc_after: float
    standard_mass: float
    current_k_factor: float
    k_factor_parameter: str = "k_factor"
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class KFactorCalibrationWorkflowResult:
    """Stored K factor workflow output."""

    run_id: str
    calibration: KFactorCalibrationResult
    audit_id: str
    write_status: str


@dataclass(frozen=True, slots=True)
class FlowSegmentCaptureConfig:
    """Reusable detection settings for one non-zero flow segment."""

    flow_rate_parameter: str
    poll_interval_s: float = 1.0
    nonzero_threshold: float = 0.0
    post_start_sample_s: float = 3.0
    post_stop_delay_s: float = 3.0
    max_wait_start_polls: int = 600
    max_wait_stop_polls: int = 600
    cancel_requested: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class FlowSegmentCaptureResult:
    """Captured timing and flow data for one open-close flow segment."""

    flow_rate_parameter: str
    started_at: datetime
    instant_flow_at: datetime
    ended_at: datetime
    start_flow: float
    instant_flow: float
    stop_flow: float
    poll_count: int

    @property
    def duration_s(self) -> float:
        return (self.ended_at - self.started_at).total_seconds()


@dataclass(frozen=True, slots=True)
class RepeatabilityTestConfig:
    """Inputs for the manual three-flow-point repeatability workflow."""

    run_id: str
    operator: str
    trials: tuple[RepeatabilityTrial, ...]
    expected_flow_point_count: int = 3
    expected_trials_per_point: int = 3
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class RepeatabilityTestWorkflowResult:
    """Stored repeatability test workflow output."""

    run_id: str
    result: RepeatabilityTestResult


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


class ZeroCalibrationWorkflow:
    """Runs a guarded zero calibration start and waits for completion."""

    def __init__(
        self,
        repository: StorageRepository,
        write_guard: WriteGuardService | None = None,
    ) -> None:
        self._repository = repository
        self._write_guard = write_guard or WriteGuardService(repository)

    def run(
        self,
        device: FlowmeterDevice,
        config: ZeroCalibrationConfig,
    ) -> ZeroCalibrationWorkflowResult:
        if config.max_poll_count < 1:
            raise ValueError("Zero calibration requires at least one status poll.")
        if config.completion_wait_s < 0:
            raise ValueError("Zero calibration wait time cannot be negative.")
        started_at = datetime.now(UTC)
        device.connect()
        identity = device.read_identity()
        _save_identity(self._repository, identity)
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="zero_calibration",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot={
                    "start_parameter": config.start_parameter,
                    "zero_offset_parameter": config.zero_offset_parameter,
                    "delta_t_parameter": config.delta_t_parameter,
                    "snapshot_parameter_names": list(config.snapshot_parameter_names),
                    "completion_wait_s": config.completion_wait_s,
                    "max_poll_count": config.max_poll_count,
                },
                software_version=config.software_version,
            )
        )
        step = WorkflowStep(
            step_id=f"{config.run_id}-STEP-001",
            run_id=config.run_id,
            name="Run zero calibration",
            step_type=WorkflowStepType.DEVICE_WRITE,
            status=WorkflowStepStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._repository.save_step(step)
        pre_snapshot, pre_snapshot_captured_at = _pre_calibration_snapshot(
            device,
            config.snapshot_parameter_names,
        )
        before = _zero_snapshot(
            _read_named_configuration(
                device,
                (
                    config.zero_offset_parameter,
                    config.delta_t_parameter,
                ),
            ),
            zero_offset_name=config.zero_offset_parameter,
            delta_t_name=config.delta_t_parameter,
        )
        decision = self._write_guard.evaluate(
            device,
            ParameterWriteRequest(
                parameter_name=config.start_parameter,
                new_value=True,
                mode=WriteMode.ARMED,
                actor=config.operator,
                workflow_state="calibration_write_armed",
                run_id=config.run_id,
                metadata={"calibration": "zero"},
            ),
        )
        if config.completion_wait_s:
            sleep(config.completion_wait_s)
        completed = False
        for _poll_index in range(config.max_poll_count):
            parameters = _read_named_configuration(device, (config.start_parameter,))
            state = _parameter_value(parameters, config.start_parameter)
            if state in (False, 0, None):
                completed = True
                break
        after = _zero_snapshot(
            _read_named_configuration(
                device,
                (
                    config.zero_offset_parameter,
                    config.delta_t_parameter,
                ),
            ),
            zero_offset_name=config.zero_offset_parameter,
            delta_t_name=config.delta_t_parameter,
        )
        record = ZeroCalibrationRecord(
            before=before,
            after=after,
            control_parameter=config.start_parameter,
            completed=completed,
        )
        passed = decision.allowed and completed
        self._repository.save_step(
            WorkflowStep(
                step_id=step.step_id,
                run_id=step.run_id,
                name=step.name,
                step_type=step.step_type,
                status=WorkflowStepStatus.PASSED
                if passed
                else WorkflowStepStatus.FAILED,
                started_at=step.started_at,
                ended_at=datetime.now(UTC),
                input_configuration={
                    "start_parameter": config.start_parameter,
                    "zero_offset_before": before.zero_offset,
                    "delta_t_before": before.delta_t,
                    "pre_snapshot": pre_snapshot,
                    "pre_snapshot_captured_at": pre_snapshot_captured_at.isoformat()
                    if pre_snapshot_captured_at is not None
                    else None,
                    "completion_wait_s": config.completion_wait_s,
                },
                output_summary={
                    "zero_offset_after": after.zero_offset,
                    "delta_t_after": after.delta_t,
                    "zero_offset_change": record.zero_offset_change,
                    "delta_t_change": record.delta_t_change,
                    "completed": completed,
                    "write_status": decision.result.status.value,
                    "audit_id": decision.audit_id,
                },
                error_message=None if passed else decision.result.message,
            )
        )
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{config.run_id}-ZERO",
                run_id=config.run_id,
                step_id=step.step_id,
                result_type="zero_calibration",
                algorithm_name="modbus_zero_coil_wait",
                algorithm_version="0.1",
                configuration_snapshot={
                    "start_parameter": config.start_parameter,
                    "completion_condition": "start parameter reads false or zero",
                    "snapshot_parameter_names": list(config.snapshot_parameter_names),
                    "completion_wait_s": config.completion_wait_s,
                },
                summary_metrics={
                    "zero_offset_before": before.zero_offset,
                    "zero_offset_after": after.zero_offset,
                    "zero_offset_change": record.zero_offset_change,
                    "delta_t_before": before.delta_t,
                    "delta_t_after": after.delta_t,
                    "delta_t_change": record.delta_t_change,
                    "completed": completed,
                    "pre_snapshot": pre_snapshot,
                    "pre_snapshot_captured_at": pre_snapshot_captured_at.isoformat()
                    if pre_snapshot_captured_at is not None
                    else None,
                },
                pass_fail_decision="passed" if passed else "failed",
                created_at=datetime.now(UTC),
            )
        )
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="zero_calibration",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.PASSED if passed else RunStatus.FAILED,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                configuration_snapshot={
                    "start_parameter": config.start_parameter,
                    "zero_offset_parameter": config.zero_offset_parameter,
                    "delta_t_parameter": config.delta_t_parameter,
                    "snapshot_parameter_names": list(config.snapshot_parameter_names),
                    "completion_wait_s": config.completion_wait_s,
                    "max_poll_count": config.max_poll_count,
                },
                software_version=config.software_version,
            )
        )
        return ZeroCalibrationWorkflowResult(
            run_id=config.run_id,
            record=record,
            audit_id=decision.audit_id,
            pre_snapshot=pre_snapshot,
            pre_snapshot_captured_at=pre_snapshot_captured_at,
        )


class KFactorCalibrationWorkflow:
    """Applies a guarded K factor update from manual mass-total inputs."""

    def __init__(
        self,
        repository: StorageRepository,
        write_guard: WriteGuardService | None = None,
    ) -> None:
        self._repository = repository
        self._write_guard = write_guard or WriteGuardService(repository)

    def run(
        self,
        device: FlowmeterDevice,
        config: KFactorCalibrationConfig,
    ) -> KFactorCalibrationWorkflowResult:
        started_at = datetime.now(UTC)
        device.connect()
        identity = device.read_identity()
        _save_identity(self._repository, identity)
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="k_factor_calibration",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot={
                    "mass_acc_before": config.mass_acc_before,
                    "mass_acc_after": config.mass_acc_after,
                    "standard_mass": config.standard_mass,
                    "current_k_factor": config.current_k_factor,
                    "k_factor_parameter": config.k_factor_parameter,
                },
                software_version=config.software_version,
            )
        )
        step = WorkflowStep(
            step_id=f"{config.run_id}-STEP-001",
            run_id=config.run_id,
            name="Calculate and apply K factor",
            step_type=WorkflowStepType.DEVICE_WRITE,
            status=WorkflowStepStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._repository.save_step(step)
        calibration = calculate_k_factor(
            KFactorCalibrationInput(
                mass_acc_before=config.mass_acc_before,
                mass_acc_after=config.mass_acc_after,
                standard_mass=config.standard_mass,
                current_k_factor=config.current_k_factor,
            )
        )
        decision = self._write_guard.evaluate(
            device,
            ParameterWriteRequest(
                parameter_name=config.k_factor_parameter,
                new_value=calibration.corrected_k_factor,
                mode=WriteMode.ARMED,
                actor=config.operator,
                workflow_state="calibration_write_armed",
                run_id=config.run_id,
                metadata={"calibration": "k_factor"},
            ),
        )
        passed = decision.allowed and decision.result.status.value == "applied"
        self._repository.save_step(
            WorkflowStep(
                step_id=step.step_id,
                run_id=step.run_id,
                name=step.name,
                step_type=step.step_type,
                status=WorkflowStepStatus.PASSED
                if passed
                else WorkflowStepStatus.FAILED,
                started_at=step.started_at,
                ended_at=datetime.now(UTC),
                input_configuration={
                    "mass_acc_before": config.mass_acc_before,
                    "mass_acc_after": config.mass_acc_after,
                    "standard_mass": config.standard_mass,
                    "current_k_factor": config.current_k_factor,
                },
                output_summary={
                    "measured_mass_delta": calibration.measured_mass_delta,
                    "corrected_k_factor": calibration.corrected_k_factor,
                    "write_status": decision.result.status.value,
                    "audit_id": decision.audit_id,
                },
                error_message=decision.result.message,
            )
        )
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{config.run_id}-KFACTOR",
                run_id=config.run_id,
                step_id=step.step_id,
                result_type="k_factor_calibration",
                algorithm_name="manual_mass_total_k_factor",
                algorithm_version="0.1",
                configuration_snapshot={"formula": "k_s = k_r / m_r * m_s"},
                summary_metrics={
                    "mass_acc_before": calibration.mass_acc_before,
                    "mass_acc_after": calibration.mass_acc_after,
                    "measured_mass_delta": calibration.measured_mass_delta,
                    "standard_mass": calibration.standard_mass,
                    "current_k_factor": calibration.current_k_factor,
                    "corrected_k_factor": calibration.corrected_k_factor,
                },
                pass_fail_decision="passed" if passed else "failed",
                created_at=datetime.now(UTC),
            )
        )
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.CALIBRATION,
                workflow_name="k_factor_calibration",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.PASSED if passed else RunStatus.FAILED,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                configuration_snapshot={
                    "mass_acc_before": config.mass_acc_before,
                    "mass_acc_after": config.mass_acc_after,
                    "standard_mass": config.standard_mass,
                    "current_k_factor": config.current_k_factor,
                    "k_factor_parameter": config.k_factor_parameter,
                },
                software_version=config.software_version,
            )
        )
        return KFactorCalibrationWorkflowResult(
            run_id=config.run_id,
            calibration=calibration,
            audit_id=decision.audit_id,
            write_status=decision.result.status.value,
        )


class RepeatabilityTestWorkflow:
    """Stores manual mass-total error and repeatability test results."""

    def __init__(self, repository: StorageRepository) -> None:
        self._repository = repository

    def run(
        self,
        device: FlowmeterDevice,
        config: RepeatabilityTestConfig,
    ) -> RepeatabilityTestWorkflowResult:
        if len({trial.flow_point for trial in config.trials}) != config.expected_flow_point_count:
            raise ValueError(
                f"Repeatability test requires {config.expected_flow_point_count} flow points."
            )
        started_at = datetime.now(UTC)
        device.connect()
        identity = device.read_identity()
        _save_identity(self._repository, identity)
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.ERROR_ANALYSIS,
                workflow_name="manual_error_repeatability",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot={
                    "expected_flow_point_count": config.expected_flow_point_count,
                    "expected_trials_per_point": config.expected_trials_per_point,
                },
                software_version=config.software_version,
            )
        )
        step = WorkflowStep(
            step_id=f"{config.run_id}-STEP-001",
            run_id=config.run_id,
            name="Analyze manual error and repeatability trials",
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.RUNNING,
            started_at=datetime.now(UTC),
            input_configuration={
                "trials": [_trial_to_dict(trial) for trial in config.trials],
            },
        )
        self._repository.save_step(step)
        result = analyze_repeatability(
            config.trials,
            expected_trials_per_point=config.expected_trials_per_point,
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
                    "flow_points": [
                        {
                            "flow_point": point.flow_point,
                            "repeatability_stddev_percent": point.repeatability_stddev_percent,
                            "trial_errors": [
                                trial.percent_error for trial in point.trials
                            ],
                        }
                        for point in result.flow_points
                    ],
                    **result.summary_metrics,
                },
            )
        )
        self._repository.save_analysis_result(
            AnalysisResultRecord(
                result_id=f"{config.run_id}-REPEATABILITY",
                run_id=config.run_id,
                step_id=step.step_id,
                result_type="manual_error_repeatability",
                algorithm_name="manual_mass_total_repeatability",
                algorithm_version="0.1",
                configuration_snapshot={
                    "formula": "e = (m_1 - m_2) / m_2 * 100%",
                    "repeatability": "sample standard deviation of percent errors per flow point",
                },
                summary_metrics=result.summary_metrics,
                pass_fail_decision="calculated",
                created_at=datetime.now(UTC),
            )
        )
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.ERROR_ANALYSIS,
                workflow_name="manual_error_repeatability",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.PASSED,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                configuration_snapshot={
                    "expected_flow_point_count": config.expected_flow_point_count,
                    "expected_trials_per_point": config.expected_trials_per_point,
                },
                software_version=config.software_version,
            )
        )
        return RepeatabilityTestWorkflowResult(run_id=config.run_id, result=result)


def capture_flow_segment(
    device: FlowmeterDevice,
    config: FlowSegmentCaptureConfig,
) -> FlowSegmentCaptureResult:
    """Detect one non-zero flow segment and capture reusable timing values."""

    if config.poll_interval_s <= 0:
        raise ValueError("Flow segment poll interval must be positive.")
    if config.post_start_sample_s < 0:
        raise ValueError("Flow segment start sample delay cannot be negative.")
    if config.post_stop_delay_s < 0:
        raise ValueError("Flow segment stop delay cannot be negative.")
    if config.max_wait_start_polls < 1 or config.max_wait_stop_polls < 1:
        raise ValueError("Flow segment poll limits must be positive.")

    poll_count = 0
    start_flow = 0.0
    started_at: datetime | None = None
    for _ in range(config.max_wait_start_polls):
        _raise_if_flow_segment_canceled(config)
        start_flow = _read_float_parameter(device, config.flow_rate_parameter)
        poll_count += 1
        if abs(start_flow) > config.nonzero_threshold:
            started_at = datetime.now(UTC)
            break
        _sleep_flow_segment(config.poll_interval_s, config)
    if started_at is None:
        raise TimeoutError(
            f"Flow segment did not start on {config.flow_rate_parameter}."
        )

    if config.post_start_sample_s:
        _sleep_flow_segment(config.post_start_sample_s, config)
    _raise_if_flow_segment_canceled(config)
    instant_flow = _read_float_parameter(device, config.flow_rate_parameter)
    poll_count += 1
    instant_flow_at = datetime.now(UTC)

    stop_flow = instant_flow
    stopped = False
    for _ in range(config.max_wait_stop_polls):
        _raise_if_flow_segment_canceled(config)
        stop_flow = _read_float_parameter(device, config.flow_rate_parameter)
        poll_count += 1
        if abs(stop_flow) <= config.nonzero_threshold:
            stopped = True
            break
        _sleep_flow_segment(config.poll_interval_s, config)
    if not stopped:
        raise TimeoutError(
            f"Flow segment did not stop on {config.flow_rate_parameter}."
        )

    if config.post_stop_delay_s:
        _sleep_flow_segment(config.post_stop_delay_s, config)
    _raise_if_flow_segment_canceled(config)
    ended_at = datetime.now(UTC)
    return FlowSegmentCaptureResult(
        flow_rate_parameter=config.flow_rate_parameter,
        started_at=started_at,
        instant_flow_at=instant_flow_at,
        ended_at=ended_at,
        start_flow=start_flow,
        instant_flow=instant_flow,
        stop_flow=stop_flow,
        poll_count=poll_count,
    )


def _raise_if_flow_segment_canceled(config: FlowSegmentCaptureConfig) -> None:
    if config.cancel_requested is not None and config.cancel_requested():
        raise RuntimeError("K factor capture canceled.")


def _sleep_flow_segment(seconds: float, config: FlowSegmentCaptureConfig) -> None:
    remaining = seconds
    while remaining > 0:
        _raise_if_flow_segment_canceled(config)
        interval = min(remaining, 0.05)
        sleep(interval)
        remaining -= interval
    _raise_if_flow_segment_canceled(config)


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


def _save_identity(repository: StorageRepository, identity: DeviceIdentity) -> None:
    repository.save_device(
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


def _trial_to_dict(trial: RepeatabilityTrial) -> dict[str, float | int]:
    return {
        "flow_point": trial.flow_point,
        "trial_index": trial.trial_index,
        "mass_acc_before": trial.mass_acc_before,
        "mass_acc_after": trial.mass_acc_after,
        "standard_mass": trial.standard_mass,
    }


def _zero_snapshot(
    parameters: tuple[ConfigurationParameter, ...],
    *,
    zero_offset_name: str,
    delta_t_name: str,
) -> ZeroCalibrationSnapshot:
    return ZeroCalibrationSnapshot(
        zero_offset=float(_parameter_value(parameters, zero_offset_name)),
        delta_t=float(_parameter_value(parameters, delta_t_name)),
        captured_at=datetime.now(UTC),
    )


def _pre_calibration_snapshot(
    device: FlowmeterDevice,
    names: tuple[str, ...],
) -> tuple[dict[str, object], datetime | None]:
    unique_names = tuple(dict.fromkeys(name for name in names if name))
    if not unique_names:
        return {}, None
    parameters = _read_named_configuration(device, unique_names)
    captured_at = datetime.now(UTC)
    return (
        {
            name: _json_metric_value(_parameter_value(parameters, name))
            for name in unique_names
        },
        captured_at,
    )


def _read_named_configuration(
    device: FlowmeterDevice,
    names: tuple[str, ...],
) -> tuple[ConfigurationParameter, ...]:
    reader = getattr(device, "read_configuration_parameters", None)
    if callable(reader):
        return reader(names)
    parameters = device.read_configuration()
    allowed = set(names)
    return tuple(parameter for parameter in parameters if parameter.name in allowed)


def _parameter_value(
    parameters: tuple[ConfigurationParameter, ...],
    name: str,
) -> object:
    for parameter in parameters:
        if parameter.name == name:
            return parameter.value
    raise ValueError(f"Missing required parameter: {name}")


def _read_float_parameter(device: FlowmeterDevice, name: str) -> float:
    return float(_parameter_value(_read_named_configuration(device, (name,)), name))


def _json_metric_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)

"""Flexible simulator-backed experiment workflow."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from coreflow.devices import FlowmeterDevice, Measurement
from coreflow.experiments import (
    BasicSignalStatsModule,
    ExperimentDefinition,
    FixtureController,
    MLInferenceModule,
    NoopFixtureController,
    NoopMLInferenceModule,
    SignalProcessingModule,
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
class ExperimentWorkflowConfig:
    """Inputs for running a flexible experiment."""

    run_id: str
    operator: str
    definition: ExperimentDefinition
    workflow_version: str = "0.1"
    software_version: str = "0.1.0"


@dataclass(frozen=True, slots=True)
class ExperimentWorkflowResult:
    """Summary returned after an experiment workflow run."""

    run_id: str
    raw_artifact_id: str
    processed_artifact_ids: tuple[str, ...]
    analysis_result_ids: tuple[str, ...]
    fixture_messages: tuple[str, ...]
    ml_messages: tuple[str, ...]


class ExperimentWorkflow:
    """Headless flexible experiment workflow for simulator-first R&D runs."""

    def __init__(
        self,
        repository: StorageRepository,
        artifact_store: ArtifactStore,
        processing_modules: tuple[SignalProcessingModule, ...] | None = None,
        fixture_controller: FixtureController | None = None,
        ml_module: MLInferenceModule | None = None,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        modules = processing_modules or (BasicSignalStatsModule(),)
        self._processing_modules = {module.name: module for module in modules}
        self._fixture_controller = fixture_controller or NoopFixtureController()
        self._ml_module = ml_module or NoopMLInferenceModule()

    def run(
        self,
        device: FlowmeterDevice,
        config: ExperimentWorkflowConfig,
    ) -> ExperimentWorkflowResult:
        if config.definition.capture_plan.sample_count <= 0:
            raise ValueError("Experiment capture requires at least one sample.")
        if not config.definition.processing:
            raise ValueError("Experiment requires at least one processing module.")

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
                run_type=RunType.EXPERIMENT,
                workflow_name="flexible_experiment",
                workflow_version=config.workflow_version,
                device_id=identity.device_id,
                operator=config.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot=config.definition.configuration_snapshot(),
                software_version=config.software_version,
            )
        )

        try:
            fixture_messages = self._run_fixture_actions(config, started_at)
            samples, raw_artifact_id = self._capture_samples(device, config, started_at)
            processed_artifact_ids, analysis_result_ids = self._run_processing(
                samples,
                config,
                started_at,
                raw_artifact_id,
            )
            ml_messages = self._run_ml_placeholders(samples, config, started_at)
        except Exception as exc:
            self._save_run(
                config=config,
                device_id=identity.device_id,
                status=RunStatus.ERROR,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                notes=str(exc),
            )
            raise

        self._save_run(
            config=config,
            device_id=identity.device_id,
            status=RunStatus.PASSED,
            started_at=started_at,
            ended_at=datetime.now(UTC),
        )
        return ExperimentWorkflowResult(
            run_id=config.run_id,
            raw_artifact_id=raw_artifact_id,
            processed_artifact_ids=tuple(processed_artifact_ids),
            analysis_result_ids=tuple(analysis_result_ids),
            fixture_messages=tuple(fixture_messages),
            ml_messages=tuple(ml_messages),
        )

    def _save_run(
        self,
        *,
        config: ExperimentWorkflowConfig,
        device_id: str,
        status: RunStatus,
        started_at: datetime,
        ended_at: datetime | None = None,
        notes: str | None = None,
    ) -> None:
        self._repository.save_run(
            RunSession(
                run_id=config.run_id,
                run_type=RunType.EXPERIMENT,
                workflow_name="flexible_experiment",
                workflow_version=config.workflow_version,
                device_id=device_id,
                operator=config.operator,
                status=status,
                started_at=started_at,
                ended_at=ended_at,
                configuration_snapshot=config.definition.configuration_snapshot(),
                software_version=config.software_version,
                notes=notes,
            )
        )

    def _run_fixture_actions(
        self,
        config: ExperimentWorkflowConfig,
        started_at: datetime,
    ) -> list[str]:
        messages: list[str] = []
        for index, action in enumerate(config.definition.fixture_actions, start=1):
            step_id = f"{config.run_id}-FIXTURE-{index:03d}"
            result = self._fixture_controller.execute(action)
            messages.append(result.message)
            status = (
                WorkflowStepStatus.FAILED
                if action.required and not result.supported
                else WorkflowStepStatus.SKIPPED
            )
            self._repository.save_step(
                WorkflowStep(
                    step_id=step_id,
                    run_id=config.run_id,
                    name=f"Fixture action {action.action_name}",
                    step_type=WorkflowStepType.DEVICE_WRITE,
                    status=status,
                    started_at=started_at,
                    ended_at=datetime.now(UTC),
                    input_configuration=action.parameters,
                    output_summary={
                        "supported": result.supported,
                        "message": result.message,
                        "metadata": result.metadata,
                    },
                    error_message=result.message
                    if action.required and not result.supported
                    else None,
                )
            )
            if action.required and not result.supported:
                raise ValueError(f"Required fixture action is unsupported: {action.action_name}")
        return messages

    def _capture_samples(
        self,
        device: FlowmeterDevice,
        config: ExperimentWorkflowConfig,
        started_at: datetime,
    ) -> tuple[tuple[Measurement, ...], str]:
        capture_step_id = f"{config.run_id}-CAPTURE-001"
        self._repository.save_step(
            WorkflowStep(
                step_id=capture_step_id,
                run_id=config.run_id,
                name="Experiment capture",
                step_type=WorkflowStepType.CAPTURE,
                status=WorkflowStepStatus.RUNNING,
                started_at=datetime.now(UTC),
                input_configuration={
                    "sample_count": config.definition.capture_plan.sample_count,
                    "label": config.definition.capture_plan.label,
                    "capture_interval_ms": config.definition.capture_plan.capture_interval_ms,
                },
            )
        )
        samples = tuple(
            device.read_measurement()
            for _ in range(config.definition.capture_plan.sample_count)
        )
        raw_artifact_id = f"{config.run_id}-EXPERIMENT-RAW"
        artifact = self._artifact_store.write_artifact(
            run_id=config.run_id,
            artifact_id=raw_artifact_id,
            artifact_type=ArtifactType.RAW,
            file_name="experiment_samples.csv",
            content=_samples_csv(samples),
            created_at=started_at,
            step_id=capture_step_id,
            file_format="csv",
        )
        self._repository.save_artifact(artifact)
        self._repository.save_step(
            WorkflowStep(
                step_id=capture_step_id,
                run_id=config.run_id,
                name="Experiment capture",
                step_type=WorkflowStepType.CAPTURE,
                status=WorkflowStepStatus.PASSED,
                started_at=started_at,
                ended_at=datetime.now(UTC),
                input_configuration={
                    "sample_count": config.definition.capture_plan.sample_count,
                    "label": config.definition.capture_plan.label,
                    "capture_interval_ms": config.definition.capture_plan.capture_interval_ms,
                },
                output_summary={
                    "sample_count": len(samples),
                    "raw_artifact_id": raw_artifact_id,
                },
            )
        )
        return samples, raw_artifact_id

    def _run_processing(
        self,
        samples: tuple[Measurement, ...],
        config: ExperimentWorkflowConfig,
        started_at: datetime,
        raw_artifact_id: str,
    ) -> tuple[list[str], list[str]]:
        processed_artifact_ids: list[str] = []
        analysis_result_ids: list[str] = []
        for index, module_config in enumerate(config.definition.processing, start=1):
            module = self._processing_modules.get(module_config.module_name)
            if module is None:
                raise ValueError(f"Unknown processing module: {module_config.module_name}")
            step_id = f"{config.run_id}-PROCESS-{index:03d}"
            self._repository.save_step(
                WorkflowStep(
                    step_id=step_id,
                    run_id=config.run_id,
                    name=f"Process {module_config.module_name}",
                    step_type=WorkflowStepType.ANALYSIS,
                    status=WorkflowStepStatus.RUNNING,
                    started_at=datetime.now(UTC),
                    input_configuration=module_config.parameters,
                )
            )
            result = module.process(samples, module_config)
            processed_artifact_id = f"{config.run_id}-PROCESSED-{index:03d}"
            artifact = self._artifact_store.write_artifact(
                run_id=config.run_id,
                artifact_id=processed_artifact_id,
                artifact_type=ArtifactType.PROCESSED,
                file_name=f"{module_config.module_name}.csv",
                content=_processed_csv(result.output_rows),
                created_at=started_at,
                step_id=step_id,
                file_format="csv",
            )
            self._repository.save_artifact(artifact)
            result_id = f"{config.run_id}-EXPERIMENT-RESULT-{index:03d}"
            self._repository.save_analysis_result(
                AnalysisResultRecord(
                    result_id=result_id,
                    run_id=config.run_id,
                    step_id=step_id,
                    result_type="experiment_signal_processing",
                    algorithm_name=result.module_name,
                    algorithm_version=result.module_version,
                    input_artifact_ids=(raw_artifact_id,),
                    configuration_snapshot=module_config.parameters,
                    summary_metrics=result.summary_metrics,
                    pass_fail_decision="processed",
                    created_at=datetime.now(UTC),
                )
            )
            self._repository.save_step(
                WorkflowStep(
                    step_id=step_id,
                    run_id=config.run_id,
                    name=f"Process {module_config.module_name}",
                    step_type=WorkflowStepType.ANALYSIS,
                    status=WorkflowStepStatus.PASSED,
                    started_at=started_at,
                    ended_at=datetime.now(UTC),
                    input_configuration=module_config.parameters,
                    output_summary={
                        "processed_artifact_id": processed_artifact_id,
                        "analysis_result_id": result_id,
                        "summary_metrics": result.summary_metrics,
                    },
                )
            )
            processed_artifact_ids.append(processed_artifact_id)
            analysis_result_ids.append(result_id)
        return processed_artifact_ids, analysis_result_ids

    def _run_ml_placeholders(
        self,
        samples: tuple[Measurement, ...],
        config: ExperimentWorkflowConfig,
        started_at: datetime,
    ) -> list[str]:
        messages: list[str] = []
        for index, ml_config in enumerate(config.definition.ml_inference, start=1):
            if not ml_config.enabled:
                continue
            step_id = f"{config.run_id}-ML-{index:03d}"
            result = self._ml_module.infer(samples, ml_config)
            messages.append(result.message or "")
            self._repository.save_step(
                WorkflowStep(
                    step_id=step_id,
                    run_id=config.run_id,
                    name=f"ML inference {ml_config.model_name}",
                    step_type=WorkflowStepType.ANALYSIS,
                    status=WorkflowStepStatus.SKIPPED
                    if not result.executed
                    else WorkflowStepStatus.PASSED,
                    started_at=started_at,
                    ended_at=datetime.now(UTC),
                    input_configuration=ml_config.parameters,
                    output_summary={
                        "executed": result.executed,
                        "message": result.message,
                        "prediction_count": len(result.predictions),
                    },
                )
            )
        return messages


def _samples_csv(samples: tuple[Measurement, ...]) -> bytes:
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


def _processed_csv(rows: tuple[dict[str, object], ...]) -> bytes:
    buffer = io.StringIO()
    fieldnames = sorted({key for row in rows for key in row})
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")

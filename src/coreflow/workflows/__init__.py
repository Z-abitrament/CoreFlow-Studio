"""Workflow state models."""

from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)

__all__ = [
    "CalibrationPreviewConfig",
    "CalibrationPreviewWorkflow",
    "CalibrationPreviewWorkflowResult",
    "KFactorCalibrationConfig",
    "KFactorCalibrationWorkflow",
    "KFactorCalibrationWorkflowResult",
    "RepeatabilityTestConfig",
    "RepeatabilityTestWorkflow",
    "RepeatabilityTestWorkflowResult",
    "ZeroCalibrationConfig",
    "ZeroCalibrationWorkflow",
    "ZeroCalibrationWorkflowResult",
    "FactoryMeasurementCheck",
    "FactoryStabilityCheck",
    "FactoryTestConfig",
    "FactoryTestWorkflow",
    "FactoryTestWorkflowResult",
    "ExperimentWorkflow",
    "ExperimentWorkflowConfig",
    "ExperimentWorkflowResult",
    "RunSession",
    "RunStatus",
    "RunType",
    "WorkflowStep",
    "WorkflowStepStatus",
    "WorkflowStepType",
]


def __getattr__(name: str) -> object:
    if name in {
        "CalibrationPreviewConfig",
        "CalibrationPreviewWorkflow",
        "CalibrationPreviewWorkflowResult",
        "KFactorCalibrationConfig",
        "KFactorCalibrationWorkflow",
        "KFactorCalibrationWorkflowResult",
        "RepeatabilityTestConfig",
        "RepeatabilityTestWorkflow",
        "RepeatabilityTestWorkflowResult",
        "ZeroCalibrationConfig",
        "ZeroCalibrationWorkflow",
        "ZeroCalibrationWorkflowResult",
    }:
        from coreflow.workflows.calibration import (
            CalibrationPreviewConfig,
            CalibrationPreviewWorkflow,
            CalibrationPreviewWorkflowResult,
            KFactorCalibrationConfig,
            KFactorCalibrationWorkflow,
            KFactorCalibrationWorkflowResult,
            RepeatabilityTestConfig,
            RepeatabilityTestWorkflow,
            RepeatabilityTestWorkflowResult,
            ZeroCalibrationConfig,
            ZeroCalibrationWorkflow,
            ZeroCalibrationWorkflowResult,
        )

        exports = {
            "CalibrationPreviewConfig": CalibrationPreviewConfig,
            "CalibrationPreviewWorkflow": CalibrationPreviewWorkflow,
            "CalibrationPreviewWorkflowResult": CalibrationPreviewWorkflowResult,
            "KFactorCalibrationConfig": KFactorCalibrationConfig,
            "KFactorCalibrationWorkflow": KFactorCalibrationWorkflow,
            "KFactorCalibrationWorkflowResult": KFactorCalibrationWorkflowResult,
            "RepeatabilityTestConfig": RepeatabilityTestConfig,
            "RepeatabilityTestWorkflow": RepeatabilityTestWorkflow,
            "RepeatabilityTestWorkflowResult": RepeatabilityTestWorkflowResult,
            "ZeroCalibrationConfig": ZeroCalibrationConfig,
            "ZeroCalibrationWorkflow": ZeroCalibrationWorkflow,
            "ZeroCalibrationWorkflowResult": ZeroCalibrationWorkflowResult,
        }
        return exports[name]
    if name in {
        "FactoryMeasurementCheck",
        "FactoryStabilityCheck",
        "FactoryTestConfig",
        "FactoryTestWorkflow",
        "FactoryTestWorkflowResult",
    }:
        from coreflow.workflows.factory_test import (
            FactoryMeasurementCheck,
            FactoryStabilityCheck,
            FactoryTestConfig,
            FactoryTestWorkflow,
            FactoryTestWorkflowResult,
        )

        exports = {
            "FactoryMeasurementCheck": FactoryMeasurementCheck,
            "FactoryStabilityCheck": FactoryStabilityCheck,
            "FactoryTestConfig": FactoryTestConfig,
            "FactoryTestWorkflow": FactoryTestWorkflow,
            "FactoryTestWorkflowResult": FactoryTestWorkflowResult,
        }
        return exports[name]
    if name in {
        "ExperimentWorkflow",
        "ExperimentWorkflowConfig",
        "ExperimentWorkflowResult",
    }:
        from coreflow.workflows.experiment import (
            ExperimentWorkflow,
            ExperimentWorkflowConfig,
            ExperimentWorkflowResult,
        )

        exports = {
            "ExperimentWorkflow": ExperimentWorkflow,
            "ExperimentWorkflowConfig": ExperimentWorkflowConfig,
            "ExperimentWorkflowResult": ExperimentWorkflowResult,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

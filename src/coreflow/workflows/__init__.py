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
    "FactoryMeasurementCheck",
    "FactoryStabilityCheck",
    "FactoryTestConfig",
    "FactoryTestWorkflow",
    "FactoryTestWorkflowResult",
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
    }:
        from coreflow.workflows.calibration import (
            CalibrationPreviewConfig,
            CalibrationPreviewWorkflow,
            CalibrationPreviewWorkflowResult,
        )

        exports = {
            "CalibrationPreviewConfig": CalibrationPreviewConfig,
            "CalibrationPreviewWorkflow": CalibrationPreviewWorkflow,
            "CalibrationPreviewWorkflowResult": CalibrationPreviewWorkflowResult,
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

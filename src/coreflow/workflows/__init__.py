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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

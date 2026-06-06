"""Workflow state value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunType(StrEnum):
    """Top-level workflow run categories."""

    CALIBRATION = "calibration"
    FACTORY_TEST = "factory_test"
    ERROR_ANALYSIS = "error_analysis"
    STABILITY = "stability"
    EXPERIMENT = "experiment"
    HARDWARE_ACCEPTANCE = "hardware_acceptance"


class RunStatus(StrEnum):
    """Lifecycle status for a run session."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    CANCELED = "canceled"
    ERROR = "error"


class WorkflowStepType(StrEnum):
    """Workflow step categories used by initial milestones."""

    DEVICE_READ = "device_read"
    DEVICE_WRITE = "device_write"
    CAPTURE = "capture"
    ANALYSIS = "analysis"
    PASS_FAIL_CHECK = "pass_fail_check"
    REPORT = "report"


class WorkflowStepStatus(StrEnum):
    """Lifecycle status for one workflow step."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RunSession:
    """Metadata for one calibration, test, analysis, or experiment run."""

    run_id: str
    run_type: RunType
    workflow_name: str
    workflow_version: str
    device_id: str
    operator: str
    status: RunStatus = RunStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)
    software_version: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """Metadata and outcome summary for one step inside a run."""

    step_id: str
    run_id: str
    name: str
    step_type: WorkflowStepType
    status: WorkflowStepStatus = WorkflowStepStatus.PENDING
    started_at: datetime | None = None
    ended_at: datetime | None = None
    input_configuration: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

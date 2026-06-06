"""Experiment definition models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CapturePlan:
    """Sample capture settings for a flexible experiment."""

    sample_count: int
    label: str = "experiment_capture"
    capture_interval_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ProcessingModuleConfig:
    """Configured signal-processing module invocation."""

    module_name: str
    module_version: str = "0.1"
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FixtureAction:
    """Placeholder fixture action requested by an experiment definition."""

    action_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    required: bool = False


@dataclass(frozen=True, slots=True)
class MLInferenceConfig:
    """Placeholder ML module invocation requested by an experiment definition."""

    model_name: str
    model_version: str = "placeholder"
    parameters: dict[str, Any] = field(default_factory=dict)
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    """A simulator-runnable R&D experiment definition."""

    experiment_id: str
    name: str
    version: str
    capture_plan: CapturePlan
    processing: tuple[ProcessingModuleConfig, ...]
    fixture_actions: tuple[FixtureAction, ...] = ()
    ml_inference: tuple[MLInferenceConfig, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def configuration_snapshot(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "version": self.version,
            "capture_plan": {
                "sample_count": self.capture_plan.sample_count,
                "label": self.capture_plan.label,
                "capture_interval_ms": self.capture_plan.capture_interval_ms,
            },
            "processing": [
                {
                    "module_name": module.module_name,
                    "module_version": module.module_version,
                    "parameters": module.parameters,
                }
                for module in self.processing
            ],
            "fixture_actions": [
                {
                    "action_name": action.action_name,
                    "parameters": action.parameters,
                    "required": action.required,
                }
                for action in self.fixture_actions
            ],
            "ml_inference": [
                {
                    "model_name": model.model_name,
                    "model_version": model.model_version,
                    "parameters": model.parameters,
                    "enabled": model.enabled,
                }
                for model in self.ml_inference
            ],
            "metadata": self.metadata,
        }

"""Extension interfaces for flexible experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from coreflow.devices import Measurement
from coreflow.experiments.models import (
    FixtureAction,
    MLInferenceConfig,
    ProcessingModuleConfig,
)


@dataclass(frozen=True, slots=True)
class SignalProcessingResult:
    """Result returned by an experiment signal-processing module."""

    module_name: str
    module_version: str
    summary_metrics: dict[str, float]
    output_rows: tuple[dict[str, Any], ...] = ()


class SignalProcessingModule(Protocol):
    """Plugin-style signal-processing module contract."""

    name: str
    version: str

    def process(
        self,
        samples: tuple[Measurement, ...],
        config: ProcessingModuleConfig,
    ) -> SignalProcessingResult: ...


@dataclass(frozen=True, slots=True)
class FixtureActionResult:
    """Outcome of a fixture placeholder action."""

    action_name: str
    supported: bool
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


class FixtureController(Protocol):
    """Placeholder boundary for future physical fixture control."""

    def execute(self, action: FixtureAction) -> FixtureActionResult: ...


@dataclass(frozen=True, slots=True)
class NoopFixtureController:
    """Records requested fixture actions without touching hardware."""

    def execute(self, action: FixtureAction) -> FixtureActionResult:
        return FixtureActionResult(
            action_name=action.action_name,
            supported=False,
            message="Fixture control is not configured for v1 simulator experiments.",
            metadata={"required": action.required, "parameters": action.parameters},
        )


@dataclass(frozen=True, slots=True)
class MLInferenceResult:
    """Outcome of an ML placeholder invocation."""

    model_name: str
    model_version: str
    executed: bool
    predictions: tuple[dict[str, Any], ...] = ()
    message: str | None = None


class MLInferenceModule(Protocol):
    """Placeholder boundary for future ML inference modules."""

    def infer(
        self,
        samples: tuple[Measurement, ...],
        config: MLInferenceConfig,
    ) -> MLInferenceResult: ...


@dataclass(frozen=True, slots=True)
class NoopMLInferenceModule:
    """Keeps ML wiring testable without selecting a model runtime."""

    def infer(
        self,
        samples: tuple[Measurement, ...],
        config: MLInferenceConfig,
    ) -> MLInferenceResult:
        return MLInferenceResult(
            model_name=config.model_name,
            model_version=config.model_version,
            executed=False,
            message="ML inference is configured as a placeholder.",
        )

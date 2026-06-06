"""Flexible experiment definitions and extension interfaces."""

from coreflow.experiments.interfaces import (
    FixtureActionResult,
    FixtureController,
    MLInferenceModule,
    MLInferenceResult,
    NoopFixtureController,
    NoopMLInferenceModule,
    SignalProcessingModule,
    SignalProcessingResult,
)
from coreflow.experiments.models import (
    CapturePlan,
    ExperimentDefinition,
    FixtureAction,
    MLInferenceConfig,
    ProcessingModuleConfig,
)
from coreflow.experiments.processing import BasicSignalStatsModule

__all__ = [
    "BasicSignalStatsModule",
    "CapturePlan",
    "ExperimentDefinition",
    "FixtureAction",
    "FixtureActionResult",
    "FixtureController",
    "MLInferenceConfig",
    "MLInferenceModule",
    "MLInferenceResult",
    "NoopFixtureController",
    "NoopMLInferenceModule",
    "ProcessingModuleConfig",
    "SignalProcessingModule",
    "SignalProcessingResult",
]

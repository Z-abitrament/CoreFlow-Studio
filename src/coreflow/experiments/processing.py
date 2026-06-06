"""Example signal-processing modules for flexible experiments."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, pstdev

from coreflow.devices import Measurement
from coreflow.experiments.interfaces import SignalProcessingResult
from coreflow.experiments.models import ProcessingModuleConfig


@dataclass(frozen=True, slots=True)
class BasicSignalStatsModule:
    """Initial example module for simulator-backed experiment processing."""

    name: str = "basic_signal_stats"
    version: str = "0.1"

    def process(
        self,
        samples: tuple[Measurement, ...],
        config: ProcessingModuleConfig,
    ) -> SignalProcessingResult:
        values = [
            sample.mass_flow
            for sample in samples
            if sample.mass_flow is not None
        ]
        if not values:
            raise ValueError("Signal processing requires mass-flow samples.")
        value_range = max(values) - min(values)
        stddev = pstdev(values) if len(values) > 1 else 0.0
        metrics = {
            "sample_count": float(len(values)),
            "mass_flow_mean": mean(values),
            "mass_flow_min": min(values),
            "mass_flow_max": max(values),
            "mass_flow_range": value_range,
            "mass_flow_stddev": stddev,
        }
        return SignalProcessingResult(
            module_name=config.module_name,
            module_version=config.module_version,
            summary_metrics=metrics,
            output_rows=tuple(
                {
                    "sample_index": index,
                    "mass_flow": value,
                    "mean_delta": value - metrics["mass_flow_mean"],
                }
                for index, value in enumerate(values)
            ),
        )

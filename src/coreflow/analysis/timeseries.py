"""Helpers for loading simple time-series artifacts."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TimeSeriesSample:
    """One timestamped scalar sample for analysis."""

    timestamp: datetime
    value: float | None


def load_mass_flow_csv(path: Path) -> tuple[TimeSeriesSample, ...]:
    """Load mass-flow samples from CoreFlow CSV artifacts."""

    samples: list[TimeSeriesSample] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = datetime.fromisoformat(row["captured_at"])
            raw_value = row.get("mass_flow")
            value = None if raw_value in (None, "", "nan") else float(raw_value)
            samples.append(TimeSeriesSample(timestamp=timestamp, value=value))
    return tuple(samples)

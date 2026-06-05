"""Storage value objects for metadata and linked files."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from typing import Any


class ArtifactType(StrEnum):
    """File artifact categories tracked by SQLite metadata."""

    RAW = "raw"
    PROCESSED = "processed"
    EXPORT = "export"
    REPORT = "report"
    LOG = "log"
    REPLAY = "replay"
    CONFIG_SNAPSHOT = "config_snapshot"


@dataclass(frozen=True, slots=True)
class Artifact:
    """A file linked to a run, step, result, or configuration snapshot."""

    artifact_id: str
    run_id: str
    artifact_type: ArtifactType
    file_path: PurePath
    file_format: str
    step_id: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

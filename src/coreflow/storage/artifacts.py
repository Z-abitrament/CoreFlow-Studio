"""Artifact file storage for run-linked files."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path, PurePath

from coreflow.storage.models import Artifact, ArtifactType


class ArtifactStore:
    """Writes artifacts under a configured local data root."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)

    def next_run_id(self, when: datetime, sequence: int) -> str:
        return f"RUN-{when:%Y%m%d}-{sequence:06d}"

    def run_directory(self, run_id: str, when: datetime) -> Path:
        return self.data_root / "artifacts" / "runs" / f"{when:%Y}" / f"{when:%m}" / run_id

    def write_artifact(
        self,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        file_name: str,
        content: bytes,
        created_at: datetime,
        step_id: str | None = None,
        file_format: str | None = None,
    ) -> Artifact:
        directory = self.run_directory(run_id, created_at) / artifact_type.value
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / file_name
        path.write_bytes(content)
        relative_path = self.relative_path(path)
        return Artifact(
            artifact_id=artifact_id,
            run_id=run_id,
            step_id=step_id,
            artifact_type=artifact_type,
            file_path=relative_path,
            file_format=file_format or _suffix_format(path),
            size_bytes=len(content),
            checksum=_sha256(content),
            created_at=created_at,
        )

    def resolve(self, path: PurePath) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return self.data_root / candidate

    def relative_path(self, path: Path) -> PurePath:
        return PurePath(path.relative_to(self.data_root))


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _suffix_format(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "bin"

"""Data integrity checks for SQLite artifact references."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coreflow.storage.repositories import StorageRepository


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    """A missing or inconsistent artifact reference."""

    artifact_id: str
    message: str


def check_artifact_integrity(
    repository: StorageRepository,
    data_root: Path,
    run_id: str | None = None,
) -> tuple[IntegrityIssue, ...]:
    issues: list[IntegrityIssue] = []
    for artifact in repository.list_artifacts(run_id=run_id):
        path = Path(artifact.file_path)
        resolved = path if path.is_absolute() else data_root / path
        if not resolved.exists():
            issues.append(
                IntegrityIssue(
                    artifact_id=artifact.artifact_id,
                    message=f"Missing artifact file: {artifact.file_path}",
                )
            )
    return tuple(issues)

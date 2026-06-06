"""Application path conventions for local desktop deployments."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path


def default_user_data_root() -> Path:
    """Return the default writable data directory for CoreFlow Studio."""

    override = os.environ.get("COREFLOW_DATA_ROOT")
    if override:
        return Path(override)
    for candidate in _default_data_root_candidates():
        if _can_create_directory(candidate):
            return candidate
    return Path(tempfile.gettempdir()) / "CoreFlow Studio"


def _default_data_root_candidates() -> Iterator[Path]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        yield Path(local_app_data) / "CoreFlow Studio"
    app_data = os.environ.get("APPDATA")
    if app_data:
        yield Path(app_data) / "CoreFlow Studio"
    try:
        yield Path.home() / ".coreflow-studio"
    except RuntimeError:
        pass
    if os.environ.get("COREFLOW_PACKAGED") == "1":
        yield Path(sys.executable).resolve().parent / "CoreFlowStudioData"
    yield Path.cwd() / "CoreFlowStudioData"
    yield Path(tempfile.gettempdir()) / "CoreFlow Studio"


def _can_create_directory(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return True

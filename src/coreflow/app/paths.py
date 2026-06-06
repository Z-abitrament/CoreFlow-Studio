"""Application path conventions for local desktop deployments."""

from __future__ import annotations

import os
from pathlib import Path


def default_user_data_root() -> Path:
    """Return the default writable data directory for CoreFlow Studio."""

    override = os.environ.get("COREFLOW_DATA_ROOT")
    if override:
        return Path(override)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "CoreFlow Studio"
    return Path.home() / ".coreflow-studio"

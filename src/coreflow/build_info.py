"""Build and version metadata exposed to packaged deployments."""

from __future__ import annotations

import os
from dataclasses import dataclass

from coreflow import __version__


@dataclass(frozen=True, slots=True)
class BuildInfo:
    """Version stamp for source and packaged builds."""

    version: str
    commit: str
    build_channel: str


def current_build_info() -> BuildInfo:
    return BuildInfo(
        version=__version__,
        commit=os.environ.get("COREFLOW_BUILD_COMMIT", "local"),
        build_channel=os.environ.get("COREFLOW_BUILD_CHANNEL", "development"),
    )

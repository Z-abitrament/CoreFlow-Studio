"""Application services."""

from coreflow.app.write_guard import WriteGuardDecision, WriteGuardService

__all__ = [
    "ChannelSnapshot",
    "CoreFlowRuntime",
    "RunInspection",
    "WriteGuardDecision",
    "WriteGuardService",
]


def __getattr__(name: str) -> object:
    if name in {"ChannelSnapshot", "CoreFlowRuntime", "RunInspection"}:
        from coreflow.app.runtime import (
            ChannelSnapshot,
            CoreFlowRuntime,
            RunInspection,
        )

        exports = {
            "ChannelSnapshot": ChannelSnapshot,
            "CoreFlowRuntime": CoreFlowRuntime,
            "RunInspection": RunInspection,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

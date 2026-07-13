"""Application services."""

from coreflow.app.write_guard import WriteGuardDecision, WriteGuardService

__all__ = [
    "ChannelSnapshot",
    "CoreFlowRuntime",
    "FillingAnalysisRecord",
    "FillingConfiguration",
    "FillingGroupSnapshot",
    "FillingHistoryEntry",
    "FillingMode",
    "FillingTrialService",
    "ModbusConnectionSettings",
    "ModbusModuleRuntime",
    "ModbusModuleStatus",
    "RunInspection",
    "WriteGuardDecision",
    "WriteGuardService",
]


def __getattr__(name: str) -> object:
    if name in {
        "FillingAnalysisRecord",
        "FillingConfiguration",
        "FillingGroupSnapshot",
        "FillingHistoryEntry",
        "FillingMode",
        "FillingTrialService",
    }:
        from coreflow.app.filling import (
            FillingAnalysisRecord,
            FillingConfiguration,
            FillingGroupSnapshot,
            FillingHistoryEntry,
            FillingMode,
            FillingTrialService,
        )

        exports = {
            "FillingAnalysisRecord": FillingAnalysisRecord,
            "FillingConfiguration": FillingConfiguration,
            "FillingGroupSnapshot": FillingGroupSnapshot,
            "FillingHistoryEntry": FillingHistoryEntry,
            "FillingMode": FillingMode,
            "FillingTrialService": FillingTrialService,
        }
        return exports[name]
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
    if name in {
        "ModbusConnectionSettings",
        "ModbusModuleRuntime",
        "ModbusModuleStatus",
    }:
        from coreflow.app.modbus_runtime import (
            ModbusConnectionSettings,
            ModbusModuleRuntime,
            ModbusModuleStatus,
        )

        exports = {
            "ModbusConnectionSettings": ModbusConnectionSettings,
            "ModbusModuleRuntime": ModbusModuleRuntime,
            "ModbusModuleStatus": ModbusModuleStatus,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

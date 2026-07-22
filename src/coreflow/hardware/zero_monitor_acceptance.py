"""Executable read-only acceptance checks for an online zero-monitor device."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Protocol

from coreflow.analysis.zero_monitor import ZeroMonitorAnalysisConfig
from coreflow.app.modbus_runtime import (
    ModbusVariableSampleResult,
)
from coreflow.app.modbus_zero_monitor import ZeroMonitorRunResult


@dataclass(frozen=True, slots=True)
class ZeroMonitorHardwareCheck:
    check_id: str
    passed: bool
    message: str
    details: dict[str, object]


@dataclass(frozen=True, slots=True)
class ZeroMonitorHardwareAcceptanceResult:
    status: str
    validation_stage: str
    started_at: datetime
    ended_at: datetime
    run_id: str | None
    artifact_id: str | None
    checks: tuple[ZeroMonitorHardwareCheck, ...]
    evidence_path: Path

    @property
    def passed(self) -> bool:
        return self.status == "passed"


class ReadOnlyZeroMonitorRuntime(Protocol):
    def read_variables(
        self,
        variable_names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
    ) -> ModbusVariableSampleResult: ...

    def run_zero_monitor(
        self,
        analysis_config: ZeroMonitorAnalysisConfig,
        *,
        zero_flow_confirmed: bool,
        zero_flow_confirmed_at: datetime | None = None,
        max_polls: int | None = None,
    ) -> ZeroMonitorRunResult: ...


class ZeroMonitorHardwareAcceptanceRunner:
    """Run a bounded online stage without calling a device-write API."""

    def __init__(
        self,
        runtime: ReadOnlyZeroMonitorRuntime,
        *,
        evidence_path: Path,
        wall_clock=None,
    ) -> None:
        self.runtime = runtime
        self.evidence_path = Path(evidence_path)
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))

    def run(
        self,
        *,
        duration_s: float = 30.0,
        zero_flow_confirmed: bool = False,
    ) -> ZeroMonitorHardwareAcceptanceResult:
        if duration_s <= 0:
            raise ValueError("duration_s must be positive.")
        started_at = self._wall_clock()
        before = _sample_value(self.runtime.read_variables(("zero_offset",), merge_adjacent=True))
        expected_polls = max(1, ceil(duration_s * 1000.0 / 100.0))
        monitor = self.runtime.run_zero_monitor(
            ZeroMonitorAnalysisConfig.production_default(),
            zero_flow_confirmed=zero_flow_confirmed,
            zero_flow_confirmed_at=started_at if zero_flow_confirmed else None,
            max_polls=expected_polls,
        )
        after = _sample_value(self.runtime.read_variables(("zero_offset",), merge_adjacent=True))
        counters = monitor.counters
        logical = int(counters.get("logical_poll_count", 0))
        physical = int(counters.get("physical_request_count", 0))
        torn = int(counters.get("torn_snapshot_reread_count", 0))
        byte_order = monitor.byte_order_verification
        checks = (
            ZeroMonitorHardwareCheck(
                "ZMON-HW-001",
                monitor.run_id is not None and monitor.artifact_id is not None,
                "Capture produced a traceable run and raw artifact.",
                {"run_id": monitor.run_id, "artifact_id": monitor.artifact_id},
            ),
            ZeroMonitorHardwareCheck(
                "ZMON-HW-002",
                logical == expected_polls,
                "The bounded 100 ms stage completed the requested logical polls.",
                {"expected_polls": expected_polls, "logical_poll_count": logical},
            ),
            ZeroMonitorHardwareCheck(
                "ZMON-HW-003",
                physical == logical + torn,
                "Each logical poll used one request plus only the recorded torn rereads.",
                {
                    "logical_poll_count": logical,
                    "physical_request_count": physical,
                    "torn_snapshot_reread_count": torn,
                },
            ),
            ZeroMonitorHardwareCheck(
                "ZMON-HW-004",
                bool(byte_order and byte_order.verified),
                "The device ByteOrder enum matched the active profile.",
                byte_order.to_dict() if byte_order is not None else {},
            ),
            ZeroMonitorHardwareCheck(
                "ZMON-HW-005",
                before is not None and after is not None and before == after,
                "The read-only stage did not change ZeroOffset.",
                {"zero_offset_before": before, "zero_offset_after": after},
            ),
            ZeroMonitorHardwareCheck(
                "ZMON-HW-006",
                monitor.metrics.get("observed_period_mean_ms") is not None
                if expected_polls >= 2
                else True,
                "Observed host poll timing was recorded without applying an unapproved limit.",
                {
                    key: monitor.metrics.get(key)
                    for key in (
                        "observed_period_mean_ms",
                        "observed_period_p50_ms",
                        "observed_period_p95_ms",
                        "observed_period_p99_ms",
                        "observed_period_max_ms",
                        "achieved_poll_rate_hz",
                    )
                },
            ),
        )
        ended_at = self._wall_clock()
        status = "passed" if all(check.passed for check in checks) else "incomplete"
        result = ZeroMonitorHardwareAcceptanceResult(
            status=status,
            validation_stage="non_laboratory_online_read_only",
            started_at=started_at,
            ended_at=ended_at,
            run_id=monitor.run_id,
            artifact_id=monitor.artifact_id,
            checks=checks,
            evidence_path=self.evidence_path,
        )
        self._write_evidence(result, monitor)
        return result

    def _write_evidence(
        self,
        result: ZeroMonitorHardwareAcceptanceResult,
        monitor: ZeroMonitorRunResult,
    ) -> None:
        payload = {
            "schema_version": 1,
            "status": result.status,
            "validation_stage": result.validation_stage,
            "started_at": result.started_at.astimezone(UTC).isoformat(),
            "ended_at": result.ended_at.astimezone(UTC).isoformat(),
            "run_id": result.run_id,
            "artifact_id": result.artifact_id,
            "checks": [asdict(check) for check in result.checks],
            "monitor": {
                "state": monitor.state.value,
                "run_status": monitor.run_status.value,
                "counters": monitor.counters,
                "metrics": monitor.metrics,
                "reason_codes": list(monitor.reason_codes),
                "advisory_codes": list(monitor.advisory_codes),
                "byte_order_verification": (
                    monitor.byte_order_verification.to_dict()
                    if monitor.byte_order_verification is not None
                    else None
                ),
            },
            "claim_boundary": (
                "This evidence covers online read-only protocol behavior only; "
                "it is not zero-flow bench, laboratory, or production-threshold qualification."
            ),
        }
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.evidence_path.with_suffix(self.evidence_path.suffix + ".partial")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.evidence_path)


def _sample_value(result: ModbusVariableSampleResult) -> object | None:
    if result.errors or not result.samples:
        return None
    return result.samples[0].value

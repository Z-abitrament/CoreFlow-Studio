from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from coreflow.analysis.zero_monitor import ZeroMonitorState
from coreflow.app.modbus_runtime import ModbusVariableSampleResult
from coreflow.app.modbus_zero_monitor import (
    ByteOrderVerification,
    ZeroMonitorRunResult,
)
from coreflow.app.variable_sampling import VariableSample
from coreflow.hardware.zero_monitor_acceptance import (
    ZeroMonitorHardwareAcceptanceRunner,
)
from coreflow.workflows.models import RunStatus


class FakeReadOnlyRuntime:
    def __init__(self, *, changed_offset: bool = False) -> None:
        self.changed_offset = changed_offset
        self.read_count = 0
        self.requested_max_polls = None

    def read_variables(self, variable_names, *, merge_adjacent=False):
        self.read_count += 1
        value = 1.0 if self.read_count == 1 or not self.changed_offset else 2.0
        return ModbusVariableSampleResult(
            samples=(
                VariableSample(
                    sample_id=f"S-{self.read_count}",
                    device_id="CFM-HW-1",
                    variable_name="zero_offset",
                    captured_at=datetime(2026, 1, 1, tzinfo=UTC),
                    value=value,
                    unit="us",
                ),
            ),
            errors=(),
        )

    def run_zero_monitor(
        self,
        analysis_config,
        *,
        zero_flow_confirmed,
        zero_flow_confirmed_at=None,
        max_polls=None,
    ):
        self.requested_max_polls = max_polls
        return ZeroMonitorRunResult(
            run_id="RUN-HW-1",
            attempt_id="ATTEMPT-HW-1",
            state=ZeroMonitorState.EVALUATING,
            run_status=RunStatus.COMPLETED,
            artifact_id="ARTIFACT-HW-1",
            analysis_result_id="RESULT-HW-1",
            counters={
                "logical_poll_count": max_polls,
                "physical_request_count": max_polls + 1,
                "torn_snapshot_reread_count": 1,
                "transport_failure_count": 0,
                "poll_overrun_count": 0,
                "missed_schedule_slot_count": 0,
                "successful_response_count": max_polls,
            },
            metrics={
                "observed_period_mean_ms": 100.0,
                "observed_period_p50_ms": 100.0,
                "observed_period_p95_ms": 101.0,
                "observed_period_p99_ms": 102.0,
                "observed_period_max_ms": 103.0,
                "achieved_poll_rate_hz": 10.0,
            },
            byte_order_verification=ByteOrderVerification(
                status="verified",
                device_value=0,
                profile_byte_order="big",
                profile_word_order="big",
                checked_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )


def test_online_runner_executes_bounded_read_only_checks_and_writes_evidence(tmp_path: Path) -> None:
    runtime = FakeReadOnlyRuntime()
    evidence = tmp_path / "zero_monitor_online.json"

    result = ZeroMonitorHardwareAcceptanceRunner(
        runtime,
        evidence_path=evidence,
    ).run(duration_s=1.0)

    assert result.passed
    assert runtime.requested_max_polls == 10
    assert evidence.exists()
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["validation_stage"] == "non_laboratory_online_read_only"
    assert "not zero-flow bench" in payload["claim_boundary"]
    assert all(item["passed"] for item in payload["checks"])


def test_online_runner_marks_zero_offset_change_incomplete(tmp_path: Path) -> None:
    runtime = FakeReadOnlyRuntime(changed_offset=True)

    result = ZeroMonitorHardwareAcceptanceRunner(
        runtime,
        evidence_path=tmp_path / "changed.json",
    ).run(duration_s=0.1)

    assert not result.passed
    check = next(item for item in result.checks if item.check_id == "ZMON-HW-005")
    assert not check.passed

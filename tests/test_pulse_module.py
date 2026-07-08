from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coreflow.app.pulse_runtime import PulseCounterRuntime
from coreflow.storage import Database, StorageRepository
from coreflow.storage.models import (
    DeviceHistoryRecord,
    DeviceRecord,
    ModbusOperationAttemptRecord,
    PulseDeviceProfileRecord,
    PulseOperationAttemptRecord,
    PulseTrialRecord,
)


def _repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return StorageRepository(database)


def test_pulse_profile_is_scoped_by_device_id(tmp_path) -> None:
    repository = _repository(tmp_path)
    repository.save_pulse_device_profile(
        PulseDeviceProfileRecord(
            profile_id="pulse-profile:CFM-001",
            device_id="CFM-001",
            display_name="CFM-001 pulse",
            channel="1",
            edge="falling",
            pulse_value=0.1,
            unit="kg",
            switch_frequency_hz=50.0,
            boundary_tolerance_s=0.0005,
            notes="bench setup",
        )
    )
    repository.save_pulse_device_profile(
        PulseDeviceProfileRecord(
            profile_id="pulse-profile:CFM-002",
            device_id="CFM-002",
            channel="0",
            edge="rising",
            pulse_value=0.05,
            unit="g",
            switch_frequency_hz=100.0,
        )
    )

    profile = repository.get_pulse_device_profile("CFM-001")

    assert profile is not None
    assert profile.device_id == "CFM-001"
    assert profile.channel == "1"
    assert profile.edge == "falling"
    assert profile.pulse_value == pytest.approx(0.1)
    assert profile.unit == "kg"
    assert profile.switch_frequency_hz == pytest.approx(50.0)
    assert profile.boundary_tolerance_s == pytest.approx(0.0005)
    assert profile.notes == "bench setup"
    assert [item.device_id for item in repository.list_pulse_device_profiles()] == [
        "CFM-001",
        "CFM-002",
    ]


def test_pulse_operation_and_trial_records_are_persisted_by_device(tmp_path) -> None:
    repository = _repository(tmp_path)
    started_at = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)
    ended_at = started_at + timedelta(seconds=1)
    repository.save_pulse_operation_attempt(
        PulseOperationAttemptRecord(
            attempt_id="PULSE-ATTEMPT-001",
            device_id="CFM-PULSE-001",
            operation_type="pulse_csv_trial",
            status="calculated",
            operator="pytest",
            started_at=started_at,
            ended_at=ended_at,
            source_path="E:/captures/pulse.csv",
            summary={
                "pulse_count": 1050,
                "measured_quantity": 52.5,
                "standard_quantity": 50.0,
                "percent_error": 5.0,
                "boundary_pulse_count": 49,
            },
            notes="first trial",
        )
    )
    repository.save_pulse_trial_record(
        PulseTrialRecord(
            trial_id="PULSE-TRIAL-001",
            attempt_id="PULSE-ATTEMPT-001",
            device_id="CFM-PULSE-001",
            flow_point=100.0,
            trial_index=1,
            trial_status="accepted",
            pulse_count=1050,
            measured_quantity=52.5,
            standard_quantity=50.0,
            percent_error=5.0,
            mean_rate=52.0,
            started_at=started_at,
            ended_at=ended_at,
            boundary_pulse_count=49,
            notes="first trial",
        )
    )

    attempts = repository.list_pulse_operation_attempts(device_id="CFM-PULSE-001")
    trials = repository.list_pulse_trial_records(device_id="CFM-PULSE-001")

    assert len(attempts) == 1
    assert attempts[0].summary["pulse_count"] == 1050
    assert attempts[0].source_path == "E:/captures/pulse.csv"
    assert len(trials) == 1
    assert trials[0].measured_quantity == pytest.approx(52.5)
    assert trials[0].standard_quantity == pytest.approx(50.0)
    assert trials[0].percent_error == pytest.approx(5.0)
    assert repository.count_rows("pulse_operation_attempts") == 1
    assert repository.count_rows("pulse_trial_records") == 1


def test_device_history_combines_modbus_and_pulse_rows_for_device(tmp_path) -> None:
    repository = _repository(tmp_path)
    repository.save_device(DeviceRecord(device_id="CFM-HISTORY-001", device_type="flowmeter"))
    first = datetime(2026, 7, 7, 9, 0, tzinfo=UTC)
    second = datetime(2026, 7, 7, 9, 5, tzinfo=UTC)
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="MODBUS-ATTEMPT-001",
            device_id="CFM-HISTORY-001",
            operation_type="manual_error_repeatability",
            status="calculated",
            operator="pytest",
            started_at=first,
            summary={"mean_percent_error": 0.2},
            notes="modbus record",
        )
    )
    repository.save_pulse_operation_attempt(
        PulseOperationAttemptRecord(
            attempt_id="PULSE-ATTEMPT-001",
            device_id="CFM-HISTORY-001",
            operation_type="pulse_csv_trial",
            status="calculated",
            operator="pytest",
            started_at=second,
            summary={"percent_error": -0.1, "measured_quantity": 99.9},
            notes="pulse record",
        )
    )

    records = repository.list_device_history_records(device_id="CFM-HISTORY-001")

    assert records == (
        DeviceHistoryRecord(
            module="Pulse",
            record_id="PULSE-ATTEMPT-001",
            device_id="CFM-HISTORY-001",
            operation_type="pulse_csv_trial",
            status="calculated",
            started_at=second,
            ended_at=None,
            summary={"percent_error": -0.1, "measured_quantity": 99.9},
            notes="pulse record",
        ),
        DeviceHistoryRecord(
            module="Modbus",
            record_id="MODBUS-ATTEMPT-001",
            device_id="CFM-HISTORY-001",
            operation_type="manual_error_repeatability",
            status="calculated",
            started_at=first,
            ended_at=None,
            summary={"mean_percent_error": 0.2},
            notes="modbus record",
        ),
    )
    assert [record.module for record in repository.list_device_history_records(
        device_id="CFM-HISTORY-001",
        module="Pulse",
    )] == ["Pulse"]


def test_pulse_runtime_loads_device_profile_and_calculates_repeatability(tmp_path) -> None:
    runtime = PulseCounterRuntime(repository=_repository(tmp_path))
    runtime.save_profile(
        device_id="CFM-PULSE-RUNTIME",
        channel="0",
        edge="rising",
        pulse_value=0.05,
        unit="g",
        switch_frequency_hz=100.0,
        boundary_tolerance_s=None,
        notes="runtime defaults",
    )

    profile = runtime.load_profile("CFM-PULSE-RUNTIME")
    result = runtime.calculate_trial_from_counts(
        device_id="CFM-PULSE-RUNTIME",
        flow_point=100.0,
        trial_index=1,
        pulse_count=1000,
        standard_quantity=49.0,
        mean_rate=50.0,
        boundary_pulse_count=2,
        source_path="synthetic.csv",
    )

    assert profile.config.pulse_value == pytest.approx(0.05)
    assert result.trial.measured_quantity == pytest.approx(50.0)
    assert result.trial.percent_error == pytest.approx((50.0 - 49.0) / 49.0 * 100.0)
    assert runtime.list_history("CFM-PULSE-RUNTIME")[0].operation_type == "pulse_csv_trial"

    runtime.calculate_trial_from_counts(
        device_id="CFM-PULSE-RUNTIME",
        flow_point=100.0,
        trial_index=2,
        pulse_count=990,
        standard_quantity=49.0,
        mean_rate=49.5,
    )
    runtime.calculate_trial_from_counts(
        device_id="CFM-PULSE-RUNTIME",
        flow_point=100.0,
        trial_index=3,
        pulse_count=1010,
        standard_quantity=49.0,
        mean_rate=50.5,
    )

    summary = runtime.calculate_repeatability(
        "CFM-PULSE-RUNTIME",
        trial_ids=(
            "PULSE-TRIAL-000001",
            "PULSE-TRIAL-000002",
            "PULSE-TRIAL-000003",
        ),
    )

    assert summary.trial_count == 3
    assert summary.mean_percent_error == pytest.approx(
        sum(trial.percent_error for trial in summary.trials) / 3
    )
    assert summary.repeatability_stddev_percent > 0


def test_pulse_runtime_saves_user_selected_repeatability_history(tmp_path) -> None:
    runtime = PulseCounterRuntime(repository=_repository(tmp_path), operator="pytest")
    runtime.save_profile(device_id="CFM-PULSE-SELECT", pulse_value=0.05)
    for index, pulse_count in enumerate((1000, 990, 1010), start=1):
        runtime.calculate_trial_from_counts(
            device_id="CFM-PULSE-SELECT",
            flow_point=100.0,
            trial_index=index,
            pulse_count=pulse_count,
            standard_quantity=50.0,
            mean_rate=pulse_count * 0.05,
            source_path=f"trial-{index}.csv",
        )

    result = runtime.save_repeatability_selection(
        "CFM-PULSE-SELECT",
        trial_ids=(
            "PULSE-TRIAL-000001",
            "PULSE-TRIAL-000002",
            "PULSE-TRIAL-000003",
        ),
    )

    assert result.trial_count == 3
    assert result.flow_point == pytest.approx(100.0)
    assert result.mean_percent_error == pytest.approx(0.0)
    assert result.repeatability_stddev_percent == pytest.approx(1.0)
    assert result.attempt.operation_type == "pulse_repeatability"
    assert result.attempt.status == "calculated"
    assert result.attempt.summary["selected_trial_ids"] == [
        "PULSE-TRIAL-000001",
        "PULSE-TRIAL-000002",
        "PULSE-TRIAL-000003",
    ]
    assert result.attempt.summary["flow_point"] == pytest.approx(100.0)
    assert result.attempt.summary["mean_percent_error"] == pytest.approx(0.0)
    assert result.attempt.summary["repeatability_stddev_percent"] == pytest.approx(1.0)
    assert runtime.list_history("CFM-PULSE-SELECT")[0].operation_type == (
        "pulse_repeatability"
    )


@pytest.mark.parametrize(
    ("trial_ids", "message"),
    [
        (
            ("PULSE-TRIAL-000001", "PULSE-TRIAL-000002"),
            "requires exactly three selected trials",
        ),
        (
            ("PULSE-TRIAL-000001", "PULSE-TRIAL-000002", "PULSE-TRIAL-000003"),
            "must be consecutive",
        ),
    ],
)
def test_pulse_runtime_rejects_invalid_repeatability_selection(
    tmp_path,
    trial_ids: tuple[str, ...],
    message: str,
) -> None:
    runtime = PulseCounterRuntime(repository=_repository(tmp_path))
    runtime.save_profile(device_id="CFM-PULSE-INVALID", pulse_value=0.05)
    for trial_index in (1, 2, 4):
        runtime.calculate_trial_from_counts(
            device_id="CFM-PULSE-INVALID",
            flow_point=100.0,
            trial_index=trial_index,
            pulse_count=1000,
            standard_quantity=50.0,
        )

    with pytest.raises(ValueError, match=message):
        runtime.save_repeatability_selection(
            "CFM-PULSE-INVALID",
            trial_ids=trial_ids,
        )

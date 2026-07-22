from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coreflow.analysis.zero_monitor import (
    ZERO_MONITOR_CRITERIA,
    ZeroMonitorAnalysisConfig,
    ZeroMonitorThreshold,
)
from coreflow.app.modbus_zero_monitor import (
    ZERO_MONITOR_VARIABLE_NAMES,
    ModbusZeroMonitorService,
    ZeroFlowConfirmation,
    recover_interrupted_zero_monitor_runs,
)
from coreflow.app.modbus_runtime import ModbusModuleRuntime
from coreflow.devices import ConfigurationParameter
from coreflow.protocols.modbus import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    WordOrder,
)
from coreflow.protocols.modbus.encoding import decode_registers
from coreflow.storage import ArtifactStore, Database, StorageRepository
from coreflow.storage.models import DeviceRecord, ModbusOperationAttemptRecord
from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)


@dataclass
class FakeClock:
    value: float = 0.0
    wall_start: datetime = datetime(2026, 1, 1, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, duration: float) -> None:
        self.value += max(0.0, duration)

    def wall(self) -> datetime:
        return self.wall_start + timedelta(seconds=self.value)


@dataclass
class FakeBlockReader:
    register_map: ModbusRegisterMap
    snapshots: list[list[int] | Exception]
    byte_order_value: int = 0
    zero_offset: float = 1.0
    calls: list[tuple[tuple[str, ...], bool, int | None]] = field(default_factory=list)

    def read_configuration_parameters(
        self,
        parameter_names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
        transport_retry_count: int | None = None,
    ) -> tuple[ConfigurationParameter, ...]:
        self.calls.append((parameter_names, merge_adjacent, transport_retry_count))
        if parameter_names == ("modbus_byte_order",):
            register = self.register_map.by_name("modbus_byte_order")
            return (_parameter(register, self.byte_order_value, [self.byte_order_value]),)
        if parameter_names == ("zero_offset",):
            register = self.register_map.by_name("zero_offset")
            return (_parameter(register, self.zero_offset, [0x3F80, 0]),)
        item = self.snapshots.pop(0)
        if isinstance(item, Exception):
            raise item
        values = []
        start = self.register_map.by_name(ZERO_MONITOR_VARIABLE_NAMES[0]).address
        for name in parameter_names:
            register = self.register_map.by_name(name)
            offset = register.address - start
            raw = item[offset : offset + register.word_count]
            values.append(
                _parameter(register, decode_registers(register, raw), raw)
            )
        return tuple(values)


def _parameter(register: ModbusRegister, value, raw: list[int]) -> ConfigurationParameter:
    return ConfigurationParameter(
        name=register.name,
        value=value,
        unit=register.unit,
        writable=register.writable,
        metadata={"raw_words": list(raw)},
    )


def _register_map(*, valid: bool = True) -> ModbusRegisterMap:
    specs = (
        ("zero_snapshot_sequence_begin", 0, 1, ModbusDataType.UINT16),
        ("zero_monitor_status", 1, 1, ModbusDataType.UINT16),
        ("zero_monitor_tick_ms", 2, 2, ModbusDataType.UINT32),
        ("zero_base_mean_100ms", 4, 2, ModbusDataType.FLOAT32),
        ("zero_base_std_100ms", 6, 2, ModbusDataType.FLOAT32),
        ("zero_live_600ms", 8, 2, ModbusDataType.FLOAT32),
        ("zero_trim_std_600ms", 10, 2, ModbusDataType.FLOAT32),
        ("zero_trim_range_600ms", 12, 2, ModbusDataType.FLOAT32),
        ("zero_raw_p2p_600ms", 14, 2, ModbusDataType.FLOAT32),
        ("zero_window_valid_count", 16, 1, ModbusDataType.UINT16),
        ("zero_snapshot_sequence_end", 17, 1, ModbusDataType.UINT16),
    )
    registers = [
        ModbusRegister(
            name=name,
            kind=RegisterKind.INPUT,
            address=95 + offset,
            word_count=words,
            data_type=data_type,
            unit=(
                "us"
                if data_type is ModbusDataType.FLOAT32
                else "ms" if name == "zero_monitor_tick_ms" else None
            ),
            byte_order=ByteOrder.BIG,
            word_order=WordOrder.BIG,
        )
        for name, offset, words, data_type in specs
    ]
    registers.extend(
        (
            ModbusRegister(
                "zero_offset",
                RegisterKind.INPUT,
                20,
                2,
                ModbusDataType.FLOAT32,
                unit="us",
            ),
            ModbusRegister(
                "modbus_byte_order",
                RegisterKind.HOLDING,
                52,
                1,
                ModbusDataType.UINT16,
                writable=True,
            ),
        )
    )
    if not valid:
        registers.pop(4)
    return ModbusRegisterMap("zero-service-test", "1", tuple(registers))


def _words(sequence: int, *, tick_ms: int | None = None, end: int | None = None) -> list[int]:
    tick = sequence * 100 if tick_ms is None else tick_ms
    return [
        sequence & 0xFFFF,
        7,
        (tick >> 16) & 0xFFFF,
        tick & 0xFFFF,
        0x3F80,
        0,
        0x3DCC,
        0xCCCD,
        0x3F80,
        0,
        0x3DCC,
        0xCCCD,
        0x3E4C,
        0xCCCD,
        0x3E99,
        0x999A,
        60,
        sequence if end is None else end,
    ]


def _config() -> ZeroMonitorAnalysisConfig:
    return ZeroMonitorAnalysisConfig(
        long_window_s=12.0,
        minimum_stable_duration_s=0.0,
        thresholds={
            name: ZeroMonitorThreshold(
                limit=10.0,
                source="test",
                unit="us",
                test_only=True,
            )
            for name in ZERO_MONITOR_CRITERIA
        },
    )


def _service(tmp_path: Path, reader: FakeBlockReader, clock: FakeClock):
    database = Database(tmp_path / "coreflow.sqlite3")
    database.initialize()
    repository = StorageRepository(database)
    repository.save_device(DeviceRecord(device_id="CFM-ZERO-1", device_type="modbus_rtu"))
    service = ModbusZeroMonitorService(
        repository=repository,
        artifact_store=ArtifactStore(tmp_path),
        reader=reader,
        register_map=reader.register_map,
        device_id="CFM-ZERO-1",
        operator="pytest",
        analysis_config=_config(),
        zero_flow_confirmation=ZeroFlowConfirmation(
            confirmed=True,
            operator="pytest",
            confirmed_at=clock.wall(),
        ),
        profile_id="profile:CFM-ZERO-1",
        clock=clock.monotonic,
        sleep_fn=clock.sleep,
        wall_clock=clock.wall,
        fsync=lambda _fd: None,
    )
    return service, repository


def test_service_streams_one_row_per_logical_poll_and_persists_history(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [_words(index) for index in range(1, 4)])
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=3)

    block_calls = [call for call in reader.calls if call[0] == ZERO_MONITOR_VARIABLE_NAMES]
    assert len(block_calls) == 3
    assert all(call[1:] == (True, 0) for call in block_calls)
    assert result.counters["logical_poll_count"] == 3
    assert result.counters["physical_request_count"] == 3
    assert result.artifact_id is not None
    artifact = next(item for item in repository.list_artifacts(result.run_id) if item.artifact_id == result.artifact_id)
    assert artifact.metadata["curve_type"] == "zero_monitor_samples"
    assert artifact.metadata["x_axis_variable"] == "device_tick_ms_unwrapped"
    rows = list(csv.DictReader((tmp_path / artifact.file_path).open(encoding="utf-8")))
    assert len(rows) == 3
    assert list(rows[0])[:3] == ["captured_at", "elapsed_s", "sample_index"]
    attempts = repository.list_modbus_operation_attempts(operation_type="modbus_zero_monitor")
    assert attempts[0].summary["flow_samples_artifact_id"] == result.artifact_id


def test_service_torn_snapshot_allows_exactly_one_full_reread(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(
        _register_map(),
        [_words(1, end=2), _words(2)],
    )
    service, _repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=1)

    block_calls = [call for call in reader.calls if call[0] == ZERO_MONITOR_VARIABLE_NAMES]
    assert len(block_calls) == 2
    assert result.counters["logical_poll_count"] == 1
    assert result.counters["physical_request_count"] == 2
    assert result.counters["torn_snapshot_reread_count"] == 1


def test_service_transport_failure_has_one_request_and_no_stale_values(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [TimeoutError("timeout")])
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=1)

    artifact = repository.list_artifacts(result.run_id)[0]
    row = next(csv.DictReader((tmp_path / artifact.file_path).open(encoding="utf-8")))
    assert result.counters["physical_request_count"] == 1
    assert row["response_status"] == "timeout"
    assert row["captured_at"]
    assert row["host_receive_time"] == ""
    assert row["sequence"] == ""
    assert row["live_zero_600ms"] == ""
    analysis = repository.list_analysis_results(result.run_id)[0]
    assert analysis.summary_metrics["candidate_count"] is None
    assert analysis.summary_metrics["repeat_std"] is None


def test_service_program_error_writes_row_then_terminates_run(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(
        _register_map(),
        [ValueError("decoder invariant failed"), _words(2)],
    )
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=3)

    assert result.run_status is RunStatus.ERROR
    assert result.counters["logical_poll_count"] == 1
    artifact = repository.list_artifacts(result.run_id)[0]
    row = next(csv.DictReader((tmp_path / artifact.file_path).open(encoding="utf-8")))
    assert row["response_status"] == "program_error"
    assert row["error_message"] == "decoder invariant failed"


def test_service_invalid_map_creates_only_unlinked_error_attempt(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(valid=False), [])
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=1)

    assert result.run_id is None
    assert reader.calls == []
    assert repository.list_runs() == ()
    attempts = repository.list_modbus_operation_attempts(operation_type="modbus_zero_monitor")
    assert len(attempts) == 1
    assert attempts[0].run_id is None
    assert attempts[0].status == "error"


def test_service_can_reach_stable_only_after_full_device_time_window(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [_words(index) for index in range(1, 122)])
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=121)

    assert result.state.value == "STABLE"
    assert result.run_status.value == "passed"
    analysis = repository.list_analysis_results(result.run_id)[0]
    assert analysis.pass_fail_decision == "passed"
    assert analysis.summary_metrics["candidate_count"] == 21
    assert analysis.summary_metrics["window_span_s"] == pytest.approx(12.0)


def test_stop_before_first_poll_creates_no_artifact_or_analysis(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [])
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=0)

    assert result.run_status.value == "error"
    assert result.artifact_id is None
    assert repository.list_artifacts(result.run_id) == ()
    assert repository.list_analysis_results(result.run_id) == ()


def test_byte_order_mismatch_blocks_before_snapshot_and_run_creation(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [], byte_order_value=3)
    service, repository = _service(tmp_path, reader, clock)

    result = service.run(max_polls=1)

    assert result.run_id is None
    assert result.reason_codes == ("BYTE_ORDER_MISMATCH",)
    assert [call[0] for call in reader.calls] == [("modbus_byte_order",)]
    assert repository.list_runs() == ()


def test_generic_history_loader_uses_device_time_and_segments(tmp_path: Path) -> None:
    clock = FakeClock()
    reader = FakeBlockReader(
        _register_map(),
        [_words(1), _words(4), _words(5)],
    )
    service, repository = _service(tmp_path, reader, clock)
    result = service.run(max_polls=3)
    runtime = ModbusModuleRuntime(repository, data_root=tmp_path)

    series = runtime.load_flow_sample_series(result.artifact_id)

    assert series.x_axis_variable == "device_tick_ms_unwrapped"
    assert series.x_axis_unit == "ms"
    assert series.segment_variable == "continuous_segment"
    assert series.segment_ids() == ("1", "2")
    assert len(series.points) == 3


def test_startup_recovery_finalizes_nonempty_partial_as_diagnostic_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "coreflow.sqlite3")
    database.initialize()
    repository = StorageRepository(database)
    store = ArtifactStore(tmp_path)
    repository.save_device(DeviceRecord(device_id="CFM-RECOVER", device_type="modbus_rtu"))
    started = datetime(2026, 1, 1, tzinfo=UTC)
    run_id = "RUN-RECOVER-ZERO"
    partial = (
        store.run_directory(run_id, started)
        / "raw"
        / "zero.csv.partial"
    )
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_text(
        "captured_at,elapsed_s,sample_index,response_status,host_receive_time\n"
        "2026-01-01T00:00:00+00:00,0,1,timeout,\n",
        encoding="utf-8",
    )
    repository.save_run(
        RunSession(
            run_id=run_id,
            run_type=RunType.STABILITY,
            workflow_name="modbus_zero_monitor",
            workflow_version="1",
            device_id="CFM-RECOVER",
            operator="pytest",
            status=RunStatus.RUNNING,
            started_at=started,
            configuration_snapshot={
                "partial_artifact_path": str(store.relative_path(partial)),
                "analysis_config": {},
            },
        )
    )
    repository.save_step(
        WorkflowStep(
            step_id=f"{run_id}-CAPTURE",
            run_id=run_id,
            name="zero_monitor_capture",
            step_type=WorkflowStepType.CAPTURE,
            status=WorkflowStepStatus.RUNNING,
        )
    )
    repository.save_step(
        WorkflowStep(
            step_id=f"{run_id}-ANALYSIS",
            run_id=run_id,
            name="zero_monitor_analysis",
            step_type=WorkflowStepType.ANALYSIS,
            status=WorkflowStepStatus.PENDING,
        )
    )
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="ATTEMPT-RECOVER",
            run_id=run_id,
            device_id="CFM-RECOVER",
            operation_type="modbus_zero_monitor",
            status="running",
            operator="pytest",
        )
    )

    recovered = recover_interrupted_zero_monitor_runs(repository, store)

    assert recovered == (run_id,)
    assert repository.get_run(run_id).status is RunStatus.ERROR
    artifact = repository.list_artifacts(run_id)[0]
    assert artifact.metadata["recovered"] is True
    assert artifact.metadata["complete"] is False
    analysis = repository.list_analysis_results(run_id)[0]
    assert analysis.pass_fail_decision is None
    assert analysis.summary_metrics["analysis_state"] == "DATA_GAP"


def test_zero_monitor_history_exports_imports_and_reopens_curve(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    clock = FakeClock()
    reader = FakeBlockReader(_register_map(), [_words(1), _words(2)])
    service, source_repository = _service(source_root, reader, clock)
    result = service.run(max_polls=2)
    source_runtime = ModbusModuleRuntime(source_repository, data_root=source_root)
    package = tmp_path / "zero_monitor_history.json"

    exported = source_runtime.export_calibration_history(
        package,
        operation="modbus_zero_monitor",
    )

    target_root = tmp_path / "target"
    target_database = Database(target_root / "coreflow.sqlite3")
    target_database.initialize()
    target_runtime = ModbusModuleRuntime(
        StorageRepository(target_database),
        data_root=target_root,
    )
    imported = target_runtime.import_calibration_history(exported.path)
    records = target_runtime.list_test_records(operation="modbus_zero_monitor")
    imported_artifact_id = records[0].metrics["flow_samples_artifact_id"]
    series = target_runtime.load_flow_sample_series(imported_artifact_id)

    assert imported.imported_runs == 1
    assert len(series.points) == 2
    assert series.x_axis_variable == "device_tick_ms_unwrapped"
    assert result.artifact_id is not None


def test_startup_recovery_rejects_partial_path_outside_artifact_root(tmp_path: Path) -> None:
    database = Database(tmp_path / "coreflow.sqlite3")
    database.initialize()
    repository = StorageRepository(database)
    store = ArtifactStore(tmp_path)
    repository.save_device(DeviceRecord(device_id="CFM-OUTSIDE", device_type="modbus_rtu"))
    repository.save_run(
        RunSession(
            run_id="RUN-OUTSIDE",
            run_type=RunType.STABILITY,
            workflow_name="modbus_zero_monitor",
            workflow_version="1",
            device_id="CFM-OUTSIDE",
            operator="pytest",
            status=RunStatus.RUNNING,
            configuration_snapshot={"partial_artifact_path": "../outside.csv.partial"},
        )
    )

    recovered = recover_interrupted_zero_monitor_runs(repository, store)

    assert recovered == ("RUN-OUTSIDE",)
    run = repository.get_run("RUN-OUTSIDE")
    assert run.status is RunStatus.ERROR
    assert run.configuration_snapshot["recovery_reason"] == (
        "PARTIAL_PATH_OUTSIDE_ARTIFACT_ROOT"
    )
    assert repository.list_artifacts("RUN-OUTSIDE") == ()

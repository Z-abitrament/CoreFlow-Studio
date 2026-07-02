from __future__ import annotations

import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import sleep
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QAbstractSpinBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QTableWidgetSelectionRange,
    QTextEdit,
)

from coreflow.analysis.calibration import RepeatabilityTrial
from coreflow.app.modbus_runtime import (
    ModbusCalibrationHistoryEntry,
    ModbusConnectionSettings,
    ModbusFlowSamplePoint,
    ModbusFlowSampleSeries,
    ModbusModuleRuntime,
    ModbusOperationMetadata,
    ModbusRepeatabilityHistoryTrial,
    ModbusRepeatabilitySimpleCapture,
    ModbusRepeatabilitySimpleTrialResult,
    ModbusTrialSamplePoint,
    ModbusVariableSamplingRunResult,
)
from coreflow.hardware import SerialPortInfo, SerialPortScanner
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    RegisterKind,
    encode_registers,
)
from coreflow.storage import Database, StorageRepository
from coreflow.storage.models import (
    DeviceRecord,
    ModbusDeviceProfileRecord,
    ModbusOperationAttemptRecord,
    ModbusTestSessionRecord,
    ModbusTrialRecord,
)
import coreflow.ui.modbus_window as modbus_window_module
from coreflow.ui.modbus_window import (
    CalibrationHistoryDialog,
    CalibrationHistoryExportDialog,
    DeviceAnalysisComparisonVariablesDialog,
    DeviceAnalysisDialog,
    DeviceAnalysisTrialSelectionDialog,
    ModbusModuleWindow,
    RepeatabilityFlowPlotDialog,
    RepeatabilitySelectionDialog,
)
from coreflow.workflows import FlowSegmentCaptureResult, RunSession, RunStatus, RunType
from tests.modbus_fakes import placeholder_fake_transport, placeholder_transport_factory


def _repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return StorageRepository(database)


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _capture_and_calculate_repeatability_trial(
    qtbot,
    window: ModbusModuleWindow,
    dialog,
    *,
    standard_mass: float,
    expected_count: int,
) -> None:
    _click(qtbot, dialog.startButton)
    qtbot.waitUntil(
        lambda count=expected_count: window.logTextEdit.toPlainText().count(
            "Repeatability captured"
        )
        >= count,
        timeout=5000,
    )
    qtbot.waitUntil(lambda: dialog.current_capture() is not None, timeout=5000)
    assert (
        dialog.captureProgressDialog is None
        or not dialog.captureProgressDialog.isVisible()
    )
    assert dialog.calculateTrialErrorButton.isEnabled()
    assert dialog.standardMassSpinBox.isEnabled()
    dialog.standardMassSpinBox.setValue(standard_mass)
    _click(qtbot, dialog.calculateTrialErrorButton)
    qtbot.waitUntil(
        lambda count=expected_count: len(dialog.trial_results()) == count,
        timeout=5000,
    )
    assert dialog.current_capture() is None


def _table_text(table, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text()


def _set_table_text(table, row: int, column: int, text: str) -> None:
    item = table.item(row, column)
    assert item is not None
    item.setText(text)


def _find_row(table, variable_name: str) -> int:
    for row in range(table.rowCount()):
        if _table_text(table, row, 0) == variable_name:
            return row
    raise AssertionError(f"missing variable row: {variable_name}")


def _has_row(table, variable_name: str) -> bool:
    for row in range(table.rowCount()):
        if _table_text(table, row, 0) == variable_name:
            return True
    return False


def _find_snapshot_row(table, variable_name: str) -> int:
    for row in range(table.rowCount()):
        if _table_text(table, row, 1) == variable_name:
            return row
    raise AssertionError(f"missing snapshot variable row: {variable_name}")


def _set_variable_sampling_selection(dialog, variable_names: tuple[str, ...]) -> None:
    selected = set(variable_names)
    for row in range(dialog.variableTable.rowCount()):
        item = dialog.variableTable.item(row, 0)
        name = _table_text(dialog.variableTable, row, 1)
        if item is not None:
            item.setCheckState(
                Qt.CheckState.Checked if name in selected else Qt.CheckState.Unchecked
            )


def _find_metric_row(table, metric_name: str) -> int:
    for row in range(table.rowCount()):
        if _table_text(table, row, 0) == metric_name:
            return row
    raise AssertionError(f"missing metric row: {metric_name}")


def _has_metric_row(table, metric_name: str) -> bool:
    for row in range(table.rowCount()):
        if _table_text(table, row, 0) == metric_name:
            return True
    return False


def _column_texts(table, column: int) -> list[str]:
    return [_table_text(table, row, column) for row in range(table.rowCount())]


class _FakePlotPoint:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def data(self) -> dict[str, object]:
        return self._data


def _history_entry(
    run_id: str,
    operation: str,
    started_at: datetime,
) -> ModbusCalibrationHistoryEntry:
    return ModbusCalibrationHistoryEntry(
        run_id=run_id,
        operation=operation,
        status="passed",
        started_at=started_at,
        ended_at=started_at,
        device_id="CFM-TEST-001",
        operator="pytest",
        metrics={},
    )


def _repeatability_trial(
    *,
    run_id: str = "RUN-FINAL-K-CALC",
    flow_point: float,
    trial_index: int,
    percent_error: float,
    original_k_factor: float = 500.0,
) -> ModbusRepeatabilitySimpleTrialResult:
    captured_at = datetime(2026, 6, 15, 8, trial_index, tzinfo=UTC)
    standard_mass = 100.0
    measured_mass_delta = standard_mass * (1.0 + percent_error / 100.0)
    return ModbusRepeatabilitySimpleTrialResult(
        run_id=run_id,
        flow_point=flow_point,
        trial_index=trial_index,
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        k_factor_parameter="k_factor",
        original_k_factor=original_k_factor,
        pre_snapshot={},
        pre_snapshot_captured_at=captured_at,
        mass_acc_before=0.0,
        mass_acc_after=measured_mass_delta,
        measured_mass_delta=measured_mass_delta,
        standard_mass=standard_mass,
        percent_error=percent_error,
        mean_flow=10.0,
        instant_flow=10.0,
        flow_started_at=captured_at,
        flow_instant_at=captured_at,
        flow_ended_at=captured_at + timedelta(seconds=10),
        poll_interval_s=0.05,
    )


def _select_runtime_profile(
    runtime: ModbusModuleRuntime,
    *,
    device_id: str = "CFM-TEST-001",
    settings: ModbusConnectionSettings | None = None,
) -> None:
    runtime.save_device_profile(
        device_id=device_id,
        metadata=runtime.operation_metadata,
        register_map=runtime.register_map,
        connection_settings=settings,
        select=True,
    )


def _open_profile_dialog(qtbot, window: ModbusModuleWindow):
    _click(qtbot, window.createDeviceProfileButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible(),
        timeout=5000,
    )
    dialog = window.deviceProfileDialog
    assert dialog is not None
    return dialog


def _open_edit_profile_dialog(qtbot, window: ModbusModuleWindow):
    _click(qtbot, window.editDeviceProfileButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible(),
        timeout=5000,
    )
    dialog = window.deviceProfileDialog
    assert dialog is not None
    return dialog


def _save_profile_from_dialog(
    qtbot,
    window: ModbusModuleWindow,
    *,
    device_id: str,
    device_model: str | None = None,
    tube_model: str | None = None,
    transmitter_model: str | None = None,
):
    dialog = _open_profile_dialog(qtbot, window)
    dialog.deviceIdLineEdit.setText(device_id)
    if device_model is not None:
        dialog.deviceModelLineEdit.setText(device_model)
    if tube_model is not None:
        dialog.tubeModelLineEdit.setText(tube_model)
    if transmitter_model is not None:
        dialog.transmitterModelLineEdit.setText(transmitter_model)
    _click(qtbot, dialog.saveButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileCombo.findData(device_id) >= 0,
        timeout=5000,
    )
    return dialog


def _ensure_window_profile(
    qtbot,
    window: ModbusModuleWindow,
    *,
    device_id: str = "CFM-UI-001",
) -> None:
    if window.deviceProfileCombo.currentData():
        return
    _save_profile_from_dialog(qtbot, window, device_id=device_id)


def _open_connection_dialog(qtbot, window: ModbusModuleWindow):
    _ensure_window_profile(qtbot, window)
    _click(qtbot, window.openConnectionButton)
    qtbot.waitUntil(lambda: window.connectionDialog is not None, timeout=5000)
    dialog = window.connectionDialog
    assert dialog is not None
    return dialog


def _wait_for_scanned_ports(qtbot, dialog, expected_count: int) -> None:
    qtbot.waitUntil(
        lambda: dialog.portCombo.count() == expected_count
        and dialog.portCombo.currentText() != "Scanning serial ports...",
        timeout=5000,
    )


def test_modbus_module_runtime_runs_without_simulator(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    frames: list[tuple[str, str, str]] = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.set_frame_logger(lambda direction, operation, data: frames.append((direction, operation, data)))
    runtime.configure_operation_metadata(
        ModbusOperationMetadata(
            device_model="CFM-100",
            tube_model="T-25",
            transmitter_model="TX-9",
        )
    )

    _select_runtime_profile(runtime, device_id="CFM-TEST-001")
    status = runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=7))
    sample_result = runtime.sample_variables()
    zero_result = runtime.run_zero_calibration()
    k_factor_run = runtime.run_k_factor_calibration(
        mass_acc_before=100.0,
        mass_acc_after=112.0,
        standard_mass=12.6,
        current_k_factor=500.0,
    )
    repeatability_run = runtime.run_repeatability_test(
        (
            RepeatabilityTrial(1.0, 1, 0.0, 10.0, 10.0),
            RepeatabilityTrial(1.0, 2, 0.0, 10.1, 10.0),
            RepeatabilityTrial(1.0, 3, 0.0, 9.9, 10.0),
            RepeatabilityTrial(2.0, 1, 0.0, 20.0, 20.0),
            RepeatabilityTrial(2.0, 2, 0.0, 20.2, 20.0),
            RepeatabilityTrial(2.0, 3, 0.0, 19.8, 20.0),
            RepeatabilityTrial(3.0, 1, 0.0, 30.0, 30.0),
            RepeatabilityTrial(3.0, 2, 0.0, 30.3, 30.0),
            RepeatabilityTrial(3.0, 3, 0.0, 29.7, 30.0),
        )
    )

    assert status.connected is True
    assert status.device_id == "CFM-TEST-001"
    assert {sample.variable_name for sample in sample_result.samples} >= {
        "mass_acc",
        "k_factor",
    }
    assert sample_result.errors == ()
    assert repository.get_run_status(zero_result.run_id) == "passed"
    assert zero_result.record.completed is True
    assert repository.get_run_status(k_factor_run) == "passed"
    assert repository.get_run_status(repeatability_run) == "passed"
    assert transports[0].coil_writes == [(16, True, 7)]
    assert transports[0].writes
    assert any(frame[0] == "TX" and frame[1] == "read" for frame in frames)
    assert any(frame[0] == "RX" and frame[1] == "read" for frame in frames)
    assert any(
        frame[0] == "RX"
        and frame[1] == "read"
        and frame[2].startswith("07 01 01 00 ")
        for frame in frames
    )
    assert all(" " in frame[2] or frame[2] for frame in frames)
    zero_history = runtime.list_calibration_history(operation="zero_calibration")
    assert len(zero_history) == 1
    assert zero_history[0].metrics["device_model"] == "CFM-100"
    assert zero_history[0].metrics["tube_model"] == "T-25"
    assert zero_history[0].metrics["transmitter_model"] == "TX-9"
    zero_run = repository.get_run(zero_result.run_id)
    assert zero_run is not None
    assert zero_run.configuration_snapshot["device_model"] == "CFM-100"


def test_modbus_module_runtime_returns_raw_frame_response_bytes(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    frames: list[tuple[str, str, str]] = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.set_frame_logger(
        lambda direction, operation, data: frames.append((direction, operation, data))
    )
    _select_runtime_profile(runtime, device_id="CFM-TEST-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=7))
    transports[0].registers[0x0000] = [0x0001, 0x0002]
    transports[0].raw_response = bytes.fromhex("07")

    response = runtime.send_raw_frame(bytes.fromhex("07 03 00 00 00 02"), append_crc=True)

    assert transports[0].raw_frames == []
    assert transports[0].reads == [(RegisterKind.HOLDING, 0x0000, 2, 7)]
    assert response == bytes.fromhex("07 03 04 00 01 00 02 4C 32")
    assert ("TX", "raw_frame", "07 03 00 00 00 02 C4 6D") in frames
    assert ("RX", "raw_frame", "07 03 04 00 01 00 02 4C 32") in frames


def test_modbus_module_runtime_routes_standard_raw_read_around_raw_recv(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    frames: list[tuple[str, str, str]] = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.set_frame_logger(
        lambda direction, operation, data: frames.append((direction, operation, data))
    )
    _select_runtime_profile(runtime, device_id="CFM-TEST-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transports[0].registers[0x003D] = [0x3BE1, 0x72D8]
    transports[0].raw_response = bytes.fromhex("01")

    response = runtime.send_raw_frame(bytes.fromhex("01 03 00 3D 00 02"), append_crc=True)

    assert transports[0].raw_frames == []
    assert transports[0].reads == [(RegisterKind.HOLDING, 0x003D, 2, 1)]
    assert response == bytes.fromhex("01 03 04 3B E1 72 D8 83 DB")
    assert ("TX", "raw_frame", "01 03 00 3D 00 02 55 C7") in frames
    assert ("RX", "raw_frame", "01 03 04 3B E1 72 D8 83 DB") in frames


def test_modbus_module_runtime_routes_standard_raw_single_write_around_raw_send(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    frames: list[tuple[str, str, str]] = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.set_frame_logger(
        lambda direction, operation, data: frames.append((direction, operation, data))
    )
    _select_runtime_profile(runtime, device_id="CFM-TEST-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transports[0].raw_response = bytes.fromhex("01")

    response = runtime.send_raw_frame(bytes.fromhex("01 06 00 3D 12 34"), append_crc=True)

    assert transports[0].raw_frames == []
    assert transports[0].writes == [(0x003D, [0x1234], 1)]
    assert response == bytes.fromhex("01 06 00 3D 12 34 15 71")
    assert ("TX", "raw_frame", "01 06 00 3D 12 34 15 71") in frames
    assert ("RX", "raw_frame", "01 06 00 3D 12 34 15 71") in frames


def test_modbus_module_runtime_requires_stable_device_profile_id(tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )

    try:
        runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    except RuntimeError as exc:
        assert "device profile" in str(exc)
    else:
        raise AssertionError("connect without a device profile should fail")

    for invalid in ("", "01", "modbus:COM9:1"):
        try:
            runtime.save_device_profile(device_id=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid device ID accepted: {invalid!r}")


def test_modbus_module_runtime_deletes_legacy_port_profile(tmp_path) -> None:
    repository = _repository(tmp_path)
    repository.save_device(
        DeviceRecord(
            device_id="modbus:COM9:1",
            device_type="modbus_rtu",
        )
    )
    repository.save_modbus_device_profile(
        ModbusDeviceProfileRecord(
            profile_id="profile:modbus:COM9:1",
            device_id="modbus:COM9:1",
            display_name="Legacy COM9 Unit 1",
        )
    )
    repository.save_modbus_test_session(
        ModbusTestSessionRecord(
            session_id="SESSION-LEGACY-COM9",
            device_id="modbus:COM9:1",
            profile_id="profile:modbus:COM9:1",
            operator="pytest",
            status="closed",
            started_at=datetime(2026, 6, 13, 8, 0, tzinfo=UTC),
        )
    )
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )

    assert runtime.delete_device_profile("modbus:COM9:1") is True

    assert runtime.get_device_profile("modbus:COM9:1") is None
    assert repository.get_device("modbus:COM9:1") is not None
    sessions = repository.list_modbus_test_sessions(device_id="modbus:COM9:1")
    assert sessions[0].profile_id is None


def test_modbus_module_runtime_captures_simple_repeatability_history(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    runtime.configure_operation_metadata(
        ModbusOperationMetadata(
            device_model="CFM-R",
            tube_model="T-R",
            transmitter_model="TX-R",
        )
    )
    _select_runtime_profile(runtime, device_id="CFM-REPEAT-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        *[
            encoded
            for _trial in range(10)
            for encoded in (
                encode_registers(mass_rate, 0.0),
                encode_registers(mass_rate, 5.0),
                encode_registers(mass_rate, 5.5),
                encode_registers(mass_rate, 0.0),
            )
        ],
    ]
    measured_deltas = (
        100.0,
        101.0,
        99.0,
        50.0,
        51.0,
        49.0,
        20.0,
        20.2,
        19.8,
        52.0,
    )
    cumulative = 0.0
    mass_acc_reads = []
    for delta in measured_deltas:
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    standards = (100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 20.0, 20.0, 20.0)
    flow_points = (600.0, 600.0, 600.0, 300.0, 300.0, 300.0, 100.0, 100.0, 100.0)
    trials = []
    run_id = None
    flow_samples: list[tuple[datetime, float]] = []
    trial_samples: list[tuple[datetime, dict[str, object]]] = []

    def record_flow_sample(timestamp: datetime, value: float) -> None:
        flow_samples.append((timestamp, value))

    def record_trial_sample(timestamp: datetime, values: dict[str, object]) -> None:
        trial_samples.append((timestamp, dict(values)))

    for index, (flow_point, standard_mass) in enumerate(zip(flow_points, standards)):
        capture = runtime.capture_repeatability_simple_trial(
            run_id=run_id,
            flow_point=flow_point,
            trial_index=index % 3 + 1,
            snapshot_variable_names=("temperature",),
            flow_rate_parameter="mass_rate",
            flow_acc_parameter="mass_acc",
            poll_interval_s=0.05,
            capture_snapshot=run_id is None,
            sample_variable_names=("temperature",),
            record_flow_samples=index == 0,
            flow_sample_callback=record_flow_sample if index == 0 else None,
            sample_callback=record_trial_sample if index == 0 else None,
        )
        run_id = capture.run_id
        trials.append(
            runtime.calculate_repeatability_simple_trial(
                capture,
                standard_mass=standard_mass,
                notes="same operation note",
            )
        )

    result = runtime.calculate_repeatability_simple_result(tuple(trials))

    assert repository.get_run_status(result.run_id) == "passed"
    assert result.analysis.summary_metrics["trial_count"] == 9.0
    assert result.analysis.summary_metrics["max_repeatability_stddev_percent"] == 2.0
    assert result.trials[1].percent_error == 1.0
    assert [value for _timestamp, value in flow_samples] == [0.0, 5.0, 5.5, 0.0]
    assert all(timestamp.tzinfo is not None for timestamp, _value in flow_samples)
    assert [sample["mass_rate"] for _timestamp, sample in trial_samples] == [
        0.0,
        5.0,
        5.5,
        0.0,
    ]
    assert all(sample["temperature"] == 21.5 for _timestamp, sample in trial_samples)
    first_trial = trials[0]
    assert first_trial.pre_snapshot["temperature"] == 21.5
    assert first_trial.post_snapshot["temperature"] == 21.5
    assert first_trial.post_snapshot_captured_at is not None
    assert first_trial.flow_sample_count == 4
    assert first_trial.trial_sample_variable_names == ("mass_rate", "temperature")
    assert first_trial.flow_samples_artifact_id is not None
    flow_sample_artifact = next(
        artifact
        for artifact in repository.list_artifacts()
        if artifact.artifact_id == first_trial.flow_samples_artifact_id
    )
    assert flow_sample_artifact.metadata["curve_type"] == "flow_rate_samples"
    assert flow_sample_artifact.metadata["flow_rate_parameter"] == "mass_rate"
    assert flow_sample_artifact.metadata["variable_names"] == [
        "mass_rate",
        "temperature",
    ]
    assert flow_sample_artifact.metadata["units"]["mass_rate"] == mass_rate.unit
    assert flow_sample_artifact.metadata["sample_count"] == 4
    flow_sample_csv = (Path(tmp_path) / flow_sample_artifact.file_path).read_text(
        encoding="utf-8"
    )
    assert "captured_at,elapsed_s,sample_index,mass_rate,temperature" in flow_sample_csv
    assert flow_sample_csv.count("\n") == 5
    assert ",1,0.0" in flow_sample_csv
    assert ",3,5.5" in flow_sample_csv
    assert ",21.5" in flow_sample_csv
    loaded_series = runtime.load_flow_sample_series(first_trial.flow_samples_artifact_id)
    assert loaded_series.artifact_id == first_trial.flow_samples_artifact_id
    assert loaded_series.flow_rate_parameter == "mass_rate"
    assert loaded_series.unit == flow_sample_artifact.metadata["unit"]
    assert [sample.value for sample in loaded_series.samples] == [0.0, 5.0, 5.5, 0.0]
    assert loaded_series.variable_names == ("mass_rate", "temperature")
    assert [point.values["temperature"] for point in loaded_series.points] == [
        21.5,
        21.5,
        21.5,
        21.5,
    ]
    history = runtime.list_calibration_history(operation="manual_error_repeatability")
    assert len(history) == 1
    assert history[0].metrics["max_abs_percent_error"] == 2.0
    assert history[0].metrics["flow_point_300_repeatability_stddev_percent"] == 2.0
    assert history[0].metrics["device_model"] == "CFM-R"
    assert history[0].metrics["tube_model"] == "T-R"
    assert history[0].metrics["transmitter_model"] == "TX-R"
    assert history[0].notes == "same operation note"
    assert len(history[0].metrics["trials"]) == 9
    assert (
        history[0].metrics["trials"][0]["flow_samples_artifact_id"]
        == first_trial.flow_samples_artifact_id
    )
    assert history[0].metrics["trials"][0]["flow_sample_count"] == 4
    assert history[0].metrics["trials"][0]["trial_sample_variable_names"] == [
        "mass_rate",
        "temperature",
    ]
    assert history[0].metrics["trials"][0]["post_snapshot"]["temperature"] == 21.5
    assert {
        trial["notes"]
        for trial in history[0].metrics["trials"]
    } == {"same operation note"}
    test_records = runtime.list_test_records(operation="manual_error_repeatability")
    assert any(
        record.operation == "manual_error_repeatability_trial"
        for record in test_records
    )
    trials = repository.list_modbus_trial_records(device_id="CFM-REPEAT-001")
    assert len(trials) == 9
    assert {trial.trial_status for trial in trials} == {"accepted"}
    assert {trial.notes for trial in trials} == {"same operation note"}
    trial_record = next(
        record
        for record in test_records
        if record.operation == "manual_error_repeatability_trial"
        and record.metrics.get("flow_samples_artifact_id")
        == first_trial.flow_samples_artifact_id
    )
    assert trial_record.notes == "same operation note"
    assert trial_record.metrics["flow_samples_artifact_id"] == (
        first_trial.flow_samples_artifact_id
    )
    assert trial_record.metrics["flow_sample_count"] == 4
    assert trial_record.metrics["trial_sample_variable_names"] == [
        "mass_rate",
        "temperature",
    ]
    assert trial_record.metrics["post_snapshot"]["temperature"] == 21.5
    summary_record = next(
        record
        for record in test_records
        if record.operation == "manual_error_repeatability"
    )
    assert summary_record.notes == "same operation note"
    raw_artifacts = [
        artifact
        for artifact in repository.list_artifacts()
        if artifact.metadata.get("curve_type") == "modbus_polling"
    ]
    assert len(raw_artifacts) >= 9
    analysis = runtime.analyze_device_history("CFM-REPEAT-001")
    assert analysis.device_id == "CFM-REPEAT-001"
    assert analysis.record_count >= 10
    assert analysis.trial_count == 9
    assert analysis.accepted_trial_count == 9
    assert analysis.diagnostic_trial_count == 0
    assert analysis.rejected_trial_count == 0
    assert analysis.overall_mean_error_percent == 0.0
    assert analysis.overall_max_abs_error_percent == 2.0
    assert len(analysis.flow_summaries) == 3
    assert analysis.operation_counts["manual_error_repeatability_trial"] == 9
    assert analysis.latest_final_k is None
    assert "No final K preview" in "\n".join(analysis.notes)


def test_modbus_module_runtime_selects_repeatability_instant_flow_from_samples(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-REP-INSTANT")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport = transports[0]
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 4.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 6.0),
        encode_registers(mass_rate, 0.0),
    ]
    transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 0.0),
        encode_registers(mass_acc, 12.0),
    ]
    samples: list[tuple[datetime, dict[str, object]]] = []

    capture = runtime.capture_repeatability_simple_trial(
        flow_point=100.0,
        trial_index=1,
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        poll_interval_s=0.05,
        post_start_sample_s=0.04,
        post_stop_delay_s=0.0,
        capture_snapshot=False,
        record_flow_samples=True,
        sample_callback=lambda captured_at, values: samples.append(
            (captured_at, dict(values))
        ),
    )

    assert [sample["mass_rate"] for _at, sample in samples] == [
        0.0,
        4.0,
        5.0,
        6.0,
        0.0,
    ]
    assert capture.segment.instant_flow == 5.0
    assert capture.segment.instant_flow_at == samples[2][0]
    sample_times = [captured_at for captured_at, _sample in samples]
    assert max(
        (next_at - previous_at).total_seconds()
        for previous_at, next_at in zip(sample_times, sample_times[1:])
    ) < 0.5


def test_modbus_module_runtime_records_variable_sampling_operation(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.configure_operation_metadata(
        ModbusOperationMetadata(
            device_model="CFM-SAMPLE",
            tube_model="T-SAMPLE",
            transmitter_model="TX-SAMPLE",
        )
    )
    _select_runtime_profile(runtime, device_id="CFM-VARS-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    temperature = register_map.by_name("temperature")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 4.5),
    ]
    transport.read_sequences[temperature.address] = [
        encode_registers(temperature, 20.0),
        encode_registers(temperature, 20.5),
        encode_registers(temperature, 21.0),
    ]
    live_samples: list[tuple[datetime, dict[str, object]]] = []

    result = runtime.run_variable_sampling(
        ("mass_rate", "temperature"),
        poll_interval_s=0.01,
        max_samples=3,
        sample_callback=lambda captured_at, values: live_samples.append(
            (captured_at, dict(values))
        ),
        notes="variable sampling note",
    )

    assert isinstance(result, ModbusVariableSamplingRunResult)
    assert result.variable_names == ("mass_rate", "temperature")
    assert result.sample_count == 3
    assert result.units["mass_rate"] == mass_rate.unit
    assert [sample.values["mass_rate"] for sample in result.samples] == [
        0.0,
        5.0,
        4.5,
    ]
    assert [sample.values["temperature"] for sample in result.samples] == [
        20.0,
        20.5,
        21.0,
    ]
    assert [values["mass_rate"] for _captured_at, values in live_samples] == [
        0.0,
        5.0,
        4.5,
    ]
    assert result.flow_samples_artifact_id is not None
    artifact = next(
        item
        for item in repository.list_artifacts()
        if item.artifact_id == result.flow_samples_artifact_id
    )
    assert artifact.metadata["curve_type"] == "variable_samples"
    assert artifact.metadata["operation_type"] == "modbus_variable_sampling"
    assert artifact.metadata["variable_names"] == ["mass_rate", "temperature"]
    csv_text = (Path(tmp_path) / artifact.file_path).read_text(encoding="utf-8")
    assert "captured_at,elapsed_s,sample_index,mass_rate,temperature" in csv_text
    assert csv_text.count("\n") == 4
    loaded = runtime.load_flow_sample_series(result.flow_samples_artifact_id)
    assert loaded.variable_names == ("mass_rate", "temperature")
    assert [point.values["temperature"] for point in loaded.points] == [
        20.0,
        20.5,
        21.0,
    ]
    records = runtime.list_test_records(operation="modbus_variable_sampling")
    assert len(records) == 1
    assert records[0].operation == "modbus_variable_sampling"
    assert records[0].metrics["sample_count"] == 3
    assert records[0].metrics["flow_samples_artifact_id"] == result.flow_samples_artifact_id
    assert records[0].metrics["device_model"] == "CFM-SAMPLE"
    assert records[0].notes == "variable sampling note"


def test_modbus_module_runtime_saves_repeatability_flow_summary_notes(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-REP-SUMMARY")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    trials = tuple(
        _repeatability_trial(
            run_id="RUN-REP-SUMMARY",
            flow_point=250.0,
            trial_index=index,
            percent_error=error,
        )
        for index, error in enumerate((0.0, 1.0, -1.0), start=1)
    )

    result = runtime.save_repeatability_flow_summary_history(
        trials,
        flow_point=250.0,
        mode="single_point",
        notes="summary note",
    )

    records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability"
    ]
    assert result.notes == "summary note"
    assert len(records) == 1
    assert records[0].notes == "summary note"
    assert records[0].metrics["mode"] == "single_point"
    assert records[0].metrics["flow_point_count"] == 1.0
    assert records[0].metrics["trial_count"] == 3.0
    assert records[0].metrics["mean_percent_error"] == pytest.approx(0.0)
    assert records[0].metrics["flow_point_250_mean_percent_error"] == pytest.approx(0.0)
    assert records[0].metrics["flow_points"][0]["mean_percent_error"] == pytest.approx(
        0.0
    )


def test_repeatability_history_summary_shows_selected_mean_error() -> None:
    entry = ModbusCalibrationHistoryEntry(
        run_id="RUN-REP-SUMMARY",
        operation="manual_error_repeatability",
        status="passed",
        started_at=datetime(2026, 6, 15, 8, 0, tzinfo=UTC),
        ended_at=datetime(2026, 6, 15, 8, 1, tzinfo=UTC),
        device_id="CFM-REP-SUMMARY",
        operator="pytest",
        metrics={
            "trial_count": 3.0,
            "mean_percent_error": 0.5,
            "max_abs_percent_error": 2.0,
            "max_repeatability_stddev_percent": 1.5,
            "flow_points": [
                {
                    "flow_point": 250.0,
                    "mean_percent_error": 0.5,
                    "repeatability_stddev_percent": 1.5,
                    "trial_errors": [0.0, 2.0, -0.5],
                }
            ],
        },
    )

    summary = modbus_window_module._history_parameter_summary(entry)
    detail = modbus_window_module._history_detail_text(entry)

    assert "mean_error=0.5%" in summary
    assert "repeatability=1.5%" in summary
    assert "max_error=" not in summary
    assert "mean_percent_error: 0.5" in detail
    assert "flow_point 250" in detail
    assert "mean_percent_error=0.5" in detail


def test_modbus_module_runtime_uses_capture_click_time_for_trial_records(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-REP-TIME")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    source_started_at = datetime(2026, 6, 15, 8, 1, tzinfo=UTC)
    source_ended_at = source_started_at + timedelta(seconds=10)
    capture_clicked_at = source_started_at - timedelta(seconds=5)
    capture = ModbusRepeatabilitySimpleCapture(
        run_id="RUN-REP-TIME",
        flow_point=250.0,
        trial_index=1,
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        k_factor_parameter="k_factor",
        original_k_factor=500.0,
        pre_snapshot={},
        pre_snapshot_captured_at=source_started_at,
        mass_acc_before=0.0,
        mass_acc_after=100.0,
        segment=FlowSegmentCaptureResult(
            flow_rate_parameter="mass_rate",
            started_at=source_started_at,
            instant_flow_at=source_started_at,
            ended_at=source_ended_at,
            start_flow=10.0,
            instant_flow=10.0,
            stop_flow=0.0,
            poll_count=3,
        ),
        poll_interval_s=0.05,
        capture_started_at=capture_clicked_at,
    )

    before_trial_calculation = datetime.now(UTC)
    trial = runtime.calculate_repeatability_simple_trial(
        capture,
        standard_mass=100.0,
    )
    after_trial_calculation = datetime.now(UTC)
    trial_record = next(
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability_trial"
    )

    assert trial_record.started_at is not None
    assert trial_record.started_at == capture_clicked_at
    assert trial_record.ended_at is not None
    assert before_trial_calculation <= trial_record.ended_at <= after_trial_calculation
    assert trial_record.started_at != trial.flow_started_at
    assert trial_record.metrics["started_at"] == trial_record.started_at.isoformat()
    assert trial_record.metrics["capture_started_at"] == capture_clicked_at.isoformat()
    assert (
        before_trial_calculation
        <= datetime.fromisoformat(str(trial_record.metrics["calculated_at"]))
        <= after_trial_calculation
    )
    assert trial_record.metrics["flow_started_at"] == source_started_at.isoformat()
    assert trial_record.metrics["flow_ended_at"] == trial.flow_ended_at.isoformat()

    before_repeatability_calculation = datetime.now(UTC)
    runtime.save_repeatability_flow_summary_history(
        (trial,),
        flow_point=250.0,
        mode="single_point",
    )
    after_repeatability_calculation = datetime.now(UTC)
    summary_record = next(
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability"
    )

    assert summary_record.started_at is not None
    assert (
        before_repeatability_calculation
        <= summary_record.started_at
        <= after_repeatability_calculation
    )
    assert summary_record.started_at != trial.flow_started_at
    assert summary_record.metrics["started_at"] == summary_record.started_at.isoformat()
    assert summary_record.metrics["ended_at"] == summary_record.ended_at.isoformat()
    assert (
        summary_record.metrics["source_trial_started_at"]
        == trial.flow_started_at.isoformat()
    )
    assert (
        summary_record.metrics["source_trial_ended_at"]
        == trial.flow_ended_at.isoformat()
    )


def test_modbus_module_runtime_calculates_repeatability_final_k(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-FINAL-K-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        *[
            encoded
            for _trial in range(9)
            for encoded in (
                encode_registers(mass_rate, 5.0),
                encode_registers(mass_rate, 5.5),
                encode_registers(mass_rate, 0.0),
            )
        ],
    ]
    measured_deltas = (100.0, 101.0, 99.0, 50.0, 51.0, 49.0, 20.0, 20.2, 19.8)
    cumulative = 0.0
    mass_acc_reads = []
    for delta in measured_deltas:
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    standards = (100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 20.0, 20.0, 20.0)
    flow_points = (600.0, 600.0, 600.0, 300.0, 300.0, 300.0, 100.0, 100.0, 100.0)
    trials = []
    run_id = None
    for index, (flow_point, standard_mass) in enumerate(zip(flow_points, standards)):
        capture = runtime.capture_repeatability_simple_trial(
            run_id=run_id,
            flow_point=flow_point,
            trial_index=index % 3 + 1,
            flow_rate_parameter="mass_rate",
            flow_acc_parameter="mass_acc",
            poll_interval_s=0.05,
            capture_snapshot=run_id is None,
        )
        run_id = capture.run_id
        trials.append(
            runtime.calculate_repeatability_simple_trial(
                capture,
                standard_mass=standard_mass,
                notes="final k note",
            )
        )

    selected = {
        600.0: tuple(trials[0:3]),
        300.0: tuple(trials[3:6]),
        100.0: tuple(trials[6:9]),
    }
    before_final_k_calculation = datetime.now(UTC)
    result = runtime.calculate_repeatability_final_k(
        selected,
        run_id=run_id,
        notes="final k note",
    )
    after_final_k_calculation = datetime.now(UTC)

    assert result["selected_trial_count"] == 9
    assert result["original_k_factor"] == 500.0
    assert result["average_error"] == 0.0
    assert result["new_k_factor"] == 500.0
    assert result["write_status"] == "not_requested"
    assert repository.get_run_status(run_id) == "passed"
    history = runtime.list_test_records(operation="manual_error_repeatability")
    assert any(
        record.operation == "manual_error_repeatability_final_k"
        and record.metrics["new_k_factor"] == 500.0
        and record.notes == "final k note"
        for record in history
    )
    final_k_record = next(
        record
        for record in history
        if record.operation == "manual_error_repeatability_final_k"
    )
    assert final_k_record.started_at is not None
    assert before_final_k_calculation <= final_k_record.started_at <= after_final_k_calculation
    assert final_k_record.metrics["started_at"] == final_k_record.started_at.isoformat()
    assert final_k_record.metrics["source_trial_started_at"] == (
        min(trial.flow_started_at for trial in trials).isoformat()
    )
    assert final_k_record.metrics["source_trial_ended_at"] == (
        max(trial.flow_ended_at for trial in trials).isoformat()
    )
    analysis = runtime.analyze_device_history("CFM-FINAL-K-001")
    assert analysis.latest_final_k is not None
    assert analysis.latest_final_k["new_k_factor"] == 500.0
    assert analysis.latest_final_k["original_k_factor"] == 500.0
    assert len(analysis.flow_summaries) == 3
    written = runtime.apply_repeatability_final_k_result(result)
    assert written["write_status"] == "applied"
    assert written["write_verified"] is True
    assert written["readback_k_factor"] == 500.0
    assert written["audit_id"]
    assert repository.count_rows("audit_logs") == 1
    assert transports[0].writes[-1][0] == register_map.by_name("k_factor").address
    final_k_metrics = repository.list_analysis_results(run_id)[-1].summary_metrics
    assert final_k_metrics["write_requested"] is True
    assert final_k_metrics["write_verified"] is True
    assert final_k_metrics["readback_k_factor"] == 500.0
    final_k_step = repository.list_steps(run_id)[-1]
    assert final_k_step.input_configuration["formula_intermediate_k"] == (
        "original_k / (1 + measurement_error_percent / 100)"
    )
    assert repository.get_run(run_id).workflow_version == "0.4-final-k"
    assert repository.list_analysis_results(run_id)[-1].algorithm_version == "0.4"

    updated = runtime.calculate_repeatability_final_k(selected, run_id=run_id)
    final_k_records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability_final_k"
    ]
    assert len(final_k_records) == 1
    assert updated["new_k_factor"] == 500.0
    assert final_k_records[0].metrics["new_k_factor"] == 500.0
    assert final_k_records[0].notes == "final k note"

    mixed_operation_selected = {
        600.0: tuple(trials[0:3]),
        300.0: tuple(trials[3:6]),
        100.0: tuple(
            replace(trial, run_id="RUN-FROM-OTHER-OPERATION")
            for trial in trials[6:9]
        ),
    }
    try:
        runtime.calculate_repeatability_final_k(
            mixed_operation_selected,
            run_id=run_id,
        )
        raise AssertionError("Expected mixed repeatability operations to fail.")
    except ValueError as exc:
        assert "one operation" in str(exc)


def test_modbus_module_repeatability_final_k_uses_measurement_error_for_new_k(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(repository)
    selected = {
        600.0: tuple(
            _repeatability_trial(flow_point=600.0, trial_index=index, percent_error=error)
            for index, error in enumerate((0.10, 0.20, 0.30), start=1)
        ),
        300.0: tuple(
            _repeatability_trial(flow_point=300.0, trial_index=index, percent_error=error)
            for index, error in enumerate((-0.10, 0.00, 0.10), start=1)
        ),
        100.0: tuple(
            _repeatability_trial(flow_point=100.0, trial_index=index, percent_error=error)
            for index, error in enumerate((0.60, 0.70, 0.80), start=1)
        ),
    }

    result = runtime.calculate_repeatability_final_k(
        selected,
        run_id="RUN-FINAL-K-CALC",
        save_history=False,
    )

    expected_flow_100_intermediate = 500.0 / (1.0 + 0.7 / 100.0)
    expected_flow_300_intermediate = 500.0 / (1.0 + 0.0 / 100.0)
    expected_new_k = (
        expected_flow_300_intermediate + expected_flow_100_intermediate
    ) / 2.0

    assert result["original_k_factor"] == 500.0
    assert result["new_k_factor"] != result["original_k_factor"]
    assert result["new_k_factor"] == pytest.approx(expected_new_k)
    assert result["delta_k_factor"] == pytest.approx(expected_new_k - 500.0)
    assert result["flow_point_100_measurement_error_percent"] == pytest.approx(0.7)
    assert result["flow_point_100_adjusted_error_percent"] == pytest.approx(0.35)
    assert result["flow_point_100_intermediate_k_factor"] == pytest.approx(
        expected_flow_100_intermediate
    )


def test_modbus_module_device_analysis_saves_selected_trial_report(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-ANALYSIS-REPORT")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        *[
            encoded
            for _trial in range(9)
            for encoded in (
                encode_registers(mass_rate, 5.0),
                encode_registers(mass_rate, 5.5),
                encode_registers(mass_rate, 0.0),
            )
        ],
    ]
    cumulative = 0.0
    mass_acc_reads = []
    for delta in (100.0, 101.0, 99.0, 50.0, 51.0, 49.0, 20.0, 20.2, 19.8):
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    trials = []
    run_id = None
    standards = (100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 20.0, 20.0, 20.0)
    flow_points = (600.0, 600.0, 600.0, 300.0, 300.0, 300.0, 100.0, 100.0, 100.0)
    for index, (flow_point, standard_mass) in enumerate(zip(flow_points, standards)):
        capture = runtime.capture_repeatability_simple_trial(
            run_id=run_id,
            flow_point=flow_point,
            trial_index=index % 3 + 1,
            snapshot_variable_names=("zero_offset", "low_threshold"),
            flow_rate_parameter="mass_rate",
            flow_acc_parameter="mass_acc",
            poll_interval_s=0.001,
            capture_snapshot=True,
        )
        run_id = capture.run_id
        trials.append(
            runtime.calculate_repeatability_simple_trial(
                capture,
                standard_mass=standard_mass,
            )
        )
    runtime.calculate_repeatability_simple_result(tuple(trials))
    runtime.disconnect()

    history_trials = runtime.list_repeatability_history_trials("CFM-ANALYSIS-REPORT")
    grouped_history_trials = {
        flow_point: tuple(
            item for item in history_trials if item.trial.flow_point == flow_point
        )
        for flow_point in (600.0, 300.0, 100.0)
    }
    selected = {
        flow_point: trials[:3]
        for flow_point, trials in grouped_history_trials.items()
    }
    preview = runtime.calculate_device_analysis_repeatability_preview(selected)
    assert preview["selected_trial_count"] == 9
    assert preview["run_id"] == "PREVIEW-DEVICE-ANALYSIS"
    assert preview["new_k_factor"] == 500.0
    assert not runtime.list_test_records(
        device_id="CFM-ANALYSIS-REPORT",
        operation="manual_error_repeatability_final_k",
    )
    before_report_save = datetime.now(UTC)
    result = runtime.calculate_device_analysis_repeatability_report(
        "CFM-ANALYSIS-REPORT",
        selected,
        comparison_variable_names=("zero_offset", "low_threshold"),
    )
    after_report_save = datetime.now(UTC)

    assert result.report_artifact_id
    assert "Selected Trial Final K Report" in result.report_text
    assert "Selected Trials:" in result.report_text
    assert "Per-Flow Calculations:" in result.report_text
    assert "Final K Calculation:" in result.report_text
    assert "old_k_factor: 500" in result.report_text
    assert "new_k_factor: 500" in result.report_text
    assert "delta_k_factor: 0" in result.report_text
    assert "zero_offset" in result.report_text
    assert "Device ID:" not in result.report_text
    assert "Source Repeatability Run IDs:" not in result.report_text
    assert "Operation:" not in result.report_text
    assert "Generated At:" not in result.report_text
    assert result.report_text.count("\t") >= 9
    records = runtime.list_test_records(
        device_id="CFM-ANALYSIS-REPORT",
        operation="manual_error_repeatability",
    )
    report_records = [
        record
        for record in records
        if record.operation == "manual_error_repeatability_final_k"
        and record.metrics.get("analysis_source") == "current_device_analysis"
    ]
    assert len(report_records) == 1
    selected_trial_results = tuple(
        history_trial.trial
        for trials in selected.values()
        for history_trial in trials
    )
    source_trial_started_at = min(
        trial.flow_started_at for trial in selected_trial_results
    )
    source_trial_ended_at = max(trial.flow_ended_at for trial in selected_trial_results)
    assert report_records[0].started_at is not None
    assert before_report_save <= report_records[0].started_at <= after_report_save
    assert report_records[0].ended_at is not None
    assert before_report_save <= report_records[0].ended_at <= after_report_save
    assert report_records[0].metrics["started_at"] == (
        report_records[0].started_at.isoformat()
    )
    assert report_records[0].metrics["source_trial_started_at"] == (
        source_trial_started_at.isoformat()
    )
    assert report_records[0].metrics["source_trial_ended_at"] == (
        source_trial_ended_at.isoformat()
    )
    assert report_records[0].metrics["new_k_factor"] == result.metrics["new_k_factor"]
    assert report_records[0].metrics["delta_k_factor"] == result.metrics["delta_k_factor"]
    assert report_records[0].metrics["report_artifact_id"] == result.report_artifact_id

    cross_operation_selected = {
        flow_point: tuple(
            ModbusRepeatabilityHistoryTrial(
                trial=replace(
                    history_trial.trial,
                    run_id=(
                        "RUN-ANALYSIS-HISTORY-A"
                        if history_trial.trial.flow_point != 100.0
                        else "RUN-ANALYSIS-HISTORY-B"
                    ),
                ),
                attempt_id=history_trial.attempt_id,
                pre_snapshot=history_trial.pre_snapshot,
                device_metadata=history_trial.device_metadata,
            )
            for history_trial in trials
        )
        for flow_point, trials in selected.items()
    }
    cross_operation_result = runtime.calculate_device_analysis_repeatability_report(
        "CFM-ANALYSIS-REPORT",
        cross_operation_selected,
        comparison_variable_names=("zero_offset", "low_threshold"),
        save_history=False,
    )

    assert cross_operation_result.metrics["new_k_factor"] == result.metrics["new_k_factor"]
    assert cross_operation_result.metrics["source_repeatability_run_ids"] == [
        "RUN-ANALYSIS-HISTORY-A",
        "RUN-ANALYSIS-HISTORY-B",
    ]

    first_flow = sorted(selected)[0]
    mismatched_first = selected[first_flow][0]
    mismatched_selected = dict(selected)
    mismatched_selected[first_flow] = (
        ModbusRepeatabilityHistoryTrial(
            trial=mismatched_first.trial,
            attempt_id=mismatched_first.attempt_id,
            pre_snapshot={
                **mismatched_first.pre_snapshot,
                "low_threshold": "different",
            },
            device_metadata=mismatched_first.device_metadata,
        ),
        *selected[first_flow][1:],
    )
    try:
        runtime.validate_repeatability_analysis_snapshot_consistency(
            mismatched_selected,
            variable_names=("zero_offset", "low_threshold"),
        )
        raise AssertionError("Expected inconsistent snapshot values to fail.")
    except ValueError as exc:
        assert "low_threshold" in str(exc)


def test_modbus_module_history_trial_uses_attempt_original_k_fallback(tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(repository)
    captured_at = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    repository.save_device(DeviceRecord(device_id="CFM-K-FALLBACK", device_type="modbus_rtu"))
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="ATT-K-FALLBACK",
            device_id="CFM-K-FALLBACK",
            operation_type="manual_error_repeatability_trial",
            status="accepted",
            operator="pytest",
            run_id="RUN-K-FALLBACK",
            started_at=captured_at,
            ended_at=captured_at,
            summary={
                "flow_point": 100.0,
                "trial_index": 1,
                "k_factor_parameter": "k_factor",
                "original_k_factor": 500.0,
                "flow_rate_parameter": "mass_rate",
                "flow_acc_parameter": "mass_acc",
                "pre_snapshot": {
                    "zero_offset": 1.0,
                    "low_threshold": 2.0,
                },
            },
        )
    )
    repository.save_modbus_trial_record(
        ModbusTrialRecord(
            trial_id="TRIAL-K-FALLBACK",
            attempt_id="ATT-K-FALLBACK",
            run_id="RUN-K-FALLBACK",
            device_id="CFM-K-FALLBACK",
            flow_point=100.0,
            trial_index=1,
            trial_status="accepted",
            k_factor_parameter="k_factor",
            original_k_factor=None,
            standard_mass=100.0,
            measured_mass_delta=100.0,
            percent_error=0.0,
            mean_flow=10.0,
            instant_flow=10.0,
            flow_started_at=captured_at,
            flow_instant_at=captured_at,
            flow_ended_at=captured_at,
        )
    )

    history_trials = runtime.list_repeatability_history_trials("CFM-K-FALLBACK")

    assert len(history_trials) == 1
    assert history_trials[0].trial.original_k_factor == 500.0


def test_device_analysis_trial_selection_dialog_saves_compare_variables(qtbot) -> None:
    captured_at = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    history_trials = tuple(
        ModbusRepeatabilityHistoryTrial(
            trial=ModbusRepeatabilitySimpleTrialResult(
                run_id="RUN-ANALYSIS-SELECT",
                flow_point=flow_point,
                trial_index=trial_index,
                flow_rate_parameter="mass_rate",
                flow_acc_parameter="mass_acc",
                k_factor_parameter="k_factor",
                original_k_factor=500.0,
                pre_snapshot={
                    "zero_offset": 1.0,
                    "low_threshold": 2.0,
                    "temperature": 25.0,
                },
                pre_snapshot_captured_at=captured_at,
                mass_acc_before=0.0,
                mass_acc_after=standard_mass + error,
                measured_mass_delta=standard_mass + error,
                standard_mass=standard_mass,
                percent_error=error,
                mean_flow=10.0,
                instant_flow=10.0,
                flow_started_at=started_at,
                flow_instant_at=started_at,
                flow_ended_at=started_at,
                poll_interval_s=0.05,
                trial_status="accepted",
            ),
            attempt_id=f"ATT-{flow_point:g}-{trial_index}",
            pre_snapshot={
                "zero_offset": 1.0,
                "low_threshold": 2.0,
                "temperature": 25.0,
            },
            device_metadata={},
        )
        for flow_index, (flow_point, standard_mass) in enumerate(
            ((100.0, 20.0), (300.0, 50.0), (600.0, 100.0))
        )
        for trial_index, error in ((1, 0.0), (2, 0.1), (3, -0.1))
        for started_at in (
            captured_at + timedelta(minutes=flow_index * 10 + trial_index),
        )
    )
    saved = []
    dialog = DeviceAnalysisTrialSelectionDialog(
        history_trials,
        comparison_variable_names=("zero_offset", "low_threshold"),
        save_comparison_variable_names=saved.append,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert not dialog.okButton.isEnabled()
    assert len(dialog.selected_trials_by_flow()) == 0
    assert dialog.selectionTable.rowCount() == 9
    assert _table_text(dialog.selectionTable, 0, 1) == "600"
    assert _table_text(dialog.selectionTable, 0, 2) == "3"
    header = dialog.selectionTable.horizontalHeader()
    assert header.sectionsMovable()
    header.moveSection(13, 1)
    assert header.logicalIndex(1) == 13
    checked_rows = [
        row
        for row in range(dialog.selectionTable.rowCount())
        if dialog.selectionTable.item(row, 0).checkState() == Qt.CheckState.Checked
    ]
    assert checked_rows == []
    for row in range(dialog.selectionTable.rowCount()):
        dialog.selectionTable.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.okButton.isEnabled()
    assert len(dialog.selected_trials_by_flow()) == 3
    preview_text = dialog.previewTextEdit.toPlainText()
    assert "Flow 100g/s: adjusted_error=0%" in preview_text
    assert "Flow 300g/s: adjusted_error=0%" in preview_text
    assert "Flow 600g/s: adjusted_error=0%" in preview_text
    assert "repeatability=0.1%" in preview_text
    assert "K value: old=500, new=500" in preview_text
    assert "trials 1-3" not in preview_text
    assert "mean=" not in preview_text
    assert _table_text(dialog.selectionTable, 0, 4) == "500"
    assert _table_text(dialog.selectionTable, 0, 9).startswith("ATT-")
    dialog.save_comparison_variables(
        ("zero_offset", "low_threshold", "temperature")
    )

    assert saved == [("zero_offset", "low_threshold", "temperature")]
    assert dialog.comparisonVariablesLabel.text() == (
        "zero_offset, low_threshold, temperature"
    )
    assert "temperature=25" in dialog.selectionTable.item(0, 13).text()


def test_device_analysis_compare_variables_dialog_saves_and_closes(qtbot) -> None:
    saved = []
    dialog = DeviceAnalysisComparisonVariablesDialog(
        ("zero_offset", "low_threshold", "temperature"),
        selected_names=("zero_offset",),
        save_selected_names=saved.append,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.selected_names() == ("zero_offset",)
    row = _find_snapshot_row(dialog.variableTable, "temperature")
    item = dialog.variableTable.item(row, 0)
    assert item is not None
    item.setCheckState(Qt.CheckState.Checked)

    assert dialog.selected_names() == ("zero_offset", "temperature")
    _click(qtbot, dialog.saveButton)

    assert saved == [("zero_offset", "temperature")]
    assert not dialog.isVisible()


def test_device_analysis_dialog_refreshes_history_after_report_save(qtbot) -> None:
    class FakeRuntime:
        def __init__(self) -> None:
            self.report_calls = 0

        def list_repeatability_history_trials(self, device_id: str) -> tuple:
            return ()

        def calculate_device_analysis_repeatability_preview(
            self,
            selected_trials: dict,
        ) -> dict:
            return {"new_k_factor": 500.0}

        def calculate_device_analysis_repeatability_report(
            self,
            device_id: str,
            selected_trials: dict,
            *,
            comparison_variable_names: tuple[str, ...],
        ) -> SimpleNamespace:
            self.report_calls += 1
            return SimpleNamespace(run_id="RUN-DEVICE-ANALYSIS-SAVE")

    refreshed = []
    runtime = FakeRuntime()
    dialog = DeviceAnalysisDialog(
        runtime,
        device_id="CFM-ANALYSIS-CALLBACK",
        report_saved_callback=lambda: refreshed.append(True),
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._selected_trials = {100.0: tuple()}
    dialog._preview_metrics = runtime.calculate_device_analysis_repeatability_preview(
        dialog._selected_trials,
    )

    dialog.save_report()

    assert runtime.report_calls == 1
    assert refreshed == [True]
    assert (
        dialog.statusLabel.text()
        == "Report saved to test history: RUN-DEVICE-ANALYSIS-SAVE"
    )


def test_device_analysis_dialog_select_button_calculates_then_save_records(
    qtbot,
    monkeypatch,
) -> None:
    selected_trials = {120.0: ("trial-a", "trial-b", "trial-c")}

    class FakeSelectionDialog:
        def __init__(
            self,
            _history_trials,
            *,
            comparison_variable_names,
            save_comparison_variable_names,
            preview_metrics_factory,
            parent=None,
        ) -> None:
            self._preview_metrics = preview_metrics_factory(selected_trials)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def comparison_variable_names(self):
            return ("zero_offset", "low_threshold")

        def selected_trials_by_flow(self):
            return selected_trials

        def preview_metrics(self):
            return self._preview_metrics

    class FakeRuntime:
        def __init__(self) -> None:
            self.preview_calls = 0
            self.report_calls = 0

        def list_repeatability_history_trials(self, device_id: str) -> tuple:
            return tuple(range(9))

        def calculate_device_analysis_repeatability_preview(
            self,
            selected_trials: dict,
        ) -> dict:
            self.preview_calls += 1
            return {
                "original_k_factor": 500.0,
                "new_k_factor": 499.5,
                "flow_points": [
                    {
                        "flow_point": 120.0,
                        "adjusted_error_percent": -0.1,
                        "repeatability_stddev_percent": 0.02,
                    }
                ],
            }

        def calculate_device_analysis_repeatability_report(
            self,
            device_id: str,
            selected_trials: dict,
            *,
            comparison_variable_names: tuple[str, ...],
        ) -> SimpleNamespace:
            self.report_calls += 1
            return SimpleNamespace(run_id="RUN-DEVICE-ANALYSIS-SAVE")

    monkeypatch.setattr(
        modbus_window_module,
        "DeviceAnalysisTrialSelectionDialog",
        FakeSelectionDialog,
    )
    refreshed = []
    runtime = FakeRuntime()
    dialog = DeviceAnalysisDialog(
        runtime,
        device_id="CFM-ANALYSIS-CALC-SAVE",
        report_saved_callback=lambda: refreshed.append(True),
    )
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.select_trials()

    assert runtime.preview_calls == 1
    assert runtime.report_calls == 0
    assert dialog.saveReportButton.isEnabled()
    assert dialog.statusLabel.text() == "Calculated from selected trials: 3"

    dialog.save_report()

    assert runtime.report_calls == 1
    assert refreshed == [True]


def test_repeatability_selection_dialog_lists_consecutive_trial_windows(qtbot) -> None:
    captured_at = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
    trials = tuple(
        ModbusRepeatabilitySimpleTrialResult(
            run_id="RUN-SELECT",
            flow_point=250.0,
            trial_index=index,
            flow_rate_parameter="mass_rate",
            flow_acc_parameter="mass_acc",
            k_factor_parameter="k_factor",
            original_k_factor=500.0,
            pre_snapshot={},
            pre_snapshot_captured_at=None,
            mass_acc_before=0.0,
            mass_acc_after=100.0 + error,
            measured_mass_delta=100.0 + error,
            standard_mass=100.0,
            percent_error=error,
            mean_flow=10.0,
            instant_flow=10.0,
            flow_started_at=captured_at,
            flow_instant_at=captured_at,
            flow_ended_at=captured_at,
            poll_interval_s=0.05,
        )
        for index, error in ((1, 0.0), (2, 1.0), (3, -1.0), (5, 2.0))
    )
    dialog = RepeatabilitySelectionDialog(trials)
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.windowCombo.count() == 1
    assert tuple(trial.trial_index for trial in dialog.selected_trials()) == (1, 2, 3)
    assert "Repeatability stddev" in dialog.previewTextEdit.toPlainText()


def test_modbus_module_runtime_saves_single_point_repeatability_summary(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-SINGLE-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        *[
            encoded
            for _trial in range(4)
            for encoded in (
                encode_registers(mass_rate, 0.0),
                encode_registers(mass_rate, 4.0),
                encode_registers(mass_rate, 4.2),
                encode_registers(mass_rate, 0.0),
            )
        ],
    ]
    measured_deltas = (100.0, 101.0, 99.0, 102.0)
    cumulative = 0.0
    mass_acc_reads = []
    for delta in measured_deltas:
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    trials = []
    run_id = None
    for index in range(1, 5):
        capture = runtime.capture_repeatability_simple_trial(
            run_id=run_id,
            flow_point=250.0,
            trial_index=index,
            flow_rate_parameter="mass_rate",
            flow_acc_parameter="mass_acc",
            poll_interval_s=0.05,
            capture_snapshot=run_id is None,
        )
        run_id = capture.run_id
        trials.append(
            runtime.calculate_repeatability_simple_trial(
                capture,
                standard_mass=100.0,
            )
        )

    summary = runtime.summarize_repeatability_flow_point(tuple(trials), flow_point=250.0)
    result = runtime.calculate_repeatability_simple_result(
        tuple(trials),
        mode="single_point",
        expected_flow_point_count=1,
        expected_trials_per_point=len(trials),
        require_complete=False,
    )

    assert summary.trial_count == 4
    assert summary.max_abs_percent_error == 2.0
    assert result.mode == "single_point"
    assert result.analysis.summary_metrics["trial_count"] == 4.0
    assert result.analysis.summary_metrics["flow_point_count"] == 1.0
    history = runtime.list_calibration_history(operation="manual_error_repeatability")
    assert len(history) == 1
    assert history[0].metrics["mode"] == "single_point"
    assert history[0].metrics["expected_trials_per_point"] == 4
    assert len(history[0].metrics["trials"]) == 4


def test_modbus_module_manual_zero_start_write_sends_fc05_first(tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    frames: list[tuple[str, str, str]] = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    runtime.set_frame_logger(lambda direction, operation, data: frames.append((direction, operation, data)))
    _select_runtime_profile(runtime, device_id="CFM-WRITE-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1, retry_count=0))

    result = runtime.write_variable("zero_calibration_start", "1")

    assert result.status.value == "applied"
    assert transports[0].coil_writes == [(16, True, 1)]
    assert frames[0] == ("TX", "write_coil", "01 05 00 10 FF 00 8D FF")
    assert frames[1] == ("RX", "write_coil", "01 05 00 10 FF 00 8D FF")
    assert not any(frame[1] == "read" for frame in frames)


def test_modbus_module_runtime_captures_simple_k_factor_and_verifies_write(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transport = placeholder_fake_transport()
    register_map = ModbusModuleRuntime(
        repository,
        transport_factory=lambda _config: transport,
        zero_calibration_wait_s=0.0,
    ).register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 0.0),
    ]
    transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 112.0),
    ]
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=lambda _config: transport,
        zero_calibration_wait_s=0.0,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-KFACTOR-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))

    capture = runtime.capture_k_factor_simple_trial(
        snapshot_variable_names=("mass_acc", "k_factor"),
        poll_interval_s=0.001,
    )
    result = runtime.calculate_k_factor_simple_result(
        capture,
        standard_mass=12.6,
        save_history=True,
    )
    written = runtime.apply_k_factor_simple_result(result)

    assert capture.mass_acc_before == 100.0
    assert capture.mass_acc_after == 112.0
    assert capture.current_k_factor == 500.0
    assert result.corrected_k_factor == 525.0
    assert result.measured_mass_delta == 12.0
    assert repository.get_run_status(result.run_id) == "passed"
    assert written.write_status == "applied"
    assert written.write_verified is True
    assert written.readback_k_factor == 525.0
    assert transport.writes[-1][0] == 102
    metrics = repository.list_analysis_results(result.run_id)[-1].summary_metrics
    assert metrics["write_requested"] is True
    assert metrics["write_verified"] is True
    assert metrics["pre_snapshot"]["k_factor"] == 500.0


def test_modbus_module_runtime_exports_and_imports_calibration_history(
    tmp_path,
) -> None:
    source_repository = _repository(tmp_path / "source")
    source_transport = placeholder_fake_transport()
    register_map = ModbusModuleRuntime(
        source_repository,
        transport_factory=lambda _config: source_transport,
        zero_calibration_wait_s=0.0,
    ).register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    source_transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 0.0),
    ]
    source_transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 112.0),
    ]
    source_runtime = ModbusModuleRuntime(
        source_repository,
        transport_factory=lambda _config: source_transport,
        zero_calibration_wait_s=0.0,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(source_runtime, device_id="CFM-EXPORT-001")
    source_runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    zero_result = source_runtime.run_zero_calibration()
    capture = source_runtime.capture_k_factor_simple_trial(
        snapshot_variable_names=("mass_acc", "k_factor"),
        poll_interval_s=0.001,
    )
    k_result = source_runtime.calculate_k_factor_simple_result(
        capture,
        standard_mass=12.6,
        save_history=True,
    )
    source_runtime.update_calibration_history_note(
        zero_result.run_id,
        "source note",
    )

    export_path = tmp_path / "exports" / "history.json"
    export_result = source_runtime.export_calibration_history(export_path)
    filtered_export_path = tmp_path / "filtered.json"
    filtered_result = source_runtime.export_calibration_history(
        filtered_export_path,
        device_id="OTHER-DEVICE",
    )

    assert export_result.path == export_path
    assert export_result.run_count >= 2
    assert export_result.analysis_result_count >= 2
    assert filtered_result.run_count == 0
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["format"] == "coreflow.modbus.calibration_history"
    assert payload["format_version"] == 1
    assert payload["future_exports"]["excel"] == "reserved"

    zero_run = source_repository.get_run(zero_result.run_id)
    assert zero_run is not None and zero_run.started_at is not None
    k_run = source_repository.get_run(k_result.run_id)
    assert k_run is not None and k_run.started_at is not None
    ranged_export_path = tmp_path / "exports" / "history_zero_range.json"
    ranged_export = source_runtime.export_calibration_history(
        ranged_export_path,
        started_from=zero_run.started_at,
        started_to=zero_run.started_at,
    )
    ranged_payload = json.loads(ranged_export_path.read_text(encoding="utf-8"))
    assert ranged_export.run_count == 1
    assert ranged_payload["started_from"] == zero_run.started_at.isoformat()
    assert ranged_payload["started_to"] == zero_run.started_at.isoformat()
    assert [
        entry["run"]["workflow_name"]
        for entry in ranged_payload["entries"]
    ] == ["zero_calibration"]

    target_repository = _repository(tmp_path / "target")
    target_runtime = ModbusModuleRuntime(
        target_repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    import_result = target_runtime.import_calibration_history(export_path)

    assert import_result.imported_runs == 2
    assert import_result.skipped_runs == 0
    assert import_result.renamed_runs == 0
    assert import_result.imported_analysis_results == 2
    imported = target_runtime.list_calibration_history()
    assert {entry.run_id for entry in imported} == {
        zero_result.run_id,
        k_result.run_id,
    }
    assert target_repository.get_run(zero_result.run_id).notes == "source note"
    assert (
        target_repository.list_analysis_results(k_result.run_id)[-1]
        .summary_metrics["corrected_k_factor"]
        == 525.0
    )

    repeated = target_runtime.import_calibration_history(export_path)

    assert repeated.imported_runs == 0
    assert repeated.skipped_runs == 2
    repeated_records = target_runtime.list_test_records()
    assert {entry.run_id for entry in repeated_records} == {
        zero_result.run_id,
        k_result.run_id,
    }
    assert target_runtime.list_test_records(device_id="OTHER-DEVICE") == ()

    rebound_repository = _repository(tmp_path / "rebound")
    rebound_runtime = ModbusModuleRuntime(
        rebound_repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    rebound_runtime.save_device_profile(
        device_id="CFM-IMPORT-001",
        metadata=ModbusOperationMetadata(
            device_model="CFM-I",
            tube_model="T-I",
            transmitter_model="TX-I",
        ),
        register_map=rebound_runtime.register_map,
        select=True,
    )

    rebound_import = rebound_runtime.import_calibration_history(
        export_path,
        target_device_id="CFM-IMPORT-001",
    )

    assert rebound_import.imported_runs == 2
    rebound_records = rebound_runtime.list_test_records(device_id="CFM-IMPORT-001")
    assert {entry.run_id for entry in rebound_records} == {
        zero_result.run_id,
        k_result.run_id,
    }
    assert rebound_runtime.list_test_records(device_id="CFM-EXPORT-001") == ()
    rebound_run = rebound_repository.get_run(zero_result.run_id)
    assert rebound_run is not None
    assert rebound_run.device_id == "CFM-IMPORT-001"
    assert rebound_run.configuration_snapshot["device_id"] == "CFM-IMPORT-001"
    assert (
        rebound_run.configuration_snapshot["imported_from_device_id"]
        == "CFM-EXPORT-001"
    )

    retarget_import = target_runtime.import_calibration_history(
        export_path,
        target_device_id="CFM-IMPORT-002",
    )

    assert retarget_import.imported_runs == 0
    assert retarget_import.skipped_runs == 0
    assert retarget_import.retargeted_runs == 2
    retargeted_records = target_runtime.list_test_records(device_id="CFM-IMPORT-002")
    assert {entry.run_id for entry in retargeted_records} == {
        zero_result.run_id,
        k_result.run_id,
    }
    assert target_runtime.list_test_records(device_id="CFM-EXPORT-001") == ()

    zero_entry = next(
        entry
        for entry in payload["entries"]
        if entry["run"]["workflow_name"] == "zero_calibration"
    )
    collision_payload = dict(payload)
    collision_payload["entries"] = [dict(zero_entry)]
    collision_payload["entries"][0]["analysis_results"] = [
        dict(zero_entry["analysis_results"][0])
    ]
    collision_payload["entries"][0]["analysis_results"][0]["summary_metrics"] = dict(
        collision_payload["entries"][0]["analysis_results"][0]["summary_metrics"]
    )
    collision_payload["entries"][0]["analysis_results"][0]["summary_metrics"][
        "delta_t_after"
    ] = 999.0
    collision_path = tmp_path / "exports" / "history_collision.json"
    collision_path.write_text(
        json.dumps(collision_payload),
        encoding="utf-8",
    )

    collision_import = target_runtime.import_calibration_history(collision_path)

    assert collision_import.imported_runs == 1
    assert collision_import.renamed_runs == 1
    assert collision_import.skipped_runs == 0
    imported_ids = {
        entry.run_id
        for entry in target_runtime.list_calibration_history(
            operation="zero_calibration"
        )
    }
    assert zero_result.run_id in imported_ids
    renamed_ids = [run_id for run_id in imported_ids if run_id.startswith("IMPORTED-")]
    assert len(renamed_ids) == 1
    renamed_run = target_repository.get_run(renamed_ids[0])
    assert renamed_run is not None
    assert renamed_run.configuration_snapshot["imported_from_run_id"] == zero_result.run_id


def test_modbus_module_import_retargets_attempts_and_trials_for_current_device(
    tmp_path,
) -> None:
    source_repository = _repository(tmp_path / "source-repeatability")
    source_transport = placeholder_fake_transport()
    source_runtime = ModbusModuleRuntime(
        source_repository,
        transport_factory=lambda _config: source_transport,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(source_runtime, device_id="CFM-SOURCE-TRIALS")
    source_runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))

    register_map = source_runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    source_transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 0.0),
    ]
    source_transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 112.0),
    ]
    capture = source_runtime.capture_repeatability_simple_trial(
        flow_point=300.0,
        trial_index=1,
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        poll_interval_s=0.001,
        sample_variable_names=("temperature",),
        record_flow_samples=True,
    )
    trial = source_runtime.calculate_repeatability_simple_trial(
        capture,
        standard_mass=12.0,
    )
    result = source_runtime.calculate_repeatability_simple_result(
        (trial,),
        mode="single_point",
        expected_flow_point_count=1,
        expected_trials_per_point=1,
        require_complete=False,
    )

    export_path = tmp_path / "repeatability_history.json"
    source_runtime.export_calibration_history(export_path)
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert {
        entry["run"]["status"]
        for entry in payload["entries"]
        if entry.get("run") is not None
    } <= {"passed", "failed", "canceled", "error"}
    exported_artifacts = [
        artifact
        for entry in payload["entries"]
        for artifact in entry["artifacts"]
    ]
    flow_sample_payload = next(
        artifact
        for artifact in exported_artifacts
        if artifact["metadata"].get("curve_type") == "flow_rate_samples"
    )
    assert flow_sample_payload["content_encoding"] == "base64"
    assert flow_sample_payload["content_base64"]
    assert "temperature" in flow_sample_payload["metadata"]["variable_names"]

    target_repository = _repository(tmp_path / "target-repeatability")
    target_runtime = ModbusModuleRuntime(
        target_repository,
        transport_factory=placeholder_transport_factory([]),
    )
    target_import = target_runtime.import_calibration_history(
        export_path,
        target_device_id="CFM-CURRENT-DEVICE",
    )

    assert target_import.imported_runs == 1
    current_records = target_runtime.list_test_records(
        device_id="CFM-CURRENT-DEVICE",
        operation="manual_error_repeatability",
    )
    assert {record.operation for record in current_records} == {
        "manual_error_repeatability",
        "manual_error_repeatability_trial",
    }
    assert any(
        record.operation == "manual_error_repeatability_trial"
        and record.metrics["device_id"] == "CFM-CURRENT-DEVICE"
        and record.metrics["imported_from_device_id"] == "CFM-SOURCE-TRIALS"
        and record.metrics["percent_error"] == 0.0
        for record in current_records
    )
    assert target_runtime.list_test_records(device_id="CFM-SOURCE-TRIALS") == ()
    trial_records = target_repository.list_modbus_trial_records(
        device_id="CFM-CURRENT-DEVICE"
    )
    assert len(trial_records) == 1
    assert trial_records[0].run_id == result.run_id
    assert trial_records[0].device_id == "CFM-CURRENT-DEVICE"
    assert (
        trial_records[0].device_metadata["imported_from_device_id"]
        == "CFM-SOURCE-TRIALS"
    )
    imported_trial_record = next(
        record
        for record in current_records
        if record.operation == "manual_error_repeatability_trial"
        and record.metrics.get("flow_samples_artifact_id")
    )
    imported_series = target_runtime.load_flow_sample_series(
        str(imported_trial_record.metrics["flow_samples_artifact_id"])
    )
    assert imported_series.variable_names == ("mass_rate", "temperature")
    assert [sample.value for sample in imported_series.samples] == [
        0.0,
        5.0,
        5.5,
        0.0,
    ]
    assert [point.values["temperature"] for point in imported_series.points] == [
        21.5,
        21.5,
        21.5,
        21.5,
    ]

    repeat_import = target_runtime.import_calibration_history(
        export_path,
        target_device_id="CFM-CURRENT-DEVICE",
    )

    assert repeat_import.imported_runs == 0
    assert repeat_import.retargeted_runs == 1
    assert len(
        target_runtime.list_test_records(
            device_id="CFM-CURRENT-DEVICE",
            operation="manual_error_repeatability",
        )
    ) == 2


def test_modbus_module_import_accepts_legacy_running_raw_capture_runs(
    tmp_path,
) -> None:
    source_repository = _repository(tmp_path / "source-legacy-running")
    source_transport = placeholder_fake_transport()
    source_runtime = ModbusModuleRuntime(
        source_repository,
        transport_factory=lambda _config: source_transport,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    _select_runtime_profile(source_runtime, device_id="CFM-LEGACY-RUNNING")
    source_runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    register_map = source_runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    source_transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 0.0),
    ]
    source_transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 112.0),
    ]
    capture = source_runtime.capture_repeatability_simple_trial(
        flow_point=300.0,
        trial_index=1,
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        poll_interval_s=0.001,
    )
    trial = source_runtime.calculate_repeatability_simple_trial(
        capture,
        standard_mass=12.0,
    )
    source_runtime.calculate_repeatability_simple_result(
        (trial,),
        mode="single_point",
        expected_flow_point_count=1,
        expected_trials_per_point=1,
        require_complete=False,
    )
    export_path = tmp_path / "legacy_running_history.json"
    source_runtime.export_calibration_history(export_path)
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    for entry in payload["entries"]:
        run = entry.get("run")
        if (
            isinstance(run, dict)
            and run.get("workflow_name") == "manual_error_repeatability_trial"
        ):
            run["status"] = "running"
            run.setdefault("configuration_snapshot", {})["raw_capture_only"] = True
    export_path.write_text(json.dumps(payload), encoding="utf-8")

    target_repository = _repository(tmp_path / "target-legacy-running")
    target_runtime = ModbusModuleRuntime(
        target_repository,
        transport_factory=placeholder_transport_factory([]),
    )
    result = target_runtime.import_calibration_history(
        export_path,
        target_device_id="CFM-CURRENT-LEGACY",
    )

    assert result.errors == ()
    assert target_runtime.list_test_records(
        device_id="CFM-CURRENT-LEGACY",
        operation="manual_error_repeatability",
    )


def test_modbus_module_window_uses_own_connection_state(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(
        provider=lambda: (
            SerialPortInfo(port="COM8", description="Other adapter"),
            SerialPortInfo(
                port="COM9",
                description="USB Serial Adapter",
                manufacturer="FTDI",
            ),
        )
    )
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    window.deviceModelLineEdit.setText("CFM-200")
    window.tubeModelLineEdit.setText("T-50")
    window.transmitterModelLineEdit.setText("TX-11")
    assert runtime.operation_metadata == ModbusOperationMetadata(
        device_model="CFM-200",
        tube_model="T-50",
        transmitter_model="TX-11",
    )
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 2)
    dialog.portCombo.setCurrentIndex(dialog.portCombo.findData("COM9"))
    dialog.unitIdSpinBox.setValue(7)

    assert window.isWindow()
    assert dialog.isVisible()
    assert not dialog.portCombo.isEditable()
    assert "USB Serial Adapter" in dialog.portCombo.currentText()
    assert window.variableMapTable.rowCount() == 8
    assert _find_row(window.variableMapTable, "mass_rate") >= 0
    assert _find_row(window.variableMapTable, "temperature") >= 0
    mass_acc_row = _find_row(window.variableMapTable, "mass_acc")
    edit_dialog = _open_edit_profile_dialog(qtbot, window)
    mass_acc_dialog_row = _find_row(edit_dialog.mapTable, "mass_acc")
    _set_table_text(edit_dialog.mapTable, mass_acc_dialog_row, 2, "30")
    _click(qtbot, edit_dialog.saveButton)
    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, mass_acc_row, 2) == "30",
        timeout=5000,
    )
    transports.clear()
    assert not window.sampleVariablesAction.isEnabled()
    assert not hasattr(window, "sampleVariablesButton")
    assert not hasattr(window, "variableTable")
    assert window.kFactorInputsGroup.isHidden()
    assert not hasattr(window, "frameTable")
    assert window.logTextEdit.objectName() == "modbusLogTextEdit"
    assert isinstance(window.logTextEdit, QTextEdit)
    body_splitter = window.findChild(QSplitter, "modbusBodySplitter")
    assert body_splitter is not None
    assert body_splitter.orientation() == Qt.Orientation.Horizontal
    assert body_splitter.widget(0) is window.variableMapTable.parentWidget()
    assert body_splitter.widget(1) is window.logTextEdit.parentWidget()
    assert not hasattr(window, "exportCalibrationHistoryAction")
    assert not hasattr(window, "importCalibrationHistoryAction")
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    assert window.sampleVariablesAction.isEnabled()
    assert window.variableMapTable.isEnabled()
    assert not window.variableMapTable.item(mass_acc_row, 2).flags() & Qt.ItemFlag.ItemIsEditable
    assert window.variableMapTable.cellWidget(mass_acc_row, 11).isEnabled()
    assert dialog.isVisible()
    dialog.close()
    assert not dialog.isVisible()
    zero_start_row = _find_row(window.variableMapTable, "zero_calibration_start")
    zero_start_read = window.variableMapTable.cellWidget(zero_start_row, 11).layout().itemAt(0).widget()
    _click(qtbot, zero_start_read)
    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, zero_start_row, 9) == "false",
        timeout=5000,
    )
    transports[0].reads.clear()
    transports[0].registers[30] = transports[0].registers[10]
    del transports[0].registers[10]
    mass_acc_read, _mass_acc_write = window._operation_buttons(mass_acc_row)
    assert mass_acc_read is not None
    _click(qtbot, mass_acc_read)
    qtbot.waitUntil(lambda: "Read mass_acc" in window.logTextEdit.toPlainText(), timeout=5000)
    assert any(read[1] == 30 for read in transports[0].reads)
    window.zeroCalibrationAction.trigger()
    qtbot.waitUntil(
        lambda: window.zeroCalibrationDialog is not None
        and window.zeroCalibrationDialog.isVisible(),
        timeout=5000,
    )
    assert window.zeroCalibrationDialog is not None
    snapshot_table = window.zeroCalibrationDialog.snapshotTable
    assert snapshot_table.rowCount() >= 8
    assert (
        snapshot_table.item(
            _find_snapshot_row(snapshot_table, "mass_rate"),
            0,
        ).checkState()
        == Qt.CheckState.Checked
    )
    assert (
        snapshot_table.item(
            _find_snapshot_row(snapshot_table, "zero_calibration_start"),
            0,
        ).checkState()
        == Qt.CheckState.Unchecked
    )
    snapshot_table.item(_find_snapshot_row(snapshot_table, "k_factor"), 0).setCheckState(
        Qt.CheckState.Checked
    )
    _click(qtbot, window.zeroCalibrationDialog.startButton)
    qtbot.waitUntil(
        lambda: "Zero calibration completed" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert window.zeroCalibrationDialog.resultTable.item(0, 1).text()
    assert window.zeroCalibrationDialog.resultTable.item(0, 2).text()
    assert _find_row(window.zeroCalibrationDialog.snapshotResultTable, "k_factor") >= 0
    assert _table_text(window.variableMapTable, zero_start_row, 9) == "false"
    window.kFactorAction.trigger()
    qtbot.waitUntil(
        lambda: window.kFactorDialog is not None and window.kFactorDialog.isVisible(),
        timeout=5000,
    )
    assert window.kFactorDialog is not None
    assert not hasattr(window.kFactorDialog, "pcFlowSimulationCheckBox")
    assert not hasattr(window.kFactorDialog, "pcFlowValueSpinBox")
    assert not hasattr(window.kFactorDialog, "pcMassDeltaSpinBox")
    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: window.repeatabilityDialog is not None
        and window.repeatabilityDialog.isVisible(),
        timeout=5000,
    )
    assert window.repeatabilityDialog is not None
    assert window.repeatabilityDialog.modeCombo.currentData() == "three_point"
    assert window.repeatabilityDialog.modeCombo.model().item(1).isEnabled()
    assert not window.repeatabilityDialog.modeCombo.model().item(2).isEnabled()
    assert window.repeatabilityDialog.minimumWidth() <= 720
    assert not hasattr(window.repeatabilityDialog, "resultTable")
    assert window.repeatabilityDialog.configurationButton.isVisible()
    assert (
        window.repeatabilityDialog.standardMassSpinBox.buttonSymbols()
        == QAbstractSpinBox.ButtonSymbols.NoButtons
    )
    assert window.repeatabilityDialog.standardMassSpinBox.suffix() == " g"
    assert window.repeatabilityDialog.calculateTrialErrorButton.text() == (
        "Calculate Trial Error"
    )
    assert not window.repeatabilityDialog.calculateTrialErrorButton.isEnabled()
    assert (
        window.repeatabilityDialog.windowFlags()
        & Qt.WindowType.WindowMaximizeButtonHint
    )
    assert window.repeatabilityDialog.selectionSummaryTextEdit.maximumHeight() > 10000
    assert (
        window.repeatabilityDialog.trialTable.sizePolicy().verticalPolicy()
        == QSizePolicy.Policy.Expanding
    )
    assert (
        window.repeatabilityDialog.trialTable.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    header = window.repeatabilityDialog.trialTable.horizontalHeader()
    assert header.sectionsMovable()
    header.moveSection(9, 0)
    assert header.logicalIndex(0) == 9
    assert not window.repeatabilityDialog.originalKFactorValueLabel.isVisible()
    assert not window.repeatabilityDialog.saveConfigButton.isVisible()
    assert not hasattr(window.repeatabilityDialog, "pcFlowSimulationCheckBox")
    assert not hasattr(window.repeatabilityDialog, "pcFlowValueSpinBox")
    assert not hasattr(window.repeatabilityDialog, "pcMassDeltaSpinBox")

    assert window.statusValueLabel.text() == "Connected CFM-UI-001"
    log = window.logTextEdit.toPlainText()
    frame_lines = [line for line in log.splitlines() if " | " in line]
    assert len(frame_lines) >= 14
    assert any(" | TX | " in line for line in frame_lines)
    assert any(" | RX | " in line for line in frame_lines)
    assert "Zero calibration completed" in log
    assert repository.count_rows("run_sessions") == 1
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.calibrationHistoryDialog is not None
    assert window.calibrationHistoryDialog.historyTable.rowCount() >= 1
    table_operations = _column_texts(window.calibrationHistoryDialog.historyTable, 1)
    assert "Zero Calibration" in table_operations
    assert window.calibrationHistoryDialog.historyTable.columnCount() == 5
    assert window.calibrationHistoryDialog.importButton.text() == "Import..."
    assert window.calibrationHistoryDialog.exportButton.text() == "Export..."
    assert (
        _table_text(window.calibrationHistoryDialog.historyTable, 0, 3)
        or _table_text(window.calibrationHistoryDialog.historyTable, 1, 3)
    )
    detail_text = window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    assert "Run ID:" in detail_text
    assert "Device Metadata" in detail_text
    assert "Device Model: CFM-200" in detail_text
    assert "Tube Model: T-50" in detail_text
    assert "Transmitter Model: TX-11" in detail_text
    window.calibrationHistoryDialog.operationCombo.setCurrentIndex(
        window.calibrationHistoryDialog.operationCombo.findData("zero_calibration")
    )
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog.historyTable.rowCount() == 1,
        timeout=5000,
    )
    qtbot.waitUntil(
        lambda: "Pre-calibration Snapshot"
        in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
        and "k_factor:" in window.calibrationHistoryDialog.detailTextEdit.toPlainText(),
        timeout=5000,
    )
    zero_history_run = repository.get_run(
        window.calibrationHistoryDialog.historyTable.item(0, 2).text()
    )
    assert zero_history_run.started_at is not None
    assert (
        zero_history_run.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    )
    assert "+00:00" not in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    assert "delta_t=" in _table_text(window.calibrationHistoryDialog.historyTable, 0, 3)
    assert "zero_offset=" in _table_text(window.calibrationHistoryDialog.historyTable, 0, 3)
    notes = window.calibrationHistoryDialog.historyTable.item(0, 4)
    notes.setText("operator approved")
    qtbot.waitUntil(
        lambda: repository.get_run(
            window.calibrationHistoryDialog.historyTable.item(0, 2).text()
        ).notes
        == "operator approved",
        timeout=5000,
    )

    _click(qtbot, window.disconnectButton)
    assert window.statusValueLabel.text() == "Disconnected"
    assert window.variableMapTable.isEnabled()
    assert window.resetVariableMapButton.isEnabled()
    _set_table_text(window.variableMapTable, mass_acc_row, 2, "31")
    assert _table_text(window.variableMapTable, mass_acc_row, 2) == "31"


def test_modbus_module_window_loads_saved_device_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_profile_dialog(qtbot, window)
    dialog.deviceIdLineEdit.setText("CFM-PROFILE-001")
    dialog.deviceModelLineEdit.setText("CFM-500")
    dialog.tubeModelLineEdit.setText("T-80")
    dialog.transmitterModelLineEdit.setText("TX-80")
    mass_acc_row = _find_row(dialog.mapTable, "mass_acc")
    _set_table_text(dialog.mapTable, mass_acc_row, 2, "88")
    _click(qtbot, dialog.saveButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileCombo.findData("CFM-PROFILE-001") >= 0,
        timeout=5000,
    )

    second_runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    second = ModbusModuleWindow(
        repository,
        runtime=second_runtime,
        port_scanner=scanner,
    )
    qtbot.addWidget(second)
    second.show()
    index = second.deviceProfileCombo.findData("CFM-PROFILE-001")
    assert index >= 0
    second.deviceProfileCombo.setCurrentIndex(index)

    assert second.deviceIdLineEdit.text() == "CFM-PROFILE-001"
    assert second.deviceModelLineEdit.text() == "CFM-500"
    assert second.tubeModelLineEdit.text() == "T-80"
    assert second.transmitterModelLineEdit.text() == "TX-80"
    loaded_mass_acc_row = _find_row(second.variableMapTable, "mass_acc")
    assert _table_text(second.variableMapTable, loaded_mass_acc_row, 2) == "88"


def test_modbus_module_window_selects_recent_device_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    _save_profile_from_dialog(
        qtbot,
        window,
        device_id="CFM-RECENT-001",
        device_model="CFM-RECENT",
    )
    preferences_path = tmp_path / "config" / "modbus_module_ui.json"
    assert json.loads(preferences_path.read_text(encoding="utf-8"))[
        "last_device_profile_id"
    ] == "CFM-RECENT-001"

    second_runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    second = ModbusModuleWindow(
        repository,
        runtime=second_runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(second)
    second.show()

    assert second.deviceProfileCombo.currentData() == "CFM-RECENT-001"
    assert second.deviceIdLineEdit.text() == "CFM-RECENT-001"
    assert second.deviceModelLineEdit.text() == "CFM-RECENT"


def test_modbus_module_repeatability_configuration_requires_device_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    dialog = window._ensure_repeatability_dialog()
    dialog.show()
    _click(qtbot, dialog.configurationDialog.saveConfigButton)

    assert (
        "select a device profile"
        in dialog.configurationDialog.statusLabel.text().lower()
    )
    assert (
        "Save repeatability configuration failed: select a device profile first."
        in window.logTextEdit.toPlainText()
    )
    assert not (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_repeatability_simple.json"
    ).exists()


def test_modbus_module_repeatability_configuration_is_per_device_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    _save_profile_from_dialog(qtbot, window, device_id="CFM-REP-A")
    a_dialog = window._ensure_repeatability_dialog()
    a_dialog.show()
    a_dialog.flowPointSpinBoxes[0].setValue(111.0)
    a_dialog.pollIntervalSpinBox.setValue(0.25)
    a_dialog.instantFlowOffsetSpinBox.setValue(1.25)
    a_dialog.configurationDialog.recordFlowSamplesCheckBox.setChecked(True)
    a_dialog.apply_configuration(
        {
            "snapshot_variable_names": ["temperature", "mass_acc"],
            "sample_variable_names": ["temperature", "zero_offset"],
        }
    )
    a_dialog.operationNotesTextEdit.setPlainText("bench A warm-up complete")
    _click(qtbot, a_dialog.configurationDialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Repeatability configuration saved"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    a_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-REP-A"
        / "modbus_repeatability_simple.json"
    )
    assert a_path.exists()
    a_saved = json.loads(a_path.read_text(encoding="utf-8"))
    assert a_saved["flow_points"][0] == 111.0
    assert a_saved["poll_interval_s"] == 0.25
    assert a_saved["instant_flow_offset_s"] == 1.25
    assert a_saved["record_flow_samples"] is True
    assert a_saved["snapshot_variable_names"] == ["temperature", "mass_acc"]
    assert "post_snapshot_variable_names" not in a_saved
    assert a_saved["sample_variable_names"] == ["temperature", "zero_offset"]
    assert a_saved["operation_notes"] == "bench A warm-up complete"
    assert a_dialog.snapshotButton.text() == "Pre/Post Snapshot... (2)"
    assert a_dialog.sampleVariablesButton.text() == "Default Trial Samples... (3)"
    assert a_dialog.operationNotesLabel.text() == "bench A warm-up complete"

    _save_profile_from_dialog(qtbot, window, device_id="CFM-REP-B")
    b_dialog = window._ensure_repeatability_dialog()
    b_dialog.flowPointSpinBoxes[0].setValue(222.0)
    b_dialog.pollIntervalSpinBox.setValue(0.75)
    b_dialog.instantFlowOffsetSpinBox.setValue(2.5)
    b_dialog.configurationDialog.recordFlowSamplesCheckBox.setChecked(False)
    b_dialog.apply_configuration(
        {
            "snapshot_variable_names": ["low_threshold"],
            "sample_variable_names": ["low_threshold"],
        }
    )
    b_dialog.operationNotesTextEdit.setPlainText("bench B cold start")
    _click(qtbot, b_dialog.configurationDialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: window.logTextEdit.toPlainText().count(
            "Repeatability configuration saved"
        )
        >= 2,
        timeout=5000,
    )
    b_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-REP-B"
        / "modbus_repeatability_simple.json"
    )
    assert b_path.exists()
    b_saved = json.loads(b_path.read_text(encoding="utf-8"))
    assert b_saved["flow_points"][0] == 222.0
    assert b_saved["poll_interval_s"] == 0.75
    assert b_saved["instant_flow_offset_s"] == 2.5
    assert b_saved["record_flow_samples"] is False
    assert b_saved["snapshot_variable_names"] == ["low_threshold"]
    assert "post_snapshot_variable_names" not in b_saved
    assert b_saved["sample_variable_names"] == ["low_threshold"]
    assert b_saved["operation_notes"] == "bench B cold start"
    assert json.loads(a_path.read_text(encoding="utf-8")) == a_saved
    assert not (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_repeatability_simple.json"
    ).exists()

    index = window.deviceProfileCombo.findData("CFM-REP-A")
    assert index >= 0
    window.deviceProfileCombo.setCurrentIndex(index)
    qtbot.waitUntil(
        lambda: a_dialog.flowPointSpinBoxes[0].value() == 111.0
        and a_dialog.pollIntervalSpinBox.value() == 0.25
        and a_dialog.instantFlowOffsetSpinBox.value() == 1.25
        and a_dialog.configurationDialog.recordFlowSamplesCheckBox.isChecked()
        and a_dialog.selected_snapshot_variable_names() == ("temperature", "mass_acc")
        and a_dialog.selected_sample_variable_names()
        == ("temperature", "zero_offset")
        and a_dialog.operationNotesLabel.text() == "bench A warm-up complete",
        timeout=5000,
    )
    index = window.deviceProfileCombo.findData("CFM-REP-B")
    assert index >= 0
    window.deviceProfileCombo.setCurrentIndex(index)
    qtbot.waitUntil(
        lambda: a_dialog.flowPointSpinBoxes[0].value() == 222.0
        and a_dialog.pollIntervalSpinBox.value() == 0.75
        and a_dialog.instantFlowOffsetSpinBox.value() == 2.5
        and not a_dialog.configurationDialog.recordFlowSamplesCheckBox.isChecked()
        and a_dialog.selected_snapshot_variable_names() == ("low_threshold",)
        and a_dialog.selected_sample_variable_names() == ("low_threshold",)
        and a_dialog.operationNotesLabel.text() == "bench B cold start",
        timeout=5000,
    )


def test_modbus_module_zero_calibration_configuration_is_per_device_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    _save_profile_from_dialog(qtbot, window, device_id="CFM-ZERO-A")
    a_dialog = window._ensure_zero_calibration_dialog()
    a_dialog.show()
    for row in range(a_dialog.snapshotTable.rowCount()):
        a_dialog.snapshotTable.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    for name in ("temperature", "mass_acc"):
        a_dialog.snapshotTable.item(
            _find_snapshot_row(a_dialog.snapshotTable, name),
            0,
        ).setCheckState(Qt.CheckState.Checked)
    _click(qtbot, a_dialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Zero calibration configuration saved"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    a_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-ZERO-A"
        / "modbus_zero_calibration.json"
    )
    assert a_path.exists()
    a_saved = json.loads(a_path.read_text(encoding="utf-8"))
    assert a_saved["snapshot_variable_names"] == ["mass_acc", "temperature"]

    _save_profile_from_dialog(qtbot, window, device_id="CFM-ZERO-B")
    b_dialog = window._ensure_zero_calibration_dialog()
    for row in range(b_dialog.snapshotTable.rowCount()):
        b_dialog.snapshotTable.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    b_dialog.snapshotTable.item(
        _find_snapshot_row(b_dialog.snapshotTable, "low_threshold"),
        0,
    ).setCheckState(Qt.CheckState.Checked)
    _click(qtbot, b_dialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: window.logTextEdit.toPlainText().count(
            "Zero calibration configuration saved"
        )
        >= 2,
        timeout=5000,
    )
    b_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-ZERO-B"
        / "modbus_zero_calibration.json"
    )
    assert b_path.exists()
    b_saved = json.loads(b_path.read_text(encoding="utf-8"))
    assert b_saved["snapshot_variable_names"] == ["low_threshold"]
    assert json.loads(a_path.read_text(encoding="utf-8")) == a_saved
    assert not (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_zero_calibration.json"
    ).exists()

    index = window.deviceProfileCombo.findData("CFM-ZERO-A")
    assert index >= 0
    window.deviceProfileCombo.setCurrentIndex(index)
    qtbot.waitUntil(
        lambda: a_dialog.selected_snapshot_variable_names()
        == ("mass_acc", "temperature"),
        timeout=5000,
    )
    index = window.deviceProfileCombo.findData("CFM-ZERO-B")
    assert index >= 0
    window.deviceProfileCombo.setCurrentIndex(index)
    qtbot.waitUntil(
        lambda: a_dialog.selected_snapshot_variable_names() == ("low_threshold",),
        timeout=5000,
    )


def test_modbus_module_window_separates_new_edit_and_delete_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    assert window.createDeviceProfileButton.text() == "New Profile"
    assert window.editDeviceProfileButton.text() == "Edit Profile"
    assert window.deleteDeviceProfileButton.text() == "Delete"
    assert not window.editDeviceProfileButton.isEnabled()
    assert not window.deleteDeviceProfileButton.isEnabled()

    _save_profile_from_dialog(
        qtbot,
        window,
        device_id="CFM-DELETE-001",
        device_model="CFM-D",
    )
    assert window.editDeviceProfileButton.isEnabled()
    assert window.deleteDeviceProfileButton.isEnabled()

    edit_dialog = _open_edit_profile_dialog(qtbot, window)
    assert edit_dialog.deviceIdLineEdit.text() == "CFM-DELETE-001"
    edit_dialog.tubeModelLineEdit.setText("T-EDITED")
    _click(qtbot, edit_dialog.saveButton)
    qtbot.waitUntil(
        lambda: window.tubeModelLineEdit.text() == "T-EDITED",
        timeout=5000,
    )

    new_dialog = _open_profile_dialog(qtbot, window)
    assert new_dialog.deviceIdLineEdit.text() == ""
    new_dialog.close()

    _click(qtbot, window.deleteDeviceProfileButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileCombo.findData("CFM-DELETE-001") < 0,
        timeout=5000,
    )
    assert runtime.get_device_profile("CFM-DELETE-001") is None
    assert repository.get_device("CFM-DELETE-001") is not None
    assert "Test records were kept" in window.logTextEdit.toPlainText()


def test_modbus_module_repeatability_canceling_trial_sample_selection_skips_capture(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    rep_dialog = window._ensure_repeatability_dialog()
    rep_dialog.show()
    rep_dialog.configurationDialog.recordFlowSamplesCheckBox.setChecked(True)

    class CancelTrialSampleSelectionDialog:
        def __init__(self, *args, **kwargs) -> None:
            self.kwargs = kwargs

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(
        modbus_window_module,
        "SnapshotSelectionDialog",
        CancelTrialSampleSelectionDialog,
    )

    _click(qtbot, rep_dialog.startButton)
    qtbot.waitUntil(
        lambda: "canceled before trial sample selection"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert rep_dialog.current_capture() is None
    assert rep_dialog.captureProgressDialog is None
    assert rep_dialog.startButton.isEnabled()


def test_modbus_module_window_live_variables_follow_profile_register_map(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    _save_profile_from_dialog(qtbot, window, device_id="CFM-MAP-FOLLOW")
    edit_dialog = _open_edit_profile_dialog(qtbot, window)
    mass_acc_dialog_row = _find_row(edit_dialog.mapTable, "mass_acc")
    _set_table_text(edit_dialog.mapTable, mass_acc_dialog_row, 2, "188")
    _click(qtbot, edit_dialog.saveButton)
    qtbot.waitUntil(
        lambda: _table_text(
            window.variableMapTable,
            _find_row(window.variableMapTable, "mass_acc"),
            2,
        )
        == "188",
        timeout=5000,
    )
    assert runtime.register_map.by_name("mass_acc").address == 188

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-MAP-FOLLOW",
        timeout=5000,
    )
    mass_acc = runtime.register_map.by_name("mass_acc")
    transports[0].registers[mass_acc.address] = encode_registers(mass_acc, 321.0)
    transports[0].reads.clear()
    mass_acc_row = _find_row(window.variableMapTable, "mass_acc")
    read_button = window.variableMapTable.cellWidget(mass_acc_row, 11).layout().itemAt(0).widget()
    _click(qtbot, read_button)
    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, mass_acc_row, 9) == "321 kg",
        timeout=5000,
    )
    assert any(read[1] == 188 for read in transports[0].reads)


def test_modbus_operation_runtime_no_pc_flow_simulation_parameter(tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(repository)

    assert "pc_flow_simulation" not in inspect.signature(
        runtime.capture_k_factor_simple_trial
    ).parameters
    assert "pc_flow_simulation" not in inspect.signature(
        runtime.capture_repeatability_simple_trial
    ).parameters


def test_modbus_module_window_k_factor_simple_flow_calculates_and_writes(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transports[0].read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 0.0),
    ]
    transports[0].read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 112.0),
    ]

    window.kFactorAction.trigger()
    qtbot.waitUntil(
        lambda: window.kFactorDialog is not None and window.kFactorDialog.isVisible(),
        timeout=5000,
    )
    assert window.kFactorDialog is not None
    k_dialog = window.kFactorDialog
    assert k_dialog.modeCombo.currentData() == "simple"
    assert not k_dialog.modeCombo.model().item(1).isEnabled()
    assert k_dialog.flowRateCombo.currentText() == "mass_rate"
    assert k_dialog.flowAccCombo.currentText() == "mass_acc"
    assert k_dialog.kFactorCombo.currentText() == "k_factor"
    k_dialog.standardMassSpinBox.setValue(12.6)
    k_dialog.writeToDeviceCheckBox.setChecked(True)
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )

    _click(qtbot, k_dialog.startButton)
    qtbot.waitUntil(
        lambda: "K factor captured" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert k_dialog.calculateButton.isEnabled()
    _click(qtbot, k_dialog.calculateButton)
    qtbot.waitUntil(
        lambda: "K factor write applied; verified=True"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert "525" in _table_text(
        k_dialog.resultTable,
        _find_metric_row(k_dialog.resultTable, "K1"),
        1,
    )
    assert (
        _table_text(
            window.variableMapTable,
            _find_row(window.variableMapTable, "k_factor"),
            9,
        )
        == "525"
    )
    assert window.calibrationHistoryDialog is not None
    assert window.calibrationHistoryDialog.historyTable.rowCount() >= 1
    assert "K Factor" in _column_texts(
        window.calibrationHistoryDialog.historyTable,
        1,
    )
    assert any(
        "write=applied" in value
        for value in _column_texts(window.calibrationHistoryDialog.historyTable, 3)
    )
    history = runtime.list_calibration_history(operation="k_factor_calibration")
    assert len(history) == 1
    assert history[0].metrics["write_verified"] is True
    assert history[0].metrics["corrected_k_factor"] == 525.0


def test_modbus_module_window_repeatability_simple_records_nine_trials(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encoded
        for _trial in range(10)
        for encoded in (
            encode_registers(mass_rate, 0.0),
            encode_registers(mass_rate, 5.0),
            encode_registers(mass_rate, 5.5),
            encode_registers(mass_rate, 0.0),
        )
    ]
    measured_deltas = (
        100.0,
        101.0,
        99.0,
        50.0,
        51.0,
        49.0,
        20.0,
        20.2,
        19.8,
        52.0,
    )
    cumulative = 0.0
    mass_acc_reads = []
    for delta in measured_deltas:
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: window.repeatabilityDialog is not None
        and window.repeatabilityDialog.isVisible(),
        timeout=5000,
    )
    assert window.repeatabilityDialog is not None
    rep_dialog = window.repeatabilityDialog
    assert not hasattr(rep_dialog, "snapshotTable")
    assert rep_dialog.snapshotButton.text().startswith("Pre/Post Snapshot...")
    assert rep_dialog.configurationButton.isVisible()
    assert not rep_dialog.saveConfigButton.isVisible()
    assert "No repeatability selection yet." in (
        rep_dialog.selectionSummaryTextEdit.toPlainText()
    )
    assert rep_dialog.kFactorCombo.currentText() == "k_factor"
    rep_dialog.pollIntervalSpinBox.setValue(0.05)
    rep_dialog.instantFlowOffsetSpinBox.setValue(0.05)
    rep_dialog.apply_configuration(
        {"snapshot_variable_names": ["temperature"]}
    )
    rep_dialog.configurationDialog.recordFlowSamplesCheckBox.setChecked(True)
    for spin, value in zip(rep_dialog.flowPointSpinBoxes, (600.0, 300.0, 100.0)):
        spin.setValue(value)
    _click(qtbot, rep_dialog.configurationButton)
    assert rep_dialog.configurationDialog.isVisible()
    config_labels = {
        widget.text()
        for widget in rep_dialog.configurationDialog.findChildren(QLabel)
    }
    assert "Operation Note" in config_labels
    assert rep_dialog.configurationDialog.operationNotesTextEdit.isVisible()
    assert rep_dialog.configurationDialog.operationNotesTextEdit.placeholderText() == (
        "Enter this operation note"
    )
    _click(qtbot, rep_dialog.configurationDialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Repeatability configuration saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    sample_selection_calls: list[dict[str, object]] = []

    class FakeTrialSampleSelectionDialog:
        def __init__(
            self,
            _registers,
            *,
            selected_names,
            required_names=(),
            title="",
            object_name="",
            plot_layout=None,
            show_plot_layout=False,
            parent=None,
        ) -> None:
            self.index = len(sample_selection_calls)
            self.required_names = tuple(required_names)
            self._plot_layout = (
                modbus_window_module.PLOT_LAYOUT_SEPARATE
                if self.index == 0
                else modbus_window_module.PLOT_LAYOUT_OVERLAY
            )
            sample_selection_calls.append(
                {
                    "selected_names": tuple(selected_names),
                    "required_names": tuple(required_names),
                    "title": title,
                    "object_name": object_name,
                    "plot_layout": plot_layout,
                    "show_plot_layout": show_plot_layout,
                }
            )

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_names(self) -> tuple[str, ...]:
            return (*self.required_names, "temperature")

        def plot_layout(self) -> str:
            return self._plot_layout

    monkeypatch.setattr(
        modbus_window_module,
        "SnapshotSelectionDialog",
        FakeTrialSampleSelectionDialog,
    )
    add_trial_dialog_calls: list[dict[str, object]] = []

    class FakeAddTrialDialog:
        next_result = QDialog.DialogCode.Rejected
        next_selection = 300.0

        def __init__(
            self,
            flow_points,
            *,
            default_flow_point=None,
            parent=None,
        ) -> None:
            self.flow_points = tuple(flow_points)
            self.default_flow_point = default_flow_point
            add_trial_dialog_calls.append(
                {
                    "flow_points": self.flow_points,
                    "default_flow_point": self.default_flow_point,
                }
            )

        def exec(self):
            return self.next_result

        def selected_flow_point(self) -> float:
            return self.next_selection

    monkeypatch.setattr(
        modbus_window_module,
        "RepeatabilityAddTrialDialog",
        FakeAddTrialDialog,
    )

    standards = (100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 20.0, 20.0, 20.0)
    for index, standard_mass in enumerate(standards, start=1):
        _capture_and_calculate_repeatability_trial(
            qtbot,
            window,
            rep_dialog,
            standard_mass=standard_mass,
            expected_count=index,
        )
        if index == 1:
            assert rep_dialog.flowPlotDialog is not None
            assert rep_dialog.flowPlotDialog.isVisible()
            assert "samples" in rep_dialog.flowPlotDialog.summaryLabel.text()
            assert len(rep_dialog.flowPlotDialog._values["mass_rate"]) >= 3
            assert set(rep_dialog.flowPlotDialog._values) == {
                "mass_rate",
                "temperature",
            }
            assert (
                rep_dialog.flowPlotDialog._plot_layout
                == modbus_window_module.PLOT_LAYOUT_SEPARATE
            )
            assert set(rep_dialog.flowPlotDialog._plot_widgets) == {
                "mass_rate",
                "temperature",
            }
        if index == 3:
            qtbot.waitUntil(
                lambda: "flow_600_repeatability_stddev_percent: 1"
                in rep_dialog.selectionSummaryTextEdit.toPlainText(),
                timeout=5000,
            )
            assert rep_dialog.addTrialButton.isEnabled()
            FakeAddTrialDialog.next_result = QDialog.DialogCode.Rejected
            _click(qtbot, rep_dialog.addTrialButton)
            assert add_trial_dialog_calls[-1] == {
                "flow_points": (600.0,),
                "default_flow_point": 600.0,
            }
            assert rep_dialog.trialTable.rowCount() == 9

    assert "Repeatability completed" not in window.logTextEdit.toPlainText()
    assert len(sample_selection_calls) == 9
    assert {
        call["object_name"]
        for call in sample_selection_calls
    } == {"modbusRepeatabilityTrialSampleSelectionTable"}
    assert all(call["show_plot_layout"] is True for call in sample_selection_calls)
    assert all(
        call["required_names"] == ("mass_rate",)
        for call in sample_selection_calls
    )

    assert "trial_count: 9" in rep_dialog.selectionSummaryTextEdit.toPlainText()
    assert _table_text(rep_dialog.trialTable, 0, 6) == "5.5"
    assert _table_text(rep_dialog.trialTable, 0, 7)
    assert _table_text(rep_dialog.trialTable, 1, 9) == "1"
    assert _table_text(rep_dialog.trialTable, 5, 9) == "-2"
    assert rep_dialog.originalKFactorValueLabel.text() == "500"
    assert not rep_dialog.configurationButton.isEnabled()
    saved_trials = rep_dialog.trial_results()
    for flow_point, selected in (
        (600.0, tuple(saved_trials[0:3])),
        (300.0, tuple(saved_trials[3:6])),
        (100.0, tuple(saved_trials[6:9])),
    ):
        summary = runtime.summarize_repeatability_flow_point(
            selected,
            flow_point=flow_point,
        )
        rep_dialog.update_selected_repeatability(summary, selected)
    assert "Flow 300" in rep_dialog.selectionSummaryTextEdit.toPlainText()
    assert rep_dialog.calculateFinalKButton.isEnabled()
    _click(qtbot, rep_dialog.calculateFinalKButton)
    qtbot.waitUntil(
        lambda: "Final K calculated" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    final_records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability_final_k"
    ]
    assert len(final_records) == 1
    assert final_records[0].metrics["selected_trial_count"] == 9
    assert final_records[0].metrics["original_k_factor"] == 500.0
    assert final_records[0].metrics["write_status"] == "not_requested"
    assert rep_dialog.writeFinalKButton.isEnabled()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    _click(qtbot, rep_dialog.writeFinalKButton)
    qtbot.waitUntil(
        lambda: "Final K write applied" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert not rep_dialog.writeFinalKButton.isEnabled()
    assert "write_verified: true" in rep_dialog.selectionSummaryTextEdit.toPlainText()
    final_records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability_final_k"
    ]
    assert len(final_records) == 1
    assert final_records[0].metrics["write_status"] == "applied"
    assert final_records[0].metrics["write_verified"] is True
    assert final_records[0].metrics["readback_k_factor"] == 500.0
    assert repository.count_rows("audit_logs") == 1
    assert rep_dialog.addTrialButton.text() == "Add Trial"
    assert rep_dialog.addTrialButton.isEnabled()
    assert not hasattr(rep_dialog, "newTestButton")

    FakeAddTrialDialog.next_result = QDialog.DialogCode.Accepted
    FakeAddTrialDialog.next_selection = 300.0
    _click(qtbot, rep_dialog.addTrialButton)
    qtbot.waitUntil(
        lambda: rep_dialog.trialTable.rowCount() == 10,
        timeout=5000,
    )
    assert add_trial_dialog_calls[-1] == {
        "flow_points": (600.0, 300.0, 100.0),
        "default_flow_point": 100.0,
    }
    assert _table_text(rep_dialog.trialTable, 9, 0) == "300"
    assert _table_text(rep_dialog.trialTable, 9, 1) == "4"
    _capture_and_calculate_repeatability_trial(
        qtbot,
        window,
        rep_dialog,
        standard_mass=50.0,
        expected_count=10,
    )
    qtbot.waitUntil(
        lambda: "trial_count: 10"
        in rep_dialog.selectionSummaryTextEdit.toPlainText(),
        timeout=5000,
    )
    assert _table_text(rep_dialog.trialTable, 9, 9) == "4"
    final_records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability_final_k"
    ]
    assert len(final_records) == 1
    assert final_records[0].metrics["selected_trial_count"] == 9
    assert final_records[0].metrics["original_k_factor"] == 500.0
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.calibrationHistoryDialog is not None
    parameter_summaries = _column_texts(
        window.calibrationHistoryDialog.historyTable,
        3,
    )
    assert any("new_k=" in value for value in parameter_summaries)
    assert any("flow=300 kg/s trial=2" in value for value in parameter_summaries)
    assert any("error=2%" in value for value in parameter_summaries)
    test_records = runtime.list_test_records(operation="manual_error_repeatability")
    assert any(
        record.operation == "manual_error_repeatability_trial"
        and record.metrics["flow_point"] == 300.0
        and record.metrics["trial_index"] == 2
        and record.metrics["k_factor_parameter"] == "k_factor"
        and record.metrics["original_k_factor"] == 500.0
        and "flow_started_at" in record.metrics
        and "flow_ended_at" in record.metrics
        for record in test_records
    )
    assert any(
        record.metrics.get("instant_flow") == 5.5
        for record in test_records
    )
    assert any(
        "mean_flow" in record.metrics
        for record in test_records
        if record.operation == "manual_error_repeatability_trial"
    )
    sample_records = [
        record
        for record in test_records
        if record.operation == "manual_error_repeatability_trial"
        and record.metrics.get("flow_samples_artifact_id")
    ]
    assert sample_records
    assert all(record.metrics["flow_sample_count"] >= 3 for record in sample_records)
    detail_text = window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    assert "flow_samples_artifact_id:" in detail_text
    assert "flow_sample_count:" in detail_text
    flow_sample_artifacts = [
        artifact
        for artifact in repository.list_artifacts()
        if artifact.metadata.get("curve_type") == "flow_rate_samples"
    ]
    assert len(flow_sample_artifacts) >= 10
    assert (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-UI-001"
        / "modbus_repeatability_simple.json"
    ).exists()
    assert not (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_repeatability_simple.json"
    ).exists()
    saved = json.loads(
        (
            tmp_path
            / "config"
            / "workflow_templates"
            / "devices"
            / "CFM-UI-001"
            / "modbus_repeatability_simple.json"
        ).read_text(encoding="utf-8")
    )
    assert saved["k_factor_parameter"] == "k_factor"
    assert saved["record_flow_samples"] is True
    assert "original_k_factor" not in saved


def test_modbus_module_window_repeatability_single_point_appends_trials(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encoded
        for _trial in range(4)
        for encoded in (
            encode_registers(mass_rate, 0.0),
            encode_registers(mass_rate, 4.0),
            encode_registers(mass_rate, 4.2),
            encode_registers(mass_rate, 0.0),
        )
    ]
    measured_deltas = (100.0, 101.0, 99.0, 102.0)
    cumulative = 0.0
    mass_acc_reads = []
    for delta in measured_deltas:
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
        cumulative += delta
        mass_acc_reads.append(encode_registers(mass_acc, cumulative))
    transport.read_sequences[mass_acc.address] = mass_acc_reads

    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: window.repeatabilityDialog is not None
        and window.repeatabilityDialog.isVisible(),
        timeout=5000,
    )
    assert window.repeatabilityDialog is not None
    rep_dialog = window.repeatabilityDialog
    rep_dialog.modeCombo.setCurrentIndex(
        rep_dialog.modeCombo.findData("single_point")
    )
    rep_dialog.pollIntervalSpinBox.setValue(0.05)
    rep_dialog.flowPointSpinBoxes[0].setValue(250.0)
    rep_dialog.apply_configuration(
        {"snapshot_variable_names": ["temperature"]}
    )
    _click(qtbot, rep_dialog.configurationDialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Repeatability configuration saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    for index in range(1, 5):
        _capture_and_calculate_repeatability_trial(
            qtbot,
            window,
            rep_dialog,
            standard_mass=100.0,
            expected_count=index,
        )

    assert rep_dialog.trialTable.rowCount() == 5
    assert _table_text(rep_dialog.trialTable, 4, 2) == "Pending"
    assert _table_text(rep_dialog.trialTable, 3, 1) == "4"
    assert _table_text(rep_dialog.trialTable, 3, 9) == "2"
    summary_text = rep_dialog.selectionSummaryTextEdit.toPlainText()
    assert "flow_250_trial_count: 4" in summary_text
    assert "flow_250_repeatability_stddev_percent" in summary_text
    assert rep_dialog.calculateRepeatabilityButton.isEnabled()
    assert not rep_dialog.calculateFinalKButton.isEnabled()
    test_records = runtime.list_test_records(operation="manual_error_repeatability")
    assert sum(
        1
        for record in test_records
        if record.operation == "manual_error_repeatability_trial"
    ) == 4
    summary_records = [
        record
        for record in test_records
        if record.operation == "manual_error_repeatability"
    ]
    assert len(summary_records) == 1
    assert summary_records[0].metrics["mode"] == "single_point"
    assert summary_records[0].metrics["trial_count"] == 4.0
    assert rep_dialog.startButton.isEnabled()


def test_repeatability_flow_plot_dialog_shows_clicked_sample_point(qtbot) -> None:
    dialog = RepeatabilityFlowPlotDialog()
    qtbot.addWidget(dialog)
    first = datetime(2026, 6, 24, 8, 0, tzinfo=UTC)
    dialog.reset_trial(
        flow_parameter="mass_rate",
        variable_names=("mass_rate", "temperature"),
        units={"mass_rate": "g/s", "temperature": "degC"},
        plot_layout=modbus_window_module.PLOT_LAYOUT_OVERLAY,
    )
    dialog.add_sample(first, {"mass_rate": 0.0, "temperature": 20.0})
    dialog.add_sample(
        first + timedelta(seconds=1),
        {"mass_rate": 5.5, "temperature": 21.0},
    )

    item = dialog._point_items[("overlay", "mass_rate")]
    spots = item.points()
    assert len(spots) == 2

    dialog._point_clicked(item, [spots[1]])

    selected = dialog.selectedPointLabel.text()
    assert "current trial" in selected
    assert "mass_rate" in selected
    assert "sample #2" in selected
    assert "t=1 s" in selected
    assert "value=5.5 g/s" in selected


def test_modbus_module_window_three_flow_repeatability_selection_records_history(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    rep_dialog = window._ensure_repeatability_dialog()
    rep_dialog.show()
    rep_dialog.operationNotesTextEdit.setPlainText("three-flow summary note")
    trials = tuple(
        replace(
            _repeatability_trial(
                run_id="RUN-THREE-FLOW-SUMMARY",
                flow_point=600.0,
                trial_index=index,
                percent_error=error,
            ),
            notes=rep_dialog.operation_notes(),
        )
        for index, error in enumerate((0.0, 1.0, -1.0), start=1)
    )
    for trial in trials:
        rep_dialog.add_trial_result(trial)

    class FakeSelectionDialog:
        def __init__(self, _trials, *, parent=None):
            self.parent = parent

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_trials(self):
            return trials

    monkeypatch.setattr(
        modbus_window_module,
        "RepeatabilitySelectionDialog",
        FakeSelectionDialog,
    )

    _click(qtbot, rep_dialog.calculateRepeatabilityButton)

    records = [
        record
        for record in runtime.list_test_records(operation="manual_error_repeatability")
        if record.operation == "manual_error_repeatability"
    ]
    assert len(records) == 1
    assert records[0].notes == "three-flow summary note"
    assert records[0].metrics["mode"] == "three_point"
    assert records[0].metrics["flow_point_count"] == 1.0
    assert records[0].metrics["flow_point_600_repeatability_stddev_percent"] == 1.0


def test_modbus_module_window_repeatability_close_starts_new_operation(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 5.0),
        encode_registers(mass_rate, 5.5),
        encode_registers(mass_rate, 0.0),
    ]
    transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 0.0),
        encode_registers(mass_acc, 0.0),
        encode_registers(mass_acc, 100.0),
    ]

    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: window.repeatabilityDialog is not None
        and window.repeatabilityDialog.isVisible(),
        timeout=5000,
    )
    assert window.repeatabilityDialog is not None
    first_dialog = window.repeatabilityDialog
    first_dialog.pollIntervalSpinBox.setValue(0.05)
    _click(qtbot, first_dialog.startButton)
    qtbot.waitUntil(
        lambda: first_dialog.current_capture() is not None,
        timeout=5000,
    )
    assert len(first_dialog.trial_results()) == 0
    assert first_dialog.calculateTrialErrorButton.isEnabled()
    assert first_dialog.standardMassSpinBox.isEnabled()
    trial_records = runtime.list_test_records(operation="manual_error_repeatability")
    assert not any(
        record.operation == "manual_error_repeatability_trial"
        for record in trial_records
    )

    first_dialog.close()
    qtbot.waitUntil(lambda: window.repeatabilityDialog is None, timeout=5000)

    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: window.repeatabilityDialog is not None
        and window.repeatabilityDialog.isVisible(),
        timeout=5000,
    )
    assert window.repeatabilityDialog is not None
    reopened = window.repeatabilityDialog
    assert reopened is not first_dialog
    assert reopened.current_capture() is None
    assert len(reopened.trial_results()) == 0
    assert reopened.statusLabel.text() == "Ready"
    assert _table_text(reopened.trialTable, 0, 2) == "Pending"
    assert not hasattr(reopened, "saveTrialButton")
    assert hasattr(reopened, "calculateTrialErrorButton")
    assert not reopened.calculateTrialErrorButton.isEnabled()
    assert not reopened.calculateRepeatabilityButton.isEnabled()


def test_modbus_module_window_k_factor_cancel_on_close_recovers_controls(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
        k_factor_post_start_sample_s=0.0,
        k_factor_post_stop_delay_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    mass_rate = runtime.register_map.by_name("mass_rate")
    transports[0].read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0)
        for _ in range(20)
    ]

    window.kFactorAction.trigger()
    qtbot.waitUntil(
        lambda: window.kFactorDialog is not None and window.kFactorDialog.isVisible(),
        timeout=5000,
    )
    assert window.kFactorDialog is not None
    k_dialog = window.kFactorDialog
    k_dialog.pollIntervalSpinBox.setValue(0.05)
    _click(qtbot, k_dialog.startButton)
    qtbot.waitUntil(lambda: window._busy, timeout=5000)
    assert not k_dialog.startButton.isEnabled()

    k_dialog.close()
    qtbot.waitUntil(
        lambda: "K factor cancel requested." in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    qtbot.waitUntil(
        lambda: not window._busy
        and "K factor failed: K factor capture canceled."
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    window.kFactorAction.trigger()
    qtbot.waitUntil(
        lambda: window.kFactorDialog is not None and window.kFactorDialog.isVisible(),
        timeout=5000,
    )
    assert window.kFactorDialog.startButton.isEnabled()
    assert window.kFactorDialog.saveConfigButton.isEnabled()
    assert window.kFactorDialog.snapshotTable.isEnabled()


def test_modbus_module_window_recovers_controls_after_close_reopen_while_busy(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(
        provider=lambda: (sleep(0.05), (SerialPortInfo(port="COM9"),))[1]
    )
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    window.refresh_ports()
    qtbot.waitUntil(lambda: window._busy, timeout=5000)
    window.close()
    qtbot.waitUntil(lambda: not window._busy, timeout=5000)
    window.show()

    assert window.openConnectionButton.isEnabled()
    assert window.variableMapTable.isEnabled()
    assert not window.sampleVariablesAction.isEnabled()


def test_modbus_module_window_saves_and_loads_variable_map(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    zero_start_row = _find_row(window.variableMapTable, "zero_calibration_start")
    window._move_variable_row(zero_start_row, 0)
    assert _table_text(window.variableMapTable, 0, 0) == "zero_calibration_start"
    dialog = _open_profile_dialog(qtbot, window)
    dialog.deviceIdLineEdit.setText("CFM-MAP-001")
    _click(qtbot, dialog.addVariableButton)
    custom_row = dialog.mapTable.rowCount() - 1
    _set_table_text(dialog.mapTable, custom_row, 0, "custom_saved")
    dialog._move_register_row(custom_row, 1)
    zero_start_dialog_row = _find_row(dialog.mapTable, "zero_calibration_start")
    _set_table_text(dialog.mapTable, zero_start_dialog_row, 2, "16")

    _click(qtbot, dialog.saveButton)

    saved_path = tmp_path / "config" / "register_maps" / "modbus_module_map.json"
    assert not saved_path.exists()
    assert window.deviceProfileCombo.findData("CFM-MAP-001") >= 0

    second_runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    second = ModbusModuleWindow(
        repository,
        runtime=second_runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(second)
    second.show()
    index = second.deviceProfileCombo.findData("CFM-MAP-001")
    assert index >= 0
    second.deviceProfileCombo.setCurrentIndex(index)

    assert _column_texts(second.variableMapTable, 0)[:2] == [
        "zero_calibration_start",
        "custom_saved",
    ]
    assert _has_row(second.variableMapTable, "temperature")
    loaded_row = _find_row(second.variableMapTable, "zero_calibration_start")
    assert _table_text(second.variableMapTable, loaded_row, 2) == "16"
    custom_loaded_row = _find_row(second.variableMapTable, "custom_saved")
    assert second.variableMapTable.item(custom_loaded_row, 0).flags() & Qt.ItemFlag.ItemIsEditable


def test_modbus_module_window_saves_and_loads_k_factor_configuration(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    dialog = window._ensure_k_factor_dialog()
    dialog.show()
    dialog.flowRateCombo.setCurrentText("temperature")
    dialog.flowAccCombo.setCurrentText("mass_acc")
    dialog.kFactorCombo.setCurrentText("low_threshold")
    dialog.pollIntervalSpinBox.setValue(2.5)
    for row in range(dialog.snapshotTable.rowCount()):
        dialog.snapshotTable.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    for name in ("temperature", "mass_acc"):
        dialog.snapshotTable.item(
            _find_snapshot_row(dialog.snapshotTable, name),
            0,
        ).setCheckState(Qt.CheckState.Checked)

    _click(qtbot, dialog.saveConfigButton)

    saved_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_k_factor_simple.json"
    )
    assert saved_path.exists()
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["flow_rate_parameter"] == "temperature"
    assert saved["flow_acc_parameter"] == "mass_acc"
    assert saved["k_factor_parameter"] == "low_threshold"
    assert saved["poll_interval_s"] == 2.5
    assert saved["snapshot_variable_names"] == ["mass_acc", "temperature"]
    assert "nonzero_threshold" not in saved
    assert "write_to_device" not in saved

    second_runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    second = ModbusModuleWindow(
        repository,
        runtime=second_runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(second)
    loaded = second._ensure_k_factor_dialog()
    loaded.show()

    assert not hasattr(loaded, "nonzeroThresholdSpinBox")
    assert loaded.flowRateCombo.currentText() == "temperature"
    assert loaded.flowAccCombo.currentText() == "mass_acc"
    assert loaded.kFactorCombo.currentText() == "low_threshold"
    assert loaded.pollIntervalSpinBox.value() == 2.5
    assert loaded.selected_snapshot_variable_names() == ("mass_acc", "temperature")


def test_modbus_module_history_filters_running_runs_and_copies_selection(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    repository.save_device(
        DeviceRecord(
            device_id="CFM-HISTORY-001",
            device_type="modbus_rtu",
            protocol_address="1",
        )
    )
    repository.save_run(
        RunSession(
            run_id="RUN-RUNNING",
            run_type=RunType.CALIBRATION,
            workflow_name="zero_calibration",
            workflow_version="0.1",
            device_id="CFM-HISTORY-001",
            operator="pytest",
            status=RunStatus.RUNNING,
            started_at=datetime(2026, 6, 11, 0, 0, tzinfo=UTC),
        )
    )
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    _select_runtime_profile(runtime, device_id="CFM-HISTORY-001")
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    runtime.run_zero_calibration()

    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )

    history = window.calibrationHistoryDialog.historyTable
    assert history.rowCount() == 1
    assert history.columnCount() == 5
    assert history.item(0, 2).text() != "RUN-RUNNING"
    visible_run = repository.get_run(history.item(0, 2).text())
    assert visible_run.started_at is not None
    expected_started_at = visible_run.started_at.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert history.item(0, 0).text() == expected_started_at
    assert (
        f"Started: {expected_started_at}"
        in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    )
    assert "delta_t=" in history.item(0, 3).text()
    assert "zero_offset=" in history.item(0, 3).text()
    history.setRangeSelected(QTableWidgetSelectionRange(0, 1, 0, 2), True)
    window.calibrationHistoryDialog.copy_selection()
    assert QApplication.clipboard().text().startswith("Zero Calibration\tRUN-")


def test_modbus_calibration_history_dialog_requests_import_and_export(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    dialog = CalibrationHistoryDialog(runtime)
    qtbot.addWidget(dialog)
    dialog.show()
    requested_exports: list[object] = []
    import_requests: list[bool] = []
    dialog.exportRequested.connect(requested_exports.append)
    dialog.importRequested.connect(lambda: import_requests.append(True))

    _click(qtbot, dialog.exportButton)
    _click(qtbot, dialog.importButton)

    assert requested_exports == [{"operation": "all", "device_id": None}]
    assert import_requests == [True]
    assert dialog.windowTitle() == "All Test Records"
    operation_filters = [
        dialog.operationCombo.itemData(index)
        for index in range(dialog.operationCombo.count())
    ]
    status_filters = [
        dialog.statusFilterCombo.itemData(index)
        for index in range(dialog.statusFilterCombo.count())
    ]
    assert "manual_error_repeatability_final_k" in operation_filters
    assert "calculated" in status_filters
    assert dialog.deviceIdFilterLineEdit.placeholderText() == "Device ID"
    assert dialog.tubeModelFilterLineEdit.placeholderText() == "Tube Model"
    assert dialog.transmitterModelFilterLineEdit.placeholderText() == "Transmitter Model"
    assert dialog.showFlowDataButton.text() == "View Flow Data"


class _FlowHistoryRuntime:
    def __init__(self, entries: tuple[ModbusCalibrationHistoryEntry, ...]) -> None:
        self.entries = entries

    def list_test_records(self, **_kwargs):
        return self.entries

    def load_flow_sample_series(self, artifact_id: str) -> ModbusFlowSampleSeries:
        index = int(artifact_id[-1])
        start = datetime(2026, 6, 16, 10, index, tzinfo=UTC)
        return ModbusFlowSampleSeries(
            artifact_id=artifact_id,
            run_id=f"RUN-FLOW-{index}",
            flow_rate_parameter="mass_rate",
            unit="g/s",
            samples=(
                ModbusFlowSamplePoint(start, 0.0),
                ModbusFlowSamplePoint(start + timedelta(seconds=1), 4.0 + index),
                ModbusFlowSamplePoint(start + timedelta(seconds=2), 0.0),
            ),
            variable_names=("mass_rate", "temperature"),
            units={"mass_rate": "g/s", "temperature": "degC"},
            points=(
                ModbusTrialSamplePoint(
                    start,
                    {"mass_rate": 0.0, "temperature": 20.0 + index},
                ),
                ModbusTrialSamplePoint(
                    start + timedelta(seconds=1),
                    {"mass_rate": 4.0 + index, "temperature": 20.5 + index},
                ),
                ModbusTrialSamplePoint(
                    start + timedelta(seconds=2),
                    {"mass_rate": 0.0, "temperature": 21.0 + index},
                ),
            ),
        )

    def update_calibration_history_note(self, _run_id: str, _notes: str) -> None:
        return None


def test_modbus_calibration_history_dialog_plots_saved_trial_flow_samples(
    qtbot,
    monkeypatch,
) -> None:
    started = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    runtime = _FlowHistoryRuntime(
        (
            _history_entry(
                "RUN-FLOW-SUMMARY",
                "manual_error_repeatability",
                started,
            ),
        )
    )
    runtime.entries[0].metrics.update(
        {
            "trials": [
                {
                    "flow_point": 100.0,
                    "trial_index": 1,
                    "flow_samples_artifact_id": "FLOW-1",
                    "flow_sample_count": 3,
                },
                {
                    "flow_point": 100.0,
                    "trial_index": 2,
                    "flow_samples_artifact_id": "FLOW-2",
                    "flow_sample_count": 3,
                },
            ]
        }
    )

    dialog = CalibrationHistoryDialog(runtime)  # type: ignore[arg-type]
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.showFlowPlotButton.isEnabled()
    assert dialog.showFlowDataButton.isEnabled()
    assert dialog.compareFlowPlotsButton.isEnabled()

    _click(qtbot, dialog.showFlowPlotButton)
    assert dialog._flow_plot_dialog is not None
    assert dialog._flow_plot_dialog.variableTable.rowCount() == 2
    assert "3 samples" in dialog._flow_plot_dialog.summaryLabel.text()
    assert "mass_rate=0" in dialog._flow_plot_dialog.summaryLabel.text()
    temperature_row = _find_snapshot_row(
        dialog._flow_plot_dialog.variableTable,
        "temperature",
    )
    dialog._flow_plot_dialog.variableTable.item(temperature_row, 0).setCheckState(
        Qt.CheckState.Checked
    )
    assert "temperature=" in dialog._flow_plot_dialog.summaryLabel.text()
    assert (
        dialog._flow_plot_dialog._plot_layout
        == modbus_window_module.PLOT_LAYOUT_OVERLAY
    )
    assert set(dialog._flow_plot_dialog._plot_widgets) == {"overlay"}
    dialog._flow_plot_dialog.set_plot_layout(
        modbus_window_module.PLOT_LAYOUT_SEPARATE
    )
    assert (
        dialog._flow_plot_dialog._plot_layout
        == modbus_window_module.PLOT_LAYOUT_SEPARATE
    )
    assert set(dialog._flow_plot_dialog._plot_widgets) == {
        "mass_rate",
        "temperature",
    }
    assert dialog._flow_plot_dialog.selectedPointLabel.text() == (
        "Selected Point: none"
    )
    assert dialog._flow_plot_dialog._point_items
    point_item = dialog._flow_plot_dialog._point_items[0]
    spots = point_item.points()
    assert len(spots) == 3
    dialog._flow_plot_dialog._point_clicked(point_item, [spots[1]])
    selected = dialog._flow_plot_dialog.selectedPointLabel.text()
    assert "flow 100 trial 1" in selected
    assert "mass_rate" in selected
    assert "sample #2" in selected
    assert "t=1 s" in selected
    assert "value=5 g/s" in selected

    _click(qtbot, dialog.showFlowDataButton)
    assert dialog._flow_sample_table_dialog is not None
    assert dialog._flow_sample_table_dialog.sampleTable.rowCount() == 3
    table_headers = [
        dialog._flow_sample_table_dialog.sampleTable.horizontalHeaderItem(column).text()
        for column in range(dialog._flow_sample_table_dialog.sampleTable.columnCount())
    ]
    assert table_headers == [
        "captured_at_local",
        "elapsed_s",
        "sample_index",
        "mass_rate",
        "temperature",
    ]
    assert dialog._flow_sample_table_dialog.sampleTable.item(1, 3).text() == "5"
    assert dialog._flow_sample_table_dialog.sampleTable.item(1, 4).text() == "21.5"

    compare_selection_calls: list[tuple[tuple[str, str], ...]] = []

    class FakeCompareSelectionDialog:
        def __init__(self, items, *, parent=None) -> None:
            self.items = tuple(items)
            compare_selection_calls.append(self.items)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_items(self):
            return self.items

    monkeypatch.setattr(
        modbus_window_module,
        "CalibrationHistoryFlowCompareSelectionDialog",
        FakeCompareSelectionDialog,
    )

    _click(qtbot, dialog.compareFlowPlotsButton)
    assert compare_selection_calls == [
        (("FLOW-1", "flow 100 trial 1"), ("FLOW-2", "flow 100 trial 2"))
    ]
    assert dialog._flow_plot_dialog is not None
    assert "2 trials" in dialog._flow_plot_dialog.summaryLabel.text()
    assert "2 variable(s)" in dialog._flow_plot_dialog.summaryLabel.text()
    assert "One plot per variable" in dialog._flow_plot_dialog.summaryLabel.text()
    assert "aligned at first sample" in dialog._flow_plot_dialog.summaryLabel.text()
    alignment_index = dialog._flow_plot_dialog.plotAlignmentCombo.findData(
        modbus_window_module.PLOT_ALIGNMENT_PREFLOW_SAMPLE
    )
    assert alignment_index >= 0
    dialog._flow_plot_dialog.plotAlignmentCombo.setCurrentIndex(alignment_index)
    assert "aligned at pre-flow sample" in (
        dialog._flow_plot_dialog.summaryLabel.text()
    )
    point_item = dialog._flow_plot_dialog._point_items[0]
    spots = point_item.points()
    assert len(spots) == 3
    assert [spot.data()["elapsed_s"] for spot in spots] == [0.0, 1.0, 2.0]


def test_history_flow_plot_dialog_aligns_to_sample_before_flow(qtbot) -> None:
    dialog = modbus_window_module.CalibrationHistoryFlowPlotDialog()
    qtbot.addWidget(dialog)
    first = datetime(2026, 6, 24, 8, 0, tzinfo=UTC)
    series = ModbusFlowSampleSeries(
        artifact_id="FLOW-PREFLOW",
        run_id="RUN-PREFLOW",
        flow_rate_parameter="mass_rate",
        unit="g/s",
        samples=(),
        variable_names=("mass_rate", "temperature"),
        units={"mass_rate": "g/s", "temperature": "degC"},
        points=(
            ModbusTrialSamplePoint(
                first,
                {"mass_rate": 0.0, "temperature": 20.0},
            ),
            ModbusTrialSamplePoint(
                first + timedelta(seconds=1),
                {"mass_rate": 0.0, "temperature": 20.5},
            ),
            ModbusTrialSamplePoint(
                first + timedelta(seconds=2),
                {"mass_rate": 5.0, "temperature": 21.0},
            ),
            ModbusTrialSamplePoint(
                first + timedelta(seconds=3),
                {"mass_rate": 0.0, "temperature": 21.5},
            ),
        ),
    )

    dialog.set_series((("flow 100 trial 1", series), ("flow 100 trial 2", series)))
    alignment_index = dialog.plotAlignmentCombo.findData(
        modbus_window_module.PLOT_ALIGNMENT_PREFLOW_SAMPLE
    )
    dialog.plotAlignmentCombo.setCurrentIndex(alignment_index)

    assert "aligned at pre-flow sample" in dialog.summaryLabel.text()
    point_item = dialog._point_items[0]
    spots = point_item.points()
    assert [spot.data()["elapsed_s"] for spot in spots] == [-1.0, 0.0, 1.0, 2.0]


def test_history_flow_sample_table_displays_local_capture_time(qtbot) -> None:
    dialog = modbus_window_module.CalibrationHistoryFlowSampleTableDialog()
    qtbot.addWidget(dialog)
    first = datetime(2026, 6, 24, 8, 0, tzinfo=UTC)
    series = ModbusFlowSampleSeries(
        artifact_id="FLOW-TABLE-TIME",
        run_id="RUN-TABLE-TIME",
        flow_rate_parameter="mass_rate",
        unit="g/s",
        samples=(),
        variable_names=("mass_rate",),
        units={"mass_rate": "g/s"},
        points=(
            ModbusTrialSamplePoint(first, {"mass_rate": 0.0}),
            ModbusTrialSamplePoint(
                first + timedelta(seconds=1),
                {"mass_rate": 5.0},
            ),
        ),
    )

    dialog.set_series("flow 100 trial 1", series)

    assert dialog.sampleTable.horizontalHeaderItem(0).text() == "captured_at_local"
    assert dialog.sampleTable.item(0, 0).text() == first.astimezone().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert "+00:00" not in dialog.sampleTable.item(0, 0).text()
    assert dialog.sampleTable.item(0, 0).text() != first.isoformat()


def test_history_flow_plot_overlay_uses_right_axis_for_second_variable(
    qtbot,
) -> None:
    dialog = modbus_window_module.CalibrationHistoryFlowPlotDialog()
    qtbot.addWidget(dialog)
    first = datetime(2026, 6, 24, 8, 0, tzinfo=UTC)
    series = ModbusFlowSampleSeries(
        artifact_id="FLOW-DUAL-AXIS",
        run_id="RUN-DUAL-AXIS",
        flow_rate_parameter="mass_rate",
        unit="g/s",
        samples=(),
        variable_names=("mass_rate", "temperature"),
        units={"mass_rate": "g/s", "temperature": "degC"},
        points=(
            ModbusTrialSamplePoint(
                first,
                {"mass_rate": 0.0, "temperature": 20.0},
            ),
            ModbusTrialSamplePoint(
                first + timedelta(seconds=1),
                {"mass_rate": 5.0, "temperature": 21.0},
            ),
        ),
    )

    dialog.set_series((("flow 100 trial 1", series), ("flow 100 trial 2", series)))
    temperature_row = _find_snapshot_row(dialog.variableTable, "temperature")
    dialog.variableTable.item(temperature_row, 0).setCheckState(Qt.CheckState.Checked)

    assert dialog._plot_layout == modbus_window_module.PLOT_LAYOUT_OVERLAY
    assert dialog._overlay_right_axis_variable == "temperature"
    assert dialog._overlay_right_axis_view is not None
    assert dialog._overlay_right_axis_view.addedItems
    assert "2 variable(s)" in dialog.summaryLabel.text()


def test_modbus_calibration_history_formats_final_k_with_precision(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    repository.save_device(
        DeviceRecord(device_id="CFM-PRECISE-K", device_type="modbus_rtu")
    )
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="ATT-PRECISE-K",
            device_id="CFM-PRECISE-K",
            operation_type="manual_error_repeatability_final_k",
            status="calculated",
            operator="pytest",
            run_id="RUN-PRECISE-K",
            started_at=now,
            ended_at=now,
            summary={
                "original_k_factor": 500.0,
                "new_k_factor": 499.87503124218944,
                "delta_k_factor": -0.12496875781056072,
                "average_error": 0.35,
                "notes": "final k summary note",
                "flow_points": [
                    {
                        "flow_point": 100.0,
                        "repeatability_stddev_percent": 0.1,
                        "measurement_error_percent": 0.7,
                        "adjusted_error_percent": 0.35,
                        "intermediate_k_factor": 496.52432969215494,
                    }
                ],
            },
        )
    )
    dialog = CalibrationHistoryDialog(runtime, device_id="CFM-PRECISE-K")
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.historyTable.rowCount() == 1
    parameter_text = _table_text(dialog.historyTable, 0, 3)
    assert "new_k=499.875031242189" in parameter_text
    assert "delta_k=-0.124968757811" in parameter_text
    assert _table_text(dialog.historyTable, 0, 4) == "final k summary note"
    assert dialog.historyTable.horizontalHeaderItem(4).text() == "Operation Note"
    detail_text = dialog.detailTextEdit.toPlainText()
    assert "Operation Note: final k summary note" in detail_text
    assert "new_k_factor: 499.875031242189" in detail_text
    assert "delta_k_factor: -0.124968757811" in detail_text
    assert (
        "new_k_formula: intermediate_k = original_k / "
        "(1 + measurement_error_percent / 100)"
    ) in detail_text
    assert "intermediate_k_factor=496.524329692155" in detail_text


def test_modbus_calibration_history_displays_recorded_units(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    now = datetime(2026, 6, 16, 10, 0, tzinfo=UTC)
    repository.save_device(
        DeviceRecord(device_id="CFM-HISTORY-UNITS", device_type="modbus_rtu")
    )
    register_map_snapshot = {
        "registers": [
            {"name": "mass_rate", "unit": "g/s"},
            {"name": "mass_acc", "unit": "g"},
            {"name": "temperature", "unit": "C"},
            {"name": "zero_offset", "unit": "kg/s"},
            {"name": "k_factor", "unit": "pulse/g"},
        ]
    }
    repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="ATT-HISTORY-UNITS",
            device_id="CFM-HISTORY-UNITS",
            operation_type="manual_error_repeatability_trial",
            status="accepted",
            operator="pytest",
            run_id="RUN-HISTORY-UNITS",
            started_at=now,
            ended_at=now,
            register_map_snapshot=register_map_snapshot,
            summary={
                "flow_point": 120.0,
                "trial_index": 1,
                "flow_rate_parameter": "mass_rate",
                "flow_acc_parameter": "mass_acc",
                "k_factor_parameter": "k_factor",
                "original_k_factor": 500.0,
                "mass_acc_before": 100.0,
                "mass_acc_after": 112.5,
                "measured_mass_delta": 12.5,
                "standard_mass": 12.0,
                "percent_error": 4.1666666667,
                "mean_flow": 3.9,
                "instant_flow": 4.2,
                "duration_s": 3.0,
                "poll_interval_s": 0.5,
                "trial_sample_variable_names": ["mass_rate", "temperature"],
                "pre_snapshot": {
                    "temperature": 21.5,
                    "mass_acc": 100.0,
                    "zero_offset": 0.01,
                    "k_factor": 500.0,
                },
                "post_snapshot": {
                    "temperature": 21.8,
                    "mass_acc": 112.5,
                },
            },
        )
    )
    dialog = CalibrationHistoryDialog(runtime, device_id="CFM-HISTORY-UNITS")
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.historyTable.rowCount() == 1
    parameter_text = _table_text(dialog.historyTable, 0, 3)
    assert "flow=120 g/s trial=1" in parameter_text
    assert "delta_m=12.5 g" in parameter_text
    assert "standard=12 g" in parameter_text
    assert "k0=500 pulse/g" in parameter_text

    detail_text = dialog.detailTextEdit.toPlainText()
    assert "flow_point: 120 g/s" in detail_text
    assert "measured_mass_delta: 12.5 g" in detail_text
    assert "instant_flow: 4.2 g/s" in detail_text
    assert "mean_flow: 3.9 g/s" in detail_text
    assert "duration_s: 3 s" in detail_text
    assert "poll_interval_s: 0.5 s" in detail_text
    assert "trial_sample_variable_names: mass_rate (g/s), temperature (C)" in detail_text
    assert "temperature: 21.5 C" in detail_text
    assert "mass_acc: 100 g" in detail_text
    assert "zero_offset: 0.01 kg/s" in detail_text
    assert "k_factor: 500 pulse/g" in detail_text


def test_modbus_module_all_test_records_opens_without_profile(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: ())
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    window.allHistoryAction.trigger()

    qtbot.waitUntil(
        lambda: window.allHistoryDialog is not None
        and window.allHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.allHistoryDialog is not None
    assert window.calibrationHistoryDialog is window.allHistoryDialog
    assert window.allHistoryDialog.windowTitle() == "All Test Records"
    assert window.allHistoryDialog.deviceIdFilterLineEdit.isEnabled()
    assert window.allHistoryDialog.historyTable.rowCount() == 0


def test_modbus_module_current_device_import_retargets_records(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: ())
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    _save_profile_from_dialog(qtbot, window, device_id="CFM-IMPORT-UI")
    import_path = tmp_path / "history.json"
    import_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, str | None]] = []

    def fake_import(path, *, target_device_id=None):
        calls.append((str(path), target_device_id))
        return object()

    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(import_path), ""),
    )
    monkeypatch.setattr(runtime, "import_calibration_history", fake_import)

    def run_now(label, action, on_finished, **_kwargs):
        on_finished(action())

    monkeypatch.setattr(window, "_run_task", run_now)

    window._open_current_device_test_records()
    assert window.currentDeviceHistoryDialog is not None
    window.currentDeviceHistoryDialog.importRequested.emit()

    assert calls == [(str(import_path), "CFM-IMPORT-UI")]


def test_modbus_module_device_analysis_opens_for_selected_profile(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: ())
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    _save_profile_from_dialog(
        qtbot,
        window,
        device_id="CFM-ANALYSIS-001",
        device_model="CFM-A",
        tube_model="T-A",
        transmitter_model="TX-A",
    )

    assert window.deviceAnalysisAction.isEnabled()
    window.deviceAnalysisAction.trigger()

    qtbot.waitUntil(
        lambda: window.deviceAnalysisDialog is not None
        and window.deviceAnalysisDialog.isVisible(),
        timeout=5000,
    )
    assert window.deviceAnalysisDialog is not None
    assert window.deviceAnalysisDialog.titleLabel.text() == (
        "Device ID: CFM-ANALYSIS-001"
    )
    assert window.deviceAnalysisDialog.selectTrialsButton.text() == (
        "Select And Calculate..."
    )
    assert window.deviceAnalysisDialog.saveReportButton.text() == "Save"
    assert window.deviceAnalysisDialog.statusLabel.text() == (
        "Accepted trials available: 0"
    )
    assert not hasattr(window.deviceAnalysisDialog, "summaryTextEdit")
    assert not hasattr(window.deviceAnalysisDialog, "flowTable")
    assert not hasattr(window.deviceAnalysisDialog, "refreshButton")
    assert not hasattr(window.deviceAnalysisDialog, "selectComparisonVariablesButton")


def test_modbus_module_all_test_records_shows_load_errors(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )

    def broken_list_test_records(**_kwargs):
        raise RuntimeError("history database is unavailable")

    runtime.list_test_records = broken_list_test_records
    scanner = SerialPortScanner(provider=lambda: ())
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    window.allHistoryAction.trigger()

    qtbot.waitUntil(
        lambda: window.allHistoryDialog is not None
        and window.allHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.allHistoryDialog is not None
    assert window.allHistoryDialog.detailTitleLabel.text() == "Load Error"
    assert "history database is unavailable" in (
        window.allHistoryDialog.detailTextEdit.toPlainText()
    )


def test_modbus_calibration_history_export_dialog_selects_time_range(
    qtbot,
) -> None:
    started_at = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
    ended_at = datetime(2026, 6, 11, 9, 45, tzinfo=UTC)
    dialog = CalibrationHistoryExportDialog(
        operation="zero_calibration",
        entries=(
            _history_entry("RUN-1", "zero_calibration", started_at),
            _history_entry("RUN-2", "k_factor_calibration", ended_at),
        ),
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.selected_operation() == "zero_calibration"
    assert dialog.selected_started_from() is None
    assert dialog.selected_started_to() is None

    dialog.fromCheckBox.setChecked(True)
    dialog.toCheckBox.setChecked(True)

    assert dialog.selected_started_from() is not None
    assert dialog.selected_started_to() is not None


def test_modbus_module_window_manual_coil_write_refreshes_value_without_read(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    zero_start_row = _find_row(window.variableMapTable, "zero_calibration_start")
    write_value = window.variableMapTable.cellWidget(zero_start_row, 10)
    write_value.setText("1")
    transports[0].reads.clear()
    write_button = window.variableMapTable.cellWidget(zero_start_row, 11).layout().itemAt(1).widget()

    _click(qtbot, write_button)

    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, zero_start_row, 9) == "true",
        timeout=5000,
    )
    assert transports[0].coil_writes == [(16, True, 1)]
    assert transports[0].reads == []


def test_modbus_module_window_supports_custom_row_read_write_and_polling(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_profile_dialog(qtbot, window)
    dialog.deviceIdLineEdit.setText("CFM-UI-001")
    _click(qtbot, dialog.addVariableButton)
    custom_row = dialog.mapTable.rowCount() - 1
    _set_table_text(dialog.mapTable, custom_row, 0, "custom_value")
    _set_table_text(dialog.mapTable, custom_row, 2, "106")
    _set_table_text(dialog.mapTable, custom_row, 3, "1")
    dialog.mapTable.cellWidget(custom_row, 4).setCurrentText("uint16")
    dialog.mapTable.cellWidget(custom_row, 7).setCurrentText("true")
    _click(qtbot, dialog.saveButton)
    custom_row = _find_row(window.variableMapTable, "custom_value")

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    dialog.unitIdSpinBox.setValue(7)
    dialog.orderCombo.setCurrentText("BADC")
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    assert runtime.register_map.by_name("custom_value").byte_order.value == "little"
    assert repository.get_device("CFM-UI-001") is not None

    transports[0].registers[106] = [0x0001]
    read_button = window.variableMapTable.cellWidget(custom_row, 11).layout().itemAt(0).widget()
    _click(qtbot, read_button)
    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, custom_row, 9) == "1",
        timeout=5000,
    )

    write_value = window.variableMapTable.cellWidget(custom_row, 10)
    write_value.setText("42")
    write_button = window.variableMapTable.cellWidget(custom_row, 11).layout().itemAt(1).widget()
    _click(qtbot, write_button)
    qtbot.waitUntil(
        lambda: "Write custom_value: applied" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    qtbot.waitUntil(lambda: not window._busy, timeout=5000)
    assert transports[0].writes[-1][0] == 106
    assert repository.count_rows("audit_logs") == 1

    transports[0].reads.clear()
    zero_offset_row = _find_row(window.variableMapTable, "zero_offset")
    k_factor_row = _find_row(window.variableMapTable, "k_factor")
    window.variableMapTable.cellWidget(zero_offset_row, 8).setChecked(True)
    window.variableMapTable.cellWidget(k_factor_row, 8).setChecked(True)
    _click(qtbot, window.pollingButton)
    qtbot.waitUntil(lambda: any(read[1] == 100 and read[2] == 4 for read in transports[0].reads), timeout=5000)
    _click(qtbot, window.pollingButton)
    assert _table_text(window.variableMapTable, zero_offset_row, 9)
    assert _table_text(window.variableMapTable, k_factor_row, 9)


def test_modbus_module_window_supports_scrollable_reorderable_map_and_disables_nonwritable_write(
    qtbot,
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    assert not window.variableMapTable.verticalHeader().isVisible()
    assert window.variableMapTable.horizontalHeader().sectionsMovable()
    assert window.variableMapTable.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert window.variableMapTable.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert window.variableMapTable.verticalScrollMode() == QAbstractItemView.ScrollMode.ScrollPerPixel
    assert window.variableMapTable.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove
    assert window.variableMapTable.dragDropOverwriteMode() is False
    assert window.variableMapTable.defaultDropAction() == Qt.DropAction.MoveAction
    assert window.variableMapTable.dragEnabled()
    assert window.variableMapTable.acceptDrops()
    assert not hasattr(window, "moveVariableUpButton")
    assert not hasattr(window, "moveVariableDownButton")
    before_names = [
        _table_text(window.variableMapTable, row, 0)
        for row in range(window.variableMapTable.rowCount())
    ]
    temperature_row = _find_row(window.variableMapTable, "temperature")
    window._move_variable_row(temperature_row, 0)
    after_names = [
        _table_text(window.variableMapTable, row, 0)
        for row in range(window.variableMapTable.rowCount())
    ]
    assert len(after_names) == len(before_names)
    assert sorted(after_names) == sorted(before_names)
    assert after_names[0] == "temperature"
    window.variableMapTable.horizontalHeader().moveSection(11, 0)
    window.variableMapTable.cellWidget(_find_row(window.variableMapTable, "k_factor"), 7).setCurrentText(
        "false"
    )

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    dialog.unitIdSpinBox.setValue(7)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    assert runtime.register_map.registers[0].name == "temperature"
    assert not window.variableMapTable.dragEnabled()
    assert not window.variableMapTable.acceptDrops()
    assert not window.deleteVariableButton.isEnabled()
    k_factor_row = _find_row(window.variableMapTable, "k_factor")
    read_button, write_button = window._operation_buttons(k_factor_row)
    assert read_button is not None and read_button.isEnabled()
    assert write_button is not None and not write_button.isEnabled()
    assert not window.variableMapTable.cellWidget(k_factor_row, 10).isEnabled()


def test_modbus_module_window_variable_map_uses_native_scroll_bar(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    for _ in range(20):
        window._add_variable_row()
    window.resize(1080, 520)
    qtbot.wait(50)
    scroll_bar = window.variableMapTable.verticalScrollBar()
    assert scroll_bar.maximum() > 0
    assert scroll_bar.isVisible()
    assert window.variableMapTable.viewport().height() > 80
    assert scroll_bar.value() == 0
    window._busy = True
    window._set_controls_enabled(False)
    assert window.variableMapTable.isEnabled()

    previous_value = scroll_bar.value()
    scroll_bar.setValue(scroll_bar.maximum())
    assert scroll_bar.value() > previous_value


def test_modbus_module_window_rejects_invalid_profile_register_map(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    _save_profile_from_dialog(qtbot, window, device_id="CFM-INVALID-MAP")
    dialog = _open_edit_profile_dialog(qtbot, window)
    mass_rate_row = _find_row(dialog.mapTable, "mass_rate")
    _set_table_text(dialog.mapTable, mass_rate_row, 2, "-1")

    _click(qtbot, dialog.saveButton)

    assert "Address must be non-negative for mass_rate." in dialog.statusLabel.text()
    assert "Save device profile failed" in window.logTextEdit.toPlainText()


def test_modbus_module_window_logs_operation_failures_without_crashing(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    dialog.unitIdSpinBox.setValue(7)

    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    transports[0].read_errors[12] = "timeout"
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: window.variableSamplingDialog is not None
        and window.variableSamplingDialog.isVisible(),
        timeout=5000,
    )
    sampling_dialog = window.variableSamplingDialog
    assert sampling_dialog is not None
    _set_variable_sampling_selection(sampling_dialog, ("delta_t",))
    _click(qtbot, sampling_dialog.startButton)
    qtbot.waitUntil(lambda: sampling_dialog.stopButton.isEnabled(), timeout=5000)
    _click(qtbot, sampling_dialog.stopButton)
    qtbot.waitUntil(
        lambda: "Variable sampling failed:" in window.logTextEdit.toPlainText()
        and "stopped before any samples" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert window.isVisible()
    assert window.sampleVariablesAction.isEnabled()
    assert not hasattr(window, "variableTable")


def test_modbus_module_window_recovers_after_slow_partial_sample(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    dialog.unitIdSpinBox.setValue(7)

    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    transports[0].read_delay_s = 0.05
    transports[0].read_errors[104] = "timeout"
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: window.variableSamplingDialog is not None
        and window.variableSamplingDialog.isVisible(),
        timeout=5000,
    )
    sampling_dialog = window.variableSamplingDialog
    assert sampling_dialog is not None
    _set_variable_sampling_selection(sampling_dialog, ("mass_rate", "low_threshold"))
    sampling_dialog.pollIntervalSpinBox.setValue(0.05)
    _click(qtbot, sampling_dialog.startButton)
    assert not window.sampleVariablesAction.isEnabled()
    qtbot.waitUntil(
        lambda: "Variable sampling warning: low_threshold: timeout"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    _click(qtbot, sampling_dialog.stopButton)
    qtbot.waitUntil(
        lambda: "Variable sampling saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert window.sampleVariablesAction.isEnabled()
    assert not hasattr(window, "variableTable")


def test_modbus_module_window_sends_raw_frame_with_auto_crc(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    assert window.rawFrameAutoCrcCheckBox.isChecked()
    transports[0].registers[0x0000] = [0x0001, 0x0002]
    transports[0].raw_response = bytes.fromhex("01")
    window.rawFrameLineEdit.setText("01 03 00 00 00 02")
    _click(qtbot, window.sendRawFrameButton)

    qtbot.waitUntil(
        lambda: " | RX | raw_frame | 01 03 04 00 01 00 02 2A 32"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert transports[0].raw_frames == []
    assert transports[0].reads == [(RegisterKind.HOLDING, 0x0000, 2, 1)]
    log = window.logTextEdit.toPlainText()
    assert " | TX | raw_frame | 01 03 00 00 00 02 C4 0B" in log
    assert " | RX | raw_frame | 01 03 04 00 01 00 02 2A 32" in log
    assert "Raw frame sent" not in log


def test_modbus_module_window_sends_raw_frame_without_auto_crc(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    window.rawFrameAutoCrcCheckBox.setChecked(False)
    window.rawFrameLineEdit.setText("01 03 00 00 00 02 99 88")
    _click(qtbot, window.sendRawFrameButton)

    qtbot.waitUntil(lambda: len(transports[0].raw_frames) == 1, timeout=5000)
    assert transports[0].raw_frames == [bytes.fromhex("01 03 00 00 00 02 99 88")]
    log = window.logTextEdit.toPlainText()
    assert " | TX | raw_frame | 01 03 00 00 00 02 99 88" in log
    assert " | RX | raw_frame | no response" in log
    assert "Raw frame sent" not in log


def test_modbus_module_window_rejects_invalid_raw_frame_input(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    window.rawFrameLineEdit.setText("01 0Z")
    _click(qtbot, window.sendRawFrameButton)

    assert transports[0].raw_frames == []
    assert "Raw frame failed: invalid hex byte: 0Z" in window.logTextEdit.toPlainText()


def test_modbus_module_window_rejects_empty_prefixed_raw_frame(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    window.rawFrameLineEdit.setText("0x")
    _click(qtbot, window.sendRawFrameButton)

    assert transports[0].raw_frames == []
    assert "Raw frame failed: enter at least one hex byte." in window.logTextEdit.toPlainText()


def test_modbus_module_window_saves_variable_sampling_configuration(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=scanner,
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )

    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: window.variableSamplingDialog is not None
        and window.variableSamplingDialog.isVisible(),
        timeout=5000,
    )
    sampling_dialog = window.variableSamplingDialog
    assert sampling_dialog is not None
    _set_variable_sampling_selection(sampling_dialog, ("delta_t", "low_threshold"))
    sampling_dialog.pollIntervalSpinBox.setValue(0.25)
    separate_index = sampling_dialog.plotLayoutCombo.findData("separate")
    assert separate_index >= 0
    sampling_dialog.plotLayoutCombo.setCurrentIndex(separate_index)
    _click(qtbot, sampling_dialog.saveConfigButton)

    config_path = (
        tmp_path
        / "config"
        / "workflow_templates"
        / "devices"
        / "CFM-UI-001"
        / "modbus_variable_sampling.json"
    )
    assert config_path.exists()
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["variable_names"] == ["delta_t", "low_threshold"]
    assert saved["poll_interval_s"] == 0.25
    assert saved["plot_layout"] == "separate"

    sampling_dialog.close()
    window.variableSamplingDialog = None
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: window.variableSamplingDialog is not None
        and window.variableSamplingDialog.isVisible(),
        timeout=5000,
    )
    restored_dialog = window.variableSamplingDialog
    assert restored_dialog is not None
    assert restored_dialog.selected_variable_names() == ("delta_t", "low_threshold")
    assert restored_dialog.pollIntervalSpinBox.value() == pytest.approx(0.25)
    assert restored_dialog.plot_layout() == "separate"
    assert "Variable sampling configuration saved" in window.logTextEdit.toPlainText()


def test_modbus_module_window_disables_connect_without_discovered_ports(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory([]),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: ())
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)

    assert dialog.portCombo.currentText() == "No serial ports found"
    assert dialog.portCombo.currentData() == ""
    assert not dialog.connectButton.isEnabled()
    assert "No serial ports found." in window.logTextEdit.toPlainText()


def test_modbus_module_window_shows_variable_sampling_operation(qtbot, tmp_path) -> None:
    repository = _repository(tmp_path)
    transports = []
    runtime = ModbusModuleRuntime(
        repository,
        transport_factory=placeholder_transport_factory(transports),
        zero_calibration_wait_s=0.0,
    )
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    window = ModbusModuleWindow(repository, runtime=runtime, port_scanner=scanner)
    qtbot.addWidget(window)
    window.show()

    operation_texts = [
        action.text()
        for action in window.operationsMenu.actions()
        if not action.isSeparator()
    ]

    assert "Variable Sampling" in operation_texts
    assert "All Test Records" in operation_texts
    assert "Current Device Test Records" in operation_texts
    assert not window.sampleVariablesAction.isEnabled()
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected CFM-UI-001",
        timeout=5000,
    )
    assert window.sampleVariablesAction.isEnabled()
    window.sampleVariablesAction.trigger()
    assert window.variableSamplingDialog is not None
    assert window.variableSamplingDialog.isVisible()
    assert (
        window.variableSamplingDialog.variableTable.objectName()
        == "modbusVariableSamplingVariableTable"
    )
    assert (
        window.variableSamplingDialog.saveConfigButton.objectName()
        == "modbusVariableSamplingSaveConfigButton"
    )

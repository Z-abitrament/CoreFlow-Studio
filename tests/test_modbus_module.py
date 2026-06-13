from __future__ import annotations

import json
from datetime import UTC, datetime
from time import sleep

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableWidgetSelectionRange

from coreflow.analysis.calibration import RepeatabilityTrial
from coreflow.app.modbus_runtime import (
    ModbusCalibrationHistoryEntry,
    ModbusConnectionSettings,
    ModbusModuleRuntime,
    ModbusOperationMetadata,
    PcFlowSimulationSettings,
)
from coreflow.hardware import SerialPortInfo, SerialPortScanner
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    RegisterKind,
    encode_registers,
)
from coreflow.storage import Database, StorageRepository
from coreflow.storage.models import DeviceRecord
from coreflow.ui.modbus_window import (
    CalibrationHistoryDialog,
    CalibrationHistoryExportDialog,
    ModbusModuleWindow,
)
from coreflow.workflows import RunSession, RunStatus, RunType
from tests.modbus_fakes import placeholder_fake_transport, placeholder_transport_factory


def _repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return StorageRepository(database)


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


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
        device_id="modbus:COM9:1",
        operator="pytest",
        metrics={},
    )


def _open_connection_dialog(qtbot, window: ModbusModuleWindow):
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
    assert status.device_id == "modbus:COM9:7"
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
            snapshot_variable_names=("temperature",),
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
            )
        )

    result = runtime.calculate_repeatability_simple_result(tuple(trials))

    assert repository.get_run_status(result.run_id) == "passed"
    assert result.analysis.summary_metrics["trial_count"] == 9.0
    assert result.analysis.summary_metrics["max_repeatability_stddev_percent"] == 2.0
    assert result.trials[1].percent_error == 1.0
    history = runtime.list_calibration_history(operation="manual_error_repeatability")
    assert len(history) == 1
    assert history[0].metrics["max_abs_percent_error"] == 2.0
    assert history[0].metrics["flow_point_300_repeatability_stddev_percent"] == 2.0
    assert history[0].metrics["device_model"] == "CFM-R"
    assert history[0].metrics["tube_model"] == "T-R"
    assert history[0].metrics["transmitter_model"] == "TX-R"
    assert len(history[0].metrics["trials"]) == 9


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
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        *[
            encoded
            for _trial in range(4)
            for encoded in (
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


def test_modbus_module_runtime_pc_simulated_flow_keeps_device_reads(
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
    runtime.configure_operation_metadata(
        ModbusOperationMetadata(
            device_model="CFM-K",
            tube_model="T-K",
            transmitter_model="TX-K",
        )
    )
    runtime.connect(ModbusConnectionSettings(port="COM9", unit_id=1))
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
    ]
    transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
    ]

    capture = runtime.capture_k_factor_simple_trial(
        snapshot_variable_names=(),
        flow_rate_parameter="mass_rate",
        flow_acc_parameter="mass_acc",
        k_factor_parameter="k_factor",
        poll_interval_s=0.05,
        pc_flow_simulation=PcFlowSimulationSettings(
            enabled=True,
            start_flow=6.0,
            instant_flow=6.5,
            stop_flow=0.0,
            mass_delta=12.0,
        ),
    )
    result = runtime.calculate_k_factor_simple_result(
        capture,
        standard_mass=12.6,
    )

    assert capture.segment.flow_rate_source == "pc_simulated"
    assert capture.segment.start_flow == 6.0
    assert capture.segment.instant_flow == 6.5
    assert capture.measured_mass_delta == 12.0
    assert result.corrected_k_factor == 525.0
    assert any(read[1] == mass_rate.address for read in transport.reads)
    assert any(read[1] == mass_acc.address for read in transport.reads)
    history = runtime.list_calibration_history(operation="k_factor_calibration")
    assert len(history) == 1
    assert history[0].metrics["flow_rate_source"] == "pc_simulated"
    assert history[0].metrics["device_model"] == "CFM-K"
    assert history[0].metrics["tube_model"] == "T-K"
    assert history[0].metrics["transmitter_model"] == "TX-K"


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

    assert export_result.path == export_path
    assert export_result.run_count == 2
    assert export_result.analysis_result_count == 2
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
    _set_table_text(window.variableMapTable, mass_acc_row, 2, "30")
    transports.clear()
    assert not window.sampleVariablesAction.isEnabled()
    assert not hasattr(window, "sampleVariablesButton")
    assert not hasattr(window, "variableTable")
    assert window.kFactorInputsGroup.isHidden()
    assert window.frameTable.objectName() == "modbusFrameTable"
    assert not hasattr(window, "exportCalibrationHistoryAction")
    assert not hasattr(window, "importCalibrationHistoryAction")
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:7",
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
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(lambda: "Sampled 7 variables" in window.logTextEdit.toPlainText(), timeout=5000)
    assert any(read[1] == 30 for read in transports[0].reads)
    assert len(transports[0].reads) == 7
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: window.logTextEdit.toPlainText().count("Sampled 7 variables") == 2,
        timeout=5000,
    )
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

    assert window.statusValueLabel.text() == "Connected modbus:COM9:7"
    assert window.frameTable.rowCount() >= 14
    assert _table_text(window.frameTable, 0, 1) in {"TX", "RX"}
    log = window.logTextEdit.toPlainText()
    assert "Zero calibration completed" in log
    assert repository.count_rows("run_sessions") == 1
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.calibrationHistoryDialog is not None
    assert window.calibrationHistoryDialog.historyTable.rowCount() == 1
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
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
    assert window.calibrationHistoryDialog.historyTable.rowCount() == 1
    assert "write=applied" in _table_text(
        window.calibrationHistoryDialog.historyTable,
        0,
        3,
    )
    history = runtime.list_calibration_history(operation="k_factor_calibration")
    assert len(history) == 1
    assert history[0].metrics["write_verified"] is True
    assert history[0].metrics["corrected_k_factor"] == 525.0


def test_modbus_module_window_k_factor_pc_simulated_flow(
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
        timeout=5000,
    )
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transports[0].read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
    ]
    transports[0].read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
    ]

    window.kFactorAction.trigger()
    qtbot.waitUntil(
        lambda: window.kFactorDialog is not None and window.kFactorDialog.isVisible(),
        timeout=5000,
    )
    assert window.kFactorDialog is not None
    k_dialog = window.kFactorDialog
    k_dialog.pcFlowSimulationCheckBox.setChecked(True)
    k_dialog.pcFlowValueSpinBox.setValue(6.0)
    k_dialog.pcMassDeltaSpinBox.setValue(12.0)
    k_dialog.standardMassSpinBox.setValue(12.6)

    _click(qtbot, k_dialog.startButton)
    qtbot.waitUntil(
        lambda: "K factor captured" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert (
        _table_text(
            k_dialog.resultTable,
            _find_metric_row(k_dialog.resultTable, "flow_rate_source"),
            1,
        )
        == "pc_simulated"
    )
    assert _table_text(
        k_dialog.resultTable,
        _find_metric_row(k_dialog.resultTable, "delta_m"),
        1,
    ) == "12"
    _click(qtbot, k_dialog.calculateButton)
    qtbot.waitUntil(
        lambda: "K factor calculated" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert "525" in _table_text(
        k_dialog.resultTable,
        _find_metric_row(k_dialog.resultTable, "K1"),
        1,
    )
    assert any(read[1] == mass_rate.address for read in transports[0].reads)
    assert any(read[1] == mass_acc.address for read in transports[0].reads)


def test_modbus_module_window_repeatability_simple_records_nine_trials(
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
        timeout=5000,
    )
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
    mass_acc_reads = [encode_registers(mass_acc, cumulative)]
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
    rep_dialog.pollIntervalSpinBox.setValue(0.05)
    for spin, value in zip(rep_dialog.flowPointSpinBoxes, (600.0, 300.0, 100.0)):
        spin.setValue(value)
    _click(qtbot, rep_dialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Repeatability configuration saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    standards = (100.0, 100.0, 100.0, 50.0, 50.0, 50.0, 20.0, 20.0, 20.0)
    for index, standard_mass in enumerate(standards, start=1):
        rep_dialog.standardMassSpinBox.setValue(standard_mass)
        _click(qtbot, rep_dialog.startButton)
        qtbot.waitUntil(
            lambda count=index: window.logTextEdit.toPlainText().count(
                "Repeatability captured"
            )
            >= count,
            timeout=5000,
        )
        _click(qtbot, rep_dialog.saveTrialButton)
        qtbot.waitUntil(
            lambda count=index: len(rep_dialog.trial_results()) == count,
            timeout=5000,
        )
        if index == 3:
            qtbot.waitUntil(
                lambda: _has_metric_row(
                    rep_dialog.resultTable,
                    "flow_600_repeatability_stddev_percent",
                ),
                timeout=5000,
            )
            assert _table_text(
                rep_dialog.resultTable,
                _find_metric_row(
                    rep_dialog.resultTable,
                    "flow_600_repeatability_stddev_percent",
                ),
                1,
            ) == "1"

    qtbot.waitUntil(
        lambda: "Repeatability completed" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert _table_text(
        rep_dialog.resultTable,
        _find_metric_row(rep_dialog.resultTable, "trial_count"),
        1,
    ) == "9"
    assert _table_text(rep_dialog.trialTable, 0, 6) == "5.5"
    assert _table_text(rep_dialog.trialTable, 0, 7)
    assert _table_text(rep_dialog.trialTable, 1, 9) == "1"
    assert _table_text(rep_dialog.trialTable, 5, 9) == "-2"
    history = runtime.list_calibration_history(operation="manual_error_repeatability")
    assert len(history) == 1
    assert history[0].metrics["flow_point_300_repeatability_stddev_percent"] == 2.0
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.calibrationHistoryDialog is not None
    assert "max_repeatability=" in _table_text(
        window.calibrationHistoryDialog.historyTable,
        0,
        3,
    )
    assert (
        "trial 300/2"
        in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    )
    assert "v1=5.5" in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    assert "v_mean=" in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
    assert (
        tmp_path
        / "config"
        / "workflow_templates"
        / "modbus_repeatability_simple.json"
    ).exists()


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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        *[
            encoded
            for _trial in range(4)
            for encoded in (
                encode_registers(mass_rate, 4.0),
                encode_registers(mass_rate, 4.2),
                encode_registers(mass_rate, 0.0),
            )
        ],
    ]
    measured_deltas = (100.0, 101.0, 99.0, 102.0)
    cumulative = 0.0
    mass_acc_reads = [encode_registers(mass_acc, cumulative)]
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
    _click(qtbot, rep_dialog.saveConfigButton)
    qtbot.waitUntil(
        lambda: "Repeatability configuration saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    for index in range(1, 5):
        rep_dialog.standardMassSpinBox.setValue(100.0)
        _click(qtbot, rep_dialog.startButton)
        qtbot.waitUntil(
            lambda count=index: window.logTextEdit.toPlainText().count(
                "Repeatability captured"
            )
            >= count,
            timeout=5000,
        )
        _click(qtbot, rep_dialog.saveTrialButton)
        qtbot.waitUntil(
            lambda count=index: len(rep_dialog.trial_results()) == count,
            timeout=5000,
        )

    assert rep_dialog.trialTable.rowCount() == 5
    assert _table_text(rep_dialog.trialTable, 4, 2) == "Pending"
    assert _table_text(rep_dialog.trialTable, 3, 1) == "4"
    assert _table_text(rep_dialog.trialTable, 3, 9) == "2"
    assert _table_text(
        rep_dialog.resultTable,
        _find_metric_row(rep_dialog.resultTable, "flow_250_trial_count"),
        1,
    ) == "4"
    assert _has_metric_row(
        rep_dialog.resultTable,
        "flow_250_repeatability_stddev_percent",
    )
    assert rep_dialog.saveResultButton.isEnabled()

    _click(qtbot, rep_dialog.saveResultButton)
    qtbot.waitUntil(
        lambda: "Repeatability summary saved" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    history = runtime.list_calibration_history(operation="manual_error_repeatability")
    assert len(history) == 1
    assert history[0].metrics["mode"] == "single_point"
    assert history[0].metrics["trial_count"] == 4.0
    assert history[0].metrics["expected_trials_per_point"] == 4
    assert len(history[0].metrics["trials"]) == 4
    assert rep_dialog.startButton.isEnabled()


def test_modbus_module_window_repeatability_pc_simulated_flow_trial(
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
        encode_registers(mass_rate, 0.0),
    ]
    transport.read_sequences[mass_acc.address] = [
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
        encode_registers(mass_acc, 100.0),
    ]

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
    rep_dialog.pcFlowSimulationCheckBox.setChecked(True)
    rep_dialog.pcFlowValueSpinBox.setValue(6.0)
    rep_dialog.pcMassDeltaSpinBox.setValue(100.0)
    rep_dialog.standardMassSpinBox.setValue(100.0)

    _click(qtbot, rep_dialog.startButton)
    qtbot.waitUntil(
        lambda: "Repeatability captured" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    assert (
        _table_text(
            rep_dialog.resultTable,
            _find_metric_row(rep_dialog.resultTable, "flow_rate_source"),
            1,
        )
        == "pc_simulated"
    )
    _click(qtbot, rep_dialog.saveTrialButton)
    qtbot.waitUntil(
        lambda: len(rep_dialog.trial_results()) == 1,
        timeout=5000,
    )
    assert _table_text(rep_dialog.trialTable, 0, 5) == "100"
    assert _table_text(rep_dialog.trialTable, 0, 9) == "0"
    assert (
        _table_text(
            rep_dialog.resultTable,
            _find_metric_row(rep_dialog.resultTable, "last_flow_rate_source"),
            1,
        )
        == "pc_simulated"
    )
    assert any(read[1] == mass_rate.address for read in transport.reads)
    assert any(read[1] == mass_acc.address for read in transport.reads)


def test_modbus_module_window_repeatability_close_discards_incomplete_capture(
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
        timeout=5000,
    )
    transport = transports[0]
    register_map = runtime.register_map
    mass_rate = register_map.by_name("mass_rate")
    mass_acc = register_map.by_name("mass_acc")
    transport.read_sequences[mass_rate.address] = [
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
    assert reopened.saveTrialButton.isEnabled() is False


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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
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
    _click(qtbot, window.addVariableButton)
    custom_row = window.variableMapTable.rowCount() - 1
    _set_table_text(window.variableMapTable, custom_row, 0, "custom_saved")
    window._move_variable_row(custom_row, 1)
    temperature_row = _find_row(window.variableMapTable, "temperature")
    window.variableMapTable.selectRow(temperature_row)
    _click(qtbot, window.deleteVariableButton)
    assert "Deleted variable: temperature" in window.logTextEdit.toPlainText()
    assert not _has_row(window.variableMapTable, "temperature")
    assert _column_texts(window.variableMapTable, 0)[:2] == [
        "zero_calibration_start",
        "custom_saved",
    ]
    zero_start_row = _find_row(window.variableMapTable, "zero_calibration_start")
    _set_table_text(window.variableMapTable, zero_start_row, 2, "16")

    _click(qtbot, window.saveVariableMapButton)

    saved_path = tmp_path / "config" / "register_maps" / "modbus_module_map.json"
    assert saved_path.exists()
    assert "Variable map saved" in window.logTextEdit.toPlainText()

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

    assert _column_texts(second.variableMapTable, 0)[:2] == [
        "zero_calibration_start",
        "custom_saved",
    ]
    assert not _has_row(second.variableMapTable, "temperature")
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
            device_id="modbus:COM9:1",
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
            device_id="modbus:COM9:1",
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

    assert requested_exports == ["all"]
    assert import_requests == [True]


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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:1",
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
    _click(qtbot, window.addVariableButton)
    custom_row = window.variableMapTable.rowCount() - 1
    _set_table_text(window.variableMapTable, custom_row, 0, "custom_value")
    _set_table_text(window.variableMapTable, custom_row, 2, "106")
    _set_table_text(window.variableMapTable, custom_row, 3, "1")
    window.variableMapTable.cellWidget(custom_row, 4).setCurrentText("uint16")
    window.variableMapTable.cellWidget(custom_row, 7).setCurrentText("true")

    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    dialog.unitIdSpinBox.setValue(7)
    dialog.orderCombo.setCurrentText("BADC")
    _click(qtbot, dialog.connectButton)
    qtbot.waitUntil(
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:7",
        timeout=5000,
    )
    assert runtime.register_map.by_name("custom_value").byte_order.value == "little"
    assert repository.get_device("modbus:COM9:7") is None

    transports[0].registers[106] = encode_registers(
        ModbusRegister(
            name="custom_value",
            kind=RegisterKind.HOLDING,
            address=106,
            word_count=1,
            data_type=ModbusDataType.UINT16,
            writable=True,
            byte_order=runtime.register_map.by_name("custom_value").byte_order,
        ),
        513,
    )
    read_button = window.variableMapTable.cellWidget(custom_row, 11).layout().itemAt(0).widget()
    _click(qtbot, read_button)
    qtbot.waitUntil(
        lambda: _table_text(window.variableMapTable, custom_row, 9) == "513",
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
    assert window.variableMapTable.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:7",
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
        _click(qtbot, window.addVariableButton)
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


def test_modbus_module_window_rejects_invalid_ui_variable_map(qtbot, tmp_path) -> None:
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
    dialog = _open_connection_dialog(qtbot, window)
    _wait_for_scanned_ports(qtbot, dialog, 1)
    _set_table_text(window.variableMapTable, 0, 2, "-1")

    _click(qtbot, dialog.connectButton)

    assert "Address must be non-negative for mass_rate." in window.logTextEdit.toPlainText()


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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:7",
        timeout=5000,
    )
    transports[0].read_errors[12] = "timeout"
    window.sampleVariablesAction.trigger()
    qtbot.waitUntil(
        lambda: "Sample warning: delta_t: timeout" in window.logTextEdit.toPlainText(),
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
        lambda: window.statusValueLabel.text() == "Connected modbus:COM9:7",
        timeout=5000,
    )
    transports[0].read_delay_s = 0.05
    transports[0].read_errors[104] = "timeout"
    window.sampleVariablesAction.trigger()
    assert not window.sampleVariablesAction.isEnabled()
    qtbot.waitUntil(
        lambda: "Sample warning: low_threshold: timeout"
        in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert window.sampleVariablesAction.isEnabled()
    assert not hasattr(window, "variableTable")


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

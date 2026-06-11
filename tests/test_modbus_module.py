from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableWidgetSelectionRange

from coreflow.analysis.calibration import RepeatabilityTrial
from coreflow.app.modbus_runtime import ModbusConnectionSettings, ModbusModuleRuntime
from coreflow.hardware import SerialPortInfo, SerialPortScanner
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    RegisterKind,
    encode_registers,
)
from coreflow.storage import Database, StorageRepository
from coreflow.storage.models import DeviceRecord
from coreflow.ui.modbus_window import ModbusModuleWindow
from coreflow.workflows import RunSession, RunStatus, RunType
from tests.modbus_fakes import placeholder_transport_factory


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


def _column_texts(table, column: int) -> list[str]:
    return [_table_text(table, row, column) for row in range(table.rowCount())]


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
        lambda: "K factor completed" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )
    window.repeatabilityAction.trigger()
    qtbot.waitUntil(
        lambda: "Repeatability completed" in window.logTextEdit.toPlainText(),
        timeout=5000,
    )

    assert window.statusValueLabel.text() == "Connected modbus:COM9:7"
    assert window.frameTable.rowCount() >= 14
    assert _table_text(window.frameTable, 0, 1) in {"TX", "RX"}
    log = window.logTextEdit.toPlainText()
    assert "Zero calibration completed" in log
    assert "K factor completed" in log
    assert "Repeatability completed" in log
    assert repository.count_rows("run_sessions") == 3
    window.calibrationHistoryAction.trigger()
    qtbot.waitUntil(
        lambda: window.calibrationHistoryDialog is not None
        and window.calibrationHistoryDialog.isVisible(),
        timeout=5000,
    )
    assert window.calibrationHistoryDialog is not None
    assert window.calibrationHistoryDialog.historyTable.rowCount() == 3
    assert window.calibrationHistoryDialog.historyTable.columnCount() == 5
    assert (
        _table_text(window.calibrationHistoryDialog.historyTable, 0, 3)
        or _table_text(window.calibrationHistoryDialog.historyTable, 1, 3)
        or _table_text(window.calibrationHistoryDialog.historyTable, 2, 3)
    )
    assert "Run ID:" in window.calibrationHistoryDialog.detailTextEdit.toPlainText()
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

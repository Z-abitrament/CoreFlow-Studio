from __future__ import annotations

from PySide6.QtCore import Qt

from coreflow.app import CoreFlowRuntime
from coreflow.simulation import replay_template_csv
from coreflow.ui import MainWindow


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _table_text(table, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text()


def test_main_window_runs_simulator_workflows(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    _click(qtbot, window.addSimulatorButton)
    assert window.deviceTable.rowCount() == 1
    assert _table_text(window.deviceTable, 0, 0) == "SIM-UI-001"
    assert _table_text(window.deviceTable, 0, 3) == "disconnected"

    _click(qtbot, window.connectButton)
    assert _table_text(window.deviceTable, 0, 3) == "connected"

    _click(qtbot, window.readLiveButton)
    assert window.massFlowLabel.text() == "10.000"
    assert window.live_values == (10.0,)
    assert _table_text(window.deviceTable, 0, 4) == "10.000"

    _click(qtbot, window.runCalibrationButton)
    qtbot.waitUntil(lambda: window.runHistoryTable.rowCount() == 1, timeout=5000)
    assert _table_text(window.runHistoryTable, 0, 1) == "calibration_preview"
    assert _details_contain(window, "calibration_preview")

    _click(qtbot, window.runFactoryTestButton)
    qtbot.waitUntil(lambda: window.runHistoryTable.rowCount() == 2, timeout=5000)
    history_workflows = {
        _table_text(window.runHistoryTable, row, 1)
        for row in range(window.runHistoryTable.rowCount())
    }
    assert history_workflows == {"calibration_preview", "automated_factory_test"}
    assert _details_contain(window, "factory_test_summary")

    _click(qtbot, window.runExperimentButton)
    qtbot.waitUntil(lambda: window.runHistoryTable.rowCount() == 3, timeout=5000)
    history_workflows = {
        _table_text(window.runHistoryTable, row, 1)
        for row in range(window.runHistoryTable.rowCount())
    }
    assert history_workflows == {
        "calibration_preview",
        "automated_factory_test",
        "flexible_experiment",
    }
    assert _details_contain(window, "experiment_signal_processing")
    assert _details_contain(window, "PROCESSED")

    _click(qtbot, window.generateExportButton)
    qtbot.waitUntil(lambda: _details_contain(window, "EXPORT-MANIFEST"), timeout=5000)
    assert _details_contain(window, "REPORT-TXT")
    assert _details_contain(window, "EXPORT-METRICS")
    assert _details_contain(window, "EXPORT-MEASUREMENTS")

    _click(qtbot, window.disconnectButton)
    assert _table_text(window.deviceTable, 0, 3) == "disconnected"


def test_serial_modbus_setup_is_visible_but_does_not_touch_hardware(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)

    window.connectionModeCombo.setCurrentText("Serial Modbus RTU")
    window.serialPortLineEdit.setText("COM9")
    window.unitIdSpinBox.setValue(12)
    _click(qtbot, window.addSimulatorButton)

    assert window.deviceTable.rowCount() == 0
    assert _log_contains(window, "disabled until hardware acceptance")


def test_main_window_adds_replay_csv_channel(qtbot, tmp_path) -> None:
    replay_path = tmp_path / "replay.csv"
    replay_path.write_bytes(replay_template_csv(sample_count=8))
    runtime = CoreFlowRuntime(data_root=tmp_path / "data")
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.connectionModeCombo.setCurrentText("Replay CSV")
    assert window.addSimulatorButton.text() == "Add Replay"
    assert window.portFieldLabel.text() == "Replay CSV"
    assert not window.unitIdSpinBox.isEnabled()
    window.serialPortLineEdit.setText(str(replay_path))
    _click(qtbot, window.addSimulatorButton)

    assert window.deviceTable.rowCount() == 1
    assert _table_text(window.deviceTable, 0, 0) == "REPLAY-TEMPLATE"
    assert _table_text(window.deviceTable, 0, 1) == "Replay"
    assert _table_text(window.deviceTable, 0, 3) == "disconnected"

    _click(qtbot, window.connectButton)
    _click(qtbot, window.readLiveButton)

    assert window.massFlowLabel.text() == "10.000"
    assert window.live_values == (10.0,)

    _click(qtbot, window.runExperimentButton)
    qtbot.waitUntil(lambda: window.runHistoryTable.rowCount() == 1, timeout=5000)
    assert _table_text(window.runHistoryTable, 0, 1) == "flexible_experiment"
    assert _details_contain(window, "experiment_signal_processing")


def test_main_window_reports_missing_replay_path(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)

    window.connectionModeCombo.setCurrentText("Replay CSV")
    _click(qtbot, window.addSimulatorButton)

    assert window.deviceTable.rowCount() == 0
    assert _log_contains(window, "Enter a replay CSV path first.")


def _details_contain(window: MainWindow, text: str) -> bool:
    for row in range(window.resultDetails.rowCount()):
        for column in range(window.resultDetails.columnCount()):
            if text in _table_text(window.resultDetails, row, column):
                return True
    return False


def _log_contains(window: MainWindow, text: str) -> bool:
    for row in range(window.statusLog.rowCount()):
        for column in range(window.statusLog.columnCount()):
            if text in _table_text(window.statusLog, row, column):
                return True
    return False

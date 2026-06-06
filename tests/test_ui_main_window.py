from __future__ import annotations

from PySide6.QtCore import Qt

from coreflow.app import CoreFlowRuntime
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

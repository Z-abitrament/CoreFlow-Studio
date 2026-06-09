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


def test_main_window_opens_independent_asio_iis_window(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    _click(qtbot, window.asioModuleButton)

    assert window.asioWindow is not None
    asio_window = window.asioWindow
    assert asio_window.isVisible()
    assert asio_window.statusValueLabel.text() == "Disconnected"
    assert asio_window.deviceCombo.count() >= 1
    assert asio_window.device_name()
    assert asio_window.sampleRateSpinBox.value() == 44100
    assert asio_window.sampleFormatCombo.currentText() == "int24"
    assert asio_window.inputChannelsCombo.currentText() == "2"
    assert asio_window.outputChannelsCombo.currentText() == "2"
    assert asio_window.probeButton.text() == "Probe"
    assert not hasattr(asio_window, "frameCountSpinBox")
    assert not hasattr(asio_window, "maxLatencySpinBox")
    assert window.deviceTable.rowCount() == 0

    window.asioWindow.close()
    window.asioWindow = None
    window.asioModuleAction.trigger()
    assert window.asioWindow is not None
    assert window.asioWindow.isVisible()


def test_asio_iis_window_fake_connection_does_not_change_device_channels(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    _click(qtbot, window.addSimulatorButton)
    _click(qtbot, window.connectButton)
    assert _table_text(window.deviceTable, 0, 3) == "connected"

    _click(qtbot, window.asioModuleButton)
    assert window.asioWindow is not None
    asio_window = window.asioWindow
    asio_window.backendCombo.setCurrentText("fake")
    asio_window.deviceCombo.setCurrentText("BRAVO-HD Device Control")
    asio_window.sampleRateSpinBox.setValue(48000)
    asio_window.bitDepthCombo.setCurrentText("32")
    asio_window.sampleFormatCombo.setCurrentText("float32")
    asio_window.frameSamplesSpinBox.setValue(64)

    _click(qtbot, asio_window.connectButton)
    qtbot.waitUntil(lambda: asio_window.statusValueLabel.text() == "Connected", timeout=5000)
    assert _plain_log_contains(asio_window, "Backend ready")
    assert _table_text(window.deviceTable, 0, 3) == "connected"

    _click(qtbot, asio_window.openTestButton)
    assert asio_window.testWindow is not None
    test_window = asio_window.testWindow
    assert test_window.signalTypeCombo.currentText() == "Sine"
    test_window.signalTypeCombo.setCurrentText("Square")
    test_window.signalFrequencySpinBox.setValue(250.0)
    test_window.displayModeCombo.setCurrentText("Input + Output")

    _click(qtbot, test_window.loopbackTestButton)
    qtbot.waitUntil(
        lambda: "Loopback passed" in test_window.summaryTextEdit.toPlainText(),
        timeout=5000,
    )
    assert len(test_window.signalPlot.listDataItems()) >= 2
    curve_names = {item.name() for item in test_window.signalPlot.listDataItems()}
    assert any(name.startswith("output:") for name in curve_names)
    assert any(name.startswith("input:") for name in curve_names)
    assert test_window.signalPlot.plotItem.legend is not None

    test_window.signalTypeCombo.setCurrentText("White Noise")
    test_window.displayModeCombo.setCurrentText("Input Only")
    _click(qtbot, test_window.liveTestButton)
    qtbot.waitUntil(
        lambda: "Non-loopback check completed" in test_window.summaryTextEdit.toPlainText(),
        timeout=5000,
    )
    assert test_window.signalPlot.listDataItems()
    assert _table_text(window.deviceTable, 0, 3) == "connected"

    _click(qtbot, asio_window.disconnectButton)
    assert asio_window.statusValueLabel.text() == "Disconnected"
    assert _table_text(window.deviceTable, 0, 3) == "connected"


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


def _plain_log_contains(window, text: str) -> bool:
    return text in window.logTextEdit.toPlainText()

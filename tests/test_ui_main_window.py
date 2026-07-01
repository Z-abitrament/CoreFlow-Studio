from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextEdit, QSplitter

from coreflow.app import CoreFlowRuntime
from coreflow.ui import MainWindow


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _table_text(table, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text()


def test_main_window_defaults_to_modbus_module(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    assert [action.text() for action in window.menuBar().actions()] == [
        "Modules",
        "Help",
    ]
    assert window.centralWidget() is window.moduleStack
    assert window.modbusWindow is not None
    assert window.moduleStack.currentWidget() is window.modbusWindow
    assert not window.modbusWindow.isWindow()
    assert window.modbusModuleAction.isChecked()
    assert window.asioWindow is None
    assert not hasattr(window, "deviceTable")
    assert not hasattr(window, "addSimulatorButton")
    assert not hasattr(window, "runCalibrationButton")


def test_main_window_opens_update_dialog_from_help_menu(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    assert window.checkUpdatesAction.text() == "Check for Updates..."
    window.checkUpdatesAction.trigger()

    assert window.updateDialog is not None
    assert window.updateDialog.isVisible()
    assert window.updateDialog.windowTitle() == "Software Update"
    assert window.updateDialog.manifestUrlEdit.objectName() == "updateManifestUrlEdit"
    assert window.updateDialog.downloadButton.text() == "Download"
    assert window.updateDialog.installButton.text() == "Update and Restart"


def test_main_window_embeds_modbus_module_from_menu(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    assert window.modbusWindow is not None
    modbus_window = window.modbusWindow
    assert window.moduleStack.currentWidget() is modbus_window
    assert not modbus_window.isWindow()
    assert modbus_window.isVisible()
    assert window.modbusModuleAction.isChecked()
    assert not window.asioModuleAction.isChecked()
    assert modbus_window.statusValueLabel.text() == "Disconnected"
    assert not modbus_window.sampleVariablesAction.isEnabled()
    assert not modbus_window.kFactorAction.isEnabled()
    assert not hasattr(modbus_window, "sampleVariablesButton")
    assert modbus_window.menuBar.objectName() == "modbusMenuBar"
    assert modbus_window.kFactorInputsGroup.isHidden()
    assert not hasattr(modbus_window, "variableTable")
    assert not hasattr(modbus_window, "frameTable")
    assert modbus_window.logTextEdit.objectName() == "modbusLogTextEdit"
    assert isinstance(modbus_window.logTextEdit, QTextEdit)
    assert modbus_window.openConnectionButton.text() == "Connection..."
    assert modbus_window.variableMapTable.minimumHeight() <= 160
    body_splitter = modbus_window.findChild(QSplitter, "modbusBodySplitter")
    assert body_splitter is not None
    assert body_splitter.orientation() == Qt.Orientation.Horizontal
    assert body_splitter.widget(0) is modbus_window.variableMapTable.parentWidget()
    assert body_splitter.widget(1) is modbus_window.logTextEdit.parentWidget()
    assert modbus_window.variableMapTable.rowCount() == 8
    assert modbus_window.variableMapTable.columnCount() == 12
    assert not modbus_window.variableMapTable.verticalHeader().isVisible()
    assert modbus_window.variableMapTable.horizontalHeader().sectionsMovable()
    for column in range(1, 8):
        assert modbus_window.variableMapTable.isColumnHidden(column)
    assert modbus_window.variableMapTable.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )
    assert modbus_window.addVariableButton.text() == "Add Variable"
    assert modbus_window.deleteVariableButton.text() == "Delete Variable"
    assert not modbus_window.addVariableButton.isVisible()
    assert not modbus_window.deleteVariableButton.isVisible()
    assert not hasattr(modbus_window, "moveVariableUpButton")
    assert not hasattr(modbus_window, "moveVariableDownButton")
    assert modbus_window.pollingButton.text() == "Start Polling"
    assert modbus_window.createDeviceProfileButton.text() == "New Profile"
    assert modbus_window.editDeviceProfileButton.text() == "Edit Profile"
    assert modbus_window.deleteDeviceProfileButton.text() == "Delete"
    assert modbus_window.allHistoryAction.text() == "All Test Records"
    assert not hasattr(modbus_window, "allTestRecordsButton")

    _click(qtbot, modbus_window.openConnectionButton)
    assert modbus_window.connectionDialog is None
    assert "create or select a device profile first" in modbus_window.logTextEdit.toPlainText()
    _click(qtbot, modbus_window.createDeviceProfileButton)
    qtbot.waitUntil(
        lambda: modbus_window.deviceProfileDialog is not None
        and modbus_window.deviceProfileDialog.isVisible(),
        timeout=5000,
    )
    profile_dialog = modbus_window.deviceProfileDialog
    assert profile_dialog is not None
    profile_dialog.deviceIdLineEdit.setText("CFM-MAIN-001")
    _click(qtbot, profile_dialog.saveButton)
    qtbot.waitUntil(
        lambda: modbus_window.deviceProfileCombo.findData("CFM-MAIN-001") >= 0,
        timeout=5000,
    )
    _click(qtbot, modbus_window.openConnectionButton)
    qtbot.waitUntil(lambda: modbus_window.connectionDialog is not None, timeout=5000)
    connection_dialog = modbus_window.connectionDialog
    assert connection_dialog is not None
    assert connection_dialog.isVisible()
    assert not connection_dialog.portCombo.isEditable()
    assert connection_dialog.refreshPortsButton.text() == "Refresh Ports"
    assert connection_dialog.unitIdSpinBox.value() == 1
    assert connection_dialog.orderCombo.currentText() == "ABCD"
    qtbot.waitUntil(lambda: not modbus_window._busy, timeout=5000)
    assert runtime.list_channels() == ()


def test_main_window_embeds_asio_iis_module_from_menu(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.asioModuleAction.trigger()

    assert window.asioWindow is not None
    asio_window = window.asioWindow
    assert window.moduleStack.currentWidget() is asio_window
    assert not asio_window.isWindow()
    assert asio_window.isVisible()
    assert window.asioModuleAction.isChecked()
    assert not window.modbusModuleAction.isChecked()
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
    assert runtime.list_channels() == ()


def test_main_window_switches_between_embedded_modules_without_popups(
    qtbot,
    tmp_path,
) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    modbus_window = window.modbusWindow
    assert modbus_window is not None

    window.asioModuleAction.trigger()
    asio_window = window.asioWindow
    assert asio_window is not None
    assert window.moduleStack.currentWidget() is asio_window
    assert not asio_window.isWindow()
    assert not modbus_window.isWindow()
    assert window.asioModuleAction.isChecked()
    assert not window.modbusModuleAction.isChecked()

    window.modbusModuleAction.trigger()
    assert window.moduleStack.currentWidget() is modbus_window
    assert window.modbusWindow is modbus_window
    assert window.asioWindow is asio_window
    assert window.modbusModuleAction.isChecked()
    assert not window.asioModuleAction.isChecked()


def test_asio_iis_embedded_fake_connection_keeps_module_state_local(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.asioModuleAction.trigger()
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
    assert runtime.list_channels() == ()

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
    assert runtime.list_channels() == ()

    _click(qtbot, asio_window.disconnectButton)
    assert asio_window.statusValueLabel.text() == "Disconnected"
    assert runtime.list_channels() == ()


def _plain_log_contains(window, text: str) -> bool:
    return text in window.logTextEdit.toPlainText()

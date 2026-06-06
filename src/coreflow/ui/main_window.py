"""Main Qt window for the first CoreFlow Studio desktop experience."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import PurePath

import pyqtgraph as pg
from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coreflow.app import ChannelSnapshot, CoreFlowRuntime, RunInspection
from coreflow.devices import Measurement
from coreflow.ui.workers import WorkflowTask


class MainWindow(QMainWindow):
    """Operational main window for simulator-backed M8 workflows."""

    def __init__(self, runtime: CoreFlowRuntime | None = None) -> None:
        super().__init__()
        self.runtime = runtime or CoreFlowRuntime()
        self._thread_pool = QThreadPool.globalInstance()
        self._live_samples: list[float] = []
        self._live_values: list[float] = []
        self._workflow_running = False
        self._cancel_requested = False
        self._active_tasks: list[WorkflowTask] = []

        self.setWindowTitle("CoreFlow Studio")
        self.resize(1180, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self._connect_signals()
        self._refresh_channels()
        self._refresh_history()

    @property
    def live_values(self) -> tuple[float, ...]:
        return tuple(self._live_values)

    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        toolbar = QHBoxLayout()
        self.addSimulatorButton = QPushButton("Add Simulator")
        self.addSimulatorButton.setObjectName("addSimulatorButton")
        self.connectButton = QPushButton("Connect")
        self.connectButton.setObjectName("connectButton")
        self.disconnectButton = QPushButton("Disconnect")
        self.disconnectButton.setObjectName("disconnectButton")
        self.readLiveButton = QPushButton("Read Live")
        self.readLiveButton.setObjectName("readLiveButton")
        self.cancelWorkflowButton = QPushButton("Cancel")
        self.cancelWorkflowButton.setObjectName("cancelWorkflowButton")
        self.cancelWorkflowButton.setEnabled(False)
        for button in (
            self.addSimulatorButton,
            self.connectButton,
            self.disconnectButton,
            self.readLiveButton,
            self.cancelWorkflowButton,
        ):
            button.setMinimumHeight(30)
            toolbar.addWidget(button)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._connection_panel())
        splitter.addWidget(self._live_panel())
        splitter.addWidget(self._workflow_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 3)
        root.addWidget(splitter, 1)

        self.setCentralWidget(central)

    def _connection_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(10)

        setup = QGroupBox("Connection")
        form = QFormLayout(setup)
        self.connectionModeCombo = QComboBox()
        self.connectionModeCombo.setObjectName("connectionModeCombo")
        self.connectionModeCombo.addItems(["Simulator", "Serial Modbus RTU"])
        self.serialPortLineEdit = QLineEdit("COM1")
        self.serialPortLineEdit.setObjectName("serialPortLineEdit")
        self.unitIdSpinBox = QSpinBox()
        self.unitIdSpinBox.setObjectName("unitIdSpinBox")
        self.unitIdSpinBox.setRange(1, 247)
        self.unitIdSpinBox.setValue(1)
        form.addRow("Mode", self.connectionModeCombo)
        form.addRow("Port", self.serialPortLineEdit)
        form.addRow("Unit ID", self.unitIdSpinBox)
        layout.addWidget(setup)

        self.deviceTable = QTableWidget(0, 5)
        self.deviceTable.setObjectName("deviceTable")
        self.deviceTable.setHorizontalHeaderLabels(
            ["Device", "Source", "Type", "State", "Mass Flow"]
        )
        self.deviceTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.deviceTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.deviceTable.verticalHeader().setVisible(False)
        self.deviceTable.setAlternatingRowColors(True)
        self.deviceTable.setMinimumWidth(340)
        self.deviceTable.setColumnWidth(0, 105)
        self.deviceTable.setColumnWidth(1, 86)
        self.deviceTable.setColumnWidth(2, 80)
        self.deviceTable.setColumnWidth(3, 82)
        self.deviceTable.setColumnWidth(4, 88)
        layout.addWidget(self.deviceTable, 1)
        return panel

    def _live_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(10)

        numbers = QGroupBox("Live Readings")
        grid = QGridLayout(numbers)
        self.massFlowLabel = QLabel("--")
        self.massFlowLabel.setObjectName("massFlowLabel")
        self.densityLabel = QLabel("--")
        self.densityLabel.setObjectName("densityLabel")
        self.temperatureLabel = QLabel("--")
        self.temperatureLabel.setObjectName("temperatureLabel")
        self.volumeFlowLabel = QLabel("--")
        self.volumeFlowLabel.setObjectName("volumeFlowLabel")
        for value_label in (
            self.massFlowLabel,
            self.densityLabel,
            self.temperatureLabel,
            self.volumeFlowLabel,
        ):
            value_label.setFrameShape(QFrame.Shape.Panel)
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label.setMinimumHeight(34)
        grid.addWidget(QLabel("Mass Flow"), 0, 0)
        grid.addWidget(self.massFlowLabel, 0, 1)
        grid.addWidget(QLabel("Density"), 1, 0)
        grid.addWidget(self.densityLabel, 1, 1)
        grid.addWidget(QLabel("Temperature"), 2, 0)
        grid.addWidget(self.temperatureLabel, 2, 1)
        grid.addWidget(QLabel("Volume Flow"), 3, 0)
        grid.addWidget(self.volumeFlowLabel, 3, 1)
        layout.addWidget(numbers)

        self.livePlot = pg.PlotWidget()
        self.livePlot.setObjectName("livePlot")
        self.livePlot.setMinimumHeight(260)
        self.livePlot.setBackground("w")
        self.livePlot.setLabel("left", "Mass Flow")
        self.livePlot.setLabel("bottom", "Sample")
        self.livePlot.showGrid(x=True, y=True, alpha=0.25)
        self._plot_curve = self.livePlot.plot([], [], pen=pg.mkPen("#2563eb", width=2))
        layout.addWidget(self.livePlot, 1)
        return panel

    def _workflow_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(10)

        actions = QGroupBox("Workflows")
        action_layout = QHBoxLayout(actions)
        self.runCalibrationButton = QPushButton("Calibration Preview")
        self.runCalibrationButton.setObjectName("runCalibrationButton")
        self.runFactoryTestButton = QPushButton("Factory Test")
        self.runFactoryTestButton.setObjectName("runFactoryTestButton")
        action_layout.addWidget(self.runCalibrationButton)
        action_layout.addWidget(self.runFactoryTestButton)
        layout.addWidget(actions)

        self.statusLog = QTableWidget(0, 3)
        self.statusLog.setObjectName("statusLog")
        self.statusLog.setHorizontalHeaderLabels(["Time", "Event", "Detail"])
        self.statusLog.verticalHeader().setVisible(False)
        self.statusLog.setAlternatingRowColors(True)
        self.statusLog.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.statusLog.setColumnWidth(0, 72)
        self.statusLog.setColumnWidth(1, 132)
        self.statusLog.setColumnWidth(2, 260)
        self.statusLog.setMinimumHeight(150)
        layout.addWidget(self.statusLog)

        self.runHistoryTable = QTableWidget(0, 5)
        self.runHistoryTable.setObjectName("runHistoryTable")
        self.runHistoryTable.setHorizontalHeaderLabels(
            ["Run", "Workflow", "Device", "Status", "Started"]
        )
        self.runHistoryTable.verticalHeader().setVisible(False)
        self.runHistoryTable.setAlternatingRowColors(True)
        self.runHistoryTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.runHistoryTable.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.runHistoryTable.setMinimumHeight(150)
        self.runHistoryTable.setColumnWidth(0, 138)
        self.runHistoryTable.setColumnWidth(1, 142)
        self.runHistoryTable.setColumnWidth(2, 96)
        self.runHistoryTable.setColumnWidth(3, 70)
        self.runHistoryTable.setColumnWidth(4, 136)
        layout.addWidget(self.runHistoryTable)

        self.resultDetails = QTableWidget(0, 3)
        self.resultDetails.setObjectName("resultDetails")
        self.resultDetails.setHorizontalHeaderLabels(["Section", "Key", "Value"])
        self.resultDetails.verticalHeader().setVisible(False)
        self.resultDetails.setAlternatingRowColors(True)
        self.resultDetails.setColumnWidth(0, 95)
        self.resultDetails.setColumnWidth(1, 160)
        self.resultDetails.setColumnWidth(2, 260)
        layout.addWidget(self.resultDetails, 1)
        return panel

    def _connect_signals(self) -> None:
        self.addSimulatorButton.clicked.connect(self._add_channel)
        self.connectButton.clicked.connect(self._connect_selected)
        self.disconnectButton.clicked.connect(self._disconnect_selected)
        self.readLiveButton.clicked.connect(self._read_live)
        self.runCalibrationButton.clicked.connect(self._run_calibration)
        self.runFactoryTestButton.clicked.connect(self._run_factory_test)
        self.cancelWorkflowButton.clicked.connect(self._request_cancel)
        self.runHistoryTable.itemSelectionChanged.connect(self._inspect_selected_run)

    def _add_channel(self) -> None:
        if self.connectionModeCombo.currentText() != "Simulator":
            self._log("Connection", "Serial Modbus setup is configured but disabled until hardware acceptance.")
            return
        snapshot = self.runtime.add_simulated_device()
        self._log("Connection", f"Added {snapshot.device_id}")
        self._refresh_channels(snapshot.device_id)

    def _connect_selected(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            self._log("Connection", "Select a channel first.")
            return
        snapshot = self.runtime.connect_device(device_id)
        self._log("Connection", f"{snapshot.device_id} {snapshot.connection_state}")
        self._refresh_channels(device_id)

    def _disconnect_selected(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            self._log("Connection", "Select a channel first.")
            return
        snapshot = self.runtime.disconnect_device(device_id)
        self._log("Connection", f"{snapshot.device_id} {snapshot.connection_state}")
        self._refresh_channels(device_id)

    def _read_live(self) -> None:
        device_id = self._selected_device_id()
        if device_id is None:
            self._log("Live", "Select a connected channel first.")
            return
        try:
            measurement = self.runtime.read_live_measurement(device_id)
        except Exception as exc:
            self._log("Live", str(exc))
            self._refresh_channels(device_id)
            return
        self._update_live_readings(measurement)
        self._log("Live", f"{device_id} mass_flow={_format_float(measurement.mass_flow)}")
        self._refresh_channels(device_id)

    def _run_calibration(self) -> None:
        device_id = self._selected_device_id()
        if device_id is not None:
            self._start_workflow(
                "Calibration",
                lambda: self.runtime.run_calibration_preview(device_id),
            )
        else:
            self._log("Calibration", "Select a connected channel first.")

    def _run_factory_test(self) -> None:
        device_id = self._selected_device_id()
        if device_id is not None:
            self._start_workflow(
                "Factory Test",
                lambda: self.runtime.run_factory_test(device_id),
            )
        else:
            self._log("Factory Test", "Select a connected channel first.")

    def _start_workflow(self, label: str, action: Callable[[], str]) -> None:
        if self._workflow_running:
            self._log(label, "A workflow is already running.")
            return
        self._workflow_running = True
        self._cancel_requested = False
        self.cancelWorkflowButton.setEnabled(True)
        self.runCalibrationButton.setEnabled(False)
        self.runFactoryTestButton.setEnabled(False)
        self._log(label, "Started")

        task = WorkflowTask(action)
        task.signals.finished.connect(lambda run_id: self._workflow_finished(label, run_id))
        task.signals.failed.connect(lambda message: self._workflow_failed(label, message))
        self._active_tasks.append(task)
        self._thread_pool.start(task)

    def _workflow_finished(self, label: str, run_id: object) -> None:
        if self._cancel_requested:
            self._log(label, f"Cancel requested after {run_id}; stored run remains inspectable.")
        else:
            self._log(label, f"Completed {run_id}")
        self._workflow_running = False
        self._cancel_requested = False
        self.cancelWorkflowButton.setEnabled(False)
        self.runCalibrationButton.setEnabled(True)
        self.runFactoryTestButton.setEnabled(True)
        self._active_tasks.clear()
        self._refresh_channels()
        self._refresh_history(str(run_id))
        self._inspect_run(str(run_id))

    def _workflow_failed(self, label: str, message: str) -> None:
        self._log(label, message)
        self._workflow_running = False
        self._cancel_requested = False
        self.cancelWorkflowButton.setEnabled(False)
        self.runCalibrationButton.setEnabled(True)
        self.runFactoryTestButton.setEnabled(True)
        self._active_tasks.clear()
        self._refresh_channels()
        self._refresh_history()

    def _request_cancel(self) -> None:
        self._cancel_requested = True
        self._log("Workflow", "Cancel requested")

    def _refresh_channels(self, select_device_id: str | None = None) -> None:
        snapshots = self.runtime.list_channels()
        self.deviceTable.setRowCount(len(snapshots))
        for row, snapshot in enumerate(snapshots):
            self._set_channel_row(row, snapshot)
            if snapshot.device_id == select_device_id:
                self.deviceTable.selectRow(row)
        if snapshots and self.deviceTable.currentRow() < 0:
            self.deviceTable.selectRow(0)

    def _set_channel_row(self, row: int, snapshot: ChannelSnapshot) -> None:
        values = [
            snapshot.device_id,
            snapshot.source,
            snapshot.device_type,
            snapshot.connection_state,
            _format_float(snapshot.last_mass_flow),
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.deviceTable.setItem(row, column, item)

    def _refresh_history(self, select_run_id: str | None = None) -> None:
        summaries = self.runtime.list_run_history()
        self.runHistoryTable.setRowCount(len(summaries))
        for row, summary in enumerate(summaries):
            values = [
                summary.run_id,
                summary.workflow_name,
                summary.device_id,
                summary.status,
                summary.started_at.isoformat(timespec="seconds")
                if summary.started_at
                else "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.runHistoryTable.setItem(row, column, item)
            if summary.run_id == select_run_id:
                self.runHistoryTable.selectRow(row)

    def _inspect_selected_run(self) -> None:
        run_id = self._selected_run_id()
        if run_id:
            self._inspect_run(run_id)

    def _inspect_run(self, run_id: str) -> None:
        inspection = self.runtime.inspect_run(run_id)
        rows: list[tuple[str, str, str]] = [
            ("Run", "run_id", inspection.summary.run_id),
            ("Run", "workflow", inspection.summary.workflow_name),
            ("Run", "status", inspection.summary.status),
            ("Run", "device", inspection.summary.device_id),
        ]
        for name, status in inspection.steps:
            rows.append(("Step", name, status))
        for result in inspection.analysis_results:
            rows.append(("Result", result.result_type, result.pass_fail_decision or ""))
            for key, value in result.summary_metrics.items():
                rows.append(("Metric", key, _format_value(value)))
        for artifact in inspection.artifacts:
            rows.append(("Artifact", artifact.artifact_id, _format_path(artifact.file_path)))
        self._populate_details(rows)

    def _populate_details(self, rows: list[tuple[str, str, str]]) -> None:
        self.resultDetails.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.resultDetails.setItem(row, column, item)

    def _update_live_readings(self, measurement: Measurement) -> None:
        self.massFlowLabel.setText(_format_float(measurement.mass_flow))
        self.densityLabel.setText(_format_float(measurement.density))
        self.temperatureLabel.setText(_format_float(measurement.temperature))
        self.volumeFlowLabel.setText(_format_float(measurement.volume_flow))
        if measurement.mass_flow is None:
            return
        self._live_samples.append(float(len(self._live_samples)))
        self._live_values.append(float(measurement.mass_flow))
        self._plot_curve.setData(self._live_samples, self._live_values)

    def _selected_device_id(self) -> str | None:
        row = self.deviceTable.currentRow()
        if row < 0:
            return None
        item = self.deviceTable.item(row, 0)
        return item.text() if item is not None else None

    def _selected_run_id(self) -> str | None:
        row = self.runHistoryTable.currentRow()
        if row < 0:
            return None
        item = self.runHistoryTable.item(row, 0)
        return item.text() if item is not None else None

    def _log(self, event: str, detail: str) -> None:
        row = self.statusLog.rowCount()
        self.statusLog.insertRow(row)
        values = [
            datetime.now().strftime("%H:%M:%S"),
            event,
            detail,
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.statusLog.setItem(row, column, item)
        self.statusLog.scrollToBottom()


def _format_float(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value:.3f}"


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _format_path(path: PurePath) -> str:
    return str(path).replace("\\", "/")

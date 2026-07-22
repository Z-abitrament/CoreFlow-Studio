"""Qt controls for the read-only Modbus zero-monitor operation."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coreflow.analysis.zero_monitor import (
    ZERO_MONITOR_CRITERIA,
    ZeroMonitorAnalysisConfig,
    ZeroMonitorThreshold,
)
from coreflow.app.modbus_zero_monitor import (
    ZERO_MONITOR_POLL_INTERVAL_MS,
    ZeroMonitorLiveUpdate,
    ZeroMonitorRunResult,
)


_CRITERION_LABELS = {
    "short_std": "Short Std",
    "short_range": "Short Range",
    "raw_p2p": "Raw P2P",
    "repeat_std": "Repeat Std",
    "long_range": "Long Range",
    "trend_span": "Trend Span",
    "max_step": "Max Step",
}


class ZeroMonitorDialog(QDialog):
    """Non-modal operator surface; all protocol work remains in the service."""

    startRequested = Signal()
    stopRequested = Signal()
    cancelRequested = Signal()
    saveRequested = Signal()
    zeroCalRequested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zero Monitor")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setMinimumSize(860, 620)
        self.resize(1040, 760)
        self._running = False
        self._points: deque[dict[str, object]] = deque(maxlen=3000)
        self._build_ui()
        self.apply_configuration(ZeroMonitorAnalysisConfig.production_default().to_dict())
        self.set_ready(connected=False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        command_row = QHBoxLayout()
        self.zeroFlowCheckBox = QCheckBox("Zero Flow Confirmed")
        self.zeroFlowCheckBox.setObjectName("modbusZeroMonitorZeroFlowCheckBox")
        self.startButton = QPushButton("Start")
        self.startButton.setObjectName("modbusZeroMonitorStartButton")
        self.stopButton = QPushButton("Stop")
        self.stopButton.setObjectName("modbusZeroMonitorStopButton")
        self.saveButton = QPushButton("Save Settings")
        self.saveButton.setObjectName("modbusZeroMonitorSaveButton")
        self.zeroCalButton = QPushButton("Zero Cal...")
        self.zeroCalButton.setObjectName("modbusZeroMonitorZeroCalButton")
        self.targetPeriodLabel = QLabel(f"Target {ZERO_MONITOR_POLL_INTERVAL_MS} ms")
        self.targetPeriodLabel.setObjectName("modbusZeroMonitorTargetPeriodLabel")
        command_row.addWidget(self.zeroFlowCheckBox)
        command_row.addWidget(self.startButton)
        command_row.addWidget(self.stopButton)
        command_row.addWidget(self.saveButton)
        command_row.addWidget(self.zeroCalButton)
        command_row.addStretch(1)
        command_row.addWidget(self.targetPeriodLabel)
        root.addLayout(command_row)

        self.statusLabel = QLabel("NOT_READY")
        self.statusLabel.setObjectName("modbusZeroMonitorStatusLabel")
        self.statusLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(self.statusLabel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("modbusZeroMonitorSplitter")
        splitter.addWidget(self._configuration_panel())
        splitter.addWidget(self._monitor_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        self.startButton.clicked.connect(self.startRequested)
        self.stopButton.clicked.connect(self.stopRequested)
        self.saveButton.clicked.connect(self.saveRequested)
        self.zeroCalButton.clicked.connect(self.zeroCalRequested)
        self.windowCombo.currentIndexChanged.connect(self._window_mode_changed)
        self.displayRangeCombo.currentIndexChanged.connect(self._resize_ring)

    def _configuration_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        window_group = QGroupBox("Decision Window")
        window_form = QFormLayout(window_group)
        self.windowCombo = QComboBox()
        self.windowCombo.setObjectName("modbusZeroMonitorWindowCombo")
        for seconds in (30.0, 60.0, 300.0):
            self.windowCombo.addItem(f"{seconds:g} s", seconds)
        self.windowCombo.addItem("Custom", "custom")
        self.customWindowSpinBox = QDoubleSpinBox()
        self.customWindowSpinBox.setObjectName("modbusZeroMonitorCustomWindowSpinBox")
        self.customWindowSpinBox.setRange(12.0, 86400.0)
        self.customWindowSpinBox.setDecimals(3)
        self.customWindowSpinBox.setValue(30.0)
        self.minimumStableLineEdit = QLineEdit()
        self.minimumStableLineEdit.setObjectName("modbusZeroMonitorMinimumStableLineEdit")
        self.minimumStableLineEdit.setPlaceholderText("Not configured")
        self.offsetLimitLineEdit = QLineEdit()
        self.offsetLimitLineEdit.setObjectName("modbusZeroMonitorOffsetLimitLineEdit")
        self.offsetLimitLineEdit.setPlaceholderText("Not configured")
        self.offsetSourceLineEdit = QLineEdit()
        self.offsetSourceLineEdit.setObjectName("modbusZeroMonitorOffsetSourceLineEdit")
        window_form.addRow("Long Window", self.windowCombo)
        window_form.addRow("Custom (s)", self.customWindowSpinBox)
        window_form.addRow("Stable Duration (s)", self.minimumStableLineEdit)
        window_form.addRow("Offset Limit (us)", self.offsetLimitLineEdit)
        window_form.addRow("Offset Source", self.offsetSourceLineEdit)
        layout.addWidget(window_group)

        threshold_group = QGroupBox("Stability Criteria")
        threshold_layout = QVBoxLayout(threshold_group)
        self.thresholdTable = QTableWidget(len(ZERO_MONITOR_CRITERIA), 4)
        self.thresholdTable.setObjectName("modbusZeroMonitorThresholdTable")
        self.thresholdTable.setHorizontalHeaderLabels(["Enabled", "Criterion", "Limit (us)", "Source"])
        self.thresholdTable.verticalHeader().setVisible(False)
        self.thresholdTable.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.thresholdTable.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.thresholdTable.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.thresholdTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for row, name in enumerate(ZERO_MONITOR_CRITERIA):
            enabled = QTableWidgetItem("")
            enabled.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            enabled.setCheckState(Qt.CheckState.Checked)
            enabled.setData(Qt.ItemDataRole.UserRole, name)
            label = QTableWidgetItem(_CRITERION_LABELS[name])
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.thresholdTable.setItem(row, 0, enabled)
            self.thresholdTable.setItem(row, 1, label)
            self.thresholdTable.setItem(row, 2, QTableWidgetItem(""))
            self.thresholdTable.setItem(row, 3, QTableWidgetItem(""))
        threshold_layout.addWidget(self.thresholdTable)
        layout.addWidget(threshold_group, 1)
        return panel

    def _monitor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        plot_controls = QHBoxLayout()
        plot_controls.addWidget(QLabel("Display"))
        self.displayRangeCombo = QComboBox()
        self.displayRangeCombo.setObjectName("modbusZeroMonitorDisplayRangeCombo")
        for seconds in (10, 30, 60, 300):
            self.displayRangeCombo.addItem(f"{seconds} s", seconds)
        plot_controls.addWidget(self.displayRangeCombo)
        plot_controls.addStretch(1)
        layout.addLayout(plot_controls)

        self.plotWidget = pg.PlotWidget()
        self.plotWidget.setObjectName("modbusZeroMonitorPlot")
        self.plotWidget.setBackground("w")
        self.plotWidget.showGrid(x=True, y=True, alpha=0.25)
        self.plotWidget.setLabel("bottom", "Device Time", units="s")
        self.plotWidget.setLabel("left", "Zero", units="us")
        self.plotWidget.addLegend(offset=(8, 8))
        layout.addWidget(self.plotWidget, 2)

        self.metricTable = QTableWidget(0, 2)
        self.metricTable.setObjectName("modbusZeroMonitorMetricTable")
        self.metricTable.setHorizontalHeaderLabels(["Metric", "Value"])
        self.metricTable.verticalHeader().setVisible(False)
        self.metricTable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.metricTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.metricTable, 1)

        self.reasonLabel = QLabel("")
        self.reasonLabel.setObjectName("modbusZeroMonitorReasonLabel")
        self.reasonLabel.setWordWrap(True)
        self.reasonLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.reasonLabel)
        return panel

    def capture_configuration(self) -> ZeroMonitorAnalysisConfig:
        thresholds: dict[str, ZeroMonitorThreshold] = {}
        for row in range(self.thresholdTable.rowCount()):
            enabled_item = self.thresholdTable.item(row, 0)
            name = str(enabled_item.data(Qt.ItemDataRole.UserRole))
            limit_text = (self.thresholdTable.item(row, 2).text() or "").strip()
            source = (self.thresholdTable.item(row, 3).text() or "").strip()
            thresholds[name] = ZeroMonitorThreshold(
                enabled=enabled_item.checkState() == Qt.CheckState.Checked,
                limit=None if not limit_text else float(limit_text),
                source=source,
                unit="us",
                test_only=False,
            )
        minimum_text = self.minimumStableLineEdit.text().strip()
        offset_text = self.offsetLimitLineEdit.text().strip()
        return ZeroMonitorAnalysisConfig(
            long_window_s=self.long_window_s(),
            minimum_stable_duration_s=(None if not minimum_text else float(minimum_text)),
            thresholds=thresholds,
            offset_limit=None if not offset_text else float(offset_text),
            offset_limit_source=self.offsetSourceLineEdit.text().strip(),
            status="configured" if minimum_text else "pending_bench_approval",
        )

    def capture_settings(self) -> dict[str, object]:
        return self.capture_configuration().to_dict()

    def apply_configuration(self, value: dict[str, object]) -> None:
        config = ZeroMonitorAnalysisConfig.from_dict(value)
        index = self.windowCombo.findData(config.long_window_s)
        if index < 0:
            index = self.windowCombo.findData("custom")
            self.customWindowSpinBox.setValue(config.long_window_s)
        self.windowCombo.setCurrentIndex(index)
        self.minimumStableLineEdit.setText(
            "" if config.minimum_stable_duration_s is None else f"{config.minimum_stable_duration_s:g}"
        )
        self.offsetLimitLineEdit.setText(
            "" if config.offset_limit is None else f"{config.offset_limit:g}"
        )
        self.offsetSourceLineEdit.setText(config.offset_limit_source)
        for row, name in enumerate(ZERO_MONITOR_CRITERIA):
            threshold = config.thresholds[name]
            self.thresholdTable.item(row, 0).setCheckState(
                Qt.CheckState.Checked if threshold.enabled else Qt.CheckState.Unchecked
            )
            self.thresholdTable.item(row, 2).setText(
                "" if threshold.limit is None else f"{threshold.limit:g}"
            )
            self.thresholdTable.item(row, 3).setText(threshold.source)
        self._window_mode_changed()

    def long_window_s(self) -> float:
        data = self.windowCombo.currentData()
        return self.customWindowSpinBox.value() if data == "custom" else float(data)

    def set_ready(self, *, connected: bool) -> None:
        self._running = False
        self.startButton.setEnabled(connected)
        self.stopButton.setEnabled(False)
        self.zeroFlowCheckBox.setEnabled(True)
        self.saveButton.setEnabled(True)
        self.zeroCalButton.setEnabled(connected)

    def set_running(self) -> None:
        self._running = True
        self._points.clear()
        self.plotWidget.clear()
        self.statusLabel.setText("Starting")
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.zeroFlowCheckBox.setEnabled(False)
        self.saveButton.setEnabled(False)
        self.zeroCalButton.setEnabled(False)

    def set_stopping(self) -> None:
        self.stopButton.setEnabled(False)
        self.statusLabel.setText("Stopping")

    def set_error(self, message: str) -> None:
        self.statusLabel.setText(f"ERROR | {message}")

    def add_update(self, update: ZeroMonitorLiveUpdate) -> None:
        row = dict(update.row)
        if row.get("device_tick_ms_unwrapped") not in (None, ""):
            self._points.append(row)
        self.statusLabel.setText(
            f"{update.analysis.state.value} | polls {update.counters.get('logical_poll_count', 0)} | "
            f"requests {update.counters.get('physical_request_count', 0)}"
        )
        self.reasonLabel.setText(
            " | ".join((*update.analysis.reason_codes, *update.analysis.advisory_codes))
        )
        metrics = {**update.analysis.metrics.to_dict(), **update.counters}
        self._set_metrics(metrics)
        self._redraw()

    def set_result(self, result: ZeroMonitorRunResult) -> None:
        self.statusLabel.setText(
            f"{result.state.value} | {result.run_status.value} | {result.run_id or result.attempt_id}"
        )
        details = [*result.reason_codes, *result.advisory_codes]
        if result.error_message:
            details.append(result.error_message)
        self.reasonLabel.setText(" | ".join(details))
        self._set_metrics({**result.metrics, **result.counters})
        self.zeroFlowCheckBox.setChecked(False)

    def _set_metrics(self, metrics: dict[str, object]) -> None:
        visible = (
            "candidate_count",
            "window_span_s",
            "short_std",
            "short_range",
            "raw_p2p",
            "long_mean",
            "repeat_std",
            "long_range",
            "long_p95_p5",
            "long_slope",
            "trend_span",
            "max_step",
            "adjacent_difference_rms",
            "zero_drift_from_cal",
            "transport_failure_count",
            "torn_snapshot_reread_count",
            "poll_overrun_count",
            "missed_schedule_slot_count",
            "observed_period_p95_ms",
            "achieved_poll_rate_hz",
        )
        self.metricTable.setRowCount(len(visible))
        for row, name in enumerate(visible):
            value = metrics.get(name)
            label_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(_format_value(value))
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.metricTable.setItem(row, 0, label_item)
            self.metricTable.setItem(row, 1, value_item)

    def _redraw(self) -> None:
        self.plotWidget.clear()
        if not self._points:
            return
        selected_range = int(self.displayRangeCombo.currentData() or 10)
        latest_tick = float(self._points[-1]["device_tick_ms_unwrapped"])
        minimum_tick = latest_tick - selected_range * 1000.0
        points = [
            point
            for point in self._points
            if float(point["device_tick_ms_unwrapped"]) >= minimum_tick
        ]
        colors = {"live_zero_600ms": "#c62828", "base_mean_100ms": "#1565c0"}
        for variable_name, color in colors.items():
            segments = tuple(dict.fromkeys(str(point.get("continuous_segment", "")) for point in points))
            for segment in segments:
                segment_points = [
                    point
                    for point in points
                    if str(point.get("continuous_segment", "")) == segment
                    and point.get(variable_name) not in (None, "")
                ]
                if not segment_points:
                    continue
                x_values = [float(point["device_tick_ms_unwrapped"]) / 1000.0 for point in segment_points]
                y_values = [float(point[variable_name]) for point in segment_points]
                label = variable_name if len(segments) == 1 else f"{variable_name} [{segment}]"
                self.plotWidget.plot(x_values, y_values, pen=pg.mkPen(color, width=2), name=label)

    def _resize_ring(self) -> None:
        seconds = int(self.displayRangeCombo.currentData() or 10)
        self._points = deque(self._points, maxlen=max(120, seconds * 12))
        self._redraw()

    def _window_mode_changed(self) -> None:
        self.customWindowSpinBox.setEnabled(self.windowCombo.currentData() == "custom")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._running:
            self.cancelRequested.emit()
        super().closeEvent(event)


def _format_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.9g}"
    return str(value)

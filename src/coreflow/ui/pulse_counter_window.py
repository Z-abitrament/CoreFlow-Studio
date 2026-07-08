"""Independent Pulse Counter module window."""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coreflow.app.pulse_runtime import PulseCounterRuntime
from coreflow.pulse_counter import PulseAnalysisResult
from coreflow.storage import PulseTrialRecord, StorageRepository


class PulseCounterWindow(QDialog):
    """CSV-backed Pulse Counter controls independent from Modbus state."""

    def __init__(
        self,
        *,
        repository: StorageRepository,
        operator: str = "operator",
        parent: QWidget | None = None,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        if embedded:
            from PySide6.QtCore import Qt

            self.setWindowFlags(Qt.WindowType.Widget)
        self.runtime = PulseCounterRuntime(repository, operator=operator)
        self._analysis: PulseAnalysisResult | None = None
        self.setWindowTitle("Pulse Counter Module")
        self.resize(980, 680)
        self.setMinimumSize(840, 560)
        self._build_ui()
        self._connect_signals()
        self._refresh_history()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._profile_group())
        root.addWidget(self._csv_group())
        root.addWidget(self._trial_group())
        self.ratePlot = pg.PlotWidget()
        self.ratePlot.setObjectName("pulseRatePlot")
        self.ratePlot.setBackground("w")
        self.ratePlot.setLabel("left", "Rate", units="g/s")
        self.ratePlot.setLabel("bottom", "Time", units="s")
        self.ratePlot.showGrid(x=True, y=True, alpha=0.25)
        root.addWidget(self.ratePlot, 1)
        root.addWidget(self._trial_records_group(), 1)
        root.addWidget(self._history_group(), 1)

    def _profile_group(self) -> QWidget:
        group = QGroupBox("Device Profile")
        layout = QHBoxLayout(group)
        self.deviceIdLineEdit = QLineEdit()
        self.deviceIdLineEdit.setObjectName("pulseDeviceIdLineEdit")
        self.deviceIdLineEdit.setPlaceholderText("Stable Device ID")
        self.channelLineEdit = QLineEdit("0")
        self.channelLineEdit.setObjectName("pulseChannelLineEdit")
        self.channelLineEdit.setMaximumWidth(80)
        self.edgeCombo = QComboBox()
        self.edgeCombo.setObjectName("pulseEdgeCombo")
        self.edgeCombo.addItems(["rising", "falling", "both"])
        self.pulseValueSpinBox = QDoubleSpinBox()
        self.pulseValueSpinBox.setObjectName("pulseValueSpinBox")
        self.pulseValueSpinBox.setRange(0.000001, 1_000_000.0)
        self.pulseValueSpinBox.setDecimals(6)
        self.pulseValueSpinBox.setValue(0.05)
        self.unitLineEdit = QLineEdit("g")
        self.unitLineEdit.setObjectName("pulseUnitLineEdit")
        self.unitLineEdit.setMaximumWidth(80)
        self.switchFrequencySpinBox = QDoubleSpinBox()
        self.switchFrequencySpinBox.setObjectName("pulseSwitchFrequencySpinBox")
        self.switchFrequencySpinBox.setRange(0.001, 1_000_000.0)
        self.switchFrequencySpinBox.setDecimals(3)
        self.switchFrequencySpinBox.setValue(100.0)
        self.saveProfileButton = QPushButton("Save Config")
        self.saveProfileButton.setObjectName("pulseSaveProfileButton")
        self.loadProfileButton = QPushButton("Load Config")
        self.loadProfileButton.setObjectName("pulseLoadProfileButton")
        layout.addWidget(QLabel("Device ID"))
        layout.addWidget(self.deviceIdLineEdit, 1)
        layout.addWidget(QLabel("Ch"))
        layout.addWidget(self.channelLineEdit)
        layout.addWidget(QLabel("Edge"))
        layout.addWidget(self.edgeCombo)
        layout.addWidget(QLabel("Pulse"))
        layout.addWidget(self.pulseValueSpinBox)
        layout.addWidget(self.unitLineEdit)
        layout.addWidget(QLabel("Switch Hz"))
        layout.addWidget(self.switchFrequencySpinBox)
        layout.addWidget(self.saveProfileButton)
        layout.addWidget(self.loadProfileButton)
        return group

    def _csv_group(self) -> QWidget:
        group = QGroupBox("CSV Analysis")
        layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self.csvPathLineEdit = QLineEdit()
        self.csvPathLineEdit.setObjectName("pulseCsvPathLineEdit")
        self.browseCsvButton = QPushButton("Browse")
        self.browseCsvButton.setObjectName("pulseBrowseCsvButton")
        self.analyzeCsvButton = QPushButton("Analyze CSV")
        self.analyzeCsvButton.setObjectName("pulseAnalyzeCsvButton")
        row.addWidget(self.csvPathLineEdit, 1)
        row.addWidget(self.browseCsvButton)
        row.addWidget(self.analyzeCsvButton)
        layout.addLayout(row)
        self.summaryLabel = QLabel("No CSV analyzed.")
        self.summaryLabel.setObjectName("pulseSummaryLabel")
        layout.addWidget(self.summaryLabel)
        return group

    def _trial_group(self) -> QWidget:
        group = QGroupBox("Trial Calculation")
        form = QFormLayout(group)
        self.flowPointSpinBox = QDoubleSpinBox()
        self.flowPointSpinBox.setObjectName("pulseFlowPointSpinBox")
        self.flowPointSpinBox.setRange(0.0, 1_000_000.0)
        self.flowPointSpinBox.setDecimals(3)
        self.flowPointSpinBox.setValue(100.0)
        self.trialIndexSpinBox = QSpinBox()
        self.trialIndexSpinBox.setObjectName("pulseTrialIndexSpinBox")
        self.trialIndexSpinBox.setRange(1, 999)
        self.standardMassSpinBox = QDoubleSpinBox()
        self.standardMassSpinBox.setObjectName("pulseStandardMassSpinBox")
        self.standardMassSpinBox.setRange(0.000001, 1_000_000.0)
        self.standardMassSpinBox.setDecimals(6)
        self.standardMassSpinBox.setValue(1.0)
        self.standardMassSpinBox.setEnabled(False)
        self.calculateTrialButton = QPushButton("Calculate Trial")
        self.calculateTrialButton.setObjectName("pulseCalculateTrialButton")
        self.calculateTrialButton.setEnabled(False)
        form.addRow("Flow Point", self.flowPointSpinBox)
        form.addRow("Trial Index", self.trialIndexSpinBox)
        form.addRow("Standard Mass", self.standardMassSpinBox)
        form.addRow("", self.calculateTrialButton)
        return group

    def _trial_records_group(self) -> QWidget:
        group = QGroupBox("Trial Records")
        layout = QVBoxLayout(group)
        self.trialTable = QTableWidget(0, 7)
        self.trialTable.setObjectName("pulseTrialTable")
        self.trialTable.setHorizontalHeaderLabels(
            [
                "Flow Point",
                "Trial",
                "Pulses",
                "Measured",
                "Standard",
                "Error %",
                "Boundary",
            ]
        )
        self.trialTable.verticalHeader().setVisible(False)
        self.trialTable.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.trialTable, 1)

        row = QHBoxLayout()
        self.repeatabilitySummaryLabel = QLabel("No repeatability selection yet.")
        self.repeatabilitySummaryLabel.setObjectName("pulseRepeatabilitySummaryLabel")
        self.calculateRepeatabilityButton = QPushButton("Calculate Repeatability...")
        self.calculateRepeatabilityButton.setObjectName("pulseCalculateRepeatabilityButton")
        self.calculateRepeatabilityButton.setEnabled(False)
        row.addWidget(self.repeatabilitySummaryLabel, 1)
        row.addWidget(self.calculateRepeatabilityButton)
        layout.addLayout(row)
        return group

    def _history_group(self) -> QWidget:
        group = QGroupBox("Pulse Records")
        layout = QVBoxLayout(group)
        self.historyTable = QTableWidget(0, 5)
        self.historyTable.setObjectName("pulseHistoryTable")
        self.historyTable.setHorizontalHeaderLabels(
            ["Started", "Operation", "Status", "Pulses", "Error %"]
        )
        self.historyTable.verticalHeader().setVisible(False)
        self.historyTable.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.historyTable)
        return group

    def _connect_signals(self) -> None:
        self.saveProfileButton.clicked.connect(self._save_profile)
        self.loadProfileButton.clicked.connect(self._load_profile)
        self.browseCsvButton.clicked.connect(self._browse_csv)
        self.analyzeCsvButton.clicked.connect(self._analyze_csv)
        self.calculateTrialButton.clicked.connect(self._calculate_trial)
        self.calculateRepeatabilityButton.clicked.connect(self._calculate_repeatability)

    def _save_profile(self) -> None:
        try:
            self._save_profile_or_raise()
        except Exception as exc:
            self._show_error("Config save failed", exc)
            return
        device_id = self._device_id()
        self.summaryLabel.setText(f"Config saved for {device_id}.")
        self._refresh_history()

    def _save_profile_or_raise(self) -> None:
        device_id = self._device_id()
        self.runtime.save_profile(
            device_id=device_id,
            channel=self.channelLineEdit.text().strip() or "0",
            edge=self.edgeCombo.currentText(),
            pulse_value=self.pulseValueSpinBox.value(),
            unit=self.unitLineEdit.text().strip() or "g",
            switch_frequency_hz=self.switchFrequencySpinBox.value(),
        )

    def _load_profile(self) -> None:
        try:
            device_id = self._device_id()
            profile = self.runtime.load_profile(device_id)
        except Exception as exc:
            self._show_error("Config load failed", exc)
            return
        self.channelLineEdit.setText(profile.config.channel)
        self.edgeCombo.setCurrentText(profile.config.edge)
        self.pulseValueSpinBox.setValue(profile.config.pulse_value)
        self.unitLineEdit.setText(profile.config.unit)
        self.switchFrequencySpinBox.setValue(profile.config.switch_frequency_hz)
        self.summaryLabel.setText(f"Config loaded for {device_id}.")
        self._refresh_history()

    def _browse_csv(self) -> None:
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Open DSView CSV",
            "",
            "CSV Files (*.csv);;All Files (*)",
        )
        if file_name:
            self.csvPathLineEdit.setText(file_name)

    def _analyze_csv(self) -> None:
        try:
            device_id = self._device_id()
            path = self._csv_path()
            self._save_profile_or_raise()
            self._analysis = self.runtime.analyze_csv(device_id=device_id, csv_path=path)
        except Exception as exc:
            self._analysis = None
            self.standardMassSpinBox.setEnabled(False)
            self.calculateTrialButton.setEnabled(False)
            self._show_error("Analysis failed", exc)
            return
        self.summaryLabel.setText(
            f"Pulses: {self._analysis.pulse_count} | "
            f"Quantity: {self._analysis.total_quantity:g} {self._analysis.config.unit} | "
            f"Boundary: {self._analysis.boundary_pulse_count}"
        )
        self._plot_analysis(self._analysis)
        self.standardMassSpinBox.setEnabled(True)
        self.calculateTrialButton.setEnabled(True)

    def _calculate_trial(self) -> None:
        if self._analysis is None:
            self.summaryLabel.setText("Analyze a CSV before calculating a trial.")
            return
        try:
            result = self.runtime.calculate_trial_from_analysis(
                device_id=self._device_id(),
                analysis=self._analysis,
                standard_quantity=self.standardMassSpinBox.value(),
                flow_point=self.flowPointSpinBox.value(),
                trial_index=self.trialIndexSpinBox.value(),
                source_path=str(self._csv_path()),
            )
        except Exception as exc:
            self._show_error("Trial calculation failed", exc)
            return
        self.summaryLabel.setText(
            f"Trial saved: measured={result.trial.measured_quantity:g} "
            f"standard={result.trial.standard_quantity:g} "
            f"error={result.trial.percent_error:.6f}%"
        )
        self._refresh_history()

    def _calculate_repeatability(self) -> None:
        try:
            device_id = self._device_id()
            trials = self.runtime.list_trials(device_id)
            selection_dialog = PulseRepeatabilitySelectionDialog(trials, parent=self)
            if selection_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            result = self.runtime.save_repeatability_selection(
                device_id,
                trial_ids=selection_dialog.selected_trial_ids(),
            )
        except Exception as exc:
            self._show_error("Repeatability calculation failed", exc)
            return
        self.repeatabilitySummaryLabel.setText(
            "Repeatability saved: "
            f"flow={result.flow_point:g} "
            f"mean={result.mean_percent_error:.6g}% "
            f"stddev={result.repeatability_stddev_percent:.6g}%"
        )
        self.summaryLabel.setText(
            "Repeatability saved: "
            f"flow={result.flow_point:g} "
            f"stddev={result.repeatability_stddev_percent:.6g}%"
        )
        self._refresh_history()

    def _plot_analysis(self, analysis: PulseAnalysisResult) -> None:
        self.ratePlot.clear()
        if not analysis.windows:
            return
        x_values = [
            (window.start_s + window.end_s) / 2.0
            for window in analysis.windows
        ]
        y_values = [window.rate for window in analysis.windows]
        self.ratePlot.plot(
            x_values,
            y_values,
            pen=pg.mkPen("#2563eb", width=1.8),
            symbol="o",
            symbolSize=5,
            symbolBrush="#2563eb",
            name="rate",
        )

    def _refresh_history(self) -> None:
        device_id = self.deviceIdLineEdit.text().strip()
        records = self.runtime.list_history(device_id) if device_id else ()
        self.historyTable.setRowCount(len(records))
        for row, record in enumerate(records):
            summary = record.summary
            pulse_count = summary.get("pulse_count", "")
            if record.operation_type == "pulse_repeatability":
                pulse_count = summary.get("trial_count", "")
            percent_error = summary.get("percent_error", "")
            if record.operation_type == "pulse_repeatability":
                percent_error = summary.get("repeatability_stddev_percent", "")
            values = (
                record.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if record.started_at
                else "",
                record.operation_type,
                record.status,
                str(pulse_count),
                str(percent_error),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
                self.historyTable.setItem(row, column, item)
        self.historyTable.resizeColumnsToContents()
        self._refresh_trials(device_id)

    def _refresh_trials(self, device_id: str) -> None:
        trials = self.runtime.list_trials(device_id) if device_id else ()
        self.trialTable.setRowCount(len(trials))
        for row, trial in enumerate(trials):
            values = (
                f"{trial.flow_point:g}",
                str(trial.trial_index),
                str(trial.pulse_count),
                f"{trial.measured_quantity:g}",
                f"{trial.standard_quantity:g}",
                f"{trial.percent_error:.6g}",
                str(trial.boundary_pulse_count),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
                self.trialTable.setItem(row, column, item)
        self.trialTable.resizeColumnsToContents()
        self.calculateRepeatabilityButton.setEnabled(_has_repeatability_window(trials))

    def _device_id(self) -> str:
        device_id = self.deviceIdLineEdit.text().strip()
        if not device_id:
            raise ValueError("Device ID is required.")
        return device_id

    def _csv_path(self) -> Path:
        raw_path = self.csvPathLineEdit.text().strip()
        if not raw_path:
            raise ValueError("CSV path is required.")
        if len(raw_path) >= 2 and raw_path[0] == raw_path[-1] and raw_path[0] in {'"', "'"}:
            raw_path = raw_path[1:-1].strip()
        if not raw_path:
            raise ValueError("CSV path is required.")
        return Path(raw_path)

    def _show_error(self, prefix: str, exc: Exception) -> None:
        self.summaryLabel.setText(f"{prefix}: {exc}")


class PulseRepeatabilitySelectionDialog(QDialog):
    """Select one flow point and three consecutive Pulse trials."""

    def __init__(
        self,
        trials: tuple[PulseTrialRecord, ...],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calculate Pulse Repeatability")
        self.setModal(True)
        self.resize(520, 360)
        self._trials_by_flow: dict[float, tuple[PulseTrialRecord, ...]] = {}
        for trial in sorted(
            trials,
            key=lambda item: (item.flow_point, item.trial_index, item.trial_id),
        ):
            flow_trials = list(self._trials_by_flow.get(trial.flow_point, ()))
            flow_trials.append(trial)
            self._trials_by_flow[trial.flow_point] = tuple(flow_trials)
        self._build_ui()
        self._flow_changed()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.flowCombo = QComboBox()
        self.flowCombo.setObjectName("pulseRepeatabilitySelectionFlowCombo")
        for flow_point in sorted(self._trials_by_flow):
            self.flowCombo.addItem(f"{flow_point:g}", flow_point)
        self.flowCombo.currentIndexChanged.connect(self._flow_changed)
        form.addRow("Flow Point", self.flowCombo)

        self.windowCombo = QComboBox()
        self.windowCombo.setObjectName("pulseRepeatabilitySelectionWindowCombo")
        self.windowCombo.currentIndexChanged.connect(self._window_changed)
        form.addRow("Trial Window", self.windowCombo)
        root.addLayout(form)

        self.previewTextEdit = QTextEdit()
        self.previewTextEdit.setObjectName("pulseRepeatabilitySelectionPreview")
        self.previewTextEdit.setReadOnly(True)
        self.previewTextEdit.setMinimumHeight(120)
        root.addWidget(self.previewTextEdit, 1)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.okButton = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        root.addWidget(self.buttonBox)

    def selected_trial_ids(self) -> tuple[str, ...]:
        return tuple(trial.trial_id for trial in self.selected_trials())

    def selected_trials(self) -> tuple[PulseTrialRecord, ...]:
        data = self.windowCombo.currentData()
        if isinstance(data, tuple) and all(
            isinstance(item, PulseTrialRecord) for item in data
        ):
            return data
        return ()

    def _flow_changed(self) -> None:
        flow_point = self.flowCombo.currentData()
        self.windowCombo.blockSignals(True)
        self.windowCombo.clear()
        if isinstance(flow_point, (int, float)):
            trials = self._trials_by_flow.get(float(flow_point), ())
            for index in range(max(0, len(trials) - 2)):
                window = trials[index : index + 3]
                if not _is_consecutive_trials(window):
                    continue
                self.windowCombo.addItem(
                    f"Trial {window[0].trial_index}-{window[-1].trial_index}",
                    window,
                )
        self.windowCombo.blockSignals(False)
        self._window_changed()

    def _window_changed(self) -> None:
        trials = self.selected_trials()
        if not trials:
            self.previewTextEdit.setPlainText("No consecutive three-trial window available.")
            self.okButton.setEnabled(False)
            return
        mean_error = sum(trial.percent_error for trial in trials) / len(trials)
        lines = [
            f"Flow Point: {trials[0].flow_point:g}",
            "Selected Trials:",
        ]
        lines.extend(
            f"Trial {trial.trial_index}: error={trial.percent_error:.6g}% "
            f"measured={trial.measured_quantity:g} standard={trial.standard_quantity:g}"
            for trial in trials
        )
        lines.append(f"Mean Error: {mean_error:.6g}%")
        self.previewTextEdit.setPlainText("\n".join(lines))
        self.okButton.setEnabled(True)


def _has_repeatability_window(trials: tuple[PulseTrialRecord, ...]) -> bool:
    by_flow: dict[float, list[PulseTrialRecord]] = {}
    for trial in trials:
        by_flow.setdefault(trial.flow_point, []).append(trial)
    return any(
        _is_consecutive_trials(tuple(window))
        for flow_trials in by_flow.values()
        for window in _sliding_windows(
            sorted(flow_trials, key=lambda item: (item.trial_index, item.trial_id)),
            3,
        )
    )


def _sliding_windows(
    trials: list[PulseTrialRecord],
    size: int,
) -> tuple[tuple[PulseTrialRecord, ...], ...]:
    if len(trials) < size:
        return ()
    return tuple(tuple(trials[index : index + size]) for index in range(len(trials) - size + 1))


def _is_consecutive_trials(trials: tuple[PulseTrialRecord, ...]) -> bool:
    if len(trials) != 3:
        return False
    indexes = [trial.trial_index for trial in sorted(trials, key=lambda item: item.trial_index)]
    return indexes == list(range(indexes[0], indexes[0] + 3))

"""Single-page PySide6 workbench for manual filling trials."""

from __future__ import annotations

import json
from math import isfinite

from PySide6.QtCore import Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coreflow.app import (
    FillingAnalysisRecord,
    FillingConfiguration,
    FillingGroupSnapshot,
    FillingMode,
    FillingTrialService,
)
from coreflow.storage import FillingAdvanceProfileRecord, StorageRepository
from coreflow.ui.filling_dialogs import FillingDeviceSelectionDialog
from coreflow.ui.filling_history import FillingHistoryDialog


class FillingModuleWindow(QDialog):
    """Coordinate the operator-facing Filling workbench with its service."""

    def __init__(
        self,
        repository: StorageRepository | None = None,
        *,
        service: FillingTrialService | None = None,
        operator: str = "operator",
        parent: QWidget | None = None,
        embedded: bool = False,
    ) -> None:
        if repository is not None and service is not None:
            raise ValueError("Provide either repository or service, not both.")
        if service is None:
            if repository is None:
                raise ValueError("A StorageRepository or FillingTrialService is required.")
            service = FillingTrialService(repository, operator=operator)
        flags = Qt.WindowType.Widget if embedded else Qt.WindowType.Dialog
        super().__init__(parent, flags)
        self.service = service
        self.embedded = embedded
        self.deviceSelectionDialog: FillingDeviceSelectionDialog | None = None
        self.historyDialog: FillingHistoryDialog | None = None
        self._profiles_by_id: dict[str, FillingAdvanceProfileRecord] = {}
        self._preview_advance_result_id: str | None = None
        self._loading = False

        self.setObjectName("fillingModuleWindow")
        self.setWindowTitle("Filling Module")
        self.resize(1080, 700)
        self.setMinimumSize(900, 600)
        self._build_ui()
        self._refresh_profiles()
        self._refresh_from_snapshot(apply_configuration=True)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QGridLayout()
        header.setHorizontalSpacing(10)
        header.setVerticalSpacing(8)
        header.addWidget(QLabel("Device ID"), 0, 0)
        self.deviceValueLabel = QLabel("Not selected")
        self.deviceValueLabel.setObjectName("fillingDeviceValueLabel")
        self.deviceValueLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        header.addWidget(self.deviceValueLabel, 0, 1)
        self.changeDeviceButton = QPushButton("Change Device...")
        self.changeDeviceButton.setObjectName("fillingChangeDeviceButton")
        header.addWidget(self.changeDeviceButton, 0, 2)
        header.setColumnStretch(3, 1)
        self.historyButton = QPushButton("History...")
        self.historyButton.setObjectName("fillingHistoryButton")
        header.addWidget(self.historyButton, 0, 4)

        header.addWidget(QLabel("Control / valve label"), 1, 0)
        self.controlValveCombo = QComboBox()
        self.controlValveCombo.setObjectName("fillingControlValveCombo")
        self.controlValveCombo.setEditable(True)
        self.controlValveCombo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        if self.controlValveCombo.lineEdit() is not None:
            self.controlValveCombo.lineEdit().setObjectName(
                "fillingControlValveLineEdit"
            )
        header.addWidget(self.controlValveCombo, 1, 1, 1, 2)
        self.newLabelButton = QPushButton("New Label...")
        self.newLabelButton.setObjectName("fillingNewLabelButton")
        header.addWidget(self.newLabelButton, 1, 3)
        header.addWidget(QLabel("Advance profile"), 2, 0)
        self.advanceProfileCombo = QComboBox()
        self.advanceProfileCombo.setObjectName("fillingAdvanceProfileCombo")
        self.advanceProfileCombo.setEditable(False)
        header.addWidget(self.advanceProfileCombo, 2, 1, 1, 4)
        root.addLayout(header)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        mode_row.addWidget(QLabel("Mode"))
        mode_row.addSpacing(10)
        self.regularModeButton = QPushButton("Regular Test")
        self.regularModeButton.setObjectName("fillingRegularModeButton")
        self.regularModeButton.setCheckable(True)
        self.advanceModeButton = QPushButton("Calculate Advance")
        self.advanceModeButton.setObjectName("fillingAdvanceModeButton")
        self.advanceModeButton.setCheckable(True)
        self.modeButtonGroup = QButtonGroup(self)
        self.modeButtonGroup.setObjectName("fillingModeButtonGroup")
        self.modeButtonGroup.setExclusive(True)
        self.modeButtonGroup.addButton(self.regularModeButton)
        self.modeButtonGroup.addButton(self.advanceModeButton)
        self.regularModeButton.setChecked(True)
        segment_style = (
            "QPushButton { padding: 5px 12px; border: 1px solid #8c9299; }"
            "QPushButton:checked { background: #dce8f2; font-weight: 600; }"
        )
        self.regularModeButton.setStyleSheet(segment_style)
        self.advanceModeButton.setStyleSheet(segment_style)
        mode_row.addWidget(self.regularModeButton)
        mode_row.addWidget(self.advanceModeButton)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        configuration = QGridLayout()
        configuration.setHorizontalSpacing(10)
        configuration.setVerticalSpacing(8)
        self.pulseSwitchSpinBox = self._number_spin_box(
            "fillingPulseSwitchSpinBox", 125.0
        )
        self.massPerPulseSpinBox = self._number_spin_box(
            "fillingMassPerPulseSpinBox", 0.1
        )
        self.massUnitEdit = QLineEdit("g")
        self.massUnitEdit.setObjectName("fillingMassUnitEdit")
        self.flowPointSpinBox = self._number_spin_box(
            "fillingFlowPointSpinBox", 100.0
        )
        self.specifiedMassSpinBox = self._number_spin_box(
            "fillingSpecifiedMassSpinBox", 1000.0
        )
        self.targetMassSpinBox = self._number_spin_box(
            "fillingTargetMassSpinBox", 1000.0
        )
        fields = (
            ("Pulse switch point (Hz)", self.pulseSwitchSpinBox),
            ("Mass per pulse", self.massPerPulseSpinBox),
            ("Mass unit", self.massUnitEdit),
            ("Flow point (g/s)", self.flowPointSpinBox),
            ("Specified mass", self.specifiedMassSpinBox),
            ("Target mass", self.targetMassSpinBox),
        )
        for index, (label_text, field) in enumerate(fields):
            row = index // 3
            pair = index % 3
            configuration.addWidget(QLabel(label_text), row, pair * 2)
            configuration.addWidget(field, row, pair * 2 + 1)
            configuration.setColumnStretch(pair * 2 + 1, 1)
        root.addLayout(configuration)

        trial_row = QHBoxLayout()
        trial_row.addWidget(QLabel("Current trial"))
        self.currentTrialIndexLabel = QLabel("1")
        self.currentTrialIndexLabel.setObjectName("fillingCurrentTrialIndexLabel")
        self.currentTrialIndexLabel.setMinimumWidth(26)
        trial_row.addWidget(self.currentTrialIndexLabel)
        trial_row.addSpacing(16)
        trial_row.addWidget(QLabel("Standard mass"))
        self.standardMassEdit = QLineEdit()
        self.standardMassEdit.setObjectName("fillingStandardMassEdit")
        validator = QDoubleValidator(0.0, 1.0e100, 12, self.standardMassEdit)
        validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
        self.standardMassEdit.setValidator(validator)
        self.standardMassEdit.setMaximumWidth(220)
        trial_row.addWidget(self.standardMassEdit)
        self.calculateTrialButton = QPushButton("Calculate Current Trial Error")
        self.calculateTrialButton.setObjectName("fillingCalculateTrialButton")
        trial_row.addWidget(self.calculateTrialButton)
        self.addTrialButton = QPushButton("Add Trial")
        self.addTrialButton.setObjectName("fillingAddTrialButton")
        trial_row.addWidget(self.addTrialButton)
        trial_row.addStretch(1)
        root.addLayout(trial_row)

        self.trialTable = QTableWidget(0, 9)
        self.trialTable.setObjectName("fillingTrialTable")
        self.trialTable.setHorizontalHeaderLabels(
            [
                "Select",
                "Trial",
                "Timestamp",
                "Flow",
                "Specified",
                "Target",
                "Standard",
                "Percent Error",
                "Status",
            ]
        )
        self.trialTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.trialTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.trialTable.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.trialTable.verticalHeader().setVisible(False)
        table_header = self.trialTable.horizontalHeader()
        for column in range(9):
            table_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table_header.setStretchLastSection(False)
        root.addWidget(self.trialTable, 1)

        analysis_row = QHBoxLayout()
        self.calculateRepeatabilityButton = QPushButton("Calculate Repeatability")
        self.calculateRepeatabilityButton.setObjectName(
            "fillingCalculateRepeatabilityButton"
        )
        self.calculateAdvanceButton = QPushButton("Calculate Advance")
        self.calculateAdvanceButton.setObjectName("fillingCalculateAdvanceButton")
        self.setAdvanceButton = QPushButton("Set Advance")
        self.setAdvanceButton.setObjectName("fillingSetAdvanceButton")
        self.endGroupButton = QPushButton("End Group")
        self.endGroupButton.setObjectName("fillingEndGroupButton")
        analysis_row.addWidget(self.calculateRepeatabilityButton)
        analysis_row.addWidget(self.calculateAdvanceButton)
        analysis_row.addWidget(self.setAdvanceButton)
        analysis_row.addStretch(1)
        analysis_row.addWidget(self.endGroupButton)
        root.addLayout(analysis_row)

        self.resultTextEdit = QTextEdit()
        self.resultTextEdit.setObjectName("fillingResultTextEdit")
        self.resultTextEdit.setReadOnly(True)
        self.resultTextEdit.setMinimumHeight(88)
        self.resultTextEdit.setMaximumHeight(140)
        root.addWidget(self.resultTextEdit)

        self.statusLabel = QLabel("Select a device to begin.")
        self.statusLabel.setObjectName("fillingStatusLabel")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.statusLabel)

        self.changeDeviceButton.clicked.connect(self.open_device_selector)
        self.newLabelButton.clicked.connect(self._new_label)
        self.historyButton.clicked.connect(self._show_history)
        self.regularModeButton.toggled.connect(
            lambda checked: self._mode_changed(FillingMode.REGULAR, checked)
        )
        self.advanceModeButton.toggled.connect(
            lambda checked: self._mode_changed(FillingMode.ADVANCE, checked)
        )
        self.specifiedMassSpinBox.valueChanged.connect(
            self._specified_mass_changed
        )
        self.advanceProfileCombo.currentIndexChanged.connect(
            self._profile_changed
        )
        self.calculateTrialButton.clicked.connect(self._calculate_current_trial)
        self.addTrialButton.clicked.connect(self._add_trial)
        self.calculateRepeatabilityButton.clicked.connect(
            self._calculate_repeatability
        )
        self.calculateAdvanceButton.clicked.connect(self._calculate_advance)
        self.setAdvanceButton.clicked.connect(self._set_advance)
        self.endGroupButton.clicked.connect(self.end_active_group)
        self.trialTable.itemChanged.connect(self._trial_item_changed)

    @staticmethod
    def _number_spin_box(object_name: str, value: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox()
        field.setObjectName(object_name)
        field.setDecimals(15)
        field.setRange(0.0, 1.0e18)
        field.setSingleStep(0.1)
        field.setValue(value)
        field.setAccelerated(True)
        return field

    def open_device_selector(self) -> FillingDeviceSelectionDialog | None:
        """Show a non-blocking shared-device selector for tests and actions."""

        try:
            snapshot = self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return None
        if snapshot.run_id is not None:
            self.statusLabel.setText("End Group before changing devices.")
            return None
        dialog = self.deviceSelectionDialog
        if dialog is None:
            dialog = FillingDeviceSelectionDialog(self.service, parent=self)
            dialog.accepted.connect(lambda: self._device_selected(dialog))
            self.deviceSelectionDialog = dialog
        dialog.refresh_devices(snapshot.device_id)
        if dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            return dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def ensure_device_selected(self) -> bool:
        """Require a modal Device ID selection when the module first opens."""

        try:
            if self.service.snapshot().device_id is not None:
                return True
        except Exception as exc:
            self._set_error(exc)
            return False
        dialog = self.open_device_selector()
        if dialog is None:
            return False
        dialog.exec()
        try:
            return self.service.snapshot().device_id is not None
        except Exception as exc:
            self._set_error(exc)
            return False

    def _device_selected(self, dialog: FillingDeviceSelectionDialog) -> None:
        device_id = dialog.selected_device_id()
        if device_id is None:
            return
        try:
            if self.service.snapshot().device_id == device_id:
                return
            restored = self.service.select_device(device_id)
        except Exception as exc:
            self._set_error(exc)
            return
        self.standardMassEdit.clear()
        self._clear_analysis_result()
        self._refresh_profiles()
        self._refresh_from_snapshot(apply_configuration=restored is not None)
        self.statusLabel.setText(f"Device selected: {device_id}")

    def _mode_changed(self, mode: FillingMode, checked: bool) -> None:
        if not checked or self._loading:
            return
        if mode is FillingMode.ADVANCE:
            self.targetMassSpinBox.setValue(self.specifiedMassSpinBox.value())
        self._apply_mode_state()
        self._clear_analysis_result()

    def _specified_mass_changed(self, value: float) -> None:
        if self._loading:
            return
        if self.advanceModeButton.isChecked():
            self.targetMassSpinBox.setValue(value)

    def _current_mode(self) -> FillingMode:
        return (
            FillingMode.ADVANCE
            if self.advanceModeButton.isChecked()
            else FillingMode.REGULAR
        )

    def _configuration_from_fields(self) -> FillingConfiguration:
        mode = self._current_mode()
        specified_mass = self.specifiedMassSpinBox.value()
        target_mass = (
            specified_mass
            if mode is FillingMode.ADVANCE
            else self.targetMassSpinBox.value()
        )
        return FillingConfiguration(
            mode=mode,
            control_valve_label=self.controlValveCombo.currentText(),
            pulse_frequency_switch_point_hz=self.pulseSwitchSpinBox.value(),
            mass_per_pulse=self.massPerPulseSpinBox.value(),
            mass_unit=self.massUnitEdit.text(),
            flow_point_g_per_s=self.flowPointSpinBox.value(),
            specified_mass=specified_mass,
            target_mass=target_mass,
        )

    def _standard_mass(self) -> float:
        text = self.standardMassEdit.text().strip()
        if not text:
            raise ValueError("Standard mass is required.")
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError("Standard mass must be a finite number greater than zero.") from exc
        if not isfinite(value) or value <= 0.0:
            raise ValueError("Standard mass must be a finite number greater than zero.")
        return value

    def _calculate_current_trial(self) -> None:
        try:
            configuration = self._configuration_from_fields()
            standard_mass = self._standard_mass()
            before = self.service.snapshot()
            if before.device_id is None:
                raise ValueError("Select a device before calculating a trial.")
            if before.run_id is None:
                self.service.start_group(configuration)
            elif not before.configuration_locked:
                self.service.update_pending_configuration(configuration)
            trial = self.service.calculate_current_trial(standard_mass)
        except Exception as exc:
            self._set_error(exc)
            self._refresh_from_snapshot(apply_configuration=False)
            self.standardMassEdit.setFocus(Qt.FocusReason.OtherFocusReason)
            return

        self.standardMassEdit.clear()
        self._clear_analysis_result()
        self._refresh_from_snapshot(apply_configuration=True)
        self.statusLabel.setText(
            f"Trial {trial.trial_index} calculated: {trial.percent_error:+.6f}%"
        )

    def _add_trial(self) -> None:
        try:
            snapshot = self.service.add_trial()
        except Exception as exc:
            self._set_error(exc)
            return
        self.standardMassEdit.clear()
        self._refresh_from_snapshot(snapshot=snapshot, apply_configuration=True)
        self.statusLabel.setText(
            f"Trial {snapshot.pending_trial_index} is ready."
        )
        self.standardMassEdit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _selected_trial_ids(self) -> tuple[str, ...]:
        selected: list[str] = []
        for row in range(self.trialTable.rowCount()):
            item = self.trialTable.item(row, 0)
            if item is None or item.checkState() is not Qt.CheckState.Checked:
                continue
            trial_id = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(trial_id, str):
                selected.append(trial_id)
        return tuple(selected)

    def _selected_trial_indexes(self) -> tuple[int, ...]:
        selected: list[int] = []
        for row in range(self.trialTable.rowCount()):
            item = self.trialTable.item(row, 0)
            index_item = self.trialTable.item(row, 1)
            if (
                item is not None
                and index_item is not None
                and item.checkState() is Qt.CheckState.Checked
            ):
                selected.append(int(index_item.text()))
        return tuple(selected)

    def _trial_item_changed(self, _item: QTableWidgetItem) -> None:
        if not self._loading:
            self._clear_analysis_result()
            self._update_action_state()

    def _calculate_repeatability(self) -> None:
        try:
            result = self.service.calculate_repeatability(
                self._selected_trial_ids()
            )
        except Exception as exc:
            self._set_error(exc)
            return
        self._preview_advance_result_id = None
        self._show_analysis_result(result)
        self._update_action_state()
        self.statusLabel.setText("Repeatability calculated and saved.")

    def _calculate_advance(self) -> None:
        try:
            result = self.service.calculate_advance(self._selected_trial_ids())
        except Exception as exc:
            self._set_error(exc)
            return
        self._preview_advance_result_id = result.result_id
        self._show_analysis_result(result)
        self._update_action_state()
        self.statusLabel.setText("Advance calculated and saved.")

    def _show_analysis_result(self, result: FillingAnalysisRecord) -> None:
        payload = {
            "result_id": result.result_id,
            "run_id": result.run_id,
            "result_type": result.result_type,
            "created_at": result.created_at.isoformat(),
            **result.metrics,
        }
        self.resultTextEdit.setPlainText(
            json.dumps(payload, indent=2, sort_keys=True, default=str)
        )

    def _set_advance(self) -> None:
        result_id = self._preview_advance_result_id
        if result_id is None:
            self.statusLabel.setText("Calculate Advance before setting it.")
            return
        try:
            profile = self.service.set_advance(result_id)
        except Exception as exc:
            self._set_error(exc)
            return

        self.standardMassEdit.clear()
        self._clear_analysis_result()
        self._refresh_profiles(selected_profile_id=profile.profile_id)
        self._refresh_from_snapshot(apply_configuration=True)
        self.statusLabel.setText(f"Advance profile set: {profile.profile_id}")

    def end_active_group(self) -> bool:
        """End the active group, remaining safe when no group exists."""

        try:
            before = self.service.snapshot()
            self.service.end_group()
            after = self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return False
        self.standardMassEdit.clear()
        self._clear_analysis_result()
        self._refresh_from_snapshot(
            snapshot=after,
            apply_configuration=bool(before.trials),
        )
        self.statusLabel.setText(
            "Group ended." if before.run_id is not None else "No active group."
        )
        return True

    def _refresh_from_snapshot(
        self,
        *,
        snapshot: FillingGroupSnapshot | None = None,
        apply_configuration: bool,
    ) -> None:
        try:
            current = snapshot or self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return

        self.deviceValueLabel.setText(current.device_id or "Not selected")
        if apply_configuration and current.configuration is not None:
            self._load_configuration(current.configuration)
        self._populate_trials(current)
        self.currentTrialIndexLabel.setText(
            str(current.pending_trial_index or 1)
        )
        self._set_configuration_locked(current.configuration_locked)
        self._apply_mode_state()
        self._update_action_state(current)

    def _load_configuration(self, configuration: FillingConfiguration) -> None:
        self._loading = True
        try:
            if configuration.mode is FillingMode.ADVANCE:
                self.advanceModeButton.setChecked(True)
            else:
                self.regularModeButton.setChecked(True)
            self._select_or_add_label(configuration.control_valve_label)
            self.pulseSwitchSpinBox.setValue(
                configuration.pulse_frequency_switch_point_hz
            )
            self.massPerPulseSpinBox.setValue(configuration.mass_per_pulse)
            self.massUnitEdit.setText(configuration.mass_unit)
            self.flowPointSpinBox.setValue(configuration.flow_point_g_per_s)
            self.specifiedMassSpinBox.setValue(configuration.specified_mass)
            self.targetMassSpinBox.setValue(configuration.target_mass)
        finally:
            self._loading = False

    def _populate_trials(self, snapshot: FillingGroupSnapshot) -> None:
        selected_ids = set(self._selected_trial_ids())
        self._loading = True
        self.trialTable.blockSignals(True)
        try:
            self.trialTable.setRowCount(0)
            for trial in snapshot.trials:
                row = self.trialTable.rowCount()
                self.trialTable.insertRow(row)
                select_item = QTableWidgetItem()
                select_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                select_item.setCheckState(
                    Qt.CheckState.Checked
                    if trial.trial_id in selected_ids
                    else Qt.CheckState.Unchecked
                )
                select_item.setData(Qt.ItemDataRole.UserRole, trial.trial_id)
                timestamp = (
                    trial.calculated_at.astimezone().strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if trial.calculated_at is not None
                    else ""
                )
                values = (
                    select_item,
                    QTableWidgetItem(str(trial.trial_index)),
                    QTableWidgetItem(timestamp),
                    QTableWidgetItem(_format_number(trial.flow_point_g_per_s)),
                    QTableWidgetItem(_format_number(trial.specified_mass)),
                    QTableWidgetItem(_format_number(trial.target_mass)),
                    QTableWidgetItem(_format_number(trial.standard_mass)),
                    QTableWidgetItem(f"{trial.percent_error:+.6f}%"),
                    QTableWidgetItem(trial.trial_status.capitalize()),
                )
                for column, item in enumerate(values):
                    self.trialTable.setItem(row, column, item)
        finally:
            self.trialTable.blockSignals(False)
            self._loading = False

    def _set_configuration_locked(self, locked: bool) -> None:
        for control in (
            self.controlValveCombo,
            self.newLabelButton,
            self.advanceProfileCombo,
            self.regularModeButton,
            self.advanceModeButton,
            self.pulseSwitchSpinBox,
            self.massPerPulseSpinBox,
            self.massUnitEdit,
            self.flowPointSpinBox,
            self.specifiedMassSpinBox,
            self.targetMassSpinBox,
        ):
            control.setEnabled(not locked)
        if not locked:
            try:
                has_device = self.service.snapshot().device_id is not None
            except Exception:
                has_device = False
            self.advanceProfileCombo.setEnabled(
                has_device and bool(self._profiles_by_id)
            )

    def _apply_mode_state(self) -> None:
        advance = self.advanceModeButton.isChecked()
        if advance and not self._loading:
            self.targetMassSpinBox.setValue(self.specifiedMassSpinBox.value())
        self.targetMassSpinBox.setReadOnly(advance)
        self.calculateRepeatabilityButton.setVisible(not advance)
        self.calculateAdvanceButton.setVisible(advance)

    def _update_action_state(
        self,
        snapshot: FillingGroupSnapshot | None = None,
    ) -> None:
        try:
            current = snapshot or self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return
        has_device = current.device_id is not None
        can_calculate_trial = has_device and (
            current.run_id is None or current.has_pending_trial
        )
        self.calculateTrialButton.setEnabled(can_calculate_trial)
        self.addTrialButton.setEnabled(
            current.run_id is not None
            and bool(current.trials)
            and not current.has_pending_trial
        )
        self.endGroupButton.setEnabled(current.run_id is not None)
        self.historyButton.setEnabled(has_device)

        indexes = tuple(sorted(self._selected_trial_indexes()))
        consecutive = (
            len(indexes) == 3
            and indexes == tuple(range(indexes[0], indexes[0] + 3))
        )
        regular = self.regularModeButton.isChecked()
        self.calculateRepeatabilityButton.setEnabled(
            regular and consecutive and current.run_id is not None
        )
        self.calculateAdvanceButton.setEnabled(
            (not regular) and len(indexes) >= 3 and current.run_id is not None
        )
        self.setAdvanceButton.setEnabled(
            (not regular)
            and current.run_id is not None
            and self._preview_advance_result_id is not None
        )

    def _refresh_profiles(self, selected_profile_id: str | None = None) -> None:
        try:
            snapshot = self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return
        self._profiles_by_id.clear()
        profiles: tuple[FillingAdvanceProfileRecord, ...] = ()
        if snapshot.device_id is not None:
            try:
                profiles = self.service.list_advance_profiles()
            except Exception as exc:
                self._set_error(exc)

        current_label = self.controlValveCombo.currentText()
        self.advanceProfileCombo.blockSignals(True)
        try:
            self.advanceProfileCombo.clear()
            self.advanceProfileCombo.addItem("No profile selected", None)
            for profile in profiles:
                self._profiles_by_id[profile.profile_id] = profile
                timestamp = (
                    profile.created_at.isoformat()
                    if profile.created_at is not None
                    else "time unavailable"
                )
                text = (
                    f"{profile.control_valve_label} | "
                    f"flow={_format_profile_number(profile.flow_point_g_per_s)} g/s | "
                    f"specified={_format_profile_number(profile.specified_mass)} "
                    f"{profile.mass_unit} | "
                    f"advance={_format_profile_number(profile.advance_mass, signed=True)} "
                    f"{profile.mass_unit} | "
                    f"{timestamp} | {profile.profile_id}"
                )
                self.advanceProfileCombo.addItem(text, profile.profile_id)
            if selected_profile_id is not None:
                index = self.advanceProfileCombo.findData(selected_profile_id)
                if index >= 0:
                    self.advanceProfileCombo.setCurrentIndex(index)
        finally:
            self.advanceProfileCombo.blockSignals(False)

        labels = sorted(
            {
                profile.control_valve_label
                for profile in profiles
                if profile.control_valve_label
            }
        )
        self.controlValveCombo.blockSignals(True)
        try:
            self.controlValveCombo.clear()
            self.controlValveCombo.addItems(labels)
            self.controlValveCombo.setEditText(current_label)
        finally:
            self.controlValveCombo.blockSignals(False)
        self.advanceProfileCombo.setEnabled(
            snapshot.device_id is not None
            and bool(profiles)
            and not snapshot.configuration_locked
        )

    def _profile_changed(self, index: int) -> None:
        if self._loading or index <= 0:
            return
        profile_id = self.advanceProfileCombo.itemData(index)
        profile = self._profiles_by_id.get(str(profile_id))
        if profile is None:
            return
        try:
            snapshot = self.service.snapshot()
        except Exception as exc:
            self._set_error(exc)
            return
        if snapshot.configuration_locked:
            self.statusLabel.setText(
                "End Group before changing the advance profile."
            )
            return
        self._loading = True
        try:
            self.regularModeButton.setChecked(True)
            self._select_or_add_label(profile.control_valve_label)
            self.pulseSwitchSpinBox.setValue(
                profile.pulse_frequency_switch_point_hz
            )
            self.massPerPulseSpinBox.setValue(profile.mass_per_pulse)
            self.massUnitEdit.setText(profile.mass_unit)
            self.flowPointSpinBox.setValue(profile.flow_point_g_per_s)
            self.specifiedMassSpinBox.setValue(profile.specified_mass)
            self.targetMassSpinBox.setValue(profile.corrected_target_mass)
        finally:
            self._loading = False
        self.standardMassEdit.clear()
        self._clear_analysis_result()
        self._apply_mode_state()
        self._update_action_state(snapshot)
        self.statusLabel.setText(f"Advance profile loaded: {profile.profile_id}")

    def _new_label(self) -> None:
        label, accepted = QInputDialog.getText(
            self,
            "New Control / Valve Label",
            "Label",
        )
        normalized = label.strip()
        if not accepted:
            return
        if not normalized:
            self.statusLabel.setText("Control/valve label must be non-empty.")
            return
        self._select_or_add_label(normalized)

    def _select_or_add_label(self, label: str) -> None:
        index = self.controlValveCombo.findText(label)
        if index < 0 and label:
            self.controlValveCombo.addItem(label)
            index = self.controlValveCombo.findText(label)
        if index >= 0:
            self.controlValveCombo.setCurrentIndex(index)
        else:
            self.controlValveCombo.setEditText(label)

    def _show_history(self) -> None:
        try:
            device_id = self.service.snapshot().device_id
        except Exception as exc:
            self._set_error(exc)
            return
        if device_id is None:
            self.statusLabel.setText("Select a device before opening history.")
            return
        dialog = self.historyDialog
        if dialog is not None and dialog.device_id == device_id:
            dialog.refresh_records()
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            return
        if dialog is not None:
            dialog.close()
            dialog.setParent(None)
            dialog.deleteLater()
            self.historyDialog = None
        dialog = FillingHistoryDialog(
            self.service,
            device_id,
            parent=self,
        )
        self.historyDialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _clear_analysis_result(self) -> None:
        self._preview_advance_result_id = None
        self.resultTextEdit.clear()

    def _set_error(self, error: Exception) -> None:
        self.statusLabel.setText(f"Filling operation failed: {error}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self.end_active_group():
            event.ignore()
            return
        super().closeEvent(event)


def _format_number(value: float) -> str:
    return f"{value:.15g}"


def _format_profile_number(value: float, *, signed: bool = False) -> str:
    return f"{value:+.15g}" if signed else f"{value:.15g}"


__all__ = ["FillingModuleWindow"]

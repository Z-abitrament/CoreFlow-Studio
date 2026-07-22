"""Standalone Modbus master module window."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pyqtgraph as pg
from shiboken6 import isValid
from PySide6.QtCore import QDateTime, QMimeData, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QDrag, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QHeaderView,
    QCheckBox,
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
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDateTimeEdit,
)

from coreflow.app.modbus_runtime import (
    ModbusCalibrationHistoryEntry,
    ModbusCalibrationHistoryExportResult,
    ModbusCalibrationHistoryImportResult,
    ModbusConnectionSettings,
    ModbusDeviceProfile,
    ModbusFlowSampleSeries,
    ModbusTrialSamplePoint,
    ModbusRepeatabilityHistoryTrial,
    ModbusKFactorSimpleCapture,
    ModbusKFactorSimpleResult,
    ModbusModuleRuntime,
    ModbusOperationMetadata,
    ModbusRepeatabilityFlowSummary,
    ModbusRepeatabilitySimpleCapture,
    ModbusRepeatabilitySimpleResult,
    ModbusRepeatabilitySimpleTrialResult,
    ModbusVariableSamplingRunResult,
    ModbusVariableSampleResult,
    ModbusZeroCalibrationResult,
)
from coreflow.app.variable_sampling import VariableSample
from coreflow.hardware import SerialPortInfo, SerialPortScanner
from coreflow.hardware.register_map import register_map_from_json, register_map_to_json
from coreflow.protocols.modbus import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    WordOrder,
)
from coreflow.storage import StorageRepository
from coreflow.ui.workers import WorkflowTask
from coreflow.ui.modbus_zero_monitor import ZeroMonitorDialog
from coreflow.app.modbus_zero_monitor import (
    ZeroMonitorLiveUpdate,
    ZeroMonitorRunResult,
)
from coreflow.app.modbus_register_maps import (
    ModbusRegisterMapCatalogEntry,
    suggest_custom_register_map_id,
)


PLOT_LAYOUT_OVERLAY = "overlay"
PLOT_LAYOUT_SEPARATE = "separate"
PLOT_LAYOUTS = (PLOT_LAYOUT_OVERLAY, PLOT_LAYOUT_SEPARATE)
PLOT_ALIGNMENT_FIRST_SAMPLE = "first_sample"
PLOT_ALIGNMENT_PREFLOW_SAMPLE = "preflow_sample"
PLOT_ALIGNMENTS = (PLOT_ALIGNMENT_FIRST_SAMPLE, PLOT_ALIGNMENT_PREFLOW_SAMPLE)


class VariableMapTableWidget(QTableWidget):
    """QTableWidget with explicit row reorder callbacks for mixed cell widgets."""

    _ROW_MIME = "application/x-coreflow-variable-map-row"

    def __init__(self, rows: int, columns: int, *, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self.row_move_requested = None
        self._drag_source_row = -1

    def startDrag(self, supported_actions) -> None:  # noqa: N802 - Qt override name
        self._drag_source_row = self.currentRow()
        if self._drag_source_row < 0:
            return
        mime = QMimeData()
        mime.setData(self._ROW_MIME, str(self._drag_source_row).encode("ascii"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._drag_source_row = -1

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.mimeData().hasFormat(self._ROW_MIME) and self.dragEnabled():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if event.mimeData().hasFormat(self._ROW_MIME) and self.dragEnabled():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override name
        source = self._source_row_from_mime(event.mimeData())
        target = self._drop_target_row(event)
        if (
            callable(self.row_move_requested)
            and 0 <= source < self.rowCount()
            and 0 <= target < self.rowCount()
            and source != target
        ):
            self.row_move_requested(source, target)
            self._drag_source_row = -1
            event.acceptProposedAction()
            return
        self._drag_source_row = -1
        event.ignore()

    def _source_row_from_mime(self, mime: QMimeData) -> int:
        if not mime.hasFormat(self._ROW_MIME):
            return self._drag_source_row
        try:
            return int(bytes(mime.data(self._ROW_MIME)).decode("ascii"))
        except ValueError:
            return self._drag_source_row

    def _drop_target_row(self, event) -> int:
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target = self.indexAt(position).row()
        if target < 0 and self.rowCount() > 0:
            target = self.rowCount() - 1
        return target


class ModbusConnectionDialog(QDialog):
    """Focused connection settings dialog for the standalone Modbus module."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Modbus Connection")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 420, 320, 340, 280)
        self._build_ui()
        self.set_controls_enabled(False)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.portCombo = QComboBox()
        self.portCombo.setObjectName("modbusPortCombo")
        self.portCombo.setEditable(False)
        self.portCombo.addItem("Scanning serial ports...", "")
        self.refreshPortsButton = QPushButton("Refresh Ports")
        self.refreshPortsButton.setObjectName("modbusRefreshPortsButton")
        port_row = QHBoxLayout()
        port_row.addWidget(self.portCombo, 1)
        port_row.addWidget(self.refreshPortsButton)
        form.addRow("Port", port_row)

        self.unitIdSpinBox = QSpinBox()
        self.unitIdSpinBox.setObjectName("modbusUnitIdSpinBox")
        self.unitIdSpinBox.setRange(1, 247)
        self.unitIdSpinBox.setValue(1)
        form.addRow("Unit ID", self.unitIdSpinBox)

        self.baudrateSpinBox = QSpinBox()
        self.baudrateSpinBox.setObjectName("modbusBaudrateSpinBox")
        self.baudrateSpinBox.setRange(1200, 921600)
        self.baudrateSpinBox.setValue(9600)
        self.baudrateSpinBox.setSingleStep(1200)
        form.addRow("Baudrate", self.baudrateSpinBox)

        self.parityCombo = QComboBox()
        self.parityCombo.setObjectName("modbusParityCombo")
        self.parityCombo.addItems(["N", "E", "O"])
        form.addRow("Parity", self.parityCombo)

        self.stopBitsSpinBox = QSpinBox()
        self.stopBitsSpinBox.setObjectName("modbusStopBitsSpinBox")
        self.stopBitsSpinBox.setRange(1, 2)
        self.stopBitsSpinBox.setValue(1)
        form.addRow("Stop Bits", self.stopBitsSpinBox)

        self.orderCombo = QComboBox()
        self.orderCombo.setObjectName("modbusOrderCombo")
        self.orderCombo.addItems(["ABCD", "BADC", "CDAB", "DCBA"])
        form.addRow("Order", self.orderCombo)

        self.timeoutSpinBox = QDoubleSpinBox()
        self.timeoutSpinBox.setObjectName("modbusTimeoutSpinBox")
        self.timeoutSpinBox.setRange(0.1, 30.0)
        self.timeoutSpinBox.setDecimals(1)
        self.timeoutSpinBox.setSingleStep(0.5)
        self.timeoutSpinBox.setValue(3.0)
        form.addRow("Timeout (s)", self.timeoutSpinBox)

        self.retriesSpinBox = QSpinBox()
        self.retriesSpinBox.setObjectName("modbusRetriesSpinBox")
        self.retriesSpinBox.setRange(0, 10)
        self.retriesSpinBox.setValue(1)
        form.addRow("Retries", self.retriesSpinBox)

        self.statusValueLabel = QLabel("Disconnected")
        self.statusValueLabel.setObjectName("modbusConnectionStatusValueLabel")
        form.addRow("Status", self.statusValueLabel)
        root.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.connectButton = QPushButton("Connect")
        self.connectButton.setObjectName("modbusConnectButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusCloseConnectionDialogButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.connectButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def selected_port(self) -> str:
        data = self.portCombo.currentData()
        if isinstance(data, str):
            return data.strip()
        return self.portCombo.currentText().split(" - ", 1)[0].strip()

    def settings(self) -> ModbusConnectionSettings:
        return ModbusConnectionSettings(
            port=self.selected_port(),
            unit_id=self.unitIdSpinBox.value(),
            baudrate=self.baudrateSpinBox.value(),
            parity=self.parityCombo.currentText(),
            stop_bits=self.stopBitsSpinBox.value(),
            order=self.orderCombo.currentText(),
            read_timeout_s=self.timeoutSpinBox.value(),
            write_timeout_s=self.timeoutSpinBox.value(),
            retry_count=self.retriesSpinBox.value(),
        )

    def set_status(self, message: str) -> None:
        self.statusValueLabel.setText(message)

    def set_controls_enabled(self, enabled: bool) -> None:
        has_port = bool(self.selected_port())
        for widget in (
            self.portCombo,
            self.refreshPortsButton,
            self.unitIdSpinBox,
            self.baudrateSpinBox,
            self.parityCombo,
            self.stopBitsSpinBox,
            self.orderCombo,
            self.timeoutSpinBox,
            self.retriesSpinBox,
        ):
            widget.setEnabled(enabled)
        self.connectButton.setEnabled(enabled and has_port)
        self.closeButton.setEnabled(True)


class DeviceProfileDialog(QDialog):
    """Device profile editor with full register-map configuration."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Profile")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 920, 640, 620, 460)
        self._last_order = "ABCD"
        self._initial_register_map: ModbusRegisterMap | None = None
        self._catalog_entries: dict[
            tuple[str, str], ModbusRegisterMapCatalogEntry
        ] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        profile_group = QGroupBox("Identity And Metadata")
        profile_form = QFormLayout(profile_group)
        self.deviceIdLineEdit = QLineEdit()
        self.deviceIdLineEdit.setObjectName("modbusProfileDeviceIdLineEdit")
        self.deviceIdLineEdit.setPlaceholderText("Stable device ID, e.g. CFM-2026-001")
        self.deviceModelLineEdit = QLineEdit()
        self.deviceModelLineEdit.setObjectName("modbusProfileDeviceModelLineEdit")
        self.tubeModelLineEdit = QLineEdit()
        self.tubeModelLineEdit.setObjectName("modbusProfileTubeModelLineEdit")
        self.transmitterModelLineEdit = QLineEdit()
        self.transmitterModelLineEdit.setObjectName(
            "modbusProfileTransmitterModelLineEdit"
        )
        profile_form.addRow("Device ID", self.deviceIdLineEdit)
        profile_form.addRow("Device Model", self.deviceModelLineEdit)
        profile_form.addRow("Tube Model", self.tubeModelLineEdit)
        profile_form.addRow("Transmitter Model", self.transmitterModelLineEdit)
        root.addWidget(profile_group)

        map_group = QGroupBox("Register Map")
        map_layout = QVBoxLayout(map_group)
        map_selector = QHBoxLayout()
        self.registerMapCombo = QComboBox()
        self.registerMapCombo.setObjectName("modbusProfileRegisterMapCombo")
        self.newRegisterMapButton = QPushButton("New List")
        self.newRegisterMapButton.setObjectName("modbusProfileNewRegisterMapButton")
        self.previewMapChangesButton = QPushButton("Preview Changes")
        self.previewMapChangesButton.setObjectName(
            "modbusProfilePreviewRegisterMapChangesButton"
        )
        map_selector.addWidget(QLabel("Register List"))
        map_selector.addWidget(self.registerMapCombo, 1)
        map_selector.addWidget(self.newRegisterMapButton)
        map_selector.addWidget(self.previewMapChangesButton)
        map_layout.addLayout(map_selector)

        map_identity = QFormLayout()
        self.registerMapIdLineEdit = QLineEdit()
        self.registerMapIdLineEdit.setObjectName("modbusProfileRegisterMapIdLineEdit")
        self.registerMapIdLineEdit.setPlaceholderText("e.g. krohne_prj_main")
        self.registerMapNameLineEdit = QLineEdit()
        self.registerMapNameLineEdit.setObjectName(
            "modbusProfileRegisterMapNameLineEdit"
        )
        self.registerMapVersionLineEdit = QLineEdit("1.0.0")
        self.registerMapVersionLineEdit.setObjectName(
            "modbusProfileRegisterMapVersionLineEdit"
        )
        map_identity.addRow("List ID", self.registerMapIdLineEdit)
        map_identity.addRow("List Name", self.registerMapNameLineEdit)
        map_identity.addRow("Version", self.registerMapVersionLineEdit)
        map_layout.addLayout(map_identity)

        self.mapTable = VariableMapTableWidget(0, 8)
        self.mapTable.setObjectName("modbusProfileRegisterMapTable")
        self.mapTable.row_move_requested = self._move_register_row
        self.mapTable.setHorizontalHeaderLabels(
            [
                "Variable",
                "Kind",
                "Address",
                "Words",
                "Type",
                "Scale",
                "Unit",
                "Writable",
            ]
        )
        self.mapTable.verticalHeader().setVisible(False)
        self.mapTable.horizontalHeader().setSectionsMovable(True)
        self.mapTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mapTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mapTable.setDragEnabled(True)
        self.mapTable.setAcceptDrops(True)
        self.mapTable.viewport().setAcceptDrops(True)
        self.mapTable.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.mapTable.setDragDropOverwriteMode(False)
        self.mapTable.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.mapTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.mapTable.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.mapTable.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.mapTable.setAlternatingRowColors(True)
        for column, width in enumerate((170, 115, 80, 70, 110, 80, 85, 80)):
            self.mapTable.setColumnWidth(column, width)
        map_layout.addWidget(self.mapTable, 1)

        map_actions = QHBoxLayout()
        self.addVariableButton = QPushButton("Add")
        self.addVariableButton.setObjectName("modbusProfileAddVariableButton")
        self.deleteVariableButton = QPushButton("Delete")
        self.deleteVariableButton.setObjectName("modbusProfileDeleteVariableButton")
        self.resetMapButton = QPushButton("Reset")
        self.resetMapButton.setObjectName("modbusProfileResetMapButton")
        self.addVariableButton.clicked.connect(self._add_register_row)
        self.deleteVariableButton.clicked.connect(self._delete_selected_register_row)
        self.resetMapButton.clicked.connect(self._reset_register_map)
        self.registerMapCombo.currentIndexChanged.connect(
            self._register_map_selection_changed
        )
        self.newRegisterMapButton.clicked.connect(self._prepare_new_register_map)
        self.previewMapChangesButton.clicked.connect(self._preview_register_map_changes)
        map_actions.addWidget(self.addVariableButton)
        map_actions.addWidget(self.deleteVariableButton)
        map_actions.addWidget(self.resetMapButton)
        map_actions.addStretch(1)
        map_layout.addLayout(map_actions)
        root.addWidget(map_group, 1)

        self.statusLabel = QLabel("")
        self.statusLabel.setObjectName("modbusProfileStatusLabel")
        root.addWidget(self.statusLabel)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.saveButton = QPushButton("Save")
        self.saveButton.setObjectName("modbusProfileSaveButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusProfileCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.saveButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def set_profile(
        self,
        *,
        device_id: str,
        metadata: ModbusOperationMetadata,
        register_map: ModbusRegisterMap,
        order: str,
        register_maps: tuple[ModbusRegisterMapCatalogEntry, ...] = (),
        register_map_id: str = "",
        register_map_version: str = "",
    ) -> None:
        self._last_order = order
        self.deviceIdLineEdit.setText(device_id)
        self.deviceModelLineEdit.setText(metadata.device_model)
        self.tubeModelLineEdit.setText(metadata.tube_model)
        self.transmitterModelLineEdit.setText(metadata.transmitter_model)
        self._catalog_entries = {
            (entry.register_map_id, entry.version): entry for entry in register_maps
        }
        self.registerMapCombo.blockSignals(True)
        self.registerMapCombo.clear()
        self.registerMapCombo.addItem("Create new register list...", None)
        for entry in register_maps:
            self.registerMapCombo.addItem(
                entry.label,
                _register_map_combo_key(entry.register_map_id, entry.version),
            )
        selected_key = (register_map_id, register_map_version)
        selected_index = self.registerMapCombo.findData(
            _register_map_combo_key(*selected_key)
        )
        self.registerMapCombo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.registerMapCombo.blockSignals(False)
        if selected_index >= 0:
            self._apply_catalog_entry(self._catalog_entries[selected_key])
        else:
            self._initial_register_map = register_map
            self._populate_register_map(register_map)
            self._set_register_map_identity(
                register_map_id=suggest_custom_register_map_id(register_map),
                display_name=register_map.name,
                version="1.0.0",
                editable=True,
            )
        self.statusLabel.setText("")

    def device_id(self) -> str:
        return self.deviceIdLineEdit.text().strip()

    def metadata(self) -> ModbusOperationMetadata:
        return ModbusOperationMetadata(
            device_model=self.deviceModelLineEdit.text().strip(),
            tube_model=self.tubeModelLineEdit.text().strip(),
            transmitter_model=self.transmitterModelLineEdit.text().strip(),
        )

    def register_map(self, *, order: str | None = None) -> ModbusRegisterMap:
        return self._register_map_from_ui(order=order or self._last_order)

    def register_map_binding(self) -> tuple[str | None, str | None, str | None]:
        register_map_id = self.registerMapIdLineEdit.text().strip()
        version = self.registerMapVersionLineEdit.text().strip()
        display_name = self.registerMapNameLineEdit.text().strip()
        if not register_map_id:
            return None, None, display_name or None
        return register_map_id or None, version or None, display_name or None

    def set_status(self, message: str) -> None:
        self.statusLabel.setText(message)

    def _register_map_selection_changed(self) -> None:
        key = self.registerMapCombo.currentData()
        if not isinstance(key, str) or "@" not in key:
            self._prepare_new_register_map()
            return
        register_map_id, version = key.split("@", 1)
        entry = self._catalog_entries.get((register_map_id, version))
        if entry is not None:
            self._apply_catalog_entry(entry)

    def _apply_catalog_entry(self, entry: ModbusRegisterMapCatalogEntry) -> None:
        self._last_order = _orders_to_order(entry.register_map)
        self._initial_register_map = entry.register_map
        self._populate_register_map(entry.register_map)
        self._set_register_map_identity(
            register_map_id=entry.register_map_id,
            display_name=entry.display_name,
            version=entry.version,
            editable=False,
        )
        self.set_status("")

    def _prepare_new_register_map(self) -> None:
        current_map = self._register_map_from_ui(order=self._last_order)
        self.registerMapCombo.blockSignals(True)
        self.registerMapCombo.setCurrentIndex(0)
        self.registerMapCombo.blockSignals(False)
        self._initial_register_map = current_map
        self._set_register_map_identity(
            register_map_id=suggest_custom_register_map_id(current_map),
            display_name="",
            version="1.0.0",
            editable=True,
        )
        self.set_status("")

    def _set_register_map_identity(
        self,
        *,
        register_map_id: str,
        display_name: str,
        version: str,
        editable: bool,
    ) -> None:
        self.registerMapIdLineEdit.setText(register_map_id)
        self.registerMapNameLineEdit.setText(display_name)
        self.registerMapVersionLineEdit.setText(version)
        self.registerMapIdLineEdit.setReadOnly(not editable)
        self.registerMapNameLineEdit.setReadOnly(not editable)
        self.registerMapVersionLineEdit.setReadOnly(not editable)

    def _preview_register_map_changes(self) -> None:
        self.set_status(self.register_map_change_summary())

    def register_map_change_summary(self) -> str:
        if self._initial_register_map is None:
            return "Register list preview: no baseline."
        current = self._register_map_from_ui(order=self._last_order)
        baseline_by_name = {
            item.name: _editable_register_signature(item)
            for item in self._initial_register_map.registers
        }
        current_by_name = {
            item.name: _editable_register_signature(item)
            for item in current.registers
        }
        added = current_by_name.keys() - baseline_by_name.keys()
        removed = baseline_by_name.keys() - current_by_name.keys()
        modified = {
            name
            for name in current_by_name.keys() & baseline_by_name.keys()
            if current_by_name[name] != baseline_by_name[name]
        }
        return (
            "Register list preview: "
            f"added={len(added)}, removed={len(removed)}, modified={len(modified)}."
        )

    def _populate_register_map(self, register_map: ModbusRegisterMap) -> None:
        registers = list(register_map.registers)
        self.mapTable.setRowCount(len(registers))
        for row, register in enumerate(registers):
            self._populate_register_map_row(
                row,
                register,
                variable_name_editable=_is_custom_ui_register(register),
            )
        self._refresh_map_edit_state()

    def _populate_register_map_row(
        self,
        row: int,
        register: ModbusRegister,
        *,
        variable_name_editable: bool,
    ) -> None:
        self._set_map_text(row, 0, register.name, editable=variable_name_editable)
        kind_combo = QComboBox()
        kind_combo.addItems([kind.value for kind in RegisterKind])
        kind_combo.setCurrentText(register.kind.value)
        self.mapTable.setCellWidget(row, 1, kind_combo)
        self._set_map_text(row, 2, str(register.address))
        self._set_map_text(row, 3, str(register.word_count))
        type_combo = QComboBox()
        type_combo.addItems([data_type.value for data_type in ModbusDataType])
        type_combo.setCurrentText(register.data_type.value)
        self.mapTable.setCellWidget(row, 4, type_combo)
        self._set_map_text(row, 5, _format_value(register.scale))
        self._set_map_text(row, 6, register.unit or "")
        writable_combo = QComboBox()
        writable_combo.addItems(["false", "true"])
        writable_combo.setCurrentText("true" if register.writable else "false")
        self.mapTable.setCellWidget(row, 7, writable_combo)

    def _add_register_row(self) -> None:
        row = self.mapTable.rowCount()
        self.mapTable.insertRow(row)
        self._populate_register_map_row(
            row,
            ModbusRegister(
                name=f"custom_{row + 1}",
                kind=RegisterKind.HOLDING,
                address=0,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                writable=False,
                metadata={"source": "modbus_module_ui_custom"},
            ),
            variable_name_editable=True,
        )
        self.mapTable.selectRow(row)
        self._refresh_map_edit_state()

    def _delete_selected_register_row(self) -> None:
        if self.mapTable.rowCount() <= 1:
            self.set_status("Keep at least one variable.")
            return
        row = self.mapTable.currentRow()
        if row < 0:
            selected = self.mapTable.selectedIndexes()
            if selected:
                row = min(index.row() for index in selected)
        if row < 0 or row >= self.mapTable.rowCount():
            self.set_status("Select a variable row first.")
            return
        self.mapTable.removeRow(row)
        next_row = min(row, self.mapTable.rowCount() - 1)
        if next_row >= 0:
            self.mapTable.selectRow(next_row)
        self._refresh_map_edit_state()

    def _reset_register_map(self) -> None:
        if self._initial_register_map is None:
            return
        self._populate_register_map(self._initial_register_map)

    def _move_register_row(self, source: int, target: int) -> None:
        if source < 0 or source >= self.mapTable.rowCount():
            return
        if target < 0 or target >= self.mapTable.rowCount():
            return
        if source == target:
            return
        rows = [
            {
                "register": self._register_from_row(row),
                "name_editable": self._is_variable_name_editable(row),
            }
            for row in range(self.mapTable.rowCount())
        ]
        moved = rows.pop(source)
        rows.insert(target, moved)
        self.mapTable.setRowCount(len(rows))
        for row, snapshot in enumerate(rows):
            self._populate_register_map_row(
                row,
                snapshot["register"],
                variable_name_editable=bool(snapshot["name_editable"]),
            )
        self.mapTable.selectRow(target)
        self._refresh_map_edit_state()

    def _set_map_text(
        self,
        row: int,
        column: int,
        value: str,
        *,
        editable: bool = True,
    ) -> None:
        item = QTableWidgetItem(value)
        item.setData(Qt.ItemDataRole.UserRole, editable)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.mapTable.setItem(row, column, item)

    def _register_from_row(self, row: int) -> ModbusRegister:
        name = _required_table_text(self.mapTable, row, 0)
        baseline = None
        if self._initial_register_map is not None:
            baseline = next(
                (
                    item
                    for item in self._initial_register_map.registers
                    if item.name == name
                ),
                None,
            )
        return ModbusRegister(
            name=name,
            kind=RegisterKind(_combo_text(self.mapTable, row, 1)),
            address=int(_required_table_text(self.mapTable, row, 2)),
            word_count=int(_required_table_text(self.mapTable, row, 3)),
            data_type=ModbusDataType(_combo_text(self.mapTable, row, 4)),
            writable=_combo_text(self.mapTable, row, 7) == "true",
            scale=float(_required_table_text(self.mapTable, row, 5)),
            unit=_table_text(self.mapTable, row, 6) or None,
            word_order=baseline.word_order if baseline is not None else WordOrder.BIG,
            byte_order=baseline.byte_order if baseline is not None else ByteOrder.BIG,
            minimum=baseline.minimum if baseline is not None else None,
            maximum=baseline.maximum if baseline is not None else None,
            description=baseline.description if baseline is not None else None,
            metadata=(
                dict(baseline.metadata)
                if baseline is not None
                else {"source": "modbus_module_ui_custom"}
            ),
        )

    def _register_map_from_ui(self, *, order: str) -> ModbusRegisterMap:
        registers: list[ModbusRegister] = []
        word_order, byte_order = _order_to_modbus_orders(order)
        seen_names: set[str] = set()
        for row in self._visual_rows():
            name = _required_table_text(self.mapTable, row, 0)
            if name in seen_names:
                raise ValueError(f"Duplicate variable name: {name}.")
            seen_names.add(name)
            register = self._register_from_row(row)
            if register.address < 0:
                raise ValueError(f"Address must be non-negative for {name}.")
            if register.word_count < 1:
                raise ValueError(f"Words must be at least 1 for {name}.")
            registers.append(
                ModbusRegister(
                    name=register.name,
                    kind=register.kind,
                    address=register.address,
                    word_count=register.word_count,
                    data_type=register.data_type,
                    writable=register.writable,
                    scale=register.scale,
                    unit=register.unit,
                    word_order=word_order,
                    byte_order=byte_order,
                    minimum=register.minimum,
                    maximum=register.maximum,
                    description=register.description,
                    metadata=register.metadata,
                )
            )
        return ModbusRegisterMap(
            name="modbus-module-ui-map",
            version=datetime.now().strftime("%Y%m%d%H%M%S"),
            registers=tuple(registers),
        )

    def _visual_rows(self) -> tuple[int, ...]:
        header = self.mapTable.verticalHeader()
        return tuple(
            header.logicalIndex(visual_row)
            for visual_row in range(self.mapTable.rowCount())
        )

    def _is_variable_name_editable(self, row: int) -> bool:
        item = self.mapTable.item(row, 0)
        if item is None:
            return True
        return bool(item.flags() & Qt.ItemFlag.ItemIsEditable)

    def _refresh_map_edit_state(self) -> None:
        self.deleteVariableButton.setEnabled(self.mapTable.rowCount() > 1)
        for row in range(self.mapTable.rowCount()):
            for column in (1, 4, 7):
                widget = self.mapTable.cellWidget(row, column)
                if widget is not None:
                    widget.setEnabled(True)
            for column in (0, 2, 3, 5, 6):
                item = self.mapTable.item(row, column)
                if item is None:
                    continue
                editable = item.data(Qt.ItemDataRole.UserRole)
                if editable is None or bool(editable):
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)


class ZeroCalibrationDialog(QDialog):
    """Operator-facing zero calibration dialog."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zero Calibration")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 660, 520, 480, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("modbusZeroCalibrationStatusLabel")
        root.addWidget(self.statusLabel)

        snapshot_group = QGroupBox("Pre-calibration Snapshot")
        snapshot_layout = QVBoxLayout(snapshot_group)
        self.snapshotTable = QTableWidget(0, 5)
        self.snapshotTable.setObjectName("modbusZeroCalibrationSnapshotTable")
        self.snapshotTable.setHorizontalHeaderLabels(
            ["Capture", "Variable", "Kind", "Address", "Type"]
        )
        self.snapshotTable.verticalHeader().setVisible(False)
        self.snapshotTable.setAlternatingRowColors(True)
        self.snapshotTable.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.snapshotTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.snapshotTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        snapshot_layout.addWidget(self.snapshotTable)
        root.addWidget(snapshot_group, 2)

        self.resultTable = QTableWidget(2, 4)
        self.resultTable.setObjectName("modbusZeroCalibrationResultTable")
        self.resultTable.setHorizontalHeaderLabels(
            ["Variable", "Before", "After", "Change"]
        )
        self.resultTable.verticalHeader().setVisible(False)
        self.resultTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.resultTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        for row, variable in enumerate(("zero_offset", "delta_t")):
            self._set_table_text(row, 0, variable)
            for column in (1, 2, 3):
                self._set_table_text(row, column, "")
        root.addWidget(self.resultTable, 1)

        self.snapshotResultTable = QTableWidget(0, 2)
        self.snapshotResultTable.setObjectName("modbusZeroCalibrationSnapshotResultTable")
        self.snapshotResultTable.setHorizontalHeaderLabels(["Snapshot Variable", "Value"])
        self.snapshotResultTable.verticalHeader().setVisible(False)
        self.snapshotResultTable.setAlternatingRowColors(True)
        self.snapshotResultTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.snapshotResultTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.snapshotResultTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.snapshotResultTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        root.addWidget(self.snapshotResultTable, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.startButton = QPushButton("Start")
        self.startButton.setObjectName("modbusZeroCalibrationStartButton")
        self.saveConfigButton = QPushButton("Save Config")
        self.saveConfigButton.setObjectName("modbusZeroCalibrationSaveConfigButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusZeroCalibrationCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.startButton)
        buttons.addWidget(self.saveConfigButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def set_snapshot_variables(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...] | None = None,
    ) -> None:
        if selected_names is None:
            selected_names = self.selected_snapshot_variable_names()
        if not selected_names:
            selected_names = _default_zero_snapshot_names(registers)
        selected = set(selected_names)
        self.snapshotTable.setRowCount(len(registers))
        for row, register in enumerate(registers):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if register.name in selected
                else Qt.CheckState.Unchecked
            )
            self.snapshotTable.setItem(row, 0, check_item)
            values = (
                register.name,
                register.kind.value,
                str(register.address),
                register.data_type.value,
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.snapshotTable.setItem(row, offset, item)

    def selected_snapshot_variable_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.snapshotTable.rowCount()):
            check_item = self.snapshotTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            name = _table_text(self.snapshotTable, row, 1)
            if name:
                names.append(name)
        return tuple(names)

    def capture_settings(self) -> dict[str, object]:
        return {
            "snapshot_variable_names": self.selected_snapshot_variable_names(),
        }

    def apply_configuration(self, settings: dict[str, object]) -> None:
        names = settings.get("snapshot_variable_names")
        if not isinstance(names, (list, tuple)):
            return
        available = {
            _table_text(self.snapshotTable, row, 1)
            for row in range(self.snapshotTable.rowCount())
        }
        selected = {
            str(name)
            for name in names
            if str(name) in available
        }
        for row in range(self.snapshotTable.rowCount()):
            check_item = self.snapshotTable.item(row, 0)
            if check_item is None:
                continue
            name = _table_text(self.snapshotTable, row, 1)
            check_item.setCheckState(
                Qt.CheckState.Checked
                if name in selected
                else Qt.CheckState.Unchecked
            )

    def set_running(self) -> None:
        self.startButton.setEnabled(False)
        self.saveConfigButton.setEnabled(False)
        self.snapshotTable.setEnabled(False)
        self.statusLabel.setText("Running...")

    def set_ready(self, *, connected: bool) -> None:
        self.startButton.setEnabled(connected)
        self.saveConfigButton.setEnabled(True)
        self.snapshotTable.setEnabled(True)
        if connected and self.statusLabel.text() == "Running...":
            self.statusLabel.setText("Ready")

    def set_result(self, result: ModbusZeroCalibrationResult) -> None:
        record = result.record
        self.statusLabel.setText(
            f"Completed {result.run_id}; coil returned to 0: {record.completed}"
        )
        values = (
            (
                record.before.zero_offset,
                record.after.zero_offset,
                record.zero_offset_change,
            ),
            (
                record.before.delta_t,
                record.after.delta_t,
                record.delta_t_change,
            ),
        )
        for row, row_values in enumerate(values):
            for offset, value in enumerate(row_values, start=1):
                self._set_table_text(row, offset, _format_value(value))
        self.set_snapshot_result(result.pre_snapshot)
        self.snapshotTable.setEnabled(True)
        self.startButton.setEnabled(True)
        self.saveConfigButton.setEnabled(True)

    def set_snapshot_result(self, snapshot: dict[str, object]) -> None:
        self.snapshotResultTable.setRowCount(len(snapshot))
        for row, (name, value) in enumerate(snapshot.items()):
            for column, text in enumerate((name, _format_value(value))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.snapshotResultTable.setItem(row, column, item)

    def set_error(self, message: str) -> None:
        self.statusLabel.setText(f"Failed: {message}")
        self.snapshotTable.setEnabled(True)
        self.startButton.setEnabled(True)
        self.saveConfigButton.setEnabled(True)

    def _set_table_text(self, row: int, column: int, value: str) -> None:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.resultTable.setItem(row, column, item)


class KFactorCalibrationDialog(QDialog):
    """Operator-facing K factor simple calibration dialog."""

    cancelRequested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("K Factor Calibration")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 780, 640, 600, 460)
        self._capture: ModbusKFactorSimpleCapture | None = None
        self._result: ModbusKFactorSimpleResult | None = None
        self._build_ui()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self.cancelRequested.emit()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("modbusKFactorStatusLabel")
        root.addWidget(self.statusLabel)

        settings_group = QGroupBox("Simple Mode")
        settings = QFormLayout(settings_group)
        self.modeCombo = QComboBox()
        self.modeCombo.setObjectName("modbusKFactorModeCombo")
        self.modeCombo.addItem("Simple", "simple")
        self.modeCombo.addItem("Advanced (reserved)", "advanced")
        self.modeCombo.model().item(1).setEnabled(False)
        settings.addRow("Mode", self.modeCombo)
        self.flowRateCombo = QComboBox()
        self.flowRateCombo.setObjectName("modbusKFactorFlowRateCombo")
        settings.addRow("Flow Rate", self.flowRateCombo)
        self.flowAccCombo = QComboBox()
        self.flowAccCombo.setObjectName("modbusKFactorFlowAccCombo")
        settings.addRow("Flow Acc", self.flowAccCombo)
        self.kFactorCombo = QComboBox()
        self.kFactorCombo.setObjectName("modbusKFactorParameterCombo")
        settings.addRow("K Factor", self.kFactorCombo)
        self.pollIntervalSpinBox = QDoubleSpinBox()
        self.pollIntervalSpinBox.setObjectName("modbusKFactorPollIntervalSpinBox")
        self.pollIntervalSpinBox.setRange(0.05, 30.0)
        self.pollIntervalSpinBox.setDecimals(2)
        self.pollIntervalSpinBox.setSingleStep(0.1)
        self.pollIntervalSpinBox.setValue(1.0)
        settings.addRow("Poll Interval (s)", self.pollIntervalSpinBox)
        self.standardMassSpinBox = _float_input(12.6)
        self.standardMassSpinBox.setObjectName("modbusKFactorStandardMassSpinBox")
        self.standardMassSpinBox.setMinimum(0.000001)
        settings.addRow("Standard Mass", self.standardMassSpinBox)
        self.saveHistoryCheckBox = QCheckBox("Record test records")
        self.saveHistoryCheckBox.setObjectName("modbusKFactorSaveHistoryCheckBox")
        self.saveHistoryCheckBox.setChecked(True)
        settings.addRow("", self.saveHistoryCheckBox)
        self.writeToDeviceCheckBox = QCheckBox("Write K1 to device after calculation")
        self.writeToDeviceCheckBox.setObjectName("modbusKFactorWriteToDeviceCheckBox")
        settings.addRow("", self.writeToDeviceCheckBox)
        root.addWidget(settings_group, 1)

        snapshot_group = QGroupBox("Pre-calibration Snapshot")
        snapshot_layout = QVBoxLayout(snapshot_group)
        self.snapshotTable = QTableWidget(0, 5)
        self.snapshotTable.setObjectName("modbusKFactorSnapshotTable")
        self.snapshotTable.setHorizontalHeaderLabels(
            ["Capture", "Variable", "Kind", "Address", "Type"]
        )
        self.snapshotTable.verticalHeader().setVisible(False)
        self.snapshotTable.setAlternatingRowColors(True)
        self.snapshotTable.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.snapshotTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.snapshotTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        snapshot_layout.addWidget(self.snapshotTable)
        root.addWidget(snapshot_group, 2)

        self.resultTable = QTableWidget(0, 2)
        self.resultTable.setObjectName("modbusKFactorResultTable")
        self.resultTable.setHorizontalHeaderLabels(["Metric", "Value"])
        self.resultTable.verticalHeader().setVisible(False)
        self.resultTable.setAlternatingRowColors(True)
        self.resultTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.resultTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.resultTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.resultTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        root.addWidget(self.resultTable, 2)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.startButton = QPushButton("Start")
        self.startButton.setObjectName("modbusKFactorStartButton")
        self.calculateButton = QPushButton("Calculate")
        self.calculateButton.setObjectName("modbusKFactorCalculateButton")
        self.calculateButton.setEnabled(False)
        self.writeButton = QPushButton("Write K1")
        self.writeButton.setObjectName("modbusKFactorWriteButton")
        self.writeButton.setEnabled(False)
        self.saveConfigButton = QPushButton("Save Config")
        self.saveConfigButton.setObjectName("modbusKFactorSaveConfigButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusKFactorCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.startButton)
        buttons.addWidget(self.calculateButton)
        buttons.addWidget(self.writeButton)
        buttons.addWidget(self.saveConfigButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def set_registers(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...] | None = None,
    ) -> None:
        names = tuple(register.name for register in registers)
        self._set_combo_items(self.flowRateCombo, names, ("flow_rate", "mass_rate", "mass_flow"))
        self._set_combo_items(self.flowAccCombo, names, ("flow_acc", "mass_acc"))
        self._set_combo_items(self.kFactorCombo, names, ("k_factor",))
        if selected_names is None:
            selected_names = self.selected_snapshot_variable_names()
        if not selected_names:
            selected_names = _default_zero_snapshot_names(registers)
        selected = set(selected_names)
        self.snapshotTable.setRowCount(len(registers))
        for row, register in enumerate(registers):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if register.name in selected
                else Qt.CheckState.Unchecked
            )
            self.snapshotTable.setItem(row, 0, check_item)
            values = (
                register.name,
                register.kind.value,
                str(register.address),
                register.data_type.value,
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.snapshotTable.setItem(row, offset, item)

    def selected_snapshot_variable_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.snapshotTable.rowCount()):
            check_item = self.snapshotTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            name = _table_text(self.snapshotTable, row, 1)
            if name:
                names.append(name)
        return tuple(names)

    def set_ready(self, *, connected: bool) -> None:
        self.startButton.setEnabled(connected)
        self.calculateButton.setEnabled(connected and self._capture is not None)
        self.writeButton.setEnabled(connected and self._result is not None)
        self.saveConfigButton.setEnabled(True)
        self._set_inputs_enabled(connected)
        if connected and self.statusLabel.text() == "Running...":
            self.statusLabel.setText("Ready")

    def set_running(self) -> None:
        self.statusLabel.setText("Running...")
        self.startButton.setEnabled(False)
        self.calculateButton.setEnabled(False)
        self.writeButton.setEnabled(False)
        self.saveConfigButton.setEnabled(False)
        self._set_inputs_enabled(False)

    def set_canceling(self) -> None:
        self.statusLabel.setText("Canceling...")
        self.startButton.setEnabled(False)
        self.calculateButton.setEnabled(False)
        self.writeButton.setEnabled(False)
        self.saveConfigButton.setEnabled(False)
        self._set_inputs_enabled(False)

    def set_captured(self, capture: ModbusKFactorSimpleCapture) -> None:
        self._capture = capture
        self._result = None
        self.statusLabel.setText(
            "Captured flow segment. Enter standard mass and click Calculate."
        )
        self.calculateButton.setEnabled(True)
        self.writeButton.setEnabled(False)
        self.saveConfigButton.setEnabled(True)
        self._set_inputs_enabled(True)
        self._populate_rows(
            [
                ("run_id", capture.run_id),
                ("m1", capture.mass_acc_before),
                ("m2", capture.mass_acc_after),
                ("delta_m", capture.measured_mass_delta),
                ("K0", capture.current_k_factor),
                ("t1", _format_datetime(capture.segment.started_at)),
                ("t2", _format_datetime(capture.segment.ended_at)),
                ("duration_s", capture.segment.duration_s),
                ("v1", capture.segment.instant_flow),
                ("flow_rate_source", capture.segment.flow_rate_source),
            ]
        )

    def set_result(self, result: ModbusKFactorSimpleResult) -> None:
        self._result = result
        self.statusLabel.setText(f"Calculated {result.run_id}")
        self.calculateButton.setEnabled(True)
        self.writeButton.setEnabled(True)
        self.saveConfigButton.setEnabled(True)
        self._set_inputs_enabled(True)
        self._populate_result(result)

    def set_write_result(self, result: ModbusKFactorSimpleResult) -> None:
        self._result = result
        self.statusLabel.setText(
            f"Write {result.write_status}; verified: {result.write_verified}"
        )
        self.writeButton.setEnabled(False)
        self.saveConfigButton.setEnabled(True)
        self._set_inputs_enabled(True)
        self._populate_result(result)

    def set_error(self, message: str) -> None:
        self.statusLabel.setText(f"Failed: {message}")
        self.startButton.setEnabled(True)
        self.calculateButton.setEnabled(self._capture is not None)
        self.writeButton.setEnabled(self._result is not None)
        self.saveConfigButton.setEnabled(True)
        self._set_inputs_enabled(True)

    def capture_settings(self) -> dict[str, object]:
        return {
            "snapshot_variable_names": self.selected_snapshot_variable_names(),
            "flow_rate_parameter": self.flowRateCombo.currentText(),
            "flow_acc_parameter": self.flowAccCombo.currentText(),
            "k_factor_parameter": self.kFactorCombo.currentText(),
            "poll_interval_s": self.pollIntervalSpinBox.value(),
        }

    def apply_configuration(self, settings: dict[str, object]) -> None:
        self._set_combo_text(self.flowRateCombo, settings.get("flow_rate_parameter"))
        self._set_combo_text(self.flowAccCombo, settings.get("flow_acc_parameter"))
        self._set_combo_text(self.kFactorCombo, settings.get("k_factor_parameter"))
        poll_interval = settings.get("poll_interval_s")
        if isinstance(poll_interval, (int, float)):
            self.pollIntervalSpinBox.setValue(float(poll_interval))
        snapshot_names = settings.get("snapshot_variable_names")
        if isinstance(snapshot_names, (list, tuple)):
            selected = {str(name) for name in snapshot_names}
            for row in range(self.snapshotTable.rowCount()):
                check_item = self.snapshotTable.item(row, 0)
                if check_item is None:
                    continue
                name = _table_text(self.snapshotTable, row, 1)
                check_item.setCheckState(
                    Qt.CheckState.Checked
                    if name in selected
                    else Qt.CheckState.Unchecked
                )

    def standard_mass(self) -> float:
        return self.standardMassSpinBox.value()

    def save_history(self) -> bool:
        return self.saveHistoryCheckBox.isChecked()

    def should_write_to_device(self) -> bool:
        return self.writeToDeviceCheckBox.isChecked()

    def current_capture(self) -> ModbusKFactorSimpleCapture | None:
        return self._capture

    def current_result(self) -> ModbusKFactorSimpleResult | None:
        return self._result

    def _populate_result(self, result: ModbusKFactorSimpleResult) -> None:
        rows = [
            ("run_id", result.run_id),
            ("m1", result.mass_acc_before),
            ("m2", result.mass_acc_after),
            ("delta_m", result.measured_mass_delta),
            ("standard_mass", result.standard_mass),
            ("K0", result.current_k_factor),
            ("K1", result.corrected_k_factor),
            ("v_mean", result.mean_flow),
            ("v1", result.instant_flow),
            ("flow_rate_source", result.flow_rate_source),
            ("t1", _format_datetime(result.flow_started_at)),
            ("t2", _format_datetime(result.flow_ended_at)),
            ("duration_s", result.duration_s),
            ("history_saved", result.history_saved),
            ("write_status", result.write_status),
            ("write_verified", result.write_verified),
        ]
        if result.readback_k_factor is not None:
            rows.append(("readback_k_factor", result.readback_k_factor))
        self._populate_rows(rows)

    def _populate_rows(self, rows: list[tuple[str, object]]) -> None:
        self.resultTable.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            value_text = (
                _format_k_value(value)
                if _is_k_metric_name(name)
                else _format_value(value)
            )
            for column, text in enumerate((name, value_text)):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.resultTable.setItem(row, column, item)

    def _set_combo_items(
        self,
        combo: QComboBox,
        names: tuple[str, ...],
        preferred: tuple[str, ...],
    ) -> None:
        current = combo.currentText()
        combo.clear()
        combo.addItems(names)
        target = current if current in names else ""
        if not target:
            target = next((name for name in preferred if name in names), names[0] if names else "")
        if target:
            combo.setCurrentText(target)

    def _set_combo_text(self, combo: QComboBox, value: object) -> None:
        if not isinstance(value, str):
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_inputs_enabled(self, enabled: bool) -> None:
        for widget in (
            self.modeCombo,
            self.flowRateCombo,
            self.flowAccCombo,
            self.kFactorCombo,
            self.pollIntervalSpinBox,
            self.standardMassSpinBox,
            self.saveHistoryCheckBox,
            self.writeToDeviceCheckBox,
            self.snapshotTable,
        ):
            widget.setEnabled(enabled)


class RepeatabilityAddTrialDialog(QDialog):
    """Select a flow point before appending an extra repeatability trial."""

    def __init__(
        self,
        flow_points: tuple[float, ...],
        *,
        default_flow_point: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Trial")
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        form = QFormLayout()
        self.flowPointCombo = QComboBox()
        self.flowPointCombo.setObjectName("modbusRepeatabilityAddTrialFlowPointCombo")
        for flow_point in flow_points:
            self.flowPointCombo.addItem(_format_value(flow_point), flow_point)
        if default_flow_point is not None:
            index = self.flowPointCombo.findData(default_flow_point)
            if index >= 0:
                self.flowPointCombo.setCurrentIndex(index)
        form.addRow("Flow Point", self.flowPointCombo)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_flow_point(self) -> float:
        return float(self.flowPointCombo.currentData())


class RepeatabilityConfigurationDialog(QDialog):
    """Configuration editor for one error/repeatability operation."""

    settingsChanged = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Error And Repeatability Configuration")
        self.setModal(True)
        self._snapshot_registers: tuple[ModbusRegister, ...] = ()
        self._snapshot_variable_names: tuple[str, ...] = ()
        self._sample_variable_names: tuple[str, ...] = ()
        self._config_enabled = True
        _fit_dialog_to_screen(self, 560, 500, 460, 360)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        settings_group = QGroupBox("Configuration")
        settings = QFormLayout(settings_group)
        self.modeCombo = QComboBox()
        self.modeCombo.setObjectName("modbusRepeatabilityModeCombo")
        self.modeCombo.addItem("Three Flow Ranges", "three_point")
        self.modeCombo.addItem("Single Flow Range", "single_point")
        self.modeCombo.addItem("Advanced (reserved)", "advanced")
        self.modeCombo.model().item(2).setEnabled(False)
        self.modeCombo.currentIndexChanged.connect(self._mode_changed)
        settings.addRow("Mode", self.modeCombo)

        self.flowRateCombo = QComboBox()
        self.flowRateCombo.setObjectName("modbusRepeatabilityFlowRateCombo")
        self.flowRateCombo.currentTextChanged.connect(
            lambda _text: self._update_sample_variables_button_text()
        )
        settings.addRow("Flow Rate", self.flowRateCombo)
        self.flowAccCombo = QComboBox()
        self.flowAccCombo.setObjectName("modbusRepeatabilityFlowAccCombo")
        settings.addRow("Flow Acc", self.flowAccCombo)
        self.kFactorCombo = QComboBox()
        self.kFactorCombo.setObjectName("modbusRepeatabilityKFactorCombo")
        settings.addRow("K Factor", self.kFactorCombo)

        self.pollIntervalSpinBox = QDoubleSpinBox()
        self.pollIntervalSpinBox.setObjectName(
            "modbusRepeatabilityPollIntervalSpinBox"
        )
        self.pollIntervalSpinBox.setRange(0.05, 30.0)
        self.pollIntervalSpinBox.setDecimals(2)
        self.pollIntervalSpinBox.setSingleStep(0.1)
        self.pollIntervalSpinBox.setValue(1.0)
        self.pollIntervalSpinBox.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        settings.addRow("Poll Interval (s)", self.pollIntervalSpinBox)
        self.instantFlowOffsetSpinBox = QDoubleSpinBox()
        self.instantFlowOffsetSpinBox.setObjectName(
            "modbusRepeatabilityInstantFlowOffsetSpinBox"
        )
        self.instantFlowOffsetSpinBox.setRange(0.0, 300.0)
        self.instantFlowOffsetSpinBox.setDecimals(2)
        self.instantFlowOffsetSpinBox.setSingleStep(0.1)
        self.instantFlowOffsetSpinBox.setValue(3.0)
        self.instantFlowOffsetSpinBox.valueChanged.connect(
            lambda _value: self.settingsChanged.emit()
        )
        settings.addRow("Instant Flow Offset (s)", self.instantFlowOffsetSpinBox)

        self.operationNotesTextEdit = QTextEdit()
        self.operationNotesTextEdit.setObjectName(
            "modbusRepeatabilityOperationNotesTextEdit"
        )
        self.operationNotesTextEdit.setAcceptRichText(False)
        self.operationNotesTextEdit.setPlaceholderText("Enter this operation note")
        self.operationNotesTextEdit.setFixedHeight(70)
        self.operationNotesTextEdit.textChanged.connect(self.settingsChanged.emit)
        settings.addRow("Operation Note", self.operationNotesTextEdit)

        self.flowPointSpinBoxes: list[QDoubleSpinBox] = []
        for index, value in enumerate((600.0, 300.0, 100.0), start=1):
            spin = _float_input(value)
            spin.setObjectName(f"modbusRepeatabilityFlowPoint{index}SpinBox")
            spin.setMinimum(0.0)
            spin.valueChanged.connect(lambda _value: self.settingsChanged.emit())
            self.flowPointSpinBoxes.append(spin)
            settings.addRow(f"Flow Point {index}", spin)

        self.snapshotButton = QPushButton("Pre/Post Snapshot...")
        self.snapshotButton.setObjectName("modbusRepeatabilitySnapshotButton")
        self.snapshotButton.clicked.connect(self._open_snapshot_dialog)
        settings.addRow("Pre/Post Snapshot", self.snapshotButton)
        self.sampleVariablesButton = QPushButton("Default Trial Samples...")
        self.sampleVariablesButton.setObjectName(
            "modbusRepeatabilitySampleVariablesButton"
        )
        self.sampleVariablesButton.clicked.connect(self._open_sample_variables_dialog)
        settings.addRow("Trial Sample Defaults", self.sampleVariablesButton)
        self.saveHistoryCheckBox = QCheckBox("Record test records")
        self.saveHistoryCheckBox.setObjectName(
            "modbusRepeatabilitySaveHistoryCheckBox"
        )
        self.saveHistoryCheckBox.setChecked(True)
        settings.addRow("", self.saveHistoryCheckBox)
        self.recordFlowSamplesCheckBox = QCheckBox("Record all flow samples")
        self.recordFlowSamplesCheckBox.setObjectName(
            "modbusRepeatabilityRecordFlowSamplesCheckBox"
        )
        self.recordFlowSamplesCheckBox.setChecked(False)
        self.recordFlowSamplesCheckBox.toggled.connect(
            lambda _checked: self.settingsChanged.emit()
        )
        settings.addRow("", self.recordFlowSamplesCheckBox)
        root.addWidget(settings_group, 1)

        self.statusLabel = QLabel("")
        self.statusLabel.setObjectName("modbusRepeatabilityConfigurationStatusLabel")
        root.addWidget(self.statusLabel)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.saveConfigButton = QPushButton("Save Config")
        self.saveConfigButton.setObjectName("modbusRepeatabilitySaveConfigButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusRepeatabilityConfigCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.saveConfigButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def set_registers(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...] | None = None,
        sample_selected_names: tuple[str, ...] | None = None,
    ) -> None:
        names = tuple(register.name for register in registers)
        self._set_combo_items(
            self.flowRateCombo,
            names,
            ("flow_rate", "mass_rate", "mass_flow"),
        )
        self._set_combo_items(self.flowAccCombo, names, ("flow_acc", "mass_acc"))
        self._set_combo_items(self.kFactorCombo, names, ("k_factor",))
        if selected_names is None:
            selected_names = self._snapshot_variable_names
        if sample_selected_names is None:
            sample_selected_names = self._sample_variable_names
        if not selected_names:
            selected_names = _default_zero_snapshot_names(registers)
        available = {register.name for register in registers}
        self._snapshot_registers = registers
        self._snapshot_variable_names = tuple(
            name for name in selected_names if name in available
        )
        self._sample_variable_names = tuple(
            name for name in sample_selected_names if name in available
        )
        self._update_snapshot_button_text()
        self._update_sample_variables_button_text()

    def selected_snapshot_variable_names(self) -> tuple[str, ...]:
        return self._snapshot_variable_names

    def selected_sample_variable_names(self) -> tuple[str, ...]:
        flow_rate_name = self.flowRateCombo.currentText()
        return tuple(
            name for name in self._sample_variable_names if name != flow_rate_name
        )

    def set_sample_variable_names(self, names: tuple[str, ...]) -> None:
        available = {register.name for register in self._snapshot_registers}
        self._sample_variable_names = tuple(
            name for name in names if name in available
        )
        self._update_sample_variables_button_text()
        self.settingsChanged.emit()

    def capture_settings(self) -> dict[str, object]:
        return {
            "mode": self.mode(),
            "snapshot_variable_names": self.selected_snapshot_variable_names(),
            "sample_variable_names": self.selected_sample_variable_names(),
            "flow_rate_parameter": self.flowRateCombo.currentText(),
            "flow_acc_parameter": self.flowAccCombo.currentText(),
            "k_factor_parameter": self.kFactorCombo.currentText(),
            "poll_interval_s": self.pollIntervalSpinBox.value(),
            "instant_flow_offset_s": self.instantFlowOffsetSpinBox.value(),
            "record_flow_samples": self.record_flow_samples(),
            "operation_notes": self.operation_notes(),
            "flow_points": self.flow_points(),
        }

    def apply_configuration(self, settings: dict[str, object]) -> None:
        mode = settings.get("mode")
        if isinstance(mode, str):
            index = self.modeCombo.findData(mode)
            if index >= 0 and self.modeCombo.model().item(index).isEnabled():
                self.modeCombo.setCurrentIndex(index)
        self._set_combo_text(self.flowRateCombo, settings.get("flow_rate_parameter"))
        self._set_combo_text(self.flowAccCombo, settings.get("flow_acc_parameter"))
        self._set_combo_text(self.kFactorCombo, settings.get("k_factor_parameter"))
        poll_interval = settings.get("poll_interval_s")
        if isinstance(poll_interval, (int, float)):
            self.pollIntervalSpinBox.setValue(float(poll_interval))
        instant_flow_offset = settings.get("instant_flow_offset_s")
        if not isinstance(instant_flow_offset, (int, float)):
            instant_flow_offset = settings.get("post_start_sample_s")
        if isinstance(instant_flow_offset, (int, float)):
            self.instantFlowOffsetSpinBox.setValue(float(instant_flow_offset))
        operation_notes = settings.get("operation_notes")
        if isinstance(operation_notes, str):
            self.operationNotesTextEdit.setPlainText(operation_notes)
        record_flow_samples = settings.get("record_flow_samples")
        if isinstance(record_flow_samples, bool):
            self.recordFlowSamplesCheckBox.setChecked(record_flow_samples)
        flow_points = settings.get("flow_points")
        if isinstance(flow_points, (list, tuple)):
            for spin, value in zip(self.flowPointSpinBoxes, flow_points):
                if isinstance(value, (int, float)):
                    spin.setValue(float(value))
        snapshot_names = settings.get("snapshot_variable_names")
        if not isinstance(snapshot_names, (list, tuple)):
            snapshot_names = settings.get("post_snapshot_variable_names")
        if isinstance(snapshot_names, (list, tuple)):
            available = {register.name for register in self._snapshot_registers}
            self._snapshot_variable_names = tuple(
                str(name) for name in snapshot_names if str(name) in available
            )
            self._update_snapshot_button_text()
        sample_names = settings.get("sample_variable_names")
        if isinstance(sample_names, (list, tuple)):
            available = {register.name for register in self._snapshot_registers}
            self._sample_variable_names = tuple(
                str(name) for name in sample_names if str(name) in available
            )
            self._update_sample_variables_button_text()
        self.settingsChanged.emit()

    def mode(self) -> str:
        value = self.modeCombo.currentData()
        return str(value) if value else "three_point"

    def is_single_point_mode(self) -> bool:
        return self.mode() == "single_point"

    def flow_points(self) -> tuple[float, float, float]:
        return tuple(spin.value() for spin in self.flowPointSpinBoxes)  # type: ignore[return-value]

    def operation_notes(self) -> str:
        return self.operationNotesTextEdit.toPlainText().strip()

    def record_flow_samples(self) -> bool:
        return self.recordFlowSamplesCheckBox.isChecked()

    def set_config_enabled(self, enabled: bool) -> None:
        self._config_enabled = enabled
        single_point = self.is_single_point_mode()
        for widget in (
            self.modeCombo,
            self.flowRateCombo,
            self.flowAccCombo,
            self.kFactorCombo,
            self.pollIntervalSpinBox,
            self.instantFlowOffsetSpinBox,
            self.operationNotesTextEdit,
            self.snapshotButton,
            self.sampleVariablesButton,
            self.saveHistoryCheckBox,
            self.recordFlowSamplesCheckBox,
            self.saveConfigButton,
        ):
            widget.setEnabled(enabled)
        for index, spin in enumerate(self.flowPointSpinBoxes):
            spin.setEnabled(enabled and (index == 0 or not single_point))

    def set_status(self, message: str) -> None:
        self.statusLabel.setText(message)

    def _open_snapshot_dialog(self) -> None:
        dialog = SnapshotSelectionDialog(
            self._snapshot_registers,
            selected_names=self._snapshot_variable_names,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._snapshot_variable_names = dialog.selected_names()
        self._update_snapshot_button_text()
        self.settingsChanged.emit()

    def _open_sample_variables_dialog(self) -> None:
        flow_rate_name = self.flowRateCombo.currentText()
        dialog = SnapshotSelectionDialog(
            self._snapshot_registers,
            selected_names=self._sample_variable_names,
            required_names=(flow_rate_name,) if flow_rate_name else (),
            title="Default Trial Sample Variables",
            object_name="modbusRepeatabilitySampleVariableSelectionTable",
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._sample_variable_names = dialog.selected_names()
        self._update_sample_variables_button_text()
        self.settingsChanged.emit()

    def _update_snapshot_button_text(self) -> None:
        count = len(self._snapshot_variable_names)
        self.snapshotButton.setText(f"Pre/Post Snapshot... ({count})")

    def _update_sample_variables_button_text(self) -> None:
        flow_rate_name = self.flowRateCombo.currentText()
        extra_count = sum(
            1 for name in self._sample_variable_names if name != flow_rate_name
        )
        self.sampleVariablesButton.setText(
            f"Default Trial Samples... ({extra_count + 1})"
        )

    def _mode_changed(self) -> None:
        self.set_config_enabled(self._config_enabled)
        self.settingsChanged.emit()

    def _set_combo_items(
        self,
        combo: QComboBox,
        names: tuple[str, ...],
        preferred: tuple[str, ...],
    ) -> None:
        current = combo.currentText()
        combo.clear()
        combo.addItems(names)
        target = current if current in names else ""
        if not target:
            target = next(
                (name for name in preferred if name in names),
                names[0] if names else "",
            )
        if target:
            combo.setCurrentText(target)

    def _set_combo_text(self, combo: QComboBox, value: object) -> None:
        if not isinstance(value, str):
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)


class RepeatabilityCaptureProgressDialog(QDialog):
    """Small non-modal status dialog for repeatability trial capture."""

    closed = Signal(bool)

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Capture Trial")
        self.setModal(False)
        self.setSizeGripEnabled(False)
        _fit_dialog_to_screen(self, 360, 140, 300, 120)
        self._auto_closing = False
        self._auto_close_timer = QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self._auto_close)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        self.messageLabel = QLabel("Acquiring data...")
        self.messageLabel.setObjectName("modbusRepeatabilityCaptureProgressLabel")
        self.messageLabel.setWordWrap(True)
        root.addWidget(self.messageLabel, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusRepeatabilityCaptureProgressCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

    def show_message(self, message: str) -> None:
        self._auto_close_timer.stop()
        self._auto_closing = False
        self.messageLabel.setText(message or "Acquiring data...")
        self.show()
        self.raise_()
        self.activateWindow()

    def complete(self, message: str = "Completed.") -> None:
        self._auto_close_timer.stop()
        self.messageLabel.setText(message)
        if not self.isVisible():
            self.show()
        self.raise_()
        self._auto_close_timer.start(2000)

    def fail(self, message: str) -> None:
        self._auto_close_timer.stop()
        self._auto_closing = False
        self.messageLabel.setText(f"Failed: {message}")
        if not self.isVisible():
            self.show()
        self.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        auto_closing = self._auto_closing
        self._auto_close_timer.stop()
        self._auto_closing = False
        self.closed.emit(auto_closing)
        super().closeEvent(event)

    def _auto_close(self) -> None:
        self._auto_closing = True
        self.close()


class RepeatabilityFlowPlotDialog(QDialog):
    """Non-modal live multi-variable chart for one repeatability trial capture."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trial Flow Samples")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        _fit_dialog_to_screen(self, 620, 380, 420, 280)
        self._timestamps: list[datetime] = []
        self._values: dict[str, list[float | None]] = {}
        self._units: dict[str, str] = {}
        self._variable_names: tuple[str, ...] = ()
        self._plot_layout = PLOT_LAYOUT_OVERLAY
        self._point_label = "current trial"
        self._curves: dict[tuple[str, str], object] = {}
        self._point_items: dict[tuple[str, str], object] = {}
        self._plot_widgets: dict[str, pg.PlotWidget] = {}
        self._plot_legends: dict[str, object] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self.summaryLabel = QLabel("No samples")
        self.summaryLabel.setObjectName("modbusRepeatabilityFlowPlotSummaryLabel")
        root.addWidget(self.summaryLabel)

        self.plotScrollArea = QScrollArea()
        self.plotScrollArea.setObjectName("modbusRepeatabilityFlowPlotScrollArea")
        self.plotScrollArea.setWidgetResizable(True)
        self.plotPanel = QWidget()
        self.plotPanelLayout = QVBoxLayout(self.plotPanel)
        self.plotPanelLayout.setContentsMargins(0, 0, 0, 0)
        self.plotPanelLayout.setSpacing(8)
        self.plotScrollArea.setWidget(self.plotPanel)
        root.addWidget(self.plotScrollArea, 1)

        self.selectedPointLabel = QLabel("Selected Point: none")
        self.selectedPointLabel.setObjectName(
            "modbusRepeatabilityFlowPlotSelectedPointLabel"
        )
        self.selectedPointLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.selectedPointLabel)

        self.flowPlot = pg.PlotWidget()
        self.flowPlot.setObjectName("modbusRepeatabilityFlowPlot")

    def reset_trial(
        self,
        *,
        flow_parameter: str,
        variable_names: tuple[str, ...],
        units: dict[str, str],
        plot_layout: str = PLOT_LAYOUT_OVERLAY,
        window_title: str | None = None,
        point_label: str = "current trial",
    ) -> None:
        self._timestamps.clear()
        self._variable_names = _unique_names(variable_names)
        self._values = {name: [] for name in self._variable_names}
        self._units = dict(units)
        self._plot_layout = _normalize_plot_layout(plot_layout)
        self._point_label = point_label
        self._curves.clear()
        self._point_items.clear()
        self._clear_plot_panel()
        self._build_plot_widgets()
        self.setWindowTitle(window_title or f"Trial Flow Samples - {flow_parameter}")
        self.summaryLabel.setText(
            f"Waiting for samples... | {_plot_layout_label(self._plot_layout)}"
        )
        self.selectedPointLabel.setText("Selected Point: none")
        self.show()
        self.raise_()

    def add_sample(self, captured_at: datetime, values: dict[str, object]) -> None:
        self._timestamps.append(captured_at)
        for name in self._variable_names:
            self._values.setdefault(name, []).append(_optional_float(values.get(name)))
        if not self._timestamps:
            return
        start = self._timestamps[0]
        x_values = [
            max(0.0, (timestamp - start).total_seconds())
            for timestamp in self._timestamps
        ]
        for (_plot_key, name), curve in self._curves.items():
            y_values = self._values.get(name, [])
            valid_x = [
                x_value
                for x_value, y_value in zip(x_values, y_values)
                if y_value is not None
            ]
            valid_y = [
                y_value
                for y_value in y_values
                if y_value is not None
            ]
            curve.setData(valid_x, valid_y)
            point_item = self._point_items.get((_plot_key, name))
            if point_item is not None:
                spots = []
                unit = self._units.get(name, "")
                for sample_index, (timestamp, x_value, y_value) in enumerate(
                    zip(self._timestamps, x_values, y_values),
                    start=1,
                ):
                    if y_value is None:
                        continue
                    spots.append(
                        {
                            "pos": (x_value, y_value),
                            "data": {
                                "label": self._point_label,
                                "variable": name,
                                "elapsed_s": x_value,
                                "captured_at": timestamp,
                                "value": y_value,
                                "unit": unit,
                                "sample_index": sample_index,
                            },
                        }
                    )
                point_item.setData(spots)
        for plot_widget in self._plot_widgets.values():
            plot_widget.plotItem.enableAutoRange(x=True, y=True)
        elapsed = x_values[-1] if x_values else 0.0
        latest_parts = []
        for name in self._variable_names[:4]:
            value = self._values.get(name, [None])[-1]
            if value is None:
                continue
            unit = f" {self._units.get(name, '')}" if self._units.get(name, "") else ""
            latest_parts.append(f"{name}={value:.6g}{unit}")
        latest_text = ", ".join(latest_parts)
        self.summaryLabel.setText(
            f"{len(self._timestamps)} samples | elapsed {elapsed:.3g} s | "
            f"{_plot_layout_label(self._plot_layout)} | latest {latest_text}"
        )

    def _build_plot_widgets(self) -> None:
        if self._plot_layout == PLOT_LAYOUT_SEPARATE:
            for index, name in enumerate(self._variable_names):
                object_name = (
                    "modbusRepeatabilityFlowPlot"
                    if index == 0
                    else f"modbusRepeatabilityFlowPlot_{_object_name_token(name)}"
                )
                plot_widget = self._new_plot_widget(
                    object_name=object_name,
                )
                self._plot_widgets[name] = plot_widget
                self._plot_legends[name] = plot_widget.addLegend(offset=(8, 8))
                unit = self._units.get(name, "")
                plot_widget.setTitle(name)
                plot_widget.setLabel("bottom", "Time", units="s")
                plot_widget.setLabel("left", name, units=unit or None)
                self.plotPanelLayout.addWidget(plot_widget, 1)
                self._curves[(name, name)] = plot_widget.plot(
                    [],
                    [],
                    pen=pg.mkPen(_plot_colors()[index % len(_plot_colors())], width=2),
                    name=_variable_label(name, unit),
                )
                point_item = self._new_point_item(
                    color=_plot_colors()[index % len(_plot_colors())],
                )
                self._point_items[(name, name)] = point_item
                plot_widget.addItem(point_item)
            if self._variable_names:
                self.flowPlot = self._plot_widgets[self._variable_names[0]]
            return

        plot_widget = self._new_plot_widget(object_name="modbusRepeatabilityFlowPlot")
        self.flowPlot = plot_widget
        self._plot_widgets["overlay"] = plot_widget
        self._plot_legends["overlay"] = plot_widget.addLegend(offset=(8, 8))
        plot_widget.setTitle(", ".join(self._variable_names))
        plot_widget.setLabel("bottom", "Time", units="s")
        plot_widget.setLabel("left", "Value")
        colors = _plot_colors()
        for index, name in enumerate(self._variable_names):
            unit = self._units.get(name, "")
            self._curves[("overlay", name)] = plot_widget.plot(
                [],
                [],
                pen=pg.mkPen(colors[index % len(colors)], width=2),
                name=_variable_label(name, unit),
            )
            point_item = self._new_point_item(color=colors[index % len(colors)])
            self._point_items[("overlay", name)] = point_item
            plot_widget.addItem(point_item)
        self.plotPanelLayout.addWidget(plot_widget, 1)

    def _new_point_item(self, *, color: str):
        item = pg.ScatterPlotItem(
            [],
            [],
            size=8,
            brush=pg.mkBrush(color),
            pen=pg.mkPen("w", width=1),
            hoverable=True,
        )
        item.sigClicked.connect(self._point_clicked)
        return item

    def _point_clicked(self, _item, points, *_args) -> None:
        if not points:
            return
        data = points[0].data()
        if isinstance(data, dict):
            self.selectedPointLabel.setText(
                "Selected Point: " + _format_plot_point_data(data)
            )

    def _new_plot_widget(self, *, object_name: str) -> pg.PlotWidget:
        plot_widget = pg.PlotWidget()
        plot_widget.setObjectName(object_name)
        plot_widget.setBackground("w")
        plot_widget.showGrid(x=True, y=True, alpha=0.25)
        plot_widget.setMinimumHeight(180)
        return plot_widget

    def _clear_plot_panel(self) -> None:
        while self.plotPanelLayout.count():
            item = self.plotPanelLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._plot_widgets.clear()
        self._plot_legends.clear()
        self._point_items.clear()


class RepeatabilityTestDialog(QDialog):
    """Operator-facing simple error and repeatability test dialog."""

    cancelRequested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Error And Repeatability Test")
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        _fit_dialog_to_screen(self, 800, 560, 620, 440)
        self._run_id: str | None = None
        self._capture: ModbusRepeatabilitySimpleCapture | None = None
        self._trials: list[ModbusRepeatabilitySimpleTrialResult] = []
        self._result: ModbusRepeatabilitySimpleResult | None = None
        self._snapshot_registers: tuple[ModbusRegister, ...] = ()
        self._snapshot_variable_names: tuple[str, ...] = ()
        self._original_k_factor: float | None = None
        self._selected_repeatability: dict[float, ModbusRepeatabilityFlowSummary] = {}
        self._selected_repeatability_trials: dict[
            float,
            tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        ] = {}
        self._final_k_result: dict[str, object] | None = None
        self.captureProgressDialog: RepeatabilityCaptureProgressDialog | None = None
        self.flowPlotDialog: RepeatabilityFlowPlotDialog | None = None
        self._capture_progress_active = False
        self._capture_progress_dismissed = False
        self.configurationDialog = RepeatabilityConfigurationDialog(parent=self)
        self.configurationDialog.settingsChanged.connect(self._flow_points_changed)
        self.configurationDialog.settingsChanged.connect(self._sync_operation_notes)
        self.modeCombo = self.configurationDialog.modeCombo
        self.flowRateCombo = self.configurationDialog.flowRateCombo
        self.flowAccCombo = self.configurationDialog.flowAccCombo
        self.kFactorCombo = self.configurationDialog.kFactorCombo
        self.pollIntervalSpinBox = self.configurationDialog.pollIntervalSpinBox
        self.instantFlowOffsetSpinBox = (
            self.configurationDialog.instantFlowOffsetSpinBox
        )
        self.flowPointSpinBoxes = self.configurationDialog.flowPointSpinBoxes
        self.operationNotesTextEdit = self.configurationDialog.operationNotesTextEdit
        self.snapshotButton = self.configurationDialog.snapshotButton
        self.sampleVariablesButton = self.configurationDialog.sampleVariablesButton
        self.saveHistoryCheckBox = self.configurationDialog.saveHistoryCheckBox
        self.saveConfigButton = self.configurationDialog.saveConfigButton
        self._build_ui()
        self._populate_trial_placeholders()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self.configurationDialog.isVisible():
            self.configurationDialog.close()
        if (
            self.captureProgressDialog is not None
            and isValid(self.captureProgressDialog)
            and self.captureProgressDialog.isVisible()
        ):
            self.captureProgressDialog.close()
        if (
            self.flowPlotDialog is not None
            and isValid(self.flowPlotDialog)
            and self.flowPlotDialog.isVisible()
        ):
            self.flowPlotDialog.close()
        self.cancelRequested.emit()
        super().closeEvent(event)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("modbusRepeatabilityStatusLabel")
        root.addWidget(self.statusLabel)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        operation_group = QGroupBox("Trial")
        operation_form = QFormLayout(operation_group)
        self.standardMassSpinBox = _float_input(10.0)
        self.standardMassSpinBox.setObjectName(
            "modbusRepeatabilityStandardMassSpinBox"
        )
        self.standardMassSpinBox.setMinimum(0.000001)
        self.standardMassSpinBox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )
        self.standardMassSpinBox.setSuffix(" g")
        operation_form.addRow("Standard Mass", self.standardMassSpinBox)
        self.operationNotesLabel = QLabel("No operation note")
        self.operationNotesLabel.setObjectName(
            "modbusRepeatabilityOperationNotesLabel"
        )
        self.operationNotesLabel.setWordWrap(True)
        operation_form.addRow("Operation Note", self.operationNotesLabel)
        self.originalKFactorValueLabel = QLabel("Not read")
        self.originalKFactorValueLabel.setObjectName(
            "modbusRepeatabilityOriginalKFactorValueLabel"
        )
        self.originalKFactorValueLabel.hide()
        self.configurationButton = QPushButton("Configuration...")
        self.configurationButton.setObjectName("modbusRepeatabilityConfigButton")
        self.configurationButton.clicked.connect(self.open_configuration)
        operation_form.addRow("Configuration", self.configurationButton)

        preview_group = QGroupBox("Selected Trials And K Preview")
        preview_layout = QVBoxLayout(preview_group)
        preview_layout.setContentsMargins(8, 8, 8, 8)
        preview_layout.setSpacing(6)
        self.selectionSummaryTextEdit = QTextEdit()
        self.selectionSummaryTextEdit.setObjectName(
            "modbusRepeatabilitySelectionSummaryText"
        )
        self.selectionSummaryTextEdit.setReadOnly(True)
        self.selectionSummaryTextEdit.setMinimumWidth(240)
        self.selectionSummaryTextEdit.setMinimumHeight(120)
        self.selectionSummaryTextEdit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.selectionSummaryTextEdit.setPlaceholderText(
            "Selected trial errors and repeatability will appear here."
        )
        preview_layout.addWidget(self.selectionSummaryTextEdit, 1)

        top_row.addWidget(operation_group, 1)
        top_row.addWidget(preview_group, 2)
        root.addLayout(top_row, 0)

        self.trialTable = QTableWidget(9, 10)
        self.trialTable.setObjectName("modbusRepeatabilityTrialTable")
        self.trialTable.setHorizontalHeaderLabels(
            [
                "Target Flow",
                "Trial",
                "State",
                "m1",
                "m2",
                "Delta m",
                "v1",
                "v_mean",
                "Standard Mass",
                "Error (%)",
            ]
        )
        self.trialTable.verticalHeader().setVisible(False)
        self.trialTable.horizontalHeader().setSectionsMovable(True)
        self.trialTable.setAlternatingRowColors(True)
        self.trialTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.trialTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.trialTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.trialTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.trialTable.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        for column, width in enumerate(
            (120, 70, 110, 95, 95, 95, 95, 95, 120, 110)
        ):
            self.trialTable.setColumnWidth(column, width)
        root.addWidget(self.trialTable, 4)

        primary_buttons = QHBoxLayout()
        primary_buttons.addStretch(1)
        self.startButton = QPushButton("Capture Trial")
        self.startButton.setObjectName("modbusRepeatabilityStartButton")
        self.calculateTrialErrorButton = QPushButton("Calculate Trial Error")
        self.calculateTrialErrorButton.setObjectName(
            "modbusRepeatabilityCalculateTrialErrorButton"
        )
        self.calculateTrialErrorButton.setEnabled(False)
        self.calculateRepeatabilityButton = QPushButton("Calculate Repeatability")
        self.calculateRepeatabilityButton.setObjectName(
            "modbusRepeatabilityCalculateButton"
        )
        self.calculateRepeatabilityButton.setEnabled(False)
        self.calculateFinalKButton = QPushButton("Calculate Final K")
        self.calculateFinalKButton.setObjectName("modbusRepeatabilityFinalKButton")
        self.calculateFinalKButton.setEnabled(False)
        self.writeFinalKButton = QPushButton("Write New K...")
        self.writeFinalKButton.setObjectName("modbusRepeatabilityWriteFinalKButton")
        self.writeFinalKButton.setEnabled(False)
        self.addTrialButton = QPushButton("Add Trial")
        self.addTrialButton.setObjectName("modbusRepeatabilityAddTrialButton")
        self.addTrialButton.clicked.connect(self.add_extra_trial)
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusRepeatabilityCloseButton")
        self.closeButton.clicked.connect(self.close)
        primary_buttons.addWidget(self.startButton)
        primary_buttons.addWidget(self.calculateTrialErrorButton)
        primary_buttons.addWidget(self.calculateRepeatabilityButton)
        primary_buttons.addWidget(self.calculateFinalKButton)
        primary_buttons.addWidget(self.writeFinalKButton)
        secondary_buttons = QHBoxLayout()
        secondary_buttons.addStretch(1)
        secondary_buttons.addWidget(self.addTrialButton)
        secondary_buttons.addWidget(self.closeButton)
        root.addLayout(primary_buttons)
        root.addLayout(secondary_buttons)
        self._refresh_selection_summary()

    def set_registers(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...] | None = None,
        sample_selected_names: tuple[str, ...] | None = None,
    ) -> None:
        self._snapshot_registers = registers
        self.configurationDialog.set_registers(
            registers,
            selected_names=selected_names,
            sample_selected_names=sample_selected_names,
        )
        self._snapshot_variable_names = (
            self.configurationDialog.selected_snapshot_variable_names()
        )
        self._sample_variable_names = (
            self.configurationDialog.selected_sample_variable_names()
        )

    def selected_snapshot_variable_names(self) -> tuple[str, ...]:
        return self.configurationDialog.selected_snapshot_variable_names()

    def selected_sample_variable_names(self) -> tuple[str, ...]:
        return self.configurationDialog.selected_sample_variable_names()

    def set_sample_variable_names(self, names: tuple[str, ...]) -> None:
        self.configurationDialog.set_sample_variable_names(names)
        self._sample_variable_names = (
            self.configurationDialog.selected_sample_variable_names()
        )

    def capture_settings(self) -> dict[str, object]:
        return self.configurationDialog.capture_settings()

    def apply_configuration(self, settings: dict[str, object]) -> None:
        self.configurationDialog.apply_configuration(settings)
        self._snapshot_variable_names = (
            self.configurationDialog.selected_snapshot_variable_names()
        )
        self._sample_variable_names = (
            self.configurationDialog.selected_sample_variable_names()
        )
        self._sync_operation_notes()
        self._populate_trial_placeholders()

    def mode(self) -> str:
        return self.configurationDialog.mode()

    def is_single_point_mode(self) -> bool:
        return self.configurationDialog.is_single_point_mode()

    def flow_points(self) -> tuple[float, float, float]:
        return self.configurationDialog.flow_points()

    def next_trial_context(self) -> tuple[float, int]:
        if self.is_single_point_mode():
            return self.flow_points()[0], len(self._trials) + 1
        extra_row = self._pending_extra_trial_row()
        if extra_row is not None:
            try:
                return (
                    float(_table_text(self.trialTable, extra_row, 0)),
                    int(_table_text(self.trialTable, extra_row, 1)),
                )
            except ValueError as exc:
                raise ValueError("Extra trial row is incomplete.") from exc
        base_count = self._base_trial_count()
        if base_count >= 9:
            raise ValueError("Add an extra trial before capturing more data.")
        flow_points = self.flow_points()
        flow_index = base_count // 3
        trial_index = base_count % 3 + 1
        return flow_points[flow_index], trial_index

    def _base_trial_count(self) -> int:
        if self.is_single_point_mode():
            return len(self._trials)
        count = 0
        for flow_point in self.flow_points():
            for trial_index in range(1, 4):
                if self._base_trial_at(flow_point, trial_index) is None:
                    return count
                count += 1
        return count

    def _base_trial_at(
        self,
        flow_point: float,
        trial_index: int,
    ) -> ModbusRepeatabilitySimpleTrialResult | None:
        for trial in self._trials:
            if trial.flow_point == flow_point and trial.trial_index == trial_index:
                return trial
        return None

    def _trial_counts_by_flow(self) -> dict[float, int]:
        counts: dict[float, int] = {}
        for trial in self._trials:
            counts[trial.flow_point] = counts.get(trial.flow_point, 0) + 1
        return counts

    def _pending_extra_trial_row(self) -> int | None:
        for row in range(9, self.trialTable.rowCount()):
            if _table_text(self.trialTable, row, 2) == "Pending":
                return row
        return None

    def _row_for_trial_result(
        self,
        trial: ModbusRepeatabilitySimpleTrialResult,
    ) -> int:
        if self.is_single_point_mode():
            return len(self._trials)
        extra_row = self._pending_extra_trial_row()
        if extra_row is not None:
            try:
                extra_flow_point = float(_table_text(self.trialTable, extra_row, 0))
                extra_trial_index = int(_table_text(self.trialTable, extra_row, 1))
            except ValueError as exc:
                raise ValueError("Extra trial row is incomplete.") from exc
            if (
                trial.flow_point == extra_flow_point
                and trial.trial_index == extra_trial_index
            ):
                return extra_row
        base_count = self._base_trial_count()
        if base_count < 9:
            return base_count
        return len(self._trials)

    def current_run_id(self) -> str | None:
        return self._run_id

    def current_capture(self) -> ModbusRepeatabilitySimpleCapture | None:
        return self._capture

    def trial_results(self) -> tuple[ModbusRepeatabilitySimpleTrialResult, ...]:
        return tuple(self._trials)

    def standard_mass(self) -> float:
        return self.standardMassSpinBox.value()

    def save_history(self) -> bool:
        return self.saveHistoryCheckBox.isChecked()

    def operation_notes(self) -> str:
        return self.configurationDialog.operation_notes()

    def open_configuration(self) -> None:
        self.configurationDialog.show()
        self.configurationDialog.raise_()
        self.configurationDialog.activateWindow()

    def _sync_operation_notes(self) -> None:
        notes = self.operation_notes()
        self.operationNotesLabel.setText(notes or "No operation note")

    def original_k_factor(self) -> float:
        selected_values = {
            trial.original_k_factor
            for trials in self._selected_repeatability_trials.values()
            for trial in trials
        }
        if selected_values:
            if len(selected_values) != 1:
                raise ValueError("selected trials have different original K values")
            return next(iter(selected_values))
        if self._original_k_factor is None:
            raise ValueError("capture a trial first to read original K factor")
        return self._original_k_factor

    def is_complete(self) -> bool:
        if self.is_single_point_mode():
            return False
        return self._base_trial_count() >= 9

    def can_save_summary(self) -> bool:
        return self.is_single_point_mode() and bool(self._trials)

    def selected_repeatability(self) -> dict[float, ModbusRepeatabilityFlowSummary]:
        return dict(self._selected_repeatability)

    def selected_repeatability_trials(
        self,
    ) -> dict[float, tuple[ModbusRepeatabilitySimpleTrialResult, ...]]:
        return dict(self._selected_repeatability_trials)

    def update_selected_repeatability(
        self,
        summary: ModbusRepeatabilityFlowSummary,
        trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
    ) -> tuple[ModbusRepeatabilityFlowSummary | None, ModbusRepeatabilityFlowSummary]:
        previous = self._selected_repeatability.get(summary.flow_point)
        self._selected_repeatability[summary.flow_point] = summary
        self._selected_repeatability_trials[summary.flow_point] = trials
        self._refresh_selection_summary()
        self.calculateFinalKButton.setEnabled(len(self._selected_repeatability) >= 3)
        return previous, summary

    def set_final_k_result(self, result: dict[str, object]) -> None:
        self._final_k_result = dict(result)
        self._refresh_selection_summary()
        self.writeFinalKButton.setEnabled(
            bool(result.get("new_k_factor"))
            and result.get("write_status") != "applied"
        )

    def final_k_result(self) -> dict[str, object] | None:
        if self._final_k_result is None:
            return None
        return dict(self._final_k_result)

    def show_capture_progress(self, message: str = "Acquiring data...") -> None:
        dialog = self._ensure_capture_progress_dialog()
        self._capture_progress_active = True
        self._capture_progress_dismissed = False
        dialog.show_message(message)

    def show_flow_plot(
        self,
        *,
        flow_parameter: str,
        variable_names: tuple[str, ...],
        units: dict[str, str],
        plot_layout: str = PLOT_LAYOUT_OVERLAY,
    ) -> None:
        dialog = self._ensure_flow_plot_dialog()
        dialog.reset_trial(
            flow_parameter=flow_parameter,
            variable_names=variable_names,
            units=units,
            plot_layout=plot_layout,
        )

    def add_flow_plot_sample(
        self,
        captured_at: datetime,
        values: dict[str, object],
    ) -> None:
        if self.flowPlotDialog is None or not isValid(self.flowPlotDialog):
            return
        self.flowPlotDialog.add_sample(captured_at, values)

    def update_capture_progress(self, message: str) -> None:
        if not self._capture_progress_active or self._capture_progress_dismissed:
            return
        dialog = self._ensure_capture_progress_dialog()
        dialog.show_message(message)

    def complete_capture_progress(self, message: str = "Completed.") -> None:
        if not self._capture_progress_active or self._capture_progress_dismissed:
            self._capture_progress_active = False
            return
        dialog = self._ensure_capture_progress_dialog()
        self._capture_progress_active = False
        dialog.complete(message)

    def fail_capture_progress(self, message: str) -> None:
        if not self._capture_progress_active or self._capture_progress_dismissed:
            self._capture_progress_active = False
            return
        dialog = self._ensure_capture_progress_dialog()
        self._capture_progress_active = False
        dialog.fail(message)

    def _ensure_capture_progress_dialog(self) -> RepeatabilityCaptureProgressDialog:
        if (
            self.captureProgressDialog is None
            or not isValid(self.captureProgressDialog)
        ):
            self.captureProgressDialog = RepeatabilityCaptureProgressDialog(
                parent=self
            )
            self.captureProgressDialog.closed.connect(
                self._capture_progress_closed
            )
        return self.captureProgressDialog

    def _capture_progress_closed(self, auto_closing: bool) -> None:
        if not auto_closing and self._capture_progress_active:
            self._capture_progress_dismissed = True

    def _ensure_flow_plot_dialog(self) -> RepeatabilityFlowPlotDialog:
        if self.flowPlotDialog is None or not isValid(self.flowPlotDialog):
            self.flowPlotDialog = RepeatabilityFlowPlotDialog(parent=self)
        return self.flowPlotDialog

    def _set_original_k_factor(self, value: float) -> None:
        self._original_k_factor = value
        self.originalKFactorValueLabel.setText(_format_k_value(value))
        self._refresh_selection_summary()

    def _refresh_selection_summary(self) -> None:
        lines: list[str] = []
        if self._original_k_factor is not None:
            lines.append(f"Original K: {_format_k_value(self._original_k_factor)}")
            lines.append("")
        if self._selected_repeatability:
            lines.append("Selected 9 trial errors and repeatability:")
            for flow_point in sorted(self._selected_repeatability):
                summary = self._selected_repeatability[flow_point]
                trials = self._selected_repeatability_trials.get(flow_point, ())
                trial_labels = ", ".join(
                    f"#{trial.trial_index}={trial.percent_error:.6g}%"
                    for trial in trials
                )
                lines.append(
                    f"Flow {flow_point:g}: stddev={summary.repeatability_stddev_percent:.6g}%, "
                    f"mean={summary.mean_percent_error:.6g}%, trials: {trial_labels}"
                )
        else:
            lines.append("No repeatability selection yet.")
        if self._final_k_result:
            lines.append("")
            lines.append("Final K preview:")
            for key in (
                "average_error",
                "new_k_factor",
                "delta_k_factor",
                "original_k_factor",
                "write_status",
                "write_verified",
                "readback_k_factor",
            ):
                if key in self._final_k_result:
                    value = self._final_k_result[key]
                    text = _format_k_value(value) if _is_k_metric_name(key) else _format_value(value)
                    lines.append(f"{key}: {text}")
        self.selectionSummaryTextEdit.setPlainText("\n".join(lines))

    def can_capture_next_trial(self) -> bool:
        if self._capture is not None:
            return False
        if self.is_single_point_mode():
            return True
        return self._base_trial_count() < 9 or self._pending_extra_trial_row() is not None

    def can_add_extra_trial(self) -> bool:
        return (
            not self.is_single_point_mode()
            and self._capture is None
            and self._pending_extra_trial_row() is None
            and bool(self.addable_extra_flow_points())
        )

    def addable_extra_flow_points(self) -> tuple[float, ...]:
        if self.is_single_point_mode():
            return ()
        counts = self._trial_counts_by_flow()
        return tuple(
            flow_point
            for flow_point in self.flow_points()
            if counts.get(flow_point, 0) >= 3
        )

    def default_extra_flow_point(self) -> float | None:
        addable = set(self.addable_extra_flow_points())
        for trial in reversed(self._trials):
            if trial.flow_point in addable:
                return trial.flow_point
        return next(iter(addable), None)

    def add_extra_trial(self) -> None:
        if self._capture is not None:
            self.statusLabel.setText(
                "Calculate the captured trial error before adding another."
            )
            return
        if self.is_single_point_mode():
            self.statusLabel.setText("Single Flow Range already appends with Capture Trial.")
            return
        addable = self.addable_extra_flow_points()
        if not addable:
            self.statusLabel.setText(
                "Complete three trials for a flow point before adding extras."
            )
            return
        if self._pending_extra_trial_row() is not None:
            self.statusLabel.setText(
                "Capture or calculate the queued extra trial first."
            )
            return
        chooser = RepeatabilityAddTrialDialog(
            addable,
            default_flow_point=self.default_extra_flow_point(),
            parent=self,
        )
        if chooser.exec() != QDialog.DialogCode.Accepted:
            return
        flow_point = chooser.selected_flow_point()
        trial_index = self._trial_counts_by_flow().get(flow_point, 0) + 1
        row = self.trialTable.rowCount()
        self.trialTable.insertRow(row)
        self._set_pending_trial_row(row, flow_point, trial_index)
        self.trialTable.selectRow(row)
        self.statusLabel.setText(
            f"Extra trial queued: flow point {flow_point:g}, trial {trial_index}."
        )
        self.startButton.setEnabled(True)
        self.calculateRepeatabilityButton.setEnabled(bool(self._trials))
        self.calculateFinalKButton.setEnabled(len(self._selected_repeatability) >= 3)
        self.addTrialButton.setEnabled(False)

    def set_ready(self, *, connected: bool) -> None:
        can_capture = connected and self.can_capture_next_trial()
        self.startButton.setEnabled(can_capture)
        self.calculateTrialErrorButton.setEnabled(
            connected and self._capture is not None
        )
        self.calculateRepeatabilityButton.setEnabled(connected and bool(self._trials))
        self.calculateFinalKButton.setEnabled(
            connected and len(self._selected_repeatability) >= 3
        )
        self.writeFinalKButton.setEnabled(
            connected
            and self._final_k_result is not None
            and self._final_k_result.get("write_status") != "applied"
        )
        self.addTrialButton.setEnabled(
            connected and self.can_add_extra_trial()
        )
        self._set_config_enabled(connected and not self._trials and self._capture is None)
        self.standardMassSpinBox.setEnabled(connected and self._capture is not None)
        if connected and self.statusLabel.text() == "Running...":
            self.statusLabel.setText("Ready")

    def set_running(self) -> None:
        self.statusLabel.setText("Running...")
        self.startButton.setEnabled(False)
        self.calculateTrialErrorButton.setEnabled(False)
        self.calculateRepeatabilityButton.setEnabled(False)
        self.calculateFinalKButton.setEnabled(False)
        self.writeFinalKButton.setEnabled(False)
        self.configurationButton.setEnabled(False)
        self.addTrialButton.setEnabled(False)
        self._set_config_enabled(False)
        self.standardMassSpinBox.setEnabled(False)

    def set_canceling(self) -> None:
        self.statusLabel.setText("Canceling...")
        self.startButton.setEnabled(False)
        self.calculateTrialErrorButton.setEnabled(False)
        self.calculateRepeatabilityButton.setEnabled(False)
        self.calculateFinalKButton.setEnabled(False)
        self.writeFinalKButton.setEnabled(False)
        self.configurationButton.setEnabled(False)
        self.addTrialButton.setEnabled(False)
        self._set_config_enabled(False)
        self.standardMassSpinBox.setEnabled(False)

    def set_captured(self, capture: ModbusRepeatabilitySimpleCapture) -> None:
        self._run_id = capture.run_id
        self._capture = capture
        self.statusLabel.setText(
            "Captured trial data. Enter standard mass, then calculate trial error."
        )
        self.startButton.setEnabled(False)
        self.calculateTrialErrorButton.setEnabled(True)
        self.calculateRepeatabilityButton.setEnabled(False)
        self.calculateFinalKButton.setEnabled(False)
        self.writeFinalKButton.setEnabled(False)
        self.configurationButton.setEnabled(False)
        self.addTrialButton.setEnabled(False)
        self._set_config_enabled(False)
        self.standardMassSpinBox.setEnabled(True)
        self.complete_capture_progress("Data acquisition completed.")
        self._append_selection_summary(
            [
                "Pending captured trial:",
                f"run_id: {capture.run_id}",
                f"flow_point: {_format_value(capture.flow_point)}",
                f"trial: {_format_value(capture.trial_index)}",
                f"delta_m: {_format_value(capture.measured_mass_delta)}",
                f"v1: {_format_value(capture.segment.instant_flow)}",
                f"v_mean: {_format_value(capture.mean_flow)}",
            ]
        )

    def add_trial_result(
        self,
        trial: ModbusRepeatabilitySimpleTrialResult,
    ) -> None:
        row = self._row_for_trial_result(trial)
        self._trials.append(trial)
        self._capture = None
        self._result = None
        self._set_trial_row(row, trial)
        if self.is_single_point_mode():
            self._populate_trial_placeholders()
        if self.is_complete():
            self.statusLabel.setText(
                "Base trial set complete. Calculate repeatability or final K when ready."
            )
        else:
            flow_point, trial_index = self.next_trial_context()
            self.statusLabel.setText(
                f"Saved trial. Next: flow point {flow_point:g}, trial {trial_index}."
            )
        self.startButton.setEnabled(self.can_capture_next_trial())
        self.calculateTrialErrorButton.setEnabled(False)
        self.calculateRepeatabilityButton.setEnabled(True)
        self.calculateFinalKButton.setEnabled(len(self._selected_repeatability) >= 3)
        self.writeFinalKButton.setEnabled(False)
        self.standardMassSpinBox.setEnabled(False)
        self.configurationButton.setEnabled(False)
        self.addTrialButton.setEnabled(self.can_add_extra_trial())

    def set_result(self, result: ModbusRepeatabilitySimpleResult) -> None:
        self._result = result
        if self.is_single_point_mode():
            self.statusLabel.setText(
                f"Repeatability summary saved {result.run_id}; next trial can be captured."
            )
        else:
            self.statusLabel.setText(f"Repeatability completed {result.run_id}")
        self.startButton.setEnabled(self.can_capture_next_trial())
        self.calculateTrialErrorButton.setEnabled(False)
        self.calculateRepeatabilityButton.setEnabled(bool(self._trials))
        self.calculateFinalKButton.setEnabled(len(self._selected_repeatability) >= 3)
        self.writeFinalKButton.setEnabled(
            self._final_k_result is not None
            and self._final_k_result.get("write_status") != "applied"
        )
        self.configurationButton.setEnabled(False)
        self.addTrialButton.setEnabled(self.can_add_extra_trial())
        self._set_config_enabled(False)
        self.standardMassSpinBox.setEnabled(False)
        lines = [
            "Repeatability summary:",
            f"run_id: {result.run_id}",
            f"trial_count: {_format_value(result.analysis.summary_metrics['trial_count'])}",
            "mean_percent_error: "
            f"{_format_value(result.analysis.summary_metrics['mean_percent_error'])}",
            "max_abs_percent_error: "
            f"{_format_value(result.analysis.summary_metrics['max_abs_percent_error'])}",
            "max_repeatability_stddev_percent: "
            f"{_format_value(result.analysis.summary_metrics['max_repeatability_stddev_percent'])}",
        ]
        for point in result.analysis.flow_points:
            lines.append(
                f"flow_{point.flow_point:g}_repeatability_stddev_percent: "
                f"{_format_value(point.repeatability_stddev_percent)}"
            )
        self._append_selection_summary(lines)

    def set_error(self, message: str) -> None:
        self.statusLabel.setText(f"Failed: {message}")
        self.startButton.setEnabled(self.can_capture_next_trial())
        self.calculateTrialErrorButton.setEnabled(self._capture is not None)
        self.calculateRepeatabilityButton.setEnabled(bool(self._trials))
        self.calculateFinalKButton.setEnabled(len(self._selected_repeatability) >= 3)
        self.writeFinalKButton.setEnabled(
            self._final_k_result is not None
            and self._final_k_result.get("write_status") != "applied"
        )
        self.configurationButton.setEnabled(self._capture is None and not self._trials)
        self.addTrialButton.setEnabled(self.can_add_extra_trial())
        self.standardMassSpinBox.setEnabled(self._capture is not None)
        self._set_config_enabled(not self._trials and self._capture is None)

    def _populate_trial_placeholders(self) -> None:
        if self._capture is not None:
            return
        if self.is_single_point_mode():
            row_count = len(self._trials) + 1
            self.trialTable.setRowCount(row_count)
            for row, trial in enumerate(self._trials):
                self._set_trial_row(row, trial)
            flow_point, trial_index = self.next_trial_context()
            self._set_pending_trial_row(row_count - 1, flow_point, trial_index)
            return
        extra_rows = []
        for row in range(9, self.trialTable.rowCount()):
            extra_rows.append(
                (
                    _table_text(self.trialTable, row, 0),
                    _table_text(self.trialTable, row, 1),
                    _table_text(self.trialTable, row, 2),
                )
            )
        self.trialTable.setRowCount(9 + len(extra_rows))
        row = 0
        for flow_point in self.flow_points():
            for trial_index in range(1, 4):
                trial = self._base_trial_at(flow_point, trial_index)
                if trial is not None:
                    self._set_trial_row(row, trial)
                else:
                    self._set_pending_trial_row(row, flow_point, trial_index)
                row += 1
        for offset, (flow_point, trial_index, state) in enumerate(extra_rows, start=9):
            if state == "Pending":
                try:
                    self._set_pending_trial_row(
                        offset,
                        float(flow_point),
                        int(trial_index),
                    )
                except ValueError:
                    self._set_trial_text(offset, 0, flow_point)
                    self._set_trial_text(offset, 1, trial_index)
                    self._set_trial_text(offset, 2, state)

    def _set_trial_row(
        self,
        row: int,
        trial: ModbusRepeatabilitySimpleTrialResult,
    ) -> None:
        values = (
            trial.flow_point,
            trial.trial_index,
            "Saved",
            trial.mass_acc_before,
            trial.mass_acc_after,
            trial.measured_mass_delta,
            trial.instant_flow,
            trial.mean_flow,
            trial.standard_mass,
            trial.percent_error,
        )
        for column, value in enumerate(values):
            self._set_trial_text(row, column, _format_value(value))

    def _set_pending_trial_row(
        self,
        row: int,
        flow_point: float,
        trial_index: int,
    ) -> None:
        self._set_trial_text(row, 0, _format_value(flow_point))
        self._set_trial_text(row, 1, str(trial_index))
        self._set_trial_text(row, 2, "Pending")
        for column in range(3, 10):
            self._set_trial_text(row, column, "")

    def _set_trial_text(self, row: int, column: int, value: str) -> None:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.trialTable.setItem(row, column, item)

    def set_progress_summary(
        self,
        *,
        latest_trial: ModbusRepeatabilitySimpleTrialResult,
        flow_summaries: tuple[ModbusRepeatabilityFlowSummary, ...],
    ) -> None:
        lines = [
            "Latest trial:",
            f"run_id: {latest_trial.run_id}",
            f"mode: {self.mode()}",
            f"trial_count: {len(self._trials)}",
            f"last_flow_point: {_format_value(latest_trial.flow_point)}",
            f"last_trial: {_format_value(latest_trial.trial_index)}",
            f"last_error_percent: {_format_value(latest_trial.percent_error)}",
            f"last_v1: {_format_value(latest_trial.instant_flow)}",
            f"last_v_mean: {_format_value(latest_trial.mean_flow)}",
        ]
        for flow_summary in flow_summaries:
            lines.extend(
                (
                    f"flow_{flow_summary.flow_point:g}_trial_count: "
                    f"{_format_value(flow_summary.trial_count)}",
                    f"flow_{flow_summary.flow_point:g}_mean_percent_error: "
                    f"{_format_value(flow_summary.mean_percent_error)}",
                    f"flow_{flow_summary.flow_point:g}_max_abs_percent_error: "
                    f"{_format_value(flow_summary.max_abs_percent_error)}",
                    f"flow_{flow_summary.flow_point:g}_repeatability_stddev_percent: "
                    f"{_format_value(flow_summary.repeatability_stddev_percent)}",
                )
            )
        self._append_selection_summary(lines)

    def _append_selection_summary(self, lines: list[str]) -> None:
        existing = self.selectionSummaryTextEdit.toPlainText().strip()
        if existing:
            text = existing + "\n\n" + "\n".join(lines)
        else:
            text = "\n".join(lines)
        self.selectionSummaryTextEdit.setPlainText(text)

    def _flow_points_changed(self) -> None:
        self._populate_trial_placeholders()

    def _mode_changed(self) -> None:
        self._populate_trial_placeholders()

    def _set_config_enabled(self, enabled: bool) -> None:
        self.configurationDialog.set_config_enabled(enabled)
        self.configurationButton.setEnabled(enabled)

    def _set_combo_items(
        self,
        combo: QComboBox,
        names: tuple[str, ...],
        preferred: tuple[str, ...],
    ) -> None:
        current = combo.currentText()
        combo.clear()
        combo.addItems(names)
        target = current if current in names else ""
        if not target:
            target = next((name for name in preferred if name in names), names[0] if names else "")
        if target:
            combo.setCurrentText(target)

    def _set_combo_text(self, combo: QComboBox, value: object) -> None:
        if not isinstance(value, str):
            return
        index = combo.findText(value)
        if index >= 0:
            combo.setCurrentIndex(index)


class RepeatabilitySelectionDialog(QDialog):
    """Select one flow point and three consecutive trials for repeatability."""

    def __init__(
        self,
        trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calculate Repeatability")
        self.setModal(True)
        self._trials_by_flow: dict[float, tuple[ModbusRepeatabilitySimpleTrialResult, ...]] = {}
        for trial in sorted(
            trials,
            key=lambda item: (item.flow_point, item.trial_index, item.flow_started_at),
        ):
            flow_trials = list(self._trials_by_flow.get(trial.flow_point, ()))
            flow_trials.append(trial)
            self._trials_by_flow[trial.flow_point] = tuple(flow_trials)
        _fit_dialog_to_screen(self, 520, 360, 420, 300)
        self._build_ui()
        self._flow_changed()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.flowCombo = QComboBox()
        self.flowCombo.setObjectName("modbusRepeatabilitySelectionFlowCombo")
        for flow_point in sorted(self._trials_by_flow):
            self.flowCombo.addItem(_format_value(flow_point), flow_point)
        self.flowCombo.currentIndexChanged.connect(self._flow_changed)
        form.addRow("Flow Point", self.flowCombo)

        self.windowCombo = QComboBox()
        self.windowCombo.setObjectName("modbusRepeatabilitySelectionWindowCombo")
        self.windowCombo.currentIndexChanged.connect(self._window_changed)
        form.addRow("Trial Window", self.windowCombo)
        root.addLayout(form)

        self.previewTextEdit = QTextEdit()
        self.previewTextEdit.setObjectName("modbusRepeatabilitySelectionPreview")
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

    def selected_trials(self) -> tuple[ModbusRepeatabilitySimpleTrialResult, ...]:
        data = self.windowCombo.currentData()
        if isinstance(data, tuple) and all(
            isinstance(item, ModbusRepeatabilitySimpleTrialResult) for item in data
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
        self.okButton.setEnabled(len(trials) == 3)
        if not trials:
            self.previewTextEdit.setPlainText(
                "No three consecutive trials are available for this flow point."
            )
            return
        errors = tuple(trial.percent_error for trial in trials)
        lines = [
            f"Flow point: {trials[0].flow_point:g}",
            "Selected trials:",
        ]
        lines.extend(
            (
                f"Trial {trial.trial_index}: "
                f"error={trial.percent_error:.6g}%, "
                f"delta_m={trial.measured_mass_delta:.6g}, "
                f"standard={trial.standard_mass:.6g}"
            )
            for trial in trials
        )
        lines.extend(
            (
                "",
                f"Mean error: {sum(errors) / len(errors):.6g}%",
                f"Repeatability stddev: {_sample_stddev(errors):.6g}%",
            )
        )
        self.previewTextEdit.setPlainText("\n".join(lines))


class DeviceAnalysisTrialSelectionDialog(QDialog):
    """Select nine individual history trials for device-analysis reporting."""

    def __init__(
        self,
        history_trials: tuple[ModbusRepeatabilityHistoryTrial, ...],
        *,
        comparison_variable_names: tuple[str, ...] = (
            "zero_offset",
            "low_threshold",
        ),
        save_comparison_variable_names: Callable[[tuple[str, ...]], None] | None = None,
        preview_metrics_factory: Callable[
            [dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]]],
            dict[str, object],
        ]
        | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Analysis Trials")
        self.setModal(True)
        self._comparison_variable_names = tuple(comparison_variable_names)
        self._save_comparison_variable_names = save_comparison_variable_names
        self._preview_metrics_factory = preview_metrics_factory
        self._preview_metrics: dict[str, object] | None = None
        self._history_trials = tuple(
            sorted(
                history_trials,
                key=lambda item: (
                    item.trial.flow_started_at,
                    item.trial.flow_point,
                    item.trial.trial_index,
                    item.attempt_id or "",
                ),
                reverse=True,
            )
        )
        self._available_comparison_variable_names = (
            _device_analysis_available_snapshot_names(self._history_trials)
        )
        if self._available_comparison_variable_names:
            selected = tuple(
                name
                for name in self._comparison_variable_names
                if name in self._available_comparison_variable_names
            )
            self._comparison_variable_names = (
                selected
                or tuple(
                    name
                    for name in ("zero_offset", "low_threshold")
                    if name in self._available_comparison_variable_names
                )
                or self._available_comparison_variable_names[:3]
            )
        _fit_dialog_to_screen(self, 980, 720, 720, 520)
        self._build_ui()
        self._update_comparison_variables_label()
        self._refresh_preview()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        variables = QHBoxLayout()
        self.comparisonVariablesLabel = QLabel("")
        self.comparisonVariablesLabel.setObjectName(
            "modbusDeviceAnalysisTrialComparisonVariablesLabel"
        )
        self.comparisonVariablesLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.selectComparisonVariablesButton = QPushButton("Select Variables...")
        self.selectComparisonVariablesButton.setObjectName(
            "modbusDeviceAnalysisTrialSelectVariablesButton"
        )
        self.selectComparisonVariablesButton.clicked.connect(
            self.select_comparison_variables
        )
        variables.addWidget(QLabel("Compare Variables"))
        variables.addWidget(self.comparisonVariablesLabel, 1)
        variables.addWidget(self.selectComparisonVariablesButton)
        root.addLayout(variables)

        self.selectionTable = QTableWidget(0, 14)
        self.selectionTable.setObjectName("modbusDeviceAnalysisTrialSelectionTable")
        self.selectionTable.setHorizontalHeaderLabels(
            [
                "Use",
                "Flow Point",
                "Trial",
                "Error (%)",
                "Old K",
                "Standard",
                "Delta",
                "v1",
                "v_mean",
                "Attempt ID",
                "Run ID",
                "Started",
                "Raw Artifact",
                "Compare Values",
            ]
        )
        self.selectionTable.verticalHeader().setVisible(False)
        self.selectionTable.horizontalHeader().setSectionsMovable(True)
        self.selectionTable.setAlternatingRowColors(True)
        self.selectionTable.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.selectionTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.selectionTable.setRowCount(len(self._history_trials))
        for row, history_trial in enumerate(self._history_trials):
            trial = history_trial.trial
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(Qt.ItemDataRole.UserRole, history_trial)
            self.selectionTable.setItem(row, 0, check_item)
            values = (
                trial.flow_point,
                trial.trial_index,
                trial.percent_error,
                trial.original_k_factor,
                trial.standard_mass,
                trial.measured_mass_delta,
                trial.instant_flow,
                trial.mean_flow,
                history_trial.attempt_id or "",
                trial.run_id,
                trial.flow_started_at,
                trial.raw_artifact_id or "",
                "",
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(
                    _format_k_value(value) if offset == 4 else _format_value(value)
                )
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.selectionTable.setItem(row, offset, item)
        self.selectionTable.itemChanged.connect(self._refresh_preview)
        widths = (
            50,
            105,
            70,
            95,
            120,
            95,
            95,
            95,
            95,
            210,
            180,
            150,
            180,
            320,
        )
        for column, width in enumerate(widths):
            self.selectionTable.setColumnWidth(column, width)
        root.addWidget(self.selectionTable, 3)

        self.previewTextEdit = QTextEdit()
        self.previewTextEdit.setObjectName("modbusDeviceAnalysisTrialSelectionPreview")
        self.previewTextEdit.setReadOnly(True)
        self.previewTextEdit.setMinimumHeight(150)
        root.addWidget(self.previewTextEdit)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.okButton = self.buttonBox.button(QDialogButtonBox.StandardButton.Ok)
        self.okButton.setText("Calculate")
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        root.addWidget(self.buttonBox)

    def selected_trials_by_flow(
        self,
    ) -> dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]]:
        selected, _messages = self._selected_trials_by_flow()
        return selected

    def preview_metrics(self) -> dict[str, object] | None:
        return None if self._preview_metrics is None else dict(self._preview_metrics)

    def _selected_history_trials(
        self,
    ) -> tuple[ModbusRepeatabilityHistoryTrial, ...]:
        trials: list[ModbusRepeatabilityHistoryTrial] = []
        for row in range(self.selectionTable.rowCount()):
            check_item = self.selectionTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            data = check_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, ModbusRepeatabilityHistoryTrial):
                trials.append(data)
        return tuple(trials)

    def _selected_trials_by_flow(
        self,
    ) -> tuple[dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]], tuple[str, ...]]:
        grouped: dict[float, list[ModbusRepeatabilityHistoryTrial]] = {}
        for history_trial in self._selected_history_trials():
            grouped.setdefault(history_trial.trial.flow_point, []).append(history_trial)
        selected = {
            flow_point: tuple(
                sorted(
                    trials,
                    key=lambda item: (
                        item.trial.trial_index,
                        item.trial.flow_started_at,
                        item.attempt_id or "",
                    ),
                )
            )
            for flow_point, trials in grouped.items()
        }
        messages: list[str] = []
        total = sum(len(trials) for trials in selected.values())
        if total != 9:
            messages.append(f"Select exactly 9 trial records. Current: {total}.")
        if len(selected) != 3:
            messages.append(
                f"Select exactly three flow points. Current: {len(selected)}."
            )
        for flow_point, trials in sorted(selected.items()):
            if len(trials) != 3:
                messages.append(
                    f"Flow {flow_point:g} must have exactly three trials."
                )
                continue
            if not _is_consecutive_trials(tuple(item.trial for item in trials)):
                indexes = ", ".join(str(item.trial.trial_index) for item in trials)
                messages.append(
                    f"Flow {flow_point:g} selected trials must be consecutive "
                    f"(current: {indexes})."
                )
        return selected, tuple(messages)

    def comparison_variable_names(self) -> tuple[str, ...]:
        return self._comparison_variable_names

    def select_comparison_variables(self) -> None:
        if not self._available_comparison_variable_names:
            QMessageBox.warning(
                self,
                "No Snapshot Variables",
                "No pre-calibration snapshot variables are available for these trials.",
            )
            return
        dialog = DeviceAnalysisComparisonVariablesDialog(
            self._available_comparison_variable_names,
            selected_names=self._comparison_variable_names,
            save_selected_names=self.save_comparison_variables,
            parent=self,
        )
        dialog.exec()

    def save_comparison_variables(self, names: tuple[str, ...]) -> None:
        self._comparison_variable_names = names
        if self._save_comparison_variable_names is not None:
            self._save_comparison_variable_names(names)
        self._update_comparison_variables_label()
        self._refresh_preview()

    def _update_comparison_variables_label(self) -> None:
        self.comparisonVariablesLabel.setText(
            ", ".join(self._comparison_variable_names) or "(none)"
        )

    def _refresh_preview(self) -> None:
        selected, validation_messages = self._selected_trials_by_flow()
        valid_selection = not validation_messages
        self.okButton.setEnabled(valid_selection)
        lines = []
        comparison_names = self.comparison_variable_names()
        self.selectionTable.blockSignals(True)
        for row in range(self.selectionTable.rowCount()):
            check_item = self.selectionTable.item(row, 0)
            compare_text = ""
            data = (
                check_item.data(Qt.ItemDataRole.UserRole)
                if check_item is not None
                else None
            )
            if isinstance(data, ModbusRepeatabilityHistoryTrial):
                compare_text = _snapshot_trial_values_text(
                    data,
                    comparison_names,
                )
            compare_item = self.selectionTable.item(row, 13)
            if compare_item is None:
                compare_item = QTableWidgetItem("")
                compare_item.setFlags(
                    compare_item.flags() & ~Qt.ItemFlag.ItemIsEditable
                )
                self.selectionTable.setItem(row, 13, compare_item)
            compare_item.setText(compare_text)
        self.selectionTable.blockSignals(False)
        lines.extend(validation_messages)
        self._preview_metrics = None
        if valid_selection:
            try:
                self._preview_metrics = self._calculate_preview_metrics(selected)
                lines.extend(_device_analysis_preview_lines(self._preview_metrics))
            except Exception as exc:
                self.okButton.setEnabled(False)
                lines.append(str(exc))
        self.previewTextEdit.setPlainText("\n".join(lines))

    def _calculate_preview_metrics(
        self,
        selected: dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]],
    ) -> dict[str, object]:
        if self._preview_metrics_factory is not None:
            return self._preview_metrics_factory(selected)
        return _device_analysis_preview_metrics_from_selection(selected)


class DeviceAnalysisComparisonVariablesDialog(QDialog):
    """Choose snapshot variables shown in device-analysis comparisons."""

    def __init__(
        self,
        variable_names: tuple[str, ...],
        *,
        selected_names: tuple[str, ...],
        save_selected_names: Callable[[tuple[str, ...]], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Compare Variables")
        self.setModal(True)
        self._variable_names = variable_names
        self._selected_names = selected_names
        self._save_selected_names = save_selected_names
        _fit_dialog_to_screen(self, 460, 420, 360, 300)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.variableTable = QTableWidget(0, 2)
        self.variableTable.setObjectName("modbusDeviceAnalysisCompareVariablePicker")
        self.variableTable.setHorizontalHeaderLabels(["Show", "Variable"])
        self.variableTable.verticalHeader().setVisible(False)
        self.variableTable.setAlternatingRowColors(True)
        self.variableTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.variableTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        selected = set(self._selected_names)
        self.variableTable.setRowCount(len(self._variable_names))
        for row, variable_name in enumerate(self._variable_names):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if variable_name in selected
                else Qt.CheckState.Unchecked
            )
            self.variableTable.setItem(row, 0, check_item)
            variable_item = QTableWidgetItem(variable_name)
            variable_item.setFlags(variable_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.variableTable.setItem(row, 1, variable_item)
        root.addWidget(self.variableTable)

        self.buttonBox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.saveButton = self.buttonBox.button(QDialogButtonBox.StandardButton.Save)
        self.saveButton.setObjectName("modbusDeviceAnalysisCompareVariableSaveButton")
        self.buttonBox.accepted.connect(self.save_and_accept)
        self.buttonBox.rejected.connect(self.reject)
        root.addWidget(self.buttonBox)

    def selected_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.variableTable.rowCount()):
            check_item = self.variableTable.item(row, 0)
            variable_item = self.variableTable.item(row, 1)
            if (
                check_item is not None
                and variable_item is not None
                and check_item.checkState() == Qt.CheckState.Checked
            ):
                names.append(variable_item.text())
        return tuple(names)

    def save_and_accept(self) -> None:
        if self._save_selected_names is not None:
            self._save_selected_names(self.selected_names())
        self.accept()


class SnapshotSelectionDialog(QDialog):
    """Choose variables captured before each trial."""

    def __init__(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...],
        required_names: tuple[str, ...] = (),
        title: str = "Pre-test Snapshot",
        object_name: str = "modbusSnapshotSelectionTable",
        plot_layout: str | None = None,
        show_plot_layout: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        _fit_dialog_to_screen(self, 520, 420, 420, 320)
        self._registers = registers
        self._selected_names = selected_names
        self._required_names = required_names
        self._object_name = object_name
        self._plot_layout = _normalize_plot_layout(plot_layout)
        self._show_plot_layout = show_plot_layout
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.snapshotTable = QTableWidget(0, 5)
        self.snapshotTable.setObjectName(self._object_name)
        self.snapshotTable.setHorizontalHeaderLabels(
            ["Capture", "Variable", "Kind", "Address", "Type"]
        )
        self.snapshotTable.verticalHeader().setVisible(False)
        self.snapshotTable.setAlternatingRowColors(True)
        self.snapshotTable.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.snapshotTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.snapshotTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        selected = set(self._selected_names) | set(self._required_names)
        required = set(self._required_names)
        self.snapshotTable.setRowCount(len(self._registers))
        for row, register in enumerate(self._registers):
            check_item = QTableWidgetItem("")
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            if register.name in required:
                flags = Qt.ItemFlag.ItemIsEnabled
            check_item.setFlags(flags)
            check_item.setCheckState(
                Qt.CheckState.Checked
                if register.name in selected
                else Qt.CheckState.Unchecked
            )
            self.snapshotTable.setItem(row, 0, check_item)
            values = (
                register.name,
                register.kind.value,
                str(register.address),
                register.data_type.value,
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.snapshotTable.setItem(row, offset, item)
        root.addWidget(self.snapshotTable, 1)

        if self._show_plot_layout:
            self.plotLayoutCombo = QComboBox()
            self.plotLayoutCombo.setObjectName(
                "modbusRepeatabilityTrialSamplePlotLayoutCombo"
            )
            self.plotLayoutCombo.addItem(
                _plot_layout_label(PLOT_LAYOUT_OVERLAY),
                PLOT_LAYOUT_OVERLAY,
            )
            self.plotLayoutCombo.addItem(
                _plot_layout_label(PLOT_LAYOUT_SEPARATE),
                PLOT_LAYOUT_SEPARATE,
            )
            self.plotLayoutCombo.setCurrentIndex(
                self.plotLayoutCombo.findData(self._plot_layout)
            )
            form = QFormLayout()
            form.addRow("Plot Layout", self.plotLayoutCombo)
            root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.snapshotTable.rowCount()):
            check_item = self.snapshotTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            name = _table_text(self.snapshotTable, row, 1)
            if name:
                names.append(name)
        return tuple(names)

    def plot_layout(self) -> str:
        combo = getattr(self, "plotLayoutCombo", None)
        if isinstance(combo, QComboBox):
            return _normalize_plot_layout(combo.currentData())
        return self._plot_layout


class VariableSamplingDialog(QDialog):
    """Operator-controlled polling, plotting, and recording for selected variables."""

    startRequested = Signal()
    stopRequested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Variable Sampling")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        _fit_dialog_to_screen(self, 620, 520, 440, 360)
        self._registers: tuple[ModbusRegister, ...] = ()
        self.flowPlotDialog: RepeatabilityFlowPlotDialog | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        settings = QFormLayout()
        self.pollIntervalSpinBox = QDoubleSpinBox()
        self.pollIntervalSpinBox.setObjectName("modbusVariableSamplingIntervalSpinBox")
        self.pollIntervalSpinBox.setRange(0.05, 3600.0)
        self.pollIntervalSpinBox.setDecimals(3)
        self.pollIntervalSpinBox.setSingleStep(0.1)
        self.pollIntervalSpinBox.setValue(1.0)
        settings.addRow("Poll Interval (s)", self.pollIntervalSpinBox)

        self.plotLayoutCombo = QComboBox()
        self.plotLayoutCombo.setObjectName("modbusVariableSamplingPlotLayoutCombo")
        self.plotLayoutCombo.addItem(
            _plot_layout_label(PLOT_LAYOUT_OVERLAY),
            PLOT_LAYOUT_OVERLAY,
        )
        self.plotLayoutCombo.addItem(
            _plot_layout_label(PLOT_LAYOUT_SEPARATE),
            PLOT_LAYOUT_SEPARATE,
        )
        settings.addRow("Plot Layout", self.plotLayoutCombo)
        root.addLayout(settings)

        self.variableTable = QTableWidget(0, 5)
        self.variableTable.setObjectName("modbusVariableSamplingVariableTable")
        self.variableTable.setHorizontalHeaderLabels(
            ["Sample", "Variable", "Kind", "Address", "Unit"]
        )
        self.variableTable.verticalHeader().setVisible(False)
        self.variableTable.setAlternatingRowColors(True)
        self.variableTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.variableTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        root.addWidget(self.variableTable, 1)

        self.notesEdit = QTextEdit()
        self.notesEdit.setObjectName("modbusVariableSamplingNotesEdit")
        self.notesEdit.setPlaceholderText("Operation notes")
        self.notesEdit.setFixedHeight(72)
        root.addWidget(self.notesEdit)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("modbusVariableSamplingStatusLabel")
        self.statusLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.statusLabel)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.startButton = QPushButton("Start")
        self.startButton.setObjectName("modbusVariableSamplingStartButton")
        self.stopButton = QPushButton("Stop")
        self.stopButton.setObjectName("modbusVariableSamplingStopButton")
        self.stopButton.setEnabled(False)
        self.saveConfigButton = QPushButton("Save Config")
        self.saveConfigButton.setObjectName("modbusVariableSamplingSaveConfigButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusVariableSamplingCloseButton")
        buttons.addWidget(self.startButton)
        buttons.addWidget(self.stopButton)
        buttons.addWidget(self.saveConfigButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

        self.startButton.clicked.connect(self.startRequested.emit)
        self.stopButton.clicked.connect(self.stopRequested.emit)
        self.closeButton.clicked.connect(self.close)

    def set_registers(
        self,
        registers: tuple[ModbusRegister, ...],
        *,
        selected_names: tuple[str, ...],
    ) -> None:
        previous_selection = set(self.selected_variable_names())
        selected = set(selected_names) or previous_selection
        self._registers = registers
        self.variableTable.blockSignals(True)
        self.variableTable.setRowCount(len(registers))
        for row, register in enumerate(registers):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if register.name in selected
                else Qt.CheckState.Unchecked
            )
            self.variableTable.setItem(row, 0, check_item)
            values = (
                register.name,
                register.kind.value,
                str(register.address),
                register.unit or "",
            )
            for offset, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.variableTable.setItem(row, offset, item)
        self.variableTable.blockSignals(False)

    def selected_variable_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.variableTable.rowCount()):
            check_item = self.variableTable.item(row, 0)
            name = _table_text(self.variableTable, row, 1)
            if (
                check_item is not None
                and check_item.checkState() == Qt.CheckState.Checked
                and name
            ):
                names.append(name)
        return tuple(names)

    def poll_interval_s(self) -> float:
        return float(self.pollIntervalSpinBox.value())

    def plot_layout(self) -> str:
        return _normalize_plot_layout(self.plotLayoutCombo.currentData())

    def notes(self) -> str:
        return self.notesEdit.toPlainText().strip()

    def capture_settings(self) -> dict[str, object]:
        return {
            "variable_names": list(self.selected_variable_names()),
            "poll_interval_s": self.poll_interval_s(),
            "plot_layout": self.plot_layout(),
        }

    def apply_configuration(self, settings: dict[str, object]) -> None:
        names = settings.get("variable_names")
        if not isinstance(names, (list, tuple)):
            names = settings.get("sample_variable_names")
        if isinstance(names, (list, tuple)):
            available = {
                _table_text(self.variableTable, row, 1)
                for row in range(self.variableTable.rowCount())
            }
            selected = {str(name) for name in names if str(name) in available}
            if selected:
                for row in range(self.variableTable.rowCount()):
                    check_item = self.variableTable.item(row, 0)
                    if check_item is None:
                        continue
                    name = _table_text(self.variableTable, row, 1)
                    check_item.setCheckState(
                        Qt.CheckState.Checked
                        if name in selected
                        else Qt.CheckState.Unchecked
                    )
        poll_interval = settings.get("poll_interval_s")
        if isinstance(poll_interval, (int, float)):
            self.pollIntervalSpinBox.setValue(float(poll_interval))
        plot_layout = settings.get("plot_layout")
        index = self.plotLayoutCombo.findData(_normalize_plot_layout(plot_layout))
        if index >= 0:
            self.plotLayoutCombo.setCurrentIndex(index)

    def set_ready(self, *, connected: bool) -> None:
        self.startButton.setEnabled(connected)
        self.stopButton.setEnabled(False)
        self.saveConfigButton.setEnabled(True)
        self.variableTable.setEnabled(True)
        self.pollIntervalSpinBox.setEnabled(True)
        self.plotLayoutCombo.setEnabled(True)
        if self.statusLabel.text() == "Running...":
            self.statusLabel.setText("Ready" if connected else "Disconnected")

    def set_running(self) -> None:
        self.startButton.setEnabled(False)
        self.stopButton.setEnabled(True)
        self.saveConfigButton.setEnabled(False)
        self.variableTable.setEnabled(False)
        self.pollIntervalSpinBox.setEnabled(False)
        self.plotLayoutCombo.setEnabled(False)
        self.statusLabel.setText("Running...")

    def set_canceling(self) -> None:
        self.stopButton.setEnabled(False)
        self.saveConfigButton.setEnabled(False)
        self.statusLabel.setText("Stopping...")

    def set_status(self, message: str) -> None:
        self.statusLabel.setText(message)

    def set_error(self, message: str) -> None:
        self.statusLabel.setText(f"Error: {message}")

    def show_live_plot(
        self,
        *,
        variable_names: tuple[str, ...],
        units: dict[str, str],
        plot_layout: str,
    ) -> None:
        dialog = self._ensure_flow_plot_dialog()
        dialog.reset_trial(
            flow_parameter=variable_names[0] if variable_names else "variables",
            variable_names=variable_names,
            units=units,
            plot_layout=plot_layout,
            window_title="Variable Sampling",
            point_label="variable sampling",
        )

    def add_sample(self, captured_at: datetime, values: dict[str, object]) -> None:
        if self.flowPlotDialog is None or not isValid(self.flowPlotDialog):
            return
        self.flowPlotDialog.add_sample(captured_at, values)

    def _ensure_flow_plot_dialog(self) -> RepeatabilityFlowPlotDialog:
        if self.flowPlotDialog is None or not isValid(self.flowPlotDialog):
            self.flowPlotDialog = RepeatabilityFlowPlotDialog(parent=self)
        return self.flowPlotDialog


class CalibrationHistoryFlowPlotDialog(QDialog):
    """Non-modal multi-variable sample chart for saved repeatability trials."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trial Flow Samples")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 720, 420, 500, 300)
        self._series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...] = ()
        self._plot_layout = PLOT_LAYOUT_OVERLAY
        self._plot_alignment = PLOT_ALIGNMENT_FIRST_SAMPLE
        self._plot_widgets: dict[str, pg.PlotWidget] = {}
        self._plot_legends: dict[str, object] = {}
        self._overlay_right_axis_view: pg.ViewBox | None = None
        self._overlay_right_axis_variable: str | None = None
        self._point_items: list[object] = []
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.summaryLabel = QLabel("No samples")
        self.summaryLabel.setObjectName("modbusHistoryFlowPlotSummaryLabel")
        root.addWidget(self.summaryLabel)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Plot Layout"))
        self.plotLayoutCombo = QComboBox()
        self.plotLayoutCombo.setObjectName("modbusHistoryFlowPlotLayoutCombo")
        self.plotLayoutCombo.addItem(
            _plot_layout_label(PLOT_LAYOUT_OVERLAY),
            PLOT_LAYOUT_OVERLAY,
        )
        self.plotLayoutCombo.addItem(
            _plot_layout_label(PLOT_LAYOUT_SEPARATE),
            PLOT_LAYOUT_SEPARATE,
        )
        self.plotLayoutCombo.currentIndexChanged.connect(self._plot_layout_changed)
        controls.addWidget(self.plotLayoutCombo)
        controls.addWidget(QLabel("Alignment"))
        self.plotAlignmentCombo = QComboBox()
        self.plotAlignmentCombo.setObjectName("modbusHistoryFlowPlotAlignmentCombo")
        self.plotAlignmentCombo.addItem(
            _plot_alignment_label(PLOT_ALIGNMENT_FIRST_SAMPLE),
            PLOT_ALIGNMENT_FIRST_SAMPLE,
        )
        self.plotAlignmentCombo.addItem(
            _plot_alignment_label(PLOT_ALIGNMENT_PREFLOW_SAMPLE),
            PLOT_ALIGNMENT_PREFLOW_SAMPLE,
        )
        self.plotAlignmentCombo.currentIndexChanged.connect(
            self._plot_alignment_changed
        )
        controls.addWidget(self.plotAlignmentCombo)
        self.segmentLabel = QLabel("Segment")
        self.segmentCombo = QComboBox()
        self.segmentCombo.setObjectName("modbusHistoryFlowPlotSegmentCombo")
        self.segmentCombo.currentIndexChanged.connect(self._redraw)
        self.segmentLabel.hide()
        self.segmentCombo.hide()
        controls.addWidget(self.segmentLabel)
        controls.addWidget(self.segmentCombo)
        controls.addStretch(1)
        root.addLayout(controls)

        self.variableTable = QTableWidget(0, 3)
        self.variableTable.setObjectName("modbusHistoryFlowPlotVariableTable")
        self.variableTable.setHorizontalHeaderLabels(["Show", "Variable", "Unit"])
        self.variableTable.verticalHeader().setVisible(False)
        self.variableTable.setAlternatingRowColors(True)
        self.variableTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.variableTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.variableTable.itemChanged.connect(self._variable_selection_changed)
        root.addWidget(self.variableTable, 0)

        self.plotScrollArea = QScrollArea()
        self.plotScrollArea.setObjectName("modbusHistoryFlowPlotScrollArea")
        self.plotScrollArea.setWidgetResizable(True)
        self.plotPanel = QWidget()
        self.plotPanelLayout = QVBoxLayout(self.plotPanel)
        self.plotPanelLayout.setContentsMargins(0, 0, 0, 0)
        self.plotPanelLayout.setSpacing(8)
        self.plotScrollArea.setWidget(self.plotPanel)
        root.addWidget(self.plotScrollArea, 1)

        self.selectedPointLabel = QLabel("Selected Point: none")
        self.selectedPointLabel.setObjectName("modbusHistoryFlowPlotSelectedPointLabel")
        self.selectedPointLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.selectedPointLabel)

        self.flowPlot = pg.PlotWidget()
        self.flowPlot.setObjectName("modbusHistoryFlowPlot")

    def set_series(
        self,
        series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...],
    ) -> None:
        self._series_items = series_items
        self.selectedPointLabel.setText("Selected Point: none")
        if not series_items:
            self.summaryLabel.setText("No flow samples available.")
            self._clear_plot_panel()
            self.variableTable.setRowCount(0)
            return
        segment_ids = tuple(
            dict.fromkeys(
                segment
                for _label, series in series_items
                for segment in series.segment_ids()
            )
        )
        self.segmentCombo.blockSignals(True)
        self.segmentCombo.clear()
        if segment_ids:
            self.segmentCombo.addItem("All", "all")
            for segment in segment_ids:
                self.segmentCombo.addItem(f"Segment {segment}", segment)
            self.segmentCombo.setCurrentIndex(self.segmentCombo.count() - 1)
        self.segmentCombo.blockSignals(False)
        show_segments = any(series.segment_variable for _label, series in series_items)
        self.segmentLabel.setVisible(show_segments)
        self.segmentCombo.setVisible(show_segments)
        self.plotAlignmentCombo.setEnabled(
            not any(series.x_axis_variable for _label, series in series_items)
        )
        self._populate_variable_table(series_items)
        self._redraw()

    def set_plot_layout(self, plot_layout: str) -> None:
        normalized = _normalize_plot_layout(plot_layout)
        index = self.plotLayoutCombo.findData(normalized)
        if index >= 0 and index != self.plotLayoutCombo.currentIndex():
            self.plotLayoutCombo.setCurrentIndex(index)
            return
        self._plot_layout = normalized
        self._redraw()

    def _populate_variable_table(
        self,
        series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...],
    ) -> None:
        previous_selection = set(self._selected_variable_names())
        self.variableTable.blockSignals(True)
        variable_names = _series_variable_names(series_items)
        preserve_selection = bool(previous_selection.intersection(variable_names))
        units = _series_units(series_items)
        self.variableTable.setRowCount(len(variable_names))
        for row, name in enumerate(variable_names):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked
                if (
                    (preserve_selection and name in previous_selection)
                    or (not preserve_selection and row == 0)
                )
                else Qt.CheckState.Unchecked
            )
            self.variableTable.setItem(row, 0, check_item)
            variable_item = QTableWidgetItem(name)
            variable_item.setFlags(variable_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.variableTable.setItem(row, 1, variable_item)
            unit_item = QTableWidgetItem(units.get(name, ""))
            unit_item.setFlags(unit_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.variableTable.setItem(row, 2, unit_item)
        self.variableTable.blockSignals(False)

    def _selected_variable_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in range(self.variableTable.rowCount()):
            check_item = self.variableTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            name = _table_text(self.variableTable, row, 1)
            if name:
                names.append(name)
        return tuple(names)

    def _variable_selection_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0:
            self._redraw()

    def _plot_layout_changed(self) -> None:
        self._plot_layout = _normalize_plot_layout(self.plotLayoutCombo.currentData())
        self._redraw()

    def _plot_alignment_changed(self) -> None:
        self._plot_alignment = _normalize_plot_alignment(
            self.plotAlignmentCombo.currentData()
        )
        self._redraw()

    def _redraw(self) -> None:
        series_items = self._series_items
        selected_variables = self._selected_variable_names()
        if not selected_variables and self.variableTable.rowCount() > 0:
            first_item = self.variableTable.item(0, 0)
            if first_item is not None:
                first_item.setCheckState(Qt.CheckState.Checked)
            selected_variables = self._selected_variable_names()

        self._clear_plot_panel()
        self.selectedPointLabel.setText("Selected Point: none")
        if not series_items or not selected_variables:
            self.summaryLabel.setText("Select at least one variable.")
            return

        units = _series_units(series_items)
        uses_device_axis = any(
            series.x_axis_variable for _label, series in series_items
        )
        x_axis_unit = next(
            (
                series.x_axis_unit or ""
                for _label, series in series_items
                if series.x_axis_variable
            ),
            "",
        )
        selected_units = {units.get(name, "") for name in selected_variables if units.get(name, "")}
        left_unit = selected_units.pop() if len(selected_units) == 1 else ""
        overlay_axis_by_variable: dict[str, str] = {}
        if self._plot_layout == PLOT_LAYOUT_SEPARATE:
            for index, variable_name in enumerate(selected_variables):
                object_name = (
                    "modbusHistoryFlowPlot"
                    if index == 0
                    else f"modbusHistoryFlowPlot_{_object_name_token(variable_name)}"
                )
                plot_widget = self._new_plot_widget(
                    object_name=object_name
                )
                unit = units.get(variable_name, "")
                plot_widget.setTitle(variable_name)
                plot_widget.setLabel(
                    "bottom",
                    "Device Time" if uses_device_axis else "Aligned Time",
                    units=x_axis_unit or ("s" if not uses_device_axis else None),
                )
                plot_widget.setLabel("left", variable_name, units=unit or None)
                self._plot_widgets[variable_name] = plot_widget
                self._plot_legends[variable_name] = plot_widget.addLegend(
                    offset=(8, 8)
                )
                self.plotPanelLayout.addWidget(plot_widget, 1)
            if selected_variables:
                self.flowPlot = self._plot_widgets[selected_variables[0]]
        else:
            plot_widget = self._new_plot_widget(object_name="modbusHistoryFlowPlot")
            self.flowPlot = plot_widget
            self._plot_widgets["overlay"] = plot_widget
            self._plot_legends["overlay"] = plot_widget.addLegend(offset=(8, 8))
            plot_widget.setLabel(
                "bottom",
                "Device Time" if uses_device_axis else "Aligned Time",
                units=x_axis_unit or ("s" if not uses_device_axis else None),
            )
            if len(selected_variables) == 2:
                left_variable, right_variable = selected_variables
                plot_widget.setLabel(
                    "left",
                    left_variable,
                    units=units.get(left_variable, "") or None,
                )
                self._setup_overlay_right_axis(
                    plot_widget,
                    variable_name=right_variable,
                    unit=units.get(right_variable, ""),
                )
                overlay_axis_by_variable = {
                    left_variable: "left",
                    right_variable: "right",
                }
            else:
                plot_widget.setLabel("left", "Value", units=left_unit or None)
            self.plotPanelLayout.addWidget(plot_widget, 1)

        colors = _plot_colors()
        max_elapsed = 0.0
        sample_count = 0
        curve_index = 0
        for index, (label, series) in enumerate(series_items):
            timestamps = [sample.captured_at for sample in series.points]
            if not timestamps:
                continue
            if series.x_axis_variable:
                x_values = list(series.x_values())
            else:
                start = _flow_alignment_timestamp(series, self._plot_alignment) or timestamps[0]
                x_values = [
                    (timestamp - start).total_seconds()
                    for timestamp in timestamps
                ]
            numeric_x = [value for value in x_values if value is not None]
            if numeric_x:
                max_elapsed = max(max_elapsed, max(numeric_x) - min(numeric_x))
            sample_count += len(timestamps)
            segment_values = series.segment_values()
            selected_segment = self.segmentCombo.currentData() if series.segment_variable else "all"
            available_segments = series.segment_ids() or ("1",)
            for variable_name in selected_variables:
                y_values = series.values_for(variable_name)
                for segment in available_segments:
                    if selected_segment not in (None, "all", segment):
                        continue
                    indexes = [
                        point_index
                        for point_index, segment_value in enumerate(segment_values)
                        if segment_value == segment
                        and point_index < len(x_values)
                        and point_index < len(y_values)
                        and x_values[point_index] is not None
                        and y_values[point_index] is not None
                    ]
                    if not indexes:
                        continue
                    valid_x = [float(x_values[point_index]) for point_index in indexes]
                    valid_y = [float(y_values[point_index]) for point_index in indexes]
                    pen = pg.mkPen(colors[curve_index % len(colors)], width=2)
                    color = colors[curve_index % len(colors)]
                    curve_index += 1
                    curve_label = (
                        label
                        if len(selected_variables) == 1
                        else f"{label} - {variable_name}"
                    )
                    if series.segment_variable and len(available_segments) > 1:
                        curve_label += f" - segment {segment}"
                    plot_key = (
                        variable_name
                        if self._plot_layout == PLOT_LAYOUT_SEPARATE
                        else "overlay"
                    )
                    plot_widget = self._plot_widgets.get(plot_key)
                    if plot_widget is None:
                        continue
                    axis_side = overlay_axis_by_variable.get(variable_name, "left")
                    self._plot_history_curve(
                        plot_widget,
                        valid_x,
                        valid_y,
                        pen=pen,
                        name=curve_label,
                        axis_side=axis_side,
                    )
                    unit = units.get(variable_name, "")
                    spots = [
                        {
                            "pos": (float(x_values[point_index]), float(y_values[point_index])),
                            "data": {
                                "label": label,
                                "variable": variable_name,
                                "elapsed_s": x_values[point_index],
                                "captured_at": timestamps[point_index],
                                "value": y_values[point_index],
                                "unit": unit,
                                "sample_index": point_index + 1,
                                "segment": segment,
                            },
                        }
                        for point_index in indexes
                    ]
                    point_item = self._new_point_item(color=color)
                    point_item.setData(spots)
                    self._point_items.append(point_item)
                    if axis_side == "right" and self._overlay_right_axis_view is not None:
                        self._overlay_right_axis_view.addItem(point_item)
                    else:
                        plot_widget.addItem(point_item)

        for plot_widget in self._plot_widgets.values():
            plot_widget.plotItem.enableAutoRange(x=True, y=True)
        if self._overlay_right_axis_view is not None:
            self._overlay_right_axis_view.enableAutoRange(x=True, y=True)
        if len(series_items) == 1:
            label, series = series_items[0]
            self.setWindowTitle(f"Trial Flow Samples - {label}")
            latest_parts = []
            for variable_name in selected_variables:
                values = [value for value in series.values_for(variable_name) if value is not None]
                if values:
                    unit = f" {units.get(variable_name, '')}" if units.get(variable_name, "") else ""
                    latest_parts.append(f"{variable_name}={values[-1]:.6g}{unit}")
            self.summaryLabel.setText(
                f"{len(series.points)} samples | span {max_elapsed:.3g} "
                f"{x_axis_unit or 's'} | "
                f"{_plot_layout_label(self._plot_layout)} | "
                f"aligned at {_plot_alignment_label(self._plot_alignment).lower()} | "
                "latest "
                + ", ".join(latest_parts)
            )
            return

        self.setWindowTitle("Compare Trial Flow Samples")
        self.summaryLabel.setText(
            f"{len(series_items)} trials | {sample_count} samples | "
            f"{len(selected_variables)} variable(s) | "
            f"{_plot_layout_label(self._plot_layout)} | "
            f"aligned at {_plot_alignment_label(self._plot_alignment).lower()}"
        )

    def _setup_overlay_right_axis(
        self,
        plot_widget: pg.PlotWidget,
        *,
        variable_name: str,
        unit: str,
    ) -> None:
        plot_item = plot_widget.plotItem
        right_view = pg.ViewBox()
        self._overlay_right_axis_view = right_view
        self._overlay_right_axis_variable = variable_name
        plot_item.showAxis("right")
        plot_item.scene().addItem(right_view)
        plot_item.getAxis("right").linkToView(right_view)
        plot_item.getAxis("right").setLabel(variable_name, units=unit or None)
        right_view.setXLink(plot_item)

        def update_right_view() -> None:
            right_view.setGeometry(plot_item.vb.sceneBoundingRect())
            right_view.linkedViewChanged(plot_item.vb, right_view.XAxis)

        update_right_view()
        plot_item.vb.sigResized.connect(update_right_view)

    def _plot_history_curve(
        self,
        plot_widget: pg.PlotWidget,
        x_values: list[float],
        y_values: list[float],
        *,
        pen: object,
        name: str,
        axis_side: str,
    ) -> object:
        if axis_side == "right" and self._overlay_right_axis_view is not None:
            curve = pg.PlotDataItem(x_values, y_values, pen=pen, name=name)
            self._overlay_right_axis_view.addItem(curve)
            legend = self._plot_legends.get("overlay")
            if legend is not None:
                legend.addItem(curve, name)
            return curve
        return plot_widget.plot(x_values, y_values, pen=pen, name=name)

    def _new_plot_widget(self, *, object_name: str) -> pg.PlotWidget:
        plot_widget = pg.PlotWidget()
        plot_widget.setObjectName(object_name)
        plot_widget.setBackground("w")
        plot_widget.showGrid(x=True, y=True, alpha=0.25)
        plot_widget.setMinimumHeight(180)
        return plot_widget

    def _new_point_item(self, *, color: str):
        item = pg.ScatterPlotItem(
            [],
            [],
            size=8,
            brush=pg.mkBrush(color),
            pen=pg.mkPen("w", width=1),
            hoverable=True,
        )
        item.sigClicked.connect(self._point_clicked)
        return item

    def _point_clicked(self, _item, points, *_args) -> None:
        if not points:
            return
        data = points[0].data()
        if isinstance(data, dict):
            self.selectedPointLabel.setText(
                "Selected Point: " + _format_plot_point_data(data)
            )

    def _clear_plot_panel(self) -> None:
        while self.plotPanelLayout.count():
            item = self.plotPanelLayout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._plot_widgets.clear()
        self._plot_legends.clear()
        self._overlay_right_axis_view = None
        self._overlay_right_axis_variable = None
        self._point_items.clear()


class CalibrationHistoryFlowSampleTableDialog(QDialog):
    """Non-modal table view for saved repeatability trial samples."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Trial Flow Sample Data")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowType.WindowMinimizeButtonHint, True)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 760, 460, 520, 320)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.summaryLabel = QLabel("No samples")
        self.summaryLabel.setObjectName("modbusHistoryFlowSampleTableSummaryLabel")
        root.addWidget(self.summaryLabel)

        self.sampleTable = QTableWidget(0, 0)
        self.sampleTable.setObjectName("modbusHistoryFlowSampleDataTable")
        self.sampleTable.setAlternatingRowColors(True)
        self.sampleTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sampleTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.sampleTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.sampleTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.sampleTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        root.addWidget(self.sampleTable, 1)

    def set_series(self, label: str, series: ModbusFlowSampleSeries) -> None:
        variable_names = _unique_names(
            (
                *series.variable_names,
                *(
                    name
                    for point in series.points
                    for name in point.values
                ),
            )
        )
        headers = ("captured_at_local", "elapsed_s", "sample_index", *variable_names)
        self.sampleTable.setColumnCount(len(headers))
        self.sampleTable.setHorizontalHeaderLabels(headers)
        self.sampleTable.setRowCount(len(series.points))
        start = series.points[0].captured_at if series.points else None
        for row, point in enumerate(series.points):
            elapsed = (
                max(0.0, (point.captured_at - start).total_seconds())
                if start is not None
                else 0.0
            )
            values: list[str] = [
                _format_datetime(point.captured_at),
                f"{elapsed:.9g}",
                str(row),
            ]
            values.extend(_format_value(point.values.get(name, "")) for name in variable_names)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.sampleTable.setItem(row, column, item)
        self.sampleTable.resizeColumnsToContents()
        self.setWindowTitle(f"Trial Flow Sample Data - {label}")
        self.summaryLabel.setText(
            f"{len(series.points)} samples | {len(variable_names)} variable(s) | {label}"
        )


class CalibrationHistoryFlowCompareSelectionDialog(QDialog):
    """Choose saved trial sample artifacts before comparing curves."""

    def __init__(
        self,
        items: tuple[tuple[str, str], ...],
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Flow Plots To Compare")
        self.setObjectName("modbusHistoryFlowCompareSelectionDialog")
        self.setModal(True)
        _fit_dialog_to_screen(self, 520, 420, 420, 300)
        self._items = items
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.selectionTable = QTableWidget(0, 3)
        self.selectionTable.setObjectName("modbusHistoryFlowCompareSelectionTable")
        self.selectionTable.setHorizontalHeaderLabels(["Compare", "Trial", "Artifact"])
        self.selectionTable.verticalHeader().setVisible(False)
        self.selectionTable.setAlternatingRowColors(True)
        self.selectionTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.selectionTable.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.selectionTable.setRowCount(len(self._items))
        for row, (artifact_id, label) in enumerate(self._items):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(
                Qt.CheckState.Checked if row < 2 else Qt.CheckState.Unchecked
            )
            self.selectionTable.setItem(row, 0, check_item)
            label_item = QTableWidgetItem(label)
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.selectionTable.setItem(row, 1, label_item)
            artifact_item = QTableWidgetItem(artifact_id)
            artifact_item.setFlags(artifact_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.selectionTable.setItem(row, 2, artifact_item)
        root.addWidget(self.selectionTable, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected_items(self) -> tuple[tuple[str, str], ...]:
        values: list[tuple[str, str]] = []
        for row in range(self.selectionTable.rowCount()):
            check_item = self.selectionTable.item(row, 0)
            if check_item is None or check_item.checkState() != Qt.CheckState.Checked:
                continue
            artifact_id = _table_text(self.selectionTable, row, 2)
            label = _table_text(self.selectionTable, row, 1)
            if artifact_id:
                values.append((artifact_id, label or artifact_id))
        return tuple(values)

    def _accept_if_valid(self) -> None:
        if len(self.selected_items()) < 2:
            QMessageBox.information(
                self,
                "Flow Samples",
                "Select at least two trials to compare.",
            )
            return
        self.accept()


class CalibrationHistoryExportDialog(QDialog):
    """Select operation and started-at range before exporting test records."""

    def __init__(
        self,
        *,
        operation: str | None = "all",
        entries: tuple[ModbusCalibrationHistoryEntry, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Test Records")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._build_ui()
        self.set_operation(operation)
        self.set_default_range(entries)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.operationCombo = QComboBox()
        self.operationCombo.setObjectName("modbusHistoryExportOperationCombo")
        for label, value in CalibrationHistoryDialog.OPERATIONS:
            self.operationCombo.addItem(label, value)
        form.addRow("Operation", self.operationCombo)

        self.fromCheckBox = QCheckBox("From")
        self.fromCheckBox.setObjectName("modbusHistoryExportFromCheckBox")
        self.fromDateTimeEdit = QDateTimeEdit()
        self.fromDateTimeEdit.setObjectName("modbusHistoryExportFromDateTime")
        self.fromDateTimeEdit.setCalendarPopup(True)
        self.fromDateTimeEdit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        from_row = QHBoxLayout()
        from_row.addWidget(self.fromCheckBox)
        from_row.addWidget(self.fromDateTimeEdit, 1)
        form.addRow("Started", from_row)

        self.toCheckBox = QCheckBox("To")
        self.toCheckBox.setObjectName("modbusHistoryExportToCheckBox")
        self.toDateTimeEdit = QDateTimeEdit()
        self.toDateTimeEdit.setObjectName("modbusHistoryExportToDateTime")
        self.toDateTimeEdit.setCalendarPopup(True)
        self.toDateTimeEdit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        to_row = QHBoxLayout()
        to_row.addWidget(self.toCheckBox)
        to_row.addWidget(self.toDateTimeEdit, 1)
        form.addRow("", to_row)
        root.addLayout(form)

        self.fromCheckBox.toggled.connect(self.fromDateTimeEdit.setEnabled)
        self.toCheckBox.toggled.connect(self.toDateTimeEdit.setEnabled)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def set_operation(self, operation: str | None) -> None:
        operation_value = operation if operation not in (None, "") else "all"
        index = self.operationCombo.findData(operation_value)
        if index >= 0:
            self.operationCombo.setCurrentIndex(index)

    def set_default_range(
        self,
        entries: tuple[ModbusCalibrationHistoryEntry, ...],
    ) -> None:
        started_values = [entry.started_at for entry in entries if entry.started_at]
        if started_values:
            from_value = min(started_values)
            to_value = max(started_values)
        else:
            now = datetime.now().astimezone()
            from_value = now.replace(hour=0, minute=0, second=0, microsecond=0)
            to_value = now
        self.fromDateTimeEdit.setDateTime(_qt_datetime_from_datetime(from_value))
        self.toDateTimeEdit.setDateTime(_qt_datetime_from_datetime(to_value))
        self.fromCheckBox.setChecked(False)
        self.toCheckBox.setChecked(False)
        self.fromDateTimeEdit.setEnabled(False)
        self.toDateTimeEdit.setEnabled(False)

    def selected_operation(self) -> str:
        value = self.operationCombo.currentData()
        return str(value) if value else "all"

    def selected_started_from(self) -> datetime | None:
        if not self.fromCheckBox.isChecked():
            return None
        return _datetime_from_qt_datetime(self.fromDateTimeEdit.dateTime())

    def selected_started_to(self) -> datetime | None:
        if not self.toCheckBox.isChecked():
            return None
        return _datetime_from_qt_datetime(self.toDateTimeEdit.dateTime())


class CalibrationHistoryDialog(QDialog):
    """Historical Modbus test record table with editable notes."""

    exportRequested = Signal(object)
    importRequested = Signal()

    TIME_COLUMN = 0
    OPERATION_COLUMN = 1
    RUN_ID_COLUMN = 2
    PARAMETER_COLUMN = 3
    NOTES_COLUMN = 4

    OPERATIONS = (
        ("All", "all"),
        ("Variable Sampling", "modbus_variable_sampling"),
        ("Zero Monitor", "modbus_zero_monitor"),
        ("Zero Calibration", "zero_calibration"),
        ("K Factor", "k_factor_calibration"),
        ("Repeatability", "manual_error_repeatability"),
        ("Repeatability Final K", "manual_error_repeatability_final_k"),
    )

    def __init__(
        self,
        runtime: ModbusModuleRuntime,
        *,
        device_id: str | None = None,
        scope_label: str = "All Devices",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._fixed_device_id = device_id
        self._scope_label = scope_label
        self._loading = False
        self._entries: tuple[ModbusCalibrationHistoryEntry, ...] = ()
        self._flow_plot_dialog: CalibrationHistoryFlowPlotDialog | None = None
        self._flow_sample_table_dialog: CalibrationHistoryFlowSampleTableDialog | None = None
        if device_id is None:
            self.setWindowTitle("All Test Records")
        else:
            self.setWindowTitle(f"Current Device Test Records - {device_id}")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 920, 600, 680, 380)
        self._build_ui()
        self._connect_signals()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        filters = QVBoxLayout()
        filter_top = QHBoxLayout()
        filter_top.addWidget(QLabel("Operation"))
        self.operationCombo = QComboBox()
        self.operationCombo.setObjectName("modbusHistoryOperationCombo")
        for label, value in self.OPERATIONS:
            self.operationCombo.addItem(label, value)
        self.deviceIdFilterLineEdit = QLineEdit()
        self.deviceIdFilterLineEdit.setObjectName("modbusHistoryDeviceIdFilter")
        self.deviceIdFilterLineEdit.setPlaceholderText("Device ID")
        if self._fixed_device_id is not None:
            self.deviceIdFilterLineEdit.setText(self._fixed_device_id)
            self.deviceIdFilterLineEdit.setEnabled(False)
        self.deviceModelFilterLineEdit = QLineEdit()
        self.deviceModelFilterLineEdit.setObjectName("modbusHistoryDeviceModelFilter")
        self.deviceModelFilterLineEdit.setPlaceholderText("Device Model")
        self.tubeModelFilterLineEdit = QLineEdit()
        self.tubeModelFilterLineEdit.setObjectName("modbusHistoryTubeModelFilter")
        self.tubeModelFilterLineEdit.setPlaceholderText("Tube Model")
        self.transmitterModelFilterLineEdit = QLineEdit()
        self.transmitterModelFilterLineEdit.setObjectName(
            "modbusHistoryTransmitterModelFilter"
        )
        self.transmitterModelFilterLineEdit.setPlaceholderText("Transmitter Model")
        self.sessionFilterLineEdit = QLineEdit()
        self.sessionFilterLineEdit.setObjectName("modbusHistorySessionFilter")
        self.sessionFilterLineEdit.setPlaceholderText("Session")
        self.statusFilterCombo = QComboBox()
        self.statusFilterCombo.setObjectName("modbusHistoryStatusFilter")
        for label, value in (
            ("Any Status", "all"),
            ("Accepted", "accepted"),
            ("Captured", "captured"),
            ("Calculated", "calculated"),
            ("Passed", "passed"),
            ("Failed", "failed"),
            ("Rejected", "rejected"),
            ("Diagnostic", "diagnostic"),
        ):
            self.statusFilterCombo.addItem(label, value)
        self.refreshButton = QPushButton("Refresh")
        self.refreshButton.setObjectName("modbusHistoryRefreshButton")
        self.showFlowPlotButton = QPushButton("View Flow Plot")
        self.showFlowPlotButton.setObjectName("modbusHistoryShowFlowPlotButton")
        self.showFlowPlotButton.setEnabled(False)
        self.showFlowDataButton = QPushButton("View Flow Data")
        self.showFlowDataButton.setObjectName("modbusHistoryShowFlowDataButton")
        self.showFlowDataButton.setEnabled(False)
        self.compareFlowPlotsButton = QPushButton("Compare Flow Plots")
        self.compareFlowPlotsButton.setObjectName(
            "modbusHistoryCompareFlowPlotsButton"
        )
        self.compareFlowPlotsButton.setEnabled(False)
        self.importButton = QPushButton("Import...")
        self.importButton.setObjectName("modbusHistoryImportButton")
        self.exportButton = QPushButton("Export...")
        self.exportButton.setObjectName("modbusHistoryExportButton")
        filter_top.addWidget(self.operationCombo)
        filter_top.addWidget(self.statusFilterCombo)
        filter_top.addWidget(self.deviceIdFilterLineEdit)
        filter_top.addStretch(1)
        filter_top.addWidget(self.showFlowPlotButton)
        filter_top.addWidget(self.showFlowDataButton)
        filter_top.addWidget(self.compareFlowPlotsButton)
        filter_top.addWidget(self.importButton)
        filter_top.addWidget(self.exportButton)
        filter_top.addWidget(self.refreshButton)
        filter_bottom = QHBoxLayout()
        filter_bottom.addWidget(self.deviceModelFilterLineEdit)
        filter_bottom.addWidget(self.tubeModelFilterLineEdit)
        filter_bottom.addWidget(self.transmitterModelFilterLineEdit)
        filter_bottom.addWidget(self.sessionFilterLineEdit)
        filters.addLayout(filter_top)
        filters.addLayout(filter_bottom)
        root.addLayout(filters)

        self.historyTable = QTableWidget(0, 5)
        self.historyTable.setObjectName("modbusCalibrationHistoryTable")
        self.historyTable.setHorizontalHeaderLabels(
            [
                "Time",
                "Operation",
                "Run ID",
                "Parameter",
                "Operation Note",
            ]
        )
        self.historyTable.verticalHeader().setVisible(False)
        self.historyTable.setAlternatingRowColors(True)
        self.historyTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems
        )
        self.historyTable.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.historyTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.historyTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.historyTable.setColumnWidth(0, 155)
        self.historyTable.setColumnWidth(1, 170)
        self.historyTable.setColumnWidth(2, 180)
        self.historyTable.setColumnWidth(3, 320)
        self.historyTable.setColumnWidth(4, 260)
        self.historyTable.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(6)
        self.detailTitleLabel = QLabel("Operation Detail")
        self.detailTitleLabel.setObjectName("modbusCalibrationHistoryDetailTitle")
        self.detailTextEdit = QTextEdit()
        self.detailTextEdit.setObjectName("modbusCalibrationHistoryDetailText")
        self.detailTextEdit.setReadOnly(True)
        self.detailTextEdit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.detailTextEdit.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        detail_layout.addWidget(self.detailTitleLabel)
        detail_layout.addWidget(self.detailTextEdit, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.historyTable)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.copyAction = QAction("Copy", self)
        self.copyAction.setObjectName("modbusHistoryCopyAction")
        self.copyAction.setShortcut(QKeySequence.StandardKey.Copy)
        self.copyAction.setShortcutContext(
            Qt.ShortcutContext.WidgetWithChildrenShortcut
        )
        self.historyTable.addAction(self.copyAction)
        self.detailTextEdit.addAction(self.copyAction)

    def _connect_signals(self) -> None:
        self.operationCombo.currentIndexChanged.connect(self.refresh)
        self.statusFilterCombo.currentIndexChanged.connect(self.refresh)
        self.deviceIdFilterLineEdit.returnPressed.connect(self.refresh)
        self.deviceModelFilterLineEdit.returnPressed.connect(self.refresh)
        self.tubeModelFilterLineEdit.returnPressed.connect(self.refresh)
        self.transmitterModelFilterLineEdit.returnPressed.connect(self.refresh)
        self.sessionFilterLineEdit.returnPressed.connect(self.refresh)
        self.refreshButton.clicked.connect(self.refresh)
        self.showFlowPlotButton.clicked.connect(self.show_selected_flow_plot)
        self.showFlowDataButton.clicked.connect(self.show_selected_flow_data)
        self.compareFlowPlotsButton.clicked.connect(self.compare_selected_flow_plots)
        self.importButton.clicked.connect(self.importRequested.emit)
        self.exportButton.clicked.connect(
            lambda: self.exportRequested.emit(
                {
                    "operation": self.operationCombo.currentData(),
                    "device_id": self._fixed_device_id,
                }
            )
        )
        self.copyAction.triggered.connect(self.copy_selection)
        self.historyTable.customContextMenuRequested.connect(
            lambda point: self._show_context_menu(self.historyTable, point)
        )
        self.detailTextEdit.customContextMenuRequested.connect(
            self._show_detail_context_menu
        )
        self.historyTable.itemSelectionChanged.connect(self._history_selection_changed)
        self.historyTable.itemChanged.connect(self._note_changed)

    def refresh(self) -> None:
        operation = self.operationCombo.currentData()
        try:
            self._entries = self.runtime.list_test_records(
                operation=operation,
                device_id=self._fixed_device_id
                or self.deviceIdFilterLineEdit.text().strip()
                or None,
                device_model=self.deviceModelFilterLineEdit.text().strip() or None,
                tube_model=self.tubeModelFilterLineEdit.text().strip() or None,
                transmitter_model=self.transmitterModelFilterLineEdit.text().strip()
                or None,
                session_id=self.sessionFilterLineEdit.text().strip() or None,
                status=str(self.statusFilterCombo.currentData() or "all"),
            )
        except Exception as exc:
            self._entries = ()
            self._loading = True
            self.historyTable.setRowCount(0)
            self._loading = False
            self.detailTitleLabel.setText("Load Error")
            self.detailTextEdit.setPlainText(
                f"Failed to load test records:\n{exc}"
            )
            return
        self._loading = True
        self.historyTable.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            self._populate_row(row, entry)
        self._loading = False
        if self._entries:
            self._populate_detail(self._entries[0])
        else:
            self.detailTitleLabel.setText("Operation Detail")
            self.detailTextEdit.clear()
        self._update_flow_plot_actions()

    def _populate_row(self, row: int, entry: ModbusCalibrationHistoryEntry) -> None:
        values = (
            _format_datetime(entry.started_at),
            _operation_label(entry.operation),
            entry.run_id,
            _history_parameter_summary(entry),
            _history_entry_notes(entry),
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, entry.run_id)
            if column != self.NOTES_COLUMN:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.historyTable.setItem(row, column, item)

    def copy_selection(self) -> None:
        focus = QApplication.focusWidget()
        if focus is self.detailTextEdit or self.detailTextEdit.isAncestorOf(focus):
            text = self.detailTextEdit.textCursor().selectedText()
            if not text:
                text = self.detailTextEdit.toPlainText()
            text = text.replace("\u2029", "\n")
        else:
            text = self._selected_table_text(self.historyTable)
        if text:
            QApplication.clipboard().setText(text)

    def _show_context_menu(self, table: QTableWidget, point) -> None:
        self.copyAction.setEnabled(bool(table.selectedIndexes()))
        menu = QMenu(self)
        menu.addAction(self.copyAction)
        menu.exec(table.viewport().mapToGlobal(point))

    def _show_detail_context_menu(self, point) -> None:
        self.copyAction.setEnabled(bool(self.detailTextEdit.toPlainText()))
        menu = QMenu(self)
        menu.addAction(self.copyAction)
        menu.exec(self.detailTextEdit.viewport().mapToGlobal(point))

    def _selected_table_text(self, table: QTableWidget) -> str:
        indexes = table.selectedIndexes()
        if not indexes:
            return ""
        selected = {(index.row(), index.column()) for index in indexes}
        rows = sorted({row for row, _column in selected})
        columns = sorted({column for _row, column in selected})
        lines: list[str] = []
        for row in rows:
            values: list[str] = []
            for column in columns:
                if (row, column) not in selected:
                    values.append("")
                    continue
                values.append(_table_text(table, row, column))
            lines.append("\t".join(values))
        return "\n".join(lines)

    def _history_selection_changed(self) -> None:
        if self._loading:
            return
        indexes = self.historyTable.selectedIndexes()
        if not indexes:
            self.detailTitleLabel.setText("Operation Detail")
            self.detailTextEdit.clear()
            self._update_flow_plot_actions()
            return
        row = min(index.row() for index in indexes)
        if 0 <= row < len(self._entries):
            self._populate_detail(self._entries[row])
        self._update_flow_plot_actions()

    def _populate_detail(self, entry: ModbusCalibrationHistoryEntry) -> None:
        self.detailTitleLabel.setText(
            f"{_operation_label(entry.operation)} Detail - {entry.run_id}"
        )
        self.detailTextEdit.setPlainText(_history_detail_text(entry))

    def _note_changed(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != self.NOTES_COLUMN:
            return
        run_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(run_id, str) or not run_id:
            return
        self.runtime.update_calibration_history_note(run_id, item.text())

    def show_selected_flow_plot(self) -> None:
        series_items = self._selected_flow_series(limit=1)
        if not series_items:
            QMessageBox.information(
                self,
                "Flow Samples",
                "The selected record has no saved flow-sample artifact.",
            )
            return
        self._show_flow_series(series_items)

    def show_selected_flow_data(self) -> None:
        series_items = self._selected_flow_series(limit=1)
        if not series_items:
            QMessageBox.information(
                self,
                "Flow Samples",
                "The selected record has no saved flow-sample artifact.",
            )
            return
        label, series = series_items[0]
        dialog = self._ensure_flow_sample_table_dialog()
        dialog.set_series(label, series)
        dialog.showNormal()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def compare_selected_flow_plots(self) -> None:
        items = self._selected_flow_artifact_items()
        if len(items) < 2:
            QMessageBox.information(
                self,
                "Flow Samples",
                "Select at least two saved flow-sample records to compare.",
            )
            return
        selection = CalibrationHistoryFlowCompareSelectionDialog(items, parent=self)
        if selection.exec() != QDialog.DialogCode.Accepted:
            return
        series_items = self._flow_series_from_items(selection.selected_items())
        if len(series_items) < 2:
            return
        self._show_flow_series(series_items)

    def _show_flow_series(
        self,
        series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...],
    ) -> None:
        dialog = self._ensure_flow_plot_dialog()
        dialog.set_series(series_items)
        dialog.showNormal()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _ensure_flow_plot_dialog(self) -> CalibrationHistoryFlowPlotDialog:
        if self._flow_plot_dialog is None or not isValid(self._flow_plot_dialog):
            self._flow_plot_dialog = CalibrationHistoryFlowPlotDialog(parent=self)
            self._flow_plot_dialog.destroyed.connect(
                lambda: setattr(self, "_flow_plot_dialog", None)
            )
        return self._flow_plot_dialog

    def _ensure_flow_sample_table_dialog(
        self,
    ) -> CalibrationHistoryFlowSampleTableDialog:
        if (
            self._flow_sample_table_dialog is None
            or not isValid(self._flow_sample_table_dialog)
        ):
            self._flow_sample_table_dialog = CalibrationHistoryFlowSampleTableDialog(
                parent=self
            )
            self._flow_sample_table_dialog.destroyed.connect(
                lambda: setattr(self, "_flow_sample_table_dialog", None)
            )
        return self._flow_sample_table_dialog

    def _selected_flow_series(
        self,
        *,
        limit: int | None,
    ) -> tuple[tuple[str, ModbusFlowSampleSeries], ...]:
        items = self._selected_flow_artifact_items()
        if limit is not None:
            items = items[:limit]
        return self._flow_series_from_items(items)

    def _selected_flow_artifact_items(self) -> tuple[tuple[str, str], ...]:
        items: list[tuple[str, str]] = []
        for entry in self._selected_entries_with_flow_samples():
            items.extend(_history_flow_sample_artifacts(entry))
        unique: list[tuple[str, str]] = []
        seen: set[str] = set()
        for artifact_id, label in items:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            unique.append((artifact_id, label))
        return tuple(unique)

    def _flow_series_from_items(
        self,
        items: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, ModbusFlowSampleSeries], ...]:
        series_items: list[tuple[str, ModbusFlowSampleSeries]] = []
        errors: list[str] = []
        for artifact_id, label in items:
            try:
                series = self.runtime.load_flow_sample_series(artifact_id)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                continue
            series_items.append((label, series))
        if errors:
            QMessageBox.warning(
                self,
                "Flow Samples",
                "Some flow-sample artifacts could not be loaded:\n"
                + "\n".join(errors[:5]),
            )
        return tuple(series_items)

    def _selected_entries_with_flow_samples(
        self,
    ) -> tuple[ModbusCalibrationHistoryEntry, ...]:
        rows = self._selected_rows()
        if not rows and self._entries:
            rows = (0,)
        return tuple(
            self._entries[row]
            for row in rows
            if 0 <= row < len(self._entries)
            and _history_flow_sample_artifacts(self._entries[row])
        )

    def _selected_rows(self) -> tuple[int, ...]:
        return tuple(
            sorted({index.row() for index in self.historyTable.selectedIndexes()})
        )

    def _update_flow_plot_actions(self) -> None:
        if not hasattr(self, "showFlowPlotButton") or not hasattr(
            self,
            "showFlowDataButton",
        ):
            return
        flow_artifact_count = sum(
            len(_history_flow_sample_artifacts(entry))
            for entry in self._selected_entries_with_flow_samples()
        )
        self.showFlowPlotButton.setEnabled(flow_artifact_count >= 1)
        self.showFlowDataButton.setEnabled(flow_artifact_count >= 1)
        self.compareFlowPlotsButton.setEnabled(flow_artifact_count >= 2)


class DeviceAnalysisDialog(QDialog):
    """Device-centered analysis built from saved Modbus records."""

    def __init__(
        self,
        runtime: ModbusModuleRuntime,
        *,
        device_id: str,
        comparison_variable_names: tuple[str, ...] = (
            "zero_offset",
            "low_threshold",
        ),
        save_comparison_variable_names: Callable[[tuple[str, ...]], None] | None = None,
        report_saved_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._device_id = device_id
        self._comparison_variable_names = tuple(comparison_variable_names)
        self._save_comparison_variable_names = save_comparison_variable_names
        self._report_saved_callback = report_saved_callback
        self._history_trials: tuple[ModbusRepeatabilityHistoryTrial, ...] = ()
        self._selected_trials: dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]] = {}
        self._preview_metrics: dict[str, object] | None = None
        self.setWindowTitle(f"Device Analysis - {device_id}")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setSizeGripEnabled(True)
        _fit_dialog_to_screen(self, 620, 180, 460, 150)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.titleLabel = QLabel(f"Device ID: {self._device_id}")
        self.titleLabel.setObjectName("modbusDeviceAnalysisTitle")
        self.titleLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.selectTrialsButton = QPushButton("Select And Calculate...")
        self.selectTrialsButton.setObjectName("modbusDeviceAnalysisSelectTrialsButton")
        self.saveReportButton = QPushButton("Save")
        self.saveReportButton.setObjectName("modbusDeviceAnalysisSaveReportButton")
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusDeviceAnalysisCloseButton")
        self.selectTrialsButton.clicked.connect(self.select_trials)
        self.saveReportButton.clicked.connect(self.save_report)
        self.closeButton.clicked.connect(self.close)
        top.addWidget(self.titleLabel, 1)
        top.addWidget(self.selectTrialsButton)
        top.addWidget(self.saveReportButton)
        top.addWidget(self.closeButton)
        root.addLayout(top)

        self.statusLabel = QLabel("")
        self.statusLabel.setObjectName("modbusDeviceAnalysisStatusLabel")
        self.statusLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.statusLabel)

    def refresh(self) -> None:
        try:
            self._history_trials = self.runtime.list_repeatability_history_trials(
                self._device_id,
            )
        except Exception as exc:
            self._history_trials = ()
            self.statusLabel.setText(f"Load failed: {exc}")
            self.selectTrialsButton.setEnabled(False)
            self.saveReportButton.setEnabled(bool(self._selected_trials))
            return
        if len(self._history_trials) < 9:
            self._selected_trials = {}
            self._preview_metrics = None
        self.selectTrialsButton.setEnabled(len(self._history_trials) >= 9)
        self.saveReportButton.setEnabled(
            bool(self._selected_trials) and self._preview_metrics is not None
        )
        if self._selected_trials:
            selected_count = sum(len(trials) for trials in self._selected_trials.values())
            self.statusLabel.setText(f"Calculated from selected trials: {selected_count}")
        else:
            self.statusLabel.setText(
                f"Accepted trials available: {len(self._history_trials)}"
            )

    def comparison_variable_names(self) -> tuple[str, ...]:
        return self._comparison_variable_names

    def save_comparison_variables(self, names: tuple[str, ...]) -> None:
        self._comparison_variable_names = names
        if self._save_comparison_variable_names is not None:
            self._save_comparison_variable_names(names)

    def select_trials(self) -> None:
        if not self._history_trials:
            self.refresh()
        dialog = DeviceAnalysisTrialSelectionDialog(
            self._history_trials,
            comparison_variable_names=self.comparison_variable_names(),
            save_comparison_variable_names=self.save_comparison_variables,
            preview_metrics_factory=(
                self.runtime.calculate_device_analysis_repeatability_preview
            ),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._comparison_variable_names = dialog.comparison_variable_names()
        selected = dialog.selected_trials_by_flow()
        preview_metrics = dialog.preview_metrics()
        if preview_metrics is None:
            preview_metrics = self.runtime.calculate_device_analysis_repeatability_preview(
                selected,
            )
        self._selected_trials = selected
        self._preview_metrics = preview_metrics
        self.saveReportButton.setEnabled(True)
        selected_count = sum(len(trials) for trials in selected.values())
        self.statusLabel.setText(f"Calculated from selected trials: {selected_count}")

    def save_report(self) -> None:
        if not self._selected_trials or self._preview_metrics is None:
            QMessageBox.warning(
                self,
                "Calculate First",
                "Select and calculate 9 trials before saving the report.",
            )
            return
        try:
            result = self.runtime.calculate_device_analysis_repeatability_report(
                self._device_id,
                self._selected_trials,
                comparison_variable_names=self.comparison_variable_names(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Save Report Failed",
                str(exc),
            )
            return
        if self._report_saved_callback is not None:
            self._report_saved_callback()
        self.statusLabel.setText(f"Report saved to test history: {result.run_id}")


class ModbusModuleWindow(QDialog):
    """Independent Modbus master UI with its own connection state."""

    _modbusFrameRecorded = Signal(str, str, str)

    def __init__(
        self,
        repository: StorageRepository,
        *,
        runtime: ModbusModuleRuntime | None = None,
        port_scanner: SerialPortScanner | None = None,
        thread_pool: QThreadPool | None = None,
        data_root: Path | None = None,
        parent: QWidget | None = None,
        embedded: bool = False,
    ) -> None:
        super().__init__(parent)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self._data_root = Path(data_root) if data_root is not None else None
        self.runtime = runtime or ModbusModuleRuntime(
            repository,
            data_root=self._data_root,
        )
        self._port_scanner = port_scanner or SerialPortScanner()
        if thread_pool is None:
            self._thread_pool = QThreadPool(self)
            self._thread_pool.setMaxThreadCount(1)
        else:
            self._thread_pool = thread_pool
        self._modbusFrameRecorded.connect(
            self._append_modbus_frame,
            Qt.ConnectionType.QueuedConnection,
        )
        self.runtime.set_frame_logger(self._record_modbus_frame)
        self._active_tasks: list[WorkflowTask] = []
        self._busy = False
        self._closing = False
        self._polling = False
        self._last_order = "ABCD"
        self._loading_profiles = False
        self._pending_map_load_error: str | None = None
        self._pending_zero_calibration_load_error: str | None = None
        self._pending_k_factor_load_error: str | None = None
        self._pending_repeatability_load_error: str | None = None
        self._pending_variable_sampling_load_error: str | None = None
        self._saved_zero_monitor_configuration: dict[str, object] = {}
        self._zero_snapshot_variable_names: tuple[str, ...] | None = None
        self._saved_variable_sampling_configuration: dict[str, object] = {}
        self._variable_sampling_variable_names: tuple[str, ...] | None = None
        self._saved_zero_calibration_configuration: dict[str, object] = {}
        self._saved_k_factor_configuration: dict[str, object] = {}
        self._k_factor_snapshot_variable_names: tuple[str, ...] | None = None
        self._k_factor_cancel_event: Event | None = None
        self._variable_sampling_cancel_event: Event | None = None
        self._zero_monitor_stop_event: Event | None = None
        self._zero_monitor_cancel_event: Event | None = None
        self._saved_repeatability_configuration: dict[str, object] = {}
        self._repeatability_snapshot_variable_names: tuple[str, ...] | None = None
        self._repeatability_sample_variable_names: tuple[str, ...] | None = None
        self._repeatability_plot_layout = PLOT_LAYOUT_OVERLAY
        self._saved_device_analysis_configuration: dict[str, object] = {}
        self._repeatability_cancel_event: Event | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_selected_variables)
        self.connectionDialog: ModbusConnectionDialog | None = None
        self.variableSamplingDialog: VariableSamplingDialog | None = None
        self.zeroMonitorDialog: ZeroMonitorDialog | None = None
        self.zeroCalibrationDialog: ZeroCalibrationDialog | None = None
        self.kFactorDialog: KFactorCalibrationDialog | None = None
        self.repeatabilityDialog: RepeatabilityTestDialog | None = None
        self.deviceProfileDialog: DeviceProfileDialog | None = None
        self.calibrationHistoryDialog: CalibrationHistoryDialog | None = None
        self.currentDeviceHistoryDialog: CalibrationHistoryDialog | None = None
        self.allHistoryDialog: CalibrationHistoryDialog | None = None
        self.deviceAnalysisDialog: DeviceAnalysisDialog | None = None
        self.setWindowTitle("Modbus Module")
        _fit_dialog_to_screen(self, 1040, 720, 760, 480)
        legacy_profile_count = self.runtime.delete_legacy_port_profiles()
        self._load_saved_register_map()
        self._load_saved_variable_sampling_configuration()
        self._load_saved_zero_monitor_configuration()
        self._load_saved_zero_calibration_configuration()
        self._load_saved_k_factor_configuration()
        self._load_saved_repeatability_configuration()
        self._build_ui()
        self._connect_signals()
        self._refresh_device_profiles()
        self._sync_status()
        self._set_connected_controls(False)
        self._log("Ready. This module connection is independent from simulator channels.")
        for error in self.runtime.register_map_install_errors:
            self._log(f"Official register list ignored: {error}")
        if self.runtime.zero_monitor_recovered_run_ids:
            self._log(
                "Recovered interrupted zero monitor run(s): "
                + ", ".join(self.runtime.zero_monitor_recovered_run_ids)
            )
        if legacy_profile_count:
            self._log(
                f"Removed {legacy_profile_count} legacy port-derived device profile(s)."
            )
        if self._pending_map_load_error:
            self._log(f"Saved variable map ignored: {self._pending_map_load_error}")
        if self._pending_zero_calibration_load_error:
            self._log(
                "Saved zero calibration configuration ignored: "
                f"{self._pending_zero_calibration_load_error}"
            )
        if self._pending_k_factor_load_error:
            self._log(
                f"Saved K factor configuration ignored: {self._pending_k_factor_load_error}"
            )
        if self._pending_repeatability_load_error:
            self._log(
                "Saved repeatability configuration ignored: "
                f"{self._pending_repeatability_load_error}"
            )
        if self._pending_variable_sampling_load_error:
            self._log(
                "Saved variable sampling configuration ignored: "
                f"{self._pending_variable_sampling_load_error}"
            )

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.menuBar = QMenuBar()
        self.menuBar.setObjectName("modbusMenuBar")
        self.operationsMenu = self.menuBar.addMenu("Operations")
        self.sampleVariablesAction = QAction("Variable Sampling", self)
        self.sampleVariablesAction.setObjectName("modbusSampleVariablesAction")
        self.zeroMonitorAction = QAction("Zero Monitor", self)
        self.zeroMonitorAction.setObjectName("modbusZeroMonitorAction")
        self.zeroCalibrationAction = QAction("Zero Cal", self)
        self.zeroCalibrationAction.setObjectName("modbusZeroCalibrationAction")
        self.kFactorAction = QAction("K Factor", self)
        self.kFactorAction.setObjectName("modbusKFactorAction")
        self.repeatabilityAction = QAction("Repeatability", self)
        self.repeatabilityAction.setObjectName("modbusRepeatabilityAction")
        self.currentDeviceHistoryAction = QAction("Current Device Test Records", self)
        self.currentDeviceHistoryAction.setObjectName(
            "modbusCurrentDeviceHistoryAction"
        )
        self.deviceAnalysisAction = QAction("Current Device Analysis", self)
        self.deviceAnalysisAction.setObjectName("modbusDeviceAnalysisAction")
        self.allHistoryAction = QAction("All Test Records", self)
        self.allHistoryAction.setObjectName("modbusAllHistoryAction")
        self.calibrationHistoryAction = self.allHistoryAction
        for action in (
            self.sampleVariablesAction,
            self.zeroMonitorAction,
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            self.operationsMenu.addAction(action)
        self.operationsMenu.addSeparator()
        self.operationsMenu.addAction(self.deviceAnalysisAction)
        self.operationsMenu.addSeparator()
        self.operationsMenu.addAction(self.calibrationHistoryAction)
        self.operationsMenu.addAction(self.currentDeviceHistoryAction)
        root.addWidget(self.menuBar)

        status_row = QHBoxLayout()
        self.openConnectionButton = QPushButton("Connection...")
        self.openConnectionButton.setObjectName("modbusOpenConnectionButton")
        self.disconnectButton = QPushButton("Disconnect")
        self.disconnectButton.setObjectName("modbusDisconnectButton")
        self.statusValueLabel = QLabel("Disconnected")
        self.statusValueLabel.setObjectName("modbusStatusValueLabel")
        status_row.addWidget(QLabel("Status"))
        status_row.addWidget(self.statusValueLabel, 1)
        status_row.addWidget(self.openConnectionButton)
        status_row.addWidget(self.disconnectButton)
        root.addLayout(status_row)

        profile_row = QHBoxLayout()
        self.deviceProfileCombo = QComboBox()
        self.deviceProfileCombo.setObjectName("modbusDeviceProfileCombo")
        self.refreshProfilesButton = QPushButton("Refresh")
        self.refreshProfilesButton.setObjectName("modbusRefreshProfilesButton")
        self.createDeviceProfileButton = QPushButton("New Profile")
        self.createDeviceProfileButton.setObjectName("modbusCreateDeviceProfileButton")
        self.editDeviceProfileButton = QPushButton("Edit Profile")
        self.editDeviceProfileButton.setObjectName("modbusEditDeviceProfileButton")
        self.deleteDeviceProfileButton = QPushButton("Delete")
        self.deleteDeviceProfileButton.setObjectName("modbusDeleteDeviceProfileButton")
        self.saveDeviceProfileButton = QPushButton("Save Profile", self)
        self.saveDeviceProfileButton.setObjectName("modbusSaveDeviceProfileButton")
        self.saveDeviceProfileButton.hide()
        self.profileSummaryLabel = QLabel("No profile selected")
        self.profileSummaryLabel.setObjectName("modbusProfileSummaryLabel")
        self.profileSummaryLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.deviceIdLineEdit = QLineEdit(self)
        self.deviceIdLineEdit.setObjectName("modbusDeviceIdLineEdit")
        self.deviceIdLineEdit.setPlaceholderText("Stable device ID, e.g. CFM-2026-001")
        self.deviceIdLineEdit.hide()
        self.deviceModelLineEdit = QLineEdit(self)
        self.deviceModelLineEdit.setObjectName("modbusDeviceModelLineEdit")
        self.deviceModelLineEdit.hide()
        self.tubeModelLineEdit = QLineEdit(self)
        self.tubeModelLineEdit.setObjectName("modbusTubeModelLineEdit")
        self.tubeModelLineEdit.hide()
        self.transmitterModelLineEdit = QLineEdit(self)
        self.transmitterModelLineEdit.setObjectName(
            "modbusTransmitterModelLineEdit"
        )
        self.transmitterModelLineEdit.hide()
        profile_row.addWidget(QLabel("Device"))
        profile_row.addWidget(self.deviceProfileCombo, 2)
        profile_row.addWidget(self.createDeviceProfileButton)
        profile_row.addWidget(self.editDeviceProfileButton)
        profile_row.addWidget(self.deleteDeviceProfileButton)
        profile_row.addWidget(self.refreshProfilesButton)
        profile_row.addWidget(self.profileSummaryLabel, 3)
        root.addLayout(profile_row)

        mapping = QGroupBox("Live Variables")
        mapping_layout = QVBoxLayout(mapping)
        self.variableMapTable = VariableMapTableWidget(0, 12)
        self.variableMapTable.setObjectName("modbusVariableMapTable")
        self.variableMapTable.row_move_requested = self._move_variable_row
        self.variableMapTable.setHorizontalHeaderLabels(
            [
                "Variable",
                "Kind",
                "Address",
                "Words",
                "Type",
                "Scale",
                "Unit",
                "Writable",
                "Poll",
                "Value",
                "Write Value",
                "Operation",
            ]
        )
        self.variableMapTable.verticalHeader().setVisible(False)
        self.variableMapTable.horizontalHeader().setSectionsMovable(True)
        self.variableMapTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.variableMapTable.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.variableMapTable.setDragEnabled(True)
        self.variableMapTable.setAcceptDrops(True)
        self.variableMapTable.viewport().setAcceptDrops(True)
        self.variableMapTable.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.variableMapTable.setDragDropOverwriteMode(False)
        self.variableMapTable.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.variableMapTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.variableMapTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.variableMapTable.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.variableMapTable.setAlternatingRowColors(True)
        self.variableMapTable.setColumnWidth(0, 180)
        self.variableMapTable.setColumnWidth(1, 120)
        self.variableMapTable.setColumnWidth(2, 80)
        self.variableMapTable.setColumnWidth(3, 70)
        self.variableMapTable.setColumnWidth(4, 110)
        self.variableMapTable.setColumnWidth(5, 80)
        self.variableMapTable.setColumnWidth(6, 80)
        self.variableMapTable.setColumnWidth(7, 70)
        self.variableMapTable.setColumnWidth(8, 55)
        self.variableMapTable.setColumnWidth(9, 190)
        self.variableMapTable.setColumnWidth(10, 120)
        self.variableMapTable.setColumnWidth(11, 150)
        for column in range(1, 8):
            self.variableMapTable.setColumnHidden(column, True)
        self.variableMapTable.setMinimumHeight(120)
        self.variableMapTable.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        mapping_layout.addWidget(self.variableMapTable)
        raw_frame_row = QHBoxLayout()
        raw_frame_row.addWidget(QLabel("Raw Frame"))
        self.rawFrameLineEdit = QLineEdit(mapping)
        self.rawFrameLineEdit.setObjectName("modbusRawFrameLineEdit")
        self.rawFrameLineEdit.setPlaceholderText("Hex bytes, e.g. 01 03 00 00 00 02")
        self.rawFrameAutoCrcCheckBox = QCheckBox("Auto CRC16", mapping)
        self.rawFrameAutoCrcCheckBox.setObjectName("modbusRawFrameAutoCrcCheckBox")
        self.rawFrameAutoCrcCheckBox.setChecked(True)
        self.sendRawFrameButton = QPushButton("Send", mapping)
        self.sendRawFrameButton.setObjectName("modbusSendRawFrameButton")
        raw_frame_row.addWidget(self.rawFrameLineEdit, 1)
        raw_frame_row.addWidget(self.rawFrameAutoCrcCheckBox)
        raw_frame_row.addWidget(self.sendRawFrameButton)
        mapping_layout.addLayout(raw_frame_row)
        mapping_actions = QHBoxLayout()
        self.addVariableButton = QPushButton("Add Variable", mapping)
        self.addVariableButton.setObjectName("modbusAddVariableButton")
        self.addVariableButton.hide()
        self.deleteVariableButton = QPushButton("Delete Variable", mapping)
        self.deleteVariableButton.setObjectName("modbusDeleteVariableButton")
        self.deleteVariableButton.hide()
        self.resetVariableMapButton = QPushButton("Reset Map", mapping)
        self.resetVariableMapButton.setObjectName("modbusResetVariableMapButton")
        self.resetVariableMapButton.hide()
        self.saveVariableMapButton = QPushButton("Save Map", mapping)
        self.saveVariableMapButton.setObjectName("modbusSaveVariableMapButton")
        self.saveVariableMapButton.hide()
        self.pollingButton = QPushButton("Start Polling")
        self.pollingButton.setObjectName("modbusPollingButton")
        mapping_actions.addStretch(1)
        mapping_actions.addWidget(self.pollingButton)
        mapping_layout.addLayout(mapping_actions)
        body_splitter = QSplitter(Qt.Orientation.Horizontal)
        body_splitter.setObjectName("modbusBodySplitter")
        body_splitter.addWidget(mapping)
        self._populate_variable_map()

        k_factor = QGroupBox("K Factor Inputs")
        k_factor.setObjectName("modbusKFactorInputsGroup")
        k_form = QFormLayout(k_factor)
        self.massAccBeforeSpinBox = _float_input(100.0)
        self.massAccBeforeSpinBox.setObjectName("massAccBeforeSpinBox")
        self.massAccAfterSpinBox = _float_input(112.0)
        self.massAccAfterSpinBox.setObjectName("massAccAfterSpinBox")
        self.standardMassSpinBox = _float_input(12.6)
        self.standardMassSpinBox.setObjectName("standardMassSpinBox")
        self.currentKFactorSpinBox = _float_input(500.0)
        self.currentKFactorSpinBox.setObjectName("currentKFactorSpinBox")
        k_form.addRow("Mass Acc Before", self.massAccBeforeSpinBox)
        k_form.addRow("Mass Acc After", self.massAccAfterSpinBox)
        k_form.addRow("Standard Mass", self.standardMassSpinBox)
        k_form.addRow("Current K Factor", self.currentKFactorSpinBox)
        k_factor.hide()
        self.kFactorInputsGroup = k_factor

        traffic_log = QGroupBox("Modbus Traffic Log")
        traffic_log_layout = QVBoxLayout(traffic_log)
        self.logTextEdit = QTextEdit(traffic_log)
        self.logTextEdit.setObjectName("modbusLogTextEdit")
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setAcceptRichText(False)
        self.logTextEdit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.logTextEdit.setMinimumWidth(320)
        self.logTextEdit.setMinimumHeight(120)
        traffic_log_layout.addWidget(self.logTextEdit)
        body_splitter.addWidget(traffic_log)
        body_splitter.setStretchFactor(0, 3)
        body_splitter.setStretchFactor(1, 2)
        body_splitter.setSizes([680, 420])
        root.addWidget(body_splitter, 1)

    def _connect_signals(self) -> None:
        self.openConnectionButton.clicked.connect(self._open_connection_dialog)
        self.deviceProfileCombo.currentIndexChanged.connect(
            self._device_profile_selection_changed
        )
        self.refreshProfilesButton.clicked.connect(self._refresh_device_profiles)
        self.createDeviceProfileButton.clicked.connect(self._new_device_profile)
        self.editDeviceProfileButton.clicked.connect(self._edit_selected_device_profile)
        self.deleteDeviceProfileButton.clicked.connect(self._delete_selected_device_profile)
        self.saveDeviceProfileButton.clicked.connect(self._save_device_profile)
        self.addVariableButton.clicked.connect(self._add_variable_row)
        self.deleteVariableButton.clicked.connect(self._delete_selected_variable_row)
        self.resetVariableMapButton.clicked.connect(self._populate_variable_map)
        self.saveVariableMapButton.clicked.connect(self._save_variable_map)
        self.pollingButton.clicked.connect(self._toggle_polling)
        self.sendRawFrameButton.clicked.connect(self._send_raw_frame)
        self.disconnectButton.clicked.connect(self._disconnect)
        self.deviceModelLineEdit.textChanged.connect(self._sync_operation_metadata)
        self.tubeModelLineEdit.textChanged.connect(self._sync_operation_metadata)
        self.transmitterModelLineEdit.textChanged.connect(
            self._sync_operation_metadata
        )
        self.deviceIdLineEdit.textChanged.connect(self._update_profile_summary)
        self.deviceModelLineEdit.textChanged.connect(self._update_profile_summary)
        self.tubeModelLineEdit.textChanged.connect(self._update_profile_summary)
        self.transmitterModelLineEdit.textChanged.connect(self._update_profile_summary)
        self.sampleVariablesAction.triggered.connect(self._sample_variables)
        self.zeroMonitorAction.triggered.connect(self._zero_monitor)
        self.zeroCalibrationAction.triggered.connect(self._zero_calibration)
        self.kFactorAction.triggered.connect(self._k_factor)
        self.repeatabilityAction.triggered.connect(self._repeatability)
        self.calibrationHistoryAction.triggered.connect(self._open_all_test_records)
        self.currentDeviceHistoryAction.triggered.connect(
            self._open_current_device_test_records
        )
        self.deviceAnalysisAction.triggered.connect(self._open_device_analysis)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._closing = False
        if not self._selected_profile_device_id():
            self._refresh_device_profiles()
        self._sync_status()
        self._set_controls_enabled(True)
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._closing = True
        self._stop_polling()
        if self._busy and self._zero_monitor_cancel_event is not None:
            self._cancel_zero_monitor()
        elif self._busy and self._k_factor_cancel_event is not None:
            self._cancel_k_factor_capture()
        elif self._busy and self._repeatability_cancel_event is not None:
            self._cancel_repeatability_capture()
        elif self.runtime.status.connected and not self._busy:
            try:
                status = self.runtime.disconnect()
            except Exception as exc:
                self._log(f"Disconnect on close failed: {exc}")
            else:
                self.statusValueLabel.setText(status.message)
                if self.connectionDialog is not None and isValid(self.connectionDialog):
                    self.connectionDialog.set_status(status.message)
        self._set_controls_enabled(True)
        super().closeEvent(event)

    def refresh_ports(self) -> None:
        self._run_task(
            "Refresh ports",
            self._port_scanner.list_ports,
            self._connection_ports_finished,
            requires_connection=False,
        )

    def _sync_operation_metadata(self) -> None:
        self.runtime.configure_operation_metadata(
            ModbusOperationMetadata(
                device_model=self.deviceModelLineEdit.text().strip(),
                tube_model=self.tubeModelLineEdit.text().strip(),
                transmitter_model=self.transmitterModelLineEdit.text().strip(),
            )
        )

    def _refresh_device_profiles(self) -> None:
        selected = self._selected_profile_device_id()
        recent = self._load_recent_device_profile_id()
        target = selected or recent
        profiles = self.runtime.list_device_profiles()
        self._loading_profiles = True
        self.deviceProfileCombo.clear()
        self.deviceProfileCombo.addItem("Select device profile...", "")
        for profile in profiles:
            self.deviceProfileCombo.addItem(profile.label, profile.device_id)
        applied_device_id = ""
        if target:
            index = self.deviceProfileCombo.findData(target)
            if index >= 0:
                self.deviceProfileCombo.setCurrentIndex(index)
                applied_device_id = target
        self._loading_profiles = False
        if applied_device_id:
            try:
                profile = self.runtime.select_device_profile(applied_device_id)
            except Exception as exc:
                self._log(f"Load device profile failed: {exc}")
            else:
                self._apply_device_profile(profile)
        self._refresh_profile_controls()

    def _selected_profile_device_id(self) -> str:
        data = self.deviceProfileCombo.currentData()
        if isinstance(data, str):
            return data.strip()
        return ""

    def _device_profile_selection_changed(self) -> None:
        if self._loading_profiles:
            return
        self._clear_zero_monitor_confirmation()
        device_id = self._selected_profile_device_id()
        if not device_id:
            self.deviceIdLineEdit.clear()
            self.deviceModelLineEdit.clear()
            self.tubeModelLineEdit.clear()
            self.transmitterModelLineEdit.clear()
            self._refresh_profile_controls()
            return
        try:
            profile = self.runtime.select_device_profile(device_id)
        except Exception as exc:
            self._log(f"Load device profile failed: {exc}")
            return
        self._apply_device_profile(profile)
        self._log(f"Loaded device profile: {profile.device_id}")

    def _apply_device_profile(self, profile: ModbusDeviceProfile) -> None:
        self._save_recent_device_profile_id(profile.device_id)
        self.deviceIdLineEdit.setText(profile.device_id)
        self.deviceModelLineEdit.setText(profile.device_model)
        self.tubeModelLineEdit.setText(profile.tube_model)
        self.transmitterModelLineEdit.setText(profile.transmitter_model)
        settings = profile.connection_settings
        order = settings.get("order")
        if isinstance(order, str) and order:
            self._last_order = order
        if profile.register_map is not None:
            self._populate_variable_map()
        self._sync_operation_metadata()
        self._load_saved_variable_sampling_configuration(device_id=profile.device_id)
        self._load_saved_zero_monitor_configuration(device_id=profile.device_id)
        self._load_saved_zero_calibration_configuration(device_id=profile.device_id)
        self._load_saved_k_factor_configuration(device_id=profile.device_id)
        self._load_saved_repeatability_configuration(device_id=profile.device_id)
        self._load_saved_device_analysis_configuration(device_id=profile.device_id)
        if (
            self.variableSamplingDialog is not None
            and isValid(self.variableSamplingDialog)
        ):
            self._refresh_variable_sampling_registers(self.variableSamplingDialog)
        if self.zeroCalibrationDialog is not None and isValid(self.zeroCalibrationDialog):
            self._refresh_zero_calibration_snapshot_variables(
                self.zeroCalibrationDialog
            )
        if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
            if (
                self.repeatabilityDialog.current_capture() is None
                and not self.repeatabilityDialog.trial_results()
            ):
                self._refresh_repeatability_registers(self.repeatabilityDialog)
            else:
                self._log(
                    "Repeatability configuration kept for the active operation."
                )
        self._refresh_history_dialogs()
        if self.currentDeviceHistoryDialog is not None and isValid(self.currentDeviceHistoryDialog):
            self.currentDeviceHistoryDialog.close()
            self.currentDeviceHistoryDialog = None
        if self.deviceAnalysisDialog is not None and isValid(self.deviceAnalysisDialog):
            self.deviceAnalysisDialog.close()
            self.deviceAnalysisDialog = None
        self._refresh_profile_controls()

    def _create_device_profile(self) -> None:
        self._new_device_profile()

    def _new_device_profile(self) -> None:
        self._open_device_profile_dialog(new_profile=True)

    def _edit_selected_device_profile(self) -> None:
        self._open_device_profile_dialog(new_profile=False)

    def _open_device_profile_dialog(self, *, new_profile: bool = False) -> None:
        if self.runtime.status.connected:
            self._log("Edit profile skipped: disconnect before changing profiles.")
            return
        if self.deviceProfileDialog is None or not isValid(self.deviceProfileDialog):
            self.deviceProfileDialog = DeviceProfileDialog(parent=self)
            self.deviceProfileDialog.saveButton.clicked.connect(
                self._save_device_profile_from_dialog
            )
            self.deviceProfileDialog.destroyed.connect(
                self._device_profile_dialog_destroyed
            )
        selected_profile_id = "" if new_profile else self._selected_profile_device_id()
        if selected_profile_id:
            profile = self.runtime.get_device_profile(selected_profile_id)
            if profile is not None:
                device_id = profile.device_id
                metadata = profile.metadata
                register_map = profile.register_map or self.runtime.register_map
            else:
                self._log("Edit profile failed: selected profile was not found.")
                return
        else:
            device_id = ""
            metadata = ModbusOperationMetadata(
                device_model=self.deviceModelLineEdit.text().strip(),
                tube_model=self.tubeModelLineEdit.text().strip(),
                transmitter_model=self.transmitterModelLineEdit.text().strip(),
            )
            try:
                register_map = self._register_map_from_ui(order=self._last_order)
            except Exception:
                register_map = self.runtime.register_map
        self.deviceProfileDialog.set_profile(
            device_id=device_id,
            metadata=metadata,
            register_map=register_map,
            order=self._last_order,
            register_maps=self.runtime.list_register_maps(),
            register_map_id=(profile.register_map_id if selected_profile_id else ""),
            register_map_version=(
                profile.register_map_version if selected_profile_id else ""
            ),
        )
        self.deviceProfileDialog.show()
        self.deviceProfileDialog.raise_()
        self.deviceProfileDialog.activateWindow()

    def _delete_selected_device_profile(self) -> None:
        if self.runtime.status.connected:
            self._log("Delete profile skipped: disconnect before deleting profiles.")
            return
        device_id = self._selected_profile_device_id()
        if not device_id:
            self._log("Delete profile skipped: select a device profile first.")
            return
        try:
            deleted = self.runtime.delete_device_profile(device_id)
        except Exception as exc:
            self._log(f"Delete profile failed: {exc}")
            return
        if not deleted:
            self._log(f"Delete profile skipped: {device_id} was not found.")
            return
        if self._load_recent_device_profile_id() == device_id:
            self._clear_recent_device_profile_id()
        self._loading_profiles = True
        self.deviceProfileCombo.setCurrentIndex(0)
        self._loading_profiles = False
        self.deviceIdLineEdit.clear()
        self.deviceModelLineEdit.clear()
        self.tubeModelLineEdit.clear()
        self.transmitterModelLineEdit.clear()
        self._refresh_device_profiles()
        self._refresh_history_dialogs()
        self._log(f"Deleted device profile: {device_id}. Test records were kept.")

    def _device_profile_dialog_destroyed(self, _object: object | None = None) -> None:
        self.deviceProfileDialog = None

    def _save_device_profile_from_dialog(self) -> None:
        dialog = self.deviceProfileDialog
        if dialog is None or not isValid(dialog):
            return
        if self.runtime.status.connected:
            dialog.set_status("Disconnect before changing profiles.")
            return
        try:
            register_map = dialog.register_map(order=self._last_order)
            register_map_id, register_map_version, register_map_display_name = (
                dialog.register_map_binding()
            )
            metadata = dialog.metadata()
            profile = self.runtime.save_device_profile(
                device_id=dialog.device_id(),
                metadata=metadata,
                register_map=register_map,
                register_map_id=register_map_id,
                register_map_version=register_map_version,
                register_map_display_name=register_map_display_name,
                select=True,
            )
        except Exception as exc:
            dialog.set_status(f"Save failed: {exc}")
            self._log(f"Save device profile failed: {exc}")
            return
        self.deviceIdLineEdit.setText(profile.device_id)
        self.deviceModelLineEdit.setText(profile.device_model)
        self.tubeModelLineEdit.setText(profile.tube_model)
        self.transmitterModelLineEdit.setText(profile.transmitter_model)
        self.runtime.configure_register_map(profile.register_map or register_map)
        self._refresh_device_profiles()
        index = self.deviceProfileCombo.findData(profile.device_id)
        if index >= 0:
            self.deviceProfileCombo.setCurrentIndex(index)
        self._apply_device_profile(profile)
        dialog.set_status(f"Saved {profile.device_id}.")
        dialog.close()
        self._log(f"Saved device profile: {profile.device_id}")

    def _save_device_profile(self, *, create: bool = False) -> ModbusDeviceProfile | None:
        if self.runtime.status.connected:
            self._log("Save device profile skipped: disconnect before changing profiles.")
            return None
        try:
            register_map = self._register_map_from_ui(order=self._last_order)
            metadata = ModbusOperationMetadata(
                device_model=self.deviceModelLineEdit.text().strip(),
                tube_model=self.tubeModelLineEdit.text().strip(),
                transmitter_model=self.transmitterModelLineEdit.text().strip(),
            )
            device_id = self.deviceIdLineEdit.text().strip()
            if not create and not device_id:
                device_id = self._selected_profile_device_id()
            profile = self.runtime.save_device_profile(
                device_id=device_id,
                metadata=metadata,
                register_map=register_map,
                select=True,
            )
        except Exception as exc:
            self._log(f"Save device profile failed: {exc}")
            return None
        self._refresh_device_profiles()
        index = self.deviceProfileCombo.findData(profile.device_id)
        if index >= 0:
            self.deviceProfileCombo.setCurrentIndex(index)
        self._apply_device_profile(profile)
        self._log(f"Saved device profile: {profile.device_id}")
        return profile

    def _ui_preferences_path(self) -> Path | None:
        if self._data_root is None:
            return None
        return self._data_root / "config" / "modbus_module_ui.json"

    def _load_recent_device_profile_id(self) -> str:
        path = self._ui_preferences_path()
        if path is None or not path.exists():
            return ""
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if not isinstance(settings, dict):
            return ""
        value = settings.get("last_device_profile_id")
        return value.strip() if isinstance(value, str) else ""

    def _save_recent_device_profile_id(self, device_id: str) -> None:
        path = self._ui_preferences_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            settings: dict[str, object] = {}
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings.update(loaded)
            settings["last_device_profile_id"] = device_id
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(f"Save recent device profile failed: {exc}")

    def _clear_recent_device_profile_id(self) -> None:
        path = self._ui_preferences_path()
        if path is None or not path.exists():
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                settings = {}
            settings.pop("last_device_profile_id", None)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(f"Clear recent device profile failed: {exc}")

    def _refresh_profile_controls(self) -> None:
        selected = bool(self._selected_profile_device_id())
        connected = self.runtime.status.connected
        self.saveDeviceProfileButton.setEnabled((not connected) and selected)
        self.createDeviceProfileButton.setEnabled(not connected)
        self.editDeviceProfileButton.setEnabled((not connected) and selected)
        self.deleteDeviceProfileButton.setEnabled((not connected) and selected)
        if hasattr(self, "deviceAnalysisAction"):
            action_enabled = not self._busy
            has_device = bool(self.runtime.status.device_id or selected)
            self.calibrationHistoryAction.setEnabled(action_enabled)
            self.currentDeviceHistoryAction.setEnabled(action_enabled and has_device)
            self.deviceAnalysisAction.setEnabled(action_enabled and has_device)
        self._update_profile_summary()

    def _update_profile_summary(self) -> None:
        if not hasattr(self, "profileSummaryLabel"):
            return
        device_id = self.deviceIdLineEdit.text().strip() or self._selected_profile_device_id()
        if not device_id:
            self.profileSummaryLabel.setText("No profile selected")
            return
        parts = [device_id]
        for value in (
            self.deviceModelLineEdit.text().strip(),
            self.tubeModelLineEdit.text().strip(),
            self.transmitterModelLineEdit.text().strip(),
        ):
            if value:
                parts.append(value)
        profile = self.runtime.get_device_profile(device_id)
        if profile is not None and profile.register_map_id:
            parts.append(
                f"{profile.register_map_id}@{profile.register_map_version}"
            )
        self.profileSummaryLabel.setText(" | ".join(parts))

    def _load_saved_register_map(self) -> None:
        path = self._saved_register_map_path()
        if path is None or not path.exists():
            return
        try:
            register_map = register_map_from_json(path.read_text(encoding="utf-8"))
            self.runtime.configure_register_map(register_map)
            self._last_order = _orders_to_order(register_map)
        except Exception as exc:
            self._pending_map_load_error = str(exc)
        else:
            self._pending_map_load_error = None

    def _save_variable_map(self) -> None:
        if self.runtime.status.connected:
            self._log("Save map skipped: disconnect before saving map changes.")
            return
        path = self._saved_register_map_path()
        if path is None:
            self._log("Save map failed: data root is not configured.")
            return
        try:
            register_map = self._register_map_from_ui(order=self._last_order)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(register_map_to_json(register_map), encoding="utf-8")
            self.runtime.configure_register_map(register_map)
        except Exception as exc:
            self._log(f"Save map failed: {exc}")
            return
        self._clear_zero_monitor_confirmation()
        self._log(f"Variable map saved: {path}")

    def _saved_register_map_path(self) -> Path | None:
        if self._data_root is None:
            return None
        return self._data_root / "config" / "register_maps" / "modbus_module_map.json"

    def _load_saved_variable_sampling_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        path = self._saved_variable_sampling_configuration_path(device_id=device_id)
        self._saved_variable_sampling_configuration = {}
        self._variable_sampling_variable_names = None
        if path is None or not path.exists():
            self._pending_variable_sampling_load_error = None
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("configuration root must be an object")
        except Exception as exc:
            self._pending_variable_sampling_load_error = str(exc)
            return
        self._saved_variable_sampling_configuration = settings
        names = settings.get("variable_names")
        if not isinstance(names, list):
            names = settings.get("sample_variable_names")
        if isinstance(names, list):
            self._variable_sampling_variable_names = tuple(
                str(name) for name in names
            )
        self._pending_variable_sampling_load_error = None

    def _save_variable_sampling_configuration(self) -> None:
        dialog = self._ensure_variable_sampling_dialog()
        path = self._saved_variable_sampling_configuration_path(
            device_id=self._operation_configuration_device_id()
        )
        if path is None:
            dialog.set_error("select a device profile before saving configuration")
            self._log(
                "Save variable sampling configuration failed: "
                "select a device profile first."
            )
            return
        settings = dialog.capture_settings()
        if not settings["variable_names"]:
            dialog.set_error("select at least one variable before saving configuration")
            self._log(
                "Save variable sampling configuration failed: "
                "select at least one variable."
            )
            return
        self._saved_variable_sampling_configuration = dict(settings)
        self._variable_sampling_variable_names = tuple(settings["variable_names"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Save variable sampling configuration failed: {exc}")
            return
        dialog.set_status("Configuration saved for current device")
        self._log(f"Variable sampling configuration saved: {path}")

    def _saved_variable_sampling_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None or not device_id:
            return None
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "devices"
            / _safe_config_name(device_id)
            / "modbus_variable_sampling.json"
        )

    def _load_saved_zero_monitor_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        self._saved_zero_monitor_configuration = {}
        path = self._saved_zero_monitor_configuration_path(device_id=device_id)
        if path is None or not path.exists():
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("configuration root must be an object")
            self._saved_zero_monitor_configuration = settings
        except Exception as exc:
            self._log(f"Saved zero monitor configuration ignored: {exc}")

    def _save_zero_monitor_configuration(self) -> None:
        dialog = self._ensure_zero_monitor_dialog()
        path = self._saved_zero_monitor_configuration_path(
            device_id=self._operation_configuration_device_id()
        )
        if path is None:
            dialog.set_error("select a device profile before saving configuration")
            return
        try:
            settings = dialog.capture_settings()
            if any(
                isinstance(item, dict) and item.get("test_only")
                for item in dict(settings.get("thresholds") or {}).values()
            ):
                raise ValueError("Test-only thresholds cannot be saved as a device profile.")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Save zero monitor configuration failed: {exc}")
            return
        self._saved_zero_monitor_configuration = dict(settings)
        dialog.statusLabel.setText("Configuration saved for current device")
        self._log(f"Zero monitor configuration saved: {path}")

    def _saved_zero_monitor_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None or not device_id:
            return None
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "devices"
            / _safe_config_name(device_id)
            / "modbus_zero_monitor.json"
        )

    def _load_saved_zero_calibration_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        path = self._saved_zero_calibration_configuration_path(device_id=device_id)
        self._saved_zero_calibration_configuration = {}
        self._zero_snapshot_variable_names = None
        if path is None or not path.exists():
            self._pending_zero_calibration_load_error = None
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("configuration root must be an object")
        except Exception as exc:
            self._pending_zero_calibration_load_error = str(exc)
            return
        self._saved_zero_calibration_configuration = settings
        snapshot_names = settings.get("snapshot_variable_names")
        if isinstance(snapshot_names, list):
            self._zero_snapshot_variable_names = tuple(
                str(name) for name in snapshot_names
            )
        self._pending_zero_calibration_load_error = None

    def _save_zero_calibration_configuration(self) -> None:
        dialog = self._ensure_zero_calibration_dialog()
        path = self._saved_zero_calibration_configuration_path(
            device_id=self._operation_configuration_device_id()
        )
        if path is None:
            dialog.set_error("select a device profile before saving configuration")
            self._log(
                "Save zero calibration configuration failed: "
                "select a device profile first."
            )
            return
        settings = dialog.capture_settings()
        self._saved_zero_calibration_configuration = dict(settings)
        self._zero_snapshot_variable_names = tuple(
            settings["snapshot_variable_names"]
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Save zero calibration configuration failed: {exc}")
            return
        dialog.statusLabel.setText("Configuration saved for current device")
        self._log(f"Zero calibration configuration saved: {path}")

    def _saved_zero_calibration_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None or not device_id:
            return None
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "devices"
            / _safe_config_name(device_id)
            / "modbus_zero_calibration.json"
        )

    def _load_saved_k_factor_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        path = self._saved_k_factor_configuration_path(device_id=device_id)
        self._saved_k_factor_configuration = {}
        self._k_factor_snapshot_variable_names = None
        if path is None or not path.exists():
            self._pending_k_factor_load_error = None
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("configuration root must be an object")
        except Exception as exc:
            self._pending_k_factor_load_error = str(exc)
            return
        self._saved_k_factor_configuration = settings
        snapshot_names = settings.get("snapshot_variable_names")
        if isinstance(snapshot_names, list):
            self._k_factor_snapshot_variable_names = tuple(str(name) for name in snapshot_names)
        self._pending_k_factor_load_error = None

    def _save_k_factor_configuration(self) -> None:
        dialog = self._ensure_k_factor_dialog()
        path = self._saved_k_factor_configuration_path(
            device_id=self._operation_configuration_device_id()
        )
        if path is None:
            dialog.set_error("data root is not configured")
            self._log("Save K factor configuration failed: data root is not configured.")
            return
        settings = dialog.capture_settings()
        self._saved_k_factor_configuration = dict(settings)
        self._k_factor_snapshot_variable_names = tuple(settings["snapshot_variable_names"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Save K factor configuration failed: {exc}")
            return
        dialog.statusLabel.setText("Configuration saved")
        self._log(f"K factor configuration saved: {path}")

    def _saved_k_factor_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None:
            return None
        if device_id:
            return (
                self._data_root
                / "config"
                / "workflow_templates"
                / "devices"
                / _safe_config_name(device_id)
                / "modbus_k_factor_simple.json"
            )
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "modbus_k_factor_simple.json"
        )

    def _load_saved_repeatability_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        path = self._saved_repeatability_configuration_path(device_id=device_id)
        self._saved_repeatability_configuration = {}
        self._repeatability_snapshot_variable_names = None
        self._repeatability_sample_variable_names = None
        if path is None or not path.exists():
            self._pending_repeatability_load_error = None
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("configuration root must be an object")
        except Exception as exc:
            self._pending_repeatability_load_error = str(exc)
            return
        snapshot_names = settings.get("snapshot_variable_names")
        if not isinstance(snapshot_names, list):
            snapshot_names = settings.get("post_snapshot_variable_names")
            if isinstance(snapshot_names, list):
                settings["snapshot_variable_names"] = list(snapshot_names)
        if isinstance(snapshot_names, list):
            self._repeatability_snapshot_variable_names = tuple(
                str(name) for name in snapshot_names
            )
        settings.pop("post_snapshot_variable_names", None)
        self._saved_repeatability_configuration = settings
        sample_names = settings.get("sample_variable_names")
        if isinstance(sample_names, list):
            self._repeatability_sample_variable_names = tuple(
                str(name) for name in sample_names
            )
        self._pending_repeatability_load_error = None

    def _save_repeatability_configuration(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        path = self._saved_repeatability_configuration_path(
            device_id=self._operation_configuration_device_id()
        )
        if path is None:
            dialog.set_error("select a device profile before saving configuration")
            dialog.configurationDialog.set_status(
                "Select a device profile before saving configuration."
            )
            self._log(
                "Save repeatability configuration failed: select a device profile first."
            )
            return
        settings = dialog.capture_settings()
        self._saved_repeatability_configuration = dict(settings)
        self._repeatability_snapshot_variable_names = tuple(
            settings["snapshot_variable_names"]
        )
        self._repeatability_sample_variable_names = tuple(
            settings["sample_variable_names"]
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            dialog.configurationDialog.set_status(f"Save failed: {exc}")
            self._log(f"Save repeatability configuration failed: {exc}")
            return
        dialog.configurationDialog.set_status("Configuration saved for current device")
        dialog.statusLabel.setText("Configuration saved")
        self._log(f"Repeatability configuration saved: {path}")

    def _saved_repeatability_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None or not device_id:
            return None
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "devices"
            / _safe_config_name(device_id)
            / "modbus_repeatability_simple.json"
        )

    def _load_saved_device_analysis_configuration(
        self,
        *,
        device_id: str | None = None,
    ) -> None:
        path = self._saved_device_analysis_configuration_path(device_id=device_id)
        self._saved_device_analysis_configuration = {}
        if path is None or not path.exists():
            return
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(settings, dict):
                self._saved_device_analysis_configuration = settings
        except Exception as exc:
            self._log(f"Saved device analysis configuration ignored: {exc}")

    def _save_device_analysis_comparison_variables(
        self,
        names: tuple[str, ...],
    ) -> None:
        device_id = self._operation_configuration_device_id()
        path = self._saved_device_analysis_configuration_path(device_id=device_id)
        if path is None:
            self._log(
                "Save device analysis configuration failed: select a device profile first."
            )
            return
        settings = {"comparison_variable_names": list(names)}
        self._saved_device_analysis_configuration = dict(settings)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(settings, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception as exc:
            self._log(f"Save device analysis configuration failed: {exc}")
            return
        self._log(f"Device analysis configuration saved: {path}")

    def _saved_device_analysis_configuration_path(
        self,
        *,
        device_id: str | None = None,
    ) -> Path | None:
        if self._data_root is None or not device_id:
            return None
        return (
            self._data_root
            / "config"
            / "workflow_templates"
            / "devices"
            / _safe_config_name(device_id)
            / "modbus_device_analysis.json"
        )

    def _device_analysis_comparison_variable_names(self) -> tuple[str, ...]:
        names = self._saved_device_analysis_configuration.get(
            "comparison_variable_names"
        )
        if isinstance(names, list):
            parsed = tuple(str(name).strip() for name in names if str(name).strip())
            if parsed:
                return parsed
        return ("zero_offset", "low_threshold")

    def _operation_configuration_device_id(self) -> str:
        return (
            self.runtime.status.device_id
            or self._selected_profile_device_id()
            or ""
        ).strip()

    def _open_connection_dialog(self) -> None:
        if not self._selected_profile_device_id():
            self._log("Connect failed: create or select a device profile first.")
            return
        if self.connectionDialog is None:
            self.connectionDialog = ModbusConnectionDialog(parent=self)
            self.connectionDialog.refreshPortsButton.clicked.connect(self.refresh_ports)
            self.connectionDialog.connectButton.clicked.connect(self._connect_from_dialog)
        self.connectionDialog.orderCombo.setCurrentText(self._last_order)
        self._apply_profile_connection_settings_to_dialog()
        self.connectionDialog.show()
        self.connectionDialog.raise_()
        self.connectionDialog.activateWindow()
        if self.runtime.status.connected:
            self.connectionDialog.set_status(self.runtime.status.message)
            self.connectionDialog.set_controls_enabled(False)
        else:
            self.refresh_ports()

    def _connect_from_dialog(self) -> None:
        if self.connectionDialog is None:
            return
        settings = self.connectionDialog.settings()
        if not settings.port:
            self._log("Connect failed: select a serial port first.")
            return
        try:
            self._last_order = settings.order
            profile = self.runtime.get_device_profile(
                self._selected_profile_device_id()
            )
            if profile is None:
                raise ValueError("select a device profile before connecting")
            register_map = self._profile_register_map_for_connection(
                profile,
                order=settings.order,
            )
            metadata = ModbusOperationMetadata(
                device_model=self.deviceModelLineEdit.text().strip(),
                tube_model=self.tubeModelLineEdit.text().strip(),
                transmitter_model=self.transmitterModelLineEdit.text().strip(),
            )
            profile = self.runtime.save_device_profile(
                device_id=profile.device_id,
                metadata=metadata,
                register_map=register_map,
                connection_settings=settings,
                select=True,
            )
            self.runtime.configure_register_map(register_map)
            self._refresh_device_profiles()
            index = self.deviceProfileCombo.findData(profile.device_id)
            if index >= 0:
                self.deviceProfileCombo.setCurrentIndex(index)
        except Exception as exc:
            self._log(f"Connect failed: {exc}")
            return
        self._run_task(
            "Connect",
            lambda: self.runtime.connect(settings),
            self._connect_finished,
            requires_connection=False,
        )

    def _profile_register_map_for_connection(
        self,
        profile: ModbusDeviceProfile,
        *,
        order: str,
    ) -> ModbusRegisterMap:
        source_map = profile.register_map or self.runtime.register_map
        word_order, byte_order = _order_to_modbus_orders(order)
        return ModbusRegisterMap(
            name=source_map.name,
            version=source_map.version,
            registers=tuple(
                replace(
                    register,
                    word_order=word_order,
                    byte_order=byte_order,
                )
                for register in source_map.registers
            ),
        )

    def _apply_profile_connection_settings_to_dialog(self) -> None:
        if self.connectionDialog is None:
            return
        device_id = self._selected_profile_device_id()
        if not device_id:
            return
        profile = self.runtime.get_device_profile(device_id)
        if profile is None:
            return
        settings = profile.connection_settings
        port = settings.get("port")
        if isinstance(port, str) and port:
            index = self.connectionDialog.portCombo.findData(port)
            if index >= 0:
                self.connectionDialog.portCombo.setCurrentIndex(index)
        unit_id = settings.get("unit_id")
        if isinstance(unit_id, int):
            self.connectionDialog.unitIdSpinBox.setValue(unit_id)
        for key, widget in (
            ("baudrate", self.connectionDialog.baudrateSpinBox),
            ("stop_bits", self.connectionDialog.stopBitsSpinBox),
            ("retry_count", self.connectionDialog.retriesSpinBox),
        ):
            value = settings.get(key)
            if isinstance(value, int):
                widget.setValue(value)
        parity = settings.get("parity")
        if isinstance(parity, str) and parity:
            self.connectionDialog.parityCombo.setCurrentText(parity)
        order = settings.get("order")
        if isinstance(order, str) and order:
            self.connectionDialog.orderCombo.setCurrentText(order)
        timeout = settings.get("read_timeout_s")
        if isinstance(timeout, (float, int)):
            self.connectionDialog.timeoutSpinBox.setValue(float(timeout))

    def _disconnect(self) -> None:
        self._stop_polling()
        if self._zero_monitor_cancel_event is not None:
            self._zero_monitor_cancel_event.set()
        if self._variable_sampling_cancel_event is not None:
            self._variable_sampling_cancel_event.set()
        status = self.runtime.disconnect()
        self.statusValueLabel.setText(status.message)
        if self.connectionDialog is not None and isValid(self.connectionDialog):
            self.connectionDialog.set_status(status.message)
        self._set_controls_enabled(True)
        self._clear_zero_monitor_confirmation()
        self._log("Disconnected")

    def _sample_variables(self) -> None:
        dialog = self._ensure_variable_sampling_dialog()
        self._load_saved_variable_sampling_configuration(
            device_id=self._operation_configuration_device_id()
        )
        self._refresh_variable_sampling_registers(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if self._busy and self._variable_sampling_cancel_event is not None:
            dialog.set_canceling()
        else:
            dialog.set_ready(connected=self.runtime.status.connected and not self._busy)

    def _zero_monitor(self) -> None:
        dialog = self._ensure_zero_monitor_dialog()
        self._load_saved_zero_monitor_configuration(
            device_id=self._operation_configuration_device_id()
        )
        if self._saved_zero_monitor_configuration and not self._busy:
            try:
                dialog.apply_configuration(self._saved_zero_monitor_configuration)
            except Exception as exc:
                dialog.set_error(str(exc))
        dialog.zeroFlowCheckBox.setChecked(False)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if self._zero_monitor_stop_event is None:
            dialog.set_ready(connected=self.runtime.status.connected and not self._busy)

    def _ensure_zero_monitor_dialog(self) -> ZeroMonitorDialog:
        if self.zeroMonitorDialog is None or not isValid(self.zeroMonitorDialog):
            self.zeroMonitorDialog = ZeroMonitorDialog(parent=self)
            self.zeroMonitorDialog.startRequested.connect(self._start_zero_monitor)
            self.zeroMonitorDialog.stopRequested.connect(self._stop_zero_monitor)
            self.zeroMonitorDialog.cancelRequested.connect(self._cancel_zero_monitor)
            self.zeroMonitorDialog.saveRequested.connect(
                self._save_zero_monitor_configuration
            )
            self.zeroMonitorDialog.zeroCalRequested.connect(self._zero_calibration)
        return self.zeroMonitorDialog

    def _start_zero_monitor(self) -> None:
        dialog = self._ensure_zero_monitor_dialog()
        if self._busy:
            dialog.set_error("another Modbus operation is running")
            return
        if not self.runtime.status.connected:
            dialog.set_error("connect the Modbus module first")
            return
        try:
            config = dialog.capture_configuration()
        except Exception as exc:
            dialog.set_error(str(exc))
            return
        self._stop_polling()
        self._sync_operation_metadata()
        stop_event = Event()
        cancel_event = Event()
        self._zero_monitor_stop_event = stop_event
        self._zero_monitor_cancel_event = cancel_event
        confirmed = dialog.zeroFlowCheckBox.isChecked()
        confirmed_at = datetime.now(UTC) if confirmed else None
        dialog.set_running()

        def run(progress):
            return self.runtime.run_zero_monitor(
                config,
                zero_flow_confirmed=confirmed,
                zero_flow_confirmed_at=confirmed_at,
                stop_requested=stop_event.is_set,
                cancel_requested=cancel_event.is_set,
                update_callback=progress,
            )

        self._run_task(
            "Zero monitor",
            run,
            self._zero_monitor_finished,
            requires_connection=True,
            on_progress=self._zero_monitor_progress,
        )

    def _stop_zero_monitor(self) -> None:
        if self._zero_monitor_stop_event is None:
            return
        self._zero_monitor_stop_event.set()
        if self.zeroMonitorDialog is not None and isValid(self.zeroMonitorDialog):
            self.zeroMonitorDialog.set_stopping()

    def _cancel_zero_monitor(self) -> None:
        if self._zero_monitor_cancel_event is not None:
            self._zero_monitor_cancel_event.set()

    def _zero_monitor_progress(self, message: object) -> None:
        if not isinstance(message, ZeroMonitorLiveUpdate):
            return
        if self.zeroMonitorDialog is not None and isValid(self.zeroMonitorDialog):
            self.zeroMonitorDialog.add_update(message)

    def _zero_monitor_finished(self, result: object) -> None:
        self._zero_monitor_stop_event = None
        self._zero_monitor_cancel_event = None
        if not isinstance(result, ZeroMonitorRunResult):
            self._log(f"Zero monitor finished: {result}")
            return
        if self.zeroMonitorDialog is not None and isValid(self.zeroMonitorDialog):
            self.zeroMonitorDialog.set_result(result)
            self.zeroMonitorDialog.set_ready(connected=self.runtime.status.connected)
        self._refresh_history_dialogs()
        self._log(
            f"Zero monitor {result.run_status.value}: "
            f"{result.run_id or result.attempt_id}, polls={result.counters.get('logical_poll_count', 0)}"
        )

    def _ensure_variable_sampling_dialog(self) -> VariableSamplingDialog:
        if (
            self.variableSamplingDialog is None
            or not isValid(self.variableSamplingDialog)
        ):
            self.variableSamplingDialog = VariableSamplingDialog(parent=self)
            self.variableSamplingDialog.startRequested.connect(
                self._start_variable_sampling
            )
            self.variableSamplingDialog.stopRequested.connect(
                self._stop_variable_sampling
            )
            self.variableSamplingDialog.saveConfigButton.clicked.connect(
                self._save_variable_sampling_configuration
            )
            self.variableSamplingDialog.destroyed.connect(
                lambda: setattr(self, "variableSamplingDialog", None)
            )
        return self.variableSamplingDialog

    def _refresh_variable_sampling_registers(
        self,
        dialog: VariableSamplingDialog,
    ) -> None:
        dialog.set_registers(
            self._sampleable_registers(),
            selected_names=self._default_variable_sampling_names(),
        )
        if self._saved_variable_sampling_configuration:
            dialog.apply_configuration(self._saved_variable_sampling_configuration)
            self._variable_sampling_variable_names = dialog.selected_variable_names()

    def _sampleable_registers(self) -> tuple[ModbusRegister, ...]:
        return tuple(
            register
            for register in self.runtime.register_map.registers
            if register.kind
            in {
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
            }
        )

    def _default_variable_sampling_names(self) -> tuple[str, ...]:
        if self._variable_sampling_variable_names:
            available = {register.name for register in self._sampleable_registers()}
            names = tuple(
                name
                for name in self._variable_sampling_variable_names
                if name in available
            )
            if names:
                return names
        selected = self._selected_poll_variable_names()
        if selected:
            return selected
        preferred = ("mass_rate", "temperature")
        available = {register.name for register in self._sampleable_registers()}
        names = tuple(name for name in preferred if name in available)
        if names:
            return names
        return tuple(register.name for register in self._sampleable_registers()[:1])

    def _start_variable_sampling(self) -> None:
        dialog = self._ensure_variable_sampling_dialog()
        if self._busy:
            dialog.set_error("another Modbus operation is running")
            return
        if not self.runtime.status.connected:
            dialog.set_error("connect the Modbus module first")
            self._log("Variable sampling failed: connect the Modbus module first.")
            return
        variable_names = dialog.selected_variable_names()
        if not variable_names:
            dialog.set_error("select at least one variable")
            return
        self._sync_operation_metadata()
        cancel_event = Event()
        self._variable_sampling_cancel_event = cancel_event
        self._variable_sampling_variable_names = variable_names
        dialog.set_running()
        dialog.show_live_plot(
            variable_names=variable_names,
            units=self._register_units(variable_names),
            plot_layout=dialog.plot_layout(),
        )

        def emit_sample(
            progress,
            captured_at: datetime,
            values: dict[str, object],
        ) -> None:
            progress(
                {
                    "kind": "variable_sampling_sample",
                    "captured_at": captured_at,
                    "values": values,
                }
            )

        self._run_task(
            "Variable sampling",
            lambda progress: self.runtime.run_variable_sampling(
                variable_names,
                poll_interval_s=dialog.poll_interval_s(),
                cancel_requested=cancel_event.is_set,
                status_callback=progress,
                sample_callback=lambda captured_at, values: emit_sample(
                    progress,
                    captured_at,
                    values,
                ),
                operation_metadata=self.runtime.operation_metadata,
                notes=dialog.notes(),
            ),
            self._variable_sampling_finished,
            requires_connection=True,
            on_progress=self._variable_sampling_progress,
        )

    def _stop_variable_sampling(self) -> None:
        if self._variable_sampling_cancel_event is None:
            return
        self._variable_sampling_cancel_event.set()
        if self.variableSamplingDialog is not None and isValid(self.variableSamplingDialog):
            self.variableSamplingDialog.set_canceling()
        self._log("Variable sampling stopping...")

    def _variable_sampling_progress(self, message: object) -> None:
        if isinstance(message, dict) and message.get("kind") == "variable_sampling_sample":
            captured_at = message.get("captured_at")
            values = message.get("values")
            if (
                isinstance(captured_at, datetime)
                and isinstance(values, dict)
                and self.variableSamplingDialog is not None
                and isValid(self.variableSamplingDialog)
            ):
                self.variableSamplingDialog.add_sample(captured_at, values)
            return
        text = str(message)
        if self.variableSamplingDialog is not None and isValid(self.variableSamplingDialog):
            self.variableSamplingDialog.set_status(text)
        self._log(f"Variable sampling: {text}")

    def _variable_sampling_finished(self, result: object) -> None:
        self._variable_sampling_cancel_event = None
        if not isinstance(result, ModbusVariableSamplingRunResult):
            self._log(f"Variable sampling finished: {result}")
            return
        if self.variableSamplingDialog is not None and isValid(self.variableSamplingDialog):
            self.variableSamplingDialog.set_status(
                f"Saved {result.sample_count} sample(s) to {result.run_id}."
            )
            self.variableSamplingDialog.set_ready(connected=self.runtime.status.connected)
        last_point = result.samples[-1] if result.samples else None
        if last_point is not None:
            self._update_map_values(
                tuple(
                    VariableSample(
                        sample_id=f"{result.run_id}-SAMPLE-{name}",
                        device_id=self.runtime.status.device_id or "",
                        variable_name=name,
                        captured_at=last_point.captured_at,
                        value=value,
                        unit=result.units.get(name, ""),
                    )
                    for name, value in last_point.values.items()
                )
            )
        self._refresh_history_dialogs()
        self._log(
            "Variable sampling saved "
            f"{result.run_id} ({result.sample_count} sample(s), "
            f"artifact={result.flow_samples_artifact_id or 'none'})."
        )
        for error in result.errors:
            self._log(f"Variable sampling warning: {error}")

    def _zero_calibration(self) -> None:
        dialog = self._ensure_zero_calibration_dialog()
        self._load_saved_zero_calibration_configuration(
            device_id=self._operation_configuration_device_id()
        )
        self._refresh_zero_calibration_snapshot_variables(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if self._busy and self._k_factor_cancel_event is not None:
            dialog.set_canceling()
        else:
            dialog.set_ready(connected=self.runtime.status.connected and not self._busy)

    def _start_zero_calibration(self) -> None:
        dialog = self._ensure_zero_calibration_dialog()
        snapshot_names = dialog.selected_snapshot_variable_names()
        self._zero_snapshot_variable_names = snapshot_names
        if self._busy:
            dialog.set_error("another Modbus operation is running")
            return
        if not self.runtime.status.connected:
            dialog.set_error("connect the Modbus module first")
            self._log("Zero calibration failed: connect the Modbus module first.")
            return
        self._sync_operation_metadata()
        metadata = self.runtime.operation_metadata
        dialog.set_running()
        self._run_task(
            "Zero calibration",
            lambda: self.runtime.run_zero_calibration(
                snapshot_variable_names=snapshot_names,
                operation_metadata=metadata,
            ),
            self._zero_calibration_finished,
            requires_connection=True,
        )

    def _ensure_zero_calibration_dialog(self) -> ZeroCalibrationDialog:
        if (
            self.zeroCalibrationDialog is None
            or not isValid(self.zeroCalibrationDialog)
        ):
            self.zeroCalibrationDialog = ZeroCalibrationDialog(parent=self)
            self.zeroCalibrationDialog.startButton.clicked.connect(
                self._start_zero_calibration
            )
            self.zeroCalibrationDialog.saveConfigButton.clicked.connect(
                self._save_zero_calibration_configuration
            )
            self._refresh_zero_calibration_snapshot_variables(
                self.zeroCalibrationDialog
            )
        return self.zeroCalibrationDialog

    def _refresh_zero_calibration_snapshot_variables(
        self,
        dialog: ZeroCalibrationDialog,
    ) -> None:
        registers = tuple(
            register
            for register in self.runtime.register_map.registers
            if register.kind
            in {
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
            }
        )
        dialog.set_snapshot_variables(
            registers,
            selected_names=self._zero_snapshot_variable_names,
        )
        if self._saved_zero_calibration_configuration:
            dialog.apply_configuration(self._saved_zero_calibration_configuration)
            self._zero_snapshot_variable_names = (
                dialog.selected_snapshot_variable_names()
            )

    def _open_calibration_history(self) -> None:
        self._open_all_test_records()

    def _open_all_test_records(self) -> None:
        try:
            if (
                self.allHistoryDialog is None
                or not isValid(self.allHistoryDialog)
            ):
                self.allHistoryDialog = CalibrationHistoryDialog(
                    self.runtime,
                    scope_label="All Devices",
                    parent=None,
                )
                self.allHistoryDialog.setAttribute(
                    Qt.WidgetAttribute.WA_DeleteOnClose,
                    True,
                )
                self.allHistoryDialog.destroyed.connect(
                    self._all_history_dialog_destroyed
                )
                self.allHistoryDialog.importRequested.connect(
                    lambda: self._import_calibration_history()
                )
                self.allHistoryDialog.exportRequested.connect(
                    self._export_calibration_history
                )
                self.calibrationHistoryDialog = self.allHistoryDialog
            else:
                self.allHistoryDialog.refresh()
        except Exception as exc:
            self._log(f"All test records failed to open: {exc}")
            return
        if self.allHistoryDialog.detailTitleLabel.text() == "Load Error":
            self._log(self.allHistoryDialog.detailTextEdit.toPlainText())
        self.allHistoryDialog.showNormal()
        self.allHistoryDialog.show()
        self.allHistoryDialog.raise_()
        self.allHistoryDialog.activateWindow()

    def _all_history_dialog_destroyed(self, _object: object | None = None) -> None:
        self.allHistoryDialog = None
        if self.calibrationHistoryDialog is not None and not isValid(self.calibrationHistoryDialog):
            self.calibrationHistoryDialog = None

    def _open_current_device_test_records(self) -> None:
        device_id = self.runtime.status.device_id or self._selected_profile_device_id()
        if not device_id:
            self._log("Current device records skipped: select a device profile first.")
            return
        if (
            self.currentDeviceHistoryDialog is None
            or not isValid(self.currentDeviceHistoryDialog)
        ):
            try:
                self.currentDeviceHistoryDialog = CalibrationHistoryDialog(
                    self.runtime,
                    device_id=device_id,
                    scope_label=f"Current Device {device_id}",
                    parent=self,
                )
            except Exception as exc:
                self._log(f"Current device test records failed to open: {exc}")
                return
            self.currentDeviceHistoryDialog.importRequested.connect(
                lambda bound_device_id=device_id: self._import_calibration_history(
                    target_device_id=bound_device_id,
                )
            )
            self.currentDeviceHistoryDialog.exportRequested.connect(
                self._export_calibration_history
            )
        else:
            self.currentDeviceHistoryDialog.refresh()
        if self.currentDeviceHistoryDialog.detailTitleLabel.text() == "Load Error":
            self._log(self.currentDeviceHistoryDialog.detailTextEdit.toPlainText())
        self.currentDeviceHistoryDialog.showNormal()
        self.currentDeviceHistoryDialog.show()
        self.currentDeviceHistoryDialog.raise_()
        self.currentDeviceHistoryDialog.activateWindow()

    def _open_device_analysis(self) -> None:
        device_id = self.runtime.status.device_id or self._selected_profile_device_id()
        if not device_id:
            self._log("Device analysis skipped: select a device profile first.")
            return
        if (
            self.deviceAnalysisDialog is None
            or not isValid(self.deviceAnalysisDialog)
            or self.deviceAnalysisDialog._device_id != device_id
        ):
            self.deviceAnalysisDialog = DeviceAnalysisDialog(
                self.runtime,
                device_id=device_id,
                comparison_variable_names=self._device_analysis_comparison_variable_names(),
                save_comparison_variable_names=(
                    self._save_device_analysis_comparison_variables
                ),
                report_saved_callback=self._refresh_history_dialogs,
                parent=self,
            )
        else:
            self.deviceAnalysisDialog.refresh()
        self.deviceAnalysisDialog.showNormal()
        self.deviceAnalysisDialog.show()
        self.deviceAnalysisDialog.raise_()
        self.deviceAnalysisDialog.activateWindow()

    def _refresh_history_dialogs(self) -> None:
        for dialog in (
            self.calibrationHistoryDialog,
            self.allHistoryDialog,
            self.currentDeviceHistoryDialog,
        ):
            if dialog is not None and isValid(dialog):
                dialog.refresh()
        if self.deviceAnalysisDialog is not None and isValid(self.deviceAnalysisDialog):
            self.deviceAnalysisDialog.refresh()

    def _export_calibration_history(self, operation: object = None) -> None:
        if self._busy:
            self._log("Export test records skipped: another Modbus operation is running.")
            return
        device_id_filter = None
        raw_operation = operation
        if isinstance(operation, dict):
            raw_operation = operation.get("operation")
            raw_device_id = operation.get("device_id")
            if isinstance(raw_device_id, str) and raw_device_id:
                device_id_filter = raw_device_id
        operation_filter = str(raw_operation) if isinstance(raw_operation, str) else None
        entries = self.runtime.list_test_records(
            operation=operation_filter,
            device_id=device_id_filter,
        )
        options = CalibrationHistoryExportDialog(
            operation=operation_filter,
            entries=entries,
            parent=self,
        )
        if options.exec() != QDialog.DialogCode.Accepted:
            return
        operation_filter = options.selected_operation()
        started_from = options.selected_started_from()
        started_to = options.selected_started_to()
        if (
            started_from is not None
            and started_to is not None
            and started_from > started_to
        ):
            self._log("Export test records failed: start time is after end time.")
            return
        default_path = self._default_calibration_history_export_path(operation_filter)
        file_name, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Test Records",
            str(default_path),
            "CoreFlow Modbus test records (*.json);;Excel workbook (*.xlsx)",
        )
        if not file_name:
            return
        if "Excel" in selected_filter or Path(file_name).suffix.lower() in {
            ".xlsx",
            ".xls",
        }:
            self._log("Export test records failed: Excel export is reserved for a future release.")
            return
        self._run_task(
            "Export test records",
            lambda: self.runtime.export_calibration_history(
                file_name,
                operation=operation_filter,
                device_id=device_id_filter,
                started_from=started_from,
                started_to=started_to,
            ),
            self._history_export_finished,
            requires_connection=False,
        )

    def _import_calibration_history(
        self,
        *,
        target_device_id: str | None = None,
    ) -> None:
        if self._busy:
            self._log("Import test records skipped: another Modbus operation is running.")
            return
        start_dir = self._default_calibration_history_directory()
        file_name, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import Test Records",
            str(start_dir),
            "CoreFlow Modbus test records (*.json);;CoreFlow Modbus calibration history (*.json)",
        )
        if not file_name:
            return
        self._run_task(
            "Import history",
            lambda: self.runtime.import_calibration_history(
                file_name,
                target_device_id=target_device_id,
            ),
            self._history_import_finished,
            requires_connection=False,
        )

    def _default_calibration_history_directory(self) -> Path:
        if self._data_root is not None:
            return self._data_root / "exports" / "modbus"
        return Path.cwd()

    def _default_calibration_history_export_path(self, operation: str | None) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        operation_name = operation if operation not in (None, "", "all") else "all"
        filename = f"modbus_test_records_{operation_name}_{stamp}.json"
        return self._default_calibration_history_directory() / filename

    def _k_factor(self) -> None:
        dialog = self._ensure_k_factor_dialog()
        self._load_saved_k_factor_configuration(
            device_id=self._operation_configuration_device_id()
        )
        self._refresh_k_factor_registers(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.set_ready(connected=self.runtime.status.connected)

    def _start_k_factor_capture(self) -> None:
        dialog = self._ensure_k_factor_dialog()
        settings = dialog.capture_settings()
        snapshot_names = tuple(settings["snapshot_variable_names"])
        self._k_factor_snapshot_variable_names = snapshot_names
        if self._busy:
            dialog.set_error("another Modbus operation is running")
            return
        if not self.runtime.status.connected:
            dialog.set_error("connect the Modbus module first")
            self._log("K factor failed: connect the Modbus module first.")
            return
        cancel_event = Event()
        self._k_factor_cancel_event = cancel_event
        dialog.set_running()
        self._run_task(
            "K factor",
            lambda progress: self.runtime.capture_k_factor_simple_trial(
                snapshot_variable_names=snapshot_names,
                flow_rate_parameter=str(settings["flow_rate_parameter"]),
                flow_acc_parameter=str(settings["flow_acc_parameter"]),
                k_factor_parameter=str(settings["k_factor_parameter"]),
                poll_interval_s=float(settings["poll_interval_s"]),
                cancel_requested=cancel_event.is_set,
                status_callback=progress,
            ),
            self._k_factor_capture_finished,
            requires_connection=True,
            on_progress=lambda message: self._k_factor_progress(str(message)),
        )

    def _k_factor_progress(self, message: str) -> None:
        if self.kFactorDialog is not None and isValid(self.kFactorDialog):
            self.kFactorDialog.statusLabel.setText(message)
        self._log(f"K factor: {message}")

    def _calculate_k_factor_result(self) -> None:
        dialog = self._ensure_k_factor_dialog()
        capture = dialog.current_capture()
        if capture is None:
            dialog.set_error("capture a flow segment first")
            return
        self._sync_operation_metadata()
        metadata = self.runtime.operation_metadata
        try:
            result = self.runtime.calculate_k_factor_simple_result(
                capture,
                standard_mass=dialog.standard_mass(),
                save_history=dialog.save_history(),
                operation_metadata=metadata,
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"K factor calculation failed: {exc}")
            return
        dialog.set_result(result)
        self._log(f"K factor calculated {result.run_id}")
        if result.history_saved:
            self._refresh_history_dialogs()
        if (
            dialog.should_write_to_device()
            and self.runtime.status.connected
            and not self._busy
        ):
            self._write_k_factor_result()

    def _write_k_factor_result(self) -> None:
        dialog = self._ensure_k_factor_dialog()
        result = dialog.current_result()
        if result is None:
            dialog.set_error("calculate K1 first")
            return
        self._run_task(
            "K factor write",
            lambda: self.runtime.apply_k_factor_simple_result(result),
            self._k_factor_write_finished,
            requires_connection=True,
        )

    def _ensure_k_factor_dialog(self) -> KFactorCalibrationDialog:
        if self.kFactorDialog is None or not isValid(self.kFactorDialog):
            self.kFactorDialog = KFactorCalibrationDialog(parent=self)
            self.kFactorDialog.startButton.clicked.connect(self._start_k_factor_capture)
            self.kFactorDialog.calculateButton.clicked.connect(
                self._calculate_k_factor_result
            )
            self.kFactorDialog.writeButton.clicked.connect(self._write_k_factor_result)
            self.kFactorDialog.saveConfigButton.clicked.connect(
                self._save_k_factor_configuration
            )
            self.kFactorDialog.cancelRequested.connect(self._cancel_k_factor_capture)
            self._refresh_k_factor_registers(self.kFactorDialog)
        return self.kFactorDialog

    def _cancel_k_factor_capture(self) -> None:
        cancel_event = self._k_factor_cancel_event
        if cancel_event is None or not self._busy:
            return
        if not cancel_event.is_set():
            cancel_event.set()
            self._log("K factor cancel requested.")
        if self.kFactorDialog is not None and isValid(self.kFactorDialog):
            self.kFactorDialog.set_canceling()

    def _refresh_k_factor_registers(self, dialog: KFactorCalibrationDialog) -> None:
        registers = tuple(
            register
            for register in self.runtime.register_map.registers
            if register.kind
            in {
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
            }
        )
        dialog.set_registers(
            registers,
            selected_names=self._k_factor_snapshot_variable_names,
        )
        if self._saved_k_factor_configuration:
            dialog.apply_configuration(self._saved_k_factor_configuration)
            self._k_factor_snapshot_variable_names = (
                dialog.selected_snapshot_variable_names()
            )

    def _k_factor_capture_finished(self, result: object) -> None:
        self._k_factor_cancel_event = None
        if not isinstance(result, ModbusKFactorSimpleCapture):
            self._log(f"K factor finished: {result}")
            return
        if self.kFactorDialog is not None and isValid(self.kFactorDialog):
            self.kFactorDialog.set_captured(result)
        self._log(
            f"K factor captured {result.run_id} "
            f"(flow_source={result.segment.flow_rate_source})"
        )

    def _k_factor_write_finished(self, result: object) -> None:
        if not isinstance(result, ModbusKFactorSimpleResult):
            self._log(f"K factor write finished: {result}")
            return
        if self.kFactorDialog is not None and isValid(self.kFactorDialog):
            self.kFactorDialog.set_write_result(result)
        self._refresh_history_dialogs()
        self._update_map_values(
            (
                VariableSample(
                    sample_id=f"{result.run_id}-KFACTOR",
                    device_id=self.runtime.status.device_id or "",
                    variable_name=result.k_factor_parameter,
                    captured_at=datetime.now(UTC),
                    value=result.readback_k_factor
                    if result.readback_k_factor is not None
                    else result.corrected_k_factor,
                ),
            )
        )
        self._log(
            f"K factor write {result.write_status}; verified={result.write_verified}"
        )

    def _repeatability(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        self._load_saved_repeatability_configuration(
            device_id=self._operation_configuration_device_id()
        )
        self._refresh_repeatability_registers(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.set_ready(connected=self.runtime.status.connected)

    def _start_repeatability_capture(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        settings = dialog.capture_settings()
        snapshot_names = tuple(settings["snapshot_variable_names"])
        self._repeatability_snapshot_variable_names = snapshot_names
        if self._busy:
            dialog.set_error("another Modbus operation is running")
            return
        if not self.runtime.status.connected:
            dialog.set_error("connect the Modbus module first")
            self._log("Repeatability failed: connect the Modbus module first.")
            return
        try:
            flow_point, trial_index = dialog.next_trial_context()
        except Exception as exc:
            dialog.set_error(str(exc))
            return
        capture_started_at = datetime.now(UTC)
        record_flow_samples = bool(settings.get("record_flow_samples"))
        flow_rate_parameter = str(settings["flow_rate_parameter"])
        sample_names = tuple(settings["sample_variable_names"])
        plot_layout = self._repeatability_plot_layout
        if record_flow_samples:
            selection = self._select_repeatability_trial_sample_variables(
                dialog,
                flow_rate_parameter=flow_rate_parameter,
                selected_names=sample_names,
            )
            if selection is None:
                dialog.set_ready(connected=self.runtime.status.connected)
                return
            selected_names, plot_layout = selection
            dialog.set_sample_variable_names(selected_names)
            sample_names = dialog.selected_sample_variable_names()
            self._repeatability_plot_layout = plot_layout
        self._repeatability_sample_variable_names = sample_names
        cancel_event = Event()
        self._repeatability_cancel_event = cancel_event
        dialog.set_running()
        if record_flow_samples:
            dialog.show_flow_plot(
                flow_parameter=flow_rate_parameter,
                variable_names=(flow_rate_parameter, *sample_names),
                units=self._register_units((flow_rate_parameter, *sample_names)),
                plot_layout=plot_layout,
            )
        run_id = dialog.current_run_id()

        def emit_trial_sample(
            progress,
            captured_at: datetime,
            values: dict[str, object],
        ) -> None:
            progress(
                {
                    "kind": "repeatability_flow_sample",
                    "captured_at": captured_at,
                    "values": values,
                }
            )

        self._run_task(
            "Repeatability",
            lambda progress: self.runtime.capture_repeatability_simple_trial(
                run_id=run_id,
                capture_started_at=capture_started_at,
                flow_point=flow_point,
                trial_index=trial_index,
                snapshot_variable_names=snapshot_names,
                sample_variable_names=sample_names,
                flow_rate_parameter=flow_rate_parameter,
                flow_acc_parameter=str(settings["flow_acc_parameter"]),
                k_factor_parameter=str(settings["k_factor_parameter"]),
                poll_interval_s=float(settings["poll_interval_s"]),
                post_start_sample_s=float(settings["instant_flow_offset_s"]),
                capture_snapshot=True,
                cancel_requested=cancel_event.is_set,
                status_callback=progress,
                record_flow_samples=record_flow_samples,
                sample_callback=(
                    lambda captured_at, values: emit_trial_sample(
                        progress,
                        captured_at,
                        values,
                    )
                    if record_flow_samples
                    else None
                ),
            ),
            self._repeatability_capture_finished,
            requires_connection=True,
            on_progress=self._repeatability_progress,
        )

    def _select_repeatability_trial_sample_variables(
        self,
        dialog: RepeatabilityTestDialog,
        *,
        flow_rate_parameter: str,
        selected_names: tuple[str, ...],
    ) -> tuple[tuple[str, ...], str] | None:
        registers = tuple(
            register
            for register in self.runtime.register_map.registers
            if register.kind
            in {
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
            }
        )
        selection = SnapshotSelectionDialog(
            registers,
            selected_names=selected_names,
            required_names=(flow_rate_parameter,) if flow_rate_parameter else (),
            title="Select This Trial Samples",
            object_name="modbusRepeatabilityTrialSampleSelectionTable",
            plot_layout=self._repeatability_plot_layout,
            show_plot_layout=True,
            parent=dialog,
        )
        if selection.exec() != QDialog.DialogCode.Accepted:
            self._log("Repeatability capture canceled before trial sample selection.")
            return None
        return (
            tuple(
                name
                for name in selection.selected_names()
                if name != flow_rate_parameter
            ),
            selection.plot_layout(),
        )

    def _repeatability_progress(self, message: object) -> None:
        if isinstance(message, dict) and message.get("kind") == "repeatability_flow_sample":
            captured_at = message.get("captured_at")
            values = message.get("values")
            if (
                isinstance(captured_at, datetime)
                and isinstance(values, dict)
                and self.repeatabilityDialog is not None
                and isValid(self.repeatabilityDialog)
            ):
                self.repeatabilityDialog.add_flow_plot_sample(captured_at, values)
            return
        text = str(message)
        if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
            self.repeatabilityDialog.statusLabel.setText(text)
            if not _is_repeatability_snapshot_read_status(text):
                self.repeatabilityDialog.update_capture_progress(text)
        self._log(f"Repeatability: {text}")

    def _save_repeatability_trial(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        capture = dialog.current_capture()
        if capture is None:
            dialog.set_error("capture a trial first")
            return
        try:
            trial = self.runtime.calculate_repeatability_simple_trial(
                capture,
                standard_mass=dialog.standard_mass(),
                notes=dialog.operation_notes(),
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Repeatability trial calculation failed: {exc}")
            return
        dialog.add_trial_result(trial)
        dialog._set_original_k_factor(trial.original_k_factor)
        self._update_map_values(
            (
                VariableSample(
                    sample_id=f"{trial.run_id}-REP-{trial.trial_index}",
                    device_id=self.runtime.status.device_id or "",
                    variable_name=trial.flow_acc_parameter,
                    captured_at=trial.flow_ended_at,
                    value=trial.mass_acc_after,
                ),
            )
        )
        self._log(
            "Repeatability trial saved "
            f"{len(dialog.trial_results())}"
            f"{'' if dialog.is_single_point_mode() else '/9'} "
            f"(flow={trial.flow_point:g}, trial={trial.trial_index}, "
            f"error={trial.percent_error:.6g}%)."
        )
        summaries: list[ModbusRepeatabilityFlowSummary] = []
        latest_summary: ModbusRepeatabilityFlowSummary | None = None
        for flow_point in dict.fromkeys(item.flow_point for item in dialog.trial_results()):
            flow_trials = tuple(
                item
                for item in dialog.trial_results()
                if item.flow_point == flow_point
            )
            if not dialog.is_single_point_mode() and len(flow_trials) < 3:
                continue
            try:
                summary = self.runtime.summarize_repeatability_flow_point(
                    flow_trials,
                    flow_point=flow_point,
                )
            except Exception as exc:
                self._log(f"Repeatability flow summary failed: {exc}")
                continue
            summaries.append(summary)
            if flow_point == trial.flow_point:
                latest_summary = summary
        if dialog.is_single_point_mode() and latest_summary is None:
            flow_trials = tuple(
                item
                for item in dialog.trial_results()
                if item.flow_point == trial.flow_point
            )
            latest_summary = self.runtime.summarize_repeatability_flow_point(
                flow_trials,
                flow_point=trial.flow_point,
            )
            summaries.append(latest_summary)
        dialog.set_progress_summary(
            latest_trial=trial,
            flow_summaries=tuple(summaries),
        )
        if latest_summary is not None:
            self._log(
                "Repeatability flow summary "
                f"flow={latest_summary.flow_point:g}, "
                f"trials={latest_summary.trial_count}, "
                "stddev="
                f"{latest_summary.repeatability_stddev_percent:.6g}%."
            )
        if dialog.is_single_point_mode():
            self._finish_repeatability_result(force_single_summary=True)
        if dialog.is_complete():
            dialog.statusLabel.setText(
                "Base trial set complete. Calculate repeatability or final K when ready."
            )

    def _calculate_repeatability_selection(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        selection_dialog = RepeatabilitySelectionDialog(
            dialog.trial_results(),
            parent=dialog,
        )
        if selection_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        trials = selection_dialog.selected_trials()
        if len(trials) != 3:
            dialog.set_error("select three consecutive trials")
            return
        try:
            summary = self.runtime.summarize_repeatability_flow_point(
                trials,
                flow_point=trials[0].flow_point,
            )
            result = self.runtime.save_repeatability_flow_summary_history(
                trials,
                flow_point=trials[0].flow_point,
                mode=dialog.mode(),
                save_history=dialog.save_history(),
                operation_metadata=self.runtime.operation_metadata,
                notes=dialog.operation_notes(),
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Repeatability selection calculation failed: {exc}")
            return
        previous, updated = dialog.update_selected_repeatability(summary, trials)
        if previous is None:
            dialog.statusLabel.setText(
                "Repeatability selected "
                f"flow={updated.flow_point:g}, "
                f"stddev={updated.repeatability_stddev_percent:.6g}%."
            )
            self._log(
                "Repeatability selected "
                f"flow={updated.flow_point:g}, "
                f"stddev={updated.repeatability_stddev_percent:.6g}% "
                f"and saved {result.run_id}."
            )
        else:
            delta = updated.repeatability_stddev_percent - previous.repeatability_stddev_percent
            dialog.statusLabel.setText(
                "Repeatability refreshed "
                f"flow={updated.flow_point:g}, "
                f"stddev={updated.repeatability_stddev_percent:.6g}% "
                f"(change={delta:.6g}%)."
            )
            self._log(
                "Repeatability refreshed "
                f"flow={updated.flow_point:g}, "
                f"stddev={updated.repeatability_stddev_percent:.6g}% "
                f"(change={delta:.6g}%) and saved {result.run_id}."
            )
        if result.history_saved:
            self._refresh_history_dialogs()

    def _calculate_repeatability_final_k(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        selected = dialog.selected_repeatability_trials()
        if len(selected) < 3:
            dialog.set_error("select repeatability trials for all three flow points first")
            return
        try:
            result = self.runtime.calculate_repeatability_final_k(
                selected,
                run_id=dialog.current_run_id(),
                save_history=dialog.save_history(),
                operation_metadata=self.runtime.operation_metadata,
                notes=dialog.operation_notes(),
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Final K calculation failed: {exc}")
            return
        dialog.set_final_k_result(result)
        new_k_text = _format_k_value(result.get("new_k_factor"))
        average_error_text = _format_value(result.get("average_error"))
        dialog.statusLabel.setText(
            "Final K calculated and saved "
            f"k={new_k_text}."
        )
        self._refresh_history_dialogs()
        self._log(
            "Final K calculated "
            f"k={new_k_text} "
            f"average_error={average_error_text}."
        )

    def _write_repeatability_final_k(self) -> None:
        dialog = self._ensure_repeatability_dialog()
        result = dialog.final_k_result()
        if result is None:
            dialog.set_error("calculate final K first")
            return
        new_k = result.get("new_k_factor")
        original_k = result.get("original_k_factor")
        k_parameter = result.get("k_factor_parameter")
        device_id = self.runtime.status.device_id or self._selected_profile_device_id()
        message = (
            "Write the new K factor to the connected device?\n\n"
            f"Device ID: {device_id or '(not selected)'}\n"
            f"K Factor variable: {k_parameter}\n"
            f"Original K: {_format_k_value(original_k)}\n"
            f"New K: {_format_k_value(new_k)}"
        )
        try:
            delta = float(new_k) - float(original_k)
        except (TypeError, ValueError):
            delta = None
        if delta is not None:
            message += f"\nDelta: {_format_k_value(delta)}"
        if (
            QMessageBox.question(
                dialog,
                "Write New K",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            dialog.statusLabel.setText("Final K write canceled.")
            self._log("Final K write canceled by operator.")
            return
        dialog.writeFinalKButton.setEnabled(False)
        self._run_task(
            "Repeatability final K write",
            lambda: self.runtime.apply_repeatability_final_k_result(
                result,
                operation_metadata=self.runtime.operation_metadata,
            ),
            self._repeatability_final_k_write_finished,
            requires_connection=True,
        )

    def _repeatability_final_k_write_finished(self, result: object) -> None:
        if not isinstance(result, dict):
            self._log(f"Final K write finished: {result}")
            if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
                self.repeatabilityDialog.set_error(str(result))
            return
        if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
            self.repeatabilityDialog.set_final_k_result(result)
            self.repeatabilityDialog.statusLabel.setText(
                "Final K write "
                f"{result.get('write_status')}; "
                f"verified={result.get('write_verified')}."
            )
        self._refresh_history_dialogs()
        self._log(
            "Final K write "
            f"{result.get('write_status')}; "
            f"verified={result.get('write_verified')} "
            f"readback={result.get('readback_k_factor')}."
        )

    def _save_repeatability_summary(self) -> None:
        self._calculate_repeatability_final_k()

    def _finish_repeatability_result(
        self,
        *,
        force_single_summary: bool = False,
    ) -> None:
        dialog = self._ensure_repeatability_dialog()
        trials = dialog.trial_results()
        if not trials:
            dialog.set_error("save at least one trial first")
            return
        if dialog.is_single_point_mode():
            expected_flow_point_count = 1
            expected_trials_per_point = len(trials)
            require_complete = False
            mode = "single_point"
        else:
            expected_flow_point_count = 3
            expected_trials_per_point = 3
            require_complete = True
            mode = "three_point"
        if dialog.is_single_point_mode() and not force_single_summary:
            return
        self._sync_operation_metadata()
        metadata = self.runtime.operation_metadata
        try:
            result = self.runtime.calculate_repeatability_simple_result(
                trials,
                save_history=dialog.save_history(),
                mode=mode,
                expected_flow_point_count=expected_flow_point_count,
                expected_trials_per_point=expected_trials_per_point,
                require_complete=require_complete,
                operation_metadata=metadata,
                notes=dialog.operation_notes(),
            )
        except Exception as exc:
            dialog.set_error(str(exc))
            self._log(f"Repeatability calculation failed: {exc}")
            return
        dialog.set_result(result)
        if result.history_saved:
            self._refresh_history_dialogs()
        if dialog.is_single_point_mode():
            self._log(
                f"Repeatability summary saved {result.run_id} "
                f"({len(result.trials)} trial(s))."
            )
        else:
            self._log(f"Repeatability completed {result.run_id}")

    def _ensure_repeatability_dialog(self) -> RepeatabilityTestDialog:
        if self.repeatabilityDialog is None or not isValid(self.repeatabilityDialog):
            self.repeatabilityDialog = RepeatabilityTestDialog(parent=self)
            self.repeatabilityDialog.destroyed.connect(
                self._repeatability_dialog_destroyed
            )
            self.repeatabilityDialog.startButton.clicked.connect(
                self._start_repeatability_capture
            )
            self.repeatabilityDialog.calculateTrialErrorButton.clicked.connect(
                self._save_repeatability_trial
            )
            self.repeatabilityDialog.calculateRepeatabilityButton.clicked.connect(
                self._calculate_repeatability_selection
            )
            self.repeatabilityDialog.calculateFinalKButton.clicked.connect(
                self._calculate_repeatability_final_k
            )
            self.repeatabilityDialog.writeFinalKButton.clicked.connect(
                self._write_repeatability_final_k
            )
            self.repeatabilityDialog.configurationDialog.saveConfigButton.clicked.connect(
                self._save_repeatability_configuration
            )
            self.repeatabilityDialog.cancelRequested.connect(
                self._cancel_repeatability_capture
            )
            self._refresh_repeatability_registers(self.repeatabilityDialog)
        return self.repeatabilityDialog

    def _repeatability_dialog_destroyed(self, _object: object | None = None) -> None:
        self.repeatabilityDialog = None

    def _cancel_repeatability_capture(self) -> None:
        cancel_event = self._repeatability_cancel_event
        if cancel_event is None or not self._busy:
            return
        if not cancel_event.is_set():
            cancel_event.set()
            self._log("Repeatability cancel requested.")
        if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
            self.repeatabilityDialog.set_canceling()

    def _refresh_repeatability_registers(
        self,
        dialog: RepeatabilityTestDialog,
    ) -> None:
        registers = tuple(
            register
            for register in self.runtime.register_map.registers
            if register.kind
            in {
                RegisterKind.COIL,
                RegisterKind.DISCRETE_INPUT,
                RegisterKind.HOLDING,
                RegisterKind.INPUT,
            }
        )
        dialog.set_registers(
            registers,
            selected_names=self._repeatability_snapshot_variable_names,
            sample_selected_names=self._repeatability_sample_variable_names,
        )
        if self._saved_repeatability_configuration:
            dialog.apply_configuration(self._saved_repeatability_configuration)
            self._repeatability_snapshot_variable_names = (
                dialog.selected_snapshot_variable_names()
            )
            self._repeatability_sample_variable_names = (
                dialog.selected_sample_variable_names()
            )

    def _repeatability_capture_finished(self, result: object) -> None:
        self._repeatability_cancel_event = None
        if not isinstance(result, ModbusRepeatabilitySimpleCapture):
            self._log(f"Repeatability finished: {result}")
            return
        if self.repeatabilityDialog is not None and isValid(self.repeatabilityDialog):
            self.repeatabilityDialog.set_captured(result)
        self._log(
            "Repeatability captured "
            f"{result.run_id} "
            f"(flow={result.flow_point:g}, trial={result.trial_index}, "
            f"flow_source={result.segment.flow_rate_source}); "
            "enter standard mass and calculate trial error."
        )

    def _history_export_finished(self, result: object) -> None:
        if not isinstance(result, ModbusCalibrationHistoryExportResult):
            self._log(f"Export test records finished: {result}")
            return
        self._log(
            "Exported test records "
            f"to {result.path} "
            f"({result.run_count} run(s), "
            f"{result.analysis_result_count} result(s), "
            f"{result.workflow_step_count} step(s))."
        )

    def _history_import_finished(self, result: object) -> None:
        if not isinstance(result, ModbusCalibrationHistoryImportResult):
            self._log(f"Import test records finished: {result}")
            return
        self._refresh_history_dialogs()
        self._log(
            "Imported test records "
            f"from {result.path} "
            f"({result.imported_runs} run(s), "
            f"{result.skipped_runs} skipped, "
            f"{result.renamed_runs} renamed, "
            f"{result.retargeted_runs} retargeted)."
        )
        for error in result.errors:
            self._log(f"Import test records warning: {error}")

    def _sync_status(self) -> None:
        self.statusValueLabel.setText(self.runtime.status.message)

    def _connect_finished(self, status: object) -> None:
        message = getattr(status, "message", str(status))
        self.statusValueLabel.setText(message)
        self._refresh_device_profiles()
        self._set_controls_enabled(True)
        if self.connectionDialog is not None:
            self.connectionDialog.set_status(message)
        self._clear_zero_monitor_confirmation()
        self._log(message)

    def _clear_zero_monitor_confirmation(self) -> None:
        if self.zeroMonitorDialog is not None and isValid(self.zeroMonitorDialog):
            self.zeroMonitorDialog.zeroFlowCheckBox.setChecked(False)

    def _connection_ports_finished(self, ports: object) -> None:
        if self.connectionDialog is None or not isValid(self.connectionDialog):
            return
        selected = self.connectionDialog.selected_port()
        self.connectionDialog.portCombo.clear()
        port_infos = ports if isinstance(ports, tuple) else ()
        for port in port_infos:
            if not isinstance(port, SerialPortInfo):
                continue
            self.connectionDialog.portCombo.addItem(_format_port(port), port.port)
        if self.connectionDialog.portCombo.count() == 0:
            self.connectionDialog.portCombo.addItem("No serial ports found", "")
            self.connectionDialog.set_controls_enabled(not self.runtime.status.connected)
            self._log("No serial ports found.")
            return
        index = self.connectionDialog.portCombo.findData(selected)
        if index >= 0:
            self.connectionDialog.portCombo.setCurrentIndex(index)
        self._apply_profile_connection_settings_to_dialog()
        self._set_controls_enabled(True)
        self._log(f"Discovered {self.connectionDialog.portCombo.count()} serial port(s).")

    def _sample_finished(self, samples: object) -> None:
        if isinstance(samples, ModbusVariableSampleResult):
            typed_samples = samples.samples
            errors = samples.errors
        else:
            typed_samples = samples if isinstance(samples, tuple) else ()
            errors = ()
        self._update_map_values(typed_samples)
        self._log(f"Sampled {len(typed_samples)} variables")
        for error in errors:
            self._log(f"Sample warning: {error}")

    def _zero_calibration_finished(self, result: object) -> None:
        if not isinstance(result, ModbusZeroCalibrationResult):
            self._log(f"Zero calibration completed {result}")
            return
        if (
            self.zeroCalibrationDialog is not None
            and isValid(self.zeroCalibrationDialog)
        ):
            self.zeroCalibrationDialog.set_result(result)
        samples = (
            VariableSample(
                sample_id=f"{result.run_id}-ZERO-OFFSET",
                device_id=self.runtime.status.device_id or "",
                variable_name="zero_offset",
                captured_at=result.record.after.captured_at,
                value=result.record.after.zero_offset,
            ),
            VariableSample(
                sample_id=f"{result.run_id}-DELTA-T",
                device_id=self.runtime.status.device_id or "",
                variable_name="delta_t",
                captured_at=result.record.after.captured_at,
                value=result.record.after.delta_t,
            ),
            VariableSample(
                sample_id=f"{result.run_id}-ZERO-START",
                device_id=self.runtime.status.device_id or "",
                variable_name="zero_calibration_start",
                captured_at=result.record.after.captured_at,
                value=not result.record.completed,
            ),
        )
        self._update_map_values(samples)
        self._refresh_history_dialogs()
        self._log(f"Zero calibration completed {result.run_id}")

    def _read_variable_row(self, row: int) -> None:
        try:
            name = _required_table_text(self.variableMapTable, row, 0)
        except Exception as exc:
            self._log(f"Read failed: {exc}")
            return
        self._run_task(
            "Read variable",
            lambda: self.runtime.read_variables((name,)),
            lambda result: self._read_variable_row_finished(row, name, result),
            requires_connection=True,
        )

    def _read_variable_for_button(self, button: QPushButton) -> None:
        row = self._row_for_operation_button(button)
        if row is None:
            self._log("Read failed: variable row no longer exists.")
            return
        self._read_variable_row(row)

    def _write_variable_row(self, row: int) -> None:
        try:
            name = _required_table_text(self.variableMapTable, row, 0)
            value_widget = self.variableMapTable.cellWidget(row, 10)
            if not isinstance(value_widget, QLineEdit):
                raise ValueError(f"Write value field missing for {name}.")
            value = value_widget.text()
        except Exception as exc:
            self._log(f"Write failed: {exc}")
            return
        self._run_task(
            "Write variable",
            lambda: self.runtime.write_variable(name, value),
            lambda result: self._write_variable_finished(row, name, result),
            requires_connection=True,
        )

    def _write_variable_for_button(self, button: QPushButton) -> None:
        row = self._row_for_operation_button(button)
        if row is None:
            self._log("Write failed: variable row no longer exists.")
            return
        self._write_variable_row(row)

    def _send_raw_frame(self) -> None:
        try:
            frame = _parse_hex_frame(self.rawFrameLineEdit.text())
        except ValueError as exc:
            self._log(f"Raw frame failed: {exc}")
            return
        self._run_task(
            "Raw frame",
            lambda: self.runtime.send_raw_frame(
                frame,
                append_crc=self.rawFrameAutoCrcCheckBox.isChecked(),
            ),
            self._raw_frame_finished,
            requires_connection=True,
        )

    def _raw_frame_finished(self, result: object) -> None:
        return

    def _row_for_operation_button(self, button: QPushButton) -> int | None:
        for row in range(self.variableMapTable.rowCount()):
            operations = self.variableMapTable.cellWidget(row, 11)
            if operations is None:
                continue
            layout = operations.layout()
            if layout is None:
                continue
            for index in range(layout.count()):
                if layout.itemAt(index).widget() is button:
                    return row
        return None

    def _read_variables_finished(self, result: object) -> None:
        if not isinstance(result, ModbusVariableSampleResult):
            self._log(f"Read finished: {result}")
            return
        self._update_map_values(result.samples)
        for error in result.errors:
            self._log(f"Read warning: {error}")
        self._log(f"Read {len(result.samples)} variable(s).")

    def _read_variable_row_finished(
        self,
        row: int,
        name: str,
        result: object,
    ) -> None:
        if not isinstance(result, ModbusVariableSampleResult):
            self._log(f"Read {name} finished: {result}")
            return
        sample = next(
            (item for item in result.samples if item.variable_name == name),
            result.samples[0] if result.samples else None,
        )
        if sample is not None and 0 <= row < self.variableMapTable.rowCount():
            self._set_map_sample_value(row, sample)
            self._log(f"Read {name}: {_format_value(sample.value)}")
        else:
            self._update_map_values(result.samples)
        for error in result.errors:
            self._log(f"Read warning: {error}")
        self._log(f"Read {len(result.samples)} variable(s).")

    def _write_variable_finished(self, row: int, name: str, result: object) -> None:
        status = getattr(result, "status", None)
        message = getattr(result, "message", None)
        status_text = getattr(status, "value", str(status))
        if message:
            self._log(f"Write {name}: {status_text} ({message})")
        else:
            self._log(f"Write {name}: {status_text}")
        if status_text == "applied" and 0 <= row < self.variableMapTable.rowCount():
            self._set_map_text(
                row,
                9,
                _format_value(getattr(result, "new_value", "")),
                editable=False,
            )

    def _toggle_polling(self) -> None:
        if self._polling:
            self._stop_polling()
            return
        if not self.runtime.status.connected:
            self._log("Polling failed: connect the Modbus module first.")
            return
        names = self._selected_poll_variable_names()
        if not names:
            self._log("Polling failed: select at least one Poll checkbox.")
            return
        self._polling = True
        self.pollingButton.setText("Stop Polling")
        self._log("Polling started.")
        self._poll_selected_variables()
        self._poll_timer.start()

    def _stop_polling(self) -> None:
        if self._poll_timer.isActive():
            self._poll_timer.stop()
        if self._polling:
            self._log("Polling stopped.")
        self._polling = False
        if isValid(self.pollingButton):
            self.pollingButton.setText("Start Polling")

    def _poll_selected_variables(self) -> None:
        if not self._polling or self._busy:
            return
        names = self._selected_poll_variable_names()
        if not names:
            self._stop_polling()
            return
        self._run_task(
            "Poll",
            lambda: self.runtime.read_variables(names, merge_adjacent=True),
            self._poll_finished,
            requires_connection=True,
        )

    def _poll_finished(self, result: object) -> None:
        if not isinstance(result, ModbusVariableSampleResult):
            self._log(f"Poll finished: {result}")
            return
        self._update_map_values(result.samples)
        for error in result.errors:
            self._log(f"Poll warning: {error}")

    def _selected_poll_variable_names(self) -> tuple[str, ...]:
        names: list[str] = []
        for row in self._visual_rows():
            widget = self.variableMapTable.cellWidget(row, 8)
            if isinstance(widget, QCheckBox) and widget.isChecked():
                try:
                    names.append(_required_table_text(self.variableMapTable, row, 0))
                except ValueError:
                    continue
        return tuple(names)

    def _update_map_values(self, samples: tuple[VariableSample, ...]) -> None:
        by_name = {sample.variable_name: sample for sample in samples}
        for row in range(self.variableMapTable.rowCount()):
            name = _table_text(self.variableMapTable, row, 0)
            sample = by_name.get(name)
            if sample is None:
                continue
            self._set_map_sample_value(row, sample)

    def _set_map_sample_value(self, row: int, sample: VariableSample) -> None:
        value = f"{_format_value(sample.value)} {sample.unit or ''}".strip()
        self._set_map_text(row, 9, value, editable=False)
        self.variableMapTable.viewport().update()

    def _register_unit(self, variable_name: str) -> str:
        try:
            return self.runtime.register_map.by_name(variable_name).unit or ""
        except KeyError:
            return ""

    def _register_units(self, variable_names: tuple[str, ...]) -> dict[str, str]:
        return {name: self._register_unit(name) for name in _unique_names(variable_names)}

    def _run_task(
        self,
        label: str,
        action,
        on_finished,
        *,
        requires_connection: bool,
        on_progress=None,
    ) -> None:
        if self._busy:
            self._log(f"{label} skipped: another Modbus operation is running.")
            return
        if requires_connection and not self.runtime.status.connected:
            self._log(f"{label} failed: connect the Modbus module first.")
            return
        self._busy = True
        self._set_controls_enabled(False)
        self._log(f"{label} started.")
        task = WorkflowTask(action, emit_progress=on_progress is not None)
        if on_progress is not None:
            task.signals.progress.connect(
                on_progress,
                Qt.ConnectionType.QueuedConnection,
            )
        task.signals.finished.connect(lambda result: self._task_finished(on_finished, result))
        task.signals.failed.connect(lambda message: self._task_failed(label, message))
        self._active_tasks.append(task)
        self._thread_pool.start(task)

    def _populate_variable_map(self) -> None:
        registers = self._variable_map_registers()
        self.variableMapTable.setRowCount(len(registers))
        for row, register in enumerate(registers):
            self._populate_variable_map_row(
                row,
                register,
                variable_name_editable=_is_custom_ui_register(register),
            )
        self._refresh_variable_map_edit_state()
        self._refresh_variable_map_scroll_range()

    def _variable_map_registers(self) -> list[ModbusRegister]:
        return _ui_registers_from_map(self.runtime.register_map)

    def _populate_variable_map_row(
        self,
        row: int,
        register: ModbusRegister,
        *,
        variable_name_editable: bool,
    ) -> None:
        self._set_map_text(row, 0, register.name, editable=variable_name_editable)
        kind_combo = QComboBox()
        kind_combo.addItems([kind.value for kind in RegisterKind])
        kind_combo.setCurrentText(register.kind.value)
        self.variableMapTable.setCellWidget(row, 1, kind_combo)
        self._set_map_text(row, 2, str(register.address))
        self._set_map_text(row, 3, str(register.word_count))
        type_combo = QComboBox()
        type_combo.addItems([data_type.value for data_type in ModbusDataType])
        type_combo.setCurrentText(register.data_type.value)
        self.variableMapTable.setCellWidget(row, 4, type_combo)
        self._set_map_text(row, 5, _format_value(register.scale))
        self._set_map_text(row, 6, register.unit or "")
        writable_combo = QComboBox()
        writable_combo.addItems(["false", "true"])
        writable_combo.setCurrentText("true" if register.writable else "false")
        self.variableMapTable.setCellWidget(row, 7, writable_combo)
        poll_box = QCheckBox()
        poll_box.setObjectName(f"modbusPollCheckBox{row}")
        self.variableMapTable.setCellWidget(row, 8, poll_box)
        self._set_map_text(row, 9, "", editable=False)
        write_value = QLineEdit()
        write_value.setObjectName(f"modbusWriteValueLineEdit{row}")
        self.variableMapTable.setCellWidget(row, 10, write_value)
        operations = QWidget()
        operation_layout = QHBoxLayout(operations)
        operation_layout.setContentsMargins(0, 0, 0, 0)
        operation_layout.setSpacing(4)
        read_button = QPushButton("Read")
        read_button.setObjectName(f"modbusReadVariableButton{row}")
        write_button = QPushButton("Write")
        write_button.setObjectName(f"modbusWriteVariableButton{row}")
        read_button.clicked.connect(
            lambda _checked=False, button=read_button: self._read_variable_for_button(button)
        )
        write_button.clicked.connect(
            lambda _checked=False, button=write_button: self._write_variable_for_button(button)
        )
        operation_layout.addWidget(read_button)
        operation_layout.addWidget(write_button)
        self.variableMapTable.setCellWidget(row, 11, operations)

    def _add_variable_row(self) -> None:
        row = self.variableMapTable.rowCount()
        self.variableMapTable.insertRow(row)
        self._populate_variable_map_row(
            row,
            ModbusRegister(
                name=f"custom_{row + 1}",
                kind=RegisterKind.HOLDING,
                address=0,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                writable=False,
                metadata={"source": "modbus_module_ui_custom"},
            ),
            variable_name_editable=True,
        )
        self._refresh_variable_map_edit_state()
        self._refresh_variable_map_scroll_range()

    def _delete_selected_variable_row(self) -> None:
        if self.runtime.status.connected:
            self._log("Delete variable skipped: disconnect before changing the map.")
            return
        if self.variableMapTable.rowCount() <= 1:
            self._log("Delete variable skipped: keep at least one variable.")
            return
        row = self.variableMapTable.currentRow()
        if row < 0:
            selected = self.variableMapTable.selectedIndexes()
            if selected:
                row = min(index.row() for index in selected)
        if row < 0 or row >= self.variableMapTable.rowCount():
            self._log("Delete variable skipped: select a variable row first.")
            return
        name = _table_text(self.variableMapTable, row, 0) or f"row {row + 1}"
        self.variableMapTable.removeRow(row)
        next_row = min(row, self.variableMapTable.rowCount() - 1)
        if next_row >= 0:
            self.variableMapTable.selectRow(next_row)
        self._refresh_variable_map_edit_state()
        self._refresh_variable_map_scroll_range()
        self._log(f"Deleted variable: {name}")

    def _move_variable_row(self, source: int, target: int) -> None:
        if self.runtime.status.connected:
            self._log("Move variable skipped: disconnect before changing the map.")
            return
        if source < 0 or source >= self.variableMapTable.rowCount():
            return
        if target < 0 or target >= self.variableMapTable.rowCount():
            return
        if source == target:
            return
        rows = [self._snapshot_variable_row(row) for row in range(self.variableMapTable.rowCount())]
        moved = rows.pop(source)
        rows.insert(target, moved)
        self.variableMapTable.setRowCount(len(rows))
        for row, snapshot in enumerate(rows):
            self._populate_variable_map_row(
                row,
                snapshot["register"],
                variable_name_editable=bool(snapshot["name_editable"]),
            )
            self._restore_row_ui_state(row, snapshot["ui_state"])
        self.variableMapTable.selectRow(target)
        self._refresh_variable_map_edit_state()
        self._refresh_variable_map_scroll_range()

    def _refresh_variable_map_scroll_range(self) -> None:
        self.variableMapTable.updateGeometries()
        self.variableMapTable.viewport().update()

    def _snapshot_variable_row(self, row: int) -> dict[str, object]:
        return {
            "register": self._register_from_row(row),
            "name_editable": self._is_variable_name_editable(row),
            "ui_state": self._row_ui_state(row),
        }

    def _register_from_row(self, row: int) -> ModbusRegister:
        return ModbusRegister(
            name=_required_table_text(self.variableMapTable, row, 0),
            kind=RegisterKind(_combo_text(self.variableMapTable, row, 1)),
            address=int(_required_table_text(self.variableMapTable, row, 2)),
            word_count=int(_required_table_text(self.variableMapTable, row, 3)),
            data_type=ModbusDataType(_combo_text(self.variableMapTable, row, 4)),
            writable=_combo_text(self.variableMapTable, row, 7) == "true",
            scale=float(_required_table_text(self.variableMapTable, row, 5)),
            unit=_table_text(self.variableMapTable, row, 6) or None,
            metadata={
                "source": "modbus_module_ui_custom"
                if self._is_variable_name_editable(row)
                else "modbus_module_ui"
            },
        )

    def _is_variable_name_editable(self, row: int) -> bool:
        item = self.variableMapTable.item(row, 0)
        if item is None:
            return True
        return bool(item.flags() & Qt.ItemFlag.ItemIsEditable)

    def _row_ui_state(self, row: int) -> dict[str, object]:
        poll_box = self.variableMapTable.cellWidget(row, 8)
        value = _table_text(self.variableMapTable, row, 9)
        write_value = self.variableMapTable.cellWidget(row, 10)
        return {
            "poll": isinstance(poll_box, QCheckBox) and poll_box.isChecked(),
            "value": value,
            "write_value": write_value.text() if isinstance(write_value, QLineEdit) else "",
        }

    def _restore_row_ui_state(self, row: int, state: dict[str, object]) -> None:
        poll_box = self.variableMapTable.cellWidget(row, 8)
        if isinstance(poll_box, QCheckBox):
            poll_box.setChecked(bool(state["poll"]))
        self._set_map_text(row, 9, str(state["value"]), editable=False)
        write_value = self.variableMapTable.cellWidget(row, 10)
        if isinstance(write_value, QLineEdit):
            write_value.setText(str(state["write_value"]))

    def _set_map_text(
        self,
        row: int,
        column: int,
        value: str,
        *,
        editable: bool = True,
    ) -> None:
        item = QTableWidgetItem(value)
        item.setData(Qt.ItemDataRole.UserRole, editable)
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.variableMapTable.setItem(row, column, item)

    def _register_map_from_ui(self, *, order: str | None = None) -> ModbusRegisterMap:
        registers: list[ModbusRegister] = []
        word_order, byte_order = _order_to_modbus_orders(order or self._last_order)
        seen_names: set[str] = set()
        for row in self._visual_rows():
            name = _required_table_text(self.variableMapTable, row, 0)
            if name in seen_names:
                raise ValueError(f"Duplicate variable name: {name}.")
            seen_names.add(name)
            kind = RegisterKind(_combo_text(self.variableMapTable, row, 1))
            address = int(_required_table_text(self.variableMapTable, row, 2))
            word_count = int(_required_table_text(self.variableMapTable, row, 3))
            data_type = ModbusDataType(_combo_text(self.variableMapTable, row, 4))
            scale = float(_required_table_text(self.variableMapTable, row, 5))
            unit_text = _table_text(self.variableMapTable, row, 6)
            writable = _combo_text(self.variableMapTable, row, 7) == "true"
            if address < 0:
                raise ValueError(f"Address must be non-negative for {name}.")
            if word_count < 1:
                raise ValueError(f"Words must be at least 1 for {name}.")
            registers.append(
                ModbusRegister(
                    name=name,
                    kind=kind,
                    address=address,
                    word_count=word_count,
                    data_type=data_type,
                    writable=writable,
                    scale=scale,
                    unit=unit_text or None,
                    word_order=word_order,
                    byte_order=byte_order,
                    metadata={
                        "source": "modbus_module_ui_custom"
                        if self._is_variable_name_editable(row)
                        else "modbus_module_ui"
                    },
                )
            )
        return ModbusRegisterMap(
            name="modbus-module-ui-map",
            version=datetime.now().strftime("%Y%m%d%H%M%S"),
            registers=tuple(registers),
        )

    def _visual_rows(self) -> tuple[int, ...]:
        header = self.variableMapTable.verticalHeader()
        return tuple(
            header.logicalIndex(visual_row)
            for visual_row in range(self.variableMapTable.rowCount())
        )

    def _task_finished(self, on_finished, result: object) -> None:
        if not self._can_update_ui():
            return
        self._busy = False
        self._active_tasks.clear()
        on_finished(result)
        self._set_controls_enabled(True)

    def _task_failed(self, label: str, message: str) -> None:
        if not self._can_update_ui():
            return
        self._busy = False
        self._active_tasks.clear()
        if label == "K factor":
            self._k_factor_cancel_event = None
        self._sync_status()
        self._set_controls_enabled(True)
        if (
            label == "Zero calibration"
            and self.zeroCalibrationDialog is not None
            and isValid(self.zeroCalibrationDialog)
        ):
            self.zeroCalibrationDialog.set_error(message)
        if label == "Variable sampling":
            self._variable_sampling_cancel_event = None
        if (
            label == "Variable sampling"
            and self.variableSamplingDialog is not None
            and isValid(self.variableSamplingDialog)
        ):
            self.variableSamplingDialog.set_error(message)
            self.variableSamplingDialog.set_ready(connected=self.runtime.status.connected)
        if label == "Zero monitor":
            self._zero_monitor_stop_event = None
            self._zero_monitor_cancel_event = None
            if self.zeroMonitorDialog is not None and isValid(self.zeroMonitorDialog):
                self.zeroMonitorDialog.set_error(message)
                self.zeroMonitorDialog.set_ready(connected=self.runtime.status.connected)
        if (
            label in {"K factor", "K factor write"}
            and self.kFactorDialog is not None
            and isValid(self.kFactorDialog)
        ):
            self.kFactorDialog.set_error(message)
        if label == "Repeatability":
            self._repeatability_cancel_event = None
        if (
            label == "Repeatability"
            and self.repeatabilityDialog is not None
            and isValid(self.repeatabilityDialog)
        ):
            self.repeatabilityDialog.set_error(message)
            self.repeatabilityDialog.fail_capture_progress(message)
        self._log(f"{label} failed: {message}")

    def _can_update_ui(self) -> bool:
        return (
            isValid(self)
            and isValid(self.logTextEdit)
        )

    def _set_connected_controls(self, connected: bool) -> None:
        self.openConnectionButton.setEnabled(True)
        self.disconnectButton.setEnabled(connected)
        self.deviceProfileCombo.setEnabled(not connected)
        self.deviceIdLineEdit.setEnabled(not connected)
        self.refreshProfilesButton.setEnabled(not connected)
        self.createDeviceProfileButton.setEnabled(not connected)
        self.editDeviceProfileButton.setEnabled((not connected) and bool(self._selected_profile_device_id()))
        self.deleteDeviceProfileButton.setEnabled((not connected) and bool(self._selected_profile_device_id()))
        self.saveDeviceProfileButton.setEnabled((not connected) and bool(self._selected_profile_device_id()))
        for action in (
            self.sampleVariablesAction,
            self.zeroMonitorAction,
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            action.setEnabled(connected)
        self.calibrationHistoryAction.setEnabled(True)
        self.deviceAnalysisAction.setEnabled(
            bool(self.runtime.status.device_id or self._selected_profile_device_id())
        )
        self.currentDeviceHistoryAction.setEnabled(
            bool(self.runtime.status.device_id or self._selected_profile_device_id())
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        enabled = enabled and not self._busy
        connected = self.runtime.status.connected
        self.openConnectionButton.setEnabled(enabled)
        self.disconnectButton.setEnabled(enabled and connected)
        self.deviceProfileCombo.setEnabled(enabled and not connected)
        self.deviceIdLineEdit.setEnabled(enabled and not connected)
        self.refreshProfilesButton.setEnabled(enabled and not connected)
        self.createDeviceProfileButton.setEnabled(enabled and not connected)
        self.editDeviceProfileButton.setEnabled(
            enabled and not connected and bool(self._selected_profile_device_id())
        )
        self.deleteDeviceProfileButton.setEnabled(
            enabled and not connected and bool(self._selected_profile_device_id())
        )
        self.saveDeviceProfileButton.setEnabled(
            enabled and not connected and bool(self._selected_profile_device_id())
        )
        self.variableMapTable.setEnabled(True)
        self.addVariableButton.setEnabled(enabled and not connected)
        self.deleteVariableButton.setEnabled(
            enabled and not connected and self.variableMapTable.rowCount() > 1
        )
        self.resetVariableMapButton.setEnabled(enabled and not connected)
        self.saveVariableMapButton.setEnabled(enabled and not connected)
        self.pollingButton.setEnabled(enabled and connected)
        self.rawFrameLineEdit.setEnabled(enabled)
        self.rawFrameAutoCrcCheckBox.setEnabled(enabled)
        self.sendRawFrameButton.setEnabled(enabled and connected)
        for widget in (
            self.deviceModelLineEdit,
            self.tubeModelLineEdit,
            self.transmitterModelLineEdit,
            self.massAccBeforeSpinBox,
            self.massAccAfterSpinBox,
            self.standardMassSpinBox,
            self.currentKFactorSpinBox,
        ):
            widget.setEnabled(enabled)
        if self.connectionDialog is not None and isValid(self.connectionDialog):
            self.connectionDialog.set_controls_enabled(enabled and not connected)
        for action in (
            self.sampleVariablesAction,
            self.zeroMonitorAction,
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            action.setEnabled(enabled and connected)
        self.calibrationHistoryAction.setEnabled(enabled)
        self.currentDeviceHistoryAction.setEnabled(
            enabled and bool(self.runtime.status.device_id or self._selected_profile_device_id())
        )
        self.deviceAnalysisAction.setEnabled(
            enabled and bool(self.runtime.status.device_id or self._selected_profile_device_id())
        )
        if (
            self.zeroCalibrationDialog is not None
            and isValid(self.zeroCalibrationDialog)
            and self.zeroCalibrationDialog.statusLabel.text() != "Running..."
        ):
            self.zeroCalibrationDialog.set_ready(connected=connected and enabled)
        if (
            self.kFactorDialog is not None
            and isValid(self.kFactorDialog)
            and self.kFactorDialog.statusLabel.text() != "Running..."
        ):
            self.kFactorDialog.set_ready(connected=connected and enabled)
        if (
            self.repeatabilityDialog is not None
            and isValid(self.repeatabilityDialog)
            and self.repeatabilityDialog.statusLabel.text() != "Running..."
        ):
            self.repeatabilityDialog.set_ready(connected=connected and enabled)
        if (
            self.variableSamplingDialog is not None
            and isValid(self.variableSamplingDialog)
            and self._variable_sampling_cancel_event is None
        ):
            self.variableSamplingDialog.set_ready(connected=connected and enabled)
        self._refresh_variable_map_edit_state()

    def _refresh_variable_map_edit_state(self) -> None:
        connected = self.runtime.status.connected
        config_enabled = (not connected) and (not self._busy)
        operation_enabled = connected and (not self._busy)
        self.deleteVariableButton.setEnabled(
            config_enabled and self.variableMapTable.rowCount() > 1
        )
        self.variableMapTable.setDragEnabled(config_enabled)
        self.variableMapTable.setAcceptDrops(config_enabled)
        self.variableMapTable.viewport().setAcceptDrops(config_enabled)
        for row in range(self.variableMapTable.rowCount()):
            for column in (1, 4, 7):
                widget = self.variableMapTable.cellWidget(row, column)
                if widget is not None:
                    widget.setEnabled(config_enabled)
            writable = _combo_text(self.variableMapTable, row, 7) == "true"
            write_value = self.variableMapTable.cellWidget(row, 10)
            if write_value is not None:
                write_value.setEnabled(operation_enabled and writable)
            poll_box = self.variableMapTable.cellWidget(row, 8)
            if poll_box is not None:
                poll_box.setEnabled(connected and not self._busy)
            read_button, write_button = self._operation_buttons(row)
            if read_button is not None:
                read_button.setEnabled(operation_enabled)
            if write_button is not None:
                write_button.setEnabled(operation_enabled and writable)
            for column in (0, 2, 3, 5, 6):
                item = self.variableMapTable.item(row, column)
                if item is None:
                    continue
                editable = item.data(Qt.ItemDataRole.UserRole)
                is_config_editable = True if editable is None else bool(editable)
                if config_enabled and is_config_editable:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)

    def _operation_buttons(
        self,
        row: int,
    ) -> tuple[QPushButton | None, QPushButton | None]:
        operations = self.variableMapTable.cellWidget(row, 11)
        if operations is None or operations.layout() is None:
            return None, None
        layout = operations.layout()
        read_button = layout.itemAt(0).widget() if layout.count() > 0 else None
        write_button = layout.itemAt(1).widget() if layout.count() > 1 else None
        return (
            read_button if isinstance(read_button, QPushButton) else None,
            write_button if isinstance(write_button, QPushButton) else None,
        )

    def _record_modbus_frame(self, direction: str, operation: str, data: str) -> None:
        self._modbusFrameRecorded.emit(direction, operation, data)

    def _append_modbus_frame(self, direction: str, operation: str, data: str) -> None:
        if not self._can_update_ui():
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logTextEdit.append(f"{stamp} | {direction} | {operation} | {data}")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logTextEdit.append(f"{stamp} {message}")


def _float_input(value: float) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(-1_000_000.0, 1_000_000.0)
    widget.setDecimals(6)
    widget.setValue(value)
    return widget


def _fit_dialog_to_screen(
    dialog: QDialog,
    width: int,
    height: int,
    minimum_width: int,
    minimum_height: int,
) -> None:
    screen = QApplication.primaryScreen()
    if screen is None:
        dialog.resize(width, height)
        dialog.setMinimumSize(minimum_width, minimum_height)
        return
    available = screen.availableGeometry()
    max_width = max(360, available.width() - 80)
    max_height = max(320, available.height() - 100)
    fitted_min_width = min(minimum_width, max_width)
    fitted_min_height = min(minimum_height, max_height)
    dialog.setMinimumSize(fitted_min_width, fitted_min_height)
    dialog.resize(
        min(width, max_width),
        min(height, max_height),
    )


def _sample_variable_names() -> tuple[str, ...]:
    return (
        "mass_rate",
        "mass_acc",
        "temperature",
        "delta_t",
        "zero_offset",
        "k_factor",
        "low_threshold",
    )


def _editable_register_names() -> tuple[str, ...]:
    return (
        *_sample_variable_names(),
        "zero_calibration_start",
    )


def _ui_registers_from_map(register_map: ModbusRegisterMap) -> list[ModbusRegister]:
    registers = list(register_map.registers)
    if any(_is_ui_register(register) for register in registers):
        return registers
    by_name = {register.name: register for register in registers}
    return [
        by_name[name]
        for name in _editable_register_names()
        if name in by_name
    ]


def _is_ui_register(register: ModbusRegister) -> bool:
    source = register.metadata.get("source")
    return source in {"modbus_module_ui", "modbus_module_ui_custom"}


def _is_custom_ui_register(register: ModbusRegister) -> bool:
    return register.metadata.get("source") == "modbus_module_ui_custom"


def _editable_register_signature(register: ModbusRegister) -> tuple[object, ...]:
    return (
        register.kind,
        register.address,
        register.word_count,
        register.data_type,
        register.writable,
        register.scale,
        register.unit,
        register.word_order,
        register.byte_order,
    )


def _register_map_combo_key(register_map_id: str, version: str) -> str:
    return f"{register_map_id}@{version}"


def _default_zero_snapshot_names(
    registers: tuple[ModbusRegister, ...],
) -> tuple[str, ...]:
    preferred = (
        "mass_rate",
        "mass_acc",
        "temperature",
        "delta_t",
        "zero_offset",
    )
    available = {register.name for register in registers}
    return tuple(name for name in preferred if name in available)


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return _format_datetime(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _format_k_value(value: object) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    text = f"{number:.12f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _format_optional_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return _format_value(value)


def _names_from_csv(text: str) -> tuple[str, ...]:
    names: list[str] = []
    for chunk in text.replace(";", ",").split(","):
        name = chunk.strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _snapshot_trial_values_text(
    trial: ModbusRepeatabilityHistoryTrial,
    variable_names: tuple[str, ...],
) -> str:
    return ", ".join(
        f"{variable_name}={_format_value(trial.pre_snapshot.get(variable_name, ''))}"
        for variable_name in variable_names
    )


def _device_analysis_available_snapshot_names(
    history_trials: tuple[ModbusRepeatabilityHistoryTrial, ...],
) -> tuple[str, ...]:
    names: list[str] = []
    for history_trial in history_trials:
        for name in history_trial.pre_snapshot:
            if name not in names:
                names.append(str(name))
    preferred = ("zero_offset", "low_threshold")
    ordered = [name for name in preferred if name in names]
    ordered.extend(name for name in names if name not in ordered)
    return tuple(ordered)


def _sample_stddev(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return variance ** 0.5


def _device_analysis_preview_metrics_from_selection(
    selected: dict[float, tuple[ModbusRepeatabilityHistoryTrial, ...]],
) -> dict[str, object]:
    flow_rows: list[dict[str, object]] = []
    measurement_errors: list[float] = []
    original_k_values = {
        item.trial.original_k_factor
        for trials in selected.values()
        for item in trials
    }
    if len(original_k_values) != 1:
        raise ValueError("selected trials have different original K values")
    original_k_factor = float(next(iter(original_k_values)))
    for flow_point, history_trials in sorted(selected.items()):
        trials = tuple(item.trial for item in history_trials)
        errors = tuple(trial.percent_error for trial in trials)
        measurement_error = sum(errors) / len(errors)
        measurement_errors.append(measurement_error)
        flow_rows.append(
            {
                "flow_point": flow_point,
                "measurement_error_percent": measurement_error,
                "repeatability_stddev_percent": _sample_stddev(errors),
            }
        )
    average_error = (max(measurement_errors) + min(measurement_errors)) / 2.0
    intermediate_k_values: list[float] = []
    for row in flow_rows:
        measurement_error = float(row["measurement_error_percent"])
        adjusted_error = measurement_error - average_error
        denominator = 1.0 + measurement_error / 100.0
        if denominator == 0:
            raise ValueError("Final K calculation produced a zero denominator.")
        row["adjusted_error_percent"] = adjusted_error
        intermediate_k_values.append(original_k_factor / denominator)
    new_k_factor = (max(intermediate_k_values) + min(intermediate_k_values)) / 2.0
    return {
        "original_k_factor": original_k_factor,
        "new_k_factor": new_k_factor,
        "flow_points": flow_rows,
    }


def _device_analysis_preview_lines(metrics: dict[str, object]) -> list[str]:
    lines: list[str] = []
    flow_rows = metrics.get("flow_points")
    if isinstance(flow_rows, list):
        for row in sorted(
            (item for item in flow_rows if isinstance(item, dict)),
            key=lambda item: float(item.get("flow_point", 0.0)),
        ):
            lines.append(
                f"Flow {_format_value(row.get('flow_point', ''))}g/s: "
                f"adjusted_error={_format_value(row.get('adjusted_error_percent', ''))}%, "
                f"repeatability={_format_value(row.get('repeatability_stddev_percent', ''))}%"
            )
    lines.append(
        "K value: "
        f"old={_format_k_value(metrics.get('original_k_factor'))}, "
        f"new={_format_k_value(metrics.get('new_k_factor'))}"
    )
    return lines


def _is_consecutive_trials(
    trials: tuple[ModbusRepeatabilitySimpleTrialResult, ...],
) -> bool:
    if not trials:
        return False
    indexes = tuple(trial.trial_index for trial in trials)
    return indexes == tuple(range(indexes[0], indexes[0] + len(indexes)))


def _safe_config_name(value: str) -> str:
    safe = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_", "."})
        else "_"
        for character in value.strip()
    ).strip("._")
    return safe or "device"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _unique_names(names: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in names if name))


def _normalize_plot_layout(value: object) -> str:
    text = str(value or PLOT_LAYOUT_OVERLAY)
    return text if text in PLOT_LAYOUTS else PLOT_LAYOUT_OVERLAY


def _plot_layout_label(value: object) -> str:
    layout = _normalize_plot_layout(value)
    if layout == PLOT_LAYOUT_SEPARATE:
        return "One plot per variable"
    return "Overlay variables"


def _normalize_plot_alignment(value: object) -> str:
    text = str(value or PLOT_ALIGNMENT_FIRST_SAMPLE)
    return text if text in PLOT_ALIGNMENTS else PLOT_ALIGNMENT_FIRST_SAMPLE


def _plot_alignment_label(value: object) -> str:
    alignment = _normalize_plot_alignment(value)
    if alignment == PLOT_ALIGNMENT_PREFLOW_SAMPLE:
        return "Pre-flow sample"
    return "First sample"


def _flow_alignment_timestamp(
    series: ModbusFlowSampleSeries,
    alignment: str,
) -> datetime | None:
    points = series.points
    if not points:
        return None
    if _normalize_plot_alignment(alignment) != PLOT_ALIGNMENT_PREFLOW_SAMPLE:
        return points[0].captured_at
    flow_values = series.values_for(series.flow_rate_parameter)
    previous_timestamp = points[0].captured_at
    for point, flow_value in zip(points, flow_values):
        if flow_value is not None and abs(flow_value) > 0.0:
            return previous_timestamp
        previous_timestamp = point.captured_at
    return points[0].captured_at


def _variable_label(name: str, unit: str = "") -> str:
    return f"{name} ({unit})" if unit else name


def _object_name_token(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def _optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_plot_point_data(data: dict[str, object]) -> str:
    label = str(data.get("label") or "")
    variable = str(data.get("variable") or "")
    unit = str(data.get("unit") or "")
    value = data.get("value")
    elapsed = _optional_float(data.get("elapsed_s"))
    sample_index = data.get("sample_index")
    captured_at = data.get("captured_at")
    parts: list[str] = []
    if label:
        parts.append(label)
    if variable:
        parts.append(variable)
    if sample_index not in (None, ""):
        parts.append(f"sample #{sample_index}")
    if elapsed is not None:
        parts.append(f"t={elapsed:.6g} s")
    if isinstance(captured_at, datetime):
        parts.append(f"time={_format_datetime(captured_at)}")
    if value not in (None, ""):
        suffix = f" {unit}" if unit else ""
        parts.append(f"value={_format_value(value)}{suffix}")
    return " | ".join(parts) if parts else "none"


def _is_repeatability_snapshot_read_status(message: str) -> bool:
    return message in {
        "Reading selected variables before this trial...",
        "Selected variables read. Start this trial when the device flow is ready.",
    }


def _plot_colors() -> tuple[str, ...]:
    return (
        "#1f77b4",
        "#d62728",
        "#2ca02c",
        "#9467bd",
        "#ff7f0e",
        "#17becf",
        "#8c564b",
        "#e377c2",
        "#7f7f7f",
    )


def _series_variable_names(
    series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...],
) -> tuple[str, ...]:
    names: list[str] = []
    for _label, series in series_items:
        if series.variable_names:
            names.extend(series.variable_names)
        elif series.flow_rate_parameter:
            names.append(series.flow_rate_parameter)
    return _unique_names(tuple(names))


def _series_units(
    series_items: tuple[tuple[str, ModbusFlowSampleSeries], ...],
) -> dict[str, str]:
    units: dict[str, str] = {}
    for _label, series in series_items:
        units.update(series.units)
        if series.flow_rate_parameter and series.unit:
            units.setdefault(series.flow_rate_parameter, series.unit)
    return units


def _qt_datetime_from_datetime(value: datetime) -> QDateTime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    local_value = value.astimezone()
    return QDateTime(
        local_value.year,
        local_value.month,
        local_value.day,
        local_value.hour,
        local_value.minute,
        local_value.second,
    )


def _datetime_from_qt_datetime(value: QDateTime) -> datetime:
    py_value = value.toPython()
    if isinstance(py_value, datetime):
        if py_value.tzinfo is None:
            return py_value.astimezone()
        return py_value
    return datetime(
        value.date().year(),
        value.date().month(),
        value.date().day(),
        value.time().hour(),
        value.time().minute(),
        value.time().second(),
    ).astimezone()


def _format_history_value(name: str, value: object) -> str:
    if name.endswith("_at") and isinstance(value, str):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return _format_datetime(parsed)
    if _is_k_metric_name(name):
        return _format_k_value(value)
    return _format_value(value)


def _format_history_value_with_unit(
    name: str,
    value: object,
    metrics: dict[str, object],
) -> str:
    if _history_metric_is_variable_list(name):
        variable_names = _format_history_variable_names(value, metrics)
        if variable_names:
            return variable_names
    text = _format_history_value(name, value)
    unit = _history_metric_unit(name, metrics)
    if not text or not unit or _history_value_already_has_unit(text, unit):
        return text
    if _history_metric_is_variable_selector(name):
        return f"{text} ({unit})"
    return f"{text} {unit}"


def _history_metric_unit(name: str, metrics: dict[str, object]) -> str:
    lower = name.lower()
    if lower.endswith("_percent") or lower in {
        "percent_error",
        "average_error",
        "average_error_percent",
        "mean_percent_error",
        "max_abs_percent_error",
        "repeatability_stddev_percent",
        "max_repeatability_stddev_percent",
        "adjusted_error_percent",
        "measurement_error_percent",
    }:
        return ""
    if lower.endswith("_count") or lower in {
        "trial_count",
        "selected_trial_count",
        "selected_flow_point_count",
        "trial_index",
        "flow_sample_count",
    }:
        return ""
    if lower.endswith("_id") or lower in {
        "attempt_id",
        "session_id",
        "run_id",
        "write_status",
        "write_verified",
        "flow_rate_source",
        "completed",
    }:
        return ""
    if lower.endswith("_s"):
        return "s"
    unit_by_variable = _history_register_units(metrics)
    explicit_variable = str(metrics.get(name) or "")
    if explicit_variable in unit_by_variable:
        return unit_by_variable[explicit_variable]
    variable_name = _history_metric_variable_name(name, metrics)
    if variable_name:
        return unit_by_variable.get(variable_name, "")
    if name in unit_by_variable:
        return unit_by_variable[name]
    return ""


def _history_metric_variable_name(
    name: str,
    metrics: dict[str, object],
) -> str:
    lower = name.lower()
    if _is_k_metric_name(name):
        return str(metrics.get("k_factor_parameter") or "k_factor")
    if lower in {
        "flow_point",
        "instant_flow",
        "mean_flow",
        "v1",
        "v_mean",
    }:
        return str(metrics.get("flow_rate_parameter") or "mass_rate")
    if lower in {
        "mass_acc_before",
        "mass_acc_after",
        "measured_mass_delta",
        "standard_mass",
    }:
        return str(metrics.get("flow_acc_parameter") or "mass_acc")
    if lower.endswith("_before") or lower.endswith("_after") or lower.endswith("_change"):
        return name.rsplit("_", 1)[0]
    return name


def _history_metric_is_variable_selector(name: str) -> bool:
    return name.lower() in {
        "flow_rate_parameter",
        "flow_acc_parameter",
        "k_factor_parameter",
        "zero_offset_parameter",
    }


def _history_metric_is_variable_list(name: str) -> bool:
    lower = name.lower()
    return lower in {
        "trial_sample_variable_names",
        "sample_variable_names",
        "snapshot_variable_names",
    } or lower.endswith("_variable_names")


def _format_history_variable_names(
    value: object,
    metrics: dict[str, object],
) -> str:
    if isinstance(value, str):
        names = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple)):
        names = [str(item).strip() for item in value if str(item).strip()]
    else:
        return ""
    units = _history_register_units(metrics)
    return ", ".join(
        f"{name} ({units[name]})" if units.get(name) else name for name in names
    )


def _history_register_units(metrics: dict[str, object]) -> dict[str, str]:
    snapshot = metrics.get("register_map_snapshot")
    units: dict[str, str] = {}
    if isinstance(snapshot, dict):
        registers = snapshot.get("registers")
        if isinstance(registers, list):
            for register in registers:
                if not isinstance(register, dict):
                    continue
                name = str(register.get("name") or "")
                unit = str(register.get("unit") or "")
                if name and unit:
                    units[name] = unit
    flow_units = metrics.get("units")
    if isinstance(flow_units, dict):
        for name, unit in flow_units.items():
            if str(name) and str(unit):
                units.setdefault(str(name), str(unit))
    return units


def _history_value_already_has_unit(text: str, unit: str) -> bool:
    return text == unit or text.endswith(f" {unit}") or text.endswith(f"({unit})")


def _is_k_metric_name(name: str) -> bool:
    lower = name.lower()
    return (
        lower in {"k0", "k1"}
        or lower.endswith("_k_factor")
        or lower.endswith("_k")
        or "intermediate_k_factor" in lower
        or "readback_k_factor" in lower
        or "corrected_k_factor" in lower
        or "original_k_factor" in lower
        or "current_k_factor" in lower
        or "new_k_factor" in lower
        or "delta_k_factor" in lower
    )


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _operation_label(value: str) -> str:
    labels = {
        "zero_calibration": "Zero Calibration",
        "modbus_zero_monitor": "Zero Monitor",
        "k_factor_calibration": "K Factor",
        "modbus_variable_sampling": "Variable Sampling",
        "manual_error_repeatability": "Repeatability",
        "manual_error_repeatability_final_k": "Repeatability Final K",
        "manual_error_repeatability_trial": "Repeatability Trial",
    }
    return labels.get(value, value)


def _metric_value(metrics: dict[str, object], key: str) -> str:
    if key not in metrics:
        return ""
    return _format_history_value_with_unit(key, metrics[key], metrics)


def _metric_pair(metrics: dict[str, object], before_key: str, after_key: str) -> str:
    before = _metric_value(metrics, before_key)
    after = _metric_value(metrics, after_key)
    if before and after:
        return f"{before} -> {after}"
    return after or before


def _history_parameter_summary(entry: ModbusCalibrationHistoryEntry) -> str:
    metrics = entry.metrics
    if entry.operation == "zero_calibration":
        delta_t = _metric_value(metrics, "delta_t_after")
        zero_offset = _metric_value(metrics, "zero_offset_after")
        values = []
        if delta_t:
            values.append(f"delta_t={delta_t}")
        if zero_offset:
            values.append(f"zero_offset={zero_offset}")
        return ", ".join(values)
    if entry.operation == "k_factor_calibration":
        k_factor = _metric_value(metrics, "corrected_k_factor")
        values = []
        if k_factor:
            values.append(f"k_factor={k_factor}")
        if "write_status" in metrics:
            values.append(f"write={_metric_value(metrics, 'write_status')}")
        if "write_verified" in metrics:
            values.append(f"verified={_metric_value(metrics, 'write_verified')}")
        return ", ".join(values)
    if entry.operation == "modbus_variable_sampling":
        values = []
        sample_count = _metric_value(metrics, "sample_count")
        if sample_count:
            values.append(f"samples={sample_count}")
        names = metrics.get("variable_names")
        if isinstance(names, list):
            label = ", ".join(str(name) for name in names[:3])
            if label:
                if len(names) > 3:
                    label += ", ..."
                values.append(f"variables={label}")
        return ", ".join(values)
    if entry.operation == "modbus_zero_monitor":
        values = []
        state = metrics.get("analysis_state")
        if state:
            values.append(f"state={state}")
        polls = metrics.get("logical_poll_count")
        if polls not in (None, ""):
            values.append(f"polls={polls}")
        rate = metrics.get("achieved_poll_rate_hz")
        if isinstance(rate, (int, float)):
            values.append(f"rate={rate:.3g} Hz")
        return ", ".join(values)
    if entry.operation == "k_factor_calibration_capture":
        values = []
        delta_m = _metric_value(metrics, "measured_mass_delta")
        if delta_m:
            values.append(f"delta_m={delta_m}")
        current_k = _metric_value(metrics, "current_k_factor")
        if current_k:
            values.append(f"k0={current_k}")
        instant_flow = _metric_value(metrics, "instant_flow")
        if instant_flow:
            values.append(f"v1={instant_flow}")
        return ", ".join(values)
    if entry.operation == "manual_error_repeatability_final_k":
        new_k = _metric_value(metrics, "new_k_factor")
        delta_k = _metric_value(metrics, "delta_k_factor")
        average_error = _metric_value(metrics, "average_error")
        values = []
        if new_k:
            values.append(f"new_k={new_k}")
        if delta_k:
            values.append(f"delta_k={delta_k}")
        if average_error:
            values.append(f"average_error={average_error}%")
        return ", ".join(values)
    if entry.operation == "manual_error_repeatability":
        trial_count = _metric_value(metrics, "trial_count")
        values = []
        if trial_count:
            values.append(f"trials={trial_count}")
        mean_error = _metric_value(metrics, "mean_percent_error")
        if mean_error:
            values.append(f"mean_error={mean_error}%")
        repeatability = _metric_value(metrics, "max_repeatability_stddev_percent")
        if repeatability:
            values.append(f"repeatability={repeatability}%")
        return ", ".join(values)
    if entry.operation == "manual_error_repeatability_trial":
        values = []
        flow_point = _metric_value(metrics, "flow_point")
        trial_index = _metric_value(metrics, "trial_index")
        if flow_point or trial_index:
            values.append(f"flow={flow_point or '?'} trial={trial_index or '?'}")
        percent_error = _metric_value(metrics, "percent_error")
        if percent_error:
            values.append(f"error={percent_error}%")
        delta_m = _metric_value(metrics, "measured_mass_delta")
        if delta_m:
            values.append(f"delta_m={delta_m}")
        standard_mass = _metric_value(metrics, "standard_mass")
        if standard_mass:
            values.append(f"standard={standard_mass}")
        original_k = _metric_value(metrics, "original_k_factor")
        if original_k:
            values.append(f"k0={original_k}")
        return ", ".join(values)
    return ""


def _history_flow_sample_artifacts(
    entry: ModbusCalibrationHistoryEntry,
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    artifact_id = entry.metrics.get("flow_samples_artifact_id")
    if isinstance(artifact_id, str) and artifact_id.strip():
        values.append((artifact_id.strip(), _history_flow_sample_label(entry, None)))
    trials = entry.metrics.get("trials")
    if isinstance(trials, list):
        for trial in trials:
            if not isinstance(trial, dict):
                continue
            trial_artifact_id = trial.get("flow_samples_artifact_id")
            if isinstance(trial_artifact_id, str) and trial_artifact_id.strip():
                values.append(
                    (
                        trial_artifact_id.strip(),
                        _history_flow_sample_label(entry, trial),
                    )
                )
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for artifact_id, label in values:
        if artifact_id in seen:
            continue
        seen.add(artifact_id)
        unique.append((artifact_id, label))
    return tuple(unique)


def _history_flow_sample_label(
    entry: ModbusCalibrationHistoryEntry,
    trial: dict[str, object] | None,
) -> str:
    if trial is None:
        flow_point = entry.metrics.get("flow_point")
        trial_index = entry.metrics.get("trial_index")
    else:
        flow_point = trial.get("flow_point")
        trial_index = trial.get("trial_index")
    values: list[str] = []
    if flow_point not in (None, ""):
        values.append(f"flow {_format_value(flow_point)}")
    if trial_index not in (None, ""):
        values.append(f"trial {_format_value(trial_index)}")
    if not values:
        values.append(_operation_label(entry.operation))
    return " ".join(values)


def _history_detail_text(entry: ModbusCalibrationHistoryEntry) -> str:
    notes = _history_entry_notes(entry)
    lines = [
        "Basic",
        f"Operation: {_operation_label(entry.operation)}",
        f"Status: {entry.status}",
        f"Started: {_format_datetime(entry.started_at)}",
        f"Ended: {_format_datetime(entry.ended_at)}",
        f"Device: {entry.device_id}",
        f"Operator: {entry.operator}",
        f"Run ID: {entry.run_id}",
        f"Operation Note: {notes}",
    ]

    result_lines = _history_result_lines(entry.metrics)
    if result_lines:
        lines.extend(("", "Result", *result_lines))

    metadata_lines = _history_device_metadata_lines(entry.metrics)
    if metadata_lines:
        lines.extend(("", "Device Metadata", *metadata_lines))

    snapshot = entry.metrics.get("pre_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        lines.extend(
            (
                "",
                "Pre-calibration Snapshot",
                *(
                    f"{name}: {_format_history_value_with_unit(name, value, entry.metrics)}"
                    for name, value in snapshot.items()
                ),
            )
        )
    post_snapshot = entry.metrics.get("post_snapshot")
    if isinstance(post_snapshot, dict) and post_snapshot:
        lines.extend(
            (
                "",
                "Post-test Snapshot",
                *(
                    f"{name}: {_format_history_value_with_unit(name, value, entry.metrics)}"
                    for name, value in post_snapshot.items()
                ),
            )
        )

    extra_lines = _history_extra_metric_lines(entry.metrics)
    if extra_lines:
        lines.extend(("", "Other Metrics", *extra_lines))
    return "\n".join(lines)


def _history_entry_notes(entry: ModbusCalibrationHistoryEntry) -> str:
    notes = entry.notes.strip()
    if notes:
        return notes
    metric_notes = entry.metrics.get("notes")
    if isinstance(metric_notes, str) and metric_notes.strip():
        return metric_notes.strip()
    trials = entry.metrics.get("trials")
    if isinstance(trials, list):
        for trial in trials:
            if not isinstance(trial, dict):
                continue
            trial_notes = trial.get("notes")
            if isinstance(trial_notes, str) and trial_notes.strip():
                return trial_notes.strip()
    return ""


def _history_device_metadata_lines(metrics: dict[str, object]) -> list[str]:
    rows: list[str] = []
    for label, key in (
        ("Device Model", "device_model"),
        ("Tube Model", "tube_model"),
        ("Transmitter Model", "transmitter_model"),
    ):
        value = _metric_value(metrics, key)
        if value:
            rows.append(f"{label}: {value}")
    return rows


def _history_result_lines(metrics: dict[str, object]) -> list[str]:
    rows: list[str] = []
    for label, before_key, after_key, change_key in (
        ("zero_offset", "zero_offset_before", "zero_offset_after", "zero_offset_change"),
        ("delta_t", "delta_t_before", "delta_t_after", "delta_t_change"),
    ):
        before = _metric_value(metrics, before_key)
        after = _metric_value(metrics, after_key)
        change = _metric_value(metrics, change_key)
        if before or after or change:
            rows.append(f"{label}: before={before}, after={after}, change={change}")
    for label, key in (
        ("completed", "completed"),
        ("corrected_k_factor", "corrected_k_factor"),
        ("measured_mass_delta", "measured_mass_delta"),
        ("flow_rate_source", "flow_rate_source"),
        ("poll_interval_s", "poll_interval_s"),
        ("sample_count", "sample_count"),
        ("sample_variable_names", "sample_variable_names"),
        ("original_k_factor", "original_k_factor"),
        ("average_error", "average_error"),
        ("new_k_factor", "new_k_factor"),
        ("delta_k_factor", "delta_k_factor"),
        ("selected_trial_count", "selected_trial_count"),
        ("trial_count", "trial_count"),
        ("flow_sample_count", "flow_sample_count"),
        ("flow_samples_artifact_id", "flow_samples_artifact_id"),
        ("trial_sample_variable_names", "trial_sample_variable_names"),
        ("mean_percent_error", "mean_percent_error"),
        ("max_abs_percent_error", "max_abs_percent_error"),
        (
            "max_repeatability_stddev_percent",
            "max_repeatability_stddev_percent",
        ),
    ):
        value = _metric_value(metrics, key)
        if value:
            rows.append(f"{label}: {value}")
    if metrics.get("new_k_factor") is not None and isinstance(
        metrics.get("flow_points"),
        list,
    ):
        rows.append(
            "new_k_formula: intermediate_k = original_k / "
            "(1 + measurement_error_percent / 100); "
            "new_k = (max(intermediate_k_values) + min(intermediate_k_values)) / 2"
        )
    flow_points = metrics.get("flow_points")
    if isinstance(flow_points, list):
        for point in flow_points:
            if not isinstance(point, dict):
                continue
            flow_point = _format_history_value_with_unit(
                "flow_point",
                point.get("flow_point", ""),
                metrics,
            )
            stddev = _format_value(point.get("repeatability_stddev_percent", ""))
            trial_errors = point.get("trial_errors", point.get("trial_errors_percent"))
            values = [
                f"repeatability_stddev_percent={stddev}",
            ]
            for label, key in (
                ("mean_percent_error", "mean_percent_error"),
                ("measurement_error_percent", "measurement_error_percent"),
                ("adjusted_error_percent", "adjusted_error_percent"),
                ("intermediate_k_factor", "intermediate_k_factor"),
            ):
                point_value = _format_history_value_with_unit(
                    key,
                    point.get(key, ""),
                    metrics,
                )
                if point_value:
                    values.append(f"{label}={point_value}")
            rows.append(
                f"flow_point {flow_point}: " + ", ".join(values)
            )
            if isinstance(trial_errors, list):
                rows.append(
                    "  trial_errors_percent="
                    + ", ".join(_format_value(value) for value in trial_errors)
                )
    trials = metrics.get("trials")
    if isinstance(trials, list):
        for trial in trials:
            if not isinstance(trial, dict):
                continue
            flow_point = _format_history_value_with_unit(
                "flow_point",
                trial.get("flow_point", ""),
                metrics,
            )
            trial_index = _format_value(trial.get("trial_index", ""))
            measured_mass_delta = _format_history_value_with_unit(
                "measured_mass_delta",
                trial.get("measured_mass_delta", ""),
                metrics,
            )
            instant_flow = _format_history_value_with_unit(
                "instant_flow",
                trial.get("instant_flow", ""),
                metrics,
            )
            mean_flow = _format_history_value_with_unit(
                "mean_flow",
                trial.get("mean_flow", ""),
                metrics,
            )
            standard_mass = _format_history_value_with_unit(
                "standard_mass",
                trial.get("standard_mass", ""),
                metrics,
            )
            sample_variables = _format_history_value_with_unit(
                "trial_sample_variable_names",
                trial.get("trial_sample_variable_names", ""),
                metrics,
            )
            rows.append(
                "trial "
                f"{flow_point}/{trial_index}: "
                "delta_m="
                f"{measured_mass_delta}, "
                "v1="
                f"{instant_flow}, "
                "v_mean="
                f"{mean_flow}, "
                f"source={_format_value(trial.get('flow_rate_source', ''))}, "
                "standard_mass="
                f"{standard_mass}, "
                f"error={_format_value(trial.get('percent_error', ''))}%, "
                f"flow_samples={_format_value(trial.get('flow_sample_count', ''))}, "
                f"sample_variables={sample_variables}, "
                "flow_samples_artifact="
                f"{_format_value(trial.get('flow_samples_artifact_id', ''))}"
            )
    return rows


def _history_extra_metric_lines(metrics: dict[str, object]) -> list[str]:
    handled = {
        "zero_offset_before",
        "zero_offset_after",
        "zero_offset_change",
        "delta_t_before",
        "delta_t_after",
        "delta_t_change",
        "completed",
        "corrected_k_factor",
        "measured_mass_delta",
        "original_k_factor",
        "average_error",
        "average_error_percent",
        "new_k_factor",
        "delta_k_factor",
        "selected_flow_point_count",
        "selected_trial_count",
        "trial_count",
        "mean_percent_error",
        "max_abs_percent_error",
        "max_repeatability_stddev_percent",
        "flow_points",
        "trials",
        "flow_samples_artifact_id",
        "flow_sample_count",
        "trial_sample_variable_names",
        "pre_snapshot",
        "post_snapshot",
        "register_map_snapshot",
        "device_model",
        "tube_model",
        "transmitter_model",
        "started_at",
        "ended_at",
    }
    rows: list[str] = []
    for name, value in _flatten_metrics(metrics):
        if (
            name in handled
            or name.startswith("pre_snapshot.")
            or name.startswith("post_snapshot.")
            or name.startswith("register_map_snapshot.")
        ):
            continue
        rows.append(f"{name}: {_format_history_value_with_unit(name, value, metrics)}")
    return rows


def _flatten_metrics(
    metrics: dict[str, object],
    prefix: str = "",
) -> tuple[tuple[str, object], ...]:
    rows: list[tuple[str, object]] = []
    for key, value in metrics.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            rows.extend(_flatten_metrics(value, name))
        else:
            rows.append((name, value))
    return tuple(rows)


def _format_port(port: SerialPortInfo) -> str:
    details = [
        value
        for value in (port.description, port.manufacturer, port.serial_number)
        if value
    ]
    if not details:
        return port.port
    return f"{port.port} - {' / '.join(details)}"


def _parse_hex_frame(text: str) -> bytes:
    normalized = (
        text.strip()
        .replace(",", " ")
        .replace(";", " ")
        .replace(":", " ")
        .replace("-", " ")
    )
    if not normalized:
        raise ValueError("enter at least one hex byte.")
    tokens = normalized.split()
    if len(tokens) == 1:
        compact = tokens[0].removeprefix("0x").removeprefix("0X")
        if not compact:
            raise ValueError("enter at least one hex byte.")
        if len(compact) % 2:
            raise ValueError("hex byte count must be even.")
        tokens = [compact[index : index + 2] for index in range(0, len(compact), 2)]
    values: list[int] = []
    for token in tokens:
        value_text = token.removeprefix("0x").removeprefix("0X")
        if not value_text or len(value_text) > 2:
            raise ValueError(f"invalid hex byte: {token}")
        try:
            value = int(value_text, 16)
        except ValueError as exc:
            raise ValueError(f"invalid hex byte: {token}") from exc
        values.append(value)
    return bytes(values)


def _format_hex_bytes(frame: bytes) -> str:
    return " ".join(f"{value:02X}" for value in frame)


def _order_to_modbus_orders(order: str) -> tuple[WordOrder, ByteOrder]:
    normalized = order.upper().strip()
    if normalized == "ABCD":
        return WordOrder.BIG, ByteOrder.BIG
    if normalized == "BADC":
        return WordOrder.BIG, ByteOrder.LITTLE
    if normalized == "CDAB":
        return WordOrder.LITTLE, ByteOrder.BIG
    if normalized == "DCBA":
        return WordOrder.LITTLE, ByteOrder.LITTLE
    raise ValueError(f"Unsupported Modbus order: {order}")


def _orders_to_order(register_map: ModbusRegisterMap) -> str:
    if not register_map.registers:
        return "ABCD"
    register = register_map.registers[0]
    if register.word_order is WordOrder.BIG and register.byte_order is ByteOrder.BIG:
        return "ABCD"
    if register.word_order is WordOrder.BIG and register.byte_order is ByteOrder.LITTLE:
        return "BADC"
    if register.word_order is WordOrder.LITTLE and register.byte_order is ByteOrder.BIG:
        return "CDAB"
    return "DCBA"


def _table_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text().strip()


def _required_table_text(table: QTableWidget, row: int, column: int) -> str:
    value = _table_text(table, row, column)
    if not value:
        raise ValueError(f"Variable map row {row + 1} has an empty required field.")
    return value


def _combo_text(table: QTableWidget, row: int, column: int) -> str:
    widget = table.cellWidget(row, column)
    if isinstance(widget, QComboBox):
        return widget.currentText()
    return _required_table_text(table, row, column)

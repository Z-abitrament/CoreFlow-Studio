"""Standalone Modbus master module window."""

from __future__ import annotations

from pathlib import Path
from datetime import UTC, datetime

from shiboken6 import isValid
from PySide6.QtCore import QMimeData, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QDrag, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMenuBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coreflow.analysis.calibration import RepeatabilityTrial
from coreflow.app.modbus_runtime import (
    ModbusCalibrationHistoryEntry,
    ModbusConnectionSettings,
    ModbusModuleRuntime,
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
        self.resize(460, 340)
        self.setMinimumSize(390, 300)
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


class ZeroCalibrationDialog(QDialog):
    """Operator-facing zero calibration dialog."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Zero Calibration")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(720, 560)
        self.setMinimumSize(560, 420)
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
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("modbusZeroCalibrationCloseButton")
        self.closeButton.clicked.connect(self.close)
        buttons.addWidget(self.startButton)
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

    def set_running(self) -> None:
        self.startButton.setEnabled(False)
        self.snapshotTable.setEnabled(False)
        self.statusLabel.setText("Running...")

    def set_ready(self, *, connected: bool) -> None:
        self.startButton.setEnabled(connected)
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

    def _set_table_text(self, row: int, column: int, value: str) -> None:
        item = QTableWidgetItem(value)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.resultTable.setItem(row, column, item)


class CalibrationHistoryDialog(QDialog):
    """Historical calibration run table with editable notes."""

    TIME_COLUMN = 0
    OPERATION_COLUMN = 1
    RUN_ID_COLUMN = 2
    PARAMETER_COLUMN = 3
    NOTES_COLUMN = 4

    OPERATIONS = (
        ("All", "all"),
        ("Zero Calibration", "zero_calibration"),
        ("K Factor", "k_factor_calibration"),
        ("Repeatability", "manual_error_repeatability"),
    )

    def __init__(
        self,
        runtime: ModbusModuleRuntime,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._loading = False
        self._entries: tuple[ModbusCalibrationHistoryEntry, ...] = ()
        self.setWindowTitle("Calibration History")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(1040, 640)
        self.setMinimumSize(760, 420)
        self._build_ui()
        self._connect_signals()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        filters = QHBoxLayout()
        filters.addWidget(QLabel("Operation"))
        self.operationCombo = QComboBox()
        self.operationCombo.setObjectName("modbusHistoryOperationCombo")
        for label, value in self.OPERATIONS:
            self.operationCombo.addItem(label, value)
        self.refreshButton = QPushButton("Refresh")
        self.refreshButton.setObjectName("modbusHistoryRefreshButton")
        filters.addWidget(self.operationCombo)
        filters.addStretch(1)
        filters.addWidget(self.refreshButton)
        root.addLayout(filters)

        self.historyTable = QTableWidget(0, 5)
        self.historyTable.setObjectName("modbusCalibrationHistoryTable")
        self.historyTable.setHorizontalHeaderLabels(
            [
                "Time",
                "Operation",
                "Run ID",
                "Parameter",
                "Notes",
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
        self.refreshButton.clicked.connect(self.refresh)
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
        self._entries = self.runtime.list_calibration_history(operation=operation)
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

    def _populate_row(self, row: int, entry: ModbusCalibrationHistoryEntry) -> None:
        values = (
            _format_datetime(entry.started_at),
            _operation_label(entry.operation),
            entry.run_id,
            _history_parameter_summary(entry),
            entry.notes,
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
            return
        row = min(index.row() for index in indexes)
        if 0 <= row < len(self._entries):
            self._populate_detail(self._entries[row])

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


class ModbusModuleWindow(QDialog):
    """Independent Modbus master UI with its own connection state."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        runtime: ModbusModuleRuntime | None = None,
        port_scanner: SerialPortScanner | None = None,
        thread_pool: QThreadPool | None = None,
        data_root: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.runtime = runtime or ModbusModuleRuntime(repository)
        self._data_root = Path(data_root) if data_root is not None else None
        self._port_scanner = port_scanner or SerialPortScanner()
        if thread_pool is None:
            self._thread_pool = QThreadPool(self)
            self._thread_pool.setMaxThreadCount(1)
        else:
            self._thread_pool = thread_pool
        self.runtime.set_frame_logger(self._record_modbus_frame)
        self._active_tasks: list[WorkflowTask] = []
        self._busy = False
        self._closing = False
        self._polling = False
        self._last_order = "ABCD"
        self._pending_map_load_error: str | None = None
        self._zero_snapshot_variable_names: tuple[str, ...] | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll_selected_variables)
        self.connectionDialog: ModbusConnectionDialog | None = None
        self.zeroCalibrationDialog: ZeroCalibrationDialog | None = None
        self.calibrationHistoryDialog: CalibrationHistoryDialog | None = None
        self.setWindowTitle("Modbus Module")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 520)
        self._load_saved_register_map()
        self._build_ui()
        self._connect_signals()
        self._sync_status()
        self._set_connected_controls(False)
        self._log("Ready. This module connection is independent from simulator channels.")
        if self._pending_map_load_error:
            self._log(f"Saved variable map ignored: {self._pending_map_load_error}")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.menuBar = QMenuBar()
        self.menuBar.setObjectName("modbusMenuBar")
        operations_menu = self.menuBar.addMenu("Operations")
        self.sampleVariablesAction = QAction("Sample Variables", self)
        self.sampleVariablesAction.setObjectName("modbusSampleVariablesAction")
        self.zeroCalibrationAction = QAction("Zero Cal", self)
        self.zeroCalibrationAction.setObjectName("modbusZeroCalibrationAction")
        self.kFactorAction = QAction("K Factor", self)
        self.kFactorAction.setObjectName("modbusKFactorAction")
        self.repeatabilityAction = QAction("Repeatability", self)
        self.repeatabilityAction.setObjectName("modbusRepeatabilityAction")
        self.calibrationHistoryAction = QAction("Calibration History", self)
        self.calibrationHistoryAction.setObjectName("modbusCalibrationHistoryAction")
        for action in (
            self.sampleVariablesAction,
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            operations_menu.addAction(action)
        operations_menu.addSeparator()
        operations_menu.addAction(self.calibrationHistoryAction)
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

        mapping = QGroupBox("Variable Map")
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
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.variableMapTable.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        self.variableMapTable.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.variableMapTable.setAlternatingRowColors(True)
        self.variableMapTable.setColumnWidth(0, 150)
        self.variableMapTable.setColumnWidth(1, 120)
        self.variableMapTable.setColumnWidth(2, 80)
        self.variableMapTable.setColumnWidth(3, 70)
        self.variableMapTable.setColumnWidth(4, 110)
        self.variableMapTable.setColumnWidth(5, 80)
        self.variableMapTable.setColumnWidth(6, 80)
        self.variableMapTable.setColumnWidth(7, 70)
        self.variableMapTable.setColumnWidth(8, 55)
        self.variableMapTable.setColumnWidth(9, 150)
        self.variableMapTable.setColumnWidth(10, 110)
        self.variableMapTable.setColumnWidth(11, 150)
        self.variableMapTable.setMinimumHeight(120)
        self.variableMapTable.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        mapping_layout.addWidget(self.variableMapTable)
        mapping_actions = QHBoxLayout()
        self.addVariableButton = QPushButton("Add Variable")
        self.addVariableButton.setObjectName("modbusAddVariableButton")
        self.deleteVariableButton = QPushButton("Delete Variable")
        self.deleteVariableButton.setObjectName("modbusDeleteVariableButton")
        self.resetVariableMapButton = QPushButton("Reset Map")
        self.resetVariableMapButton.setObjectName("modbusResetVariableMapButton")
        self.saveVariableMapButton = QPushButton("Save Map")
        self.saveVariableMapButton.setObjectName("modbusSaveVariableMapButton")
        self.pollingButton = QPushButton("Start Polling")
        self.pollingButton.setObjectName("modbusPollingButton")
        mapping_actions.addWidget(self.addVariableButton)
        mapping_actions.addWidget(self.deleteVariableButton)
        mapping_actions.addWidget(self.resetVariableMapButton)
        mapping_actions.addWidget(self.saveVariableMapButton)
        mapping_actions.addStretch(1)
        mapping_actions.addWidget(self.pollingButton)
        mapping_layout.addLayout(mapping_actions)
        body_splitter = QSplitter(Qt.Orientation.Vertical)
        body_splitter.setObjectName("modbusBodySplitter")
        body_splitter.addWidget(mapping)
        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout(bottom_panel)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(10)
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

        self.frameTable = QTableWidget(0, 4)
        self.frameTable.setObjectName("modbusFrameTable")
        self.frameTable.setHorizontalHeaderLabels(["Time", "Direction", "Operation", "Data"])
        self.frameTable.verticalHeader().setVisible(False)
        self.frameTable.setAlternatingRowColors(True)
        self.frameTable.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.frameTable.setColumnWidth(0, 80)
        self.frameTable.setColumnWidth(1, 70)
        self.frameTable.setColumnWidth(2, 120)
        self.frameTable.setColumnWidth(3, 720)
        self.frameTable.setMinimumHeight(150)
        bottom_layout.addWidget(self.frameTable, 1)

        self.logTextEdit = QTextEdit()
        self.logTextEdit.setObjectName("modbusLogTextEdit")
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMinimumHeight(80)
        bottom_layout.addWidget(self.logTextEdit, 1)
        body_splitter.addWidget(bottom_panel)
        body_splitter.setStretchFactor(0, 3)
        body_splitter.setStretchFactor(1, 2)
        body_splitter.setSizes([420, 300])
        root.addWidget(body_splitter, 1)

    def _connect_signals(self) -> None:
        self.openConnectionButton.clicked.connect(self._open_connection_dialog)
        self.addVariableButton.clicked.connect(self._add_variable_row)
        self.deleteVariableButton.clicked.connect(self._delete_selected_variable_row)
        self.resetVariableMapButton.clicked.connect(self._populate_variable_map)
        self.saveVariableMapButton.clicked.connect(self._save_variable_map)
        self.pollingButton.clicked.connect(self._toggle_polling)
        self.disconnectButton.clicked.connect(self._disconnect)
        self.sampleVariablesAction.triggered.connect(self._sample_variables)
        self.zeroCalibrationAction.triggered.connect(self._zero_calibration)
        self.kFactorAction.triggered.connect(self._k_factor)
        self.repeatabilityAction.triggered.connect(self._repeatability)
        self.calibrationHistoryAction.triggered.connect(self._open_calibration_history)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._closing = True
        self._queue_shutdown_disconnect()
        super().closeEvent(event)

    def _queue_shutdown_disconnect(self) -> None:
        task = WorkflowTask(self.runtime.disconnect)
        self._active_tasks.append(task)
        self._thread_pool.start(task)

    def refresh_ports(self) -> None:
        self._run_task(
            "Refresh ports",
            self._port_scanner.list_ports,
            self._connection_ports_finished,
            requires_connection=False,
        )

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
        self._log(f"Variable map saved: {path}")

    def _saved_register_map_path(self) -> Path | None:
        if self._data_root is None:
            return None
        return self._data_root / "config" / "register_maps" / "modbus_module_map.json"

    def _open_connection_dialog(self) -> None:
        if self.connectionDialog is None:
            self.connectionDialog = ModbusConnectionDialog(parent=self)
            self.connectionDialog.refreshPortsButton.clicked.connect(self.refresh_ports)
            self.connectionDialog.connectButton.clicked.connect(self._connect_from_dialog)
        self.connectionDialog.orderCombo.setCurrentText(self._last_order)
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
            self.runtime.configure_register_map(
                self._register_map_from_ui(order=settings.order)
            )
        except Exception as exc:
            self._log(f"Connect failed: {exc}")
            return
        self._run_task(
            "Connect",
            lambda: self.runtime.connect(settings),
            self._connect_finished,
            requires_connection=False,
        )

    def _disconnect(self) -> None:
        self._stop_polling()
        status = self.runtime.disconnect()
        self.statusValueLabel.setText(status.message)
        if self.connectionDialog is not None and isValid(self.connectionDialog):
            self.connectionDialog.set_status(status.message)
        self._set_controls_enabled(True)
        self._log("Disconnected")

    def _sample_variables(self) -> None:
        self._run_task(
            "Sample",
            lambda: self.runtime.sample_variables(_sample_variable_names()),
            self._sample_finished,
            requires_connection=True,
        )

    def _zero_calibration(self) -> None:
        dialog = self._ensure_zero_calibration_dialog()
        self._refresh_zero_calibration_snapshot_variables(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.set_ready(connected=self.runtime.status.connected)

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
        dialog.set_running()
        self._run_task(
            "Zero calibration",
            lambda: self.runtime.run_zero_calibration(
                snapshot_variable_names=snapshot_names,
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

    def _open_calibration_history(self) -> None:
        if (
            self.calibrationHistoryDialog is None
            or not isValid(self.calibrationHistoryDialog)
        ):
            self.calibrationHistoryDialog = CalibrationHistoryDialog(
                self.runtime,
                parent=self,
            )
        else:
            self.calibrationHistoryDialog.refresh()
        self.calibrationHistoryDialog.show()
        self.calibrationHistoryDialog.raise_()
        self.calibrationHistoryDialog.activateWindow()

    def _k_factor(self) -> None:
        mass_acc_before = self.massAccBeforeSpinBox.value()
        mass_acc_after = self.massAccAfterSpinBox.value()
        standard_mass = self.standardMassSpinBox.value()
        current_k_factor = self.currentKFactorSpinBox.value()
        self._run_task(
            "K factor",
            lambda: self.runtime.run_k_factor_calibration(
                mass_acc_before=mass_acc_before,
                mass_acc_after=mass_acc_after,
                standard_mass=standard_mass,
                current_k_factor=current_k_factor,
            ),
            lambda run_id: self._log(f"K factor completed {run_id}"),
            requires_connection=True,
        )

    def _repeatability(self) -> None:
        self._run_task(
            "Repeatability",
            lambda: self.runtime.run_repeatability_test(_default_trials()),
            lambda run_id: self._log(f"Repeatability completed {run_id}"),
            requires_connection=True,
        )

    def _sync_status(self) -> None:
        self.statusValueLabel.setText(self.runtime.status.message)

    def _connect_finished(self, status: object) -> None:
        message = getattr(status, "message", str(status))
        self.statusValueLabel.setText(message)
        self._set_controls_enabled(True)
        if self.connectionDialog is not None:
            self.connectionDialog.set_status(message)
        self._log(message)

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
        if (
            self.calibrationHistoryDialog is not None
            and isValid(self.calibrationHistoryDialog)
        ):
            self.calibrationHistoryDialog.refresh()
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

    def _run_task(
        self,
        label: str,
        action,
        on_finished,
        *,
        requires_connection: bool,
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
        task = WorkflowTask(action)
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
        register_map = self.runtime.register_map
        registers = list(register_map.registers)
        if any(_is_ui_register(register) for register in registers):
            return registers
        by_name = {register.name: register for register in registers}
        return [
            by_name[name]
            for name in _editable_register_names()
            if name in by_name
        ]

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
        self._sync_status()
        self._set_controls_enabled(True)
        if (
            label == "Zero calibration"
            and self.zeroCalibrationDialog is not None
            and isValid(self.zeroCalibrationDialog)
        ):
            self.zeroCalibrationDialog.set_error(message)
        self._log(f"{label} failed: {message}")

    def _can_update_ui(self) -> bool:
        return (
            not self._closing
            and isValid(self)
            and isValid(self.logTextEdit)
            and isValid(self.frameTable)
        )

    def _set_connected_controls(self, connected: bool) -> None:
        self.openConnectionButton.setEnabled(True)
        self.disconnectButton.setEnabled(connected)
        for action in (
            self.sampleVariablesAction,
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            action.setEnabled(connected)
        self.calibrationHistoryAction.setEnabled(True)

    def _set_controls_enabled(self, enabled: bool) -> None:
        enabled = enabled and not self._busy
        connected = self.runtime.status.connected
        self.openConnectionButton.setEnabled(enabled)
        self.disconnectButton.setEnabled(enabled and connected)
        self.variableMapTable.setEnabled(True)
        self.addVariableButton.setEnabled(enabled and not connected)
        self.deleteVariableButton.setEnabled(
            enabled and not connected and self.variableMapTable.rowCount() > 1
        )
        self.resetVariableMapButton.setEnabled(enabled and not connected)
        self.saveVariableMapButton.setEnabled(enabled and not connected)
        self.pollingButton.setEnabled(enabled and connected)
        for widget in (
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
            self.zeroCalibrationAction,
            self.kFactorAction,
            self.repeatabilityAction,
        ):
            action.setEnabled(enabled and connected)
        self.calibrationHistoryAction.setEnabled(enabled)
        if (
            self.zeroCalibrationDialog is not None
            and isValid(self.zeroCalibrationDialog)
            and self.zeroCalibrationDialog.statusLabel.text() != "Running..."
        ):
            self.zeroCalibrationDialog.set_ready(connected=connected and enabled)
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
        if not self._can_update_ui():
            return
        row = self.frameTable.rowCount()
        self.frameTable.insertRow(row)
        values = (
            datetime.now().strftime("%H:%M:%S"),
            direction,
            operation,
            data,
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.frameTable.setItem(row, column, item)
        self.frameTable.scrollToBottom()

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logTextEdit.append(f"{stamp} {message}")


def _float_input(value: float) -> QDoubleSpinBox:
    widget = QDoubleSpinBox()
    widget.setRange(-1_000_000.0, 1_000_000.0)
    widget.setDecimals(6)
    widget.setValue(value)
    return widget


def _default_trials() -> tuple[RepeatabilityTrial, ...]:
    return (
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


def _is_ui_register(register: ModbusRegister) -> bool:
    source = register.metadata.get("source")
    return source in {"modbus_module_ui", "modbus_module_ui_custom"}


def _is_custom_ui_register(register: ModbusRegister) -> bool:
    return register.metadata.get("source") == "modbus_module_ui_custom"


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


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _format_history_value(name: str, value: object) -> str:
    if name.endswith("_at") and isinstance(value, str):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return _format_datetime(parsed)
    return _format_value(value)


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _operation_label(value: str) -> str:
    labels = {
        "zero_calibration": "Zero Calibration",
        "k_factor_calibration": "K Factor",
        "manual_error_repeatability": "Repeatability",
    }
    return labels.get(value, value)


def _metric_value(metrics: dict[str, object], key: str) -> str:
    if key not in metrics:
        return ""
    return _format_value(metrics[key])


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
        return f"k_factor={k_factor}" if k_factor else ""
    if entry.operation == "manual_error_repeatability":
        trial_count = _metric_value(metrics, "trial_count")
        return f"trial_count={trial_count}" if trial_count else ""
    return ""


def _history_detail_text(entry: ModbusCalibrationHistoryEntry) -> str:
    lines = [
        "Basic",
        f"Operation: {_operation_label(entry.operation)}",
        f"Status: {entry.status}",
        f"Started: {_format_datetime(entry.started_at)}",
        f"Ended: {_format_datetime(entry.ended_at)}",
        f"Device: {entry.device_id}",
        f"Operator: {entry.operator}",
        f"Run ID: {entry.run_id}",
        f"Notes: {entry.notes}",
    ]

    result_lines = _history_result_lines(entry.metrics)
    if result_lines:
        lines.extend(("", "Result", *result_lines))

    snapshot = entry.metrics.get("pre_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        lines.extend(
            (
                "",
                "Pre-calibration Snapshot",
                *(
                    f"{name}: {_format_value(value)}"
                    for name, value in snapshot.items()
                ),
            )
        )

    extra_lines = _history_extra_metric_lines(entry.metrics)
    if extra_lines:
        lines.extend(("", "Other Metrics", *extra_lines))
    return "\n".join(lines)


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
        ("trial_count", "trial_count"),
    ):
        value = _metric_value(metrics, key)
        if value:
            rows.append(f"{label}: {value}")
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
        "trial_count",
        "pre_snapshot",
    }
    rows: list[str] = []
    for name, value in _flatten_metrics(metrics):
        if name in handled or name.startswith("pre_snapshot."):
            continue
        rows.append(f"{name}: {_format_history_value(name, value)}")
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

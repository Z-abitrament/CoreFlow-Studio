"""Current-device history view for filling trials and analyses."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coreflow.app import FillingHistoryEntry, FillingTrialService


_RECORD_TYPE_LABELS = {
    "trial": "Filling Trial",
    "repeatability": "Filling Repeatability",
    "advance_calculation": "Filling Advance Calculation",
    "advance_profile": "Filling Advance Profile Set",
}


class FillingHistoryDialog(QDialog):
    """Display filling records locked to one selected Device ID."""

    def __init__(
        self,
        service: FillingTrialService,
        device_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.device_id = device_id
        self._entries_by_id: dict[str, FillingHistoryEntry] = {}
        self.setObjectName("fillingHistoryDialog")
        self.setWindowTitle("Filling History")
        self.setModal(False)
        self.resize(880, 560)
        self.setMinimumSize(720, 460)
        self._build_ui()
        self.refresh_records()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(QLabel("Device ID"))
        self.deviceValueLabel = QLabel(self.device_id)
        self.deviceValueLabel.setObjectName("fillingHistoryDeviceValueLabel")
        self.deviceValueLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        header.addWidget(self.deviceValueLabel)
        header.addStretch(1)
        self.refreshButton = QPushButton("Refresh")
        self.refreshButton.setObjectName("fillingHistoryRefreshButton")
        header.addWidget(self.refreshButton)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setObjectName("fillingHistorySplitter")
        self.recordTable = QTableWidget(0, 9)
        self.recordTable.setObjectName("fillingHistoryRecordTable")
        self.recordTable.setHorizontalHeaderLabels(
            [
                "Type",
                "Time",
                "Flow Point",
                "Specified",
                "Target / Corrected",
                "Control / Valve Label",
                "Notes",
                "Summary",
                "Record ID",
            ]
        )
        self.recordTable.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.recordTable.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.recordTable.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.recordTable.verticalHeader().setVisible(False)
        self.recordTable.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        header_view = self.recordTable.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setSectionsMovable(True)
        header_view.setStretchLastSection(False)
        for column, width in enumerate(
            (180, 150, 110, 110, 150, 190, 190, 260, 220)
        ):
            self.recordTable.setColumnWidth(column, width)
        splitter.addWidget(self.recordTable)

        self.detailTextEdit = QTextEdit()
        self.detailTextEdit.setObjectName("fillingHistoryDetailTextEdit")
        self.detailTextEdit.setReadOnly(True)
        splitter.addWidget(self.detailTextEdit)
        splitter.setSizes([300, 220])
        root.addWidget(splitter, 1)

        footer = QHBoxLayout()
        self.statusLabel = QLabel()
        self.statusLabel.setObjectName("fillingHistoryStatusLabel")
        self.statusLabel.setWordWrap(True)
        footer.addWidget(self.statusLabel, 1)
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("fillingHistoryCloseButton")
        footer.addWidget(self.closeButton)
        root.addLayout(footer)

        self.refreshButton.clicked.connect(self.refresh_records)
        self.closeButton.clicked.connect(self.close)
        self.recordTable.itemSelectionChanged.connect(self._show_selection)

    def refresh_records(self) -> None:
        """Refresh history without leaking query failures to the workbench."""

        self.recordTable.setRowCount(0)
        self.detailTextEdit.clear()
        self._entries_by_id.clear()
        try:
            entries = self.service.list_history(device_id=self.device_id)
        except Exception as exc:
            self.statusLabel.setText(f"History query failed: {exc}")
            return

        for entry in entries:
            if entry.device_id != self.device_id:
                continue
            self._entries_by_id[entry.record_id] = entry
            row = self.recordTable.rowCount()
            self.recordTable.insertRow(row)
            type_item = QTableWidgetItem(
                _RECORD_TYPE_LABELS.get(entry.record_type, entry.record_type)
            )
            type_item.setData(Qt.ItemDataRole.UserRole, entry.record_id)
            type_item.setData(Qt.ItemDataRole.UserRole + 1, entry.record_type)
            time_item = QTableWidgetItem(
                entry.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if entry.created_at is not None
                else ""
            )
            flow_point, specified, target, label, notes = _condition_values(
                entry
            )
            flow_item = QTableWidgetItem(flow_point)
            specified_item = QTableWidgetItem(specified)
            target_item = QTableWidgetItem(target)
            label_item = QTableWidgetItem(label)
            notes_item = QTableWidgetItem(notes)
            summary_item = QTableWidgetItem(entry.summary)
            id_item = QTableWidgetItem(entry.record_id)
            id_item.setData(Qt.ItemDataRole.UserRole, entry.record_id)
            for column, item in enumerate(
                (
                    type_item,
                    time_item,
                    flow_item,
                    specified_item,
                    target_item,
                    label_item,
                    notes_item,
                    summary_item,
                    id_item,
                )
            ):
                if item.text():
                    item.setToolTip(item.text())
                self.recordTable.setItem(row, column, item)

        self.statusLabel.setText(f"{len(self._entries_by_id)} records")
        if self.recordTable.rowCount():
            self.recordTable.selectRow(0)

    def refresh(self) -> None:
        """Public compatibility alias for callers refreshing an open dialog."""

        self.refresh_records()

    def _show_selection(self) -> None:
        row = self.recordTable.currentRow()
        if row < 0:
            self.detailTextEdit.clear()
            return
        id_item = self.recordTable.item(row, 8)
        if id_item is None:
            self.detailTextEdit.clear()
            return
        record_id = id_item.data(Qt.ItemDataRole.UserRole)
        entry = self._entries_by_id.get(str(record_id))
        if entry is None:
            self.detailTextEdit.clear()
            return
        payload = {
            "record_id": entry.record_id,
            "record_type": entry.record_type,
            "run_id": entry.run_id,
            "device_id": entry.device_id,
            "created_at": (
                entry.created_at.isoformat()
                if entry.created_at is not None
                else None
            ),
            "summary": entry.summary,
            "details": entry.details,
        }
        self.detailTextEdit.setPlainText(
            json.dumps(payload, indent=2, sort_keys=True, default=str)
        )


def _condition_values(
    entry: FillingHistoryEntry,
) -> tuple[str, str, str, str, str]:
    details = entry.details
    metrics = _mapping(details.get("metrics"))
    configuration = _mapping(details.get("configuration_snapshot"))
    if not configuration:
        configuration = _mapping(metrics.get("configuration_snapshot"))
    sources = (details, metrics, configuration)
    mass_unit = _text_value(_first_value(("mass_unit",), sources))

    flow = _measurement(
        _first_value(("flow_point_g_per_s",), sources),
        "g/s",
    )
    specified = _measurement(
        _first_value(("specified_mass",), sources),
        mass_unit,
    )
    target_keys = (
        ("corrected_target_mass", "target_mass")
        if entry.record_type in {"advance_calculation", "advance_profile"}
        else ("target_mass", "corrected_target_mass")
    )
    target = _measurement(_first_value(target_keys, sources), mass_unit)
    label = _text_value(
        _first_value(("control_valve_label",), sources)
    )
    notes = _text_value(
        _first_value(("notes", "run_notes"), (details,))
    )
    return flow, specified, target, label, notes


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _first_value(
    keys: tuple[str, ...],
    sources: tuple[dict[str, object], ...],
) -> object | None:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None and value != "":
                return value
    return None


def _measurement(value: object | None, unit: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, (int, float)):
        text = f"{float(value):.15g}"
    else:
        text = str(value)
    return f"{text} {unit}" if unit else text


def _text_value(value: object | None) -> str:
    return "" if value is None else str(value)


__all__ = ["FillingHistoryDialog"]

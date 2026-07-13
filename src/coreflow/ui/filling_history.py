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
        self.recordTable = QTableWidget(0, 4)
        self.recordTable.setObjectName("fillingHistoryRecordTable")
        self.recordTable.setHorizontalHeaderLabels(
            ["Type", "Time", "Summary", "ID"]
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
        header_view = self.recordTable.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
            entries = self.service.list_history()
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
            summary_item = QTableWidgetItem(entry.summary)
            id_item = QTableWidgetItem(entry.record_id)
            id_item.setData(Qt.ItemDataRole.UserRole, entry.record_id)
            for column, item in enumerate(
                (type_item, time_item, summary_item, id_item)
            ):
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
        id_item = self.recordTable.item(row, 3)
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


__all__ = ["FillingHistoryDialog"]

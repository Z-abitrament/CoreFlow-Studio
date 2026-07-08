"""Cross-module Device ID history dialog."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from coreflow.storage import DeviceHistoryRecord, StorageRepository


class DeviceHistoryDialog(QDialog):
    """Device-centered history across Modbus, Pulse, and future modules."""

    def __init__(
        self,
        repository: StorageRepository,
        *,
        device_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.setWindowTitle("Device History")
        self.resize(920, 520)
        self._build_ui()
        if device_id:
            self.deviceIdLineEdit.setText(device_id)
            self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        controls = QHBoxLayout()
        self.deviceIdLineEdit = QLineEdit()
        self.deviceIdLineEdit.setObjectName("deviceHistoryDeviceIdLineEdit")
        self.moduleFilterCombo = QComboBox()
        self.moduleFilterCombo.setObjectName("deviceHistoryModuleFilterCombo")
        self.moduleFilterCombo.addItems(["All", "Modbus", "Pulse"])
        self.refreshButton = QPushButton("Refresh")
        self.refreshButton.setObjectName("deviceHistoryRefreshButton")
        controls.addWidget(QLabel("Device ID"))
        controls.addWidget(self.deviceIdLineEdit, 1)
        controls.addWidget(QLabel("Module"))
        controls.addWidget(self.moduleFilterCombo)
        controls.addWidget(self.refreshButton)
        root.addLayout(controls)

        self.historyTable = QTableWidget(0, 6)
        self.historyTable.setObjectName("deviceHistoryTable")
        self.historyTable.setHorizontalHeaderLabels(
            ["Started", "Module", "Operation", "Status", "Device ID", "Summary"]
        )
        self.historyTable.horizontalHeader().setStretchLastSection(True)
        self.historyTable.verticalHeader().setVisible(False)
        self.historyTable.setSortingEnabled(False)
        root.addWidget(self.historyTable, 1)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("deviceHistoryStatusLabel")
        root.addWidget(self.statusLabel)

        self.refreshButton.clicked.connect(self.refresh)

    def refresh(self) -> None:
        device_id = self.deviceIdLineEdit.text().strip()
        if not device_id:
            self.historyTable.setRowCount(0)
            self.statusLabel.setText("Enter a Device ID.")
            return
        records = self.repository.list_device_history_records(
            device_id=device_id,
            module=self.moduleFilterCombo.currentText(),
        )
        self._populate(records)
        self.statusLabel.setText(f"{len(records)} record(s).")

    def _populate(self, records: tuple[DeviceHistoryRecord, ...]) -> None:
        self.historyTable.setRowCount(len(records))
        for row, record in enumerate(records):
            values = (
                _format_dt(record.started_at),
                record.module,
                record.operation_type,
                record.status,
                record.device_id,
                _summary(record.summary),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
                self.historyTable.setItem(row, column, item)
        self.historyTable.resizeColumnsToContents()


def _format_dt(value: datetime | None) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S") if value else ""


def _summary(summary: dict[str, Any]) -> str:
    keys = (
        "percent_error",
        "mean_percent_error",
        "repeatability_stddev_percent",
        "measured_quantity",
        "standard_quantity",
        "pulse_count",
        "new_k",
        "delta_k",
    )
    parts = [f"{key}={summary[key]}" for key in keys if key in summary]
    if parts:
        return ", ".join(parts)
    return ", ".join(f"{key}={value}" for key, value in sorted(summary.items())[:4])

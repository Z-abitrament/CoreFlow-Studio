from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt

from coreflow.app import CoreFlowRuntime
from coreflow.storage.models import (
    ModbusOperationAttemptRecord,
    PulseOperationAttemptRecord,
)
from coreflow.ui.device_history import DeviceHistoryDialog


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _table_text(table, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text()


def test_device_history_dialog_shows_modbus_and_pulse_rows(qtbot, tmp_path) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path)
    runtime.repository.save_modbus_operation_attempt(
        ModbusOperationAttemptRecord(
            attempt_id="MODBUS-HIST-001",
            device_id="CFM-HISTORY-UI",
            operation_type="manual_error_repeatability",
            status="calculated",
            operator="pytest",
            started_at=datetime(2026, 7, 7, 9, 0, tzinfo=UTC),
            summary={"mean_percent_error": 0.2},
        )
    )
    runtime.repository.save_pulse_operation_attempt(
        PulseOperationAttemptRecord(
            attempt_id="PULSE-HIST-001",
            device_id="CFM-HISTORY-UI",
            operation_type="pulse_csv_trial",
            status="calculated",
            operator="pytest",
            started_at=datetime(2026, 7, 7, 9, 5, tzinfo=UTC),
            summary={"percent_error": -0.1, "measured_quantity": 99.9},
        )
    )
    dialog = DeviceHistoryDialog(runtime.repository, parent=None)
    qtbot.addWidget(dialog)
    dialog.show()

    dialog.deviceIdLineEdit.setText("CFM-HISTORY-UI")
    _click(qtbot, dialog.refreshButton)

    assert dialog.historyTable.rowCount() == 2
    assert _table_text(dialog.historyTable, 0, 1) == "Pulse"
    assert _table_text(dialog.historyTable, 1, 1) == "Modbus"

    dialog.moduleFilterCombo.setCurrentText("Pulse")
    _click(qtbot, dialog.refreshButton)

    assert dialog.historyTable.rowCount() == 1
    assert _table_text(dialog.historyTable, 0, 1) == "Pulse"
    assert "percent_error=-0.1" in _table_text(dialog.historyTable, 0, 5)

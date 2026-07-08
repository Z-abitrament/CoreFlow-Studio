from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from coreflow.app import CoreFlowRuntime
from coreflow.storage import PulseTrialRecord
from coreflow.ui import pulse_counter_window
from coreflow.ui import MainWindow


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _table_text(table, row: int, column: int) -> str:
    item = table.item(row, column)
    return "" if item is None else item.text()


def test_pulse_module_imports_csv_calculates_trial_and_saves_history(
    qtbot,
    tmp_path,
) -> None:
    csv_path = _write_dsview_csv(tmp_path)
    runtime = CoreFlowRuntime(data_root=tmp_path / "data")
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.pulseModuleAction.trigger()

    assert window.pulseWindow is not None
    pulse_window = window.pulseWindow
    assert window.moduleStack.currentWidget() is pulse_window
    assert pulse_window.deviceIdLineEdit.text() == ""
    assert pulse_window.channelLineEdit.text() == "0"
    assert pulse_window.edgeCombo.currentText() == "rising"
    assert pulse_window.pulseValueSpinBox.value() == 0.05
    assert pulse_window.switchFrequencySpinBox.value() == 100.0
    assert runtime.list_channels() == ()

    pulse_window.deviceIdLineEdit.setText("CFM-PULSE-UI")
    pulse_window.channelLineEdit.setText("0")
    pulse_window.pulseValueSpinBox.setValue(0.05)
    pulse_window.switchFrequencySpinBox.setValue(100.0)
    _click(qtbot, pulse_window.saveProfileButton)

    pulse_window.csvPathLineEdit.setText(str(csv_path))
    _click(qtbot, pulse_window.analyzeCsvButton)

    assert "Pulses: 3" in pulse_window.summaryLabel.text()
    assert "Quantity: 0.15 g" in pulse_window.summaryLabel.text()
    assert pulse_window.ratePlot.listDataItems()
    assert pulse_window.standardMassSpinBox.isEnabled()

    pulse_window.flowPointSpinBox.setValue(100.0)
    pulse_window.trialIndexSpinBox.setValue(1)
    pulse_window.standardMassSpinBox.setValue(0.14)
    _click(qtbot, pulse_window.calculateTrialButton)

    assert pulse_window.historyTable.rowCount() == 1
    assert _table_text(pulse_window.historyTable, 0, 1) == "pulse_csv_trial"
    assert "7.142857" in _table_text(pulse_window.historyTable, 0, 4)
    assert runtime.repository.count_rows("pulse_operation_attempts") == 1
    assert runtime.repository.count_rows("pulse_trial_records") == 1

    second = MainWindow(runtime=runtime)
    qtbot.addWidget(second)
    second.show()
    second.pulseModuleAction.trigger()
    assert second.pulseWindow is not None
    second.pulseWindow.deviceIdLineEdit.setText("CFM-PULSE-UI")
    _click(qtbot, second.pulseWindow.loadProfileButton)
    assert second.pulseWindow.pulseValueSpinBox.value() == 0.05
    assert second.pulseWindow.historyTable.rowCount() == 1


def test_pulse_module_reports_missing_device_id_for_csv_analysis(
    qtbot,
    tmp_path,
) -> None:
    csv_path = _write_dsview_csv(tmp_path)
    runtime = CoreFlowRuntime(data_root=tmp_path / "data")
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.pulseModuleAction.trigger()
    assert window.pulseWindow is not None
    pulse_window = window.pulseWindow
    pulse_window.csvPathLineEdit.setText(str(csv_path))

    _click(qtbot, pulse_window.analyzeCsvButton)

    assert "Device ID is required." in pulse_window.summaryLabel.text()
    assert not pulse_window.standardMassSpinBox.isEnabled()
    assert not pulse_window.calculateTrialButton.isEnabled()


def test_pulse_module_accepts_quoted_csv_path(
    qtbot,
    tmp_path,
) -> None:
    csv_path = _write_dsview_csv(tmp_path)
    runtime = CoreFlowRuntime(data_root=tmp_path / "data")
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()

    window.pulseModuleAction.trigger()
    assert window.pulseWindow is not None
    pulse_window = window.pulseWindow
    pulse_window.deviceIdLineEdit.setText("CFM-PULSE-QUOTED")
    pulse_window.csvPathLineEdit.setText(f'"{csv_path}"')

    _click(qtbot, pulse_window.analyzeCsvButton)

    assert "Pulses: 3" in pulse_window.summaryLabel.text()
    assert "Quantity: 0.15 g" in pulse_window.summaryLabel.text()
    assert pulse_window.standardMassSpinBox.isEnabled()


def test_pulse_module_user_selects_trials_for_repeatability(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    runtime = CoreFlowRuntime(data_root=tmp_path / "data")
    window = MainWindow(runtime=runtime)
    qtbot.addWidget(window)
    window.show()
    pulse_runtime = pulse_counter_window.PulseCounterRuntime(
        runtime.repository,
        operator="pytest",
    )
    pulse_runtime.save_profile(device_id="CFM-PULSE-REPEAT", pulse_value=0.05)
    for trial_index, pulse_count in enumerate((1000, 990, 1010), start=1):
        pulse_runtime.calculate_trial_from_counts(
            device_id="CFM-PULSE-REPEAT",
            flow_point=100.0,
            trial_index=trial_index,
            pulse_count=pulse_count,
            standard_quantity=50.0,
        )

    selected_trial_ids: tuple[str, ...] = ()

    class FakeSelectionDialog:
        def __init__(self, trials, *, parent=None):
            nonlocal selected_trial_ids
            selected_trial_ids = tuple(trial.trial_id for trial in trials)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def selected_trial_ids(self):
            return selected_trial_ids

    monkeypatch.setattr(
        pulse_counter_window,
        "PulseRepeatabilitySelectionDialog",
        FakeSelectionDialog,
    )

    window.pulseModuleAction.trigger()
    assert window.pulseWindow is not None
    pulse_window = window.pulseWindow
    pulse_window.deviceIdLineEdit.setText("CFM-PULSE-REPEAT")
    _click(qtbot, pulse_window.loadProfileButton)

    assert pulse_window.trialTable.rowCount() == 3
    assert pulse_window.calculateRepeatabilityButton.isEnabled()
    _click(qtbot, pulse_window.calculateRepeatabilityButton)

    assert selected_trial_ids == (
        "PULSE-TRIAL-000001",
        "PULSE-TRIAL-000002",
        "PULSE-TRIAL-000003",
    )
    assert "Repeatability saved" in pulse_window.repeatabilitySummaryLabel.text()
    assert "stddev=1" in pulse_window.repeatabilitySummaryLabel.text()
    assert pulse_window.historyTable.rowCount() == 4
    assert _table_text(pulse_window.historyTable, 0, 1) == "pulse_repeatability"
    assert runtime.repository.count_rows("pulse_operation_attempts") == 4


def test_pulse_repeatability_selection_dialog_uses_consecutive_trial_windows(
    qtbot,
) -> None:
    dialog = pulse_counter_window.PulseRepeatabilitySelectionDialog(
        (
            _pulse_trial("PULSE-TRIAL-1", flow_point=100.0, trial_index=1),
            _pulse_trial("PULSE-TRIAL-2", flow_point=100.0, trial_index=2),
            _pulse_trial("PULSE-TRIAL-3", flow_point=100.0, trial_index=3),
            _pulse_trial("PULSE-TRIAL-5", flow_point=100.0, trial_index=5),
            _pulse_trial("PULSE-TRIAL-6", flow_point=200.0, trial_index=1),
            _pulse_trial("PULSE-TRIAL-7", flow_point=200.0, trial_index=2),
        )
    )
    qtbot.addWidget(dialog)

    assert dialog.flowCombo.count() == 2
    dialog.flowCombo.setCurrentText("100")
    assert dialog.windowCombo.count() == 1
    assert dialog.windowCombo.currentText() == "Trial 1-3"
    assert dialog.selected_trial_ids() == (
        "PULSE-TRIAL-1",
        "PULSE-TRIAL-2",
        "PULSE-TRIAL-3",
    )

    dialog.flowCombo.setCurrentText("200")
    assert dialog.windowCombo.count() == 0
    assert not dialog.okButton.isEnabled()


def _pulse_trial(
    trial_id: str,
    *,
    flow_point: float,
    trial_index: int,
    percent_error: float = 0.0,
) -> PulseTrialRecord:
    return PulseTrialRecord(
        trial_id=trial_id,
        device_id="CFM-PULSE-DIALOG",
        flow_point=flow_point,
        trial_index=trial_index,
        trial_status="accepted",
        pulse_count=1000,
        measured_quantity=50.0,
        standard_quantity=50.0,
        percent_error=percent_error,
    )


def _write_dsview_csv(tmp_path) -> object:
    csv_path = tmp_path / "pulse-ui.csv"
    csv_path.write_text(
        "\n".join(
            [
                "; CSV, generated by libsigrok4DSL 0.2.0",
                "; Channels (1/16)",
                "; Sample rate: 25 MHz",
                "Time(s), 0",
                "0,0",
                "0.001,1",
                "0.0015,0",
                "0.011,1",
                "0.0115,0",
                "0.021,1",
                "0.0215,0",
            ]
        ),
        encoding="utf-8",
    )
    return csv_path

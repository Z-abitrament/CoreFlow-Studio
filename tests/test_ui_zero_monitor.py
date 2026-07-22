from __future__ import annotations

from datetime import UTC, datetime

from coreflow.analysis.zero_monitor import (
    ZeroMonitorEvaluation,
    ZeroMonitorMetrics,
    ZeroMonitorState,
)
from coreflow.app.modbus_zero_monitor import ZeroMonitorLiveUpdate
from coreflow.app.modbus_runtime import (
    ModbusFlowSampleSeries,
    ModbusTrialSamplePoint,
)
from coreflow.storage import Database, StorageRepository
from coreflow.ui.modbus_window import (
    CalibrationHistoryFlowPlotDialog,
    ModbusModuleWindow,
)
from coreflow.ui.modbus_zero_monitor import ZeroMonitorDialog


def test_zero_monitor_dialog_defaults_are_diagnostic_and_target_is_read_only(qtbot) -> None:
    dialog = ZeroMonitorDialog()
    qtbot.addWidget(dialog)
    dialog.show()

    config = dialog.capture_configuration()

    assert dialog.targetPeriodLabel.text() == "Target 100 ms"
    assert config.minimum_stable_duration_s is None
    assert all(item.enabled and item.limit is None for item in config.thresholds.values())
    assert dialog.startButton.isEnabled() is False
    assert dialog.zeroFlowCheckBox.isEnabled() is True
    criterion_labels = {
        dialog.thresholdTable.item(row, 1).text()
        for row in range(dialog.thresholdTable.rowCount())
    }
    assert "Long Range" in criterion_labels


def test_zero_monitor_dialog_displays_full_and_robust_long_ranges(qtbot) -> None:
    dialog = ZeroMonitorDialog()
    qtbot.addWidget(dialog)

    dialog._set_metrics(
        {"long_mean": 10.0, "long_range": 20.0, "long_p95_p5": 18.0}
    )

    displayed = {
        dialog.metricTable.item(row, 0).text(): dialog.metricTable.item(row, 1).text()
        for row in range(dialog.metricTable.rowCount())
    }
    assert displayed["long_mean"] == "10"
    assert displayed["long_range"] == "20"
    assert displayed["long_p95_p5"] == "18"


def test_zero_monitor_dialog_locks_confirmation_and_renders_live_update(qtbot) -> None:
    dialog = ZeroMonitorDialog()
    qtbot.addWidget(dialog)
    dialog.set_ready(connected=True)
    dialog.zeroFlowCheckBox.setChecked(True)
    dialog.set_running()
    update = ZeroMonitorLiveUpdate(
        row={
            "device_tick_ms_unwrapped": 1200,
            "continuous_segment": 1,
            "live_zero_600ms": 1.25,
            "base_mean_100ms": 1.0,
        },
        processed=None,
        analysis=ZeroMonitorEvaluation(
            state=ZeroMonitorState.NOT_READY,
            metrics=ZeroMonitorMetrics(candidate_count=1),
            reason_codes=("LONG_WINDOW_NOT_READY",),
        ),
        counters={"logical_poll_count": 1, "physical_request_count": 1},
    )

    dialog.add_update(update)

    assert dialog.zeroFlowCheckBox.isEnabled() is False
    assert dialog.stopButton.isEnabled() is True
    assert "NOT_READY" in dialog.statusLabel.text()
    assert "LONG_WINDOW_NOT_READY" in dialog.reasonLabel.text()
    assert len(dialog._points) == 1


def test_modbus_operations_menu_exposes_zero_monitor(qtbot, tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite3")
    database.initialize()
    window = ModbusModuleWindow(
        StorageRepository(database),
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    labels = [action.text() for action in window.operationsMenu.actions()]

    assert "Zero Monitor" in labels
    assert window.zeroMonitorAction.objectName() == "modbusZeroMonitorAction"


def test_zero_monitor_dialog_zero_flow_confirmation_clears_on_reopen(qtbot, tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite3")
    database.initialize()
    window = ModbusModuleWindow(StorageRepository(database), data_root=tmp_path)
    qtbot.addWidget(window)
    dialog = window._ensure_zero_monitor_dialog()
    dialog.zeroFlowCheckBox.setChecked(True)

    window._zero_monitor()

    assert dialog.zeroFlowCheckBox.isChecked() is False


def test_generic_history_plot_defaults_to_latest_zero_monitor_segment(qtbot) -> None:
    captured = datetime(2026, 1, 1, tzinfo=UTC)
    series = ModbusFlowSampleSeries(
        artifact_id="ZERO-CURVE",
        run_id="RUN-ZERO",
        flow_rate_parameter="live_zero_600ms",
        unit="us",
        samples=(),
        variable_names=("live_zero_600ms",),
        units={"live_zero_600ms": "us"},
        points=(
            ModbusTrialSamplePoint(
                captured_at=captured,
                values={
                    "device_tick_ms_unwrapped": 100,
                    "continuous_segment": 1,
                    "live_zero_600ms": 1.0,
                },
            ),
            ModbusTrialSamplePoint(
                captured_at=captured,
                values={
                    "device_tick_ms_unwrapped": 200,
                    "continuous_segment": 1,
                    "live_zero_600ms": 1.1,
                },
            ),
            ModbusTrialSamplePoint(
                captured_at=captured,
                values={
                    "device_tick_ms_unwrapped": 50,
                    "continuous_segment": 2,
                    "live_zero_600ms": 0.9,
                },
            ),
        ),
        x_axis_variable="device_tick_ms_unwrapped",
        x_axis_unit="ms",
        x_axis_scope="continuous_segment",
        segment_variable="continuous_segment",
    )
    dialog = CalibrationHistoryFlowPlotDialog()
    qtbot.addWidget(dialog)

    dialog.set_series((("Zero Monitor", series),))

    assert dialog.segmentCombo.currentData() == "2"
    assert dialog.plotAlignmentCombo.isEnabled() is False
    assert len(dialog._point_items) == 1
    dialog.segmentCombo.setCurrentIndex(dialog.segmentCombo.findData("all"))
    assert len(dialog._point_items) == 2

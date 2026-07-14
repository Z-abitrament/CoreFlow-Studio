from __future__ import annotations

from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QHeaderView, QMessageBox

from coreflow.app import FillingConfiguration, FillingMode, FillingTrialService
from coreflow.storage import Database, DeviceRecord, StorageRepository
from coreflow.ui.filling_dialogs import (
    FillingDeviceSelectionDialog,
    NewFillingDeviceDialog,
)
from coreflow.ui.filling_history import FillingHistoryDialog
from coreflow.ui.filling_window import FillingModuleWindow


START = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    for device_id in ("CFM-UI-1", "CFM-UI-2"):
        repository.create_device(
            DeviceRecord(
                device_id=device_id,
                device_type="modbus_rtu",
                model=f"MODEL-{device_id}",
                created_at=START,
                updated_at=START,
            )
        )
    return repository


def _click(qtbot, button) -> None:
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def _select_device(
    qtbot,
    window: FillingModuleWindow,
    device_id: str = "CFM-UI-1",
) -> None:
    window.open_device_selector()
    dialog = window.deviceSelectionDialog
    assert dialog is not None and dialog.isVisible()
    index = dialog.deviceCombo.findData(device_id)
    assert index >= 0
    dialog.deviceCombo.setCurrentIndex(index)
    _click(qtbot, dialog.selectButton)
    assert window.deviceValueLabel.text() == device_id


def _set_configuration(
    window: FillingModuleWindow,
    *,
    label: str = "CTRL-A + VALVE-2",
    pulse_switch: float = 125.0,
    mass_per_pulse: float = 0.1,
    mass_unit: str = "g",
    flow_point: float = 100.0,
    specified_mass: float = 1000.0,
    target_mass: float = 995.0,
) -> None:
    window.controlValveCombo.setEditText(label)
    window.pulseSwitchSpinBox.setValue(pulse_switch)
    window.massPerPulseSpinBox.setValue(mass_per_pulse)
    window.massUnitEdit.setText(mass_unit)
    window.flowPointSpinBox.setValue(flow_point)
    window.specifiedMassSpinBox.setValue(specified_mass)
    if window.regularModeButton.isChecked():
        window.targetMassSpinBox.setValue(target_mass)


def _calculate_trial(
    qtbot,
    window: FillingModuleWindow,
    standard_mass: float,
    *,
    add_next: bool,
) -> None:
    window.standardMassEdit.setText(str(standard_mass))
    _click(qtbot, window.calculateTrialButton)
    assert window.standardMassEdit.text() == ""
    if add_next:
        _click(qtbot, window.addTrialButton)


def _check_trial(window: FillingModuleWindow, row: int, checked: bool = True) -> None:
    item = window.trialTable.item(row, 0)
    assert item is not None
    item.setCheckState(
        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    )


def _service_configuration(
    mode: FillingMode,
    *,
    label: str = "CTRL-A + VALVE-2",
    specified_mass: float = 1000.0,
    target_mass: float | None = None,
) -> FillingConfiguration:
    return FillingConfiguration(
        mode=mode,
        control_valve_label=label,
        pulse_frequency_switch_point_hz=125.0,
        mass_per_pulse=0.1,
        mass_unit="g",
        flow_point_g_per_s=100.0,
        specified_mass=specified_mass,
        target_mass=(
            specified_mass
            if mode is FillingMode.ADVANCE
            else 995.0 if target_mass is None else target_mass
        ),
    )


def _service_trials(
    service: FillingTrialService,
    masses: tuple[float, ...],
    *,
    note_prefix: str | None = None,
) -> tuple[str, ...]:
    trial_ids: list[str] = []
    for index, mass in enumerate(masses):
        trial_ids.append(
            service.calculate_current_trial(
                mass,
                notes=(
                    f"{note_prefix} {index + 1}"
                    if note_prefix is not None
                    else None
                ),
            ).trial_id
        )
        if index < len(masses) - 1:
            service.add_trial()
    return tuple(trial_ids)


def test_workbench_uses_shared_device_selector_and_has_stable_compact_controls(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()

    assert not hasattr(window, "deviceIdLineEdit")
    assert window.minimumWidth() <= 900
    assert window.minimumHeight() <= 600
    assert window.trialTable.columnCount() == 9
    assert [
        window.trialTable.horizontalHeaderItem(column).text()
        for column in range(9)
    ] == [
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
    header = window.trialTable.horizontalHeader()
    assert all(
        header.sectionResizeMode(column) is QHeaderView.ResizeMode.Interactive
        for column in range(window.trialTable.columnCount())
    )
    assert isinstance(window.standardMassEdit.validator(), QDoubleValidator)
    assert window.standardMassEdit.text() == ""
    assert window.massPerPulseSpinBox.decimals() >= 15
    assert window.massPerPulseSpinBox.maximum() >= 1.0e18

    stable_controls = (
        window.deviceValueLabel,
        window.changeDeviceButton,
        window.controlValveCombo,
        window.advanceProfileCombo,
        window.regularModeButton,
        window.advanceModeButton,
        window.pulseSwitchSpinBox,
        window.massPerPulseSpinBox,
        window.massUnitEdit,
        window.flowPointSpinBox,
        window.specifiedMassSpinBox,
        window.targetMassSpinBox,
        window.currentTrialIndexLabel,
        window.standardMassEdit,
        window.calculateTrialButton,
        window.addTrialButton,
        window.trialTable,
        window.calculateRepeatabilityButton,
        window.calculateAdvanceButton,
        window.setAdvanceButton,
        window.resultTextEdit,
        window.historyButton,
        window.endGroupButton,
        window.statusLabel,
    )
    assert all(control.objectName().startswith("filling") for control in stable_controls)

    window.open_device_selector()
    dialog = window.deviceSelectionDialog
    assert dialog is not None and dialog.isVisible()
    assert not dialog.deviceCombo.isEditable()
    assert dialog.deviceCombo.findData("CFM-UI-1") >= 0
    assert dialog.deviceCombo.findData("CFM-UI-2") >= 0


def test_ensure_device_selected_runs_modal_selector(qtbot, repository) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()

    def accept_first_device() -> None:
        dialog = window.deviceSelectionDialog
        if dialog is None:
            QTimer.singleShot(0, accept_first_device)
            return
        dialog.deviceCombo.setCurrentIndex(
            dialog.deviceCombo.findData("CFM-UI-1")
        )
        dialog.selectButton.click()

    QTimer.singleShot(0, accept_first_device)
    assert window.ensure_device_selected() is True
    assert window.deviceValueLabel.text() == "CFM-UI-1"


def test_new_device_dialog_creates_selectable_device_and_rejects_duplicates(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    selector = FillingDeviceSelectionDialog(service)
    qtbot.addWidget(selector)
    selector.show()

    _click(qtbot, selector.newDeviceButton)
    create_dialog = selector.newDeviceDialog
    assert create_dialog is not None and create_dialog.isVisible()
    create_dialog.deviceIdLineEdit.setText(" CFM-UI-NEW ")
    create_dialog.modelLineEdit.setText("M-NEW")
    _click(qtbot, create_dialog.createButton)

    assert repository.get_device("CFM-UI-NEW") is not None
    assert selector.deviceCombo.currentData() == "CFM-UI-NEW"
    _click(qtbot, selector.newDeviceButton)
    assert selector.newDeviceDialog is create_dialog
    assert len(selector.findChildren(NewFillingDeviceDialog)) == 1
    create_dialog.close()
    selector.close()

    duplicate = NewFillingDeviceDialog(service)
    qtbot.addWidget(duplicate)
    duplicate.show()
    duplicate.deviceIdLineEdit.setText("CFM-UI-NEW")
    duplicate.modelLineEdit.setText("replacement")
    _click(qtbot, duplicate.createButton)

    assert duplicate.isVisible()
    assert duplicate.deviceIdLineEdit.selectedText() == "CFM-UI-NEW"
    assert "already exists" in duplicate.statusLabel.text()
    assert repository.get_device("CFM-UI-NEW").model == "M-NEW"

    duplicate.deviceIdLineEdit.clear()
    _click(qtbot, duplicate.createButton)
    assert duplicate.isVisible()
    assert "non-empty" in duplicate.statusLabel.text()


def test_successful_trial_formats_row_clears_input_and_requires_manual_add(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)

    assert window.standardMassEdit.text() == ""
    window.standardMassEdit.setText("1005")
    _click(qtbot, window.calculateTrialButton)

    assert window.trialTable.rowCount() == 1
    assert window.trialTable.item(0, 1).text() == "1"
    assert window.trialTable.item(0, 7).text() == "+0.500000%"
    assert window.trialTable.item(0, 8).text() == "Calculated"
    assert window.trialTable.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert window.standardMassEdit.text() == ""
    assert not window.calculateTrialButton.isEnabled()
    assert window.addTrialButton.isEnabled()

    _click(qtbot, window.addTrialButton)
    assert window.currentTrialIndexLabel.text() == "2"
    assert window.standardMassEdit.text() == ""
    assert window.calculateTrialButton.isEnabled()


def test_device_restore_is_per_device_and_standard_mass_stays_blank(
    qtbot,
    repository: StorageRepository,
) -> None:
    first = FillingModuleWindow(repository=repository)
    qtbot.addWidget(first)
    first.show()
    _select_device(qtbot, first, "CFM-UI-1")
    _set_configuration(
        first,
        label="RESTORE-ME",
        pulse_switch=333.125,
        mass_per_pulse=0.000125,
        flow_point=225.75,
        specified_mass=750.25,
        target_mass=742.125,
    )
    _calculate_trial(qtbot, first, 751.0, add_next=False)
    assert first.end_active_group() is True

    second = FillingModuleWindow(repository=repository)
    qtbot.addWidget(second)
    second.show()
    _select_device(qtbot, second, "CFM-UI-1")

    assert second.controlValveCombo.currentText() == "RESTORE-ME"
    assert second.pulseSwitchSpinBox.value() == pytest.approx(333.125)
    assert second.massPerPulseSpinBox.value() == pytest.approx(0.000125)
    assert second.flowPointSpinBox.value() == pytest.approx(225.75)
    assert second.specifiedMassSpinBox.value() == pytest.approx(750.25)
    assert second.targetMassSpinBox.value() == pytest.approx(742.125)
    assert second.standardMassEdit.text() == ""

    third = FillingModuleWindow(repository=repository)
    qtbot.addWidget(third)
    third.show()
    before = third.flowPointSpinBox.value()
    _select_device(qtbot, third, "CFM-UI-2")
    assert third.flowPointSpinBox.value() == before
    assert third.controlValveCombo.currentText() != "RESTORE-ME"
    assert third.standardMassEdit.text() == ""


def test_mode_mirrors_target_and_first_trial_locks_complete_configuration(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)

    _click(qtbot, window.advanceModeButton)
    window.specifiedMassSpinBox.setValue(1234.56789)
    assert window.advanceModeButton.isChecked()
    assert window.targetMassSpinBox.isReadOnly()
    assert window.targetMassSpinBox.value() == pytest.approx(1234.56789)

    _click(qtbot, window.regularModeButton)
    assert window.regularModeButton.isChecked()
    assert not window.targetMassSpinBox.isReadOnly()
    window.targetMassSpinBox.setValue(1200.25)
    _calculate_trial(qtbot, window, 1235.0, add_next=False)

    locked_controls = (
        window.controlValveCombo,
        window.advanceProfileCombo,
        window.regularModeButton,
        window.advanceModeButton,
        window.pulseSwitchSpinBox,
        window.massPerPulseSpinBox,
        window.massUnitEdit,
        window.flowPointSpinBox,
        window.specifiedMassSpinBox,
        window.targetMassSpinBox,
    )
    assert all(not control.isEnabled() for control in locked_controls)


def test_trial_failure_keeps_standard_mass_and_retryable_ui_state(
    qtbot,
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FillingTrialService(repository)
    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)
    window.standardMassEdit.setText("1005.125")

    def fail_calculation(_standard_mass: float) -> None:
        raise RuntimeError("injected trial write failure")

    monkeypatch.setattr(service, "calculate_current_trial", fail_calculation)
    _click(qtbot, window.calculateTrialButton)

    assert window.standardMassEdit.text() == "1005.125"
    assert window.trialTable.rowCount() == 0
    assert service.snapshot().has_pending_trial
    assert window.calculateTrialButton.isEnabled()
    assert "injected trial write failure" in window.statusLabel.text()


def test_repeatability_button_requires_exactly_three_consecutive_trials(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)
    for index, mass in enumerate((1005.0, 1006.0, 1004.0, 1007.0)):
        _calculate_trial(qtbot, window, mass, add_next=index < 3)

    for row in (0, 1, 3):
        _check_trial(window, row)
    assert not window.calculateRepeatabilityButton.isEnabled()

    _check_trial(window, 0, checked=False)
    _check_trial(window, 2)
    assert window.calculateRepeatabilityButton.isEnabled()
    assert not window.calculateAdvanceButton.isEnabled()

    selected_ids = [
        window.trialTable.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in (1, 2, 3)
    ]
    _click(qtbot, window.calculateRepeatabilityButton)
    result = window.resultTextEdit.toPlainText()
    assert all(trial_id in result for trial_id in selected_ids)
    assert "repeatability_stddev_percent" in result


def test_advance_allows_nonconsecutive_selection_and_set_starts_regular_trial_one(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)
    _click(qtbot, window.advanceModeButton)
    for index, mass in enumerate((1005.0, 1001.0, 1004.0, 1006.0)):
        _calculate_trial(qtbot, window, mass, add_next=index < 3)

    for row in (0, 2, 3):
        _check_trial(window, row)
    assert window.calculateAdvanceButton.isEnabled()
    assert not window.calculateRepeatabilityButton.isEnabled()

    selected_ids = [
        window.trialTable.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in (0, 2, 3)
    ]
    _click(qtbot, window.calculateAdvanceButton)
    preview = window.resultTextEdit.toPlainText()
    assert all(trial_id in preview for trial_id in selected_ids)
    assert "mean_standard_mass" in preview
    assert "advance_mass" in preview
    assert "corrected_target_mass" in preview
    assert window.setAdvanceButton.isEnabled()

    _click(qtbot, window.setAdvanceButton)
    assert window.regularModeButton.isChecked()
    assert window.trialTable.rowCount() == 0
    assert window.resultTextEdit.toPlainText() == ""
    assert window.currentTrialIndexLabel.text() == "1"
    assert window.standardMassEdit.text() == ""
    assert window.targetMassSpinBox.value() == pytest.approx(995.0)
    assert window.calculateTrialButton.isEnabled()
    assert not window.setAdvanceButton.isEnabled()


def test_advance_preview_is_invalidated_when_trial_selection_changes(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    _set_configuration(window)
    _click(qtbot, window.advanceModeButton)
    for index, mass in enumerate((1005.0, 1006.0, 1004.0, 1007.0)):
        _calculate_trial(qtbot, window, mass, add_next=index < 3)

    for row in (0, 1, 2):
        _check_trial(window, row)
    _click(qtbot, window.calculateAdvanceButton)
    assert window.setAdvanceButton.isEnabled()
    assert window.resultTextEdit.toPlainText()

    _check_trial(window, 2, checked=False)
    _check_trial(window, 3)

    assert window.calculateAdvanceButton.isEnabled()
    assert not window.setAdvanceButton.isEnabled()
    assert window.resultTextEdit.toPlainText() == ""
    profiles_before = service.list_advance_profiles()
    _click(qtbot, window.setAdvanceButton)
    assert service.list_advance_profiles() == profiles_before
    assert service.snapshot().configuration.mode is FillingMode.ADVANCE


def test_multiple_same_condition_profiles_remain_distinct_and_load_full_snapshot(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    expected_targets: set[float] = set()
    for label, masses in (
        ("CTRL-A + VALVE-2", (1005.0, 1006.0, 1004.0)),
        ("CTRL-A + VALVE-2", (1007.0, 1005.0, 1006.0)),
    ):
        service.start_group(_service_configuration(FillingMode.ADVANCE, label=label))
        trial_ids = _service_trials(service, masses)
        result = service.calculate_advance(trial_ids)
        profile = service.set_advance(result.result_id)
        expected_targets.add(profile.corrected_target_mass)
        service.end_group()

    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()

    assert window.advanceProfileCombo.count() == 3
    profile_texts = [
        window.advanceProfileCombo.itemText(index)
        for index in range(1, window.advanceProfileCombo.count())
    ]
    assert len(set(profile_texts)) == 2
    assert all("advance=" in text and "100" in text for text in profile_texts)

    loaded_targets: set[float] = set()
    for index in range(1, window.advanceProfileCombo.count()):
        window.advanceProfileCombo.setCurrentIndex(index)
        loaded_targets.add(window.targetMassSpinBox.value())
        assert window.regularModeButton.isChecked()
        assert window.controlValveCombo.currentText() == "CTRL-A + VALVE-2"
        assert window.pulseSwitchSpinBox.value() == pytest.approx(125.0)
        assert window.massPerPulseSpinBox.value() == pytest.approx(0.1)
        assert window.standardMassEdit.text() == ""
    assert sorted(loaded_targets) == pytest.approx(sorted(expected_targets))


def test_selected_advance_profile_can_be_deleted_without_erasing_history(
    qtbot,
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    service.start_group(_service_configuration(FillingMode.ADVANCE))
    result = service.calculate_advance(
        _service_trials(service, (1005.0, 1006.0, 1004.0))
    )
    profile = service.set_advance(result.result_id)
    service.end_group()

    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    window.advanceProfileCombo.setCurrentIndex(1)
    assert window.deleteAdvanceProfileButton.isEnabled()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes),
    )

    _click(qtbot, window.deleteAdvanceProfileButton)

    assert window.advanceProfileCombo.count() == 1
    assert service.list_advance_profiles() == ()
    assert any(entry.record_id == profile.profile_id for entry in service.list_history())


def test_close_profile_conditions_remain_distinguishable_in_profile_summary(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    specified_values = (1000.0000001, 1000.0000002)
    for specified in specified_values:
        service.start_group(
            _service_configuration(
                FillingMode.ADVANCE,
                specified_mass=specified,
            )
        )
        trial_ids = _service_trials(
            service,
            (specified + 1.0, specified + 1.1, specified + 0.9),
        )
        result = service.calculate_advance(trial_ids)
        service.set_advance(result.result_id)
        service.end_group()

    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    summaries = [
        window.advanceProfileCombo.itemText(index)
        for index in range(1, window.advanceProfileCombo.count())
    ]

    assert any("specified=1000.0000001" in text for text in summaries)
    assert any("specified=1000.0000002" in text for text in summaries)


def test_history_table_exposes_scannable_conditions_for_all_record_types(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    service.start_group(
        _service_configuration(FillingMode.REGULAR, label="REGULAR-LABEL")
    )
    regular_ids = _service_trials(
        service,
        (1005.0, 1006.0, 1004.0),
        note_prefix="regular note",
    )
    repeatability = service.calculate_repeatability(regular_ids)
    service.end_group()
    service.start_group(
        _service_configuration(FillingMode.ADVANCE, label="ADVANCE-LABEL")
    )
    advance_ids = _service_trials(
        service,
        (1005.0, 1006.0, 1004.0),
        note_prefix="advance note",
    )
    advance = service.calculate_advance(advance_ids)
    profile = service.set_advance(advance.result_id)
    service.end_group()

    dialog = FillingHistoryDialog(service, "CFM-UI-1")
    qtbot.addWidget(dialog)
    dialog.show()

    assert [
        dialog.recordTable.horizontalHeaderItem(column).text()
        for column in range(dialog.recordTable.columnCount())
    ] == [
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
    rows = {
        dialog.recordTable.item(row, 8).text(): row
        for row in range(dialog.recordTable.rowCount())
    }
    regular_trial_row = rows[regular_ids[0]]
    assert dialog.recordTable.item(regular_trial_row, 2).text() == "100 g/s"
    assert dialog.recordTable.item(regular_trial_row, 3).text() == "1000 g"
    assert dialog.recordTable.item(regular_trial_row, 4).text() == "995 g"
    assert dialog.recordTable.item(regular_trial_row, 5).text() == "REGULAR-LABEL"
    assert dialog.recordTable.item(regular_trial_row, 6).text() == "regular note 1"

    repeatability_row = rows[repeatability.result_id]
    assert dialog.recordTable.item(repeatability_row, 2).text() == "100 g/s"
    assert dialog.recordTable.item(repeatability_row, 4).text() == "995 g"
    assert dialog.recordTable.item(repeatability_row, 5).text() == "REGULAR-LABEL"

    advance_row = rows[advance.result_id]
    assert dialog.recordTable.item(advance_row, 4).text() == "995 g"
    assert dialog.recordTable.item(advance_row, 5).text() == "ADVANCE-LABEL"

    profile_row = rows[profile.profile_id]
    assert dialog.recordTable.item(profile_row, 2).text() == "100 g/s"
    assert dialog.recordTable.item(profile_row, 4).text() == "995 g"
    assert dialog.recordTable.item(profile_row, 5).text() == "ADVANCE-LABEL"
    assert "advance note" in dialog.recordTable.item(profile_row, 6).text()
    assert dialog.recordTable.item(profile_row, 6).toolTip()


def test_history_shows_four_record_types_complete_details_and_isolates_query_error(
    qtbot,
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    service.start_group(_service_configuration(FillingMode.REGULAR))
    regular_ids = _service_trials(service, (1005.0, 1006.0, 1004.0))
    service.calculate_repeatability(regular_ids)
    service.end_group()
    service.start_group(_service_configuration(FillingMode.ADVANCE))
    advance_ids = _service_trials(service, (1005.0, 1006.0, 1004.0))
    advance_result = service.calculate_advance(advance_ids)
    profile = service.set_advance(advance_result.result_id)
    service.end_group()

    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    _click(qtbot, window.historyButton)
    dialog = window.historyDialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.deviceValueLabel.text() == "CFM-UI-1"
    assert not hasattr(dialog, "deviceCombo")

    record_types = {
        dialog.recordTable.item(row, 0).data(Qt.ItemDataRole.UserRole + 1)
        for row in range(dialog.recordTable.rowCount())
    }
    assert all(
        dialog.recordTable.item(row, 0).data(Qt.ItemDataRole.UserRole)
        == dialog.recordTable.item(row, 8).text()
        for row in range(dialog.recordTable.rowCount())
    )
    assert {
        "trial",
        "repeatability",
        "advance_calculation",
        "advance_profile",
    } <= record_types

    result_row = next(
        row
        for row in range(dialog.recordTable.rowCount())
        if dialog.recordTable.item(row, 8).text() == advance_result.result_id
    )
    dialog.recordTable.selectRow(result_row)
    detail = dialog.detailTextEdit.toPlainText()
    assert all(trial_id in detail for trial_id in advance_ids)
    assert "configuration_snapshot" in detail
    assert "metrics" in detail
    assert profile.profile_id in detail

    dialog.close()
    _click(qtbot, window.historyButton)
    assert window.historyDialog is dialog
    assert len(window.findChildren(FillingHistoryDialog)) == 1

    workbench_status = window.statusLabel.text()

    def fail_history(**_kwargs: object):
        raise RuntimeError("injected history query failure")

    monkeypatch.setattr(service, "list_history", fail_history)
    _click(qtbot, dialog.refreshButton)
    assert dialog.recordTable.rowCount() == 0
    assert "injected history query failure" in dialog.statusLabel.text()
    assert window.statusLabel.text() == workbench_status
    assert service.snapshot().device_id == "CFM-UI-1"


def test_same_device_accept_and_cancel_preserve_draft_while_real_switch_restores(
    qtbot,
    repository: StorageRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-2")
    service.start_group(
        _service_configuration(
            FillingMode.REGULAR,
            label="DEVICE-B-SAVED",
            specified_mass=750.25,
            target_mass=742.125,
        )
    )
    service.calculate_current_trial(751.0)
    service.end_group()
    service.select_device("CFM-UI-1")
    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    _set_configuration(
        window,
        label="DEVICE-A-DRAFT",
        pulse_switch=333.125,
        mass_per_pulse=0.000125,
        flow_point=225.75,
        specified_mass=1000.5,
        target_mass=990.25,
    )
    window.standardMassEdit.setText("1001.75")

    original_select_device = service.select_device
    select_calls: list[str] = []

    def tracking_select(device_id: str):
        select_calls.append(device_id)
        return original_select_device(device_id)

    monkeypatch.setattr(service, "select_device", tracking_select)
    window.open_device_selector()
    selector = window.deviceSelectionDialog
    assert selector is not None
    selector.deviceCombo.setCurrentIndex(
        selector.deviceCombo.findData("CFM-UI-1")
    )
    _click(qtbot, selector.selectButton)

    assert select_calls == []
    assert window.controlValveCombo.currentText() == "DEVICE-A-DRAFT"
    assert window.pulseSwitchSpinBox.value() == pytest.approx(333.125)
    assert window.standardMassEdit.text() == "1001.75"

    window.open_device_selector()
    assert window.deviceSelectionDialog is selector
    window.standardMassEdit.setText("1002.25")
    _click(qtbot, selector.cancelButton)
    assert select_calls == []
    assert window.controlValveCombo.currentText() == "DEVICE-A-DRAFT"
    assert window.standardMassEdit.text() == "1002.25"
    assert len(window.findChildren(FillingDeviceSelectionDialog)) == 1

    window.open_device_selector()
    assert window.deviceSelectionDialog is selector
    selector.deviceCombo.setCurrentIndex(
        selector.deviceCombo.findData("CFM-UI-2")
    )
    _click(qtbot, selector.selectButton)

    assert select_calls == ["CFM-UI-2"]
    assert window.deviceValueLabel.text() == "CFM-UI-2"
    assert window.controlValveCombo.currentText() == "DEVICE-B-SAVED"
    assert window.specifiedMassSpinBox.value() == pytest.approx(750.25)
    assert window.targetMassSpinBox.value() == pytest.approx(742.125)
    assert window.standardMassEdit.text() == ""


def test_open_history_remains_fixed_to_device_after_workbench_switch(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    service.select_device("CFM-UI-1")
    service.start_group(_service_configuration(FillingMode.REGULAR, label="A"))
    device_a_trial = service.calculate_current_trial(1005.0)
    service.end_group()
    service.select_device("CFM-UI-2")
    service.start_group(_service_configuration(FillingMode.REGULAR, label="B"))
    device_b_trial = service.calculate_current_trial(1006.0)
    service.end_group()
    service.select_device("CFM-UI-1")

    window = FillingModuleWindow(service=service)
    qtbot.addWidget(window)
    window.show()
    _click(qtbot, window.historyButton)
    dialog = window.historyDialog
    assert dialog is not None
    assert dialog.deviceValueLabel.text() == "CFM-UI-1"
    assert dialog.recordTable.rowCount() == 1
    assert dialog.recordTable.item(0, 8).text() == device_a_trial.trial_id

    assert window.end_active_group() is True
    _select_device(qtbot, window, "CFM-UI-2")
    _click(qtbot, dialog.refreshButton)

    assert dialog.deviceValueLabel.text() == "CFM-UI-1"
    assert dialog.recordTable.rowCount() == 1
    record_ids = {
        dialog.recordTable.item(row, 8).text()
        for row in range(dialog.recordTable.rowCount())
    }
    assert device_a_trial.trial_id in record_ids
    assert device_b_trial.trial_id not in record_ids
    assert service.snapshot().device_id == "CFM-UI-2"


def test_change_device_requires_end_and_end_group_is_safe(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window, "CFM-UI-1")
    _set_configuration(window)
    _calculate_trial(qtbot, window, 1005.0, add_next=False)

    _click(qtbot, window.changeDeviceButton)
    assert window.deviceSelectionDialog is None or not window.deviceSelectionDialog.isVisible()
    assert "End Group" in window.statusLabel.text()
    assert window.deviceValueLabel.text() == "CFM-UI-1"

    assert window.end_active_group() is True
    assert window.end_active_group() is True
    _select_device(qtbot, window, "CFM-UI-2")
    assert window.deviceValueLabel.text() == "CFM-UI-2"
    assert window.trialTable.rowCount() == 0


def test_real_close_ends_group_but_embedded_hide_preserves_state(
    qtbot,
    repository: StorageRepository,
) -> None:
    service = FillingTrialService(repository)
    embedded = FillingModuleWindow(service=service, embedded=True)
    qtbot.addWidget(embedded)
    embedded.show()
    _select_device(qtbot, embedded)
    _set_configuration(embedded)
    embedded.standardMassEdit.setText("1005.25")
    embedded.hide()

    assert service.snapshot().run_id is None
    assert embedded.standardMassEdit.text() == "1005.25"
    embedded.show()
    _click(qtbot, embedded.calculateTrialButton)
    assert service.snapshot().run_id is not None
    assert embedded.trialTable.rowCount() == 1

    embedded.hide()
    assert service.snapshot().run_id is not None
    assert embedded.trialTable.rowCount() == 1
    embedded.show()
    embedded.close()
    assert service.snapshot().run_id is None


def test_filling_window_keeps_header_actions_compact_on_wide_screens(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository, embedded=True)
    qtbot.addWidget(window)
    window.resize(1600, 800)
    window.show()

    assert window.newLabelButton.width() < 220
    assert window.historyButton.width() < 220
    assert window.controlValveCombo.width() > window.newLabelButton.width()


def test_numeric_inputs_display_compact_and_persist_visible_values(
    qtbot,
    repository: StorageRepository,
) -> None:
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()
    _select_device(qtbot, window)
    window.controlValveCombo.setEditText("CTRL-A + VALVE-2")

    assert window.specifiedMassSpinBox.decimals() == 15
    assert window.specifiedMassSpinBox.cleanText() == "1000"
    assert window.massPerPulseSpinBox.cleanText() == "0.1"

    window.standardMassEdit.setText("1005")
    _click(qtbot, window.calculateTrialButton)
    trial = window.service.snapshot().trials[0]
    assert trial.specified_mass == 1000.0
    assert trial.target_mass == 1000.0

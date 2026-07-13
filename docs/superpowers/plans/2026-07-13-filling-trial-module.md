# Filling Trial Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the independent Filling Module specified in `docs/superpowers/specs/2026-07-13-filling-trial-module-design.md`, including device-linked manual trials, error/repeatability calculations, advance profiles, history, persistence, UI, documentation, and version 0.7.0.

**Architecture:** Add pure filling calculations under `coreflow.analysis`, filling records and schema v4 under `coreflow.storage`, and a stateful headless `FillingTrialService` under `coreflow.app`. Add one self-contained PySide6 workbench under `coreflow.ui`, then register it in the existing main-window module stack. No pulse, serial, valve, or controller I/O enters this milestone.

**Tech Stack:** Python 3.11+, dataclasses, `sqlite3`, PySide6, pytest, pytest-qt.

---

## File Map

Create:

- `src/coreflow/analysis/filling.py`: pure calculation inputs, results, and validation.
- `src/coreflow/app/filling.py`: trial-group state machine and persistence orchestration.
- `src/coreflow/ui/filling_dialogs.py`: shared-device and control/valve label dialogs.
- `src/coreflow/ui/filling_history.py`: current-device filling history dialog.
- `src/coreflow/ui/filling_window.py`: single-page filling workbench.
- `tests/test_analysis_filling.py`: formula and numerical validation tests.
- `tests/test_storage_filling.py`: schema v4, repository, and atomic-transition tests.
- `tests/test_filling_service.py`: headless lifecycle, selection, and history tests.
- `tests/test_ui_filling.py`: operator-path Qt tests.
- `docs/M15_VERIFICATION.md`: verification evidence and hardware exclusions.

Modify:

- `src/coreflow/analysis/__init__.py`: export filling calculations.
- `src/coreflow/workflows/models.py`: add filling run type and neutral completed status.
- `tests/test_workflows_storage_models.py`: cover shared enum additions.
- `src/coreflow/storage/models.py`: add filling trial/profile records.
- `src/coreflow/storage/database.py`: schema v4 and filling tables.
- `src/coreflow/storage/repositories.py`: device listing, filling CRUD, history filters, and atomic set-advance transition.
- `src/coreflow/storage/__init__.py`: export new records.
- `tests/test_storage_foundation.py`: update migration-count expectation.
- `src/coreflow/app/__init__.py`: lazy-export filling service types.
- `src/coreflow/ui/main_window.py`: register and close the new embedded module.
- `src/coreflow/ui/__init__.py`: export the filling window.
- `tests/test_ui_main_window.py`: verify menu integration and module state isolation.
- `tests/test_packaging.py`: verify the packaged import surface includes the filling module.
- `tests/test_bootstrap.py`: update exact package and startup-log version assertions.
- `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/TEST_PLAN.md`, `docs/DATA_MODEL.md`, `docs/SIMULATION.md`, `docs/PROTOCOLS.md`: canonical behavior and traceability.
- `docs/USER_MANUAL.en.md`, `docs/USER_MANUAL.zh-CN.md`: operator workflow.
- `pyproject.toml`, `src/coreflow/__init__.py`: synchronized 0.7.0 version.

### Task 1: Filling Calculations And Shared Workflow Status

**Files:**

- Create: `tests/test_analysis_filling.py`
- Modify: `tests/test_workflows_storage_models.py`
- Create: `src/coreflow/analysis/filling.py`
- Modify: `src/coreflow/analysis/__init__.py`
- Modify: `src/coreflow/workflows/models.py`

- [ ] **Step 1: Write failing calculation and enum tests**

Create tests covering the agreed formulas and selection rules:

```python
from __future__ import annotations

import pytest

from coreflow.analysis.filling import (
    FillingTrialValue,
    calculate_advance,
    calculate_repeatability,
    calculate_trial_error,
)
from coreflow.workflows import RunStatus, RunType, WorkflowStepStatus


def test_filling_trial_error_uses_specified_mass() -> None:
    assert calculate_trial_error(1000.0, 1005.0) == pytest.approx(0.5)
    assert calculate_trial_error(1000.0, 995.0) == pytest.approx(-0.5)


def test_filling_repeatability_requires_three_consecutive_trials() -> None:
    trials = (
        FillingTrialValue("T-1", 1, 1000.0, 1005.0, 0.5),
        FillingTrialValue("T-2", 2, 1000.0, 1006.0, 0.6),
        FillingTrialValue("T-3", 3, 1000.0, 1004.0, 0.4),
    )
    result = calculate_repeatability(trials)
    assert result.mean_error_percent == pytest.approx(0.5)
    assert result.repeatability_stddev_percent == pytest.approx(0.1)

    with pytest.raises(ValueError, match="consecutive"):
        calculate_repeatability((trials[0], trials[2], FillingTrialValue("T-4", 4, 1000.0, 1005.0, 0.5)))


def test_filling_advance_allows_negative_advance() -> None:
    trials = tuple(
        FillingTrialValue(f"T-{index}", index, 1000.0, value, (value - 1000.0) / 10.0)
        for index, value in enumerate((998.0, 997.0, 999.0), start=1)
    )
    result = calculate_advance(trials)
    assert result.mean_standard_mass == pytest.approx(998.0)
    assert result.advance_mass == pytest.approx(-2.0)
    assert result.corrected_target_mass == pytest.approx(1002.0)


def test_filling_calculations_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="specified mass"):
        calculate_trial_error(0.0, 1.0)
    with pytest.raises(ValueError, match="at least 3"):
        calculate_advance((FillingTrialValue("T-1", 1, 1.0, 1.0, 0.0),))


def test_filling_workflow_enums_are_neutral() -> None:
    assert RunType.FILLING_TRIAL.value == "filling_trial"
    assert RunStatus.COMPLETED.value == "completed"
    assert WorkflowStepStatus.COMPLETED.value == "completed"
```

- [ ] **Step 2: Run tests and confirm the missing API failure**

Run:

```powershell
conda run -n coreflow-studio python -m pytest tests/test_analysis_filling.py tests/test_workflows_storage_models.py -q
```

Expected: collection fails because `coreflow.analysis.filling`, `RunType.FILLING_TRIAL`, and `RunStatus.COMPLETED` do not exist.

- [ ] **Step 3: Implement pure filling calculations and enum values**

Implement this public shape in `coreflow/analysis/filling.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Sequence


@dataclass(frozen=True, slots=True)
class FillingTrialValue:
    trial_id: str
    trial_index: int
    specified_mass: float
    standard_mass: float
    error_percent: float


@dataclass(frozen=True, slots=True)
class FillingRepeatabilityResult:
    trial_ids: tuple[str, ...]
    trial_indexes: tuple[int, ...]
    errors_percent: tuple[float, ...]
    mean_error_percent: float
    repeatability_stddev_percent: float


@dataclass(frozen=True, slots=True)
class FillingAdvanceResult:
    trial_ids: tuple[str, ...]
    trial_indexes: tuple[int, ...]
    standard_masses: tuple[float, ...]
    specified_mass: float
    mean_standard_mass: float
    advance_mass: float
    corrected_target_mass: float


def calculate_trial_error(specified_mass: float, standard_mass: float) -> float:
    _positive("specified mass", specified_mass)
    _positive("standard mass", standard_mass)
    return (standard_mass - specified_mass) / specified_mass * 100.0


def calculate_repeatability(trials: Sequence[FillingTrialValue]) -> FillingRepeatabilityResult:
    ordered = tuple(sorted(trials, key=lambda trial: trial.trial_index))
    if len(ordered) != 3:
        raise ValueError("Repeatability requires exactly 3 trials.")
    indexes = tuple(trial.trial_index for trial in ordered)
    if indexes != tuple(range(indexes[0], indexes[0] + 3)):
        raise ValueError("Repeatability trials must be consecutive.")
    errors = tuple(trial.error_percent for trial in ordered)
    mean = sum(errors) / 3.0
    stddev = sqrt(sum((value - mean) ** 2 for value in errors) / 2.0)
    return FillingRepeatabilityResult(
        trial_ids=tuple(trial.trial_id for trial in ordered),
        trial_indexes=indexes,
        errors_percent=errors,
        mean_error_percent=mean,
        repeatability_stddev_percent=stddev,
    )


def calculate_advance(trials: Sequence[FillingTrialValue]) -> FillingAdvanceResult:
    selected = tuple(trials)
    if len(selected) < 3:
        raise ValueError("Advance calculation requires at least 3 trials.")
    specified = selected[0].specified_mass
    _positive("specified mass", specified)
    if any(trial.specified_mass != specified for trial in selected):
        raise ValueError("Advance trials must share one specified mass.")
    masses = tuple(trial.standard_mass for trial in selected)
    for mass in masses:
        _positive("standard mass", mass)
    mean = sum(masses) / len(masses)
    advance = mean - specified
    corrected = specified - advance
    _positive("corrected target mass", corrected)
    return FillingAdvanceResult(
        trial_ids=tuple(trial.trial_id for trial in selected),
        trial_indexes=tuple(trial.trial_index for trial in selected),
        standard_masses=masses,
        specified_mass=specified,
        mean_standard_mass=mean,
        advance_mass=advance,
        corrected_target_mass=corrected,
    )


def _positive(label: str, value: float) -> None:
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{label.capitalize()} must be finite and greater than zero.")
```

Add `FILLING_TRIAL = "filling_trial"` to `RunType` and `COMPLETED =
"completed"` to both run and workflow-step status enums. Export the new
calculation types and functions from `coreflow.analysis`.

- [ ] **Step 4: Run focused tests**

Run the Step 2 command again. Expected: all tests pass.

- [ ] **Step 5: Commit the calculation slice**

```powershell
git add src/coreflow/analysis/filling.py src/coreflow/analysis/__init__.py src/coreflow/workflows/models.py tests/test_analysis_filling.py tests/test_workflows_storage_models.py
git commit -m "feat(filling): add trial calculations"
```

### Task 2: Schema V4 And Filling Repositories

**Files:**

- Create: `tests/test_storage_filling.py`
- Modify: `tests/test_storage_foundation.py`
- Modify: `src/coreflow/storage/models.py`
- Modify: `src/coreflow/storage/database.py`
- Modify: `src/coreflow/storage/repositories.py`
- Modify: `src/coreflow/storage/__init__.py`

- [ ] **Step 1: Write failing schema, CRUD, and atomic-transition tests**

The tests must create a real v3-shaped SQLite database without first running
the v4 initializer, seed an existing device plus an orphan Modbus profile, then
call `initialize()`:

```python
def _create_v3_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE devices(device_id TEXT PRIMARY KEY, device_type TEXT NOT NULL, serial_number TEXT, model TEXT, firmware_version TEXT, hardware_version TEXT, protocol_address TEXT, connection_metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE modbus_device_profiles(profile_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE, display_name TEXT, device_model TEXT, tube_model TEXT, transmitter_model TEXT, connection_settings_json TEXT NOT NULL DEFAULT '{}', register_map_json TEXT NOT NULL DEFAULT '{}', notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (3, '2026-07-13T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO devices(device_id, device_type, created_at, updated_at) VALUES ('CFM-1', 'modbus_rtu', '2026-07-13', '2026-07-13')"
        )
        connection.execute(
            "INSERT INTO modbus_device_profiles(profile_id, device_id, created_at, updated_at) VALUES ('profile:ORPHAN', 'ORPHAN', '2026-07-13', '2026-07-13')"
        )


def test_schema_v4_preserves_v3_data(tmp_path) -> None:
    path = tmp_path / "coreflow.sqlite"
    _create_v3_database(path)
    database = Database(path)
    database.initialize()
    repository = StorageRepository(database)

    assert repository.get_device("CFM-1") is not None
    assert repository.get_device("ORPHAN") is not None
    assert repository.count_rows("filling_trial_records") == 0
    assert repository.count_rows("filling_advance_profiles") == 0
    with database.connect() as connection:
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    assert versions == {3, 4}
```

Add CRUD tests that construct one `FillingTrialRecord` and one
`FillingAdvanceProfileRecord`, verify `list_devices()`, device/run filters,
latest-trial ordering, multiple same-condition profiles, and the unique
`(run_id, trial_index)` constraint.

Add atomic tests that inject duplicate IDs into
`save_filling_trial_transition()`, `save_filling_analysis()`, and
`save_filling_advance_transition()`, then verify no partial step, trial,
analysis, profile, or run-status update was committed.

- [ ] **Step 2: Run storage tests and confirm missing tables/types**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_storage_filling.py tests/test_storage_foundation.py -q
```

Expected: failures for missing filling record classes, tables, and repository methods.

- [ ] **Step 3: Add storage records and schema v4**

Add immutable dataclasses with this field contract:

```python
@dataclass(frozen=True, slots=True)
class FillingTrialRecord:
    trial_id: str
    run_id: str
    device_id: str
    trial_index: int
    trial_status: str
    mode: str
    control_valve_label: str
    pulse_frequency_switch_point_hz: float
    mass_per_pulse: float
    mass_unit: str
    flow_point_g_per_s: float
    specified_mass: float
    target_mass: float
    standard_mass: float
    percent_error: float
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    calculated_at: datetime | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class FillingAdvanceProfileRecord:
    profile_id: str
    device_id: str
    source_result_id: str
    control_valve_label: str
    pulse_frequency_switch_point_hz: float
    mass_per_pulse: float
    mass_unit: str
    flow_point_g_per_s: float
    specified_mass: float
    advance_mass: float
    corrected_target_mass: float
    source_trial_ids: tuple[str, ...]
    created_at: datetime | None = None
    configuration_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
```

Set `SCHEMA_VERSION = 4`. Add version-dispatched initialization: a fresh
database creates the complete schema and records version 4; a database whose
maximum migration version is 3 executes idempotent v4 table/index DDL in one
transaction, backfills any orphan `modbus_device_profiles.device_id` into
`devices` as `modbus_rtu`, then records version 4. Reject versions above 4.
Add foreign keys to `devices`, `run_sessions`, and `analysis_results`, indexes
for device/time history, and `UNIQUE(run_id, trial_index)`.

- [ ] **Step 4: Implement repository methods**

Add these exact public methods and row converters:

```text
create_device(record: DeviceRecord) -> None
list_devices() -> tuple[DeviceRecord, ...]
save_filling_trial(record: FillingTrialRecord) -> None
get_filling_trial(trial_id: str) -> FillingTrialRecord | None
list_filling_trials(*, run_id: str | None = None, device_id: str | None = None) -> tuple[FillingTrialRecord, ...]
latest_filling_trial(device_id: str) -> FillingTrialRecord | None
save_filling_advance_profile(record: FillingAdvanceProfileRecord) -> None
list_filling_advance_profiles(device_id: str) -> tuple[FillingAdvanceProfileRecord, ...]
get_analysis_result(result_id: str) -> AnalysisResultRecord | None
list_runs(limit: int | None = None, *, device_id: str | None = None, run_type: str | None = None) -> tuple[RunSummary, ...]
save_filling_trial_transition(*, run: RunSession, step: WorkflowStep, trial: FillingTrialRecord) -> None
save_filling_analysis(*, step: WorkflowStep, result: AnalysisResultRecord) -> None
save_filling_advance_transition(*, profile: FillingAdvanceProfileRecord, completed_run: RunSession, new_run: RunSession) -> None
```

Implement all three transition methods with one `with
self._database.connect() as connection:` block each and connection-scoped
insert helpers. Filling trials, profiles, steps, and analysis results use plain
`INSERT`, not `INSERT OR REPLACE`, so immutable history cannot be overwritten.
Do not call public repository methods from inside the transaction because those
methods open new connections.

Add both new table names to `count_rows()` and export both records from
`coreflow.storage`.

- [ ] **Step 5: Run focused storage tests**

Run the Step 2 command. Expected: all tests pass and old storage tests retain data.

- [ ] **Step 6: Commit the storage slice**

```powershell
git add src/coreflow/storage tests/test_storage_filling.py tests/test_storage_foundation.py
git commit -m "feat(storage): persist filling trials"
```

### Task 3: Headless Filling Trial Service

**Files:**

- Create: `tests/test_filling_service.py`
- Create: `src/coreflow/app/filling.py`
- Modify: `src/coreflow/app/__init__.py`

- [ ] **Step 1: Write failing service lifecycle tests**

Use a real temporary repository and a fixed clock. Cover:

```python
def _config(mode: FillingMode = FillingMode.REGULAR) -> FillingConfiguration:
    return FillingConfiguration(
        mode=mode,
        control_valve_label="CTRL-A + VALVE-2",
        pulse_frequency_switch_point_hz=125.0,
        mass_per_pulse=0.1,
        mass_unit="g",
        flow_point_g_per_s=100.0,
        specified_mass=1000.0,
        target_mass=1000.0 if mode is FillingMode.ADVANCE else 995.0,
    )


def test_service_calculates_trial_and_requires_manual_add(service) -> None:
    service.select_device("CFM-1")
    service.start_group(_config())
    trial = service.calculate_current_trial(1005.0)
    assert trial.percent_error == pytest.approx(0.5)
    assert service.snapshot().has_pending_trial is False
    service.add_trial()
    assert service.snapshot().pending_trial_index == 2


def test_service_sets_advance_into_new_regular_group(service) -> None:
    service.select_device("CFM-1")
    service.start_group(_config(FillingMode.ADVANCE))
    trial_ids = []
    for mass in (1005.0, 1006.0, 1004.0):
        trial_ids.append(service.calculate_current_trial(mass).trial_id)
        if len(trial_ids) < 3:
            service.add_trial()
    calculation = service.calculate_advance(trial_ids)
    profile = service.set_advance(calculation.result_id)
    snapshot = service.snapshot()
    assert profile.advance_mass == pytest.approx(5.0)
    assert snapshot.configuration.mode is FillingMode.REGULAR
    assert snapshot.configuration.target_mass == pytest.approx(995.0)
    assert snapshot.pending_trial_index == 1
```

Also cover duplicate device creation, missing device selection, per-device last
configuration, blank pending standard mass, configuration mismatch rejection,
three-consecutive repeatability, nonconsecutive advance selection, multiple
profiles, closing empty/nonempty groups, and history record types.

- [ ] **Step 2: Run service tests and confirm missing service API**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_filling_service.py -q
```

Expected: import failure for `coreflow.app.filling`.

- [ ] **Step 3: Implement service models and validation**

Implement and export:

```python
class FillingMode(StrEnum):
    REGULAR = "regular"
    ADVANCE = "advance"


@dataclass(frozen=True, slots=True)
class FillingConfiguration:
    mode: FillingMode
    control_valve_label: str
    pulse_frequency_switch_point_hz: float
    mass_per_pulse: float
    mass_unit: str
    flow_point_g_per_s: float
    specified_mass: float
    target_mass: float

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "control_valve_label": self.control_valve_label,
            "pulse_frequency_switch_point_hz": self.pulse_frequency_switch_point_hz,
            "mass_per_pulse": self.mass_per_pulse,
            "mass_unit": self.mass_unit,
            "flow_point_g_per_s": self.flow_point_g_per_s,
            "specified_mass": self.specified_mass,
            "target_mass": self.target_mass,
        }


@dataclass(frozen=True, slots=True)
class FillingGroupSnapshot:
    device_id: str | None
    run_id: str | None
    status: RunStatus | None
    configuration: FillingConfiguration | None
    configuration_locked: bool
    has_pending_trial: bool
    pending_trial_index: int | None
    trials: tuple[FillingTrialRecord, ...]


@dataclass(frozen=True, slots=True)
class FillingAnalysisRecord:
    result_id: str
    run_id: str
    result_type: str
    created_at: datetime
    metrics: dict[str, object]


@dataclass(frozen=True, slots=True)
class FillingHistoryEntry:
    record_id: str
    record_type: str
    run_id: str
    device_id: str
    created_at: datetime | None
    summary: str
    details: dict[str, object]
```

Validate finite positive numeric fields, nonempty label/unit, and
`target_mass == specified_mass` in advance mode.

- [ ] **Step 4: Implement lifecycle and persistence methods**

Implement this public service surface with these exact signatures:

```text
FillingTrialService(repository, *, operator="operator", software_version=__version__, clock=utc_now, token_factory=new_token)
list_devices() -> tuple[DeviceRecord, ...]
create_device(*, device_id: str, model: str | None = None) -> DeviceRecord
select_device(device_id: str) -> FillingConfiguration | None
start_group(configuration: FillingConfiguration) -> FillingGroupSnapshot
update_pending_configuration(configuration: FillingConfiguration) -> FillingGroupSnapshot
calculate_current_trial(standard_mass: float, *, notes: str | None = None) -> FillingTrialRecord
add_trial() -> FillingGroupSnapshot
calculate_repeatability(trial_ids: Sequence[str]) -> FillingAnalysisRecord
calculate_advance(trial_ids: Sequence[str]) -> FillingAnalysisRecord
set_advance(result_id: str) -> FillingAdvanceProfileRecord
end_group() -> None
list_advance_profiles() -> tuple[FillingAdvanceProfileRecord, ...]
list_history() -> tuple[FillingHistoryEntry, ...]
snapshot() -> FillingGroupSnapshot
```

New devices use the existing neutral `future_adapter` type. Use UUID-suffixed
IDs, injected UTC timestamps, `RunType.FILLING_TRIAL`, workflow name
`filling_trial_group`, workflow version `"1"`, and `RunStatus.COMPLETED` for
neutral completion. A pending Trial's `started_at` is the initial group creation
time or the `Add Trial` click time; `calculated_at` is the successful save time.
Persist a completed workflow step for each trial and analysis, with source Trial
IDs in both configuration and summary metrics.

- [ ] **Step 5: Run service and storage tests**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_filling_service.py tests/test_storage_filling.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the service slice**

```powershell
git add src/coreflow/app/filling.py src/coreflow/app/__init__.py tests/test_filling_service.py
git commit -m "feat(filling): add trial service"
```

### Task 4: Filling Workbench And History UI

**Files:**

- Create: `tests/test_ui_filling.py`
- Create: `src/coreflow/ui/filling_dialogs.py`
- Create: `src/coreflow/ui/filling_history.py`
- Create: `src/coreflow/ui/filling_window.py`
- Modify: `src/coreflow/ui/__init__.py`

- [ ] **Step 1: Write failing operator-path UI tests**

Instantiate `FillingModuleWindow` with a real temporary repository and verify:

```python
def test_filling_window_selects_device_and_calculates_trial(qtbot, repository) -> None:
    repository.save_device(DeviceRecord(device_id="CFM-UI-1", device_type="modbus_rtu"))
    window = FillingModuleWindow(repository=repository)
    qtbot.addWidget(window)
    window.show()

    window.open_device_selector()
    dialog = window.deviceSelectionDialog
    assert dialog is not None and dialog.isVisible()
    assert dialog.deviceCombo.findData("CFM-UI-1") >= 0
    assert not hasattr(window, "deviceIdLineEdit")
    dialog.deviceCombo.setCurrentIndex(dialog.deviceCombo.findData("CFM-UI-1"))
    qtbot.mouseClick(dialog.selectButton, Qt.MouseButton.LeftButton)

    window.controlValveCombo.setEditText("CTRL-A + VALVE-2")
    window.pulseSwitchSpinBox.setValue(125.0)
    window.massPerPulseSpinBox.setValue(0.1)
    window.massUnitEdit.setText("g")
    window.flowPointSpinBox.setValue(100.0)
    window.specifiedMassSpinBox.setValue(1000.0)
    window.targetMassSpinBox.setValue(995.0)
    assert window.standardMassEdit.text() == ""
    window.standardMassEdit.setText("1005")
    qtbot.mouseClick(window.calculateTrialButton, Qt.MouseButton.LeftButton)

    assert window.trialTable.rowCount() == 1
    assert window.trialTable.item(0, 7).text() == "+0.500000%"
    assert window.standardMassEdit.text() == ""
    assert not window.calculateTrialButton.isEnabled()
    qtbot.mouseClick(window.addTrialButton, Qt.MouseButton.LeftButton)
    assert window.currentTrialIndexLabel.text() == "2"
```

Add tests for new-device creation, mode segmentation, advance-mode target
mirroring, exactly-three repeatability selection, at-least-three advance
selection, Set Advance clearing the table into regular mode, profile selection,
history table/detail values, validation messages, and module-state preservation.

- [ ] **Step 2: Run UI tests and confirm missing window API**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_ui_filling.py -q
```

Expected: import failure for `coreflow.ui.filling_window`.

- [ ] **Step 3: Implement device dialogs**

In `filling_dialogs.py`, add these classes and methods:

```text
NewFillingDeviceDialog(service: FillingTrialService, *, parent: QWidget | None = None)
FillingDeviceSelectionDialog(service: FillingTrialService, *, parent: QWidget | None = None)
FillingDeviceSelectionDialog.refresh_devices() -> None
```

The selector is noneditable, the creation dialog explicitly accepts Device ID,
and model, and duplicate IDs show a focused validation message. The service
assigns the neutral `future_adapter` device type.

- [ ] **Step 4: Implement the embedded single-page workbench**

Add `FillingModuleWindow(QDialog)` with `embedded=True` support and these stable
object attributes used by tests and future operator-path maintenance:

```text
deviceValueLabel, changeDeviceButton, controlValveCombo, advanceProfileCombo,
regularModeButton, advanceModeButton, pulseSwitchSpinBox,
massPerPulseSpinBox, massUnitEdit, flowPointSpinBox, specifiedMassSpinBox,
targetMassSpinBox, currentTrialIndexLabel, standardMassEdit,
calculateTrialButton, addTrialButton, trialTable,
calculateRepeatabilityButton, calculateAdvanceButton, setAdvanceButton,
resultTextEdit, historyButton, endGroupButton, statusLabel
```

Use an exclusive `QButtonGroup` for the segmented mode control, a
`QDoubleValidator` on the blank-capable standard-mass `QLineEdit`, and a
`QTableWidget` with checkbox items in column 0. Keep all formulas and writes in
the service. Refresh the complete view from `service.snapshot()` after every
successful action.

- [ ] **Step 5: Implement history dialog and close behavior**

Add `FillingHistoryDialog` in `filling_history.py` with a current-device label, record table, and
read-only detail pane. Populate it from `service.list_history()` and show source
Trial IDs and input/result metrics. On real window close, call
`service.end_group()`; hiding during module switching must not close the group.

- [ ] **Step 6: Run UI and headless tests**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_ui_filling.py tests/test_filling_service.py -q
```

Expected: all tests pass with `QT_QPA_PLATFORM=offscreen` from `tests/conftest.py`.

- [ ] **Step 7: Commit the UI slice**

```powershell
git add src/coreflow/ui/filling_dialogs.py src/coreflow/ui/filling_history.py src/coreflow/ui/filling_window.py src/coreflow/ui/__init__.py tests/test_ui_filling.py
git commit -m "feat(ui): add filling trial workbench"
```

### Task 5: Main Window, Packaging Surface, And Version

**Files:**

- Modify: `src/coreflow/ui/main_window.py`
- Modify: `tests/test_ui_main_window.py`
- Modify: `tests/test_packaging.py`
- Modify: `tests/test_bootstrap.py`
- Modify: `packaging/windows/coreflow_studio.spec`
- Modify: `pyproject.toml`
- Modify: `src/coreflow/__init__.py`

- [ ] **Step 1: Write failing main-shell and version assertions**

Extend `tests/test_ui_main_window.py` to trigger `fillingModuleAction`, assert an
embedded `fillingWindow`, check all three module actions, select a device, switch
away and back, and verify the Filling workbench state is retained while Modbus
and ASIO connection state remains unchanged.

Extend `tests/test_packaging.py::test_windows_packaging_files_are_present` to
assert explicit hidden imports for the filling UI, dialogs, history, and service.
Update `tests/test_bootstrap.py` and every exact version assertion from
0.6.3/0.6.4 to 0.7.0.

- [ ] **Step 2: Run focused shell/version tests and confirm failure**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_ui_main_window.py tests/test_packaging.py tests/test_bootstrap.py tests/test_version_policy.py -q
```

Expected: failures for missing filling action/window and old version 0.6.4.

- [ ] **Step 3: Register the module in MainWindow**

Add:

```python
self.fillingWindow: FillingModuleWindow | None = None
self.fillingModuleAction = modules_menu.addAction("Filling Module")
self.fillingModuleAction.setCheckable(True)
self.fillingModuleAction.triggered.connect(self._show_filling_module)

def _show_filling_module(self) -> None:
    if self.fillingWindow is None:
        self.fillingWindow = FillingModuleWindow(
            repository=self.runtime.repository,
            operator=self.runtime.operator,
            parent=self.moduleStack,
            embedded=True,
        )
        self.moduleStack.addWidget(self.fillingWindow)
    self._set_current_module(self.fillingWindow)
    self.fillingWindow.open_device_selector_if_needed()
```

Update checked-action state and close handling for all three modules. Add
`coreflow.ui.filling_window`, `coreflow.ui.filling_dialogs`,
`coreflow.ui.filling_history`, and `coreflow.app.filling` to the PyInstaller
`hiddenimports` list.

- [ ] **Step 4: Bump both version sources to 0.7.0**

Set `project.version = "0.7.0"` in `pyproject.toml` and
`__version__ = "0.7.0"` in `src/coreflow/__init__.py`.

- [ ] **Step 5: Run focused tests and version hook**

```powershell
conda run -n coreflow-studio python -m pytest tests/test_ui_main_window.py tests/test_packaging.py tests/test_bootstrap.py tests/test_version_policy.py -q
conda run -n coreflow-studio python scripts/check_version_update.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the shell/version slice**

```powershell
git add src/coreflow/ui/main_window.py tests/test_ui_main_window.py tests/test_packaging.py tests/test_bootstrap.py packaging/windows/coreflow_studio.spec pyproject.toml src/coreflow/__init__.py
git commit -m "feat(ui): register filling module"
```

### Task 6: Canonical Documentation And Verification

**Files:**

- Modify: `docs/PRD.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/IMPLEMENTATION_PLAN.md`
- Modify: `docs/TEST_PLAN.md`
- Modify: `docs/DATA_MODEL.md`
- Modify: `docs/SIMULATION.md`
- Modify: `docs/PROTOCOLS.md`
- Modify: `docs/USER_MANUAL.en.md`
- Modify: `docs/USER_MANUAL.zh-CN.md`
- Create: `docs/M15_VERIFICATION.md`

- [ ] **Step 1: Add traceable filling requirements and operator instructions**

Add PRD requirement `PRD-FR-015`, milestone M15, and test IDs:

```text
TP-FILL-CALC-001
TP-FILL-DATA-001
TP-FILL-SVC-001
TP-FILL-UI-001
```

Document the exact formulas, Device ID selection, blank standard-mass behavior,
manual Add Trial behavior, multiple advance profiles, Set Advance transition,
schema v4 entities, and the explicit no-hardware boundary. Add equivalent
English and Chinese operator sequences.

- [ ] **Step 2: Write M15 verification evidence**

`docs/M15_VERIFICATION.md` must list the implemented files, exact test commands,
test counts/results, version check, manual limitations, and confirmation that no
pulse/valve/controller hardware was accessed.

- [ ] **Step 3: Run documentation consistency checks**

```powershell
rg -n "PRD-FR-015|TP-FILL-|M15|filling_trial|灌装" docs
git diff --check
```

Expected: every identifier appears in the intended canonical documents and
`git diff --check` has no output.

- [ ] **Step 4: Run the complete automated suite**

```powershell
conda run -n coreflow-studio python -m pytest -q
conda run -n coreflow-studio python scripts/check_version_update.py
```

Expected: all tests pass and the version check exits 0.

- [ ] **Step 5: Run source UI smoke**

Launch the UI from source with a temporary data root, open the Filling Module,
select/create a device, calculate three trials, calculate repeatability, run an
advance calculation, set it, and inspect history. Close the UI after the smoke
path.

- [ ] **Step 6: Commit documentation and verification**

```powershell
git add docs
git commit -m "docs(filling): document trial workflow"
```

### Task 7: Final Review And Branch Completion

**Files:**

- Review all files changed since commit `34cc7c5`.

- [ ] **Step 1: Request code review**

Use `superpowers:requesting-code-review` with the approved design, this plan,
the complete diff, and test evidence. Resolve every validated blocker with a
new failing regression test before changing production code.

- [ ] **Step 2: Re-run verification after review fixes**

```powershell
conda run -n coreflow-studio python -m pytest -q
conda run -n coreflow-studio python scripts/check_version_update.py
git diff --check
git status --short
```

Expected: tests and checks pass; status contains only intentionally uncommitted
changes, or is clean after checkpoint commits.

- [ ] **Step 3: Finish the development branch**

Use `superpowers:verification-before-completion`, then
`superpowers:finishing-a-development-branch`. Report commit hashes, exact test
results, source UI smoke evidence, remaining hardware exclusions, and final
working-tree status.

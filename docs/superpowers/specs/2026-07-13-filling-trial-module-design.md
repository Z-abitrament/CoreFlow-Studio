# Filling Trial Module Design

**Date:** 2026-07-13

**Status:** User-approved design

**Target milestone:** M15, post-M12 filling trial module

**Target version:** 0.7.0

## Purpose

Add an independent Filling Module for operator-run filling trials. The module
records the configured pulse-output context and standard-scale mass, calculates
filling error and three-trial repeatability, derives valve-closing advance mass
from selected trials, and retains device-linked history.

The first version is calculation and record keeping only. The operator runs the
physical filling cycle outside CoreFlow Studio and enters the standard-scale
mass afterward.

## Confirmed Scope

The first version must:

- Appear as an independent module in the main `Modules` menu.
- Bind every run, trial, analysis result, and advance profile to an existing
  flowmeter Device ID from the shared device store.
- Let the operator explicitly create a shared device from the device-selection
  dialog when the needed Device ID does not exist.
- Prompt for a Device ID when the Filling Module is first opened in an
  application session instead of accepting an inline free-text Device ID.
- Support one flow point with any number of manually appended trials in a trial
  group.
- Support a regular error/repeatability mode and an advance-calculation mode.
- Store each calculated trial immediately and keep completed history after the
  operation window closes.
- Restore the last calculated trial's configuration for the selected device
  when the module is opened again.
- Leave the current trial's standard-mass input empty whenever a new trial is
  prepared or the module is reopened.
- Store multiple reusable advance profiles for the same flowmeter, flow point,
  and specified mass.
- Distinguish advance profiles with a required operator-entered control/valve
  combination label.
- Store every repeatability and advance calculation in history with its source
  Trial IDs.

## Out Of Scope

The first version does not:

- Read, count, calculate, or display pulse totals.
- Read live pulse output.
- Detect flow start, valve-close command time, or final valve closure.
- Control a valve or control device.
- Send target mass, advance mass, pulse settings, or other values to hardware.
- Open or reuse a Modbus or ASIO/IIS connection.
- Define production pulse electrical characteristics or a control-device
  protocol.
- Apply hidden pass/fail thresholds.

Future pulse acquisition and control-device writes require separate protocol,
simulation, safety, write-guard, audit, and hardware-validation designs.

## Terminology

- **Device ID:** Stable business identity of the flowmeter. It is selected from
  the shared device store and is not a Modbus Unit ID or COM-port-derived value.
- **Control/valve label:** Operator-entered text identifying the external
  controller and valve combination. It is not another Device ID.
- **Specified mass:** The desired final filling mass.
- **Target mass:** The mass threshold configured in the external controller for
  deciding when to close the valve.
- **Standard mass:** The final mass measured by the standard scale after one
  filling trial.
- **Advance mass:** Estimated mass delivered after the controller reaches its
  close threshold and before the valve finishes closing.
- **Trial group:** One run containing trials with one locked configuration
  snapshot.
- **Advance profile:** An immutable, reusable record created when the operator
  sets a calculated advance result.

## Selected Architecture

The Filling Module is independent from the Modbus Module. It reuses shared
device identity, run metadata, analysis-result storage conventions, and Qt
module-shell patterns, but it does not import Modbus UI state, connection state,
or protocol logic.

### Presentation Layer

Create an embedded `FillingModuleWindow` under `coreflow/ui`. It is created on
demand by `MainWindow`, inserted into the existing module stack, and retained
while the user switches between modules.

The UI collects inputs and renders records. It does not implement formulas or
write directly to SQLite.

### Application Layer

Create a headless `FillingTrialService` under `coreflow/app`. It owns:

- Device selection and shared-device creation coordination.
- Trial-group lifecycle.
- Input validation and configuration locking.
- Trial calculation and persistence.
- Repeatability selection and calculation.
- Advance selection, calculation, setting, and profile creation.
- History queries and last-used-configuration queries.

New Device records created from this module use the existing neutral
`future_adapter` device type because this milestone does not define a protocol
adapter. Duplicate creation uses insert-only repository behavior.

### Analysis Layer

Create focused filling calculation functions under `coreflow/analysis`. They
accept explicit values and return immutable result objects. They have no Qt,
storage, or protocol dependencies.

### Storage Layer

Extend the existing repository rather than adding UI-level SQL. Reuse
`run_sessions`, `workflow_steps`, and `analysis_results`, and add dedicated
tables for queryable filling trials and reusable advance profiles.

Each saved Trial and each repeatability/advance calculation has a completed
workflow step. Add a neutral `completed` step status for the same reason the run
model needs neutral completion.

## Device Selection

On the first entry into the Filling Module during an application session:

1. Open a modal device-selection dialog.
2. Populate the selector with `StorageRepository.list_devices()` results.
3. Require the operator to select one Device ID before opening a trial group.
4. Offer `New Device...` as an explicit secondary action.
5. Reject duplicate Device IDs in the creation dialog instead of silently
   updating an existing device.
6. Save the new item as a shared `DeviceRecord`, then select it.

The workbench header displays the selected Device ID and a `Change Device...`
action. Switching back to the already-created Filling Module in the same
application session retains its current selection. Changing devices requires
the current trial group to be ended first.

Current Modbus profile creation writes matching shared `DeviceRecord` rows. The
v3-to-v4 migration also backfills legacy orphan Modbus profile IDs so every
existing flowmeter profile can appear in the Filling Module selector.

## Single-Page Workbench

The approved UI is a single-page workbench modeled on the operator rhythm of
the existing Modbus Repeatability dialog.

### Header

The header contains:

- Current Device ID.
- `Change Device...`.
- Control/valve combination label selector and `New Label...` action.
- Advance-profile selector. Multiple profiles may share a label and are
  distinguished by flow point, specified mass, advance mass, and timestamp.
- `History...`, filtered to the current Device ID.

### Mode Control

Use a two-option segmented control:

- `Regular Test`
- `Calculate Advance`

Changing mode is allowed only before the first trial in a group is calculated.

### Configuration

Display these fields directly on the workbench:

- Pulse frequency switch point, stored in Hz.
- Mass per pulse.
- Mass unit.
- Flow point, stored and displayed in g/s.
- Specified mass.
- Target mass.

The selected mass unit applies to mass per pulse, specified mass, target mass,
standard mass, mean standard mass, advance mass, and corrected target mass. The
first version stores the unit string and requires exact unit equality when
combining trials; it performs no automatic unit conversion.

In `Calculate Advance` mode, target mass is read-only and follows specified
mass until an advance is set. In `Regular Test` mode, target mass is editable or
loaded from a selected advance profile.

When the first trial is calculated, all group configuration fields and the mode
are locked. A different flow point, specified mass, target mass, pulse setting,
unit, label, or mode requires a new trial group.

For a selected device, initial field values come from that device's most recent
calculated filling trial. Unsaved draft input is not treated as the last-used
configuration.

### Current Trial

The current-trial area contains:

- Current trial index.
- An empty standard-mass input.
- Primary action `Calculate Current Trial Error`.

Successful calculation saves the trial, clears the standard-mass field, and
leaves no pending next trial. There is no separate `Save Trial` action.

`Add Trial` explicitly creates the next pending trial with an empty
standard-mass field, matching the requested manual append behavior. The first
group starts with pending Trial 1, and only one pending current trial can exist
at a time.

### Trial Table

Show these columns:

- Selection checkbox.
- Trial index.
- Trial timestamp.
- Flow point.
- Specified mass.
- Target mass.
- Standard mass.
- Percent error.
- Status.

The table only combines trials from the current group. Historical trials are
available in the history window.

### Analysis Actions

Regular mode exposes `Calculate Repeatability`. The operator must select exactly
three calculated trials from the current group, and their trial indexes must be
consecutive.

Advance mode exposes `Calculate Advance`. The operator must select at least
three calculated trials from the current group. The selected trials need not be
consecutive. Each calculation creates a new immutable history result, even when
the operator recalculates from a different selection.

After an advance calculation, a result area displays:

- Selected Trial IDs.
- Mean standard mass.
- Specified mass.
- Advance mass.
- Corrected target mass.
- Calculation time.

`Set Advance` is enabled only for the currently previewed stored advance result.

## Calculations

All values in one calculation use the same selected mass unit.

### Trial Error

```text
trial_error_percent =
    (standard_mass - specified_mass) / specified_mass * 100
```

The target mass is deliberately excluded from the error denominator. Trial
error always describes the final standard-scale mass relative to the desired
specified mass.

### Repeatability

For exactly three selected consecutive trial errors:

```text
mean_error = (e1 + e2 + e3) / 3

repeatability_stddev =
    sqrt(((e1 - mean_error)^2
        + (e2 - mean_error)^2
        + (e3 - mean_error)^2) / (3 - 1))
```

This is the sample standard deviation, consistent with the Modbus manual
repeatability workflow.

### Advance Mass

For at least three selected trials:

```text
mean_standard_mass = sum(selected standard masses) / selected trial count
advance_mass = mean_standard_mass - specified_mass
corrected_target_mass = specified_mass - advance_mass
```

Negative advance mass is valid. It means the selected trials averaged below the
specified mass, so the corrected target mass increases. The UI must preserve and
display the sign.

Calculations retain full runtime precision. Display formatting does not change
stored values, and history retains both original input values and results.

## Set-Advance Transition

`Set Advance` performs one atomic service operation:

1. Verify the selected advance result still belongs to the active group and
   current Device ID.
2. Create a new immutable advance profile with its own profile ID.
3. Link the new profile to the immutable source advance result. History derives
   whether a calculation was set by the presence of this profile; the source
   analysis row is not rewritten.
4. Complete the uncorrected advance-calculation run.
5. Create a new regular-test run with the same Device ID, label, pulse settings,
   unit, flow point, and specified mass.
6. Set the new run's target mass to the corrected target mass.
7. Clear the Trial table and prepare an empty Trial 1.

If any persistence step fails, the transaction rolls back and the UI remains in
the original advance-calculation group.

This transition prevents uncorrected and corrected target-mass trials from
being selected together for repeatability.

## Lifecycle And State

Add a neutral `completed` value to the shared run-status model. Using `passed`
would imply an acceptance threshold that this workflow does not define.

Trial-group states are:

- `pending`: group configured but no trial calculated.
- `running`: at least one trial calculated and more work may be appended.
- `completed`: operator ends a nonempty group, calculates and sets an advance,
  changes devices after ending the group, or closes the application with saved
  trials.
- `canceled`: operator ends or closes an empty group.
- `error`: an unrecoverable service or persistence error prevents continuation.

The UI starts and persists a pending group when the operator first invokes
Trial calculation with a valid configuration. The Trial and run/step status
update then commit atomically. Set Advance also creates its corrected pending
group atomically. A validation failure before group creation remains an
unpersisted UI draft.

Pending current-trial input exists only in UI memory. Closing discards it.
Calculated trials are never discarded by normal close behavior.

## Data Model

Increase `SCHEMA_VERSION` from 3 to 4 and add an explicit v3-to-v4 migration
test. The migration creates the new tables and indexes, preserves existing
data, backfills orphan Modbus profile Device IDs, records version 4 only after
success, and rejects databases from a future schema version.

### Shared Run Records

Add `filling_trial` to `RunType`. Each trial group creates one `RunSession` with:

- `run_type=filling_trial`
- `workflow_name=filling_trial_group`
- `workflow_version="1"`
- Device ID, operator/source, timestamps, status, software version, and notes.
- A complete group-configuration snapshot.

Repeatability and advance calculations create `AnalysisResult` rows with result
types `filling_repeatability` and `filling_advance`.

### Filling Trial Records

Add `filling_trial_records` with:

- `trial_id` primary key.
- `run_id` foreign key to `run_sessions`.
- `device_id` foreign key to `devices`.
- Trial index and status.
- Mode and control/valve label.
- Pulse frequency switch point, mass per pulse, mass unit, flow point,
  specified mass, and target mass.
- Standard mass and percent error.
- Full configuration snapshot JSON.
- `started_at` and `calculated_at` timestamps. `started_at` is the time pending
  Trial 1 or a manually added Trial is created; `calculated_at` is the
  successful calculation/save time.
- Notes.

The unique key `(run_id, trial_index)` prevents accidental duplicate indexes.

### Advance Profiles

Add `filling_advance_profiles` with:

- `profile_id` primary key.
- Device ID foreign key.
- Source advance analysis-result ID.
- Control/valve label.
- Pulse and mass parameter snapshot.
- Flow point, specified mass, advance mass, and corrected target mass.
- Source Trial IDs JSON.
- Full configuration snapshot JSON and notes.
- Created timestamp.

Profiles are immutable. Recalculating or setting another advance creates a new
profile and does not overwrite earlier values.

### Analysis Metrics

Repeatability metrics include:

- Source Trial IDs and trial indexes.
- Source trial time range.
- Three trial errors.
- Mean error.
- Sample standard deviation.

Advance metrics include:

- Source Trial IDs and trial indexes.
- Source standard masses.
- Selected trial count.
- Mean standard mass.
- Specified mass.
- Advance mass.
- Corrected target mass.

Whether an advance calculation was set is derived from an immutable
`filling_advance_profiles` row whose source analysis-result ID points to the
calculation.

## History

`History...` opens a current-device history window. It shows four record types:

- Filling Trial.
- Filling Repeatability.
- Filling Advance Calculation.
- Filling Advance Profile Set.

The table shows timestamp, record type, Run/Trial/Result ID, flow point,
specified mass, target or corrected target mass, result summary, label, and
notes. A detail pane exposes the full parameter snapshot, formulas' input
values, source Trial IDs, calculation timestamps, and linked advance profile.

The current Device ID is a locked default filter so records from different
flowmeters cannot be mixed accidentally.

## Validation

Before calculation or group creation:

- Device ID must reference an existing shared device.
- Control/valve label must not be empty.
- Pulse frequency switch point must be finite and greater than zero.
- Mass per pulse must be finite and greater than zero.
- Mass unit must not be empty.
- Flow point must be finite and greater than zero.
- Specified mass, target mass, and standard mass must be finite and greater
  than zero.
- Corrected target mass must be finite and greater than zero.

Before repeatability or advance calculation, all source trials must be
calculated records from the active group and share the same Device ID, mode,
label, flow point, specified mass, target mass, unit, pulse settings, and
configuration snapshot.

No acceptance threshold or automatic pass/fail decision is introduced.

## Error Handling And Atomicity

- Field validation failures create no history row and are shown next to the
  relevant input or as one focused validation message.
- Trial calculation and trial persistence are one transaction. A storage
  failure leaves the standard-mass input intact for retry.
- Repeatability and advance results are not displayed as saved until their
  analysis rows are committed.
- Set-advance transition is atomic as described above.
- History-query failure is isolated to the history view and does not crash the
  workbench.
- Module failures do not alter Modbus or ASIO/IIS module state.

## Test Strategy

### Calculation Tests

- Positive, zero, and negative trial error.
- Sample standard deviation for three known errors.
- Positive and negative advance mass.
- Corrected target validation.
- Nonfinite, zero, and negative inputs.
- Stored values are unaffected by display formatting.

### Service Tests

- Explicit existing-device selection and explicit new-device creation.
- Duplicate Device ID rejection.
- Last calculated trial configuration restoration per Device ID.
- Standard mass is never restored into a new pending trial.
- First calculated trial locks group configuration.
- Trial calculation persists immediately; `Add Trial` alone prepares the blank
  next trial.
- Manual trial append preserves earlier records.
- Repeatability requires exactly three consecutive trials.
- Advance calculation requires at least three trials but not consecutive ones.
- Cross-device and mismatched-snapshot selections are rejected.
- Multiple advance calculations and profiles do not overwrite one another.
- Set Advance completes the old group and creates a corrected regular group.
- Closing a nonempty group completes it; closing an empty group cancels it.
- Persistence failure leaves retryable UI/service state.

### Storage Tests

- Fresh v4 schema creation.
- Existing v3 database migration to v4 without data loss.
- Device foreign keys, unique trial indexes, CRUD, filtering, and ordering.
- Multiple same-condition advance profiles remain independently queryable.
- Trial, repeatability, advance, and profile records retain source IDs and
  configuration snapshots.

### Qt Tests

- Main `Modules` menu opens the embedded Filling Module.
- Initial device-selection dialog lists shared IDs and has no inline Device ID
  entry on the workbench.
- New-device dialog creates and selects a shared device.
- Module switching preserves current workbench state.
- Both modes expose the correct controls and target-mass behavior.
- Standard-mass input starts blank after opening and after every calculation.
- Buttons enable only when their selection and state requirements are met.
- Set Advance clears the old table and shows corrected target mass in a new
  regular group.
- History table and detail pane show persisted values and source IDs.

### Regression And Packaging Tests

- Existing full test suite remains green.
- Version-policy test verifies 0.7.0 in both version sources.
- PyInstaller analysis/import smoke includes the new UI and service modules.
- No real serial, pulse, valve, or control-device hardware is accessed.

## Documentation Changes During Implementation

Update these canonical documents in the implementation slice:

- `docs/PRD.md`: add Filling Trial scope and a new functional requirement.
- `docs/ARCHITECTURE.md`: add the independent Filling Module boundary and data
  flow.
- `docs/IMPLEMENTATION_PLAN.md`: add M15 Filling Trial Module.
- `docs/TEST_PLAN.md`: add filling calculation, service, storage, and UI cases.
- `docs/DATA_MODEL.md`: add filling trial and advance-profile entities.
- `docs/SIMULATION.md`: document hardware-free manual-input test scenarios.
- `docs/PROTOCOLS.md`: record that pulse acquisition and controller writes are
  future contracts and are absent from this milestone.
- `docs/USER_MANUAL.en.md` and `docs/USER_MANUAL.zh-CN.md`: document the
  operator workflow.
- `docs/M15_VERIFICATION.md`: record implementation evidence and remaining
  hardware exclusions.

Synchronize `pyproject.toml` and `src/coreflow/__init__.py` to version `0.7.0`.

## Acceptance Criteria

The module is complete for this milestone when:

- An operator can open the independent Filling Module and select or explicitly
  create a shared flowmeter Device ID.
- The operator can run any number of manual filling trials and calculate each
  trial's error from a newly entered standard mass.
- The operator can calculate repeatability from exactly three consecutive
  trials.
- The operator can calculate advance mass from at least three selected trials,
  store the calculation in history, and set it as a new reusable profile.
- Setting an advance creates a corrected regular-test group without mixing old
  and new target-mass trials.
- Multiple advance profiles for one flowmeter and filling condition remain
  available and traceable.
- Reopening restores the last calculated trial configuration but never restores
  a standard-mass value.
- All records are tied to Device ID, run, source Trial IDs, timestamps, software
  version, and configuration snapshots.
- Automated calculation, service, migration, repository, UI, regression, and
  packaging-import tests pass without physical hardware.

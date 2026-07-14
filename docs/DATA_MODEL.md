# Data Model

## Summary
CoreFlow Studio stores structured metadata and results in SQLite and stores large or external artifacts as files. Every calibration, test, and experiment run must be traceable from device identity and configuration to raw data, processed results, reports, and audit logs.

The current database version is schema v4. Schema v4 adds queryable Filling
Trial records and immutable filling advance profiles while preserving the
shared device, run, workflow-step, and analysis-result provenance model.

## Storage Principles
- SQLite is the local source of truth for metadata, status, metrics, and artifact references.
- Large raw captures and generated files are stored outside SQLite.
- Every artifact file must have a database record.
- Every workflow run must be reproducible or explain why it cannot be reproduced.
- Schema changes must be handled through explicit migrations after the first implementation.

## Suggested User Data Layout
On Windows, store application data under a user-writable application data directory.

Suggested layout:

```text
CoreFlowStudioData/
  coreflow.sqlite
  artifacts/
    runs/
      2026/
        06/
          RUN-20260605-000001/
            raw/
            processed/
            exports/
            reports/
            logs/
  simulator/
    scenarios/
    replays/
  config/
    register_maps/
    workflow_templates/
```

The exact base directory should be configurable.

## Core Entities

### Device
Represents a physical or simulated flowmeter transmitter.

Fields:

- Device ID.
- Device type: simulated, Modbus RTU, or future adapter.
- Serial number when known.
- Model.
- Firmware version.
- Hardware version.
- Protocol address or unit ID.
- Last known connection metadata.
- Created and updated timestamps.

For Filling Trial records, Device ID is the stable identity of the flowmeter
only. It must not be used as the identity of an external controller, valve,
Modbus unit, or COM port. Explicitly created filling-only devices use the
neutral `future_adapter` device type until a real adapter contract exists.

### Run Session
Represents one calibration, factory test, stability test, or experiment run.

Fields:

- Run ID.
- Run type.
- Workflow name and version.
- Device ID.
- Operator or automation source.
- Start time and end time.
- Status: pending, running, completed, passed, failed, canceled, error. The
  neutral `completed` value is used when a workflow has no pass/fail threshold.
- Configuration snapshot.
- Register-map, workflow-template, simulator-scenario, and threshold snapshot references when used.
- Software version.
- Notes.

### Workflow Step
Represents one step inside a run.

Fields:

- Step ID.
- Run ID.
- Step name.
- Step type.
- Start time and end time.
- Status.
- Input configuration.
- Output summary.
- Error message if failed.

### Measurement Sample
Low-rate samples may be stored in SQLite if they are small. High-rate or long-duration samples must be stored in artifact files.

Fields for small samples:

- Sample ID.
- Run ID.
- Step ID.
- Timestamp.
- Mass flow.
- Volume flow.
- Density.
- Temperature.
- Status flags.
- Source channel.

### Variable Sample
Stores timestamped values from configured Modbus or simulator logical variables, especially low-rate variables used by operator workflows.

Fields:

- Sample ID.
- Device ID.
- Run ID and step ID when the sample belongs to a workflow.
- Variable name, such as `mass_acc`, `delta_t`, `zero_offset`, or `k_factor`.
- Timestamp.
- Value as typed JSON.
- Unit.
- Source channel.
- Metadata copied from the register map or simulator parameter definition.

### Analysis Result
Stores calculated outputs from calibration, error analysis, stability analysis, signal processing, or ML inference.

Fields:

- Result ID.
- Run ID.
- Step ID.
- Result type.
- Input artifact references.
- Algorithm name and version.
- Configuration snapshot.
- Summary metrics.
- Pass/fail decision when applicable.

Filling analyses use `filling_repeatability` and `filling_advance` result
types. Their pass/fail decision is null because M15 defines no acceptance
threshold.

### Calibration Record
Stores calibration-specific outcomes.

Fields:

- Calibration ID.
- Run ID.
- Device ID.
- Reference points.
- Measured values.
- Calculated coefficients or parameter changes.
- Acceptance thresholds used.
- Preview or applied status.
- Write audit references.
- Zero calibration before/after `zero_offset`, `delta_t`, timestamps, control parameter, and completion status.
- K factor calibration pre-operation snapshot values, flow-rate segment timestamps, instantaneous flow sample, accumulated-mass before/after, measured mass delta, standard mass, current K factor, corrected K factor, mean flow, write request/apply/verify status, and readback value when available.
- Manual repeatability test mode, configured target-flow ranges, per-trial flow-segment timestamps, third-second instantaneous flow `v1`, mean flow `v_mean`, accumulated-mass before/after values, standard masses, percent errors, per-range repeatability standard deviations, and whether the saved summary came from fixed three-flow-range mode or the appendable single-flow-range mode.

### Modbus Device Profile
Stores operator-maintained Modbus device context for the standalone Modbus
module.

Fields:

- Profile ID.
- Stable Device ID entered by the operator. It is independent from the Modbus
  RTU unit ID and must not be a simple numeric bus address.
- Display name.
- Device model.
- Tube model.
- Transmitter model.
- Connection settings snapshot, including serial port and Modbus unit ID.
- Register-map configuration snapshot.
- Notes.
- Created and updated timestamps.

### Modbus Test Session
Groups flexible Modbus operation attempts for one device ID.

History JSON export includes Modbus test sessions referenced by exported
operation attempts and trial records. Import clears source profile references so
session rows do not depend on a profile that may not exist in the target
database; when importing for the current device, the session Device ID and
metadata are retargeted to that current Device ID.

Fields:

- Session ID.
- Device ID.
- Profile ID when available.
- Operator.
- Status.
- Start and end time.
- Device metadata snapshot.
- Register-map snapshot.
- Notes.

### Modbus Operation Attempt
Stores every Modbus operation attempt that should be reviewable later.

Fields:

- Attempt ID.
- Session ID.
- Run ID when the attempt belongs to a stored run.
- Device ID.
- Operation type, such as `zero_calibration`,
  `k_factor_calibration_capture`, `k_factor_calibration`,
  `manual_error_repeatability_trial`, or `manual_error_repeatability`.
- Status.
- Start and end time.
- Operator.
- Device metadata snapshot.
- Register-map snapshot.
- Raw artifact ID when available.
- Summary metrics.
- Notes.

### Modbus Trial Record
Stores each manual error/repeatability trial independently from the final
summary so rejected, diagnostic, and repeated trials can still be analyzed.

Fields:

- Trial ID.
- Session ID.
- Attempt ID.
- Run ID when available.
- Device ID.
- Flow point.
- Trial index.
- Trial status: accepted, rejected, diagnostic, or future operator states.
- Flow-rate variable name and flow-accumulator variable name used for the
  trial.
- K factor variable name used for the trial when available.
- Original K factor value automatically read before the flow segment when
  available.
- Accumulated mass before and after.
- Measured mass delta.
- Standard mass.
- Percent error.
- Mean flow.
- Instant flow.
- Flow segment timestamps.
- Raw Modbus polling artifact ID.
- Optional flow-rate sample CSV artifact ID when all-flow-sample recording is
  enabled.
- Optional flow-rate sample count.
- Device metadata snapshot.
- Notes.

The manual error/repeatability workflow keeps every trial, including extra,
rejected, and diagnostic trials. A standard final-K preview is derived only from
operator-selected accepted data: three flow points, one consecutive three-trial
window per flow point, and 9 selected trials total. Stored final-K metrics should
include:

- selected flow-point count and selected trial count.
- selected trial indexes and selected trial percent errors per flow point.
- per-flow-point measurement error, calculated as the arithmetic mean of the
  three selected trial percent errors.
- per-flow-point repeatability standard deviation, calculated as sample
  standard deviation of the same three selected trial percent errors.
- final `average_error`, calculated as `(max(measurement_errors) +
  min(measurement_errors)) / 2`.
- per-flow-point adjusted error, calculated as `measurement_error -
  average_error`.
- per-flow-point intermediate K, calculated as `original_k / (1 +
  measurement_error_percent / 100)`.
- final `new_k_factor`, calculated as `(max(intermediate_k_values) +
  min(intermediate_k_values)) / 2`.
- the original K factor value and K factor variable name used by the selected
  trials.
- optional device-analysis provenance fields when the final-K result is
  generated from `Current Device Analysis`, including
  `analysis_source=current_device_analysis`, source repeatability run IDs,
  saved comparison variable names, and a report artifact ID for the text
  report.
- optional write outcome fields when the operator applies the final K to the
  connected device: `write_requested`, `write_status`, `write_verified`,
  `readback_k_factor`, and `audit_id`.

### Filling Trial Record
Stores each manually calculated filling trial in `filling_trial_records`. One
trial belongs to one `filling_trial` run and one shared flowmeter Device ID.

Fields:

- Trial ID, run ID, and flowmeter Device ID.
- Trial index and trial status.
- Mode: regular or advance calculation.
- Control/valve label identifying the external controller and valve
  combination; this is separate from Device ID.
- Pulse frequency switch point in Hz, mass per pulse, and mass unit.
- Flow point in g/s, specified mass, and target mass.
- Operator-entered standard-scale mass and calculated percent error.
- Full immutable group-configuration snapshot.
- `started_at`, the UTC time pending Trial 1 or an explicitly added trial was
  prepared.
- `calculated_at`, the UTC time the calculated trial was saved.
- Notes.

The regular error is `(standard_mass - specified_mass) / specified_mass * 100`.
The table stores no pulse total. `UNIQUE(run_id, trial_index)` prevents duplicate
indexes, and the device/calculated-time index supports current-device history
ordered by UTC calculation time.

### Filling Advance Profile
Stores each reusable, immutable result of `Set Advance` in
`filling_advance_profiles`. One flowmeter may have multiple profiles, including
profiles with the same flow point and specified mass. The control/valve label,
full parameter snapshot, source result, source trials, and creation time keep
them distinguishable and traceable.

Fields:

- Profile ID and flowmeter Device ID.
- Source `filling_advance` analysis-result ID.
- Control/valve label.
- Pulse frequency switch point, mass per pulse, and mass unit.
- Flow point, specified mass, signed advance mass, and corrected target mass.
- Source Trial IDs.
- Full configuration snapshot and notes.
- UTC creation timestamp.

Profiles are append-only from the application perspective. Creating another
profile never updates or deletes an earlier profile. Indexes support descending
current-device creation history and lookup by source analysis-result ID.

### Filling Trial Provenance And Transitions
Every filling group reuses a `run_sessions` row with
`run_type=filling_trial`, `workflow_name=filling_trial_group`, and workflow
version `1`. Each calculated trial and each repeatability/advance calculation
has a completed workflow step. Analysis metrics retain source Trial IDs, source
indexes and timestamps, original input values, result values, full
configuration snapshots, and notes where supplied.

Current-device filling history exposes four record categories:

- Filling Trial.
- Filling Repeatability.
- Filling Advance Calculation.
- Filling Advance Profile Set.

Repeatability records retain exactly three consecutive source Trial IDs, their
errors, mean error, and sample standard deviation. Advance records retain at
least three source Trial IDs, source standard masses, mean standard mass,
specified mass, signed advance mass, and corrected target mass. Neither result
stores a hidden pass/fail threshold.

Repository transactions keep related rows atomic:

- Trial calculation creates or updates the group run and inserts its completed
  step and trial together.
- Repeatability or advance calculation inserts its completed step and immutable
  analysis result together.
- `Set Advance` inserts the immutable profile, completes the old advance run,
  and inserts the corrected pending regular run together. A failure rolls back
  the whole transition, so old and corrected trial groups cannot be mixed.

Foreign keys bind trials to runs and devices, including the matching
`(run_id, device_id)` pair, and bind advance profiles to devices and source
analysis results. Database initialization enables foreign-key enforcement.

### Schema v3 To v4 Migration
Initialization creates schema v4 for new databases and migrates schema v3 in
one transaction. The migration creates both filling tables and their indexes,
preserves existing rows, converts legacy timestamps used for backfill to UTC,
and inserts missing shared `devices` rows for orphan
`modbus_device_profiles.device_id` values as `modbus_rtu`. It records migration
version 4 only after all steps succeed and rejects databases newer than the
supported schema version.

### Artifact
Represents a file linked to a run, step, or result.

Fields:

- Artifact ID.
- Run ID.
- Step ID when applicable.
- Artifact type: raw, processed, export, report, log, replay, config snapshot.
- File path.
- File format.
- Size.
- Checksum if available.
- Created timestamp.

### Audit Log
Records safety-sensitive actions.

Fields:

- Audit ID.
- Timestamp.
- Actor.
- Action type.
- Workflow state at time of action.
- Device ID.
- Run ID when applicable.
- Target, such as register or parameter name.
- Previous value when available.
- New value when available.
- Dry-run flag.
- Validation result.
- Protocol request reference when a real or simulated write is transmitted.
- Result.
- Error message if failed.

Audit entries are required for attempted parameter writes, rejected writes, dry-run writes, simulator writes, and successful hardware writes. Audit records should be append-only from the application perspective.

### Configuration Snapshot
Stores or references configuration used to interpret a run.

Fields:

- Snapshot ID.
- Snapshot type: register map, workflow template, simulator scenario, thresholds, report template, or processing configuration.
- Name and version.
- Content hash when available.
- Artifact reference or serialized content.
- Created timestamp.

Runs and analysis results should reference configuration snapshots so future review can determine which assumptions produced a result.

## File Formats

Initial formats:

- CSV for simple tabular exports.
- JSON for configuration snapshots, simulator scenarios, and workflow templates.
- JSON for standalone Modbus calibration-history transfer packages between lab PCs.
- JSON for ASIO/IIS loopback diagnostics.
- SQLite for metadata and small structured results.
- Plain text or JSON lines for diagnostic logs.
- PDF, HTML, or DOCX can be considered for reports after report requirements are known.

For high-rate or long-duration data, choose between CSV and Parquet during implementation. Use CSV first unless measured performance requires Parquet. ASIO/IIS frame captures may use `.npy` for deterministic numeric arrays during hardware acceptance, with a JSON sidecar for configuration and metrics.

## Run Directory Naming
Use stable run IDs and human-sortable folders.

Suggested format:

```text
RUN-YYYYMMDD-NNNNNN
```

The run ID must be unique even if multiple runs start in the same second.

## Data Integrity Requirements
- A run cannot be marked complete until required artifacts are written or explicitly marked unavailable.
- SQLite records must reference relative paths from the configured data root when possible.
- Missing artifacts must produce a clear diagnostic.
- Raw data used for final results must not be overwritten by later processing.
- Processed outputs must reference the raw artifact and processing configuration used to create them.
- Calibration and write-capable runs must reference the register map, thresholds, and workflow template used for validation.
- Variable samples linked to a run must reference an existing device, run, and workflow step.
- Audit log entries must not be deleted by normal run cleanup or report export actions.
- Modbus operation attempts and trial records must keep both the device ID
  reference and the device metadata snapshot present at the time of the
  operation.
- Modbus raw polling artifacts must be linked from the operation attempt or
  trial record that produced them.
- Filling trials must reference the same Device ID as their run; advance
  profiles must reference an advance analysis result owned by the same device
  and source run.
- Filling trial, analysis, and profile timestamps are stored as UTC-aware ISO
  values. UI-local formatting must not rewrite persisted UTC provenance.
- A Set Advance failure must leave no partial profile, completed old run, or
  corrected new run.

## Backup And Portability
v1 is local-first. A complete run package should be portable by copying:

- The relevant SQLite records.
- The run artifact folder.
- Configuration snapshots.
- Report/export files.

Full backup and restore tools are future work.

Standalone Modbus test records can also be moved between PCs with a
module-specific JSON package. That package stores completed Modbus run
metadata, workflow steps, analysis metrics, notes, device metadata, artifact
metadata and file content, operation attempts, and repeatability trial records
for zero calibration, K factor calibration, and repeatability records.
Repeatability trial-sample CSV artifacts are included so saved flow and
extra-variable curves remain viewable after import on another PC. Import remains
compatible with the earlier run/analysis package shape: it skips identical run
IDs and preserves conflicting imported runs under a new imported run ID so
independent test PCs do not overwrite each other's local history. Older packages
that contain artifact metadata but no embedded file content can still be
imported, but missing artifact files remain unavailable. The package metadata
records the selected operation filter and optional started-at time range used
for export. Excel export is reserved for a later reporting/export pass; JSON
remains the portable interchange format for now.

## Known Unknowns
- Final report file format.
- Required retention period.
- Required checksum or signing policy.
- Whether production systems require operator login or electronic signature.
- Whether customer or regulatory systems require a specific export schema.
- Whether low-rate variable samples require retention outside workflow-linked runs for routine diagnostics.

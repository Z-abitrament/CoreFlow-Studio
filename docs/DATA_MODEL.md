# Data Model

## Summary
CoreFlow Studio stores structured metadata and results in SQLite and stores large or external artifacts as files. Every calibration, test, and experiment run must be traceable from device identity and configuration to raw data, processed results, reports, and audit logs.

The current database version is schema v6. Schema v5 added retireable Filling
Trial records and immutable filling advance profiles. Schema v6 adds a
versioned Modbus register-map catalog and Device Profile bindings while
preserving inline and historical map snapshots.

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
- Bound register-map ID and version.
- Effective register-map configuration snapshot retained for compatibility
  and recovery.
- Notes.
- Created and updated timestamps.

### Modbus Register Map Catalog Entry

Stores one immutable reusable register-map version independently from devices.

Fields:

- Stable register-map ID.
- Version.
- Display name.
- Source: official, custom, legacy, or template.
- Normalized-content SHA-256 checksum.
- Complete register-map JSON.
- Created and updated timestamps.

The primary key is `(register_map_id, version)`. One version may be referenced
by multiple Device Profiles. A client update may add an official version but
must reject different content under an existing ID/version pair.

### Schema v5 To v6 Migration

Initialization creates the catalog and adds nullable map-ID/version bindings
to existing Device Profiles. Every unbound profile with a valid inline map is
assigned a deterministic legacy catalog entry derived from its normalized
content checksum. Identical maps are deduplicated. Inline profile maps and all
run/session/attempt snapshots remain unchanged.

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
  `modbus_zero_monitor`, `k_factor_calibration_capture`, `k_factor_calibration`,
  `manual_error_repeatability_trial`, or `manual_error_repeatability`.
- Status.
- Start and end time.
- Operator.
- Device metadata snapshot.
- Register-map snapshot.
- Raw artifact ID when available.
- Summary metrics.
- Notes.

### Modbus Zero Monitor Record

M16 reuses `ModbusOperationAttempt`, `AnalysisResult`, and `Artifact`; it does
not add a new SQLite table. A `modbus_zero_monitor` attempt with at least one
logical poll row references one long-form read-only capture and one reproducible
analysis result. Pre-start failures and runs stopped before the first poll have
no artifact or analysis result.

Each monitor session that passes pre-start validation owns one `RunSession`
with `run_type=stability`, `workflow_name=modbus_zero_monitor`, and
`workflow_version=1`. It also owns a `zero_monitor_capture` capture step and a
`zero_monitor_analysis` analysis step. The run, capture step, and operation
attempt are created as running, while the analysis step starts as pending. A
pre-start validation failure is stored as an error operation attempt without a
run ID and does not create an empty run or artifact.

The operation summary stores:

- Device metadata, connection settings, and register-map snapshot.
- Byte-order verification status, observed device enum/order, configured
  byte/word order, verification timestamp, and error when applicable.
- Zero-flow confirmation value, actor, and confirmation timestamp when present.
- Fixed `target_poll_interval_ms=100` plus observed start-to-start period mean,
  P50, P95, P99, maximum, and achieved poll rate calculated from monotonic
  logical-poll start times.
- Start/end time, logical poll count, physical request count, accepted count,
  duplicate count, gap count, invalid count, restart count, torn-snapshot
  reread count, transport-failure count, poll-overrun count, and missed
  schedule-slot count.
- Official zero offset read at operation start when available.
- Analysis-window and threshold snapshot, including the source of every
  threshold.
- Final state and reason codes.
- Raw snapshot artifact ID and analysis-result ID.

The configured long decision window is one of 30, 60, or 300 seconds, or a
custom value in the inclusive range 12 through 86400 seconds. A separate
10-second plot range is display state only and must not be stored or interpreted
as the long decision window.
Long-window decisions require both a continuous segment covering the selected
window and at least 20 independent 600 ms candidates.

The 24-hour maximum bounds the rolling decision deque, not capture duration.
Capture may continue until Stop while raw CSV rows keep streaming; after the
window fills, analysis evicts candidates older than the selected `Tlong`.

The 100 ms target is a versioned zero-monitor protocol constant, not per-device
configuration. The UI may display it but cannot edit it. Independent 600 ms
candidates remain sequence-based every six device publications regardless of
observed host poll timing; timing degradation is recorded without silently
changing the target or analysis thresholds.

Zero-flow confirmation is immutable run provenance, not per-device
configuration. The run snapshot stores `confirmed`, operator, confirmation
time, Device ID, profile ID, and register-map checksum. An unconfirmed run
stores `confirmed=false` and remains diagnostic-only. Confirmation is accepted
only before Start, is locked while running, and is cleared after every terminal
outcome, reconnect, Device ID/profile/register-map change, or dialog reopen.
Changing from diagnostic capture to confirmed evaluation therefore requires a
new run; samples from the earlier run are never reused.

When `modbus_byte_order` is configured, pre-start reads its unaffected 16-bit
enum and maps 0/1/2/3 to ABCD/BADC/CDAB/DCBA. Matching order is stored as
`verified`. Mismatch, invalid enum, or read failure creates only an unlinked
error attempt and no run. If the logical register is absent, a diagnostic run
stores `unavailable` plus `BYTE_ORDER_UNVERIFIED`, remains `EVALUATING`, and
cannot produce pass/fail. The service never changes device or profile order.

The threshold snapshot stores seven stability criteria: short standard
deviation, short range, raw peak-to-peak, repeatability standard deviation,
long robust range, trend span, and maximum step. Each criterion stores
`enabled`, `limit`, and `source`. All seven default to enabled; disabling one
must be explicit. Every enabled criterion requires a finite nonnegative limit
and nonempty source. With no enabled criteria or any incomplete enabled
criterion, analysis remains `EVALUATING` and pass/fail remains null.
`minimum_stable_duration_s` is also required and must be finite and
nonnegative.

Firmware zero-monitor amplitudes and `ZeroOffset` share the configured `us`
engineering unit. Stability/offset limits and trend span are stored in `us`,
slope in `us/s`, and windows/durations in seconds; M16 performs no implicit
unit conversion. A configured threshold cannot participate in a decision when
its saved unit does not match the snapshot/register-map unit.
Unit mismatch is stored as `THRESHOLD_UNIT_MISMATCH` and cannot be treated as a
disabled criterion.

The initial production threshold snapshot deliberately has null limits and
`status=pending_bench_approval`, including null minimum stable duration and
optional offset limit. It produces `EVALUATING` with null pass/fail. Synthetic
test thresholds carry `test_only=true` and must never be persisted as
production profile defaults. A future approved value records its unit, source,
approval identifier, and effective time.

The optional offset limit and source are stored separately from stability
criteria. Offset status is `UNAVAILABLE`, `WITHIN_LIMIT`, or `EXCEEDED` and is
recorded as an advisory, not as an input to the overall stability state. A
result may therefore be `STABLE` with an `OFFSET_EXCEEDED` advisory. A missing
offset limit does not block a stability decision.

Long-window calculation uses candidates in the current continuous segment
whose inclusive age from the latest device timestamp is at most the configured
window. Sample standard deviation uses `ddof=1`; P5/P95 use NumPy linear
interpolation. Least-squares slope uses seconds centered on the window's mean
device time, and trend span is the absolute slope multiplied by the configured
window duration. Threshold equality passes and no hidden comparison epsilon is
stored or applied.

The result stores `stable_since_device_time` and `stable_duration_s`. The timer
starts when confirmed zero-flow context, complete configuration, window
readiness, and every enabled criterion first hold together. It resets on a data
gap, restart, invalid/internal-error sample, or enabled-criterion violation.
Duplicate sequence values do not advance or reset it. Poll overrun alone and
offset advisories do not reset it when device sequence and data remain
continuous.

Continuity state stores `segment_break_pending`, segment ID, break reason,
state reason codes, advisory codes, and cumulative counters. Hard transport,
coherence, sequence, tick, ready-bit inconsistency, invalid-data, and internal
errors mark the event row `DATA_GAP`; the next unique valid row starts a new
segment at `NOT_READY` and is accepted as that segment's first statistical
sample/candidate anchor. A ready-bit drop after an active segment also breaks
the segment. `ZERO_CAL_RUNNING` and nonzero reserved bits end the old segment
but remain diagnostic `EVALUATING` until clear, after which a new segment starts.
Duplicate sequence and continuity-preserving overrun are advisories and do not
advance/reset continuity state. Cumulative evidence never resets when the live
state recovers.

Sequence delta uses unsigned 16-bit modular arithmetic: 0 is a duplicate only
when tick and all 18 raw words are unchanged, 1 is next, 2 through 32767 is a
forward gap, and the upper half is rollback/restart territory. Device tick uses
unsigned 32-bit modular delta; the lower nonzero half unwraps forward and the
upper half is rollback/restart. For forward sequence delta `d`, the firmware
baseline requires exactly `d * 100 ms`; mismatch is
`DEVICE_TIME_DISCONTINUITY`. Unwrapped tick is monotonic within a segment only.

The raw CSV stores one row for every logical poll attempt, including timeout,
CRC, exception-response, torn-reread failure, and program-error rows, rather
than only accepted statistical samples. To reuse the existing Test Records
curve and data viewer, its first columns are `captured_at`, `elapsed_s`, and
`sample_index`. `captured_at` is the UTC poll-completion time and is always
present; `elapsed_s` is monotonic time from run start; `sample_index` equals the
one-based logical poll index. `host_receive_time` is nullable and present only
when at least one response arrived.

Additional evidence includes scheduled elapsed time, schedule lag, request
start/duration, physical-request and torn-reread counts, response status, error
code/message, initial and reread raw words, raw/unwrapped device time,
continuous segment, sequence and sequence delta, status bits, valid count, DSP
statistics, official zero offset, zero drift, snapshot consistency,
communication gap, poll overrun, missed schedule slots, and the
statistics-acceptance flag. Rows also store reserved status bits, segment-break
reason, analysis state, state reason codes, and advisory codes. Failed rows
leave device and measurement fields null rather than copying the previous
snapshot.

The raw artifact uses `curve_type=zero_monitor_samples`,
`flow_rate_parameter=live_zero_600ms`, `variable_names`, `units`, and the other
existing generic curve metadata fields. `variable_names` contains only numeric
series exposed by the shared viewer. The loader accepts the new curve type but
continues to use the existing CSV parser, plot, data table, and JSON package
path; no separate zero-monitor history viewer or synonymous primary/sample
variable metadata fields are introduced.

Optional generic curve metadata sets
`x_axis_variable=device_tick_ms_unwrapped`, `x_axis_unit=ms`,
`x_axis_scope=continuous_segment`, and
`segment_variable=continuous_segment`. The shared viewer splits traces at
segment boundaries, defaults to the latest segment, never bridges missing/error
rows, and retains all rows in the data table. Older artifacts without these
keys keep the captured-at/single-segment fallback.

Capture writes a same-directory `.csv.partial` file under the run artifact
folder and stores its relative path in the running RunSession snapshot. The
writer flushes and fsyncs at least once per second and on every terminal path.
Finalization closes and atomically renames it to `.csv` before checksum and
artifact metadata are saved. Runtime memory contains only the visible plot
ring and independent candidates inside `Tlong`, never all raw poll rows.

Startup recovery inspects only recorded partial paths inside the artifact root
for zero-monitor runs left in `running`. A nonempty partial becomes an artifact
with `complete=false`, `recovered=true`, and
`recovery_reason=unclean_shutdown`, plus a diagnostic analysis result with null
pass/fail whenever any poll row exists; if every row failed, numeric metrics
remain null while error counts are retained. Empty partials create neither
artifact nor analysis. The capture/attempt/run finish as error and cannot
resume. The design loss window for an unclean process or power failure is less
than one second.

The analysis result uses result type `modbus_zero_monitor_stability` and stores
independent-window candidate count, long mean, sample repeatability standard
deviation, full max-min range, separate linear P95-P5 robust range, trend,
trend span, maximum step,
adjacent-difference RMS, configured thresholds, final state, state reason
codes, offset-check status, and advisory codes.
It also stores the final stable-duration value and calculation-method version.
Its pass/fail decision remains null while required thresholds are absent or
unit-incompatible, zero-flow context is unconfirmed, or ByteOrder is unverified.

Final lifecycle mapping is fixed as follows:

- A normal operator Stop is not cancellation.
- `STABLE` produces a passed run, passed analysis step, passed attempt, and
  `pass_fail_decision=passed`.
- `UNSTABLE` produces a failed run, failed analysis step, failed attempt, and
  `pass_fail_decision=failed`.
- A normal Stop ending in `NOT_READY`, `EVALUATING`, or `DATA_GAP` produces a
  completed run, completed analysis step, completed attempt, and null pass/fail.
- Operator close/cancel produces a canceled run and attempt. Existing rows are
  retained and analyzed diagnostically with null pass/fail; with no rows, the
  analysis step is skipped and no analysis result or artifact is created.
- Disconnect, communication failure, or program error produces an error run,
  capture step, and attempt. Existing rows are retained with a diagnostic
  analysis result and null pass/fail; with no rows, analysis is skipped and no
  analysis result or artifact is created.

On finalization, the artifact is stored first, then the analysis result and
step outcomes, then the operation attempt, and the terminal RunSession status
is written last. This ordering prevents a normally completed run from becoming
visible without its required evidence references.

Ten-hertz snapshot rows remain in the CSV artifact instead of
`variable_samples`; SQLite stores structured summary and provenance only.

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

### Schema Migration Compatibility

Initialization creates schema v6 for new databases and upgrades supported
schema v3-v5 databases in one transaction. It creates both filling tables and
their indexes, preserves existing rows, converts legacy timestamps used for
backfill to UTC, inserts missing shared `devices` rows for orphan
`modbus_device_profiles.device_id` values as `modbus_rtu`, and migrates inline
profile maps into the v6 register-map catalog. It records version 6 only after
all steps succeed and rejects databases newer than the supported schema.

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
- CSV for Modbus zero-monitor snapshots, with JSON-serializable artifact
  metadata for units, primary series, threshold snapshot, and continuity.
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

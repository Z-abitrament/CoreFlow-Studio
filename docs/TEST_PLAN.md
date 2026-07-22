# Test Plan

## Summary
Testing must prove that CoreFlow Studio can automate Coriolis flowmeter workflows safely, repeatably, and without physical hardware during v1 development. Simulator-driven tests are required before hardware acceptance tests.

## Test Strategy
- Unit test domain models, calculations, protocol encoding, storage repositories, and workflow state transitions.
- Integration test workflows against simulated transmitters.
- Protocol test Modbus RTU through fake, loopback, or simulator-backed transports.
- UI smoke test the main Qt workflows.
- UI bug-fix tests must follow the operator path that exposed the bug, including
  opening the relevant dialog/window and asserting that the expected label,
  input, table column, and detail text are visible to the user.
- Data integrity test SQLite records and referenced artifacts together.
- Hardware acceptance tests are defined but deferred until real transmitters and register maps are available.

## Required Test Categories

### M0 Bootstrap Tests
ID: TP-M0-001

Goal: Verify repository bootstrap and developer setup.

Scenarios:

- Confirm git is initialized locally.
- Confirm `.gitignore` excludes virtual environments, Python caches, build outputs, logs, SQLite runtime files, and generated artifacts.
- Confirm the repository Git hook path can be set to `.githooks`.
- Confirm `scripts/check_version_update.py` passes when the software version in `pyproject.toml` and `src/coreflow/__init__.py` is synchronized.
- Create or update the `coreflow-studio` conda environment from `environment.yml`.
- Install development dependencies.
- Run the test suite with pytest.
- Run the minimal application entry point and confirm it exits cleanly.
- Confirm `git status --short` is clean after the final checkpoint commit.

### M1 Core Interface Tests
ID: TP-M1-001

Goal: Verify core domain interfaces and value objects.

Scenarios:

- Instantiate device identity, health, measurement, configuration, communication diagnostic, and parameter-write models.
- Instantiate workflow run and step status models.
- Instantiate storage artifact models.
- Implement a test `FlowmeterDevice` without importing simulator, Modbus, storage repository, or UI code.
- Confirm existing M0 entry-point tests still pass.

### Simulator Tests
ID: TP-SIM-001

Goal: Verify deterministic simulator behavior.

Scenarios:

- Same seed and scenario produce identical readings.
- Different scenario configuration changes readings as expected.
- Noise, drift, delay, invalid value, timeout, and disconnection faults can be triggered.
- CSV replay files can drive deterministic measurements through the same device interface.
- Replay source path is attached to device/run metadata for traceability.
- Replay devices reject configuration writes.

ID: TP-SIM-002

Goal: Verify multi-device simulator scale.

Scenarios:

- Run 4 virtual devices concurrently.
- Run 8 virtual devices concurrently.
- Confirm one faulted virtual device does not stop other virtual devices.

### Protocol Tests
ID: TP-PROTO-001

Goal: Verify Modbus RTU adapter behavior before hardware use.

Scenarios:

- Read holding/input registers through fake or loopback target.
- Read configured coil and discrete-input values through fake or loopback target.
- Write a configured coil through the guarded device path for zero-calibration start behavior.
- Send standard Modbus raw frames through the public Python API and confirm
  they reuse the high-level read/write transport path instead of the low-level
  raw receive path.
- Send one raw frame through `CoreFlowStudioConsole.exe --modbus-raw` or the
  equivalent source CLI path and confirm the response is printed as uppercase
  hexadecimal bytes.
- Decode configured data types and scaling.
- Handle timeout and retry.
- Reject writes to read-only register definitions.
- Record communication diagnostics.

ID: TP-PROTO-002

Goal: Verify future Modbus listener diagnostics before using com0com or hub4com on a lab PC.

Scenarios:

- Use fake serial endpoints or recorded frames to exercise listener parsing without installed virtual-port drivers.
- Configure source/destination virtual COM route metadata without changing normal Modbus master connections.
- Store captured frames and timestamps as diagnostic artifacts.
- Confirm listener mode is read-only and cannot write transmitter parameters or inject frames.
- On an approved lab PC, verify com0com/hub4com route discovery and frame capture as a hardware acceptance extension.

### Integration Tests
ID: TP-INT-001

Goal: Verify device connection management across multiple channels.

Scenarios:

- Connect and disconnect multiple simulated devices.
- Mix connected, disconnected, and faulted channels.
- Confirm the application service exposes per-device status.

### Workflow Tests
ID: TP-WF-001

Goal: Verify calibration preview workflow.

Scenarios:

- Collect configured reference points from simulator.
- Store raw captures and calculated preview results.
- Produce proposed parameter writes without applying them.
- Mark workflow failed when required data is missing.

ID: TP-WF-003

Goal: Verify manual Modbus calibration workflows.

Scenarios:

- Run zero calibration against a fake or simulator device with configurable start coil/parameter, before/after `zero_offset`, before/after `delta_t`, and completion polling.
- Confirm `Variable Sampling` lets the operator choose configured Modbus variables, poll interval, plot layout, and notes; persists the selected variables, interval, and plot layout per Device ID with `Save Config`; updates a non-modal live plot while polling until `Stop`; writes a wide CSV raw artifact with sampled variable units where known; refreshes the Live Variables table with the latest values; stores a `modbus_variable_sampling` test record with sample count and sample artifact ID; and allows the saved samples to be reopened from Test Records as a plot or table, with table capture times displayed in local UI time while raw CSV `captured_at` timestamps remain UTC.
- Confirm the Zero Cal dialog can persist and reload selected pre-calibration snapshot variables per Device ID, with no global zero-calibration configuration fallback.
- Confirm zero calibration writes only through `WriteGuardService` in an explicit write-capable state and creates an audit record.
- Run simple K factor calibration from selected pre-operation variables, one reusable non-zero-to-zero flow segment, accumulated-mass before/after values, standard mass, and current K factor.
- Confirm the K Factor dialog can persist and reload selected variables, polling interval, and pre-operation snapshot selections without persisting write-to-device intent.
- Confirm corrected K factor is calculated as `k_s = k_r / m_r * m_s`.
- Confirm K factor apply writes only through the write guard, rereads the configured K factor parameter for verification, and stores run, analysis, and audit records with write-request/apply/verify status.
- Confirm K factor operation no longer exposes PC-side flow simulation controls or runtime parameters; captured flow segments must come from configured device reads.
- Run error/repeatability testing from selected pre-test variables, configured flow-rate and accumulated-mass variables, and either three operator-configured target-flow ranges with three non-zero-to-zero flow-segment trials per range or a single target-flow range with operator-appended trials.
- Confirm the Repeatability dialog can persist and reload selected variables, polling interval, mode, target-flow range settings, and pre-test snapshot selections per Device ID, with no global repeatability configuration fallback.
- Confirm the Repeatability configuration dialog exposes an operator-visible
  operation-note input when opened through the `Configuration...` button, can
  persist and reload operation notes per Device ID, the main operation dialog
  displays the saved notes, and each calculated trial record stores the same
  notes.
- Confirm each repeatability trial reads the selected pre-trial variables automatically, tells the operator when the trial can start, continuously polls through the configured instant-flow offset after flow starts, selects `v1` from the captured real-time samples instead of performing an extra post-start read, leaves a pending capture after `Capture Trial`, then calculates and stores the trial only after the operator enters `Standard Mass` and clicks `Calculate Trial Error`.
- Confirm each repeatability trial Test Records timestamp is the `Capture Trial` click time, not the captured flow-segment start/end time or the later `Calculate Trial Error` calculation/save time; confirm the calculation/save time remains traceable as detail metrics.
- Confirm the Repeatability configuration dialog exposes `Record all flow samples` plus default trial-sample variables, persists them per Device ID, and when enabled asks the operator to confirm this trial's sample/plot variables and plot layout before each `Capture Trial`; accepting opens a separate non-modal time-value plot that updates from live flow-rate and operator-selected extra-variable samples without blocking the repeatability operation dialog, supports both overlay and one-plot-per-variable display, while canceling leaves the trial uncaptured.
- Confirm live and reopened repeatability sample plots allow the operator to click a plotted sample point and view the exact trial label, variable, sample index, relative time, capture time, value, and unit.
- Confirm the same `Record all flow samples` option writes each trial's selected sample variables to a wide CSV raw artifact and stores the sample artifact ID, sample count, and sampled variable names in the trial history details.
- Confirm saved repeatability trial sample artifacts can be reopened from Test Records as both plots and tabular sample data, and that comparing multiple trials first lets the operator choose the specific trial artifacts to compare, then lets the operator choose whether each selected trial is aligned at its first sample or at the sample immediately before the first nonzero flow-rate sample while allowing selected variables to be shown either overlaid in one relative-time plot or separated into one plot per variable; when overlaying exactly two variables, confirm the first selected variable uses the left Y axis and the second selected variable uses the right Y axis.
- Confirm Test Records table summaries and detail panes display units for values whose units are known from the saved operation register-map snapshot or saved sample metadata, including configured flow-rate, accumulated-mass, K-factor, snapshot, duration, and sampled-variable-list fields.
- Confirm each repeatability trial record includes flow start, instant-sample, and end timestamps plus the raw Modbus polling artifact reference.
- Confirm Three Flow Ranges mode does not write a final summary merely because 9 trials exist; `Calculate Repeatability` must use an operator-selected consecutive three-trial window for one flow point.
- Confirm Three Flow Ranges `Calculate Repeatability` saves the selected-window error/repeatability calculation as a test record with the operation notes and a timestamp matching the repeatability calculation/save time, and Single Flow Range refreshes and saves the current error/repeatability summary after every `Calculate Trial Error` with the same notes and timestamp semantics.
- Confirm additional repeatability trials can be appended as soon as any flow point has 3 calculated trials; `Add Trial` opens a flow-point selector that defaults to the most recently completed eligible flow point, preserves earlier trial records, and allows extra trials to be selected as part of a later consecutive three-trial repeatability window.
- Confirm the selected flow-point `mean` shown in `Selected Trials And K Preview` is the arithmetic mean of that flow point's three selected trial percent errors, and is distinct from the final-K `average_error`.
- Confirm `Calculate Final K` requires three selected flow points and 9 selected trials, calculates per-flow-point measurement errors, calculates `average_error = (max(measurement_errors) + min(measurement_errors)) / 2`, calculates adjusted errors for review, calculates intermediate K values using `measurement_error` in `intermediate_k = original_k / (1 + measurement_error / 100)`, calculates final `new_k = (max(intermediate_k_values) + min(intermediate_k_values)) / 2`, and `delta_k = new_k - original_k`, writes the final-K preview with sufficient K-value precision for manual device entry, preserves operation notes, shows those notes in Test Records table/detail views, and overwrites the previous final-K preview for the same operation when repeated.
- Confirm `Write New K...` is available only after a final-K preview exists, shows an operator confirmation with Device ID, K factor variable, original K, new K, and delta, writes only through the write guard when confirmed, reads back the K factor variable, records `write_status`, `write_verified`, `readback_k_factor`, and `audit_id`, and leaves the preview unchanged when canceled.
- Confirm `Current Device Analysis` opens as a single-purpose 9-trial calculation dialog for the selected Device ID, does not show a device-history text summary or per-flow summary table, and does not write to the device.
- Confirm the device-analysis trial picker shows each accepted trial as a selectable row with Attempt ID, Run ID, old K, error, raw artifact, and comparison values; starts with no trial rows selected; orders rows by trial start time with the most recent trial first; lets the operator reorder columns by dragging table headers; lets the operator choose exactly 9 rows covering exactly three flow points with three consecutive trial indexes per point; saves checkbox-selected comparison-variable display preferences from a popup that closes after `Save`; rejects the 9-trial selection when original K, `zero_offset`, or `low_threshold` do not match; `Select And Calculate...` calculates and previews per-flow `adjusted_error`, per-flow repeatability, and old/new K without saving; and `Save` records the generated text report as `manual_error_repeatability_final_k` with `analysis_source=current_device_analysis`, uses the report save time as the Test Records timestamp while retaining selected-trial time range metrics, refreshes any open Test Records windows, and can be found with operation filter `Repeatability Final K` and status filter `Calculated`.
- Confirm Single Flow Range mode keeps a pending next-trial row available after each calculated trial and updates the current repeatability summary after every `Calculate Trial Error` save.
- Confirm repeatability operation no longer exposes PC-side flow simulation controls or runtime parameters; trial flow segments must come from configured device reads.
- Confirm repeatability trial tables and history details store trial errors, `v1`, `v_mean`, per-range repeatability standard deviations, and summary metrics for review/export.

ID: TP-WF-002

Goal: Verify automated factory test workflow.

Scenarios:

- Run communication health, identity capture, measurement check, and stability segment.
- Store step-level pass/fail decisions.
- Continue unrelated devices when one device fails.
- Produce a report-ready run record.

### Calculation Tests
ID: TP-CALC-001

Goal: Verify error analysis.

Scenarios:

- Calculate absolute error, relative error, and summary statistics from stored reference and measured values.
- Handle zero or near-zero reference values according to configured policy.
- Preserve enough intermediate data for review.

ID: TP-CALC-002

Goal: Verify stability analysis.

Scenarios:

- Calculate mean, standard deviation, range, drift estimate, and dropout count from stored time-series data.
- Detect simulator-injected drift and dropouts.
- Recompute the same result from persisted data.

ID: TP-CALC-003

Goal: Verify manual mass-total error and repeatability calculations.

Scenarios:

- Calculate percent error for each trial as `e = (delta_m - standard_mass) / standard_mass * 100%`.
- Require three flow points with three trials per point for the standard workflow.
- Calculate repeatability as the standard deviation of the three percent errors at each flow point.
- Allow the Modbus single-flow-range mode to calculate and save a current summary from the trials completed so far, then continue appending additional trials under the same run.
- Reject zero or negative standard mass values.
- Store repeatability summary metrics for later review.
- For the Modbus Simple-mode repeatability workflow, use the captured accumulated-mass change for `delta_m` and the operator-entered standard-scale mass for `standard_mass`.

### M15 Filling Trial Tests
ID: TP-FILL-CALC-001

Goal: Verify the pure Filling Trial calculations independently from Qt,
storage, devices, and protocols.

Scenarios:

- Calculate regular trial error as `(standard_mass - specified_mass) /
  specified_mass * 100` for positive, zero, and negative results.
- Calculate sample standard deviation from exactly three consecutive trial
  errors and reject nonconsecutive or non-three selections.
- Calculate advance from at least three selected trials, including
  nonconsecutive indexes, as `mean_standard_mass - specified_mass`.
- Preserve negative advance and calculate corrected target as
  `specified_mass - advance_mass`.
- Reject nonfinite, zero, negative, mismatched-unit, or mismatched-snapshot
  inputs as applicable, and confirm no calculation uses a pulse total.

ID: TP-FILL-DATA-001

Goal: Verify schema v6 persistence, including Filling Trial provenance and
atomic transitions.

Scenarios:

- Create a fresh schema v6 database with `filling_trial_records`,
  `filling_advance_profiles`, history indexes, unique trial indexes, and foreign
  keys to shared devices, runs, and analysis results.
- Migrate an existing schema v3 database without data loss; backfill orphan
  Modbus profile Device IDs into shared devices as `modbus_rtu`, normalize
  backfill timestamps to UTC, and record version 4 only after success.
- Reject databases whose schema version is newer than v6.

### M17 Modbus Register Map Library Tests

ID: TP-RMAP-001

Goal: Verify reusable register lists, Device ID bindings, migration, update
installation, and history compatibility without hardware access.

Scenarios:

- Migrate inline profile maps into deterministic legacy catalog rows and
  deduplicate identical definitions by normalized SHA-256.
- Bind multiple Device IDs to one list ID/version and preserve inline effective
  snapshots for compatibility.
- Edit one custom or legacy binding into a new version without changing another
  Device ID or any completed session/attempt snapshot.
- Reject different content under an existing official ID/version and require a
  custom clone before editing an official list.
- Install packaged official JSON versions idempotently without rebinding an
  existing Device ID.
- Parse active Krohne DSP address/access/kind declarations while ignoring
  commented-out mappings; fail extraction for unmodeled symbols or width/table
  disagreements, and reproduce the checked-in `krohne-prj-main` JSON exactly.
- Confirm the generated Krohne main list covers every active DSP mapping plus
  the reviewed client `mass_rate` alias, records the DSP source commit, and
  contains the zero-monitor block without a separate specialized list.
- Open Device Profile through the operator path, select an existing list or
  create a new list ID/name/version, and preview added/removed/modified rows.
- Confirm all catalog/profile changes are disconnected-only and generate no
  Modbus requests or device writes.
- Persist and query trials by run and Device ID, the latest calculated trial,
  and multiple immutable same-condition profiles for one flowmeter.
- Preserve source Trial IDs, source analysis-result IDs, full configuration
  snapshots, notes, and UTC timestamps.
- Roll back trial, analysis, profile, old-run completion, and corrected-new-run
  writes when any atomic transition step fails.
- Reject cross-device run/trial and analysis/profile provenance mismatches.

ID: TP-FILL-SVC-001

Goal: Verify the headless Filling Trial lifecycle and device-centered rules.

Scenarios:

- Select an existing shared flowmeter Device ID or explicitly create a unique
  `future_adapter` device; do not treat the control/valve label as a Device ID.
- Restore configuration from the selected Device ID's latest calculated trial,
  but leave standard mass blank after opening, calculating, and `Add Trial`.
- Lock mode, label, pulse settings, unit, flow point, specified mass, and target
  mass after the first calculation in a group.
- Save each calculated trial immediately and append the next one only through
  explicit `Add Trial`.
- Require exactly three consecutive source trials for repeatability and at
  least three, not necessarily consecutive, source trials for advance.
- Store every repeatability and advance calculation with source Trial IDs,
  snapshots, UTC timestamps, and no pass/fail threshold.
- Create multiple immutable advance profiles per device.
- Make `Set Advance` atomically complete the old group and create a corrected
  regular group at blank Trial 1 without mixing old and new trials.
- Return current-device history in four categories: trial, repeatability,
  advance calculation, and advance profile set.
- Complete nonempty groups, cancel empty groups, and retain retryable state when
  close or persistence fails.

ID: TP-FILL-UI-001

Goal: Verify the operator path through the embedded Filling Trial workbench.

Scenarios:

- Open `Modules > Filling Module`, choose a shared Device ID from a noneditable
  selector, or use the explicit new-device dialog.
- Confirm Device ID, control/valve label, and advance-profile controls are
  visually distinct and module switching preserves the workbench state.
- Configure pulse switch point, mass per pulse, unit, flow point, specified
  mass, target mass, mode, and label; confirm Advance mode mirrors specified
  mass until an advance is set.
- Calculate a trial, confirm persisted table values and blank standard mass,
  then use `Add Trial` to prepare the next blank input.
- Enforce checkbox selection rules for repeatability and advance actions.
- Set an advance, confirm the old table clears, corrected target appears in a
  new regular group, and Trial 1 standard mass is blank.
- Add/select multiple advance profiles and inspect all four current-device
  history record types with source IDs, snapshots, results, and notes.
- Confirm validation errors are visible and no Filling Trial action changes
  Modbus or ASIO/IIS connection state or emits protocol traffic.

Focused commands:

```powershell
conda run -n coreflow-studio python -m pytest tests/test_analysis_filling.py tests/test_workflows_storage_models.py -q
conda run -n coreflow-studio python -m pytest tests/test_storage_filling.py tests/test_storage_foundation.py -q
conda run -n coreflow-studio python -m pytest tests/test_filling_service.py tests/test_storage_filling.py tests/test_analysis_filling.py -q
conda run -n coreflow-studio python -m pytest tests/test_ui_filling.py tests/test_filling_service.py -q
conda run -n coreflow-studio python -m pytest tests/test_ui_main_window.py tests/test_packaging.py tests/test_bootstrap.py tests/test_version_policy.py -q
conda run -n coreflow-studio python scripts/check_version_update.py
```

Final integration commands:

```powershell
conda run -n coreflow-studio python -m pytest -q
conda run -n coreflow-studio python -m coreflow --ui
```

The source UI smoke must open the Filling Module, select or create a Device ID,
calculate regular and advance trials, calculate repeatability, set an advance,
and inspect history. It must not connect to or operate pulse, controller, valve,
serial, Modbus, or ASIO/IIS hardware.

### M16 Modbus Zero Monitor Tests

ID: TP-ZMON-001

Goal: Verify the read-only Modbus zero-monitor operation from coherent protocol
snapshot through live UI, stored artifact, and reproducible analysis.

Scenarios:

- Load committed fixtures only from
  `tests/fixtures/modbus_zero_monitor/`. Verify their schema and provenance
  identify the approved `Krohne_prj` PC tests, map definition, and upload code;
  CI must not require the external firmware worktree.
- Assert the literal firmware vectors cover PDU address `0x005F`, 18 words,
  input/holding readability, write rejection, unchanged 16-bit fields, and the
  four documented raw-word encodings of float32 `12.5`. Do not generate golden
  raw words with the decoder/encoder under test.
- Keep host-generated wrap, gap, torn-read, invalid-value, timing, metric, and
  state fixtures separate and mark all synthetic thresholds as test-only.
  Firmware source/hash or expected-value changes require an explicit fixture
  review; tests must never refresh golden values automatically.
- Validate the exact relative layout while allowing a configurable absolute
  block start. Reject missing or duplicate variables, wrong relative order,
  gaps, overlaps, aliases/unrelated mappings inside the block, incorrect data
  types, word counts, scales or units, mixed register kinds, and writable
  snapshot fields. Validate an available `zero_offset` as float32/2 words,
  scale 1.0, unit `us`, and matching byte/word order. Assert each
  failure returns all applicable structured errors, emits no device request,
  and stores only an unlinked error attempt.
- Accept an exact-layout block configured wholly as input or wholly as holding,
  but never auto-switch FC03/FC04. Verify missing `zero_offset` still allows
  capture and reports offset checking unavailable.
- Map device ByteOrder enum 0/1/2/3 exactly to big-big, little-big, big-little,
  and little-little. Verify all four matches, all mismatch combinations, invalid
  enum, and read failure. Mismatch/invalid/failure emits no snapshot request,
  creates no run, records an unlinked error attempt, and never edits device or
  profile configuration. Accept the current DSP's FC03 holding/RW ByteOrder
  register because the monitor only reads it.
- With no `modbus_byte_order` logical register, allow diagnostic capture but
  persist/display `BYTE_ORDER_UNVERIFIED`, remain `EVALUATING`, and produce no
  pass/fail. Verify the startup byte-order read uses normal connection retries,
  while 10 Hz block reads still override transport retries to zero.
- Use a fake block reader to prove a normal poll and each timeout/CRC/exception
  failure perform exactly one contiguous 18-register request even when the
  connection retry count is nonzero. Verify other Modbus operations retain
  their configured retry behavior.
- Prove only a begin/end sequence mismatch permits one immediate complete-block
  reread: a successful reread uses two physical requests in one logical poll,
  while a failed or still-torn reread stops at two and records a gap.
- Verify logical-poll, physical-request, torn-reread, transport-failure,
  overrun, and missed-slot counters. Assert requests never overlap and a late
  poll skips elapsed schedule points without a catch-up burst.
- Verify the M16 target interval is the fixed 100 ms service constant, appears
  read-only in the UI, and is absent from per-device configuration. Persist
  `target_poll_interval_ms=100` plus monotonic start-to-start mean, P50, P95,
  P99, maximum, and achieved-rate statistics, including null rate with fewer
  than two poll starts.
- Under deliberately slow fake responses, verify timing quality degrades and
  missed slots increase without changing the target interval, selecting
  candidates by host elapsed time, or modifying thresholds.
- Decode the snapshot under supported byte/word-order combinations.
- Accept only matching begin/end sequences with `BASE_READY`, `LIVE_READY`,
  `DATA_VALID`, valid count 60, finite floating-point fields, no zero-calibration
  or internal-error bit, and zero reserved bits. Treat LIVE-ready without
  BASE-ready as inconsistent data.
- Verify startup BASE/LIVE not-ready yields `NOT_READY`; invalid/count/nonfinite/
  internal-error, failed torn reread, sequence gap, transport failure, restart,
  or ready-bit inconsistency yields `DATA_GAP` on the event row. The next unique
  valid row starts a new segment at `NOT_READY`, becomes its first accepted
  sample/candidate anchor, and does not reuse old candidates. Dropping BASE or
  LIVE readiness after an active segment also breaks that segment.
- While `ZERO_CAL_RUNNING` is set, preserve rows and time/sequence evidence but
  return `EVALUATING + ZERO_CAL_ACTIVE`, accept no statistics, and end the old
  segment. Apply the same segment isolation to nonzero reserved bits with
  `UNSUPPORTED_STATUS_BITS`; after either clears, restart from `NOT_READY`.
- Verify duplicate sequence preserves the previous live state, does not advance
  candidates/timer or break the segment, and adds `DUPLICATE_SNAPSHOT` advisory.
  A continuity-preserving poll overrun behaves similarly with `POLL_OVERRUN`;
  cumulative counters remain visible after state recovery.
- Handle duplicate sequence, missing sequence, 16-bit sequence wrap, 32-bit
  device-tick wrap, unexplained tick rollback, timeout, invalid data, immediate
  torn-snapshot retry, and poll overrun deterministically.
- Apply exact modular half-range rules for 16-bit sequence and 32-bit tick.
  Accept a duplicate only when tick and all 18 words are unchanged; treat a
  changed same-sequence payload as `DUPLICATE_PAYLOAD_CHANGED`. Require forward
  tick delta to equal sequence delta times 100 ms and report
  `DEVICE_TIME_DISCONTINUITY` otherwise. Never fabricate a cross-restart
  unwrapped device time.
- Select one independent 600 ms candidate every six valid published snapshots;
  skip missing candidates and do not substitute overlapping windows.
- Calculate live offset drift, long mean, repeatability standard deviation, full
  `max-min` range, separate linear P95-P5 robust range, least-squares trend,
  trend span, maximum step, and adjacent-difference RMS
  `sqrt(0.5 * mean(diff^2))` from device time and persisted data.
- Verify the inclusive long-window boundary, sample standard deviation with
  `ddof=1`, NumPy linear P5/P95 interpolation, centered device-time slope in
  value-per-second units, configured-window trend span, and threshold equality
  as passing. Metric assertions use explicit test tolerances, while state
  comparisons use no hidden epsilon.
- Offer 30, 60, and 300 second long-decision presets, accept inclusive custom
  boundaries 12 and 86400 seconds, reject 11.999 and 86400.001 seconds, and
  keep a 10-second plot range independent from analysis.
  Assert `NOT_READY` with 19 independent candidates and long-window readiness
  only after both the selected device-time span and at least 20 candidates are
  present; never fill a missing candidate with an overlapping window.
- Return `NOT_READY`, `DATA_GAP`, `EVALUATING`, `STABLE`, or `UNSTABLE` with
  reason codes; never return `STABLE` without confirmed zero-flow context and
  configured required thresholds.
- Default all seven stability criteria to enabled; require finite nonnegative
  limits and nonempty sources for every enabled criterion. Assert that missing
  enabled configuration or zero enabled criteria yields `EVALUATING`, while an
  explicitly disabled criterion is omitted from evaluation and reason codes.
- Verify the initial production configuration has null limits,
  `status=pending_bench_approval`, and no minimum stable duration, producing
  diagnostic `EVALUATING` with null pass/fail. Reject test-only thresholds from
  production-profile persistence and never synthesize missing defaults.
- Verify zero magnitude limits and trend span use `us`, slope uses `us/s`, and
  durations use seconds. A unit mismatch prevents decision evaluation; no
  implicit unit conversion is performed, and `THRESHOLD_UNIT_MISMATCH` is
  persisted rather than treating the criterion as disabled.
- Keep offset checking independent: assert all enabled stability criteria may
  produce `STABLE` together with an `OFFSET_EXCEEDED` advisory, and assert a
  missing offset limit reports `UNAVAILABLE` without blocking `STABLE`.
- Require finite nonnegative `minimum_stable_duration_s`. Assert the timer uses
  unwrapped device time, reports `EVALUATING` immediately below the duration
  boundary, and reaches `STABLE` exactly at the boundary. Reset it on gap,
  restart, invalid/internal-error data, or enabled-criterion violation; do not
  advance it on duplicates or reset it for a continuity-preserving overrun or
  offset advisory.
- Open `Operations > Zero Monitor`, start and stop from the operator path, show
  status/quality counters and live curves, inspect point details, and preserve
  per-device configuration without persisting the zero-flow confirmation.
- Allow zero-flow confirmation only before Start, persist its operator, time,
  Device ID, profile ID, and register-map checksum in the run snapshot, and
  lock it while monitoring. Clear it after Stop/cancel/error, reconnect,
  Device ID/profile/register-map change, and dialog reopen.
- Verify an unconfirmed run remains diagnostic and never reaches `STABLE`.
  Switching to confirmed evaluation must create a new run and must not reuse
  candidates or stable-duration time from the unconfirmed run.
- Pause normal polling and disable conflicting Modbus operations while the
  monitor owns the channel; restore controls after stop, disconnect, or error.
- Save `modbus_zero_monitor` Test Records with raw CSV and analysis metadata,
  verify the CSV begins with `captured_at`, `elapsed_s`, and `sample_index`, and
  verify metadata uses `curve_type=zero_monitor_samples`,
  `flow_rate_parameter=live_zero_600ms`, `variable_names`, and `units`.
- Stream one CSV row per logical poll to a same-directory partial file,
  including timeout/CRC/exception/torn-reread failures with nullable measurement
  fields and non-null completion timestamps. Verify raw-word evidence, request
  timing/count fields, and that previous measurement values are never copied
  into failure rows.
- With fake clock and writer hooks, verify flush plus fsync occurs within every
  one-second interval and on all terminal paths; finalization is an atomic
  same-directory rename followed by checksum/artifact registration.
- Run a long deterministic capture and assert raw-row memory does not grow with
  run duration: the UI ring is bounded by its display range and the analysis
  deque by `Tlong`. Simulate capture beyond 24 hours and verify CSV output
  continues while the rolling candidate deque does not exceed the 86400-second
  window.
- Recover nonempty partial files for interrupted running zero-monitor runs as
  incomplete/recovered artifacts with diagnostic null-pass/fail results and
  error lifecycle states. A failure-only file keeps error counts with null
  numeric metrics. A partial with no logical-poll rows produces no artifact or
  analysis; invalid/out-of-root paths are rejected, and recovered runs cannot
  resume.
- Reuse the existing Test Records generic plot/data viewer for the saved
  zero-monitor artifact, then preserve and reopen the same artifact through
  JSON export/import without a dedicated zero-monitor history parser or view.
- Verify optional generic viewer metadata selects unwrapped device time as the
  x-axis, splits traces by continuous segment, defaults to the latest segment,
  permits selecting one/all segments, omits failure rows from curves, and never
  draws across a gap/restart. The data table still contains every logical poll;
  legacy artifacts retain captured-at/single-segment fallback behavior.
- Verify lifecycle persistence for normal Stop with `STABLE`, `UNSTABLE`,
  `NOT_READY`, `EVALUATING`, and `DATA_GAP`; assert the specified run, capture
  step, analysis step, attempt, and nullable pass/fail states.
- Verify normal Stop after failure-only logical poll rows preserves a CSV and
  diagnostic `DATA_GAP` analysis with null numeric metrics. Stop before the
  first logical poll creates no artifact or analysis and ends as error.
- Verify operator cancel and transport/program error both preserve partial CSV
  evidence and a diagnostic analysis result when rows exist, but create no
  empty artifact or fabricated analysis result when no logical poll row was
  written. Verify pre-start validation failure creates only an unlinked error
  attempt, and verify the terminal run state is persisted after evidence links.
- Confirm `Zero Cal...` opens the existing guarded operation only after monitor
  stop and that no monitor calculation or UI action writes `ZeroOffset`.
- On a real device, first run read-only mapping, timing, and no-state-change
  checks; run zero-flow bench capture as a separate, explicitly labeled stage.

### Data Integrity Tests
ID: TP-DATA-001

Goal: Verify SQLite records and artifact files stay consistent.

Scenarios:

- Create a run with raw data, processed metrics, and reports.
- Confirm every artifact referenced in SQLite exists on disk.
- Confirm missing artifacts are reported clearly.
- Recover an interrupted zero-monitor partial artifact without presenting it as
  complete or passed, and keep its checksum, recovery metadata, and error run
  mutually consistent.
- Confirm audit log records parameter-write attempts.
- Store timestamped variable samples with device identity, variable name, value, unit, source channel, and optional run/step references.
- Store standalone Modbus device profiles, test sessions, operation attempts,
  repeatability trial records, and raw Modbus polling artifact references with
  device metadata snapshots.
- Create schema v6 register-map catalog rows, migrate and deduplicate legacy
  inline profile maps by checksum, and preserve every historical map snapshot.
- Bind multiple Device IDs to one map ID/version, create a new version without
  rebinding other profiles, and reject same-ID/same-version content conflicts.
- Export standalone Modbus test records to a portable JSON package with
  optional operation and started-at time-range filters, include operation
  attempts, trial records, artifact metadata, and embedded artifact file
  content including repeatability trial-sample CSV curves, import it into
  another local repository, preserve notes, metrics, and viewable flow/extra
  variable plots, skip duplicate runs, and rename conflicting imported run IDs
  without overwriting local records.

### Safety And Write-Guard Tests
ID: TP-SAFE-001

Goal: Verify safety-sensitive writes are guarded, previewed, and audited.

Scenarios:

- Run calibration preview and confirm no device parameter write is sent.
- Reject writes when the workflow is not in an explicit write-capable state.
- Reject writes to registers or parameters not marked writable in configuration.
- Reject out-of-range values before protocol transmission.
- Run dry-run mode and confirm proposed writes are audited without changing device state.
- Record audit log entries for successful, failed, rejected, and simulated write attempts.

### UI Tests
ID: TP-UI-001

Goal: Verify the main module shell and embedded module UI.

Scenarios:

- Launch the main window.
- Confirm the main window only exposes the `Modules` menu and the central module workspace.
- Confirm the main window opens directly into the embedded Modbus Module by default.
- Select the Modbus Module from the `Modules` menu and confirm it remains in the main workspace instead of opening a top-level module window.
- Confirm the Modbus Module has its own connection state and does not create or connect simulator/replay channels in the main window.
- Open the ASIO/IIS Module from the `Modules` menu and confirm it refreshes into the main workspace instead of opening a top-level module window.
- Switch between Modbus and ASIO/IIS modules without losing each module's local UI state.
- For every bug fix that adds, moves, or relabels a control in a dialog, open
  that dialog through the same button/action used by the operator and assert the
  label and input widget are visible. Do not rely only on direct internal-widget
  access.
- For every bug fix that changes data shown in history, reports, or detail
  panels, assert both the persisted record and the user-visible table/detail
  text.
- Open Device Profile through the operator path, select an existing register
  list or create a new list ID/version, preview map changes, and confirm list
  controls are unavailable while connected.

ID: TP-UI-002

Goal: Verify live and historical display where exposed by the active module or console-backed smoke workflows.

Scenarios:

- Show live module values or traces where the active module supports them.
- Open stored Modbus test records from the active Modbus module.
- Display stored result tables, details, and artifact links where the active module exposes history.

ID: TP-UI-003

Goal: Verify the ASIO/IIS module UI remains independent from other communication paths.

Scenarios:

- Open the ASIO/IIS module from the main window `Modules` menu.
- Display editable ASIO/IIS normal-use parameters for detected device, sample rate, bit depth or sample format, input/output channel count up to 2, frame size, and drive/test amplitude.
- Connect and disconnect the module through its own controls.
- Probe the selected module from the main ASIO/IIS workspace and show driver capability messages.
- Show module status and log messages for connection, diagnostics, and loopback runs.
- Confirm ASIO/IIS connect/disconnect does not change simulator, replay, serial Modbus, or other device-channel connection state.
- Confirm test-only settings such as frame count and latency search are not exposed in the normal-use parameter panel.
- Open the ASIO/IIS test dialog and run loopback and non-loopback checks with plotted or tabulated data for user confirmation.
- Choose sine, square, or white-noise test signals and edit signal parameters such as amplitude and frequency where applicable.
- Display input, output, or input and output together on one plot.

### Report Tests
ID: TP-RPT-001

Goal: Verify reports and exports.

Scenarios:

- Generate a calibration report from simulator data.
- Generate a factory test report from simulator data.
- Export measurement and metric CSV files.
- Confirm reports include device identity, run configuration, timestamps, results, and artifact references.
- Confirm the standalone Modbus Test Records window exposes JSON import/export,
  lets the operator choose an export operation and started-at time range, shows
  trial-level operation attempts in addition to summaries, and reserves Excel
  export for a later report/export implementation.

### Extension Tests
ID: TP-EXT-001

Goal: Verify experiment extension interfaces.

Scenarios:

- Run an experiment with simulated data capture.
- Execute a sample signal-processing module.
- Store processing configuration and outputs.
- Keep ML and fixture-control placeholders isolated from core workflows.

### ASIO/IIS Frame Stream Tests
ID: TP-ASIO-001

Goal: Verify the headless ASIO/IIS module without physical hardware.

Scenarios:

- Validate frame format configuration for sample rate, bit depth or sample format, channel count, and samples per frame.
- Reject unsupported or unsafe frame settings before opening hardware.
- Use a fake loopback backend to transmit deterministic frame payloads and capture them through the same API.
- Detect frame delay, correlation score, normalized error, and pass/fail status from captured loopback data.
- Report backend-unavailable diagnostics without importing optional ASIO dependencies at application startup.

ID: TP-ASIO-002

Goal: Verify the BRAVO-HD ASIO/IIS hardware loopback path on the lab PC.

Scenarios:

- Enumerate Windows audio devices and host APIs and confirm the selected device name includes `BRAVO-HD Device Control` or the configured alias.
- Confirm an ASIO host API is available before running the loopback test.
- Open the selected device in full-duplex mode with configured sample rate, bit depth or sample format, channel counts, and frame size.
- Output deterministic IIS frames through the master IIS path while capturing the connected slave IIS input path.
- Pass when captured data matches the generated payload within configured correlation and error thresholds after latency compensation.
- Fail with a clear diagnostic when the device, ASIO backend, channel configuration, or loopback signal is missing.

### Packaging Tests
ID: TP-PKG-001

Goal: Verify the Windows distributable can be built and can run simulator workflows without physical hardware.

Scenarios:

- Build the PyInstaller distributable folder from a clean checkout or clean working tree.
- After a user-visible UI fix, confirm the packaged executable timestamp/build
  metadata is newer than the changed source files before asking operators to
  verify the fix from `dist\`.
- Confirm the packaging script uses the configured conda environment rather than a hard-coded local `.venv`.
- Confirm the main packaged executable opens the UI without a console window.
- Confirm the console diagnostics executable prints version and build metadata.
- Confirm the console diagnostics executable can write the placeholder Modbus register-map template.
- Confirm the console diagnostics executable can generate a full GitHub Release
  update package and `latest.json` manifest from a packaged distribution folder,
  excluding runtime data folders.
- Confirm that when a previous source version and previous full update package
  are provided, the console diagnostics executable generates a smaller patch
  update package, lists that patch before the full package in `latest.json`, and
  skips patch generation for source versions older than the patch-capable
  updater.
- Confirm that `scripts\release.ps1` documents and enforces a clean working
  tree before building, verifying, generating update assets, tagging, pushing,
  and creating the GitHub Release, and that `.githooks\post-commit` only invokes
  it when `coreflow.autoRelease=true` and both version files changed.
- Run the console diagnostics executable's headless simulator smoke command with an explicit data root.
- Run the console diagnostics executable with `--ui`, capture stdout/stderr, and confirm the UI stays alive through startup without missing-module errors.
- Run the windowed UI executable and confirm it stays alive through startup.
- Open `Help > Check for Updates...` from the packaged UI, enter a reachable
  `latest.json` URL, check for an update, download it, verify SHA-256, and
  confirm `Update and Restart` starts the external updater without requiring the
  operator to run PowerShell commands. Verify a matching patch package is
  selected ahead of the full package and that the full package remains a
  fallback when no safe patch exists.
- Force or mock a packaged UI startup failure and confirm the failure is appended to `<data-root>\logs\startup.log`.
- Confirm the simulator smoke command performs connection, live read, calibration preview, factory test, experiment, and export generation.
- Confirm runtime data is stored under `%LOCALAPPDATA%\CoreFlow Studio` by default or `COREFLOW_DATA_ROOT` when configured.
- Confirm the package README includes USB-to-serial driver notes and packaging limits.
- Confirm English and Chinese user manuals are included in the distribution folder.

## Hardware Acceptance Tests
Hardware tests are not required for the documentation harness or early simulator implementation. Modbus transmitter tests become active when real transmitters, register maps, serial settings, and safety rules are available. ASIO/IIS loopback tests become active when the BRAVO-HD USB sound-card driver and Python ASIO backend are available on the lab PC.

Planned scenarios:

- Detect available USB-to-serial ports on Windows.
- Connect to a known transmitter by Modbus unit ID.
- Read identity and health registers.
- Read live measurements.
- Run read-only factory test steps.
- Validate write guards before calibration parameter writes.
- Perform a controlled parameter write only after approval and audit logging are implemented.
- Read the configured zero-monitor snapshot block without writes, verify the
  observed publication rate and byte order, and preserve raw frame/artifact
  evidence before any zero-calibration trigger is considered.
- Enumerate the BRAVO-HD ASIO device.
- Run the ASIO/IIS headless loopback smoke test with the paired IIS master output and slave input wiring.

## Documentation Harness Verification
Before implementation begins, verify:

- All docs consistently specify Windows-first, Python + Qt, simulator-first, Modbus RTU, 4-8 ports, and SQLite plus files.
- Each PRD functional requirement maps to a milestone and test ID.
- Known unknowns are explicit and not hidden as invented defaults.
- Hardware workflows have simulator equivalents.

Manual verification checklist:

- Compare `docs/PRD.md` functional requirements with `docs/IMPLEMENTATION_PLAN.md` milestones and this test plan.
- Confirm `docs/ARCHITECTURE.md`, `docs/PROTOCOLS.md`, and `docs/SIMULATION.md` all use the same device-interface boundary.
- Confirm `docs/DATA_MODEL.md` stores structured records in SQLite and large raw/report artifacts as files.
- Confirm write-capable operations are described as guarded and auditable in `AGENTS.md`, `docs/PRD.md`, `docs/PROTOCOLS.md`, `docs/DATA_MODEL.md`, and this file.
- Run available markdown linting or spell/format checks if tooling is present; otherwise manually scan headings, tables, and code blocks.

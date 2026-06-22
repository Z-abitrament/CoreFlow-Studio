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
- Confirm each repeatability trial reads the selected pre-trial variables automatically, tells the operator when the trial can start, leaves a pending capture after `Capture Trial`, then calculates and stores the trial only after the operator enters `Standard Mass` and clicks `Calculate Trial Error`.
- Confirm each repeatability trial Test Records timestamp is the `Calculate Trial Error` calculation/save time, not the captured flow-segment start or end time.
- Confirm each repeatability trial record includes flow start, instant-sample, and end timestamps plus the raw Modbus polling artifact reference.
- Confirm Three Flow Ranges mode does not write a final summary merely because 9 trials exist; `Calculate Repeatability` must use an operator-selected consecutive three-trial window for one flow point.
- Confirm Three Flow Ranges `Calculate Repeatability` saves the selected-window error/repeatability calculation as a test record with the operation notes and a timestamp matching the repeatability calculation/save time, and Single Flow Range refreshes and saves the current error/repeatability summary after every `Calculate Trial Error` with the same notes and timestamp semantics.
- Confirm additional repeatability trials can be appended as soon as any flow point has 3 calculated trials; `Add Trial` opens a flow-point selector that defaults to the most recently completed eligible flow point, preserves earlier trial records, and allows extra trials to be selected as part of a later consecutive three-trial repeatability window.
- Confirm the selected flow-point `mean` shown in `Selected Trials And K Preview` is the arithmetic mean of that flow point's three selected trial percent errors, and is distinct from the final-K `average_error`.
- Confirm `Calculate Final K` requires three selected flow points and 9 selected trials, calculates per-flow-point measurement errors, calculates `average_error = (max(measurement_errors) + min(measurement_errors)) / 2`, calculates adjusted errors, intermediate K values, final `new_k = (max(intermediate_k_values) + min(intermediate_k_values)) / 2`, and `delta_k = new_k - original_k`, writes the final-K preview with sufficient K-value precision for manual device entry, preserves operation notes, shows those notes in Test Records table/detail views, and overwrites the previous final-K preview for the same operation when repeated.
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

### Data Integrity Tests
ID: TP-DATA-001

Goal: Verify SQLite records and artifact files stay consistent.

Scenarios:

- Create a run with raw data, processed metrics, and reports.
- Confirm every artifact referenced in SQLite exists on disk.
- Confirm missing artifacts are reported clearly.
- Confirm audit log records parameter-write attempts.
- Store timestamped variable samples with device identity, variable name, value, unit, source channel, and optional run/step references.
- Store standalone Modbus device profiles, test sessions, operation attempts,
  repeatability trial records, and raw Modbus polling artifact references with
  device metadata snapshots.
- Export standalone Modbus test records to a portable JSON package with
  optional operation and started-at time-range filters, include operation
  attempts, trial records, and artifact metadata, import it into another local
  repository, preserve notes and metrics, skip duplicate runs, and rename
  conflicting imported run IDs without overwriting local records.

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
- Run the console diagnostics executable's headless simulator smoke command with an explicit data root.
- Run the console diagnostics executable with `--ui`, capture stdout/stderr, and confirm the UI stays alive through startup without missing-module errors.
- Run the windowed UI executable and confirm it stays alive through startup.
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

# Test Plan

## Summary
Testing must prove that CoreFlow Studio can automate Coriolis flowmeter workflows safely, repeatably, and without physical hardware during v1 development. Simulator-driven tests are required before hardware acceptance tests.

## Test Strategy
- Unit test domain models, calculations, protocol encoding, storage repositories, and workflow state transitions.
- Integration test workflows against simulated transmitters.
- Protocol test Modbus RTU through fake, loopback, or simulator-backed transports.
- UI smoke test the main Qt workflows.
- Data integrity test SQLite records and referenced artifacts together.
- Hardware acceptance tests are defined but deferred until real transmitters and register maps are available.

## Required Test Categories

### M0 Bootstrap Tests
ID: TP-M0-001

Goal: Verify repository bootstrap and developer setup.

Scenarios:

- Confirm git is initialized locally.
- Confirm `.gitignore` excludes virtual environments, Python caches, build outputs, logs, SQLite runtime files, and generated artifacts.
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
- Run K factor calibration from accumulated-mass before/after values, standard mass, and current K factor.
- Confirm corrected K factor is calculated as `k_s = k_r / m_r * m_s`.
- Confirm K factor apply writes only through the write guard and stores run, step, analysis, and audit records.

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

- Calculate percent error for each trial as `e = (m_1 - m_2) / m_2 * 100%`.
- Require three flow points with three trials per point for the standard workflow.
- Calculate repeatability as the standard deviation of the three percent errors at each flow point.
- Reject zero or negative standard mass values.
- Store repeatability summary metrics for later review.

### Data Integrity Tests
ID: TP-DATA-001

Goal: Verify SQLite records and artifact files stay consistent.

Scenarios:

- Create a run with raw data, processed metrics, and reports.
- Confirm every artifact referenced in SQLite exists on disk.
- Confirm missing artifacts are reported clearly.
- Confirm audit log records parameter-write attempts.
- Store timestamped variable samples with device identity, variable name, value, unit, source channel, and optional run/step references.

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

Goal: Verify main connection and workflow UI.

Scenarios:

- Launch the main window.
- Create simulated channels.
- Connect and disconnect devices.
- Start and cancel a workflow without freezing the UI.
- Open the standalone Modbus Module from the toolbar or menu.
- Confirm the Modbus Module has its own connection state and does not create or connect simulator/replay channels in the main window.

ID: TP-UI-002

Goal: Verify live and historical display.

Scenarios:

- Show live numeric readings.
- Show live time-series chart.
- Open a completed run from history.
- Display stored result tables and artifact links.

ID: TP-UI-003

Goal: Verify the ASIO/IIS module UI remains independent from other communication paths.

Scenarios:

- Open the ASIO/IIS module window from the main window.
- Display editable ASIO/IIS normal-use parameters for detected device, sample rate, bit depth or sample format, input/output channel count up to 2, frame size, and drive/test amplitude.
- Connect and disconnect the module through its own controls.
- Probe the selected module from the main ASIO/IIS window and show driver capability messages.
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

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
- Decode configured data types and scaling.
- Handle timeout and retry.
- Reject writes to read-only register definitions.
- Record communication diagnostics.

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

### Data Integrity Tests
ID: TP-DATA-001

Goal: Verify SQLite records and artifact files stay consistent.

Scenarios:

- Create a run with raw data, processed metrics, and reports.
- Confirm every artifact referenced in SQLite exists on disk.
- Confirm missing artifacts are reported clearly.
- Confirm audit log records parameter-write attempts.

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

ID: TP-UI-002

Goal: Verify live and historical display.

Scenarios:

- Show live numeric readings.
- Show live time-series chart.
- Open a completed run from history.
- Display stored result tables and artifact links.

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
- Confirm the simulator smoke command performs connection, live read, calibration preview, factory test, experiment, and export generation.
- Confirm runtime data is stored under `%LOCALAPPDATA%\CoreFlow Studio` by default or `COREFLOW_DATA_ROOT` when configured.
- Confirm the package README includes USB-to-serial driver notes and packaging limits.
- Confirm English and Chinese user manuals are included in the distribution folder.

## Hardware Acceptance Tests
Hardware tests are not required for the documentation harness or early simulator implementation. They become active when real transmitters, register maps, serial settings, and safety rules are available.

Planned scenarios:

- Detect available USB-to-serial ports on Windows.
- Connect to a known transmitter by Modbus unit ID.
- Read identity and health registers.
- Read live measurements.
- Run read-only factory test steps.
- Validate write guards before calibration parameter writes.
- Perform a controlled parameter write only after approval and audit logging are implemented.

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

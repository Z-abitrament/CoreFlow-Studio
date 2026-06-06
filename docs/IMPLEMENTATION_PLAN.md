# Implementation Plan

## Summary
This plan describes the order for implementing CoreFlow Studio after the documentation harness exists. The sequence is simulation-first and keeps real hardware integration behind interfaces until the simulator, workflows, storage, and UI foundation are stable.

## Milestones

### M0: Repository Bootstrap
Create the Python project skeleton.

Deliverables:

- Python package structure matching `docs/ARCHITECTURE.md`.
- Dependency management file.
- Test runner configuration.
- Basic logging configuration.
- Minimal CLI or app entry point.
- Developer setup instructions.
- Documentation harness verification checklist linked from the developer setup notes.
- Local git initialization when `.git` is absent.
- `.gitignore` for Python, Qt, virtual environments, caches, build outputs, logs, SQLite runtime files, and generated artifacts.
- First baseline checkpoint commit for documentation and workflow setup.
- Developer setup instructions linked from `docs/DEVELOPMENT_WORKFLOW.md`.
- Windows setup and verification commands in `docs/DEVELOPER_SETUP.md`.

Done when:

- Tests can be run from a clean checkout.
- A minimal app entry point starts and exits cleanly.
- The canonical documentation set has been reviewed for consistent v1 scope and source-of-truth assumptions.
- `git status --short` is clean after the final M0 checkpoint commit.

### M1: Core Domain Interfaces
Define the first stable interfaces and data objects.

Deliverables:

- `FlowmeterDevice` interface.
- Measurement, device identity, health, configuration, and communication diagnostic models.
- Workflow step and run status models.
- Storage artifact model.

Done when:

- Unit tests can instantiate domain objects.
- Workflows can depend on interfaces without importing simulator or Modbus code.

### M2: Simulator Foundation
Implement simulated transmitters and simulator scenarios.

Deliverables:

- Deterministic `SimulatedFlowmeterDevice`.
- Configurable measurement behavior for flow, density, temperature, noise, drift, and zero offset.
- Fault injection for timeout, disconnection, invalid values, and write failure.
- Multi-device simulator manager for 4-8 virtual devices.

Done when:

- A test can run 8 virtual devices concurrently.
- Simulator readings are deterministic with a fixed seed.

### M3: Modbus RTU Protocol Adapter
Implement the first real-device communication path behind the device interface.

Deliverables:

- Serial configuration model for port, baud rate, parity, stop bits, timeout, and unit ID.
- Modbus RTU client wrapper using pyserial and pymodbus or equivalent.
- Register-map abstraction with configurable addresses, data types, scaling, and writable flags.
- Timeout, retry, and protocol error reporting.

Done when:

- Protocol tests pass against a fake or loopback Modbus target.
- No workflow imports serial or Modbus implementation details.

### M4: Storage Foundation
Implement local data persistence.

Deliverables:

- SQLite database initialization and schema migration baseline.
- Repositories for devices, runs, steps, results, metrics, artifacts, and audit logs.
- Artifact file store for raw captures, processed outputs, exports, and reports.
- Run directory naming and retention conventions.

Done when:

- A simulated run can create a database record and linked artifact files.
- Data integrity tests verify that artifacts referenced in SQLite exist.

### M5: Calibration Workflow Foundation
Implement simulator-backed calibration preview.

Deliverables:

- Workflow definition for collecting reference points.
- Calculation interface for calibration coefficients or placeholders.
- Result preview without writing device parameters.
- Audit-ready representation of proposed parameter writes.
- Dry-run execution path for write-capable calibration steps.
- Write-guard service that validates workflow state, writable permission, value ranges, and actor/source before any parameter write is allowed.

Done when:

- Calibration preview runs end-to-end against simulator data.
- Unknown production formulas are isolated behind configurable or replaceable calculation modules.
- Write attempts can be previewed, rejected, dry-run audited, or applied to simulator state through one guarded application-level path.

### M6: Automated Factory Test Workflow
Implement a fixed test sequence.

Deliverables:

- Communication health check.
- Device identity and configuration capture.
- Measurement check against configured reference points.
- Stability segment.
- Step-level pass/fail results.

Done when:

- A complete simulated outgoing test creates stored results and a report-ready run record.

### M7: Error And Stability Analysis
Implement initial analysis modules.

Deliverables:

- Error metrics against reference values.
- Repeatability metrics.
- Short-term stability metrics.
- Drift and noise estimates.
- Configurable thresholds.

Done when:

- Calculations are reproducible from stored data.
- Unit tests cover nominal, boundary, and abnormal data.

### M8: Qt Desktop UI
Build the first usable desktop experience.

Deliverables:

- Main window with device/channel list.
- Connection setup for simulator and serial Modbus paths.
- Workflow launch and progress panels.
- Live numeric readings and time-series chart.
- Run history and result inspection views.

Done when:

- A user can launch the app, connect simulated devices, run calibration preview, run factory test, and inspect stored results.
- UI tests or smoke checks verify the main paths.
- Verification notes are recorded in `docs/M8_VERIFICATION.md`.

### M9: Reporting And Export
Generate operator-readable outputs.

Deliverables:

- Calibration and factory test report templates.
- CSV export for tabular measurements and metrics.
- Export package that includes metadata, results, and artifact references.

Done when:

- A simulator-generated run can produce report and CSV artifacts.
- Report tests verify required fields are present.
- Verification notes are recorded in `docs/M9_VERIFICATION.md`.

### M10: Flexible Experiment Extensions
Add the first extension points for R&D workflows.

Deliverables:

- Experiment definition model.
- Signal-processing module interface.
- Fixture-control placeholder interface.
- ML inference placeholder interface.
- Example simulator-backed experiment.

Done when:

- A simple experiment can collect data, run a processing module, store outputs, and display results.

### M11: Hardware Acceptance Preparation
Prepare for physical transmitter testing.

Deliverables:

- Hardware checklist.
- Register-map configuration template.
- Serial adapter validation tool.
- Dry-run and write-guard checks.
- Hardware acceptance test cases.
- Operator approval and audit-log review procedure for any first hardware parameter write.

Done when:

- A real-device test session can be attempted without changing workflows or UI architecture.
- Read-only hardware checks can run before any write-capable workflow is enabled.

### M12: Windows Packaging
Package the desktop app for a Windows lab or factory PC.

Deliverables:

- Packaging configuration.
- Dependency and driver notes.
- User data directory convention.
- Version stamping.
- Basic installer or distributable folder.

Done when:

- A clean Windows machine can run the packaged app with simulator workflows.

## Implementation Defaults
- Use PySide6 for Qt unless a documented blocker appears.
- Use pytest for tests.
- Use pyqtgraph for live time-series plots.
- Use SQLite directly or SQLAlchemy with lightweight migrations.
- Use CSV or Parquet for large tabular artifacts; choose CSV first unless performance requires Parquet.
- Use seeded simulator scenarios for repeatable integration tests.

## Traceability Requirement
Every PRD functional requirement must map to at least one milestone and one test case. Maintain the traceability table in `docs/PRD.md` and the test identifiers in `docs/TEST_PLAN.md` as implementation grows.

## Documentation Updates During Implementation
Update the docs when:

- A public interface changes.
- The package layout changes.
- A schema or artifact format changes.
- A workflow step is added, removed, or redefined.
- A known unknown becomes known.
- Hardware behavior contradicts simulator assumptions.

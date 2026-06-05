# Product Requirements Document

## Summary
CoreFlow Studio is a PC-side automation application for Coriolis flowmeter transmitter debugging, factory calibration, fixed test flows, error analysis, stability analysis, flexible experiments, data processing, and visualization.

v1 is a Windows-first Python + Qt desktop application. It must run complete workflows against simulated transmitters before real hardware is available. The first concrete communication path is Modbus RTU over USB-to-serial, with architecture support for 4-8 simultaneous ports.

## Target Users
- Factory test engineers who run repeatable calibration and outgoing inspection flows.
- R&D engineers who run flexible experiments, signal-processing analysis, fixture-control experiments, and exploratory data workflows.
- Firmware and embedded engineers who need PC-side visibility into transmitter state, communication behavior, and calibration parameters.
- Quality engineers who need traceable reports, error analysis, and stability evidence.

## Product Goals
- Automate fixed factory workflows that are currently manual or semi-manual.
- Support repeatable calibration by applying known references, collecting measurements, calculating corrections, and writing validated parameters.
- Analyze flowmeter error, repeatability, drift, stability, and abnormal behavior.
- Support flexible R&D experiments without rewriting the core application.
- Communicate with multiple transmitters or ports concurrently.
- Preserve raw and processed data for traceability and later analysis.
- Allow development and testing to proceed without physical instruments by using deterministic simulators.

## v1 Scope
v1 must deliver the software foundation for:

- Device connection management for 4-8 simulated or serial Modbus RTU channels.
- A simulator-first workflow path for calibration, automated test runs, error analysis, stability analysis, and reporting.
- A desktop UI for monitoring devices, launching workflows, viewing live data, and inspecting results.
- A workflow engine that can run fixed factory procedures without embedding logic in the UI.
- Data capture, storage, and export using SQLite plus linked files.
- Protocol abstraction that allows real Modbus RTU transmitters to replace simulated devices later.
- Extension points for signal processing, fixture control, and machine learning experiments.

## Out Of Scope For v1
- Cloud services, central servers, or multi-user network databases.
- Final production calibration formulas until the instrument team provides them.
- Final transmitter register maps until firmware documentation is provided.
- Final fixture drivers until test-bench hardware is specified.
- Regulatory certification tooling.
- Full machine learning model lifecycle management.

## Primary Workflows

### 1. Device Setup And Connection
The user can select simulated devices or serial ports, configure baud rate and Modbus unit IDs, connect to multiple channels, and view connection status.

Acceptance criteria:

- The application can run 4-8 simulated transmitters at once.
- Connection status is visible per port or device.
- Communication errors are logged without freezing the UI.
- Simulated and real-device connection paths expose the same application-level device interface.

### 2. Factory Calibration
The user can run a guided calibration workflow using known reference points. The workflow collects readings, calculates provisional calibration results, validates limits, and records the outcome.

Acceptance criteria:

- Calibration can run end-to-end against simulator data.
- Calibration steps are explicit and resumable after recoverable failures.
- Writes to transmitter parameters are separated from calculation and preview.
- Every calibration run stores raw data, input configuration, calculated outputs, and pass/fail status.

### 3. Automated Factory Test
The user can run a fixed outgoing test procedure that checks communication, basic health, measurement behavior, error against reference values, and stability over time.

Acceptance criteria:

- A test sequence can run without manual intervention after configuration.
- Each step records timing, inputs, outputs, and pass/fail decision.
- A completed run produces an operator-readable report.
- The same workflow can run against simulated devices for development.

### 4. Error Analysis
The user can compare measured flow values against reference values, calculate error metrics, and inspect error across test points.

Acceptance criteria:

- The application stores each reference point and measured result.
- Error metrics are calculated reproducibly from stored data.
- Plots and tables can be generated from completed runs.
- Unknown production thresholds remain configurable.

### 5. Stability Analysis
The user can collect data over a configured duration and evaluate short-term stability, drift, noise, and abnormal interruptions.

Acceptance criteria:

- Time-series data is stored in files linked to run metadata.
- Stability metrics are computed from the stored data, not only from live memory.
- Long-running capture does not block the UI.
- Simulator scenarios can inject drift, noise, communication delay, and dropouts.

### 6. Flexible Experiments
R&D users can define experiment-like runs that collect raw signals, apply signal-processing modules, control future fixtures, and optionally run machine-learning models.

Acceptance criteria:

- Experiment workflows reuse the same device, storage, and visualization infrastructure.
- Signal-processing and ML code is isolated behind interfaces.
- v1 provides extension points and examples, not a complete ML platform.
- Experimental outputs remain traceable to input data and processing configuration.

### 7. Reporting And Export
The user can export reports and data packages for calibration, tests, and experiments.

Acceptance criteria:

- Reports include device identity, run configuration, timestamps, results, and file references.
- CSV export is available for tabular data.
- Raw capture files remain accessible from the run record.
- Report generation can be tested against simulator-generated runs.

## Functional Requirements

| ID | Requirement | First Implementation Milestone | Test Coverage |
| --- | --- | --- | --- |
| PRD-FR-001 | Manage multiple device connections through one UI. | M2, M8 | TP-INT-001, TP-UI-001 |
| PRD-FR-002 | Support simulated transmitters before physical hardware. | M1, M2 | TP-SIM-001, TP-SIM-002 |
| PRD-FR-003 | Implement Modbus RTU as the first real protocol path. | M3 | TP-PROTO-001 |
| PRD-FR-004 | Run fixed factory calibration workflows. | M5 | TP-WF-001, TP-SAFE-001 |
| PRD-FR-005 | Run automated factory test workflows. | M6 | TP-WF-002 |
| PRD-FR-006 | Compute error and stability analysis metrics. | M7 | TP-CALC-001, TP-CALC-002 |
| PRD-FR-007 | Store traceable run metadata and linked files. | M4 | TP-DATA-001, TP-SAFE-001 |
| PRD-FR-008 | Display live and historical data. | M8 | TP-UI-002 |
| PRD-FR-009 | Export reports and data files. | M9 | TP-RPT-001 |
| PRD-FR-010 | Provide extension points for experiments, signal processing, fixture control, and ML. | M10 | TP-EXT-001 |
| PRD-FR-011 | Guard safety-sensitive device writes with preview, validation, dry-run support, and audit logging. | M5, M11 | TP-SAFE-001 |

## Non-Functional Requirements
- The UI must remain responsive during communication, capture, analysis, and report generation.
- Communication failures on one port must not stop unrelated ports.
- Workflow state must be recoverable enough to diagnose failed runs.
- All device writes must be auditable.
- Data storage must support later review of the exact raw data behind each result.
- Simulator behavior must be deterministic when seeded.
- The application must be usable on a Windows factory or lab PC without requiring a server.

## Safety Requirements
Calibration and configuration writes may eventually change transmitter behavior or factory fixture state. v1 must therefore design write-capable paths as explicit, testable workflows even when only simulated devices are available.

Required safeguards:

- Read-only diagnostics and analysis workflows must be separate from write-capable calibration workflows.
- Calibration calculations must produce a preview of proposed parameter changes before any write is allowed.
- Device writes must require an explicit workflow state, validated register or parameter permissions, configured value ranges, and an actor identity.
- Dry-run mode must be available for workflows that can change transmitter parameters.
- Every write attempt, including failed and simulated writes, must create an audit record.
- Simulator behavior must not define production safety limits; production limits remain configuration inputs until approved.

## Documentation Harness Acceptance
This repository is considered ready for first implementation when:

- The canonical documents are complete enough for an engineer to bootstrap the Python project without inventing hardware details.
- v1 scope is consistently local, Windows-first, Python + Qt, simulator-first, Modbus RTU over USB-serial, 4-8 ports, and SQLite plus files.
- Every major product workflow has an implementation milestone and at least one test scenario.
- Unknown calibration formulas, register maps, fixture behavior, thresholds, and report obligations are documented as unknowns or configuration inputs.

## Known Unknowns
- Final calibration formulas and numerical methods.
- Transmitter Modbus register map, data types, scaling, writable parameter ranges, and commit behavior.
- Exact transmitter identity fields and serial-number source.
- Factory fixture interfaces for reference flow source, valves, temperature control, pressure sensors, or drive control.
- Acceptance thresholds for calibration, error, repeatability, drift, stability, and communication quality.
- Required report format for customers, quality systems, or regulatory records.
- Required cybersecurity or access-control policy for parameter writes.

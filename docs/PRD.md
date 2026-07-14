# Product Requirements Document

## Summary
CoreFlow Studio is a PC-side automation application for Coriolis flowmeter transmitter debugging, factory calibration, fixed test flows, error analysis, stability analysis, flexible experiments, data processing, and visualization.

v1 is a Windows-first Python + Qt desktop application. It must run complete workflows against simulated transmitters before real hardware is available. The first concrete communication path is Modbus RTU over USB-to-serial, with architecture support for 4-8 simultaneous ports.

The current delivered baseline is M15 at software version `0.7.0` and SQLite
schema v4. M15 adds the independent Filling Trial Module as a manual-input,
hardware-free workflow; it does not change the completed M12 packaging scope or
the safety requirements for protocol-backed operations.

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
- A Modbus RTU master operator path for configurable variable maps, timestamped variable reads, zero calibration, K factor calibration, and manual mass-total repeatability tests.
- Extension points for signal processing, fixture control, and machine learning experiments.
- An ASIO-backed IIS frame I/O module for lab hardware tests, exposed through an independent UI window after the headless module is verified.
- An independent Filling Trial Module for manual filling-error,
  repeatability, valve-closing advance, and advance-profile record keeping.

## Out Of Scope For v1
- Cloud services, central servers, or multi-user network databases.
- Final production calibration formulas until the instrument team provides them.
- Final transmitter register maps until firmware documentation is provided.
- Final fixture drivers until test-bench hardware is specified.
- Regulatory certification tooling.
- Full machine learning model lifecycle management.
- Production use of a com0com/hub4com Modbus listener until virtual-port tooling is installed, tested, and approved on the lab PC.
- Pulse acquisition, pulse-total calculation, valve or controller control, and
  controller parameter writes from the Filling Trial Module.

## Primary Workflows

### 1. Device Setup And Connection
The user can select simulated devices or serial ports, configure baud rate and Modbus unit IDs, connect to multiple channels, and view connection status.

Acceptance criteria:

- The application can run 4-8 simulated transmitters at once.
- Connection status is visible per port or device.
- Communication errors are logged without freezing the UI.
- Simulated and real-device connection paths expose the same application-level device interface.
- Serial Modbus setup exposes normal parameters, port identification, and Modbus unit ID without hard-coding COM port numbers.
- Modbus variable definitions can be configured by logical name, address, register/coil type, data type, scale, unit, and read/write permission.
- Required logical variables include read-only `mass_rate`, `mass_acc`, `temperature`, `delta_t`, `zero_offset`, and `frequency`, plus writable `k_factor` and `low_threshold` when the register map marks them writable.
- Timestamped Modbus variable reads are stored in SQLite with device identity and optional run or workflow-step linkage.

### 2. Factory Calibration
The user can run a guided calibration workflow using known reference points. The workflow collects readings, calculates provisional calibration results, validates limits, and records the outcome.

Acceptance criteria:

- Calibration can run end-to-end against simulator data.
- Calibration steps are explicit and resumable after recoverable failures.
- Writes to transmitter parameters are separated from calculation and preview.
- Every calibration run stores raw data, input configuration, calculated outputs, and pass/fail status.
- Zero calibration uses a configurable coil or parameter to start the operation, records `zero_offset`, `delta_t`, and timestamps before and after, and blocks unrelated write-capable operations until the calibration completion state is read.
- K factor calibration supports manual valve-operation timing, records accumulated mass before and after, accepts the standard mass from the operator, calculates `k_s = k_r / m_r * m_s`, and writes the new K factor only through the write guard.

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
- Manual mass-total repeatability supports three operator-entered flow points with three trials per point.
- Each trial records accumulated mass before and after, standard mass, percent error `e = (m_1 - m_2) / m_2 * 100%`, and the repeatability standard deviation for each flow point.

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

### 8. Filling Trial Module
The operator can run a manual Filling Trial workflow for one flowmeter Device
ID without opening a protocol connection. Device ID is selected from the shared
device store; when it does not yet exist, the operator may explicitly create a
neutral `future_adapter` device record. Device ID identifies only the
flowmeter. A separate control/valve label identifies the external controller
and valve combination, and each flowmeter may retain multiple immutable advance
profiles.

Acceptance criteria:

- The operator configures pulse frequency switch point in Hz, mass per pulse,
  mass unit, flow point in g/s, specified mass, and target mass, then enters the
  standard-scale mass separately for each trial.
- A new or reopened trial, including one prepared by `Add Trial`, always has a
  blank standard-mass input. Other fields restore from that Device ID's most
  recent calculated filling trial, not from an unsaved draft.
- Regular trial error is calculated as `(standard_mass - specified_mass) /
  specified_mass * 100`. Repeatability is the sample standard deviation of
  exactly three selected consecutive trial errors.
- Advance calculation accepts at least three selected trials from the active
  group; they need not be consecutive. It stores mean standard mass,
  `advance_mass = mean_standard_mass - specified_mass`, and
  `corrected_target_mass = specified_mass - advance_mass`. Negative advance is
  valid and no pulse total is calculated.
- Every repeatability and advance calculation is stored with source Trial IDs
  and a full configuration snapshot. `Set Advance` creates an immutable
  profile, completes the old group, and atomically opens a corrected regular
  group at blank Trial 1 so uncorrected and corrected trials cannot be mixed.
- Current-device history distinguishes Filling Trial, Filling Repeatability,
  Filling Advance Calculation, and Filling Advance Profile Set records. It
  records inputs, results, source IDs, timestamps, notes, and snapshots without
  inventing pass/fail thresholds.
- v1 uses manual entries only. It does not read pulses, control a valve, write
  a controller, write a transmitter, or generate protocol traffic.

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
| PRD-FR-012 | Support an ASIO/IIS frame-stream module for USB sound-card hardware, including independent connection state, configurable frame format, output, input capture, status/log diagnostics, and loopback verification. | M13 | TP-ASIO-001, TP-ASIO-002, TP-UI-003 |
| PRD-FR-013 | Support a Modbus master operator module with configurable variables, timestamped variable sampling, zero calibration, K factor calibration, and manual mass-total error/repeatability tests. | M3, M5, M7, M8, M11 | TP-PROTO-001, TP-DATA-001, TP-WF-003, TP-CALC-003, TP-SAFE-001 |
| PRD-FR-014 | Support a read-only Modbus listener/sniffer workflow using com0com and hub4com for lab diagnostics after virtual-port tooling is installed and approved. | M14 | TP-PROTO-002 |
| PRD-FR-015 | Provide an independent manual Filling Trial Module with shared flowmeter identity, regular error/repeatability, advance calculation, immutable advance profiles, atomic corrected-group transition, and device-filtered history without hardware communication. | M15 | TP-FILL-CALC-001, TP-FILL-DATA-001, TP-FILL-SVC-001, TP-FILL-UI-001 |

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
- Final addresses, data types, scaling, and writable ranges for `mass_rate`, `mass_acc`, `temperature`, `delta_t`, `zero_offset`, `frequency`, `k_factor`, `low_threshold`, and zero-calibration start/status.
- Whether zero calibration completion is represented by the same start coil returning to zero or by a separate status register on production firmware.
- Whether K factor calibration must verify the written value by readback or by an additional transmitter commit/apply action.
- Lab permission to install and configure com0com/hub4com virtual serial ports and permission to open serial devices for listener diagnostics.
- Exact ASIO driver capabilities for the BRAVO-HD USB sound-card module, including supported sample rates, channel layout, sample formats, and whether the Python audio backend exposes the vendor ASIO driver directly.
- Final production meaning of IIS frame payloads beyond loopback transport validation.

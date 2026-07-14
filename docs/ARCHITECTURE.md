# Architecture

## Summary
CoreFlow Studio is a modular Windows desktop application built with Python and Qt. The architecture separates UI, application services, workflows, devices, protocols, simulation, data processing, and storage so that fixed factory procedures and flexible experiments can run against either simulated transmitters or real USB-serial Modbus RTU hardware. The M15 Filling Trial Module follows the same layering but is deliberately manual-input and communication-free.

## Architectural Principles
- Simulation-first: every core workflow must run without physical hardware.
- Hardware abstraction: workflows call application-level device interfaces, not serial ports directly.
- Workflow-driven automation: calibration and test procedures live in a workflow engine, not in UI event handlers.
- Multi-port isolation: each device channel has independent connection state, error handling, timing, and logs.
- Traceable data: raw data, processed results, reports, and device writes are linked to run metadata.
- Extensible experiments: signal processing, fixture control, and ML modules plug into the same run and storage model.

## Runtime Layers

### Presentation Layer
The Qt desktop UI provides:

- Device and port connection views.
- Workflow launch and progress views.
- Live numeric readings and time-series charts.
- Calibration/test result tables.
- Historical run browser.
- Report/export actions.
- Experiment configuration views.
- A single-page Filling Trial workbench and current-device filling history.

The UI must not contain protocol logic, calibration formulas, direct database writes, or long-running blocking work.

### Application Service Layer
Application services coordinate user actions and domain operations:

- `DeviceManager`: owns configured device sessions and channel lifecycle.
- `WorkflowRunner`: starts, pauses, cancels, resumes, and reports workflow state.
- `AnalysisService`: runs error, stability, and signal-processing calculations.
- `ReportService`: builds report artifacts from stored runs.
- `StorageService`: writes and reads metadata, results, and file references.
- `ExperimentService`: manages flexible experiment definitions and module execution.
- `WriteGuardService`: validates safety-sensitive device write requests before they reach any device adapter.
- `VariableSamplingService`: reads configured device variables and stores timestamped values with device/run/step traceability.
- `FillingTrialService`: owns shared-device selection, filling-group state,
  validation, trial/analysis persistence, immutable advance profiles, history,
  and the atomic Set Advance transition.

### Workflow Layer
Workflows are explicit state machines or step graphs. Each workflow step declares:

- Required inputs.
- Device reads or writes.
- Capture duration or sample count.
- Analysis operations.
- Pass/fail checks.
- Outputs to store.
- Failure behavior.

Initial workflows:

- Calibration preview workflow.
- Calibration write workflow.
- Zero calibration workflow.
- K factor calibration workflow.
- Automated factory test workflow.
- Error analysis workflow.
- Manual mass-total error and repeatability workflow.
- Stability capture workflow.
- Flexible experiment workflow.

### Device Abstraction Layer
The application talks to `FlowmeterDevice` interfaces rather than specific transports.

Required capabilities:

- Connect and disconnect.
- Read device identity and health.
- Read live measurements.
- Read and write configuration parameters through guarded methods.
- Start and stop data capture where supported.
- Report communication diagnostics.

Implementations:

- `SimulatedFlowmeterDevice` for deterministic virtual transmitters.
- `ModbusRtuFlowmeterDevice` for real USB-serial Modbus RTU transmitters.
- `AsioIisFrameTransport` for headless USB sound-card ASIO/IIS frame input and output tests.
- Future adapters for custom UART, Ethernet, or vendor libraries.

Device implementations must not decide whether a calibration workflow is allowed to write. They enforce device-level capabilities and parameter constraints, while workflow state, actor/source, dry-run mode, and audit requirements are coordinated by application services.

The first Modbus-oriented calibration workflows remain headless and device-interface based:

- Zero calibration writes a configured start coil or parameter through the write guard, then polls configured read-only variables until the completion state is observed.
- K factor calibration captures selected pre-operation variables, records one configured flow-rate segment and accumulated-mass boundaries, accepts operator-entered standard mass, calculates the proposed K factor, and writes only through the same guarded application path as other calibration writes.
- Manual error/repeatability testing records operator-configured flow points and standard masses while capturing accumulated-mass boundaries from the same reusable non-zero-to-zero flow-segment process used by K factor calibration. The UI supports both fixed three-flow-range tests and a single-flow-range mode that can append additional trials before saving the current summary.

### Protocol And Transport Layer
The first real protocol adapter is Modbus RTU over serial.

Responsibilities:

- Serial port open/close and configuration.
- Request scheduling per port.
- Timeout, retry, and backoff.
- Register, coil, and discrete-input encoding/decoding where the configured map allows it.
- Diagnostics and protocol error reporting.
- Optional frame logging for debugging.
- Optional Modbus listener/sniffer diagnostics through a future com0com/hub4com virtual-port setup.

Protocol code must not know about calibration workflows or UI widgets.

Register maps, scaling, writable permissions, and acceptance limits must be loaded from configuration or workflow inputs. Production register addresses and calibration thresholds must not be embedded in protocol, workflow, or UI code.

The Modbus master UI should expose configuration in focused dialogs rather than protocol code:

- Serial/channel setup: port identification, baud rate, parity, stop bits, timeout, retries, and unit ID.
- Variable editor: logical variable name, Modbus table type, address, word count, data type, endianness, scale, unit, writable flag, and valid range.
- Calibration dialogs: zero calibration control/status, K factor simple/advanced modes, and repeatability trials in three-flow-range or single-flow-range modes.

### ASIO/IIS Frame Stream Layer
The ASIO/IIS module is a headless hardware I/O boundary for a USB sound-card module that appears in Windows Device Manager as `BRAVO-HD Device Control`.

Responsibilities:

- Discover audio devices and host APIs, and explicitly report whether an ASIO backend is available.
- Open the selected device in full-duplex mode when supported.
- Output deterministic frame payloads over the IIS output path.
- Capture frame payloads from the IIS input path.
- Keep configurable frame parameters outside UI code: sample rate, bit depth or sample format, channel counts, samples per frame, frame count, and test amplitude.
- Run loopback verification when the board's IIS master output is wired to the IIS slave input.
- Report diagnostics such as selected device, host API, detected latency, correlation score, normalized error, dropped or short frames, and backend errors.

The ASIO/IIS module is not a calibration workflow, does not write transmitter parameters, and must not be treated as a Modbus register-map source. It can later feed flexible experiments or device adapters after the frame payload semantics are defined.

The ASIO/IIS UI is a separate window with independent connection state. Connecting or disconnecting this module must not connect, disconnect, block, or reconfigure simulator, replay, serial Modbus, or future transmitter communication channels. The main ASIO/IIS window should show normal-use settings only: detected device, sample rate, bit depth/sample format, input channel count, output channel count, frame size, and drive/test amplitude. The main window should also keep a quick `Probe` action for checking device capabilities. Loopback-specific values such as frame count and latency search window belong in the test workflow or test dialog defaults, not the normal-use parameter panel.

Current signal semantics:

- Input frames are the flowmeter left and right signal channels after ADC conversion. LRCK distinguishes the left and right IIS channels.
- Output frames contain one effective drive-signal channel. The hardware DAC converts that digital stream to the electrical drive signal used by the flowmeter.
- Continuous frame streaming runs in the background. Downstream processing, storage, and live visualization of these streams are deferred until the analysis/display requirements are defined.
- Hardware tests are launched from a separate ASIO/IIS test dialog. The dialog supports loopback and non-loopback checks, lets the user choose a test signal such as sine, square, or white noise with waveform parameters, and displays input and output on the same plot with input-only, output-only, or combined display modes.

### Safety And Write Guard Layer
Write-capable operations pass through a dedicated guard before reaching a simulated or real device.

Responsibilities:

- Confirm the active workflow step is allowed to write.
- Confirm the target parameter is configured and writable.
- Validate type, range, unit, and transform inputs.
- Support preview and dry-run modes.
- Attach actor or automation source information.
- Create audit log records for allowed, rejected, failed, and simulated writes.

The guard sits above the device interface so simulator and hardware writes exercise the same application-level safety behavior.

### Multi-Port Scheduler
The system must support 4-8 concurrent ports.

Design requirements:

- One failing device must not block other devices.
- Each port has independent timeout and retry state.
- Scheduler events are surfaced to the application through signals, async callbacks, or message queues.
- Long operations must not run on the UI thread.
- The scheduler must support simulator channels and real serial channels through the same device interface.

Preferred implementation:

- Use Qt threads, Python worker threads, or asyncio integrated carefully with Qt.
- Choose one concurrency model during implementation and document it.
- Keep device I/O serialized per device unless the protocol adapter explicitly supports concurrency.

### Data Processing Layer
Data processing handles:

- Error calculations against reference values.
- Repeatability and stability metrics.
- Drift and noise estimates.
- Filtering and signal-processing transforms.
- Future ML inference modules.

Processing modules must receive explicit input data and configuration and return structured outputs. They must not read directly from UI widgets or hidden global state.

Filling calculations live in `coreflow.analysis` as pure functions. Trial error,
three-trial sample standard deviation, mean standard mass, signed advance mass,
and corrected target mass accept explicit values and have no Qt, storage,
device, or protocol dependency.

### Storage Layer
Storage uses SQLite for structured records and files for large data artifacts.

SQLite stores:

- Device records.
- Run sessions.
- Workflow steps.
- Calibration results.
- Error and stability metrics.
- Timestamped low-rate variable samples.
- File artifact references.
- Audit log entries.
- Filling trial records and immutable filling advance profiles.

Files store:

- Raw time-series captures.
- High-rate signal data.
- ASIO/IIS frame captures and loopback diagnostic artifacts.
- Exported CSV files.
- Generated reports.
- Replay and simulator scenario files.

See `docs/DATA_MODEL.md` for detailed direction.

### Simulation Layer
The simulator provides virtual transmitters that obey the same device interface as real hardware.

Simulator capabilities:

- Deterministic readings.
- Configurable flow, density, temperature, zero offset, noise, drift, and response delay.
- Fault injection for timeouts, CRC errors, invalid values, disconnections, and parameter-write failures.
- Replay from recorded files.
- Multi-device scenarios for load testing.

See `docs/SIMULATION.md` for details.

### Filling Trial Module Boundary
The independent Filling Trial Module is split across four owned boundaries:

- `coreflow.analysis` implements the pure formulas and numerical validation.
- `coreflow.storage` persists schema v5 filling trials and advance profiles,
  reusing run sessions, workflow steps, and analysis results for provenance.
- `coreflow.app.FillingTrialService` is the headless state machine and the only
  layer allowed to coordinate calculations and persistence.
- `coreflow.ui` collects operator input and renders service snapshots/history.
  Qt widgets do not calculate results or issue SQL.

The selected Device ID always refers to a flowmeter in the shared `devices`
table. It is not a controller ID, valve ID, Modbus unit ID, or COM-derived
identifier. When a needed flowmeter is absent, the module can explicitly create
a `future_adapter` device record. The independent control/valve label describes
the external controller and valve combination; one Device ID may have several
labels and multiple immutable advance profiles.

Each group locks mode and the full parameter snapshot after its first calculated
trial: pulse frequency switch point, mass per pulse, mass unit, flow point,
specified mass, target mass, and control/valve label. The service stores each
trial and each repeatability/advance analysis immediately. `Set Advance` uses
one repository transaction to create a profile, complete the old advance group,
and create a corrected regular group with a blank pending Trial 1. This boundary
prevents trials from the old and corrected target masses from being mixed.

M15 does not call `FlowmeterDevice`, Modbus, ASIO/IIS, serial, pulse, valve, or
controller APIs. The operator conducts the physical filling cycle externally
and manually enters the standard-scale mass. The module reads no pulse total,
controls no valve, writes no controller or transmitter, and creates no protocol
traffic. Any future pulse/controller adapter requires its own protocol,
simulation, capability, safety, audit, and hardware-validation contract.

## Suggested Package Boundaries
The exact file tree will be created during implementation, but the first code pass should preserve these boundaries:

- `coreflow/ui`: Qt views, widgets, models, and controllers.
- `coreflow/app`: application services and orchestration.
- `coreflow/workflows`: workflow definitions and runner.
- `coreflow/devices`: device interfaces and device-level models.
- `coreflow/protocols`: Modbus RTU and future protocol adapters.
- `coreflow/simulation`: virtual transmitter and replay support.
- `coreflow/analysis`: calibration, error, stability, signal-processing, and ML extension interfaces.
- `coreflow/storage`: SQLite repositories and artifact storage.
- `coreflow/reports`: report generation and exports.
- `tests`: unit, integration, simulator, and UI tests.

## Data Flow
Typical factory workflow:

1. User selects device channels and workflow configuration in the UI.
2. UI calls an application service to start a workflow.
3. Workflow runner validates inputs and creates a run session in storage.
4. Workflow steps read from or write to devices through `FlowmeterDevice`.
5. Raw samples are streamed to artifact files.
6. Analysis modules compute results from stored or buffered data.
7. Storage records step results, metrics, and file references.
8. UI receives progress events and displays status.
9. Report service generates final artifacts from stored run data.

Manual Filling Trial data flow:

1. The UI asks `FillingTrialService` to select an existing shared flowmeter
   Device ID or explicitly create a `future_adapter` record.
2. The operator selects a control/valve label and enters the group configuration.
3. The operator runs the physical cycle externally and enters one standard-scale
   mass; the service calls pure analysis code and atomically stores the run,
   completed step, and trial.
4. The operator explicitly adds another blank trial when required.
5. The service stores a repeatability result from exactly three consecutive
   trials or an advance result from at least three selected trials.
6. `Set Advance` atomically persists an immutable profile and returns a new
   corrected regular group. The UI only renders the resulting service snapshot.

## Error Handling
- Communication errors are attached to the affected channel and workflow step.
- Recoverable failures can retry or mark a step failed according to workflow configuration.
- Nonrecoverable failures stop the affected workflow, store a failure result, and keep other device channels running.
- Parameter write failures must be explicit and auditable.
- Simulator-injected failures must use the same error path as real hardware failures.

## Known Unknowns
- Final concurrency implementation choice: Qt threads, Python threads, or asyncio integration.
- Final Modbus register map and scaling.
- Final calibration and stability algorithms.
- Exact report templates.
- Fixture-control architecture once external hardware is specified.

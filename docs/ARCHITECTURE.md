# Architecture

## Summary
CoreFlow Studio is a modular Windows desktop application built with Python and Qt. The architecture separates UI, workflows, devices, protocols, simulation, data processing, and storage so that fixed factory procedures and flexible experiments can run against either simulated transmitters or real USB-serial Modbus RTU hardware.

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
- Automated factory test workflow.
- Error analysis workflow.
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

### Protocol And Transport Layer
The first real protocol adapter is Modbus RTU over serial.

Responsibilities:

- Serial port open/close and configuration.
- Request scheduling per port.
- Timeout, retry, and backoff.
- Register encoding and decoding.
- Diagnostics and protocol error reporting.
- Optional frame logging for debugging.

Protocol code must not know about calibration workflows or UI widgets.

Register maps, scaling, writable permissions, and acceptance limits must be loaded from configuration or workflow inputs. Production register addresses and calibration thresholds must not be embedded in protocol, workflow, or UI code.

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

### Storage Layer
Storage uses SQLite for structured records and files for large data artifacts.

SQLite stores:

- Device records.
- Run sessions.
- Workflow steps.
- Calibration results.
- Error and stability metrics.
- File artifact references.
- Audit log entries.

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

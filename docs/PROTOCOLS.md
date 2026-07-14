# Protocols

## Summary
The first concrete hardware communication path is Modbus RTU over USB-to-serial. Protocol implementation must be isolated behind device interfaces so workflows and UI code can run against simulated devices, real Modbus RTU transmitters, or future protocols without structural rewrites.

## Supported Protocols

### v1 Concrete Protocol
- Modbus RTU over serial.
- Windows USB-to-serial adapters.
- Configurable serial settings.
- Configurable Modbus unit IDs.
- Configurable register maps.
- Modbus master operation for configured variable reads and guarded calibration writes.
- Scriptable Modbus raw-frame access through `coreflow.modbus_api.ModbusRawClient`
  and `CoreFlowStudioConsole.exe --modbus-raw` for local lab automation.

### Future Modbus Listener Diagnostics
- Modbus listener or sniffer mode using com0com plus hub4com virtual serial ports.
- Intended for lab diagnostics and protocol visibility, not for production calibration decisions.
- Requires operator permission to install/configure virtual serial-port drivers and to open the relevant serial endpoints.
- Must be implemented as a separate diagnostic tool path so it cannot silently proxy or modify transmitter traffic.

### Post-M12 Hardware Frame Stream
- ASIO-backed USB sound-card frame I/O for IIS lab testing.
- Windows Device Manager name: `BRAVO-HD Device Control`.
- Full-duplex output and input when the driver exposes compatible channels.
- Configurable sample rate, bit depth or sample format, input/output channel counts, samples per frame, frame count, and signal amplitude.
- Headless diagnostics first; UI controls are deferred until the hardware module passes acceptance tests.
- On the current lab PC, the registered native BRAVO-HD ASIO driver reports 44100 Hz, 2 input channels, 2 output channels, ASIOSTInt24LSB sample data, and a preferred 4410-sample buffer.

### Future Protocol Adapters
- Custom UART frame protocol.
- Ethernet or TCP-based protocol.
- Vendor DLL or SDK adapter.
- Pulse-acquisition adapter for validated flowmeter pulse-output hardware.
- Controller/valve adapter for a separately specified and safety-reviewed
  closing-control protocol.

Future adapters must implement the same application-level device interface.

The ASIO/IIS frame stream is lower-level than the `FlowmeterDevice` interface until payload semantics are defined. It should expose a transport-style frame API and may later be wrapped by an experiment module or device adapter.

### M15 Filling Trial Communication Boundary
The Filling Trial Module performs no communication in M15. Its Device ID is
selected from the shared device store and identifies only the flowmeter under
test. A control/valve label is descriptive metadata for the external controller
and valve combination; it is not a protocol endpoint or another Device ID.

The operator manually enters the configured pulse context and the final
standard-scale mass after running the physical cycle outside CoreFlow Studio.
The module does not:

- Open or reuse a Modbus, serial, ASIO/IIS, simulator-device, or replay
  connection.
- Read, count, calculate, or display pulse totals.
- Detect a pulse switch point, valve-close command, or final valve closure.
- Write target mass, advance mass, pulse settings, or any other value to a
  controller, valve, flowmeter, or transmitter.
- Add Modbus requests, serial frames, or any other protocol traffic.

Future pulse acquisition or controller/valve control is a protocol-adapter TODO,
not an extension of the M15 manual-input service. Before it is enabled, a
separate design must define electrical and frame contracts, capability
reporting, deterministic fake/simulator behavior, timeout/failure handling,
write guard and audit requirements, operator arming, and real-hardware
acceptance. Values or timing invented for a manual test or simulator must never
silently become hardware defaults.

## Device Interface Expectations
Protocol adapters must support these device-level operations where the transmitter and register map allow them:

- Connect.
- Disconnect.
- Read device identity.
- Read device health and diagnostics.
- Read live measurements.
- Read calibration/configuration parameters.
- Preview writable parameter changes.
- Write guarded calibration/configuration parameters.
- Return communication diagnostics.

If a protocol cannot support an operation, it must report a clear capability error rather than silently doing nothing.

## Serial Configuration
Each serial channel must have independent configuration:

- Port name, such as `COM3`.
- Baud rate.
- Data bits.
- Parity.
- Stop bits.
- Read timeout.
- Write timeout.
- Retry count.
- Modbus unit ID.

Default values should live in configuration files or UI defaults, not in protocol logic.

## Modbus Register Map
The real register map is a known unknown. The implementation must load register definitions from configuration.

Each register definition should include:

- Logical name.
- Register type: holding, input, coil, or discrete input.
- Address.
- Word count.
- Data type.
- Endianness.
- Scale factor or transform.
- Unit.
- Read/write permission.
- Valid range for writable values.
- Description.
- Metadata marking template, simulator, customer, or firmware-documentation source.

The application should not hard-code production register addresses in workflow code.

Initial register-map files should be JSON or YAML and versioned as configuration artifacts. Each workflow run must store the register-map version or snapshot used for device communication.

The UI-facing variable editor should allow users to add, edit, and remove logical variables before connecting to hardware. Edits must update a configuration artifact or runtime snapshot; they must not patch protocol code.

## Measurement Register Groups
The first register-map template should reserve logical names for:

- Mass rate.
- Accumulated mass.
- Mass flow.
- Volume flow.
- Density.
- Temperature.
- Delta T.
- Drive gain or drive status if available.
- Tube frequency or signal quality if available.
- Alarm flags.
- Device status.
- Zero offset or calibration state.
- K factor.
- Low threshold.
- Zero-calibration start/status coil or parameter.

These names are logical placeholders until firmware documentation provides addresses and scaling.

## Timeout And Retry Policy
Each request should use:

- Per-channel timeout.
- Configurable retry count.
- Backoff between retries.
- Clear final failure state.
- Diagnostic record for each failed request.

Timeouts on one channel must not block unrelated channels.

## Write Safety
Parameter writes are safety-sensitive.

Required behavior:

- Writable registers must be explicitly marked writable in the register map.
- Writes must validate type, range, and workflow state before sending.
- Calibration workflows must support preview before write.
- Every write attempt must be logged with timestamp, device identity, register name, previous value when available, new value, result, and operator or automation source.
- Simulator write behavior must match the same code path used for real devices at the application level.
- Zero calibration must treat the configured start coil or parameter as write-capable and must poll only configured read-capable variables for completion.
- K factor calibration must calculate the proposed value from recorded flow-segment accumulated-mass inputs and standard mass, then use a guarded write to the configured K factor parameter.

Write-capable workflows should use these states:

- `preview`: calculate proposed changes and store them without device modification.
- `dry_run`: validate and audit proposed writes without transmitting a write request.
- `armed`: allow a specific workflow step to send validated writes.
- `applied`: record successful device write and resulting verification read when available.
- `rejected`: record validation failure, permission failure, or operator denial.

Protocol adapters must only receive write requests after application-level validation has succeeded. They still must enforce register-map permissions and type/range validation as a final local check.

The scriptable raw-frame API is a diagnostics and integration surface. It can
send Modbus write function codes when a caller explicitly supplies such a
frame, but it does not replace guarded calibration workflows or audited
parameter-write operations.

## Scriptable Modbus API
External Python tools can import `coreflow.modbus_api.ModbusRawClient` to open a
local Modbus RTU serial connection and send one or more raw frames without
driving the Qt window. The packaged console executable exposes the same path via
`--modbus-raw` for non-Python callers.

Standard function codes `01`, `02`, `03`, `04`, `05`, `06`, `0F`, and `10` are
routed through the same high-level communication methods used by the Modbus
Module read/write controls, then returned as raw response bytes. Non-standard
or invalid-CRC frames fall back to the low-level raw send path for diagnostics.

See `docs/MODBUS_API.md` for caller examples and CLI options.

## Diagnostics
Protocol adapters must expose:

- Request count.
- Successful response count.
- Timeout count.
- CRC or frame error count when available.
- Exception response count.
- Last error.
- Last successful response time.
- Average response time.

Diagnostics should be visible in the UI and stored when they affect workflow outcomes.

## Port Discovery
The Windows implementation should enumerate serial ports and display enough information to identify adapters:

- COM port name.
- USB vendor/product information when available.
- Adapter serial number when available.
- Current connection status.

Port discovery must not assume that COM port numbers are stable between machines or reconnects.

Port discovery is advisory only. The persisted device record should include the last known port metadata, but device identity read from the transmitter or simulator is the stronger identifier when available.

## Modbus Master Operator Workflows

The Modbus master module supports these headless workflow contracts before UI wiring:

- Variable sampling: read configured logical variables and persist the value, timestamp, device identity, source channel, and optional run/step reference.
- Zero calibration: record `zero_offset` and `delta_t` before start, write the configured start coil/parameter through the write guard, poll until the configured completion state is read, then record `zero_offset`, `delta_t`, completion state, and timestamps.
- K factor calibration: capture selected pre-operation variables, read accumulated mass `m1` and current K factor, poll the configured flow-rate variable until a non-zero flow segment starts and then returns to zero, record the instantaneous flow sample and ending accumulated mass `m2`, accept standard mass from the operator, calculate `k_s = k_r / m_r * m_s`, and apply the new value only through the write guard with readback verification when requested.
- Error/repeatability: in Three Flow Ranges mode, run three configured target-flow ranges with three trials per range. Each trial first reads the operator-selected pre-trial variables plus the configured K factor variable, notifies the operator that the trial can start, captures accumulated mass before/after one reusable non-zero-to-zero flow segment, records the automatically read original K, `v1`, `v_mean`, and flow start/instant/end timestamps, accepts the operator's standard-scale mass, calculates trial percent error `e = (delta_m - standard_mass) / standard_mass * 100%`, and stores the trial when the operator clicks `Calculate Trial Error`; the trial Test Records timestamp is the earlier `Capture Trial` click time, while the calculation/save timestamp and the flow-segment timestamps remain stored as trial metrics. Extra trials may be appended after the base 9 trials and must not delete or overwrite earlier trial records. `Calculate Repeatability` lets the operator choose one flow point and one consecutive three-trial window from the current operation; the displayed `mean` is `(e1 + e2 + e3) / 3` for that selected flow point, repeatability is the sample standard deviation of those three errors, and the repeatability Test Records timestamp is the calculation/save time while selected trial time range metrics remain traceable. `Calculate Final K` uses three selected flow points and 9 selected trials. For each flow point, `measurement_error` is the mean of that point's selected three trial errors; `average_error = (max(measurement_errors) + min(measurement_errors)) / 2`; `adjusted_error = measurement_error - average_error`; `intermediate_k = original_k / (1 + measurement_error / 100)`; and `new_k = (max(intermediate_k_values) + min(intermediate_k_values)) / 2`. Recalculation overwrites only the previous final-K record. `Write New K...` is a separate operator-confirmed action after the preview exists; it writes through the write guard, reads back the K factor variable, records verification status and audit ID, and updates the same final-K record. Single Flow Range mode uses one configured target-flow range, allows the operator to append more trials at any time, and refreshes the current error/repeatability summary after each calculated trial. Advanced mode is reserved for future multi-run or fitting logic.
- Lab-only PC flow simulation: K factor and repeatability captures may replace the flow-segment state and calculated accumulated-mass delta with operator-entered PC values while still issuing the configured Modbus reads to the connected slave. This path is explicit, does not write the flow-rate variable to the slave, and marks captured history with `flow_rate_source=pc_simulated`.

## Modbus Listener Diagnostics

The listener/sniffer path is deferred until a lab PC has com0com and hub4com installed and approved.

Required constraints:

- Listener setup must be explicit and separate from normal Modbus master connections.
- The tool must report which virtual COM pair and hub4com route are active.
- Captured frames should be stored as diagnostic artifacts when attached to a run.
- The listener must default to read-only observation. Any future proxy or injection capability requires a separate safety review.
- Automated tests must use fake serial endpoints or recorded frames before opening virtual COM ports.

## Future Protocol Plugin Rules
Future protocol adapters must:

- Implement the same device interface used by Modbus and simulator devices.
- Provide capability metadata.
- Reuse shared diagnostics and audit logging.
- Avoid direct UI dependencies.
- Include fake or simulator-backed tests before hardware tests.
- Treat simulation values as labeled test inputs until approved hardware
  documentation supplies the production contract.

## ASIO/IIS Frame Stream Contract
The ASIO/IIS module must keep hardware settings explicit and traceable.

Configuration fields:

- Device name or alias.
- Required host API, normally `ASIO`.
- Sample rate in Hz.
- Sample format or bit depth.
- Input and output channel counts.
- Input and output channel offsets when needed.
- Samples per frame.
- Number of frames for a test run.
- Test amplitude and acceptance thresholds.

Runtime behavior:

- Optional ASIO dependencies are imported lazily.
- Missing dependencies, missing ASIO host API, missing device, and incompatible channel counts are distinct errors.
- Hardware access is headless and must not block the Qt UI thread when integrated later.
- Loopback tests use deterministic payloads and compensate for hardware latency before comparing captured input with generated output.
- Captured raw data and diagnostics should be stored as artifacts when run from an acceptance workflow.
- The UI-facing connection state belongs to the ASIO/IIS module only and is independent from transmitter communication adapters.
- Device selection in the UI should be populated from discovered ASIO drivers or audio backend devices rather than typed manually.
- Normal-use UI channel selection should be presented as choices up to 2 channels. Test-only values, including frame count and latency search window, should be owned by test dialogs or defaults.
- The ASIO/IIS test dialog should generate explicit test signals, initially sine, square, and white noise, and show input/output traces on the same plot so users can visually confirm signal path behavior.

Signal semantics:

- IIS input carries two ADC-derived flowmeter signal channels, left and right, separated by LRCK.
- IIS output carries one effective digital drive-signal channel; after DAC conversion this becomes the flowmeter electrical drive signal.
- Until processing and visualization requirements are finalized, continuous stream data stays behind the ASIO/IIS module boundary and is summarized only as status and diagnostics.

## Known Unknowns
- Final transmitter register map.
- Required Modbus function codes.
- Serial defaults for production hardware.
- Device write commit/apply semantics.
- Exact zero calibration start and completion semantics.
- Whether com0com/hub4com will be installed globally on the target lab PC or packaged/documented as an external prerequisite.
- Whether production high-rate signal capture uses Modbus, UART, ASIO/IIS frame streaming, or another path.
- Whether BRAVO-HD ASIO sample formats, channel ordering, and stable device alias remain the same across driver revisions and other lab PCs.
- Pulse-output electrical characteristics, acquisition hardware, counter
  semantics, and switch-point timing for a future Filling Trial adapter.
- Controller/valve protocol, write permissions, arming rules, acknowledgement,
  timing, failure recovery, and audit requirements for a future automated
  filling workflow.

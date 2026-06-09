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

Future adapters must implement the same application-level device interface.

The ASIO/IIS frame stream is lower-level than the `FlowmeterDevice` interface until payload semantics are defined. It should expose a transport-style frame API and may later be wrapped by an experiment module or device adapter.

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

The application should not hard-code production register addresses in workflow code.

Initial register-map files should be JSON or YAML and versioned as configuration artifacts. Each workflow run must store the register-map version or snapshot used for device communication.

## Measurement Register Groups
The first register-map template should reserve logical names for:

- Mass flow.
- Volume flow.
- Density.
- Temperature.
- Drive gain or drive status if available.
- Tube frequency or signal quality if available.
- Alarm flags.
- Device status.
- Zero offset or calibration state.

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

Write-capable workflows should use these states:

- `preview`: calculate proposed changes and store them without device modification.
- `dry_run`: validate and audit proposed writes without transmitting a write request.
- `armed`: allow a specific workflow step to send validated writes.
- `applied`: record successful device write and resulting verification read when available.
- `rejected`: record validation failure, permission failure, or operator denial.

Protocol adapters must only receive write requests after application-level validation has succeeded. They still must enforce register-map permissions and type/range validation as a final local check.

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

## Future Protocol Plugin Rules
Future protocol adapters must:

- Implement the same device interface used by Modbus and simulator devices.
- Provide capability metadata.
- Reuse shared diagnostics and audit logging.
- Avoid direct UI dependencies.
- Include fake or simulator-backed tests before hardware tests.

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
- Whether production high-rate signal capture uses Modbus, UART, ASIO/IIS frame streaming, or another path.
- Whether BRAVO-HD ASIO sample formats, channel ordering, and stable device alias remain the same across driver revisions and other lab PCs.

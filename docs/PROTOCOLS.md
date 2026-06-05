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

### Future Protocol Adapters
- Custom UART frame protocol.
- IIS-like or streaming signal path if required by hardware.
- Ethernet or TCP-based protocol.
- Vendor DLL or SDK adapter.

Future adapters must implement the same application-level device interface.

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

## Known Unknowns
- Final transmitter register map.
- Required Modbus function codes.
- Serial defaults for production hardware.
- Device write commit/apply semantics.
- Whether high-rate signal capture uses Modbus, UART, IIS-like streaming, or another path.

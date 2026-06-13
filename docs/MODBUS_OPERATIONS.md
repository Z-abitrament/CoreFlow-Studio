# Modbus Operation Logic

This document describes the current standalone Modbus Module behavior for
operators, developers, and AI-assisted future edits. It is the working contract
for the module UI, runtime services, calibration history, and safety behavior.

## Scope

The standalone Modbus Module is a direct Modbus RTU master window. It is
independent from the main simulator/replay device list and keeps its own
connection state, variable map, operation dialogs, communication-frame log, and
calibration history browser.

This document covers implemented operation logic only. It does not define
production transmitter register maps, production calibration acceptance
thresholds, fixture timing, or customer report templates.

## Shared Operation Context

The Modbus window has three operator-entered device context fields:

- Device Model
- Tube Model
- Transmitter Model

The runtime stores these fields as `ModbusOperationMetadata`. When an operation
writes calibration history, the metadata snapshot for that operation is attached
to the run configuration and analysis summary metrics with these keys:

- `device_model`
- `tube_model`
- `transmitter_model`

The snapshot is taken at the point where the operation is started or the final
result is calculated. Later edits in the UI must not rewrite a completed
operation's history. The calibration history detail pane displays the fields
under `Device Metadata`, and JSON export/import preserves them through the stored
run and analysis records.

## Connection And Variable Map

The operator opens `Connection...`, selects a serial port, unit ID, serial
settings, timeout/retry settings, and byte/word order. The runtime creates a
`ModbusRtuFlowmeterDevice` using the current variable map and transport.

The `Variable Map` table is editable only while disconnected. It defines:

- variable name
- Modbus table kind
- address
- word count
- data type
- scale
- unit
- writable flag
- poll selection

Saved maps are stored in the user data directory under:

```text
config/register_maps/modbus_module_map.json
```

Polling and operation reads use the configured map. Adjacent reads may be merged
when they are in the same Modbus table and merging is safe for the operation.

## Sample Variables

`Sample Variables` reads the standard sample set from the connected device,
updates the `Value` column, and stores variable samples in SQLite. A failed
variable read is logged as a warning; successful samples keep their timestamp,
variable name, value, unit, and source metadata.

This operation does not create calibration history.

## Zero Calibration

`Zero Cal` opens a dialog with selectable pre-calibration snapshot variables.
When the operator clicks `Start`:

1. The UI records the selected snapshot variables and current device metadata.
2. The runtime reads the selected snapshot variables.
3. The runtime reads `zero_offset` and `delta_t` before the operation.
4. The runtime requests a write to `zero_calibration_start` through write guard.
5. The runtime waits for the configured completion delay.
6. The runtime reads completion state plus post-calibration `zero_offset` and
   `delta_t`.
7. The UI refreshes the main variable map values for the result variables.
8. A calibration run and analysis result are stored in SQLite.

The current implementation uses the configured write guard and records audit
data for the zero-start write. It does not apply production acceptance
thresholds; the operator reviews the before/after values.

History operation name:

```text
zero_calibration
```

## K Factor Simple Mode

`K Factor` opens a dedicated dialog. `Simple` is the implemented mode; advanced
mode is reserved.

The operator selects:

- flow-rate variable
- flow-accumulator variable
- K factor variable
- poll interval
- optional snapshot variables
- standard mass
- whether to record calibration history
- whether to write the corrected K factor to the device

When the operator clicks `Start`, the runtime captures one flow segment:

1. Capture optional pre-calibration snapshot variables.
2. Read the initial mass accumulator and current K factor.
3. Wait for a nonzero flow-rate segment.
4. Record the instant-flow sample after the configured post-start sample delay.
5. Wait for the flow rate to return to zero.
6. Read the final mass accumulator after the configured post-stop delay.

When the operator clicks `Calculate`, the runtime computes:

```text
measured_mass_delta = mass_acc_after - mass_acc_before
K1 = K0 / measured_mass_delta * standard_mass
mean_flow = measured_mass_delta / segment_duration_s
```

If history recording is enabled, the result is stored as a calibration run and
analysis result. If device write is enabled, the runtime applies the corrected K
factor through write guard, reads it back, marks verification status, and updates
the same history run.

History operation name:

```text
k_factor_calibration
```

## Repeatability Simple Mode

`Repeatability` opens a dedicated dialog. The implemented modes are:

- `Three Flow Ranges`
- `Single Flow Range`

Advanced mode is reserved.

Each trial captures one flow segment using the selected flow-rate and
flow-accumulator variables. The runtime records:

- target flow point
- trial index
- mass accumulator before and after the segment
- measured mass delta
- standard mass entered by the operator
- instant flow after the configured post-start sample delay
- mean flow across the captured segment
- percent error

Trial percent error is:

```text
e = (measured_mass_delta - standard_mass) / standard_mass * 100%
```

For `Three Flow Ranges`, the complete result contains 3 flow points and 3 trials
per flow point. After each third trial at a flow point, the UI shows the
repeatability summary for that point. After 9 trials, the runtime stores the
history result when history recording is enabled.

For `Single Flow Range`, the operator may save any number of trials for one flow
point. The UI refreshes the current summary after each saved trial. `Save
Summary` stores the current set of trials as history.

Repeatability per flow point is the sample standard deviation of the trial
percent errors. The history summary also stores mean percent error, maximum
absolute percent error, trial count, per-flow summaries, and trial details.

History operation name:

```text
manual_error_repeatability
```

## PC Simulated Flow Segment

K Factor and Repeatability include `PC simulate flow segment` for lab checks when
a Modbus slave is connected but real flow is not available.

When enabled:

- The runtime still reads the configured flow-rate and accumulator variables
  from the Modbus slave so communication is exercised.
- The PC supplies the flow-segment state used for start/instant/stop timing.
- `PC Sim Delta m` may supply the measured mass delta used in calculations.
- The runtime does not write the flow-rate variable to the slave.
- Saved history is marked with `flow_rate_source=pc_simulated`.

This mode is not a production calibration substitute.

## Calibration History

Calibration history is derived from SQLite run sessions and analysis results.
Only these workflow names are shown:

- `zero_calibration`
- `k_factor_calibration`
- `manual_error_repeatability`

The history table shows timestamp, operation, run ID, parameter summary, and
operator notes. The detail pane shows:

- basic run metadata
- result summary
- device metadata
- pre-calibration snapshot
- remaining metrics

Notes are stored on the run session. JSON export writes a portable package that
includes device records, run sessions, workflow steps, and analysis results.
Import skips exact duplicate runs and renames conflicting imported run IDs.

Excel export is reserved for a later release.

## Safety Rules For Future Operation Changes

Future edits must preserve these rules:

- Do not write device parameters without write guard and audit logging.
- Do not silently treat placeholder register maps as production maps.
- Keep operation metadata snapshots stable for the history entry being written.
- Keep runtime logic testable without the Qt UI.
- Store formulas, thresholds, fixture behavior, and customer-specific rules as
  configuration or explicitly documented implementation, not hidden constants.
- Update this document whenever operation sequence, calculation, persistence, or
  history fields change.

# Pulse Counter Module Design

## Goal
Add an independent Pulse Counter module that can run alongside the Modbus
module, uses its own device/configuration state, and stores pulse calibration
history under the same stable Device ID concept.

## Scope
The first UI slice supports DSView CSV imports only. It does not open DSLogic
hardware or implement a live driver. Future live pulse input should feed the
same `coreflow.pulse_counter` analysis API.

## Module Boundary
Pulse Counter is a peer module to Modbus and ASIO/IIS. It does not reuse the
Modbus connection, serial worker, register map, or session state. Operators can
open Modbus and Pulse modules during the same application session, and each
module keeps its own controls and history.

## Device ID And Configuration
Pulse configuration is saved per stable Device ID. Each Pulse profile stores
the channel, edge selection, pulse value, unit, fixed switch frequency, optional
boundary tolerance, and notes. Switching Device ID reloads that device's Pulse
configuration instead of applying a global default.

## Pulse History
Pulse history follows the Modbus Test Records shape where practical. Each CSV
analysis/trial record stores the Device ID, operation type, status, timestamps,
operator, summary metrics, raw source/artifact references when available, and
notes. Trial calculation uses operator-entered standard mass and pulse-derived
measured mass:

```text
measured_mass = pulse_count * pulse_value
error_percent = (measured_mass - standard_mass) / standard_mass * 100
```

Repeatability for a selected set of trials is the sample standard deviation of
the selected percent errors.

## Cross-Module Device History
Module-local histories remain available: Modbus Test Records show Modbus
records, and Pulse Records show Pulse records. A new device-level history view
aggregates records by Device ID and includes a module column so operators can
filter All, Modbus, or Pulse.

## Validation
Local validation covers storage, pulse calculations, UI module independence,
Pulse CSV import/trial save, and cross-module history aggregation. Packaged
build verification remains required before handing an executable to operators.

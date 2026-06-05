# Simulation

## Summary
Simulation is the primary v1 development path. Every calibration, factory test, stability analysis, and flexible experiment workflow must run against simulated transmitters before real hardware is connected.

The simulator must implement the same application-level device interface as real Modbus RTU transmitters.

## Goals
- Enable full workflow development without physical instruments.
- Make tests deterministic and repeatable.
- Support 4-8 concurrent virtual transmitters.
- Exercise normal behavior, noisy behavior, drift, delays, dropouts, invalid values, and write failures.
- Provide replay support from recorded or generated data files.
- Keep simulator assumptions explicit so they can be replaced by real hardware configuration later.

## Simulator Device Model
Each simulated transmitter should expose:

- Device identity.
- Firmware and hardware version placeholders.
- Live mass flow.
- Live volume flow.
- Density.
- Temperature.
- Status flags.
- Alarm flags.
- Calibration parameters.
- Communication diagnostics.

Values should be generated from scenario configuration and deterministic random seeds.

## Scenario Configuration
A simulator scenario should define:

- Device identity fields.
- Nominal flow profile.
- Density and temperature profile.
- Noise level.
- Drift rate.
- Zero offset.
- Response delay.
- Fault schedule.
- Writable parameter behavior.
- Seed for deterministic random behavior.

Use JSON for initial scenario files.

Scenario files are test inputs, not production hardware specifications. When a scenario includes calibration thresholds, register-like logical names, or fault behavior, those values must be labeled as simulator assumptions unless they come from approved hardware documentation.

## Flow Profiles
Initial profile types:

- Constant value.
- Step sequence.
- Ramp.
- Sine or periodic variation.
- Replay from file.

Profiles should produce timestamped values so stability and drift analysis can be tested.

## Fault Injection
The simulator must support:

- Timeout.
- Disconnection.
- Invalid measurement values.
- Alarm/status flag changes.
- Delayed responses.
- Write rejection.
- Parameter value out of range.
- Sudden offset change.
- Slow drift.
- Dropout segments in time-series capture.

Faults should be configurable by time, sample count, workflow step, or explicit test trigger.

## Multi-Port Simulation
The simulator manager must support 4-8 virtual devices concurrently.

Requirements:

- Independent scenario per virtual device.
- Independent fault state per virtual device.
- One failing virtual device must not block others.
- Tests must cover at least 8 concurrent virtual devices.

## Replay Mode
Replay mode reads previously captured or generated data.

Requirements:

- Preserve original timestamps where useful.
- Optionally play at real time or accelerated test time.
- Attach replay source path to run metadata.
- Support deterministic re-analysis from stored replay files.

## Write Behavior
Simulated writes must follow the same application-level safety path as real writes.

Required behavior:

- Validate writable parameter definitions.
- Apply configured range checks.
- Support preview-only calibration results.
- Record audit logs for simulated write attempts.
- Allow scenario-driven write failure.

Simulated writes may update virtual device state for workflow testing, but they must not bypass preview, dry-run, workflow-state, register-permission, range-validation, or audit-log requirements.

## Traceability
Simulator-backed runs must store:

- Scenario name, version, and seed.
- Fault schedule used during the run.
- Replay file reference when replay mode is active.
- Generated raw data artifacts when captures are used for analysis.
- Configuration snapshots used for thresholds or register-like logical names.

## Testing Uses
The simulator is required for:

- Workflow integration tests.
- UI smoke tests.
- Multi-port scheduler tests.
- Error and stability calculation tests.
- Report generation tests.
- Regression tests for communication failure behavior.

## Known Unknowns
- Exact transmitter measurement set.
- Exact calibration parameter behavior.
- Whether high-rate internal signal data can be simulated from physical models or only from replay files.
- Required fidelity for DSP-level or sensor-level experiments.

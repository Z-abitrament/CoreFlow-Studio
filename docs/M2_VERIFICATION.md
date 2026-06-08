# M2 Verification

## Scope
M2 implements the simulator foundation required before real hardware work. It keeps simulator behavior behind the M1 `FlowmeterDevice` interface and does not implement workflow execution, Modbus communication, storage repositories, replay files, or UI.

## Implemented
- `SimulatedFlowmeterDevice` implementing `FlowmeterDevice`.
- `SimulatorScenario`, flow profiles, scenario parameters, and scheduled fault rules.
- Deterministic readings from seeded scenarios.
- Configurable constant, step, ramp, and sine flow profiles.
- Configurable density, temperature, zero offset, noise, drift, response delay diagnostics, and writable parameters.
- Fault injection for timeout, disconnection, invalid values, alarm flags, response delay diagnostics, and write failure.
- `SimulatorManager` for multi-device scenarios and fault isolation.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest
```

## Results
- Pytest passed: 21 tests passed.
- M0 entry-point tests still pass.
- M1 interface and model tests still pass.
- M2 simulator tests cover `TP-SIM-001` and `TP-SIM-002`.

## Notes
- Timeout faults do not consume a measurement sample in M2. Tests avoid relying on retry policy details, which remain a workflow/protocol concern.
- Simulated writes validate configured permissions and ranges. Full application-level write guards and audit persistence remain planned for M5.
- Replay-file support and high-rate signal simulation remain future simulator work.

# M6 Verification

## Scope
M6 implements the automated factory test workflow foundation. It does not implement final production acceptance thresholds, report templates, UI, or hardware acceptance.

## Implemented
- `FactoryTestWorkflow` that runs headlessly against any `FlowmeterDevice`.
- Configuration models for measurement checks and stability checks.
- Communication health step.
- Device identity and configuration capture step.
- Measurement check against configured reference mass flow and max absolute error.
- Stability segment with range and optional standard-deviation limits.
- Step-level pass/fail records in SQLite.
- Raw CSV artifacts for measurement and stability samples.
- Report-ready analysis summary record with run-level pass/fail status.
- Simulator-backed tests including multi-device fault isolation.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest
```

## Results
- Pytest passed: 43 tests passed.
- M0 entry-point tests still pass.
- M1 interface/model tests still pass.
- M2 simulator tests still pass.
- M3 Modbus protocol tests still pass.
- M4 storage tests still pass.
- M5 calibration preview and write-guard tests still pass.
- M6 workflow tests cover `TP-WF-002`.

## Notes
- Acceptance thresholds are workflow inputs, not production defaults.
- Stability calculations are basic workflow checks; full analysis modules remain planned for M7.
- Generated run records are report-ready but report rendering remains planned for M9.

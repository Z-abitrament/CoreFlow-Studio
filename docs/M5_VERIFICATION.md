# M5 Verification

## Scope
M5 implements calibration preview workflow foundation and application-level write guarding. It does not implement production calibration formulas, a full workflow engine, calibration apply workflow, report generation, UI, or hardware acceptance.

## Implemented
- Calibration reference point, collected measurement, and preview result models.
- `CalibrationCalculator` interface.
- `PlaceholderCalibrationCalculator` that calculates mean error and proposed `zero_offset` preview writes without claiming production validity.
- `WriteGuardService` for preview, dry-run, armed-state validation, range checks, writable checks, and audit persistence.
- `CalibrationPreviewWorkflow` that runs headlessly against any `FlowmeterDevice`.
- Simulator-backed calibration preview that stores device metadata, run session, workflow steps, raw CSV artifacts, analysis result, and audit records.
- Package-level import cycle cleanup so storage models and repositories remain independent from workflow implementation.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest
```

## Results
- Pytest passed: 40 tests passed.
- M0 entry-point tests still pass.
- M1 interface/model tests still pass.
- M2 simulator tests still pass.
- M3 Modbus protocol tests still pass.
- M4 storage tests still pass.
- M5 workflow and safety tests cover `TP-WF-001` and `TP-SAFE-001`.

## Notes
- Placeholder calibration output must be replaced with approved production formulas before real calibration use.
- Calibration preview creates proposed writes and audit records, but does not apply device parameter changes.
- Full calibration write workflow remains future work after the write guard is integrated with explicit operator approval.

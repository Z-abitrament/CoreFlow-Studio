# M1 Verification

## Scope
M1 defines the first stable core domain interfaces and value objects. It does not implement simulator behavior, Modbus communication, storage repositories, workflow execution, or UI.

## Implemented
- `FlowmeterDevice` abstract interface.
- Device identity, health, measurement, configuration, communication diagnostic, and parameter-write models.
- Workflow run and step status models.
- Storage artifact model.
- Tests proving domain models can be instantiated and workflows can depend on `FlowmeterDevice` without importing simulator or Modbus code.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest
```

## Results
- Pytest passed: 12 tests passed.
- Existing M0 entry-point tests still pass.
- New M1 interface and model tests pass.

## Notes
- Device write models describe preview, dry-run, armed, applied, rejected, and failed outcomes, but do not implement write guards yet. Guard behavior remains planned for M5.
- M2 should implement `SimulatedFlowmeterDevice` using the M1 interface without changing workflow-facing contracts.
- Test coverage maps to `TP-M1-001` in `docs/TEST_PLAN.md`.

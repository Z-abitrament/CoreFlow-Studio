# M4 Verification

## Scope
M4 implements the local storage foundation. It does not implement workflow execution, report generation, packaging, or UI.

## Implemented
- SQLite database initialization with schema migration baseline.
- Tables for devices, run sessions, workflow steps, analysis results, artifacts, audit logs, and schema migrations.
- Repository facade for saving devices, runs, steps, analysis results, artifact metadata, and audit logs.
- Artifact file store using the documented `artifacts/runs/YYYY/MM/RUN-YYYYMMDD-NNNNNN/` layout.
- Human-sortable run ID helper.
- Artifact checksums, size recording, relative paths from data root, and missing-file integrity checks.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest
```

## Results
- Pytest passed: 35 tests passed.
- M0 entry-point tests still pass.
- M1 interface/model tests still pass.
- M2 simulator tests still pass.
- M3 Modbus protocol tests still pass.
- M4 storage tests cover `TP-DATA-001`.

## Notes
- SQLite uses the Python standard library `sqlite3`.
- Full backup/restore tooling remains future work.
- Audit persistence is available, while full application-level write guard orchestration remains planned for M5.

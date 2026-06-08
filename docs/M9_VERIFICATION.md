# M9 Verification

## Scope
M9 implements the first report and export artifact generation path for simulator-backed calibration preview and factory test runs. It does not implement customer-specific report templates, PDF/DOCX rendering, electronic signatures, retention policy, or regulatory export schemas.

## Implemented
- `coreflow.reports.ReportExportService` for stored run export.
- Operator-readable plain-text report artifact.
- Metrics CSV export from stored analysis results.
- Measurement CSV export aggregated from raw run CSV artifacts.
- Export manifest JSON with run metadata, device metadata, workflow steps, source artifacts, and generated artifacts.
- SQLite artifact records for generated report and export files.
- Runtime method for generating export packages from selected runs.
- Qt UI `Generate Export` action for completed run inspection.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest tests\test_report_export_service.py tests\test_ui_main_window.py -q
conda run -n coreflow-studio python -m pytest -q
```

## Results
- Report/export tests passed: 5 tests passed.
- Full test suite passed: 58 tests passed.
- M9 covers `TP-RPT-001` for simulator-backed calibration preview and factory test runs.

## Notes
- CSV remains the first export format for tabular data.
- Plain text is used for the initial operator-readable report because final customer or regulatory report format is still a known unknown.
- Generated artifacts are stored under the existing run artifact directory and referenced from SQLite.

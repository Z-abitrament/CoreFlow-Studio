# M8 Verification

## Scope
M8 implements the first usable Qt desktop UI for simulator-backed operation. It does not implement report generation, production calibration formulas, real hardware acceptance, or serial-port hardware access.

## Implemented
- Qt main window with device/channel list.
- Connection setup panel with simulator and Serial Modbus RTU configuration fields.
- Simulator channel creation, connect, disconnect, and live measurement read actions.
- Live numeric readings for mass flow, density, temperature, and volume flow.
- Live mass-flow time-series chart using pyqtgraph.
- Background worker boundary for calibration preview and factory test launch.
- Workflow status log and cancel-request button.
- Run history table backed by SQLite run-session records.
- Result inspection table showing run summary, step statuses, analysis results, metrics, and artifact links.
- `python -m coreflow --ui` launch path with optional `--data-root`.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest tests\test_ui_main_window.py -q
.\.venv\Scripts\python -m pytest -q
```

## Results
- UI smoke tests passed: 2 tests passed.
- Full test suite passed: 55 tests passed.
- M8 covers `TP-UI-001` and `TP-UI-002` for simulator-backed paths.

## Notes
- Serial Modbus RTU UI fields are visible for the connection setup path, but hardware access remains disabled until M11 hardware acceptance preparation.
- The cancel button records a cancel request at the UI boundary. Step-level workflow cancellation remains a future workflow-runner feature.
- UI tests use Qt offscreen mode and deterministic simulator data.

# M8 Verification

## Scope
M8 implements the first usable Qt desktop UI for simulator-backed operation. It does not implement report generation, production calibration formulas, real hardware acceptance, or serial-port hardware access.

## Implemented
- Qt main window with device/channel list.
- Connection setup panel with simulator and Serial Modbus RTU configuration fields.
- Simulator channel creation, connect, disconnect, and live measurement read actions.
- Live numeric readings for mass flow, density, temperature, and volume flow.
- Live mass-flow time-series chart using pyqtgraph.
- Background worker boundary for calibration preview, factory test, and experiment launch.
- Independent Modbus module window entry point from the toolbar and Modules menu.
- Modbus module window with its own connection state, connection dialog, order selector, larger editable and persistable variable map, row-level variable read/write controls, one-second selected-variable polling, Operations menu, TX/RX frame display, zero calibration dialog, Test Records dialog, K factor calibration, and manual mass-total repeatability controls.
- Workflow status log and cancel-request button.
- Run history table backed by SQLite run-session records.
- Result inspection table showing run summary, step statuses, analysis results, metrics, and artifact links.
- `python -m coreflow --ui` launch path with optional `--data-root`.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest tests\test_ui_main_window.py -q
conda run -n coreflow-studio python -m pytest -q
```

## Results
- UI smoke tests cover simulator, replay, standalone Modbus module, export, and ASIO/IIS window paths.
- Full test suite passed in the current verification run.
- M8 covers `TP-UI-001` and `TP-UI-002` for simulator-backed paths and standalone module entry points.

## Notes
- Serial Modbus RTU UI fields are visible for the connection setup path, but hardware access remains disabled until M11 hardware acceptance preparation.
- The Modbus module is independent from simulator/replay channels. It opens a Modbus connection only from its connection dialog when the operator clicks `Connect`; the dialog can be closed manually after a successful connection.
- Variable Map supports custom variables before connection, row-level values after connection, guarded row writes, and selected-variable polling. Poll cycles can merge adjacent addresses in the same Modbus table into one read request.
- Variable Map supports visible scroll bars, column reordering, default `mass_rate` and `temperature` rows, disabled write controls for non-writable rows, and `Save Map` persistence under the user data directory. The standalone Modbus window uses a vertical splitter so the map remains scrollable when the window is compressed.
- The old operation button strip and sampled-variable result table have been removed; operations are exposed through the Modbus module menu, and raw TX/RX data codes are shown in the frame table.
- Zero calibration opens a dedicated dialog. The `Start` action reads before values, writes `zero_calibration_start` through the write guard, waits before checking completion, displays before/after `zero_offset` and `delta_t`, and refreshes the Variable Map values including the final coil state.
- Test Records opens as a separate dialog, supports operation filtering, shows
  timestamped Modbus operation attempts from SQLite, keeps compatibility with
  older run/analysis records, and allows editable operator notes for run-backed
  records.
- Modbus test records now include automatically created device profiles, test
  sessions, operation attempts, accepted repeatability trial records, and raw
  Modbus polling artifact references.
- The cancel button records a cancel request at the UI boundary. Step-level workflow cancellation remains a future workflow-runner feature.
- UI tests use Qt offscreen mode and deterministic simulator data.

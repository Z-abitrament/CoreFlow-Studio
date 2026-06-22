# M8 Verification

## Scope
M8 originally implemented the first usable Qt desktop UI for simulator-backed operation. The current UI shell is module-centered: the main window keeps only the Modules menu and swaps the selected module into the central workspace.

## Implemented
- Qt main window with only the `Modules` menu and a central module workspace.
- Embedded Modbus module shown by default on startup and selectable from `Modules > Modbus Module`.
- Embedded ASIO/IIS module selected from `Modules > ASIO/IIS Module`.
- Module switching refreshes the main workspace instead of opening a new top-level module window.
- Modbus module with its own connection state, connection dialog, order selector, larger editable and persistable variable map, row-level variable read/write controls, one-second selected-variable polling, Operations menu, TX/RX frame display, zero calibration dialog, Test Records dialog, K factor calibration, and manual mass-total repeatability controls.
- ASIO/IIS module with independent connection state, device discovery, probe, normal-use parameters, and test dialog.
- `python -m coreflow --ui` launch path with optional `--data-root`.

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest tests\test_ui_main_window.py -q
conda run -n coreflow-studio python -m pytest -q
```

## Results
- UI smoke tests cover module-menu shell behavior plus embedded Modbus and ASIO/IIS paths.
- Full test suite passed in the current verification run.
- M8 covers module entry behavior for `TP-UI-001` and ASIO/IIS module behavior for `TP-UI-003`.

## Notes
- The old main-window simulator/replay dashboard has been removed from the UI shell. Simulator and replay smoke paths remain available from console diagnostics.
- The Modbus module is independent from simulator/replay channels. It opens a Modbus connection only from its connection dialog when the operator clicks `Connect`; the dialog can be closed manually after a successful connection.
- Variable Map supports custom variables before connection, row-level values after connection, guarded row writes, and selected-variable polling. Poll cycles can merge adjacent addresses in the same Modbus table into one read request.
- Variable Map supports visible scroll bars, column reordering, default `mass_rate` and `temperature` rows, disabled write controls for non-writable rows, and `Save Map` persistence under the user data directory. The Modbus module layout uses a vertical splitter so the map remains scrollable when the workspace is compressed.
- The old operation button strip and sampled-variable result table have been removed; operations are exposed through the Modbus module menu, and raw TX/RX data codes are shown in the frame table.
- Zero calibration opens a dedicated dialog. The `Start` action reads before values, writes `zero_calibration_start` through the write guard, waits before checking completion, displays before/after `zero_offset` and `delta_t`, and refreshes the Variable Map values including the final coil state.
- Test Records opens as a separate dialog, supports operation filtering, shows
  timestamped Modbus operation attempts from SQLite, keeps compatibility with
  older run/analysis records, and allows editable operator notes for run-backed
  records.
- Modbus test records now include automatically created device profiles, test
  sessions, operation attempts, accepted repeatability trial records, and raw
  Modbus polling artifact references.
- UI tests use Qt offscreen mode and deterministic simulator data.

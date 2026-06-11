# CoreFlow Studio User Manual

## Scope
This manual describes the current M12 CoreFlow Studio build. The application is a Windows-first desktop tool for simulator-backed Coriolis flowmeter workflow development and packaging validation.

The current build supports simulated devices, live readings, calibration preview, a standalone Modbus Module window, automated factory test, a basic flexible experiment, run inspection, and report/export generation. The standalone Modbus Module can attempt a configured serial Modbus connection from its own window, but production real-transmitter use still requires a validated register map, confirmed calibration formulas, and hardware acceptance.

## Starting The Application
From the packaged distribution folder, double-click:

```text
CoreFlowStudio.exe
```

The desktop UI opens without a PowerShell or console window.

For command-line diagnostics, open PowerShell in the distribution folder and run:

```powershell
.\CoreFlowStudioConsole.exe --build-info
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
.\CoreFlowStudioConsole.exe --write-replay-template .\replay_template.csv
.\CoreFlowStudioConsole.exe --replay-smoke .\replay_template.csv --data-root .\replay-smoke-data
```

From a source checkout, use:

```powershell
conda run -n coreflow-studio python -m coreflow --ui
```

## Data Storage
CoreFlow Studio stores structured run metadata in SQLite and stores raw captures, reports, CSV exports, and manifests as files.

Default data-root priority:

1. `COREFLOW_DATA_ROOT`, when set.
2. `%LOCALAPPDATA%\CoreFlow Studio`.
3. `%APPDATA%\CoreFlow Studio`.
4. User home fallback: `.coreflow-studio`.
5. Packaged executable folder fallback: `CoreFlowStudioData`.
6. Current working directory fallback: `CoreFlowStudioData`.
7. Temp directory fallback.

The main database is named:

```text
coreflow.sqlite
```

Run artifacts are stored under:

```text
artifacts/runs/<year>/<month>/<run_id>/
```

## Main Window Overview
The UI has three working areas.

- Connection: choose simulator or serial mode, add simulator channels, connect and disconnect devices, and view device state.
- Live Readings: show mass flow, density, temperature, volume flow, and a live mass-flow plot.
- Workflows And Results: run workflows, inspect status events, browse run history, inspect results, and generate exports.

## Simulator Workflow
The current UI workflow is simulator-first.

1. Leave Mode set to `Simulator`.
2. Click `Add Simulator`.
3. Select the new device row, for example `SIM-UI-001`.
4. Click `Connect`.
5. Click `Read Live`.

The live reading fields and chart update from deterministic simulator data.

## Replay CSV Workflow
Replay CSV mode loads recorded or generated samples as a read-only simulated device.

1. Generate or prepare a replay CSV file.
2. Set Mode to `Replay CSV`.
3. Enter the CSV path in the Replay CSV field.
4. Click `Add Replay`.
5. Select the replay channel.
6. Click `Connect`.
7. Click `Read Live` or run supported workflows such as `Run Experiment`.

Replay CSV files require `mass_flow`. Optional columns include `captured_at`, `volume_flow`, `density`, `temperature`, `status_flags`, and `source_channel`.

## Serial Modbus RTU Mode
`Serial Modbus RTU` in the main connection panel is shown as a future hardware path, but main-window serial device creation is disabled in this build.

If you choose serial mode and click `Add Simulator`, the status log reports that serial Modbus setup is configured but disabled until hardware acceptance. This is intentional: real-device register maps, acceptance thresholds, fixture rules, and write policies are still known unknowns.

For direct Modbus master operations, use the standalone Modbus Module from the toolbar or the `Modules` menu. That module is independent from the main simulator/replay device list.

Use the console command below only to write a placeholder register-map template for engineering review:

```powershell
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
```

Do not use the placeholder register map as production transmitter documentation.

## Calibration Preview
Calibration Preview collects simulator samples against a built-in reference point and stores a preview result. It does not write parameters to a device.

1. Add and connect a simulator.
2. Select the connected simulator row.
3. Click `Calibration Preview`.
4. Wait for the workflow to complete.
5. Select the new run in Run History.
6. Review steps, metrics, decisions, and artifacts in Result Details.

The calculation module is a placeholder until production calibration formulas are supplied.

## Standalone Modbus Module
Open the Modbus Module from the main toolbar or `Modules` menu. The module has its own connection state, connection dialog, variable map, Operations menu, communication-frame view, and log. It does not require adding a simulator or replay channel in the main window.

- Click `Connection...` to open the Modbus connection dialog. The port list is discovered automatically from connected serial adapters. Use `Refresh Ports` after plugging in or removing a USB-to-serial adapter. Use `Order` for 32-bit byte/word order such as `ABCD`, `BADC`, `CDAB`, or `DCBA`. Use `Timeout` and `Retries` to tolerate slower or occasionally missed device responses.
- Edit the `Variable Map` table before connecting to set each variable's register kind, address, word count, data type, scale, unit, and writable flag.
- The default map includes `mass_rate`, `mass_acc`, `temperature`, `delta_t`, `zero_offset`, `k_factor`, `low_threshold`, and `zero_calibration_start`.
- The `Variable Map` table keeps scroll bars visible when variables or columns exceed the visible area. Drag column headers to reorder columns.
- Use `Add Variable` before connecting to add a custom variable row and define its name, address, type, and writable setting. Use `Delete Variable` to remove the selected row while disconnected.
- Use `Save Map` before connecting to persist the current variable map under the user data directory. The Modbus Module loads that saved map the next time it opens, so edited addresses, types, scales, units, writable flags, and row order do not need to be re-entered.
- The editable map covers sampled variables plus the zero-calibration start coil. Disconnect before changing the map for a new connection.
- `Connect` opens the selected Modbus RTU port only from the connection dialog. After the connection succeeds, the dialog can be closed manually while the module window remains connected.
- After connecting, use each row's `Read` button to query one variable and refresh the `Value` column. Writable rows can use `Write Value` and `Write`; non-writable rows disable write controls. Writes still go through the write guard and audit log.
- Select row `Poll` checkboxes and click `Start Polling` to poll selected variables once per second. Each polling cycle reads selected variables sequentially, and adjacent variables with the same Modbus table are merged into one read request where possible.
- Use the `Operations` menu for `Sample Variables`, `Zero Cal`, `K Factor`, `Repeatability`, and `Calibration History`. The K Factor input panel is hidden in this build; the existing K Factor operation path is retained for later dialog work.
- The communication-frame table shows live TX/RX Modbus data codes for reads and writes.
- `Sample Variables` reads the configured variables one by one, stores successful values such as accumulated mass, Delta T, zero offset, K factor, and low threshold with timestamps, updates the `Value` column, and logs warnings for variables that do not respond.
- `Zero Cal` opens a dialog with a `Start` button. Starting reads `zero_offset` and `delta_t`, writes `zero_calibration_start` to 1 through the write guard, waits 3 seconds, reads the coil completion state, then displays before/after `zero_offset` and `delta_t` values for operator judgment. The Variable Map `Value` column is refreshed with the after values, including the final `zero_calibration_start` coil state.
- `Calibration History` opens an independent table that can remain open beside calibration dialogs. It can show all calibration operations or one operation type, includes timestamps, and lets the operator edit notes. `K Factor` and `Repeatability` dedicated dialogs are still future UI work.

The current module still uses the placeholder register-map template unless engineering supplies a validated map. Do not use the placeholder map as production transmitter documentation.

## Factory Test
Factory Test runs a fixed simulator-backed outgoing-test path:

- communication and device context through the device interface;
- measurement check against a reference mass flow;
- short stability segment;
- step-level pass/fail results;
- stored raw artifacts and analysis records.

Steps:

1. Add and connect a simulator.
2. Select the connected simulator row.
3. Click `Factory Test`.
4. Select the completed run in Run History.
5. Inspect metrics and artifacts in Result Details.

## Flexible Experiment
Run Experiment executes the current sample R&D workflow:

- capture 6 simulator samples;
- run the `basic_signal_stats` processing module;
- keep fixture control as a no-op placeholder;
- keep ML inference as a placeholder result.

Steps:

1. Add and connect a simulator.
2. Select the connected simulator row.
3. Click `Run Experiment`.
4. Select the completed run in Run History.
5. Inspect processing metrics and generated artifacts.

## Reports And Exports
Generate Export creates report and export artifacts for a selected run.

1. Select a completed run in Run History.
2. Click `Generate Export`.
3. Select the run again if needed.
4. Review generated artifacts in Result Details.

Export package artifacts include:

- `operator_report.txt`
- `metrics.csv`
- `measurements.csv`
- `export_manifest.json`

The artifact paths shown in Result Details are relative to the active data root.

## Status Log And Run History
The status log shows connection actions, live-read messages, workflow start/completion messages, and user-requested cancellation notices.

Run History lists stored runs with:

- run ID;
- workflow name;
- device ID;
- status;
- start time.

Selecting a run populates Result Details with run metadata, workflow steps, analysis results, metrics, and artifacts.

## Cancellation Behavior
The `Cancel` button records that cancellation was requested. Current workflow tasks are short simulator tasks and may complete before cancellation can stop them. If a run completes after cancellation was requested, it remains stored and inspectable.

## Command-Line Diagnostics
Use `CoreFlowStudioConsole.exe` in the packaged folder.

Print build metadata:

```powershell
.\CoreFlowStudioConsole.exe --build-info
```

Run headless simulator verification:

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

Write the placeholder Modbus register-map template:

```powershell
.\CoreFlowStudioConsole.exe --write-register-map-template .\placeholder_modbus.json
```

Write a deterministic replay CSV template:

```powershell
.\CoreFlowStudioConsole.exe --write-replay-template .\replay_template.csv
```

Run a replay-backed simulator smoke check:

```powershell
.\CoreFlowStudioConsole.exe --replay-smoke .\replay_template.csv --data-root .\replay-smoke-data
```

Replay CSV files require a `mass_flow` column. Optional columns are `captured_at`, `volume_flow`, `density`, `temperature`, `status_flags`, and `source_channel`. Replay devices are simulator devices and are read-only.

## Safety Notes
- Simulator workflows are safe and require no hardware.
- Calibration Preview does not write device parameters.
- The standalone Modbus Module can open the selected COM port when the operator clicks `Connect` in its connection dialog.
- Write-capable Modbus operations must go through explicit write-guard and audit behavior.
- Real transmitter register maps, calibration formulas, fixture behavior, and acceptance thresholds must be supplied before hardware use.
- Do not use the placeholder register map for production transmitter writes.

## Troubleshooting
If the UI does not open, run the console smoke command:

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

If the smoke command passes but the UI does not show, check whether another security policy is blocking GUI execution.

If the windowed UI exits before a window appears, packaged startup failures are appended to:

```text
%LOCALAPPDATA%\CoreFlow Studio\logs\startup.log
```

If `COREFLOW_DATA_ROOT` is set, the log is written under:

```text
<COREFLOW_DATA_ROOT>\logs\startup.log
```

Run `.\CoreFlowStudioConsole.exe --ui` from PowerShell when you want the same startup path with visible console diagnostics.

If data cannot be written under `%LOCALAPPDATA%`, CoreFlow Studio falls back through other writable locations. You can force a data directory:

```powershell
$env:COREFLOW_DATA_ROOT = "D:\CoreFlowStudioData"
.\CoreFlowStudio.exe
```

## Current Limits
- No signed installer or MSI.
- No production calibration formulas.
- No production-approved hardware register map.
- No armed production calibration-parameter write workflow.
- No customer-specific report templates.
- No real ML model execution.
- Replay file UI currently accepts a typed CSV path; it does not yet include a file browser.

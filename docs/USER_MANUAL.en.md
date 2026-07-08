# CoreFlow Studio User Manual

## Scope
This manual describes the current M12 CoreFlow Studio build. The application is a Windows-first desktop tool for simulator-backed Coriolis flowmeter workflow development and packaging validation.

The current desktop UI is module-centered. The main window opens directly into the `Modbus Module` workspace by default. Use `Modules` to switch to another module such as `ASIO/IIS Module` or `Pulse Counter Module`, and use `History > Device History` to review records for one Device ID across modules. Headless simulator, replay, and export smoke paths remain available from the console diagnostics executable, but the old simulator dashboard is no longer shown in the main window.

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
The main window keeps the active module workspace in the center. On startup, the active workspace is `Modbus Module`.

- `Modules > Modbus Module` returns to the Modbus master operator interface.
- `Modules > ASIO/IIS Module` shows the ASIO/IIS frame-stream interface in the main window.
- `Modules > Pulse Counter Module` shows the pulse-counting interface in the main window.
- `History > Device History` opens a device-centered record browser that can show all modules, only Modbus records, or only Pulse records for one Device ID.
- Selecting another module replaces the central workspace instead of opening a new top-level module window.
- `Help > Check for Updates...` opens the software update dialog. Paste the
  GitHub Release `latest.json` URL once, click `Save URL`, then use `Check`,
  `Download`, and `Update and Restart`. The target PC operator does not need to
  run PowerShell commands for updates. The downloaded package is verified with
  SHA-256 before the external updater patches or replaces the install folder,
  and user data under `%LOCALAPPDATA%\CoreFlow Studio` is left in place. When a
  matching patch package is available, the app downloads that smaller package;
  otherwise it falls back to the full update package.

## Modbus Module
Open `Modules > Modbus Module`. The module has its own connection state, device profiles, connection dialog, variable map, Operations menu, communication-frame view, and log.

- Create or select a `Device Profile` before connecting. Use `New Profile` to create a new device profile and `Edit Profile` to modify the selected one. The `Device ID` is a stable asset ID for the tested device and is independent from the Modbus RTU unit ID. Do not use simple numeric unit addresses such as `01` as device IDs. When the Modbus Module opens, it automatically selects the most recently used saved profile if it still exists.
- A saved profile stores the device metadata, connection settings, and register map. Selecting a profile loads those fields back into the Modbus window.
- Edit the full register map inside the profile dialog before connecting. It sets each variable's register kind, address, word count, data type, scale, unit, and writable flag. `Delete` removes the selected reusable profile, but existing device records and test records remain stored under that Device ID.
- Click `Connection...` after selecting a profile to open the Modbus connection dialog. The port list is discovered automatically from connected serial adapters. Use `Refresh Ports` after plugging in or removing a USB-to-serial adapter. Use `Order` for 32-bit byte/word order such as `ABCD`, `BADC`, `CDAB`, or `DCBA`. Use `Timeout` and `Retries` to tolerate slower or occasionally missed device responses.
- The default map includes `mass_rate`, `mass_acc`, `temperature`, `delta_t`, `zero_offset`, `k_factor`, `low_threshold`, and `zero_calibration_start`.
- The module shows a compact `Live Variables` table for runtime work. It hides register-map configuration columns and keeps variable name, poll checkbox, value, write value, and row read/write actions visible.
- Use `Add`, `Delete`, and `Reset` in the profile dialog to maintain custom variable rows while disconnected. Saving the profile persists edited addresses, types, scales, units, writable flags, and row order with that device ID.
- The editable profile map covers sampled variables plus the zero-calibration start coil. Disconnect before changing the map for a new connection.
- `Connect` opens the selected Modbus RTU port only from the connection dialog. The selected profile's `Device ID` is used for stored data, while the dialog's Unit ID is stored only as the Modbus protocol address. After the connection succeeds, the dialog can be closed manually while the module window remains connected.
- After connecting, use each row's `Read` button to query one variable and refresh the `Value` column. Writable rows can use `Write Value` and `Write`; non-writable rows disable write controls. Writes still go through the write guard and audit log.
- Select row `Poll` checkboxes and click `Start Polling` to poll selected variables once per second. Each polling cycle reads selected variables sequentially, and adjacent variables with the same Modbus table are merged into one read request where possible.
- Use the `Operations` menu for `Variable Sampling`, `Zero Cal`, `K Factor`,
  `Repeatability`, `Current Device Test Records`, and `All Test Records`. The
  old inline K Factor input panel is hidden; K Factor now opens its own dialog.
- `Variable Sampling` opens a dialog where the operator chooses variables, poll
  interval, plot layout, and notes. `Start` opens a non-modal live plot and polls
  the selected variables until `Stop`; the operation saves a wide CSV artifact,
  records variable units where available, refreshes the latest values in the Live
  Variables table, and adds a test record.
- The communication-frame table shows live TX/RX Modbus data codes for reads and writes.
- `Zero Cal` opens a dialog with selectable pre-calibration snapshot variables and a `Start` button. Use `Save Config` to persist the selected snapshot variables for the current Device ID only. Starting reads the saved or selected snapshot variables plus `zero_offset` and `delta_t`, writes `zero_calibration_start` to 1 through the write guard, waits 3 seconds, reads the coil completion state, then displays before/after `zero_offset` and `delta_t` values for operator judgment. The Live Variables `Value` column is refreshed with the after values, including the final `zero_calibration_start` coil state.
- `K Factor` opens a dialog with Simple mode enabled and Advanced mode reserved. Simple mode captures the same selectable pre-calibration snapshot style as Zero Cal, reads the configured flow accumulator and current K factor, detects one non-zero flow segment from the configured flow-rate variable, waits for the operator's standard-scale mass input, calculates `K1`, and can optionally write `K1` back to the device with readback verification. Use `Save Configuration` to persist the selected variables, polling interval, and snapshot selections for the next K Factor session; the write-to-device choice is not persisted. Test records store the capture, calculation, optional write status, and raw Modbus polling artifact reference.
- `Repeatability` opens a dialog with Three Flow Ranges, Single Flow Range, and reserved Advanced modes. The main operation dialog keeps only the per-trial standard-scale mass input visible; use `Configuration...` before the first trial to set variables, polling interval, instant-flow offset, mode, target-flow ranges, K Factor variable, test-record saving, all-flow-sample plotting, default trial-sample variables, operation notes, and one shared pre/post snapshot selection, then `Save Config` to persist those settings for this device profile only. Different Device IDs do not share repeatability configuration. Saved operation notes are shown on the repeatability operation dialog, and every calculated trial under that operation stores the same notes. Each trial first reads the selected snapshot variables and the configured K Factor variable and reports progress in the operation status without a separate read-complete popup. If all-flow-sample recording is enabled, `Capture Trial` first asks the operator to confirm this trial's sample/plot variables and choose whether selected variables are overlaid in one chart or shown as one chart per variable, then a separate non-modal time-value plot opens and updates during capture without blocking the operation dialog. Click plotted sample points to inspect their trial label, variable, sample index, relative time, capture time, and value. The flow-rate variable is always sampled, and any extra variables confirmed for that trial are sampled in the same cycle and saved in the same wide CSV raw artifact. After flow starts, polling continues through the configured instant-flow offset and `v1` is selected from the captured real-time samples rather than from an extra post-start read. After the non-zero-to-zero flow segment completes, the same snapshot variables are read again as the post-trial snapshot, then enter `Standard Mass` and click `Calculate Trial Error` to save the trial, calculate percent error, and record the automatically read original K, `v1`, `v_mean`, flow start/instant/end timestamps, pre/post snapshots, the raw Modbus polling artifact, and when enabled the trial-sample artifact ID, sample count, and sampled variable names. The test-record timestamp is the `Capture Trial` click time; the later trial error calculation/save time and flow start/instant/end remain available in the record details. There is no `Save Trial` button. Closing before 9 trials keeps already captured trials in test records, and the next open starts a new operation. Use `Calculate Repeatability` to choose one flow point and one consecutive three-trial window; that repeatability record uses the repeatability calculation/save time as its timestamp. After selecting three flow points, use `Calculate Final K` to store the final-K preview from the selected 9 trials and their automatically read original K; repeating it overwrites only the previous final-K preview. `Add Trial` appends more trials after the base set.
- `Current Device Test Records` opens an independent table locked to the selected or connected device profile. `All Test Records` opens the global browser across every device tested with this program from the Operations menu. Both views can show one operation type, include timestamps, summarize key parameters such as K factor write status, variable-sampling sample count, or repeatability summary, and let the operator edit run notes when a run-backed record is selected. For variable-sampling records and repeatability trials with saved samples, use `View Flow Plot` to reopen a saved curve, `View Flow Data` to inspect the saved samples in a table, or `Compare Flow Plots` to choose specific saved sample artifacts and compare them aligned at each first sample or the pre-flow point. The plot window includes a variable table and plot-layout selector so flow and any sampled extra variables can be shown individually, overlaid in one chart, or split into one chart per variable; clicking a plotted point shows its exact sample details. Use `Export...` to choose an operation type and optional started-at time range, then write a portable JSON test-record package for another PC. Use `Import...` to load compatible packages. Duplicate runs are skipped; conflicting imported run IDs are kept under new imported IDs. Excel export is reserved for a later release.

The current module still uses the placeholder register-map template unless engineering supplies a validated map. Do not use the placeholder map as production transmitter documentation.

For implementation-level operation sequences and history fields, see `docs/MODBUS_OPERATIONS.md`.

## Pulse Counter Module
Open `Modules > Pulse Counter Module`. The module is independent from the Modbus connection and does not open or reconfigure Modbus serial ports.

- Click `Configure...` to open the configuration dialog, enter a stable `Device ID`, configure the pulse channel, edge, pulse value, unit, and fixed switch frequency, then click `Apply` or `Save Config`. The saved Pulse configuration belongs to that Device ID only.
- Click `Load Config` in the configuration dialog after entering a Device ID to restore that device's saved Pulse configuration.
- Enter or browse to a DSView/libsigrok4DSL CSV export and click `Analyze CSV`. The first implementation is offline CSV analysis only; it does not open DSLogic hardware or perform live capture.
- The analysis extracts configured pulse edges, converts pulse count to measured quantity, aggregates rate into fixed switch-frequency windows, and plots rate versus time. Pulses close to a switch-window boundary are counted in the summary because adjacent frequency segments may have assignment uncertainty.
- Enter `Standard Mass`, flow point, and trial index, then click `Calculate Trial`. The measured mass comes from pulse data: `pulse_count * pulse_value`. The saved trial error is `(measured_mass - standard_mass) / standard_mass * 100%`. Saved trials appear in `Trial Records`.
- Use `Calculate Repeatability...` to choose one flow point and one consecutive three-trial window from saved Pulse trials. The selected-window result is saved as a `pulse_repeatability` record with mean error and repeatability standard deviation. Repeatability is not calculated automatically just because three trials exist.
- CSV Analysis and Trial Calculation are shown side by side, and the rate plot and Trial Records areas can be resized by dragging their splitters. Pulse records are stored under the same stable Device ID concept as Modbus records, and history is reviewed from `History > Device History`. The module does not write transmitter parameters.

## Device History
Open `History > Device History` when one physical device has records from more than one module.

- Enter the Device ID and click `Refresh`.
- Use the module filter to show `All`, `Modbus`, or `Pulse`.
- The table shows timestamp, module, operation, status, Device ID, and key summary values such as percent error, measured quantity, standard quantity, pulse count, or Modbus repeatability metrics.

## ASIO/IIS Module
Open `Modules > ASIO/IIS Module`. The module keeps its own connection state and does not create or connect transmitter channels.

- Select the backend and device, then review normal-use parameters such as sample rate, bit depth, sample format, input/output channel count, samples per frame, and test amplitude.
- Use `Refresh Devices` to rescan device options.
- Use `Probe` to check the selected device or backend capabilities.
- Use `Connect` and `Disconnect` to change only the ASIO/IIS module state.
- Use `Tests` to open the loopback and non-loopback test dialog. The test dialog can generate sine, square, or white-noise signals and plot input, output, or both together.

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
- The Modbus Module can open the selected COM port when the operator clicks `Connect` in its connection dialog.
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

If the Modbus Module reports `Unable to open Modbus RTU transport`, first confirm that the selected port is the USB-to-serial adapter, not a Bluetooth or virtual COM port. Then check that the adapter driver is installed, the port is not already open in another terminal or serial-monitor program, and the baud rate, parity, stop bits, unit ID, timeout, and byte/word order match the transmitter setup. The connection error includes the selected COM port and serial parameters to make this check easier.

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

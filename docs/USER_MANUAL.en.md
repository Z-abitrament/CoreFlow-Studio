# CoreFlow Studio User Manual

## Scope
This manual describes the current M15 CoreFlow Studio build, version `0.7.0`.
The application is a Windows-first desktop tool; the M12 Windows packaging
foundation remains in place and M15 adds the independent manual Filling Trial
Module.

The current desktop UI is module-centered. The main window keeps only the `Modules` menu and opens directly into the `Modbus Module` workspace by default. Use the menu to switch to `Filling Module` or `ASIO/IIS Module`. Headless simulator, replay, and export smoke paths remain available from the console diagnostics executable, but the old simulator dashboard is no longer shown in the main window.

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
The main window intentionally contains only the `Modules` menu and the active module workspace. On startup, the active workspace is `Modbus Module`.

- `Modules > Modbus Module` returns to the Modbus master operator interface.
- `Modules > Filling Module` shows the manual Filling Trial workbench.
- `Modules > ASIO/IIS Module` shows the ASIO/IIS frame-stream interface in the main window.
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

## Filling Trial Module
Open `Modules > Filling Module`. This module records manual filling trials and
calculates regular error, three-trial repeatability, and valve-closing advance.
It does not require or open a hardware connection.

### Select The Flowmeter
1. On first entry, select a flowmeter from the `Device ID` list and click
   `Select`.
2. If the flowmeter is absent, click `New Device...`, enter its stable Device ID
   and optional model, then click `Create`. Duplicate IDs are rejected.
3. Use `Change Device...` later only after ending the active group.

Device ID identifies the flowmeter only. It is not a Modbus Unit ID, COM port,
controller ID, or valve ID. A device created here is stored neutrally as a
`future_adapter`; that label does not claim that a hardware adapter exists.

### Set The Filling Condition
1. Choose or enter the `Control / valve label`. Use `New Label...` for a new
   controller-and-valve combination. This label is separate from Device ID.
2. If a saved correction is needed, choose an `Advance profile`. One flowmeter
   can retain multiple immutable profiles, including profiles for the same flow
   and specified mass; use the label, operating values, advance, and timestamp
   to distinguish them.
3. Choose `Regular Test` for error/repeatability or `Calculate Advance` to
   derive a closing correction.
4. Enter `Pulse switch point (Hz)`, `Mass per pulse`, `Mass unit`, `Flow point
   (g/s)`, `Specified mass`, and `Target mass`.

`Specified mass` is the desired final mass. `Target mass` is the threshold used
in the external controller. In Calculate Advance mode, target follows specified
mass until an advance is set. `Standard mass` is the final standard-scale
reading entered separately after each physical trial. The selected mass unit
applies to all mass fields; the module does not convert units.

For a selected device, fields restore from its most recent calculated trial.
Unsaved drafts are not restored. `Standard mass` always opens blank. After the
first trial is calculated, mode, label, pulse settings, unit, flow point,
specified mass, and target mass are locked for that group.

### Calculate And Add Trials
1. Run the physical filling cycle outside CoreFlow Studio.
2. Enter that trial's standard-scale result in `Standard mass`.
3. Click `Calculate Current Trial Error`. The trial is saved immediately and
   appears in the table; there is no separate Save action.
4. To continue, click `Add Trial`. The next trial is prepared with a blank
   standard-mass field. Run the next external cycle and repeat the calculation.

Regular error is:

```text
(standard mass - specified mass) / specified mass * 100%
```

Target mass is not the denominator. A positive result is above specified mass,
a negative result is below it, and zero is an exact match. These are calculated
values, not automatic pass/fail decisions.

### Calculate Repeatability
In `Regular Test`, select exactly three calculated table rows with consecutive
Trial numbers, then click `Calculate Repeatability`. The saved result is the
sample standard deviation of their three percent errors. Another count or a gap
in Trial numbers is rejected. The calculation is added to history with all
three source Trial IDs and the full condition snapshot.

### Calculate And Set Advance
1. Choose `Calculate Advance` before calculating the first trial in the group.
2. Calculate at least three trials. Select the rows to use; they do not need to
   be consecutive.
3. Click `Calculate Advance`. The calculation is saved immediately and displays
   source trials, mean standard mass, specified mass, signed advance mass, and
   corrected target mass.
4. Review the result, then click `Set Advance` to keep it as a reusable profile.

The calculation is:

```text
mean standard mass = average(selected standard masses)
advance mass = mean standard mass - specified mass
corrected target mass = specified mass - advance mass
```

Negative advance is valid and increases the corrected target. `Set Advance` is
atomic: it creates a new immutable profile, completes the old advance group,
and starts a new `Regular Test` group at corrected target and blank Trial 1.
The old uncorrected trials are cleared from the current table so they cannot be
mixed with corrected trials. Setting another calculation creates another
profile; earlier profiles are not overwritten.

### History And Group End
Click `History...` to open records locked to the current Device ID. The four
types are `Filling Trial`, `Filling Repeatability`, `Filling Advance
Calculation`, and `Filling Advance Profile Set`. Select a row to inspect source
Trial IDs, input/result values, full snapshots, timestamps, labels, and notes.

Click `End Group` before changing Device ID, changing an active profile, or
starting a different condition. Ending a group with saved trials completes it;
ending an empty group cancels it. Closing the application discards an
uncalculated standard-mass entry, but already calculated trials remain stored.

### Filling Trial Errors And Limits
- Select a Device ID before calculating or opening history.
- Control/valve label and mass unit must be nonempty. Numeric configuration,
  standard mass, and corrected target must be finite and greater than zero.
- If controls are locked, end the current group before changing its condition.
- If a save or Set Advance error is shown, do not assume the result was stored;
  correct the problem and retry. The service keeps Set Advance all-or-nothing.
- The module reads no pulses and has no pulse-total field. It does not control a
  valve, write a controller/transmitter, or send Modbus, serial, or other
  protocol traffic. The operator remains responsible for the external filling
  cycle and standard-scale reading.

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
- Filling Trial v1 is manual-input only: no pulse acquisition or total, valve
  control, controller/transmitter write, or production pass/fail threshold.

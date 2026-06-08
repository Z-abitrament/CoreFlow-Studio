# CoreFlow Studio User Manual

## Scope
This manual describes the current M12 CoreFlow Studio build. The application is a Windows-first desktop tool for simulator-backed Coriolis flowmeter workflow development and packaging validation.

The current build supports simulated devices, live readings, calibration preview, automated factory test, a basic flexible experiment, run inspection, and report/export generation. It does not yet enable real hardware operation from the UI, production calibration formulas, armed parameter writes, signed installers, or customer-specific report templates.

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
```

From a source checkout, use:

```powershell
.\.venv\Scripts\python -m coreflow --ui
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

## Serial Modbus RTU Mode
`Serial Modbus RTU` is shown as a future hardware path, but real hardware UI enablement is disabled in this build.

If you choose serial mode and click `Add Simulator`, the status log reports that serial Modbus setup is configured but disabled until hardware acceptance. This is intentional: real-device register maps, acceptance thresholds, fixture rules, and write policies are still known unknowns.

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

## Safety Notes
- Simulator workflows are safe and require no hardware.
- Calibration Preview does not write device parameters.
- Hardware write workflows are not enabled.
- Real transmitter register maps, calibration formulas, fixture behavior, and acceptance thresholds must be supplied before hardware use.
- Any future hardware write must go through explicit write-guard and audit behavior.

## Troubleshooting
If the UI does not open, run the console smoke command:

```powershell
.\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\smoke-data
```

If the smoke command passes but the UI does not show, check whether another security policy is blocking GUI execution.

If data cannot be written under `%LOCALAPPDATA%`, CoreFlow Studio falls back through other writable locations. You can force a data directory:

```powershell
$env:COREFLOW_DATA_ROOT = "D:\CoreFlowStudioData"
.\CoreFlowStudio.exe
```

## Current Limits
- No signed installer or MSI.
- No production calibration formulas.
- No real hardware UI enablement.
- No armed calibration-parameter write workflow.
- No customer-specific report templates.
- No real ML model execution.
- No replay-file UI.

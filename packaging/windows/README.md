# CoreFlow Studio Windows Package

## Purpose
This folder documents the M12 Windows distributable layout for CoreFlow Studio. The first packaged build is intended for simulator workflows and hardware acceptance preparation, not production calibration writes.

## Build
Run from the repository root in Windows PowerShell:

```powershell
.\packaging\windows\build.ps1
```

Useful options:

```powershell
.\packaging\windows\build.ps1 -BuildChannel lab
.\packaging\windows\build.ps1 -SkipTests
```

The build script creates:

```text
dist/
  CoreFlowStudio/
    CoreFlowStudio.exe
    README.md
    _internal/
```

## Runtime Data
By default, packaged builds store local SQLite data and artifacts under:

```text
%LOCALAPPDATA%\CoreFlow Studio
```

Set `COREFLOW_DATA_ROOT` to override the data directory for lab validation.

## Driver Notes
- Simulator workflows require no external drivers.
- Real Modbus RTU hardware requires a Windows USB-to-serial driver from the adapter vendor.
- Confirm the adapter appears as a COM port before attempting hardware acceptance preparation.
- The placeholder register map must be replaced or edited from transmitter firmware documentation before real device use.

## Smoke Check
After building, launch:

```powershell
.\dist\CoreFlowStudio\CoreFlowStudio.exe
.\dist\CoreFlowStudio\CoreFlowStudio.exe --build-info
.\dist\CoreFlowStudio\CoreFlowStudio.exe --write-register-map-template .\dist\CoreFlowStudio\placeholder_modbus.json
.\dist\CoreFlowStudio\CoreFlowStudio.exe --simulator-smoke --data-root .\dist\CoreFlowStudio\smoke-data
.\dist\CoreFlowStudio\CoreFlowStudio.exe --ui
```

Opening `CoreFlowStudio.exe` with no command-line arguments starts the Qt desktop UI. The other commands are diagnostics for build metadata, protocol templates, and headless simulator verification.

The `--simulator-smoke` command runs the simulator path headlessly: add a device, connect it, read live values, run calibration preview, run factory test, run an experiment, and generate an export package. In the UI, the same simulator workflows should be available from the main window.

## Known Limits
- No installer is produced yet; M12 creates a distributable folder.
- Armed hardware writes remain disabled outside explicit future workflows.
- Customer-specific report templates, signing, and retention policy are future work.

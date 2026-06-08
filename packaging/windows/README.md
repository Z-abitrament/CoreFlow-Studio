# CoreFlow Studio Windows Package

## Purpose
This folder documents the M12 Windows distributable layout for CoreFlow Studio. The first packaged build is intended for simulator workflows and hardware acceptance preparation, not production calibration writes.

## Build
Create or update the conda environment first:

```powershell
conda env create -f environment.yml
conda activate coreflow-studio
```

If the environment already exists:

```powershell
conda env update -f environment.yml --prune
conda activate coreflow-studio
```

Then run from the repository root in Windows PowerShell:

```powershell
.\packaging\windows\build.ps1
```

Useful options:

```powershell
.\packaging\windows\build.ps1 -BuildChannel lab
.\packaging\windows\build.ps1 -CondaEnv coreflow-studio
.\packaging\windows\build.ps1 -SkipTests
```

The build script defaults to the `coreflow-studio` conda environment. If that environment is already active, the script uses the active `python`; otherwise it resolves the environment path with `conda env list --json` and runs that environment's `python.exe` directly.

The build script creates:

```text
dist/
  CoreFlowStudio/
    CoreFlowStudio.exe
    CoreFlowStudioConsole.exe
    README.md
    USER_MANUAL.en.md
    USER_MANUAL.zh-CN.md
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
After building, run the packaged verification script:

```powershell
.\packaging\windows\verify_package.ps1
```

The script checks required files, build metadata, headless simulator smoke, the console UI startup path, and the windowed UI startup path. It fails if either UI process exits during startup or if the console UI path writes startup errors.

Manual smoke commands are:

```powershell
.\dist\CoreFlowStudio\CoreFlowStudio.exe
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --build-info
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --write-register-map-template .\dist\CoreFlowStudio\placeholder_modbus.json
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\dist\CoreFlowStudio\smoke-data
```

Opening `CoreFlowStudio.exe` with no command-line arguments starts the Qt desktop UI without a console window. Use `CoreFlowStudioConsole.exe` for command-line diagnostics, build metadata, protocol templates, and headless simulator verification.

The `--simulator-smoke` command runs the simulator path headlessly: add a device, connect it, read live values, run calibration preview, run factory test, run an experiment, and generate an export package. In the UI, the same simulator workflows should be available from the main window.

## Known Limits
- No installer is produced yet; M12 creates a distributable folder.
- Armed hardware writes remain disabled outside explicit future workflows.
- Customer-specific report templates, signing, and retention policy are future work.

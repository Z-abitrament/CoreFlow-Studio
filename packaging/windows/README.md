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

## GitHub Release Updates
For online updates, publish a GitHub Release asset set that always contains a
full update zip and `latest.json`. Starting with source versions `0.6.1` and
newer, the same command can also create a smaller file-level patch zip from the
previous full update package. Target PCs automatically prefer a matching patch
package and fall back to the full package when no safe patch is available.

After building and verifying the package, generate full assets from the
repository root:

```powershell
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --make-update-package .\dist\CoreFlowStudio --update-output-dir .\dist\updates --update-base-url https://github.com/<owner>/<repo>/releases/download/v0.6.1
```

To generate a patch package as well, provide the previous version and the
previous release's full zip:

```powershell
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --make-update-package .\dist\CoreFlowStudio --update-output-dir .\dist\updates --update-base-url https://github.com/<owner>/<repo>/releases/download/v0.6.2 --previous-update-version 0.6.1 --previous-update-package .\dist\updates\CoreFlowStudio-0.6.1-full.zip
```

Upload all generated files to the GitHub Release:

```text
dist\updates\CoreFlowStudio-0.6.2-full.zip
dist\updates\CoreFlowStudio-0.6.1-to-0.6.2-patch.zip
dist\updates\latest.json
```

On the target PC, operators do not need PowerShell commands. They open
`Help > Check for Updates...`, paste or keep the Release manifest URL, click
`Check`, then `Download`, then `Update and Restart`.

Before applying an update, close every CoreFlow Studio window that uses the
same installation folder. The external updater waits for the executable lock
to clear for up to 90 seconds; if it remains locked, it preserves the current
installation and writes the actionable reason to
`%LOCALAPPDATA%\CoreFlow Studio\updates\update.log`.

Recommended public Release manifest URL:

```text
https://github.com/<owner>/<repo>/releases/latest/download/latest.json
```

The app verifies the downloaded zip with the SHA-256 hash from `latest.json`
before the updater replaces or patches the install folder. User data under
`%LOCALAPPDATA%\CoreFlow Studio` is not part of the package and is not replaced.

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
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --write-replay-template .\dist\CoreFlowStudio\replay_template.csv
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\dist\CoreFlowStudio\smoke-data
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --replay-smoke .\dist\CoreFlowStudio\replay_template.csv --data-root .\dist\CoreFlowStudio\replay-smoke-data
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --modbus-raw "01 03 00 3D 00 02" --modbus-port COM9 --modbus-unit 1 --modbus-auto-crc
```

Opening `CoreFlowStudio.exe` with no command-line arguments starts the Qt desktop UI without a console window. Use `CoreFlowStudioConsole.exe` for command-line diagnostics, build metadata, protocol templates, and headless simulator verification.

The `--simulator-smoke` command runs the simulator path headlessly: add a device, connect it, read live values, run calibration preview, run factory test, run an experiment, and generate an export package. In the UI, the same simulator workflows should be available from the main window.

The `--write-replay-template` command writes a deterministic CSV replay file. The `--replay-smoke` command loads that CSV as a read-only simulated device and runs a replay-backed experiment workflow.

The `--modbus-raw` command sends one local Modbus RTU frame and prints the RX
bytes as uppercase hex. It uses the same standard raw-frame routing as the
Python `coreflow.modbus_api.ModbusRawClient` API. Use `--modbus-auto-crc` when
the input frame omits the CRC bytes.

## Troubleshooting Startup
If `CoreFlowStudio.exe` exits or reports an error before the window appears, run the console UI path from PowerShell:

```powershell
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --ui
```

Packaged UI startup failures are also appended to:

```text
%LOCALAPPDATA%\CoreFlow Studio\logs\startup.log
```

When `COREFLOW_DATA_ROOT` is set, the log is written under `<COREFLOW_DATA_ROOT>\logs\startup.log`.

## Known Limits
- No installer is produced yet; M12 creates a distributable folder.
- Armed hardware writes remain disabled outside explicit future workflows.
- Customer-specific report templates, signing, and retention policy are future work.

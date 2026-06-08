# Developer Setup

## Summary
CoreFlow Studio v1 is a Windows-first Python project. Development uses a conda environment so scientific, Qt, serial, test, and packaging dependencies can be reproduced consistently on lab PCs.

## Prerequisites
- Windows PowerShell.
- Anaconda or Miniconda available as `conda`.
- Git.

## Setup
Run from the repository root:

```powershell
conda env create -f environment.yml
conda activate coreflow-studio
```

If the environment already exists, update it instead:

```powershell
conda env update -f environment.yml --prune
conda activate coreflow-studio
```

## Verification
Run:

```powershell
python -m pytest
python -m coreflow
python -m coreflow --version
python -m coreflow --write-register-map-template .\config\register_maps\placeholder_modbus.json
python -m coreflow --simulator-smoke --data-root .\CoreFlowStudioData\smoke
python -m coreflow --ui
.\packaging\windows\build.ps1
```

Expected behavior:

- The test suite passes.
- The default entry point prints that the M0 bootstrap is ready.
- The version command prints the package version.
- The register-map command writes a placeholder Modbus template for hardware acceptance preparation.
- The simulator smoke command runs headless simulator-backed calibration preview, factory test, experiment, and export generation.
- The UI command launches the Qt desktop application and stores local runtime data under the configured user data directory by default.
- The packaging script creates a windowed UI executable at `dist\CoreFlowStudio\CoreFlowStudio.exe`.
- The packaging script creates a console diagnostics executable at `dist\CoreFlowStudio\CoreFlowStudioConsole.exe`.
- The packaging script copies `USER_MANUAL.en.md` and `USER_MANUAL.zh-CN.md` into the distribution folder.
- In the UI, simulator-backed completed runs can generate report and CSV export artifacts from the run history panel.
- The UI can run a small simulator-backed experiment from the workflow panel and inspect stored processing results.

## Notes
- Source runs can be launched with `--data-root <path>` to choose where SQLite data and artifacts are stored.
- Packaged runs use `%LOCALAPPDATA%\CoreFlow Studio` by default, or `COREFLOW_DATA_ROOT` when that environment variable is set.
- The packaging script defaults to the `coreflow-studio` conda environment. Use `.\packaging\windows\build.ps1 -CondaEnv <name>` to build with another conda environment.
- If PowerShell blocks local scripts, run the packaging script with process-level bypass: `powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1`.
- See `docs/DEVELOPMENT_WORKFLOW.md` for local git and overnight autonomous-run guidance.

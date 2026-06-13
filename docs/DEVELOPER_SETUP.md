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
git config core.hooksPath .githooks
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
python scripts/check_version_update.py
python -m coreflow --write-register-map-template .\config\register_maps\placeholder_modbus.json
python -m coreflow --simulator-smoke --data-root .\CoreFlowStudioData\smoke
python -m coreflow --asio-list-devices
python -m coreflow --asio-probe-native --asio-device BRAVO-HD
python -m coreflow --asio-loopback-smoke --asio-backend native --asio-device BRAVO-HD --asio-sample-rate 44100 --asio-bit-depth 24 --asio-sample-format int24 --asio-frame-samples 4410 --asio-frame-count 8 --asio-amplitude 0.1 --asio-max-latency-samples 12000
python -m coreflow --ui
.\packaging\windows\build.ps1
```

Expected behavior:

- The test suite passes.
- The default entry point prints that the M0 bootstrap is ready.
- The version command prints the package version.
- The version check confirms `pyproject.toml` and `src\coreflow\__init__.py` agree and the staged commit satisfies the project version policy.
- Pytest temporary and cache files stay under `.tmp\` instead of the repository root.
- The register-map command writes a placeholder Modbus template for hardware acceptance preparation.
- The simulator smoke command runs headless simulator-backed calibration preview, factory test, experiment, and export generation.
- The ASIO device-list command prints available audio host APIs/devices or clearly reports that the optional ASIO backend is unavailable.
- The native ASIO probe reports the BRAVO-HD driver capabilities without streaming.
- The ASIO loopback smoke command passes only when the BRAVO-HD driver, ASIO backend, channel configuration, and IIS loopback wiring are available. The listed BRAVO-HD command uses the hardware settings verified on this lab PC: 44100 Hz, 24-bit LSB samples, 2 input channels, 2 output channels, and a 4410-sample ASIO buffer.
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
- ASIO/IIS hardware testing requires the optional Python audio backend and permission to open the Windows audio device. The BRAVO-HD module must be installed and the IIS master output must be wired to the IIS slave input for loopback verification.
- See `docs/DEVELOPMENT_WORKFLOW.md` for local git and overnight autonomous-run guidance.

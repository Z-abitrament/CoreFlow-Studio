# Developer Setup

## Summary
CoreFlow Studio v1 is a Windows-first Python project. M0 uses a standard virtual environment and pip so a clean workstation can run tests and the minimal entry point before application features are added.

## Prerequisites
- Windows PowerShell.
- Python 3.11 or newer available as `python`.
- Git.

## Setup
Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

## Verification
Run:

```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m coreflow
.\.venv\Scripts\python -m coreflow --version
.\.venv\Scripts\python -m coreflow --write-register-map-template .\config\register_maps\placeholder_modbus.json
.\.venv\Scripts\python -m coreflow --ui
```

Expected behavior:

- The test suite passes.
- The default entry point prints that the M0 bootstrap is ready.
- The version command prints the package version.
- The register-map command writes a placeholder Modbus template for hardware acceptance preparation.
- The UI command launches the Qt desktop application and stores local runtime data under `CoreFlowStudioData` by default.
- In the UI, simulator-backed completed runs can generate report and CSV export artifacts from the run history panel.
- The UI can run a small simulator-backed experiment from the workflow panel and inspect stored processing results.

## Notes
- The UI can be launched with `--data-root <path>` to keep SQLite data and artifacts outside the repository root during manual checks.
- See `docs/DEVELOPMENT_WORKFLOW.md` for local git and overnight autonomous-run guidance.

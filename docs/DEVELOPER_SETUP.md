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
```

Expected behavior:

- The test suite passes.
- The default entry point prints that the M0 bootstrap is ready.
- The version command prints the package version.

## Notes
- M0 does not include simulator, workflow, storage, protocol, or UI implementation.
- See `docs/DEVELOPMENT_WORKFLOW.md` for local git and overnight autonomous-run guidance.

# M0 Verification

## Environment
- Date: 2026-06-05.
- Platform: Windows.
- Python: 3.13.2.
- Git: 2.47.0.windows.1.
- Virtual environment: `.venv`.

## Commands Run
```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m coreflow
.\.venv\Scripts\python -m coreflow --version
.\.venv\Scripts\coreflow.exe --version
git status --short
```

## Results
- Dependency installation completed successfully after network access was approved.
- Pytest passed: 3 tests passed.
- `python -m coreflow` exited cleanly and printed the M0 bootstrap message.
- `python -m coreflow --version` and the `coreflow` console script printed version `0.1.0`.
- Final tracked working tree status was clean.

## Notes
- `py --version` was not available on this machine; `python` was used instead.
- `.venv`, caches, and editable-install metadata are intentionally ignored by git.

# M0 Verification

## Environment
- Date: 2026-06-05.
- Platform: Windows.
- Python: 3.13.2.
- Git: 2.47.0.windows.1.
- Conda environment: `coreflow-studio`.

## Commands Run
```powershell
python --version
conda env create -f environment.yml
conda env update -f environment.yml --prune
conda run -n coreflow-studio python -m pytest
conda run -n coreflow-studio python -m coreflow
conda run -n coreflow-studio python -m coreflow --version
conda run -n coreflow-studio coreflow --version
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
- Local environment artifacts, caches, and editable-install metadata are intentionally ignored by git.

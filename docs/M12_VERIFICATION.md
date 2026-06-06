# M12 Verification

## Scope
M12 implements the first Windows distributable-folder packaging path. It does not produce a signed installer, MSI, auto-updater, code signing certificate flow, or production deployment policy.

## Implemented
- PyInstaller packaging configuration at `packaging/windows/coreflow_studio.spec`.
- Windows PowerShell build script at `packaging/windows/build.ps1`.
- Packaging README with driver notes, runtime data location, smoke checks, and known limits.
- User data directory convention:
  - `COREFLOW_DATA_ROOT` override when set.
  - `%LOCALAPPDATA%\CoreFlow Studio` on Windows.
  - home-directory, packaged-folder, current-working-directory, and temp-directory fallbacks for restricted Windows environments.
- Build metadata via `coreflow.build_info` and `python -m coreflow --build-info`.
- Headless packaged-app simulator smoke command via `python -m coreflow --simulator-smoke`.
- Packaged executable opens the Qt desktop UI by default when started with no command-line action.
- Packaged build filters external Anaconda ICU DLLs that can break PySide6 QtWidgets loading on Windows.
- PyInstaller runtime hook generation for packaged build commit/channel stamping.
- `pyinstaller` added to dev dependencies.

## Commands Run
```powershell
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest tests\test_packaging.py -q
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
.\dist\CoreFlowStudio\CoreFlowStudio.exe --build-info
.\dist\CoreFlowStudio\CoreFlowStudio.exe --write-register-map-template .\dist\CoreFlowStudio\placeholder_modbus.json
.\dist\CoreFlowStudio\CoreFlowStudio.exe --simulator-smoke --data-root .\dist\CoreFlowStudio\smoke-data
.\dist\CoreFlowStudio\CoreFlowStudio.exe
```

## Results
- Packaging tests passed: 8 tests passed.
- Build script ran the full test suite: 74 tests passed.
- PyInstaller produced `dist\CoreFlowStudio\CoreFlowStudio.exe`.
- Packaged executable started and printed build info.
- Packaged executable wrote a placeholder register-map JSON file.
- Packaged executable ran simulator-backed calibration preview, factory test, experiment, and export generation through the headless smoke command.
- Packaged executable no longer exits immediately when opened without arguments; the UI process stayed running for the no-argument startup check.

## Notes
- The initial build uses a distributable folder, not an installer.
- The package currently uses console mode so `--build-info` and other command-line diagnostics are visible during lab validation.
- Build metadata appends a `-dirty` suffix when tracked files have uncommitted changes; final handoff builds should be created from a clean working tree.
- PowerShell script execution may require process-level execution-policy bypass on locked-down lab PCs.

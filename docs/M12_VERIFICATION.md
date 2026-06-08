# M12 Verification

## Scope
M12 implements the first Windows distributable-folder packaging path. It does not produce a signed installer, MSI, auto-updater, code signing certificate flow, or production deployment policy.

## Implemented
- PyInstaller packaging configuration at `packaging/windows/coreflow_studio.spec`.
- Windows PowerShell build script at `packaging/windows/build.ps1`.
- Windows PowerShell package verification script at `packaging/windows/verify_package.ps1`.
- Packaging README with driver notes, runtime data location, smoke checks, and known limits.
- User data directory convention:
  - `COREFLOW_DATA_ROOT` override when set.
  - `%LOCALAPPDATA%\CoreFlow Studio` on Windows.
  - home-directory, packaged-folder, current-working-directory, and temp-directory fallbacks for restricted Windows environments.
- Build metadata via `coreflow.build_info` and `python -m coreflow --build-info`.
- Headless packaged-app simulator smoke command via `python -m coreflow --simulator-smoke`.
- Packaged `CoreFlowStudio.exe` opens the Qt desktop UI without a console window.
- Packaged `CoreFlowStudioConsole.exe` keeps console diagnostics available for build info, simulator smoke, and register-map template generation.
- English and Chinese user manuals are included in the distribution folder.
- Packaged build filters external Anaconda ICU DLLs that can break PySide6 QtWidgets loading on Windows.
- PyInstaller runtime hook generation for packaged build commit/channel stamping.
- `pyinstaller` added to dev dependencies.

## Commands Run
```powershell
conda env update -f environment.yml --prune
conda run -n coreflow-studio python -m pytest tests\test_packaging.py -q
powershell -ExecutionPolicy Bypass -File .\packaging\windows\build.ps1
powershell -ExecutionPolicy Bypass -File .\packaging\windows\verify_package.ps1
.\dist\CoreFlowStudio\CoreFlowStudio.exe
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --build-info
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --write-register-map-template .\dist\CoreFlowStudio\placeholder_modbus.json
.\dist\CoreFlowStudio\CoreFlowStudioConsole.exe --simulator-smoke --data-root .\dist\CoreFlowStudio\smoke-data
```

## Results
- Packaging tests passed.
- Build script ran the full test suite.
- PyInstaller produced `dist\CoreFlowStudio\CoreFlowStudio.exe` and `dist\CoreFlowStudio\CoreFlowStudioConsole.exe`.
- Windowed packaged executable started without a console window.
- Console packaged executable printed build info.
- Console packaged executable wrote a placeholder register-map JSON file.
- Console packaged executable ran simulator-backed calibration preview, factory test, experiment, and export generation through the headless smoke command.
- Distribution includes `USER_MANUAL.en.md` and `USER_MANUAL.zh-CN.md`.

## Notes
- The initial build uses a distributable folder, not an installer.
- The main executable is windowed; use `CoreFlowStudioConsole.exe` for command-line diagnostics.
- Build metadata appends a `-dirty` suffix when tracked files have uncommitted changes; final handoff builds should be created from a clean working tree.
- PowerShell script execution may require process-level execution-policy bypass on locked-down lab PCs.

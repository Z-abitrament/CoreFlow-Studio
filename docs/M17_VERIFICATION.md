# M17 Register-Map Library Verification

## Delivered Baseline

M17 is implemented at software version `0.9.0` and SQLite schema v6. The Modbus
register-map catalog is independent from device, transmitter, and tube model
metadata. A Device ID binds to one explicit register-list ID and version while
retaining an inline snapshot for compatibility and traceability.

The official `krohne-prj-main` list is extracted from the full active Modbus
mapping at Krohne DSP commit `f0a1b39`. It includes the existing measurements,
configuration, communication, coils, zero-monitor block, and PI drive-gain
register. No separate zero-monitor extension-package or specialized list is
used.

## Verified Behavior

- Schema v6 migrates legacy inline maps into a deduplicated catalog without
  altering the profile snapshots.
- Multiple Device IDs can share the same list version.
- Editing one custom or legacy binding creates or reuses another version and
  changes only that Device ID.
- Official list versions are immutable; editing requires `New List`.
- Installing bundled lists during a client update does not rebind existing
  Device IDs.
- The Device Profile UI can select an existing list, clone a new list, edit the
  full register table, and preview added, removed, and modified variables.
- Runtime snapshots include the catalog ID and version in addition to the full
  effective register map.
- Windows packaging includes the generated `krohne_prj_main.json` under
  `config/register_maps`.
- The generated main list contains 59 logical entries: 32 holding registers,
  24 input registers, and 3 coils. It covers every active DSP mapped-register
  symbol plus the reviewed `mass_rate` client alias.

## Verification Evidence

Run from the repository root in the `coreflow-studio` Conda environment on
2026-07-22:

```powershell
conda run -n coreflow-studio python -m py_compile scripts\extract_krohne_register_map.py src\coreflow\app\modbus_register_maps.py src\coreflow\app\modbus_runtime.py src\coreflow\ui\modbus_window.py src\coreflow\storage\database.py src\coreflow\storage\models.py src\coreflow\storage\repositories.py
```

Result: passed.

```powershell
conda run -n coreflow-studio python scripts\extract_krohne_register_map.py --dsp-root "E:\CFM DSP - Digital Driving\visualdsppp_\Krohne_prj" --output config\register_maps\krohne_prj_main.json --check
```

Result: the checked-in JSON exactly matches the active map at DSP commit
`f0a1b39`.

```powershell
conda run -n coreflow-studio python -m pytest tests\test_krohne_register_map_extractor.py tests\test_modbus_register_map_library.py tests\test_zero_monitor_protocol.py tests\test_modbus_device.py tests\test_packaging.py -q
```

Result: `52 passed in 11.94s`.

```powershell
conda run -n coreflow-studio python -m pytest -q
```

Result: `442 passed in 379.57s`.

```powershell
conda run -n coreflow-studio python scripts\check_version_update.py
git diff --check
```

Result: both passed. Git only reported the repository's existing Windows line
ending notices.

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -SkipTests
powershell -ExecutionPolicy Bypass -File packaging\windows\verify_package.ps1
```

Result: both executables built successfully; packaged version/build metadata,
simulator smoke, replay smoke, console UI startup, and windowed UI startup all
passed. The bundled file was verified at
`dist\CoreFlowStudio\_internal\config\register_maps\krohne_prj_main.json`.

## Hardware Boundary

No serial port was opened and no Modbus read or write was sent during this
verification. Real-device discovery of register-list ID/version remains pending
because the DSP firmware does not yet expose an agreed fixed discovery block.
When that contract is added, it requires separate read-only hardware validation.

# M15 Verification

## Scope
M15 implements the independent Filling Trial Module for manual operator-run
filling trials. The delivered baseline is software version `0.7.0` with SQLite
schema v4.

This milestone is calculation and record keeping only. No test in the M15
implementation evidence opened pulse, controller, valve, serial, Modbus, or
ASIO/IIS hardware, and the Filling Trial runtime creates no protocol traffic.

## Implemented Files
Analysis and workflow models:

- `src/coreflow/analysis/filling.py`
- `src/coreflow/analysis/__init__.py`
- `src/coreflow/workflows/models.py`

Storage and schema v4:

- `src/coreflow/storage/database.py`
- `src/coreflow/storage/models.py`
- `src/coreflow/storage/repositories.py`
- `src/coreflow/storage/__init__.py`

Headless application service:

- `src/coreflow/app/filling.py`
- `src/coreflow/app/__init__.py`

Qt workbench, history, and module registration:

- `src/coreflow/ui/filling_dialogs.py`
- `src/coreflow/ui/filling_history.py`
- `src/coreflow/ui/filling_window.py`
- `src/coreflow/ui/main_window.py`
- `src/coreflow/ui/__init__.py`

Packaging surface and synchronized version sources:

- `packaging/windows/coreflow_studio.spec`
- `pyproject.toml`
- `src/coreflow/__init__.py`

Automated coverage:

- `tests/test_analysis_filling.py`
- `tests/test_storage_filling.py`
- `tests/test_filling_service.py`
- `tests/test_ui_filling.py`
- `tests/test_workflows_storage_models.py`
- `tests/test_storage_foundation.py`
- `tests/test_ui_main_window.py`
- `tests/test_packaging.py`
- `tests/test_bootstrap.py`
- `tests/test_version_policy.py`

Canonical and operator documentation:

- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/IMPLEMENTATION_PLAN.md`
- `docs/TEST_PLAN.md`
- `docs/DATA_MODEL.md`
- `docs/SIMULATION.md`
- `docs/PROTOCOLS.md`
- `docs/USER_MANUAL.en.md`
- `docs/USER_MANUAL.zh-CN.md`
- `docs/M15_VERIFICATION.md`

## Implemented Behavior
- Device ID is selected from shared flowmeter devices. Explicit creation writes
  a neutral `future_adapter` record; Device ID is not a controller/valve ID.
- Each Device ID can retain multiple control/valve labels and multiple immutable
  advance profiles.
- Every new or reopened standard-mass input is blank. Other configuration fields
  restore from that Device ID's latest calculated trial.
- Regular error, exactly-three-consecutive sample standard deviation, signed
  advance, and corrected target formulas are implemented without pulse totals.
- Trials, repeatability, advance calculations, and profile-set events retain
  source IDs, full snapshots, notes, and UTC timestamps without pass/fail
  thresholds.
- Set Advance atomically creates the profile, completes the old group, and
  creates corrected regular Trial 1 so old and corrected trials cannot mix.
- Schema v4 adds filling tables, indexes, foreign keys, atomic repository
  transitions, future-version rejection, and v3 migration with orphan Modbus
  Device ID backfill.

## Commands And Evidence
The following focused commands were run during the implementation slices. The
counts below are the final passing results reported by those slices.

| Scope | Command | Evidence |
| --- | --- | --- |
| Analysis and shared workflow models | `conda run -n coreflow-studio python -m pytest tests/test_analysis_filling.py tests/test_workflows_storage_models.py -q` | 44 passed |
| Schema v4 and storage | `conda run -n coreflow-studio python -m pytest tests/test_storage_filling.py tests/test_storage_foundation.py -q` | 30 passed |
| Service, storage, and analysis | `conda run -n coreflow-studio python -m pytest tests/test_filling_service.py tests/test_storage_filling.py tests/test_analysis_filling.py -q` | 125 passed |
| Qt workbench and headless service | `conda run -n coreflow-studio python -m pytest tests/test_ui_filling.py tests/test_filling_service.py -q` | 80 passed |
| Main shell, packaging import surface, bootstrap, and version policy | `conda run -n coreflow-studio python -m pytest tests/test_ui_main_window.py tests/test_packaging.py tests/test_bootstrap.py tests/test_version_policy.py -q` | 34 passed |

Version evidence:

- `pyproject.toml` contains version `0.7.0`.
- `src/coreflow/__init__.py` contains `__version__ = "0.7.0"`.
- The 34-test Task 5 result includes the version-policy coverage.
- `conda run -n coreflow-studio python scripts/check_version_update.py` exited
  successfully during the final integration pass.

Documentation consistency commands:

```powershell
rg -n "PRD-FR-015|TP-FILL-|M15|filling_trial|灌装" docs
git diff --check
```

## Final Integration Evidence
The focused evidence above is complete. Final integration results are recorded
separately rather than inferred from focused suite counts:

- Full `conda run --no-capture-output -n coreflow-studio python -m pytest -q
  --basetemp=.tmp/pytest-full-m15-final`: **369 passed in 166.08 seconds**.
- Final filling calculation/service/UI regression:
  `tests/test_analysis_filling.py tests/test_filling_service.py
  tests/test_ui_filling.py`: **123 passed in 53.23 seconds**.
- The final package was rebuilt with
  `packaging/windows/build.ps1 -SkipTests`; `verify_package.ps1` passed its
  simulator smoke, replay smoke, console UI startup check, and windowed UI
  startup check.
- Source UI smoke selected `SIM-UI-001`, recorded manual standard-mass trials,
  calculated regular error and advance, set an advance profile, and displayed
  all filling history record types. The final packaged UI smoke again selected
  `SIM-UI-001` and recorded a `1005 g` standard mass as `+0.500000%` error.
- `test_advance_preserves_exact_ordinary_decimal_results` requires an advance
  result of exactly `5.0 g` and a corrected target of exactly `995.0 g` for
  standard masses `1005`, `1006`, and `1004`; this guards the persisted result
  against binary-float display noise.

## Known Limits
- M15 uses manual inputs only. It does not read pulse output, calculate a pulse
  total, control a valve, write a controller/transmitter, or open a protocol
  connection.
- `future_adapter` is a neutral shared-device classification, not a production
  hardware adapter or inferred controller contract.
- No production pulse electrical specification, controller/valve protocol,
  fixture behavior, or acceptance threshold is defined.
- Filling Trial results are neutral calculations; no hidden pass/fail decision
  is stored.
- Physical filling behavior and actual standard-scale accuracy remain operator
  and lab responsibilities outside this hardware-free verification.

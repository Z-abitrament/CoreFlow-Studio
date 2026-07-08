# Pulse Counter Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first usable Pulse Counter UI module with per-device configuration, pulse calibration history, and cross-module device history.

**Architecture:** Keep Pulse Counter independent from Modbus protocol state. Store Pulse profiles and operation records in SQLite, expose a small application runtime service, and add Qt views that reuse the existing module-stack pattern.

**Tech Stack:** Python, PySide6, pyqtgraph, sqlite3, pytest, pytest-qt.

---

### Task 1: Pulse Storage

**Files:**
- Modify: `src/coreflow/storage/database.py`
- Modify: `src/coreflow/storage/models.py`
- Modify: `src/coreflow/storage/repositories.py`
- Test: `tests/test_pulse_module.py`

- [ ] Write failing tests for saving/loading Pulse profiles, Pulse operation records, Pulse trial records, and cross-module device history rows.
- [ ] Add SQLite tables for `pulse_device_profiles`, `pulse_operation_attempts`, and `pulse_trial_records`.
- [ ] Add storage dataclasses and repository methods.
- [ ] Add a repository method that returns unified device-history rows from Modbus attempts and Pulse attempts.
- [ ] Run `conda run -n coreflow-studio pytest tests\test_pulse_module.py`.

### Task 2: Pulse Runtime

**Files:**
- Create: `src/coreflow/app/pulse_runtime.py`
- Modify: `src/coreflow/app/__init__.py`
- Test: `tests/test_pulse_module.py`

- [ ] Write failing tests for per-device config persistence, CSV analysis, trial calculation, repeatability summary, and history listing.
- [ ] Implement a `PulseCounterRuntime` that maps UI inputs to `PulseAnalysisConfig`.
- [ ] Save CSV analysis/trial results as Pulse operation attempts and trial records.
- [ ] Calculate repeatability from selected Pulse trial errors.
- [ ] Run `conda run -n coreflow-studio pytest tests\test_pulse_module.py tests\test_pulse_counter.py`.

### Task 3: Pulse UI

**Files:**
- Create: `src/coreflow/ui/pulse_counter_window.py`
- Modify: `src/coreflow/ui/main_window.py`
- Test: `tests/test_ui_main_window.py`
- Test: `tests/test_pulse_ui.py`

- [ ] Write failing Qt tests for opening the Pulse module, saving a Device ID scoped profile, importing a CSV, calculating a trial, and showing local Pulse history.
- [ ] Add a Pulse module menu action and embedded module window.
- [ ] Build a compact Pulse UI with profile, configuration, CSV import, rate plot, trial calculation, repeatability, and history controls.
- [ ] Ensure the Pulse UI uses no Modbus connection state and does not modify runtime device channels.
- [ ] Run `conda run -n coreflow-studio pytest tests\test_pulse_ui.py tests\test_ui_main_window.py`.

### Task 4: Device History UI

**Files:**
- Create: `src/coreflow/ui/device_history.py`
- Modify: `src/coreflow/ui/main_window.py`
- Test: `tests/test_device_history.py`

- [ ] Write failing Qt tests for a device-history dialog that shows both Modbus and Pulse rows for one Device ID and supports module filtering.
- [ ] Add a menu action or module button for Device History.
- [ ] Implement the history table with columns for time, module, operation, status, Device ID, and summary.
- [ ] Run `conda run -n coreflow-studio pytest tests\test_device_history.py`.

### Task 5: Docs, Full Verification, Build

**Files:**
- Modify: `docs/PULSE_COUNTER.md`
- Modify: `docs/MODBUS_OPERATIONS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/USER_MANUAL.en.md`
- Modify: `docs/USER_MANUAL.zh-CN.md`

- [ ] Document Pulse module operation and cross-module device history.
- [ ] Run `conda run -n coreflow-studio pytest`.
- [ ] Run `powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1`.
- [ ] Run `powershell -ExecutionPolicy Bypass -File packaging\windows\verify_package.ps1`.

# Implementation Plan

## Summary
This plan describes the order for implementing CoreFlow Studio after the documentation harness exists. The sequence is simulation-first and keeps real hardware integration behind interfaces until the simulator, workflows, storage, and UI foundation are stable.

## Milestones

### M0: Repository Bootstrap
Create the Python project skeleton.

Deliverables:

- Python package structure matching `docs/ARCHITECTURE.md`.
- Dependency management file.
- Test runner configuration.
- Basic logging configuration.
- Minimal CLI or app entry point.
- Developer setup instructions.
- Documentation harness verification checklist linked from the developer setup notes.
- Local git initialization when `.git` is absent.
- `.gitignore` for Python, Qt, virtual environments, caches, build outputs, logs, SQLite runtime files, and generated artifacts.
- First baseline checkpoint commit for documentation and workflow setup.
- Developer setup instructions linked from `docs/DEVELOPMENT_WORKFLOW.md`.
- Windows setup and verification commands in `docs/DEVELOPER_SETUP.md`.

Done when:

- Tests can be run from a clean checkout.
- A minimal app entry point starts and exits cleanly.
- The canonical documentation set has been reviewed for consistent v1 scope and source-of-truth assumptions.
- `git status --short` is clean after the final M0 checkpoint commit.

### M1: Core Domain Interfaces
Define the first stable interfaces and data objects.

Deliverables:

- `FlowmeterDevice` interface.
- Measurement, device identity, health, configuration, and communication diagnostic models.
- Workflow step and run status models.
- Storage artifact model.

Done when:

- Unit tests can instantiate domain objects.
- Workflows can depend on interfaces without importing simulator or Modbus code.

### M2: Simulator Foundation
Implement simulated transmitters and simulator scenarios.

Deliverables:

- Deterministic `SimulatedFlowmeterDevice`.
- CSV replay-backed simulator device for deterministic re-analysis.
- Configurable measurement behavior for flow, density, temperature, noise, drift, and zero offset.
- Fault injection for timeout, disconnection, invalid values, and write failure.
- Multi-device simulator manager for 4-8 virtual devices.

Done when:

- A test can run 8 virtual devices concurrently.
- Simulator readings are deterministic with a fixed seed.
- Replay CSV files can drive the same device interface used by workflows.

### M3: Modbus RTU Protocol Adapter
Implement the first real-device communication path behind the device interface.

Deliverables:

- Serial configuration model for port, baud rate, parity, stop bits, timeout, and unit ID.
- Modbus RTU client wrapper using pyserial and pymodbus or equivalent.
- Register-map abstraction with configurable addresses, data types, scaling, and writable flags.
- Coil and discrete-input support for calibration start/status signals where the register map configures them.
- Headless variable sampling service that stores timestamped configured variables in SQLite.
- Timeout, retry, and protocol error reporting.

Done when:

- Protocol tests pass against a fake or loopback Modbus target.
- Configured Modbus variables can be sampled and stored without opening real hardware.
- No workflow imports serial or Modbus implementation details.

### M4: Storage Foundation
Implement local data persistence.

Deliverables:

- SQLite database initialization and schema migration baseline.
- Repositories for devices, runs, steps, results, metrics, artifacts, and audit logs.
- Artifact file store for raw captures, processed outputs, exports, and reports.
- Run directory naming and retention conventions.

Done when:

- A simulated run can create a database record and linked artifact files.
- Data integrity tests verify that artifacts referenced in SQLite exist.

### M5: Calibration Workflow Foundation
Implement simulator-backed calibration preview.

Deliverables:

- Workflow definition for collecting reference points.
- Calculation interface for calibration coefficients or placeholders.
- Result preview without writing device parameters.
- Audit-ready representation of proposed parameter writes.
- Dry-run execution path for write-capable calibration steps.
- Write-guard service that validates workflow state, writable permission, value ranges, and actor/source before any parameter write is allowed.
- Headless zero calibration workflow using a configured start coil/parameter and before/after `zero_offset` and `delta_t` records.
- Headless K factor calibration workflow using a reusable flow-segment capture, operator standard-mass input, guarded K factor write, and audit record.

Done when:

- Calibration preview runs end-to-end against simulator data.
- Unknown production formulas are isolated behind configurable or replaceable calculation modules.
- Write attempts can be previewed, rejected, dry-run audited, or applied to simulator state through one guarded application-level path.
- Zero and K factor calibration workflows can run against fake or simulator devices without physical hardware.

### M6: Automated Factory Test Workflow
Implement a fixed test sequence.

Deliverables:

- Communication health check.
- Device identity and configuration capture.
- Measurement check against configured reference points.
- Stability segment.
- Step-level pass/fail results.

Done when:

- A complete simulated outgoing test creates stored results and a report-ready run record.

### M7: Error And Stability Analysis
Implement initial analysis modules.

Deliverables:

- Error metrics against reference values.
- Repeatability metrics.
- Manual mass-total error/repeatability calculations for three flow points with three trials per point.
- Short-term stability metrics.
- Drift and noise estimates.
- Configurable thresholds.

Done when:

- Calculations are reproducible from stored data.
- Unit tests cover nominal, boundary, and abnormal data.

### M8: Qt Desktop UI
Build the first usable desktop experience.

Deliverables:

- Main window with device/channel list.
- Connection setup for simulator and serial Modbus paths.
- Workflow launch and progress panels.
- Live numeric readings and time-series chart.
- Run history and result inspection views.

Done when:

- A user can launch the app, connect simulated devices, run calibration preview, run factory test, and inspect stored results.
- UI tests or smoke checks verify the main paths.
- Verification notes are recorded in `docs/M8_VERIFICATION.md`.

### M9: Reporting And Export
Generate operator-readable outputs.

Deliverables:

- Calibration and factory test report templates.
- CSV export for tabular measurements and metrics.
- Export package that includes metadata, results, and artifact references.

Done when:

- A simulator-generated run can produce report and CSV artifacts.
- Report tests verify required fields are present.
- Verification notes are recorded in `docs/M9_VERIFICATION.md`.

### M10: Flexible Experiment Extensions
Add the first extension points for R&D workflows.

Deliverables:

- Experiment definition model.
- Signal-processing module interface.
- Fixture-control placeholder interface.
- ML inference placeholder interface.
- Example simulator-backed experiment.

Done when:

- A simple experiment can collect data, run a processing module, store outputs, and display results.
- Verification notes are recorded in `docs/M10_VERIFICATION.md`.

### M11: Hardware Acceptance Preparation
Prepare for physical transmitter testing.

Deliverables:

- Hardware checklist.
- Register-map configuration template.
- Serial adapter validation tool.
- Dry-run and write-guard checks.
- Hardware acceptance test cases.
- Operator approval and audit-log review procedure for any first hardware parameter write.

Done when:

- A real-device test session can be attempted without changing workflows or UI architecture.
- Read-only hardware checks can run before any write-capable workflow is enabled.
- Verification notes are recorded in `docs/M11_VERIFICATION.md`.

### M12: Windows Packaging
Package the desktop app for a Windows lab or factory PC.

Deliverables:

- Packaging configuration.
- Dependency and driver notes.
- User data directory convention.
- Version stamping.
- Basic installer or distributable folder.

Done when:

- A clean Windows machine can run the packaged app with simulator workflows.
- Verification notes are recorded in `docs/M12_VERIFICATION.md`.

### M13: ASIO/IIS Frame Stream Module
Implement the first USB sound-card ASIO/IIS hardware module with a dedicated UI window that remains independent from transmitter communication channels.

Deliverables:

- ASIO/IIS frame-format and stream-configuration models.
- Optional ASIO backend that can discover Windows audio devices and clearly report when the ASIO host API or selected BRAVO-HD device is unavailable.
- Fake loopback backend for deterministic automated tests without physical hardware.
- Headless frame output and input capture APIs with configurable sample rate, bit depth or sample format, channel counts, samples per frame, frame count, and amplitude.
- Headless loopback acceptance runner for the BRAVO-HD device when IIS master output is wired to IIS slave input.
- CLI diagnostics for ASIO device listing and loopback smoke testing.
- Dedicated ASIO/IIS window with connect/disconnect control, parameter editing, module status, and log messages.
- Documentation updates for optional dependencies, hardware permissions, and hardware test commands.

Done when:

- Unit tests pass without ASIO hardware.
- The fake backend proves frame ordering, validation, and loopback metrics deterministically.
- On the lab PC, the CLI can detect the configured ASIO device or report the exact missing backend/driver condition.
- With the two IIS groups physically connected, the loopback smoke test passes against the BRAVO-HD hardware. The current verified hardware command uses native ASIO at 44100 Hz, ASIOSTInt24LSB, 2 input channels, 2 output channels, and 4410 samples per frame.
- The UI can open the ASIO/IIS window, edit parameters, connect and disconnect the module, and report status/log messages without changing other device-channel connection state.

### M14: Modbus Listener Diagnostics
Implement a separate read-only Modbus listener/sniffer tool path for lab diagnostics using com0com and hub4com after the virtual-port tooling is available.

Deliverables:

- Documented com0com/hub4com setup checklist.
- Listener configuration model for source/destination virtual COM ports, baud rate, parity, stop bits, and capture path.
- Read-only frame capture and timestamped diagnostic artifact storage.
- Fake serial endpoint or recorded-frame tests before opening virtual COM ports.
- UI or CLI diagnostics that clearly distinguish listener mode from the normal Modbus master path.

Done when:

- Automated tests pass without com0com/hub4com installed.
- On a lab PC with approved virtual-port tooling, the listener can capture frames from a known test route and store a diagnostic artifact.
- Listener mode cannot perform parameter writes or proxy/inject frames without an explicit future safety review.

### M15: Filling Trial Module
Implement the independent manual Filling Trial Module.

Status: implementation complete at software version `0.7.0` and SQLite schema
v4. Focused slice evidence is recorded in `docs/M15_VERIFICATION.md`; the final
repository-wide pytest result and source UI smoke evidence are appended there by
the final integration flow.

Deliverables:

- Pure filling-error, exactly-three-consecutive repeatability, and signed
  valve-closing advance calculations under `coreflow.analysis`.
- Schema v4 filling trial and immutable advance-profile records, indexes,
  foreign keys, v3 migration, legacy orphan Modbus Device ID backfill, and
  atomic persistence transitions.
- Headless `FillingTrialService` for shared flowmeter selection, explicit
  `future_adapter` device creation, group lifecycle, configuration restoration,
  manual trial append, analysis, Set Advance, profiles, and history.
- Embedded Qt Filling Trial workbench available from `Modules > Filling Module`,
  with current-device history and no formula or SQL logic in Qt code.
- Per-device restoration of the last calculated trial configuration while every
  new or reopened standard-scale mass entry remains blank.
- Multiple immutable advance profiles for one flowmeter, distinguished by
  control/valve label and full operating-condition snapshot.
- Manual-input-only operation with no pulse total, pulse acquisition, valve
  control, controller/transmitter write, or protocol traffic.
- English and Chinese operator documentation plus M15 verification evidence.

Done when:

- A shared flowmeter Device ID can be selected or explicitly created, and its
  Device ID is not reused as a controller/valve identity.
- Regular error and three-consecutive-trial repeatability are stored with source
  Trial IDs, timestamps, notes, and full configuration snapshots.
- At least three advance trials, including nonconsecutive selections, can create
  signed advance results and multiple immutable profiles.
- Set Advance completes the old group and atomically opens corrected regular
  Trial 1 without mixing old and corrected target-mass trials.
- Schema v4 migration, storage, service, UI, shell, packaging-import, and version
  focused tests have passing evidence in `docs/M15_VERIFICATION.md`.
- Final full-suite and source UI smoke results are recorded in
  `docs/M15_VERIFICATION.md` by the final integration pass rather than inferred
  from the focused suites.

### M16: Modbus Real-Time Zero Monitor

Add a read-only zero-monitor operation inside the existing Modbus Module.

Status: Phase 1-3 application implementation and Phase 4 read-only test code
are complete at software version `0.8.0`. Local deterministic, storage, history,
and Qt evidence is recorded in `docs/M16_VERIFICATION.md`; real-device Phase 4
execution remains pending.

Deliverables:

- Configurable logical map for the coherent 18-register DSP zero snapshot,
  with configurable absolute start and strict validation of fixed relative
  offsets, types, word counts, scale, units, ownership, and one merged
  FC04/FC03 read. The reviewed Krohne ABCD baseline is versioned at
  `config/register_maps/krohne_prj_main.json`.
- Optional 16-bit device ByteOrder preflight with exact four-enum mapping,
  mismatch blocking, diagnostic-only behavior when unavailable, and no
  automatic device/profile mutation.
- Headless zero-monitor service for 100 ms polling, cancellation,
  sequence/timestamp unwrapping, data-gap detection, continuous segments, and
  streaming partial CSV persistence, bounded in-memory windows, atomic artifact
  finalization, and interrupted-run recovery.
- Read-only display and persistence of the fixed 100 ms target plus observed
  monotonic poll-period distribution and achieved rate; no per-device interval
  setting in M16.
- Pure zero-monitor analysis for non-overlapping 600 ms candidates, short and
  long metrics, configurable thresholds, states, and reason codes.
- Explicit continuity recovery for hard gaps, zero-calibration activity,
  reserved status bits, duplicate frames, and non-breaking overruns, with
  persistent counters and deterministic new-segment behavior.
- A diagnostic production baseline with threshold values intentionally blank
  and pending bench approval; synthetic thresholds are test-only and cannot be
  persisted as production defaults.
- A custom long-decision window validated within 12 through 86400 seconds;
  capture duration remains unbounded until Stop while analysis memory remains
  bounded by the rolling window.
- `Operations > Zero Monitor` non-modal UI with zero-flow context, live plots,
  status/quality counters, metric table, per-device configuration, and a link
  to the existing guarded `Zero Cal` operation.
- Test Records integration, including plot/data reopen and portable JSON
  export/import of the snapshot artifact.
- Fake-transport fixtures followed by separate read-only real-device evidence.

Done when:

- A normal or transport-failed logical poll is proven to issue one coherent
  18-register request rather than per-variable reads; only a torn snapshot may
  issue one bounded full-block reread.
- Invalid, torn, duplicate, missing, wrapped, and restarted snapshot sequences
  produce deterministic states and preserved evidence.
- Failed logical polls are stored without stale measurement values; long runs
  remain memory-bounded, and an interrupted partial capture is recovered as an
  explicitly incomplete artifact and error run.
- Live and persisted curves can be reviewed through the existing Modbus UI and
  Test Records infrastructure.
- Missing thresholds or unconfirmed zero-flow context cannot produce a
  `STABLE` result.
- No zero-monitor path writes a device; formal calibration remains guarded and
  audited by the existing workflow.
- Hardware claims are limited to the validation stage actually executed.

### M17: Modbus Register Map Library

Separate reusable, versioned register maps from Device Profiles without
rewriting historical register-map snapshots.

Status: local implementation complete at software version `0.9.0` and SQLite
schema v6. Real-device validation is not required for catalog management;
future DSP discovery-register validation remains pending.

Deliverables:

- SQLite schema v6 register-map catalog keyed by stable map ID and version.
- Deterministic migration of existing inline Device Profile maps into shared
  legacy catalog entries, deduplicated by normalized content checksum.
- Device Profile binding to one map ID and version while retaining an effective
  inline snapshot for compatibility and recovery.
- Disconnected-only UI for selecting an existing list or creating a named list,
  with change preview and immutable official versions.
- Packaged official map discovery so client updates can install new catalog
  versions without silently rebinding existing Device IDs.
- A checked-in Krohne DSP extractor that validates active address, width,
  register-kind, and access declarations, then generates the complete
  `krohne-prj-main` official list with reviewed client semantics and source
  commit metadata.
- Future fixed DSP discovery-block contract documented but not implemented
  until addresses and encodings are approved.

Done when:

- Existing profiles reopen with the same effective register definitions after
  migration and identical legacy maps are shared.
- Multiple Device IDs can bind the same list version, while editing one profile
  creates or selects another version without rewriting the other profile.
- Runtime sessions and operation attempts still store the complete effective
  map snapshot used for communication.
- Official same-ID/same-version content conflicts are rejected, and packaged
  maps are present in the Windows distribution.
- Local storage, runtime, Qt, packaging, and regression tests pass without
  opening serial hardware or issuing device writes.

## Implementation Defaults
- Use PySide6 for Qt unless a documented blocker appears.
- Use pytest for tests.
- Use pyqtgraph for live time-series plots.
- Use SQLite directly or SQLAlchemy with lightweight migrations.
- Use CSV or Parquet for large tabular artifacts; choose CSV first unless performance requires Parquet.
- Use seeded simulator scenarios for repeatable integration tests.

## Traceability Requirement
Every PRD functional requirement must map to at least one milestone and one test case. Maintain the traceability table in `docs/PRD.md` and the test identifiers in `docs/TEST_PLAN.md` as implementation grows.

## Documentation Updates During Implementation
Update the docs when:

- A public interface changes.
- The package layout changes.
- A schema or artifact format changes.
- A workflow step is added, removed, or redefined.
- A known unknown becomes known.
- Hardware behavior contradicts simulator assumptions.

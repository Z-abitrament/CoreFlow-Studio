# CoreFlow Studio Agent Guide

## Purpose
CoreFlow Studio is a Windows-first desktop application for automating Coriolis flowmeter transmitter debugging, factory calibration, fixed test flows, error analysis, stability analysis, flexible experiments, data processing, and visualization.

This repository starts as a documentation harness. Future coding agents must treat these documents as the source of truth before writing application code.

## Canonical Documents
Read these documents before implementation work:

- `docs/PRD.md`: product goals, user workflows, requirements, and known unknowns.
- `docs/ARCHITECTURE.md`: target software architecture and boundaries.
- `docs/IMPLEMENTATION_PLAN.md`: milestone order and implementation sequence.
- `docs/TEST_PLAN.md`: required verification strategy.
- `docs/PROTOCOLS.md`: communication and protocol contracts.
- `docs/DATA_MODEL.md`: storage model, file layout, and data traceability.
- `docs/SIMULATION.md`: simulator requirements and hardware-free development path.
- `docs/DEVELOPMENT_WORKFLOW.md`: local git workflow, overnight run checklist, permissions, and developer setup.
- `docs/DEVELOPER_SETUP.md`: Windows PowerShell setup and M0 verification commands.

If documents disagree, prefer this order:

1. Safety and data integrity requirements.
2. Product requirements in `docs/PRD.md`.
3. Architecture boundaries in `docs/ARCHITECTURE.md`.
4. Implementation sequencing in `docs/IMPLEMENTATION_PLAN.md`.
5. Tests and acceptance criteria in `docs/TEST_PLAN.md`.

## Non-Negotiable Defaults
- Build v1 as a local Windows desktop application.
- Use Python as the primary language.
- Use Qt for the GUI, preferably PySide6 unless there is a strong documented reason to choose PyQt.
- Use simulation-first development. Every v1 workflow must run against virtual transmitters without physical hardware.
- Use Modbus RTU over USB-to-serial as the first concrete hardware protocol.
- Design for 4-8 concurrent ports from the beginning.
- Store structured metadata and results in SQLite.
- Store large raw signals, time-series captures, exports, and report artifacts as files referenced from SQLite.
- Keep real hardware, simulators, replay sources, and future protocols behind stable interfaces.

## Engineering Rules
- Do not hard-code calibration formulas, Modbus register maps, acceptance thresholds, or fixture behavior unless they are provided in project configuration or explicitly documented.
- Treat unknown instrument details as configuration points, simulator inputs, or documented TODOs.
- Keep the workflow engine independent from the GUI so automated tests can run headless.
- Keep protocol adapters independent from calibration, analysis, and UI code.
- Make data traceable: every test run must be tied to device identity, software version, operator or automation source, configuration, timestamps, raw files, processed outputs, and pass/fail decisions.
- Prefer deterministic tests and deterministic simulator scenarios.
- Add tests when changing protocol parsing, workflow state transitions, calculations, storage, or report generation.

## Suggested Python Stack
- GUI: PySide6.
- Serial and protocol: pyserial, pymodbus.
- Data: sqlite3 or SQLAlchemy, pandas, NumPy.
- Signal processing: SciPy.
- Machine learning extensions: scikit-learn first; keep model execution isolated behind plugin-style interfaces.
- Plotting: pyqtgraph for live signals; matplotlib only where static reports need it.
- Packaging: PyInstaller or equivalent Windows packaging after the application has stable entry points.
- Testing: pytest, pytest-qt, and simulator-driven integration tests.

## Development Workflow For Future Agents
1. Read the canonical documents.
2. Identify the relevant milestone in `docs/IMPLEMENTATION_PLAN.md`.
3. Confirm whether the work is documentation-only, application code, tests, or packaging.
4. Implement the smallest coherent milestone slice.
5. Run the relevant tests from `docs/TEST_PLAN.md`.
6. Update docs when behavior, interfaces, schemas, or known unknowns change.

## Version Control And Overnight Work
- Initialize a local git repository before implementation work if `.git` is absent.
- Use checkpoint commits after coherent passing slices.
- Never commit failing or half-written work unless the commit message clearly marks it as `WIP:`.
- Never rewrite history, delete user changes, or run destructive git commands without explicit approval.
- Do not push to a remote unless the user provides a remote and asks for it.
- For unattended or overnight work, stop at the requested milestone, then summarize completed work, tests, blockers, dirty working tree status, and commit hashes before handing back.

## Safety And Integrity
The application may eventually control physical test fixtures and write calibration parameters to instruments. Future implementation must assume unsafe writes are possible.

Required safeguards:

- Separate read-only diagnostics from write-capable calibration operations.
- Require explicit workflow state before writing device parameters.
- Record every device write in an audit trail.
- Support dry-run mode in workflows that can change transmitter parameters.
- Never allow simulator assumptions to silently become hardware assumptions.

## Current Repository State
This repository has completed M5 calibration preview workflow foundation. It contains the canonical documentation harness, minimal Python package, entry point, dependency metadata, smoke tests, `FlowmeterDevice`, device data models, workflow status models, storage artifact models, deterministic simulator scenarios, `SimulatedFlowmeterDevice`, a multi-device simulator manager, Modbus register-map models, encoding/decoding helpers, a fake-testable transport abstraction, `ModbusRtuFlowmeterDevice`, SQLite schema initialization, metadata repositories, artifact file storage, artifact integrity checks, calibration preview models, a placeholder calibration calculator, `WriteGuardService`, and a headless calibration preview workflow. It does not yet contain production calibration formulas, calibration apply workflow, report generation, hardware acceptance, replay-file support, packaging, or UI implementation.

Before creating code, confirm that the requested work is moving from documentation harness to implementation. If the user asks to continue documentation work, update the canonical documents and keep them internally consistent before adding any source files.

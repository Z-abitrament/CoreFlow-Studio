# Data Model

## Summary
CoreFlow Studio stores structured metadata and results in SQLite and stores large or external artifacts as files. Every calibration, test, and experiment run must be traceable from device identity and configuration to raw data, processed results, reports, and audit logs.

## Storage Principles
- SQLite is the local source of truth for metadata, status, metrics, and artifact references.
- Large raw captures and generated files are stored outside SQLite.
- Every artifact file must have a database record.
- Every workflow run must be reproducible or explain why it cannot be reproduced.
- Schema changes must be handled through explicit migrations after the first implementation.

## Suggested User Data Layout
On Windows, store application data under a user-writable application data directory.

Suggested layout:

```text
CoreFlowStudioData/
  coreflow.sqlite
  artifacts/
    runs/
      2026/
        06/
          RUN-20260605-000001/
            raw/
            processed/
            exports/
            reports/
            logs/
  simulator/
    scenarios/
    replays/
  config/
    register_maps/
    workflow_templates/
```

The exact base directory should be configurable.

## Core Entities

### Device
Represents a physical or simulated flowmeter transmitter.

Fields:

- Device ID.
- Device type: simulated, Modbus RTU, or future adapter.
- Serial number when known.
- Model.
- Firmware version.
- Hardware version.
- Protocol address or unit ID.
- Last known connection metadata.
- Created and updated timestamps.

### Run Session
Represents one calibration, factory test, stability test, or experiment run.

Fields:

- Run ID.
- Run type.
- Workflow name and version.
- Device ID.
- Operator or automation source.
- Start time and end time.
- Status: pending, running, passed, failed, canceled, error.
- Configuration snapshot.
- Register-map, workflow-template, simulator-scenario, and threshold snapshot references when used.
- Software version.
- Notes.

### Workflow Step
Represents one step inside a run.

Fields:

- Step ID.
- Run ID.
- Step name.
- Step type.
- Start time and end time.
- Status.
- Input configuration.
- Output summary.
- Error message if failed.

### Measurement Sample
Low-rate samples may be stored in SQLite if they are small. High-rate or long-duration samples must be stored in artifact files.

Fields for small samples:

- Sample ID.
- Run ID.
- Step ID.
- Timestamp.
- Mass flow.
- Volume flow.
- Density.
- Temperature.
- Status flags.
- Source channel.

### Variable Sample
Stores timestamped values from configured Modbus or simulator logical variables, especially low-rate variables used by operator workflows.

Fields:

- Sample ID.
- Device ID.
- Run ID and step ID when the sample belongs to a workflow.
- Variable name, such as `mass_acc`, `delta_t`, `zero_offset`, or `k_factor`.
- Timestamp.
- Value as typed JSON.
- Unit.
- Source channel.
- Metadata copied from the register map or simulator parameter definition.

### Analysis Result
Stores calculated outputs from calibration, error analysis, stability analysis, signal processing, or ML inference.

Fields:

- Result ID.
- Run ID.
- Step ID.
- Result type.
- Input artifact references.
- Algorithm name and version.
- Configuration snapshot.
- Summary metrics.
- Pass/fail decision when applicable.

### Calibration Record
Stores calibration-specific outcomes.

Fields:

- Calibration ID.
- Run ID.
- Device ID.
- Reference points.
- Measured values.
- Calculated coefficients or parameter changes.
- Acceptance thresholds used.
- Preview or applied status.
- Write audit references.
- Zero calibration before/after `zero_offset`, `delta_t`, timestamps, control parameter, and completion status.
- K factor calibration pre-operation snapshot values, flow-rate segment timestamps, instantaneous flow sample, accumulated-mass before/after, measured mass delta, standard mass, current K factor, corrected K factor, mean flow, write request/apply/verify status, and readback value when available.
- Manual repeatability test mode, configured target-flow ranges, per-trial flow-segment timestamps, third-second instantaneous flow `v1`, mean flow `v_mean`, accumulated-mass before/after values, standard masses, percent errors, per-range repeatability standard deviations, and whether the saved summary came from fixed three-flow-range mode or the appendable single-flow-range mode.

### Artifact
Represents a file linked to a run, step, or result.

Fields:

- Artifact ID.
- Run ID.
- Step ID when applicable.
- Artifact type: raw, processed, export, report, log, replay, config snapshot.
- File path.
- File format.
- Size.
- Checksum if available.
- Created timestamp.

### Audit Log
Records safety-sensitive actions.

Fields:

- Audit ID.
- Timestamp.
- Actor.
- Action type.
- Workflow state at time of action.
- Device ID.
- Run ID when applicable.
- Target, such as register or parameter name.
- Previous value when available.
- New value when available.
- Dry-run flag.
- Validation result.
- Protocol request reference when a real or simulated write is transmitted.
- Result.
- Error message if failed.

Audit entries are required for attempted parameter writes, rejected writes, dry-run writes, simulator writes, and successful hardware writes. Audit records should be append-only from the application perspective.

### Configuration Snapshot
Stores or references configuration used to interpret a run.

Fields:

- Snapshot ID.
- Snapshot type: register map, workflow template, simulator scenario, thresholds, report template, or processing configuration.
- Name and version.
- Content hash when available.
- Artifact reference or serialized content.
- Created timestamp.

Runs and analysis results should reference configuration snapshots so future review can determine which assumptions produced a result.

## File Formats

Initial formats:

- CSV for simple tabular exports.
- JSON for configuration snapshots, simulator scenarios, and workflow templates.
- JSON for standalone Modbus calibration-history transfer packages between lab PCs.
- JSON for ASIO/IIS loopback diagnostics.
- SQLite for metadata and small structured results.
- Plain text or JSON lines for diagnostic logs.
- PDF, HTML, or DOCX can be considered for reports after report requirements are known.

For high-rate or long-duration data, choose between CSV and Parquet during implementation. Use CSV first unless measured performance requires Parquet. ASIO/IIS frame captures may use `.npy` for deterministic numeric arrays during hardware acceptance, with a JSON sidecar for configuration and metrics.

## Run Directory Naming
Use stable run IDs and human-sortable folders.

Suggested format:

```text
RUN-YYYYMMDD-NNNNNN
```

The run ID must be unique even if multiple runs start in the same second.

## Data Integrity Requirements
- A run cannot be marked complete until required artifacts are written or explicitly marked unavailable.
- SQLite records must reference relative paths from the configured data root when possible.
- Missing artifacts must produce a clear diagnostic.
- Raw data used for final results must not be overwritten by later processing.
- Processed outputs must reference the raw artifact and processing configuration used to create them.
- Calibration and write-capable runs must reference the register map, thresholds, and workflow template used for validation.
- Variable samples linked to a run must reference an existing device, run, and workflow step.
- Audit log entries must not be deleted by normal run cleanup or report export actions.

## Backup And Portability
v1 is local-first. A complete run package should be portable by copying:

- The relevant SQLite records.
- The run artifact folder.
- Configuration snapshots.
- Report/export files.

Full backup and restore tools are future work.

Standalone Modbus calibration history can also be moved between PCs with a
module-specific JSON package. That package stores completed Modbus calibration
run metadata, workflow steps, analysis metrics, notes, and device metadata for
zero calibration, K factor calibration, and repeatability records. Import skips
identical run IDs and preserves conflicting imported runs under a new imported
run ID so independent test PCs do not overwrite each other's local history. The
package metadata records the selected operation filter and optional started-at
time range used for export. Excel export is reserved for a later
reporting/export pass; JSON remains the portable interchange format for now.

## Known Unknowns
- Final report file format.
- Required retention period.
- Required checksum or signing policy.
- Whether production systems require operator login or electronic signature.
- Whether customer or regulatory systems require a specific export schema.
- Whether low-rate variable samples require retention outside workflow-linked runs for routine diagnostics.

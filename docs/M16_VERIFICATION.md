# M16 Verification

## Scope

M16 implements the read-only Modbus real-time zero monitor at software version
`0.8.0` without changing SQLite schema v5.

Completed locally:

- Phase 1 pure models, map validation, four-order decoding, continuity, bounded
  independent-window analysis, production-empty thresholds, and deterministic
  firmware-derived fixtures.
- Phase 2 retry-free coherent polling, one bounded torn reread, streaming
  partial CSV, atomic artifact finalization, lifecycle records, startup
  recovery, Test Records loading, and JSON export/import.
- Phase 3 `Operations > Zero Monitor`, zero-flow run assertion, fixed 100 ms
  target display, configuration, live plots, quality counters, history segment
  selection, and guarded Zero Cal navigation.
- Phase 4 bounded read-only online runner and CLI evidence generation.
- A reviewed ABCD-default Krohne register map at
  `config/register_maps/krohne_prj_main.json`, tied to firmware
  HEAD `f0a1b39ba1f4394253ee0adf7d0aee47c123ff9a`.
- Host compatibility closure for the firmware's FC03 holding/RW ByteOrder and
  ZeroOffset registers, strict scale/unit validation, normal DSP warmup state,
  and separate long mean, max-min, P95-P5, and Allan-style adjacent RMS values.

Not executed in this verification:

- A real serial transmitter connection.
- Online FC03/FC04, ByteOrder, 10 Hz timing, or no-state-change evidence.
- Controlled zero-flow bench threshold qualification.

## Local Commands

```powershell
conda run -n coreflow-studio python -m pytest tests\test_zero_monitor_analysis.py tests\test_zero_monitor_protocol.py tests\test_zero_monitor_service.py tests\test_ui_zero_monitor.py tests\test_zero_monitor_hardware_acceptance.py -q
conda run -n coreflow-studio python -m pytest -q
```

The focused M16 suite covers pure calculations, map/continuity behavior,
coherent-request bounds, byte-order preflight, failure rows, stable-window
readiness, storage lifecycle, interrupted recovery, history export/import,
segmented plotting, UI controls, the reviewed Krohne map, and the online-runner
verdict contract.

Final local results on 2026-07-21:

- M16 focused command: `57 passed in 15.29s`.
- Repository-wide command: `431 passed in 206.52s`.
- Python compile check: passed.
- `git diff --check`: passed; Git reported only the repository's existing
  Windows CRLF conversion warnings.

## Online Command

Run this only after selecting the correct production Device ID, serial settings,
and reviewed register-map JSON. It performs reads only.

```powershell
conda run -n coreflow-studio python -m coreflow `
  --zero-monitor-online-test `
  --zero-monitor-register-map .\config\register_maps\krohne_prj_main.json `
  --zero-monitor-device-id <device-id> `
  --modbus-port <COM-port> `
  --modbus-unit <unit-id> `
  --modbus-baudrate 9600 `
  --modbus-parity N `
  --modbus-stop-bits 1 `
  --zero-monitor-duration 30 `
  --data-root <evidence-data-root>
```

The runner records one JSON verdict plus the normal SQLite/CSV evidence. It
checks run/artifact creation, logical-to-physical request accounting,
ByteOrder verification, ZeroOffset before/after equality, and observed timing.
It deliberately applies no unapproved timing or stability threshold.

## Handoff State

Local software status: complete.

Hardware-coupled event status: `waiting-field-test`. The strongest current
evidence is deterministic local software and Qt verification. A successful
future online run may claim only `non_laboratory_online_read_only` validation;
bench or production qualification requires the separate zero-flow procedure
and approved thresholds documented in the M16 design.

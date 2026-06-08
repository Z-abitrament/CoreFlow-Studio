# M11 Verification

## Scope
M11 prepares for first physical transmitter testing. It does not enable automatic hardware connection from the UI, does not perform armed parameter writes, and does not claim that the placeholder register map matches production firmware.

## Implemented
- Placeholder Modbus register-map template with logical names required by `docs/PROTOCOLS.md`.
- JSON serialization and loading helpers for Modbus register maps.
- Serial port scanner abstraction using pyserial list-port discovery by default and injected providers in tests.
- `HardwareAcceptanceRunner` for read-only acceptance preparation checks:
  - port discovery;
  - connection open;
  - identity read;
  - health read;
  - live measurement read;
  - read-only factory preview;
  - write-guard dry-run validation without armed writes.
- Hardware acceptance run metadata and log artifact persistence when a device identity can be read.
- CLI command to write the placeholder register-map template:

```powershell
conda run -n coreflow-studio python -m coreflow --write-register-map-template .\config\register_maps\placeholder_modbus.json
```

## Commands Run
```powershell
conda run -n coreflow-studio python -m pytest tests\test_hardware_acceptance.py -q
conda run -n coreflow-studio python -m pytest -q
```

## Results
- M11 focused tests passed: 5 tests passed.
- Full test suite passed: 66 tests passed.
- M11 covers the hardware acceptance preparation scenarios in `docs/TEST_PLAN.md` with fake Modbus transport and injected serial-port discovery.

## Notes
- The placeholder register map is marked as a template and must be replaced or edited from real firmware documentation before real transmitter use.
- The dry-run check forces `WriteMode.DRY_RUN`; it does not transmit Modbus write requests.
- First real hardware use still requires operator review of serial settings, register map, read-only checks, and audit logs.

# M3 Verification

## Scope
M3 implements the first Modbus RTU protocol adapter behind the M1 `FlowmeterDevice` interface. It does not require physical hardware and does not implement workflow execution, storage repositories, hardware acceptance, or UI.

## Implemented
- `SerialConfig` for per-channel serial settings and Modbus unit ID.
- `ModbusRegisterMap` and `ModbusRegister` configuration models.
- Register encoding and decoding for bool, signed/unsigned 16-bit, signed/unsigned 32-bit, and float32 values.
- Word order, byte order, scaling, writable permission, and range metadata.
- `ModbusTransport` abstraction for fake/loopback testing.
- `PymodbusSerialTransport` wrapper for pymodbus 3.13 using `device_id`.
- `ModbusRtuFlowmeterDevice` implementing `FlowmeterDevice`.
- Timeout/retry diagnostics and write validation before protocol transmission.

## Commands Run
```powershell
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m coreflow
.\.venv\Scripts\python -m coreflow --version
```

## Results
- Pytest passed: 31 tests passed.
- M0 entry-point tests still pass.
- M1 interface and model tests still pass.
- M2 simulator tests still pass.
- M3 protocol tests cover `TP-PROTO-001` using an in-memory fake transport.

## Notes
- Physical USB-to-serial hardware tests remain deferred until register maps, serial settings, and safety rules are available.
- Production register addresses are not hard-coded; tests use local fixture register maps.
- Coil/discrete input support is modeled but not implemented for reads in M3 because v1 measurement/configuration needs are register-oriented.

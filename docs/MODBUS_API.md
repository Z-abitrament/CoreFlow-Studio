# Modbus API

## Purpose
CoreFlow Studio exposes a small Python API for scripts and external engineering tools that need the same Modbus RTU raw-frame behavior as the Modbus Module, without driving the Qt window.

Use this API for local lab automation on a PC that owns the USB-to-serial adapter. It is not a network service and does not expose a long-running remote-control port.

## Python Import API
Install or run CoreFlow Studio from a checkout where `coreflow-studio` is available, then use:

```python
from coreflow.modbus_api import ModbusRawClient, bytes_to_hex

with ModbusRawClient(port="COM9", unit_id=1, baudrate=19200) as client:
    response = client.send_raw_frame("01 03 00 3D 00 02", append_crc=True)

print(bytes_to_hex(response))
```

`send_raw_frame` accepts either bytes or a hex string. Hex strings may include spaces, for example `01 03 00 3D 00 02`, or be compact, for example `0103003D0002`.

When `append_crc=True`, pass the frame body without CRC. When `append_crc=False`, pass the complete RTU frame including CRC.

## Standard Frame Routing
For standard Modbus RTU function codes, the API uses the same high-level communication path as the Modbus Module Read/Write controls and then returns the response as raw bytes:

- `01`, `02`, `03`, `04`: read coils, discrete inputs, holding registers, or input registers.
- `05`, `06`, `0F`, `10`: write single coil, single register, multiple coils, or multiple registers.

Non-standard frames, malformed standard frames, or frames with invalid CRC fall back to the low-level raw frame send path for diagnostics.

## CLI Wrapper
The packaged console executable can send one raw frame and print the response as uppercase hex:

```powershell
.\CoreFlowStudioConsole.exe --modbus-raw "01 03 00 3D 00 02" --modbus-port COM9 --modbus-unit 1 --modbus-auto-crc
```

Useful options:

```text
--modbus-baudrate 19200
--modbus-parity N
--modbus-stop-bits 1
--modbus-timeout 3.0
--modbus-retries 3
```

The CLI exits with code `0` on success and `2` when the port cannot be opened, the frame is invalid, or the Modbus request fails.

## Safety
This API can transmit Modbus write function codes when the caller sends them. It is intended for explicit engineering scripts, not unattended production calibration writes. Higher-level calibration workflows and audited parameter writes remain in the main application workflow layer.

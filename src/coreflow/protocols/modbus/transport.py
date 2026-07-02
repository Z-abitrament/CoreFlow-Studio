"""Transport abstractions for Modbus RTU adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException
from serial import SerialException

from coreflow.protocols.modbus.models import RegisterKind, SerialConfig


@dataclass(frozen=True, slots=True)
class TransportResponse:
    """Normalized transport response from a Modbus request."""

    values: list[int] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ModbusTransport(Protocol):
    """Minimal transport protocol used by the device adapter."""

    def connect(self) -> bool: ...

    def close(self) -> None: ...

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse: ...

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse: ...

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse: ...

    def write_single_register(
        self,
        address: int,
        value: int,
        unit_id: int,
    ) -> TransportResponse: ...

    def write_coils(
        self,
        address: int,
        values: list[bool],
        unit_id: int,
    ) -> TransportResponse: ...

    def send_raw_frame(self, frame: bytes) -> TransportResponse: ...


class PymodbusSerialTransport:
    """pyserial/pymodbus-backed Modbus RTU transport."""

    def __init__(self, config: SerialConfig) -> None:
        self._config = config
        self._client = ModbusSerialClient(
            port=config.port,
            baudrate=config.baudrate,
            bytesize=config.data_bits,
            parity=config.parity,
            stopbits=config.stop_bits,
            timeout=config.read_timeout_s,
            retries=config.retry_count,
        )
        self._last_error: str | None = None

    def connect(self) -> bool:
        try:
            connected = bool(self._client.connect())
        except (OSError, SerialException, ModbusException) as exc:
            self._last_error = _format_open_error(self._config, str(exc))
            return False
        if connected:
            self._last_error = None
            return True
        self._last_error = _format_open_error(self._config, None)
        return False

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def close(self) -> None:
        self._client.close()

    def _ensure_connected(self) -> bool:
        connected = getattr(self._client, "connected", True)
        if callable(connected):
            connected = connected()
        if connected:
            return True
        return self.connect()

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        try:
            if kind is RegisterKind.HOLDING:
                response = self._client.read_holding_registers(
                    address,
                    count=count,
                    device_id=unit_id,
                )
            elif kind is RegisterKind.INPUT:
                response = self._client.read_input_registers(
                    address,
                    count=count,
                    device_id=unit_id,
                )
            elif kind is RegisterKind.COIL:
                response = self._client.read_coils(
                    address,
                    count=count,
                    device_id=unit_id,
                )
            elif kind is RegisterKind.DISCRETE_INPUT:
                response = self._client.read_discrete_inputs(
                    address,
                    count=count,
                    device_id=unit_id,
                )
            else:
                return TransportResponse(error=f"Unsupported register kind: {kind}")
        except ModbusException as exc:
            return TransportResponse(error=str(exc))

        if response is None:
            return TransportResponse(error="No Modbus response.")
        if hasattr(response, "isError") and response.isError():
            return TransportResponse(error=str(response))
        if kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT):
            bits = getattr(response, "bits", None)
            if bits is not None and len(bits) >= count:
                return TransportResponse(values=[1 if bit else 0 for bit in bits[:count]])
            values = _decode_bit_response(response, count)
            if values is not None:
                return TransportResponse(values=values)
        registers = getattr(response, "registers", None)
        if registers is not None:
            return TransportResponse(values=list(registers))
        return TransportResponse(error="Modbus response did not include values.")

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        try:
            response = self._client.write_registers(
                address,
                values,
                device_id=unit_id,
            )
        except ModbusException as exc:
            return TransportResponse(error=str(exc))

        if response is None:
            return TransportResponse(error="No Modbus response.")
        if hasattr(response, "isError") and response.isError():
            return TransportResponse(error=str(response))
        return TransportResponse(values=list(values))

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        try:
            response = self._client.write_coil(
                address,
                value,
                device_id=unit_id,
            )
        except ModbusException as exc:
            return TransportResponse(error=str(exc))

        if response is None:
            return TransportResponse(error="No Modbus response.")
        if hasattr(response, "isError") and response.isError():
            return TransportResponse(error=str(response))
        return TransportResponse(values=[1 if value else 0])

    def write_single_register(
        self,
        address: int,
        value: int,
        unit_id: int,
    ) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        try:
            response = self._client.write_register(address, value, device_id=unit_id)
        except ModbusException as exc:
            return TransportResponse(error=str(exc))
        return _write_response_result(response, [value])

    def write_coils(
        self,
        address: int,
        values: list[bool],
        unit_id: int,
    ) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        try:
            response = self._client.write_coils(address, values, device_id=unit_id)
        except ModbusException as exc:
            return TransportResponse(error=str(exc))
        return _write_response_result(response, [1 if value else 0 for value in values])

    def send_raw_frame(self, frame: bytes) -> TransportResponse:
        if not self._ensure_connected():
            return TransportResponse(error=self._open_error())
        outbound = bytes(frame)
        routed_response = self._send_standard_raw_frame(outbound)
        if routed_response is not None:
            return routed_response
        try:
            written = self._client.send(outbound)
        except (OSError, SerialException, ModbusException) as exc:
            return TransportResponse(error=str(exc))
        expected = len(outbound)
        if written != expected:
            return TransportResponse(
                error=f"Raw frame write incomplete: wrote {written} of {expected} byte(s)."
            )
        try:
            response = self._client.recv(None)
        except (OSError, SerialException, ModbusException) as exc:
            return TransportResponse(error=str(exc))
        return TransportResponse(values=list(response))

    def _open_error(self) -> str:
        return self._last_error or _format_open_error(self._config, None)

    def _send_standard_raw_frame(self, frame: bytes) -> TransportResponse | None:
        if not _valid_modbus_crc(frame) or len(frame) < 4:
            return None
        unit_id = frame[0]
        function = frame[1]
        try:
            if function in (0x01, 0x02, 0x03, 0x04):
                if len(frame) != 8:
                    return None
                address = _read_u16(frame, 2)
                count = _read_u16(frame, 4)
                kind = _raw_read_kind(function)
                response = self.read_registers(kind, address, count, unit_id)
                if not response.ok:
                    return response
                payload = (
                    _bits_to_response_bytes(response.values or [], count)
                    if kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT)
                    else _words_to_bytes(response.values or [])
                )
                return TransportResponse(
                    values=list(_raw_frame([unit_id, function, len(payload), *payload]))
                )
            if function == 0x05:
                if len(frame) != 8:
                    return None
                address = _read_u16(frame, 2)
                raw_value = _read_u16(frame, 4)
                if raw_value not in (0x0000, 0xFF00):
                    return None
                response = self.write_coil(address, raw_value == 0xFF00, unit_id)
                if not response.ok:
                    return response
                return TransportResponse(values=list(_raw_frame(list(frame[:6]))))
            if function == 0x06:
                if len(frame) != 8:
                    return None
                address = _read_u16(frame, 2)
                value = _read_u16(frame, 4)
                response = self.write_single_register(address, value, unit_id)
                if not response.ok:
                    return response
                return TransportResponse(values=list(_raw_frame(list(frame[:6]))))
            if function == 0x0F:
                if len(frame) < 9:
                    return None
                address = _read_u16(frame, 2)
                count = _read_u16(frame, 4)
                byte_count = frame[6]
                if len(frame) != 9 + byte_count:
                    return None
                values = _decode_bit_values(frame[7 : 7 + byte_count], count)
                response = self.write_coils(address, values, unit_id)
                if not response.ok:
                    return response
                return TransportResponse(
                    values=list(_raw_frame([unit_id, function, *_u16(address), *_u16(count)]))
                )
            if function == 0x10:
                if len(frame) < 9:
                    return None
                address = _read_u16(frame, 2)
                count = _read_u16(frame, 4)
                byte_count = frame[6]
                if len(frame) != 9 + byte_count or byte_count != count * 2:
                    return None
                values = [
                    _read_u16(frame, offset)
                    for offset in range(7, 7 + byte_count, 2)
                ]
                response = self.write_registers(address, values, unit_id)
                if not response.ok:
                    return response
                return TransportResponse(
                    values=list(_raw_frame([unit_id, function, *_u16(address), *_u16(count)]))
                )
        except (OSError, SerialException, ModbusException) as exc:
            return TransportResponse(error=str(exc))
        return None

def _format_open_error(config: SerialConfig, detail: str | None) -> str:
    message = (
        "Unable to open Modbus RTU transport "
        f"on {config.port} "
        f"({config.baudrate} baud, {config.data_bits}{config.parity}{config.stop_bits}, "
        f"timeout={config.read_timeout_s:g}s)."
    )
    if detail:
        return f"{message} {detail}"
    return (
        f"{message} Check that the USB-to-serial driver is installed, "
        "the COM port is not already open in another program, and the selected port matches the adapter."
    )


def _decode_bit_response(response: object, count: int) -> list[int] | None:
    """Decode FC01/FC02 data from pymodbus response payload when bits is empty."""

    encoder = getattr(response, "encode", None)
    if not callable(encoder):
        return None
    payload = encoder()
    if isinstance(payload, str):
        payload = payload.encode()
    if not isinstance(payload, bytes | bytearray) or len(payload) < 1:
        return None
    byte_count = int(payload[0])
    data = bytes(payload[1 : 1 + byte_count])
    if len(data) < byte_count:
        return None
    values: list[int] = []
    for byte in data:
        for bit_index in range(8):
            values.append(1 if byte & (1 << bit_index) else 0)
            if len(values) == count:
                return values
    return values if len(values) >= count else None


def _write_response_result(response: object, values: list[int]) -> TransportResponse:
    if response is None:
        return TransportResponse(error="No Modbus response.")
    if hasattr(response, "isError") and response.isError():
        return TransportResponse(error=str(response))
    return TransportResponse(values=values)


def _raw_read_kind(function: int) -> RegisterKind:
    if function == 0x01:
        return RegisterKind.COIL
    if function == 0x02:
        return RegisterKind.DISCRETE_INPUT
    if function == 0x03:
        return RegisterKind.HOLDING
    if function == 0x04:
        return RegisterKind.INPUT
    raise ValueError(f"Unsupported raw read function: {function}")


def _read_u16(frame: bytes | bytearray, offset: int) -> int:
    return (frame[offset] << 8) | frame[offset + 1]


def _u16(value: int) -> list[int]:
    return [(value >> 8) & 0xFF, value & 0xFF]


def _words_to_bytes(values: list[int]) -> list[int]:
    bytes_out: list[int] = []
    for value in values:
        bytes_out.extend(_u16(value))
    return bytes_out


def _bits_to_response_bytes(values: list[int], count: int) -> list[int]:
    byte_count = (count + 7) // 8
    payload = [0] * byte_count
    for index, value in enumerate(values[:count]):
        if value:
            payload[index // 8] |= 1 << (index % 8)
    return payload


def _decode_bit_values(payload: bytes | bytearray, count: int) -> list[bool]:
    values: list[bool] = []
    for byte in payload:
        for bit_index in range(8):
            values.append(bool(byte & (1 << bit_index)))
            if len(values) == count:
                return values
    return values


def _raw_frame(payload: list[int]) -> bytes:
    frame = bytes(value & 0xFF for value in payload)
    crc = _modbus_crc(frame)
    return frame + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def _valid_modbus_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    expected = _modbus_crc(frame[:-2])
    actual = frame[-2] | (frame[-1] << 8)
    return expected == actual


def _modbus_crc(values: bytes | bytearray) -> int:
    crc = 0xFFFF
    for value in values:
        crc ^= value
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF

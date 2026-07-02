"""Public Modbus RTU API for scripts and external CoreFlow integrations."""

from __future__ import annotations

from collections.abc import Callable

from coreflow.protocols.modbus import (
    PymodbusSerialTransport,
    RegisterKind,
    SerialConfig,
    TransportResponse,
)
from coreflow.protocols.modbus.transport import ModbusTransport


class ModbusCommunicationError(RuntimeError):
    """Raised when a Modbus request cannot be completed."""


TransportFactory = Callable[[SerialConfig], ModbusTransport]


class ModbusRawClient:
    """Small context-managed API for Modbus RTU raw-frame communication."""

    def __init__(
        self,
        *,
        port: str,
        unit_id: int = 1,
        baudrate: int = 19200,
        data_bits: int = 8,
        parity: str = "N",
        stop_bits: int = 1,
        read_timeout_s: float = 3.0,
        write_timeout_s: float = 3.0,
        retry_count: int = 3,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self.config = SerialConfig(
            port=port,
            unit_id=unit_id,
            baudrate=baudrate,
            data_bits=data_bits,
            parity=parity,
            stop_bits=stop_bits,
            read_timeout_s=read_timeout_s,
            write_timeout_s=write_timeout_s,
            retry_count=retry_count,
        )
        factory = transport_factory or PymodbusSerialTransport
        self._transport = factory(self.config)
        self._connected = False

    def __enter__(self) -> "ModbusRawClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        if self._connected:
            return
        if not self._transport.connect():
            last_error = getattr(self._transport, "last_error", None)
            raise ModbusCommunicationError(
                last_error or f"Unable to open Modbus RTU transport on {self.config.port}."
            )
        self._connected = True

    def close(self) -> None:
        self._transport.close()
        self._connected = False

    def send_raw_frame(
        self,
        frame: bytes | bytearray | str,
        *,
        append_crc: bool = False,
    ) -> bytes:
        self.connect()
        outbound = parse_hex_frame(frame)
        if append_crc:
            outbound = append_modbus_crc(outbound)
        response = self._send_standard_raw_frame(outbound)
        if response is None:
            response = self._transport.send_raw_frame(outbound)
        if not response.ok:
            raise ModbusCommunicationError(response.error or "Modbus raw frame failed.")
        return bytes(response.values or [])

    def _send_standard_raw_frame(self, frame: bytes) -> TransportResponse | None:
        if not valid_modbus_crc(frame):
            return None
        unit_id = frame[0]
        function = frame[1]
        if function in (0x01, 0x02, 0x03, 0x04):
            if len(frame) != 8:
                return None
            address = read_u16(frame, 2)
            count = read_u16(frame, 4)
            kind = raw_read_kind(function)
            response = self._transport.read_registers(kind, address, count, unit_id)
            if not response.ok:
                return response
            payload = (
                bits_to_response_bytes(response.values or [], count)
                if kind in (RegisterKind.COIL, RegisterKind.DISCRETE_INPUT)
                else words_to_bytes(response.values or [])
            )
            return TransportResponse(
                values=list(raw_frame_bytes([unit_id, function, len(payload), *payload]))
            )
        if function == 0x05:
            if len(frame) != 8:
                return None
            address = read_u16(frame, 2)
            raw_value = read_u16(frame, 4)
            if raw_value not in (0x0000, 0xFF00):
                return None
            response = self._transport.write_coil(address, raw_value == 0xFF00, unit_id)
            if not response.ok:
                return response
            return TransportResponse(values=list(raw_frame_bytes(list(frame[:6]))))
        if function == 0x06:
            if len(frame) != 8:
                return None
            address = read_u16(frame, 2)
            value = read_u16(frame, 4)
            writer = getattr(self._transport, "write_single_register", None)
            if not callable(writer):
                return None
            response = writer(address, value, unit_id)
            if not response.ok:
                return response
            return TransportResponse(values=list(raw_frame_bytes(list(frame[:6]))))
        if function == 0x0F:
            if len(frame) < 9:
                return None
            address = read_u16(frame, 2)
            count = read_u16(frame, 4)
            byte_count = frame[6]
            if len(frame) != 9 + byte_count:
                return None
            writer = getattr(self._transport, "write_coils", None)
            if not callable(writer):
                return None
            response = writer(address, decode_bit_values(frame[7 : 7 + byte_count], count), unit_id)
            if not response.ok:
                return response
            return TransportResponse(
                values=list(raw_frame_bytes([unit_id, function, *u16(address), *u16(count)]))
            )
        if function == 0x10:
            if len(frame) < 9:
                return None
            address = read_u16(frame, 2)
            count = read_u16(frame, 4)
            byte_count = frame[6]
            if len(frame) != 9 + byte_count or byte_count != count * 2:
                return None
            values = [
                read_u16(frame, offset)
                for offset in range(7, 7 + byte_count, 2)
            ]
            response = self._transport.write_registers(address, values, unit_id)
            if not response.ok:
                return response
            return TransportResponse(
                values=list(raw_frame_bytes([unit_id, function, *u16(address), *u16(count)]))
            )
        return None


def parse_hex_frame(frame: bytes | bytearray | str) -> bytes:
    if isinstance(frame, bytes | bytearray):
        return bytes(frame)
    tokens = _hex_tokens(frame)
    if not tokens:
        raise ValueError("enter at least one hex byte.")
    values: list[int] = []
    for token in tokens:
        if len(token) != 2:
            raise ValueError(f"invalid hex byte: {token}")
        try:
            values.append(int(token, 16))
        except ValueError as exc:
            raise ValueError(f"invalid hex byte: {token}") from exc
    return bytes(values)


def append_modbus_crc(frame: bytes) -> bytes:
    crc = modbus_crc(frame)
    return frame + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def valid_modbus_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    expected = modbus_crc(frame[:-2])
    actual = frame[-2] | (frame[-1] << 8)
    return expected == actual


def raw_frame_bytes(payload: list[int]) -> bytes:
    frame = bytes(value & 0xFF for value in payload)
    return append_modbus_crc(frame)


def bytes_to_hex(frame: bytes | bytearray) -> str:
    return " ".join(f"{value:02X}" for value in frame)


def raw_read_kind(function: int) -> RegisterKind:
    if function == 0x01:
        return RegisterKind.COIL
    if function == 0x02:
        return RegisterKind.DISCRETE_INPUT
    if function == 0x03:
        return RegisterKind.HOLDING
    if function == 0x04:
        return RegisterKind.INPUT
    raise ValueError(f"Unsupported raw read function: {function}")


def read_u16(frame: bytes | bytearray, offset: int) -> int:
    return (frame[offset] << 8) | frame[offset + 1]


def u16(value: int) -> list[int]:
    return [(value >> 8) & 0xFF, value & 0xFF]


def words_to_bytes(values: list[int]) -> list[int]:
    bytes_out: list[int] = []
    for value in values:
        bytes_out.extend(u16(value))
    return bytes_out


def bits_to_response_bytes(values: list[int], count: int) -> list[int]:
    byte_count = (count + 7) // 8
    payload = [0] * byte_count
    for index, value in enumerate(values[:count]):
        if value:
            payload[index // 8] |= 1 << (index % 8)
    return payload


def decode_bit_values(payload: bytes | bytearray, count: int) -> list[bool]:
    values: list[bool] = []
    for byte in payload:
        for bit_index in range(8):
            values.append(bool(byte & (1 << bit_index)))
            if len(values) == count:
                return values
    return values


def modbus_crc(values: bytes | bytearray) -> int:
    crc = 0xFFFF
    for value in values:
        crc ^= value
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def _hex_tokens(frame: str) -> list[str]:
    text = frame.strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    if not text:
        return []
    if any(character.isspace() for character in text):
        return [token.removeprefix("0x").removeprefix("0X") for token in text.split()]
    if len(text) % 2:
        raise ValueError(f"invalid hex byte: {text[-1]}")
    return [text[index : index + 2] for index in range(0, len(text), 2)]

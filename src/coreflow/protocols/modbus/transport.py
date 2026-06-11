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

    def _open_error(self) -> str:
        return self._last_error or _format_open_error(self._config, None)


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

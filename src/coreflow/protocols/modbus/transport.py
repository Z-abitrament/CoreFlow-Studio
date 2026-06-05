"""Transport abstractions for Modbus RTU adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pymodbus.client import ModbusSerialClient
from pymodbus.exceptions import ModbusException

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

    def connect(self) -> bool:
        return bool(self._client.connect())

    def close(self) -> None:
        self._client.close()

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
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
            else:
                return TransportResponse(error=f"Unsupported register kind: {kind}")
        except ModbusException as exc:
            return TransportResponse(error=str(exc))

        if response is None:
            return TransportResponse(error="No Modbus response.")
        if hasattr(response, "isError") and response.isError():
            return TransportResponse(error=str(response))
        registers = getattr(response, "registers", None)
        if registers is None:
            return TransportResponse(error="Modbus response did not include registers.")
        return TransportResponse(values=list(registers))

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
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

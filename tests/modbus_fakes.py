from __future__ import annotations

from dataclasses import dataclass, field
from time import sleep

from coreflow.hardware import build_placeholder_register_map
from coreflow.protocols.modbus import (
    ModbusRegisterMap,
    RegisterKind,
    SerialConfig,
    TransportResponse,
    encode_registers,
)


@dataclass
class FakeModbusTransport:
    registers: dict[int, list[int]]
    connected: bool = False
    read_delay_s: float = 0.0
    read_errors: dict[int, str] = field(default_factory=dict)
    read_sequences: dict[int, list[list[int]]] = field(default_factory=dict)
    reads: list[tuple[RegisterKind, int, int, int]] = field(default_factory=list)
    writes: list[tuple[int, list[int], int]] = field(default_factory=list)
    coil_writes: list[tuple[int, bool, int]] = field(default_factory=list)
    raw_frames: list[bytes] = field(default_factory=list)
    raw_response: bytes = b""

    def connect(self) -> bool:
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
        if self.read_delay_s:
            sleep(self.read_delay_s)
        self.reads.append((kind, address, count, unit_id))
        if address in self.read_errors:
            return TransportResponse(error=self.read_errors[address])
        if address in self.read_sequences and self.read_sequences[address]:
            values = self.read_sequences[address].pop(0)
            self.registers[address] = list(values)
            return TransportResponse(values=values[:count])
        values: list[int] = []
        cursor = address
        while len(values) < count:
            chunk = self.registers[cursor]
            values.extend(chunk)
            cursor += len(chunk)
        return TransportResponse(values=values[:count])

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
        self.writes.append((address, list(values), unit_id))
        self.registers[address] = list(values)
        return TransportResponse(values=values)

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse:
        self.coil_writes.append((address, value, unit_id))
        self.registers[address] = [0]
        return TransportResponse(values=[0])

    def send_raw_frame(self, frame: bytes) -> TransportResponse:
        self.raw_frames.append(bytes(frame))
        return TransportResponse(values=list(self.raw_response))


def placeholder_fake_transport() -> FakeModbusTransport:
    register_map = build_placeholder_register_map()
    values = {
        "mass_flow": 10.0,
        "mass_rate": 10.0,
        "mass_acc": 100.0,
        "volume_flow": 0.01,
        "density": 998.2,
        "temperature": 21.5,
        "delta_t": 0.12,
        "frequency": 83.0,
        "device_status": 1,
        "alarm_flags": 0,
        "serial_number": 12345,
        "zero_offset": 0.25,
        "k_factor": 500.0,
        "low_threshold": 0.1,
        "zero_calibration_start": False,
    }
    return FakeModbusTransport(_encoded_registers(register_map, values))


def _encoded_registers(
    register_map: ModbusRegisterMap,
    values: dict[str, object],
) -> dict[int, list[int]]:
    registers: dict[int, list[int]] = {}
    for name, value in values.items():
        register = register_map.by_name(name)
        registers[register.address] = encode_registers(register, value)
    return registers


def placeholder_transport_factory(holder: list[FakeModbusTransport]):
    def factory(_config: SerialConfig) -> FakeModbusTransport:
        transport = placeholder_fake_transport()
        holder.append(transport)
        return transport

    return factory

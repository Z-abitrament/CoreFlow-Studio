"""Configuration models for Modbus RTU communication."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RegisterKind(StrEnum):
    """Modbus register table type."""

    HOLDING = "holding"
    INPUT = "input"
    COIL = "coil"
    DISCRETE_INPUT = "discrete_input"


class ModbusDataType(StrEnum):
    """Data types supported by the first register-map abstraction."""

    UINT16 = "uint16"
    INT16 = "int16"
    UINT32 = "uint32"
    INT32 = "int32"
    FLOAT32 = "float32"
    BOOL = "bool"


class WordOrder(StrEnum):
    """Register word order for multi-register values."""

    BIG = "big"
    LITTLE = "little"


class ByteOrder(StrEnum):
    """Byte order within each 16-bit register."""

    BIG = "big"
    LITTLE = "little"


@dataclass(frozen=True, slots=True)
class SerialConfig:
    """Per-channel serial and Modbus configuration."""

    port: str
    unit_id: int
    baudrate: int = 19200
    data_bits: int = 8
    parity: str = "N"
    stop_bits: int = 1
    read_timeout_s: float = 3.0
    write_timeout_s: float = 3.0
    retry_count: int = 3


@dataclass(frozen=True, slots=True)
class ModbusRegister:
    """Logical register definition loaded from configuration."""

    name: str
    kind: RegisterKind
    address: int
    word_count: int
    data_type: ModbusDataType
    writable: bool = False
    scale: float = 1.0
    unit: str | None = None
    word_order: WordOrder = WordOrder.BIG
    byte_order: ByteOrder = ByteOrder.BIG
    minimum: float | None = None
    maximum: float | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModbusRegisterMap:
    """A versioned logical-name register map."""

    name: str
    version: str
    registers: tuple[ModbusRegister, ...]

    def by_name(self, name: str) -> ModbusRegister:
        for register in self.registers:
            if register.name == name:
                return register
        raise KeyError(f"Unknown Modbus register: {name}")

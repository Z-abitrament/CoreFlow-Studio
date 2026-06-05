"""Modbus RTU protocol adapter."""

from coreflow.protocols.modbus.device import ModbusRtuFlowmeterDevice
from coreflow.protocols.modbus.encoding import decode_registers, encode_registers
from coreflow.protocols.modbus.models import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    SerialConfig,
    WordOrder,
)
from coreflow.protocols.modbus.transport import (
    ModbusTransport,
    PymodbusSerialTransport,
    TransportResponse,
)

__all__ = [
    "ByteOrder",
    "ModbusDataType",
    "ModbusRegister",
    "ModbusRegisterMap",
    "ModbusRtuFlowmeterDevice",
    "ModbusTransport",
    "PymodbusSerialTransport",
    "RegisterKind",
    "SerialConfig",
    "TransportResponse",
    "WordOrder",
    "decode_registers",
    "encode_registers",
]

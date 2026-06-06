"""Register-map template helpers for hardware acceptance preparation."""

from __future__ import annotations

import json
from typing import Any

from coreflow.protocols.modbus import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    WordOrder,
)


def build_placeholder_register_map() -> ModbusRegisterMap:
    """Create a non-production register-map template with logical names."""

    placeholder = {"placeholder": True, "source": "M11 hardware acceptance template"}
    return ModbusRegisterMap(
        name="coreflow-placeholder-coriolis-map",
        version="0.1-template",
        registers=(
            ModbusRegister(
                name="mass_flow",
                kind=RegisterKind.INPUT,
                address=0,
                word_count=2,
                data_type=ModbusDataType.FLOAT32,
                unit="kg/s",
                description="Placeholder mass-flow input register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="volume_flow",
                kind=RegisterKind.INPUT,
                address=2,
                word_count=2,
                data_type=ModbusDataType.FLOAT32,
                unit="m3/h",
                description="Placeholder volume-flow input register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="density",
                kind=RegisterKind.INPUT,
                address=4,
                word_count=2,
                data_type=ModbusDataType.FLOAT32,
                unit="kg/m3",
                description="Placeholder density input register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="temperature",
                kind=RegisterKind.INPUT,
                address=6,
                word_count=2,
                data_type=ModbusDataType.FLOAT32,
                unit="C",
                description="Placeholder temperature input register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="device_status",
                kind=RegisterKind.INPUT,
                address=8,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                description="Placeholder device status register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="alarm_flags",
                kind=RegisterKind.INPUT,
                address=9,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                description="Placeholder alarm flags register.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="serial_number",
                kind=RegisterKind.HOLDING,
                address=20,
                word_count=2,
                data_type=ModbusDataType.UINT32,
                description="Placeholder transmitter serial number.",
                metadata=placeholder,
            ),
            ModbusRegister(
                name="zero_offset",
                kind=RegisterKind.HOLDING,
                address=100,
                word_count=2,
                data_type=ModbusDataType.FLOAT32,
                writable=True,
                minimum=-10.0,
                maximum=10.0,
                unit="kg/s",
                description="Placeholder writable zero-offset parameter.",
                metadata={
                    **placeholder,
                    "write_requires": "application write guard, operator approval, and hardware acceptance procedure",
                },
            ),
        ),
    )


def register_map_to_json(register_map: ModbusRegisterMap) -> str:
    payload = {
        "name": register_map.name,
        "version": register_map.version,
        "registers": [_register_to_dict(register) for register in register_map.registers],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def register_map_from_json(content: str) -> ModbusRegisterMap:
    payload = json.loads(content)
    return ModbusRegisterMap(
        name=str(payload["name"]),
        version=str(payload["version"]),
        registers=tuple(_register_from_dict(item) for item in payload["registers"]),
    )


def _register_to_dict(register: ModbusRegister) -> dict[str, Any]:
    return {
        "name": register.name,
        "kind": register.kind.value,
        "address": register.address,
        "word_count": register.word_count,
        "data_type": register.data_type.value,
        "writable": register.writable,
        "scale": register.scale,
        "unit": register.unit,
        "word_order": register.word_order.value,
        "byte_order": register.byte_order.value,
        "minimum": register.minimum,
        "maximum": register.maximum,
        "description": register.description,
        "metadata": register.metadata,
    }


def _register_from_dict(payload: dict[str, Any]) -> ModbusRegister:
    return ModbusRegister(
        name=str(payload["name"]),
        kind=RegisterKind(payload["kind"]),
        address=int(payload["address"]),
        word_count=int(payload["word_count"]),
        data_type=ModbusDataType(payload["data_type"]),
        writable=bool(payload.get("writable", False)),
        scale=float(payload.get("scale", 1.0)),
        unit=payload.get("unit"),
        word_order=WordOrder(payload.get("word_order", WordOrder.BIG.value)),
        byte_order=ByteOrder(payload.get("byte_order", ByteOrder.BIG.value)),
        minimum=payload.get("minimum"),
        maximum=payload.get("maximum"),
        description=payload.get("description"),
        metadata=dict(payload.get("metadata", {})),
    )

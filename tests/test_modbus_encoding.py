from __future__ import annotations

import pytest

from coreflow.protocols.modbus import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    RegisterKind,
    WordOrder,
    decode_registers,
    encode_registers,
)


def _register(
    data_type: ModbusDataType,
    word_count: int,
    scale: float = 1.0,
    word_order: WordOrder = WordOrder.BIG,
    byte_order: ByteOrder = ByteOrder.BIG,
) -> ModbusRegister:
    return ModbusRegister(
        name="value",
        kind=RegisterKind.HOLDING,
        address=0,
        word_count=word_count,
        data_type=data_type,
        scale=scale,
        word_order=word_order,
        byte_order=byte_order,
    )


def test_decode_scaled_int16_register() -> None:
    register = _register(ModbusDataType.INT16, word_count=1, scale=0.1)

    assert decode_registers(register, [0xFF9C]) == -10.0


def test_encode_scaled_uint16_register() -> None:
    register = _register(ModbusDataType.UINT16, word_count=1, scale=0.01)

    assert encode_registers(register, 12.34) == [1234]


def test_16_bit_registers_ignore_byte_order() -> None:
    uint_register = _register(
        ModbusDataType.UINT16,
        word_count=1,
        byte_order=ByteOrder.LITTLE,
    )
    int_register = _register(
        ModbusDataType.INT16,
        word_count=1,
        byte_order=ByteOrder.LITTLE,
    )

    assert decode_registers(uint_register, [0x0001]) == 1
    assert encode_registers(uint_register, 1) == [0x0001]
    assert decode_registers(int_register, [0xFFFE]) == -2
    assert encode_registers(int_register, -2) == [0xFFFE]


def test_decode_and_encode_little_word_order_int32() -> None:
    register = _register(
        ModbusDataType.INT32,
        word_count=2,
        word_order=WordOrder.LITTLE,
    )

    assert decode_registers(register, [0x5678, 0x1234]) == 0x12345678
    assert encode_registers(register, 0x12345678) == [0x5678, 0x1234]


def test_float32_round_trip_with_byte_order() -> None:
    register = _register(
        ModbusDataType.FLOAT32,
        word_count=2,
        byte_order=ByteOrder.LITTLE,
    )

    encoded = encode_registers(register, 12.5)

    assert decode_registers(register, encoded) == pytest.approx(12.5)


def test_decode_rejects_wrong_word_count() -> None:
    register = _register(ModbusDataType.UINT32, word_count=2)

    with pytest.raises(ValueError):
        decode_registers(register, [1])

"""Modbus register encoding and decoding helpers."""

from __future__ import annotations

import struct
from typing import Any

from coreflow.protocols.modbus.models import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    WordOrder,
)


def decode_registers(register: ModbusRegister, words: list[int]) -> Any:
    """Decode raw Modbus words into a scaled logical value."""

    if len(words) != register.word_count:
        raise ValueError(
            f"Register {register.name} expected {register.word_count} values, "
            f"got {len(words)}."
        )
    ordered_words = _apply_word_order(words, register.word_order)

    if register.data_type is ModbusDataType.BOOL:
        return bool(ordered_words[0])
    if register.data_type is ModbusDataType.UINT16:
        raw = _apply_byte_order_word(ordered_words[0], register.byte_order)
    elif register.data_type is ModbusDataType.INT16:
        raw = _to_signed(
            _apply_byte_order_word(ordered_words[0], register.byte_order),
            16,
        )
    elif register.data_type is ModbusDataType.UINT32:
        raw = _combine_words(_apply_byte_order_words(ordered_words, register.byte_order))
    elif register.data_type is ModbusDataType.INT32:
        raw = _to_signed(
            _combine_words(_apply_byte_order_words(ordered_words, register.byte_order)),
            32,
        )
    elif register.data_type is ModbusDataType.FLOAT32:
        raw = _decode_float32(ordered_words, register.byte_order)
    else:
        raise ValueError(f"Unsupported data type: {register.data_type}")

    if isinstance(raw, float):
        return raw * register.scale
    if register.scale == 1.0:
        return raw
    return raw * register.scale


def encode_registers(register: ModbusRegister, value: Any) -> list[int]:
    """Encode a logical value into raw Modbus words."""

    if register.data_type is ModbusDataType.BOOL:
        words = [1 if bool(value) else 0]
    else:
        unscaled = float(value) / register.scale if register.scale else float(value)
        if register.data_type is ModbusDataType.UINT16:
            words = [_check_unsigned(int(round(unscaled)), 16)]
        elif register.data_type is ModbusDataType.INT16:
            words = [_from_signed(int(round(unscaled)), 16)]
        elif register.data_type is ModbusDataType.UINT32:
            words = _split_words(_check_unsigned(int(round(unscaled)), 32))
        elif register.data_type is ModbusDataType.INT32:
            words = _split_words(_from_signed(int(round(unscaled)), 32))
        elif register.data_type is ModbusDataType.FLOAT32:
            words = _encode_float32(float(unscaled), register.byte_order)
        else:
            raise ValueError(f"Unsupported data type: {register.data_type}")
        if register.data_type is not ModbusDataType.FLOAT32:
            words = _apply_byte_order_words(words, register.byte_order)
    return _apply_word_order(words, register.word_order)


def _apply_word_order(words: list[int], word_order: WordOrder) -> list[int]:
    if word_order is WordOrder.LITTLE:
        return list(reversed(words))
    return list(words)


def _apply_byte_order_words(words: list[int], byte_order: ByteOrder) -> list[int]:
    return [_apply_byte_order_word(word, byte_order) for word in words]


def _apply_byte_order_word(word: int, byte_order: ByteOrder) -> int:
    if byte_order is ByteOrder.LITTLE:
        return ((word & 0xFF) << 8) | ((word >> 8) & 0xFF)
    return word


def _combine_words(words: list[int]) -> int:
    if len(words) != 2:
        raise ValueError("32-bit values require exactly two registers.")
    return (words[0] << 16) | words[1]


def _split_words(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def _to_signed(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return value - (1 << bits) if value & sign_bit else value


def _from_signed(value: int, bits: int) -> int:
    minimum = -(1 << (bits - 1))
    maximum = (1 << (bits - 1)) - 1
    if value < minimum or value > maximum:
        raise ValueError(f"Signed {bits}-bit value out of range: {value}")
    return value & ((1 << bits) - 1)


def _check_unsigned(value: int, bits: int) -> int:
    if value < 0 or value > (1 << bits) - 1:
        raise ValueError(f"Unsigned {bits}-bit value out of range: {value}")
    return value


def _decode_float32(words: list[int], byte_order: ByteOrder) -> float:
    data = _words_to_bytes(words, byte_order)
    return struct.unpack(">f", data)[0]


def _encode_float32(value: float, byte_order: ByteOrder) -> list[int]:
    data = struct.pack(">f", value)
    return _bytes_to_words(data, byte_order)


def _words_to_bytes(words: list[int], byte_order: ByteOrder) -> bytes:
    output = bytearray()
    for word in words:
        data = word.to_bytes(2, byteorder="big")
        if byte_order is ByteOrder.LITTLE:
            data = bytes(reversed(data))
        output.extend(data)
    return bytes(output)


def _bytes_to_words(data: bytes, byte_order: ByteOrder) -> list[int]:
    words: list[int] = []
    for index in range(0, len(data), 2):
        chunk = data[index : index + 2]
        if byte_order is ByteOrder.LITTLE:
            chunk = bytes(reversed(chunk))
        words.append(int.from_bytes(chunk, byteorder="big"))
    return words

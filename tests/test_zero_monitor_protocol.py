from __future__ import annotations

import json
from pathlib import Path

import pytest

from coreflow.analysis.zero_monitor import (
    ZERO_MONITOR_CRITERIA,
    ZeroMonitorAnalysisConfig,
    ZeroMonitorState,
    ZeroMonitorThreshold,
)
from coreflow.app.modbus_zero_monitor import (
    ZERO_MONITOR_VARIABLE_NAMES,
    ZeroMonitorProcessor,
    decode_zero_monitor_words,
    validate_zero_monitor_register_map,
)
from coreflow.hardware.register_map import register_map_from_json
from coreflow.protocols.modbus import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    WordOrder,
)


_SPECS = (
    ("zero_snapshot_sequence_begin", 0, 1, ModbusDataType.UINT16),
    ("zero_monitor_status", 1, 1, ModbusDataType.UINT16),
    ("zero_monitor_tick_ms", 2, 2, ModbusDataType.UINT32),
    ("zero_base_mean_100ms", 4, 2, ModbusDataType.FLOAT32),
    ("zero_base_std_100ms", 6, 2, ModbusDataType.FLOAT32),
    ("zero_live_600ms", 8, 2, ModbusDataType.FLOAT32),
    ("zero_trim_std_600ms", 10, 2, ModbusDataType.FLOAT32),
    ("zero_trim_range_600ms", 12, 2, ModbusDataType.FLOAT32),
    ("zero_raw_p2p_600ms", 14, 2, ModbusDataType.FLOAT32),
    ("zero_window_valid_count", 16, 1, ModbusDataType.UINT16),
    ("zero_snapshot_sequence_end", 17, 1, ModbusDataType.UINT16),
)


def zero_map(
    *,
    order: str = "ABCD",
    start: int = 95,
    extra: tuple[ModbusRegister, ...] = (),
) -> ModbusRegisterMap:
    byte_order, word_order = {
        "ABCD": (ByteOrder.BIG, WordOrder.BIG),
        "BADC": (ByteOrder.LITTLE, WordOrder.BIG),
        "CDAB": (ByteOrder.BIG, WordOrder.LITTLE),
        "DCBA": (ByteOrder.LITTLE, WordOrder.LITTLE),
    }[order]
    registers = tuple(
        ModbusRegister(
            name=name,
            kind=RegisterKind.INPUT,
            address=start + offset,
            word_count=words,
            data_type=data_type,
            unit=(
                "us"
                if data_type is ModbusDataType.FLOAT32
                else "ms" if name == "zero_monitor_tick_ms" else None
            ),
            byte_order=byte_order,
            word_order=word_order,
        )
        for name, offset, words, data_type in _SPECS
    )
    return ModbusRegisterMap("zero-test", "1", (*registers, *extra))


def _test_config() -> ZeroMonitorAnalysisConfig:
    return ZeroMonitorAnalysisConfig(
        long_window_s=12.0,
        minimum_stable_duration_s=0.0,
        thresholds={
            name: ZeroMonitorThreshold(
                limit=100.0,
                source="test",
                unit="us",
                test_only=True,
            )
            for name in ZERO_MONITOR_CRITERIA
        },
    )


def _words(sequence: int, tick_ms: int, *, status: int = 7, end: int | None = None):
    words = [
        sequence & 0xFFFF,
        status,
        (tick_ms >> 16) & 0xFFFF,
        tick_ms & 0xFFFF,
        0x3F80,
        0,
        0x3DCC,
        0xCCCD,
        0x3F80,
        0,
        0x3DCC,
        0xCCCD,
        0x3E4C,
        0xCCCD,
        0x3E99,
        0x999A,
        60,
        sequence if end is None else end,
    ]
    return words


def test_mapping_validator_accepts_exact_contiguous_read_only_block() -> None:
    result = validate_zero_monitor_register_map(zero_map())

    assert result.valid
    assert result.block_start == 95
    assert result.register_kind is RegisterKind.INPUT
    assert result.variable_names == ZERO_MONITOR_VARIABLE_NAMES


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("missing", "MISSING_VARIABLE"),
        ("offset", "WRONG_RELATIVE_OFFSET"),
        ("writable", "WRITABLE_SNAPSHOT_VARIABLE"),
        ("kind", "MIXED_REGISTER_KIND"),
        ("type", "WRONG_DATA_TYPE"),
        ("words", "WRONG_WORD_COUNT"),
        ("scale", "WRONG_SCALE"),
        ("unit", "WRONG_UNIT"),
        ("tick_unit", "WRONG_UNIT"),
        ("overlap", "UNEXPECTED_BLOCK_MAPPING"),
    ],
)
def test_mapping_validator_rejects_structural_errors(mutation: str, code: str) -> None:
    registers = list(zero_map().registers)
    if mutation == "missing":
        registers.pop(3)
    elif mutation == "overlap":
        registers.append(
            ModbusRegister(
                "unrelated",
                RegisterKind.INPUT,
                100,
                1,
                ModbusDataType.UINT16,
            )
        )
    else:
        target_index = 2 if mutation == "tick_unit" else 3
        target = registers[target_index]
        changes = {
            "offset": {"address": target.address + 1},
            "writable": {"writable": True},
            "kind": {"kind": RegisterKind.HOLDING},
            "type": {"data_type": ModbusDataType.UINT32},
            "words": {"word_count": 1},
            "scale": {"scale": 0.001},
            "unit": {"unit": "ms"},
            "tick_unit": {"unit": "us"},
        }[mutation]
        values = {
            field: getattr(target, field)
            for field in target.__dataclass_fields__
        }
        values.update(changes)
        registers[target_index] = ModbusRegister(**values)
    result = validate_zero_monitor_register_map(
        ModbusRegisterMap("invalid", "1", tuple(registers))
    )
    assert code in {error.code for error in result.errors}


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"scale": 0.001}, "WRONG_SCALE"),
        ({"unit": "ms"}, "WRONG_UNIT"),
        ({"data_type": ModbusDataType.UINT32}, "WRONG_DATA_TYPE"),
        ({"word_count": 1}, "WRONG_WORD_COUNT"),
    ],
)
def test_mapping_validator_rejects_incompatible_zero_offset(
    changes: dict[str, object],
    code: str,
) -> None:
    values = {
        "name": "zero_offset",
        "kind": RegisterKind.HOLDING,
        "address": 20,
        "word_count": 2,
        "data_type": ModbusDataType.FLOAT32,
        "writable": True,
        "scale": 1.0,
        "unit": "us",
        "byte_order": ByteOrder.BIG,
        "word_order": WordOrder.BIG,
    }
    values.update(changes)

    result = validate_zero_monitor_register_map(
        zero_map(extra=(ModbusRegister(**values),))
    )

    assert code in {error.code for error in result.errors}


def test_versioned_krohne_profile_matches_current_dsp_contract() -> None:
    path = (
        Path(__file__).parents[1]
        / "config"
        / "register_maps"
        / "krohne_prj_main.json"
    )
    register_map = register_map_from_json(path.read_text(encoding="utf-8"))

    result = validate_zero_monitor_register_map(register_map)

    assert result.valid
    assert result.block_start == 95
    assert result.register_kind is RegisterKind.INPUT
    assert (
        register_map.by_name("zero_snapshot_sequence_begin").metadata["source_head"]
        == "f0a1b39ba1f4394253ee0adf7d0aee47c123ff9a"
    )
    assert register_map.by_name("zero_offset").address == 20
    assert register_map.by_name("zero_offset").kind is RegisterKind.HOLDING
    byte_order = register_map.by_name("modbus_byte_order")
    assert byte_order.address == 52
    assert byte_order.kind is RegisterKind.HOLDING
    assert byte_order.writable is True
    assert register_map.by_name("zero_calibration_start").address == 16


def test_firmware_golden_vectors_decode_for_all_four_orders() -> None:
    path = Path(__file__).parent / "fixtures" / "modbus_zero_monitor" / "firmware_snapshot_vectors.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    for vector in payload["vectors"]:
        snapshot = decode_zero_monitor_words(
            zero_map(order=vector["order"]),
            vector["raw_words"],
        )
        for name, expected in vector["expected"].items():
            assert getattr(snapshot, name) == pytest.approx(expected), vector["id"]


def test_processor_handles_wrap_duplicate_gap_and_recovery_segment() -> None:
    processor = ZeroMonitorProcessor(
        _test_config(),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    first = processor.process(decode_zero_monitor_words(zero_map(), _words(65535, 0xFFFFFF9B)))
    wrapped = processor.process(decode_zero_monitor_words(zero_map(), _words(0, 0xFFFFFFFF)))
    duplicate = processor.process(decode_zero_monitor_words(zero_map(), _words(0, 0xFFFFFFFF)))
    gap = processor.process(decode_zero_monitor_words(zero_map(), _words(3, 299)))
    recovered = processor.process(decode_zero_monitor_words(zero_map(), _words(4, 399)))

    assert first.accept_for_statistics
    assert wrapped.accept_for_statistics
    assert wrapped.device_tick_ms_unwrapped == 0xFFFFFFFF
    assert "DUPLICATE_SNAPSHOT" in duplicate.advisory_codes
    assert not duplicate.accept_for_statistics
    assert gap.analysis.state is ZeroMonitorState.DATA_GAP
    assert "SEQUENCE_GAP" in gap.reason_codes
    assert recovered.continuous_segment == first.continuous_segment + 1
    assert recovered.analysis.state is ZeroMonitorState.NOT_READY


def test_processor_distinguishes_dsp_warmup_from_invalid_full_window() -> None:
    processor = ZeroMonitorProcessor(
        _test_config(),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    warmup_words = _words(1, 100, status=1)
    warmup_words[16] = 10
    warmup = processor.process(decode_zero_monitor_words(zero_map(), warmup_words))
    invalid_words = _words(2, 200, status=3)
    invalid_words[16] = 59
    invalid = processor.process(decode_zero_monitor_words(zero_map(), invalid_words))

    assert warmup.analysis.state is ZeroMonitorState.NOT_READY
    assert warmup.reason_codes == ("DSP_WINDOW_NOT_READY",)
    assert not warmup.communication_gap
    assert invalid.analysis.state is ZeroMonitorState.DATA_GAP
    assert invalid.reason_codes == ("DATA_INVALID",)


def test_processor_rejects_changed_duplicate_payload_and_tick_discontinuity() -> None:
    processor = ZeroMonitorProcessor(
        _test_config(),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    processor.process(decode_zero_monitor_words(zero_map(), _words(10, 1000)))
    changed_words = _words(10, 1000)
    changed_words[9] = 1
    changed = processor.process(decode_zero_monitor_words(zero_map(), changed_words))
    recovered = processor.process(decode_zero_monitor_words(zero_map(), _words(11, 1100)))
    discontinuous = processor.process(decode_zero_monitor_words(zero_map(), _words(12, 1300)))

    assert changed.analysis.state is ZeroMonitorState.DATA_GAP
    assert "DUPLICATE_PAYLOAD_CHANGED" in changed.reason_codes
    assert recovered.analysis.state is ZeroMonitorState.NOT_READY
    assert discontinuous.analysis.state is ZeroMonitorState.DATA_GAP
    assert "DEVICE_TIME_DISCONTINUITY" in discontinuous.reason_codes


def test_processor_isolates_zero_cal_and_reserved_status_bits() -> None:
    processor = ZeroMonitorProcessor(
        _test_config(),
        zero_flow_confirmed=True,
        byte_order_verified=True,
    )
    processor.process(decode_zero_monitor_words(zero_map(), _words(1, 100)))
    zero_cal = processor.process(decode_zero_monitor_words(zero_map(), _words(2, 200, status=15)))
    after_cal = processor.process(decode_zero_monitor_words(zero_map(), _words(3, 300)))
    reserved = processor.process(decode_zero_monitor_words(zero_map(), _words(4, 400, status=0x27)))

    assert zero_cal.analysis.state is ZeroMonitorState.EVALUATING
    assert "ZERO_CAL_ACTIVE" in zero_cal.reason_codes
    assert after_cal.continuous_segment == 2
    assert after_cal.analysis.state is ZeroMonitorState.NOT_READY
    assert reserved.analysis.state is ZeroMonitorState.EVALUATING
    assert "UNSUPPORTED_STATUS_BITS" in reserved.reason_codes

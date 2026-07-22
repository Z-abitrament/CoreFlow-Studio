"""Read-only Modbus zero-snapshot validation and continuity processing."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path, PurePath
from time import monotonic, sleep
from typing import Any, Protocol
from uuid import uuid4

from coreflow.analysis.zero_monitor import (
    IndependentCandidateSelector,
    ZeroMonitorAnalysisConfig,
    ZeroMonitorAnalyzer,
    ZeroMonitorCandidate,
    ZeroMonitorEvaluation,
    ZeroMonitorMetrics,
    ZeroMonitorState,
)
from coreflow.devices import ConfigurationParameter
from coreflow.protocols.modbus.encoding import decode_registers
from coreflow.protocols.modbus.models import (
    ByteOrder,
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    RegisterKind,
    WordOrder,
)
from coreflow.storage import ArtifactStore, ArtifactType, StorageRepository
from coreflow.storage.models import (
    AnalysisResultRecord,
    Artifact,
    ModbusOperationAttemptRecord,
)
from coreflow.workflows.models import (
    RunSession,
    RunStatus,
    RunType,
    WorkflowStep,
    WorkflowStepStatus,
    WorkflowStepType,
)
from coreflow import __version__


ZERO_MONITOR_POLL_INTERVAL_MS = 100
ZERO_MONITOR_WINDOW_VALID_COUNT = 60
ZERO_MONITOR_WORKFLOW_VERSION = "1"


@dataclass(frozen=True, slots=True)
class _SnapshotFieldSpec:
    name: str
    offset: int
    word_count: int
    data_type: ModbusDataType


ZERO_MONITOR_FIELD_SPECS = (
    _SnapshotFieldSpec("zero_snapshot_sequence_begin", 0, 1, ModbusDataType.UINT16),
    _SnapshotFieldSpec("zero_monitor_status", 1, 1, ModbusDataType.UINT16),
    _SnapshotFieldSpec("zero_monitor_tick_ms", 2, 2, ModbusDataType.UINT32),
    _SnapshotFieldSpec("zero_base_mean_100ms", 4, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_base_std_100ms", 6, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_live_600ms", 8, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_trim_std_600ms", 10, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_trim_range_600ms", 12, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_raw_p2p_600ms", 14, 2, ModbusDataType.FLOAT32),
    _SnapshotFieldSpec("zero_window_valid_count", 16, 1, ModbusDataType.UINT16),
    _SnapshotFieldSpec("zero_snapshot_sequence_end", 17, 1, ModbusDataType.UINT16),
)
ZERO_MONITOR_VARIABLE_NAMES = tuple(item.name for item in ZERO_MONITOR_FIELD_SPECS)


class ModbusConfigurationBlockReader(Protocol):
    """Narrow capability required by the high-rate coherent block consumer."""

    def read_configuration_parameters(
        self,
        parameter_names: tuple[str, ...],
        *,
        merge_adjacent: bool = False,
        transport_retry_count: int | None = None,
    ) -> tuple[ConfigurationParameter, ...]: ...


@dataclass(frozen=True, slots=True)
class ZeroMonitorMapError:
    code: str
    variable_name: str | None
    message: str


@dataclass(frozen=True, slots=True)
class ZeroMonitorMapValidationResult:
    errors: tuple[ZeroMonitorMapError, ...]
    block_start: int | None = None
    register_kind: RegisterKind | None = None
    variable_names: tuple[str, ...] = ZERO_MONITOR_VARIABLE_NAMES

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True, slots=True)
class ZeroMonitorSnapshot:
    """One decoded 18-word firmware publication with raw evidence."""

    sequence: int
    sequence_end: int
    status: int
    tick_ms: int
    base_mean_100ms: float
    base_std_100ms: float
    live_zero_600ms: float
    trim_std_600ms: float
    trim_range_600ms: float
    raw_p2p_600ms: float
    valid_count: int
    raw_words: tuple[int, ...]

    @property
    def snapshot_consistent(self) -> bool:
        return self.sequence == self.sequence_end

    @property
    def base_ready(self) -> bool:
        return bool(self.status & 0x01)

    @property
    def live_ready(self) -> bool:
        return bool(self.status & 0x02)

    @property
    def data_valid(self) -> bool:
        return bool(self.status & 0x04)

    @property
    def zero_cal_running(self) -> bool:
        return bool(self.status & 0x08)

    @property
    def internal_error(self) -> bool:
        return bool(self.status & 0x10)

    @property
    def reserved_status_bits(self) -> int:
        return self.status & 0xFFE0


@dataclass(frozen=True, slots=True)
class ZeroMonitorProcessedSample:
    snapshot: ZeroMonitorSnapshot
    analysis: ZeroMonitorEvaluation
    continuous_segment: int
    device_tick_ms_unwrapped: int | None
    sequence_delta: int | None
    accept_for_statistics: bool
    communication_gap: bool
    segment_break_reason: str | None = None
    reason_codes: tuple[str, ...] = ()
    advisory_codes: tuple[str, ...] = ()


def validate_zero_monitor_register_map(
    register_map: ModbusRegisterMap,
) -> ZeroMonitorMapValidationResult:
    """Validate the fixed relative layout before any device I/O is attempted."""

    errors: list[ZeroMonitorMapError] = []
    by_name: dict[str, list[ModbusRegister]] = {}
    for register in register_map.registers:
        by_name.setdefault(register.name, []).append(register)
    for spec in ZERO_MONITOR_FIELD_SPECS:
        matches = by_name.get(spec.name, [])
        if not matches:
            errors.append(
                ZeroMonitorMapError(
                    "MISSING_VARIABLE",
                    spec.name,
                    f"Required snapshot variable is missing: {spec.name}",
                )
            )
        elif len(matches) > 1:
            errors.append(
                ZeroMonitorMapError(
                    "DUPLICATE_VARIABLE",
                    spec.name,
                    f"Snapshot variable occurs more than once: {spec.name}",
                )
            )
    begin_matches = by_name.get("zero_snapshot_sequence_begin", [])
    block_start = begin_matches[0].address if len(begin_matches) == 1 else None
    selected = [
        by_name[spec.name][0]
        for spec in ZERO_MONITOR_FIELD_SPECS
        if len(by_name.get(spec.name, [])) == 1
    ]
    for spec in ZERO_MONITOR_FIELD_SPECS:
        matches = by_name.get(spec.name, [])
        if len(matches) != 1:
            continue
        register = matches[0]
        if block_start is not None and register.address != block_start + spec.offset:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_RELATIVE_OFFSET",
                    spec.name,
                    f"{spec.name} must be at block offset {spec.offset}.",
                )
            )
        if register.word_count != spec.word_count:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_WORD_COUNT",
                    spec.name,
                    f"{spec.name} must use {spec.word_count} register word(s).",
                )
            )
        if register.data_type is not spec.data_type:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_DATA_TYPE",
                    spec.name,
                    f"{spec.name} must use {spec.data_type.value}.",
                )
            )
        if register.scale != 1.0:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_SCALE",
                    spec.name,
                    f"{spec.name} must use scale 1.0.",
                )
            )
        expected_unit = (
            "us"
            if spec.data_type is ModbusDataType.FLOAT32
            else "ms" if spec.name == "zero_monitor_tick_ms" else None
        )
        if expected_unit is not None and register.unit != expected_unit:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_UNIT",
                    spec.name,
                    f"{spec.name} must use unit {expected_unit}.",
                )
            )
        if register.writable:
            errors.append(
                ZeroMonitorMapError(
                    "WRITABLE_SNAPSHOT_VARIABLE",
                    spec.name,
                    f"Snapshot variable must be read-only: {spec.name}",
                )
            )
    kinds = {register.kind for register in selected}
    register_kind = next(iter(kinds)) if len(kinds) == 1 else None
    if len(kinds) > 1 or (kinds and not kinds <= {RegisterKind.INPUT, RegisterKind.HOLDING}):
        errors.append(
            ZeroMonitorMapError(
                "MIXED_REGISTER_KIND",
                None,
                "All snapshot fields must use one input or holding register kind.",
            )
        )
    if block_start is not None:
        block_end = block_start + 18
        selected_ids = {id(register) for register in selected}
        for register in register_map.registers:
            overlaps = register.address < block_end and (
                register.address + register.word_count > block_start
            )
            if overlaps and id(register) not in selected_ids:
                errors.append(
                    ZeroMonitorMapError(
                        "UNEXPECTED_BLOCK_MAPPING",
                        register.name,
                        f"Unrelated mapping overlaps the zero snapshot: {register.name}",
                    )
                )
        occupied: dict[int, str] = {}
        for register in selected:
            for address in range(register.address, register.address + register.word_count):
                previous = occupied.get(address)
                if previous is not None:
                    errors.append(
                        ZeroMonitorMapError(
                            "WORD_OVERLAP",
                            register.name,
                            f"{register.name} overlaps {previous} at {address}.",
                        )
                    )
                occupied[address] = register.name
        missing_words = [address for address in range(block_start, block_end) if address not in occupied]
        if missing_words:
            errors.append(
                ZeroMonitorMapError(
                    "BLOCK_GAP",
                    None,
                    "Zero snapshot block has unmapped register words.",
                )
            )
    multiword = [register for register in selected if register.word_count == 2]
    orders = {(register.byte_order, register.word_order) for register in multiword}
    if len(orders) > 1:
        errors.append(
            ZeroMonitorMapError(
                "BYTE_ORDER_MISMATCH",
                None,
                "All 32-bit zero snapshot fields must use the same byte and word order.",
            )
        )
    try:
        zero_offset = register_map.by_name("zero_offset")
    except KeyError:
        zero_offset = None
    if zero_offset is not None and block_start is not None:
        if zero_offset.address < block_start + 18 and (
            zero_offset.address + zero_offset.word_count > block_start
        ):
            errors.append(
                ZeroMonitorMapError(
                    "UNEXPECTED_BLOCK_MAPPING",
                    "zero_offset",
                    "zero_offset must be outside the coherent snapshot block.",
                )
            )
        if zero_offset.kind not in {RegisterKind.INPUT, RegisterKind.HOLDING}:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_REGISTER_KIND",
                    "zero_offset",
                    "zero_offset must be an input or holding register.",
                )
            )
        if zero_offset.data_type is not ModbusDataType.FLOAT32:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_DATA_TYPE",
                    "zero_offset",
                    "zero_offset must use float32.",
                )
            )
        if zero_offset.word_count != 2:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_WORD_COUNT",
                    "zero_offset",
                    "zero_offset must use 2 register words.",
                )
            )
        if zero_offset.scale != 1.0:
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_SCALE",
                    "zero_offset",
                    "zero_offset must use scale 1.0.",
                )
            )
        if zero_offset.unit != "us":
            errors.append(
                ZeroMonitorMapError(
                    "WRONG_UNIT",
                    "zero_offset",
                    "zero_offset must use unit us.",
                )
            )
        if multiword and (
            zero_offset.byte_order,
            zero_offset.word_order,
        ) != (
            multiword[0].byte_order,
            multiword[0].word_order,
        ):
            errors.append(
                ZeroMonitorMapError(
                    "BYTE_ORDER_MISMATCH",
                    "zero_offset",
                    "zero_offset must use the same 32-bit order as the snapshot.",
                )
            )
    return ZeroMonitorMapValidationResult(
        errors=tuple(_unique_map_errors(errors)),
        block_start=block_start,
        register_kind=register_kind,
    )


def decode_zero_monitor_words(
    register_map: ModbusRegisterMap,
    words: list[int] | tuple[int, ...],
) -> ZeroMonitorSnapshot:
    """Decode one literal 18-word block according to the validated profile order."""

    if len(words) != 18:
        raise ValueError(f"Zero snapshot requires exactly 18 words, received {len(words)}.")
    validation = validate_zero_monitor_register_map(register_map)
    if not validation.valid:
        raise ValueError("Invalid zero-monitor register map: " + ", ".join(error.code for error in validation.errors))
    decoded: dict[str, int | float] = {}
    for spec in ZERO_MONITOR_FIELD_SPECS:
        register = register_map.by_name(spec.name)
        decoded[spec.name] = decode_registers(
            register,
            list(words[spec.offset : spec.offset + spec.word_count]),
        )
    return ZeroMonitorSnapshot(
        sequence=int(decoded["zero_snapshot_sequence_begin"]),
        sequence_end=int(decoded["zero_snapshot_sequence_end"]),
        status=int(decoded["zero_monitor_status"]),
        tick_ms=int(decoded["zero_monitor_tick_ms"]),
        base_mean_100ms=float(decoded["zero_base_mean_100ms"]),
        base_std_100ms=float(decoded["zero_base_std_100ms"]),
        live_zero_600ms=float(decoded["zero_live_600ms"]),
        trim_std_600ms=float(decoded["zero_trim_std_600ms"]),
        trim_range_600ms=float(decoded["zero_trim_range_600ms"]),
        raw_p2p_600ms=float(decoded["zero_raw_p2p_600ms"]),
        valid_count=int(decoded["zero_window_valid_count"]),
        raw_words=tuple(int(word) & 0xFFFF for word in words),
    )


def snapshot_from_parameters(
    register_map: ModbusRegisterMap,
    parameters: tuple[ConfigurationParameter, ...],
) -> ZeroMonitorSnapshot:
    """Reassemble the original block from merged-read parameter metadata."""

    by_name = {parameter.name: parameter for parameter in parameters}
    words: list[int] = []
    for name in ZERO_MONITOR_VARIABLE_NAMES:
        parameter = by_name.get(name)
        if parameter is None:
            raise ValueError(f"Merged zero snapshot omitted {name}.")
        raw = parameter.metadata.get("raw_words")
        if not isinstance(raw, list) or not all(isinstance(word, int) for word in raw):
            raise ValueError(f"Merged zero snapshot omitted raw-word evidence for {name}.")
        words.extend(raw)
    return decode_zero_monitor_words(register_map, words)


class ZeroMonitorProcessor:
    """Apply status, modular continuity, segmentation and pure stability analysis."""

    def __init__(
        self,
        config: ZeroMonitorAnalysisConfig,
        *,
        zero_flow_confirmed: bool,
        byte_order_verified: bool,
        official_zero_offset: float | None = None,
    ) -> None:
        self._analyzer = ZeroMonitorAnalyzer(config)
        self._selector = IndependentCandidateSelector(6)
        self._zero_flow_confirmed = zero_flow_confirmed
        self._byte_order_verified = byte_order_verified
        self._official_zero_offset = official_zero_offset
        self._previous: ZeroMonitorSnapshot | None = None
        self._previous_unwrapped_tick: int | None = None
        self._continuous_segment = 0
        self._start_new_segment = True
        self._last_analysis = ZeroMonitorEvaluation(
            state=ZeroMonitorState.NOT_READY,
            reason_codes=("NO_ACCEPTED_SNAPSHOT",),
        )

    def process(
        self,
        snapshot: ZeroMonitorSnapshot,
        *,
        poll_overrun: bool = False,
    ) -> ZeroMonitorProcessedSample:
        if not snapshot.snapshot_consistent:
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "TORN_SNAPSHOT")
        finite_values = (
            snapshot.base_mean_100ms,
            snapshot.base_std_100ms,
            snapshot.live_zero_600ms,
            snapshot.trim_std_600ms,
            snapshot.trim_range_600ms,
            snapshot.raw_p2p_600ms,
        )
        if not all(isfinite(value) for value in finite_values):
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "NONFINITE_VALUE")
        if snapshot.internal_error:
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "INTERNAL_ERROR")
        if snapshot.live_ready and not snapshot.base_ready:
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "READY_STATE_INCONSISTENT")
        if not snapshot.base_ready or not snapshot.live_ready:
            if self._previous is None:
                return self._event(snapshot, ZeroMonitorState.NOT_READY, "DSP_WINDOW_NOT_READY")
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "READINESS_DROPPED")
        if not snapshot.data_valid:
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "DATA_INVALID")
        if snapshot.valid_count != ZERO_MONITOR_WINDOW_VALID_COUNT:
            return self._break(snapshot, ZeroMonitorState.DATA_GAP, "INVALID_WINDOW_COUNT")
        if snapshot.zero_cal_running:
            return self._break(snapshot, ZeroMonitorState.EVALUATING, "ZERO_CAL_ACTIVE")
        if snapshot.reserved_status_bits:
            return self._break(
                snapshot,
                ZeroMonitorState.EVALUATING,
                "UNSUPPORTED_STATUS_BITS",
            )

        sequence_delta: int | None = None
        unwrapped_tick = snapshot.tick_ms
        advisories: list[str] = []
        if self._previous is not None:
            sequence_delta = (snapshot.sequence - self._previous.sequence) & 0xFFFF
            tick_delta = (snapshot.tick_ms - self._previous.tick_ms) & 0xFFFFFFFF
            if sequence_delta == 0:
                if snapshot.tick_ms == self._previous.tick_ms and snapshot.raw_words == self._previous.raw_words:
                    advisories.append("DUPLICATE_SNAPSHOT")
                    if poll_overrun:
                        advisories.append("POLL_OVERRUN")
                    return ZeroMonitorProcessedSample(
                        snapshot=snapshot,
                        analysis=self._last_analysis,
                        continuous_segment=self._continuous_segment,
                        device_tick_ms_unwrapped=self._previous_unwrapped_tick,
                        sequence_delta=0,
                        accept_for_statistics=False,
                        communication_gap=False,
                        advisory_codes=tuple(
                            (*self._last_analysis.advisory_codes, *advisories)
                        ),
                    )
                return self._break(
                    snapshot,
                    ZeroMonitorState.DATA_GAP,
                    "DUPLICATE_PAYLOAD_CHANGED",
                )
            if sequence_delta >= 0x8000:
                return self._break(snapshot, ZeroMonitorState.DATA_GAP, "DEVICE_RESTART")
            if tick_delta == 0 or tick_delta >= 0x80000000:
                return self._break(snapshot, ZeroMonitorState.DATA_GAP, "DEVICE_RESTART")
            if tick_delta != sequence_delta * ZERO_MONITOR_POLL_INTERVAL_MS:
                return self._break(
                    snapshot,
                    ZeroMonitorState.DATA_GAP,
                    "DEVICE_TIME_DISCONTINUITY",
                )
            if sequence_delta > 1:
                return self._break(snapshot, ZeroMonitorState.DATA_GAP, "SEQUENCE_GAP")
            unwrapped_tick = int(self._previous_unwrapped_tick or 0) + tick_delta

        if self._start_new_segment:
            self._continuous_segment += 1
            self._start_new_segment = False
            self._selector.reset()
            self._analyzer.reset()
            unwrapped_tick = snapshot.tick_ms
        self._previous = snapshot
        self._previous_unwrapped_tick = unwrapped_tick
        selected = self._selector.accept(snapshot.sequence)
        current_candidate = ZeroMonitorCandidate(
            sequence=snapshot.sequence,
            device_tick_ms=unwrapped_tick,
            continuous_segment=self._continuous_segment,
            live_zero_600ms=snapshot.live_zero_600ms,
            trim_std_600ms=snapshot.trim_std_600ms,
            trim_range_600ms=snapshot.trim_range_600ms,
            raw_p2p_600ms=snapshot.raw_p2p_600ms,
        )
        if selected:
            self._last_analysis = self._analyzer.add_candidate(
                current_candidate,
                zero_flow_confirmed=self._zero_flow_confirmed,
                byte_order_verified=self._byte_order_verified,
                official_zero_offset=self._official_zero_offset,
            )
        else:
            self._last_analysis = self._analyzer.evaluate_snapshot(
                current_candidate,
                zero_flow_confirmed=self._zero_flow_confirmed,
                byte_order_verified=self._byte_order_verified,
                official_zero_offset=self._official_zero_offset,
            )
        if poll_overrun:
            advisories.append("POLL_OVERRUN")
        return ZeroMonitorProcessedSample(
            snapshot=snapshot,
            analysis=self._last_analysis,
            continuous_segment=self._continuous_segment,
            device_tick_ms_unwrapped=unwrapped_tick,
            sequence_delta=sequence_delta,
            accept_for_statistics=True,
            communication_gap=False,
            reason_codes=self._last_analysis.reason_codes,
            advisory_codes=tuple((*self._last_analysis.advisory_codes, *advisories)),
        )

    def transport_gap(self, reason: str) -> ZeroMonitorEvaluation:
        self._reset_continuity()
        self._last_analysis = ZeroMonitorEvaluation(
            state=ZeroMonitorState.DATA_GAP,
            reason_codes=(reason,),
        )
        return self._last_analysis

    def _break(
        self,
        snapshot: ZeroMonitorSnapshot,
        state: ZeroMonitorState,
        reason: str,
    ) -> ZeroMonitorProcessedSample:
        segment = self._continuous_segment
        self._reset_continuity()
        self._last_analysis = ZeroMonitorEvaluation(
            state=state,
            metrics=ZeroMonitorMetrics(),
            reason_codes=(reason,),
        )
        return ZeroMonitorProcessedSample(
            snapshot=snapshot,
            analysis=self._last_analysis,
            continuous_segment=segment,
            device_tick_ms_unwrapped=None,
            sequence_delta=None,
            accept_for_statistics=False,
            communication_gap=state is ZeroMonitorState.DATA_GAP,
            segment_break_reason=reason,
            reason_codes=(reason,),
        )

    def _event(
        self,
        snapshot: ZeroMonitorSnapshot,
        state: ZeroMonitorState,
        reason: str,
    ) -> ZeroMonitorProcessedSample:
        self._last_analysis = ZeroMonitorEvaluation(state=state, reason_codes=(reason,))
        return ZeroMonitorProcessedSample(
            snapshot=snapshot,
            analysis=self._last_analysis,
            continuous_segment=self._continuous_segment,
            device_tick_ms_unwrapped=None,
            sequence_delta=None,
            accept_for_statistics=False,
            communication_gap=False,
            reason_codes=(reason,),
        )

    def _reset_continuity(self) -> None:
        self._previous = None
        self._previous_unwrapped_tick = None
        self._start_new_segment = True
        self._selector.reset()
        self._analyzer.reset()


def byte_word_order_from_device_enum(value: int) -> tuple[ByteOrder, WordOrder]:
    try:
        return {
            0: (ByteOrder.BIG, WordOrder.BIG),
            1: (ByteOrder.LITTLE, WordOrder.BIG),
            2: (ByteOrder.BIG, WordOrder.LITTLE),
            3: (ByteOrder.LITTLE, WordOrder.LITTLE),
        }[int(value)]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unsupported Modbus ByteOrder enum: {value}") from exc


def _unique_map_errors(errors: list[ZeroMonitorMapError]) -> list[ZeroMonitorMapError]:
    unique: list[ZeroMonitorMapError] = []
    seen: set[tuple[str, str | None]] = set()
    for error in errors:
        key = (error.code, error.variable_name)
        if key not in seen:
            seen.add(key)
            unique.append(error)
    return unique


ZERO_MONITOR_CSV_FIELDS = (
    "captured_at",
    "elapsed_s",
    "sample_index",
    "logical_poll_index",
    "scheduled_elapsed_s",
    "schedule_lag_ms",
    "request_started_at",
    "request_start_elapsed_s",
    "request_duration_ms",
    "physical_request_count",
    "torn_snapshot_reread_count",
    "response_status",
    "error_code",
    "error_message",
    "initial_raw_words_hex",
    "reread_raw_words_hex",
    "host_receive_time",
    "device_tick_ms_raw",
    "device_tick_ms_unwrapped",
    "continuous_segment",
    "sequence",
    "sequence_delta",
    "status",
    "reserved_status_bits",
    "base_ready",
    "live_ready",
    "data_valid",
    "zero_cal_running",
    "internal_error",
    "valid_count",
    "base_mean_100ms",
    "base_std_100ms",
    "live_zero_600ms",
    "trim_std_600ms",
    "trim_range_600ms",
    "raw_p2p_600ms",
    "official_zero_offset",
    "zero_drift_from_cal",
    "snapshot_consistent",
    "communication_gap",
    "segment_break_reason",
    "analysis_state",
    "state_reason_codes",
    "advisory_codes",
    "poll_overrun",
    "missed_schedule_slot_count",
    "accept_for_statistics",
)


@dataclass(frozen=True, slots=True)
class ZeroFlowConfirmation:
    confirmed: bool
    operator: str
    confirmed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmed": self.confirmed,
            "operator": self.operator,
            "confirmed_at": (
                self.confirmed_at.astimezone(UTC).isoformat()
                if self.confirmed_at is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ByteOrderVerification:
    status: str
    device_value: int | None = None
    profile_byte_order: str | None = None
    profile_word_order: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    checked_at: datetime | None = None

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    @property
    def blocking(self) -> bool:
        return self.status == "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "device_value": self.device_value,
            "profile_byte_order": self.profile_byte_order,
            "profile_word_order": self.profile_word_order,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "checked_at": (
                self.checked_at.astimezone(UTC).isoformat()
                if self.checked_at is not None
                else None
            ),
        }


@dataclass(slots=True)
class ZeroMonitorQualityCounters:
    logical_poll_count: int = 0
    physical_request_count: int = 0
    torn_snapshot_reread_count: int = 0
    transport_failure_count: int = 0
    poll_overrun_count: int = 0
    missed_schedule_slot_count: int = 0
    successful_response_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "logical_poll_count": self.logical_poll_count,
            "physical_request_count": self.physical_request_count,
            "torn_snapshot_reread_count": self.torn_snapshot_reread_count,
            "transport_failure_count": self.transport_failure_count,
            "poll_overrun_count": self.poll_overrun_count,
            "missed_schedule_slot_count": self.missed_schedule_slot_count,
            "successful_response_count": self.successful_response_count,
        }


@dataclass(frozen=True, slots=True)
class ZeroMonitorLiveUpdate:
    row: dict[str, Any]
    processed: ZeroMonitorProcessedSample | None
    analysis: ZeroMonitorEvaluation
    counters: dict[str, int]


@dataclass(frozen=True, slots=True)
class ZeroMonitorRunResult:
    run_id: str | None
    attempt_id: str
    state: ZeroMonitorState
    run_status: RunStatus
    artifact_id: str | None
    analysis_result_id: str | None
    counters: dict[str, int]
    metrics: dict[str, Any]
    reason_codes: tuple[str, ...] = ()
    advisory_codes: tuple[str, ...] = ()
    byte_order_verification: ByteOrderVerification | None = None
    error_message: str | None = None


class ZeroMonitorCsvWriter:
    """Streaming same-directory partial writer with bounded loss on interruption."""

    def __init__(
        self,
        partial_path: Path,
        *,
        clock: Callable[[], float] = monotonic,
        fsync: Callable[[int], None] = os.fsync,
    ) -> None:
        if not str(partial_path).endswith(".csv.partial"):
            raise ValueError("Zero-monitor partial path must end with .csv.partial.")
        self.partial_path = Path(partial_path)
        self.final_path = Path(str(self.partial_path)[: -len(".partial")])
        self.partial_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._fsync = fsync
        self._file = self.partial_path.open("w", encoding="utf-8", newline="")
        self._writer = csv.DictWriter(
            self._file,
            fieldnames=ZERO_MONITOR_CSV_FIELDS,
            extrasaction="ignore",
        )
        self._writer.writeheader()
        self._last_sync = clock()
        self.row_count = 0

    def write(self, row: dict[str, Any]) -> None:
        self._writer.writerow({name: _csv_value(row.get(name)) for name in ZERO_MONITOR_CSV_FIELDS})
        self.row_count += 1
        if self._clock() - self._last_sync >= 1.0:
            self.sync()

    def sync(self) -> None:
        if self._file.closed:
            return
        self._file.flush()
        self._fsync(self._file.fileno())
        self._last_sync = self._clock()

    def finalize(self) -> Path:
        if not self._file.closed:
            self.sync()
            self._file.close()
        os.replace(self.partial_path, self.final_path)
        return self.final_path

    def close_partial(self) -> None:
        if not self._file.closed:
            self.sync()
            self._file.close()


class ModbusZeroMonitorService:
    """Own one read-only zero-monitor run and its traceable local evidence."""

    def __init__(
        self,
        *,
        repository: StorageRepository,
        artifact_store: ArtifactStore,
        reader: ModbusConfigurationBlockReader,
        register_map: ModbusRegisterMap,
        device_id: str,
        operator: str,
        analysis_config: ZeroMonitorAnalysisConfig,
        zero_flow_confirmation: ZeroFlowConfirmation,
        session_id: str | None = None,
        profile_id: str | None = None,
        device_metadata: dict[str, Any] | None = None,
        clock: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = sleep,
        wall_clock: Callable[[], datetime] | None = None,
        fsync: Callable[[int], None] = os.fsync,
    ) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.reader = reader
        self.register_map = register_map
        self.device_id = device_id
        self.operator = operator
        self.analysis_config = analysis_config
        self.zero_flow_confirmation = zero_flow_confirmation
        self.session_id = session_id
        self.profile_id = profile_id
        self.device_metadata = dict(device_metadata or {})
        self._clock = clock
        self._sleep = sleep_fn
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._fsync = fsync

    def run(
        self,
        *,
        stop_requested: Callable[[], bool] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        update_callback: Callable[[ZeroMonitorLiveUpdate], None] | None = None,
        max_polls: int | None = None,
    ) -> ZeroMonitorRunResult:
        stop_requested = stop_requested or (lambda: False)
        cancel_requested = cancel_requested or (lambda: False)
        attempt_id = f"ZMON-ATTEMPT-{uuid4().hex}"
        started_at = self._wall_clock()
        validation = validate_zero_monitor_register_map(self.register_map)
        if not validation.valid:
            errors = [
                {"code": error.code, "variable_name": error.variable_name, "message": error.message}
                for error in validation.errors
            ]
            message = "; ".join(f"{error['code']}: {error['message']}" for error in errors)
            self._save_prestart_attempt(
                attempt_id,
                started_at,
                "REGISTER_MAP_INVALID",
                message,
                {"validation_errors": errors},
            )
            return ZeroMonitorRunResult(
                run_id=None,
                attempt_id=attempt_id,
                state=ZeroMonitorState.EVALUATING,
                run_status=RunStatus.ERROR,
                artifact_id=None,
                analysis_result_id=None,
                counters=ZeroMonitorQualityCounters().to_dict(),
                metrics={},
                reason_codes=("REGISTER_MAP_INVALID",),
                error_message=message,
            )
        byte_order = self._verify_byte_order()
        if byte_order.blocking:
            message = byte_order.error_message or byte_order.error_code or "ByteOrder preflight failed."
            self._save_prestart_attempt(
                attempt_id,
                started_at,
                byte_order.error_code or "BYTE_ORDER_READ_FAILED",
                message,
                {"byte_order_verification": byte_order.to_dict()},
            )
            return ZeroMonitorRunResult(
                run_id=None,
                attempt_id=attempt_id,
                state=ZeroMonitorState.EVALUATING,
                run_status=RunStatus.ERROR,
                artifact_id=None,
                analysis_result_id=None,
                counters=ZeroMonitorQualityCounters().to_dict(),
                metrics={},
                reason_codes=(byte_order.error_code or "BYTE_ORDER_READ_FAILED",),
                byte_order_verification=byte_order,
                error_message=message,
            )

        run_id = f"RUN-{started_at:%Y%m%d}-{uuid4().hex[:12]}"
        capture_step_id = f"{run_id}-ZERO-CAPTURE"
        analysis_step_id = f"{run_id}-ZERO-ANALYSIS"
        artifact_id = f"{run_id}-ZERO-SAMPLES-{uuid4().hex[:8]}"
        partial_path = (
            self.artifact_store.run_directory(run_id, started_at)
            / ArtifactType.RAW.value
            / f"{artifact_id}_zero_monitor.csv.partial"
        )
        official_zero_offset = self._read_optional_zero_offset()
        register_snapshot = _register_map_snapshot(self.register_map)
        register_checksum = _json_checksum(register_snapshot)
        configuration = {
            "target_poll_interval_ms": ZERO_MONITOR_POLL_INTERVAL_MS,
            "analysis_config": self.analysis_config.to_dict(),
            "zero_flow_confirmation": {
                **self.zero_flow_confirmation.to_dict(),
                "device_id": self.device_id,
                "profile_id": self.profile_id,
                "register_map_checksum": register_checksum,
            },
            "byte_order_verification": byte_order.to_dict(),
            "profile_id": self.profile_id,
            "register_map": register_snapshot,
            "register_map_checksum": register_checksum,
            "partial_artifact_path": str(self.artifact_store.relative_path(partial_path)),
            "official_zero_offset": official_zero_offset,
        }
        self.repository.save_run(
            RunSession(
                run_id=run_id,
                run_type=RunType.STABILITY,
                workflow_name="modbus_zero_monitor",
                workflow_version=ZERO_MONITOR_WORKFLOW_VERSION,
                device_id=self.device_id,
                operator=self.operator,
                status=RunStatus.RUNNING,
                started_at=started_at,
                configuration_snapshot=configuration,
                software_version=__version__,
            )
        )
        self.repository.save_step(
            WorkflowStep(
                step_id=capture_step_id,
                run_id=run_id,
                name="zero_monitor_capture",
                step_type=WorkflowStepType.CAPTURE,
                status=WorkflowStepStatus.RUNNING,
                started_at=started_at,
                input_configuration=configuration,
            )
        )
        self.repository.save_step(
            WorkflowStep(
                step_id=analysis_step_id,
                run_id=run_id,
                name="zero_monitor_analysis",
                step_type=WorkflowStepType.ANALYSIS,
                status=WorkflowStepStatus.PENDING,
                input_configuration={"analysis_config": self.analysis_config.to_dict()},
            )
        )
        self.repository.save_modbus_operation_attempt(
            ModbusOperationAttemptRecord(
                attempt_id=attempt_id,
                session_id=self.session_id,
                run_id=run_id,
                device_id=self.device_id,
                operation_type="modbus_zero_monitor",
                status="running",
                started_at=started_at,
                operator=self.operator,
                device_metadata=self.device_metadata,
                register_map_snapshot=register_snapshot,
                summary={"partial_artifact_path": configuration["partial_artifact_path"]},
            )
        )

        writer = ZeroMonitorCsvWriter(partial_path, clock=self._clock, fsync=self._fsync)
        counters = ZeroMonitorQualityCounters()
        processor = ZeroMonitorProcessor(
            self.analysis_config,
            zero_flow_confirmed=self.zero_flow_confirmation.confirmed,
            byte_order_verified=byte_order.verified,
            official_zero_offset=official_zero_offset,
        )
        last_analysis = ZeroMonitorEvaluation(
            state=ZeroMonitorState.NOT_READY,
            reason_codes=("NO_LOGICAL_POLL",),
        )
        run_start = self._clock()
        schedule_index = 0
        completion_reason = "stop"
        fatal_error: str | None = None
        try:
            while True:
                if cancel_requested():
                    completion_reason = "cancel"
                    break
                if stop_requested():
                    completion_reason = "stop"
                    break
                if max_polls is not None and counters.logical_poll_count >= max_polls:
                    completion_reason = "stop"
                    break
                scheduled_elapsed_s = schedule_index * ZERO_MONITOR_POLL_INTERVAL_MS / 1000.0
                deadline = run_start + scheduled_elapsed_s
                now = self._clock()
                if now < deadline:
                    self._sleep(deadline - now)
                request_start = self._clock()
                request_started_at = self._wall_clock()
                logical_index = counters.logical_poll_count + 1
                physical_count = 0
                torn_rereads = 0
                initial_snapshot: ZeroMonitorSnapshot | None = None
                reread_snapshot: ZeroMonitorSnapshot | None = None
                processed: ZeroMonitorProcessedSample | None = None
                poll_fatal_error: str | None = None
                response_status = "ok"
                error_code = ""
                error_message = ""
                host_receive_time: datetime | None = None
                try:
                    physical_count += 1
                    initial_snapshot = self._read_snapshot()
                    host_receive_time = self._wall_clock()
                    if not initial_snapshot.snapshot_consistent:
                        torn_rereads = 1
                        physical_count += 1
                        reread_snapshot = self._read_snapshot()
                        host_receive_time = self._wall_clock()
                        if not reread_snapshot.snapshot_consistent:
                            response_status = "torn_reread_failed"
                            error_code = "TORN_SNAPSHOT"
                            error_message = "Begin/end sequence mismatch after one full reread."
                            last_analysis = processor.transport_gap(error_code)
                        else:
                            processed = processor.process(reread_snapshot)
                            last_analysis = processed.analysis
                    else:
                        processed = processor.process(initial_snapshot)
                        last_analysis = processed.analysis
                except Exception as exc:
                    response_status = _response_status_from_exception(exc)
                    error_code = _error_code_from_exception(exc)
                    error_message = str(exc)
                    if response_status in {
                        "timeout",
                        "crc_error",
                        "exception_response",
                    }:
                        counters.transport_failure_count += 1
                    last_analysis = processor.transport_gap(error_code)
                    if response_status == "program_error" or isinstance(
                        exc, ConnectionError
                    ):
                        poll_fatal_error = str(exc)
                request_end = self._clock()
                captured_at = self._wall_clock()
                next_schedule_index = max(
                    schedule_index + 1,
                    int((request_end - run_start) / (ZERO_MONITOR_POLL_INTERVAL_MS / 1000.0)) + 1,
                )
                missed_slots = max(0, next_schedule_index - schedule_index - 1)
                poll_overrun = missed_slots > 0
                if processed is not None and poll_overrun:
                    processed = replace(
                        processed,
                        advisory_codes=tuple((*processed.advisory_codes, "POLL_OVERRUN")),
                    )
                counters.logical_poll_count += 1
                counters.physical_request_count += physical_count
                counters.torn_snapshot_reread_count += torn_rereads
                counters.missed_schedule_slot_count += missed_slots
                if poll_overrun:
                    counters.poll_overrun_count += 1
                if host_receive_time is not None:
                    counters.successful_response_count += 1
                row = _zero_monitor_csv_row(
                    captured_at=captured_at,
                    elapsed_s=request_end - run_start,
                    logical_index=logical_index,
                    scheduled_elapsed_s=scheduled_elapsed_s,
                    schedule_lag_ms=max(0.0, (request_start - deadline) * 1000.0),
                    request_started_at=request_started_at,
                    request_start_elapsed_s=request_start - run_start,
                    request_duration_ms=(request_end - request_start) * 1000.0,
                    physical_request_count=physical_count,
                    torn_snapshot_reread_count=torn_rereads,
                    response_status=response_status,
                    error_code=error_code,
                    error_message=error_message,
                    initial_snapshot=initial_snapshot,
                    reread_snapshot=reread_snapshot,
                    processed=processed,
                    analysis=last_analysis,
                    host_receive_time=host_receive_time,
                    official_zero_offset=official_zero_offset,
                    poll_overrun=poll_overrun,
                    missed_schedule_slot_count=missed_slots,
                )
                writer.write(row)
                if update_callback is not None:
                    update_callback(
                        ZeroMonitorLiveUpdate(
                            row=row,
                            processed=processed,
                            analysis=last_analysis,
                            counters=counters.to_dict(),
                        )
                    )
                if poll_fatal_error is not None:
                    completion_reason = "error"
                    fatal_error = poll_fatal_error
                    break
                schedule_index = next_schedule_index
        except Exception as exc:
            completion_reason = "error"
            fatal_error = str(exc)
        finally:
            if writer.row_count:
                final_path = writer.finalize()
            else:
                writer.close_partial()
                final_path = None

        ended_at = self._wall_clock()
        return self._finalize_run(
            run_id=run_id,
            attempt_id=attempt_id,
            capture_step_id=capture_step_id,
            analysis_step_id=analysis_step_id,
            artifact_id=artifact_id,
            final_path=final_path,
            started_at=started_at,
            ended_at=ended_at,
            completion_reason=completion_reason,
            fatal_error=fatal_error,
            last_analysis=last_analysis,
            counters=counters,
            byte_order=byte_order,
            register_snapshot=register_snapshot,
            writer_row_count=writer.row_count,
        )

    def _read_snapshot(self) -> ZeroMonitorSnapshot:
        parameters = self.reader.read_configuration_parameters(
            ZERO_MONITOR_VARIABLE_NAMES,
            merge_adjacent=True,
            transport_retry_count=0,
        )
        return snapshot_from_parameters(self.register_map, parameters)

    def _read_optional_zero_offset(self) -> float | None:
        try:
            register = self.register_map.by_name("zero_offset")
        except KeyError:
            return None
        if register.writable and register.kind not in {RegisterKind.INPUT, RegisterKind.HOLDING}:
            return None
        try:
            parameters = self.reader.read_configuration_parameters(
                ("zero_offset",),
                merge_adjacent=True,
                transport_retry_count=None,
            )
            value = float(parameters[0].value)
        except Exception:
            return None
        return value if isfinite(value) else None

    def _verify_byte_order(self) -> ByteOrderVerification:
        checked_at = self._wall_clock()
        multiword = [
            self.register_map.by_name(spec.name)
            for spec in ZERO_MONITOR_FIELD_SPECS
            if spec.word_count == 2
        ]
        profile_byte = multiword[0].byte_order
        profile_word = multiword[0].word_order
        try:
            register = self.register_map.by_name("modbus_byte_order")
        except KeyError:
            return ByteOrderVerification(
                status="unavailable",
                profile_byte_order=profile_byte.value,
                profile_word_order=profile_word.value,
                checked_at=checked_at,
            )
        if (
            register.data_type is not ModbusDataType.UINT16
            or register.word_count != 1
            or register.kind not in {RegisterKind.INPUT, RegisterKind.HOLDING}
        ):
            return ByteOrderVerification(
                status="failed",
                profile_byte_order=profile_byte.value,
                profile_word_order=profile_word.value,
                error_code="BYTE_ORDER_VALUE_INVALID",
                error_message="modbus_byte_order must be one readable uint16 register.",
                checked_at=checked_at,
            )
        try:
            parameters = self.reader.read_configuration_parameters(
                ("modbus_byte_order",),
                merge_adjacent=True,
                transport_retry_count=None,
            )
            value = int(parameters[0].value)
        except Exception as exc:
            return ByteOrderVerification(
                status="failed",
                profile_byte_order=profile_byte.value,
                profile_word_order=profile_word.value,
                error_code="BYTE_ORDER_READ_FAILED",
                error_message=str(exc),
                checked_at=checked_at,
            )
        try:
            expected = byte_word_order_from_device_enum(value)
        except ValueError as exc:
            return ByteOrderVerification(
                status="failed",
                device_value=value,
                profile_byte_order=profile_byte.value,
                profile_word_order=profile_word.value,
                error_code="BYTE_ORDER_VALUE_INVALID",
                error_message=str(exc),
                checked_at=checked_at,
            )
        if expected != (profile_byte, profile_word):
            return ByteOrderVerification(
                status="failed",
                device_value=value,
                profile_byte_order=profile_byte.value,
                profile_word_order=profile_word.value,
                error_code="BYTE_ORDER_MISMATCH",
                error_message="Device ByteOrder enum does not match the active profile.",
                checked_at=checked_at,
            )
        return ByteOrderVerification(
            status="verified",
            device_value=value,
            profile_byte_order=profile_byte.value,
            profile_word_order=profile_word.value,
            checked_at=checked_at,
        )

    def _save_prestart_attempt(
        self,
        attempt_id: str,
        started_at: datetime,
        error_code: str,
        message: str,
        summary: dict[str, Any],
    ) -> None:
        self.repository.save_modbus_operation_attempt(
            ModbusOperationAttemptRecord(
                attempt_id=attempt_id,
                session_id=self.session_id,
                run_id=None,
                device_id=self.device_id,
                operation_type="modbus_zero_monitor",
                status="error",
                started_at=started_at,
                ended_at=self._wall_clock(),
                operator=self.operator,
                device_metadata=self.device_metadata,
                register_map_snapshot=_register_map_snapshot(self.register_map),
                summary={**summary, "error_code": error_code, "error_message": message},
            )
        )

    def _finalize_run(
        self,
        *,
        run_id: str,
        attempt_id: str,
        capture_step_id: str,
        analysis_step_id: str,
        artifact_id: str,
        final_path: Path | None,
        started_at: datetime,
        ended_at: datetime,
        completion_reason: str,
        fatal_error: str | None,
        last_analysis: ZeroMonitorEvaluation,
        counters: ZeroMonitorQualityCounters,
        byte_order: ByteOrderVerification,
        register_snapshot: dict[str, Any],
        writer_row_count: int,
    ) -> ZeroMonitorRunResult:
        has_rows = writer_row_count > 0 and final_path is not None
        artifact: Artifact | None = None
        analysis_result_id: str | None = None
        timing = _timing_summary(final_path) if final_path is not None else _empty_timing_summary()
        numeric_metrics = last_analysis.metrics.to_dict()
        if counters.successful_response_count == 0:
            numeric_metrics = {name: None for name in numeric_metrics}
        metrics = {**numeric_metrics, **counters.to_dict(), **timing}
        if has_rows:
            artifact = Artifact(
                artifact_id=artifact_id,
                run_id=run_id,
                step_id=capture_step_id,
                artifact_type=ArtifactType.RAW,
                file_path=self.artifact_store.relative_path(final_path),
                file_format="csv",
                size_bytes=final_path.stat().st_size,
                checksum=_file_sha256(final_path),
                created_at=ended_at,
                metadata={
                    "source": "modbus_module",
                    "operation_type": "modbus_zero_monitor",
                    "curve_type": "zero_monitor_samples",
                    "flow_rate_parameter": "live_zero_600ms",
                    "x_axis_variable": "device_tick_ms_unwrapped",
                    "x_axis_unit": "ms",
                    "x_axis_scope": "continuous_segment",
                    "segment_variable": "continuous_segment",
                    "unit": "us",
                    "units": {
                        "base_mean_100ms": "us",
                        "base_std_100ms": "us",
                        "live_zero_600ms": "us",
                        "trim_std_600ms": "us",
                        "trim_range_600ms": "us",
                        "raw_p2p_600ms": "us",
                        "zero_drift_from_cal": "us",
                    },
                    "variable_names": [
                        "base_mean_100ms",
                        "base_std_100ms",
                        "live_zero_600ms",
                        "trim_std_600ms",
                        "trim_range_600ms",
                        "raw_p2p_600ms",
                        "zero_drift_from_cal",
                    ],
                    "sample_count": writer_row_count,
                    "successful_response_count": counters.successful_response_count,
                    "failed_poll_count": _failed_poll_count(final_path),
                    "complete": completion_reason == "stop" and fatal_error is None,
                    "recovered": False,
                },
            )
            self.repository.save_artifact(artifact)
            analysis_result_id = f"{run_id}-ZERO-RESULT"
            self.repository.save_analysis_result(
                AnalysisResultRecord(
                    result_id=analysis_result_id,
                    run_id=run_id,
                    step_id=analysis_step_id,
                    result_type="modbus_zero_monitor_stability",
                    algorithm_name="coreflow.zero_monitor",
                    algorithm_version="1",
                    input_artifact_ids=(artifact_id,),
                    configuration_snapshot=self.analysis_config.to_dict(),
                    summary_metrics={
                        **metrics,
                        "analysis_state": last_analysis.state.value,
                        "reason_codes": list(last_analysis.reason_codes),
                        "advisory_codes": list(last_analysis.advisory_codes),
                    },
                    pass_fail_decision=(
                        "passed"
                        if completion_reason == "stop" and last_analysis.state is ZeroMonitorState.STABLE
                        else (
                            "failed"
                            if completion_reason == "stop" and last_analysis.state is ZeroMonitorState.UNSTABLE
                            else None
                        )
                    ),
                    created_at=ended_at,
                )
            )
        run_status, capture_status, analysis_status, attempt_status = _terminal_statuses(
            completion_reason,
            fatal_error,
            has_rows,
            last_analysis.state,
        )
        self.repository.save_step(
            WorkflowStep(
                step_id=capture_step_id,
                run_id=run_id,
                name="zero_monitor_capture",
                step_type=WorkflowStepType.CAPTURE,
                status=capture_status,
                started_at=started_at,
                ended_at=ended_at,
                output_summary={**counters.to_dict(), "artifact_id": artifact_id if artifact else None},
                error_message=fatal_error,
            )
        )
        self.repository.save_step(
            WorkflowStep(
                step_id=analysis_step_id,
                run_id=run_id,
                name="zero_monitor_analysis",
                step_type=WorkflowStepType.ANALYSIS,
                status=analysis_status,
                started_at=started_at if has_rows else None,
                ended_at=ended_at,
                output_summary={
                    "analysis_result_id": analysis_result_id,
                    "analysis_state": last_analysis.state.value,
                    **metrics,
                },
                error_message=fatal_error,
            )
        )
        self.repository.save_modbus_operation_attempt(
            ModbusOperationAttemptRecord(
                attempt_id=attempt_id,
                session_id=self.session_id,
                run_id=run_id,
                device_id=self.device_id,
                operation_type="modbus_zero_monitor",
                status=attempt_status,
                started_at=started_at,
                ended_at=ended_at,
                operator=self.operator,
                device_metadata=self.device_metadata,
                register_map_snapshot=register_snapshot,
                raw_artifact_id=artifact_id if artifact else None,
                summary={
                    **metrics,
                    "analysis_state": last_analysis.state.value,
                    "reason_codes": list(last_analysis.reason_codes),
                    "advisory_codes": list(last_analysis.advisory_codes),
                    "flow_samples_artifact_id": artifact_id if artifact else None,
                    "byte_order_verification": byte_order.to_dict(),
                    "completion_reason": completion_reason,
                    "error_message": fatal_error,
                },
            )
        )
        run = self.repository.get_run(run_id)
        if run is None:
            raise RuntimeError(f"Zero-monitor run disappeared before finalization: {run_id}")
        self.repository.save_run(
            replace(
                run,
                status=run_status,
                ended_at=ended_at,
                configuration_snapshot={
                    **run.configuration_snapshot,
                    "artifact_id": artifact_id if artifact else None,
                    "analysis_result_id": analysis_result_id,
                    "completion_reason": completion_reason,
                },
            )
        )
        return ZeroMonitorRunResult(
            run_id=run_id,
            attempt_id=attempt_id,
            state=last_analysis.state,
            run_status=run_status,
            artifact_id=artifact_id if artifact else None,
            analysis_result_id=analysis_result_id,
            counters=counters.to_dict(),
            metrics=metrics,
            reason_codes=last_analysis.reason_codes,
            advisory_codes=last_analysis.advisory_codes,
            byte_order_verification=byte_order,
            error_message=fatal_error,
        )


def recover_interrupted_zero_monitor_runs(
    repository: StorageRepository,
    artifact_store: ArtifactStore,
    *,
    recovered_at: datetime | None = None,
) -> tuple[str, ...]:
    """Finalize recorded partial files from zero-monitor runs left running."""

    recovered_at = recovered_at or datetime.now(UTC)
    recovered: list[str] = []
    root = artifact_store.data_root.resolve()
    for summary in repository.list_runs():
        if (
            summary.status != RunStatus.RUNNING.value
            or summary.workflow_name != "modbus_zero_monitor"
        ):
            continue
        run = repository.get_run(summary.run_id)
        if run is None:
            continue
        relative = run.configuration_snapshot.get("partial_artifact_path")
        if not isinstance(relative, str) or not relative.endswith(".csv.partial"):
            _mark_recovery_rejected(
                repository,
                run,
                recovered_at,
                "INVALID_PARTIAL_PATH",
            )
            recovered.append(run.run_id)
            continue
        partial = artifact_store.resolve(PurePath(relative)).resolve()
        if not partial.is_relative_to(root):
            _mark_recovery_rejected(
                repository,
                run,
                recovered_at,
                "PARTIAL_PATH_OUTSIDE_ARTIFACT_ROOT",
            )
            recovered.append(run.run_id)
            continue
        if not partial.exists():
            _mark_recovery_rejected(
                repository,
                run,
                recovered_at,
                "PARTIAL_FILE_MISSING",
            )
            recovered.append(run.run_id)
            continue
        rows = _read_csv_rows(partial)
        steps = {step.name: step for step in repository.list_steps(run.run_id)}
        capture = steps.get("zero_monitor_capture")
        analysis = steps.get("zero_monitor_analysis")
        final_path: Path | None = None
        artifact_id: str | None = None
        analysis_result_id: str | None = None
        if rows:
            final_path = Path(str(partial)[: -len(".partial")])
            os.replace(partial, final_path)
            artifact_id = f"{run.run_id}-ZERO-RECOVERED-{uuid4().hex[:8]}"
            response_successes = sum(bool(row.get("host_receive_time")) for row in rows)
            failed_polls = sum(row.get("response_status") != "ok" for row in rows)
            recovered_counters = {
                "logical_poll_count": len(rows),
                "physical_request_count": sum(
                    _row_int(row, "physical_request_count") for row in rows
                ),
                "torn_snapshot_reread_count": sum(
                    _row_int(row, "torn_snapshot_reread_count") for row in rows
                ),
                "transport_failure_count": sum(
                    row.get("response_status")
                    in {"timeout", "crc_error", "exception_response"}
                    for row in rows
                ),
                "poll_overrun_count": sum(
                    _row_bool(row, "poll_overrun") for row in rows
                ),
                "missed_schedule_slot_count": sum(
                    _row_int(row, "missed_schedule_slot_count") for row in rows
                ),
                "successful_response_count": response_successes,
            }
            artifact = Artifact(
                artifact_id=artifact_id,
                run_id=run.run_id,
                step_id=capture.step_id if capture is not None else None,
                artifact_type=ArtifactType.RAW,
                file_path=artifact_store.relative_path(final_path),
                file_format="csv",
                size_bytes=final_path.stat().st_size,
                checksum=_file_sha256(final_path),
                created_at=recovered_at,
                metadata={
                    "source": "modbus_module",
                    "operation_type": "modbus_zero_monitor",
                    "curve_type": "zero_monitor_samples",
                    "flow_rate_parameter": "live_zero_600ms",
                    "x_axis_variable": "device_tick_ms_unwrapped",
                    "x_axis_unit": "ms",
                    "x_axis_scope": "continuous_segment",
                    "segment_variable": "continuous_segment",
                    "unit": "us",
                    "units": {"live_zero_600ms": "us"},
                    "variable_names": ["live_zero_600ms"],
                    "sample_count": len(rows),
                    "successful_response_count": response_successes,
                    "failed_poll_count": failed_polls,
                    "complete": False,
                    "recovered": True,
                    "recovery_reason": "unclean_shutdown",
                },
            )
            repository.save_artifact(artifact)
            analysis_result_id = f"{run.run_id}-ZERO-RECOVERED-RESULT"
            repository.save_analysis_result(
                AnalysisResultRecord(
                    result_id=analysis_result_id,
                    run_id=run.run_id,
                    step_id=analysis.step_id if analysis is not None else None,
                    result_type="modbus_zero_monitor_stability",
                    algorithm_name="coreflow.zero_monitor.recovery",
                    algorithm_version="1",
                    input_artifact_ids=(artifact_id,),
                    configuration_snapshot=run.configuration_snapshot.get(
                        "analysis_config", {}
                    ),
                    summary_metrics={
                        "analysis_state": ZeroMonitorState.DATA_GAP.value,
                        **recovered_counters,
                        "failed_poll_count": failed_polls,
                        "recovered": True,
                        "reason_codes": ["UNCLEAN_SHUTDOWN"],
                    },
                    pass_fail_decision=None,
                    created_at=recovered_at,
                )
            )
        else:
            partial.unlink(missing_ok=True)
        if capture is not None:
            repository.save_step(
                replace(
                    capture,
                    status=WorkflowStepStatus.ERROR,
                    ended_at=recovered_at,
                    output_summary={"artifact_id": artifact_id, "recovered": True},
                    error_message="Unclean shutdown; partial capture recovered.",
                )
            )
        if analysis is not None:
            repository.save_step(
                replace(
                    analysis,
                    status=(
                        WorkflowStepStatus.COMPLETED
                        if rows
                        else WorkflowStepStatus.SKIPPED
                    ),
                    ended_at=recovered_at,
                    output_summary={"analysis_result_id": analysis_result_id, "recovered": True},
                )
            )
        attempts = tuple(
            attempt
            for attempt in repository.list_modbus_operation_attempts(
                device_id=run.device_id,
                operation_type="modbus_zero_monitor",
            )
            if attempt.run_id == run.run_id
        )
        for attempt in attempts:
            repository.save_modbus_operation_attempt(
                replace(
                    attempt,
                    status="error",
                    ended_at=recovered_at,
                    raw_artifact_id=artifact_id,
                    summary={
                        **attempt.summary,
                        "recovered": True,
                        "recovery_reason": "unclean_shutdown",
                        "flow_samples_artifact_id": artifact_id,
                        "analysis_result_id": analysis_result_id,
                    },
                )
            )
        repository.save_run(
            replace(
                run,
                status=RunStatus.ERROR,
                ended_at=recovered_at,
                configuration_snapshot={
                    **run.configuration_snapshot,
                    "artifact_id": artifact_id,
                    "analysis_result_id": analysis_result_id,
                    "recovered": True,
                },
            )
        )
        recovered.append(run.run_id)
    return tuple(recovered)


def _mark_recovery_rejected(
    repository: StorageRepository,
    run: RunSession,
    recovered_at: datetime,
    reason: str,
) -> None:
    for step in repository.list_steps(run.run_id):
        repository.save_step(
            replace(
                step,
                status=(
                    WorkflowStepStatus.ERROR
                    if step.name == "zero_monitor_capture"
                    else WorkflowStepStatus.SKIPPED
                ),
                ended_at=recovered_at,
                output_summary={**step.output_summary, "recovery_rejected": True},
                error_message=reason,
            )
        )
    for attempt in repository.list_modbus_operation_attempts(
        device_id=run.device_id,
        operation_type="modbus_zero_monitor",
    ):
        if attempt.run_id != run.run_id:
            continue
        repository.save_modbus_operation_attempt(
            replace(
                attempt,
                status="error",
                ended_at=recovered_at,
                summary={
                    **attempt.summary,
                    "recovery_rejected": True,
                    "recovery_reason": reason,
                },
            )
        )
    repository.save_run(
        replace(
            run,
            status=RunStatus.ERROR,
            ended_at=recovered_at,
            configuration_snapshot={
                **run.configuration_snapshot,
                "recovery_rejected": True,
                "recovery_reason": reason,
            },
        )
    )


def _zero_monitor_csv_row(
    *,
    captured_at: datetime,
    elapsed_s: float,
    logical_index: int,
    scheduled_elapsed_s: float,
    schedule_lag_ms: float,
    request_started_at: datetime,
    request_start_elapsed_s: float,
    request_duration_ms: float,
    physical_request_count: int,
    torn_snapshot_reread_count: int,
    response_status: str,
    error_code: str,
    error_message: str,
    initial_snapshot: ZeroMonitorSnapshot | None,
    reread_snapshot: ZeroMonitorSnapshot | None,
    processed: ZeroMonitorProcessedSample | None,
    analysis: ZeroMonitorEvaluation,
    host_receive_time: datetime | None,
    official_zero_offset: float | None,
    poll_overrun: bool,
    missed_schedule_slot_count: int,
) -> dict[str, Any]:
    snapshot = processed.snapshot if processed is not None else None
    metrics = analysis.metrics
    row: dict[str, Any] = {
        "captured_at": captured_at.astimezone(UTC).isoformat(),
        "elapsed_s": elapsed_s,
        "sample_index": logical_index,
        "logical_poll_index": logical_index,
        "scheduled_elapsed_s": scheduled_elapsed_s,
        "schedule_lag_ms": schedule_lag_ms,
        "request_started_at": request_started_at.astimezone(UTC).isoformat(),
        "request_start_elapsed_s": request_start_elapsed_s,
        "request_duration_ms": request_duration_ms,
        "physical_request_count": physical_request_count,
        "torn_snapshot_reread_count": torn_snapshot_reread_count,
        "response_status": response_status,
        "error_code": error_code,
        "error_message": error_message,
        "initial_raw_words_hex": _raw_words_hex(initial_snapshot),
        "reread_raw_words_hex": _raw_words_hex(reread_snapshot),
        "host_receive_time": (
            host_receive_time.astimezone(UTC).isoformat()
            if host_receive_time is not None
            else ""
        ),
        "official_zero_offset": official_zero_offset,
        "zero_drift_from_cal": metrics.zero_drift_from_cal,
        "analysis_state": analysis.state.value,
        "state_reason_codes": list(
            processed.reason_codes if processed is not None else analysis.reason_codes
        ),
        "advisory_codes": list(
            processed.advisory_codes if processed is not None else analysis.advisory_codes
        ),
        "poll_overrun": poll_overrun,
        "missed_schedule_slot_count": missed_schedule_slot_count,
        "accept_for_statistics": (
            processed.accept_for_statistics if processed is not None else False
        ),
    }
    if snapshot is not None:
        row.update(
            {
                "device_tick_ms_raw": snapshot.tick_ms,
                "device_tick_ms_unwrapped": processed.device_tick_ms_unwrapped,
                "continuous_segment": processed.continuous_segment,
                "sequence": snapshot.sequence,
                "sequence_delta": processed.sequence_delta,
                "status": snapshot.status,
                "reserved_status_bits": snapshot.reserved_status_bits,
                "base_ready": snapshot.base_ready,
                "live_ready": snapshot.live_ready,
                "data_valid": snapshot.data_valid,
                "zero_cal_running": snapshot.zero_cal_running,
                "internal_error": snapshot.internal_error,
                "valid_count": snapshot.valid_count,
                "base_mean_100ms": snapshot.base_mean_100ms,
                "base_std_100ms": snapshot.base_std_100ms,
                "live_zero_600ms": snapshot.live_zero_600ms,
                "trim_std_600ms": snapshot.trim_std_600ms,
                "trim_range_600ms": snapshot.trim_range_600ms,
                "raw_p2p_600ms": snapshot.raw_p2p_600ms,
                "snapshot_consistent": snapshot.snapshot_consistent,
                "communication_gap": processed.communication_gap,
                "segment_break_reason": processed.segment_break_reason,
            }
        )
    return row


def _terminal_statuses(
    completion_reason: str,
    fatal_error: str | None,
    has_rows: bool,
    state: ZeroMonitorState,
) -> tuple[RunStatus, WorkflowStepStatus, WorkflowStepStatus, str]:
    if fatal_error is not None or (completion_reason == "stop" and not has_rows):
        return (
            RunStatus.ERROR,
            WorkflowStepStatus.ERROR,
            WorkflowStepStatus.COMPLETED if has_rows else WorkflowStepStatus.SKIPPED,
            "error",
        )
    if completion_reason == "cancel":
        return (
            RunStatus.CANCELED,
            WorkflowStepStatus.CANCELED,
            WorkflowStepStatus.COMPLETED if has_rows else WorkflowStepStatus.SKIPPED,
            "canceled",
        )
    if state is ZeroMonitorState.STABLE:
        return RunStatus.PASSED, WorkflowStepStatus.COMPLETED, WorkflowStepStatus.PASSED, "passed"
    if state is ZeroMonitorState.UNSTABLE:
        return RunStatus.FAILED, WorkflowStepStatus.COMPLETED, WorkflowStepStatus.FAILED, "failed"
    return RunStatus.COMPLETED, WorkflowStepStatus.COMPLETED, WorkflowStepStatus.COMPLETED, "completed"


def _register_map_snapshot(register_map: ModbusRegisterMap) -> dict[str, Any]:
    return {
        "name": register_map.name,
        "version": register_map.version,
        "registers": [
            {
                "name": register.name,
                "kind": register.kind.value,
                "address": register.address,
                "word_count": register.word_count,
                "data_type": register.data_type.value,
                "scale": register.scale,
                "unit": register.unit,
                "writable": register.writable,
                "byte_order": register.byte_order.value,
                "word_order": register.word_order.value,
            }
            for register in register_map.registers
        ],
    }


def _json_checksum(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _raw_words_hex(snapshot: ZeroMonitorSnapshot | None) -> str:
    if snapshot is None:
        return ""
    return " ".join(f"{word:04X}" for word in snapshot.raw_words)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return value


def _response_status_from_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "crc" in text:
        return "crc_error"
    if "exception" in text:
        return "exception_response"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    return "program_error"


def _error_code_from_exception(exc: Exception) -> str:
    return {
        "timeout": "TRANSPORT_TIMEOUT",
        "crc_error": "TRANSPORT_CRC_ERROR",
        "exception_response": "MODBUS_EXCEPTION_RESPONSE",
        "program_error": "PROGRAM_ERROR",
    }[_response_status_from_exception(exc)]


def _timing_summary(path: Path) -> dict[str, float | None]:
    starts: list[float] = []
    with path.open("r", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            try:
                starts.append(float(row["request_start_elapsed_s"]))
            except (KeyError, TypeError, ValueError):
                continue
    intervals = [
        (current - previous) * 1000.0
        for previous, current in zip(starts, starts[1:])
    ]
    if not intervals:
        return _empty_timing_summary()
    ordered = sorted(intervals)
    duration_s = starts[-1] - starts[0]
    return {
        "observed_period_mean_ms": sum(intervals) / len(intervals),
        "observed_period_p50_ms": _linear_percentile(ordered, 0.50),
        "observed_period_p95_ms": _linear_percentile(ordered, 0.95),
        "observed_period_p99_ms": _linear_percentile(ordered, 0.99),
        "observed_period_max_ms": max(intervals),
        "achieved_poll_rate_hz": (
            (len(starts) - 1) / duration_s if duration_s > 0 else None
        ),
    }


def _failed_poll_count(path: Path) -> int:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return sum(
            row.get("response_status") != "ok"
            for row in csv.DictReader(stream)
        )


def _empty_timing_summary() -> dict[str, None]:
    return {
        "observed_period_mean_ms": None,
        "observed_period_p50_ms": None,
        "observed_period_p95_ms": None,
        "observed_period_p99_ms": None,
        "observed_period_max_ms": None,
        "achieved_poll_rate_hz": None,
    }


def _linear_percentile(ordered: list[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream))
    except (OSError, csv.Error):
        return []


def _row_int(row: dict[str, str], name: str) -> int:
    try:
        return int(row.get(name) or 0)
    except ValueError:
        return 0


def _row_bool(row: dict[str, str], name: str) -> bool:
    return str(row.get(name) or "").strip().lower() in {"1", "true", "yes"}

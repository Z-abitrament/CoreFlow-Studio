"""Generate the official CoreFlow register list from Krohne DSP source maps."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ADDRESS_DEFINE_RE = re.compile(
    r"^\s*#define\s+(MbAddr_[A-Za-z0-9_]+)\s+\((0x[0-9A-Fa-f]+|[0-9]+)(?:U|UL)?\)",
    re.MULTILINE,
)
ACCESS_RE = re.compile(
    r"MB_MAP_ACCESS_REG_(16|32)\(\s*(MbAddr_[A-Za-z0-9_]+)\s*,\s*(RO|WO|RW)\s*\)"
)
KIND_RE = re.compile(
    r"MB_MAP_(HOLDING|INPUT)_REG_(16|32)\(\s*(MbAddr_[A-Za-z0-9_]+)\s*\)"
)
C_COMMENT_RE = re.compile(r"//[^\r\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True, slots=True)
class MappedRegister:
    width: int
    access: str
    kind: str


@dataclass(frozen=True, slots=True)
class RegisterSpec:
    symbol: str
    name: str
    data_type: str
    group: str
    description: str
    unit: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _spec(
    symbol: str,
    name: str,
    data_type: str,
    group: str,
    description: str,
    *,
    unit: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    **metadata: Any,
) -> RegisterSpec:
    return RegisterSpec(
        symbol=symbol,
        name=name,
        data_type=data_type,
        group=group,
        description=description,
        unit=unit,
        minimum=minimum,
        maximum=maximum,
        metadata=metadata,
    )


REGISTER_SPECS = (
    _spec("MbAddr_PulseOut_MaxFreq_40000", "pulse_output_max_frequency", "uint16", "pulse_output", "Maximum pulse output frequency.", unit="Hz"),
    _spec("MbAddr_PulseOut_Variable_40001", "pulse_output_variable", "uint16", "pulse_output", "Pulse output source selection.", minimum=0, maximum=1, enum_values={"0": "mass", "1": "volume"}),
    _spec("MbAddr_PulseOut_QualityPerPulse_40002", "pulse_output_quantity_per_pulse", "float32", "pulse_output", "Quantity represented by one output pulse."),
    _spec("MbAddr_MassFlow", "mass_flow", "float32", "measurement", "Live mass flow in the unit selected by mass_flow_unit.", unit_selector="mass_flow_unit"),
    _spec("MbAddr_MassFlow", "mass_rate", "float32", "measurement", "Client workflow alias for mass_flow.", unit_selector="mass_flow_unit", alias_for="mass_flow"),
    _spec("MbAddr_Temperature", "temperature", "float32", "measurement", "Live temperature in the unit selected by temperature_unit.", unit_selector="temperature_unit"),
    _spec("MbAddr_MassTotal", "mass_acc", "float32", "measurement", "Accumulated mass in the unit selected by mass_total_unit.", unit_selector="mass_total_unit"),
    _spec("MbAddr_deltaT_us", "delta_t", "float32", "measurement", "Measured tube time difference.", unit="us"),
    _spec("MbAddr_signal_freq", "frequency", "float32", "measurement", "Measured tube signal frequency.", unit="Hz"),
    _spec("MbAddr_LowThreshold", "low_threshold", "float32", "flow_configuration", "Low-flow cutoff threshold in the internal mass-flow unit.", unit="g/s"),
    _spec("MbAddr_ZeroOffset", "zero_offset", "float32", "flow_configuration", "Official zero offset used by the DSP flow calculation.", unit="us"),
    _spec("MbAddr_MassFlowFactor", "mass_flow_factor", "float32", "flow_configuration", "Mass-flow scale factor."),
    _spec("MbAddr_K_Flow_Rate", "k_factor", "float32", "flow_configuration", "K flow-rate calibration factor."),
    _spec("MbAddr_MF_wide", "mass_flow_filter_width", "uint32", "flow_configuration", "Mass-flow moving-average filter width.", unit="samples"),
    _spec("MbAddr_MassFlowUnit", "mass_flow_unit", "uint16", "unit_configuration", "Mass-flow output unit code.", enum_values={"70": "g/s", "71": "g/min", "72": "g/h", "73": "kg/s", "74": "kg/min", "75": "kg/h", "76": "kg/day", "77": "t/min", "78": "t/h", "79": "t/day"}),
    _spec("MbAddr_DensityUnit", "density_unit", "uint16", "unit_configuration", "Density output unit code.", enum_values={"91": "g/cc", "92": "kg/m3", "95": "g/ml", "96": "kg/L", "97": "g/L"}),
    _spec("MbAddr_TemperatureUnit", "temperature_unit", "uint16", "unit_configuration", "Temperature output unit code.", enum_values={"32": "deg C", "33": "deg F"}),
    _spec("MbAddr_VolumeFlowUnit", "volume_flow_unit", "uint16", "unit_configuration", "Volume-flow output unit code.", enum_values={"0": "ml/s", "17": "L/min", "19": "m3/h", "24": "L/s", "25": "L/day", "28": "m3/s", "29": "m3/day", "131": "m3/min", "138": "L/h"}),
    _spec("MbAddr_MassTotalUnit", "mass_total_unit", "uint16", "unit_configuration", "Accumulated-mass output unit code.", enum_values={"11": "kg", "60": "g", "62": "t"}),
    _spec("MbAddr_VolumeTotalUnit", "volume_total_unit", "uint16", "unit_configuration", "Accumulated-volume output unit code.", enum_values={"0": "ml", "41": "L", "43": "m3"}),
    _spec("MbAddr_Modbus_BaudRate", "modbus_baud_rate", "uint32", "communication", "Requested Modbus RTU baud rate.", unit="bit/s", enum_values={"1200": "1200", "2400": "2400", "4800": "4800", "9600": "9600", "19200": "19200", "38400": "38400", "57600": "57600", "115200": "115200"}),
    _spec("MbAddr_Modbus_Parity", "modbus_parity", "uint16", "communication", "Requested Modbus parity.", minimum=0, maximum=2, enum_values={"0": "none", "1": "odd", "2": "even"}),
    _spec("MbAddr_Modbus_DataBits", "modbus_data_bits", "uint16", "communication", "Requested Modbus RTU data bits.", minimum=8, maximum=8),
    _spec("MbAddr_Modbus_StopBits", "modbus_stop_bits", "uint16", "communication", "Requested Modbus stop bits.", minimum=1, maximum=2),
    _spec("MbAddr_Modbus_ByteOrder", "modbus_byte_order", "uint16", "communication", "Order used for every 32-bit Modbus value.", minimum=0, maximum=3, enum_values={"0": "ABCD", "1": "BADC", "2": "CDAB", "3": "DCBA"}),
    _spec("MbAddr_Modbus_Apply", "modbus_apply", "uint16", "communication", "Write 1 to apply validated communication settings after the response.", minimum=0, maximum=1),
    _spec("MbAddr_Modbus_Status", "modbus_status", "uint16", "communication", "Communication configuration status code.", minimum=0, maximum=6, enum_values={"0": "ok", "1": "pending", "2": "invalid_baudrate", "3": "invalid_parity", "4": "invalid_stop_bits", "5": "invalid_data_bits", "6": "invalid_byte_order"}),
    _spec("MbAddr_VolumeFlow", "volume_flow", "float32", "measurement", "Live volume flow in the unit selected by volume_flow_unit.", unit_selector="volume_flow_unit"),
    _spec("MbAddr_VolumeTotal", "volume_acc", "float32", "measurement", "Accumulated volume in the unit selected by volume_total_unit.", unit_selector="volume_total_unit"),
    _spec("MbAddr_Density", "density", "float32", "measurement", "Live density in the unit selected by density_unit.", unit_selector="density_unit"),
    _spec("MbAddr_TubePeriod", "tube_period", "float32", "measurement", "Measured tube vibration period."),
    _spec("MbAddr_DensityEnable", "density_enable", "uint16", "density_configuration", "Enable density and volume calculations.", minimum=0, maximum=1),
    _spec("MbAddr_DensitySlope", "density_slope", "float32", "density_configuration", "Density calibration slope."),
    _spec("MbAddr_DensityOffset", "density_offset", "float32", "density_configuration", "Density calibration offset."),
    _spec("MbAddr_DensityTemperatureCoefficient", "density_temperature_coefficient", "float32", "density_configuration", "Density temperature compensation coefficient."),
    _spec("MbAddr_DensityFactor", "density_factor", "float32", "density_configuration", "Density scale factor."),
    _spec("MbAddr_DensityPressureFactor", "density_pressure_factor", "float32", "density_configuration", "Density pressure compensation factor."),
    _spec("MbAddr_DensitySystemOffset", "density_system_offset", "float32", "density_configuration", "Density system offset."),
    _spec("MbAddr_DensityCutoff", "density_cutoff", "float32", "density_configuration", "Density cutoff threshold."),
    _spec("MbAddr_VolumeFactor", "volume_factor", "float32", "density_configuration", "Volume-flow scale factor."),
    _spec("MbAddr_VolumeFlowCutoff", "volume_flow_cutoff", "float32", "density_configuration", "Volume-flow cutoff threshold."),
    _spec("MbAddr_SignalFreqScale", "signal_frequency_scale", "float32", "density_configuration", "Signal-frequency scale used by density calculation."),
    _spec("MbAddr_LPOVoltage", "lpo_voltage", "float32", "measurement", "Left pickup signal magnitude reported by the DSP."),
    _spec("MbAddr_RPOVoltage", "rpo_voltage", "float32", "measurement", "Right pickup signal magnitude reported by the DSP."),
    _spec("MbAddr_ZeroSnapshotSequenceBegin", "zero_snapshot_sequence_begin", "uint16", "zero_monitor", "Coherent zero-monitor snapshot begin sequence."),
    _spec("MbAddr_ZeroMonitorStatus", "zero_monitor_status", "uint16", "zero_monitor", "Zero-monitor readiness and validity bit field.", minimum=0, maximum=31, bit_flags={"0": "base_ready", "1": "window_full", "2": "window_all_valid", "3": "zero_calibration_running", "4": "internal_error"}),
    _spec("MbAddr_ZeroMonitorTickMs", "zero_monitor_tick_ms", "uint32", "zero_monitor", "Device-side zero-monitor time.", unit="ms"),
    _spec("MbAddr_ZeroBaseMean100ms", "zero_base_mean_100ms", "float32", "zero_monitor", "Zero-monitor 100 ms base mean.", unit="us"),
    _spec("MbAddr_ZeroBaseStd100ms", "zero_base_std_100ms", "float32", "zero_monitor", "Zero-monitor 100 ms population standard deviation.", unit="us"),
    _spec("MbAddr_ZeroLive600ms", "zero_live_600ms", "float32", "zero_monitor", "Live trimmed zero value over the current 600 ms window.", unit="us"),
    _spec("MbAddr_ZeroTrimStd600ms", "zero_trim_std_600ms", "float32", "zero_monitor", "Trimmed 600 ms population standard deviation.", unit="us"),
    _spec("MbAddr_ZeroTrimRange600ms", "zero_trim_range_600ms", "float32", "zero_monitor", "Trimmed 600 ms range.", unit="us"),
    _spec("MbAddr_ZeroRawP2P600ms", "zero_raw_p2p_600ms", "float32", "zero_monitor", "Raw 600 ms peak-to-peak range.", unit="us"),
    _spec("MbAddr_ZeroWindowValidCount", "zero_window_valid_count", "uint16", "zero_monitor", "Valid sample count in the current 600 ms window.", minimum=0, maximum=60, unit="samples"),
    _spec("MbAddr_ZeroSnapshotSequenceEnd", "zero_snapshot_sequence_end", "uint16", "zero_monitor", "Coherent zero-monitor snapshot end sequence."),
    _spec("MbAddr_PIDriveGain", "pi_drive_gain", "uint32", "measurement", "Non-negative saturated raw PI drive gain reported at 500 Hz.", unit="raw counts"),
)


COIL_SPECS = (
    _spec("MbAddr_ZERO_CALIBRATION_TRIGGER", "zero_calibration_start", "bool", "operation", "Write true to start zero calibration; firmware clears it when complete."),
    _spec("MbAddr_MASS_TOTAL_RESET_TRIGGER", "mass_total_reset", "bool", "operation", "Write true to clear accumulated mass; firmware clears it afterward."),
    _spec("MbAddr_VOLUME_TOTAL_RESET_TRIGGER", "volume_total_reset", "bool", "operation", "Write true to clear accumulated volume; firmware clears it afterward."),
)


def parse_address_defines(content: str) -> dict[str, int]:
    content = _strip_c_comments(content)
    return {
        symbol: int(raw_value, 0)
        for symbol, raw_value in ADDRESS_DEFINE_RE.findall(content)
    }


def parse_mapped_registers(content: str) -> dict[str, MappedRegister]:
    content = _strip_c_comments(content)
    access_entries: dict[str, tuple[int, str]] = {}
    for raw_width, symbol, access in ACCESS_RE.findall(content):
        value = (int(raw_width) // 16, access)
        if symbol in access_entries and access_entries[symbol] != value:
            raise ValueError(f"Conflicting access declarations for {symbol}.")
        access_entries[symbol] = value

    kind_entries: dict[str, tuple[int, str]] = {}
    for raw_kind, raw_width, symbol in KIND_RE.findall(content):
        value = (int(raw_width) // 16, raw_kind.lower())
        if symbol in kind_entries and kind_entries[symbol] != value:
            raise ValueError(f"Conflicting register-kind declarations for {symbol}.")
        kind_entries[symbol] = value

    if access_entries.keys() != kind_entries.keys():
        access_only = sorted(access_entries.keys() - kind_entries.keys())
        kind_only = sorted(kind_entries.keys() - access_entries.keys())
        raise ValueError(
            f"Access/type tables disagree; access-only={access_only}, kind-only={kind_only}."
        )

    result: dict[str, MappedRegister] = {}
    for symbol, (width, access) in access_entries.items():
        kind_width, kind = kind_entries[symbol]
        if width != kind_width:
            raise ValueError(f"Width declarations disagree for {symbol}.")
        result[symbol] = MappedRegister(width=width, access=access, kind=kind)
    return result


def _strip_c_comments(content: str) -> str:
    return C_COMMENT_RE.sub("", content)


def build_register_map_payload(dsp_root: Path) -> dict[str, Any]:
    dsp_root = Path(dsp_root).resolve()
    def_path = dsp_root / "module" / "modbus_handler" / "mb_map_def.h"
    map_path = dsp_root / "module" / "modbus_handler" / "mb_map.c"
    definitions = parse_address_defines(def_path.read_text(encoding="utf-8"))
    mapped = parse_mapped_registers(map_path.read_text(encoding="utf-8"))
    source_head = _source_head(dsp_root)

    modeled_symbols = {spec.symbol for spec in REGISTER_SPECS}
    missing_semantics = sorted(mapped.keys() - modeled_symbols)
    missing_mapping = sorted(modeled_symbols - mapped.keys())
    if missing_semantics or missing_mapping:
        raise ValueError(
            "DSP register map and client semantics disagree; "
            f"unmodeled={missing_semantics}, missing_from_dsp={missing_mapping}."
        )

    registers = [
        _register_payload(spec, definitions, mapped[spec.symbol], source_head)
        for spec in REGISTER_SPECS
    ]
    registers.extend(
        _coil_payload(spec, definitions, source_head) for spec in COIL_SPECS
    )
    return {
        "name": "krohne-prj-main",
        "version": f"1.0.0+{source_head[:7]}",
        "registers": registers,
    }


def _register_payload(
    spec: RegisterSpec,
    definitions: dict[str, int],
    mapped: MappedRegister,
    source_head: str,
) -> dict[str, Any]:
    expected_width = 1 if spec.data_type in {"uint16", "int16", "bool"} else 2
    if mapped.width != expected_width:
        raise ValueError(
            f"Semantic width for {spec.symbol} is {expected_width}, DSP maps {mapped.width}."
        )
    return _base_payload(
        spec,
        definitions,
        source_head,
        kind=mapped.kind,
        writable=mapped.access in {"RW", "WO"},
        native_access=mapped.access,
        readable_via=(
            ["input", "holding"] if mapped.kind == "input" else ["holding"]
        ),
    )


def _coil_payload(
    spec: RegisterSpec,
    definitions: dict[str, int],
    source_head: str,
) -> dict[str, Any]:
    return _base_payload(
        spec,
        definitions,
        source_head,
        kind="coil",
        writable=True,
        native_access="RW",
        readable_via=["coil"],
    )


def _base_payload(
    spec: RegisterSpec,
    definitions: dict[str, int],
    source_head: str,
    *,
    kind: str,
    writable: bool,
    native_access: str,
    readable_via: list[str],
) -> dict[str, Any]:
    if spec.symbol not in definitions:
        raise ValueError(f"Missing active DSP address definition: {spec.symbol}.")
    firmware_index = definitions[spec.symbol]
    if firmware_index < 1:
        raise ValueError(f"DSP callback index must be positive for {spec.symbol}.")
    metadata = {
        "address_basis": "zero-based-pdu",
        "firmware_map_index": f"0x{firmware_index:02X}",
        "firmware_symbol": spec.symbol,
        "group": spec.group,
        "native_access": native_access,
        "readable_via": readable_via,
        "source_file": "module/modbus_handler/mb_map.c",
        "source_head": source_head,
        "source_project": "Krohne_prj",
        **spec.metadata,
    }
    if writable:
        metadata["write_requires"] = (
            "separate guarded communication workflow"
            if spec.group == "communication"
            else "explicit guarded Modbus operation workflow"
        )
    return {
        "name": spec.name,
        "kind": kind,
        "address": firmware_index - 1,
        "word_count": 1 if spec.data_type in {"uint16", "int16", "bool"} else 2,
        "data_type": spec.data_type,
        "writable": writable,
        "scale": 1.0,
        "unit": spec.unit,
        "word_order": "big",
        "byte_order": "big",
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "description": spec.description,
        "metadata": metadata,
    }


def _source_head(dsp_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=dsp_root,
        check=True,
        capture_output=True,
        text=True,
    )
    source_head = result.stdout.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", source_head):
        raise ValueError(f"Unexpected DSP Git commit: {source_head!r}.")
    return source_head


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsp-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/register_maps/krohne_prj_main.json"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail when the checked-in JSON differs from current DSP source.",
    )
    args = parser.parse_args()
    payload = build_register_map_payload(args.dsp_root)
    rendered = json.dumps(payload, indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if not args.output.exists() or args.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"Register map is stale: {args.output}")
        print(f"Register map is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        f"Wrote {len(payload['registers'])} registers to {args.output} "
        f"from {payload['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

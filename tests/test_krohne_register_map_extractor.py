from __future__ import annotations

import json
from pathlib import Path

from scripts.extract_krohne_register_map import (
    parse_address_defines,
    parse_mapped_registers,
)

from coreflow.app.modbus_register_maps import (
    default_bundled_register_map_paths,
    official_record_from_path,
)
from coreflow.hardware.register_map import register_map_from_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAIN_MAP_PATH = PROJECT_ROOT / "config" / "register_maps" / "krohne_prj_main.json"


def test_extractor_parses_only_active_addresses_and_mapped_register_contracts() -> None:
    definitions = parse_address_defines(
        """
        // #define MbAddr_Disabled (0x20)
        #define MbAddr_MassFlow (7)
        #define MbAddr_Modbus_Status (0x37)
        """
    )
    mapped = parse_mapped_registers(
        """
        // MB_MAP_ACCESS_REG_16(MbAddr_Disabled, RW),
        // MB_MAP_HOLDING_REG_16(MbAddr_Disabled),
        MB_MAP_ACCESS_REG_32(MbAddr_MassFlow, RO),
        MB_MAP_ACCESS_REG_16(MbAddr_Modbus_Status, RO),
        MB_MAP_INPUT_REG_32(MbAddr_MassFlow),
        MB_MAP_HOLDING_REG_16(MbAddr_Modbus_Status),
        """
    )

    assert definitions == {"MbAddr_MassFlow": 7, "MbAddr_Modbus_Status": 55}
    assert mapped["MbAddr_MassFlow"].width == 2
    assert mapped["MbAddr_MassFlow"].access == "RO"
    assert mapped["MbAddr_MassFlow"].kind == "input"
    assert mapped["MbAddr_Modbus_Status"].width == 1
    assert mapped["MbAddr_Modbus_Status"].kind == "holding"
    assert "MbAddr_Disabled" not in mapped


def test_generated_main_map_covers_dsp_and_client_workflow_names() -> None:
    payload = json.loads(MAIN_MAP_PATH.read_text(encoding="utf-8"))
    register_map = register_map_from_json(json.dumps(payload))
    by_name = {item.name: item for item in register_map.registers}

    assert register_map.name == "krohne-prj-main"
    assert register_map.version == "1.0.0+f0a1b39"
    assert len(register_map.registers) == 59
    assert by_name["mass_flow"].address == 6
    assert by_name["mass_rate"].address == 6
    assert by_name["mass_rate"].metadata["alias_for"] == "mass_flow"
    assert by_name["mass_acc"].address == 10
    assert by_name["k_factor"].address == 24
    assert by_name["zero_calibration_start"].address == 16
    assert by_name["zero_snapshot_sequence_begin"].address == 95
    assert by_name["pi_drive_gain"].address == 113
    assert by_name["modbus_byte_order"].address == 52
    assert all(
        item.metadata["source_head"].startswith("f0a1b39")
        for item in register_map.registers
    )
    assert all(not item.metadata.get("placeholder", False) for item in register_map.registers)


def test_bundled_official_catalog_uses_complete_main_map() -> None:
    bundled = default_bundled_register_map_paths()
    names = {path.name for path in bundled}

    assert "krohne_prj_main.json" in names
    assert "krohne_prj_zero_monitor_abcd.json" not in names
    record = official_record_from_path(MAIN_MAP_PATH)
    assert record.register_map_id == "krohne-prj-main"
    assert record.version == "1.0.0+f0a1b39"
    assert len(record.register_map["registers"]) == 59

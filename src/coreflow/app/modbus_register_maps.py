"""Versioned Modbus register-map catalog helpers."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from coreflow.hardware.register_map import register_map_from_json, register_map_to_json
from coreflow.protocols.modbus import ModbusRegisterMap
from coreflow.storage.models import ModbusRegisterMapRecord


_REGISTER_MAP_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True, slots=True)
class ModbusRegisterMapCatalogEntry:
    """UI-ready immutable register-map catalog version."""

    register_map_id: str
    version: str
    display_name: str
    source: str
    checksum: str
    register_map: ModbusRegisterMap

    @property
    def label(self) -> str:
        return f"{self.display_name} ({self.register_map_id} @ {self.version})"


def validate_register_map_identity(register_map_id: str, version: str) -> tuple[str, str]:
    normalized_id = str(register_map_id).strip().lower()
    normalized_version = str(version).strip()
    if not normalized_id:
        raise ValueError("Register list ID is required.")
    if not _REGISTER_MAP_ID_PATTERN.fullmatch(normalized_id):
        raise ValueError(
            "Register list ID may contain only lowercase letters, numbers, '.', '_', and '-'."
        )
    if not normalized_version:
        raise ValueError("Register list version is required.")
    return normalized_id, normalized_version


def register_map_checksum(register_map: ModbusRegisterMap) -> str:
    """Hash register definitions independently from catalog ID/version labels."""

    payload = json.loads(register_map_to_json(register_map))
    canonical = json.dumps(
        payload["registers"],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def suggest_custom_register_map_id(register_map: ModbusRegisterMap) -> str:
    return f"custom-{register_map_checksum(register_map)[:16]}"


def register_map_record(
    *,
    register_map_id: str,
    version: str,
    display_name: str,
    source: str,
    register_map: ModbusRegisterMap,
) -> ModbusRegisterMapRecord:
    normalized_id, normalized_version = validate_register_map_identity(
        register_map_id,
        version,
    )
    return ModbusRegisterMapRecord(
        register_map_id=normalized_id,
        version=normalized_version,
        display_name=str(display_name).strip() or normalized_id,
        source=str(source).strip() or "custom",
        checksum=register_map_checksum(register_map),
        register_map=json.loads(register_map_to_json(register_map)),
    )


def catalog_entry_from_record(
    record: ModbusRegisterMapRecord,
) -> ModbusRegisterMapCatalogEntry:
    return ModbusRegisterMapCatalogEntry(
        register_map_id=record.register_map_id,
        version=record.version,
        display_name=record.display_name,
        source=record.source,
        checksum=record.checksum,
        register_map=register_map_from_json(json.dumps(record.register_map)),
    )


def default_bundled_register_map_paths() -> tuple[Path, ...]:
    """Find source or PyInstaller-bundled official register-map files."""

    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        roots.append(Path(bundle_root) / "config" / "register_maps")
    roots.append(Path(__file__).resolve().parents[3] / "config" / "register_maps")
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.json")):
            resolved = path.resolve()
            if resolved not in seen:
                paths.append(resolved)
                seen.add(resolved)
    return tuple(paths)


def official_record_from_path(path: Path) -> ModbusRegisterMapRecord:
    register_map = register_map_from_json(Path(path).read_text(encoding="utf-8"))
    register_map_id = _slug(register_map.name)
    return register_map_record(
        register_map_id=register_map_id,
        version=register_map.version,
        display_name=register_map.name,
        source="official",
        register_map=register_map,
    )


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", str(value).strip().lower())
    normalized = normalized.strip("-._")
    return normalized or "register-map"

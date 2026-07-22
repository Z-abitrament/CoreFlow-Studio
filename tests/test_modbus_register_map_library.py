from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest
from PySide6.QtCore import Qt

from coreflow.app.modbus_runtime import ModbusModuleRuntime
from coreflow.hardware import build_placeholder_register_map
from coreflow.hardware.register_map import register_map_to_json
from coreflow.protocols.modbus import ModbusRegisterMap
from coreflow.hardware import SerialPortScanner
from coreflow.storage import (
    Database,
    ModbusDeviceProfileRecord,
    ModbusRegisterMapRecord,
    StorageRepository,
)
from coreflow.ui.modbus_window import ModbusModuleWindow


def _payload(register_map: ModbusRegisterMap) -> dict[str, object]:
    return json.loads(register_map_to_json(register_map))


def test_schema_v6_migrates_and_deduplicates_inline_profile_maps(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    register_map = build_placeholder_register_map()
    payload = _payload(register_map)

    with database.connect() as connection:
        connection.execute("DELETE FROM schema_migrations WHERE version = 6")
        for suffix in ("A", "B"):
            connection.execute(
                """
                INSERT INTO modbus_device_profiles (
                    profile_id, device_id, register_map_json, created_at, updated_at
                ) VALUES (?, ?, ?, datetime('now'), datetime('now'))
                """,
                (f"profile:DEV-{suffix}", f"DEV-{suffix}", json.dumps(payload)),
            )

    database.initialize()
    repository = StorageRepository(database)
    profiles = repository.list_modbus_device_profiles()
    catalog = repository.list_modbus_register_maps()

    assert len(catalog) == 1
    assert catalog[0].source == "legacy"
    assert catalog[0].register_map == payload
    assert len({profile.register_map_id for profile in profiles}) == 1
    assert len({profile.register_map_version for profile in profiles}) == 1
    assert all(profile.register_map == payload for profile in profiles)
    with database.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) AS version FROM schema_migrations"
        ).fetchone()["version"]
    assert version == 6


def test_register_map_catalog_rejects_same_identity_with_different_content(
    tmp_path,
) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    register_map = build_placeholder_register_map()
    payload = _payload(register_map)
    repository.save_modbus_register_map(
        ModbusRegisterMapRecord(
            register_map_id="krohne_prj_main",
            version="1.0.0",
            display_name="Krohne Main",
            source="official",
            checksum="checksum-a",
            register_map=payload,
        )
    )

    changed_payload = dict(payload)
    changed_payload["registers"] = list(payload["registers"])[1:]
    with pytest.raises(ValueError, match="already exists with different content"):
        repository.save_modbus_register_map(
            ModbusRegisterMapRecord(
                register_map_id="krohne_prj_main",
                version="1.0.0",
                display_name="Krohne Main",
                source="official",
                checksum="checksum-b",
                register_map=changed_payload,
            )
        )

    assert repository.get_modbus_register_map(
        "krohne_prj_main", "1.0.0"
    ).register_map == payload


def test_runtime_shares_map_binding_and_versions_only_edited_profile(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    runtime = ModbusModuleRuntime(repository, bundled_register_map_paths=())
    register_map = build_placeholder_register_map()

    first = runtime.save_device_profile(
        device_id="DEV-A",
        register_map=register_map,
        register_map_id="krohne_prj_main",
        register_map_version="1.0.0",
        register_map_display_name="Krohne Main",
    )
    second = runtime.save_device_profile(
        device_id="DEV-B",
        register_map=register_map,
        register_map_id="krohne_prj_main",
        register_map_version="1.0.0",
        register_map_display_name="Krohne Main",
    )

    assert first.register_map_id == second.register_map_id == "krohne_prj_main"
    assert first.register_map_version == second.register_map_version == "1.0.0"

    changed_register = replace(register_map.by_name("mass_acc"), address=188)
    changed_map = ModbusRegisterMap(
        name=register_map.name,
        version=register_map.version,
        registers=tuple(
            changed_register if item.name == "mass_acc" else item
            for item in register_map.registers
        ),
    )
    edited = runtime.save_device_profile(
        device_id="DEV-A",
        register_map=changed_map,
    )
    unchanged = runtime.get_device_profile("DEV-B")

    assert edited.register_map_id == "krohne_prj_main"
    assert edited.register_map_version != "1.0.0"
    assert edited.register_map.by_name("mass_acc").address == 188
    assert unchanged is not None
    assert unchanged.register_map_version == "1.0.0"
    assert unchanged.register_map.by_name("mass_acc").address != 188


def test_runtime_installs_bundled_map_without_rebinding_existing_profile(
    tmp_path,
) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    register_map = build_placeholder_register_map()
    repository.save_modbus_device_profile(
        ModbusDeviceProfileRecord(
            profile_id="profile:DEV-BOUND",
            device_id="DEV-BOUND",
            register_map=_payload(register_map),
        )
    )
    database.initialize()
    before = repository.get_modbus_device_profile("DEV-BOUND")
    assert before is not None

    bundled = tmp_path / "official.json"
    bundled.write_text(register_map_to_json(register_map), encoding="utf-8")
    runtime = ModbusModuleRuntime(
        repository,
        bundled_register_map_paths=(bundled,),
    )
    after = repository.get_modbus_device_profile("DEV-BOUND")

    assert after is not None
    assert (after.register_map_id, after.register_map_version) == (
        before.register_map_id,
        before.register_map_version,
    )
    assert any(entry.source == "official" for entry in runtime.list_register_maps())


def test_official_map_is_bindable_but_requires_clone_before_edit(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    register_map = build_placeholder_register_map()
    bundled = tmp_path / "official.json"
    bundled.write_text(register_map_to_json(register_map), encoding="utf-8")
    runtime = ModbusModuleRuntime(
        repository,
        bundled_register_map_paths=(bundled,),
    )
    official = next(entry for entry in runtime.list_register_maps() if entry.source == "official")

    profile = runtime.save_device_profile(
        device_id="DEV-OFFICIAL",
        register_map=official.register_map,
        register_map_id=official.register_map_id,
        register_map_version=official.version,
    )
    snapshot = runtime._register_map_snapshot()

    assert profile.register_map_source == "official"
    assert snapshot["register_map_id"] == official.register_map_id
    assert snapshot["register_map_catalog_version"] == official.version
    changed = ModbusRegisterMap(
        name=official.register_map.name,
        version=official.register_map.version,
        registers=(
            replace(official.register_map.registers[0], address=999),
            *official.register_map.registers[1:],
        ),
    )
    with pytest.raises(ValueError, match="Official register lists are immutable"):
        runtime.save_device_profile(
            device_id="DEV-OFFICIAL",
            register_map=changed,
        )


def test_schema_v6_rejects_database_newer_than_supported(tmp_path) -> None:
    path = tmp_path / "future.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)"
    )
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (7, datetime('now'))"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="newer than supported version 6"):
        Database(path).initialize()


def test_device_profile_ui_selects_shared_list_and_versions_only_edited_device(
    qtbot,
    tmp_path,
) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    runtime = ModbusModuleRuntime(repository, bundled_register_map_paths=())
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=SerialPortScanner(provider=lambda: ()),
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.createDeviceProfileButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible()
    )
    first_dialog = window.deviceProfileDialog
    assert first_dialog is not None
    assert first_dialog.registerMapCombo.currentData() is None
    first_dialog.deviceIdLineEdit.setText("DEV-UI-A")
    first_dialog.registerMapIdLineEdit.setText("shared_main")
    first_dialog.registerMapNameLineEdit.setText("Shared Main")
    first_dialog.registerMapVersionLineEdit.setText("1.0.0")
    qtbot.mouseClick(first_dialog.saveButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: runtime.get_device_profile("DEV-UI-A") is not None)

    qtbot.mouseClick(window.createDeviceProfileButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible()
    )
    second_dialog = window.deviceProfileDialog
    assert second_dialog is not None
    second_dialog.deviceIdLineEdit.setText("DEV-UI-B")
    index = second_dialog.registerMapCombo.findData("shared_main@1.0.0")
    assert index >= 0
    second_dialog.registerMapCombo.setCurrentIndex(index)
    assert second_dialog.registerMapIdLineEdit.text() == "shared_main"
    assert second_dialog.registerMapIdLineEdit.isReadOnly()
    qtbot.mouseClick(second_dialog.saveButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: runtime.get_device_profile("DEV-UI-B") is not None)

    first_index = window.deviceProfileCombo.findData("DEV-UI-A")
    window.deviceProfileCombo.setCurrentIndex(first_index)
    qtbot.mouseClick(window.editDeviceProfileButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible()
    )
    edit_dialog = window.deviceProfileDialog
    assert edit_dialog is not None
    mass_acc_row = next(
        row
        for row in range(edit_dialog.mapTable.rowCount())
        if edit_dialog.mapTable.item(row, 0).text() == "mass_acc"
    )
    edit_dialog.mapTable.item(mass_acc_row, 2).setText("188")
    qtbot.mouseClick(edit_dialog.previewMapChangesButton, Qt.MouseButton.LeftButton)
    assert "modified=1" in edit_dialog.statusLabel.text()
    qtbot.mouseClick(edit_dialog.saveButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: runtime.get_device_profile("DEV-UI-A").register_map_version
        != "1.0.0"
    )

    first = runtime.get_device_profile("DEV-UI-A")
    second = runtime.get_device_profile("DEV-UI-B")
    assert first is not None and second is not None
    assert first.register_map_id == second.register_map_id == "shared_main"
    assert first.register_map_version != second.register_map_version
    assert second.register_map.by_name("mass_acc").address != 188


def test_device_profile_ui_create_new_option_unlocks_identity(qtbot, tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    register_map = build_placeholder_register_map()
    runtime = ModbusModuleRuntime(repository, bundled_register_map_paths=())
    runtime.save_device_profile(
        device_id="DEV-EXISTING",
        register_map=register_map,
        register_map_id="shared_main",
        register_map_version="1.0.0",
        register_map_display_name="Shared Main",
    )
    window = ModbusModuleWindow(
        repository,
        runtime=runtime,
        port_scanner=SerialPortScanner(provider=lambda: ()),
        data_root=tmp_path,
    )
    qtbot.addWidget(window)
    window.show()

    qtbot.mouseClick(window.createDeviceProfileButton, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: window.deviceProfileDialog is not None
        and window.deviceProfileDialog.isVisible()
    )
    dialog = window.deviceProfileDialog
    assert dialog is not None
    shared_index = dialog.registerMapCombo.findData("shared_main@1.0.0")
    dialog.registerMapCombo.setCurrentIndex(shared_index)
    assert dialog.registerMapIdLineEdit.isReadOnly()

    dialog.registerMapCombo.setCurrentIndex(0)

    assert not dialog.registerMapIdLineEdit.isReadOnly()
    assert dialog.registerMapCombo.currentData() is None
    assert dialog.registerMapVersionLineEdit.text() == "1.0.0"

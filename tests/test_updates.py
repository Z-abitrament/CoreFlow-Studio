from __future__ import annotations

import hashlib
import json
import subprocess
import time
import zipfile

import coreflow.app.updates as updates

from coreflow.app.updates import (
    UpdateCheckResult,
    UpdatePackage,
    UpdateService,
    UpdateSettings,
    compare_versions,
    create_full_update_package,
    create_patch_update_package,
    create_update_release_assets,
    file_sha256,
    parse_update_manifest,
    select_update_package,
)


def test_updater_waits_for_all_coreflow_executable_locks_to_clear() -> None:
    script = updates._UPDATER_SCRIPT

    assert "function Wait-ForInstallUnlock" in script
    assert "[System.IO.FileShare]::None" in script
    assert "CoreFlowStudio.exe remains in use" in script
    assert "Update aborted:" in script
    assert "Wait-ForInstallUnlock -InstallDir $installPath.Path" in script


def test_updater_waits_for_locked_executable_then_applies_patch(tmp_path) -> None:
    install_dir = tmp_path / "CoreFlowStudio"
    install_dir.mkdir()
    executable = install_dir / "CoreFlowStudio.exe"
    executable.write_bytes(b"old executable")
    updated_executable = b"updated executable"

    package_path = tmp_path / "update.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(
            "coreflow_patch_manifest.json",
            json.dumps(
                {
                    "package_type": "patch",
                    "changed_files": [
                        {
                            "path": "CoreFlowStudio.exe",
                            "sha256": hashlib.sha256(updated_executable).hexdigest(),
                        }
                    ],
                    "deleted_files": [],
                }
            ),
        )
        archive.writestr("CoreFlowStudio/CoreFlowStudio.exe", updated_executable)

    script_path = tmp_path / "apply_update.ps1"
    script_path.write_text(updates._UPDATER_SCRIPT, encoding="utf-8")
    log_path = tmp_path / "update.log"
    lock_command = (
        "$handle = [System.IO.File]::Open("
        f"'{executable}', "
        "[System.IO.FileMode]::Open, "
        "[System.IO.FileAccess]::Read, "
        "[System.IO.FileShare]::Read); "
        "Start-Sleep -Seconds 2; $handle.Dispose()"
    )
    locker = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command", lock_command]
    )
    time.sleep(0.2)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-PackageZip",
                str(package_path),
                "-InstallDir",
                str(install_dir),
                "-RestartExe",
                str(tmp_path / "not-started.exe"),
                "-ExpectedSha256",
                file_sha256(package_path),
                "-LogPath",
                str(log_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        locker.wait(timeout=10)

    assert completed.returncode == 0, completed.stderr
    assert executable.read_bytes() == updated_executable
    log_text = log_path.read_text(encoding="utf-8")
    assert "Waiting for CoreFlowStudio.exe to be released" in log_text
    assert "Patch update installed successfully." in log_text


def test_update_manifest_selects_patch_then_full() -> None:
    manifest = parse_update_manifest(
        json.dumps(
            {
                "latest_version": "0.6.0",
                "release_notes": "New update flow",
                "packages": [
                    {
                        "type": "full",
                        "to_version": "0.6.0",
                        "url": "CoreFlowStudio-0.6.0-full.zip",
                        "sha256": "a" * 64,
                    },
                    {
                        "type": "patch",
                        "from_version": "0.5.2",
                        "to_version": "0.6.0",
                        "url": "CoreFlowStudio-0.5.2-to-0.6.0.zip",
                        "sha256": "b" * 64,
                    },
                ],
            }
        )
    )

    assert compare_versions("0.6.0", "0.5.2") > 0
    assert compare_versions("v0.6.0", "0.6.0") == 0
    selected = select_update_package(manifest, current_version="0.5.2")
    assert selected is not None
    assert selected.package_type == "patch"
    assert selected.from_version == "0.5.2"
    fallback = select_update_package(manifest, current_version="0.4.0")
    assert fallback is not None
    assert fallback.package_type == "full"


def test_update_service_checks_and_downloads_file_manifest(tmp_path) -> None:
    package_path = tmp_path / "CoreFlowStudio-0.6.0-full.zip"
    package_path.write_bytes(b"fake update package")
    manifest_path = tmp_path / "latest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "latest_version": "0.6.0",
                "release_notes": "Release notes",
                "packages": [
                    {
                        "type": "full",
                        "to_version": "0.6.0",
                        "url": package_path.as_uri(),
                        "sha256": file_sha256(package_path),
                        "size_bytes": package_path.stat().st_size,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(data_root=tmp_path / "data", current_version="0.5.2")
    service.save_settings(UpdateSettings(manifest_url=manifest_path.as_uri()))

    check = service.check_for_updates(service.load_settings().manifest_url)
    assert check.update_available is True
    assert check.latest_version == "0.6.0"
    assert check.package is not None

    progress: list[tuple[int, int | None]] = []
    downloaded = service.download_update(
        check,
        progress_callback=lambda current, total: progress.append((current, total)),
    )

    assert downloaded.package_path.exists()
    assert downloaded.package_path.read_bytes() == b"fake update package"
    assert progress


def test_update_service_rejects_bad_checksum(tmp_path) -> None:
    package_path = tmp_path / "CoreFlowStudio-0.6.0-full.zip"
    package_path.write_bytes(b"fake update package")
    service = UpdateService(data_root=tmp_path / "data", current_version="0.5.2")
    check = UpdateCheckResult(
        current_version="0.5.2",
        latest_version="0.6.0",
        update_available=True,
        package=UpdatePackage(
            package_type="full",
            to_version="0.6.0",
            url=package_path.as_uri(),
            sha256="0" * 64,
        ),
        manifest_url=(tmp_path / "latest.json").as_uri(),
    )

    try:
        service.download_update(check)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("bad checksum should fail")


def test_create_full_update_package_writes_zip_and_manifest(tmp_path) -> None:
    dist_dir = tmp_path / "CoreFlowStudio"
    internal = dist_dir / "_internal"
    internal.mkdir(parents=True)
    (dist_dir / "CoreFlowStudio.exe").write_bytes(b"exe")
    (dist_dir / "CoreFlowStudioConsole.exe").write_bytes(b"console")
    (internal / "dependency.dll").write_bytes(b"dll")

    zip_path, manifest_path = create_full_update_package(
        dist_dir=dist_dir,
        output_dir=tmp_path / "updates",
        version="0.6.0",
        base_url="https://github.com/acme/CoreFlowStudio/releases/download/v0.6.0",
    )

    assert zip_path.exists()
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["latest_version"] == "0.6.0"
    assert manifest["packages"][0]["sha256"] == file_sha256(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        assert "CoreFlowStudio/CoreFlowStudio.exe" in archive.namelist()
        assert "CoreFlowStudio/_internal/dependency.dll" in archive.namelist()


def test_create_patch_update_package_writes_only_changed_files(tmp_path) -> None:
    previous_dir = tmp_path / "previous" / "CoreFlowStudio"
    current_dir = tmp_path / "current" / "CoreFlowStudio"
    (previous_dir / "_internal").mkdir(parents=True)
    (current_dir / "_internal").mkdir(parents=True)
    (previous_dir / "CoreFlowStudio.exe").write_bytes(b"old exe")
    (current_dir / "CoreFlowStudio.exe").write_bytes(b"new exe")
    (previous_dir / "README.md").write_text("same", encoding="utf-8")
    (current_dir / "README.md").write_text("same", encoding="utf-8")
    (previous_dir / "_internal" / "old.dll").write_bytes(b"remove me")
    (current_dir / "_internal" / "new.dll").write_bytes(b"add me")

    zip_path, package = create_patch_update_package(
        previous_dist_dir=previous_dir,
        dist_dir=current_dir,
        output_dir=tmp_path / "updates",
        from_version="0.6.1",
        to_version="0.6.2",
        base_url="https://github.com/acme/CoreFlowStudio/releases/download/v0.6.2",
    )

    assert package.package_type == "patch"
    assert package.from_version == "0.6.1"
    assert package.to_version == "0.6.2"
    assert package.sha256 == file_sha256(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        assert "coreflow_patch_manifest.json" in names
        assert "CoreFlowStudio/CoreFlowStudio.exe" in names
        assert "CoreFlowStudio/_internal/new.dll" in names
        assert "CoreFlowStudio/README.md" not in names
        manifest = json.loads(archive.read("coreflow_patch_manifest.json"))
    assert manifest["from_version"] == "0.6.1"
    assert manifest["to_version"] == "0.6.2"
    assert [entry["path"] for entry in manifest["changed_files"]] == [
        "CoreFlowStudio.exe",
        "_internal/new.dll",
    ]
    assert manifest["deleted_files"] == ["_internal/old.dll"]


def test_create_update_release_assets_skips_patch_for_legacy_source(tmp_path) -> None:
    previous_dir = tmp_path / "previous" / "CoreFlowStudio"
    current_dir = tmp_path / "current" / "CoreFlowStudio"
    previous_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    (previous_dir / "CoreFlowStudio.exe").write_bytes(b"old")
    (current_dir / "CoreFlowStudio.exe").write_bytes(b"new")

    result = create_update_release_assets(
        previous_dist_dir=previous_dir,
        dist_dir=current_dir,
        output_dir=tmp_path / "updates",
        previous_version="0.6.0",
        version="0.6.1",
        base_url="https://github.com/acme/CoreFlowStudio/releases/download/v0.6.1",
    )

    assert result.full_zip_path.exists()
    assert result.patch_zip_path is None
    assert "older than 0.6.1" in result.skipped_patch_reason
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert [package["type"] for package in manifest["packages"]] == ["full"]


def test_create_update_release_assets_includes_patch_for_supported_source(
    tmp_path,
) -> None:
    previous_dir = tmp_path / "previous" / "CoreFlowStudio"
    current_dir = tmp_path / "current" / "CoreFlowStudio"
    previous_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    (previous_dir / "CoreFlowStudio.exe").write_bytes(b"old")
    (current_dir / "CoreFlowStudio.exe").write_bytes(b"new")

    result = create_update_release_assets(
        previous_dist_dir=previous_dir,
        dist_dir=current_dir,
        output_dir=tmp_path / "updates",
        previous_version="0.6.1",
        version="0.6.2",
        base_url="https://github.com/acme/CoreFlowStudio/releases/download/v0.6.2",
    )

    assert result.full_zip_path.exists()
    assert result.patch_zip_path is not None
    assert result.patch_zip_path.exists()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert [package["type"] for package in manifest["packages"]] == ["patch", "full"]
    assert manifest["packages"][0]["from_version"] == "0.6.1"


def test_create_update_release_assets_rejects_non_upgrade_patch(tmp_path) -> None:
    previous_dir = tmp_path / "previous" / "CoreFlowStudio"
    current_dir = tmp_path / "current" / "CoreFlowStudio"
    previous_dir.mkdir(parents=True)
    current_dir.mkdir(parents=True)
    (previous_dir / "CoreFlowStudio.exe").write_bytes(b"old")
    (current_dir / "CoreFlowStudio.exe").write_bytes(b"new")

    try:
        create_update_release_assets(
            previous_dist_dir=previous_dir,
            dist_dir=current_dir,
            output_dir=tmp_path / "updates",
            previous_version="0.6.1",
            version="0.6.1",
            base_url="https://github.com/acme/CoreFlowStudio/releases/download/v0.6.1",
        )
    except ValueError as exc:
        assert "older than the new version" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("non-upgrade patch generation should fail")

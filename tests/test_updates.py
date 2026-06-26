from __future__ import annotations

import json
import zipfile

from coreflow.app.updates import (
    UpdateCheckResult,
    UpdatePackage,
    UpdateService,
    UpdateSettings,
    compare_versions,
    create_full_update_package,
    file_sha256,
    parse_update_manifest,
    select_update_package,
)


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

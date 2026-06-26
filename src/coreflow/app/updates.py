"""Online update helpers for packaged CoreFlow Studio deployments."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from coreflow import __version__


UPDATE_SETTINGS_ENV = "COREFLOW_UPDATE_MANIFEST_URL"
PATCH_MANIFEST_NAME = "coreflow_patch_manifest.json"
PATCH_INSTALLER_MIN_VERSION = "0.6.1"


@dataclass(frozen=True, slots=True)
class UpdateAssetBuildResult:
    """Release assets created for a GitHub Release update."""

    full_zip_path: Path
    manifest_path: Path
    patch_zip_path: Path | None = None
    skipped_patch_reason: str = ""


@dataclass(frozen=True, slots=True)
class UpdatePackage:
    """One downloadable update package described by a release manifest."""

    package_type: str
    to_version: str
    url: str
    sha256: str
    size_bytes: int | None = None
    from_version: str | None = None
    file_name: str | None = None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class UpdateManifest:
    """Parsed update manifest downloaded from GitHub Release or another host."""

    latest_version: str
    packages: tuple[UpdatePackage, ...]
    release_notes: str = ""
    generated_at: str = ""
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """Result of comparing the current installation with a manifest."""

    current_version: str
    latest_version: str
    update_available: bool
    package: UpdatePackage | None
    manifest_url: str
    release_notes: str = ""


@dataclass(frozen=True, slots=True)
class UpdateSettings:
    """User-editable update settings."""

    manifest_url: str = ""


@dataclass(frozen=True, slots=True)
class DownloadedUpdate:
    """A downloaded and checksum-verified update package."""

    package_path: Path
    package: UpdatePackage
    checked_at: datetime


def _validate_patch_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or value.startswith("/")
        or value.startswith("\\")
    ):
        raise ValueError(f"Unsafe patch path: {value}")


class UpdateSettingsStore:
    """Persist update settings under the normal user data directory."""

    def __init__(self, data_root: Path) -> None:
        self._path = data_root / "config" / "updates.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> UpdateSettings:
        env_url = os.environ.get(UPDATE_SETTINGS_ENV, "").strip()
        if env_url:
            return UpdateSettings(manifest_url=env_url)
        if not self._path.exists():
            return UpdateSettings()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return UpdateSettings()
        if not isinstance(data, dict):
            return UpdateSettings()
        return UpdateSettings(manifest_url=str(data.get("manifest_url") or "").strip())

    def save(self, settings: UpdateSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"manifest_url": settings.manifest_url}, indent=2),
            encoding="utf-8",
        )


class UpdateService:
    """Check, download, and stage updates for the desktop UI."""

    def __init__(
        self,
        *,
        data_root: Path,
        current_version: str = __version__,
        timeout_s: float = 20.0,
    ) -> None:
        self.data_root = Path(data_root)
        self.current_version = current_version
        self.timeout_s = timeout_s
        self.settings_store = UpdateSettingsStore(self.data_root)

    def load_settings(self) -> UpdateSettings:
        return self.settings_store.load()

    def save_settings(self, settings: UpdateSettings) -> None:
        self.settings_store.save(settings)

    def check_for_updates(self, manifest_url: str) -> UpdateCheckResult:
        manifest_url = manifest_url.strip()
        if not manifest_url:
            raise ValueError("Enter the GitHub Release latest.json URL first.")
        manifest = parse_update_manifest(
            _read_url_bytes(manifest_url, timeout_s=self.timeout_s)
        )
        update_available = compare_versions(
            manifest.latest_version,
            self.current_version,
        ) > 0
        package = (
            select_update_package(
                manifest,
                current_version=self.current_version,
            )
            if update_available
            else None
        )
        if update_available and package is None:
            raise ValueError(
                "The update manifest does not contain a full package or a "
                "matching patch for this version."
            )
        return UpdateCheckResult(
            current_version=self.current_version,
            latest_version=manifest.latest_version,
            update_available=update_available,
            package=package,
            manifest_url=manifest_url,
            release_notes=manifest.release_notes,
        )

    def download_update(
        self,
        check_result: UpdateCheckResult,
        *,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> DownloadedUpdate:
        package = check_result.package
        if package is None:
            raise ValueError("No update package is available to download.")
        package_url = resolve_package_url(check_result.manifest_url, package.url)
        file_name = _safe_download_name(package, package_url)
        target_dir = self.data_root / "updates" / "downloads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / file_name
        part_path = target_dir / f"{file_name}.part"
        if part_path.exists():
            part_path.unlink()
        _download_url(
            package_url,
            part_path,
            timeout_s=self.timeout_s,
            progress_callback=progress_callback,
        )
        digest = file_sha256(part_path)
        expected = package.sha256.lower()
        if digest.lower() != expected:
            part_path.unlink(missing_ok=True)
            raise ValueError(
                "Downloaded update checksum mismatch: "
                f"expected {expected}, got {digest}."
            )
        if target_path.exists():
            target_path.unlink()
        part_path.replace(target_path)
        return DownloadedUpdate(
            package_path=target_path,
            package=package,
            checked_at=datetime.now(UTC),
        )

    def can_install_update(self) -> bool:
        return is_packaged_app()

    def install_downloaded_update(self, downloaded: DownloadedUpdate) -> None:
        if not self.can_install_update():
            raise RuntimeError(
                "Update installation is available only from the packaged app."
            )
        install_dir = current_install_dir()
        restart_exe = install_dir / "CoreFlowStudio.exe"
        script_path = self.write_updater_script()
        log_path = self.data_root / "updates" / "update.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-PackageZip",
            str(downloaded.package_path),
            "-InstallDir",
            str(install_dir),
            "-RestartExe",
            str(restart_exe),
            "-WaitForPid",
            str(os.getpid()),
            "-ExpectedSha256",
            downloaded.package.sha256,
            "-LogPath",
            str(log_path),
        ]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(  # noqa: S603 - command is fixed, arguments are explicit.
            command,
            cwd=str(self.data_root),
            startupinfo=startupinfo,
            creationflags=creationflags,
        )

    def write_updater_script(self) -> Path:
        script_path = self.data_root / "updates" / "apply_coreflow_update.ps1"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(_UPDATER_SCRIPT, encoding="utf-8")
        return script_path


def parse_update_manifest(payload: bytes | str) -> UpdateManifest:
    """Parse and validate a release update manifest."""

    data = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    if not isinstance(data, dict):
        raise ValueError("Update manifest root must be an object.")
    latest_version = str(data.get("latest_version") or "").strip()
    if not latest_version:
        raise ValueError("Update manifest is missing latest_version.")
    raw_packages = data.get("packages")
    if not isinstance(raw_packages, list):
        raise ValueError("Update manifest packages must be a list.")
    packages: list[UpdatePackage] = []
    for index, raw in enumerate(raw_packages, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"Update package #{index} must be an object.")
        package_type = str(raw.get("type") or "").strip().lower()
        to_version = str(raw.get("to_version") or latest_version).strip()
        url = str(raw.get("url") or "").strip()
        sha256 = str(raw.get("sha256") or "").strip().lower()
        if package_type not in {"full", "patch"}:
            raise ValueError(f"Update package #{index} has unsupported type.")
        if not to_version or not url or not sha256:
            raise ValueError(f"Update package #{index} is missing required fields.")
        packages.append(
            UpdatePackage(
                package_type=package_type,
                to_version=to_version,
                url=url,
                sha256=sha256,
                size_bytes=_optional_int(raw.get("size_bytes")),
                from_version=(
                    str(raw.get("from_version")).strip()
                    if raw.get("from_version") is not None
                    else None
                ),
                file_name=(
                    str(raw.get("file_name")).strip()
                    if raw.get("file_name") is not None
                    else None
                ),
                notes=str(raw.get("notes") or ""),
            )
        )
    return UpdateManifest(
        latest_version=latest_version,
        packages=tuple(packages),
        release_notes=str(data.get("release_notes") or ""),
        generated_at=str(data.get("generated_at") or ""),
        schema_version=int(data.get("schema_version") or 1),
    )


def select_update_package(
    manifest: UpdateManifest,
    *,
    current_version: str,
) -> UpdatePackage | None:
    """Prefer an exact patch, then fall back to a full package."""

    exact_patches = [
        package
        for package in manifest.packages
        if package.package_type == "patch"
        and package.from_version == current_version
        and package.to_version == manifest.latest_version
    ]
    if exact_patches:
        return exact_patches[0]
    full_packages = [
        package
        for package in manifest.packages
        if package.package_type == "full"
        and package.to_version == manifest.latest_version
    ]
    return full_packages[0] if full_packages else None


def compare_versions(left: str, right: str) -> int:
    """Compare simple semantic version strings without adding a dependency."""

    left_parts = _version_parts(left)
    right_parts = _version_parts(right)
    max_len = max(len(left_parts), len(right_parts))
    left_parts.extend([0] * (max_len - len(left_parts)))
    right_parts.extend([0] * (max_len - len(right_parts)))
    return (left_parts > right_parts) - (left_parts < right_parts)


def resolve_package_url(manifest_url: str, package_url: str) -> str:
    return urllib.parse.urljoin(manifest_url, package_url)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_packaged_app() -> bool:
    return bool(getattr(sys, "frozen", False)) or os.environ.get("COREFLOW_PACKAGED") == "1"


def current_install_dir() -> Path:
    if is_packaged_app():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def _read_url_bytes(url: str, *, timeout_s: float) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme in {"http", "https", "file"}:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
            return response.read()
    return Path(url).read_bytes()


def _download_url(
    url: str,
    target_path: Path,
    *,
    timeout_s: float,
    progress_callback: Callable[[int, int | None], None] | None,
) -> None:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310
        total = _optional_int(response.headers.get("Content-Length"))
        downloaded = 0
        with target_path.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded, total)
    if progress_callback is not None:
        progress_callback(target_path.stat().st_size, target_path.stat().st_size)


def _safe_download_name(package: UpdatePackage, package_url: str) -> str:
    raw_name = package.file_name or Path(urllib.parse.urlparse(package_url).path).name
    safe_name = "".join(
        character
        if character.isascii()
        and (character.isalnum() or character in {"-", "_", "."})
        else "_"
        for character in raw_name
    ).strip("._")
    return safe_name or f"CoreFlowStudio-{package.to_version}-{package.package_type}.zip"


def _version_parts(value: str) -> list[int]:
    text = value.strip().lower()
    if text.startswith("v"):
        text = text[1:]
    parts: list[int] = []
    for token in text.replace("-", ".").replace("+", ".").split("."):
        digits = "".join(character for character in token if character.isdigit())
        if not digits:
            parts.append(0)
        else:
            parts.append(int(digits))
    return parts or [0]


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def create_full_update_package(
    *,
    dist_dir: Path,
    output_dir: Path,
    version: str,
    base_url: str,
    package_name: str | None = None,
) -> tuple[Path, Path]:
    """Create a full GitHub Release update zip and latest.json manifest."""

    output_dir = Path(output_dir)
    zip_path, package = _create_full_update_zip(
        dist_dir=dist_dir,
        output_dir=output_dir,
        version=version,
        base_url=base_url,
        package_name=package_name,
    )
    manifest_path = output_dir / "latest.json"
    _write_latest_manifest(
        manifest_path,
        latest_version=version,
        packages=(package,),
    )
    return zip_path, manifest_path


def create_update_release_assets(
    *,
    dist_dir: Path,
    output_dir: Path,
    version: str,
    base_url: str,
    previous_version: str | None = None,
    previous_dist_dir: Path | None = None,
    previous_package: Path | None = None,
) -> UpdateAssetBuildResult:
    """Create full and optional patch update assets for a GitHub Release."""

    if previous_dist_dir is not None and previous_package is not None:
        raise ValueError("Use either previous_dist_dir or previous_package, not both.")
    if (previous_dist_dir is not None or previous_package is not None) and not previous_version:
        raise ValueError("previous_version is required when building a patch package.")
    if previous_version and compare_versions(previous_version, version) >= 0:
        raise ValueError("previous_version must be older than the new version.")

    output_dir = Path(output_dir)
    full_zip_path, full_package = _create_full_update_zip(
        dist_dir=dist_dir,
        output_dir=output_dir,
        version=version,
        base_url=base_url,
    )
    packages: list[UpdatePackage] = [full_package]
    patch_zip_path: Path | None = None
    skipped_patch_reason = ""

    if previous_version is not None:
        if previous_dist_dir is None and previous_package is None:
            skipped_patch_reason = (
                "Patch package skipped because no previous dist folder or "
                "previous full update package was provided."
            )
        elif compare_versions(previous_version, PATCH_INSTALLER_MIN_VERSION) < 0:
            skipped_patch_reason = (
                "Patch package skipped because source versions older than "
                f"{PATCH_INSTALLER_MIN_VERSION} cannot apply patch updates safely."
            )
        elif previous_dist_dir is not None:
            patch_zip_path, patch_package = create_patch_update_package(
                previous_dist_dir=previous_dist_dir,
                dist_dir=dist_dir,
                output_dir=output_dir,
                from_version=previous_version,
                to_version=version,
                base_url=base_url,
            )
            packages.insert(0, patch_package)
        elif previous_package is not None:
            with _extracted_update_package(previous_package) as extracted_dist_dir:
                patch_zip_path, patch_package = create_patch_update_package(
                    previous_dist_dir=extracted_dist_dir,
                    dist_dir=dist_dir,
                    output_dir=output_dir,
                    from_version=previous_version,
                    to_version=version,
                    base_url=base_url,
                )
            packages.insert(0, patch_package)

    manifest_path = Path(output_dir) / "latest.json"
    _write_latest_manifest(
        manifest_path,
        latest_version=version,
        packages=tuple(packages),
    )
    return UpdateAssetBuildResult(
        full_zip_path=full_zip_path,
        manifest_path=manifest_path,
        patch_zip_path=patch_zip_path,
        skipped_patch_reason=skipped_patch_reason,
    )


def create_patch_update_package(
    *,
    previous_dist_dir: Path,
    dist_dir: Path,
    output_dir: Path,
    from_version: str,
    to_version: str,
    base_url: str,
    package_name: str | None = None,
) -> tuple[Path, UpdatePackage]:
    """Create a file-level patch package between two packaged dist folders."""

    import zipfile

    if compare_versions(from_version, PATCH_INSTALLER_MIN_VERSION) < 0:
        raise ValueError(
            "Patch updates require source version "
            f"{PATCH_INSTALLER_MIN_VERSION} or newer."
        )
    if compare_versions(from_version, to_version) >= 0:
        raise ValueError("from_version must be older than to_version.")
    previous_dist_dir = Path(previous_dist_dir)
    dist_dir = Path(dist_dir)
    output_dir = Path(output_dir)
    _ensure_dist_dir(previous_dist_dir)
    _ensure_dist_dir(dist_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    previous_files = _package_file_map(previous_dist_dir)
    new_files = _package_file_map(dist_dir)
    changed_files: list[tuple[str, Path]] = []
    for relative, new_path in sorted(new_files.items()):
        _validate_patch_relative_path(relative)
        old_path = previous_files.get(relative)
        if old_path is None or file_sha256(old_path) != file_sha256(new_path):
            changed_files.append((relative, new_path))
    deleted_files = sorted(set(previous_files) - set(new_files))
    for relative in deleted_files:
        _validate_patch_relative_path(relative)
    if not changed_files and not deleted_files:
        raise ValueError("Patch package would contain no file changes.")

    name = package_name or f"CoreFlowStudio-{from_version}-to-{to_version}-patch.zip"
    zip_path = output_dir / name
    if zip_path.exists():
        zip_path.unlink()
    patch_manifest = {
        "schema_version": 1,
        "package_type": "patch",
        "from_version": from_version,
        "to_version": to_version,
        "changed_files": [
            {
                "path": relative,
                "sha256": file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
            for relative, path in changed_files
        ],
        "deleted_files": deleted_files,
    }
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        archive.writestr(
            PATCH_MANIFEST_NAME,
            json.dumps(patch_manifest, indent=2, sort_keys=True),
        )
        for relative, path in changed_files:
            archive.write(path, f"CoreFlowStudio/{relative}")

    package = UpdatePackage(
        package_type="patch",
        from_version=from_version,
        to_version=to_version,
        file_name=name,
        url=_package_url(base_url, name),
        sha256=file_sha256(zip_path),
        size_bytes=zip_path.stat().st_size,
    )
    return zip_path, package


def _create_full_update_zip(
    *,
    dist_dir: Path,
    output_dir: Path,
    version: str,
    base_url: str,
    package_name: str | None = None,
) -> tuple[Path, UpdatePackage]:
    import zipfile

    dist_dir = Path(dist_dir)
    output_dir = Path(output_dir)
    _ensure_dist_dir(dist_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = package_name or f"CoreFlowStudio-{version}-full.zip"
    zip_path = output_dir / name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative, path in sorted(_package_file_map(dist_dir).items()):
            archive.write(path, f"CoreFlowStudio/{relative}")
    package = UpdatePackage(
        package_type="full",
        to_version=version,
        file_name=name,
        url=_package_url(base_url, name),
        sha256=file_sha256(zip_path),
        size_bytes=zip_path.stat().st_size,
    )
    return zip_path, package


def _ensure_dist_dir(dist_dir: Path) -> None:
    if not (dist_dir / "CoreFlowStudio.exe").exists():
        raise ValueError(f"Not a CoreFlowStudio dist directory: {dist_dir}")


def _package_file_map(dist_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(Path(dist_dir).rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(dist_dir)
        if _should_exclude_from_update_package(relative):
            continue
        files[relative.as_posix()] = path
    return files


def _package_url(base_url: str, name: str) -> str:
    return f"{base_url.rstrip('/')}/{name}" if base_url else name


def _write_latest_manifest(
    manifest_path: Path,
    *,
    latest_version: str,
    packages: tuple[UpdatePackage, ...],
    release_notes: str = "",
) -> None:
    manifest = {
        "schema_version": 1,
        "latest_version": latest_version,
        "generated_at": datetime.now(UTC).isoformat(),
        "release_notes": release_notes,
        "packages": [_package_to_manifest_dict(package) for package in packages],
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _package_to_manifest_dict(package: UpdatePackage) -> dict[str, object]:
    data: dict[str, object] = {
        "type": package.package_type,
        "to_version": package.to_version,
        "file_name": package.file_name,
        "url": package.url,
        "sha256": package.sha256,
        "size_bytes": package.size_bytes,
    }
    if package.from_version is not None:
        data["from_version"] = package.from_version
    if package.notes:
        data["notes"] = package.notes
    return {key: value for key, value in data.items() if value is not None}


class _extracted_update_package:
    def __init__(self, package_path: Path) -> None:
        self.package_path = Path(package_path)
        self._temp_dir = None

    def __enter__(self) -> Path:
        import tempfile
        import zipfile

        self._temp_dir = tempfile.TemporaryDirectory()
        extract_root = Path(self._temp_dir.name)
        with zipfile.ZipFile(self.package_path) as archive:
            _safe_extract_zip(archive, extract_root)
        nested = extract_root / "CoreFlowStudio"
        if (nested / "CoreFlowStudio.exe").exists():
            return nested
        if (extract_root / "CoreFlowStudio.exe").exists():
            return extract_root
        raise ValueError(f"Update package does not contain CoreFlowStudio.exe: {self.package_path}")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()


def _safe_extract_zip(archive, extract_root: Path) -> None:
    root = extract_root.resolve()
    for member in archive.infolist():
        target = (extract_root / member.filename).resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"Unsafe path in update package: {member.filename}")
        archive.extract(member, extract_root)


def _should_exclude_from_update_package(relative_path: Path) -> bool:
    parts = relative_path.parts
    if not parts:
        return True
    if parts[0] in {
        "CoreFlowStudioData",
        "verify-smoke-data",
        "verify-replay-data",
        "updates",
    }:
        return True
    if relative_path.name in {
        "verify-replay-template.csv",
        "ui_stdout.log",
        "ui_stderr.log",
    }:
        return True
    return False


_UPDATER_SCRIPT = r"""param(
    [Parameter(Mandatory = $true)]
    [string]$PackageZip,
    [Parameter(Mandatory = $true)]
    [string]$InstallDir,
    [Parameter(Mandatory = $true)]
    [string]$RestartExe,
    [int]$WaitForPid = 0,
    [string]$ExpectedSha256 = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

function Write-UpdateLog {
    param([string]$Message)
    if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
        $parent = Split-Path -Parent $LogPath
        if (-not [string]::IsNullOrWhiteSpace($parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Add-Content -LiteralPath $LogPath -Value "$(Get-Date -Format o) $Message"
    }
}

function Assert-SafePatchPath {
    param([string]$RelativePath)
    if ([string]::IsNullOrWhiteSpace($RelativePath)) {
        throw "Patch path is empty."
    }
    if ([System.IO.Path]::IsPathRooted($RelativePath)) {
        throw "Patch path is absolute: $RelativePath"
    }
    $parts = $RelativePath -split '[\\/]'
    if ($parts -contains '..') {
        throw "Patch path escapes the install directory: $RelativePath"
    }
}

function Join-InstallPath {
    param(
        [string]$Root,
        [string]$RelativePath
    )
    Assert-SafePatchPath -RelativePath $RelativePath
    return Join-Path $Root $RelativePath
}

Write-UpdateLog "Starting CoreFlow Studio update."
if ($WaitForPid -gt 0) {
    try {
        Wait-Process -Id $WaitForPid -Timeout 90
    } catch {
        Write-UpdateLog "Wait for process $WaitForPid timed out or failed: $_"
    }
}

if (-not (Test-Path -LiteralPath $PackageZip)) {
    throw "Update package not found: $PackageZip"
}
if (-not [string]::IsNullOrWhiteSpace($ExpectedSha256)) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $PackageZip).Hash.ToLowerInvariant()
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Update package checksum mismatch: $actual"
    }
}

$installPath = Resolve-Path -LiteralPath $InstallDir
$parent = Split-Path -Parent $installPath.Path
$leaf = Split-Path -Leaf $installPath.Path
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$backup = Join-Path $parent "$leaf.backup.$stamp"
$extract = Join-Path ([System.IO.Path]::GetTempPath()) "CoreFlowUpdate-$([Guid]::NewGuid())"
New-Item -ItemType Directory -Path $extract -Force | Out-Null
$patchManifestName = "coreflow_patch_manifest.json"

try {
    Expand-Archive -LiteralPath $PackageZip -DestinationPath $extract -Force
    $patchManifestPath = Join-Path $extract $patchManifestName

    if (Test-Path -LiteralPath $patchManifestPath) {
        $patchManifest = Get-Content -LiteralPath $patchManifestPath -Raw | ConvertFrom-Json
        if ($patchManifest.package_type -ne "patch") {
            throw "Unsupported patch package manifest type: $($patchManifest.package_type)"
        }
        $payload = Join-Path $extract "CoreFlowStudio"
        if (-not (Test-Path -LiteralPath $payload)) {
            throw "Patch package does not contain a CoreFlowStudio payload folder."
        }

        Write-UpdateLog "Backing up current install for patch: $backup"
        Copy-Item -LiteralPath $installPath.Path -Destination $backup -Recurse -Force
        try {
            foreach ($entry in @($patchManifest.deleted_files)) {
                if ([string]::IsNullOrWhiteSpace([string]$entry)) {
                    continue
                }
                $target = Join-InstallPath -Root $installPath.Path -RelativePath ([string]$entry)
                if (Test-Path -LiteralPath $target) {
                    Remove-Item -LiteralPath $target -Recurse -Force
                }
            }
            foreach ($entry in @($patchManifest.changed_files)) {
                $relative = [string]$entry.path
                if ([string]::IsNullOrWhiteSpace($relative)) {
                    continue
                }
                $source = Join-InstallPath -Root $payload -RelativePath $relative
                $target = Join-InstallPath -Root $installPath.Path -RelativePath $relative
                if (-not (Test-Path -LiteralPath $source)) {
                    throw "Patch payload missing changed file: $relative"
                }
                $targetParent = Split-Path -Parent $target
                if (-not [string]::IsNullOrWhiteSpace($targetParent)) {
                    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
                }
                Copy-Item -LiteralPath $source -Destination $target -Force
                if (-not [string]::IsNullOrWhiteSpace([string]$entry.sha256)) {
                    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash.ToLowerInvariant()
                    if ($actual -ne ([string]$entry.sha256).ToLowerInvariant()) {
                        throw "Patched file checksum mismatch for $relative"
                    }
                }
            }
            Remove-Item -LiteralPath $backup -Recurse -Force
            Write-UpdateLog "Patch update installed successfully."
        } catch {
            Write-UpdateLog "Patch install failed, restoring backup: $_"
            if (Test-Path -LiteralPath $installPath.Path) {
                Remove-Item -LiteralPath $installPath.Path -Recurse -Force
            }
            Move-Item -LiteralPath $backup -Destination $installPath.Path
            throw
        }
    } else {
        $payload = Join-Path $extract "CoreFlowStudio"
        if (-not (Test-Path -LiteralPath (Join-Path $payload "CoreFlowStudio.exe"))) {
            $payload = $extract
        }
        if (-not (Test-Path -LiteralPath (Join-Path $payload "CoreFlowStudio.exe"))) {
            throw "Update package does not contain CoreFlowStudio.exe."
        }

        Write-UpdateLog "Moving current install to backup: $backup"
        Move-Item -LiteralPath $installPath.Path -Destination $backup
        try {
            Write-UpdateLog "Installing payload from: $payload"
            Move-Item -LiteralPath $payload -Destination $installPath.Path
            Remove-Item -LiteralPath $backup -Recurse -Force
        } catch {
            Write-UpdateLog "Install failed, restoring backup: $_"
            if (Test-Path -LiteralPath $installPath.Path) {
                Remove-Item -LiteralPath $installPath.Path -Recurse -Force
            }
            Move-Item -LiteralPath $backup -Destination $installPath.Path
            throw
        }
        Write-UpdateLog "Full update installed successfully."
    }
    if (Test-Path -LiteralPath $RestartExe) {
        Start-Process -FilePath $RestartExe
    }
} finally {
    if (Test-Path -LiteralPath $extract) {
        Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    }
}
"""

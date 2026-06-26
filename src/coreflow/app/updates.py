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

    import zipfile

    dist_dir = Path(dist_dir)
    output_dir = Path(output_dir)
    if not (dist_dir / "CoreFlowStudio.exe").exists():
        raise ValueError(f"Not a CoreFlowStudio dist directory: {dist_dir}")
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
        for path in sorted(dist_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(dist_dir)
            if _should_exclude_from_update_package(relative):
                continue
            archive.write(path, Path("CoreFlowStudio") / relative)
    sha256 = file_sha256(zip_path)
    package_url = f"{base_url.rstrip('/')}/{name}" if base_url else name
    manifest = {
        "schema_version": 1,
        "latest_version": version,
        "generated_at": datetime.now(UTC).isoformat(),
        "release_notes": "",
        "packages": [
            {
                "type": "full",
                "to_version": version,
                "file_name": name,
                "url": package_url,
                "sha256": sha256,
                "size_bytes": zip_path.stat().st_size,
            }
        ],
    }
    manifest_path = output_dir / "latest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return zip_path, manifest_path


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

try {
    Expand-Archive -LiteralPath $PackageZip -DestinationPath $extract -Force
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
    Write-UpdateLog "Update installed successfully."
    if (Test-Path -LiteralPath $RestartExe) {
        Start-Process -FilePath $RestartExe
    }
} finally {
    if (Test-Path -LiteralPath $extract) {
        Remove-Item -LiteralPath $extract -Recurse -Force -ErrorAction SilentlyContinue
    }
}
"""

"""Pre-commit version policy check for CoreFlow Studio."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path


VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')
VERSION_RELEVANT_PREFIXES = (
    "src/",
    "packaging/",
)
VERSION_FILES = {
    "pyproject.toml",
    "src/coreflow/__init__.py",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check whether staged changes satisfy the CoreFlow version policy."
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to the current working directory.",
    )
    args = parser.parse_args(argv)
    repo = args.base.resolve()

    pyproject_version = _read_pyproject_version(repo / "pyproject.toml")
    package_version = _read_package_version(repo / "src" / "coreflow" / "__init__.py")
    if pyproject_version != package_version:
        print(
            "Version check failed: pyproject.toml and src/coreflow/__init__.py "
            f"disagree ({pyproject_version!r} != {package_version!r}).",
            file=sys.stderr,
        )
        return 1

    staged = _staged_files(repo)
    if not staged:
        return 0

    relevant = [
        path
        for path in staged
        if path.startswith(VERSION_RELEVANT_PREFIXES)
    ]
    if not relevant:
        return 0

    changed_versions = sorted(VERSION_FILES.intersection(staged))
    if changed_versions == sorted(VERSION_FILES):
        return 0

    print(
        "Version check failed: staged code or packaging changes may need a software "
        "version update.",
        file=sys.stderr,
    )
    print(
        "Update both pyproject.toml and src/coreflow/__init__.py, or commit only "
        "documentation/tests when the version should stay unchanged.",
        file=sys.stderr,
    )
    print("Version-relevant staged files:", file=sys.stderr)
    for path in relevant:
        print(f"  - {path}", file=sys.stderr)
    return 1


def _read_pyproject_version(path: Path) -> str:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return str(payload["project"]["version"])


def _read_package_version(path: Path) -> str:
    match = VERSION_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"Unable to find __version__ in {path}")
    return match.group(1)


def _staged_files(repo: Path) -> set[str]:
    completed = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        line.strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    }


if __name__ == "__main__":
    raise SystemExit(main())

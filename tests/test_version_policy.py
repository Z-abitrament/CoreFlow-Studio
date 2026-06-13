from __future__ import annotations

from pathlib import Path

import scripts.check_version_update as version_check


def test_version_check_passes_for_docs_only_staged_files(monkeypatch, tmp_path) -> None:
    _write_version_files(tmp_path)
    monkeypatch.setattr(
        version_check,
        "_staged_files",
        lambda _repo: {"docs/DEVELOPMENT_WORKFLOW.md"},
    )

    assert version_check.main(["--base", str(tmp_path)]) == 0


def test_version_check_blocks_relevant_changes_without_version_files(
    monkeypatch, tmp_path, capsys
) -> None:
    _write_version_files(tmp_path)
    monkeypatch.setattr(
        version_check,
        "_staged_files",
        lambda _repo: {"src/coreflow/app/runtime.py"},
    )

    assert version_check.main(["--base", str(tmp_path)]) == 1
    assert "may need a software version update" in capsys.readouterr().err


def test_version_check_allows_relevant_changes_with_both_version_files(
    monkeypatch, tmp_path
) -> None:
    _write_version_files(tmp_path)
    monkeypatch.setattr(
        version_check,
        "_staged_files",
        lambda _repo: {
            "pyproject.toml",
            "src/coreflow/__init__.py",
            "packaging/windows/build.ps1",
        },
    )

    assert version_check.main(["--base", str(tmp_path)]) == 0


def test_version_check_allows_pytest_config_without_version_bump(
    monkeypatch, tmp_path
) -> None:
    _write_version_files(tmp_path)
    monkeypatch.setattr(
        version_check,
        "_staged_files",
        lambda _repo: {"pyproject.toml"},
    )

    assert version_check.main(["--base", str(tmp_path)]) == 0


def test_version_check_blocks_mismatched_version_sources(
    monkeypatch, tmp_path, capsys
) -> None:
    _write_version_files(tmp_path, pyproject_version="0.1.0", package_version="0.2.0")
    monkeypatch.setattr(version_check, "_staged_files", lambda _repo: set())

    assert version_check.main(["--base", str(tmp_path)]) == 1
    assert "disagree" in capsys.readouterr().err


def _write_version_files(
    root: Path,
    *,
    pyproject_version: str = "0.1.0",
    package_version: str = "0.1.0",
) -> None:
    root.joinpath("pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "coreflow-studio"',
                f'version = "{pyproject_version}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    package_dir = root / "src" / "coreflow"
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text(
        f'"""CoreFlow Studio package."""\n\n__version__ = "{package_version}"\n',
        encoding="utf-8",
    )

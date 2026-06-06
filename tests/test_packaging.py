from __future__ import annotations

from pathlib import Path

from coreflow.__main__ import build_parser, main, should_launch_packaged_ui_by_default
from coreflow.app.paths import default_user_data_root
from coreflow.build_info import current_build_info


def test_default_user_data_root_prefers_environment_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("COREFLOW_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert default_user_data_root() == tmp_path / "data"


def test_default_user_data_root_uses_local_app_data(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("COREFLOW_DATA_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    assert default_user_data_root() == tmp_path / "local" / "CoreFlow Studio"


def test_default_user_data_root_falls_back_when_local_app_data_unwritable(
    monkeypatch, tmp_path
) -> None:
    blocked_local = tmp_path / "blocked-local"
    blocked_local.write_text("not a directory", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("COREFLOW_DATA_ROOT", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(blocked_local))
    monkeypatch.setattr(Path, "home", lambda: home)

    assert default_user_data_root() == home / ".coreflow-studio"


def test_default_user_data_root_uses_working_directory_when_user_dirs_fail(
    monkeypatch, tmp_path
) -> None:
    blocked_local = tmp_path / "blocked-local"
    blocked_app = tmp_path / "blocked-app"
    blocked_home = tmp_path / "blocked-home"
    blocked_local.write_text("not a directory", encoding="utf-8")
    blocked_app.write_text("not a directory", encoding="utf-8")
    blocked_home.write_text("not a directory", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.delenv("COREFLOW_DATA_ROOT", raising=False)
    monkeypatch.delenv("COREFLOW_PACKAGED", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(blocked_local))
    monkeypatch.setenv("APPDATA", str(blocked_app))
    monkeypatch.setattr(Path, "home", lambda: blocked_home)
    monkeypatch.chdir(cwd)

    assert default_user_data_root() == cwd / "CoreFlowStudioData"


def test_build_info_uses_environment_stamp(monkeypatch, capsys) -> None:
    monkeypatch.setenv("COREFLOW_BUILD_COMMIT", "abc1234")
    monkeypatch.setenv("COREFLOW_BUILD_CHANNEL", "pytest")

    info = current_build_info()
    assert info.commit == "abc1234"
    assert info.build_channel == "pytest"
    assert main(["--build-info"]) == 0

    captured = capsys.readouterr()
    assert "commit=abc1234" in captured.out
    assert "channel=pytest" in captured.out


def test_packaged_no_argument_launches_ui_by_default(monkeypatch, tmp_path) -> None:
    calls: list[Path | None] = []

    def fake_launch_ui(data_root: Path | None = None) -> int:
        calls.append(data_root)
        return 0

    monkeypatch.setenv("COREFLOW_PACKAGED", "1")
    monkeypatch.setattr("coreflow.__main__.launch_ui", fake_launch_ui)

    parser = build_parser()
    assert should_launch_packaged_ui_by_default(parser.parse_args([])) is True
    assert (
        should_launch_packaged_ui_by_default(
            parser.parse_args(["--data-root", str(tmp_path)])
        )
        is True
    )
    assert (
        should_launch_packaged_ui_by_default(parser.parse_args(["--build-info"]))
        is False
    )
    assert main(["--data-root", str(tmp_path)]) == 0
    assert calls == [tmp_path]


def test_simulator_smoke_cli_runs_workflows(tmp_path, capsys) -> None:
    assert main(["--simulator-smoke", "--data-root", str(tmp_path)]) == 0

    captured = capsys.readouterr()
    assert "Simulator smoke passed:" in captured.out
    assert "SIM-PACKAGE-SMOKE" in captured.out
    assert "calibration_run=" in captured.out
    assert "factory_run=" in captured.out
    assert "experiment_run=" in captured.out
    assert "manifest=" in captured.out
    assert (tmp_path / "coreflow.sqlite").exists()


def test_windows_packaging_files_are_present() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec = repo_root / "packaging" / "windows" / "coreflow_studio.spec"
    build_script = repo_root / "packaging" / "windows" / "build.ps1"
    readme = repo_root / "packaging" / "windows" / "README.md"

    assert spec.exists()
    assert build_script.exists()
    assert readme.exists()
    spec_text = spec.read_text(encoding="utf-8")
    script_text = build_script.read_text(encoding="utf-8")
    assert "coreflow\" / \"__main__.py" in spec_text
    assert "generated_build_stamp.py" in spec_text
    assert "console=True" in spec_text
    assert "collect_dynamic_libs(\"PySide6\")" in spec_text
    assert "collect_dynamic_libs(\"shiboken6\")" in spec_text
    assert "icudt73.dll" in spec_text
    assert "icuuc.dll" in spec_text
    assert "PyInstaller" in script_text
    assert "COREFLOW_BUILD_COMMIT" in script_text
    assert "COREFLOW_PACKAGED" in script_text
    assert "--simulator-smoke" in readme.read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%\\CoreFlow Studio" in readme.read_text(encoding="utf-8")

from __future__ import annotations

from pathlib import Path

from coreflow.__main__ import main
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
    assert "PyInstaller" in script_text
    assert "COREFLOW_BUILD_COMMIT" in script_text
    assert "--simulator-smoke" in readme.read_text(encoding="utf-8")
    assert "%LOCALAPPDATA%\\CoreFlow Studio" in readme.read_text(encoding="utf-8")

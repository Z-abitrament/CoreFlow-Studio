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


def test_default_user_data_root_falls_back_when_existing_dir_is_not_writable(
    monkeypatch, tmp_path
) -> None:
    local = tmp_path / "local"
    app = tmp_path / "app"
    home = tmp_path / "home"
    home.mkdir()

    def fake_can_create_directory(path: Path) -> bool:
        return path == app / "CoreFlow Studio"

    monkeypatch.delenv("COREFLOW_DATA_ROOT", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(app))
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr("coreflow.app.paths._can_create_directory", fake_can_create_directory)

    assert default_user_data_root() == app / "CoreFlow Studio"


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
    assert (
        should_launch_packaged_ui_by_default(
            parser.parse_args(["--make-update-package", str(tmp_path / "dist")])
        )
        is False
    )
    assert (
        should_launch_packaged_ui_by_default(
            parser.parse_args(["--write-replay-template", str(tmp_path / "replay.csv")])
        )
        is False
    )
    assert (
        should_launch_packaged_ui_by_default(
            parser.parse_args(["--replay-smoke", str(tmp_path / "replay.csv")])
        )
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


def test_replay_template_and_smoke_cli_run_workflow(tmp_path, capsys) -> None:
    replay_path = tmp_path / "replay.csv"
    data_root = tmp_path / "data"

    assert main(["--write-replay-template", str(replay_path)]) == 0
    assert replay_path.exists()
    assert "mass_flow" in replay_path.read_text(encoding="utf-8")

    assert main(["--replay-smoke", str(replay_path), "--data-root", str(data_root)]) == 0

    captured = capsys.readouterr()
    assert "Wrote replay template:" in captured.out
    assert "Replay smoke passed:" in captured.out
    assert "experiment_run=" in captured.out
    assert (data_root / "coreflow.sqlite").exists()


def test_make_update_package_cli_writes_release_assets(tmp_path, capsys) -> None:
    dist_dir = tmp_path / "CoreFlowStudio"
    (dist_dir / "_internal").mkdir(parents=True)
    (dist_dir / "CoreFlowStudio.exe").write_bytes(b"exe")
    (dist_dir / "CoreFlowStudioConsole.exe").write_bytes(b"console")
    (dist_dir / "_internal" / "dependency.dll").write_bytes(b"dll")
    output_dir = tmp_path / "updates"

    assert (
        main(
            [
                "--make-update-package",
                str(dist_dir),
                "--update-output-dir",
                str(output_dir),
                "--update-base-url",
                "https://github.com/acme/CoreFlowStudio/releases/download/v0.6.0",
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Wrote update package:" in captured.out
    assert "Wrote update manifest:" in captured.out
    assert (output_dir / "CoreFlowStudio-0.6.0-full.zip").exists()
    manifest = (output_dir / "latest.json").read_text(encoding="utf-8")
    assert '"latest_version": "0.6.0"' in manifest
    assert "CoreFlowStudio-0.6.0-full.zip" in manifest


def test_windows_packaging_files_are_present() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    spec = repo_root / "packaging" / "windows" / "coreflow_studio.spec"
    build_script = repo_root / "packaging" / "windows" / "build.ps1"
    verify_script = repo_root / "packaging" / "windows" / "verify_package.ps1"
    readme = repo_root / "packaging" / "windows" / "README.md"
    environment = repo_root / "environment.yml"
    workflow_doc = repo_root / "docs" / "DEVELOPMENT_WORKFLOW.md"

    assert spec.exists()
    assert build_script.exists()
    assert verify_script.exists()
    assert readme.exists()
    assert environment.exists()
    spec_text = spec.read_text(encoding="utf-8")
    script_text = build_script.read_text(encoding="utf-8")
    verify_text = verify_script.read_text(encoding="utf-8")
    environment_text = environment.read_text(encoding="utf-8")
    workflow_text = workflow_doc.read_text(encoding="utf-8")
    assert "coreflow\" / \"__main__.py" in spec_text
    assert "generated_build_stamp.py" in spec_text
    assert 'name="CoreFlowStudio"' in spec_text
    assert "console=False" in spec_text
    assert 'name="CoreFlowStudioConsole"' in spec_text
    assert "console=True" in spec_text
    assert "collect_dynamic_libs(\"PySide6\")" in spec_text
    assert "collect_dynamic_libs(\"shiboken6\")" in spec_text
    assert '"PySide6.QtOpenGL"' in spec_text
    assert "collect_conda_qt_binaries" in spec_text
    assert "Library\" / \"bin\"" in spec_text
    assert "shiboken6*.dll" in spec_text
    assert "pyside6*.dll" in spec_text
    assert "icudt73.dll" in spec_text
    assert "icuuc.dll" in spec_text
    assert "PyInstaller" in script_text
    assert '$CondaEnv = "coreflow-studio"' in script_text
    assert "conda" in script_text
    assert "Resolve-CondaPython" in script_text
    assert "conda env list --json" in script_text
    assert "python.exe" in script_text
    assert "PYTHONNOUSERSITE" in script_text
    assert "Assert-DistNotRunning" in script_text
    assert "Close running packaged CoreFlow Studio processes before building" in script_text
    assert ".venv" not in script_text
    assert "COREFLOW_BUILD_COMMIT" in script_text
    assert "COREFLOW_PACKAGED" in script_text
    assert "USER_MANUAL.en.md" in script_text
    assert "USER_MANUAL.zh-CN.md" in script_text
    assert "CoreFlowStudioConsole.exe" in verify_text
    assert "--write-replay-template" in verify_text
    assert "--replay-smoke" in verify_text
    assert "--ui" in verify_text
    assert "RedirectStandardError" in verify_text
    assert "pyside6.cp313-win_amd64.dll" in verify_text
    assert "shiboken6.cp313-win_amd64.dll" in verify_text
    assert "name: coreflow-studio" in environment_text
    assert "pyinstaller>=6.6" in environment_text
    assert "pytest>=8.0" in environment_text
    assert "pytest-qt>=4.4" in environment_text
    assert "-e ." in environment_text
    assert "Conventional Commits" in workflow_text
    readme_text = readme.read_text(encoding="utf-8")
    assert "verify_package.ps1" in readme_text
    assert "CoreFlowStudioConsole.exe --simulator-smoke" in readme_text
    assert "CoreFlowStudioConsole.exe --write-replay-template" in readme_text
    assert "CoreFlowStudioConsole.exe --replay-smoke" in readme_text
    assert "CoreFlowStudioConsole.exe --ui" in readme_text
    assert "--make-update-package" in readme_text
    assert "latest.json" in readme_text
    assert "startup.log" in readme_text
    assert "CoreFlowStudio.exe` with no command-line arguments" in readme_text
    assert "%LOCALAPPDATA%\\CoreFlow Studio" in readme_text
    english_manual = repo_root / "docs" / "USER_MANUAL.en.md"
    chinese_manual = repo_root / "docs" / "USER_MANUAL.zh-CN.md"
    assert english_manual.exists()
    assert chinese_manual.exists()
    assert "startup.log" in english_manual.read_text(encoding="utf-8")
    assert "startup.log" in chinese_manual.read_text(encoding="utf-8")

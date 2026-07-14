from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import coreflow
from coreflow.__main__ import main


def test_package_exposes_version() -> None:
    assert coreflow.__version__ == "0.7.1"


def test_main_returns_success(capsys) -> None:
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "CoreFlow Studio M0 bootstrap is ready." in captured.out


def test_packaged_ui_startup_failure_writes_log(monkeypatch, tmp_path, capsys) -> None:
    def fake_launch_ui(data_root=None) -> int:
        raise RuntimeError("qt dependency missing")

    monkeypatch.setenv("COREFLOW_PACKAGED", "1")
    monkeypatch.setattr("coreflow.__main__.launch_ui", fake_launch_ui)

    assert main(["--ui", "--data-root", str(tmp_path)]) == 1

    captured = capsys.readouterr()
    log_path = tmp_path / "logs" / "startup.log"
    assert "UI startup failed" in captured.err
    assert str(log_path) in captured.err
    log_text = log_path.read_text(encoding="utf-8")
    assert "build=version=0.7.1" in log_text
    assert "RuntimeError: qt dependency missing" in log_text
    assert "traceback:" in log_text


def test_source_ui_startup_failure_is_re_raised(monkeypatch, tmp_path) -> None:
    def fake_launch_ui(data_root=None) -> int:
        raise RuntimeError("source startup failure")

    monkeypatch.delenv("COREFLOW_PACKAGED", raising=False)
    monkeypatch.setattr("coreflow.__main__.launch_ui", fake_launch_ui)

    with pytest.raises(RuntimeError, match="source startup failure"):
        main(["--ui", "--data-root", str(tmp_path)])

    assert not (tmp_path / "logs" / "startup.log").exists()


def test_module_entry_point_runs() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    completed = subprocess.run(
        [sys.executable, "-m", "coreflow", "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert "CoreFlow Studio 0.7.1" in completed.stdout

from __future__ import annotations

import subprocess
import sys

import coreflow
from coreflow.__main__ import main


def test_package_exposes_version() -> None:
    assert coreflow.__version__ == "0.1.0"


def test_main_returns_success(capsys) -> None:
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "CoreFlow Studio M0 bootstrap is ready." in captured.out


def test_module_entry_point_runs() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "coreflow", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "CoreFlow Studio 0.1.0" in completed.stdout

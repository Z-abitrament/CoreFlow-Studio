"""Qt application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from coreflow.app import CoreFlowRuntime
from coreflow.ui.main_window import MainWindow


def run_app(argv: list[str] | None = None, data_root: Path | None = None) -> int:
    """Start the CoreFlow Studio Qt desktop application."""

    app = QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    runtime = CoreFlowRuntime(data_root=data_root)
    window = MainWindow(runtime=runtime)
    window.show()
    if owns_app:
        return int(app.exec())
    return 0

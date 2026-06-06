"""Qt worker helpers for short workflow actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

T = TypeVar("T")


class WorkerSignals(QObject):
    """Signals emitted by a background workflow task."""

    finished = Signal(object)
    failed = Signal(str)


class WorkflowTask(QRunnable):
    """Run a callable on a Qt thread-pool worker."""

    def __init__(self, action: Callable[[], T]) -> None:
        super().__init__()
        self._action = action
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.finished.emit(self._action())
        except Exception as exc:  # pragma: no cover - Qt signal boundary
            self.signals.failed.emit(str(exc))

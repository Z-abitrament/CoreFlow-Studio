"""Main Qt window for module-centered CoreFlow Studio operation."""

from __future__ import annotations

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QMainWindow, QStackedWidget, QWidget

from coreflow.app import CoreFlowRuntime
from coreflow.ui.asio_window import AsioIisWindow
from coreflow.ui.modbus_window import ModbusModuleWindow


class MainWindow(QMainWindow):
    """Main shell that swaps designed modules into the central workspace."""

    def __init__(self, runtime: CoreFlowRuntime | None = None) -> None:
        super().__init__()
        self.runtime = runtime or CoreFlowRuntime()
        self._thread_pool = QThreadPool.globalInstance()
        self.modbusWindow: ModbusModuleWindow | None = None
        self.asioWindow: AsioIisWindow | None = None

        self.setWindowTitle("CoreFlow Studio")
        self.resize(1180, 760)
        self.setMinimumSize(980, 620)
        self._build_ui()
        self._show_modbus_module()

    def _build_ui(self) -> None:
        self.moduleStack = QStackedWidget(self)
        self.moduleStack.setObjectName("moduleStack")
        self.setCentralWidget(self.moduleStack)
        self._build_menu()

    def _build_menu(self) -> None:
        modules_menu = self.menuBar().addMenu("Modules")
        self.modbusModuleAction = modules_menu.addAction("Modbus Module")
        self.modbusModuleAction.setObjectName("modbusModuleAction")
        self.modbusModuleAction.setCheckable(True)
        self.modbusModuleAction.triggered.connect(self._show_modbus_module)
        self.asioModuleAction = modules_menu.addAction("ASIO/IIS Module")
        self.asioModuleAction.setObjectName("asioModuleAction")
        self.asioModuleAction.setCheckable(True)
        self.asioModuleAction.triggered.connect(self._show_asio_module)

    def _show_modbus_module(self) -> None:
        if self.modbusWindow is None:
            self.modbusWindow = ModbusModuleWindow(
                repository=self.runtime.repository,
                data_root=self.runtime.data_root,
                parent=self.moduleStack,
                embedded=True,
            )
            self.moduleStack.addWidget(self.modbusWindow)
        self._set_current_module(self.modbusWindow)

    def _show_asio_module(self) -> None:
        if self.asioWindow is None:
            self.asioWindow = AsioIisWindow(
                thread_pool=self._thread_pool,
                parent=self.moduleStack,
                embedded=True,
            )
            self.moduleStack.addWidget(self.asioWindow)
        self._set_current_module(self.asioWindow)

    def _set_current_module(self, widget: QWidget) -> None:
        widget.show()
        self.moduleStack.setCurrentWidget(widget)
        self.modbusModuleAction.setChecked(widget is self.modbusWindow)
        self.asioModuleAction.setChecked(widget is self.asioWindow)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self.modbusWindow is not None:
            self.modbusWindow.close()
        if self.asioWindow is not None:
            self.asioWindow.close()
        super().closeEvent(event)

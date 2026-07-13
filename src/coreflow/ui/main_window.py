"""Main Qt window for module-centered CoreFlow Studio operation."""

from __future__ import annotations

import logging

from PySide6.QtCore import QCoreApplication, QThreadPool, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from coreflow.app import CoreFlowRuntime
from coreflow.app.updates import (
    DownloadedUpdate,
    UpdateCheckResult,
    UpdateService,
    UpdateSettings,
)
from coreflow.ui.asio_window import AsioIisWindow
from coreflow.ui.filling_window import FillingModuleWindow
from coreflow.ui.modbus_window import ModbusModuleWindow
from coreflow.ui.workers import WorkflowTask


LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main shell that swaps designed modules into the central workspace."""

    def __init__(self, runtime: CoreFlowRuntime | None = None) -> None:
        super().__init__()
        self.runtime = runtime or CoreFlowRuntime()
        self._thread_pool = QThreadPool.globalInstance()
        self.modbusWindow: ModbusModuleWindow | None = None
        self.asioWindow: AsioIisWindow | None = None
        self.fillingWindow: FillingModuleWindow | None = None
        self.updateDialog: UpdateDialog | None = None

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
        self.fillingModuleAction = modules_menu.addAction("Filling Module")
        self.fillingModuleAction.setObjectName("fillingModuleAction")
        self.fillingModuleAction.setCheckable(True)
        self.fillingModuleAction.triggered.connect(self._show_filling_module)

        help_menu = self.menuBar().addMenu("Help")
        self.checkUpdatesAction = help_menu.addAction("Check for Updates...")
        self.checkUpdatesAction.setObjectName("checkUpdatesAction")
        self.checkUpdatesAction.triggered.connect(self._open_update_dialog)

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

    def _show_filling_module(self) -> None:
        previous_widget = self.moduleStack.currentWidget()
        if self.fillingWindow is None:
            self.fillingWindow = FillingModuleWindow(
                repository=self.runtime.repository,
                operator=self.runtime.operator,
                parent=self.moduleStack,
                embedded=True,
            )
            self.fillingWindow.setWindowFlags(Qt.WindowType.Widget)
            self.moduleStack.addWidget(self.fillingWindow)
        if not self.fillingWindow.ensure_device_selected():
            if previous_widget is not None:
                self._set_current_module(previous_widget)
            else:
                self.fillingModuleAction.setChecked(False)
            return
        self._set_current_module(self.fillingWindow)

    def _set_current_module(self, widget: QWidget) -> None:
        widget.show()
        self.moduleStack.setCurrentWidget(widget)
        self.modbusModuleAction.setChecked(widget is self.modbusWindow)
        self.asioModuleAction.setChecked(widget is self.asioWindow)
        self.fillingModuleAction.setChecked(widget is self.fillingWindow)

    def _open_update_dialog(self) -> None:
        if self.updateDialog is None or not self.updateDialog.isVisible():
            self.updateDialog = UpdateDialog(
                UpdateService(data_root=self.runtime.data_root),
                parent=self,
            )
        self.updateDialog.show()
        self.updateDialog.raise_()
        self.updateDialog.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        if self.fillingWindow is not None:
            try:
                ended = self.fillingWindow.end_active_group()
                if not ended:
                    LOGGER.warning("Filling group cleanup reported failure during shutdown.")
            except Exception:
                LOGGER.exception("Filling group cleanup failed during shutdown.")
        if self.modbusWindow is not None:
            self.modbusWindow.close()
        if self.asioWindow is not None:
            self.asioWindow.close()
        super().closeEvent(event)


class UpdateDialog(QDialog):
    """Operator-facing update dialog for GitHub Release style manifests."""

    def __init__(self, service: UpdateService, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._check_result: UpdateCheckResult | None = None
        self._downloaded_update: DownloadedUpdate | None = None
        self._active_task: WorkflowTask | None = None
        self.setWindowTitle("Software Update")
        self.setModal(False)
        self.resize(620, 420)
        self._build_ui()
        self._load_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        self.manifestUrlEdit = QLineEdit()
        self.manifestUrlEdit.setObjectName("updateManifestUrlEdit")
        self.manifestUrlEdit.setPlaceholderText(
            "https://github.com/<owner>/<repo>/releases/latest/download/latest.json"
        )
        form.addRow("Update URL", self.manifestUrlEdit)
        root.addLayout(form)

        self.statusLabel = QLabel("Ready")
        self.statusLabel.setObjectName("updateStatusLabel")
        self.statusLabel.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        root.addWidget(self.statusLabel)

        self.progressBar = QProgressBar()
        self.progressBar.setObjectName("updateProgressBar")
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        root.addWidget(self.progressBar)

        self.detailsTextEdit = QTextEdit()
        self.detailsTextEdit.setObjectName("updateDetailsTextEdit")
        self.detailsTextEdit.setReadOnly(True)
        root.addWidget(self.detailsTextEdit, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.saveUrlButton = QPushButton("Save URL")
        self.saveUrlButton.setObjectName("updateSaveUrlButton")
        self.checkButton = QPushButton("Check")
        self.checkButton.setObjectName("updateCheckButton")
        self.downloadButton = QPushButton("Download")
        self.downloadButton.setObjectName("updateDownloadButton")
        self.downloadButton.setEnabled(False)
        self.installButton = QPushButton("Update and Restart")
        self.installButton.setObjectName("updateInstallButton")
        self.installButton.setEnabled(False)
        self.closeButton = QPushButton("Close")
        self.closeButton.setObjectName("updateCloseButton")
        buttons.addWidget(self.saveUrlButton)
        buttons.addWidget(self.checkButton)
        buttons.addWidget(self.downloadButton)
        buttons.addWidget(self.installButton)
        buttons.addWidget(self.closeButton)
        root.addLayout(buttons)

        self.saveUrlButton.clicked.connect(self._save_settings)
        self.checkButton.clicked.connect(self._check_for_updates)
        self.downloadButton.clicked.connect(self._download_update)
        self.installButton.clicked.connect(self._install_update)
        self.closeButton.clicked.connect(self.close)

    def _load_settings(self) -> None:
        settings = self.service.load_settings()
        self.manifestUrlEdit.setText(settings.manifest_url)

    def _save_settings(self) -> None:
        self.service.save_settings(
            UpdateSettings(manifest_url=self.manifestUrlEdit.text().strip())
        )
        self.statusLabel.setText("Update URL saved.")

    def _check_for_updates(self) -> None:
        self._set_busy(True, "Checking for updates...")
        manifest_url = self.manifestUrlEdit.text().strip()
        self.service.save_settings(UpdateSettings(manifest_url=manifest_url))
        task = WorkflowTask(lambda: self.service.check_for_updates(manifest_url))
        task.signals.finished.connect(self._check_finished)
        task.signals.failed.connect(self._task_failed)
        self._active_task = task
        QThreadPool.globalInstance().start(task)

    def _check_finished(self, result: object) -> None:
        self._set_busy(False)
        self._active_task = None
        if not isinstance(result, UpdateCheckResult):
            self.statusLabel.setText(str(result))
            return
        self._check_result = result
        self._downloaded_update = None
        self.installButton.setEnabled(False)
        if not result.update_available:
            self.downloadButton.setEnabled(False)
            self.statusLabel.setText(
                f"Already up to date: {result.current_version}."
            )
            self.detailsTextEdit.setPlainText("No newer version was found.")
            return
        package = result.package
        size_text = (
            _format_bytes(package.size_bytes)
            if package is not None and package.size_bytes is not None
            else "unknown size"
        )
        self.downloadButton.setEnabled(package is not None)
        self.statusLabel.setText(
            f"Update available: {result.current_version} -> {result.latest_version}."
        )
        notes = result.release_notes
        if not notes and package is not None:
            notes = package.notes
        self.detailsTextEdit.setPlainText(
            "Package: "
            f"{package.package_type if package is not None else 'none'} "
            f"({size_text})\n\n"
            f"{notes}"
        )

    def _download_update(self) -> None:
        if self._check_result is None:
            self.statusLabel.setText("Check for updates first.")
            return
        self._set_busy(True, "Downloading update...")
        self.progressBar.setValue(0)

        def action(progress):
            return self.service.download_update(
                self._check_result,
                progress_callback=lambda downloaded, total: progress(
                    {"downloaded": downloaded, "total": total}
                ),
            )

        task = WorkflowTask(action, emit_progress=True)
        task.signals.progress.connect(self._download_progress)
        task.signals.finished.connect(self._download_finished)
        task.signals.failed.connect(self._task_failed)
        self._active_task = task
        QThreadPool.globalInstance().start(task)

    def _download_progress(self, value: object) -> None:
        if not isinstance(value, dict):
            return
        downloaded = int(value.get("downloaded") or 0)
        total = value.get("total")
        if isinstance(total, int) and total > 0:
            self.progressBar.setValue(min(100, int(downloaded * 100 / total)))
            self.statusLabel.setText(
                f"Downloading update... {_format_bytes(downloaded)} / {_format_bytes(total)}"
            )
        else:
            self.statusLabel.setText(f"Downloading update... {_format_bytes(downloaded)}")

    def _download_finished(self, result: object) -> None:
        self._set_busy(False)
        self._active_task = None
        if not isinstance(result, DownloadedUpdate):
            self.statusLabel.setText(str(result))
            return
        self._downloaded_update = result
        self.progressBar.setValue(100)
        self.installButton.setEnabled(True)
        self.statusLabel.setText("Update downloaded and verified.")
        self.detailsTextEdit.append(f"\nDownloaded: {result.package_path}")

    def _install_update(self) -> None:
        if self._downloaded_update is None:
            self.statusLabel.setText("Download an update first.")
            return
        if not self.service.can_install_update():
            QMessageBox.information(
                self,
                "Packaged App Required",
                "Update installation is only available from the packaged app.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Update and Restart",
            "CoreFlow Studio will close, install the update, and restart. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.install_downloaded_update(self._downloaded_update)
        except Exception as exc:
            self.statusLabel.setText(f"Install failed: {exc}")
            return
        QCoreApplication.quit()

    def _task_failed(self, message: str) -> None:
        self._set_busy(False)
        self._active_task = None
        self.statusLabel.setText(f"Update failed: {message}")

    def _set_busy(self, busy: bool, message: str | None = None) -> None:
        self.saveUrlButton.setEnabled(not busy)
        self.checkButton.setEnabled(not busy)
        self.downloadButton.setEnabled(
            (not busy)
            and self._check_result is not None
            and self._check_result.update_available
            and self._check_result.package is not None
        )
        self.installButton.setEnabled((not busy) and self._downloaded_update is not None)
        if message is not None:
            self.statusLabel.setText(message)


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    units = ("B", "KB", "MB", "GB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return f"{number:.1f} GB"

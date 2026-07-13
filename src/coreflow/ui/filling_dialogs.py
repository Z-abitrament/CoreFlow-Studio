"""Device-selection dialogs for the Filling Module."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from coreflow.app import FillingTrialService


class NewFillingDeviceDialog(QDialog):
    """Create one insert-only shared device record."""

    def __init__(
        self,
        service: FillingTrialService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.created_device_id: str | None = None
        self.setObjectName("fillingNewDeviceDialog")
        self.setWindowTitle("New Filling Device")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.deviceIdLineEdit = QLineEdit()
        self.deviceIdLineEdit.setObjectName("fillingNewDeviceIdLineEdit")
        self.modelLineEdit = QLineEdit()
        self.modelLineEdit.setObjectName("fillingNewDeviceModelLineEdit")
        form.addRow("Device ID", self.deviceIdLineEdit)
        form.addRow("Model (optional)", self.modelLineEdit)
        root.addLayout(form)

        self.statusLabel = QLabel()
        self.statusLabel.setObjectName("fillingNewDeviceStatusLabel")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setStyleSheet("color: #a33b32;")
        root.addWidget(self.statusLabel)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancelButton = QPushButton("Cancel")
        self.cancelButton.setObjectName("fillingNewDeviceCancelButton")
        self.createButton = QPushButton("Create")
        self.createButton.setObjectName("fillingNewDeviceCreateButton")
        self.createButton.setDefault(True)
        actions.addWidget(self.cancelButton)
        actions.addWidget(self.createButton)
        root.addLayout(actions)

        self.cancelButton.clicked.connect(self.reject)
        self.createButton.clicked.connect(self._create_device)
        self.deviceIdLineEdit.returnPressed.connect(self._create_device)

    def _create_device(self) -> None:
        try:
            record = self.service.create_device(
                device_id=self.deviceIdLineEdit.text(),
                model=self.modelLineEdit.text().strip() or None,
            )
        except Exception as exc:
            self.statusLabel.setText(str(exc))
            self.deviceIdLineEdit.setFocus(Qt.FocusReason.OtherFocusReason)
            self.deviceIdLineEdit.selectAll()
            return
        self.created_device_id = record.device_id
        self.accept()


class FillingDeviceSelectionDialog(QDialog):
    """Select an existing shared Device ID for filling work."""

    def __init__(
        self,
        service: FillingTrialService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.newDeviceDialog: NewFillingDeviceDialog | None = None
        self.setObjectName("fillingDeviceSelectionDialog")
        self.setWindowTitle("Select Filling Device")
        self.setModal(True)
        self.setMinimumWidth(500)
        self._build_ui()
        self.refresh_devices()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        row = QHBoxLayout()
        label = QLabel("Device ID")
        label.setObjectName("fillingDeviceSelectionLabel")
        self.deviceCombo = QComboBox()
        self.deviceCombo.setObjectName("fillingDeviceSelectionCombo")
        self.deviceCombo.setEditable(False)
        self.deviceCombo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.deviceCombo.setMinimumContentsLength(24)
        row.addWidget(label)
        row.addWidget(self.deviceCombo, 1)
        root.addLayout(row)

        self.statusLabel = QLabel()
        self.statusLabel.setObjectName("fillingDeviceSelectionStatusLabel")
        self.statusLabel.setWordWrap(True)
        self.statusLabel.setStyleSheet("color: #a33b32;")
        root.addWidget(self.statusLabel)

        actions = QHBoxLayout()
        self.newDeviceButton = QPushButton("New Device...")
        self.newDeviceButton.setObjectName("fillingDeviceSelectionNewButton")
        actions.addWidget(self.newDeviceButton)
        actions.addStretch(1)
        self.cancelButton = QPushButton("Cancel")
        self.cancelButton.setObjectName("fillingDeviceSelectionCancelButton")
        self.selectButton = QPushButton("Select")
        self.selectButton.setObjectName("fillingDeviceSelectionSelectButton")
        self.selectButton.setDefault(True)
        actions.addWidget(self.cancelButton)
        actions.addWidget(self.selectButton)
        root.addLayout(actions)

        self.newDeviceButton.clicked.connect(self._open_new_device)
        self.cancelButton.clicked.connect(self.reject)
        self.selectButton.clicked.connect(self._select_device)
        self.deviceCombo.currentIndexChanged.connect(self._update_select_button)

    def selected_device_id(self) -> str | None:
        value = self.deviceCombo.currentData(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) and value else None

    def refresh_devices(self, selected_device_id: str | None = None) -> None:
        """Reload shared devices while retaining or selecting a stable ID."""

        desired = selected_device_id or self.selected_device_id()
        self.deviceCombo.clear()
        try:
            records = self.service.list_devices()
        except Exception as exc:
            self.statusLabel.setText(f"Unable to list devices: {exc}")
            self._update_select_button()
            return

        for record in records:
            display = record.device_id
            if record.model:
                display = f"{record.device_id}  |  {record.model}"
            self.deviceCombo.addItem(display, record.device_id)

        if desired is not None:
            index = self.deviceCombo.findData(desired)
            if index >= 0:
                self.deviceCombo.setCurrentIndex(index)
        self.statusLabel.clear()
        self._update_select_button()

    def _update_select_button(self, _index: int | None = None) -> None:
        self.selectButton.setEnabled(self.selected_device_id() is not None)

    def _select_device(self) -> None:
        if self.selected_device_id() is None:
            self.statusLabel.setText("Select a Device ID.")
            self.deviceCombo.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self.accept()

    def _open_new_device(self) -> None:
        if self.newDeviceDialog is not None and self.newDeviceDialog.isVisible():
            self.newDeviceDialog.raise_()
            self.newDeviceDialog.activateWindow()
            return
        dialog = NewFillingDeviceDialog(self.service, parent=self)
        dialog.accepted.connect(lambda: self._device_created(dialog))
        self.newDeviceDialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _device_created(self, dialog: NewFillingDeviceDialog) -> None:
        self.refresh_devices(dialog.created_device_id)


__all__ = [
    "FillingDeviceSelectionDialog",
    "NewFillingDeviceDialog",
]

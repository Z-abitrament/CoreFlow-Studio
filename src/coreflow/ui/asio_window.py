"""Independent ASIO/IIS module window."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from coreflow.protocols.asio import (
    AsioIisFrameConfig,
    AsioLoopbackMetrics,
    AsioLoopbackThresholds,
    AsioRegistryScanner,
    NativeAsioDriverProbe,
    build_asio_backend,
)
from coreflow.protocols.asio.backend import AsioIisBackend
from coreflow.protocols.asio.loopback import compare_loopback_capture
from coreflow.protocols.asio.models import AsioStreamDiagnostics
from coreflow.ui.workers import WorkflowTask


DEFAULT_FRAME_COUNT = 8
DEFAULT_MAX_LATENCY_SAMPLES = 12_000


@dataclass(frozen=True, slots=True)
class AsioTestData:
    """Small plotting payload returned from an ASIO/IIS test run."""

    title: str
    output: np.ndarray
    captured: np.ndarray
    aligned_captured: np.ndarray
    diagnostics: AsioStreamDiagnostics
    metrics: AsioLoopbackMetrics | None
    summary: str


class AsioIisWindow(QDialog):
    """ASIO/IIS controls that are independent from device communication channels."""

    def __init__(
        self,
        thread_pool: QThreadPool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._connected = False
        self._active_tasks: list[WorkflowTask] = []
        self.testWindow: AsioIisTestWindow | None = None

        self.setWindowTitle("ASIO/IIS Module")
        self.resize(560, 430)
        self.setMinimumSize(520, 380)
        self._build_ui()
        self._connect_signals()
        self._set_connected(False, "Disconnected")
        self.refresh_devices()
        self._log("Ready. This module connection is independent from transmitter channels.")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._status_panel())
        root.addWidget(self._parameters_panel())
        root.addWidget(self._log_panel(), 1)

    def _status_panel(self) -> QWidget:
        group = QGroupBox("Module")
        layout = QHBoxLayout(group)
        self.statusValueLabel = QLabel("Disconnected")
        self.statusValueLabel.setObjectName("asioStatusValueLabel")
        self.statusValueLabel.setMinimumWidth(140)
        self.connectButton = QPushButton("Connect")
        self.connectButton.setObjectName("asioConnectButton")
        self.disconnectButton = QPushButton("Disconnect")
        self.disconnectButton.setObjectName("asioDisconnectButton")
        self.refreshDevicesButton = QPushButton("Refresh Devices")
        self.refreshDevicesButton.setObjectName("asioRefreshDevicesButton")
        self.probeButton = QPushButton("Probe")
        self.probeButton.setObjectName("asioProbeButton")
        self.openTestButton = QPushButton("Tests")
        self.openTestButton.setObjectName("asioOpenTestButton")
        layout.addWidget(QLabel("Status"))
        layout.addWidget(self.statusValueLabel, 1)
        layout.addWidget(self.connectButton)
        layout.addWidget(self.disconnectButton)
        layout.addWidget(self.refreshDevicesButton)
        layout.addWidget(self.probeButton)
        layout.addWidget(self.openTestButton)
        return group

    def _parameters_panel(self) -> QWidget:
        group = QGroupBox("Parameters")
        form = QFormLayout(group)

        self.backendCombo = QComboBox()
        self.backendCombo.setObjectName("asioBackendCombo")
        self.backendCombo.addItems(["native", "fake", "sounddevice"])

        self.deviceCombo = QComboBox()
        self.deviceCombo.setObjectName("asioDeviceCombo")
        self.deviceCombo.setEditable(False)

        self.sampleRateSpinBox = QSpinBox()
        self.sampleRateSpinBox.setObjectName("asioSampleRateSpinBox")
        self.sampleRateSpinBox.setRange(8_000, 384_000)
        self.sampleRateSpinBox.setValue(44_100)

        self.bitDepthCombo = QComboBox()
        self.bitDepthCombo.setObjectName("asioBitDepthCombo")
        self.bitDepthCombo.addItems(["24", "16", "32"])

        self.sampleFormatCombo = QComboBox()
        self.sampleFormatCombo.setObjectName("asioSampleFormatCombo")
        self.sampleFormatCombo.addItems(["int24", "float32", "int16", "int32"])

        self.inputChannelsCombo = QComboBox()
        self.inputChannelsCombo.setObjectName("asioInputChannelsCombo")
        self.inputChannelsCombo.addItems(["1", "2"])
        self.inputChannelsCombo.setCurrentText("2")

        self.outputChannelsCombo = QComboBox()
        self.outputChannelsCombo.setObjectName("asioOutputChannelsCombo")
        self.outputChannelsCombo.addItems(["1", "2"])
        self.outputChannelsCombo.setCurrentText("2")

        self.frameSamplesSpinBox = QSpinBox()
        self.frameSamplesSpinBox.setObjectName("asioFrameSamplesSpinBox")
        self.frameSamplesSpinBox.setRange(1, 262_144)
        self.frameSamplesSpinBox.setValue(4_410)

        self.amplitudeSpinBox = QDoubleSpinBox()
        self.amplitudeSpinBox.setObjectName("asioAmplitudeSpinBox")
        self.amplitudeSpinBox.setDecimals(3)
        self.amplitudeSpinBox.setRange(0.001, 0.95)
        self.amplitudeSpinBox.setSingleStep(0.01)
        self.amplitudeSpinBox.setValue(0.1)

        form.addRow("Backend", self.backendCombo)
        form.addRow("Device", self.deviceCombo)
        form.addRow("Sample Rate", self.sampleRateSpinBox)
        form.addRow("Bit Depth", self.bitDepthCombo)
        form.addRow("Sample Format", self.sampleFormatCombo)
        form.addRow("Input Channels", self.inputChannelsCombo)
        form.addRow("Output Channels", self.outputChannelsCombo)
        form.addRow("Samples / Frame", self.frameSamplesSpinBox)
        form.addRow("Test Amplitude", self.amplitudeSpinBox)
        return group

    def _log_panel(self) -> QWidget:
        group = QGroupBox("Status Log")
        layout = QVBoxLayout(group)
        self.logTextEdit = QPlainTextEdit()
        self.logTextEdit.setObjectName("asioLogTextEdit")
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMinimumHeight(120)
        layout.addWidget(self.logTextEdit)
        return group

    def _connect_signals(self) -> None:
        self.connectButton.clicked.connect(self._connect_module)
        self.disconnectButton.clicked.connect(self._disconnect_module)
        self.refreshDevicesButton.clicked.connect(self.refresh_devices)
        self.probeButton.clicked.connect(self._probe_module)
        self.openTestButton.clicked.connect(self._open_test_window)
        self.backendCombo.currentTextChanged.connect(lambda _backend: self.refresh_devices())
        self.bitDepthCombo.currentTextChanged.connect(self._sync_sample_format)

    def refresh_devices(self) -> None:
        previous = self.device_name()
        self.deviceCombo.clear()
        try:
            devices = self._discover_devices()
        except Exception as exc:
            self.deviceCombo.addItem("BRAVO-HD")
            self._log(f"Device discovery failed: {_format_native_error(exc)}")
            return
        for name in devices:
            self.deviceCombo.addItem(name)
        if not devices:
            self.deviceCombo.addItem("BRAVO-HD")
            self._log("No ASIO/IIS devices discovered; using BRAVO-HD as a placeholder.")
        elif previous:
            index = self.deviceCombo.findText(previous)
            if index >= 0:
                self.deviceCombo.setCurrentIndex(index)
        self._log(f"Discovered {self.deviceCombo.count()} device option(s).")

    def device_name(self) -> str:
        return self.deviceCombo.currentText().strip()

    def _discover_devices(self) -> tuple[str, ...]:
        backend_name = self.backendCombo.currentText()
        if backend_name == "native":
            return tuple(driver.name for driver in AsioRegistryScanner().list_drivers() if driver.clsid)
        devices = build_asio_backend(backend_name).list_devices()
        names = tuple(device.name for device in devices if device.max_input_channels or device.max_output_channels)
        return names

    def _apply_capabilities(self, sample_rate: float, preferred_buffer_size: int) -> None:
        self.sampleRateSpinBox.setValue(int(round(sample_rate)))
        self.frameSamplesSpinBox.setValue(preferred_buffer_size)

    def _connect_module(self) -> None:
        if self._connected:
            self._log("Already connected.")
            return
        if self.backendCombo.currentText() == "native":
            self._run_native_ui_action("Connect", self._connect_action)
            return
        self._run_task("Connect", self._connect_action)

    def _disconnect_module(self) -> None:
        if not self._connected:
            self._log("Already disconnected.")
            return
        self._set_connected(False, "Disconnected")
        self._log("Disconnected. Other communication channels were not changed.")

    def _probe_module(self) -> None:
        if self.backendCombo.currentText() == "native":
            self._run_native_ui_action("Probe", self._probe_action)
            return
        self._run_task("Probe", self._probe_action)

    def _open_test_window(self) -> None:
        if self.testWindow is None:
            self.testWindow = AsioIisTestWindow(
                config_provider=self._config,
                backend_provider=lambda: build_asio_backend(self.backendCombo.currentText()),
                thread_pool=self._thread_pool,
                parent=self,
            )
        self.testWindow.show()
        self.testWindow.raise_()
        self.testWindow.activateWindow()

    def _connect_action(self) -> str:
        backend_name = self.backendCombo.currentText()
        if backend_name == "native":
            capabilities = NativeAsioDriverProbe(driver_name=self.device_name()).query_capabilities()
            self._apply_capabilities(capabilities.sample_rate, capabilities.preferred_buffer_size)
            return (
                "Native ASIO ready: "
                f"{capabilities.driver_name}, "
                f"{capabilities.sample_rate:g} Hz, "
                f"{capabilities.input_channels} in/{capabilities.output_channels} out, "
                f"preferred buffer {capabilities.preferred_buffer_size}."
            )
        devices = build_asio_backend(backend_name).list_devices()
        matched = [
            device
            for device in devices
            if self.device_name().casefold() in device.name.casefold()
        ]
        if not matched:
            raise RuntimeError(f"No {backend_name} ASIO/IIS device matched {self.device_name()!r}.")
        device = matched[0]
        return (
            "Backend ready: "
            f"{device.name}, {device.host_api}, "
            f"{device.max_input_channels} in/{device.max_output_channels} out."
        )

    def _probe_action(self) -> str:
        backend_name = self.backendCombo.currentText()
        if backend_name == "native":
            capabilities = NativeAsioDriverProbe(driver_name=self.device_name()).query_capabilities()
            self._apply_capabilities(capabilities.sample_rate, capabilities.preferred_buffer_size)
            return (
                "Native ASIO probe: "
                f"{capabilities.driver_name}, "
                f"{capabilities.sample_rate:g} Hz, "
                f"{capabilities.input_channels} in/{capabilities.output_channels} out, "
                f"sample={capabilities.channels[0].sample_type_name if capabilities.channels else 'unknown'}, "
                f"preferred buffer {capabilities.preferred_buffer_size}."
            )
        devices = build_asio_backend(backend_name).list_devices()
        lines = [
            f"{device.index}: {device.name} ({device.host_api}) "
            f"in={device.max_input_channels} out={device.max_output_channels}"
            for device in devices
        ]
        return "\n".join(lines) if lines else "No devices reported by backend."

    def _run_task(self, label: str, action: Callable[[], str]) -> None:
        self._set_controls_enabled(False)
        self._log(f"{label} started.")
        task = WorkflowTask(action)
        task.signals.finished.connect(lambda message: self._task_finished(label, message))
        task.signals.failed.connect(lambda message: self._task_failed(label, message))
        self._active_tasks.append(task)
        self._thread_pool.start(task)

    def _run_native_ui_action(self, label: str, action: Callable[[], str]) -> None:
        self._set_controls_enabled(False)
        self._log(f"{label} started.")
        try:
            message = action()
        except Exception as exc:
            self._task_failed(label, _format_native_error(exc))
        else:
            self._task_finished(label, message)

    def _task_finished(self, label: str, message: object) -> None:
        if label == "Connect":
            self._set_connected(True, "Connected")
        self._set_controls_enabled(True)
        self._active_tasks.clear()
        self._log(str(message))

    def _task_failed(self, label: str, message: str) -> None:
        if label == "Connect":
            self._set_connected(False, "Disconnected")
        self._set_controls_enabled(True)
        self._active_tasks.clear()
        self._log(f"{label} failed: {message}")

    def _set_connected(self, connected: bool, text: str) -> None:
        self._connected = connected
        self.statusValueLabel.setText(text)
        self.connectButton.setEnabled(not connected)
        self.disconnectButton.setEnabled(connected)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.connectButton.setEnabled(enabled and not self._connected)
        self.disconnectButton.setEnabled(enabled and self._connected)
        self.refreshDevicesButton.setEnabled(enabled)
        self.probeButton.setEnabled(enabled)
        self.openTestButton.setEnabled(enabled)
        for widget in (
            self.backendCombo,
            self.deviceCombo,
            self.sampleRateSpinBox,
            self.bitDepthCombo,
            self.sampleFormatCombo,
            self.inputChannelsCombo,
            self.outputChannelsCombo,
            self.frameSamplesSpinBox,
            self.amplitudeSpinBox,
        ):
            widget.setEnabled(enabled)

    def _config(self) -> AsioIisFrameConfig:
        return AsioIisFrameConfig(
            device_name=self.device_name(),
            sample_rate=self.sampleRateSpinBox.value(),
            bit_depth=int(self.bitDepthCombo.currentText()),
            sample_format=self.sampleFormatCombo.currentText(),
            input_channels=int(self.inputChannelsCombo.currentText()),
            output_channels=int(self.outputChannelsCombo.currentText()),
            samples_per_frame=self.frameSamplesSpinBox.value(),
            frame_count=DEFAULT_FRAME_COUNT,
            amplitude=self.amplitudeSpinBox.value(),
        )

    def _sync_sample_format(self, bit_depth: str) -> None:
        preferred = {"16": "int16", "24": "int24", "32": "float32"}.get(bit_depth)
        if preferred:
            self.sampleFormatCombo.setCurrentText(preferred)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logTextEdit.appendPlainText(f"[{timestamp}] {message}")
        self.logTextEdit.verticalScrollBar().setValue(
            self.logTextEdit.verticalScrollBar().maximum()
        )


class AsioIisTestWindow(QDialog):
    """Loopback and live stream check window with plots."""

    def __init__(
        self,
        *,
        config_provider: Callable[[], AsioIisFrameConfig],
        backend_provider: Callable[[], AsioIisBackend],
        thread_pool: QThreadPool | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        self._backend_provider = backend_provider
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._active_tasks: list[WorkflowTask] = []
        self.setWindowTitle("ASIO/IIS Tests")
        self.resize(860, 620)
        self.setMinimumSize(760, 520)
        self._build_ui()
        self._connect_signals()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        signal_group = QGroupBox("Signal")
        signal_form = QFormLayout(signal_group)
        self.signalTypeCombo = QComboBox()
        self.signalTypeCombo.setObjectName("asioTestSignalTypeCombo")
        self.signalTypeCombo.addItems(["Sine", "Square", "White Noise"])

        self.signalAmplitudeSpinBox = QDoubleSpinBox()
        self.signalAmplitudeSpinBox.setObjectName("asioTestAmplitudeSpinBox")
        self.signalAmplitudeSpinBox.setDecimals(3)
        self.signalAmplitudeSpinBox.setRange(0.001, 0.95)
        self.signalAmplitudeSpinBox.setSingleStep(0.01)
        self.signalAmplitudeSpinBox.setValue(0.1)

        self.signalFrequencySpinBox = QDoubleSpinBox()
        self.signalFrequencySpinBox.setObjectName("asioTestFrequencySpinBox")
        self.signalFrequencySpinBox.setDecimals(2)
        self.signalFrequencySpinBox.setRange(0.1, 20_000.0)
        self.signalFrequencySpinBox.setSingleStep(10.0)
        self.signalFrequencySpinBox.setValue(100.0)

        self.displayModeCombo = QComboBox()
        self.displayModeCombo.setObjectName("asioTestDisplayModeCombo")
        self.displayModeCombo.addItems(["Input + Output", "Input Only", "Output Only"])

        signal_form.addRow("Waveform", self.signalTypeCombo)
        signal_form.addRow("Amplitude", self.signalAmplitudeSpinBox)
        signal_form.addRow("Frequency", self.signalFrequencySpinBox)
        signal_form.addRow("Display", self.displayModeCombo)
        root.addWidget(signal_group)

        actions = QGroupBox("Test")
        action_layout = QHBoxLayout(actions)
        self.loopbackTestButton = QPushButton("Loopback")
        self.loopbackTestButton.setObjectName("asioLoopbackTestButton")
        self.liveTestButton = QPushButton("Non-Loopback")
        self.liveTestButton.setObjectName("asioLiveTestButton")
        self.testStatusLabel = QLabel("Idle")
        self.testStatusLabel.setObjectName("asioTestStatusLabel")
        action_layout.addWidget(self.loopbackTestButton)
        action_layout.addWidget(self.liveTestButton)
        action_layout.addWidget(self.testStatusLabel, 1)
        root.addWidget(actions)

        self.summaryTextEdit = QPlainTextEdit()
        self.summaryTextEdit.setObjectName("asioTestSummaryTextEdit")
        self.summaryTextEdit.setReadOnly(True)
        self.summaryTextEdit.setMaximumHeight(120)
        root.addWidget(self.summaryTextEdit)

        self.signalPlot = self._plot("Input / Output")
        self.signalPlot.setObjectName("asioTestSignalPlot")
        root.addWidget(self.signalPlot, 1)

    def _plot(self, title: str) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.setTitle(title)
        plot.setLabel("left", "Normalized")
        plot.setLabel("bottom", "Sample")
        plot.showGrid(x=True, y=True, alpha=0.25)
        plot.addLegend(offset=(-12, 12))
        return plot

    def _connect_signals(self) -> None:
        self.loopbackTestButton.clicked.connect(self._run_loopback_test)
        self.liveTestButton.clicked.connect(self._run_live_test)
        self.displayModeCombo.currentTextChanged.connect(lambda _mode: self._replot_last_data())

    def _run_loopback_test(self) -> None:
        self._run_task("Loopback", self._loopback_action)

    def _run_live_test(self) -> None:
        self._run_task("Non-Loopback", self._live_action)

    def _loopback_action(self) -> AsioTestData:
        config = self._config_provider()
        output = self._generate_test_signal(config)
        capture = self._backend_provider().run_full_duplex(config, output)
        metrics = compare_loopback_capture(
            generated=output,
            captured=capture.captured,
            thresholds=AsioLoopbackThresholds(max_latency_samples=DEFAULT_MAX_LATENCY_SAMPLES),
        )
        return AsioTestData(
            title="Loopback",
            output=output,
            captured=capture.captured,
            aligned_captured=_align_captured_to_output(
                capture.captured,
                metrics.delay_samples,
                output.shape[0],
            ),
            diagnostics=capture.diagnostics,
            metrics=metrics,
            summary=(
                f"Loopback {'passed' if metrics.passed else 'failed'}: "
                f"correlation={metrics.correlation:.6f}, "
                f"error={metrics.normalized_error:.6g}, "
                f"delay={metrics.delay_samples} samples, "
                f"input_rms={metrics.input_rms:.6g}. "
                f"{metrics.message}"
            ),
        )

    def _live_action(self) -> AsioTestData:
        config = self._config_provider()
        output = self._generate_test_signal(config)
        capture = self._backend_provider().run_full_duplex(config, output)
        output_rms = _rms(output)
        input_rms = _rms(capture.captured)
        summary = (
            "Non-loopback check completed: "
            f"output_rms={output_rms:.6g}, "
            f"input_rms={input_rms:.6g}, "
            f"captured_samples={capture.captured.shape[0]}."
        )
        return AsioTestData(
            title="Non-Loopback",
            output=output,
            captured=capture.captured,
            aligned_captured=capture.captured,
            diagnostics=capture.diagnostics,
            metrics=None,
            summary=summary,
        )

    def _generate_test_signal(self, config: AsioIisFrameConfig) -> np.ndarray:
        signal = generate_test_signal(
            signal_type=self.signalTypeCombo.currentText(),
            sample_rate=config.sample_rate,
            total_samples=config.total_samples,
            channels=config.output_channels,
            amplitude=self.signalAmplitudeSpinBox.value(),
            frequency_hz=self.signalFrequencySpinBox.value(),
            seed=config.seed,
        )
        return signal

    def _run_task(self, label: str, action: Callable[[], AsioTestData]) -> None:
        self._set_running(True, label)
        task = WorkflowTask(action)
        task.signals.finished.connect(self._task_finished)
        task.signals.failed.connect(self._task_failed)
        self._active_tasks.append(task)
        self._thread_pool.start(task)

    def _task_finished(self, result: object) -> None:
        self._set_running(False, "Done")
        self._active_tasks.clear()
        data = result if isinstance(result, AsioTestData) else None
        if data is None:
            self.summaryTextEdit.setPlainText(str(result))
            return
        self._last_data = data
        self.summaryTextEdit.setPlainText(self._format_summary(data))
        self._plot_data(data)

    def _task_failed(self, message: str) -> None:
        self._set_running(False, "Failed")
        self._active_tasks.clear()
        self.summaryTextEdit.setPlainText(message)

    def _set_running(self, running: bool, label: str) -> None:
        self.testStatusLabel.setText(label)
        self.loopbackTestButton.setEnabled(not running)
        self.liveTestButton.setEnabled(not running)

    def _format_summary(self, data: AsioTestData) -> str:
        lines = [
            data.summary,
            (
                "Diagnostics: "
                f"backend={data.diagnostics.backend}, "
                f"device={data.diagnostics.device_name}, "
                f"sample_rate={data.diagnostics.sample_rate}, "
                f"duration={data.diagnostics.duration_s:.3f}s"
                if data.diagnostics.duration_s is not None
                else "Diagnostics unavailable."
            ),
        ]
        if data.metrics is not None:
            lines.append(
                "Metrics: "
                f"compared={data.metrics.compared_samples}, "
                f"gain={data.metrics.estimated_gain:.6g}, "
                f"passed={data.metrics.passed}"
            )
        return "\n".join(lines)

    def _plot_data(self, data: AsioTestData) -> None:
        self.signalPlot.clear()
        mode = self.displayModeCombo.currentText()
        if mode in ("Input + Output", "Output Only"):
            _plot_channels(
                self.signalPlot,
                data.output,
                ("Output Drive", "Output R"),
                prefix="output",
                colors=("#2563eb", "#60a5fa"),
            )
        if mode in ("Input + Output", "Input Only"):
            _plot_channels(
                self.signalPlot,
                data.aligned_captured,
                ("Input L", "Input R"),
                prefix="input",
                colors=("#dc2626", "#f97316"),
            )

    def _replot_last_data(self) -> None:
        data = getattr(self, "_last_data", None)
        if isinstance(data, AsioTestData):
            self._plot_data(data)


def generate_test_signal(
    *,
    signal_type: str,
    sample_rate: int,
    total_samples: int,
    channels: int,
    amplitude: float,
    frequency_hz: float,
    seed: int,
) -> np.ndarray:
    time_values = np.arange(total_samples, dtype=np.float32) / np.float32(sample_rate)
    normalized = signal_type.casefold()
    if normalized == "sine":
        base = np.sin(2.0 * np.pi * frequency_hz * time_values)
    elif normalized == "square":
        base = np.where(
            np.sin(2.0 * np.pi * frequency_hz * time_values) >= 0.0,
            1.0,
            -1.0,
        )
    elif normalized == "white noise":
        rng = np.random.default_rng(seed)
        base = rng.normal(0.0, 1.0, total_samples)
        peak = float(np.max(np.abs(base)))
        if peak > 0.0:
            base = base / peak
    else:
        base = np.zeros(total_samples, dtype=np.float32)
    output = (np.asarray(base, dtype=np.float32) * np.float32(amplitude)).reshape(-1, 1)
    if channels <= 1:
        return output
    return np.repeat(output, channels, axis=1)


def _plot_channels(
    plot: pg.PlotWidget,
    values: np.ndarray,
    labels: tuple[str, ...],
    *,
    prefix: str,
    colors: tuple[str, ...],
) -> None:
    if values.size == 0:
        return
    limit = min(values.shape[0], 4_000)
    x_values = list(range(limit))
    for channel in range(values.shape[1]):
        name = labels[channel] if channel < len(labels) else f"Channel {channel + 1}"
        plot.plot(
            x_values,
            values[:limit, channel].tolist(),
            pen=pg.mkPen(colors[channel % len(colors)], width=1.8),
            name=f"{prefix}: {name}",
        )


def _align_captured_to_output(
    captured: np.ndarray,
    delay_samples: int,
    output_sample_count: int,
) -> np.ndarray:
    aligned = np.zeros((output_sample_count, captured.shape[1]), dtype=np.float32)
    if delay_samples >= 0:
        available = min(output_sample_count, max(0, captured.shape[0] - delay_samples))
        if available > 0:
            aligned[:available, :] = captured[delay_samples : delay_samples + available, :]
        return aligned
    lead = -delay_samples
    available = min(max(0, output_sample_count - lead), captured.shape[0])
    if available > 0:
        aligned[lead : lead + available, :] = captured[:available, :]
    return aligned


def _rms(values: np.ndarray) -> float:
    if values.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(values.astype(np.float64)))))


def _format_native_error(exc: Exception) -> str:
    message = str(exc)
    if "-2147467259" in message or "0x80004005" in message:
        return (
            f"{message}. Native ASIO driver returned E_FAIL; close other audio "
            "clients or the BRAVO-HD control panel, then retry."
        )
    return message

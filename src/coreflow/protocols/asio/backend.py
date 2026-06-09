"""Backend adapters for headless ASIO/IIS frame streams."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import numpy as np

from coreflow.protocols.asio.models import (
    AsioBackendUnavailable,
    AsioCapabilityError,
    AsioDeviceInfo,
    AsioDeviceNotFound,
    AsioIisFrameConfig,
    AsioSampleFormat,
    AsioStreamDiagnostics,
)


@dataclass(frozen=True, slots=True)
class AsioCaptureResult:
    """Captured input samples and backend diagnostics."""

    captured: np.ndarray
    diagnostics: AsioStreamDiagnostics


class AsioIisBackend(Protocol):
    """Minimal backend contract for frame-stream loopback tests."""

    name: str

    def list_devices(self) -> tuple[AsioDeviceInfo, ...]: ...

    def run_full_duplex(
        self,
        config: AsioIisFrameConfig,
        output: np.ndarray,
    ) -> AsioCaptureResult: ...


@dataclass(slots=True)
class FakeAsioIisBackend:
    """Deterministic fake backend used by tests and no-hardware diagnostics."""

    delay_samples: int = 8
    gain: float = 1.0
    noise_rms: float = 0.0
    devices: tuple[AsioDeviceInfo, ...] = (
        AsioDeviceInfo(
            name="BRAVO-HD Device Control",
            host_api="ASIO",
            index=0,
            max_input_channels=2,
            max_output_channels=2,
            default_sample_rate=48_000,
            is_default_input=True,
            is_default_output=True,
        ),
    )
    name: str = "fake"

    def list_devices(self) -> tuple[AsioDeviceInfo, ...]:
        return self.devices

    def run_full_duplex(
        self,
        config: AsioIisFrameConfig,
        output: np.ndarray,
    ) -> AsioCaptureResult:
        device = _select_full_duplex_device(self.devices, config)
        output = _ensure_2d_float(output)
        if output.shape != (config.total_samples, config.output_channels):
            raise AsioCapabilityError(
                "Output payload shape does not match ASIO frame configuration: "
                f"expected {(config.total_samples, config.output_channels)}, "
                f"got {output.shape}."
            )
        captured = np.zeros((config.total_samples, config.input_channels), dtype=np.float32)
        common_channels = min(config.input_channels, config.output_channels)
        if self.delay_samples < config.total_samples:
            source_end = config.total_samples - max(self.delay_samples, 0)
            target_start = max(self.delay_samples, 0)
            captured[target_start:, :common_channels] = (
                output[:source_end, :common_channels] * self.gain
            )
        if self.noise_rms:
            rng = np.random.default_rng(config.seed + 1)
            captured += rng.normal(0.0, self.noise_rms, captured.shape).astype(np.float32)
        return AsioCaptureResult(
            captured=captured,
            diagnostics=AsioStreamDiagnostics(
                backend=self.name,
                device_name=device.name,
                host_api=device.host_api,
                input_device_index=device.index,
                output_device_index=device.index,
                sample_rate=config.sample_rate,
                duration_s=config.duration_s,
                messages=(f"fake_delay_samples={self.delay_samples}",),
            ),
        )


@dataclass(slots=True)
class SoundDeviceAsioBackend:
    """Lazy `sounddevice`/PortAudio backend for Windows ASIO devices."""

    module_loader: Callable[[], Any] | None = None
    name: str = "sounddevice"
    _module: Any | None = field(default=None, init=False, repr=False)

    def list_devices(self) -> tuple[AsioDeviceInfo, ...]:
        sounddevice = self._load_sounddevice()
        try:
            raw_host_apis = sounddevice.query_hostapis()
            raw_devices = sounddevice.query_devices()
        except Exception as exc:  # pragma: no cover - depends on host audio stack
            raise AsioBackendUnavailable(
                f"Unable to query Windows audio devices through sounddevice: {exc}"
            ) from exc
        host_apis = _as_tuple(raw_host_apis)
        default_input = _default_device_index(sounddevice, "input")
        default_output = _default_device_index(sounddevice, "output")
        devices: list[AsioDeviceInfo] = []
        for index, item in enumerate(_as_tuple(raw_devices)):
            host_api_index = int(item.get("hostapi", -1))
            host_api_name = (
                host_apis[host_api_index].get("name", "unknown")
                if 0 <= host_api_index < len(host_apis)
                else "unknown"
            )
            devices.append(
                AsioDeviceInfo(
                    name=str(item.get("name", "")),
                    host_api=host_api_name,
                    index=index,
                    max_input_channels=int(item.get("max_input_channels", 0)),
                    max_output_channels=int(item.get("max_output_channels", 0)),
                    default_sample_rate=float(item["default_samplerate"])
                    if item.get("default_samplerate") is not None
                    else None,
                    is_default_input=index == default_input,
                    is_default_output=index == default_output,
                    metadata={"raw": dict(item)},
                )
            )
        return tuple(devices)

    def run_full_duplex(
        self,
        config: AsioIisFrameConfig,
        output: np.ndarray,
    ) -> AsioCaptureResult:
        sounddevice = self._load_sounddevice()
        devices = self.list_devices()
        input_device, output_device = _select_device_pair(devices, config)
        output = _prepare_sounddevice_output(output, config)
        try:
            captured = sounddevice.playrec(
                output,
                samplerate=config.sample_rate,
                channels=config.input_channels,
                device=(input_device.index, output_device.index),
                blocking=True,
                dtype=config.sample_format.value,
                blocksize=config.samples_per_frame,
            )
        except Exception as exc:  # pragma: no cover - depends on hardware
            raise AsioBackendUnavailable(
                "Unable to run ASIO/IIS full-duplex stream through sounddevice: "
                f"{exc}"
            ) from exc
        return AsioCaptureResult(
            captured=_normalize_captured(captured, config.sample_format),
            diagnostics=AsioStreamDiagnostics(
                backend=self.name,
                device_name=output_device.name
                if input_device.index == output_device.index
                else f"input={input_device.name}; output={output_device.name}",
                host_api=config.host_api,
                input_device_index=input_device.index,
                output_device_index=output_device.index,
                sample_rate=config.sample_rate,
                duration_s=config.duration_s,
                input_latency_s=_latency_s(input_device.metadata),
                output_latency_s=_latency_s(output_device.metadata),
                messages=(
                    "sounddevice uses the installed PortAudio build; ASIO is "
                    "available only when that build exposes the ASIO host API.",
                ),
            ),
        )

    def _load_sounddevice(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            module = (
                self.module_loader()
                if self.module_loader is not None
                else importlib.import_module("sounddevice")
            )
        except ModuleNotFoundError as exc:
            raise AsioBackendUnavailable(
                "Optional ASIO backend dependency `sounddevice` is not installed. "
                "Install the ASIO extra or add a PortAudio/sounddevice build that "
                "exposes the Windows ASIO host API."
            ) from exc
        object.__setattr__(self, "_module", module)
        return module


def build_asio_backend(name: str) -> AsioIisBackend:
    """Construct a backend by CLI-friendly name."""

    normalized = name.casefold()
    if normalized == "fake":
        return FakeAsioIisBackend()
    if normalized in ("auto", "sounddevice"):
        return SoundDeviceAsioBackend()
    if normalized == "native":
        from coreflow.protocols.asio.native import NativeAsioIisBackend

        return NativeAsioIisBackend()
    raise ValueError(f"Unknown ASIO backend: {name}")


def _select_full_duplex_device(
    devices: tuple[AsioDeviceInfo, ...],
    config: AsioIisFrameConfig,
) -> AsioDeviceInfo:
    matching_host_api = [
        device
        for device in devices
        if device.host_api.casefold() == config.host_api.casefold()
    ]
    if not matching_host_api:
        raise AsioBackendUnavailable(
            f"Audio host API {config.host_api!r} is not available."
        )
    matching_name = [
        device
        for device in matching_host_api
        if config.device_name.casefold() in device.name.casefold()
    ]
    if not matching_name:
        raise AsioDeviceNotFound(
            f"ASIO device containing {config.device_name!r} was not found."
        )
    for device in matching_name:
        if device.supports(config):
            return device
    raise AsioCapabilityError(
        "Matched ASIO device does not support requested channel counts: "
        f"input={config.input_channels}, output={config.output_channels}."
    )


def _select_device_pair(
    devices: tuple[AsioDeviceInfo, ...],
    config: AsioIisFrameConfig,
) -> tuple[AsioDeviceInfo, AsioDeviceInfo]:
    matching_host_api = [
        device
        for device in devices
        if device.host_api.casefold() == config.host_api.casefold()
    ]
    if not matching_host_api:
        raise AsioBackendUnavailable(
            f"Audio host API {config.host_api!r} is not available."
        )
    matching_name = [
        device
        for device in matching_host_api
        if config.device_name.casefold() in device.name.casefold()
    ]
    if not matching_name:
        raise AsioDeviceNotFound(
            f"ASIO device containing {config.device_name!r} was not found."
        )
    input_candidates = [
        device
        for device in matching_name
        if device.max_input_channels >= config.input_channels + config.input_channel_offset
    ]
    output_candidates = [
        device
        for device in matching_name
        if device.max_output_channels
        >= config.output_channels + config.output_channel_offset
    ]
    for device in matching_name:
        if device in input_candidates and device in output_candidates:
            return device, device
    if input_candidates and output_candidates:
        return input_candidates[0], output_candidates[0]
    raise AsioCapabilityError(
        "Matched ASIO device cannot satisfy requested full-duplex channel counts: "
        f"input={config.input_channels}, output={config.output_channels}."
    )


def _prepare_sounddevice_output(
    output: np.ndarray,
    config: AsioIisFrameConfig,
) -> np.ndarray:
    output = _ensure_2d_float(output)
    if output.shape != (config.total_samples, config.output_channels):
        raise AsioCapabilityError(
            "Output payload shape does not match ASIO frame configuration: "
            f"expected {(config.total_samples, config.output_channels)}, "
            f"got {output.shape}."
        )
    if config.sample_format is AsioSampleFormat.FLOAT32:
        return output.astype(np.float32, copy=False)
    if config.sample_format is AsioSampleFormat.INT16:
        return np.round(np.clip(output, -1.0, 1.0) * 32767.0).astype(np.int16)
    return np.round(np.clip(output, -1.0, 1.0) * 2147483647.0).astype(np.int32)


def _normalize_captured(
    captured: np.ndarray,
    sample_format: AsioSampleFormat,
) -> np.ndarray:
    if sample_format is AsioSampleFormat.INT16:
        return captured.astype(np.float32) / 32768.0
    if sample_format is AsioSampleFormat.INT32:
        return captured.astype(np.float32) / 2147483648.0
    return captured.astype(np.float32, copy=False)


def _ensure_2d_float(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2:
        raise AsioCapabilityError(f"ASIO frame payload must be 2-D, got {array.ndim}-D.")
    return array


def _default_device_index(sounddevice: Any, kind: str) -> int | None:
    try:
        default = sounddevice.default.device
    except Exception:
        return None
    if not isinstance(default, (list, tuple)) or len(default) != 2:
        return None
    value = default[0] if kind == "input" else default[1]
    return int(value) if value is not None and value >= 0 else None


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return tuple(value)


def _latency_s(metadata: dict[str, Any]) -> float | None:
    raw = metadata.get("raw", {})
    latency = raw.get("default_low_input_latency") or raw.get("default_low_output_latency")
    return float(latency) if latency is not None else None

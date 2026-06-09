"""Models for headless ASIO/IIS frame streaming."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


DEFAULT_ASIO_DEVICE_NAME = "BRAVO-HD Device Control"


class AsioError(RuntimeError):
    """Base class for ASIO/IIS hardware diagnostics."""


class AsioBackendUnavailable(AsioError):
    """Raised when the optional ASIO backend cannot be loaded or used."""


class AsioDeviceNotFound(AsioError):
    """Raised when the requested ASIO device cannot be found."""


class AsioCapabilityError(AsioError):
    """Raised when a device cannot satisfy the requested frame configuration."""


class NativeAsioError(AsioError):
    """Raised when a native Windows ASIO driver call fails."""


class AsioSampleFormat(StrEnum):
    """Sample formats exposed through the Python audio backend."""

    FLOAT32 = "float32"
    INT16 = "int16"
    INT24 = "int24"
    INT32 = "int32"


@dataclass(frozen=True, slots=True)
class AsioIisFrameConfig:
    """Configurable IIS frame I/O settings for one headless ASIO run."""

    device_name: str = DEFAULT_ASIO_DEVICE_NAME
    host_api: str = "ASIO"
    sample_rate: int = 48_000
    bit_depth: int = 32
    sample_format: AsioSampleFormat | str = AsioSampleFormat.FLOAT32
    input_channels: int = 2
    output_channels: int = 2
    input_channel_offset: int = 0
    output_channel_offset: int = 0
    samples_per_frame: int = 256
    frame_count: int = 64
    amplitude: float = 0.25
    seed: int = 20260609

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_format", AsioSampleFormat(self.sample_format))
        if not self.device_name.strip():
            raise ValueError("ASIO device_name must not be empty.")
        if not self.host_api.strip():
            raise ValueError("ASIO host_api must not be empty.")
        if self.sample_rate <= 0:
            raise ValueError("ASIO sample_rate must be positive.")
        if self.bit_depth not in (16, 24, 32):
            raise ValueError("ASIO bit_depth must be one of 16, 24, or 32.")
        if self.bit_depth == 24 and self.sample_format is not AsioSampleFormat.INT24:
            object.__setattr__(self, "sample_format", AsioSampleFormat.INT24)
        if self.input_channels <= 0 or self.output_channels <= 0:
            raise ValueError("ASIO input_channels and output_channels must be positive.")
        if self.input_channel_offset < 0 or self.output_channel_offset < 0:
            raise ValueError("ASIO channel offsets must not be negative.")
        if self.samples_per_frame <= 0:
            raise ValueError("ASIO samples_per_frame must be positive.")
        if self.frame_count <= 0:
            raise ValueError("ASIO frame_count must be positive.")
        if not 0.0 < self.amplitude <= 0.95:
            raise ValueError("ASIO amplitude must be in the range (0.0, 0.95].")

    @property
    def total_samples(self) -> int:
        return self.samples_per_frame * self.frame_count

    @property
    def duration_s(self) -> float:
        return self.total_samples / self.sample_rate

    def snapshot(self) -> dict[str, Any]:
        return {
            "device_name": self.device_name,
            "host_api": self.host_api,
            "sample_rate": self.sample_rate,
            "bit_depth": self.bit_depth,
            "sample_format": self.sample_format.value,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "input_channel_offset": self.input_channel_offset,
            "output_channel_offset": self.output_channel_offset,
            "samples_per_frame": self.samples_per_frame,
            "frame_count": self.frame_count,
            "amplitude": self.amplitude,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class AsioDeviceInfo:
    """Small, serializable snapshot of an audio device."""

    name: str
    host_api: str
    index: int
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float | None = None
    is_default_input: bool = False
    is_default_output: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def matches(self, device_name: str, host_api: str) -> bool:
        return (
            device_name.casefold() in self.name.casefold()
            and self.host_api.casefold() == host_api.casefold()
        )

    def supports(self, config: AsioIisFrameConfig) -> bool:
        return (
            self.matches(config.device_name, config.host_api)
            and self.max_input_channels >= config.input_channels + config.input_channel_offset
            and self.max_output_channels
            >= config.output_channels + config.output_channel_offset
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "host_api": self.host_api,
            "index": self.index,
            "max_input_channels": self.max_input_channels,
            "max_output_channels": self.max_output_channels,
            "default_sample_rate": self.default_sample_rate,
            "is_default_input": self.is_default_input,
            "is_default_output": self.is_default_output,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class NativeAsioChannelInfo:
    """Channel metadata reported by a native Windows ASIO driver."""

    channel: int
    is_input: bool
    is_active: bool
    channel_group: int
    sample_type: int
    name: str

    @property
    def sample_type_name(self) -> str:
        return _ASIO_SAMPLE_TYPE_NAMES.get(self.sample_type, f"unknown({self.sample_type})")

    def snapshot(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "is_input": self.is_input,
            "is_active": self.is_active,
            "channel_group": self.channel_group,
            "sample_type": self.sample_type,
            "sample_type_name": self.sample_type_name,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class NativeAsioDriverCapabilities:
    """Capabilities reported by a registered native Windows ASIO driver."""

    driver_name: str
    driver_version: int
    input_channels: int
    output_channels: int
    input_latency_samples: int
    output_latency_samples: int
    min_buffer_size: int
    max_buffer_size: int
    preferred_buffer_size: int
    buffer_granularity: int
    sample_rate: float
    channels: tuple[NativeAsioChannelInfo, ...]
    driver_message: str | None = None

    def supports_buffer_size(self, buffer_size: int) -> bool:
        if buffer_size == self.preferred_buffer_size:
            return True
        if buffer_size < self.min_buffer_size or buffer_size > self.max_buffer_size:
            return False
        if self.buffer_granularity == 0:
            return buffer_size == self.preferred_buffer_size
        if self.buffer_granularity == -1:
            size = self.min_buffer_size
            while size <= self.max_buffer_size:
                if size == buffer_size:
                    return True
                size *= 2
            return False
        return (buffer_size - self.min_buffer_size) % self.buffer_granularity == 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "driver_name": self.driver_name,
            "driver_version": self.driver_version,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "input_latency_samples": self.input_latency_samples,
            "output_latency_samples": self.output_latency_samples,
            "min_buffer_size": self.min_buffer_size,
            "max_buffer_size": self.max_buffer_size,
            "preferred_buffer_size": self.preferred_buffer_size,
            "buffer_granularity": self.buffer_granularity,
            "sample_rate": self.sample_rate,
            "driver_message": self.driver_message,
            "channels": [channel.snapshot() for channel in self.channels],
        }


@dataclass(frozen=True, slots=True)
class AsioStreamDiagnostics:
    """Diagnostics returned by one full-duplex frame-stream run."""

    backend: str
    device_name: str
    host_api: str
    input_device_index: int | None = None
    output_device_index: int | None = None
    sample_rate: int | None = None
    duration_s: float | None = None
    input_latency_s: float | None = None
    output_latency_s: float | None = None
    messages: tuple[str, ...] = ()

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "device_name": self.device_name,
            "host_api": self.host_api,
            "input_device_index": self.input_device_index,
            "output_device_index": self.output_device_index,
            "sample_rate": self.sample_rate,
            "duration_s": self.duration_s,
            "input_latency_s": self.input_latency_s,
            "output_latency_s": self.output_latency_s,
            "messages": list(self.messages),
        }


_ASIO_SAMPLE_TYPE_NAMES = {
    0: "ASIOSTInt16MSB",
    1: "ASIOSTInt24MSB",
    2: "ASIOSTInt32MSB",
    3: "ASIOSTFloat32MSB",
    4: "ASIOSTFloat64MSB",
    8: "ASIOSTInt32MSB16",
    9: "ASIOSTInt32MSB18",
    10: "ASIOSTInt32MSB20",
    11: "ASIOSTInt32MSB24",
    16: "ASIOSTInt16LSB",
    17: "ASIOSTInt24LSB",
    18: "ASIOSTInt32LSB",
    19: "ASIOSTFloat32LSB",
    20: "ASIOSTFloat64LSB",
    24: "ASIOSTInt32LSB16",
    25: "ASIOSTInt32LSB18",
    26: "ASIOSTInt32LSB20",
    27: "ASIOSTInt32LSB24",
}


@dataclass(frozen=True, slots=True)
class AsioLoopbackThresholds:
    """Pass/fail thresholds for IIS loopback acceptance."""

    min_correlation: float = 0.95
    max_normalized_error: float = 0.15
    min_input_rms: float = 0.001
    max_latency_samples: int = 4096

    def __post_init__(self) -> None:
        if not 0.0 < self.min_correlation <= 1.0:
            raise ValueError("min_correlation must be in the range (0.0, 1.0].")
        if self.max_normalized_error < 0.0:
            raise ValueError("max_normalized_error must not be negative.")
        if self.min_input_rms < 0.0:
            raise ValueError("min_input_rms must not be negative.")
        if self.max_latency_samples < 0:
            raise ValueError("max_latency_samples must not be negative.")


@dataclass(frozen=True, slots=True)
class AsioLoopbackMetrics:
    """Comparison metrics between generated and captured frame streams."""

    generated_samples: int
    captured_samples: int
    compared_samples: int
    delay_samples: int
    correlation: float
    normalized_error: float
    estimated_gain: float
    output_rms: float
    input_rms: float
    passed: bool
    message: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "generated_samples": self.generated_samples,
            "captured_samples": self.captured_samples,
            "compared_samples": self.compared_samples,
            "delay_samples": self.delay_samples,
            "correlation": self.correlation,
            "normalized_error": self.normalized_error,
            "estimated_gain": self.estimated_gain,
            "output_rms": self.output_rms,
            "input_rms": self.input_rms,
            "passed": self.passed,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class AsioLoopbackResult:
    """Full result for one ASIO/IIS loopback smoke run."""

    config: AsioIisFrameConfig
    diagnostics: AsioStreamDiagnostics
    metrics: AsioLoopbackMetrics

    @property
    def passed(self) -> bool:
        return self.metrics.passed

    def snapshot(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "config": self.config.snapshot(),
            "diagnostics": self.diagnostics.snapshot(),
            "metrics": self.metrics.snapshot(),
        }

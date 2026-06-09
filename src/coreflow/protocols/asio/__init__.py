"""Headless ASIO/IIS frame-stream support."""

from coreflow.protocols.asio.backend import (
    AsioCaptureResult,
    AsioIisBackend,
    FakeAsioIisBackend,
    SoundDeviceAsioBackend,
    build_asio_backend,
)
from coreflow.protocols.asio.loopback import (
    format_device_listing,
    generate_loopback_payload,
    run_asio_loopback_smoke,
)
from coreflow.protocols.asio.models import (
    AsioBackendUnavailable,
    AsioCapabilityError,
    AsioDeviceInfo,
    AsioDeviceNotFound,
    AsioError,
    AsioIisFrameConfig,
    AsioLoopbackMetrics,
    AsioLoopbackResult,
    AsioLoopbackThresholds,
    AsioSampleFormat,
    AsioStreamDiagnostics,
    NativeAsioChannelInfo,
    NativeAsioDriverCapabilities,
    NativeAsioError,
)
from coreflow.protocols.asio.native import (
    NativeAsioDriverProbe,
    format_native_asio_capabilities,
)
from coreflow.protocols.asio.registry import (
    AsioRegistryScanner,
    RegisteredAsioDriver,
    format_registered_asio_drivers,
)

__all__ = [
    "AsioBackendUnavailable",
    "AsioCaptureResult",
    "AsioCapabilityError",
    "AsioDeviceInfo",
    "AsioDeviceNotFound",
    "AsioError",
    "AsioIisBackend",
    "AsioIisFrameConfig",
    "AsioLoopbackMetrics",
    "AsioLoopbackResult",
    "AsioLoopbackThresholds",
    "AsioRegistryScanner",
    "AsioSampleFormat",
    "AsioStreamDiagnostics",
    "FakeAsioIisBackend",
    "NativeAsioChannelInfo",
    "NativeAsioDriverCapabilities",
    "NativeAsioDriverProbe",
    "NativeAsioError",
    "RegisteredAsioDriver",
    "SoundDeviceAsioBackend",
    "build_asio_backend",
    "format_device_listing",
    "format_native_asio_capabilities",
    "format_registered_asio_drivers",
    "generate_loopback_payload",
    "run_asio_loopback_smoke",
]

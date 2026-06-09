from __future__ import annotations

import re

import numpy as np
import pytest

from coreflow.__main__ import main
from coreflow.protocols.asio import (
    AsioBackendUnavailable,
    AsioDeviceInfo,
    AsioIisFrameConfig,
    AsioLoopbackThresholds,
    AsioRegistryScanner,
    FakeAsioIisBackend,
    NativeAsioChannelInfo,
    NativeAsioDriverCapabilities,
    RegisteredAsioDriver,
    SoundDeviceAsioBackend,
    format_device_listing,
    format_native_asio_capabilities,
    format_registered_asio_drivers,
    generate_loopback_payload,
    run_asio_loopback_smoke,
)
from coreflow.ui.asio_window import _align_captured_to_output, generate_test_signal


def test_asio_frame_config_validates_settings() -> None:
    config = AsioIisFrameConfig(
        sample_rate=48_000,
        bit_depth=32,
        samples_per_frame=128,
        frame_count=4,
    )

    assert config.total_samples == 512
    assert config.snapshot()["device_name"] == "BRAVO-HD Device Control"
    assert config.snapshot()["sample_format"] == "float32"

    with pytest.raises(ValueError, match="sample_rate"):
        AsioIisFrameConfig(sample_rate=0)

    with pytest.raises(ValueError, match="bit_depth"):
        AsioIisFrameConfig(bit_depth=20)

    with pytest.raises(ValueError, match="amplitude"):
        AsioIisFrameConfig(amplitude=1.5)


def test_loopback_payload_is_deterministic_and_framed() -> None:
    config = AsioIisFrameConfig(
        samples_per_frame=16,
        frame_count=8,
        output_channels=2,
        amplitude=0.2,
        seed=42,
    )

    first = generate_loopback_payload(config)
    second = generate_loopback_payload(config)

    assert first.shape == (128, 2)
    assert first.dtype.name == "float32"
    assert float(abs(first).max()) <= 0.2002
    assert (first == second).all()


def test_ui_test_signal_generation_supports_common_waveforms() -> None:
    sine = generate_test_signal(
        signal_type="Sine",
        sample_rate=1000,
        total_samples=100,
        channels=1,
        amplitude=0.2,
        frequency_hz=10.0,
        seed=1,
    )
    square = generate_test_signal(
        signal_type="Square",
        sample_rate=1000,
        total_samples=100,
        channels=2,
        amplitude=0.3,
        frequency_hz=10.0,
        seed=1,
    )
    noise = generate_test_signal(
        signal_type="White Noise",
        sample_rate=1000,
        total_samples=100,
        channels=1,
        amplitude=0.1,
        frequency_hz=10.0,
        seed=1,
    )

    assert sine.shape == (100, 1)
    assert square.shape == (100, 2)
    assert noise.shape == (100, 1)
    assert float(abs(sine).max()) <= 0.2002
    assert np.array_equal(square[:, 0], square[:, 1])
    assert float(abs(noise).max()) <= 0.1002


def test_ui_loopback_plot_alignment_removes_initial_capture_delay() -> None:
    captured = np.zeros((10, 1), dtype=np.float32)
    captured[4:8, 0] = [1.0, 2.0, 3.0, 4.0]

    aligned = _align_captured_to_output(captured, delay_samples=4, output_sample_count=6)

    assert aligned[:, 0].tolist() == [1.0, 2.0, 3.0, 4.0, 0.0, 0.0]


def test_fake_asio_backend_passes_loopback_with_latency_compensation() -> None:
    config = AsioIisFrameConfig(samples_per_frame=64, frame_count=16, amplitude=0.3)
    result = run_asio_loopback_smoke(
        config,
        backend=FakeAsioIisBackend(delay_samples=17, gain=0.85, noise_rms=0.0001),
        thresholds=AsioLoopbackThresholds(
            min_correlation=0.99,
            max_normalized_error=0.02,
            min_input_rms=0.001,
            max_latency_samples=64,
        ),
    )

    assert result.passed is True
    assert result.metrics.delay_samples == 17
    assert result.metrics.correlation > 0.99
    assert result.metrics.estimated_gain == pytest.approx(0.85, rel=0.02)
    assert result.diagnostics.backend == "fake"


def test_fake_asio_backend_reports_missing_host_api() -> None:
    config = AsioIisFrameConfig(host_api="ASIO")
    backend = FakeAsioIisBackend(
        devices=(
            AsioDeviceInfo(
                name="BRAVO-HD Device Control",
                host_api="WASAPI",
                index=1,
                max_input_channels=2,
                max_output_channels=2,
            ),
        )
    )

    with pytest.raises(AsioBackendUnavailable, match="host API"):
        run_asio_loopback_smoke(config, backend=backend)


def test_format_device_listing_includes_both_host_api_and_name() -> None:
    listing = format_device_listing(
        (
            AsioDeviceInfo(
                name="BRAVO-HD Device Control",
                host_api="ASIO",
                index=3,
                max_input_channels=2,
                max_output_channels=2,
                default_sample_rate=48_000,
            ),
        )
    )

    assert "host_api" in listing
    assert "ASIO" in listing
    assert "BRAVO-HD Device Control" in listing


def test_asio_registry_scanner_uses_injected_provider() -> None:
    scanner = AsioRegistryScanner(
        provider=lambda: (
            RegisteredAsioDriver(
                name="BRAVO-HD",
                clsid="{E3FB0907-F46E-4623-8509-32BAF28B3CA9}",
                driver_path=r"C:\Program Files (x86)\SaviAudio\BRAVO-HD\x64\BravoHDASIO.dll",
            ),
        )
    )

    drivers = scanner.list_drivers()
    listing = format_registered_asio_drivers(drivers)

    assert drivers[0].name == "BRAVO-HD"
    assert "BravoHDASIO.dll" in listing


def test_native_asio_capability_format_includes_channels() -> None:
    capabilities = NativeAsioDriverCapabilities(
        driver_name="BRAVO-HD",
        driver_version=2,
        input_channels=2,
        output_channels=2,
        input_latency_samples=4400,
        output_latency_samples=4400,
        min_buffer_size=88,
        max_buffer_size=26460,
        preferred_buffer_size=4410,
        buffer_granularity=44,
        sample_rate=44100.0,
        channels=(
            NativeAsioChannelInfo(
                channel=0,
                is_input=True,
                is_active=False,
                channel_group=3,
                sample_type=17,
                name="BRAVO-HD_IN_L",
            ),
        ),
    )

    output = format_native_asio_capabilities(capabilities)

    assert "Driver: BRAVO-HD" in output
    assert "preferred=4410" in output
    assert "ASIOSTInt24LSB" in output
    assert capabilities.supports_buffer_size(440) is True
    assert capabilities.supports_buffer_size(4410) is True


def test_sounddevice_backend_is_lazy_and_reports_missing_dependency() -> None:
    def missing_loader() -> object:
        raise ModuleNotFoundError("sounddevice")

    backend = SoundDeviceAsioBackend(module_loader=missing_loader)

    with pytest.raises(AsioBackendUnavailable, match="sounddevice"):
        backend.list_devices()


def test_cli_lists_fake_asio_devices(capsys) -> None:
    assert main(["--asio-list-devices", "--asio-backend", "fake"]) == 0

    captured = capsys.readouterr()
    assert "Registered Windows ASIO drivers" in captured.out
    assert "PortAudio/sounddevice devices" in captured.out
    assert "BRAVO-HD Device Control" in captured.out
    assert "ASIO" in captured.out


def test_cli_runs_fake_asio_loopback(capsys) -> None:
    code = main(
        [
            "--asio-loopback-smoke",
            "--asio-backend",
            "fake",
            "--asio-frame-samples",
            "64",
            "--asio-frame-count",
            "8",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert re.search(r'"passed": true', captured.out)
    assert "ASIO/IIS loopback matched" in captured.out

"""Loopback generation and analysis for ASIO/IIS frame-stream tests."""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable

import numpy as np

from coreflow.protocols.asio.backend import (
    AsioIisBackend,
    build_asio_backend,
)
from coreflow.protocols.asio.models import (
    AsioDeviceInfo,
    AsioIisFrameConfig,
    AsioLoopbackMetrics,
    AsioLoopbackResult,
    AsioLoopbackThresholds,
)


def generate_loopback_payload(config: AsioIisFrameConfig) -> np.ndarray:
    """Generate a deterministic normalized payload for one IIS loopback run."""

    rng = np.random.default_rng(config.seed)
    payload = rng.normal(
        0.0,
        1.0,
        size=(config.total_samples, config.output_channels),
    ).astype(np.float32)
    # Taper the first and last frame to avoid a hard edge on real audio drivers.
    ramp_samples = min(config.samples_per_frame, config.total_samples // 4)
    if ramp_samples > 0:
        ramp = np.linspace(0.0, 1.0, ramp_samples, dtype=np.float32)
        payload[:ramp_samples] *= ramp[:, None]
        payload[-ramp_samples:] *= ramp[::-1, None]
    peak = float(np.max(np.abs(payload)))
    if peak > 0.0:
        payload *= np.float32(config.amplitude / peak)
    return payload


def run_asio_loopback_smoke(
    config: AsioIisFrameConfig,
    *,
    backend: AsioIisBackend | None = None,
    thresholds: AsioLoopbackThresholds | None = None,
) -> AsioLoopbackResult:
    """Run one headless ASIO/IIS loopback smoke test."""

    active_backend = backend or build_asio_backend("auto")
    active_thresholds = thresholds or AsioLoopbackThresholds()
    generated = generate_loopback_payload(config)
    capture = active_backend.run_full_duplex(config, generated)
    _asio_debug(
        "captured "
        f"shape={capture.captured.shape} "
        f"rms={_rms(capture.captured):.8f}"
    )
    metrics = compare_loopback_capture(
        generated=generated,
        captured=capture.captured,
        thresholds=active_thresholds,
    )
    _asio_debug(
        "metrics "
        f"passed={metrics.passed} "
        f"corr={metrics.correlation:.6f} "
        f"error={metrics.normalized_error:.6f} "
        f"delay={metrics.delay_samples}"
    )
    return AsioLoopbackResult(
        config=config,
        diagnostics=capture.diagnostics,
        metrics=metrics,
    )


def compare_loopback_capture(
    *,
    generated: np.ndarray,
    captured: np.ndarray,
    thresholds: AsioLoopbackThresholds,
) -> AsioLoopbackMetrics:
    """Compare generated and captured streams while compensating latency."""

    generated = _as_2d_float(generated)
    captured = _as_2d_float(captured)
    common_channels = min(generated.shape[1], captured.shape[1])
    generated = generated[:, :common_channels]
    captured = captured[:, :common_channels]
    max_delay = min(
        thresholds.max_latency_samples,
        max(generated.shape[0], captured.shape[0]) - 1,
    )
    best = _best_alignment(generated, captured, max_delay=max_delay)
    output_rms = _rms(best.generated)
    input_rms = _rms(best.captured)
    passed = (
        best.correlation >= thresholds.min_correlation
        and best.normalized_error <= thresholds.max_normalized_error
        and input_rms >= thresholds.min_input_rms
    )
    if passed:
        message = "ASIO/IIS loopback matched the generated frame payload."
    elif input_rms < thresholds.min_input_rms:
        message = "Captured ASIO/IIS input is too small; check device routing and IIS wiring."
    elif best.correlation < thresholds.min_correlation:
        message = "Captured ASIO/IIS input does not correlate with generated frames."
    else:
        message = "Captured ASIO/IIS input error exceeds the configured threshold."
    return AsioLoopbackMetrics(
        generated_samples=generated.shape[0],
        captured_samples=captured.shape[0],
        compared_samples=best.generated.shape[0],
        delay_samples=best.delay_samples,
        correlation=best.correlation,
        normalized_error=best.normalized_error,
        estimated_gain=best.estimated_gain,
        output_rms=output_rms,
        input_rms=input_rms,
        passed=passed,
        message=message,
    )


def format_device_listing(devices: Iterable[AsioDeviceInfo]) -> str:
    """Return a stable text table for CLI diagnostics."""

    rows = [
        (
            "index",
            "host_api",
            "inputs",
            "outputs",
            "default_rate",
            "name",
        )
    ]
    for device in devices:
        rows.append(
            (
                str(device.index),
                device.host_api,
                str(device.max_input_channels),
                str(device.max_output_channels),
                "" if device.default_sample_rate is None else f"{device.default_sample_rate:g}",
                device.name,
            )
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    lines = []
    for row in rows:
        lines.append(
            "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        )
    return "\n".join(lines)


def loopback_result_to_json(result: AsioLoopbackResult) -> str:
    """Serialize a loopback result without raw sample arrays."""

    return json.dumps(result.snapshot(), ensure_ascii=False, indent=2, sort_keys=True)


class _AlignmentResult:
    def __init__(
        self,
        *,
        delay_samples: int,
        correlation: float,
        normalized_error: float,
        estimated_gain: float,
        generated: np.ndarray,
        captured: np.ndarray,
    ) -> None:
        self.delay_samples = delay_samples
        self.correlation = correlation
        self.normalized_error = normalized_error
        self.estimated_gain = estimated_gain
        self.generated = generated
        self.captured = captured


def _best_alignment(
    generated: np.ndarray,
    captured: np.ndarray,
    *,
    max_delay: int,
) -> _AlignmentResult:
    _asio_debug(f"estimating delay max_delay={max_delay}")
    estimated_delay = _estimate_delay(generated, captured, max_delay=max_delay)
    _asio_debug(f"estimated delay={estimated_delay}")
    search_radius = min(max_delay, 8)
    best: _AlignmentResult | None = None
    for delay in range(estimated_delay - search_radius, estimated_delay + search_radius + 1):
        if delay < -max_delay or delay > max_delay:
            continue
        generated_segment, captured_segment = _aligned_segments(generated, captured, delay)
        if generated_segment.size == 0 or captured_segment.size == 0:
            continue
        candidate = _score_alignment(delay, generated_segment, captured_segment)
        _asio_debug(
            f"scored delay={delay} corr={candidate.correlation:.6f} "
            f"err={candidate.normalized_error:.6f}"
        )
        if best is None or candidate.correlation > best.correlation:
            best = candidate
    if best is None:
        empty = np.zeros((0, 1), dtype=np.float32)
        return _AlignmentResult(
            delay_samples=0,
            correlation=0.0,
            normalized_error=float("inf"),
            estimated_gain=0.0,
            generated=empty,
            captured=empty,
        )
    return best


def _estimate_delay(
    generated: np.ndarray,
    captured: np.ndarray,
    *,
    max_delay: int,
) -> int:
    generated_mono = generated[:, 0].astype(np.float64)
    captured_mono = captured[:, 0].astype(np.float64)
    generated_mono -= float(np.mean(generated_mono))
    captured_mono -= float(np.mean(captured_mono))
    _asio_debug(
        "running fft correlation "
        f"values={captured_mono.shape[0]} kernel={generated_mono.shape[0]}"
    )
    correlation = _fft_correlate(captured_mono, generated_mono)
    _asio_debug("finished fft correlation")
    center = generated_mono.shape[0] - 1
    start = max(0, center - max_delay)
    stop = min(correlation.shape[0], center + max_delay + 1)
    if stop <= start:
        return 0
    local_index = int(np.argmax(np.abs(correlation[start:stop])))
    return start + local_index - center


def _fft_correlate(values: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    size = values.shape[0] + kernel.shape[0] - 1
    fft_size = 1 << (size - 1).bit_length()
    values_fft = np.fft.rfft(values, fft_size)
    kernel_fft = np.fft.rfft(kernel[::-1], fft_size)
    return np.fft.irfft(values_fft * kernel_fft, fft_size)[:size]


def _aligned_segments(
    generated: np.ndarray,
    captured: np.ndarray,
    delay_samples: int,
) -> tuple[np.ndarray, np.ndarray]:
    if delay_samples >= 0:
        count = min(generated.shape[0], captured.shape[0] - delay_samples)
        if count <= 0:
            return generated[:0], captured[:0]
        return generated[:count], captured[delay_samples : delay_samples + count]
    lead = -delay_samples
    count = min(generated.shape[0] - lead, captured.shape[0])
    if count <= 0:
        return generated[:0], captured[:0]
    return generated[lead : lead + count], captured[:count]


def _score_alignment(
    delay_samples: int,
    generated: np.ndarray,
    captured: np.ndarray,
) -> _AlignmentResult:
    g = generated.reshape(-1).astype(np.float64)
    c = captured.reshape(-1).astype(np.float64)
    g = g - float(np.mean(g))
    c = c - float(np.mean(c))
    g_norm = float(np.sqrt(np.sum(g * g)))
    c_norm = float(np.sqrt(np.sum(c * c)))
    if g_norm == 0.0 or c_norm == 0.0:
        correlation = 0.0
    else:
        correlation = float(np.sum(g * c) / (g_norm * c_norm))
    gain_denominator = float(np.sum(g * g))
    estimated_gain = float(np.sum(c * g) / gain_denominator) if gain_denominator else 0.0
    residual = c - (estimated_gain * g)
    reference_rms = _rms(estimated_gain * g)
    normalized_error = _rms(residual) / max(reference_rms, 1e-12)
    return _AlignmentResult(
        delay_samples=delay_samples,
        correlation=correlation,
        normalized_error=normalized_error,
        estimated_gain=estimated_gain,
        generated=generated,
        captured=captured,
    )


def _as_2d_float(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim == 1:
        return array[:, None]
    if array.ndim != 2:
        raise ValueError(f"Loopback arrays must be 1-D or 2-D, got {array.ndim}-D.")
    return array


def _rms(values: np.ndarray) -> float:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(array))))


def _asio_debug(message: str) -> None:
    if os.environ.get("COREFLOW_ASIO_DEBUG") == "1":
        print(f"[asio-loopback] {message}", file=sys.stderr, flush=True)

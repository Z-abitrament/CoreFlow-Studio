"""Native Windows ASIO driver probing.

This module intentionally keeps native driver calls behind explicit CLI or
hardware-test entry points. Importing it does not open hardware.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from coreflow.protocols.asio.backend import AsioCaptureResult
from coreflow.protocols.asio.models import (
    AsioCapabilityError,
    AsioDeviceInfo,
    AsioIisFrameConfig,
    AsioStreamDiagnostics,
    NativeAsioChannelInfo,
    NativeAsioDriverCapabilities,
    NativeAsioError,
)
from coreflow.protocols.asio.registry import (
    AsioRegistryScanner,
    RegisteredAsioDriver,
)


ASIO_OK = 0
ASE_SUCCESS = 0x3F4847A0
CLSCTX_INPROC_SERVER = 1
IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"
ASIOST_INT24_LSB = 17


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _ASIOChannelInfo(ctypes.Structure):
    _fields_ = [
        ("channel", ctypes.c_long),
        ("isInput", ctypes.c_long),
        ("isActive", ctypes.c_long),
        ("channelGroup", ctypes.c_long),
        ("type", ctypes.c_long),
        ("name", ctypes.c_char * 32),
    ]


class _ASIOBufferInfo(ctypes.Structure):
    _fields_ = [
        ("isInput", ctypes.c_long),
        ("channelNum", ctypes.c_long),
        ("buffers", ctypes.c_void_p * 2),
    ]


class _ASIOTimeInfo(ctypes.Structure):
    _fields_ = [
        ("speed", ctypes.c_double),
        ("systemTime", ctypes.c_longlong),
        ("samplePosition", ctypes.c_longlong),
        ("sampleRate", ctypes.c_double),
        ("flags", ctypes.c_long),
        ("reserved", ctypes.c_char * 12),
    ]


class _ASIOTimeCode(ctypes.Structure):
    _fields_ = [
        ("speed", ctypes.c_double),
        ("timeCodeSamples", ctypes.c_longlong),
        ("flags", ctypes.c_long),
        ("future", ctypes.c_char * 64),
    ]


class _ASIOTime(ctypes.Structure):
    _fields_ = [
        ("reserved", ctypes.c_long * 4),
        ("timeInfo", _ASIOTimeInfo),
        ("timeCode", _ASIOTimeCode),
    ]


_BUFFER_SWITCH = ctypes.CFUNCTYPE(None, ctypes.c_long, ctypes.c_long)
_SAMPLE_RATE_DID_CHANGE = ctypes.CFUNCTYPE(None, ctypes.c_double)
_ASIO_MESSAGE = ctypes.CFUNCTYPE(
    ctypes.c_long,
    ctypes.c_long,
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
)
_BUFFER_SWITCH_TIME_INFO = ctypes.CFUNCTYPE(
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_long,
    ctypes.c_long,
)


class _ASIOCallbacks(ctypes.Structure):
    _fields_ = [
        ("bufferSwitch", _BUFFER_SWITCH),
        ("sampleRateDidChange", _SAMPLE_RATE_DID_CHANGE),
        ("asioMessage", _ASIO_MESSAGE),
        ("bufferSwitchTimeInfo", _BUFFER_SWITCH_TIME_INFO),
    ]


@dataclass(slots=True)
class NativeAsioDriverProbe:
    """Queries capabilities from a registered native Windows ASIO driver."""

    driver_name: str = "BRAVO-HD"
    registry_scanner: AsioRegistryScanner | None = None

    def query_capabilities(self) -> NativeAsioDriverCapabilities:
        driver = self._find_driver()
        if not driver.clsid:
            raise NativeAsioError(f"ASIO driver {driver.name!r} has no CLSID.")
        session = _NativeAsioSession(driver.clsid)
        return session.query_capabilities()

    def _find_driver(self) -> RegisteredAsioDriver:
        scanner = self.registry_scanner or AsioRegistryScanner()
        drivers = scanner.list_drivers()
        for driver in drivers:
            if self.driver_name.casefold() in driver.name.casefold() and driver.clsid:
                return driver
        raise NativeAsioError(f"Registered ASIO driver {self.driver_name!r} was not found.")


@dataclass(slots=True)
class NativeAsioIisBackend:
    """Native Windows ASIO backend for BRAVO-HD IIS loopback smoke tests."""

    driver_name: str = "BRAVO-HD"
    name: str = "native"

    def list_devices(self) -> tuple[AsioDeviceInfo, ...]:
        capabilities = NativeAsioDriverProbe(self.driver_name).query_capabilities()
        return (
            AsioDeviceInfo(
                name=capabilities.driver_name,
                host_api="ASIO",
                index=0,
                max_input_channels=capabilities.input_channels,
                max_output_channels=capabilities.output_channels,
                default_sample_rate=capabilities.sample_rate,
                metadata={"native_capabilities": capabilities.snapshot()},
            ),
        )

    def run_full_duplex(
        self,
        config: AsioIisFrameConfig,
        output: np.ndarray,
    ) -> AsioCaptureResult:
        session = _NativeAsioStreamSession(self.driver_name, config, output)
        return session.run()


def format_native_asio_capabilities(
    capabilities: NativeAsioDriverCapabilities,
) -> str:
    """Format native driver capabilities for CLI diagnostics."""

    lines = [
        f"Driver: {capabilities.driver_name}",
        f"Version: {capabilities.driver_version}",
        f"Sample rate: {capabilities.sample_rate:g} Hz",
        (
            "Channels: "
            f"inputs={capabilities.input_channels} "
            f"outputs={capabilities.output_channels}"
        ),
        (
            "Latencies: "
            f"input={capabilities.input_latency_samples} samples "
            f"output={capabilities.output_latency_samples} samples"
        ),
        (
            "Buffer sizes: "
            f"min={capabilities.min_buffer_size} "
            f"max={capabilities.max_buffer_size} "
            f"preferred={capabilities.preferred_buffer_size} "
            f"granularity={capabilities.buffer_granularity}"
        ),
    ]
    if capabilities.driver_message:
        lines.append(f"Driver message: {capabilities.driver_message}")
    lines.append("Channels:")
    for channel in capabilities.channels:
        direction = "input" if channel.is_input else "output"
        active = "active" if channel.is_active else "inactive"
        lines.append(
            f"  {direction}[{channel.channel}] {channel.name} "
            f"group={channel.channel_group} "
            f"type={channel.sample_type_name} "
            f"{active}"
        )
    return "\n".join(lines)


class _NativeAsioStreamSession:
    def __init__(
        self,
        driver_name: str,
        config: AsioIisFrameConfig,
        output: np.ndarray,
    ) -> None:
        if config.host_api.casefold() != "asio":
            raise AsioCapabilityError("Native ASIO backend requires host_api='ASIO'.")
        self._driver_name = driver_name
        self._config = config
        self._output = np.asarray(output, dtype=np.float32)
        if self._output.shape != (config.total_samples, config.output_channels):
            raise AsioCapabilityError(
                "Output payload shape does not match ASIO frame configuration: "
                f"expected {(config.total_samples, config.output_channels)}, "
                f"got {self._output.shape}."
            )
        self._capture = np.zeros((config.total_samples, config.input_channels), dtype=np.float32)
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._buffer_index = 0
        self._position = 0
        self._callback_count = 0
        self._buffer_size = config.samples_per_frame
        self._input_buffer_infos: list[_ASIOBufferInfo] = []
        self._output_buffer_infos: list[_ASIOBufferInfo] = []
        self._callback_errors: list[str] = []
        self._callbacks: _ASIOCallbacks | None = None
        self._buffers_created = False
        self._buffer_switch_cb: Any | None = None
        self._sample_rate_cb: Any | None = None
        self._message_cb: Any | None = None
        self._time_info_cb: Any | None = None
        self._session: _NativeAsioSession | None = None
        self._capabilities: NativeAsioDriverCapabilities | None = None
        self._driver_messages: list[str] = []

    def run(self) -> AsioCaptureResult:
        driver = NativeAsioDriverProbe(self._driver_name)._find_driver()
        if not driver.clsid:
            raise NativeAsioError(f"ASIO driver {driver.name!r} has no CLSID.")
        session = _NativeAsioSession(driver.clsid)
        self._session = session
        session._open()
        try:
            session._call_init()
            self._capabilities = session._query_capabilities_without_lifecycle()
            self._validate_capabilities(self._capabilities)
            self._buffer_size = self._config.samples_per_frame
            _native_debug("validated capabilities")
            self._create_buffers(session)
            _native_debug("created buffers")
            start_time = time.monotonic()
            _check_asio_result(session._method(7, ctypes.c_long)(session._pointer.value), "start")
            _native_debug("started stream")
            timeout = max(3.0, self._config.duration_s + 5.0)
            completed = self._done.wait(timeout=timeout)
            _native_debug(
                f"wait completed={completed} position={self._position} callbacks={self._callback_count}"
            )
            _native_debug("stopping stream")
            stop_result = session._method(8, ctypes.c_long)(session._pointer.value)
            _native_debug(f"stopped stream result={stop_result}")
            if stop_result != ASIO_OK:
                self._driver_messages.append(f"stop_result={stop_result}")
            time.sleep(0.05)
            elapsed = time.monotonic() - start_time
            if not completed:
                raise NativeAsioError(
                    "Native ASIO stream did not complete before timeout; "
                    f"captured {self._position}/{self._config.total_samples} samples."
                )
            if self._callback_errors:
                raise NativeAsioError("; ".join(self._callback_errors))
            return AsioCaptureResult(
                captured=self._capture,
                diagnostics=AsioStreamDiagnostics(
                    backend="native",
                    device_name=self._capabilities.driver_name,
                    host_api="ASIO",
                    input_device_index=0,
                    output_device_index=0,
                    sample_rate=int(round(self._capabilities.sample_rate)),
                    duration_s=elapsed,
                    input_latency_s=(
                        self._capabilities.input_latency_samples
                        / self._capabilities.sample_rate
                    ),
                    output_latency_s=(
                        self._capabilities.output_latency_samples
                        / self._capabilities.sample_rate
                    ),
                    messages=(
                        f"driver_sample_type={self._sample_type_name()}",
                        f"buffer_size={self._buffer_size}",
                        *tuple(self._driver_messages),
                    ),
                ),
            )
        finally:
            try:
                if self._buffers_created:
                    _native_debug("disposing buffers")
                    session._dispose_buffers()
                    _native_debug("disposed buffers")
                    self._buffers_created = False
            finally:
                self._callbacks = None
                self._buffer_switch_cb = None
                self._sample_rate_cb = None
                self._message_cb = None
                self._time_info_cb = None
                _native_debug("releasing driver")
                session._release()
                _native_debug("released driver")

    def _validate_capabilities(self, capabilities: NativeAsioDriverCapabilities) -> None:
        if self._config.device_name.casefold() not in capabilities.driver_name.casefold():
            raise AsioCapabilityError(
                f"Native ASIO driver {capabilities.driver_name!r} does not match "
                f"requested device {self._config.device_name!r}."
            )
        if capabilities.input_channels < self._config.input_channels:
            raise AsioCapabilityError("Native ASIO driver does not expose enough input channels.")
        if capabilities.output_channels < self._config.output_channels:
            raise AsioCapabilityError("Native ASIO driver does not expose enough output channels.")
        if abs(capabilities.sample_rate - self._config.sample_rate) > 0.5:
            raise AsioCapabilityError(
                "Native ASIO driver sample rate does not match requested rate: "
                f"driver={capabilities.sample_rate:g}, requested={self._config.sample_rate}."
            )
        if not capabilities.supports_buffer_size(self._config.samples_per_frame):
            raise AsioCapabilityError(
                "Native ASIO driver does not support requested samples_per_frame "
                f"{self._config.samples_per_frame}; preferred is "
                f"{capabilities.preferred_buffer_size}."
            )
        sample_types = {channel.sample_type for channel in capabilities.channels}
        if sample_types != {ASIOST_INT24_LSB}:
            raise AsioCapabilityError(
                "Native ASIO backend currently supports BRAVO-HD ASIOSTInt24LSB only; "
                f"driver reported sample types {sorted(sample_types)}."
            )

    def _create_buffers(self, session: "_NativeAsioSession") -> None:
        buffer_infos: list[_ASIOBufferInfo] = []
        for channel in range(self._config.input_channels):
            info = _ASIOBufferInfo(isInput=1, channelNum=channel)
            buffer_infos.append(info)
            self._input_buffer_infos.append(info)
        for channel in range(self._config.output_channels):
            info = _ASIOBufferInfo(isInput=0, channelNum=channel)
            buffer_infos.append(info)
            self._output_buffer_infos.append(info)
        array_type = _ASIOBufferInfo * len(buffer_infos)
        info_array = array_type(*buffer_infos)
        self._install_callbacks()
        create_buffers = session._method(
            19,
            ctypes.c_long,
            ctypes.POINTER(_ASIOBufferInfo),
            ctypes.c_long,
            ctypes.c_long,
            ctypes.POINTER(_ASIOCallbacks),
        )
        result = create_buffers(
            session._pointer.value,
            info_array,
            len(buffer_infos),
            self._buffer_size,
            ctypes.byref(self._callbacks),
        )
        _check_asio_result(result, "createBuffers")
        self._buffers_created = True
        self._input_buffer_infos = [info_array[index] for index in range(self._config.input_channels)]
        self._output_buffer_infos = [
            info_array[index]
            for index in range(self._config.input_channels, len(buffer_infos))
        ]
        missing_buffers = [
            f"input[{index}]"
            for index, info in enumerate(self._input_buffer_infos)
            if not info.buffers[0] or not info.buffers[1]
        ] + [
            f"output[{index}]"
            for index, info in enumerate(self._output_buffer_infos)
            if not info.buffers[0] or not info.buffers[1]
        ]
        if missing_buffers:
            raise NativeAsioError(
                "Native ASIO createBuffers returned empty buffer pointers: "
                + ", ".join(missing_buffers)
            )
        self._driver_messages.append(
            "buffers="
            f"inputs={len(self._input_buffer_infos)} "
            f"outputs={len(self._output_buffer_infos)}"
        )

    def _install_callbacks(self) -> None:
        self._buffer_switch_cb = _BUFFER_SWITCH(self._buffer_switch)
        self._sample_rate_cb = _SAMPLE_RATE_DID_CHANGE(self._sample_rate_did_change)
        self._message_cb = _ASIO_MESSAGE(self._asio_message)
        self._time_info_cb = _BUFFER_SWITCH_TIME_INFO(self._buffer_switch_time_info)
        self._callbacks = _ASIOCallbacks(
            self._buffer_switch_cb,
            self._sample_rate_cb,
            self._message_cb,
            self._time_info_cb,
        )

    def _buffer_switch(self, double_buffer_index: int, _direct_process: int) -> None:
        self._process_buffer(double_buffer_index)

    def _buffer_switch_time_info(
        self,
        time_info: Any,
        double_buffer_index: int,
        direct_process: int,
    ) -> Any:
        self._process_buffer(double_buffer_index)
        return time_info

    def _process_buffer(self, double_buffer_index: int) -> None:
        with self._lock:
            try:
                if self._done.is_set():
                    self._clear_output_buffers(double_buffer_index)
                    return
                start = self._position
                stop = min(start + self._buffer_size, self._config.total_samples)
                count = stop - start
                if count <= 0:
                    self._clear_output_buffers(double_buffer_index)
                    self._done.set()
                    return
                self._read_input_buffers(double_buffer_index, start, count)
                self._write_output_buffers(double_buffer_index, start, count)
                self._position = stop
                self._callback_count += 1
                if self._callback_count <= 3:
                    _native_debug(
                        f"callback index={double_buffer_index} count={count} position={self._position}"
                    )
                if self._position >= self._config.total_samples:
                    self._done.set()
            except Exception as exc:  # pragma: no cover - defensive callback boundary
                self._callback_errors.append(str(exc))
                self._done.set()

    def _read_input_buffers(self, buffer_index: int, start: int, count: int) -> None:
        for channel_index, info in enumerate(self._input_buffer_infos):
            if channel_index >= self._config.input_channels:
                break
            data = _read_int24_lsb_buffer(info.buffers[buffer_index], count)
            self._capture[start : start + count, channel_index] = data

    def _write_output_buffers(self, buffer_index: int, start: int, count: int) -> None:
        for channel_index, info in enumerate(self._output_buffer_infos):
            if channel_index >= self._config.output_channels:
                break
            data = self._output[start : start + count, channel_index]
            _write_int24_lsb_buffer(info.buffers[buffer_index], data)
            if count < self._buffer_size:
                _clear_int24_lsb_buffer(
                    info.buffers[buffer_index],
                    start=count,
                    count=self._buffer_size - count,
                )

    def _clear_output_buffers(self, buffer_index: int) -> None:
        for info in self._output_buffer_infos:
            _clear_int24_lsb_buffer(info.buffers[buffer_index], start=0, count=self._buffer_size)

    def _sample_rate_did_change(self, _sample_rate: float) -> None:
        self._callback_errors.append("Native ASIO sample rate changed during loopback test.")
        self._done.set()

    def _asio_message(
        self,
        selector: int,
        _value: int,
        _message: Any,
        _opt: Any,
    ) -> int:
        if selector == 1:  # kAsioSelectorSupported
            supported_selector = _value
            return 1 if supported_selector in (2, 7, 8, 9) else 0
        if selector == 2:  # kAsioEngineVersion
            return 2
        if selector == 3:  # kAsioResetRequest
            self._callback_errors.append("Native ASIO driver requested reset.")
            self._done.set()
            return 1
        if selector == 7:  # kAsioSupportsTimeInfo
            return 1
        if selector == 8:  # kAsioSupportsTimeCode
            return 0
        if selector == 9:  # kAsioMMCCommand
            return 0
        return 0

    def _sample_type_name(self) -> str:
        if not self._capabilities:
            return "unknown"
        sample_types = {channel.sample_type_name for channel in self._capabilities.channels}
        return ",".join(sorted(sample_types))


def _read_int24_lsb_buffer(address: int, count: int) -> np.ndarray:
    raw_type = ctypes.c_ubyte * (count * 3)
    raw = raw_type.from_address(address)
    values = np.frombuffer(bytes(raw), dtype=np.uint8).reshape(count, 3).astype(np.int32)
    signed = values[:, 0] | (values[:, 1] << 8) | (values[:, 2] << 16)
    signed = np.where(signed & 0x800000, signed - 0x1000000, signed)
    return (signed.astype(np.float32) / 8388608.0).astype(np.float32)


def _write_int24_lsb_buffer(address: int, values: np.ndarray) -> None:
    clipped = np.clip(np.asarray(values, dtype=np.float32), -1.0, 0.99999988)
    ints = np.round(clipped * 8388607.0).astype(np.int32)
    raw_type = ctypes.c_ubyte * (len(ints) * 3)
    raw = raw_type.from_address(address)
    unsigned = ints & 0xFFFFFF
    out = np.empty((len(ints), 3), dtype=np.uint8)
    out[:, 0] = unsigned & 0xFF
    out[:, 1] = (unsigned >> 8) & 0xFF
    out[:, 2] = (unsigned >> 16) & 0xFF
    raw[:] = out.reshape(-1).tolist()


def _clear_int24_lsb_buffer(address: int, *, start: int, count: int) -> None:
    if count <= 0:
        return
    raw_type = ctypes.c_ubyte * (count * 3)
    raw = raw_type.from_address(address + start * 3)
    raw[:] = b"\0" * (count * 3)


class _NativeAsioSession:
    def __init__(self, clsid: str) -> None:
        self._ole32 = ctypes.OleDLL("ole32")
        self._clsid = self._guid(clsid)
        self._iid_iunknown = self._guid(IID_IUNKNOWN)
        self._pointer = ctypes.c_void_p()
        self._initialized = False

    def query_capabilities(self) -> NativeAsioDriverCapabilities:
        self._open()
        try:
            self._call_init()
            return self._query_capabilities_without_lifecycle()
        finally:
            self._dispose_buffers()
            self._release()

    def _query_capabilities_without_lifecycle(self) -> NativeAsioDriverCapabilities:
        driver_name = self._get_driver_name()
        driver_version = self._get_driver_version()
        driver_message = self._get_error_message()
        input_channels, output_channels = self._get_channels()
        input_latency, output_latency = self._get_latencies()
        min_buffer, max_buffer, preferred_buffer, granularity = self._get_buffer_size()
        sample_rate = self._get_sample_rate()
        channels = self._get_channel_infos(input_channels, output_channels)
        return NativeAsioDriverCapabilities(
            driver_name=driver_name,
            driver_version=driver_version,
            input_channels=input_channels,
            output_channels=output_channels,
            input_latency_samples=input_latency,
            output_latency_samples=output_latency,
            min_buffer_size=min_buffer,
            max_buffer_size=max_buffer,
            preferred_buffer_size=preferred_buffer,
            buffer_granularity=granularity,
            sample_rate=sample_rate,
            channels=channels,
            driver_message=driver_message or None,
        )

    def _open(self) -> None:
        self._ole32.CoInitialize(None)
        self._initialized = True
        hr = self._ole32.CoCreateInstance(
            ctypes.byref(self._clsid),
            None,
            CLSCTX_INPROC_SERVER,
            ctypes.byref(self._iid_iunknown),
            ctypes.byref(self._pointer),
        )
        if hr != 0 or not self._pointer.value:
            self._close_com()
            raise NativeAsioError(f"CoCreateInstance failed for ASIO driver: 0x{hr & 0xFFFFFFFF:08X}")

    def _call_init(self) -> None:
        init = self._method(3, ctypes.c_long, ctypes.c_void_p)
        if init(self._pointer.value, None) != 1:
            raise NativeAsioError("Native ASIO driver init failed.")

    def _get_driver_name(self) -> str:
        buffer = ctypes.create_string_buffer(64)
        self._method(4, None, ctypes.c_char_p)(self._pointer.value, buffer)
        return _decode_c_string(buffer)

    def _get_driver_version(self) -> int:
        return int(self._method(5, ctypes.c_long)(self._pointer.value))

    def _get_error_message(self) -> str:
        buffer = ctypes.create_string_buffer(124)
        self._method(6, None, ctypes.c_char_p)(self._pointer.value, buffer)
        return _decode_c_string(buffer)

    def _get_channels(self) -> tuple[int, int]:
        inputs = ctypes.c_long()
        outputs = ctypes.c_long()
        result = self._method(
            9,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
        )(self._pointer.value, ctypes.byref(inputs), ctypes.byref(outputs))
        _check_asio_result(result, "getChannels")
        return int(inputs.value), int(outputs.value)

    def _get_latencies(self) -> tuple[int, int]:
        input_latency = ctypes.c_long()
        output_latency = ctypes.c_long()
        result = self._method(
            10,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
        )(self._pointer.value, ctypes.byref(input_latency), ctypes.byref(output_latency))
        _check_asio_result(result, "getLatencies")
        return int(input_latency.value), int(output_latency.value)

    def _get_buffer_size(self) -> tuple[int, int, int, int]:
        min_size = ctypes.c_long()
        max_size = ctypes.c_long()
        preferred_size = ctypes.c_long()
        granularity = ctypes.c_long()
        result = self._method(
            11,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_long),
        )(
            self._pointer.value,
            ctypes.byref(min_size),
            ctypes.byref(max_size),
            ctypes.byref(preferred_size),
            ctypes.byref(granularity),
        )
        _check_asio_result(result, "getBufferSize")
        return (
            int(min_size.value),
            int(max_size.value),
            int(preferred_size.value),
            int(granularity.value),
        )

    def _get_sample_rate(self) -> float:
        sample_rate = ctypes.c_double()
        result = self._method(
            13,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_double),
        )(self._pointer.value, ctypes.byref(sample_rate))
        _check_asio_result(result, "getSampleRate")
        return float(sample_rate.value)

    def _get_channel_infos(
        self,
        input_channels: int,
        output_channels: int,
    ) -> tuple[NativeAsioChannelInfo, ...]:
        channels: list[NativeAsioChannelInfo] = []
        get_channel_info = self._method(
            18,
            ctypes.c_long,
            ctypes.POINTER(_ASIOChannelInfo),
        )
        for is_input, count in ((True, input_channels), (False, output_channels)):
            for channel_index in range(count):
                info = _ASIOChannelInfo(
                    channel=channel_index,
                    isInput=1 if is_input else 0,
                )
                result = get_channel_info(self._pointer.value, ctypes.byref(info))
                _check_asio_result(result, f"getChannelInfo[{channel_index}]")
                channels.append(
                    NativeAsioChannelInfo(
                        channel=channel_index,
                        is_input=is_input,
                        is_active=bool(info.isActive),
                        channel_group=int(info.channelGroup),
                        sample_type=int(info.type),
                        name=_decode_bytes(info.name),
                    )
                )
        return tuple(channels)

    def _dispose_buffers(self) -> None:
        if self._pointer.value:
            dispose = self._method(20, ctypes.c_long)
            dispose(self._pointer.value)

    def _release(self) -> None:
        if self._pointer.value:
            release = self._method(2, ctypes.c_ulong)
            release(self._pointer.value)
            self._pointer = ctypes.c_void_p()
        self._close_com()

    def _close_com(self) -> None:
        if self._initialized:
            self._ole32.CoUninitialize()
            self._initialized = False

    def _method(self, index: int, restype: Any, *argtypes: Any) -> Any:
        if not self._pointer.value:
            raise NativeAsioError("Native ASIO driver pointer is not open.")
        vtable = ctypes.c_void_p.from_address(self._pointer.value).value
        function = ctypes.c_void_p.from_address(
            vtable + ctypes.sizeof(ctypes.c_void_p) * index
        ).value
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(function)

    def _guid(self, value: str) -> _GUID:
        guid = _GUID()
        result = self._ole32.CLSIDFromString(ctypes.c_wchar_p(value), ctypes.byref(guid))
        if result != 0:
            raise NativeAsioError(f"Invalid Windows GUID {value!r}: 0x{result & 0xFFFFFFFF:08X}")
        return guid


def _check_asio_result(result: int, operation: str) -> None:
    if result != ASIO_OK:
        raise NativeAsioError(f"Native ASIO {operation} failed with code {result}.")


def _decode_c_string(buffer: Any) -> str:
    return _decode_bytes(buffer.value)


def _decode_bytes(value: bytes | bytearray | Any) -> str:
    raw = bytes(value).split(b"\0", 1)[0]
    return raw.decode("mbcs", errors="replace")


def _native_debug(message: str) -> None:
    if os.environ.get("COREFLOW_ASIO_DEBUG") == "1":
        print(f"[native-asio] {message}", file=sys.stderr, flush=True)

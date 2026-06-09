"""CSV replay-backed simulator device."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from coreflow.devices import (
    CommunicationDiagnostic,
    CommunicationState,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    DeviceType,
    FlowmeterDevice,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteResultStatus,
)

DEFAULT_REPLAY_START = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class ReplayFile:
    """Parsed replay data and its resolved virtual device identity."""

    source_path: Path
    device_id: str
    samples: tuple[Measurement, ...]


class ReplayFlowmeterDevice(FlowmeterDevice):
    """Virtual transmitter that reads deterministic measurements from CSV."""

    def __init__(
        self,
        source_path: Path,
        *,
        device_id: str | None = None,
        loop: bool = False,
    ) -> None:
        self._replay = load_replay_file(source_path, device_id=device_id)
        self._identity = DeviceIdentity(
            device_id=self._replay.device_id,
            device_type=DeviceType.SIMULATED,
            serial_number=self._replay.device_id,
            model="Replay Simulated Coriolis Transmitter",
            firmware_version="replay-csv-0.1",
            hardware_version="virtual",
            protocol_address=self._replay.device_id,
            metadata={
                "scenario": "csv_replay",
                "replay_source": str(self._replay.source_path),
                "replay_sample_count": len(self._replay.samples),
            },
        )
        self._loop = loop
        self._sample_index = 0
        self._connected = False
        self._state = CommunicationState.DISCONNECTED
        self._request_count = 0
        self._success_count = 0
        self._timeout_count = 0
        self._frame_error_count = 0
        self._exception_count = 0
        self._last_error: str | None = None
        self._last_success_at: datetime | None = None

    @property
    def identity(self) -> DeviceIdentity:
        return self._identity

    @property
    def replay_file(self) -> ReplayFile:
        return self._replay

    @property
    def sample_index(self) -> int:
        return self._sample_index

    def connect(self) -> None:
        self._request_count += 1
        self._connected = True
        self._state = CommunicationState.CONNECTED
        self._record_success()

    def disconnect(self) -> None:
        self._request_count += 1
        self._connected = False
        self._state = CommunicationState.DISCONNECTED
        self._record_success()

    def read_identity(self) -> DeviceIdentity:
        self._begin_request()
        self._require_connected()
        self._record_success()
        return self._identity

    def read_health(self) -> DeviceHealth:
        self._begin_request()
        if not self._connected:
            return DeviceHealth(
                state=CommunicationState.DISCONNECTED,
                status_flags=("replay",),
                message="Replay device is disconnected.",
                captured_at=datetime.now(UTC),
            )
        self._record_success()
        return DeviceHealth(
            state=self._state,
            status_flags=("simulated", "replay"),
            captured_at=datetime.now(UTC),
        )

    def read_measurement(self) -> Measurement:
        self._begin_request()
        self._require_connected()
        if self._sample_index >= len(self._replay.samples):
            if not self._loop:
                self._exception_count += 1
                self._last_error = "Replay data exhausted."
                raise EOFError(self._last_error)
            self._sample_index = 0

        sample = self._replay.samples[self._sample_index]
        self._sample_index += 1
        self._record_success(sample.captured_at)
        return sample

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        self._begin_request()
        self._require_connected()
        self._record_success()
        return (
            ConfigurationParameter(
                name="replay_source",
                value=str(self._replay.source_path),
                writable=False,
                metadata={"role": "traceability"},
            ),
            ConfigurationParameter(
                name="replay_sample_count",
                value=len(self._replay.samples),
                writable=False,
                metadata={"role": "traceability"},
            ),
        )

    def write_configuration(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        self._begin_request()
        self._require_connected()
        self._exception_count += 1
        self._last_error = "Replay devices are read-only."
        return ParameterWriteResult(
            parameter_name=request.parameter_name,
            status=WriteResultStatus.REJECTED,
            new_value=request.new_value,
            audit_id=f"REPLAY-AUDIT-{self._request_count:06d}",
            message=self._last_error,
        )

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return CommunicationDiagnostic(
            state=self._state,
            request_count=self._request_count,
            successful_response_count=self._success_count,
            timeout_count=self._timeout_count,
            frame_error_count=self._frame_error_count,
            exception_response_count=self._exception_count,
            last_error=self._last_error,
            last_success_at=self._last_success_at,
        )

    def _begin_request(self) -> None:
        self._request_count += 1

    def _require_connected(self) -> None:
        if not self._connected:
            self._state = CommunicationState.DISCONNECTED
            self._last_error = "Replay device is disconnected."
            raise ConnectionError(self._last_error)

    def _record_success(self, captured_at: datetime | None = None) -> None:
        self._success_count += 1
        self._last_success_at = captured_at or datetime.now(UTC)
        self._last_error = None


def load_replay_file(source_path: Path, *, device_id: str | None = None) -> ReplayFile:
    """Load low-rate replay measurements from a CSV file."""

    path = Path(source_path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = {_normalize_key(name) for name in reader.fieldnames or ()}
        if "mass_flow" not in fieldnames:
            raise ValueError("Replay CSV requires a mass_flow column.")
        rows = [
            (row_number, _normalize_row(row))
            for row_number, row in enumerate(reader, start=2)
        ]
    if not rows:
        raise ValueError("Replay CSV contains no measurement rows.")

    resolved_device_id = device_id or _first_source_channel(rows) or _device_id_from_path(path)
    samples = tuple(
        _measurement_from_row(
            row=row,
            row_number=row_number,
            sample_index=sample_index,
            source_path=path,
            device_id=resolved_device_id,
        )
        for sample_index, (row_number, row) in enumerate(rows)
    )
    return ReplayFile(
        source_path=path,
        device_id=resolved_device_id,
        samples=samples,
    )


def replay_template_csv(sample_count: int = 8) -> bytes:
    """Return a small deterministic replay CSV suitable for smoke checks."""

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "captured_at",
            "mass_flow",
            "volume_flow",
            "density",
            "temperature",
            "status_flags",
            "source_channel",
        ]
    )
    for index in range(sample_count):
        captured_at = DEFAULT_REPLAY_START + timedelta(milliseconds=index * 100)
        mass_flow = 10.0 + index * 0.01
        density = 998.2
        writer.writerow(
            [
                captured_at.isoformat(),
                f"{mass_flow:.3f}",
                f"{mass_flow / density:.6f}",
                f"{density:.1f}",
                f"{20.0 + index * 0.05:.2f}",
                "replay",
                "REPLAY-TEMPLATE",
            ]
        )
    return buffer.getvalue().encode("utf-8")


def _measurement_from_row(
    *,
    row: dict[str, str],
    row_number: int,
    sample_index: int,
    source_path: Path,
    device_id: str,
) -> Measurement:
    source_channel = _field(row, "source_channel", "device_id") or device_id
    captured_at = _parse_timestamp(
        _field(row, "captured_at", "timestamp", "time"),
        sample_index=sample_index,
        row_number=row_number,
    )
    mass_flow = _parse_float(
        _required_field(row, "mass_flow", row_number=row_number),
        column="mass_flow",
        row_number=row_number,
    )
    volume_flow = _parse_optional_float(
        _field(row, "volume_flow"),
        column="volume_flow",
        row_number=row_number,
    )
    density = _parse_optional_float(
        _field(row, "density"),
        column="density",
        row_number=row_number,
    )
    temperature = _parse_optional_float(
        _field(row, "temperature"),
        column="temperature",
        row_number=row_number,
    )
    return Measurement(
        captured_at=captured_at,
        mass_flow=mass_flow,
        volume_flow=volume_flow,
        density=density,
        temperature=temperature,
        status_flags=_parse_status_flags(_field(row, "status_flags", "status")),
        source_channel=source_channel,
        raw_values={
            **row,
            "replay_source": str(source_path),
            "replay_row_number": row_number,
            "replay_sample_index": sample_index,
        },
    )


def _normalize_row(row: dict[str, str | None]) -> dict[str, str]:
    return {
        _normalize_key(key): (value or "").strip()
        for key, value in row.items()
        if key is not None
    }


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def _field(row: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = row.get(name)
        if value:
            return value
    return None


def _required_field(row: dict[str, str], name: str, *, row_number: int) -> str:
    value = row.get(name)
    if not value:
        raise ValueError(f"Replay CSV row {row_number} requires {name}.")
    return value


def _parse_timestamp(
    value: str | None,
    *,
    sample_index: int,
    row_number: int,
) -> datetime:
    if not value:
        return DEFAULT_REPLAY_START + timedelta(milliseconds=sample_index * 100)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            f"Invalid replay timestamp on row {row_number}: {value}"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_float(value: str, *, column: str, row_number: int) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"Invalid replay numeric value in {column} on row {row_number}: {value}"
        ) from exc


def _parse_optional_float(
    value: str | None,
    *,
    column: str,
    row_number: int,
) -> float | None:
    if value is None:
        return None
    return _parse_float(value, column=column, row_number=row_number)


def _parse_status_flags(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    normalized = value.replace(";", "|").replace(",", "|")
    return tuple(part.strip() for part in normalized.split("|") if part.strip())


def _first_source_channel(rows: list[tuple[int, dict[str, str]]]) -> str | None:
    for _, row in rows:
        source_channel = _field(row, "source_channel", "device_id")
        if source_channel:
            return source_channel
    return None


def _device_id_from_path(path: Path) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", path.stem).strip("-").upper()
    return f"REPLAY-{slug or 'DATA'}"

"""Application service for timestamped device-variable sampling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from coreflow.devices import ConfigurationParameter, FlowmeterDevice
from coreflow.storage import DeviceRecord, StorageRepository, VariableSampleRecord


@dataclass(frozen=True, slots=True)
class VariableSample:
    """One variable value captured from a device."""

    sample_id: str
    device_id: str
    variable_name: str
    captured_at: datetime
    value: object
    unit: str | None = None
    run_id: str | None = None
    step_id: str | None = None
    source_channel: str | None = None


class VariableSamplingService:
    """Reads configured device variables and stores them in SQLite."""

    def __init__(self, repository: StorageRepository) -> None:
        self._repository = repository

    def sample_configuration(
        self,
        device: FlowmeterDevice,
        *,
        variable_names: tuple[str, ...] | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> tuple[VariableSample, ...]:
        identity = device.read_identity()
        self._repository.save_device(
            DeviceRecord(
                device_id=identity.device_id,
                device_type=identity.device_type.value,
                serial_number=identity.serial_number,
                model=identity.model,
                firmware_version=identity.firmware_version,
                hardware_version=identity.hardware_version,
                protocol_address=identity.protocol_address,
                connection_metadata=identity.metadata,
            )
        )
        allowed = set(variable_names or ())
        captured_at = datetime.now(UTC)
        source_channel = identity.protocol_address or identity.device_id
        samples: list[VariableSample] = []
        for index, parameter in enumerate(device.read_configuration(), start=1):
            if allowed and parameter.name not in allowed:
                continue
            sample = _sample_from_parameter(
                parameter,
                device_id=identity.device_id,
                captured_at=captured_at,
                index=index,
                run_id=run_id,
                step_id=step_id,
                source_channel=source_channel,
            )
            self._repository.save_variable_sample(
                VariableSampleRecord(
                    sample_id=sample.sample_id,
                    device_id=sample.device_id,
                    run_id=sample.run_id,
                    step_id=sample.step_id,
                    variable_name=sample.variable_name,
                    captured_at=sample.captured_at,
                    value=sample.value,
                    unit=sample.unit,
                    source_channel=sample.source_channel,
                    metadata=parameter.metadata,
                )
            )
            samples.append(sample)
        return tuple(samples)


def _sample_from_parameter(
    parameter: ConfigurationParameter,
    *,
    device_id: str,
    captured_at: datetime,
    index: int,
    run_id: str | None,
    step_id: str | None,
    source_channel: str | None,
) -> VariableSample:
    timestamp_token = captured_at.strftime("%Y%m%d%H%M%S%f")
    return VariableSample(
        sample_id=f"VAR-{device_id}-{timestamp_token}-{index:03d}",
        device_id=device_id,
        variable_name=parameter.name,
        captured_at=captured_at,
        value=parameter.value,
        unit=parameter.unit,
        run_id=run_id,
        step_id=step_id,
        source_channel=source_channel,
    )

"""Stable device interface used by workflows and future adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from coreflow.devices.models import (
    CommunicationDiagnostic,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
)


class FlowmeterDevice(ABC):
    """Application-level interface for simulated and physical transmitters."""

    @abstractmethod
    def connect(self) -> None:
        """Open the device session."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the device session."""

    @abstractmethod
    def read_identity(self) -> DeviceIdentity:
        """Read stable device identity fields when available."""

    @abstractmethod
    def read_health(self) -> DeviceHealth:
        """Read health, alarm, and status information."""

    @abstractmethod
    def read_measurement(self) -> Measurement:
        """Read the latest live measurement."""

    @abstractmethod
    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        """Read configuration parameters exposed by the adapter."""

    @abstractmethod
    def write_configuration(
        self, request: ParameterWriteRequest
    ) -> ParameterWriteResult:
        """Write or dry-run a guarded configuration change."""

    @abstractmethod
    def communication_diagnostics(self) -> CommunicationDiagnostic:
        """Return communication counters and last-error state."""

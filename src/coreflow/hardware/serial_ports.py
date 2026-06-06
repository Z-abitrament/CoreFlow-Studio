"""Serial adapter discovery for hardware acceptance preparation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    """Small, UI-ready snapshot of a discovered serial adapter."""

    port: str
    description: str | None = None
    hardware_id: str | None = None
    manufacturer: str | None = None
    serial_number: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SerialPortScanner:
    """Enumerates available serial ports without opening them."""

    def __init__(
        self,
        provider: Callable[[], Iterable[Any]] | None = None,
    ) -> None:
        self._provider = provider or _pyserial_comports

    def list_ports(self) -> tuple[SerialPortInfo, ...]:
        ports: list[SerialPortInfo] = []
        for item in self._provider():
            if isinstance(item, SerialPortInfo):
                ports.append(item)
                continue
            ports.append(
                SerialPortInfo(
                    port=str(getattr(item, "device", "")),
                    description=_none_if_empty(getattr(item, "description", None)),
                    hardware_id=_none_if_empty(getattr(item, "hwid", None)),
                    manufacturer=_none_if_empty(getattr(item, "manufacturer", None)),
                    serial_number=_none_if_empty(getattr(item, "serial_number", None)),
                    metadata={
                        "vid": getattr(item, "vid", None),
                        "pid": getattr(item, "pid", None),
                        "location": getattr(item, "location", None),
                    },
                )
            )
        return tuple(port for port in ports if port.port)


def _pyserial_comports() -> Iterable[Any]:
    from serial.tools import list_ports

    return list_ports.comports()


def _none_if_empty(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None

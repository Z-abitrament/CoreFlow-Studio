"""Windows ASIO driver registry discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RegisteredAsioDriver:
    """ASIO driver entry registered with Windows."""

    name: str
    clsid: str | None = None
    driver_path: str | None = None
    registry_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "clsid": self.clsid,
            "driver_path": self.driver_path,
            "registry_path": self.registry_path,
            "metadata": self.metadata,
        }


class AsioRegistryScanner:
    """Enumerates installed Windows ASIO driver registrations."""

    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider or _scan_windows_registry

    def list_drivers(self) -> tuple[RegisteredAsioDriver, ...]:
        return tuple(self._provider())


def format_registered_asio_drivers(
    drivers: tuple[RegisteredAsioDriver, ...],
) -> str:
    """Return a stable text table for registered ASIO drivers."""

    if not drivers:
        return "No Windows ASIO driver registrations found."
    rows = [("name", "clsid", "driver_path")]
    for driver in drivers:
        rows.append((driver.name, driver.clsid or "", driver.driver_path or ""))
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    return "\n".join(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )


def _scan_windows_registry() -> tuple[RegisteredAsioDriver, ...]:
    try:
        import winreg
    except ModuleNotFoundError:
        return ()
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ASIO"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\ASIO"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\ASIO"),
    )
    drivers: list[RegisteredAsioDriver] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for hive, path in roots:
        try:
            with winreg.OpenKey(hive, path) as root:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    driver = _read_driver(winreg, root, name, path)
                    key = (driver.name, driver.clsid, driver.driver_path)
                    if key not in seen:
                        seen.add(key)
                        drivers.append(driver)
        except OSError:
            continue
    return tuple(drivers)


def _read_driver(
    winreg: Any,
    root: Any,
    name: str,
    parent_path: str,
) -> RegisteredAsioDriver:
    clsid: str | None = None
    driver_path: str | None = None
    metadata: dict[str, Any] = {}
    with winreg.OpenKey(root, name) as key:
        value_count = winreg.QueryInfoKey(key)[1]
        for value_index in range(value_count):
            value_name, value, _value_type = winreg.EnumValue(key, value_index)
            metadata[value_name] = value
            if value_name.casefold() == "clsid":
                clsid = str(value)
            elif value_name.casefold() == "driver":
                driver_path = str(value)
    return RegisteredAsioDriver(
        name=name,
        clsid=clsid,
        driver_path=driver_path,
        registry_path=parent_path + "\\" + name,
        metadata=metadata,
    )

"""Machine-readable local API contract for CoreFlow Studio integrations."""

from __future__ import annotations

from coreflow import __version__


def build_api_manifest() -> dict[str, object]:
    """Return the supported local API capabilities as JSON-ready data."""

    return {
        "schema_version": 1,
        "application": {
            "name": "CoreFlow Studio",
            "package": "coreflow",
            "version": __version__,
        },
        "capabilities": [
            {
                "id": "modbus.raw_frame",
                "stability": "stable",
                "summary": (
                    "Send one local Modbus RTU raw frame through the same transport "
                    "path used by the standalone Modbus Module."
                ),
                "python_api": {
                    "import": "coreflow.modbus_api.ModbusRawClient",
                    "method": "send_raw_frame",
                },
                "cli": {
                    "source_command": "python -m coreflow --modbus-raw",
                    "packaged_command": "CoreFlowStudioConsole.exe --modbus-raw",
                    "json_flag": "--modbus-json",
                },
                "arguments": [
                    {"name": "frame", "required": True, "type": "hex-string-or-bytes"},
                    {"name": "port", "required": True, "type": "serial-port"},
                    {"name": "unit_id", "required": False, "type": "integer"},
                    {"name": "append_crc", "required": False, "type": "boolean"},
                ],
                "output_modes": ["text", "json"],
                "examples": [
                    {
                        "shell": (
                            "python -m coreflow --modbus-raw "
                            '"01 03 00 3D 00 02" --modbus-port COM9 '
                            "--modbus-unit 1 --modbus-auto-crc --modbus-json"
                        )
                    }
                ],
                "safety": [
                    (
                        "This is a local diagnostics and lab automation surface, "
                        "not a remote-control service."
                    ),
                    (
                        "Callers can explicitly send Modbus write function codes; "
                        "guarded calibration workflows and audited parameter writes "
                        "remain separate."
                    ),
                ],
                "limitations": [
                    "Requires a local PC that owns the USB-to-serial adapter.",
                    "Does not load production calibration formulas or acceptance thresholds.",
                ],
            }
        ],
    }

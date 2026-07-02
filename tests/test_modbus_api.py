from __future__ import annotations

import pytest

from coreflow.protocols.modbus import RegisterKind, SerialConfig, TransportResponse
from tests.modbus_fakes import FakeModbusTransport


def test_modbus_raw_client_standard_read_uses_transport_read(monkeypatch) -> None:
    from coreflow.modbus_api import ModbusRawClient

    transports: list[FakeModbusTransport] = []

    def factory(config: SerialConfig) -> FakeModbusTransport:
        transport = FakeModbusTransport({0x003D: [0x3BE1, 0x72D8]})
        transport.raw_response = bytes.fromhex("01")
        transports.append(transport)
        return transport

    monkeypatch.setattr("coreflow.modbus_api.PymodbusSerialTransport", factory)

    with ModbusRawClient(port="COM9", unit_id=1) as client:
        response = client.send_raw_frame("01 03 00 3D 00 02", append_crc=True)

    assert response == bytes.fromhex("01 03 04 3B E1 72 D8 83 DB")
    assert transports[0].reads == [(RegisterKind.HOLDING, 0x003D, 2, 1)]
    assert transports[0].raw_frames == []
    assert transports[0].connected is False


def test_modbus_raw_client_nonstandard_frame_falls_back_to_raw_send(monkeypatch) -> None:
    from coreflow.modbus_api import ModbusRawClient

    transports: list[FakeModbusTransport] = []

    def factory(config: SerialConfig) -> FakeModbusTransport:
        transport = FakeModbusTransport({})
        transport.raw_response = bytes.fromhex("01 41 99 88 77 32 10")
        transports.append(transport)
        return transport

    monkeypatch.setattr("coreflow.modbus_api.PymodbusSerialTransport", factory)

    with ModbusRawClient(port="COM9", unit_id=1) as client:
        response = client.send_raw_frame(bytes.fromhex("01 41 00 00 C0 01"))

    assert response == bytes.fromhex("01 41 99 88 77 32 10")
    assert transports[0].raw_frames == [bytes.fromhex("01 41 00 00 C0 01")]


def test_modbus_raw_client_reports_open_failure(monkeypatch) -> None:
    from coreflow.modbus_api import ModbusCommunicationError, ModbusRawClient

    class FailingTransport:
        last_error = "COM9 denied"

        def __init__(self, _config: SerialConfig) -> None:
            return None

        def connect(self) -> bool:
            return False

        def close(self) -> None:
            return None

    monkeypatch.setattr("coreflow.modbus_api.PymodbusSerialTransport", FailingTransport)

    with pytest.raises(ModbusCommunicationError, match="COM9 denied"):
        ModbusRawClient(port="COM9", unit_id=1).connect()

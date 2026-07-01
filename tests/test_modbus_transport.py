from __future__ import annotations

from serial import SerialException

from coreflow.protocols.modbus import RegisterKind, SerialConfig
from coreflow.protocols.modbus.transport import PymodbusSerialTransport


class _BitPayloadResponse:
    bits: list[bool] = []
    registers = None

    def isError(self) -> bool:  # noqa: N802 - pymodbus API
        return False

    def encode(self) -> bytes:
        return b"\x01\x00"


class _BitPayloadWithEmptyRegistersResponse(_BitPayloadResponse):
    registers: list[int] = []


class _ClientWithEmptyBits:
    def __init__(self, response=None) -> None:
        self.response = response or _BitPayloadResponse()

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        return None

    def read_coils(self, address: int, *, count: int, device_id: int):
        assert address == 0x10
        assert count == 1
        assert device_id == 1
        return self.response


class _RegisterPayloadResponse:
    registers = [0x1234]

    def isError(self) -> bool:  # noqa: N802 - pymodbus API
        return False


class _DisconnectedClient:
    connected = False

    def __init__(self) -> None:
        self.connect_count = 0
        self.read_count = 0

    def connect(self) -> bool:
        self.connect_count += 1
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def read_holding_registers(self, address: int, *, count: int, device_id: int):
        assert address == 4
        assert count == 1
        assert device_id == 1
        self.read_count += 1
        return _RegisterPayloadResponse()


class _RawSendClient:
    connected = True

    def __init__(self, response: bytes = b"") -> None:
        self.sent: list[bytes] = []
        self.response = response
        self.recv_sizes: list[int | None] = []

    def connect(self) -> bool:
        return True

    def close(self) -> None:
        self.connected = False

    def send(self, request: bytes) -> int:
        self.sent.append(bytes(request))
        return len(request)

    def recv(self, size: int | None) -> bytes:
        self.recv_sizes.append(size)
        return self.response


class _FailingOpenClient:
    connected = False

    def connect(self) -> bool:
        raise SerialException("Access is denied.")

    def close(self) -> None:
        return None


def test_pymodbus_transport_decodes_bit_payload_when_bits_is_empty() -> None:
    transport = PymodbusSerialTransport(SerialConfig(port="COM1", unit_id=1))
    transport._client = _ClientWithEmptyBits()

    response = transport.read_registers(RegisterKind.COIL, 0x10, 1, 1)

    assert response.ok
    assert response.values == [0]


def test_pymodbus_transport_prefers_bit_payload_for_coils_with_empty_registers() -> None:
    transport = PymodbusSerialTransport(SerialConfig(port="COM1", unit_id=1))
    transport._client = _ClientWithEmptyBits(_BitPayloadWithEmptyRegistersResponse())

    response = transport.read_registers(RegisterKind.COIL, 0x10, 1, 1)

    assert response.ok
    assert response.values == [0]


def test_pymodbus_transport_reconnects_before_request_when_client_closed() -> None:
    transport = PymodbusSerialTransport(SerialConfig(port="COM1", unit_id=1))
    client = _DisconnectedClient()
    transport._client = client

    response = transport.read_registers(RegisterKind.HOLDING, 4, 1, 1)

    assert response.ok
    assert response.values == [0x1234]
    assert client.connect_count == 1
    assert client.read_count == 1


def test_pymodbus_transport_sends_raw_frame_bytes_and_reads_response() -> None:
    transport = PymodbusSerialTransport(SerialConfig(port="COM1", unit_id=1))
    client = _RawSendClient(bytes.fromhex("01 03 04 00 01 00 02 2A 32"))
    transport._client = client

    response = transport.send_raw_frame(bytes.fromhex("01 03 00 00 00 02 C4 0B"))

    assert response.ok
    assert client.sent == [bytes.fromhex("01 03 00 00 00 02 C4 0B")]
    assert client.recv_sizes == [None]
    assert response.values == list(bytes.fromhex("01 03 04 00 01 00 02 2A 32"))


def test_pymodbus_transport_reports_serial_open_details() -> None:
    transport = PymodbusSerialTransport(
        SerialConfig(
            port="COM42",
            unit_id=1,
            baudrate=9600,
            parity="E",
            stop_bits=2,
            read_timeout_s=1.5,
        )
    )
    transport._client = _FailingOpenClient()

    assert transport.connect() is False
    assert transport.last_error is not None
    assert "COM42" in transport.last_error
    assert "9600 baud" in transport.last_error
    assert "8E2" in transport.last_error
    assert "Access is denied." in transport.last_error

    response = transport.read_registers(RegisterKind.HOLDING, 4, 1, 1)

    assert response.error == transport.last_error

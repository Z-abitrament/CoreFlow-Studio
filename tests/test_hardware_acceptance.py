from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coreflow.__main__ import main
from coreflow.devices import ParameterWriteRequest, WriteMode
from coreflow.hardware import (
    HardwareAcceptanceRunner,
    SerialPortInfo,
    SerialPortScanner,
    build_placeholder_register_map,
    register_map_from_json,
    register_map_to_json,
)
from coreflow.protocols.modbus import (
    ModbusDataType,
    ModbusRegister,
    ModbusRegisterMap,
    ModbusRtuFlowmeterDevice,
    RegisterKind,
    SerialConfig,
    TransportResponse,
)
from coreflow.storage import (
    ArtifactStore,
    Database,
    StorageRepository,
    check_artifact_integrity,
)


@dataclass
class FakeModbusTransport:
    registers: dict[int, list[int]]
    connected: bool = False
    writes: list[tuple[int, list[int], int]] | None = None

    def __post_init__(self) -> None:
        self.writes = []

    def connect(self) -> bool:
        self.connected = True
        return True

    def close(self) -> None:
        self.connected = False

    def read_registers(
        self,
        kind: RegisterKind,
        address: int,
        count: int,
        unit_id: int,
    ) -> TransportResponse:
        return TransportResponse(values=self.registers[address][:count])

    def write_registers(
        self,
        address: int,
        values: list[int],
        unit_id: int,
    ) -> TransportResponse:
        assert self.writes is not None
        self.writes.append((address, list(values), unit_id))
        self.registers[address] = list(values)
        return TransportResponse(values=values)

    def write_coil(
        self,
        address: int,
        value: bool,
        unit_id: int,
    ) -> TransportResponse:
        assert self.writes is not None
        self.writes.append((address, [1 if value else 0], unit_id))
        self.registers[address] = [1 if value else 0]
        return TransportResponse(values=[1 if value else 0])


def _register_map() -> ModbusRegisterMap:
    return ModbusRegisterMap(
        name="hardware-acceptance-test",
        version="0.1",
        registers=(
            ModbusRegister(
                name="mass_flow",
                kind=RegisterKind.INPUT,
                address=0,
                word_count=1,
                data_type=ModbusDataType.INT16,
                scale=0.1,
            ),
            ModbusRegister(
                name="density",
                kind=RegisterKind.INPUT,
                address=1,
                word_count=1,
                data_type=ModbusDataType.UINT16,
                scale=0.1,
            ),
            ModbusRegister(
                name="temperature",
                kind=RegisterKind.INPUT,
                address=2,
                word_count=1,
                data_type=ModbusDataType.INT16,
                scale=0.1,
            ),
            ModbusRegister(
                name="device_status",
                kind=RegisterKind.INPUT,
                address=3,
                word_count=1,
                data_type=ModbusDataType.UINT16,
            ),
            ModbusRegister(
                name="serial_number",
                kind=RegisterKind.HOLDING,
                address=10,
                word_count=1,
                data_type=ModbusDataType.UINT16,
            ),
            ModbusRegister(
                name="zero_offset",
                kind=RegisterKind.HOLDING,
                address=20,
                word_count=1,
                data_type=ModbusDataType.INT16,
                writable=True,
                scale=0.01,
                minimum=-10.0,
                maximum=10.0,
            ),
        ),
    )


def _device(transport: FakeModbusTransport) -> ModbusRtuFlowmeterDevice:
    return ModbusRtuFlowmeterDevice(
        SerialConfig(port="COM9", unit_id=7, retry_count=0),
        _register_map(),
        transport=transport,
    )


def _transport() -> FakeModbusTransport:
    return FakeModbusTransport(
        registers={
            0: [100],
            1: [9982],
            2: [215],
            3: [1],
            10: [12345],
            20: [0],
        }
    )


def test_placeholder_register_map_round_trips_as_json() -> None:
    register_map = build_placeholder_register_map()
    content = register_map_to_json(register_map)
    restored = register_map_from_json(content)

    assert restored.name == "coreflow-placeholder-coriolis-map"
    assert restored.by_name("mass_flow").metadata["placeholder"] is True
    assert restored.by_name("zero_offset").writable is True
    assert "production" not in restored.version


def test_serial_port_scanner_uses_injected_provider() -> None:
    scanner = SerialPortScanner(
        provider=lambda: (
            SerialPortInfo(
                port="COM9",
                description="USB Serial",
                hardware_id="USB VID:PID",
                manufacturer="Example",
                serial_number="ABC",
            ),
        )
    )

    ports = scanner.list_ports()

    assert ports[0].port == "COM9"
    assert ports[0].serial_number == "ABC"


def test_hardware_acceptance_runner_is_read_only_and_audits_dry_run(tmp_path) -> None:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    artifact_store = ArtifactStore(tmp_path)
    transport = _transport()
    device = _device(transport)
    scanner = SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM9"),))
    runner = HardwareAcceptanceRunner(
        repository=repository,
        artifact_store=artifact_store,
        port_scanner=scanner,
    )

    result = runner.run_read_only_checks(
        run_id="RUN-20260606-001100",
        device=device,
        expected_port="COM9",
        dry_run_request=ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=1.0,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="calibration_write_armed",
            run_id="RUN-20260606-001100",
        ),
    )

    checks = {check.name: check for check in result.checks}
    assert result.passed is True
    assert checks["port_discovery"].passed is True
    assert checks["measurement"].details["mass_flow"] == 10.0
    assert checks["write_guard_dry_run"].details["status"] == "dry_run"
    assert transport.writes == []
    assert result.artifact_id == "RUN-20260606-001100-HARDWARE-ACCEPTANCE"
    assert repository.get_run_status("RUN-20260606-001100") == "passed"
    assert repository.count_rows("audit_logs") == 1
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_hardware_acceptance_runner_reports_missing_expected_port() -> None:
    runner = HardwareAcceptanceRunner(
        port_scanner=SerialPortScanner(provider=lambda: (SerialPortInfo(port="COM3"),))
    )

    result = runner.run_read_only_checks(
        run_id="RUN-NO-PORT",
        device=_device(_transport()),
        expected_port="COM9",
    )

    checks = {check.name: check for check in result.checks}
    assert result.passed is False
    assert checks["port_discovery"].message == "Expected port COM9 not found."


def test_cli_writes_register_map_template(tmp_path, capsys) -> None:
    output_path = Path(tmp_path / "config" / "register_map.json")

    assert main(["--write-register-map-template", str(output_path)]) == 0
    captured = capsys.readouterr()
    restored = register_map_from_json(output_path.read_text(encoding="utf-8"))

    assert "Wrote register-map template" in captured.out
    assert restored.by_name("alarm_flags").metadata["placeholder"] is True

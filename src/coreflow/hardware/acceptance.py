"""Read-only hardware acceptance preparation checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from coreflow.app.write_guard import WriteGuardService
from coreflow.devices import (
    FlowmeterDevice,
    ParameterWriteRequest,
    WriteMode,
)
from coreflow.hardware.serial_ports import SerialPortScanner
from coreflow.storage import ArtifactStore, ArtifactType, StorageRepository
from coreflow.storage.models import DeviceRecord
from coreflow.workflows.models import RunSession, RunStatus, RunType


@dataclass(frozen=True, slots=True)
class HardwareAcceptanceCheck:
    """One hardware acceptance preparation check result."""

    name: str
    passed: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HardwareAcceptanceResult:
    """Combined result of a read-only hardware acceptance run."""

    run_id: str
    passed: bool
    checks: tuple[HardwareAcceptanceCheck, ...]
    artifact_id: str | None = None


class HardwareAcceptanceRunner:
    """Runs safe, read-only checks before any real hardware write is attempted."""

    def __init__(
        self,
        repository: StorageRepository | None = None,
        artifact_store: ArtifactStore | None = None,
        port_scanner: SerialPortScanner | None = None,
        write_guard: WriteGuardService | None = None,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store
        self._port_scanner = port_scanner or SerialPortScanner()
        self._write_guard = write_guard or WriteGuardService(repository)

    def run_read_only_checks(
        self,
        *,
        run_id: str,
        device: FlowmeterDevice,
        expected_port: str | None = None,
        dry_run_request: ParameterWriteRequest | None = None,
    ) -> HardwareAcceptanceResult:
        checks: list[HardwareAcceptanceCheck] = []
        checks.append(self._check_port_discovery(expected_port))
        connected = self._check_connection(device)
        checks.append(connected)
        run_persisted = False
        if connected.passed:
            identity_check = self._check_identity(device)
            checks.append(identity_check)
            if identity_check.passed:
                run_persisted = self._persist_identity_and_run(run_id, device)
                checks.extend(
                    [
                        self._check_health(device),
                        self._check_measurement(device),
                        self._check_read_only_factory_preview(device),
                    ]
                )
                if dry_run_request is not None:
                    checks.append(self._check_write_guard_dry_run(device, dry_run_request))
        passed = all(check.passed for check in checks)
        artifact_id = self._write_summary(run_id, checks) if run_persisted else None
        if run_persisted:
            self._mark_run_complete(
                run_id,
                status=RunStatus.PASSED if passed else RunStatus.FAILED,
            )
        return HardwareAcceptanceResult(
            run_id=run_id,
            passed=passed,
            checks=tuple(checks),
            artifact_id=artifact_id,
        )

    def _persist_identity_and_run(
        self,
        run_id: str,
        device: FlowmeterDevice,
    ) -> bool:
        if self._repository is None:
            return False
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
        self._repository.save_run(
            RunSession(
                run_id=run_id,
                run_type=RunType.HARDWARE_ACCEPTANCE,
                workflow_name="hardware_acceptance_preparation",
                workflow_version="0.1",
                device_id=identity.device_id,
                operator="hardware_acceptance",
                status=RunStatus.RUNNING,
                started_at=datetime.now(UTC),
                configuration_snapshot={
                    "mode": "read_only",
                    "armed_writes_allowed": False,
                },
                software_version="0.1.0",
            )
        )
        return True

    def _mark_run_complete(self, run_id: str, status: RunStatus) -> None:
        if self._repository is None:
            return
        run = self._repository.get_run(run_id)
        if run is None:
            return
        self._repository.save_run(
            RunSession(
                run_id=run.run_id,
                run_type=run.run_type,
                workflow_name=run.workflow_name,
                workflow_version=run.workflow_version,
                device_id=run.device_id,
                operator=run.operator,
                status=status,
                started_at=run.started_at,
                ended_at=datetime.now(UTC),
                configuration_snapshot=run.configuration_snapshot,
                software_version=run.software_version,
                notes=run.notes,
            )
        )

    def _check_port_discovery(self, expected_port: str | None) -> HardwareAcceptanceCheck:
        ports = self._port_scanner.list_ports()
        details = {
            "ports": [
                {
                    "port": port.port,
                    "description": port.description,
                    "hardware_id": port.hardware_id,
                    "manufacturer": port.manufacturer,
                    "serial_number": port.serial_number,
                }
                for port in ports
            ]
        }
        if expected_port is None:
            return HardwareAcceptanceCheck(
                name="port_discovery",
                passed=True,
                message=f"Discovered {len(ports)} serial port(s).",
                details=details,
            )
        found = any(port.port == expected_port for port in ports)
        return HardwareAcceptanceCheck(
            name="port_discovery",
            passed=found,
            message=f"Expected port {expected_port} {'found' if found else 'not found'}.",
            details=details,
        )

    def _check_connection(self, device: FlowmeterDevice) -> HardwareAcceptanceCheck:
        try:
            device.connect()
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="connect",
                passed=False,
                message=str(exc),
            )
        return HardwareAcceptanceCheck(
            name="connect",
            passed=True,
            message="Device connection opened.",
            details=_diagnostic_details(device),
        )

    def _check_identity(self, device: FlowmeterDevice) -> HardwareAcceptanceCheck:
        try:
            identity = device.read_identity()
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="identity",
                passed=False,
                message=str(exc),
            )
        return HardwareAcceptanceCheck(
            name="identity",
            passed=True,
            message=f"Read identity for {identity.device_id}.",
            details={
                "device_id": identity.device_id,
                "device_type": identity.device_type.value,
                "serial_number": identity.serial_number,
                "model": identity.model,
                "firmware_version": identity.firmware_version,
                "hardware_version": identity.hardware_version,
                "protocol_address": identity.protocol_address,
            },
        )

    def _check_health(self, device: FlowmeterDevice) -> HardwareAcceptanceCheck:
        try:
            health = device.read_health()
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="health",
                passed=False,
                message=str(exc),
            )
        passed = not health.alarm_flags
        return HardwareAcceptanceCheck(
            name="health",
            passed=passed,
            message="Health read completed." if passed else "Health reports alarms.",
            details={
                "state": health.state.value,
                "status_flags": list(health.status_flags),
                "alarm_flags": list(health.alarm_flags),
                "message": health.message,
            },
        )

    def _check_measurement(self, device: FlowmeterDevice) -> HardwareAcceptanceCheck:
        try:
            measurement = device.read_measurement()
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="measurement",
                passed=False,
                message=str(exc),
            )
        passed = measurement.mass_flow is not None
        return HardwareAcceptanceCheck(
            name="measurement",
            passed=passed,
            message="Live measurement read completed." if passed else "Mass-flow value missing.",
            details={
                "mass_flow": measurement.mass_flow,
                "volume_flow": measurement.volume_flow,
                "density": measurement.density,
                "temperature": measurement.temperature,
                "status_flags": list(measurement.status_flags),
                "source_channel": measurement.source_channel,
            },
        )

    def _check_read_only_factory_preview(
        self,
        device: FlowmeterDevice,
    ) -> HardwareAcceptanceCheck:
        try:
            health = device.read_health()
            measurement = device.read_measurement()
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="read_only_factory_preview",
                passed=False,
                message=str(exc),
            )
        passed = not health.alarm_flags and measurement.mass_flow is not None
        return HardwareAcceptanceCheck(
            name="read_only_factory_preview",
            passed=passed,
            message="Read-only factory checks can be attempted.",
            details={
                "health_alarm_count": len(health.alarm_flags),
                "mass_flow_present": measurement.mass_flow is not None,
            },
        )

    def _check_write_guard_dry_run(
        self,
        device: FlowmeterDevice,
        request: ParameterWriteRequest,
    ) -> HardwareAcceptanceCheck:
        safe_request = ParameterWriteRequest(
            parameter_name=request.parameter_name,
            new_value=request.new_value,
            mode=WriteMode.DRY_RUN,
            actor=request.actor,
            workflow_state=request.workflow_state,
            run_id=request.run_id,
            expected_previous_value=request.expected_previous_value,
            metadata={**request.metadata, "hardware_acceptance": True},
        )
        try:
            decision = self._write_guard.evaluate(device, safe_request)
        except Exception as exc:
            return HardwareAcceptanceCheck(
                name="write_guard_dry_run",
                passed=False,
                message=str(exc),
            )
        return HardwareAcceptanceCheck(
            name="write_guard_dry_run",
            passed=decision.allowed,
            message=decision.result.message or decision.result.status.value,
            details={
                "allowed": decision.allowed,
                "audit_id": decision.audit_id,
                "parameter_name": decision.result.parameter_name,
                "status": decision.result.status.value,
                "previous_value": decision.result.previous_value,
                "new_value": decision.result.new_value,
            },
        )

    def _write_summary(
        self,
        run_id: str,
        checks: list[HardwareAcceptanceCheck],
    ) -> str | None:
        if self._repository is None or self._artifact_store is None:
            return None
        created_at = datetime.now(UTC)
        artifact_id = f"{run_id}-HARDWARE-ACCEPTANCE"
        lines = ["CoreFlow Studio Hardware Acceptance Preparation", ""]
        for check in checks:
            result = "passed" if check.passed else "failed"
            lines.append(f"{check.name}: {result} - {check.message}")
        artifact = self._artifact_store.write_artifact(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=ArtifactType.LOG,
            file_name="hardware_acceptance.txt",
            content="\n".join(lines).encode("utf-8"),
            created_at=created_at,
            file_format="txt",
        )
        self._repository.save_artifact(artifact)
        return artifact_id


def _diagnostic_details(device: FlowmeterDevice) -> dict[str, Any]:
    diagnostics = device.communication_diagnostics()
    return {
        "state": diagnostics.state.value,
        "request_count": diagnostics.request_count,
        "successful_response_count": diagnostics.successful_response_count,
        "timeout_count": diagnostics.timeout_count,
        "frame_error_count": diagnostics.frame_error_count,
        "exception_response_count": diagnostics.exception_response_count,
        "last_error": diagnostics.last_error,
        "average_response_ms": diagnostics.average_response_ms,
    }

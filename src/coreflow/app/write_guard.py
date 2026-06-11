"""Application-level guard for safety-sensitive device writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from coreflow.devices import (
    ConfigurationParameter,
    FlowmeterDevice,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
    WriteResultStatus,
)
from coreflow.storage.models import AuditLogRecord
from coreflow.storage.repositories import StorageRepository


@dataclass(frozen=True, slots=True)
class WriteGuardDecision:
    """Validation decision and optional device result."""

    allowed: bool
    audit_id: str
    result: ParameterWriteResult


class WriteGuardService:
    """Validates preview, dry-run, and armed writes before device access."""

    def __init__(
        self,
        repository: StorageRepository | None = None,
        write_capable_states: tuple[str, ...] = ("calibration_write_armed",),
    ) -> None:
        self._repository = repository
        self._write_capable_states = write_capable_states

    def preview(
        self,
        device: FlowmeterDevice,
        request: ParameterWriteRequest,
    ) -> WriteGuardDecision:
        preview_request = ParameterWriteRequest(
            parameter_name=request.parameter_name,
            new_value=request.new_value,
            mode=WriteMode.PREVIEW,
            actor=request.actor,
            workflow_state=request.workflow_state,
            run_id=request.run_id,
            expected_previous_value=request.expected_previous_value,
            metadata=request.metadata,
        )
        audit_id = f"AUD-{uuid4().hex}"
        previous = self._read_parameter(device, preview_request.parameter_name)
        validation_error = self._validate(preview_request, previous)
        status = (
            WriteResultStatus.REJECTED
            if validation_error is not None
            else WriteResultStatus.PREVIEWED
        )
        result = ParameterWriteResult(
            parameter_name=preview_request.parameter_name,
            status=status,
            previous_value=previous.value if previous else None,
            new_value=preview_request.new_value,
            audit_id=audit_id,
            message=validation_error,
        )
        self._audit(
            preview_request,
            result,
            audit_id,
            validation_error or "previewed",
        )
        return WriteGuardDecision(
            allowed=validation_error is None,
            audit_id=audit_id,
            result=result,
        )

    def evaluate(
        self,
        device: FlowmeterDevice,
        request: ParameterWriteRequest,
    ) -> WriteGuardDecision:
        previous = self._read_parameter(device, request.parameter_name)
        return self._evaluate_with_previous(device, request, previous)

    def evaluate_known_parameter(
        self,
        device: FlowmeterDevice,
        request: ParameterWriteRequest,
        parameter: ConfigurationParameter,
    ) -> WriteGuardDecision:
        """Evaluate a write when validated parameter metadata is already loaded."""

        return self._evaluate_with_previous(device, request, parameter)

    def _evaluate_with_previous(
        self,
        device: FlowmeterDevice,
        request: ParameterWriteRequest,
        previous: ConfigurationParameter | None,
    ) -> WriteGuardDecision:
        audit_id = f"AUD-{uuid4().hex}"
        validation_error = self._validate(request, previous)
        if validation_error is not None:
            result = ParameterWriteResult(
                parameter_name=request.parameter_name,
                status=WriteResultStatus.REJECTED,
                previous_value=previous.value if previous else None,
                new_value=request.new_value,
                audit_id=audit_id,
                message=validation_error,
            )
            self._audit(request, result, audit_id, validation_error)
            return WriteGuardDecision(allowed=False, audit_id=audit_id, result=result)

        guarded_request = ParameterWriteRequest(
            parameter_name=request.parameter_name,
            new_value=request.new_value,
            mode=request.mode,
            actor=request.actor,
            workflow_state=request.workflow_state,
            run_id=request.run_id,
            expected_previous_value=request.expected_previous_value,
            metadata={**request.metadata, "guard_audit_id": audit_id},
        )
        result = device.write_configuration(guarded_request)
        result = ParameterWriteResult(
            parameter_name=result.parameter_name,
            status=result.status,
            previous_value=result.previous_value,
            new_value=result.new_value,
            audit_id=audit_id,
            message=result.message,
        )
        self._audit(request, result, audit_id, "accepted")
        return WriteGuardDecision(allowed=True, audit_id=audit_id, result=result)

    def _validate(
        self,
        request: ParameterWriteRequest,
        previous: ConfigurationParameter | None,
    ) -> str | None:
        if previous is None:
            return f"Unknown parameter: {request.parameter_name}"
        if not previous.writable:
            return f"Parameter is not writable: {request.parameter_name}"
        if isinstance(request.new_value, int | float):
            if previous.minimum is not None and request.new_value < previous.minimum:
                return f"Value below minimum for {request.parameter_name}"
            if previous.maximum is not None and request.new_value > previous.maximum:
                return f"Value above maximum for {request.parameter_name}"
        if (
            request.mode is WriteMode.ARMED
            and request.workflow_state not in self._write_capable_states
        ):
            return f"Workflow state is not write-capable: {request.workflow_state}"
        return None

    def _read_parameter(
        self, device: FlowmeterDevice, parameter_name: str
    ) -> ConfigurationParameter | None:
        reader = getattr(device, "read_configuration_parameters", None)
        if callable(reader):
            parameters = reader((parameter_name,))
        else:
            parameters = device.read_configuration()
        for parameter in parameters:
            if parameter.name == parameter_name:
                return parameter
        return None

    def _audit(
        self,
        request: ParameterWriteRequest,
        result: ParameterWriteResult,
        audit_id: str,
        validation_result: str,
    ) -> None:
        if self._repository is None:
            return
        self._repository.save_audit_log(
            AuditLogRecord(
                audit_id=audit_id,
                timestamp=datetime.now(UTC),
                actor=request.actor,
                action_type="parameter_write",
                run_id=request.run_id,
                workflow_state=request.workflow_state,
                target=request.parameter_name,
                previous_value=result.previous_value,
                new_value=request.new_value,
                dry_run=request.mode is WriteMode.DRY_RUN,
                validation_result=validation_result,
                result=result.status.value,
                error_message=result.message,
            )
        )

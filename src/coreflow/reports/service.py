"""Report and export artifact generation."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from coreflow.storage import (
    Artifact,
    ArtifactStore,
    ArtifactType,
    DeviceRecord,
    StorageRepository,
)
from coreflow.workflows.models import RunSession, WorkflowStep


@dataclass(frozen=True, slots=True)
class ExportPackageResult:
    """Artifacts produced for one report/export package."""

    run_id: str
    report_artifact_id: str
    metrics_artifact_id: str
    measurements_artifact_id: str
    manifest_artifact_id: str

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return (
            self.report_artifact_id,
            self.metrics_artifact_id,
            self.measurements_artifact_id,
            self.manifest_artifact_id,
        )


class ReportExportService:
    """Build operator-readable reports and CSV exports from stored runs."""

    def __init__(
        self,
        repository: StorageRepository,
        artifact_store: ArtifactStore,
    ) -> None:
        self._repository = repository
        self._artifact_store = artifact_store

    def generate_export_package(self, run_id: str) -> ExportPackageResult:
        run = self._require_run(run_id)
        device = self._repository.get_device(run.device_id)
        steps = self._repository.list_steps(run_id)
        analysis_results = self._repository.list_analysis_results(run_id)
        source_artifacts = self._repository.list_artifacts(run_id)
        created_at = datetime.now(UTC)

        report_artifact = self._write_artifact(
            run_id=run_id,
            artifact_id=f"{run_id}-REPORT-TXT",
            artifact_type=ArtifactType.REPORT,
            file_name="operator_report.txt",
            content=self._render_report(
                run=run,
                device=device,
                steps=steps,
                source_artifacts=source_artifacts,
            ),
            created_at=created_at,
            file_format="txt",
        )
        metrics_artifact = self._write_artifact(
            run_id=run_id,
            artifact_id=f"{run_id}-EXPORT-METRICS",
            artifact_type=ArtifactType.EXPORT,
            file_name="metrics.csv",
            content=self._metrics_csv(run, analysis_results),
            created_at=created_at,
            file_format="csv",
        )
        measurements_artifact = self._write_artifact(
            run_id=run_id,
            artifact_id=f"{run_id}-EXPORT-MEASUREMENTS",
            artifact_type=ArtifactType.EXPORT,
            file_name="measurements.csv",
            content=self._measurements_csv(source_artifacts),
            created_at=created_at,
            file_format="csv",
        )
        manifest_artifact = self._write_artifact(
            run_id=run_id,
            artifact_id=f"{run_id}-EXPORT-MANIFEST",
            artifact_type=ArtifactType.EXPORT,
            file_name="export_manifest.json",
            content=self._manifest_json(
                run=run,
                device=device,
                steps=steps,
                source_artifacts=source_artifacts,
                generated_artifacts=(
                    report_artifact,
                    metrics_artifact,
                    measurements_artifact,
                ),
                created_at=created_at,
            ),
            created_at=created_at,
            file_format="json",
        )
        return ExportPackageResult(
            run_id=run_id,
            report_artifact_id=report_artifact.artifact_id,
            metrics_artifact_id=metrics_artifact.artifact_id,
            measurements_artifact_id=measurements_artifact.artifact_id,
            manifest_artifact_id=manifest_artifact.artifact_id,
        )

    def _write_artifact(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        file_name: str,
        content: bytes,
        created_at: datetime,
        file_format: str,
    ) -> Artifact:
        artifact = self._artifact_store.write_artifact(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            file_name=file_name,
            content=content,
            created_at=created_at,
            file_format=file_format,
        )
        self._repository.save_artifact(artifact)
        return artifact

    def _render_report(
        self,
        *,
        run: RunSession,
        device: DeviceRecord | None,
        steps: tuple[WorkflowStep, ...],
        source_artifacts: tuple[Artifact, ...],
    ) -> bytes:
        analysis_results = self._repository.list_analysis_results(run.run_id)
        lines = [
            "CoreFlow Studio Operator Report",
            "",
            "Run",
            f"Run ID: {run.run_id}",
            f"Workflow: {run.workflow_name}",
            f"Run type: {run.run_type.value}",
            f"Status: {run.status.value}",
            f"Operator: {run.operator}",
            f"Software version: {run.software_version or ''}",
            f"Started at: {_format_dt(run.started_at)}",
            f"Ended at: {_format_dt(run.ended_at)}",
            "",
            "Device",
            f"Device ID: {run.device_id}",
            f"Device type: {device.device_type if device else ''}",
            f"Serial number: {device.serial_number if device else ''}",
            f"Model: {device.model if device else ''}",
            f"Firmware: {device.firmware_version if device else ''}",
            "",
            "Configuration Snapshot",
            json.dumps(run.configuration_snapshot, indent=2, sort_keys=True, default=str),
            "",
            "Workflow Steps",
        ]
        for step in steps:
            lines.extend(
                [
                    f"- {step.name}: {step.status.value}",
                    f"  Type: {step.step_type.value}",
                    f"  Started: {_format_dt(step.started_at)}",
                    f"  Ended: {_format_dt(step.ended_at)}",
                    f"  Output: {json.dumps(step.output_summary, sort_keys=True, default=str)}",
                ]
            )
            if step.error_message:
                lines.append(f"  Error: {step.error_message}")
        lines.extend(["", "Results"])
        for result in analysis_results:
            lines.append(
                f"- {result.result_type}: {result.pass_fail_decision or ''}"
            )
            lines.append(
                f"  Algorithm: {result.algorithm_name} {result.algorithm_version}"
            )
            for key, value in result.summary_metrics.items():
                lines.append(f"  {key}: {value}")
        lines.extend(["", "Artifacts"])
        for artifact in source_artifacts:
            lines.append(
                f"- {artifact.artifact_id}: {artifact.artifact_type.value} {artifact.file_path}"
            )
        lines.append("")
        return "\n".join(lines).encode("utf-8")

    def _metrics_csv(self, run: RunSession, analysis_results: tuple[Any, ...]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "run_id",
                "workflow_name",
                "result_id",
                "result_type",
                "metric_name",
                "metric_value",
                "pass_fail_decision",
            ]
        )
        for result in analysis_results:
            if result.summary_metrics:
                for key, value in result.summary_metrics.items():
                    writer.writerow(
                        [
                            run.run_id,
                            run.workflow_name,
                            result.result_id,
                            result.result_type,
                            key,
                            value,
                            result.pass_fail_decision or "",
                        ]
                    )
            else:
                writer.writerow(
                    [
                        run.run_id,
                        run.workflow_name,
                        result.result_id,
                        result.result_type,
                        "",
                        "",
                        result.pass_fail_decision or "",
                    ]
                )
        return buffer.getvalue().encode("utf-8")

    def _measurements_csv(self, source_artifacts: tuple[Artifact, ...]) -> bytes:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "artifact_id",
                "captured_at",
                "mass_flow",
                "volume_flow",
                "density",
                "temperature",
                "status_flags",
                "source_channel",
            ]
        )
        for artifact in source_artifacts:
            if artifact.artifact_type is not ArtifactType.RAW or artifact.file_format != "csv":
                continue
            path = self._artifact_store.resolve(artifact.file_path)
            for row in _read_measurement_rows(path):
                writer.writerow(
                    [
                        artifact.artifact_id,
                        row.get("captured_at", ""),
                        row.get("mass_flow", ""),
                        row.get("volume_flow", ""),
                        row.get("density", ""),
                        row.get("temperature", ""),
                        row.get("status_flags", ""),
                        row.get("source_channel", ""),
                    ]
                )
        return buffer.getvalue().encode("utf-8")

    def _manifest_json(
        self,
        *,
        run: RunSession,
        device: DeviceRecord | None,
        steps: tuple[WorkflowStep, ...],
        source_artifacts: tuple[Artifact, ...],
        generated_artifacts: tuple[Artifact, ...],
        created_at: datetime,
    ) -> bytes:
        payload = {
            "package_version": "0.1",
            "generated_at": created_at.isoformat(),
            "run": {
                "run_id": run.run_id,
                "run_type": run.run_type.value,
                "workflow_name": run.workflow_name,
                "workflow_version": run.workflow_version,
                "status": run.status.value,
                "operator": run.operator,
                "started_at": _format_dt(run.started_at),
                "ended_at": _format_dt(run.ended_at),
                "software_version": run.software_version,
                "configuration_snapshot": run.configuration_snapshot,
            },
            "device": _device_payload(device),
            "steps": [
                {
                    "step_id": step.step_id,
                    "name": step.name,
                    "step_type": step.step_type.value,
                    "status": step.status.value,
                    "output_summary": step.output_summary,
                    "error_message": step.error_message,
                }
                for step in steps
            ],
            "source_artifacts": [_artifact_payload(artifact) for artifact in source_artifacts],
            "generated_artifacts": [
                _artifact_payload(artifact) for artifact in generated_artifacts
            ],
        }
        return json.dumps(payload, indent=2, sort_keys=True, default=str).encode("utf-8")

    def _require_run(self, run_id: str) -> RunSession:
        run = self._repository.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown run: {run_id}")
        return run


def _read_measurement_rows(path: Path) -> tuple[dict[str, str], ...]:
    if not path.exists():
        return ()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(dict(row) for row in reader)


def _device_payload(device: DeviceRecord | None) -> dict[str, Any] | None:
    if device is None:
        return None
    return {
        "device_id": device.device_id,
        "device_type": device.device_type,
        "serial_number": device.serial_number,
        "model": device.model,
        "firmware_version": device.firmware_version,
        "hardware_version": device.hardware_version,
        "protocol_address": device.protocol_address,
        "connection_metadata": device.connection_metadata,
    }


def _artifact_payload(artifact: Artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_type": artifact.artifact_type.value,
        "file_path": str(artifact.file_path),
        "file_format": artifact.file_format,
        "size_bytes": artifact.size_bytes,
        "checksum": artifact.checksum,
        "created_at": _format_dt(artifact.created_at),
    }


def _format_dt(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()

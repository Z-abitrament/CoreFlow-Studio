from __future__ import annotations

import csv
import json

from coreflow.analysis.calibration import CalibrationReferencePoint
from coreflow.reports import ReportExportService
from coreflow.simulation import (
    FlowProfile,
    FlowProfileKind,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import (
    ArtifactStore,
    ArtifactType,
    Database,
    StorageRepository,
    check_artifact_integrity,
)
from coreflow.workflows.calibration import (
    CalibrationPreviewConfig,
    CalibrationPreviewWorkflow,
)
from coreflow.workflows.factory_test import (
    FactoryMeasurementCheck,
    FactoryStabilityCheck,
    FactoryTestConfig,
    FactoryTestWorkflow,
)


def _storage(tmp_path):
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    repository = StorageRepository(database)
    return repository, ArtifactStore(tmp_path)


def _device(device_id: str, mass_flow: float = 10.0) -> SimulatedFlowmeterDevice:
    return SimulatedFlowmeterDevice(
        SimulatorScenario(
            name=f"report-{device_id}",
            device_id=device_id,
            seed=4,
            flow_profile=FlowProfile(kind=FlowProfileKind.CONSTANT, value=mass_flow),
        )
    )


def test_report_export_package_for_calibration_preview(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    device = _device("SIM-RPT-CAL", mass_flow=10.5)
    CalibrationPreviewWorkflow(repository, artifact_store).run(
        device,
        CalibrationPreviewConfig(
            run_id="RUN-20260606-000900",
            operator="pytest",
            reference_points=(
                CalibrationReferencePoint(reference_mass_flow=10.0, sample_count=3),
            ),
        ),
    )

    result = ReportExportService(repository, artifact_store).generate_export_package(
        "RUN-20260606-000900"
    )
    artifacts = {artifact.artifact_id: artifact for artifact in repository.list_artifacts()}
    report_text = (tmp_path / artifacts[result.report_artifact_id].file_path).read_text(
        encoding="utf-8"
    )
    metrics_rows = _read_csv(tmp_path / artifacts[result.metrics_artifact_id].file_path)
    measurement_rows = _read_csv(
        tmp_path / artifacts[result.measurements_artifact_id].file_path
    )
    manifest = json.loads(
        (tmp_path / artifacts[result.manifest_artifact_id].file_path).read_text(
            encoding="utf-8"
        )
    )

    assert result.artifact_ids == (
        "RUN-20260606-000900-REPORT-TXT",
        "RUN-20260606-000900-EXPORT-METRICS",
        "RUN-20260606-000900-EXPORT-MEASUREMENTS",
        "RUN-20260606-000900-EXPORT-MANIFEST",
    )
    assert "CoreFlow Studio Operator Report" in report_text
    assert "calibration_preview" in report_text
    metrics = {row["metric_name"]: row["metric_value"] for row in metrics_rows}
    assert metrics["mean_error"] == "0.5"
    assert len(measurement_rows) == 3
    assert measurement_rows[0]["artifact_id"] == "RUN-20260606-000900-RAW-001"
    assert manifest["run"]["workflow_name"] == "calibration_preview"
    assert len(manifest["generated_artifacts"]) == 3
    assert artifacts[result.report_artifact_id].artifact_type is ArtifactType.REPORT
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_report_export_package_for_factory_test(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    device = _device("SIM-RPT-FACTORY")
    FactoryTestWorkflow(repository, artifact_store).run(
        device,
        FactoryTestConfig(
            run_id="RUN-20260606-000901",
            operator="pytest",
            measurement_check=FactoryMeasurementCheck(
                reference_mass_flow=10.0,
                sample_count=3,
                max_abs_error=0.25,
            ),
            stability_check=FactoryStabilityCheck(
                sample_count=4,
                max_range=0.1,
                max_stddev=0.1,
            ),
        ),
    )

    result = ReportExportService(repository, artifact_store).generate_export_package(
        "RUN-20260606-000901"
    )
    artifacts = {artifact.artifact_id: artifact for artifact in repository.list_artifacts()}
    metrics_rows = _read_csv(tmp_path / artifacts[result.metrics_artifact_id].file_path)
    measurement_rows = _read_csv(
        tmp_path / artifacts[result.measurements_artifact_id].file_path
    )
    manifest = json.loads(
        (tmp_path / artifacts[result.manifest_artifact_id].file_path).read_text(
            encoding="utf-8"
        )
    )

    metric_names = {row["metric_name"] for row in metrics_rows}
    assert {"measurement_abs_error", "stability_range"} <= metric_names
    assert len(measurement_rows) == 7
    assert manifest["run"]["workflow_name"] == "automated_factory_test"
    assert len(manifest["source_artifacts"]) == 2
    assert repository.count_rows("artifacts") == 6
    assert check_artifact_integrity(repository, tmp_path) == ()


def test_report_export_rejects_unknown_run(tmp_path) -> None:
    repository, artifact_store = _storage(tmp_path)
    service = ReportExportService(repository, artifact_store)

    try:
        service.generate_export_package("RUN-DOES-NOT-EXIST")
    except ValueError as exc:
        assert "Unknown run" in str(exc)
    else:
        raise AssertionError("Expected unknown run export to fail")


def _read_csv(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))

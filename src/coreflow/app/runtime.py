"""Application runtime services used by the first Qt desktop UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from coreflow import __version__
from coreflow.analysis.calibration import CalibrationReferencePoint
from coreflow.app.paths import default_user_data_root
from coreflow.devices import CommunicationState, FlowmeterDevice, Measurement
from coreflow.experiments import (
    CapturePlan,
    ExperimentDefinition,
    FixtureAction,
    MLInferenceConfig,
    ProcessingModuleConfig,
)
from coreflow.reports import ExportPackageResult, ReportExportService
from coreflow.simulation import (
    FlowProfile,
    FlowProfileKind,
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import (
    AnalysisResultRecord,
    Artifact,
    ArtifactStore,
    Database,
    RunSummary,
    StorageRepository,
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
from coreflow.workflows.experiment import (
    ExperimentWorkflow,
    ExperimentWorkflowConfig,
)


@dataclass(frozen=True, slots=True)
class ChannelSnapshot:
    """UI-ready state for one configured device channel."""

    device_id: str
    device_type: str
    connection_state: str
    source: str
    last_mass_flow: float | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class RunInspection:
    """Stored result details shown by the run-history panel."""

    summary: RunSummary
    steps: tuple[tuple[str, str], ...]
    analysis_results: tuple[AnalysisResultRecord, ...]
    artifacts: tuple[Artifact, ...]


@dataclass(slots=True)
class _ManagedDevice:
    device: FlowmeterDevice
    source: str
    device_type: str
    last_measurement: Measurement | None = None
    last_error: str | None = None


@dataclass(slots=True)
class CoreFlowRuntime:
    """Headless application facade for the M8 UI smoke path."""

    data_root: Path | None = None
    operator: str = "operator"
    _sequence: int = 0
    _devices: dict[str, _ManagedDevice] = field(default_factory=dict)
    database: Database = field(init=False)
    repository: StorageRepository = field(init=False)
    artifact_store: ArtifactStore = field(init=False)

    def __post_init__(self) -> None:
        root = self.data_root or default_user_data_root()
        self.data_root = Path(root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        database = Database(self.data_root / "coreflow.sqlite")
        database.initialize()
        self.database = database
        self.repository = StorageRepository(database)
        self.artifact_store = ArtifactStore(self.data_root)
        self._sequence = self.repository.count_rows("run_sessions")

    def add_simulated_device(
        self,
        device_id: str | None = None,
        *,
        mass_flow: float = 10.0,
        seed: int | None = None,
    ) -> ChannelSnapshot:
        """Create a deterministic simulator channel without connecting it."""

        index = len(self._devices) + 1
        resolved_id = device_id or f"SIM-UI-{index:03d}"
        resolved_seed = seed if seed is not None else index
        scenario = SimulatorScenario(
            name=f"ui-simulator-{resolved_id}",
            device_id=resolved_id,
            seed=resolved_seed,
            flow_profile=FlowProfile(
                kind=FlowProfileKind.CONSTANT,
                value=mass_flow,
            ),
            parameters=(
                ScenarioParameter(
                    name="zero_offset",
                    value=0.0,
                    unit="kg/s",
                    writable=True,
                    minimum=-10.0,
                    maximum=10.0,
                ),
            ),
            metadata={"created_by": "m8_ui"},
        )
        self._devices[resolved_id] = _ManagedDevice(
            device=SimulatedFlowmeterDevice(scenario),
            source="Simulator",
            device_type="simulated",
        )
        return self.channel_snapshot(resolved_id)

    def connect_device(self, device_id: str) -> ChannelSnapshot:
        managed = self._managed(device_id)
        try:
            managed.device.connect()
            managed.last_error = None
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            managed.last_error = str(exc)
        return self.channel_snapshot(device_id)

    def disconnect_device(self, device_id: str) -> ChannelSnapshot:
        managed = self._managed(device_id)
        try:
            managed.device.disconnect()
            managed.last_error = None
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            managed.last_error = str(exc)
        return self.channel_snapshot(device_id)

    def read_live_measurement(self, device_id: str) -> Measurement:
        managed = self._managed(device_id)
        try:
            measurement = managed.device.read_measurement()
        except Exception as exc:
            managed.last_error = str(exc)
            raise
        managed.last_measurement = measurement
        managed.last_error = None
        return measurement

    def run_calibration_preview(self, device_id: str) -> str:
        managed = self._managed(device_id)
        run_id = self._next_run_id()
        workflow = CalibrationPreviewWorkflow(
            repository=self.repository,
            artifact_store=self.artifact_store,
        )
        workflow.run(
            managed.device,
            CalibrationPreviewConfig(
                run_id=run_id,
                operator=self.operator,
                reference_points=(
                    CalibrationReferencePoint(
                        reference_mass_flow=10.0,
                        sample_count=3,
                        tolerance=0.25,
                    ),
                ),
                software_version=__version__,
            ),
        )
        return run_id

    def run_factory_test(self, device_id: str) -> str:
        managed = self._managed(device_id)
        run_id = self._next_run_id()
        workflow = FactoryTestWorkflow(
            repository=self.repository,
            artifact_store=self.artifact_store,
        )
        workflow.run(
            managed.device,
            FactoryTestConfig(
                run_id=run_id,
                operator=self.operator,
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
                software_version=__version__,
            ),
        )
        return run_id

    def run_default_experiment(self, device_id: str) -> str:
        managed = self._managed(device_id)
        run_id = self._next_run_id()
        definition = ExperimentDefinition(
            experiment_id="EXP-BASIC-STATS",
            name="Basic signal statistics",
            version="0.1",
            capture_plan=CapturePlan(sample_count=6, label="ui_basic_stats"),
            processing=(
                ProcessingModuleConfig(module_name="basic_signal_stats"),
            ),
            fixture_actions=(
                FixtureAction(
                    action_name="fixture_placeholder",
                    parameters={"mode": "noop"},
                    required=False,
                ),
            ),
            ml_inference=(
                MLInferenceConfig(
                    model_name="placeholder_model",
                    enabled=True,
                ),
            ),
            metadata={"created_by": "m10_runtime_default"},
        )
        workflow = ExperimentWorkflow(
            repository=self.repository,
            artifact_store=self.artifact_store,
        )
        workflow.run(
            managed.device,
            ExperimentWorkflowConfig(
                run_id=run_id,
                operator=self.operator,
                definition=definition,
                software_version=__version__,
            ),
        )
        return run_id

    def list_channels(self) -> tuple[ChannelSnapshot, ...]:
        return tuple(self.channel_snapshot(device_id) for device_id in self._devices)

    def channel_snapshot(self, device_id: str) -> ChannelSnapshot:
        managed = self._managed(device_id)
        diagnostic = managed.device.communication_diagnostics()
        if managed.last_error:
            state = CommunicationState.FAULTED.value
        else:
            state = diagnostic.state.value
        return ChannelSnapshot(
            device_id=device_id,
            device_type=managed.device_type,
            connection_state=state,
            source=managed.source,
            last_mass_flow=managed.last_measurement.mass_flow
            if managed.last_measurement is not None
            else None,
            last_error=managed.last_error,
        )

    def list_run_history(self) -> tuple[RunSummary, ...]:
        return self.repository.list_runs(limit=100)

    def inspect_run(self, run_id: str) -> RunInspection:
        summaries = {
            summary.run_id: summary for summary in self.repository.list_runs(limit=200)
        }
        summary = summaries[run_id]
        return RunInspection(
            summary=summary,
            steps=self.repository.list_step_statuses(run_id),
            analysis_results=self.repository.list_analysis_results(run_id),
            artifacts=self.repository.list_artifacts(run_id),
        )

    def generate_export_package(self, run_id: str) -> ExportPackageResult:
        service = ReportExportService(
            repository=self.repository,
            artifact_store=self.artifact_store,
        )
        return service.generate_export_package(run_id)

    def _next_run_id(self) -> str:
        self._sequence += 1
        return self.artifact_store.next_run_id(datetime.now(UTC), self._sequence)

    def _managed(self, device_id: str) -> _ManagedDevice:
        try:
            return self._devices[device_id]
        except KeyError as exc:
            raise ValueError(f"Unknown device: {device_id}") from exc

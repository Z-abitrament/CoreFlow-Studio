from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from coreflow.analysis.calibration import (
    KFactorCalibrationInput,
    RepeatabilityTrial,
    analyze_repeatability,
    calculate_k_factor,
)
from coreflow.devices import (
    CommunicationDiagnostic,
    CommunicationState,
    ConfigurationParameter,
    DeviceHealth,
    DeviceIdentity,
    DeviceType,
    FlowmeterDevice,
    Measurement,
    ParameterWriteRequest,
    ParameterWriteResult,
    WriteMode,
    WriteResultStatus,
)
from coreflow.simulation import (
    ScenarioParameter,
    SimulatedFlowmeterDevice,
    SimulatorScenario,
)
from coreflow.storage import Database, StorageRepository
from coreflow.workflows.calibration import (
    KFactorCalibrationConfig,
    KFactorCalibrationWorkflow,
    RepeatabilityTestConfig,
    RepeatabilityTestWorkflow,
    ZeroCalibrationConfig,
    ZeroCalibrationWorkflow,
)


def _repository(tmp_path) -> StorageRepository:
    database = Database(tmp_path / "coreflow.sqlite")
    database.initialize()
    return StorageRepository(database)


def test_calculate_k_factor_uses_manual_mass_total_formula() -> None:
    result = calculate_k_factor(
        KFactorCalibrationInput(
            mass_acc_before=100.0,
            mass_acc_after=112.0,
            standard_mass=12.6,
            current_k_factor=500.0,
        )
    )

    assert result.measured_mass_delta == 12.0
    assert result.corrected_k_factor == pytest.approx(525.0)


def test_analyze_repeatability_calculates_point_stddevs() -> None:
    result = analyze_repeatability(
        (
            RepeatabilityTrial(1.0, 1, 0.0, 10.0, 10.0),
            RepeatabilityTrial(1.0, 2, 0.0, 10.1, 10.0),
            RepeatabilityTrial(1.0, 3, 0.0, 9.9, 10.0),
            RepeatabilityTrial(2.0, 1, 0.0, 20.0, 20.0),
            RepeatabilityTrial(2.0, 2, 0.0, 20.2, 20.0),
            RepeatabilityTrial(2.0, 3, 0.0, 19.8, 20.0),
            RepeatabilityTrial(3.0, 1, 0.0, 30.0, 30.0),
            RepeatabilityTrial(3.0, 2, 0.0, 30.3, 30.0),
            RepeatabilityTrial(3.0, 3, 0.0, 29.7, 30.0),
        )
    )

    assert result.summary_metrics["flow_point_count"] == 3.0
    assert result.summary_metrics["trial_count"] == 9.0
    assert result.flow_points[0].repeatability_stddev_percent == pytest.approx(1.0)


def test_k_factor_workflow_writes_through_guard_and_stores_result(tmp_path) -> None:
    repository = _repository(tmp_path)
    device = SimulatedFlowmeterDevice(
        SimulatorScenario(
            name="k-factor",
            device_id="SIM-K",
            seed=1,
            parameters=(
                ScenarioParameter(
                    name="k_factor",
                    value=500.0,
                    writable=True,
                    minimum=0.0,
                    maximum=1000.0,
                ),
            ),
        )
    )
    workflow = KFactorCalibrationWorkflow(repository)

    result = workflow.run(
        device,
        KFactorCalibrationConfig(
            run_id="RUN-20260609-KFACTOR",
            operator="pytest",
            mass_acc_before=100.0,
            mass_acc_after=112.0,
            standard_mass=12.6,
            current_k_factor=500.0,
        ),
    )

    assert result.write_status == "applied"
    assert result.calibration.corrected_k_factor == pytest.approx(525.0)
    assert repository.get_run_status("RUN-20260609-KFACTOR") == "passed"
    assert repository.count_rows("audit_logs") == 1
    assert device.read_configuration()[0].value == pytest.approx(525.0)


def test_repeatability_workflow_stores_three_by_three_result(tmp_path) -> None:
    repository = _repository(tmp_path)
    device = SimulatedFlowmeterDevice(SimulatorScenario("repeatability", "SIM-R", 1))
    workflow = RepeatabilityTestWorkflow(repository)

    result = workflow.run(
        device,
        RepeatabilityTestConfig(
            run_id="RUN-20260609-REP",
            operator="pytest",
            trials=(
                RepeatabilityTrial(1.0, 1, 0.0, 10.0, 10.0),
                RepeatabilityTrial(1.0, 2, 0.0, 10.1, 10.0),
                RepeatabilityTrial(1.0, 3, 0.0, 9.9, 10.0),
                RepeatabilityTrial(2.0, 1, 0.0, 20.0, 20.0),
                RepeatabilityTrial(2.0, 2, 0.0, 20.2, 20.0),
                RepeatabilityTrial(2.0, 3, 0.0, 19.8, 20.0),
                RepeatabilityTrial(3.0, 1, 0.0, 30.0, 30.0),
                RepeatabilityTrial(3.0, 2, 0.0, 30.3, 30.0),
                RepeatabilityTrial(3.0, 3, 0.0, 29.7, 30.0),
            ),
        ),
    )

    assert result.result.summary_metrics["trial_count"] == 9.0
    assert repository.get_run_status("RUN-20260609-REP") == "passed"
    assert repository.count_rows("analysis_results") == 1


def test_zero_calibration_workflow_records_before_after_values(tmp_path) -> None:
    repository = _repository(tmp_path)
    device = ZeroCalibrationFakeDevice()
    workflow = ZeroCalibrationWorkflow(repository)

    result = workflow.run(
        device,
        ZeroCalibrationConfig(
            run_id="RUN-20260609-ZERO",
            operator="pytest",
            snapshot_parameter_names=("mass_acc", "delta_t"),
            completion_wait_s=0.0,
            max_poll_count=3,
        ),
    )

    assert result.record.completed is True
    assert result.record.zero_offset_change == pytest.approx(-0.25)
    assert result.record.delta_t_change == pytest.approx(-0.02)
    assert result.pre_snapshot == {"mass_acc": 100.0, "delta_t": 0.12}
    assert device.events[:2] == [
        ("read", ("mass_acc", "delta_t")),
        ("read", ("zero_offset", "delta_t")),
    ]
    write_index = device.events.index(("write", "zero_calibration_start", True))
    assert write_index > 1
    assert repository.get_run_status("RUN-20260609-ZERO") == "passed"
    assert repository.count_rows("audit_logs") == 1
    metrics = repository.list_analysis_results("RUN-20260609-ZERO")[0].summary_metrics
    assert metrics["pre_snapshot"]["mass_acc"] == pytest.approx(100.0)


@dataclass
class ZeroCalibrationFakeDevice(FlowmeterDevice):
    connected: bool = False
    start: bool = False
    poll_count: int = 0
    after_write: bool = False
    events: list[tuple] | None = None

    def connect(self) -> None:
        self.connected = True
        if self.events is None:
            self.events = []

    def disconnect(self) -> None:
        self.connected = False

    def read_identity(self) -> DeviceIdentity:
        return DeviceIdentity(
            device_id="ZERO-FAKE",
            device_type=DeviceType.SIMULATED,
            serial_number="ZERO-FAKE",
        )

    def read_health(self) -> DeviceHealth:
        return DeviceHealth(state=CommunicationState.CONNECTED)

    def read_measurement(self) -> Measurement:
        return Measurement(captured_at=datetime.now(UTC))

    def read_configuration(self) -> tuple[ConfigurationParameter, ...]:
        if self.events is not None:
            self.events.append(("read", None))
        return self._configuration_parameters()

    def _configuration_parameters(self) -> tuple[ConfigurationParameter, ...]:
        if self.start and self.after_write:
            self.poll_count += 1
            if self.poll_count >= 1:
                self.start = False
        zero_offset = 0.25 if self.poll_count == 0 else 0.0
        delta_t = 0.12 if self.poll_count == 0 else 0.10
        return (
            ConfigurationParameter(
                name="zero_calibration_start",
                value=self.start,
                writable=True,
            ),
            ConfigurationParameter(name="zero_offset", value=zero_offset),
            ConfigurationParameter(name="delta_t", value=delta_t),
            ConfigurationParameter(name="mass_acc", value=100.0),
        )

    def read_configuration_parameters(
        self,
        names: tuple[str, ...],
    ) -> tuple[ConfigurationParameter, ...]:
        if self.events is None:
            self.events = []
        self.events.append(("read", names))
        allowed = set(names)
        return tuple(
            parameter
            for parameter in self._configuration_parameters()
            if parameter.name in allowed
        )

    def write_configuration(
        self,
        request: ParameterWriteRequest,
    ) -> ParameterWriteResult:
        if self.events is not None:
            self.events.append(
                ("write", request.parameter_name, request.new_value)
            )
        if request.mode is WriteMode.ARMED:
            self.start = bool(request.new_value)
            self.poll_count = 0
            self.after_write = True
            status = WriteResultStatus.APPLIED
        else:
            status = WriteResultStatus.DRY_RUN
        return ParameterWriteResult(
            parameter_name=request.parameter_name,
            status=status,
            previous_value=False,
            new_value=request.new_value,
        )

    def communication_diagnostics(self) -> CommunicationDiagnostic:
        return CommunicationDiagnostic(state=CommunicationState.CONNECTED)

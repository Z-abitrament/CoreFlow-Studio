from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from coreflow.devices import ParameterWriteRequest, WriteMode, WriteResultStatus
from coreflow.simulation import (
    ReplayFlowmeterDevice,
    load_replay_file,
    replay_template_csv,
)


def test_load_replay_file_parses_csv_measurements(tmp_path) -> None:
    replay_path = tmp_path / "nominal.csv"
    replay_path.write_text(
        "\n".join(
            [
                "captured_at,mass_flow,volume_flow,density,temperature,status_flags,source_channel",
                "2026-06-09T08:00:00+00:00,10.0,0.010,998.2,20.0,replay|stable,SIM-REPLAY",
                "2026-06-09T08:00:00.100000+00:00,10.1,0.011,998.2,20.1,,SIM-REPLAY",
            ]
        ),
        encoding="utf-8",
    )

    replay = load_replay_file(replay_path)

    assert replay.device_id == "SIM-REPLAY"
    assert len(replay.samples) == 2
    assert replay.samples[0].captured_at == datetime(2026, 6, 9, 8, 0, tzinfo=UTC)
    assert replay.samples[0].mass_flow == 10.0
    assert replay.samples[0].status_flags == ("replay", "stable")
    assert replay.samples[0].raw_values["replay_source"] == str(replay_path)
    assert replay.samples[0].raw_values["replay_row_number"] == 2


def test_replay_file_defaults_timestamps_and_device_id(tmp_path) -> None:
    replay_path = tmp_path / "factory capture.csv"
    replay_path.write_text(
        "\n".join(
            [
                "mass_flow",
                "5.0",
                "5.1",
            ]
        ),
        encoding="utf-8",
    )

    replay = load_replay_file(replay_path)

    assert replay.device_id == "REPLAY-FACTORY-CAPTURE"
    assert replay.samples[0].captured_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert replay.samples[1].captured_at == datetime(
        2026, 1, 1, tzinfo=UTC
    ) + timedelta(milliseconds=100)


def test_replay_file_rejects_missing_mass_flow(tmp_path) -> None:
    replay_path = tmp_path / "invalid.csv"
    replay_path.write_text("density\n998.2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mass_flow"):
        load_replay_file(replay_path)


def test_replay_device_reads_samples_and_reports_eof(tmp_path) -> None:
    replay_path = tmp_path / "nominal.csv"
    replay_path.write_bytes(replay_template_csv(sample_count=2))
    device = ReplayFlowmeterDevice(replay_path)

    device.connect()
    identity = device.read_identity()
    first = device.read_measurement()
    second = device.read_measurement()

    assert identity.metadata["scenario"] == "csv_replay"
    assert identity.metadata["replay_sample_count"] == 2
    assert first.mass_flow == 10.0
    assert second.mass_flow == 10.01
    with pytest.raises(EOFError, match="exhausted"):
        device.read_measurement()
    assert device.communication_diagnostics().exception_response_count == 1


def test_replay_device_can_loop_samples_for_longer_workflows(tmp_path) -> None:
    replay_path = tmp_path / "loop.csv"
    replay_path.write_bytes(replay_template_csv(sample_count=2))
    device = ReplayFlowmeterDevice(replay_path, loop=True)

    device.connect()

    assert [device.read_measurement().mass_flow for _ in range(3)] == [
        10.0,
        10.01,
        10.0,
    ]


def test_replay_device_is_read_only(tmp_path) -> None:
    replay_path = tmp_path / "read-only.csv"
    replay_path.write_bytes(replay_template_csv(sample_count=1))
    device = ReplayFlowmeterDevice(replay_path)
    device.connect()

    result = device.write_configuration(
        ParameterWriteRequest(
            parameter_name="zero_offset",
            new_value=0.1,
            mode=WriteMode.ARMED,
            actor="pytest",
            workflow_state="calibration_write_armed",
        )
    )

    assert result.status is WriteResultStatus.REJECTED
    assert "read-only" in (result.message or "")


def test_replay_template_csv_is_loadable(tmp_path) -> None:
    replay_path = tmp_path / "template.csv"
    replay_path.write_bytes(replay_template_csv())

    replay = load_replay_file(replay_path)

    assert len(replay.samples) == 8
    assert replay.samples[0].source_channel == "REPLAY-TEMPLATE"

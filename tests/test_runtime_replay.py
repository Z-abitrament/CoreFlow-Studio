from __future__ import annotations

from coreflow.app import CoreFlowRuntime
from coreflow.simulation import replay_template_csv


def test_runtime_runs_experiment_against_replay_device(tmp_path) -> None:
    replay_path = tmp_path / "replay.csv"
    replay_path.write_bytes(replay_template_csv(sample_count=8))
    runtime = CoreFlowRuntime(data_root=tmp_path)

    channel = runtime.add_replay_device(replay_path)
    connected = runtime.connect_device(channel.device_id)
    first = runtime.read_live_measurement(channel.device_id)
    run_id = runtime.run_default_experiment(channel.device_id)
    stored_device = runtime.repository.get_device(channel.device_id)
    artifacts = runtime.repository.list_artifacts(run_id)

    assert channel.source == "Replay"
    assert channel.device_type == "simulated"
    assert connected.connection_state == "connected"
    assert first.mass_flow == 10.0
    assert runtime.repository.get_run_status(run_id) == "passed"
    assert stored_device is not None
    assert stored_device.connection_metadata["scenario"] == "csv_replay"
    assert stored_device.connection_metadata["replay_source"] == str(replay_path)
    assert any(artifact.artifact_id.endswith("EXPERIMENT-RAW") for artifact in artifacts)

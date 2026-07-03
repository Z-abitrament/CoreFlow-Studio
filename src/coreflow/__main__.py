"""Minimal M0 command-line entry point for CoreFlow Studio."""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from coreflow import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coreflow",
        description="CoreFlow Studio M0 bootstrap entry point.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the CoreFlow Studio version and exit.",
    )
    parser.add_argument(
        "--build-info",
        action="store_true",
        help="Print packaged-build version metadata and exit.",
    )
    parser.add_argument(
        "--api-manifest",
        action="store_true",
        help="Print the machine-readable local CoreFlow API manifest and exit.",
    )
    parser.add_argument(
        "--make-update-package",
        type=Path,
        default=None,
        help="Create update zip assets and latest.json from a packaged dist folder.",
    )
    parser.add_argument(
        "--update-output-dir",
        type=Path,
        default=None,
        help="Output directory for --make-update-package.",
    )
    parser.add_argument(
        "--update-base-url",
        default="",
        help="Base GitHub Release asset URL for generated update manifests.",
    )
    parser.add_argument(
        "--previous-update-version",
        default=None,
        help="Previous released version used to generate a patch package.",
    )
    parser.add_argument(
        "--previous-update-dist",
        type=Path,
        default=None,
        help="Previous packaged dist folder used to generate a patch package.",
    )
    parser.add_argument(
        "--previous-update-package",
        type=Path,
        default=None,
        help="Previous full update zip used to generate a patch package.",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch the Qt desktop UI.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Local SQLite and artifact data directory for app workflows.",
    )
    parser.add_argument(
        "--simulator-smoke",
        action="store_true",
        help=(
            "Run a headless simulator workflow smoke check against the local "
            "data store and exit."
        ),
    )
    parser.add_argument(
        "--write-register-map-template",
        type=Path,
        default=None,
        help="Write the M11 placeholder Modbus register-map JSON template and exit.",
    )
    parser.add_argument(
        "--write-replay-template",
        type=Path,
        default=None,
        help="Write a deterministic CSV replay template and exit.",
    )
    parser.add_argument(
        "--replay-smoke",
        type=Path,
        default=None,
        help="Run a headless replay-backed simulator workflow smoke check and exit.",
    )
    parser.add_argument(
        "--modbus-raw",
        default=None,
        help="Send one Modbus RTU raw frame and print the RX bytes as hex.",
    )
    parser.add_argument(
        "--modbus-port",
        default="COM1",
        help="COM port for --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-unit",
        type=int,
        default=1,
        help="Default Modbus unit ID for --modbus-raw connection settings.",
    )
    parser.add_argument(
        "--modbus-baudrate",
        type=int,
        default=19200,
        help="Baud rate for --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-parity",
        default="N",
        choices=("N", "E", "O"),
        help="Serial parity for --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-stop-bits",
        type=int,
        default=1,
        choices=(1, 2),
        help="Serial stop bits for --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-timeout",
        type=float,
        default=3.0,
        help="Read timeout in seconds for --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-retries",
        type=int,
        default=3,
        help="Retry count for standard Modbus operations in --modbus-raw.",
    )
    parser.add_argument(
        "--modbus-auto-crc",
        action="store_true",
        help="Append Modbus CRC16 to --modbus-raw before sending.",
    )
    parser.add_argument(
        "--modbus-json",
        action="store_true",
        help="Print --modbus-raw result as machine-readable JSON.",
    )
    parser.add_argument(
        "--asio-list-devices",
        action="store_true",
        help="List ASIO driver registrations and audio devices for IIS diagnostics.",
    )
    parser.add_argument(
        "--asio-loopback-smoke",
        action="store_true",
        help="Run a headless ASIO/IIS full-duplex loopback smoke check.",
    )
    parser.add_argument(
        "--asio-probe-native",
        action="store_true",
        help="Query a registered native Windows ASIO driver without streaming.",
    )
    parser.add_argument(
        "--asio-backend",
        choices=("auto", "sounddevice", "native", "fake"),
        default="auto",
        help="ASIO backend for device listing or loopback diagnostics.",
    )
    parser.add_argument(
        "--asio-device",
        default="BRAVO-HD Device Control",
        help="Audio device name or substring for ASIO/IIS diagnostics.",
    )
    parser.add_argument(
        "--asio-host-api",
        default="ASIO",
        help="Required audio host API for ASIO/IIS diagnostics.",
    )
    parser.add_argument(
        "--asio-sample-rate",
        type=int,
        default=48000,
        help="ASIO/IIS sample rate in Hz.",
    )
    parser.add_argument(
        "--asio-bit-depth",
        type=int,
        default=32,
        help="ASIO/IIS bit depth: 16, 24, or 32.",
    )
    parser.add_argument(
        "--asio-sample-format",
        choices=("float32", "int16", "int24", "int32"),
        default="float32",
        help="Python backend sample format for ASIO/IIS diagnostics.",
    )
    parser.add_argument(
        "--asio-input-channels",
        type=int,
        default=2,
        help="Input channel count for ASIO/IIS loopback diagnostics.",
    )
    parser.add_argument(
        "--asio-output-channels",
        type=int,
        default=2,
        help="Output channel count for ASIO/IIS loopback diagnostics.",
    )
    parser.add_argument(
        "--asio-frame-samples",
        type=int,
        default=256,
        help="Samples per IIS frame for ASIO/IIS loopback diagnostics.",
    )
    parser.add_argument(
        "--asio-frame-count",
        type=int,
        default=64,
        help="Number of IIS frames for ASIO/IIS loopback diagnostics.",
    )
    parser.add_argument(
        "--asio-amplitude",
        type=float,
        default=0.25,
        help="Normalized ASIO/IIS loopback output amplitude.",
    )
    parser.add_argument(
        "--asio-min-correlation",
        type=float,
        default=0.95,
        help="Minimum correlation for ASIO/IIS loopback pass/fail.",
    )
    parser.add_argument(
        "--asio-max-normalized-error",
        type=float,
        default=0.15,
        help="Maximum normalized error for ASIO/IIS loopback pass/fail.",
    )
    parser.add_argument(
        "--asio-max-latency-samples",
        type=int,
        default=12000,
        help="Maximum latency search window for ASIO/IIS loopback comparison.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(effective_argv)

    if args.version:
        print(f"CoreFlow Studio {__version__}")
    elif args.api_manifest:
        from coreflow.app.api_manifest import build_api_manifest

        print(json.dumps(build_api_manifest(), indent=2, sort_keys=True))
    elif args.build_info:
        from coreflow.build_info import current_build_info

        info = current_build_info()
        print(
            f"CoreFlow Studio {info.version} "
            f"commit={info.commit} channel={info.build_channel}"
        )
    elif args.make_update_package is not None:
        return make_update_package_cli(
            args.make_update_package,
            output_dir=args.update_output_dir,
            base_url=args.update_base_url,
            previous_version=args.previous_update_version,
            previous_dist_dir=args.previous_update_dist,
            previous_package=args.previous_update_package,
        )
    elif args.write_register_map_template is not None:
        from coreflow.hardware import (
            build_placeholder_register_map,
            register_map_to_json,
        )

        args.write_register_map_template.parent.mkdir(parents=True, exist_ok=True)
        args.write_register_map_template.write_text(
            register_map_to_json(build_placeholder_register_map()),
            encoding="utf-8",
        )
        print(f"Wrote register-map template: {args.write_register_map_template}")
    elif args.write_replay_template is not None:
        from coreflow.simulation import replay_template_csv

        args.write_replay_template.parent.mkdir(parents=True, exist_ok=True)
        args.write_replay_template.write_bytes(replay_template_csv())
        print(f"Wrote replay template: {args.write_replay_template}")
    elif args.simulator_smoke:
        return run_simulator_smoke(data_root=args.data_root)
    elif args.replay_smoke is not None:
        return run_replay_smoke(args.replay_smoke, data_root=args.data_root)
    elif args.modbus_raw is not None:
        return run_modbus_raw_cli(args)
    elif args.asio_list_devices:
        return list_asio_devices(backend_name=args.asio_backend)
    elif args.asio_probe_native:
        return probe_native_asio_driver(driver_name=args.asio_device)
    elif args.asio_loopback_smoke:
        return run_asio_loopback_cli(args)
    elif args.ui or should_launch_packaged_ui_by_default(args):
        return launch_ui_with_startup_logging(data_root=args.data_root)
    else:
        print("CoreFlow Studio M0 bootstrap is ready.")

    return 0


def should_launch_packaged_ui_by_default(args: argparse.Namespace) -> bool:
    """Return true when a packaged executable has no explicit CLI action."""

    if os.environ.get("COREFLOW_PACKAGED") != "1":
        return False
    return not any(
        (
            args.version,
            args.api_manifest,
            args.build_info,
            args.make_update_package is not None,
            args.simulator_smoke,
            args.write_register_map_template is not None,
            args.write_replay_template is not None,
            args.replay_smoke is not None,
            args.modbus_raw is not None,
            args.asio_list_devices,
            args.asio_probe_native,
            args.asio_loopback_smoke,
        )
    )


def launch_ui(data_root: Path | None = None) -> int:
    from coreflow.ui import run_app

    return run_app(data_root=data_root)


def launch_ui_with_startup_logging(data_root: Path | None = None) -> int:
    """Launch the UI and preserve packaged startup failures in a local log."""

    try:
        return launch_ui(data_root=data_root)
    except Exception as exc:
        if os.environ.get("COREFLOW_PACKAGED") != "1":
            raise
        try:
            log_path = write_startup_exception(exc, data_root=data_root)
        except Exception as log_exc:  # pragma: no cover - last-resort diagnostics
            print(
                "CoreFlow Studio UI startup failed before the window opened.",
                file=sys.stderr,
            )
            print(f"Startup log could not be written: {log_exc}", file=sys.stderr)
        else:
            print(
                "CoreFlow Studio UI startup failed before the window opened. "
                f"Details were written to: {log_path}",
                file=sys.stderr,
            )
        return 1


def write_startup_exception(
    exc: BaseException,
    *,
    data_root: Path | None = None,
) -> Path:
    from coreflow.build_info import current_build_info

    log_path = startup_log_path(data_root=data_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    build_info = current_build_info()
    formatted_traceback = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write("=" * 72)
        handle.write("\n")
        handle.write(f"timestamp={datetime.now(UTC).isoformat()}\n")
        handle.write(
            "build="
            f"version={build_info.version} "
            f"commit={build_info.commit} "
            f"channel={build_info.build_channel}\n"
        )
        handle.write(f"executable={sys.executable}\n")
        handle.write(f"argv={sys.argv!r}\n")
        handle.write("traceback:\n")
        handle.write(formatted_traceback)
        if not formatted_traceback.endswith("\n"):
            handle.write("\n")
    return log_path


def make_update_package_cli(
    dist_dir: Path,
    *,
    output_dir: Path | None,
    base_url: str,
    previous_version: str | None = None,
    previous_dist_dir: Path | None = None,
    previous_package: Path | None = None,
) -> int:
    from coreflow.build_info import current_build_info
    from coreflow.app.updates import create_update_release_assets

    info = current_build_info()
    resolved_output_dir = output_dir or dist_dir.parent / "updates"
    result = create_update_release_assets(
        dist_dir=dist_dir,
        output_dir=resolved_output_dir,
        version=info.version,
        base_url=base_url,
        previous_version=previous_version,
        previous_dist_dir=previous_dist_dir,
        previous_package=previous_package,
    )
    print(f"Wrote full update package: {result.full_zip_path}")
    if result.patch_zip_path is not None:
        print(f"Wrote patch update package: {result.patch_zip_path}")
    elif result.skipped_patch_reason:
        print(result.skipped_patch_reason)
    print(f"Wrote update manifest: {result.manifest_path}")
    return 0


def startup_log_path(data_root: Path | None = None) -> Path:
    if data_root is not None:
        root = data_root
    else:
        from coreflow.app.paths import default_user_data_root

        root = default_user_data_root()
    return root / "logs" / "startup.log"


def run_simulator_smoke(data_root: Path | None = None) -> int:
    """Run packaged-app smoke coverage without requiring a visible Qt window."""

    from coreflow.app import CoreFlowRuntime

    runtime = CoreFlowRuntime(data_root=data_root, operator="simulator_smoke")
    channel = runtime.add_simulated_device(
        device_id="SIM-PACKAGE-SMOKE",
        mass_flow=10.0,
        seed=1200,
    )
    runtime.connect_device(channel.device_id)
    measurement = runtime.read_live_measurement(channel.device_id)
    calibration_run_id = runtime.run_calibration_preview(channel.device_id)
    factory_run_id = runtime.run_factory_test(channel.device_id)
    experiment_run_id = runtime.run_default_experiment(channel.device_id)
    export = runtime.generate_export_package(factory_run_id)

    print(
        "Simulator smoke passed: "
        f"device={channel.device_id} "
        f"mass_flow={measurement.mass_flow:.3f} "
        f"calibration_run={calibration_run_id} "
        f"factory_run={factory_run_id} "
        f"experiment_run={experiment_run_id} "
        f"manifest={export.manifest_artifact_id}"
    )
    return 0


def run_replay_smoke(replay_path: Path, data_root: Path | None = None) -> int:
    """Run a headless replay-backed workflow smoke check."""

    from coreflow.app import CoreFlowRuntime

    runtime = CoreFlowRuntime(data_root=data_root, operator="replay_smoke")
    channel = runtime.add_replay_device(replay_path)
    runtime.connect_device(channel.device_id)
    first = runtime.read_live_measurement(channel.device_id)
    experiment_run_id = runtime.run_default_experiment(channel.device_id)

    print(
        "Replay smoke passed: "
        f"device={channel.device_id} "
        f"mass_flow={first.mass_flow:.3f} "
        f"experiment_run={experiment_run_id} "
        f"source={replay_path}"
    )
    return 0


def run_modbus_raw_cli(args: argparse.Namespace) -> int:
    """Send one Modbus RTU raw frame from command-line arguments."""

    from coreflow.modbus_api import (
        ModbusCommunicationError,
        ModbusRawClient,
        bytes_to_hex,
    )

    request_payload = {
        "frame": args.modbus_raw,
        "append_crc": args.modbus_auto_crc,
        "port": args.modbus_port,
        "unit_id": args.modbus_unit,
    }
    try:
        with ModbusRawClient(
            port=args.modbus_port,
            unit_id=args.modbus_unit,
            baudrate=args.modbus_baudrate,
            parity=args.modbus_parity,
            stop_bits=args.modbus_stop_bits,
            read_timeout_s=args.modbus_timeout,
            retry_count=args.modbus_retries,
        ) as client:
            response = client.send_raw_frame(
                args.modbus_raw,
                append_crc=args.modbus_auto_crc,
            )
    except (ModbusCommunicationError, ValueError) as exc:
        if args.modbus_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "capability": "modbus.raw_frame",
                        "request": request_payload,
                        "error": str(exc),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"Modbus raw frame failed: {exc}", file=sys.stderr)
        return 2
    response_hex = bytes_to_hex(response)
    if args.modbus_json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "capability": "modbus.raw_frame",
                    "request": request_payload,
                    "response_hex": response_hex,
                },
                sort_keys=True,
            )
        )
    else:
        print(response_hex)
    return 0


def list_asio_devices(*, backend_name: str = "auto") -> int:
    """List host audio devices for ASIO/IIS hardware diagnostics."""

    from coreflow.protocols.asio import (
        AsioError,
        AsioRegistryScanner,
        build_asio_backend,
        format_device_listing,
        format_registered_asio_drivers,
    )

    print("Registered Windows ASIO drivers:")
    print(format_registered_asio_drivers(AsioRegistryScanner().list_drivers()))
    print("")
    print("PortAudio/sounddevice devices:")
    try:
        backend = build_asio_backend(backend_name)
        devices = backend.list_devices()
    except AsioError as exc:
        print(f"ASIO device listing unavailable: {exc}", file=sys.stderr)
        return 2
    print(format_device_listing(devices))
    return 0


def run_asio_loopback_cli(args: argparse.Namespace) -> int:
    """Run one headless ASIO/IIS loopback diagnostic from CLI arguments."""

    from coreflow.protocols.asio import (
        AsioError,
        AsioIisFrameConfig,
        AsioLoopbackThresholds,
        build_asio_backend,
        run_asio_loopback_smoke,
    )
    from coreflow.protocols.asio.loopback import loopback_result_to_json

    try:
        config = AsioIisFrameConfig(
            device_name=args.asio_device,
            host_api=args.asio_host_api,
            sample_rate=args.asio_sample_rate,
            bit_depth=args.asio_bit_depth,
            sample_format=args.asio_sample_format,
            input_channels=args.asio_input_channels,
            output_channels=args.asio_output_channels,
            samples_per_frame=args.asio_frame_samples,
            frame_count=args.asio_frame_count,
            amplitude=args.asio_amplitude,
        )
        result = run_asio_loopback_smoke(
            config,
            backend=build_asio_backend(args.asio_backend),
            thresholds=AsioLoopbackThresholds(
                min_correlation=args.asio_min_correlation,
                max_normalized_error=args.asio_max_normalized_error,
                max_latency_samples=args.asio_max_latency_samples,
            ),
        )
    except (AsioError, ValueError) as exc:
        print(f"ASIO/IIS loopback failed before comparison: {exc}", file=sys.stderr)
        return 2

    print(loopback_result_to_json(result))
    return 0 if result.passed else 1


def probe_native_asio_driver(*, driver_name: str) -> int:
    """Query a native Windows ASIO driver registration without streaming."""

    from coreflow.protocols.asio import (
        NativeAsioDriverProbe,
        NativeAsioError,
        format_native_asio_capabilities,
    )

    try:
        capabilities = NativeAsioDriverProbe(driver_name=driver_name).query_capabilities()
    except NativeAsioError as exc:
        print(f"Native ASIO probe failed: {exc}", file=sys.stderr)
        return 2
    print(format_native_asio_capabilities(capabilities))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

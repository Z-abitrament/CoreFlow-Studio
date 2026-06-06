"""Minimal M0 command-line entry point for CoreFlow Studio."""

from __future__ import annotations

import argparse
import os
import sys
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    effective_argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(effective_argv)

    if args.version:
        print(f"CoreFlow Studio {__version__}")
    elif args.build_info:
        from coreflow.build_info import current_build_info

        info = current_build_info()
        print(
            f"CoreFlow Studio {info.version} "
            f"commit={info.commit} channel={info.build_channel}"
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
    elif args.simulator_smoke:
        return run_simulator_smoke(data_root=args.data_root)
    elif args.ui or should_launch_packaged_ui_by_default(args):
        return launch_ui(data_root=args.data_root)
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
            args.build_info,
            args.simulator_smoke,
            args.write_register_map_template is not None,
        )
    )


def launch_ui(data_root: Path | None = None) -> int:
    from coreflow.ui import run_app

    return run_app(data_root=data_root)


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


if __name__ == "__main__":
    raise SystemExit(main())

"""Minimal M0 command-line entry point for CoreFlow Studio."""

from __future__ import annotations

import argparse
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
        "--ui",
        action="store_true",
        help="Launch the Qt desktop UI.",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Local SQLite and artifact data directory for the Qt UI.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"CoreFlow Studio {__version__}")
    elif args.ui:
        from coreflow.ui import run_app

        return run_app(data_root=args.data_root)
    else:
        print("CoreFlow Studio M0 bootstrap is ready.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

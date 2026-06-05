"""Minimal M0 command-line entry point for CoreFlow Studio."""

from __future__ import annotations

import argparse

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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(f"CoreFlow Studio {__version__}")
    else:
        print("CoreFlow Studio M0 bootstrap is ready.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

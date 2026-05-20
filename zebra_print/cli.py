"""Unified command line interface for Zebra print tools."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from . import convert_cli, proxy_cli
from .printing import list_windows_printers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zebra-print",
        description="Convert ZPL locally or run a TCP ZPL capture/forwarding proxy.",
    )
    parser.add_argument("--version", action="version", version=f"zebra-print {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    convert_parser = subparsers.add_parser("convert", help="Render a ZPL file to a PNG image")
    convert_cli.add_arguments(convert_parser)
    convert_parser.set_defaults(func=convert_cli.run, command_parser=convert_parser)

    proxy_parser = subparsers.add_parser("proxy", help="Listen for raw ZPL jobs and optionally print or forward them")
    proxy_cli.add_arguments(proxy_parser)
    proxy_parser.set_defaults(func=proxy_cli.run, command_parser=proxy_parser)

    printers_parser = subparsers.add_parser("printers", help="List local Windows printers")
    printers_parser.set_defaults(func=run_printers, command_parser=printers_parser)

    return parser


def run_printers(_args: argparse.Namespace, _parser: argparse.ArgumentParser) -> int:
    print(list_windows_printers())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args, args.command_parser)


if __name__ == "__main__":
    sys.exit(main())

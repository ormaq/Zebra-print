"""Command line interface for the ZPL capture proxy."""

from __future__ import annotations

import argparse
import logging

from .printer_info import load_printer_info
from .printing import list_windows_printers
from .proxy import ZPLCaptureProxy
from .renderer import RenderOptions


def parse_forward_target(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    if ":" not in value:
        raise argparse.ArgumentTypeError("forward target must use HOST:PORT")
    host, port_text = value.rsplit(":", 1)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("forward target port must be an integer") from exc
    if not host or port <= 0:
        raise argparse.ArgumentTypeError("forward target must use HOST:PORT")
    return host, port


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--list-printers", action="store_true", help="List Windows printers and exit")
    parser.add_argument("--bind-host", default="0.0.0.0", help="Host/IP to bind, defaults to all interfaces")
    parser.add_argument("--listen-port", type=int, default=9100, help="TCP port to listen on")
    parser.add_argument("--save-dir", default="./zpl_jobs", help="Directory for captured ZPL and rendered PNG files")
    parser.add_argument("--target-printer", help="Optional Windows printer name for the rendered PNG")
    parser.add_argument(
        "--print-mode",
        choices=("fit", "actual", "stretch"),
        default="fit",
        help="How to place the PNG on the Windows print page",
    )
    parser.add_argument("--forward-to-zebra", help="Optional HOST:PORT for forwarding the original ZPL")
    parser.add_argument("--no-forward", action="store_true", help="Ignore --forward-to-zebra and only save/render/print")
    parser.add_argument(
        "--printer-info-config",
        help="Optional JSON file overriding the default Zebra printer query info",
    )
    parser.add_argument("--dpi", type=int, default=203, help="Fallback DPI and PNG metadata")
    parser.add_argument("--width", type=float, default=4.0, help="Fallback label width in inches")
    parser.add_argument("--height", type=float, default=3.0, help="Fallback label height in inches")
    parser.add_argument("--ignore-zpl-size", action="store_true", help="Ignore ^PW/^LL in incoming ZPL")
    parser.add_argument("--crop", action="store_true", help="Crop rendered PNGs to non-white content")
    parser.add_argument(
        "--strict-graphic-crc",
        action="store_true",
        help="Fail graphic decoding when a B64/Z64 CRC is present and does not match",
    )
    parser.add_argument("--verbose", action="store_true", help="Print renderer warnings and proxy debug logs")
    return parser


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.list_printers:
        print(list_windows_printers())
        return 0

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    forward_host = None
    forward_port = None
    if args.forward_to_zebra and not args.no_forward:
        forward_host, forward_port = parse_forward_target(args.forward_to_zebra)

    options = RenderOptions(
        dpi=args.dpi,
        width_inches=args.width,
        height_inches=args.height,
        use_zpl_size=not args.ignore_zpl_size,
        crop=args.crop,
        verbose=args.verbose,
        strict_graphic_crc=args.strict_graphic_crc,
    )
    try:
        printer_info = load_printer_info(args.printer_info_config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    proxy = ZPLCaptureProxy(
        bind_host=args.bind_host,
        listen_port=args.listen_port,
        save_dir=args.save_dir,
        render_options=options,
        target_printer=args.target_printer,
        print_mode=args.print_mode,
        forward_host=forward_host,
        forward_port=forward_port,
        printer_info=printer_info,
    )
    proxy.start()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture raw ZPL jobs, render PNG copies, and optionally reprint/forward.")
    return add_arguments(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args, parser)

#!/usr/bin/env python3
"""Command line interface for offline ZPL-to-image conversion.

This command renders common ZPL label commands locally with Pillow. It does not
call Labelary or any other web API.

Examples:
  python -m zebra_print convert examples/example.zpl --output label.png
  zebra-print convert examples/example.zpl --width 4 --height 3 --dpi 203 --output label.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .renderer import DEFAULT_MAX_CANVAS_PIXELS, DEFAULT_MAX_GRAPHIC_BYTES, RenderOptions, render_zpl_file


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("zpl_file", nargs="?", help="Path to the ZPL file")
    parser.add_argument("-o", "--output", help="Output image path, defaults to the ZPL filename with .png")
    parser.add_argument("--dpi", type=int, default=203, help="Label DPI used for fallback sizing and PNG metadata")
    parser.add_argument("--width", type=float, default=4.0, help="Fallback label width in inches")
    parser.add_argument("--height", type=float, default=3.0, help="Fallback label height in inches")
    parser.add_argument(
        "--max-canvas-pixels",
        type=int,
        default=DEFAULT_MAX_CANVAS_PIXELS,
        help="Maximum rendered canvas area in pixels; use 0 to disable",
    )
    parser.add_argument(
        "--max-graphic-bytes",
        type=int,
        default=DEFAULT_MAX_GRAPHIC_BYTES,
        help="Maximum decoded graphic payload size in bytes; use 0 to disable",
    )
    parser.add_argument(
        "--ignore-zpl-size",
        action="store_true",
        help="Use --width/--height even when the ZPL contains ^PW or ^LL",
    )
    parser.add_argument("--crop", action="store_true", help="Crop blank whitespace around rendered content")
    parser.add_argument(
        "--strict-graphic-crc",
        action="store_true",
        help="Fail graphic decoding when a B64/Z64 CRC is present and does not match",
    )
    parser.add_argument("--verbose", action="store_true", help="Print parsed command warnings and render details")
    return parser


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if not args.zpl_file:
        parser.error("zpl_file is required")

    zpl_path = Path(args.zpl_file)
    if not zpl_path.exists():
        parser.error(f"file not found: {zpl_path}")

    output_path = Path(args.output) if args.output else zpl_path.with_suffix(".png")
    options = RenderOptions(
        dpi=args.dpi,
        width_inches=args.width,
        height_inches=args.height,
        use_zpl_size=not args.ignore_zpl_size,
        crop=args.crop,
        verbose=args.verbose,
        strict_graphic_crc=args.strict_graphic_crc,
        max_canvas_pixels=args.max_canvas_pixels,
        max_graphic_bytes=args.max_graphic_bytes,
    )

    try:
        image, report = render_zpl_file(zpl_path, options)
        image.save(output_path, dpi=(args.dpi, args.dpi))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Rendered: {output_path}")
    print(f"Size: {image.width}x{image.height}px @ {args.dpi} DPI")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a ZPL file to a local PNG image.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return add_arguments(parser)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run(args, parser)


if __name__ == "__main__":
    sys.exit(main())

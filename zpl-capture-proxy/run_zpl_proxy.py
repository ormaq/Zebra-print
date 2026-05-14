#!/usr/bin/env python3
"""Capture raw ZPL jobs over TCP and render local PNG copies."""

from __future__ import annotations

import sys
from pathlib import Path


SIBLING_CONVERTER = Path(__file__).resolve().parents[1] / "zpl-image-converter"
if SIBLING_CONVERTER.exists():
    sys.path.insert(0, str(SIBLING_CONVERTER))

from zpl_capture_proxy.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

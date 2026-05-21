"""Font loading with Zebra stored-font mapping support."""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any

try:
    from PIL import ImageFont
except ImportError as exc:  # pragma: no cover - exercised by users without deps.
    raise SystemExit("Pillow is required. Install it with: python -m pip install Pillow") from exc


LOGGER = logging.getLogger(__name__)
FONT_MAPPING_NAME = "font_mapping.json"
BUILT_IN_FALLBACKS = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/Arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
)

_font_cache: dict[tuple[str, int], ImageFont.ImageFont] = {}
_font_mapping: dict[str, tuple[str, ...]] = {}
_default_fallback: tuple[str, ...] = BUILT_IN_FALLBACKS
_mapping_loaded = False


def load_font_mapping() -> None:
    """Load the packaged font mapping once."""
    global _font_mapping, _default_fallback, _mapping_loaded

    if _mapping_loaded:
        return

    try:
        config = _load_mapping_config()
        _font_mapping = _read_mapping_entries(config)
        _default_fallback = _read_string_list(config.get("default_fallback"), "default_fallback") or BUILT_IN_FALLBACKS
        LOGGER.debug("Loaded font mappings for %s printer fonts", len(_font_mapping))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.warning("Could not load font mapping: %s; using built-in fallback", exc)
        _font_mapping = {}
        _default_fallback = BUILT_IN_FALLBACKS
    finally:
        _mapping_loaded = True


def load_font_for_printer_font(printer_font: str, size: int) -> ImageFont.ImageFont | None:
    """Return a mapped system font for a Zebra stored font, if one is available."""
    load_font_mapping()

    normalized_name = normalize_printer_font(printer_font)
    normalized_size = max(1, int(size))
    cache_key = (normalized_name, normalized_size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    for font_path in _font_mapping.get(normalized_name, ()):
        font = _try_load_truetype(font_path, normalized_size)
        if font is not None:
            _font_cache[cache_key] = font
            LOGGER.debug("Mapped %s -> %s", normalized_name, font_path)
            return font

    return None


def load_font(size: int, printer_font: str | None = None) -> ImageFont.ImageFont:
    """Load a mapped printer font, a configured fallback font, or Pillow's default."""
    normalized_size = max(1, int(size))

    if printer_font:
        mapped_font = load_font_for_printer_font(printer_font, normalized_size)
        if mapped_font is not None:
            return mapped_font
    else:
        load_font_mapping()

    fallback_key = ("DEFAULT", normalized_size)
    if fallback_key in _font_cache:
        return _font_cache[fallback_key]

    for font_path in _default_fallback:
        font = _try_load_truetype(font_path, normalized_size)
        if font is not None:
            _font_cache[fallback_key] = font
            return font

    return ImageFont.load_default()


def normalize_printer_font(printer_font: str) -> str:
    return printer_font.strip().upper()


def clear_font_cache(reset_mapping: bool = False) -> None:
    """Clear cached fonts, and optionally force the mapping JSON to reload."""
    global _font_mapping, _default_fallback, _mapping_loaded

    _font_cache.clear()
    if reset_mapping:
        _font_mapping = {}
        _default_fallback = BUILT_IN_FALLBACKS
        _mapping_loaded = False


def _load_mapping_config() -> dict[str, Any]:
    config_file = resources.files(__package__).joinpath(FONT_MAPPING_NAME)
    with config_file.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError(f"{FONT_MAPPING_NAME} must be a JSON object")
    return config


def _read_mapping_entries(config: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    raw_mappings = config.get("mappings", {})
    if not isinstance(raw_mappings, dict):
        raise ValueError("font_mapping.json 'mappings' must be an object")

    mappings: dict[str, tuple[str, ...]] = {}
    for printer_font, font_config in raw_mappings.items():
        if not isinstance(printer_font, str) or not isinstance(font_config, dict):
            continue
        system_fonts = _read_string_list(font_config.get("system_fonts"), f"mappings.{printer_font}.system_fonts")
        if system_fonts:
            mappings[normalize_printer_font(printer_font)] = system_fonts
    return mappings


def _read_string_list(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"font_mapping.json '{label}' must be a list of non-empty strings")
    return tuple(value)


def _try_load_truetype(font_path: str, size: int) -> ImageFont.ImageFont | None:
    try:
        return ImageFont.truetype(font_path, size)
    except OSError:
        return None

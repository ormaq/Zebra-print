"""Configurable Zebra printer query responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

STX = b"\x02"
ETX = b"\x03"
CRLF = b"\r\n"

DEFAULT_CONFIG_NAME = "printer_info.json"
SUPPORTED_TOP_LEVEL_KEYS = frozenset({"host_identification", "host_ram_status"})
HOST_IDENTIFICATION_KEYS = frozenset({"model", "firmware", "device_id", "memory"})
HOST_RAM_STATUS_KEYS = frozenset({"total_kb", "available_kb", "largest_free_kb"})


@dataclass(frozen=True)
class HostIdentification:
    model: str
    firmware: str
    device_id: str
    memory: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str) -> "HostIdentification":
        _reject_unknown_keys(data, HOST_IDENTIFICATION_KEYS, "host_identification", source)
        return cls(
            model=_read_ascii_string(data, "model", source),
            firmware=_read_ascii_string(data, "firmware", source),
            device_id=_read_ascii_string(data, "device_id", source),
            memory=_read_ascii_string(data, "memory", source),
        )

    @property
    def payload(self) -> str:
        return f"{self.model},{self.firmware},{self.device_id},{self.memory}"

    def response_bytes(self) -> bytes:
        return _frame(self.payload)


@dataclass(frozen=True)
class HostRamStatus:
    total_kb: int
    available_kb: int
    largest_free_kb: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str) -> "HostRamStatus":
        _reject_unknown_keys(data, HOST_RAM_STATUS_KEYS, "host_ram_status", source)
        return cls(
            total_kb=_read_int(data, "total_kb", source),
            available_kb=_read_int(data, "available_kb", source),
            largest_free_kb=_read_int(data, "largest_free_kb", source),
        )

    @property
    def payload(self) -> str:
        return f"{self.total_kb},{self.available_kb},{self.largest_free_kb}"

    def response_bytes(self) -> bytes:
        return _frame(self.payload)


@dataclass(frozen=True)
class PrinterInfo:
    host_identification: HostIdentification
    host_ram_status: HostRamStatus

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], source: str) -> "PrinterInfo":
        _reject_unknown_keys(data, SUPPORTED_TOP_LEVEL_KEYS, "printer_info", source)
        host_identification = _read_mapping(data, "host_identification", source)
        host_ram_status = _read_mapping(data, "host_ram_status", source)
        return cls(
            host_identification=HostIdentification.from_mapping(host_identification, source),
            host_ram_status=HostRamStatus.from_mapping(host_ram_status, source),
        )

    def response_for_query(self, query_type: str) -> bytes:
        query = query_type.upper()
        if query == "~HI":
            return self.host_identification.response_bytes()
        if query == "~HM":
            return self.host_ram_status.response_bytes()
        return STATIC_QUERY_RESPONSES.get(query, UNKNOWN_QUERY_RESPONSE)


def load_printer_info(config_path: str | Path | None = None) -> PrinterInfo:
    """Load printer info, applying an optional JSON override over the packaged default."""
    data = _load_default_config()
    source = DEFAULT_CONFIG_NAME

    if config_path is not None:
        path = Path(config_path)
        data = _merge_mappings(data, _load_json_file(path))
        source = str(path)

    return PrinterInfo.from_mapping(data, source)


def _load_default_config() -> dict[str, Any]:
    try:
        config = resources.files(__package__).joinpath(DEFAULT_CONFIG_NAME)
        with config.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid packaged printer info config JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Packaged printer info config must be a JSON object: {DEFAULT_CONFIG_NAME}")
    return data


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Printer info config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid printer info config JSON at {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Printer info config must be a JSON object: {path}")
    return data


def _merge_mappings(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = {**current, **value}
        else:
            merged[key] = value
    return merged


def _read_mapping(data: Mapping[str, Any], key: str, source: str) -> Mapping[str, Any]:
    value = data.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"Printer info config '{key}' must be an object in {source}")
    return value


def _reject_unknown_keys(data: Mapping[str, Any], allowed_keys: frozenset[str], label: str, source: str) -> None:
    unknown_keys = sorted(set(data) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"Unsupported {label} key(s) in {source}: {', '.join(unknown_keys)}")


def _read_ascii_string(data: Mapping[str, Any], key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Printer info config '{key}' must be a non-empty string in {source}")
    if any(char in value for char in "\r\n\x02\x03"):
        raise ValueError(f"Printer info config '{key}' contains unsupported control characters in {source}")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError(f"Printer info config '{key}' must be ASCII in {source}") from exc
    return value


def _read_int(data: Mapping[str, Any], key: str, source: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Printer info config '{key}' must be a non-negative integer in {source}")
    return value


def _frame(payload: str) -> bytes:
    return STX + payload.encode("ascii") + ETX + CRLF


UNKNOWN_QUERY_RESPONSE = _frame("OK")

STATIC_QUERY_RESPONSES: Mapping[str, bytes] = {
    "~HS": (
        b"\x02030,0,1,0212,015,0,0,0,000,0,0,0\x03\r\n"
        b"\x02001,0,0,0,1,2,4,0,00000000,1,000\x03\r\n"
        b"\x020000,0\x03\r\n"
    ),
    "~HQES": (
        b"\x02\r\n\r\n  PRINTER STATUS                            \r\n"
        b"   ERRORS:         1 00000000 00010000      \r\n"
        b"   WARNINGS:       0 00000000 00000000      \r\n\x03"
    ),
    "^HH": (
        b"\x02  +30.0               DARKNESS          \r\n"
        b"  LOW                 DARKNESS SWITCH   \r\n"
        b"  2.0 IPS             PRINT SPEED       \r\n"
        b"  +010                TEAR OFF ADJUST   \r\n"
        b"  TEAR OFF            PRINT MODE        \r\n"
        b"  GAP/NOTCH           MEDIA TYPE        \r\n"
        b"  TRANSMISSIVE        SENSOR SELECT     \r\n"
        b"  THERMAL-TRANS.      PRINT METHOD      \r\n"
        b"  599                 PRINT WIDTH       \r\n"
        b"  0212                LABEL LENGTH      \r\n"
        b"  15.0IN   380MM      MAXIMUM LENGTH    \r\n"
        b"  ZPL II              ZPL MODE          \r\n"
        b"  <~>  7EH            CONTROL PREFIX    \r\n"
        b"  <^>  5EH            FORMAT PREFIX     \r\n"
        b"  <,>  2CH            DELIMITER CHAR    \r\n"
        b"\x03\r\n"
    ),
    "~HD": (
        b"\x02\r\n"
        b"Head Temp = 24\r\n"
        b"Ambient Temp = 00\r\n"
        b"Head Test = Test Not Run\r\n"
        b"Darkness Adjust = 30\r\n"
        b"Print Speed = 2.0\r\n"
        b"Slew Speed = 6.0\r\n"
        b"Backfeed Speed = 2.0\r\n"
        b"Static_pitch_length = 0212\r\n"
        b"Dynamic_pitch_length = 0226\r\n"
        b"Max_dynamic_pitch_length = 0236\r\n"
        b"Min_dynamic_pitch_length = 0219\r\n"
        b"COMMAND PFX = ~ : FORMAT PFX = ^ : DELIMITER = ,\r\n"
        b"Dynamic_top_position = 0000\r\n"
        b"\r\n"
        b"No ribbon A/D = 0000\r\n"
        b"\r\n"
        b"PCB Temp = 00\r\n"
        b"\x03\r\n"
    ),
}

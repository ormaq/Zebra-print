"""Offline ZPL label renderer.

The renderer is intentionally self-contained: it parses common ZPL layout,
text, shape, barcode, downloaded-graphic, and field-graphic commands and paints
the result into a Pillow image without calling a web API.
"""

from __future__ import annotations

import base64
import re
import zlib
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover - exercised by users without deps.
    raise SystemExit("Pillow is required. Install it with: python -m pip install Pillow") from exc

from .font_loader import load_font as load_mapped_font
from .font_loader import load_font_for_printer_font


CONTROL_CHARS = "^~"
ORIENTATIONS = {"N", "R", "I", "B"}
GRAPHIC_PAYLOAD_PATTERN = re.compile(r":?(Z64|B64):([^:]+)(?::([0-9A-Fa-f]{4}))?$", re.IGNORECASE)
DEFAULT_MAX_CANVAS_PIXELS = 20_000_000
DEFAULT_MAX_GRAPHIC_BYTES = 16 * 1024 * 1024


class RenderOptions:
    def __init__(
        self,
        dpi: int = 203,
        width_inches: float = 4.0,
        height_inches: float = 3.0,
        use_zpl_size: bool = True,
        crop: bool = False,
        verbose: bool = False,
        strict_graphic_crc: bool = False,
        max_canvas_pixels: int | None = DEFAULT_MAX_CANVAS_PIXELS,
        max_graphic_bytes: int | None = DEFAULT_MAX_GRAPHIC_BYTES,
    ):
        self.dpi = dpi
        self.width_inches = width_inches
        self.height_inches = height_inches
        self.use_zpl_size = use_zpl_size
        self.crop = crop
        self.verbose = verbose
        self.strict_graphic_crc = strict_graphic_crc
        self.max_canvas_pixels = max_canvas_pixels
        self.max_graphic_bytes = max_graphic_bytes


class RenderReport:
    def __init__(self):
        self.warnings: list[str] = []
        self.downloaded_graphics = 0
        self.rendered_graphics = 0
        self.rendered_barcodes = 0
        self.rendered_text_fields = 0


class ZPLCommand:
    def __init__(self, prefix: str, name: str, params: str, offset: int):
        self.prefix = prefix
        self.name = name
        self.params = params
        self.offset = offset


class Graphic:
    def __init__(self, name: str, total_bytes: int, bytes_per_row: int, data: bytes):
        self.name = name
        self.total_bytes = total_bytes
        self.bytes_per_row = bytes_per_row
        self.data = data

    @property
    def width(self) -> int:
        return self.bytes_per_row * 8

    @property
    def height(self) -> int:
        if self.bytes_per_row <= 0:
            return 0
        return len(self.data) // self.bytes_per_row


class FontSpec:
    def __init__(self, name: str = "0", orientation: str = "N", height: int = 30, width: int = 30):
        self.name = name
        self.orientation = orientation
        self.height = height
        self.width = width


class BarcodeSpec:
    def __init__(self, kind: str, params: list[str]):
        self.kind = kind
        self.params = params


class DataMatrixSymbol:
    def __init__(
        self,
        rows: int,
        cols: int,
        region_rows: int,
        region_cols: int,
        data_codewords: int,
        error_codewords: int,
    ):
        self.rows = rows
        self.cols = cols
        self.region_rows = region_rows
        self.region_cols = region_cols
        self.data_codewords = data_codewords
        self.error_codewords = error_codewords

    @property
    def data_rows(self) -> int:
        return (self.rows // (self.region_rows + 2)) * self.region_rows

    @property
    def data_cols(self) -> int:
        return (self.cols // (self.region_cols + 2)) * self.region_cols


DM_SYMBOLS = [
    DataMatrixSymbol(10, 10, 8, 8, 3, 5),
    DataMatrixSymbol(12, 12, 10, 10, 5, 7),
    DataMatrixSymbol(14, 14, 12, 12, 8, 10),
    DataMatrixSymbol(16, 16, 14, 14, 12, 12),
    DataMatrixSymbol(18, 18, 16, 16, 18, 14),
    DataMatrixSymbol(20, 20, 18, 18, 22, 18),
    DataMatrixSymbol(22, 22, 20, 20, 30, 20),
    DataMatrixSymbol(24, 24, 22, 22, 36, 24),
    DataMatrixSymbol(26, 26, 24, 24, 44, 28),
]


CODE39_PATTERNS = {
    "0": "nnnwwnwnn",
    "1": "wnnwnnnnw",
    "2": "nnwwnnnnw",
    "3": "wnwwnnnnn",
    "4": "nnnwwnnnw",
    "5": "wnnwwnnnn",
    "6": "nnwwwnnnn",
    "7": "nnnwnnwnw",
    "8": "wnnwnnwnn",
    "9": "nnwwnnwnn",
    "A": "wnnnnwnnw",
    "B": "nnwnnwnnw",
    "C": "wnwnnwnnn",
    "D": "nnnnwwnnw",
    "E": "wnnnwwnnn",
    "F": "nnwnwwnnn",
    "G": "nnnnnwwnw",
    "H": "wnnnnwwnn",
    "I": "nnwnnwwnn",
    "J": "nnnnwwwnn",
    "K": "wnnnnnnww",
    "L": "nnwnnnnww",
    "M": "wnwnnnnwn",
    "N": "nnnnwnnww",
    "O": "wnnnwnnwn",
    "P": "nnwnwnnwn",
    "Q": "nnnnnnwww",
    "R": "wnnnnnwwn",
    "S": "nnwnnnwwn",
    "T": "nnnnwnwwn",
    "U": "wwnnnnnnw",
    "V": "nwwnnnnnw",
    "W": "wwwnnnnnn",
    "X": "nwnnwnnnw",
    "Y": "wwnnwnnnn",
    "Z": "nwwnwnnnn",
    "-": "nwnnnnwnw",
    ".": "wwnnnnwnn",
    " ": "nwwnnnwnn",
    "$": "nwnwnwnnn",
    "/": "nwnwnnnwn",
    "+": "nwnnnwnwn",
    "%": "nnnwnwnwn",
    "*": "nwnnwnwnn",
}


CODE11_PATTERNS = {
    "0": "101011",
    "1": "1101011",
    "2": "1001011",
    "3": "1100101",
    "4": "1011011",
    "5": "1101101",
    "6": "1001101",
    "7": "1010011",
    "8": "1101001",
    "9": "110101",
    "-": "101101",
}


I25_PATTERNS = {
    "0": "nnwwn",
    "1": "wnnnw",
    "2": "nwnnw",
    "3": "wwnnn",
    "4": "nnwnw",
    "5": "wnwnn",
    "6": "nwwnn",
    "7": "nnnww",
    "8": "wnnwn",
    "9": "nwnwn",
}


POSTNET_PATTERNS = {
    "0": "11000",
    "1": "00011",
    "2": "00101",
    "3": "00110",
    "4": "01001",
    "5": "01010",
    "6": "01100",
    "7": "10001",
    "8": "10010",
    "9": "10100",
}


EAN_L_PATTERNS = {
    "0": "0001101",
    "1": "0011001",
    "2": "0010011",
    "3": "0111101",
    "4": "0100011",
    "5": "0110001",
    "6": "0101111",
    "7": "0111011",
    "8": "0110111",
    "9": "0001011",
}


EAN_G_PATTERNS = {
    "0": "0100111",
    "1": "0110011",
    "2": "0011011",
    "3": "0100001",
    "4": "0011101",
    "5": "0111001",
    "6": "0000101",
    "7": "0010001",
    "8": "0001001",
    "9": "0010111",
}


EAN_R_PATTERNS = {
    "0": "1110010",
    "1": "1100110",
    "2": "1101100",
    "3": "1000010",
    "4": "1011100",
    "5": "1001110",
    "6": "1010000",
    "7": "1000100",
    "8": "1001000",
    "9": "1110100",
}


UPCE_PARITY = {
    "0": {
        "0": "EEEOOO",
        "1": "EEOEOO",
        "2": "EEOOEO",
        "3": "EEOOOE",
        "4": "EOEEOO",
        "5": "EOOEEO",
        "6": "EOOOEE",
        "7": "EOEOEO",
        "8": "EOEOOE",
        "9": "EOOEOE",
    },
    "1": {
        "0": "OOOEEE",
        "1": "OOEOEE",
        "2": "OOEEOE",
        "3": "OOEEEO",
        "4": "OEOOEE",
        "5": "OEEOOE",
        "6": "OEEEOO",
        "7": "OEOEOE",
        "8": "OEOEEO",
        "9": "OEEOEO",
    },
}


EAN13_PARITY = {
    "0": "LLLLLL",
    "1": "LLGLGG",
    "2": "LLGGLG",
    "3": "LLGGGL",
    "4": "LGLLGG",
    "5": "LGGLLG",
    "6": "LGGGLL",
    "7": "LGLGLG",
    "8": "LGLGGL",
    "9": "LGGLGL",
}


EAN2_PARITY = {
    "0": "LL",
    "1": "LG",
    "2": "GL",
    "3": "GG",
}


EAN5_PARITY = {
    "0": "GGLLL",
    "1": "GLGLL",
    "2": "GLLGL",
    "3": "GLLLG",
    "4": "LGGLL",
    "5": "LLGGL",
    "6": "LLLGG",
    "7": "LGLGL",
    "8": "LGLLG",
    "9": "LLGLG",
}


CODABAR_PATTERNS = {
    "0": "nnnnnww",
    "1": "nnnnwwn",
    "2": "nnnwnnw",
    "3": "wwnnnnn",
    "4": "nnwnnwn",
    "5": "wnnnnwn",
    "6": "nwnnnnw",
    "7": "nwnnwnn",
    "8": "nwwnnnn",
    "9": "wnnwnnn",
    "-": "nnnwwnn",
    "$": "nnwwnnn",
    ":": "wnnwnwn",
    "/": "wnwnnwn",
    ".": "wnwnwnn",
    "+": "nnwnwnw",
    "A": "nnwwnwn",
    "B": "nwnwnnw",
    "C": "nnnwnww",
    "D": "nnnwwwn",
}


MSI_PATTERNS = {
    "0": "100100100100",
    "1": "100100100110",
    "2": "100100110100",
    "3": "100100110110",
    "4": "100110100100",
    "5": "100110100110",
    "6": "100110110100",
    "7": "100110110110",
    "8": "110100100100",
    "9": "110100100110",
}


CODE93_PATTERNS = {
    "0": "100010100",
    "1": "101001000",
    "2": "101000100",
    "3": "101000010",
    "4": "100101000",
    "5": "100100100",
    "6": "100100010",
    "7": "101010000",
    "8": "100010010",
    "9": "100001010",
    "A": "110101000",
    "B": "110100100",
    "C": "110100010",
    "D": "110010100",
    "E": "110010010",
    "F": "110001010",
    "G": "101101000",
    "H": "101100100",
    "I": "101100010",
    "J": "100110100",
    "K": "100011010",
    "L": "101011000",
    "M": "101001100",
    "N": "101000110",
    "O": "100101100",
    "P": "100010110",
    "Q": "110110100",
    "R": "110110010",
    "S": "110101100",
    "T": "110100110",
    "U": "110010110",
    "V": "110011010",
    "W": "101101100",
    "X": "101100110",
    "Y": "100110110",
    "Z": "100111010",
    "-": "100101110",
    ".": "111010100",
    " ": "111010010",
    "$": "111001010",
    "/": "101101110",
    "+": "101110110",
    "%": "110101110",
    "a": "100100110",
    "b": "111011010",
    "c": "111010110",
    "d": "100110010",
    "*": "101011110",
}

CODE93_CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%abcd"

GRAPHIC_SYMBOLS = {
    "A": "\u00ae",
    "B": "\u00a9",
    "C": "\u2122",
    "D": "\u2120",
    "R": "\u00ae",
    "CIRCLE-R": "\u00ae",
    "COPYRIGHT": "\u00a9",
    "TM": "\u2122",
    "SM": "\u2120",
}


CODE128_PATTERNS = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
]


def render_zpl_file(path: str | Path, options: RenderOptions | None = None) -> tuple[Image.Image, RenderReport]:
    zpl_bytes = Path(path).read_bytes()
    return render_zpl_bytes(zpl_bytes, options)


def render_zpl_bytes(zpl_bytes: bytes, options: RenderOptions | None = None) -> tuple[Image.Image, RenderReport]:
    text = decode_zpl_bytes(zpl_bytes)
    return ZPLRenderer(options or RenderOptions()).render(text)


def decode_zpl_bytes(zpl_bytes: bytes) -> str:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return zpl_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return zpl_bytes.decode("latin-1", errors="replace")


def tokenize_zpl(zpl_text: str) -> list[ZPLCommand]:
    commands: list[ZPLCommand] = []
    i = 0
    length = len(zpl_text)
    caret_prefix = "^"
    tilde_prefix = "~"

    while i < length:
        control_chars = caret_prefix + tilde_prefix
        next_positions = [pos for pos in (zpl_text.find(ch, i) for ch in control_chars) if pos != -1]
        if not next_positions:
            break
        start = min(next_positions)
        if start + 1 >= length:
            break

        actual_prefix = zpl_text[start]
        prefix = "^" if actual_prefix == caret_prefix else "~"
        cmd_start = start + 1
        first = zpl_text[cmd_start]
        if prefix == "^" and first.upper() == "A":
            if cmd_start + 1 < length and zpl_text[cmd_start + 1] not in ",\r\n" + control_chars:
                name = zpl_text[cmd_start : cmd_start + 2].upper()
                param_start = cmd_start + 2
            else:
                name = "A"
                param_start = cmd_start + 1
        else:
            name = zpl_text[cmd_start : cmd_start + 2].upper()
            param_start = cmd_start + 2

        if name in {"CC", "CT"}:
            end = min(length, param_start + 1)
        else:
            next_controls = [pos for pos in (zpl_text.find(ch, param_start) for ch in control_chars) if pos != -1]
            end = min(next_controls) if next_controls else length
        params = zpl_text[param_start:end].strip("\r\n")
        commands.append(ZPLCommand(prefix=prefix, name=name, params=params, offset=start))
        if name == "CC" and params:
            caret_prefix = params[0]
        elif name == "CT" and params:
            tilde_prefix = params[0]
        i = end

    return commands


class ZPLRenderer:
    """Render common ZPL label commands into a Pillow image."""

    def __init__(self, options: RenderOptions | None = None):
        self.options = options or RenderOptions()
        self.report = RenderReport()
        self.graphics: dict[str, Graphic] = {}
        self.image: Image.Image | None = None
        self.draw: ImageDraw.ImageDraw | None = None

        self.width_px = max(1, int(round(self.options.width_inches * self.options.dpi)))
        self.height_px = max(1, int(round(self.options.height_inches * self.options.dpi)))
        self.label_home_x = 0
        self.label_home_y = 0
        self.label_top = 0
        self.label_shift = 0
        self.current_x = 0
        self.current_y = 0
        self.position_is_baseline = False
        self.font = FontSpec()
        self.default_font = FontSpec()
        self.field_block: tuple[int, int, int, str] | None = None
        self.field_hex_escape: str | None = None
        self.reverse_print = False
        self.pending_barcode: BarcodeSpec | None = None
        self.by_module_width = 2
        self.by_wide_ratio = 3.0
        self.by_height = 50
        self.formats: dict[str, list[ZPLCommand]] = {}
        self.storing_format_name: str | None = None
        self.stored_format_commands: list[ZPLCommand] = []
        self.saved_images: dict[str, Image.Image] = {}
        self.objects: dict[str, bytes] = {}
        self.font_aliases: dict[str, str] = {}
        self.character_set: str | None = None
        self.field_clock_indicator = "%"
        self.field_clock_delimiter = "%"
        self.default_orientation = "N"
        self.field_origins: list[tuple[int, int, bool]] | None = None
        self.field_number: str | None = None
        self.field_values: dict[str, str] = {}
        self.field_had_data = False
        self.field_extract: tuple[int, int | None] | None = None
        self.field_direction = "H"
        self.field_character_gap = 0
        self.font_links: list[tuple[str, str]] = []
        self.date_time_format: str | None = None
        self.advanced_text_properties: str | None = None
        self.encoding_table: str | None = None
        self.serialization_mask: str | None = None
        self.serialization_increment = 1
        self.memory_map: dict[str, str] = {}
        self.job_metadata: dict[str, str | bool] = {}
        self.printer_metadata: dict[str, str | bool] = {}
        self.media_metadata: dict[str, str | bool | int] = {}
        self.code_validation = False
        self.print_orientation = "N"
        self.rtc_metadata: dict[str, str | bool] = {}
        self.network_metadata: dict[str, str | bool] = {}
        self.host_status: dict[str, str | bool] = {}
        self.mirror_print = False
        self.rfid_metadata: dict[str, str | bool] = {}
        self.command_prefix = "^"
        self.tilde_prefix = "~"
        self.parameter_delimiter = ","
        self.units_metadata: dict[str, str | bool] = {}

    def render(self, zpl_text: str) -> tuple[Image.Image, RenderReport]:
        commands = tokenize_zpl(zpl_text)
        self._prescan(commands)
        self._create_canvas()

        for command in commands:
            self._handle_command(command)

        assert self.image is not None
        if self.mirror_print:
            self.image = self.image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.print_orientation == "I":
            self.image = self.image.rotate(180)
        if self.options.crop:
            self.image = crop_to_content(self.image)
        return self.image, self.report

    def _prescan(self, commands: Iterable[ZPLCommand]) -> None:
        for command in commands:
            if self.options.use_zpl_size and command.name == "PW":
                width = parse_int(first_csv(command.params))
                if width is not None and width > 0:
                    self.width_px = width
                elif width is not None:
                    self.warn(f"Ignoring non-positive ^PW width: {width}")
            elif self.options.use_zpl_size and command.name == "LL":
                height = parse_int(first_csv(command.params))
                if height is not None and height > 0:
                    self.height_px = height
                elif height is not None:
                    self.warn(f"Ignoring non-positive ^LL height: {height}")
            elif self.options.use_zpl_size and command.prefix == "~" and command.name == "JL":
                height = parse_int(first_csv(command.params))
                if height is not None and height > 0:
                    self.height_px = height
                elif height is not None:
                    self.warn(f"Ignoring non-positive ~JL height: {height}")
            elif command.prefix == "~" and command.name == "DG":
                self._download_graphic(command.params)
            elif command.prefix == "~" and command.name == "DY":
                self._download_object(command.params)

    def _create_canvas(self) -> None:
        self._check_pixel_area(self.width_px, self.height_px, "canvas")
        self.image = Image.new("RGB", (self.width_px, self.height_px), "white")
        self.draw = ImageDraw.Draw(self.image)

    def _check_pixel_area(self, width: int, height: int, label: str) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid {label} size: {width}x{height}px")
        max_pixels = self.options.max_canvas_pixels
        if max_pixels is not None and max_pixels > 0 and width * height > max_pixels:
            raise ValueError(f"{label} size {width}x{height}px exceeds max canvas pixels {max_pixels}")

    def _handle_command(self, command: ZPLCommand) -> None:
        name = command.name
        params = command.params

        if self.storing_format_name:
            if name == "XZ":
                self._finish_format_download()
            else:
                self.stored_format_commands.append(command)
            return

        if name in {"XA", "XZ", "JZ", "JM", "MC", "LR", "LS", "SZ"}:
            if name == "LS":
                self.label_shift = parse_int(first_csv(params)) or 0
            return
        if command.prefix == "~" and name in {"DG", "DY"}:
            return
        if command.prefix == "~" and name == "DN":
            return
        if command.prefix == "~" and name in {"HB", "HD", "HM", "HU"}:
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name == "JA":
            self._cancel_all(params)
            return
        if command.prefix == "~" and name == "JB":
            self._initialize_flash_memory()
            return
        if command.prefix == "~" and name in {"JC", "JF"}:
            self._ack_printer_metadata(name, params)
            return
        if command.prefix == "~" and name in {"JD", "JE"}:
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name == "JG":
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name in {"JI", "JN", "JO", "JQ"}:
            self._ack_printer_metadata(name, params)
            return
        if command.prefix == "~" and name == "JL":
            self._ack_media_metadata(name, params)
            return
        if command.prefix == "~" and name == "JP":
            self._pause_and_cancel_format(params)
            return
        if command.prefix == "~" and name == "JR":
            self._power_on_reset(params)
            return
        if command.prefix == "~" and name == "JS":
            self._ack_job_metadata(name, params)
            return
        if command.prefix == "~" and name == "JX":
            self._cancel_partial_format(params)
            return
        if command.prefix == "~" and name in {"KB", "RO"}:
            self._ack_printer_metadata(name, params)
            return
        if command.prefix == "~" and name in {"PH", "PL", "PP", "PR", "PS", "TA"}:
            self._ack_job_metadata(name, params)
            return
        if command.prefix == "~" and name == "PM":
            self._set_mirror_print(params)
            return
        if command.prefix == "~" and name == "SD":
            self._ack_media_metadata(name, params)
            return
        if command.prefix == "~" and name in {"WC", "WQ"}:
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name in {"HI", "HQ"}:
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name == "NC":
            self._ack_network_metadata(name, params)
            return
        if command.prefix == "~" and name in {"NR", "NT"}:
            self._ack_network_metadata(name, params)
            return
        if command.prefix == "~" and name in {"WL", "WR"}:
            self._ack_network_metadata(name, params)
            return
        if command.prefix == "~" and name == "HL":
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name in {"DB", "DE", "DS", "DT", "DU"}:
            self._ack_download_metadata(name, params)
            return
        if command.prefix == "~" and name == "HS":
            self._ack_host_status(name, params)
            return
        if command.prefix == "~" and name == "CC":
            self._set_command_prefix(params)
            return
        if command.prefix == "~" and name == "CD":
            self._set_parameter_delimiter(params)
            return
        if command.prefix == "~" and name == "CT":
            self._set_tilde_prefix(params)
            return
        if name == "DF":
            self._start_format_download(params)
        elif name == "XF":
            self._recall_format(params)
        elif name == "CI":
            self._set_character_set(params)
        elif name == "CW":
            self._assign_font_identifier(params)
        elif name == "FC":
            self._set_field_clock(params)
        elif name == "FE":
            self._set_field_extract(params)
        elif name == "FL":
            self._set_font_link(params)
        elif name == "FM":
            self._set_multiple_field_origins(params)
        elif name == "FN":
            self._set_field_number(params)
        elif name == "FP":
            self._set_field_parameter(params)
        elif name == "FW":
            self._set_field_orientation(params)
        elif name == "FX":
            return
        elif name == "KD":
            self.date_time_format = params
        elif name == "PA":
            self.advanced_text_properties = params
        elif name == "SE":
            self._select_encoding_table(params)
        elif name == "SF":
            self._set_serialization_field(params)
        elif name == "SN":
            self._render_serialized_data(params)
        elif name == "TB":
            self._set_text_block(params)
        elif name == "CM":
            self._set_memory_map(params)
        elif name == "CC":
            self._set_command_prefix(params)
        elif name == "CD":
            self._set_parameter_delimiter(params)
        elif name == "CT":
            self._set_tilde_prefix(params)
        elif name == "CN":
            self.job_metadata["cut_now"] = True
        elif name == "CO":
            self.job_metadata["cache_on"] = first_csv(params) or True
        elif name == "CP":
            self.job_metadata["remove_label"] = first_csv(params) or True
        elif name == "CV":
            self.code_validation = (first_csv(params) or "Y").upper() != "N"
        elif name == "JB":
            self._initialize_flash_memory()
        elif name in {"JH", "JI", "JJ", "JS", "JT", "JU", "JW", "KL", "KN", "KP"}:
            self._ack_printer_metadata(name, params)
        elif name in {"KV", "MA", "MI", "MN", "MP", "MT"}:
            self._ack_printer_metadata(name, params)
        elif name in {"MD", "MF", "ML", "MM"}:
            self._ack_media_metadata(name, params)
        elif name == "LT":
            self._set_label_top(params)
        elif name == "MU":
            self._set_units_of_measurement(params)
        elif name in {"MW", "SI", "SL"}:
            self._ack_printer_metadata(name, params)
        elif name in {"PF", "PH", "PN", "PP", "PR"}:
            self._ack_job_metadata(name, params)
        elif name == "PO":
            self._set_print_orientation(params)
        elif name == "PM":
            self._set_mirror_print(params)
        elif name == "SC":
            self._ack_printer_metadata(name, params)
        elif name in {"SO", "ST"}:
            self._ack_rtc_metadata(name, params)
        elif name in {"SP", "XB"}:
            self._ack_job_metadata(name, params)
        elif name == "PQ":
            self._ack_job_metadata(name, params)
        elif name in {"SQ", "SX"}:
            self._ack_network_metadata(name, params)
        elif name in {"SR", "SS", "XS", "ZZ"}:
            self._ack_printer_metadata(name, params)
        elif name in {"HF", "HG", "HH", "HT", "HV", "HW", "HY", "HZ"}:
            self._ack_host_query(name, params)
        elif name in {"LF", "WD"}:
            self._ack_host_query(name, params)
        elif name == "TO":
            self._transfer_object(params)
        elif name in {"KC", "NC", "ND", "NI", "NN", "NP", "NS", "NT", "NW", "WA", "WE", "WL", "WP", "WR", "WS", "WX"}:
            self._ack_network_metadata(name, params)
        elif name == "HL":
            self._ack_host_status(name, params)
        elif name == "HR":
            self._ack_rfid_metadata(name, params)
        elif name in {"RB", "RF", "RL", "RS", "RU", "RW"}:
            self._ack_rfid_metadata(name, params)
        elif name == "NB":
            self._ack_host_status(name, params)
        elif name == "ID":
            self._delete_graphic(params)
        elif name == "IL":
            self._load_stored_image(params)
        elif name == "IM":
            self._move_stored_image(params)
        elif name == "IS":
            self._save_current_image(params)
        elif name == "EG":
            self._erase_graphics()
        elif name == "LH":
            parts = split_csv(params)
            self.label_home_x = parse_int(parts[0] if parts else "") or 0
            self.label_home_y = parse_int(parts[1] if len(parts) > 1 else "") or 0
        elif name in {"FO", "FT"}:
            self._set_position(name, params)
        elif name.startswith("A"):
            self._set_font(name, params)
        elif name == "CF":
            self._set_default_font(params)
        elif name == "BY":
            self._set_barcode_defaults(params)
        elif name == "FB":
            self._set_field_block(params)
        elif name == "FH":
            self.field_hex_escape = params[:1] if params else "_"
        elif name == "FR":
            self.reverse_print = True
        elif name in {"FD", "FV"}:
            self._render_field_data(params)
        elif name == "FS":
            self._finish_field()
        elif name in {
            "B0",
            "B1",
            "B2",
            "B3",
            "B4",
            "B5",
            "B7",
            "B8",
            "B9",
            "BA",
            "BB",
            "BC",
            "BD",
            "BE",
            "BF",
            "BI",
            "BJ",
            "BK",
            "BL",
            "BM",
            "BO",
            "BP",
            "BR",
            "BS",
            "BT",
            "BU",
            "BZ",
            "BQ",
            "BX",
        }:
            self.pending_barcode = BarcodeSpec(kind=name, params=split_csv(params))
        elif name == "GB":
            self._draw_graphic_box(params)
        elif name == "GC":
            self._draw_circle(params)
        elif name == "GD":
            self._draw_diagonal(params)
        elif name == "GE":
            self._draw_ellipse(params)
        elif name == "GS":
            self._draw_graphic_symbol(params)
        elif name == "XG":
            self._draw_downloaded_graphic(params)
        elif name == "GF":
            self._draw_embedded_graphic(params)

    def _set_position(self, name: str, params: str) -> None:
        parts = split_csv(params)
        x = parse_int(parts[0] if parts else "") or 0
        y = parse_int(parts[1] if len(parts) > 1 else "") or 0
        self.current_x = self.label_home_x + self.label_shift + x
        self.current_y = self.label_home_y + self.label_top + y
        self.position_is_baseline = name == "FT"

    def _set_font(self, name: str, params: str) -> None:
        parts = split_csv(params)
        orientation = self.default_orientation
        height = self.font.height
        width = self.font.width
        font_name = name[1:] or self.default_font.name

        if parts:
            first = parts[0]
            if first and first[0].upper() in ORIENTATIONS:
                orientation = first[0].upper()
            elif first == "":
                orientation = self.default_font.orientation
        if len(parts) > 1:
            height = parse_int(parts[1]) or height
        if len(parts) > 2:
            width = parse_int(parts[2]) or height
        if name == "A@" and len(parts) > 3 and parts[3]:
            font_name = normalize_graphic_name(parts[3])
            if font_name not in self.objects and font_name not in self.font_aliases.values():
                test_font = load_font_for_printer_font(font_name, height)
                if not test_font:
                    self.warn(f"Stored font not found for ^A@: {font_name}; using fallback font")
        elif font_name in self.font_aliases:
            font_name = self.font_aliases[font_name]
        self.font = FontSpec(name=font_name, orientation=orientation, height=height, width=width)

    def _set_default_font(self, params: str) -> None:
        parts = split_csv(params)
        font_name = parts[0] if parts and parts[0] else "0"
        if font_name in self.font_aliases:
            font_name = self.font_aliases[font_name]
        height = parse_int(parts[1] if len(parts) > 1 else "") or self.default_font.height
        width = parse_int(parts[2] if len(parts) > 2 else "") or height
        self.default_font = FontSpec(name=font_name, height=height, width=width)
        self.font = self.default_font

    def _set_character_set(self, params: str) -> None:
        self.character_set = first_csv(params) or None

    def _assign_font_identifier(self, params: str) -> None:
        parts = split_csv(params)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            self.warn(f"Skipping malformed ^CW font assignment: {params[:60]}")
            return
        self.font_aliases[parts[0].upper()[:1]] = normalize_graphic_name(parts[1])

    def _set_field_clock(self, params: str) -> None:
        parts = split_csv(params)
        if parts and parts[0]:
            self.field_clock_indicator = parts[0][0]
        if len(parts) > 1 and parts[1]:
            self.field_clock_delimiter = parts[1][0]

    def _set_field_extract(self, params: str) -> None:
        parts = split_csv(params)
        start = parse_int(parts[0] if parts else "") or 1
        length = parse_int(parts[1] if len(parts) > 1 else "")
        self.field_extract = (max(1, start), length if length and length > 0 else None)

    def _set_font_link(self, params: str) -> None:
        parts = split_csv(params)
        if len(parts) >= 2 and parts[0] and parts[1]:
            self.font_links.append((normalize_graphic_name(parts[0]), normalize_graphic_name(parts[1])))

    def _set_multiple_field_origins(self, params: str) -> None:
        parts = split_csv(params)
        origins: list[tuple[int, int, bool]] = []
        for index in range(0, len(parts), 2):
            x = parse_int(parts[index] if index < len(parts) else "")
            y = parse_int(parts[index + 1] if index + 1 < len(parts) else "")
            if x is None or y is None:
                continue
            origins.append((self.label_home_x + self.label_shift + x, self.label_home_y + self.label_top + y, False))
        self.field_origins = origins or None

    def _set_field_number(self, params: str) -> None:
        number = first_csv(params).strip()
        self.field_number = number or None

    def _set_field_parameter(self, params: str) -> None:
        parts = split_csv(params)
        if parts and parts[0]:
            self.field_direction = parts[0].upper()[:1]
        gap = parse_int(parts[1] if len(parts) > 1 else "")
        if gap is not None:
            self.field_character_gap = gap

    def _set_field_orientation(self, params: str) -> None:
        orientation = parse_orientation(first_csv(params))
        self.default_orientation = orientation
        self.font.orientation = orientation
        self.default_font.orientation = orientation

    def _select_encoding_table(self, params: str) -> None:
        table_name = normalize_graphic_name(first_csv(params))
        self.encoding_table = table_name or None
        if table_name and table_name not in self.objects:
            self.warn(f"Encoding table not found for ^SE: {table_name}; using decoded input text")

    def _set_serialization_field(self, params: str) -> None:
        parts = split_csv(params)
        self.serialization_mask = parts[0] if parts and parts[0] else None
        self.serialization_increment = parse_int(parts[1] if len(parts) > 1 else "") or 1

    def _render_serialized_data(self, params: str) -> None:
        parts = split_csv(params)
        value = parts[0] if parts else ""
        increment = parse_int(parts[1] if len(parts) > 1 else "") or self.serialization_increment
        rendered = apply_serialization_mask(self.serialization_mask, value) if self.serialization_mask else value
        self._render_field_data(rendered)
        self.serialization_increment = increment

    def _set_text_block(self, params: str) -> None:
        parts = split_csv(params)
        width = parse_int(parts[0] if parts else "") or 0
        height = parse_int(parts[1] if len(parts) > 1 else "") or self.font.height
        line_spacing = parse_int(parts[2] if len(parts) > 2 else "") or 0
        justification = (parts[3] if len(parts) > 3 and parts[3] else "L").upper()
        line_step = max(1, self.font.height + line_spacing)
        max_lines = max(1, height // line_step)
        self.field_block = (width, max_lines, line_spacing, justification)

    def _set_memory_map(self, params: str) -> None:
        parts = split_csv(params)
        for index, drive in enumerate(("B", "E", "R", "A")):
            if index < len(parts) and parts[index]:
                self.memory_map[drive] = parts[index].upper()[:1]

    def _set_command_prefix(self, params: str) -> None:
        if params:
            self.command_prefix = params[0]

    def _set_parameter_delimiter(self, params: str) -> None:
        if params:
            self.parameter_delimiter = params[0]

    def _set_tilde_prefix(self, params: str) -> None:
        if params:
            self.tilde_prefix = params[0]

    def _set_label_top(self, params: str) -> None:
        self.label_top = parse_int(first_csv(params)) or 0
        self.media_metadata["LT"] = self.label_top

    def _set_units_of_measurement(self, params: str) -> None:
        parts = split_csv(params)
        mode = (parts[0] if parts and parts[0] else "D").upper()[:1]
        self.units_metadata = {
            "mode": mode,
            "format_base": parts[1] if len(parts) > 1 and parts[1] else True,
            "desired_base": parts[2] if len(parts) > 2 and parts[2] else True,
        }

    def _ack_printer_metadata(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.printer_metadata[name] = value

    def _ack_download_metadata(self, name: str, params: str) -> None:
        key = normalize_graphic_name(first_csv(params))
        if key:
            self._store_object(key, params.encode("latin-1", errors="replace"))
        self.printer_metadata[name] = params.strip() if params.strip() else True

    def _ack_job_metadata(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.job_metadata[name] = value

    def _ack_network_metadata(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.network_metadata[name] = value

    def _ack_rtc_metadata(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.rtc_metadata[name] = value

    def _ack_rfid_metadata(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.rfid_metadata[name] = value

    def _ack_host_status(self, name: str, params: str) -> None:
        value = params.strip() if params.strip() else True
        self.host_status[name] = value

    def _ack_host_query(self, name: str, params: str) -> None:
        value: str | bool = params.strip() if params.strip() else True
        if name == "HF":
            key = normalize_graphic_name(first_csv(params))
            if key:
                value = "FOUND" if (key in self.formats or key.split(":", 1)[-1] in self.formats) else "MISSING"
        elif name in {"HG", "HY"}:
            key = normalize_graphic_name(first_csv(params))
            if key:
                value = "FOUND" if (key in self.graphics or key.split(":", 1)[-1] in self.graphics) else "MISSING"
        elif name == "HT":
            value = str(len(self.font_links))
        elif name == "HW":
            value = str(len(self.graphics) + len(self.formats) + len(self.objects) + len(self.saved_images))
        self.host_status[name] = value

    def _cancel_all(self, params: str) -> None:
        self.job_metadata["cancel_all"] = params.strip() if params.strip() else True
        self.storing_format_name = None
        self.stored_format_commands = []

    def _pause_and_cancel_format(self, params: str) -> None:
        self.job_metadata["pause_cancel_format"] = params.strip() if params.strip() else True
        self.storing_format_name = None
        self.stored_format_commands = []

    def _cancel_partial_format(self, params: str) -> None:
        self.job_metadata["cancel_partial_format"] = params.strip() if params.strip() else True
        self.storing_format_name = None
        self.stored_format_commands = []

    def _power_on_reset(self, params: str) -> None:
        self.job_metadata["power_on_reset"] = params.strip() if params.strip() else True
        self.pending_barcode = None
        self.field_block = None
        self.field_hex_escape = None
        self.reverse_print = False
        self.field_origins = None
        self.field_number = None
        self.field_had_data = False
        self.field_extract = None
        self.storing_format_name = None
        self.stored_format_commands = []

    def _set_print_orientation(self, params: str) -> None:
        orientation = first_csv(params).upper()[:1]
        self.print_orientation = "I" if orientation == "I" else "N"
        self.job_metadata["print_orientation"] = self.print_orientation

    def _set_mirror_print(self, params: str) -> None:
        value = (first_csv(params) or "Y").upper()[:1]
        self.mirror_print = value != "N"
        self.job_metadata["mirror_print"] = self.mirror_print

    def _ack_media_metadata(self, name: str, params: str) -> None:
        value: str | bool | int = params.strip() if params.strip() else True
        if name in {"MD", "ML"}:
            parsed = parse_int(first_csv(params))
            if parsed is not None:
                value = parsed
        self.media_metadata[name] = value
        if name == "MF":
            self.job_metadata["media_feed"] = value
        elif name == "MM":
            self.job_metadata["print_mode"] = value
        elif name == "ML" and isinstance(value, int) and value > 0 and self.height_px > value:
            self.warn(f"Label length {self.height_px}px exceeds ^ML maximum {value}px")

    def _set_barcode_defaults(self, params: str) -> None:
        parts = split_csv(params)
        self.by_module_width = parse_int(parts[0] if parts else "") or self.by_module_width
        ratio = parse_float(parts[1] if len(parts) > 1 else "")
        if ratio:
            self.by_wide_ratio = ratio
        self.by_height = parse_int(parts[2] if len(parts) > 2 else "") or self.by_height

    def _set_field_block(self, params: str) -> None:
        parts = split_csv(params)
        width = parse_int(parts[0] if parts else "") or 0
        max_lines = parse_int(parts[1] if len(parts) > 1 else "") or 1
        line_spacing = parse_int(parts[2] if len(parts) > 2 else "") or 0
        justification = (parts[3] if len(parts) > 3 and parts[3] else "L").upper()
        self.field_block = (width, max_lines, line_spacing, justification)

    def _render_field_data(self, raw_data: str) -> None:
        self.field_had_data = True
        data = self._decode_field_data(raw_data)
        if self.serialization_mask:
            data = apply_serialization_mask(self.serialization_mask, data)
        data = self._apply_field_extract(data)
        if self.field_number:
            self.field_values[self.field_number] = data
        self._render_resolved_field_data(data)

    def _render_resolved_field_data(self, data: str) -> None:
        origins = self.field_origins or [(self.current_x, self.current_y, self.position_is_baseline)]
        original_x = self.current_x
        original_y = self.current_y
        original_baseline = self.position_is_baseline
        for x, y, baseline in origins:
            self.current_x = x
            self.current_y = y
            self.position_is_baseline = baseline
            self._render_single_field_data(data)
        self.current_x = original_x
        self.current_y = original_y
        self.position_is_baseline = original_baseline

    def _render_single_field_data(self, data: str) -> None:
        if self.pending_barcode:
            self._draw_barcode(self.pending_barcode, data)
            return
        if data:
            self._draw_text(data)
            self.report.rendered_text_fields += 1

    def _apply_field_extract(self, data: str) -> str:
        if not self.field_extract:
            return data
        start, length = self.field_extract
        offset = max(0, start - 1)
        if length is None:
            return data[offset:]
        return data[offset : offset + length]

    def _finish_field(self) -> None:
        if self.field_number and self.field_number in self.field_values and not self.field_had_data:
            self._render_resolved_field_data(self.field_values[self.field_number])
        self.pending_barcode = None
        self.field_block = None
        self.field_hex_escape = None
        self.reverse_print = False
        self.position_is_baseline = False
        self.field_origins = None
        self.field_number = None
        self.field_had_data = False
        self.field_extract = None
        self.field_direction = "H"
        self.field_character_gap = 0
        self.serialization_mask = None

    def _decode_field_data(self, text: str) -> str:
        escape = self.field_hex_escape
        if not escape:
            return text
        out: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == escape and i + 2 < len(text) and is_hex(text[i + 1 : i + 3]):
                out.append(chr(int(text[i + 1 : i + 3], 16)))
                i += 3
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def _draw_text(self, text: str) -> None:
        assert self.image is not None
        font = load_mapped_font(self.font.height, self.font.name)
        lines = self._wrap_text(text, font)
        x, y = self.current_x, self.current_y
        if self.position_is_baseline:
            try:
                y -= font.getmetrics()[0]
            except Exception:
                y -= self.font.height

        line_step = self.font.height + (self.field_block[2] if self.field_block else 0)
        if self.field_direction == "V":
            for char in text:
                if char not in "\r\n":
                    self._draw_single_text_line(char, x, y, font)
                y += max(1, line_step + self.field_character_gap)
            return
        for line in lines:
            self._draw_single_text_line(line, self._justified_line_x(line, x, font), y, font)
            y += max(1, line_step)

    def _wrap_text(self, text: str, font: ImageFont.ImageFont) -> list[str]:
        if not self.field_block:
            return text.split("\\&") if "\\&" in text else [text]
        width, max_lines, _line_spacing, _justification = self.field_block
        if width <= 0 or max_lines <= 1:
            return [text]
        words = text.replace("\\&", "\n").split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            bbox = font.getbbox(candidate)
            if bbox[2] - bbox[0] <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) >= max_lines:
                    break
        if current and len(lines) < max_lines:
            lines.append(current)
        return lines or [text]

    def _justified_line_x(self, text: str, x: int, font: ImageFont.ImageFont) -> int:
        if not self.field_block:
            return x
        width, _max_lines, _line_spacing, justification = self.field_block
        justification = justification[:1].upper()
        if width <= 0 or justification not in {"C", "R"}:
            return x

        text_width = self._scaled_text_width(text, font)
        remaining = max(0, width - text_width)
        if justification == "C":
            return x + remaining // 2
        return x + remaining

    def _scaled_text_width(self, text: str, font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox(text)
        text_width = max(1, bbox[2] - bbox[0] + 4)
        if self.font.width and self.font.height:
            text_width = int(round(text_width * max(0.1, self.font.width / self.font.height)))
        return text_width

    def _draw_single_text_line(self, text: str, x: int, y: int, font: ImageFont.ImageFont) -> None:
        assert self.image is not None
        if self.field_character_gap and len(text) > 1:
            current_x = x
            for char in text:
                self._draw_single_text_line(char, current_x, y, font)
                try:
                    bbox = font.getbbox(char)
                    current_x += max(1, bbox[2] - bbox[0]) + self.field_character_gap
                except Exception:
                    current_x += self.font.width + self.field_character_gap
            return
        bbox = font.getbbox(text)
        text_w = max(1, bbox[2] - bbox[0])
        text_h = max(1, bbox[3] - bbox[1])
        self._check_pixel_area(text_w + 4, text_h + 4, "text mask")
        mask = Image.new("L", (text_w + 4, text_h + 4), 0)
        temp_draw = ImageDraw.Draw(mask)
        temp_draw.text((2 - bbox[0], 2 - bbox[1]), text, fill=255, font=font)

        if self.font.width and self.font.height:
            scale_x = max(0.1, self.font.width / self.font.height)
            target_w = max(1, int(round(mask.width * scale_x)))
            if target_w != mask.width:
                self._check_pixel_area(target_w, mask.height, "text mask")
                mask = mask.resize((target_w, mask.height), Image.Resampling.BICUBIC)

        mask = rotate_mask(mask, self.font.orientation)
        if self.reverse_print:
            self._invert_masked_area(mask, x, y)
        else:
            self.image.paste("black", (x, y), mask)

    def _invert_masked_area(self, mask: Image.Image, x: int, y: int) -> None:
        assert self.image is not None
        left = max(0, x)
        top = max(0, y)
        right = min(self.image.width, x + mask.width)
        bottom = min(self.image.height, y + mask.height)
        if left >= right or top >= bottom:
            return

        region_box = (left, top, right, bottom)
        mask_box = (left - x, top - y, right - x, bottom - y)
        region = self.image.crop(region_box)
        cropped_mask = mask.crop(mask_box)
        self.image.paste(ImageChops.invert(region), region_box, cropped_mask)

    def _draw_graphic_box(self, params: str) -> None:
        assert self.draw is not None
        parts = split_csv(params)
        width = parse_int(parts[0] if parts else "") or 0
        height = parse_int(parts[1] if len(parts) > 1 else "") or 0
        thickness = parse_int(parts[2] if len(parts) > 2 else "") or 1
        color = (parts[3] if len(parts) > 3 and parts[3] else "B").upper()
        ink = "white" if color == "W" else "black"
        box = [self.current_x, self.current_y, self.current_x + width, self.current_y + height]
        if thickness >= min(width, height):
            self.draw.rectangle(box, fill=ink)
        else:
            self.draw.rectangle(box, outline=ink, width=max(1, thickness))

    def _draw_circle(self, params: str) -> None:
        assert self.draw is not None
        parts = split_csv(params)
        diameter = parse_int(parts[0] if parts else "") or 0
        thickness = parse_int(parts[1] if len(parts) > 1 else "") or 1
        color = (parts[2] if len(parts) > 2 and parts[2] else "B").upper()
        ink = "white" if color == "W" else "black"
        box = [self.current_x, self.current_y, self.current_x + diameter, self.current_y + diameter]
        self.draw.ellipse(box, outline=ink, width=max(1, thickness))

    def _draw_diagonal(self, params: str) -> None:
        assert self.draw is not None
        parts = split_csv(params)
        width = parse_int(parts[0] if parts else "") or 0
        height = parse_int(parts[1] if len(parts) > 1 else "") or 0
        thickness = parse_int(parts[2] if len(parts) > 2 else "") or 1
        orientation = (parts[4] if len(parts) > 4 and parts[4] else "R").upper()
        end = (self.current_x + width, self.current_y + height) if orientation == "R" else (self.current_x, self.current_y + height)
        start = (self.current_x, self.current_y) if orientation == "R" else (self.current_x + width, self.current_y)
        self.draw.line([start, end], fill="black", width=max(1, thickness))

    def _draw_ellipse(self, params: str) -> None:
        assert self.draw is not None
        parts = split_csv(params)
        width = parse_int(parts[0] if parts else "") or 0
        height = parse_int(parts[1] if len(parts) > 1 else "") or 0
        thickness = parse_int(parts[2] if len(parts) > 2 else "") or 1
        color = (parts[3] if len(parts) > 3 and parts[3] else "B").upper()
        ink = "white" if color == "W" else "black"
        if width <= 0 or height <= 0:
            return
        box = [self.current_x, self.current_y, self.current_x + width, self.current_y + height]
        if thickness >= min(width, height):
            self.draw.ellipse(box, fill=ink)
        else:
            self.draw.ellipse(box, outline=ink, width=max(1, thickness))

    def _draw_graphic_symbol(self, params: str) -> None:
        parts = split_csv(params)
        symbol_id = (parts[0] if parts and parts[0] else "A").upper()
        symbol = GRAPHIC_SYMBOLS.get(symbol_id)
        if symbol is None:
            self.warn(f"Graphic symbol not supported: {symbol_id}")
            return
        height = parse_int(parts[1] if len(parts) > 1 else "") or self.font.height
        width = parse_int(parts[2] if len(parts) > 2 else "") or height
        previous = self.font
        self.font = FontSpec(name=previous.name, orientation=previous.orientation, height=height, width=width)
        self._draw_text(symbol)
        self.font = previous

    def _download_graphic(self, params: str) -> None:
        parts = split_csv(params, maxsplit=3)
        if len(parts) < 4:
            self.warn(f"Skipping malformed ~DG graphic: {params[:60]}")
            return
        name = normalize_graphic_name(parts[0])
        total = parse_int(parts[1]) or 0
        bytes_per_row = parse_int(parts[2]) or 0
        try:
            data = decode_graphic_payload(
                parts[3],
                total,
                bytes_per_row,
                self.options.strict_graphic_crc,
                self.options.max_graphic_bytes,
            )
        except ValueError as exc:
            self.warn(f"Could not decode graphic {name}: {exc}")
            return
        graphic = Graphic(name=name, total_bytes=total, bytes_per_row=bytes_per_row, data=data)
        self._store_graphic(graphic)
        self.report.downloaded_graphics += 1

    def _download_object(self, params: str) -> None:
        parts = split_csv(params, maxsplit=6)
        if len(parts) < 2:
            self.warn(f"Skipping malformed ~DY object: {params[:60]}")
            return
        name = normalize_graphic_name(parts[0])
        total, bytes_per_row, payload = parse_download_object_parts(parts)
        if total is not None and bytes_per_row is not None and payload is not None:
            try:
                data = decode_graphic_payload(
                    payload,
                    total,
                    bytes_per_row,
                    self.options.strict_graphic_crc,
                    self.options.max_graphic_bytes,
                )
            except ValueError as exc:
                self.warn(f"Could not decode object {name}: {exc}")
                return
            self._store_graphic(Graphic(name=name, total_bytes=total, bytes_per_row=bytes_per_row, data=data))
            self.report.downloaded_graphics += 1
            return

        payload = parts[-1]
        data = payload.encode("latin-1", errors="replace")
        self._store_object(name, data)

    def _store_graphic(self, graphic: Graphic) -> None:
        keys = {graphic.name}
        if ":" in graphic.name:
            keys.add(graphic.name.split(":", 1)[1])
        for key in keys:
            self.graphics[key] = graphic

    def _store_object(self, name: str, data: bytes) -> None:
        keys = {name}
        if ":" in name:
            keys.add(name.split(":", 1)[1])
        for key in keys:
            self.objects[key] = data

    def _delete_graphic(self, params: str) -> None:
        name = normalize_graphic_name(first_csv(params))
        keys = {name}
        if ":" in name:
            keys.add(name.split(":", 1)[1])
        for key in keys:
            self.graphics.pop(key, None)
            self.saved_images.pop(key, None)
            self.objects.pop(key, None)

    def _erase_graphics(self) -> None:
        self.graphics.clear()
        self.saved_images.clear()

    def _move_stored_image(self, params: str) -> None:
        parts = split_csv(params)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            self.warn(f"Skipping malformed ^IM image move: {params[:60]}")
            return
        source = normalize_graphic_name(parts[0])
        target = normalize_graphic_name(parts[1])
        source_keys = [source, source.split(":", 1)[-1]]
        for key in source_keys:
            if key in self.graphics:
                self._store_graphic(
                    Graphic(target, self.graphics[key].total_bytes, self.graphics[key].bytes_per_row, self.graphics[key].data)
                )
                return
            if key in self.saved_images:
                self._store_saved_image(target, self.saved_images[key].copy())
                return
            if key in self.objects:
                self._store_object(target, self.objects[key])
                return
        self.warn(f"Stored image not found for ^IM: {source}")

    def _save_current_image(self, params: str) -> None:
        assert self.image is not None
        name = normalize_graphic_name(first_csv(params))
        if not name:
            self.warn("Skipping ^IS with no image name")
            return
        self._store_saved_image(name, self.image.copy())

    def _store_saved_image(self, name: str, image: Image.Image) -> None:
        keys = {name}
        if ":" in name:
            keys.add(name.split(":", 1)[1])
        for key in keys:
            self.saved_images[key] = image

    def _transfer_object(self, params: str) -> None:
        parts = split_csv(params)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            self.warn(f"Skipping malformed ^TO transfer: {params[:60]}")
            return
        source = normalize_graphic_name(parts[0])
        target = normalize_graphic_name(parts[1])
        remove_source = len(parts) > 2 and parts[2].upper().startswith("M")
        if self._copy_virtual_object(source, target, remove_source):
            return
        self.warn(f"Transfer source not found for ^TO: {source}")

    def _copy_virtual_object(self, source: str, target: str, remove_source: bool = False) -> bool:
        source_keys = [source, source.split(":", 1)[-1]]
        for key in source_keys:
            if key in self.graphics:
                graphic = self.graphics[key]
                self._store_graphic(Graphic(target, graphic.total_bytes, graphic.bytes_per_row, graphic.data))
                if remove_source:
                    self._delete_by_alias(self.graphics, source)
                return True
            if key in self.saved_images:
                self._store_saved_image(target, self.saved_images[key].copy())
                if remove_source:
                    self._delete_by_alias(self.saved_images, source)
                return True
            if key in self.objects:
                self._store_object(target, self.objects[key])
                if remove_source:
                    self._delete_by_alias(self.objects, source)
                return True
            if key in self.formats:
                self._store_format(target, list(self.formats[key]))
                if remove_source:
                    self._delete_by_alias(self.formats, source)
                return True
        return False

    def _delete_by_alias(self, store: dict, name: str) -> None:
        keys = {name}
        if ":" in name:
            keys.add(name.split(":", 1)[1])
        for key in keys:
            store.pop(key, None)

    def _initialize_flash_memory(self) -> None:
        flash_prefixes = ("E:", "B:")
        for store in (self.graphics, self.saved_images, self.objects, self.formats):
            keys_to_remove: set[str] = set()
            for key in list(store):
                if key.startswith(flash_prefixes):
                    keys_to_remove.add(key)
                    keys_to_remove.add(key.split(":", 1)[1])
            for key in keys_to_remove:
                store.pop(key, None)

    def _start_format_download(self, params: str) -> None:
        name = normalize_graphic_name(first_csv(params))
        if not name:
            self.warn("Skipping ^DF with no format name")
            return
        self.storing_format_name = name
        self.stored_format_commands = []

    def _finish_format_download(self) -> None:
        if not self.storing_format_name:
            return
        self._store_format(self.storing_format_name, list(self.stored_format_commands))
        self.storing_format_name = None
        self.stored_format_commands = []

    def _store_format(self, name: str, commands: list[ZPLCommand]) -> None:
        keys = {name}
        if ":" in name:
            keys.add(name.split(":", 1)[1])
        for key in keys:
            self.formats[key] = commands

    def _recall_format(self, params: str) -> None:
        name = normalize_graphic_name(first_csv(params))
        commands = self.formats.get(name) or self.formats.get(name.split(":", 1)[-1])
        if commands is None:
            self.warn(f"Stored format not found: {name}")
            return
        for stored_command in commands:
            self._handle_command(stored_command)

    def _load_stored_image(self, params: str) -> None:
        name = normalize_graphic_name(first_csv(params))
        if not name:
            return
        image = self.saved_images.get(name) or self.saved_images.get(name.split(":", 1)[-1])
        if image is not None:
            assert self.image is not None
            self.image.paste(image, (self.current_x, self.current_y))
            self.report.rendered_graphics += 1
            return

        graphic = self.graphics.get(name) or self.graphics.get(name.split(":", 1)[-1])
        if graphic is None:
            self.warn(f"Stored image not found: {name}")
            return
        self._paste_graphic(graphic_to_image(graphic), 1, 1)
        self.report.rendered_graphics += 1

    def _draw_downloaded_graphic(self, params: str) -> None:
        parts = split_csv(params)
        if not parts:
            return
        name = normalize_graphic_name(parts[0])
        xmul = parse_int(parts[1] if len(parts) > 1 else "") or 1
        ymul = parse_int(parts[2] if len(parts) > 2 else "") or 1
        graphic = self.graphics.get(name) or self.graphics.get(name.split(":", 1)[-1])
        if not graphic:
            self.warn(f"Graphic not found: {name}")
            return
        self._paste_graphic(graphic_to_image(graphic), xmul, ymul)
        self.report.rendered_graphics += 1

    def _draw_embedded_graphic(self, params: str) -> None:
        parts = split_csv(params, maxsplit=4)
        if len(parts) < 5:
            self.warn(f"Skipping malformed ^GF graphic: {params[:60]}")
            return
        compression = parts[0].upper()[:1]
        total = parse_int(parts[1]) or parse_int(parts[2]) or 0
        bytes_per_row = parse_int(parts[3]) or 0
        payload = parts[4]
        try:
            if compression == "B":
                check_graphic_size(total, self.options.max_graphic_bytes)
                data = payload.encode("latin-1")[:total]
            else:
                data = decode_graphic_payload(
                    payload,
                    total,
                    bytes_per_row,
                    self.options.strict_graphic_crc,
                    self.options.max_graphic_bytes,
                )
        except ValueError as exc:
            self.warn(f"Could not decode ^GF graphic: {exc}")
            return
        self._paste_graphic(graphic_to_image(Graphic("FIELD", total, bytes_per_row, data)), 1, 1)
        self.report.rendered_graphics += 1

    def _paste_graphic(self, graphic_image: Image.Image, xmul: int, ymul: int) -> None:
        assert self.image is not None
        xmul = max(1, xmul)
        ymul = max(1, ymul)
        if xmul != 1 or ymul != 1:
            self._check_pixel_area(graphic_image.width * xmul, graphic_image.height * ymul, "graphic")
            graphic_image = graphic_image.resize(
                (graphic_image.width * xmul, graphic_image.height * ymul),
                Image.Resampling.NEAREST,
            )
        else:
            self._check_pixel_area(graphic_image.width, graphic_image.height, "graphic")
        mask = Image.eval(graphic_image.convert("L"), lambda p: 255 if p < 128 else 0)
        self.image.paste("black", (self.current_x, self.current_y), mask)

    def _draw_barcode(self, barcode: BarcodeSpec, data: str) -> None:
        if barcode.kind == "B0":
            self._draw_unsupported_barcode("^B0", "Aztec", data)
        elif barcode.kind == "B1":
            self._draw_code11(data, barcode.params)
        elif barcode.kind == "B2":
            self._draw_interleaved_2_of_5(data, barcode.params)
        elif barcode.kind == "B4":
            self._draw_unsupported_barcode("^B4", "Code 49", data)
        elif barcode.kind == "B5":
            self._draw_planet(data, barcode.params)
        elif barcode.kind == "B7":
            self._draw_unsupported_barcode("^B7", "PDF417", data)
        elif barcode.kind == "B8":
            self._draw_ean8(data, barcode.params)
        elif barcode.kind == "B9":
            self._draw_upce(data, barcode.params)
        elif barcode.kind == "BA":
            self._draw_code93(data, barcode.params)
        elif barcode.kind == "BB":
            self._draw_unsupported_barcode("^BB", "CODABLOCK", data)
        elif barcode.kind == "BD":
            self._draw_unsupported_barcode("^BD", "UPS MaxiCode", data)
        elif barcode.kind == "BE":
            self._draw_ean13(data, barcode.params)
        elif barcode.kind == "BF":
            self._draw_unsupported_barcode("^BF", "MicroPDF417", data)
        elif barcode.kind == "BI":
            self._draw_2_of_5(data, barcode.params, "Industrial 2 of 5", "industrial")
        elif barcode.kind == "BJ":
            self._draw_2_of_5(data, barcode.params, "Standard 2 of 5", "standard")
        elif barcode.kind == "BK":
            self._draw_codabar(data, barcode.params)
        elif barcode.kind == "BL":
            self._draw_logmars(data, barcode.params)
        elif barcode.kind == "BM":
            self._draw_msi(data, barcode.params)
        elif barcode.kind == "BO":
            self._draw_unsupported_barcode("^BO", "Aztec", data)
        elif barcode.kind == "BP":
            self._draw_plessey(data, barcode.params)
        elif barcode.kind == "BR":
            self._draw_unsupported_barcode("^BR", "GS1 DataBar", data)
        elif barcode.kind == "BS":
            self._draw_upc_ean_extension(data, barcode.params)
        elif barcode.kind == "BT":
            self._draw_unsupported_barcode("^BT", "TLC39", data)
        elif barcode.kind == "BU":
            self._draw_upca(data, barcode.params)
        elif barcode.kind == "BZ":
            self._draw_postal(data, barcode.params)
        elif barcode.kind == "BX":
            self._draw_datamatrix(data, barcode.params)
        elif barcode.kind == "BQ":
            self._draw_qrcode(data, barcode.params)
        elif barcode.kind == "B3":
            self._draw_code39(data, barcode.params)
        elif barcode.kind == "BC":
            self._draw_code128(data, barcode.params)

    def _draw_unsupported_barcode(self, command_name: str, barcode_name: str, data: str) -> None:
        self.warn(f"{command_name} {barcode_name} barcode rendering is not implemented; drew fallback text")
        self._draw_fallback_barcode_text(f"{command_name}:{data}")

    def _draw_code11(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[2] if len(params) > 2 else "") or self.by_height
        text = data.strip().upper()
        if not text or any(char not in CODE11_PATTERNS for char in text):
            self.warn(f"Code 11 fallback for unsupported data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        start_stop = "1011001"
        bits = start_stop + "0" + "0".join(CODE11_PATTERNS[char] for char in text) + "0" + start_stop
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_interleaved_2_of_5(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit():
            self.warn(f"Interleaved 2 of 5 fallback for non-numeric data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) % 2:
            self.warn(f"Interleaved 2 of 5 padded odd-length data with a leading zero: {data!r}")
            digits = "0" + digits

        module = max(1, self.by_module_width)
        wide = max(module + 1, int(round(module * self.by_wide_ratio)))

        def width_for(marker: str) -> int:
            return wide if marker == "w" else module

        widths: list[tuple[bool, int]] = [(True, module), (False, module), (True, module), (False, module)]
        for i in range(0, len(digits), 2):
            bar_pattern = I25_PATTERNS[digits[i]]
            space_pattern = I25_PATTERNS[digits[i + 1]]
            for bar_marker, space_marker in zip(bar_pattern, space_pattern):
                widths.append((True, width_for(bar_marker)))
                widths.append((False, width_for(space_marker)))
        widths.extend([(True, wide), (False, module), (True, module)])
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_planet(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit():
            self.warn(f"PLANET fallback for non-numeric data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        bars = [True]
        for digit in digits:
            bars.extend(bit == "0" for bit in POSTNET_PATTERNS[digit])
        bars.append(True)
        self._paste_postal_bars(bars, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_ean8(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit() or len(digits) not in {7, 8}:
            self.warn(f"EAN-8 fallback for invalid data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) == 7:
            digits += mod10_check_digit(digits)
        elif mod10_check_digit(digits[:-1]) != digits[-1]:
            self.warn(f"EAN-8 fallback for check digit mismatch: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        bits = (
            "101"
            + "".join(EAN_L_PATTERNS[digit] for digit in digits[:4])
            + "01010"
            + "".join(EAN_R_PATTERNS[digit] for digit in digits[4:])
            + "101"
        )
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_upce(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        parsed = normalize_upce(data.strip())
        if not parsed:
            self.warn(f"UPC-E fallback for invalid data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        number_system, core, check_digit = parsed
        parity = UPCE_PARITY[number_system][check_digit]
        bits = "101"
        for digit, marker in zip(core, parity):
            bits += EAN_G_PATTERNS[digit] if marker == "E" else EAN_L_PATTERNS[digit]
        bits += "010101"
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_code93(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        text = data.strip().upper()
        normal_chars = set(CODE93_CHARSET[:43])
        if not text or any(char not in normal_chars for char in text):
            self.warn(f"Code 93 fallback for unsupported data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        encoded = list(text)
        check_c = code93_check_char(encoded, 20)
        check_k = code93_check_char(encoded + [check_c], 15)
        symbols = ["*"] + encoded + [check_c, check_k, "*"]
        bits = "".join(CODE93_PATTERNS[symbol] for symbol in symbols) + "1"
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_ean13(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit() or len(digits) not in {12, 13}:
            self.warn(f"EAN-13 fallback for invalid data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) == 12:
            digits += mod10_check_digit(digits)
        elif mod10_check_digit(digits[:-1]) != digits[-1]:
            self.warn(f"EAN-13 fallback for check digit mismatch: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        parity = EAN13_PARITY[digits[0]]
        left = "".join(
            EAN_L_PATTERNS[digit] if marker == "L" else EAN_G_PATTERNS[digit]
            for digit, marker in zip(digits[1:7], parity)
        )
        bits = "101" + left + "01010" + "".join(EAN_R_PATTERNS[digit] for digit in digits[7:]) + "101"
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_2_of_5(self, data: str, params: list[str], barcode_name: str, variant: str) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit():
            self.warn(f"{barcode_name} fallback for non-numeric data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        module = max(1, self.by_module_width)
        wide = max(module + 1, int(round(module * self.by_wide_ratio)))
        inter_bar_gap = module if variant == "industrial" else max(1, module // 2)

        widths: list[tuple[bool, int]] = [(True, module), (False, module), (True, module), (False, module)]
        for digit in digits:
            for marker in I25_PATTERNS[digit]:
                widths.append((True, wide if marker == "w" else module))
                widths.append((False, inter_bar_gap))
            widths.append((False, module))
        widths.extend([(True, wide), (False, module), (True, module)])
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_codabar(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[2] if len(params) > 2 else "") or parse_int(params[1] if len(params) > 1 else "") or self.by_height
        text = data.strip().upper()
        if not text:
            self.warn(f"Codabar fallback for empty data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if text[0] not in "ABCD":
            text = "A" + text
        if text[-1] not in "ABCD":
            text = text + "B"
        if any(char not in CODABAR_PATTERNS for char in text):
            self.warn(f"Codabar fallback for unsupported data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        module = max(1, self.by_module_width)
        wide = max(module + 1, int(round(module * self.by_wide_ratio)))
        widths: list[tuple[bool, int]] = []
        for char_index, char in enumerate(text):
            for i, marker in enumerate(CODABAR_PATTERNS[char]):
                widths.append((i % 2 == 0, wide if marker == "w" else module))
            if char_index != len(text) - 1:
                widths.append((False, module))
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_logmars(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or parse_int(params[2] if len(params) > 2 else "") or self.by_height
        module = max(1, self.by_module_width)
        wide = max(module + 1, int(round(module * self.by_wide_ratio)))
        encoded = f"*{data.strip().upper()}*"
        if any(ch not in CODE39_PATTERNS for ch in encoded):
            self.warn(f"LOGMARS fallback for unsupported data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        widths: list[tuple[bool, int]] = []
        for char_index, char in enumerate(encoded):
            for i, marker in enumerate(CODE39_PATTERNS[char]):
                widths.append((i % 2 == 0, wide if marker == "w" else module))
            if char_index != len(encoded) - 1:
                widths.append((False, module))
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_msi(self, data: str, params: list[str]) -> None:
        self._draw_msi_like(data, params, "MSI")

    def _draw_plessey(self, data: str, params: list[str]) -> None:
        self._draw_msi_like(data, params, "Plessey")

    def _draw_msi_like(self, data: str, params: list[str], barcode_name: str) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[2] if len(params) > 2 else "") or parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit():
            self.warn(f"{barcode_name} fallback for non-numeric data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        bits = "110" + "".join(MSI_PATTERNS[digit] for digit in digits) + "1001"
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_upc_ean_extension(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit() or len(digits) not in {2, 5}:
            self.warn(f"UPC/EAN extension fallback for invalid data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) == 2:
            parity = EAN2_PARITY[str(int(digits) % 4)]
        else:
            checksum = extension5_checksum(digits)
            parity = EAN5_PARITY[str(checksum)]

        bits = "1011"
        for index, (digit, marker) in enumerate(zip(digits, parity)):
            if index:
                bits += "01"
            bits += EAN_L_PATTERNS[digit] if marker == "L" else EAN_G_PATTERNS[digit]
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_upca(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit() or len(digits) not in {11, 12}:
            self.warn(f"UPC-A fallback for invalid data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) == 11:
            digits += mod10_check_digit(digits)
        elif mod10_check_digit(digits[:-1]) != digits[-1]:
            self.warn(f"UPC-A fallback for check digit mismatch: {data!r}")
            self._draw_fallback_barcode_text(data)
            return

        bits = (
            "101"
            + "".join(EAN_L_PATTERNS[digit] for digit in digits[:6])
            + "01010"
            + "".join(EAN_R_PATTERNS[digit] for digit in digits[6:])
            + "101"
        )
        self._paste_binary_barcode(bits, height, orientation, max(1, self.by_module_width))
        self.report.rendered_barcodes += 1

    def _draw_postal(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        digits = data.strip()
        if not digits.isdigit():
            self.warn(f"Postal barcode fallback for non-numeric data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        if len(digits) in {5, 9, 11}:
            digits += postal_check_digit(digits)

        bars = [True]
        for digit in digits:
            bars.extend(bit == "1" for bit in POSTNET_PATTERNS[digit])
        bars.append(True)
        self._paste_postal_bars(bars, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_datamatrix(self, data: str, params: list[str]) -> None:
        module_size = parse_int(params[1] if len(params) > 1 else "") or max(2, self.by_module_width)
        requested_cols = parse_int(params[3] if len(params) > 3 else "")
        requested_rows = parse_int(params[4] if len(params) > 4 else "")
        orientation = parse_orientation(params[0] if params else "")
        try:
            matrix = datamatrix_matrix(data, requested_rows, requested_cols)
        except ValueError as exc:
            self.warn(f"DataMatrix fallback for {data!r}: {exc}")
            self._draw_fallback_barcode_text(f"DM:{data}")
            return
        self._paste_bit_matrix(matrix, module_size, orientation)
        self.report.rendered_barcodes += 1

    def _draw_qrcode(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        module_size = parse_int(params[2] if len(params) > 2 else "") or max(2, self.by_module_width)
        if len(data) >= 3 and data[1] == ",":
            data = data[3:]
        try:
            import qrcode  # type: ignore
        except ImportError:
            self.warn("QR rendering requires optional package: python -m pip install qrcode[pil]")
            self._draw_fallback_barcode_text(f"QR:{data}")
            return
        qr = qrcode.QRCode(box_size=module_size, border=1)
        qr.add_data(data)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
        qr_img = rotate_mask(Image.eval(qr_img, lambda p: 255 if p < 128 else 0), orientation)
        assert self.image is not None
        self.image.paste("black", (self.current_x, self.current_y), qr_img)
        self.report.rendered_barcodes += 1

    def _draw_code39(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[2] if len(params) > 2 else "") or self.by_height
        module = max(1, self.by_module_width)
        wide = max(module + 1, int(round(module * self.by_wide_ratio)))
        encoded = f"*{data.upper()}*"
        if any(ch not in CODE39_PATTERNS for ch in encoded):
            self.warn(f"Code 39 fallback for unsupported data: {data!r}")
            self._draw_fallback_barcode_text(data)
            return
        widths: list[tuple[bool, int]] = []
        for char_index, char in enumerate(encoded):
            pattern = CODE39_PATTERNS[char]
            for i, marker in enumerate(pattern):
                widths.append((i % 2 == 0, wide if marker == "w" else module))
            if char_index != len(encoded) - 1:
                widths.append((False, module))
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _draw_code128(self, data: str, params: list[str]) -> None:
        orientation = parse_orientation(params[0] if params else "")
        height = parse_int(params[1] if len(params) > 1 else "") or self.by_height
        try:
            codes = encode_code128_b(data)
        except ValueError as exc:
            self.warn(f"Code 128 fallback for {data!r}: {exc}")
            self._draw_fallback_barcode_text(data)
            return
        widths: list[tuple[bool, int]] = []
        for code in codes:
            pattern = CODE128_PATTERNS[code]
            for i, width_char in enumerate(pattern):
                widths.append((i % 2 == 0, int(width_char) * self.by_module_width))
        self._paste_1d_barcode(widths, height, orientation)
        self.report.rendered_barcodes += 1

    def _paste_binary_barcode(self, bits: str, height: int, orientation: str, module_width: int = 1) -> None:
        module_width = max(1, module_width)
        widths: list[tuple[bool, int]] = []
        if not bits:
            return

        current = bits[0]
        count = 0
        for bit in bits:
            if bit == current:
                count += 1
            else:
                widths.append((current == "1", count * module_width))
                current = bit
                count = 1
        widths.append((current == "1", count * module_width))
        self._paste_1d_barcode(widths, height, orientation)

    def _paste_postal_bars(self, bars: list[bool], height: int, orientation: str) -> None:
        bar_width = max(1, self.by_module_width)
        gap = max(1, bar_width)
        tall_height = max(1, height)
        short_height = max(1, int(round(height * 0.45)))
        width = max(1, len(bars) * bar_width + max(0, len(bars) - 1) * gap)
        self._check_pixel_area(width, tall_height, "barcode")
        mask = Image.new("L", (width, tall_height), 0)
        draw = ImageDraw.Draw(mask)
        x = 0
        for is_tall in bars:
            bar_height = tall_height if is_tall else short_height
            y = tall_height - bar_height
            draw.rectangle([x, y, x + bar_width - 1, tall_height - 1], fill=255)
            x += bar_width + gap
        mask = rotate_mask(mask, orientation)
        assert self.image is not None
        self.image.paste("black", (self.current_x, self.current_y), mask)

    def _paste_1d_barcode(self, widths: list[tuple[bool, int]], height: int, orientation: str) -> None:
        total_width = sum(width for _is_bar, width in widths)
        self._check_pixel_area(max(1, total_width), max(1, height), "barcode")
        mask = Image.new("L", (max(1, total_width), max(1, height)), 0)
        draw = ImageDraw.Draw(mask)
        x = 0
        for is_bar, width in widths:
            if is_bar:
                draw.rectangle([x, 0, x + width - 1, height], fill=255)
            x += width
        mask = rotate_mask(mask, orientation)
        assert self.image is not None
        self.image.paste("black", (self.current_x, self.current_y), mask)

    def _paste_bit_matrix(self, matrix: list[list[int]], module_size: int, orientation: str) -> None:
        rows = len(matrix)
        cols = len(matrix[0]) if rows else 0
        self._check_pixel_area(max(1, cols * module_size), max(1, rows * module_size), "barcode")
        mask = Image.new("L", (cols, rows), 0)
        pixels = mask.load()
        for y, row in enumerate(matrix):
            for x, value in enumerate(row):
                if value:
                    pixels[x, y] = 255
        if module_size != 1:
            mask = mask.resize((cols * module_size, rows * module_size), Image.Resampling.NEAREST)
        mask = rotate_mask(mask, orientation)
        assert self.image is not None
        self.image.paste("black", (self.current_x, self.current_y), mask)

    def _draw_fallback_barcode_text(self, text: str) -> None:
        previous = self.font
        self.font = FontSpec(height=max(12, previous.height), width=max(12, previous.width))
        self._draw_text(text)
        self.font = previous

    def warn(self, message: str) -> None:
        self.report.warnings.append(message)
        if self.options.verbose:
            print(f"warning: {message}")


def split_csv(value: str, maxsplit: int = -1) -> list[str]:
    return [part.strip() for part in value.split(",", maxsplit)]


def first_csv(value: str) -> str:
    return split_csv(value, maxsplit=1)[0] if value else ""


def parse_download_object_parts(parts: list[str]) -> tuple[int | None, int | None, str | None]:
    for total_index, row_index in ((1, 2), (2, 3), (3, 4)):
        if len(parts) <= row_index + 1:
            continue
        total = parse_int(parts[total_index])
        bytes_per_row = parse_int(parts[row_index])
        if total is not None and bytes_per_row is not None:
            return total, bytes_per_row, parts[-1]
    return None, None, None


def parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_orientation(value: str) -> str:
    return value[:1].upper() if value[:1].upper() in ORIENTATIONS else "N"


def mod10_check_digit(digits: str) -> str:
    total = 0
    for index, digit in enumerate(reversed(digits)):
        total += int(digit) * (3 if index % 2 == 0 else 1)
    return str((10 - (total % 10)) % 10)


def postal_check_digit(digits: str) -> str:
    return str((10 - (sum(int(digit) for digit in digits) % 10)) % 10)


def extension5_checksum(digits: str) -> int:
    total = 0
    for index, digit in enumerate(digits):
        total += int(digit) * (3 if index % 2 == 0 else 9)
    return total % 10


def apply_serialization_mask(mask: str | None, value: str) -> str:
    if not mask or "#" not in mask:
        return value
    value_chars = list(value)
    output = list(mask)
    for index in range(len(output) - 1, -1, -1):
        if output[index] == "#":
            output[index] = value_chars.pop() if value_chars else "0"
    prefix = "".join(value_chars)
    return prefix + "".join(output)


def normalize_upce(value: str) -> tuple[str, str, str] | None:
    if not value.isdigit() or len(value) not in {6, 7, 8}:
        return None
    if len(value) == 6:
        number_system = "0"
        core = value
        check_digit = mod10_check_digit(upce_to_upca_base(number_system, core))
    elif len(value) == 7:
        number_system = value[0]
        core = value[1:]
        if number_system not in UPCE_PARITY:
            return None
        check_digit = mod10_check_digit(upce_to_upca_base(number_system, core))
    else:
        number_system = value[0]
        core = value[1:7]
        check_digit = value[7]
        if number_system not in UPCE_PARITY:
            return None
        if mod10_check_digit(upce_to_upca_base(number_system, core)) != check_digit:
            return None
    return number_system, core, check_digit


def upce_to_upca_base(number_system: str, core: str) -> str:
    if core[-1] in "012":
        return number_system + core[0:2] + core[-1] + "0000" + core[2:5]
    if core[-1] == "3":
        return number_system + core[0:3] + "00000" + core[3:5]
    if core[-1] == "4":
        return number_system + core[0:4] + "00000" + core[4]
    return number_system + core[0:5] + "0000" + core[-1]


def code93_check_char(symbols: list[str], max_weight: int) -> str:
    total = 0
    weight = 1
    for symbol in reversed(symbols):
        total += CODE93_CHARSET.index(symbol) * weight
        weight = 1 if weight == max_weight else weight + 1
    return CODE93_CHARSET[total % 47]


def is_hex(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]+", value))


def normalize_graphic_name(value: str) -> str:
    return value.strip().upper()


def load_font(size: int) -> ImageFont.ImageFont:
    """Legacy function for backward compatibility. Use font_loader.load_font() instead."""
    return load_mapped_font(size)


def rotate_mask(mask: Image.Image, orientation: str) -> Image.Image:
    orientation = parse_orientation(orientation)
    if orientation == "R":
        return mask.rotate(-90, expand=True)
    if orientation == "I":
        return mask.rotate(180, expand=True)
    if orientation == "B":
        return mask.rotate(90, expand=True)
    return mask


def decode_graphic_payload(
    payload: str,
    total_bytes: int,
    bytes_per_row: int,
    strict_crc: bool = False,
    max_bytes: int | None = DEFAULT_MAX_GRAPHIC_BYTES,
) -> bytes:
    check_graphic_size(total_bytes, max_bytes)
    compact = "".join(payload.split())
    compressed_match = GRAPHIC_PAYLOAD_PATTERN.search(compact)
    if compressed_match:
        format_name, encoded_data, crc = compressed_match.groups()
        if crc:
            actual_crc = crc16_ccitt_hex(encoded_data.encode("ascii"))
            if strict_crc and actual_crc.upper() != crc.upper():
                raise ValueError(f"{format_name.upper()} CRC mismatch: expected {crc.upper()}, got {actual_crc}")
        try:
            data = base64.b64decode(encoded_data)
            if format_name.upper() == "Z64":
                data = zlib.decompress(data)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
    else:
        data = decode_ascii_hex_graphic(compact, bytes_per_row)

    check_graphic_size(len(data), max_bytes)
    if total_bytes and len(data) < total_bytes:
        data = data + b"\x00" * (total_bytes - len(data))
    if total_bytes and len(data) > total_bytes:
        data = data[:total_bytes]
    return data


def check_graphic_size(size: int, max_bytes: int | None = DEFAULT_MAX_GRAPHIC_BYTES) -> None:
    if size < 0:
        raise ValueError(f"graphic data size must be non-negative, got {size}")
    if max_bytes is not None and max_bytes > 0 and size > max_bytes:
        raise ValueError(f"graphic data size {size} bytes exceeds max graphic bytes {max_bytes}")


def crc16_ccitt_hex(data: bytes, poly: int = 0x8408) -> str:
    """Return Zebra-compatible CRC-16-CCITT for B64/Z64 graphic payloads."""
    crc = 0xFFFF
    for byte in data:
        current = byte & 0xFF
        for _ in range(8):
            if (crc & 0x0001) ^ (current & 0x0001):
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
            current >>= 1
    crc = ~crc & 0xFFFF
    crc = (crc << 8) | ((crc >> 8) & 0xFF)
    return f"{crc & 0xFFFF:04X}"


def decode_ascii_hex_graphic(value: str, bytes_per_row: int) -> bytes:
    expanded = expand_zpl_ascii_hex(value, bytes_per_row)
    hex_only = "".join(ch for ch in expanded if ch in "0123456789abcdefABCDEF")
    if len(hex_only) % 2:
        hex_only += "0"
    try:
        return bytes.fromhex(hex_only)
    except ValueError as exc:
        raise ValueError("invalid ASCII-hex graphic data") from exc


def expand_zpl_ascii_hex(value: str, bytes_per_row: int) -> str:
    repeat_counts = {
        **{chr(ord("G") + i): i + 1 for i in range(19)},
        **{chr(ord("g") + i): (i + 1) * 20 for i in range(20)},
    }
    row_nibbles = bytes_per_row * 2 if bytes_per_row else 0
    rows: list[str] = []
    current = ""
    previous = ""
    repeat = 0

    def finish_row(row: str) -> str:
        if row_nibbles and len(row) < row_nibbles:
            row = row + ("0" * (row_nibbles - len(row)))
        return row

    for ch in value:
        if ch in repeat_counts:
            repeat += repeat_counts[ch]
            continue
        if ch == ",":
            current = finish_row(current)
        elif ch == "!":
            fill = "F" * max(0, row_nibbles - len(current))
            current += fill
        elif ch == ":":
            if previous:
                rows.append(previous)
            current = ""
            repeat = 0
            continue
        elif ch in "0123456789abcdefABCDEF":
            current += ch * (repeat or 1)
        else:
            continue
        repeat = 0
        if row_nibbles and len(current) >= row_nibbles:
            previous = current[:row_nibbles]
            rows.append(previous)
            current = current[row_nibbles:]
    if current:
        rows.append(finish_row(current))
    return "".join(rows)


def graphic_to_image(graphic: Graphic) -> Image.Image:
    width = max(1, graphic.width)
    height = max(1, graphic.height)
    image = Image.new("L", (width, height), 255)
    pixels = image.load()
    index = 0
    for y in range(height):
        for byte_index in range(graphic.bytes_per_row):
            if index >= len(graphic.data):
                return image
            byte = graphic.data[index]
            index += 1
            for bit in range(8):
                x = byte_index * 8 + bit
                if x < width and (byte & (0x80 >> bit)):
                    pixels[x, y] = 0
    return image


def crop_to_content(image: Image.Image) -> Image.Image:
    background = Image.new(image.mode, image.size, "white")
    diff = ImageChops.difference(image, background)
    bbox = diff.getbbox()
    return image.crop(bbox) if bbox else image


def datamatrix_matrix(data: str, requested_rows: int | None = None, requested_cols: int | None = None) -> list[list[int]]:
    data_codewords = datamatrix_ascii_encode(data)
    symbol = choose_datamatrix_symbol(len(data_codewords), requested_rows, requested_cols)
    padded = pad_datamatrix_codewords(data_codewords, symbol.data_codewords)
    full_codewords = padded + datamatrix_error_codewords(padded, symbol.error_codewords)
    placed = place_datamatrix_modules(full_codewords, symbol.data_rows, symbol.data_cols)
    return add_datamatrix_finder(placed, symbol)


def datamatrix_ascii_encode(data: str) -> list[int]:
    raw = data.encode("iso-8859-1", errors="replace")
    codewords: list[int] = []
    i = 0
    while i < len(raw):
        if i + 1 < len(raw) and 48 <= raw[i] <= 57 and 48 <= raw[i + 1] <= 57:
            codewords.append(130 + int(chr(raw[i]) + chr(raw[i + 1])))
            i += 2
        elif raw[i] > 127:
            codewords.extend([235, raw[i] - 127])
            i += 1
        else:
            codewords.append(raw[i] + 1)
            i += 1
    return codewords


def choose_datamatrix_symbol(data_count: int, requested_rows: int | None, requested_cols: int | None) -> DataMatrixSymbol:
    if requested_rows and requested_cols:
        requested = [
            symbol
            for symbol in DM_SYMBOLS
            if symbol.rows == requested_rows and symbol.cols == requested_cols and data_count <= symbol.data_codewords
        ]
        if requested:
            return requested[0]
    for symbol in DM_SYMBOLS:
        if data_count <= symbol.data_codewords:
            return symbol
    raise ValueError("data is too large for built-in ECC200 DataMatrix renderer")


def pad_datamatrix_codewords(codewords: list[int], capacity: int) -> list[int]:
    if len(codewords) > capacity:
        raise ValueError("encoded data exceeds symbol capacity")
    result = list(codewords)
    if len(result) < capacity:
        result.append(129)
    while len(result) < capacity:
        position = len(result) + 1
        pseudo_random = ((149 * position) % 253) + 1
        pad = 129 + pseudo_random
        if pad > 254:
            pad -= 254
        result.append(pad)
    return result


def gf_tables() -> tuple[list[int], list[int]]:
    exp = [0] * 512
    log = [0] * 256
    value = 1
    for i in range(255):
        exp[i] = value
        log[value] = i
        value <<= 1
        if value & 0x100:
            value ^= 0x12D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = gf_tables()


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def datamatrix_generator_factors(error_count: int) -> list[int]:
    polynomial = [1]
    for i in range(1, error_count + 1):
        root = GF_EXP[i]
        new_poly = [0] * (len(polynomial) + 1)
        for j, coefficient in enumerate(polynomial):
            new_poly[j] ^= gf_mul(coefficient, root)
            new_poly[j + 1] ^= coefficient
        polynomial = new_poly
    return polynomial[:-1]


def datamatrix_error_codewords(data_codewords: list[int], error_count: int) -> list[int]:
    factors = datamatrix_generator_factors(error_count)
    ecc = [0] * error_count
    for codeword in data_codewords:
        m = ecc[-1] ^ codeword
        for k in range(error_count - 1, 0, -1):
            ecc[k] = ecc[k - 1] ^ gf_mul(m, factors[k]) if m else ecc[k - 1]
        ecc[0] = gf_mul(m, factors[0]) if m else 0
    return list(reversed(ecc))


def place_datamatrix_modules(codewords: list[int], rows: int, cols: int) -> list[list[int]]:
    modules: list[list[int | None]] = [[None for _ in range(cols)] for _ in range(rows)]

    def module(row: int, col: int, codeword_index: int, bit: int) -> None:
        if row < 0:
            row += rows
            col += 4 - ((rows + 4) % 8)
        if col < 0:
            col += cols
            row += 4 - ((cols + 4) % 8)
        if 0 <= row < rows and 0 <= col < cols and codeword_index < len(codewords):
            modules[row][col] = 1 if codewords[codeword_index] & (1 << (8 - bit)) else 0

    def utah(row: int, col: int, codeword_index: int) -> None:
        module(row - 2, col - 2, codeword_index, 1)
        module(row - 2, col - 1, codeword_index, 2)
        module(row - 1, col - 2, codeword_index, 3)
        module(row - 1, col - 1, codeword_index, 4)
        module(row - 1, col, codeword_index, 5)
        module(row, col - 2, codeword_index, 6)
        module(row, col - 1, codeword_index, 7)
        module(row, col, codeword_index, 8)

    def corner1(codeword_index: int) -> None:
        module(rows - 1, 0, codeword_index, 1)
        module(rows - 1, 1, codeword_index, 2)
        module(rows - 1, 2, codeword_index, 3)
        module(0, cols - 2, codeword_index, 4)
        module(0, cols - 1, codeword_index, 5)
        module(1, cols - 1, codeword_index, 6)
        module(2, cols - 1, codeword_index, 7)
        module(3, cols - 1, codeword_index, 8)

    def corner2(codeword_index: int) -> None:
        module(rows - 3, 0, codeword_index, 1)
        module(rows - 2, 0, codeword_index, 2)
        module(rows - 1, 0, codeword_index, 3)
        module(0, cols - 4, codeword_index, 4)
        module(0, cols - 3, codeword_index, 5)
        module(0, cols - 2, codeword_index, 6)
        module(0, cols - 1, codeword_index, 7)
        module(1, cols - 1, codeword_index, 8)

    def corner3(codeword_index: int) -> None:
        module(rows - 3, 0, codeword_index, 1)
        module(rows - 2, 0, codeword_index, 2)
        module(rows - 1, 0, codeword_index, 3)
        module(0, cols - 2, codeword_index, 4)
        module(0, cols - 1, codeword_index, 5)
        module(1, cols - 1, codeword_index, 6)
        module(2, cols - 1, codeword_index, 7)
        module(3, cols - 1, codeword_index, 8)

    def corner4(codeword_index: int) -> None:
        module(rows - 1, 0, codeword_index, 1)
        module(rows - 1, cols - 1, codeword_index, 2)
        module(0, cols - 3, codeword_index, 3)
        module(0, cols - 2, codeword_index, 4)
        module(0, cols - 1, codeword_index, 5)
        module(1, cols - 3, codeword_index, 6)
        module(1, cols - 2, codeword_index, 7)
        module(1, cols - 1, codeword_index, 8)

    row = 4
    col = 0
    codeword_index = 0
    while row < rows or col < cols:
        if row == rows and col == 0:
            corner1(codeword_index)
            codeword_index += 1
        if row == rows - 2 and col == 0 and cols % 4 != 0:
            corner2(codeword_index)
            codeword_index += 1
        if row == rows - 2 and col == 0 and cols % 8 == 4:
            corner3(codeword_index)
            codeword_index += 1
        if row == rows + 4 and col == 2 and cols % 8 == 0:
            corner4(codeword_index)
            codeword_index += 1

        while row >= 0 and col < cols:
            if row < rows and col >= 0 and modules[row][col] is None:
                utah(row, col, codeword_index)
                codeword_index += 1
            row -= 2
            col += 2
        row += 1
        col += 3

        while row < rows and col >= 0:
            if row >= 0 and col < cols and modules[row][col] is None:
                utah(row, col, codeword_index)
                codeword_index += 1
            row += 2
            col -= 2
        row += 3
        col += 1

    if modules[rows - 1][cols - 1] is None:
        modules[rows - 1][cols - 1] = 1
        modules[rows - 2][cols - 2] = 1
    return [[0 if value is None else value for value in line] for line in modules]


def add_datamatrix_finder(data_modules: list[list[int]], symbol: DataMatrixSymbol) -> list[list[int]]:
    matrix = [[0 for _ in range(symbol.cols)] for _ in range(symbol.rows)]
    row_regions = symbol.rows // (symbol.region_rows + 2)
    col_regions = symbol.cols // (symbol.region_cols + 2)

    for rr in range(row_regions):
        for cr in range(col_regions):
            top = rr * (symbol.region_rows + 2)
            left = cr * (symbol.region_cols + 2)
            bottom = top + symbol.region_rows + 1
            right = left + symbol.region_cols + 1

            for col in range(left, right + 1):
                matrix[top][col] = 1 if (col - left) % 2 == 0 else 0
                matrix[bottom][col] = 1
            for row in range(top, bottom + 1):
                matrix[row][left] = 1
                matrix[row][right] = 1 if (row - top) % 2 == 0 else 0

            for r in range(symbol.region_rows):
                for c in range(symbol.region_cols):
                    data_r = rr * symbol.region_rows + r
                    data_c = cr * symbol.region_cols + c
                    matrix[top + 1 + r][left + 1 + c] = data_modules[data_r][data_c]
    return matrix


def encode_code128_b(data: str) -> list[int]:
    codes = [104]
    for char in data:
        code = ord(char) - 32
        if code < 0 or code > 95:
            raise ValueError("only Code 128 set B printable ASCII is supported")
        codes.append(code)
    checksum = codes[0]
    for i, code in enumerate(codes[1:], start=1):
        checksum += i * code
    codes.append(checksum % 103)
    codes.append(106)
    return codes

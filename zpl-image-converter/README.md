# ZPL Image Converter

Pure offline ZPL-to-image conversion.

This folder is the reusable production package. It converts ZPL into Pillow
images or PNG files. It does not listen on a port, print to Windows printers, or
forward jobs.

## Install

```powershell
python -m pip install -r requirements.txt
```

For package-style use:

```powershell
python -m pip install -e .
```

## Convert A ZPL File

```powershell
python convert_zpl_to_image.py example.zpl --output label.png
```

Or, after editable install:

```powershell
zpl-to-image example.zpl --output label.png
```

Useful options:

```powershell
python convert_zpl_to_image.py example.zpl --crop
python convert_zpl_to_image.py example.zpl --dpi 203 --width 4 --height 3
python convert_zpl_to_image.py example.zpl --strict-graphic-crc
```

## Library Use

```python
from zpl_image_converter import RenderOptions, render_zpl_file

image, report = render_zpl_file("example.zpl", RenderOptions(dpi=203))
image.save("label.png")
```

## Renderer Scope

Supported well enough for the current reprint workflow:

- chained ZPL command parsing
- `^PW`, `^LL`, `^LH`, `^LS`
- `^FO`, `^FT`, `^A`, `^CF`, `^FB`, `^FH`, `^FD`, `^FS`
- `^GB`, `^GC`, `^GD`
- downloaded graphics with `~DG`, `^XG`, `^ID`
- field graphics with `^GF`
- ASCII-hex, `:B64:`, and `:Z64:` graphic payloads
- Code 39, Code 128 subset B, QR fallback when `qrcode` is installed, and a
  built-in ECC200 DataMatrix renderer for common square symbols

This is not a full Zebra firmware emulator. Unknown hardware/configuration
commands are ignored when they do not affect the rendered label.

# Zebra Print

One Python package for working with ZPL labels. It can render ZPL to PNG
offline, or run a TCP capture proxy that saves incoming print jobs, renders PNG
copies, optionally prints the PNG to a Windows printer, and optionally forwards
the original ZPL to a Zebra-compatible printer.

## Install

```powershell
py -m pip install -r requirements.txt
py -m pip install -e .
```

You can also run it from a checkout without installing:

```powershell
py -m zebra_print --help
```

## Convert A ZPL File

```powershell
py -m zebra_print convert examples/example.zpl --output label.png
```

After editable install:

```powershell
zebra-print convert examples/example.zpl --output label.png
```

Useful converter options:

```powershell
zebra-print convert examples/example.zpl --crop
zebra-print convert examples/example.zpl --dpi 203 --width 4 --height 3
zebra-print convert examples/example.zpl --strict-graphic-crc
zebra-print convert examples/example.zpl --max-canvas-pixels 20000000 --max-graphic-bytes 16777216
```

## Run The Capture Proxy

Save incoming jobs as `.zpl` and `.png` files:

```powershell
zebra-print proxy --listen-port 9100 --save-dir zpl_jobs
```

The proxy binds to `127.0.0.1` by default. To accept jobs from other machines
on the network, bind explicitly:

```powershell
zebra-print proxy --bind-host 0.0.0.0 --listen-port 9100 --save-dir zpl_jobs
```

Print the rendered PNG to a Windows printer:

```powershell
zebra-print proxy --target-printer "Printer Name"
```

Forward the original raw ZPL while keeping the captured ZPL and PNG copy:

```powershell
zebra-print proxy --forward-to-zebra 192.168.1.50:9100
```

Override the default printer info used for Zebra host-identification queries
such as `~HI`:

```powershell
zebra-print proxy --printer-info-config .\printer_info.local.json
```

Limit large or malformed jobs:

```powershell
zebra-print proxy --max-job-bytes 26214400 --max-canvas-pixels 20000000 --max-graphic-bytes 16777216
```

List Windows printers:

```powershell
zebra-print printers
```

## Library Use

```python
from zebra_print import RenderOptions, render_zpl_file

image, report = render_zpl_file("examples/example.zpl", RenderOptions(dpi=203))
image.save("label.png")
```

```python
from zebra_print import RenderOptions, ZPLCaptureProxy

proxy = ZPLCaptureProxy(
    bind_host="0.0.0.0",
    listen_port=9100,
    save_dir="zpl_jobs",
    render_options=RenderOptions(dpi=203),
    forward_host="192.168.1.50",
    forward_port=9100,
)
proxy.start()
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
- many common linear barcodes, QR fallback when `qrcode` is installed, and a
  built-in ECC200 DataMatrix renderer for common square symbols

This is not a full Zebra firmware emulator. Unknown hardware/configuration
commands are ignored when they do not affect the rendered label.

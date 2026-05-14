# ZPL Capture Proxy

TCP listener and reprint workflow for raw ZPL jobs.

This folder is intentionally separate from the reusable converter package. It
depends on `../zpl-image-converter` for rendering and owns the operational
concerns:

- listening on a TCP port
- saving captured `.zpl` jobs
- rendering `.png` copies
- optionally printing PNGs to a Windows printer
- optionally forwarding the original ZPL to a Zebra-compatible printer

## Install

From this folder:

```powershell
python -m pip install -r requirements.txt
```

That installs the sibling converter package in editable mode.

## Run

Save incoming jobs as ZPL and PNG:

```powershell
python run_zpl_proxy.py --listen-port 9100 --save-dir zpl_jobs
```

Print the rendered PNG to a Windows printer:

```powershell
python run_zpl_proxy.py --target-printer "Printer Name"
```

Forward the original raw ZPL while keeping the PNG copy:

```powershell
python run_zpl_proxy.py --forward-to-zebra 192.168.1.50:9100
```

Override the default printer info used for Zebra host-identification queries
such as `~HI`:

```powershell
python run_zpl_proxy.py --printer-info-config .\printer_info.local.json
```

The packaged default lives at `zpl_capture_proxy/printer_info.json` and matches
the current `ZD621-200dpi,V93.21.46Z,8,8176KB` response. Override files can
provide any subset of the same JSON keys.

List Windows printers:

```powershell
python run_zpl_proxy.py --list-printers
```

## Relationship To Converter

The proxy does not contain the renderer. Rendering comes from:

```python
from zpl_image_converter import RenderOptions, render_zpl_bytes
```

Keep renderer improvements in `../zpl-image-converter`; keep network, printing,
and forwarding behavior here.

"""Windows printer helpers for rendered label images."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def print_image_to_windows_printer(image_file: str | Path, printer_name: str, mode: str = "fit", timeout: int = 60) -> None:
    image_path = Path(image_file).resolve()
    if mode not in {"fit", "actual", "stretch"}:
        raise ValueError("mode must be fit, actual, or stretch")

    ps_script = f"""
$ErrorActionPreference = 'Stop'
$file = {_ps_quote(image_path)}
$printer = {_ps_quote(printer_name)}
$mode = {_ps_quote(mode)}
Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($file)
$doc = New-Object System.Drawing.Printing.PrintDocument
$doc.PrinterSettings.PrinterName = $printer
if (-not $doc.PrinterSettings.IsValid) {{
    throw "Printer not found or unavailable: $printer"
}}
$doc.DefaultPageSettings.Margins = New-Object System.Drawing.Printing.Margins(0, 0, 0, 0)
$doc.add_PrintPage({{
    param($sender, $ev)
    $ev.Graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $bounds = $ev.MarginBounds
    if ($bounds.Width -le 0 -or $bounds.Height -le 0) {{
        $bounds = $ev.PageBounds
    }}
    if ($mode -eq 'actual') {{
        $w = [single](($image.Width / $image.HorizontalResolution) * 100.0)
        $h = [single](($image.Height / $image.VerticalResolution) * 100.0)
        $dest = New-Object System.Drawing.RectangleF($bounds.Left, $bounds.Top, $w, $h)
    }} elseif ($mode -eq 'stretch') {{
        $dest = New-Object System.Drawing.RectangleF($bounds.Left, $bounds.Top, $bounds.Width, $bounds.Height)
    }} else {{
        $scale = [Math]::Min($bounds.Width / $image.Width, $bounds.Height / $image.Height)
        $w = [single]($image.Width * $scale)
        $h = [single]($image.Height * $scale)
        $x = [single]($bounds.Left + (($bounds.Width - $w) / 2.0))
        $y = [single]($bounds.Top + (($bounds.Height - $h) / 2.0))
        $dest = New-Object System.Drawing.RectangleF($x, $y, $w, $h)
    }}
    $ev.Graphics.DrawImage($image, $dest)
    $ev.HasMorePages = $false
}})
$doc.Print()
$image.Dispose()
$doc.Dispose()
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "unknown print error"
        raise RuntimeError(details)


def list_windows_printers() -> str:
    ps = "Get-Printer | Sort-Object Name | Format-Table Name,DriverName,PortName -AutoSize | Out-String"
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    fallback = subprocess.run(
        ["wmic", "printer", "get", "name,portname,drivername", "/format:table"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return (fallback.stdout or fallback.stderr or result.stderr).strip()

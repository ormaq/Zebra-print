"""TCP ZPL capture proxy."""

from __future__ import annotations

import logging
import socket
import threading
from datetime import datetime
from pathlib import Path

from zpl_image_converter import RenderOptions, render_zpl_bytes

from .printer_info import PrinterInfo, load_printer_info
from .printing import print_image_to_windows_printer

LOGGER = logging.getLogger(__name__)

QUERY_COMMANDS = (
    (b"~HQES", "~HQES"),
    (b"~HI", "~HI"),
    (b"~HS", "~HS"),
    (b"^HH", "^HH"),
    (b"~HM", "~HM"),
    (b"~HD", "~HD"),
)
STANDALONE_QUERY_COMMANDS = (b"~HQES", b"~HM", b"~HD")
WRAPPED_QUERY_COMMANDS = (b"~HI", b"~HS", b"^HH")


class ZPLCaptureProxy:
    """Listen for raw ZPL jobs, save originals, and render PNG copies."""

    def __init__(
        self,
        bind_host: str,
        listen_port: int,
        save_dir: str | Path,
        render_options: RenderOptions,
        target_printer: str | None = None,
        print_mode: str = "fit",
        forward_host: str | None = None,
        forward_port: int | None = None,
        printer_info: PrinterInfo | None = None,
    ):
        self.bind_host = bind_host
        self.listen_port = listen_port
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.render_options = render_options
        self.target_printer = target_printer
        self.print_mode = print_mode
        self.forward_host = forward_host
        self.forward_port = forward_port
        self.printer_info = printer_info or load_printer_info()
        self.connection_count = 0
        self.count_lock = threading.Lock()

    def start(self) -> None:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((self.bind_host, self.listen_port))
        server_socket.listen(10)

        LOGGER.info("ZPL capture proxy running")
        LOGGER.info("Listening: %s:%s", self.bind_host, self.listen_port)
        LOGGER.info("Save dir: %s", self.save_dir.resolve())
        LOGGER.info(
            "Render fallback: %sx%s in @ %s DPI",
            self.render_options.width_inches,
            self.render_options.height_inches,
            self.render_options.dpi,
        )
        LOGGER.info("Target printer: %s", self.target_printer or "(none, save PNG only)")
        LOGGER.info("Printer HI: %s", self.printer_info.host_identification.payload)
        if self.forward_host and self.forward_port:
            LOGGER.info("Forward original ZPL: %s:%s", self.forward_host, self.forward_port)
        LOGGER.info("Press Ctrl+C to stop")

        try:
            while True:
                client_socket, address = server_socket.accept()
                with self.count_lock:
                    self.connection_count += 1
                    conn_id = self.connection_count
                thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address, conn_id),
                    daemon=True,
                )
                thread.start()
        except KeyboardInterrupt:
            LOGGER.info("Stopping proxy")
        finally:
            server_socket.close()
            LOGGER.info("Processed %s connection(s)", self.connection_count)

    def handle_client(self, client_socket: socket.socket, address: tuple[str, int], conn_id: int) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        LOGGER.info(
            "[%s] job %s from %s:%s",
            datetime.now().strftime("%H:%M:%S"),
            conn_id,
            address[0],
            address[1],
        )

        try:
            data = self._read_socket_with_query_detection(client_socket)
            if not data:
                LOGGER.debug("No data received for job %s", conn_id)
                return

            LOGGER.info("Received %s bytes", len(data))
            LOGGER.debug("First 100 bytes: %r", data[:100])
            LOGGER.debug("First 50 bytes hex: %s", data[:50].hex(" "))

            if self._is_printer_query(data):
                query_type = self._detect_query_type(data)
                LOGGER.debug("Detected printer query: %s", query_type)
                self._send_query_response(client_socket, query_type)
                return

            zpl_file = self.save_dir / f"job_{timestamp}_conn{conn_id}.zpl"
            png_file = self.save_dir / f"job_{timestamp}_conn{conn_id}.png"
            zpl_file.write_bytes(data)
            LOGGER.info("Saved ZPL: %s", zpl_file)

            try:
                image, report = render_zpl_bytes(data, self.render_options)
                image.save(png_file, dpi=(self.render_options.dpi, self.render_options.dpi))
                LOGGER.info("Rendered PNG: %s (%sx%s px)", png_file, image.width, image.height)
                for warning in report.warnings:
                    LOGGER.warning("Render warning: %s", warning)
            except Exception as exc:
                LOGGER.warning("Render failed: %s", exc)
                png_file = None

            if png_file and self.target_printer:
                try:
                    print_image_to_windows_printer(png_file, self.target_printer, mode=self.print_mode)
                    LOGGER.info("Printed PNG to: %s", self.target_printer)
                except Exception as exc:
                    LOGGER.warning("Print failed: %s", exc)

            if self.forward_host and self.forward_port:
                self.forward_original_zpl(data)
        except Exception as exc:
            LOGGER.warning("Job %s failed: %s", conn_id, exc)
        finally:
            client_socket.close()

    def _read_socket_with_query_detection(self, client_socket: socket.socket) -> bytes:
        """Read socket data with early detection for query commands."""
        chunks: list[bytes] = []
        extended_for_print_job = False
        client_socket.settimeout(0.5)

        while True:
            try:
                chunk = client_socket.recv(65536)
            except socket.timeout:
                if not chunks:
                    break

                data_so_far = b"".join(chunks)
                if self._is_complete_query_command(data_so_far):
                    break
                if not extended_for_print_job:
                    client_socket.settimeout(5.0)
                    extended_for_print_job = True
                    continue
                break

            if not chunk:
                break
            chunks.append(chunk)

            data_so_far = b"".join(chunks)
            if self._is_complete_query_command(data_so_far):
                break

        return b"".join(chunks)

    def _is_complete_query_command(self, data: bytes) -> bool:
        """Check if data contains a complete query command."""
        data_upper = data.upper()
        for cmd in STANDALONE_QUERY_COMMANDS:
            if cmd in data_upper:
                return True
        for cmd in WRAPPED_QUERY_COMMANDS:
            if cmd in data_upper and b"^XZ" in data_upper:
                return True
        return False

    def _detect_query_type(self, data: bytes) -> str:
        """Detect which type of query command was sent."""
        data_upper = data.upper()
        for command, query_type in QUERY_COMMANDS:
            if command in data_upper:
                return query_type
        return "UNKNOWN"

    def _is_printer_query(self, data: bytes) -> bool:
        """Check if the ZPL data contains any printer query command."""
        data_upper = data.upper()
        return any(command in data_upper for command, _query_type in QUERY_COMMANDS)

    def _send_query_response(self, client_socket: socket.socket, query_type: str) -> None:
        """Send the configured response for a printer query."""
        query = query_type.upper()
        response = self.printer_info.response_for_query(query)
        client_socket.sendall(response)
        LOGGER.debug("Sent %s response (%s bytes)", query, len(response))

    def forward_original_zpl(self, data: bytes) -> bool:
        assert self.forward_host is not None
        assert self.forward_port is not None
        try:
            with socket.create_connection((self.forward_host, self.forward_port), timeout=10) as sock:
                sock.sendall(data)
            LOGGER.info("Forwarded original ZPL to %s:%s", self.forward_host, self.forward_port)
            return True
        except Exception as exc:
            LOGGER.warning("Forward failed: %s", exc)
            return False

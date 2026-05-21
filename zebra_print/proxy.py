"""TCP ZPL capture proxy."""

from __future__ import annotations

import logging
import socket
import threading
from datetime import datetime
from pathlib import Path

from .printer_info import PrinterInfo, load_printer_info
from .printing import print_image_to_windows_printer
from .renderer import RenderOptions, render_zpl_bytes

LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_JOB_BYTES = 25 * 1024 * 1024
DEFAULT_QUERY_KEEPALIVE_TIMEOUT = 30.0

QUERY_COMMANDS = (
    (b"~HQES", "~HQES"),
    (b"~HI", "~HI"),
    (b"~HS", "~HS"),
    (b"^HH", "^HH"),
    (b"~HM", "~HM"),
    (b"~HD", "~HD"),
)
QUERY_COMMAND_BY_PAYLOAD = {command: query_type for command, query_type in QUERY_COMMANDS}
STANDALONE_QUERY_COMMANDS = (b"~HQES", b"~HM", b"~HD")
WRAPPED_QUERY_COMMANDS = (b"~HI", b"~HS", b"^HH")

HOST_DIRECTORY_QUERY = b"^HW"


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
        max_job_bytes: int | None = DEFAULT_MAX_JOB_BYTES,
        query_keepalive_timeout: float = DEFAULT_QUERY_KEEPALIVE_TIMEOUT,
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
        self.max_job_bytes = max_job_bytes
        self.query_keepalive_timeout = query_keepalive_timeout
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
        if self.max_job_bytes is not None and self.max_job_bytes > 0:
            LOGGER.info("Max job size: %s bytes", self.max_job_bytes)
        LOGGER.info("Query keepalive timeout: %ss", self.query_keepalive_timeout)
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
        LOGGER.info(
            "[%s] job %s from %s:%s",
            datetime.now().strftime("%H:%M:%S"),
            conn_id,
            address[0],
            address[1],
        )

        try:
            read_timeout = 0.5
            request_count = 0
            while True:
                data = self._read_socket_with_query_detection(client_socket, initial_timeout=read_timeout)
                if not data:
                    LOGGER.debug("No more data received for connection %s", conn_id)
                    return

                request_count += 1
                LOGGER.info("Received %s bytes", len(data))
                LOGGER.debug("First 100 bytes: %r", data[:100])
                LOGGER.debug("First 50 bytes hex: %s", data[:50].hex(" "))

                if self._is_printer_query(data):
                    query_type = self._detect_query_type(data)
                    LOGGER.debug("Detected printer query: %s", query_type)
                    self._send_query_response(client_socket, query_type)
                    read_timeout = self.query_keepalive_timeout
                    continue

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                file_stem = f"job_{timestamp}_conn{conn_id}_req{request_count}"
                zpl_file = self.save_dir / f"{file_stem}.zpl"
                png_file = self.save_dir / f"{file_stem}.png"
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
                read_timeout = self.query_keepalive_timeout
        except Exception as exc:
            LOGGER.warning("Job %s failed: %s", conn_id, exc)
        finally:
            client_socket.close()

    def _read_socket_with_query_detection(self, client_socket: socket.socket, initial_timeout: float = 0.5) -> bytes:
        """Read socket data with early detection for query commands."""
        chunks: list[bytes] = []
        total_bytes = 0
        extended_for_print_job = False
        client_socket.settimeout(initial_timeout)

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
            total_bytes += len(chunk)
            if self.max_job_bytes is not None and self.max_job_bytes > 0 and total_bytes > self.max_job_bytes:
                raise ValueError(f"ZPL job exceeds max job size of {self.max_job_bytes} bytes")

            data_so_far = b"".join(chunks)
            if self._is_complete_query_command(data_so_far):
                break

        return b"".join(chunks)

    def _is_complete_query_command(self, data: bytes) -> bool:
        """Check if data contains a complete query command."""
        return self._detect_query_type(data) != "UNKNOWN"

    def _detect_query_type(self, data: bytes) -> str:
        """Detect which type of query command was sent."""
        payload = normalize_query_payload(data)
        query_type = QUERY_COMMAND_BY_PAYLOAD.get(payload)
        if query_type and payload in STANDALONE_QUERY_COMMANDS:
            return query_type

        if payload.startswith(b"^XA") and payload.endswith(b"^XZ"):
            inner = payload[3:-3]
            query_type = QUERY_COMMAND_BY_PAYLOAD.get(inner)
            if query_type and inner in WRAPPED_QUERY_COMMANDS:
                return query_type
            if is_host_directory_query(inner):
                return "^HW"
        return "UNKNOWN"

    def _is_printer_query(self, data: bytes) -> bool:
        """Check if the ZPL data contains any printer query command."""
        return self._detect_query_type(data) != "UNKNOWN"

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


def normalize_query_payload(data: bytes) -> bytes:
    """Normalize query payloads without parsing label field contents as commands."""
    return b"".join(data.upper().split())


def is_host_directory_query(inner_payload: bytes) -> bool:
    """Return true for a single ^HW command, including optional drive/path parameters."""
    return (
        inner_payload.startswith(HOST_DIRECTORY_QUERY)
        and b"^" not in inner_payload[1:]
        and b"~" not in inner_payload
    )

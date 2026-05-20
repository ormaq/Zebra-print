import contextlib
import io
import socket
import tempfile
import threading
import unittest

from zebra_print import RenderOptions, ZPLCaptureProxy
from zebra_print.cli import main


class FakeSocket:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def recv(self, _size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


class ProxyTests(unittest.TestCase):
    def test_forward_original_zpl_sends_bytes_to_target(self):
        payload = b"^XA^FO10,10^A0N,24,24^FDHELLO^FS^XZ"
        received: list[bytes] = []
        ready = threading.Event()
        port_holder: list[int] = []

        def run_server() -> None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
                server.bind(("127.0.0.1", 0))
                server.listen(1)
                port_holder.append(server.getsockname()[1])
                ready.set()
                conn, _address = server.accept()
                with conn:
                    received.append(conn.recv(65536))

        server_thread = threading.Thread(target=run_server)
        server_thread.start()
        self.assertTrue(ready.wait(timeout=2.0))

        with tempfile.TemporaryDirectory() as directory:
            proxy = ZPLCaptureProxy(
                bind_host="127.0.0.1",
                listen_port=0,
                save_dir=directory,
                render_options=RenderOptions(),
                forward_host="127.0.0.1",
                forward_port=port_holder[0],
            )

            self.assertTrue(proxy.forward_original_zpl(payload))

        server_thread.join(timeout=2.0)
        self.assertFalse(server_thread.is_alive())
        self.assertEqual(received, [payload])

    def test_printer_query_detection_requires_exact_query_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            proxy = ZPLCaptureProxy(
                bind_host="127.0.0.1",
                listen_port=0,
                save_dir=directory,
                render_options=RenderOptions(),
            )

            self.assertTrue(proxy._is_printer_query(b"~HQES\r\n"))
            self.assertEqual(proxy._detect_query_type(b"^XA~HI^XZ"), "~HI")
            self.assertFalse(proxy._is_printer_query(b"^XA^FO10,10^A0N,24,24^FD~HI^FS^XZ"))
            self.assertEqual(proxy._detect_query_type(b"^XA^FO10,10^A0N,24,24^FD~HI^FS^XZ"), "UNKNOWN")

    def test_read_socket_rejects_oversized_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            proxy = ZPLCaptureProxy(
                bind_host="127.0.0.1",
                listen_port=0,
                save_dir=directory,
                render_options=RenderOptions(),
                max_job_bytes=5,
            )

            with self.assertRaisesRegex(ValueError, "exceeds max job size"):
                proxy._read_socket_with_query_detection(FakeSocket([b"1234", b"56"]))

    def test_invalid_forward_target_exits_with_parser_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["proxy", "--forward-to-zebra", "bad"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("forward target must use HOST:PORT", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

import socket
import tempfile
import threading
import unittest

from zebra_print import RenderOptions, ZPLCaptureProxy


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


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from zebra_print.printer_info import load_printer_info


class PrinterInfoTests(unittest.TestCase):
    def test_default_host_identification_matches_existing_response(self):
        printer_info = load_printer_info()

        self.assertEqual(
            printer_info.response_for_query("~HI"),
            b"\x02ZD621-200dpi,V93.21.46Z,8,8176KB\x03\r\n",
        )

    def test_override_can_change_host_identification(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "printer-info.json"
            config_path.write_text(
                json.dumps({"host_identification": {"model": "ZT411", "firmware": "V99.1"}}),
                encoding="utf-8",
            )

            printer_info = load_printer_info(config_path)

        self.assertEqual(printer_info.response_for_query("~HI"), b"\x02ZT411,V99.1,8,8176KB\x03\r\n")

    def test_override_can_change_ram_status(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "printer-info.json"
            config_path.write_text(
                json.dumps({"host_ram_status": {"total_kb": 4096, "available_kb": 3000}}),
                encoding="utf-8",
            )

            printer_info = load_printer_info(config_path)

        self.assertEqual(printer_info.response_for_query("~HM"), b"\x024096,3000,7380\x03\r\n")

    def test_host_directory_query_returns_font_directory(self):
        printer_info = load_printer_info()
        response = printer_info.response_for_query("^HW")

        self.assertTrue(response.startswith(b"\x02\r\n- DIR E:*.*"))
        self.assertIn(b"E:EHI3EVUG.TTF", response)
        self.assertTrue(response.endswith(b"\x03"))

    def test_rejects_non_ascii_host_identification(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "printer-info.json"
            config_path.write_text(
                json.dumps({"host_identification": {"model": "ZD621-\u2603"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "must be ASCII"):
                load_printer_info(config_path)

    def test_rejects_unknown_nested_key(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "printer-info.json"
            config_path.write_text(
                json.dumps({"host_identification": {"printer_model": "ZT411"}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Unsupported host_identification key"):
                load_printer_info(config_path)


if __name__ == "__main__":
    unittest.main()

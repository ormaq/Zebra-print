import contextlib
import io
import unittest
from pathlib import Path

from zebra_print.cli import main


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_convert_limit_failure_exits_with_parser_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            main(["convert", str(ROOT / "examples" / "example.zpl"), "--max-canvas-pixels", "100"])

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("exceeds max canvas pixels", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

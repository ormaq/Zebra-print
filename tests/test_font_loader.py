import unittest
from importlib import resources

from zebra_print import font_loader
from zebra_print.renderer import RenderOptions, render_zpl_bytes


class FontLoaderTests(unittest.TestCase):
    def tearDown(self):
        font_loader.clear_font_cache(reset_mapping=True)

    def test_packaged_font_mapping_is_loadable(self):
        mapping = resources.files("zebra_print").joinpath("font_mapping.json")

        self.assertTrue(mapping.is_file())
        font_loader.load_font_mapping()
        self.assertIsNotNone(font_loader.load_font(16, "E:EHI3EVUG.TTF"))

    def test_mapped_printer_font_does_not_emit_missing_font_warning(self):
        zpl = "^XA^PW260^LL100^FO10,30^A@N,24,24,E:EHI3EVUG.TTF^FDMAPPED FONT^FS^XZ"

        _image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertFalse(any("Stored font not found" in warning for warning in report.warnings))
        self.assertEqual(report.rendered_text_fields, 1)


if __name__ == "__main__":
    unittest.main()

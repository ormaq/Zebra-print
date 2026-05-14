import base64
import unittest
import zlib
from pathlib import Path

from zpl_image_converter.renderer import RenderOptions, crc16_ccitt_hex, render_zpl_bytes, render_zpl_file


ROOT = Path(__file__).resolve().parents[1]


def count_dark_pixels(image):
    return sum(image.convert("L").histogram()[:128])


class RendererTests(unittest.TestCase):
    def test_example_label_renders_nonblank(self):
        image, report = render_zpl_file(ROOT / "example.zpl", RenderOptions())
        self.assertEqual(image.size, (609, 406))
        self.assertGreater(sum(image.convert("L").histogram()[:128]), 30000)
        self.assertEqual(report.rendered_graphics, 0)
        self.assertEqual(report.rendered_barcodes, 2)

    def test_b64_and_z64_graphic_fields_render(self):
        raw = b"\x80"
        b64 = base64.b64encode(raw).decode("ascii")
        z64 = base64.b64encode(zlib.compress(raw)).decode("ascii")

        zpl = (
            "^XA"
            f"^FO0,0^GFA,1,1,1,:B64:{b64}:{crc16_ccitt_hex(b64.encode('ascii'))}^FS"
            f"^FO2,0^GFA,1,1,1,:Z64:{z64}:{crc16_ccitt_hex(z64.encode('ascii'))}^FS"
            "^XZ"
        )
        path = ROOT / "_tmp_graphics.zpl"
        try:
            path.write_text(zpl, encoding="ascii")
            image, report = render_zpl_file(path, RenderOptions(width_inches=0.1, height_inches=0.1))
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_graphics, 2)
        self.assertLess(image.convert("L").getpixel((0, 0)), 128)
        self.assertLess(image.convert("L").getpixel((2, 0)), 128)

    def test_first_barcode_batch_renders_supported_symbols(self):
        zpl = (
            "^XA^PW720^LL480"
            "^FO20,20^BY2,3,50^B1N,N,50,N,N^FD12345-6^FS"
            "^FO20,90^BY2,3,50^B2N,50,N,N,N^FD123456^FS"
            "^FO20,160^BY2,3,50^B5N,50,N,N^FD12345678901^FS"
            "^FO20,230^BY2,3,50^B8N,50,N,N^FD5512345^FS"
            "^FO20,300^BY2,3,50^B9N,50,N,N^FD123450^FS"
            "^FO20,370^BY2,3,50^BAN,50,N,N,N^FDCODE93^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_barcodes, 6)
        self.assertGreater(count_dark_pixels(image), 5000)

    def test_first_barcode_batch_warns_and_falls_back_for_unsupported_symbols(self):
        zpl = (
            "^XA^PW500^LL220"
            "^FO20,20^B0N,4^FDAZTEC^FS"
            "^FO20,70^B4N,50^FDCODE49^FS"
            "^FO20,120^B7N,4,4,5^FDPDF417^FS"
            "^FO20,170^BBN,50^FDCODABLOCK^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.rendered_barcodes, 0)
        self.assertEqual(len(report.warnings), 4)
        for command in ("^B0", "^B4", "^B7", "^BB"):
            self.assertTrue(any(command in warning for warning in report.warnings))
        self.assertGreater(count_dark_pixels(image), 100)

    def test_second_barcode_batch_renders_supported_symbols(self):
        zpl = (
            "^XA^PW760^LL570"
            "^FO20,20^BY2,3,50^BEN,50,N,N^FD590123412345^FS"
            "^FO20,90^BY2,3,50^BIN,50,N,N^FD123456^FS"
            "^FO20,160^BY2,3,50^BJN,50,N,N^FD654321^FS"
            "^FO20,230^BY2,3,50^BKN,N,50,N,N,N,N^FDA12345B^FS"
            "^FO20,300^BY2,3,50^BLN,50,N^FDLOG123^FS"
            "^FO20,370^BY2,3,50^BMN,N,50,N,N,N^FD123456^FS"
            "^FO20,440^BY2,3,50^BPN,N,50,N,N^FD98765^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_barcodes, 7)
        self.assertGreater(count_dark_pixels(image), 7000)

    def test_second_barcode_batch_warns_and_falls_back_for_unsupported_symbols(self):
        zpl = (
            "^XA^PW500^LL180"
            "^FO20,20^BDN,5^FDMAXICODE^FS"
            "^FO20,70^BFN,4,4^FDMICROPDF^FS"
            "^FO20,120^BON,4^FDAZTEC2^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.rendered_barcodes, 0)
        self.assertEqual(len(report.warnings), 3)
        for command in ("^BD", "^BF", "^BO"):
            self.assertTrue(any(command in warning for warning in report.warnings))
        self.assertGreater(count_dark_pixels(image), 100)

    def test_third_batch_renders_barcodes_graphics_and_recalled_formats(self):
        zpl = (
            "~DGR:DOT.GRF,1,1,80"
            "^XA^PW620^LL430"
            "^FO20,20^BY2,3,55^BUN,55,N,N^FD036000291452^FS"
            "^FO20,95^BY2,3,45^BSN,45,N,N^FD12^FS"
            "^FO20,160^BY2,3,55^BZN,55,N,N^FD12345^FS"
            "^FO240,20^GE90,45,5,B^FS"
            "^FO240,90^GSA,34,34^FS"
            "^FO240,150^ILR:DOT.GRF^FS"
            "^DFR:FMT.ZPL^FO360,20^GB70,35,3^FS^XZ"
            "^XA^XFR:FMT.ZPL^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_barcodes, 3)
        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 1)
        self.assertGreater(count_dark_pixels(image), 5000)

    def test_third_batch_warns_and_falls_back_for_unsupported_composite_barcodes(self):
        zpl = (
            "^XA^PW500^LL140"
            "^FO20,20^BRN,6^FD0101234567890128^FS"
            "^FO20,75^BTN,50^FDTLC39^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.rendered_barcodes, 0)
        self.assertEqual(len(report.warnings), 2)
        for command in ("^BR", "^BT"):
            self.assertTrue(any(command in warning for warning in report.warnings))
        self.assertGreater(count_dark_pixels(image), 100)

    def test_fourth_batch_handles_virtual_image_storage_commands(self):
        zpl = (
            "~DYR:DY.GRF,1,1,80"
            "^XA^PW180^LL140"
            "^FO5,5^XGR:DY.GRF^FS"
            "^IMR:DY.GRF,R:COPY.GRF"
            "^FO20,5^XGR:COPY.GRF^FS"
            "^ISR:SAVED.PNG"
            "^FO5,35^ILR:SAVED.PNG^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 3)
        self.assertGreater(count_dark_pixels(image), 3)

    def test_fourth_batch_erase_graphics_acknowledges_cleared_storage(self):
        zpl = (
            "~DGR:DOT.GRF,1,1,80"
            "^XA^PW120^LL80"
            "^EGR:*.GRF"
            "^FO5,5^XGR:DOT.GRF^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 0)
        self.assertTrue(any("Graphic not found" in warning for warning in report.warnings))
        self.assertEqual(count_dark_pixels(image), 0)

    def test_fourth_batch_handles_font_encoding_and_clock_metadata(self):
        zpl = (
            "~DYR:FONT.TTF,T,synthetic-font"
            "^XA^PW260^LL100"
            "^CI28"
            "^CWZ,R:FONT.TTF"
            "^FC%,%"
            "^FO10,20^AZN,24,24^FDALIAS FONT^FS"
            "^FO10,55^A@N,24,24,R:FONT.TTF^FDSTORED FONT^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 2)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_fifth_batch_renders_variable_extract_and_multiple_origin_fields(self):
        zpl = (
            "^XA^PW420^LL220"
            "^FXThis comment should not render"
            "^FO10,20^FE2,4^FDABCDEFG^FS"
            "^FM10,70,140,70,270,70^FVREPEAT^FS"
            "^FO10,120^FN1^FDVALUE^FS"
            "^FO140,120^FN1^FS"
            "^FWB"
            "^FO330,170^A0,24,24^FDROT^FS"
            "^FPV,2"
            "^FO360,20^A0N,18,18^FDVERT^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertGreaterEqual(report.rendered_text_fields, 8)
        self.assertGreater(count_dark_pixels(image), 1000)

    def test_fifth_batch_acknowledges_text_metadata_commands(self):
        zpl = (
            "~DYR:FALLBACK.TTF,T,synthetic-font"
            "^XA^PW260^LL100"
            "^FLR:BASE.TTF,R:FALLBACK.TTF"
            "^KD%Y-%m-%d"
            "^PA1,2,3"
            "^FO10,20^A@N,24,24,R:FALLBACK.TTF^FDMETA OK^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_sixth_batch_renders_text_block_and_serialized_fields(self):
        zpl = (
            "~DYR:ENC.TBL,T,synthetic-encoding"
            "^XA^PW420^LL220"
            "^SER:ENC.TBL"
            "^FO10,20^A0N,20,20^TB160,48,2,L^FDONE TWO THREE FOUR FIVE SIX^FS"
            "^FO10,95^A0N,24,24^SFABC-###,1^FD7^FS"
            "^FO10,140^A0N,24,24^SFITEM-####,1^SN42,1,Y^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertGreaterEqual(report.rendered_text_fields, 3)
        self.assertGreater(count_dark_pixels(image), 1000)

    def test_sixth_batch_acknowledges_printer_storage_commands(self):
        zpl = (
            "~DGE:FLASH.GRF,1,1,80"
            "^XA^PW160^LL100"
            "^CMR,E,B,A"
            "^CN"
            "^COY"
            "^CPY"
            "^CVY"
            "^JB"
            "^FO5,5^XGE:FLASH.GRF^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 0)
        self.assertTrue(any("Graphic not found" in warning for warning in report.warnings))
        self.assertEqual(count_dark_pixels(image), 0)

    def test_seventh_batch_acknowledges_printer_setup_metadata(self):
        zpl = (
            "^XA^PW260^LL120"
            "^JH10,20"
            "^JI"
            "^JJ1,2"
            "^JSA"
            "^JT30"
            "^JUS"
            "^JW50"
            "^KLEN"
            "^KNRENDERER"
            "^KP0000"
            "^FO10,40^A0N,24,24^FDVISIBLE^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eighth_batch_acknowledges_media_and_printer_metadata(self):
        zpl = (
            "^XA^PW260^LL120"
            "^KV1,2"
            "^MAalerts"
            "^MD12"
            "^MFN,N"
            "^MImaintenance"
            "^ML200"
            "^MMT"
            "^MNY"
            "^MPM"
            "^MTT"
            "^FO10,40^A0N,24,24^FDMEDIA^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eighth_batch_warns_when_label_exceeds_maximum_length(self):
        zpl = "^XA^PW160^LL120^ML60^FO10,20^A0N,20,20^FDLONG^FS^XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertTrue(any("^ML maximum" in warning for warning in report.warnings))
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 50)

    def test_ninth_batch_acknowledges_print_job_metadata(self):
        zpl = (
            "^XA^PW260^LL120"
            "^MWY"
            "^PF20"
            "^PH"
            "^PN"
            "^PP"
            "^PR4,4,4"
            "^SC9600,8,N,1"
            "^SI10,20"
            "^SLD,Y"
            "^FO10,40^A0N,24,24^FDJOB META^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_ninth_batch_print_orientation_rotates_entire_output(self):
        zpl = "^XA^PW80^LL60^POI^FO0,0^GB10,10,10^FS^XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())
        gray = image.convert("L")

        self.assertEqual(report.warnings, [])
        self.assertLess(gray.getpixel((75, 55)), 128)
        self.assertGreater(gray.getpixel((0, 0)), 128)

    def test_tenth_batch_acknowledges_rtc_network_and_printer_metadata(self):
        zpl = (
            "^XA^PW300^LL130"
            "^SO+01:00"
            "^SP"
            "^SQalert"
            "^SR100"
            "^SSsensor"
            "^ST2026,05,14,10,30,00"
            "^SXalert-config"
            "^XB"
            "^XS"
            "^ZZ"
            "^FO10,45^A0N,24,24^FDMETA 10^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eleventh_batch_acknowledges_tilde_status_and_control_commands(self):
        zpl = (
            "~HB"
            "~HD"
            "~HM"
            "~HU"
            "~JA"
            "~JC"
            "~JD"
            "~JE"
            "~JF1"
            "^XA^PW260^LL120^FO10,40^A0N,24,24^FDTILDE META^FS^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eleventh_batch_reset_optional_memory_clears_flash_graphics(self):
        zpl = "~DGE:FLASH.GRF,1,1,80~JB^XA^PW120^LL80^FO5,5^XGE:FLASH.GRF^FS^XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 0)
        self.assertTrue(any("Graphic not found" in warning for warning in report.warnings))
        self.assertEqual(count_dark_pixels(image), 0)

    def test_twelfth_batch_acknowledges_tilde_diagnostic_and_control_commands(self):
        zpl = (
            "~JG"
            "~JI"
            "~JN"
            "~JO"
            "~JP"
            "~JQ"
            "~JR"
            "~JSB"
            "~JX"
            "^XA^PW260~JL90^FO10,30^A0N,24,24^FDRESET OK^FS^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(image.size, (260, 90))
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_thirteenth_batch_acknowledges_tilde_job_and_media_commands(self):
        zpl = (
            "~KB"
            "~PH"
            "~PL20"
            "~PP"
            "~PR"
            "~PS"
            "~RO"
            "~SD18"
            "~TA10"
            "^XA^PW260^LL120^FO10,40^A0N,24,24^FDTILDE JOB^FS^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_thirteenth_batch_mirror_print_flips_output(self):
        zpl = "^XA^PW80^LL60~PMY^FO0,0^GB10,10,10^FS^XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())
        gray = image.convert("L")

        self.assertEqual(report.warnings, [])
        self.assertLess(gray.getpixel((75, 5)), 128)
        self.assertGreater(gray.getpixel((0, 0)), 128)

    def test_fourteenth_batch_acknowledges_host_status_queries(self):
        zpl = (
            "~DGR:DOT.GRF,1,1,80"
            "~WC"
            "~WQstatus"
            "^XA^PW320^LL140"
            "^DFR:FMT.ZPL^FO10,10^GB20,20,2^FS^XZ"
            "^XA"
            "^HFR:FMT.ZPL"
            "^HGR:DOT.GRF"
            "^HH"
            "^HT"
            "^HV1"
            "^HW"
            "^HYR:DOT.GRF"
            "^HZ"
            "^FO10,55^A0N,24,24^FDHOST QUERY^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_fifteenth_batch_transfers_objects_and_acknowledges_network_metadata(self):
        zpl = (
            "~DGR:DOT.GRF,1,1,80"
            "~HI"
            "~HQES"
            "~NC"
            "^XA^PW320^LL140"
            "^LF"
            "^TOR:DOT.GRF,R:COPIED.GRF"
            "^WD"
            "^KCclient-id"
            "^NB"
            "^NCW"
            "^NDip,mask,gateway"
            "^FO10,10^XGR:COPIED.GRF^FS"
            "^FO10,55^A0N,24,24^FDNETWORK META^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.downloaded_graphics, 1)
        self.assertEqual(report.rendered_graphics, 1)
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_sixteenth_batch_acknowledges_network_metadata(self):
        zpl = (
            "~NR"
            "~NT"
            "^XA^PW340^LL140"
            "^NI123"
            "^NNpublic,private"
            "^NP1,2"
            "^NSwired"
            "^NTsmtp.example.com"
            "^NW30"
            "^WAantenna"
            "^WEwep"
            "^FO10,55^A0N,24,24^FDNET META 2^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_seventeenth_batch_acknowledges_wireless_and_rfid_metadata(self):
        zpl = (
            "~WL"
            "~WR"
            "~HL"
            "^XA^PW360^LL140"
            "^WLleap"
            "^WPpassword"
            "^WRrate"
            "^WSradio"
            "^WXsecurity"
            "^HL"
            "^HRcalibrate"
            "^FO10,55^A0N,24,24^FDWIRELESS RFID^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eighteenth_batch_acknowledges_job_status_and_download_metadata(self):
        zpl = (
            "~HS"
            "~DBR:FONT.FNT,metadata"
            "~DER:ENC.TBL,metadata"
            "~DSR:SCALABLE.FNT,metadata"
            "~DTR:BOUNDED.TTF,metadata"
            "~DUR:UNBOUNDED.TTF,metadata"
            "^XA^PW320^LL140"
            "^PQ2,0,1,Y"
            "^CD;"
            "^FO10,55^A0N,24,24^FDBATCH 18^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eighteenth_batch_changed_caret_prefix_parses_following_commands(self):
        zpl = "^XA^CC!!FO10,25!A0N,24,24!FDNEW CARET!FS!XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions(width_inches=2, height_inches=1))

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_eighteenth_batch_tilde_changed_caret_prefix_parses_following_commands(self):
        zpl = "^XA~CC!!FO10,25!A0N,24,24!FDTILDE CARET!FS!XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions(width_inches=2, height_inches=1))

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_final_batch_label_top_offsets_following_fields(self):
        zpl = "^XA^PW120^LL100^LT40^FO10,0^GB20,20,20^FS^XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(image.getpixel((15, 10)), (255, 255, 255))
        self.assertEqual(image.getpixel((15, 45)), (0, 0, 0))

    def test_final_batch_changed_tilde_prefix_parses_following_commands(self):
        zpl = "^XA~CT!!CC##FO10,25#A0N,24,24#FDNEW TILDE#FS#XZ"

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions(width_inches=2, height_inches=1))

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)

    def test_final_batch_acknowledges_units_delimiter_and_rfid_commands(self):
        zpl = (
            "~CD;"
            "^XA^PW360^LL160"
            "^MUD,203,203"
            "^RB96,8,3,3,20,24,38"
            "^RFW,H,0,12,1"
            "^RL1,A"
            "^RS8,0,22,4,N"
            "^RUH,0,8"
            "^RW20,20"
            "^FO10,65^A0N,24,24^FDRFID FINAL^FS"
            "^XZ"
        )

        image, report = render_zpl_bytes(zpl.encode("ascii"), RenderOptions())

        self.assertEqual(report.warnings, [])
        self.assertEqual(report.rendered_text_fields, 1)
        self.assertGreater(count_dark_pixels(image), 100)


if __name__ == "__main__":
    unittest.main()

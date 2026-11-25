import unittest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from scripts.build_assets import (
    IST,
    xml_escape,
    strip_dangerous_html,
    format_relative_and_absolute,
    compute_content_hash,
    validate_svg_content,
    replace_marker,
    generate_last_scan_stamp,
)

class TestProfileAutomation(unittest.TestCase):

    def test_ist_conversion_across_midnight(self):
        # 18:45 UTC on Nov 25 is 00:15 IST on Nov 26
        dt_utc = datetime(2026, 11, 25, 18, 45, 0, tzinfo=timezone.utc)
        dt_ist = dt_utc.astimezone(IST)
        self.assertEqual(dt_ist.day, 26)
        self.assertEqual(dt_ist.hour, 0)
        self.assertEqual(dt_ist.minute, 15)
        self.assertEqual(dt_ist.strftime("%d %b %Y, %H:%M IST"), "26 Nov 2026, 00:15 IST")

    def test_relative_and_absolute_formatting(self):
        now_utc = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        # 30 mins ago
        dt_30m = now_utc - timedelta(minutes=30)
        res_30m = format_relative_and_absolute(dt_30m, now_utc)
        self.assertIn("30 mins ago", res_30m)
        self.assertIn("IST", res_30m)

        # 3 days ago
        dt_3d = now_utc - timedelta(days=3)
        res_3d = format_relative_and_absolute(dt_3d, now_utc)
        self.assertIn("3 days ago", res_3d)
        self.assertIn("IST", res_3d)

    def test_hostile_input_sanitization(self):
        hostile_svg_text = "</text><script>alert('XSS')</script>"
        escaped = xml_escape(hostile_svg_text)
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)

        hostile_md = "Test <script src='bad.js'></script> payload onload=alert(1)"
        cleaned = strip_dangerous_html(hostile_md)
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("onload", cleaned)

    def test_validation_gate_blocks_hostile_svg(self):
        clean_svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="10" cy="10" r="5"/></svg>'
        self.assertTrue(validate_svg_content(clean_svg, "clean.svg"))

        script_svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assertFalse(validate_svg_content(script_svg, "script.svg"))

        onload_svg = '<svg xmlns="http://www.w3.org/2000/svg"><circle onload="alert(1)"/></svg>'
        self.assertFalse(validate_svg_content(onload_svg, "onload.svg"))

        ext_href_svg = '<svg xmlns="http://www.w3.org/2000/svg"><a href="https://malicious.com"><circle/></a></svg>'
        self.assertFalse(validate_svg_content(ext_href_svg, "ext_href.svg"))

    def test_marker_replacement_and_errors(self):
        doc = "Header\n<!--auto:test:start-->\nold\n<!--auto:test:end-->\nFooter"
        new_doc = replace_marker(doc, "test", "NEW_CONTENT")
        self.assertIn("NEW_CONTENT", new_doc)
        self.assertNotIn("old", new_doc)
        self.assertIn("Header", new_doc)
        self.assertIn("Footer", new_doc)

        # Missing marker
        with self.assertRaises(ValueError):
            replace_marker(doc, "nonexistent", "CONTENT")

        # Duplicate marker
        dup_doc = doc + "\n<!--auto:test:start-->\n<!--auto:test:end-->"
        with self.assertRaises(ValueError):
            replace_marker(dup_doc, "test", "CONTENT")

    def test_staleness_stamp_after_72_hours(self):
        now_utc = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
        fresh_sync = now_utc - timedelta(hours=2)
        fresh_stamp = generate_last_scan_stamp(fresh_sync, now_utc)
        self.assertIn("System telemetry last synced:", fresh_stamp)

        stale_sync = now_utc - timedelta(hours=75)
        stale_stamp = generate_last_scan_stamp(stale_sync, now_utc)
        self.assertIn("Telemetry notice: Data as of", stale_stamp)
        self.assertIn("(automated sync delayed)", stale_stamp)

    def test_hash_stability(self):
        content = "Deterministic SVG content"
        h1 = compute_content_hash(content)
        h2 = compute_content_hash(content)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 8)

if __name__ == "__main__":
    unittest.main()

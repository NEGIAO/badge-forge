"""取数层单测：JSON 路径、格式化、SSRF 拒绝、图片类型嗅探。

不发起真实网络请求：只覆盖纯函数与「应当被拒绝」的路径。
"""

import unittest

from core.source import (
    SourceError,
    assert_public_url,
    extract,
    format_value,
    normalize_icon,
    sniff_image_type,
)


class SourceErrorTests(unittest.TestCase):
    def test_defaults_to_message_as_public(self):
        exc = SourceError("上游返回 HTTP 404", stage="fetch")
        self.assertEqual(exc.stage, "fetch")
        self.assertEqual(exc.public, "上游返回 HTTP 404")

    def test_public_can_be_sanitized(self):
        exc = SourceError("拒绝请求内网地址：10.0.0.5", stage="guard", public="目标地址指向内网或保留地址，已拒绝")
        self.assertIn("10.0.0.5", str(exc))       # 日志里保留细节
        self.assertNotIn("10.0.0.5", exc.public)  # 对外脱敏

    def test_guard_errors_are_tagged(self):
        with self.assertRaises(SourceError) as ctx:
            assert_public_url("http://127.0.0.1/")
        self.assertEqual(ctx.exception.stage, "guard")
        self.assertNotIn("127.0.0.1", ctx.exception.public)


class ExtractTests(unittest.TestCase):
    PAYLOAD = {
        "metrics": {"todayUsers": 11, "totalPv": 79782},
        "list": [{"name": "a"}, {"name": "b"}],
        "message": "79,782",
    }

    def test_nested_key(self):
        self.assertEqual(extract(self.PAYLOAD, "$.metrics.todayUsers"), 11)

    def test_without_dollar(self):
        self.assertEqual(extract(self.PAYLOAD, "metrics.totalPv"), 79782)

    def test_array_index(self):
        self.assertEqual(extract(self.PAYLOAD, "$.list[1].name"), "b")
        self.assertEqual(extract(self.PAYLOAD, "$.list[-1].name"), "b")

    def test_root_path_returns_payload(self):
        self.assertEqual(extract(self.PAYLOAD, "$"), self.PAYLOAD)

    def test_missing_key_raises(self):
        with self.assertRaises(SourceError):
            extract(self.PAYLOAD, "$.metrics.nope")

    def test_index_out_of_range_raises(self):
        with self.assertRaises(SourceError):
            extract(self.PAYLOAD, "$.list[9]")


class FormatTests(unittest.TestCase):
    def test_thousands(self):
        self.assertEqual(format_value(79782), "79,782")

    def test_plain(self):
        self.assertEqual(format_value(79782, "plain"), "79782")

    def test_compact_chinese_units(self):
        self.assertEqual(format_value(79782, "compact"), "7.98万")
        self.assertEqual(format_value(120000000, "compact"), "1.20亿")

    def test_time(self):
        self.assertEqual(format_value("2026-10-28T11:21:20.488+08:00", "time"), "10-28 11:21")
        self.assertEqual(format_value("not-a-time", "time"), "n/a")

    def test_none(self):
        self.assertEqual(format_value(None), "n/a")

    def test_boolean(self):
        self.assertEqual(format_value(True), "yes")

    def test_long_text_truncated(self):
        self.assertEqual(len(format_value("x" * 200)), 48)


class SsrfGuardTests(unittest.TestCase):
    def test_rejects_non_http_scheme(self):
        with self.assertRaises(SourceError):
            assert_public_url("ftp://example.com/x")

    def test_rejects_loopback(self):
        with self.assertRaises(SourceError):
            assert_public_url("http://127.0.0.1/")

    def test_rejects_private_range(self):
        with self.assertRaises(SourceError):
            assert_public_url("http://10.0.0.1/")

    def test_rejects_metadata_endpoint(self):
        with self.assertRaises(SourceError):
            assert_public_url("http://169.254.169.254/latest/meta-data/")

    def test_allowlist_blocks_other_hosts(self):
        with self.assertRaises(SourceError):
            assert_public_url("https://api.github.com/x", {"api.negiao.cn"})

    def test_rejects_missing_host(self):
        with self.assertRaises(SourceError):
            assert_public_url("http:///nohost")


class ImageSniffTests(unittest.TestCase):
    def test_png(self):
        self.assertEqual(sniff_image_type(b"\x89PNG\r\n\x1a\n...."), "image/png")

    def test_svg(self):
        self.assertEqual(sniff_image_type(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'), "image/svg+xml")

    def test_svg_with_xml_declaration(self):
        body = b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"></svg>'
        self.assertEqual(sniff_image_type(body), "image/svg+xml")

    def test_html_is_rejected(self):
        self.assertIsNone(sniff_image_type(b"<!doctype html><html></html>", "text/html"))


class NormalizeIconTests(unittest.TestCase):
    def test_injects_white_fill_on_bare_svg(self):
        body = b'<svg role="img" viewBox="0 0 24 24"><path d="M0 0"/></svg>'
        out = normalize_icon(body, "image/svg+xml")
        self.assertIn(b'fill="#ffffff"', out)

    def test_keeps_explicit_root_fill(self):
        body = b'<svg fill="#ff0000"><path d="M0 0"/></svg>'
        self.assertEqual(normalize_icon(body, "image/svg+xml"), body)

    def test_replaces_current_color(self):
        body = b'<svg><path fill="currentColor" d="M0 0"/></svg>'
        out = normalize_icon(body, "image/svg+xml")
        self.assertNotIn(b"currentColor", out)
        self.assertIn(b'fill="#ffffff"', out)

    def test_non_svg_untouched(self):
        body = b"\x89PNG\r\n\x1a\nrest"
        self.assertEqual(normalize_icon(body, "image/png"), body)


if __name__ == "__main__":
    unittest.main()

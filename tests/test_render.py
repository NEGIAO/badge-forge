"""渲染层单测：列宽、转义、配色白名单、高度缩放。

运行：python -m unittest discover -s tests -t .
"""

import unittest

from core.render import BadgeSpec, measure, normalize_color, render


class MeasureTests(unittest.TestCase):
    def test_cjk_is_full_width(self):
        self.assertAlmostEqual(measure("今日访客", 11), 44)

    def test_ascii_digits(self):
        self.assertAlmostEqual(measure("11", 11), 14)

    def test_scales_with_font_size(self):
        self.assertAlmostEqual(measure("11", 22), 28)


class ColorTests(unittest.TestCase):
    def test_named_color(self):
        self.assertEqual(normalize_color("green", "#000"), "#4caf50")

    def test_hex_passthrough(self):
        self.assertEqual(normalize_color("#4CAF50", "#000"), "#4caf50")
        self.assertEqual(normalize_color("#abc", "#000"), "#abc")

    def test_injection_falls_back(self):
        self.assertEqual(normalize_color('"><script>alert(1)</script>', "#555555"), "#555555")
        self.assertEqual(normalize_color("rgb(1,2,3)", "#555555"), "#555555")

    def test_empty_uses_fallback(self):
        self.assertEqual(normalize_color("", "#123456"), "#123456")
        self.assertEqual(normalize_color(None, "#123456"), "#123456")


class RenderTests(unittest.TestCase):
    def test_two_columns(self):
        svg = render(BadgeSpec(value="11", text="今日访客"))
        self.assertIn('width="78"', svg)  # 54 + 24
        self.assertIn('height="20"', svg)
        self.assertNotIn("<image", svg)

    def test_three_columns_with_icon(self):
        spec = BadgeSpec(value="11", text="今日访客", icon="data:image/svg+xml;base64,AAA")
        svg = render(spec)
        self.assertIn('width="98"', svg)  # 20 + 54 + 24
        self.assertIn("<image", svg)
        self.assertIn('preserveAspectRatio="xMidYMid meet"', svg)

    def test_empty_text_skips_column(self):
        svg = render(BadgeSpec(value="11", text=""))
        self.assertIn('width="24"', svg)

    def test_empty_value_skips_column(self):
        svg = render(BadgeSpec(value="", text="WebGIS"))
        self.assertIn('width="54"', svg)  # 只剩文字列
        self.assertNotIn(">n/a<", svg)

    def test_all_empty_degrades_to_placeholder(self):
        svg = render(BadgeSpec(value="", text=""))
        self.assertIn("n/a", svg)
        self.assertNotIn('width="0"', svg)

    def test_text_is_escaped(self):
        svg = render(BadgeSpec(value="<b>&", text="a<b"))
        self.assertIn("a&lt;b", svg)
        self.assertIn("&lt;b&gt;&amp;", svg)
        self.assertNotIn("<b>", svg)

    def test_gloss_toggle(self):
        self.assertIn("linearGradient", render(BadgeSpec(value="1", gloss=True)))
        self.assertNotIn("linearGradient", render(BadgeSpec(value="1", gloss=False)))

    def test_height_scales_font(self):
        svg = render(BadgeSpec(value="1", height=28))
        self.assertIn('height="28"', svg)
        self.assertIn('font-size="15"', svg)  # round(28 * 0.55)

    def test_custom_colors_applied(self):
        svg = render(BadgeSpec(value="1", text="x", value_bg="#007ec6", text_bg="#333333"))
        self.assertIn('fill="#007ec6"', svg)
        self.assertIn('fill="#333333"', svg)


if __name__ == "__main__":
    unittest.main()

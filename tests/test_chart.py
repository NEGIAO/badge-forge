"""活动图渲染单测：几何、配色、标题、边界值。

运行：python -m unittest discover -s tests -t .
"""

import unittest

from core.chart import (
    DEFAULT_THEME,
    build_theme,
    normalize_hex,
    render_activity_graph,
    render_notice,
)

SERIES = [{"date": str(day), "count": day % 5} for day in range(1, 32)]


class ColorTests(unittest.TestCase):
    def test_accepts_with_and_without_hash(self):
        self.assertEqual(normalize_hex("#70a5fd", "000000"), "70a5fd")
        self.assertEqual(normalize_hex("70a5fd", "000000"), "70a5fd")
        self.assertEqual(normalize_hex("#abc", "000000"), "abc")

    def test_rejects_injection(self):
        self.assertEqual(normalize_hex('" onload="alert(1)', "000000"), "000000")
        self.assertEqual(normalize_hex("rgb(1,2,3)", "000000"), "000000")

    def test_color_override_applies_to_all_roles(self):
        theme = build_theme(color="#ff0000")
        self.assertEqual(theme["color"], "ff0000")
        self.assertEqual(theme["line"], "ff0000")
        self.assertEqual(theme["title"], "ff0000")
        self.assertEqual(theme["area"], "ff0000")
        self.assertEqual(theme["bg"], DEFAULT_THEME["bg"])  # 未覆盖的背景保持默认

    def test_bg_override(self):
        self.assertEqual(build_theme(bg="#ffffff")["bg"], "ffffff")


class RenderTests(unittest.TestCase):
    def test_basic_dimensions(self):
        svg = render_activity_graph("NEGIAO", SERIES)
        self.assertIn('width="1200"', svg)
        self.assertIn('height="420"', svg)
        self.assertIn("ct-line", svg)
        self.assertEqual(svg.count('class="ct-point"'), len(SERIES))

    def test_custom_width(self):
        svg = render_activity_graph("NEGIAO", SERIES, width=900)
        self.assertIn('width="900"', svg)

    def test_width_is_clamped(self):
        self.assertIn('width="800"', render_activity_graph("x", SERIES, width=100))
        self.assertIn('width="1600"', render_activity_graph("x", SERIES, width=9999))

    def test_default_title_uses_name(self):
        svg = render_activity_graph("NEGIAO", SERIES)
        self.assertIn("NEGIAO&#x27;s Contribution Graph", svg)

    def test_custom_title(self):
        svg = render_activity_graph("NEGIAO", SERIES, title="我的提交")
        self.assertIn("我的提交", svg)

    def test_hide_title(self):
        svg = render_activity_graph("NEGIAO", SERIES, hide_title=True)
        self.assertNotIn("Contribution Graph", svg)

    def test_title_is_escaped(self):
        svg = render_activity_graph("x", SERIES, title="<script>alert(1)</script>")
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_empty_series_degrades(self):
        svg = render_activity_graph("x", [])
        self.assertIn("ct-line", svg)
        self.assertEqual(svg.count('class="ct-point"'), 1)

    def test_single_point_centered(self):
        svg = render_activity_graph("x", [{"date": "1", "count": 3}])
        self.assertIn("ct-point", svg)


class StyleParamTests(unittest.TestCase):
    def test_line_width_applied(self):
        svg = render_activity_graph("x", SERIES, line_width=9)
        self.assertIn("stroke-width: 9px", svg)

    def test_line_width_clamped(self):
        self.assertIn("stroke-width: 12px", render_activity_graph("x", SERIES, line_width=99))
        self.assertIn("stroke-width: 1px", render_activity_graph("x", SERIES, line_width=-5))

    def test_dot_size_zero_hides_points(self):
        svg = render_activity_graph("x", SERIES, dot_size=0)
        self.assertIn("stroke-width: 0px", svg)

    def test_dot_size_clamped(self):
        self.assertIn("stroke-width: 24px", render_activity_graph("x", SERIES, dot_size=99))

    def test_area_and_grid_opacity(self):
        svg = render_activity_graph("x", SERIES, area_opacity=0.5, grid_opacity=0.8)
        self.assertIn("fill-opacity: 0.5", svg)
        self.assertIn("stroke-opacity: 0.8", svg)

    def test_opacity_clamped(self):
        svg = render_activity_graph("x", SERIES, area_opacity=5, grid_opacity=-1)
        self.assertIn("fill-opacity: 1", svg)
        self.assertIn("stroke-opacity: 0", svg)


class NoticeTests(unittest.TestCase):
    def test_notice_contains_text(self):
        svg = render_notice("未配置 GITHUB_TOKEN", "详见 README")
        self.assertIn("未配置 GITHUB_TOKEN", svg)
        self.assertIn("详见 README", svg)

    def test_notice_escapes(self):
        svg = render_notice("<b>x</b>", "")
        self.assertNotIn("<b>x</b>", svg)


if __name__ == "__main__":
    unittest.main()

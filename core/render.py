"""SVG 徽章渲染：三列（图标 / 文字 / 数值）。

设计原则
--------
1. **零依赖**：不引 `badgen` / `badge-maker`，避免为一个 20px 高的图拉进字体宽度表（约 900KB）。
2. **可控宽度**：三列各自按内容定宽，图标列固定为正方形，文字列与数值列按字符类别估算。
3. **不信任任何输入**：颜色只接受白名单命名色或 `#rgb/#rrggbb`，文字一律做 XML 转义 ——
   徽章内容来自 URL 查询参数，是典型的注入面。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

# ── 颜色 ──────────────────────────────────────────────────────────────────────

NAMED_COLORS: dict[str, str] = {
    "green": "#4caf50",
    "blue": "#007ec6",
    "grey": "#555555",
    "gray": "#555555",
    "red": "#e05d44",
    "orange": "#fe7d37",
    "yellow": "#dfb317",
    "purple": "#9f4dbc",
    "pink": "#e75480",
    "teal": "#1d9e75",
    "lightgrey": "#9f9f9f",
    "lightgray": "#9f9f9f",
    "darkgrey": "#3f3f3f",
    "black": "#333333",
    "white": "#ffffff",
    "transparent": "none",
}

_HEX_RE = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$")


def normalize_color(raw: str | None, fallback: str) -> str:
    """把颜色参数规整成可安全写入 SVG 的值。

    参数：raw —— 请求原值（命名色 / #rgb / #rrggbb / transparent）；fallback —— 非法时的兜底。
    返回：合法的颜色字面量，或 fallback。
    核心逻辑：先查命名色表，再用正则白名单校验；其余一律丢弃，防止 `"`、`<` 等字符逃逸出属性。
    """
    if not raw:
        return fallback
    value = str(raw).strip().lower()
    if value in NAMED_COLORS:
        return NAMED_COLORS[value]
    if _HEX_RE.match(value):
        return value
    return fallback


# ── 文本宽度估算 ──────────────────────────────────────────────────────────────

_NARROW = set("iljtfrI.,:;'|!()[]{}/\\ ")
_WIDE = set("mwMW@%")


def measure(text: str, font_size: float = 11) -> float:
    """估算文本在 Verdana 下的像素宽度（以 11px 为基准按比例缩放）。

    参数：text —— 待测文本；font_size —— 实际字号。
    返回：宽度（float，未含内边距）。
    核心逻辑：按字符类别给经验值 —— CJK/全角 11px、窄字符 4px、宽字符 10px、大写 8px、其余 7px。
    误差约 ±2px：20px 高的徽章上肉眼不可辨，换来的是零字体表依赖。
    """
    width = 0.0
    for char in text:
        code = ord(char)
        if code > 0x2E80:  # CJK、全角标点、emoji 之外的宽字符
            width += 11
        elif char in _NARROW:
            width += 4
        elif char in _WIDE:
            width += 10
        elif char.isupper():
            width += 8
        else:
            width += 7
    return width * font_size / 11


# ── 规格与渲染 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BadgeSpec:
    """一次渲染的全部输入。字段全部为「已规整」后的值，渲染函数不再做校验。"""

    value: str
    text: str = ""
    icon: str | None = None  # 已内联的 data URI；None 表示不渲染图标列
    icon_bg: str = "#555555"
    text_bg: str = "#555555"
    value_bg: str = "#4caf50"
    height: int = 20
    radius: int = 3
    gloss: bool = True


def render(spec: BadgeSpec) -> str:
    """渲染三列徽章 SVG。

    参数：spec —— 已规整的 BadgeSpec。
    返回：SVG 字符串。
    核心逻辑：
      1. 三列各自定宽：图标列 = 高度（正方形），文字列/数值列 = 文本宽 + 内边距；
      2. 用 clipPath 一次性裁出圆角，避免逐列算圆角；
      3. 叠一层 18% 白色渐变做顶部高光（shields 的 flat 风格就是这么来的）。
    """
    height = spec.height
    font_size = max(9, round(height * 0.55))
    pad = max(6, round(height * 0.5))  # 每列左右内边距之和
    baseline = round(height / 2 + font_size * 0.35)

    icon_width = height if spec.icon else 0
    text_width = (measure(spec.text, font_size) + pad) if spec.text else 0
    value_width = (measure(spec.value, font_size) + pad) if spec.value else 0
    total = icon_width + text_width + value_width
    if total <= 0:  # 三列全空：退化为一枚最小占位徽章，避免产出 0 宽 SVG
        spec = BadgeSpec(
            value="n/a",
            height=spec.height,
            radius=spec.radius,
            gloss=spec.gloss,
        )
        value_width = measure("n/a", font_size) + pad
        total = value_width

    if spec.text and spec.value:
        label = escape(f"{spec.text}: {spec.value}")
    elif spec.text:
        label = escape(spec.text)
    else:
        label = escape(spec.value)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{round(total)}" height="{height}" viewBox="0 0 {round(total)} {height}" '
        f'role="img" aria-label="{label}">',
        f"<title>{label}</title>",
        f'<clipPath id="r"><rect width="{round(total)}" height="{height}" rx="{spec.radius}"/></clipPath>',
    ]

    if spec.gloss:
        parts.append(
            '<linearGradient id="g" x2="0" y2="100%">'
            '<stop offset="0" stop-color="#ffffff" stop-opacity=".18"/>'
            '<stop offset="1" stop-opacity=".18"/>'
            "</linearGradient>"
        )

    # 底色块
    parts.append('<g clip-path="url(#r)">')
    cursor = 0.0
    if spec.icon:
        parts.append(f'<rect width="{icon_width}" height="{height}" fill="{spec.icon_bg}"/>')
        cursor += icon_width
    if spec.text:
        parts.append(
            f'<rect x="{round(cursor)}" width="{round(text_width)}" height="{height}" '
            f'fill="{spec.text_bg}"/>'
        )
        cursor += text_width
    if spec.value:
        parts.append(
            f'<rect x="{round(cursor)}" width="{round(value_width)}" height="{height}" '
            f'fill="{spec.value_bg}"/>'
        )
    if spec.gloss:
        parts.append(f'<rect width="{round(total)}" height="{height}" fill="url(#g)"/>')
    parts.append("</g>")

    # 图标
    if spec.icon:
        inset = max(2, round(height * 0.15))
        size = height - inset * 2
        parts.append(
            f'<image x="{inset}" y="{inset}" width="{size}" height="{size}" '
            f'href="{spec.icon}" xlink:href="{spec.icon}" preserveAspectRatio="xMidYMid meet"/>'
        )

    # 文字
    parts.append(
        f'<g fill="#ffffff" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'font-size="{font_size}" text-anchor="middle">'
    )
    if spec.text:
        parts.append(
            f'<text x="{round(icon_width + text_width / 2, 1)}" y="{baseline}">{escape(spec.text)}</text>'
        )
    if spec.value:
        parts.append(
            f'<text x="{round(icon_width + text_width + value_width / 2, 1)}" y="{baseline}">'
            f"{escape(spec.value)}</text>"
        )
    parts.append("</g>")
    parts.append("</svg>")

    return "".join(parts)

"""活动图渲染：移植自 `negiao-api` Worker 的 `renderActivityGraphSvg` + `chartistCss`。

保持与线上 Worker 版本一致的几何（1200×420、Chartist 的 padding/axis）与配色（tokyo-night），
这样同一份数据在两处渲染出来的图可以叠在一起看不出差别。

与原实现的两处**有意的**差异：
1. 标题用 `<text>` 而不是 `<foreignObject>` + XHTML `<h1>` —— foreignObject 在部分渲染器
   （Safari 的 `<img>` 上下文、resvg/rsvg 之类的转换器）里不生效，标题会整个消失；
2. 缓存键由调用方负责，这里只做纯渲染（原实现把标题漏在缓存键外，改了标题仍命中旧图）。
"""

from __future__ import annotations

import math
import re
from html import escape

# tokyo-night（与 Worker 的 TOKYO_NIGHT 同值）
DEFAULT_THEME = {
    "bg": "1a1b27",
    "border": "0000",  # 透明边框：原实现 hide_border=true
    "color": "70a5fd",
    "title": "70a5fd",
    "line": "70a5fd",
    "point": "a9b1d6",
    "area": "70a5fd",
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# 画布与 Chartist 布局常量（与 Worker 逐项对应，勿随意改动）
PAD_TOP = 80
PAD_RIGHT = 50
PAD_BOTTOM = 20
PAD_LEFT = 20
AXIS_Y_OFFSET = 70
AXIS_X_OFFSET = 50
DEFAULT_WIDTH = 1200
DEFAULT_HEIGHT = 420


def normalize_hex(raw: str | None, fallback: str) -> str:
    """把颜色规整成不带 `#` 的十六进制串（SVG 里用 `#${value}` 拼接）。

    只接受 3 位或 6 位十六进制，其余一律回落 —— 颜色来自查询参数，是注入面。
    """
    if not raw:
        return fallback
    match = _HEX_RE.match(str(raw).strip())
    return match.group(1) if match else fallback


def build_theme(color: str = "", bg: str = "", point: str = "") -> dict[str, str]:
    """按用户覆盖项生成配色表。`color` 同时作用于标题/折线/轴/面积，语义最直观。"""
    theme = dict(DEFAULT_THEME)
    primary = normalize_hex(color, DEFAULT_THEME["color"])
    if color:
        theme["color"] = theme["title"] = theme["line"] = theme["area"] = primary
    theme["bg"] = normalize_hex(bg, DEFAULT_THEME["bg"])
    theme["point"] = normalize_hex(point, DEFAULT_THEME["point"])
    return theme


def _chartist_css(
    theme: dict[str, str],
    *,
    line_width: float,
    dot_size: float,
    area_opacity: float,
    grid_opacity: float,
) -> str:
    """Chartist 的标签/网格/折线样式（含入场动画），与 Worker 版本同源。

    线宽、点径、面积与网格的透明度全部由参数注入 —— 这些是最常被调整的视觉项。
    """
    return f"""
    .ct-label {{
      fill: #{theme['color']};
      color: #{theme['color']};
      font-size: .75rem;
      line-height: 1;
    }}
    .ct-grid-background, .ct-line {{ fill: none; }}
    .ct-label.ct-horizontal.ct-start {{ text-anchor: start; }}
    .ct-label.ct-horizontal.ct-end {{ text-anchor: start; }}
    .ct-label.ct-vertical.ct-start {{ text-anchor: end; }}
    .ct-label.ct-vertical.ct-end {{ text-anchor: start; }}
    .ct-grid {{
      stroke: #{theme['color']};
      stroke-width: 1px;
      stroke-opacity: {grid_opacity:g};
      stroke-dasharray: 2px;
    }}
    .ct-point {{
      stroke-width: {dot_size:g}px;
      stroke-linecap: round;
      stroke: #{theme['point']};
      animation: blink 1s ease-in-out forwards;
    }}
    .ct-line {{
      stroke-width: {line_width:g}px;
      stroke-dasharray: 5000;
      stroke-dashoffset: 5000;
      stroke: #{theme['line']};
      animation: dash 5s ease-in-out forwards;
    }}
    .ct-area {{ stroke: none; fill-opacity: {area_opacity:g}; }}
    .ct-series-a .ct-area {{ fill: #{theme['area']}; }}
    @keyframes blink {{
      from {{ opacity: 0; transform: translateX(-20px); }}
      to {{ opacity: 1; transform: translateX(0); }}
    }}
    @keyframes dash {{
      to {{ stroke-dashoffset: 0; }}
    }}"""


def render_activity_graph(
    name: str,
    contributions: list[dict],
    *,
    title: str = "",
    hide_title: bool = False,
    color: str = "",
    bg: str = "",
    point: str = "",
    width: int = DEFAULT_WIDTH,
    line_width: float = 4.0,
    dot_size: float = 10.0,
    area_opacity: float = 0.1,
    grid_opacity: float = 0.3,
) -> str:
    """渲染 GitHub 贡献活动图。

    参数：
      name          —— 用户显示名，用于默认标题
      contributions —— `[{"date": "1", "count": 3}, ...]`，date 为当月日号（官方 X 轴就是这么显示的）
      title         —— 自定义标题；为空时用 `{name}'s Contribution Graph`
      hide_title    —— 隐藏标题（画布高度不变，只是不画）
      color / bg / point —— 配色覆盖，取值 `#rrggbb` 或 `rrggbb`
      width         —— 画布宽度 800~1600，高度固定 420
      line_width    —— 折线宽度 1~12 px
      dot_size      —— 数据点直径 0~24 px（0 = 不画点）
      area_opacity  —— 面积填充透明度 0~1
      grid_opacity  —— 网格线透明度 0~1
    返回：SVG 字符串。
    """
    width = max(800, min(1600, int(width)))
    height = DEFAULT_HEIGHT
    line_width = max(1.0, min(12.0, float(line_width)))
    dot_size = max(0.0, min(24.0, float(dot_size)))
    area_opacity = max(0.0, min(1.0, float(area_opacity)))
    grid_opacity = max(0.0, min(1.0, float(grid_opacity)))
    theme = build_theme(color, bg, point)

    values = [int(item.get("count") or 0) for item in contributions]
    labels = [str(item.get("date", "")) for item in contributions]
    if not values:
        values, labels = [0], ["1"]
    count = len(values)
    high = max(max(values), 1)

    # 绘图区（与 Chartist 默认布局对齐）
    x0 = PAD_LEFT + AXIS_Y_OFFSET
    x1 = width - PAD_RIGHT
    y_top = PAD_TOP
    y_bot = height - PAD_BOTTOM - AXIS_X_OFFSET
    plot_w = x1 - x0
    plot_h = y_bot - y_top

    def x_at(index: int) -> float:
        return (x0 + x1) / 2 if count == 1 else x0 + (index / (count - 1)) * plot_w

    def y_at(value: float) -> float:
        return y_bot - (value / high) * plot_h

    heading = "" if hide_title else (title.strip() or f"{name or 'user'}'s Contribution Graph")

    # 网格：水平 5 档 + 每个数据点一条竖线
    h_grids: list[str] = []
    y_labels: list[str] = []
    for step in range(5):
        ratio = step / 4
        y = y_bot - ratio * plot_h
        y_labels.append(
            f'<text x="{x0 - 10:.2f}" y="{y + 5:.2f}" class="ct-label ct-vertical ct-start">'
            f"{round(ratio * high)}</text>"
        )
        h_grids.append(f'<line x1="{x0}" y1="{y:.2f}" x2="{x1}" y2="{y:.2f}" class="ct-grid"/>')

    v_grids: list[str] = []
    x_labels: list[str] = []
    stride = max(1, math.ceil(count / 31))
    for index in range(count):
        x = x_at(index)
        v_grids.append(f'<line x1="{x:.2f}" y1="{y_top}" x2="{x:.2f}" y2="{y_bot}" class="ct-grid"/>')
        # 官方 X 轴标签很密，隔点显示更清晰（仍接近 chartist 全量标签的观感）
        if count <= 35 or index % stride == 0:
            x_labels.append(
                f'<text x="{x:.2f}" y="{y_bot + 28:.2f}" class="ct-label ct-horizontal ct-end">'
                f"{escape(labels[index])}</text>"
            )

    line_d = " ".join(
        f"{'M' if index == 0 else 'L'}{x_at(index):.2f},{y_at(value):.2f}"
        for index, value in enumerate(values)
    )
    area_d = f"{line_d} L{x_at(count - 1):.2f},{y_bot} L{x_at(0):.2f},{y_bot} Z"
    points = "\n      ".join(
        f'<line x1="{x_at(i):.2f}" y1="{y_at(v):.2f}" x2="{x_at(i):.2f}" y2="{y_at(v):.2f}" '
        'class="ct-point"/>'
        for i, v in enumerate(values)
    )

    heading_el = (
        f'<text x="{width / 2:.0f}" y="48" text-anchor="middle" fill="#{theme["title"]}" '
        f'font-family="\'Segoe UI\', Ubuntu, sans-serif" font-size="20" font-weight="600">'
        f"{escape(heading)}</text>"
        if heading
        else ""
    )

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg">
    <rect id="cardBg" x="0" y="0" rx="0" height="100%" width="100%" fill="#{theme['bg']}" fill-opacity="1" stroke-opacity="1" style="stroke:#{theme['border']}; stroke-width:1;"/>
    <style>
      svg {{ font: 600 18px 'Segoe UI', Ubuntu, sans-serif; user-select: none; }}
      {_chartist_css(theme, line_width=line_width, dot_size=dot_size,
                     area_opacity=area_opacity, grid_opacity=grid_opacity)}
    </style>
    {heading_el}
    <g class="ct-grids">
      {"".join(h_grids)}
      {"".join(v_grids)}
    </g>
    <g class="ct-labels">
      <text x="{(x0 + x1) / 2:.2f}" y="{height - 8}" text-anchor="middle" class="ct-label">Days</text>
      <text x="{PAD_LEFT + 8}" y="{PAD_TOP - 12}" class="ct-label">Contributions</text>
      {"".join(y_labels)}
      {"".join(x_labels)}
    </g>
    <g class="ct-series ct-series-a">
      <path d="{area_d}" class="ct-area"/>
      <path d="{line_d}" class="ct-line"/>
    </g>
    <g class="ct-series-a">
      {points}
    </g>
  </svg>"""


def render_notice(heading: str, detail: str = "", width: int = DEFAULT_WIDTH) -> str:
    """渲染一张说明性 SVG（如「未配置 GITHUB_TOKEN」）。

    为什么不用 JSON 报错：这个端点主要被 `<img>` 引用，JSON 在页面上就是一张破图；
    画一张带文字的图，读者至少知道发生了什么。
    """
    width = max(400, min(1600, int(width)))
    height = 120
    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
    <rect width="100%" height="100%" fill="#1a1b27" rx="6"/>
    <text x="{width / 2:.0f}" y="52" text-anchor="middle" fill="#70a5fd" font-family="'Segoe UI', Ubuntu, sans-serif" font-size="18" font-weight="600">{escape(heading)}</text>
    <text x="{width / 2:.0f}" y="80" text-anchor="middle" fill="#a9b1d6" font-family="'Segoe UI', Ubuntu, sans-serif" font-size="13">{escape(detail)}</text>
  </svg>"""

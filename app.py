"""badge-forge —— 三列 SVG 徽章生成服务（图标 / 文字 / 数据）。

对外只有两类入口：
  GET /badge   生成 SVG（可被 <img> 直接引用）
  GET /        配置页（可视化拼参数、复制链接）

服务端职责刻意保持很小：**取数 → 取值 → 拼 SVG**，不存业务状态、不落库、不做鉴权。
这让它易于自托管，也便于别人 fork 后改样式。
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config
from core.cache import TTLCache
from core.chart import render_activity_graph, render_notice
from core.github import GithubError, fetch_contributions
from core.render import BadgeSpec, normalize_color, render
from core.source import (
    SourceError,
    fetch_and_extract,
    fetch_badge_value,
    fetch_bytes,
    format_value,
    normalize_icon,
    sniff_image_type,
    to_data_uri,
)

VERSION = "1.0.0"
WEB_DIR = Path(__file__).resolve().parent / "web"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("badge-forge")

app = FastAPI(
    title="badge-forge",
    version=VERSION,
    # 关掉自带的 /docs 与 /redoc，改在 /api/ 下自建：
    # 默认的 docs 会用**根路径** /openapi.json，一旦应用挂在 nginx 的 /badge/ 子路径下，
    # 浏览器就会去请求站点根的 /openapi.json（被静态 root 吃掉）→ Swagger 报 404。
    # 把 schema 与文档都收到 /api/ 下，且文档页用相对地址引用 schema，
    # 这样根路径部署与子路径部署都能正确解析。
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
)
# 徽章走 <img>，不需要凭据；开放 CORS 让配置页可在任意域名下调试
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/api/docs", include_in_schema=False)
async def api_docs():
    """Swagger UI。

    `openapi_url` 必须是**相对地址**：文档页在 `/api/docs`，相对解析到 `/api/openapi.json`；
    挂在 nginx 的 `/badge/` 下时自然变成 `/badge/api/openapi.json`，两种部署都不用改配置。
    默认 favicon 指向 FastAPI 的 CDN，这里换成本地图标（`../favicon.ico`），断网也能开。
    """
    return get_swagger_ui_html(
        openapi_url="openapi.json",
        title=f"badge-forge {VERSION} · API",
        swagger_favicon_url="../favicon.ico",
    )


_value_cache = TTLCache(config.CACHE_MAXSIZE)
_icon_cache = TTLCache(256)
_svg_cache = TTLCache(config.CACHE_MAXSIZE)
_chart_data_cache = TTLCache(256)  # GitHub 贡献序列：一天只变一次，6h 足够

PRESETS = [
    {
        "name": "51.la 今日访客",
        "text": "今日访客",
        "data": "https://api.negiao.cn/api/51la",
        "query": "$.metrics.todayUsers",
        "rbg": "green",
    },
    {
        "name": "GitHub Stars",
        "text": "Stars",
        # 走自有 Worker 而不是 api.github.com：国内直连 GitHub 常 ConnectTimeout，
        # 且未认证 API 只有 60 次/小时的额度，做示例源不可靠。
        "data": "https://api.negiao.cn/api/stats",
        "query": "$.stars",
        "icon": "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/github.svg",
        "rbg": "blue",
    },
    {
        "name": "数据更新时间",
        "text": "更新于",
        "data": "https://api.negiao.cn/api/51la",
        "query": "$.updatedAt",
        "format": "time",
        "rbg": "grey",
    },
    {
        "name": "纯图标 + 文字（无数据）",
        "text": "WebGIS",
        "data": "",
        "icon": "https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/cloudflare.svg",
        "rbg": "orange",
    },
]


def _int_param(raw: str | None, default: int, low: int, high: int) -> int:
    """把查询参数转成受限整数：非法值回落默认，超界夹到边界（徽章不该因参数写错就 422）。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _truthy(raw: str | None, default: bool = True) -> bool:
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


async def _resolve_icon(url: str) -> str | None:
    """抓取并内联图标。失败返回 None（整列省略，徽章仍可渲染）。"""
    if not url:
        return None
    cached = _icon_cache.get(url)
    if cached is not None:
        ok, value = cached
        return value if ok else None

    try:
        body, content_type = await fetch_bytes(
            url,
            timeout=config.ICON_TIMEOUT,
            max_bytes=config.ICON_MAX_BYTES,
            allowed_hosts=config.ALLOWED_HOSTS or None,
            accept="image/*",
        )
        mime = sniff_image_type(body, content_type)
        if not mime:
            raise SourceError("图标不是受支持的图片类型")
        uri = to_data_uri(normalize_icon(body, mime), mime)
        _icon_cache.set(url, (True, uri), config.ICON_CACHE_TTL)
        return uri
    except Exception as exc:  # 图标失败不应让整个徽章失败
        logger.warning("icon failed %s: %s", url, exc)
        _icon_cache.set(url, (False, None), 60)  # 失败只缓存 60s，避免反复打上游
        return None


async def _resolve_value(data_url: str, query: str, fmt: str) -> str:
    """取数并缓存。失败**不缓存**，让下一次请求立即重试。"""
    key = f"{data_url}|{query}|{fmt}"
    cached = _value_cache.get(key)
    if cached is not None:
        return cached
    value = await fetch_badge_value(
        data_url,
        query,
        fmt,
        timeout=config.DATA_TIMEOUT,
        max_bytes=config.DATA_MAX_BYTES,
        allowed_hosts=config.ALLOWED_HOSTS or None,
    )
    _value_cache.set(key, value, config.DATA_CACHE_TTL)
    return value


@app.get("/badge")
@app.get("/svg")
async def badge(
    request: Request,
    text: str = "",
    data: str = "",
    icon: str = "",
    query: str = "",
    format: str = "comma",
    ibg: str = "",
    lbg: str = "",
    rbg: str = "",
    h: str = "",
    r: str = "",
    gloss: str = "",
) -> Response:
    """渲染徽章 SVG。

    参数（全部可选，除 `data` 之外都可缺省）：
      text   中间文字            data   数据 URL（取不到值时右侧显示 n/a）
      icon   左侧图标 URL        query  JSON 路径，如 `$.metrics.todayUsers`
      format 数值格式 comma|plain|compact|time
      ibg/lbg/rbg  图标列/文字列/数值列底色（命名色或 #rgb/#rrggbb）
      h      高度 16~32          r      圆角 0~h/2      gloss  0 关闭顶部高光
    返回：`image/svg+xml`。取数失败时仍返回 200 + `n/a`，避免 README 出现破图。
    """
    height = _int_param(h, config.DEFAULT_HEIGHT, 16, 32)
    radius = _int_param(r, config.DEFAULT_RADIUS, 0, height // 2)
    fmt = format if format in ("comma", "plain", "compact", "time") else "comma"
    left = text.strip()[:40]

    status = "ok"
    detail = ""
    icon_uri = await _resolve_icon(icon.strip()) if icon.strip() else None

    if data.strip():
        try:
            value = await _resolve_value(data.strip(), query.strip(), fmt)
        except SourceError as exc:
            value, status, detail = "n/a", "error", exc.public
            logger.warning("value failed %s: %s", data.strip(), exc)
        except Exception as exc:  # 兜底：任何意外都降级为 n/a，不让 <img> 挂掉
            value, status, detail = "n/a", "error", str(exc)
            logger.exception("value unexpected error %s", data.strip())
    else:
        # 未提供 data：整列省略（徽章退化为「图标 + 文字」），而不是显示一个假的 n/a
        value = ""

    spec = BadgeSpec(
        value=value,
        text=left,
        icon=icon_uri,
        icon_bg=normalize_color(ibg, "#555555"),
        text_bg=normalize_color(lbg, "#555555"),
        value_bg=normalize_color(rbg, "#4caf50"),
        height=height,
        radius=radius,
        gloss=_truthy(gloss),
    )
    svg = render(spec)

    etag = 'W/"' + hashlib.sha1(svg.encode("utf-8")).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    cacheable = status == "ok"
    headers = {
        "ETag": etag,
        "X-Badge-Status": status,
        "Cache-Control": (
            f"public, max-age={config.SVG_CACHE_TTL}, s-maxage={config.SVG_CACHE_TTL}"
            if cacheable
            else "no-store"
        ),
    }
    if cacheable:
        headers["CDN-Cache-Control"] = f"max-age={config.SVG_CACHE_TTL}"
    if detail and config.DEBUG_ERRORS:
        headers["X-Badge-Error"] = detail[:200]

    return Response(content=svg, media_type="image/svg+xml; charset=utf-8", headers=headers)


@app.get("/activity-graph")
@app.get("/api/activity-graph.svg")
async def activity_graph(
    request: Request,
    username: str = "",
    days: str = "",
    hide_title: str = "",
    custom_title: str = "",
    color: str = "",
    bg: str = "",
    point: str = "",
    width: str = "",
    line: str = "",
    dot: str = "",
    area: str = "",
    grid: str = "",
) -> Response:
    """渲染 GitHub 贡献活动图（移植自 negiao-api Worker 的同名端点）。

    参数：username（必填）、days（7~90，默认 31）、hide_title、custom_title、
          color / bg / point（`#rrggbb` 或 `rrggbb`）、width（800~1600，默认 1200）、
          line（线宽 1~12）、dot（点径 0~24，0 = 不画点）、area / grid（透明度百分比 0~100）。
    返回：`image/svg+xml`。取数失败或未配 Token 时返回**说明性 SVG**（仍 200），
          因为该端点主要被 `<img>` 引用，返回 JSON 只会变成一张破图。
    """
    name = username.strip()
    span = _int_param(days, 31, 7, 90)
    canvas = _int_param(width, 1200, 800, 1600)
    title = custom_title.strip()[:60]
    plain_title = _truthy(hide_title, default=False)
    line_width = _int_param(line, 4, 1, 12)
    dot_size = _int_param(dot, 10, 0, 24)
    area_opacity = _int_param(area, 10, 0, 100) / 100
    grid_opacity = _int_param(grid, 30, 0, 100) / 100

    if not name:
        return _notice_response("缺少 username 参数", "示例：/activity-graph?username=NEGIAO&days=31")

    if not config.GITHUB_TOKEN:
        return _notice_response(
            "未配置 GITHUB_TOKEN",
            "在 /etc/badge-forge.env 设置只读 PAT 后重启服务（详见 README）",
        )

    cache_key = (
        f"graph|{name.lower()}|{span}|{canvas}|{int(plain_title)}|{title}|"
        f"{color}|{bg}|{point}|{line_width}|{dot_size}|{area_opacity}|{grid_opacity}"
    )
    cached = _svg_cache.get(cache_key)
    if cached is not None:
        return Response(
            content=cached,
            media_type="image/svg+xml; charset=utf-8",
            headers={"X-Cache-Status": "HIT", "Cache-Control": f"public, max-age={config.CHART_SVG_CACHE_TTL}"},
        )

    try:
        payload = _chart_data_cache.get(f"{name.lower()}|{span}")
        if payload is None:
            payload = await fetch_contributions(name, span, config.GITHUB_TOKEN, timeout=config.GITHUB_TIMEOUT)
            _chart_data_cache.set(f"{name.lower()}|{span}", payload, config.CHART_DATA_CACHE_TTL)
    except GithubError as exc:
        logger.warning("activity graph failed %s: %s", name, exc)
        return _notice_response("活动图取数失败", exc.public)

    svg = render_activity_graph(
        payload["name"],
        payload["contributions"],
        title=title,
        hide_title=plain_title,
        color=color,
        bg=bg,
        point=point,
        width=canvas,
        line_width=line_width,
        dot_size=dot_size,
        area_opacity=area_opacity,
        grid_opacity=grid_opacity,
    )
    _svg_cache.set(cache_key, svg, config.CHART_SVG_CACHE_TTL)

    etag = 'W/"' + hashlib.sha1(svg.encode("utf-8")).hexdigest()[:20] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={
            "ETag": etag,
            "X-Cache-Status": "MISS",
            "Cache-Control": f"public, max-age={config.CHART_SVG_CACHE_TTL}, s-maxage={config.CHART_SVG_CACHE_TTL}",
            "CDN-Cache-Control": f"max-age={config.CHART_SVG_CACHE_TTL}",
        },
    )


def _notice_response(heading: str, detail: str) -> Response:
    """说明性 SVG：不缓存，让配置修好后立刻生效。"""
    return Response(
        content=render_notice(heading, detail),
        media_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Cache-Status": "ERROR"},
    )


@app.get("/api/config")
async def api_config() -> JSONResponse:
    """配置页需要的元信息：默认值、边界、预设、当前实例限制。"""
    return JSONResponse(
        {
            "version": VERSION,
            "defaults": {
                "height": config.DEFAULT_HEIGHT,
                "radius": config.DEFAULT_RADIUS,
                "format": "comma",
                "ibg": "#555555",
                "lbg": "#555555",
                "rbg": "#4caf50",
            },
            "limits": {"height": [16, 32], "radius": [0, 16], "text": 40},
            "publicBaseUrl": config.PUBLIC_BASE_URL,
            "allowedHosts": sorted(config.ALLOWED_HOSTS),
            "presets": PRESETS,
            # 活动图需要只读 PAT；没配时前端把该 tab 标成不可用，而不是让用户撞一张报错图
            "github": {
                "enabled": bool(config.GITHUB_TOKEN),
                "chartDefaults": {
                    "username": "NEGIAO",
                    "days": 31,
                    "width": 1200,
                    "color": "#70a5fd",
                    "bg": "#1a1b27",
                    "point": "#a9b1d6",
                },
                "chartLimits": {"days": [7, 90], "width": [800, 1600]},
            },
        }
    )


@app.get("/api/explain")
async def api_explain(data: str = "", query: str = "", format: str = "") -> JSONResponse:
    """取值诊断：告诉配置页「取到了什么」或「卡在哪一步」。

    参数：data —— 数据 URL；query —— JSON 路径；format —— 数值格式（仅影响返回的 value）。
    返回：`{ok, stage, ...}`。成功带 `raw / type / value / ms`，失败带 `reason`。
    核心逻辑：只回**可公开**的原因（`SourceError.public`）—— 内网 IP 之类只进日志，
    否则这个接口就成了内网探测的探针。
    """
    target = data.strip()
    if not target:
        return JSONResponse({"ok": False, "stage": "input", "reason": "缺少 data 参数"}, status_code=400)

    fmt = format if format in ("comma", "plain", "compact", "time") else "comma"
    started = time.perf_counter()
    try:
        raw, _ = await fetch_and_extract(
            target,
            query.strip(),
            timeout=config.DATA_TIMEOUT,
            max_bytes=config.DATA_MAX_BYTES,
            allowed_hosts=config.ALLOWED_HOSTS or None,
        )
    except SourceError as exc:
        logger.info("explain failed %s: %s", target, exc)
        return JSONResponse({"ok": False, "stage": exc.stage, "reason": exc.public})
    except Exception:
        logger.exception("explain unexpected error %s", target)
        return JSONResponse({"ok": False, "stage": "fetch", "reason": "取数时发生意外错误"})

    # 结构化值不直接回传（可能是整棵 JSON），只给类型与规模
    preview = raw
    if isinstance(raw, (dict, list)):
        preview = f"<{type(raw).__name__} · {len(raw)} 项>"
    elif isinstance(raw, str) and len(raw) > 80:
        preview = raw[:80] + "…"

    return JSONResponse(
        {
            "ok": True,
            "stage": "ok",
            "raw": preview,
            "type": type(raw).__name__,
            "value": format_value(raw, fmt),
            "ms": round((time.perf_counter() - started) * 1000),
        }
    )


@app.get("/health")
async def health() -> JSONResponse:
    """健康检查：进程存活 + 缓存规模（供容器探针与运维观察）。"""
    return JSONResponse(
        {
            "status": "ok",
            "version": VERSION,
            "cache": {"value": len(_value_cache), "icon": len(_icon_cache), "svg": len(_svg_cache)},
        }
    )


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    """极简 favicon：省一次 404 日志。"""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="7" fill="#4caf50"/>'
        '<path d="M9 16.5l4.5 4.5L23 11" fill="none" stroke="#fff" '
        'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


# 静态页放在最后挂载：/badge 与 /api/* 已先注册，不会被吞掉
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")

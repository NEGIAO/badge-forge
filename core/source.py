"""数据源抓取：SSRF 防护 + JSON 路径取值 + 数值格式化。

为什么需要这一层
----------------
徽章的「数值」来自 `?data=<任意 URL>`，由**服务端**去请求。如果不加约束，这个端点就是
一个开放代理：可以打 `127.0.0.1`、`10.0.0.0/8`、`169.254.169.254`（云元数据）等内网地址。
本模块的职责就是在发请求前把这些地址挡掉，并限制耗时与响应体积。

残余风险（如实说明）
--------------------
「解析域名 → 校验 IP → 再发起请求」之间存在 DNS rebinding 的时间窗。要彻底关闭它需要
在 TLS 握手层把 SNI 与连接目标解耦（自定义 resolver + 固定 IP），复杂度远超本服务收益。
因此这里采取与 shields.io 同级的做法：每次请求（含每一跳重定向）都重新校验，
配合默认 5s 超时与 256KB 上限；对更高安全要求的部署，可用 `BADGE_ALLOWED_HOSTS` 收敛到白名单。
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "badge-forge/1.0 (+https://github.com/NEGIAO/badge-forge)"
MAX_TEXT_LEN = 48


class SourceError(Exception):
    """取数失败。

    message 进服务端日志（可含内网 IP 等细节）；
    public 是**可以回给调用方**的版本 —— 配置页靠它告诉用户「到底哪一步错了」。
    两者分开，是为了既能排错、又不把内网信息变成探测探针。
    """

    def __init__(self, message: str, stage: str = "fetch", public: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage  # guard | fetch | parse | extract
        self.public = public or message


# ── SSRF 防护 ─────────────────────────────────────────────────────────────────


def _is_public_ip(ip: str) -> bool:
    """判断地址是否可对外请求。私有、回环、链路本地、组播、保留段一律拒绝。"""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_url(url: str, allowed_hosts: set[str] | None = None) -> None:
    """校验 URL 可以安全请求：协议、主机白名单、解析出的所有 IP。

    参数：url —— 待校验地址；allowed_hosts —— 非空时只允许这些主机名。
    返回：None（不通过则抛 SourceError）。
    核心逻辑：解析出**全部** A/AAAA 记录并逐一校验 —— 只查第一条会被「一条公网 + 一条内网」
    的解析结果绕过。
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SourceError("仅支持 http/https", stage="guard", public="只支持 http/https 地址")
    host = parts.hostname
    if not host:
        raise SourceError("URL 缺少主机名", stage="guard", public="URL 缺少主机名")
    if allowed_hosts and host not in allowed_hosts:
        raise SourceError(f"主机 {host} 不在白名单内", stage="guard", public=f"主机 {host} 不在白名单内")

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceError(f"域名解析失败：{host}", stage="guard", public=f"域名解析失败：{host}") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise SourceError(f"域名无可用地址：{host}", stage="guard", public=f"域名无可用地址：{host}")
    for address in addresses:
        if not _is_public_ip(address):
            raise SourceError(
                f"拒绝请求内网地址：{address}",
                stage="guard",
                public="目标地址指向内网或保留地址，已拒绝",
            )


# ── 抓取 ──────────────────────────────────────────────────────────────────────


async def fetch_bytes(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    allowed_hosts: set[str] | None = None,
    max_redirects: int = 3,
    accept: str = "*/*",
) -> tuple[bytes, str]:
    """抓取 URL 内容，带 SSRF 校验、重定向逐跳复检、体积上限。

    参数：url —— 目标地址；timeout —— 单跳超时（秒）；max_bytes —— 响应体上限；
          allowed_hosts —— 主机白名单；max_redirects —— 最多跟随跳数。
    返回：`(body, content_type)`。
    核心逻辑：关闭 httpx 的自动重定向，自己逐跳处理 —— 否则第一跳合法、第二跳指向
    169.254.169.254 时就被绕过了。
    """
    current = url
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout),
        headers=headers,
    ) as client:
        for _ in range(max_redirects + 1):
            # getaddrinfo 是阻塞调用，放到线程池，避免堵住事件循环
            await asyncio.to_thread(assert_public_url, current, allowed_hosts)
            try:
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise SourceError("重定向缺少 Location", stage="fetch", public="上游重定向缺少 Location 头")
                        current = urljoin(current, location)
                        continue
                    if response.status_code >= 400:
                        raise SourceError(
                            f"上游返回 HTTP {response.status_code}",
                            stage="fetch",
                            public=f"上游返回 HTTP {response.status_code}",
                        )

                    buffer = bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_bytes:
                            raise SourceError(
                                f"响应体超过 {max_bytes} 字节上限",
                                stage="fetch",
                                public=f"上游响应超过 {max_bytes // 1024}KB 上限",
                            )
                    return bytes(buffer), response.headers.get("content-type", "")
            except httpx.TimeoutException as exc:
                # 网络层的异常必须归类成 SourceError，否则会以「意外错误」冒到接口层
                raise SourceError(
                    f"上游超时：{current}（{exc.__class__.__name__}）",
                    stage="fetch",
                    public=f"上游响应超时（>{timeout:g}s）",
                ) from exc
            except httpx.HTTPError as exc:
                raise SourceError(
                    f"上游请求失败：{current}（{exc.__class__.__name__}: {exc}）",
                    stage="fetch",
                    public=f"无法连接上游（{exc.__class__.__name__}）",
                ) from exc

    raise SourceError("重定向次数过多", stage="fetch", public="重定向次数过多（超过 3 跳）")


# ── JSON 取值 ─────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"\.?([\w$@-]+)|\[(-?\d+)\]")


def extract(payload, path: str):
    """按简化 JSONPath 取值。

    参数：payload —— 已解析的 JSON；path —— 形如 `$.a.b`、`a.b`、`$.list[0].name`、`[0]`。
    返回：取到的值。
    核心逻辑：只支持「对象键 + 数组下标」，不支持过滤/通配 —— 覆盖 95% 的统计接口，
    且不会因为表达式求值引入风险。
    """
    if not path or path.strip() in ("", "$"):
        return payload
    expression = path.strip()
    if expression.startswith("$"):
        expression = expression[1:]

    current = payload
    for match in _TOKEN_RE.finditer(expression):
        key, index = match.group(1), match.group(2)
        if index is not None:
            position = int(index)
            if isinstance(current, list) and -len(current) <= position < len(current):
                current = current[position]
            else:
                raise SourceError(f"数组下标越界：{position}", stage="extract", public=f"数组下标越界：[{position}]")
        elif key:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                raise SourceError(f"缺少字段：{key}", stage="extract", public=f"响应里没有字段「{key}」")
    return current


def _pick_default(payload):
    """未指定 query 时的取值策略：优先 shields 契约字段，其次常见统计字段。"""
    if isinstance(payload, dict):
        for key in ("message", "value", "total", "count", "data"):
            if key in payload and not isinstance(payload[key], (dict, list)):
                return payload[key]
        raise SourceError(
            "未指定 query，且响应里没有可识别的数值字段",
            stage="extract",
            public="未指定 query，且响应里没有 message/value/total/count/data 字段",
        )
    return payload


# ── 格式化 ────────────────────────────────────────────────────────────────────

_TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")


def format_value(value, fmt: str = "comma") -> str:
    """把原始值转成徽章右侧文案。

    参数：value —— 任意 JSON 值；fmt —— `comma`（默认）/ `plain` / `compact` / `time`。
    返回：不超过 48 字符的字符串。
    核心逻辑：数字按千分位或中文单位（万/亿）压缩；ISO 时间截成 `MM-DD HH:mm`；
    布尔转 yes/no；其余一律按文本截断。
    """
    if value is None:
        return "n/a"

    if fmt == "time":
        match = _TIME_RE.match(str(value))
        return f"{match.group(2)}-{match.group(3)} {match.group(4)}:{match.group(5)}" if match else "n/a"

    if isinstance(value, bool):
        return "yes" if value else "no"

    if isinstance(value, (int, float)):
        if fmt == "plain":
            text = str(int(value)) if float(value).is_integer() else str(value)
        elif fmt == "compact":
            text = _compact(float(value))
        else:
            number = float(value)
            text = f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"
        return text

    text = " ".join(str(value).split())
    return text[:MAX_TEXT_LEN] or "n/a"


def _compact(number: float) -> str:
    """中文单位压缩：≥1 亿用「亿」，≥1 万用「万」，其余走千分位。"""
    absolute = abs(number)
    if absolute >= 1e8:
        return f"{number / 1e8:.2f}亿"
    if absolute >= 1e4:
        return f"{number / 1e4:.2f}万"
    return f"{int(number):,}" if float(number).is_integer() else f"{number:,.2f}"


async def fetch_and_extract(
    url: str,
    query: str,
    *,
    timeout: float,
    max_bytes: int,
    allowed_hosts: set[str] | None = None,
):
    """取数 + 解析 + 取值，返回 `(原始值, content_type)`。格式化交给调用方。

    拆出这一层是为了让 `/api/explain` 能同时拿到「原始值」与「失败阶段」——
    配置页据此告诉用户到底是网络、JSON 解析还是路径写错了。
    """
    body, content_type = await fetch_bytes(
        url,
        timeout=timeout,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        accept="application/json, text/plain;q=0.9, */*;q=0.8",
    )
    text = body.decode("utf-8", "replace").strip()
    if not text:
        raise SourceError("上游响应为空", stage="parse", public="上游响应为空（0 字节）")

    payload = None
    if "json" in content_type.lower() or text[:1] in ("{", "["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError("上游返回的内容不是合法 JSON", stage="parse", public="上游返回的内容不是合法 JSON") from exc

    if payload is None:
        # 纯文本源（如某些计数接口直接返回数字）
        return text, content_type

    return (extract(payload, query) if query else _pick_default(payload)), content_type


async def fetch_badge_value(
    url: str,
    query: str,
    fmt: str,
    *,
    timeout: float,
    max_bytes: int,
    allowed_hosts: set[str] | None = None,
) -> str:
    """取数 + 取值 + 格式化的组合入口。返回徽章右侧文案。"""
    raw, _ = await fetch_and_extract(
        url,
        query,
        timeout=timeout,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
    )
    return format_value(raw, fmt)


# ── 图标 ──────────────────────────────────────────────────────────────────────

_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(body: bytes, content_type: str = "") -> str | None:
    """识别图片 MIME。只认 SVG / PNG / JPEG / GIF / WEBP，其余一律拒绝（防止把 HTML 当图标内联）。"""
    for magic, mime in _IMAGE_MAGIC:
        if body.startswith(magic):
            return mime
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    head = body[:512].lstrip()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in body[:2048]):
        return "image/svg+xml"
    declared = content_type.split(";")[0].strip().lower()
    return declared if declared in {"image/svg+xml", "image/png", "image/jpeg", "image/gif", "image/webp"} else None


_SVG_ROOT_RE = re.compile(rb"<svg\b[^>]*>", re.IGNORECASE)


def normalize_icon(body: bytes, mime: str) -> bytes:
    """把单色 SVG 图标规整成「在深色底上可见」的样子。

    参数：body —— 原始字节；mime —— 嗅探出的类型。
    返回：处理后的字节（非 SVG 原样返回）。
    核心逻辑：Simple Icons 这类单色图标默认不带 fill（渲染为黑色），放到 #555 底上几乎看不见。
      ① 先把 `currentColor` 换成白色 —— 它在 <image> 上下文里解析不出主题色，只会是黑；
      ② 再给 `<svg>` 根节点补 `fill="#ffffff"`，让没有自身 fill 的 path 继承；
         自带 fill 的 path（多色品牌 logo）不受影响，仍然是原色。
    """
    if mime != "image/svg+xml":
        return body

    body = body.replace(b"currentColor", b"#ffffff")
    match = _SVG_ROOT_RE.search(body)
    if not match:
        return body

    root_tag = match.group(0)
    if b"fill=" in root_tag:
        return body  # 根节点已声明颜色，尊重原样

    patched = root_tag[:-1] + b' fill="#ffffff">'
    return body[: match.start()] + patched + body[match.end():]


def to_data_uri(body: bytes, mime: str) -> str:
    """把图片内联成 data URI。

    为什么内联而不是直接写外链：GitHub 会用 camo 代理 SVG，但**不会**重写 SVG 内部的
    外链图片 —— 许多图床禁止外链，徽章就会缺角。内联后 SVG 自包含，任何渲染器都能显示。
    """
    return f"data:{mime};base64,{base64.b64encode(body).decode('ascii')}"

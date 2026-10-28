"""配置：全部走环境变量，默认值即「开箱可用」。

命名沿用 negiao 系服务的习惯（与 tile-proxy 一致）：`服务名_配置名`，
布尔值用 `true/false`，端口默认避开已占用的 9002（tile-proxy）与 7860（WebGIS 后端）。
"""

from __future__ import annotations

import os


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


# 服务
PORT = _int("PORT", 9010)
HOST = _str("HOST", "0.0.0.0")
PUBLIC_BASE_URL = _str("BADGE_PUBLIC_BASE_URL", "").rstrip("/")

# 上游取数
DATA_TIMEOUT = _float("BADGE_DATA_TIMEOUT", 5.0)
DATA_MAX_BYTES = _int("BADGE_DATA_MAX_BYTES", 256 * 1024)
MAX_REDIRECTS = _int("BADGE_MAX_REDIRECTS", 3)

# 图标
ICON_TIMEOUT = _float("BADGE_ICON_TIMEOUT", 5.0)
ICON_MAX_BYTES = _int("BADGE_ICON_MAX_BYTES", 48 * 1024)

# 缓存（秒）
DATA_CACHE_TTL = _int("BADGE_DATA_CACHE_TTL", 300)
ICON_CACHE_TTL = _int("BADGE_ICON_CACHE_TTL", 3600)
SVG_CACHE_TTL = _int("BADGE_SVG_CACHE_TTL", 300)
CACHE_MAXSIZE = _int("BADGE_CACHE_MAXSIZE", 2048)

# 安全
# 非空时只允许这些主机名（逗号分隔）。对外开放实例建议收敛到自有域名。
ALLOWED_HOSTS = {h.strip() for h in _str("BADGE_ALLOWED_HOSTS", "").split(",") if h.strip()}
# 出错时是否把原因写进 X-Badge-Error 响应头（默认关闭，避免把内网信息回给调用方）
DEBUG_ERRORS = _bool("BADGE_DEBUG", False)

# 视觉默认值
DEFAULT_HEIGHT = _int("BADGE_DEFAULT_HEIGHT", 20)
DEFAULT_RADIUS = _int("BADGE_DEFAULT_RADIUS", 3)

# ── GitHub 活动图 ─────────────────────────────────────────────────────────────
# 只读 PAT：查询公开贡献日历，**不需要任何 scope**。留空则活动图不可用（徽章不受影响）。
# 生产环境请放 /etc/badge-forge.env（600），由 systemd EnvironmentFile 注入，不要写进仓库。
GITHUB_TOKEN = _str("GITHUB_TOKEN", "")
GITHUB_TIMEOUT = _float("BADGE_GITHUB_TIMEOUT", 8.0)
# 贡献数据一天只变一次，6 小时足够；SVG 同步缓存，避免重复渲染
CHART_DATA_CACHE_TTL = _int("BADGE_CHART_DATA_TTL", 6 * 3600)
CHART_SVG_CACHE_TTL = _int("BADGE_CHART_SVG_TTL", 6 * 3600)

"""GitHub 贡献数据获取（GraphQL）。

为什么必须要 token
------------------
GitHub 的 GraphQL 接口对**未认证**请求直接返回 403 —— REST 那套「匿名 60 次/小时」的额度
不适用于 GraphQL。所以活动图必须配一个 PAT。

权限最小化
----------
查询**公开用户**的贡献日历，只需要一个**不勾任何 scope**的 classic PAT；
fine-grained PAT 则只需勾 Public Repositories (read-only)。不需要 repo / workflow / 任何写权限。

缓存不在本模块：调用方按 `username + days` 做 6 小时缓存，避免刷 README 就打满 5000 点/小时的配额。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx

GRAPHQL_URL = "https://api.github.com/graphql"
USER_AGENT = "badge-forge/1.0 (+https://github.com/NEGIAO/badge-forge)"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

_QUERY = """
query userInfo($LOGIN: String!, $FROM: DateTime!, $TO: DateTime!) {
  user(login: $LOGIN) {
    name
    contributionsCollection(from: $FROM, to: $TO) {
      contributionCalendar {
        weeks {
          contributionDays { contributionCount date }
        }
      }
    }
  }
}"""


class GithubError(Exception):
    """取数失败。`public` 是可直接展示给调用方的版本。"""

    def __init__(self, message: str, public: str | None = None) -> None:
        super().__init__(message)
        self.public = public or message


def is_valid_username(username: str) -> bool:
    """GitHub 用户名规则：字母数字与连字符，1~39 位，不能以连字符开头结尾。"""
    return bool(_USERNAME_RE.match(username or ""))


async def fetch_contributions(
    username: str,
    days: int,
    token: str,
    *,
    timeout: float = 8.0,
) -> dict:
    """拉取某用户最近 `days` 天的贡献序列。

    参数：username —— GitHub 登录名；days —— 7~90；token —— 只读 PAT。
    返回：`{"name": 显示名, "contributions": [{"date": "1", "count": 3}, ...]}`。
    核心逻辑：GraphQL 一次取回整段日历（按周分组），摊平成天序列后**去掉最后一天为 0 的记录**
    （今天还没贡献），再从尾部截到 days —— 与官方图表的取数口径一致。
    """
    if not token:
        raise GithubError("missing GITHUB_TOKEN", "未配置 GITHUB_TOKEN，活动图不可用")
    if not is_valid_username(username):
        raise GithubError(f"invalid username: {username}", "用户名不合法（仅字母、数字与连字符）")

    days = max(7, min(90, int(days)))
    now = datetime.now(timezone.utc)
    variables = {
        "LOGIN": username,
        "FROM": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "TO": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.post(
                GRAPHQL_URL,
                json={"query": _QUERY, "variables": variables},
                headers={
                    "User-Agent": USER_AGENT,
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
    except httpx.TimeoutException as exc:
        raise GithubError(f"graphql timeout: {exc}", "连接 GitHub 超时") from exc
    except httpx.HTTPError as exc:
        raise GithubError(f"graphql request failed: {exc}", "无法连接 GitHub") from exc

    if response.status_code in (401, 403):
        raise GithubError(
            f"graphql -> HTTP {response.status_code}",
            "GitHub Token 无效、已过期或配额耗尽",
        )
    if response.status_code >= 400:
        raise GithubError(
            f"graphql -> HTTP {response.status_code}",
            f"GitHub 返回 HTTP {response.status_code}",
        )

    payload = response.json()
    if payload.get("errors"):
        message = str(payload["errors"][0].get("message", ""))[:120]
        raise GithubError(f"graphql errors: {message}", f"GitHub 返回错误：{message}")

    user = (payload.get("data") or {}).get("user")
    if not user:
        raise GithubError(f"user not found: {username}", f"找不到用户 {username}")

    weeks = ((user.get("contributionsCollection") or {}).get("contributionCalendar") or {}).get("weeks") or []
    series: list[dict] = []
    for week in weeks:
        for day in week.get("contributionDays") or []:
            date = day.get("date")
            if not date:
                continue
            series.append({"date": str(int(date[8:10])), "count": int(day.get("contributionCount") or 0)})

    # 与官方一致：去掉「今天还没贡献」的空尾巴，再从尾部截到 days
    if series and series[-1]["count"] == 0:
        series.pop()
    overflow = len(series) - days
    if overflow > 0:
        series = series[overflow:]

    return {
        "name": user.get("name") or username,
        "contributions": series or [{"date": "1", "count": 0}],
    }

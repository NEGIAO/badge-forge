"""进程内 TTL + LRU 缓存。

为什么不用 Redis：本服务是无状态、可水平复制的「渲染器」，缓存只是为了避免
每次 README 刷新都回源上游。进程内缓存足够，且让部署保持单容器、零外部依赖。
多副本时各自缓存，最坏情况是多回源几次，不影响正确性。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    """带过期时间与容量上限的键值缓存（LRU 淘汰）。"""

    def __init__(self, maxsize: int = 1024) -> None:
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Any | None:
        """取缓存。过期即删除并返回 None（调用方需自行区分「未命中」与「缓存了 None」）。"""
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        """写入并刷新顺序；超出容量时淘汰最久未用的条目。"""
        self._data[key] = (time.monotonic() + ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

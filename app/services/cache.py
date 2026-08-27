"""In-process TTL + LRU cache.

Scraping the same profile twice in a minute is wasted risk — every avoidable
Voyager call is one more data point toward the account being flagged. This
cache is deliberately process-local: it needs no external dependency, and a
Render restart simply starts cold.

For a multi-instance deployment, swap the implementation for Redis behind the
same ``get``/``set`` interface.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """Async-safe LRU cache with per-entry expiry."""

    def __init__(self, *, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max(1, max_entries)
        self._data: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    async def get(self, key: str) -> T | None:
        if not self.enabled:
            return None
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at < time.monotonic():
                del self._data[key]
                self._misses += 1
                return None
            self._data.move_to_end(key)
            self._hits += 1
            return entry.value

    async def set(self, key: str, value: T) -> None:
        if not self.enabled:
            return
        async with self._lock:
            self._data[key] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    async def invalidate(self, key: str) -> bool:
        async with self._lock:
            return self._data.pop(key, None) is not None

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "entries": len(self._data),
            "max_entries": self._max,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
        }

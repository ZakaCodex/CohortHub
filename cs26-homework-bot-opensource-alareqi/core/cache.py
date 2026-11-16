"""ذاكرة داخلية قصيرة الأمد تُبطَل فورًا عند تغيّر رقم الإصدار في جدول data_versions."""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Mapping, TypeVar

T = TypeVar("T")


class VersionedCache:
    def __init__(self, ttl: float = 300.0, max_items: int = 5000) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self._versions: dict[str, int] = {}
        self._items: dict[str, tuple[int, float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def update_versions(self, versions: Mapping[str, int]) -> list[str]:
        """يسجّل الإصدارات الجديدة ويعيد قائمة النطاقات التي تغيّرت."""
        changed = [s for s, v in versions.items() if self._versions.get(s) != v]
        for s in changed:
            self._versions[s] = versions[s]
        return changed

    def version(self, scope: str) -> int:
        return self._versions.get(scope, 0)

    def _valid(self, key: str, scope: str) -> tuple[bool, object | None]:
        hit = self._items.get(key)
        if hit and hit[0] == self.version(scope) and hit[1] > time.monotonic():
            return True, hit[2]
        return False, None

    async def get_or_load(self, key: str, scope: str, loader: Callable[[], Awaitable[T]]) -> T:
        ok, value = self._valid(key, scope)
        if ok:
            return value  # type: ignore[return-value]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:  # يمنع تحميل نفس المفتاح مرتين معًا
            ok, value = self._valid(key, scope)
            if ok:
                return value  # type: ignore[return-value]
            version_before = self.version(scope)
            loaded = await loader()
            if len(self._items) >= self.max_items:
                self._items.pop(next(iter(self._items)))
            self._items[key] = (version_before, time.monotonic() + self.ttl, loaded)
            return loaded

    def invalidate(self, key: str) -> None:
        self._items.pop(key, None)

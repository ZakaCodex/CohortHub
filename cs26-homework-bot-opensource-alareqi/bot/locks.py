"""قفل لكل مستخدم: يُنفَّذ تحديثات المستخدم الواحد بالتتابع (الملفات المتتالية، الأزرار السريعة)
بينما تبقى تحديثات المستخدمين المختلفين متوازية تمامًا، فلا سباقات على user_data ولا ردود مكررة."""
from __future__ import annotations

import asyncio
import functools
from typing import Any, Awaitable, Callable

_locks: dict[int, asyncio.Lock] = {}
_waiters: dict[int, int] = {}


def _uid(update: Any) -> int | None:
    user = getattr(update, "effective_user", None)
    if user is None and getattr(update, "poll_answer", None) is not None:
        user = update.poll_answer.user
    return getattr(user, "id", None)


def per_user(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(handler)
    async def wrapper(update: Any, context: Any, *args: Any, **kwargs: Any) -> Any:
        uid = _uid(update)
        if uid is None:
            return await handler(update, context, *args, **kwargs)
        lock = _locks.setdefault(uid, asyncio.Lock())
        _waiters[uid] = _waiters.get(uid, 0) + 1
        try:
            async with lock:
                return await handler(update, context, *args, **kwargs)
        finally:
            _waiters[uid] -= 1
            if _waiters[uid] <= 0:  # لا نُراكم أقفالًا لمستخدمين غير نشطين
                _waiters.pop(uid, None)
                _locks.pop(uid, None)

    return wrapper

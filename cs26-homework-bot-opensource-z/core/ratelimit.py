"""دلو رموز لإبقاء الإرسال تحت حدّ تيليجرام (~30 رسالة/ث): نستهدف 25."""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float = 25.0, burst: int = 25) -> None:
        self.rate, self.capacity = rate, float(burst)
        self._tokens, self._t = float(burst), time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._t) * self.rate)
                self._t = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)

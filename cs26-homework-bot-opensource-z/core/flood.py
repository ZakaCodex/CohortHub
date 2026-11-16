"""حارس الإغراق (flood) للبوت: يحدّ من سيل الطلبات من مستخدم واحد دون أن يضرّ بالاستخدام الطبيعي.

الحدود سخيّة عمدًا: الطالب قد يطلب أكثر من ١٠ ملفات في أقل من ١٠ ثوانٍ (ضغطات أزرار)،
وقد يرسل ألبومًا من ٢٠ ملفًا دفعة واحدة؛ لذلك ضغطات الأزرار لها حدّ أعلى من الرسائل،
والحدّ الافتراضي لا يُصيب إلا من يرسل عشرات الطلبات في نافذة قصيرة (سكربت/إغراق).
"""
from __future__ import annotations

import os
import time
from collections import deque
from typing import Callable

OK, DROP, NOTICE = "ok", "drop", "notice"


class FloodGuard:
    def __init__(
        self,
        msg_limit: int = 30,          # رسائل/ملفات واردة لكل مستخدم في النافذة (يتسع لألبوم ٢٠ ملفًا)
        cb_limit: int = 60,           # ضغطات أزرار لكل مستخدم في النافذة (طلب ملفات كثيرة بسرعة)
        window: float = 10.0,
        notice_gap: float = 30.0,     # لا نكرر رسالة التنبيه لنفس المستخدم قبل هذه المدة
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = {"msg": msg_limit, "cb": cb_limit}
        self.window, self.notice_gap, self._clock = window, notice_gap, clock
        self._hits: dict[tuple[int, str], deque[float]] = {}
        self._noticed: dict[int, float] = {}
        self._calls = 0

    @classmethod
    def from_env(cls) -> "FloodGuard":
        def num(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, "") or default)
            except ValueError:
                return default
        return cls(
            msg_limit=int(num("FLOOD_MSG_LIMIT", 30)),
            cb_limit=int(num("FLOOD_CB_LIMIT", 60)),
            window=num("FLOOD_WINDOW", 10.0),
        )

    def check(self, user_id: int, kind: str) -> str:
        """kind: 'msg' أو 'cb'. يعيد ok (مرّر) أو drop (تجاهل بصمت) أو notice (تجاهل + تنبيه واحد للمستخدم)."""
        now = self._clock()
        self._calls += 1
        if self._calls % 2000 == 0:
            self._prune(now)
        q = self._hits.setdefault((user_id, kind), deque())
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) < self.limits[kind]:
            q.append(now)
            return OK
        # الطلبات المرفوضة لا تُحتسب: تنتهي العقوبة تلقائيًا بمجرد أن يهدأ المستخدم
        last = self._noticed.get(user_id)
        if last is None or now - last >= self.notice_gap:
            self._noticed[user_id] = now
            return NOTICE
        return DROP

    def _prune(self, now: float) -> None:
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] > self.window]:
            del self._hits[key]
        for uid in [u for u, t in self._noticed.items() if now - t > self.notice_gap]:
            del self._noticed[uid]

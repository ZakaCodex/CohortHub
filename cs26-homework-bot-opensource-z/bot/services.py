"""خدمات مشتركة داخل البوت: القاعدة، الذاكرة المؤقتة المُبطَلة بالإصدارات، المجتمعات."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from telegram.ext import ContextTypes

from core.cache import VersionedCache
from core.config import load_settings
from core.store import Store

log = logging.getLogger("bot")
VERSION_POLL_SECONDS = 15


class Services:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.store = Store(self.settings.supabase_url, self.settings.supabase_service_key, self.settings.http_timeout)
        self.cache = VersionedCache(ttl=600)
        self.developer_contact = os.getenv("DEVELOPER_CONTACT", "").strip() or None
        self._watcher: asyncio.Task | None = None

    async def profile(self, user: Any, *, force: bool = False) -> dict[str, Any]:
        """ملف المستخدم (دور + مجموعة + صلاحيات) من الذاكرة، ويُبطَل فور تغيّر الأدوار أو الربط من التطبيق."""
        key = f"p:{user.id}"
        if force:
            self.cache.invalidate(key)
        else:
            ok, cached = self.cache._valid(key, "roles")
            if ok and isinstance(cached, dict) and (cached.get("user") or {}).get("kind") in ("new", "guest"):
                # لا نحتفظ بحالة new/guest في الذاكرة حتى يعمل الربط من داخل الـ Mini App فورًا عند العودة للبوت
                self.cache.invalidate(key)

        name = " ".join(p for p in (user.first_name, user.last_name) if p)

        async def load() -> dict[str, Any]:
            return await self.store.bootstrap(user.id, name, user.username)

        prof = await self.cache.get_or_load(key, "roles", load)
        if (prof.get("user") or {}).get("kind") == "new":
            self.cache.invalidate(key)
        return prof

    def forget(self, tg_id: int) -> None:
        self.cache.invalidate(f"p:{tg_id}")

    async def destination(self, key: str) -> dict[str, Any] | None:
        """مجتمع/topic مسجَّل (chat_id, topic_id) أو None."""
        async def load() -> dict[str, Any] | None:
            rows = await self.store.select("tg_destinations", {"select": "chat_id,topic_id", "key": f"eq.{key}"})
            return rows[0] if rows else None

        return await self.cache.get_or_load(f"d:{key}", "destinations", load)

    async def _watch_versions(self) -> None:
        while True:
            try:
                changed = self.cache.update_versions(await self.store.versions())
                if changed:
                    log.info("versions changed: %s", changed)
            except Exception as exc:  # noqa: BLE001 — المراقب لا يجوز أن يسقط
                log.warning("version poll failed: %s", exc)
            await asyncio.sleep(VERSION_POLL_SECONDS)

    def start_watcher(self) -> None:
        self._watcher = asyncio.create_task(self._watch_versions())

    async def close(self) -> None:
        if self._watcher:
            self._watcher.cancel()
        await self.store.aclose()


def svc(context: ContextTypes.DEFAULT_TYPE) -> Services:
    return context.application.bot_data["svc"]

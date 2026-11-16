"""إعدادات من متغيرات البيئة فقط. لا أسرار داخل الشيفرة."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _req(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"متغير البيئة {name} مطلوب")
    return value


@dataclass(frozen=True)
class Settings:
    bot_token: str
    supabase_url: str
    supabase_service_key: str
    web_app_url: str
    bot_username: str = ""
    # أثناء التطوير: توكن البوت التجريبي. عند الإطلاق نضع التوكن الأصلي هنا فقط.
    init_data_max_age: int = 24 * 3600
    http_timeout: float = 8.0


def load_settings(need_bot: bool = True) -> Settings:
    return Settings(
        bot_token=_req("BOT_TOKEN"),
        supabase_url=_req("SUPABASE_URL").rstrip("/"),
        supabase_service_key=_req("SUPABASE_SERVICE_KEY"),
        web_app_url=os.getenv("WEB_APP_URL", "").strip().rstrip("/"),
        bot_username=os.getenv("BOT_USERNAME", "").strip().lstrip("@"),
        init_data_max_age=int(os.getenv("INIT_DATA_MAX_AGE", str(24 * 3600))),
        http_timeout=float(os.getenv("HTTP_TIMEOUT", "8")),
    )

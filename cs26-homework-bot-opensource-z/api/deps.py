"""اعتماديات مشتركة لمسارات API."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Header, HTTPException, Request, Response  # noqa: E402

from core.config import load_settings  # noqa: E402
from core.store import Store  # noqa: E402
from core.tg_auth import AuthError, TgUser, validate_init_data  # noqa: E402

_settings = None
_store: Store | None = None
_resolved_bot_username: str | None = None


def settings():
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def store() -> Store:
    global _store
    if _store is None:
        s = settings()
        _store = Store(s.supabase_url, s.supabase_service_key, s.http_timeout)
    return _store


async def resolve_bot_username() -> str:
    """يعيد معرّف البوت من الإعدادات أو يجلبه تلقائيًا مرة واحدة عبر getMe إن لم يُضبط BOT_USERNAME."""
    global _resolved_bot_username
    if _resolved_bot_username:
        return _resolved_bot_username
    s = settings()
    if s.bot_username:
        _resolved_bot_username = s.bot_username.lstrip("@")
        return _resolved_bot_username
    if not s.bot_token:
        return ""
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(f"https://api.telegram.org/bot{s.bot_token}/getMe")
            if r.status_code == 200:
                uname = ((r.json().get("result") or {}).get("username") or "").strip()
                if uname:
                    _resolved_bot_username = uname
                    return uname
    except Exception:  # noqa: BLE001
        pass
    return ""


async def build_bot_link(payload: str) -> str | None:
    name = await resolve_bot_username()
    return f"https://t.me/{name}?start={payload}" if name else None


async def current_user(authorization: str = Header(default="")) -> TgUser:
    scheme, _, init_data = authorization.partition(" ")
    if scheme.lower() != "tma" or not init_data:
        raise HTTPException(401, "unauthorized")
    try:
        s = settings()
        return validate_init_data(init_data, s.bot_token, s.init_data_max_age)
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc


def etag_response(request: Request, payload: Any, *, cache_control: str = "private, no-cache") -> Response:
    """ETag من المحتوى؛ إن طابق If-None-Match نعيد 304 بحجم شبه صفري."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    tag = '"' + hashlib.sha1(body.encode()).hexdigest()[:20] + '"'
    headers = {"ETag": tag, "Cache-Control": cache_control, "Vary": "Authorization", "X-Server-Time": str(int(time.time()))}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)

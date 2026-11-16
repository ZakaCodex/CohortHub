"""التحقق من توقيع initData القادم من Telegram Mini App (في كل طلب)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class TgUser:
    id: int
    first_name: str
    last_name: str | None
    username: str | None

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p).strip()


def validate_init_data(init_data: str, bot_token: str, max_age: int = 24 * 3600,
                       now: float | None = None) -> TgUser:
    if not init_data or len(init_data) > 4096:
        raise AuthError("initData مفقود")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received:
        raise AuthError("hash مفقود")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        raise AuthError("توقيع غير صالح")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise AuthError("auth_date غير صالح") from exc
    if (now if now is not None else time.time()) - auth_date > max_age:
        raise AuthError("انتهت صلاحية الجلسة")

    try:
        user = json.loads(pairs["user"])
        return TgUser(
            id=int(user["id"]),
            first_name=str(user.get("first_name", "")),
            last_name=user.get("last_name"),
            username=user.get("username"),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthError("بيانات المستخدم غير صالحة") from exc

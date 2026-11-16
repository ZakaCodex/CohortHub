"""استخراج بيانات الملف من الرسالة وإرساله بحسب نوعه (بـ file_id دون إعادة رفع، مع دعم الـ caption والأزرار المرفقة)."""
from __future__ import annotations

from html import escape
from typing import Any

from core.text import fit_caption


def extract(msg: Any) -> dict[str, Any] | None:
    if msg.document:
        d = msg.document
        return {"media_kind": "document", "file_id": d.file_id, "unique_id": d.file_unique_id,
                "name": d.file_name, "mime": d.mime_type, "size": d.file_size}
    if msg.photo:
        p = msg.photo[-1]
        return {"media_kind": "photo", "file_id": p.file_id, "unique_id": p.file_unique_id,
                "name": None, "mime": "image/jpeg", "size": p.file_size}
    for kind in ("video", "audio", "voice", "animation", "video_note"):
        o = getattr(msg, kind, None)
        if o:
            return {"media_kind": kind, "file_id": o.file_id, "unique_id": o.file_unique_id,
                    "name": getattr(o, "file_name", None), "mime": getattr(o, "mime_type", None), "size": o.file_size}
    return None


def is_image(m: dict[str, Any]) -> bool:
    return m["media_kind"] == "photo" or (m.get("mime") or "").lower().startswith("image/")


async def send_by_kind(
    bot: Any,
    chat_id: int,
    kind: str,
    file_id: str,
    caption: str | None = None,
    thread: int | None = None,
    reply_markup: Any = None,
):
    kw: dict[str, Any] = {"chat_id": chat_id, "message_thread_id": thread}
    if reply_markup is not None:
        kw["reply_markup"] = reply_markup
    if kind == "text":
        # عنصر نصي/رابط من تسليم واجب: النص نفسه محفوظ في file_id؛ نرسله رسالة نصية (الـ caption رأس الرسالة)
        body = escape(str(file_id or "")[:3000])
        head = f"{caption}\n\n" if caption else ""
        return await bot.send_message(text=(head + body)[:4096], parse_mode="HTML", **kw)
    caption = fit_caption(caption)
    if kind == "video_note":
        return await bot.send_video_note(video_note=file_id, **kw)
    if caption is not None:
        kw["parse_mode"] = "HTML"
    senders = {"video": ("send_video", "video"), "audio": ("send_audio", "audio"), "voice": ("send_voice", "voice"),
               "animation": ("send_animation", "animation"), "photo": ("send_photo", "photo")}
    method, arg = senders.get(kind, ("send_document", "document"))
    return await getattr(bot, method)(**{arg: file_id}, caption=caption, **kw)

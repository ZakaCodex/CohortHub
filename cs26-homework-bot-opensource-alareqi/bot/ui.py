"""إرسال الرسائل (دائمًا رسائل جديدة) ولوحات الأزرار."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from core.permissions import can
from core.text import split_text


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def webapp_url(base_url: str, view: str = "") -> str:
    if not base_url or not view:
        return base_url
    parsed = urlparse(base_url)
    q = dict(parse_qsl(parsed.query))
    q["view"] = view
    return urlunparse(parsed._replace(query=urlencode(q)))


def webapp_btn(text: str, base_url: str, view: str = "") -> InlineKeyboardButton | None:
    if not base_url:
        return None
    return InlineKeyboardButton(text, web_app=WebAppInfo(url=webapp_url(base_url, view)))


def kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup | None:
    return InlineKeyboardMarkup(rows) if rows else None


async def say(bot: Any, chat_id: int, text: str, markup: InlineKeyboardMarkup | None = None,
              thread: int | None = None):
    """يرسل رسالة جديدة؛ إن تجاوزت 4096 حرفًا تُقسَّم تلقائيًا وتُرفق الأزرار بآخر جزء. يعيد آخر رسالة."""
    parts = split_text(text)
    msg = None
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        msg = await bot.send_message(chat_id, part, reply_markup=markup if last else None, parse_mode="HTML",
                                     disable_web_page_preview=True, message_thread_id=thread)
    return msg


def kb_entry() -> InlineKeyboardMarkup:
    return kb([[btn("🎓 أنا طالب في الدفعة", "start:link")], [btn("👀 دخول كزائر", "start:guest")]])


def kb_menu(web_url: str, kind: str, perms: list[dict]) -> InlineKeyboardMarkup | None:
    """القائمة الرئيسية في البوت: الملفات والواجبات واختبارات MCQ + فتح التطبيق الشامل."""
    rows: list[list[InlineKeyboardButton]] = []
    if web_url:
        rows.append([InlineKeyboardButton("📱 فتح التطبيق الشامل (Mini App)", web_app=WebAppInfo(url=web_url))])
    second = []
    if can(perms, "files.view"):
        second.append(btn("📚 الملفات", "m:files"))
    if can(perms, "assignments.submit"):
        second.append(btn("📝 الواجبات", "m:asg"))
    if second:
        rows.append(second)
    if can(perms, "mcq.take"):
        rows.append([btn("🧪 اختبارات MCQ", "m:mcq")])
    if kind == "guest":  # الزائر قد يصبح طالبًا لاحقًا بربط رقمه
        rows.append([btn("🎓 أنا طالب: ربط حسابي", "start:link")])
    return kb(rows)


def kb_confirm(academic_no: str) -> InlineKeyboardMarkup:
    return kb([[btn("✅ نعم، هذه بياناتي", f"link:ok:{academic_no}"), btn("رقم آخر", "link:retry")]])

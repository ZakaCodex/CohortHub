"""الملفات داخل البوت: التصفح على طريقة البوت الرسمي (مادة ← نظري/عملي ← نوع المحتوى المتاح ← ملف) مع تعديل الرسالة نفسها بدل إرسال رسالة جديدة، والإرسال بـ copyMessage/file_id، ورفع الملفات للمجتمع."""
from __future__ import annotations

import logging
from html import escape
from typing import Any

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot.media import send_by_kind
from bot.services import Services, svc
from bot.ui import btn, kb, say
from core.files import PARTS, TYPES_PLURAL, build_caption, file_title

log = logging.getLogger("files")
PAGE = 8

TYPE_ICONS = {
    "lecture": "📖",
    "summary": "📝",
    "exam": "📋",
    "exercises": "✍️",
    "translation": "🌐",
    "reference": "📚",
    "benefit": "💡",
}


async def _show(bot: Any, chat_id: int, text: str, markup: Any = None, edit_msg: Any = None) -> Any:
    """يعدّل الرسالة الحالية عند التنقّل داخل قوائم الملفات، أو يرسل رسالة جديدة إن تعذّر التعديل."""
    if edit_msg is not None and hasattr(edit_msg, "edit_text"):
        try:
            return await edit_msg.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
        except TelegramError:
            pass
    if edit_msg is not None and hasattr(bot, "edit_message_text") and getattr(edit_msg, "message_id", None):
        try:
            return await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=edit_msg.message_id,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except TelegramError:
            pass
    return await say(bot, chat_id, text, markup)


# ---------------- التصفح (نفس تقسيم البوت الرسمي مع تعديل الرسالة الحالية) ----------------
async def show_subjects(bot: Any, chat_id: int, profile: dict[str, Any], edit_msg: Any = None) -> None:
    subjects = profile.get("subjects", [])
    if not subjects:
        return await _show(bot, chat_id, "لم تُضف مواد بعد.", edit_msg=edit_msg)
    rows = [[btn(f"📚 {x['name']}", f"fl:s:{x['id']}")] for x in subjects]
    rows.append([btn("🔙 القائمة الرئيسية", "m:home")])
    await _show(bot, chat_id, "📚 <b>قسم الملفات الدراسية</b>\nاختر المقرر الدراسي:", kb(rows), edit_msg=edit_msg)


async def show_parts(
    bot: Any,
    s: Services,
    chat_id: int,
    tg: int,
    sid: int,
    profile: dict[str, Any] | None = None,
    edit_msg: Any = None,
) -> None:
    facets = await s.store.rpc("file_facets", {"p_tg": tg, "p_subject": sid})
    if not facets:
        return await _show(
            bot,
            chat_id,
            "لا توجد ملفات مرفوعة لهذه المادة حتى الآن.",
            kb([[btn("🔙 رجوع للمواد", "m:files")]]),
            edit_msg=edit_msg,
        )
    subj_name = ""
    if profile:
        subj_name = next((x["name"] for x in profile.get("subjects", []) if int(x["id"]) == sid), "")
    theory_n = sum(int(f.get("n") or 0) for f in facets if f.get("part") == "theory")
    prac_n = sum(int(f.get("n") or 0) for f in facets if f.get("part") == "practical")
    part_btns = []
    if theory_n > 0:
        part_btns.append(btn(f"📚 نظري ({theory_n})", f"fl:p:{sid}:theory"))
    else:
        part_btns.append(btn("📚 نظري", f"fl:p:{sid}:theory"))
    if prac_n > 0:
        part_btns.append(btn(f"💻 عملي ({prac_n})", f"fl:p:{sid}:practical"))
    else:
        part_btns.append(btn("💻 عملي", f"fl:p:{sid}:practical"))
    rows = [
        part_btns,
        [btn("🔙 رجوع للمواد", "m:files")],
    ]
    header = f"📚 <b>{escape(subj_name)}</b>\n" if subj_name else ""
    await _show(bot, chat_id, f"{header}اختر القسم (نظري أو عملي):", kb(rows), edit_msg=edit_msg)


async def show_facets(
    bot: Any,
    s: Services,
    chat_id: int,
    tg: int,
    sid: int,
    part: str | None = None,
    edit_msg: Any = None,
) -> None:
    if not part:
        return await show_parts(bot, s, chat_id, tg, sid, edit_msg=edit_msg)
    facets = await s.store.rpc("file_facets", {"p_tg": tg, "p_subject": sid})
    part_facets = [f for f in (facets or []) if f.get("part") == part and int(f.get("n") or 0) > 0]
    if not part_facets:
        return await _show(
            bot,
            chat_id,
            f"لا توجد ملفات مرفوعة في قسم <b>{PARTS.get(part, part)}</b> لهذه المادة بعد.",
            kb([[btn("🔙 رجوع (نظري / عملي)", f"fl:s:{sid}")]]),
            edit_msg=edit_msg,
        )
    rows = [
        [btn(f"{TYPE_ICONS.get(f['content_type'], '📁')} {TYPES_PLURAL.get(f['content_type'], f['content_type'])} ({f['n']})",
             f"fl:l:{sid}:{part}:{f['content_type']}:0")]
        for f in part_facets
    ]
    rows.append([btn("🔙 رجوع (نظري / عملي)", f"fl:s:{sid}")])
    await _show(bot, chat_id, f"📂 <b>القسم: {PARTS.get(part, part)}</b>\nاختر نوع المحتوى المطلوب:", kb(rows), edit_msg=edit_msg)


async def show_list(
    bot: Any,
    s: Services,
    chat_id: int,
    tg: int,
    sid: int,
    part: str,
    ctype: str,
    page: int,
    edit_msg: Any = None,
) -> None:
    rows = await s.store.rpc("list_files", {"p_tg": tg, "p_subject": sid, "p_part": part, "p_type": ctype,
                                            "p_limit": PAGE, "p_offset": page * PAGE})
    if not rows:
        return await _show(
            bot,
            chat_id,
            "لا توجد ملفات هنا.",
            kb([[btn("🔙 رجوع للأقسام", f"fl:p:{sid}:{part}")]]),
            edit_msg=edit_msg,
        )
    buttons = [[btn("📄 " + file_title(r), f"fl:g:{r['id']}")] for r in rows]
    nav_row = []
    if page > 0:
        nav_row.append(btn("▶ السابق", f"fl:l:{sid}:{part}:{ctype}:{page - 1}"))
    if len(rows) == PAGE:
        nav_row.append(btn("المزيد ◀", f"fl:l:{sid}:{part}:{ctype}:{page + 1}"))
    if nav_row:
        buttons.append(nav_row)
    buttons.append([btn("🔙 رجوع للأقسام", f"fl:p:{sid}:{part}")])
    type_lbl = TYPES_PLURAL.get(ctype, ctype)
    await _show(bot, chat_id, f"📂 <b>{PARTS[part]} · {type_lbl}</b>\nاضغط على الملف لإرساله فورًا:", kb(buttons), edit_msg=edit_msg)


async def send_file(bot: Any, s: Services, chat_id: int, tg: int, fid: int) -> None:
    f = await s.store.rpc("get_file", {"p_tg": tg, "p_id": fid})
    if f.get("status") != "ok":
        return await say(bot, chat_id, "هذا الملف لم يعد متاحًا.")
    try:
        if not (f.get("src_chat") and f.get("src_msg")):
            raise TelegramError("no source")
        await bot.copy_message(chat_id, f["src_chat"], f["src_msg"])   # دون إعادة رفع
    except TelegramError:
        await send_by_kind(bot, chat_id, f["media_kind"], f["file_id"])


# ---------------- الرفع (البيانات من التطبيق ثم الملف هنا) ----------------
async def start_upload(bot: Any, chat_id: int, draft_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["up"] = draft_id
    await say(bot, chat_id, "أرسل الملف الآن وسأنشره في مجتمع المادة بالصيغة المعتمدة.")


async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, m: dict[str, Any], *, silent_if_no_draft: bool = False) -> bool:
    s, chat, tg = svc(context), update.effective_chat.id, update.effective_user.id
    prep = await s.store.rpc("prepare_file", {"p_actor": tg, "p_unique": m["unique_id"], "p_draft": context.user_data.get("up")})
    if prep["status"] != "ok":
        if silent_if_no_draft:
            return False
        await say(context.bot, chat, "لا يوجد رفع جارٍ. ابدأ من التطبيق: الملفات ← رفع ملف.")
        return True

    dup = prep.get("duplicate")
    if dup and context.user_data.get("dup_ok") != m["unique_id"]:
        context.user_data["dup_ok"] = m["unique_id"]
        await say(context.bot, chat, f"⚠️ هذا الملف مرفوع سابقًا في المادة نفسها (رقم {dup}).\nأعد إرساله إن أردت رفعه مرة أخرى.")
        return True

    meta, subj, doc = prep["payload"], prep["subject"], prep.get("doctor")
    caption = build_caption(meta, subj["tag"], doc["name"] if doc else None)
    post: dict[str, Any] = {"caption": caption}
    warn = None
    dest = await s.destination(f"cohort:subject:{subj['id']}")
    if dest:
        try:
            sent = await context.bot.copy_message(chat_id=dest["chat_id"], from_chat_id=chat,
                                                  message_id=update.effective_message.message_id,
                                                  caption=caption, message_thread_id=dest.get("topic_id"))
            post.update(chat_id=dest["chat_id"], topic_id=dest.get("topic_id"), message_id=sent.message_id)
        except TelegramError as exc:
            warn = f"تعذّر النشر في المجتمع ({exc.message}). تأكد أن البوت مشرف فيه."
    else:
        warn = "لم يُضبط مجتمع هذه المادة بعد، فحُفظ الملف ويظهر للطلاب في البوت والتطبيق."

    res = await s.store.rpc("register_file", {"p_actor": tg, "p_draft": prep["draft_id"], "p_media": m, "p_post": post})
    if res["status"] != "ok":
        await say(context.bot, chat, "تعذّر حفظ الملف. أعد المحاولة من التطبيق.")
        return True
    context.user_data.pop("up", None)
    old = res.get("old")
    if old:   # استبدال نسخة أقدم: نحذف رسالتها من المجتمع
        try:
            await context.bot.delete_message(old["chat_id"], old["message_id"])
        except TelegramError:
            pass
    await say(context.bot, chat, "تم رفع الملف ✅" + (f"\n⚠️ {warn}" if warn else ""))
    return True


# ---------------- موجّه الأزرار ----------------
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str], profile: dict[str, Any] | None = None) -> None:
    s, bot = svc(context), context.bot
    chat, tg = update.effective_chat.id, update.effective_user.id
    q = update.callback_query
    edit_msg = q.message if q else None
    action = parts[1]
    if action == "s":
        await show_parts(bot, s, chat, tg, int(parts[2]), profile, edit_msg=edit_msg)
    elif action == "p":
        await show_facets(bot, s, chat, tg, int(parts[2]), parts[3], edit_msg=edit_msg)
    elif action == "l":
        await show_list(bot, s, chat, tg, int(parts[2]), parts[3], parts[4], int(parts[5]), edit_msg=edit_msg)
    elif action == "g":
        await send_file(bot, s, chat, tg, int(parts[2]))

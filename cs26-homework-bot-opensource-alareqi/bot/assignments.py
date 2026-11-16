"""الواجبات داخل البوت: عرض الواجبات المفتوحة التي لم يسلمها الطالب فقط عند اختيار «الواجبات» في البوت (مثال: نظم تشغيل - عملي)، التسليم، التعديل المباشر من التطبيق، التسليم للدكتور مع البيانات كـ Caption مدمج بالملف، المرفقات."""
from __future__ import annotations

import asyncio
import logging
from html import escape
from typing import Any

from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from bot.media import is_image, send_by_kind
from bot.services import Services, svc
from bot.ui import btn, kb, say
from core.assignments import STATUS_AR, can_submit, parse_ts, student_status
from core.messages import PART_AR, submission_header
from core.store import StoreError
from core.timeutil import format_datetime_ar, now_utc

log = logging.getLogger("assignments")
EMOJI = {"open": "🟢", "submitted": "✅", "late": "🟠", "missed": "🔴"}


# ---------------- التصفح: قائمة الواجبات المفتوحة التي لم يسلمها الطالب فقط ----------------
async def show_list(
    bot: Any,
    s: Services,
    chat_id: int,
    tg_id: int,
    *_args: Any,
    **_kwargs: Any,
) -> None:
    """يعرض الواجبات المفتوحة التي لم يسلمها الطالب فقط لمجموعته النظرية والعملية مباشرة بصيغة: اسم المادة - نظري/عملي."""
    rows = await s.store.rpc("my_assignments", {"p_tg": tg_id, "p_limit": 40})
    now = now_utc()
    pending_rows = [r for r in (rows or []) if can_submit(now, r) and not parse_ts(r.get("submitted_at"))]
    if not pending_rows:
        return await _say(
            bot,
            chat_id,
            "لا توجد واجبات غير مسلَّمة لمجموعتك حاليًا ✅\n"
            "يمكنك استعراض واجباتك المسلَّمة أو تعديل ملفاتها من داخل التطبيق (Mini App).",
            kb([[btn("🔙 القائمة الرئيسية", "m:home")]]),
        )

    # حساب ما إذا كان لنفس المادة والقسم أكثر من واجب مفتوح غير مسلّم لتمييزها بالرقم عند الحاجة فقط
    pair_counts: dict[tuple[str, str], int] = {}
    for r in pending_rows:
        key = (r.get("subject", ""), r.get("part", ""))
        pair_counts[key] = pair_counts.get(key, 0) + 1

    buttons = []
    for r in pending_rows:
        subj = r.get("subject", "")
        part_ar = PART_AR.get(r.get("part", ""), "")
        label = f"{subj} - {part_ar}"
        if pair_counts.get((subj, r.get("part", "")), 0) > 1:
            label += f" ({r['number']})"
        buttons.append([btn(label, f"a:d:{r['id']}")])

    buttons.append([btn("🔙 القائمة الرئيسية", "m:home")])
    await _say(bot, chat_id, "📝 <b>الواجبات المتاحة (غير المسلَّمة)</b>\nاختر الواجب للتسليم:", kb(buttons))


async def show_subjects(bot: Any, s: Services, chat_id: int, tg_id: int, profile: dict[str, Any] | None = None) -> None:
    """توجيه مباشر لقائمة الواجبات المفتوحة غير المسلَّمة دون المرور بقائمة المواد."""
    await show_list(bot, s, chat_id, tg_id)


async def _say(bot, chat_id, text, markup=None):
    return await say(bot, chat_id, text, markup)


async def show_detail(bot: Any, s: Services, chat_id: int, tg_id: int, aid: int) -> None:
    d = await s.store.rpc("assignment_detail", {"p_tg": tg_id, "p_id": aid})
    if d.get("status") != "ok":
        return await _say(bot, chat_id, "هذا الواجب غير متاح.")
    now = now_utc()
    sub = d.get("submission") or {}
    row = {"due_at": d["due_at"], "closes_at": d["closes_at"], "status": d["status_db"], "submitted_at": sub.get("submitted_at")}
    st = student_status(now, row)
    lines = [f"📝 <b>واجب {d['number']} — {escape(d['subject'])}</b> ({PART_AR.get(d['part'], '')})", f"👥 {escape(d['grp'])}"]
    if d.get("description"):
        lines.append(f"\n{escape(d['description'])}")
    lines.append(f"\n⏰ آخر موعد: {format_datetime_ar(parse_ts(d['due_at']))}")
    status_line = f"الحالة: {EMOJI[st]} {STATUS_AR[st]}"
    if st == "submitted":
        status_line += " (سُلّم متأخرًا)" if sub.get("is_late") else ""
        if sub.get("received_at"):
            status_line += " · استلمه الدكتور"
    lines.append(status_line)
    if d.get("allow_text"):
        lines.append("✍️ يقبل هذا الواجب ملفات ونصوصًا وروابط")

    rows = []
    if can_submit(now, row):
        rows.append([btn("✏️ تعديل إرسال الملف" if st == "submitted" else "📤 ابدأ التسليم", f"sub:go:{aid}")])
    extra = []
    if d.get("attachments"):
        extra.append(btn("📎 المرفق", f"a:att:{aid}"))
    if st == "submitted":
        extra.append(btn("📁 ملفات تسليمي", f"a:my:{aid}"))
    if extra:
        rows.append(extra)
    rows.append([btn("🔙 رجوع للواجبات", "m:asg")])
    await _say(bot, chat_id, "\n".join(lines), kb(rows))


async def start_submit_direct(
    bot: Any,
    s: Services,
    chat_id: int,
    tg_id: int,
    aid: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """يبدأ جلسة إرسال أو تعديل ملف الواجب فورًا عند التحويل المباشر من الـ Mini App."""
    d = await s.store.rpc("assignment_detail", {"p_tg": tg_id, "p_id": aid})
    if d.get("status") != "ok":
        return await _say(bot, chat_id, "هذا الواجب غير متاح.")
    now = now_utc()
    sub = d.get("submission") or {}
    row = {"due_at": d["due_at"], "closes_at": d["closes_at"], "status": d["status_db"], "submitted_at": sub.get("submitted_at")}
    if not can_submit(now, row):
        return await _say(bot, chat_id, "انتهى وقت هذا الواجب ولم يعد يقبل تسليمات أو تعديلات.")
    context.user_data.pop("attach", None)
    context.user_data.pop("sh_sub", None)   # جلسة واحدة فقط: لا نخلط تسليم الواجب بمشاركة مفتوحة
    context.user_data["sub"] = aid
    allow_text = bool(d.get("allow_text"))
    context.user_data["sub_text"] = allow_text   # لا يُقبل النص إلا إن فعّله المشرف في هذا الواجب
    is_edit = bool(parse_ts(sub.get("submitted_at")))
    title = f"✏️ <b>تعديل تسليم واجب {d['number']} — {escape(d['subject'])}</b>" if is_edit else f"📤 <b>تسليم واجب {d['number']} — {escape(d['subject'])}</b>"
    what = "الملف أو النص/الرابط الصحيح" if allow_text else "الملف الصحيح"
    what_new = "ملفاتك (أي صيغة عدا الصور) و/أو اكتب نصك أو رابطك كرسالة" if allow_text else "ملفاتك (أي صيغة عدا الصور)"
    hint = (
        f"أرسل الآن {what} (أي صيغة عدا الصور)، دفعة واحدة أو منفصلة، ثم اضغط «✅ إنهاء التسليم».\n"
        "سيحلّ التسليم الجديد محلّ تسليمك السابق تلقائيًا لدى الدكتور."
        if is_edit
        else f"أرسل {what_new}، دفعة واحدة أو منفصلة، ثم اضغط «✅ إنهاء التسليم»."
    )
    await _say(bot, chat_id, f"{title}\n\n{hint}", _session_kb(aid))


async def send_files(bot: Any, chat_id: int, files: list[dict[str, Any]]) -> None:
    for f in files:
        await send_by_kind(bot, chat_id, f["media_kind"], f["file_id"])


# ---------------- التسليم ----------------
def _session_kb(aid: int):
    return kb([[btn("✅ إنهاء التسليم", f"sub:fin:{aid}")], [btn("إلغاء", f"sub:cancel:{aid}")]])


async def on_submission_media(update: Update, context: ContextTypes.DEFAULT_TYPE, m: dict[str, Any]) -> None:
    s, aid = svc(context), context.user_data["sub"]
    chat, tg = update.effective_chat.id, update.effective_user.id
    if is_image(m):
        return await _say(context.bot, chat, "الصور غير مقبولة في التسليم. أرسل الملف بصيغة أخرى (PDF، Word، ZIP…).")
    res = await s.store.rpc("add_submission_file", {"p_tg": tg, "p_assignment": aid, "p_file": m})
    st = res["status"]
    if st == "ok":
        group = update.effective_message.media_group_id
        if group and context.user_data.get("sub_group") == group:
            return   # ملف إضافي من الدفعة نفسها: لا نكرر الرد
        context.user_data["sub_group"] = group
        return await _say(context.bot, chat, "استلمت الملف ✅ أرسل المزيد أو اضغط «إنهاء التسليم».", _session_kb(aid))
    if st == "too_many":   # نُبقي الجلسة مفتوحة ونُظهر زر الإنهاء كي لا تضيع الملفات المرفوعة
        return await _say(context.bot, chat, "وصلت الحد الأقصى من الملفات (20). اضغط «إنهاء التسليم» لإرسال ما رفعته.",
                          _session_kb(aid))
    context.user_data.pop("sub", None)
    msg = {"closed": "انتهى وقت هذا الواجب ولم يعد يقبل تسليمات.",
           "not_target": "هذا الواجب ليس لمجموعتك."}.get(st, "تعذّر استلام الملف.")
    await _say(context.bot, chat, msg)


async def on_submission_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """نص أو رابط أثناء جلسة تسليم: يُقبل فقط إن فعّل المشرف «السماح بإرسال نصوص» لهذا الواجب."""
    ud = context.user_data
    aid = ud.get("sub")
    text = (update.effective_message.text or "").strip()
    if not aid or not text:
        return False
    chat, tg = update.effective_chat.id, update.effective_user.id
    if not ud.get("sub_text"):
        await _say(context.bot, chat, "هذا الواجب يقبل الملفات فقط. أرسل ملفك (أي صيغة عدا الصور) ثم اضغط «✅ إنهاء التسليم».",
                   _session_kb(aid))
        return True
    if len(text) > 3000:
        await _say(context.bot, chat, "النص طويل جدًا (الحد 3000 حرف). اختصره أو أرسله كملف.", _session_kb(aid))
        return True
    s = svc(context)
    res = await s.store.rpc("add_submission_file", {
        "p_tg": tg, "p_assignment": aid,
        "p_file": {"media_kind": "text", "file_id": text, "unique_id": None, "name": text[:80], "mime": None, "size": len(text)},
    })
    st = res["status"]
    if st == "ok":
        await _say(context.bot, chat, "استلمت النص ✅ أرسل المزيد أو اضغط «إنهاء التسليم».", _session_kb(aid))
        return True
    if st == "too_many":
        await _say(context.bot, chat, "وصلت الحد الأقصى من العناصر (20). اضغط «إنهاء التسليم» لإرسال ما أرسلته.", _session_kb(aid))
        return True
    if st == "text_not_allowed":
        ud["sub_text"] = False
        await _say(context.bot, chat, "هذا الواجب يقبل الملفات فقط. أرسل ملفك ثم اضغط «✅ إنهاء التسليم».", _session_kb(aid))
        return True
    ud.pop("sub", None)
    msg = {"closed": "انتهى وقت هذا الواجب ولم يعد يقبل تسليمات.",
           "not_target": "هذا الواجب ليس لمجموعتك."}.get(st, "تعذّر استلام النص.")
    await _say(context.bot, chat, msg)
    return True


async def deliver(bot: Any, s: Services, pkg: dict[str, Any]) -> bool:
    """يرسل ملف/ملفات الطالب لمجتمع الدكتور (topic مجموعة الطالب) مرفقًا معها الـ Caption ببيانات الطالب وزر الاستلام في نفس رسالة الملف دون رسائل نصية منفصلة."""
    dest: dict[str, Any] | None = None
    if pkg.get("dest_chat_id"):
        dest = {"chat_id": pkg["dest_chat_id"], "topic_id": pkg.get("dest_topic_id")}
    elif pkg.get("dest_key"):
        dest = await s.destination(pkg["dest_key"])
    if not dest:
        return False

    files = pkg.get("files") or []
    if not files:
        return False

    for old in pkg.get("old_deliveries", []):
        try:
            await bot.delete_message(old["chat_id"], old["message_id"])
        except TelegramError:
            pass

    chat, topic = dest["chat_id"], dest.get("topic_id")
    caption_text = submission_header(pkg)
    recv_markup = kb([[btn("✔️ تم الاستلام", f"recv:{pkg['submission_id']}")]])
    total_files = len(files)

    sent: list[dict[str, int]] = []
    try:
        for idx, f in enumerate(files):
            is_last = idx == total_files - 1
            file_caption = (
                caption_text
                if total_files == 1
                else f"{caption_text}\n📎 ({idx + 1}/{total_files})"
            )
            markup = recv_markup if is_last else None
            try:
                m = await send_by_kind(
                    bot,
                    chat,
                    f["media_kind"],
                    f["file_id"],
                    caption=file_caption,
                    thread=topic,
                    reply_markup=markup,
                )
            except BadRequest as exc:
                if topic is not None and "thread" in str(exc).lower():
                    topic = None
                    m = await send_by_kind(
                        bot,
                        chat,
                        f["media_kind"],
                        f["file_id"],
                        caption=file_caption,
                        thread=None,
                        reply_markup=markup,
                    )
                else:
                    raise
            sent.append({"chat_id": chat, "message_id": m.message_id})
    except TelegramError as exc:
        log.warning("delivery failed for %s (chat=%s, topic=%s): %s", pkg["submission_id"], chat, topic, exc)
        for x in sent:   # لا نترك تسليمًا ناقصًا
            try:
                await bot.delete_message(x["chat_id"], x["message_id"])
            except TelegramError:
                pass
        return False
    for attempt in range(3):
        try:
            await s.store.rpc("record_delivery", {"p_sub": pkg["submission_id"], "p_msgs": sent})
            return True
        except StoreError as exc:
            log.warning("record_delivery failed (%s/3) for %s: %s", attempt + 1, pkg["submission_id"], exc)
            await asyncio.sleep(0.5 * (attempt + 1))
    for x in sent:   # تعذّر تسجيل التسليم: نحذف المرسَل كي لا يتكرر عند إعادة المحاولة لاحقًا
        try:
            await bot.delete_message(x["chat_id"], x["message_id"])
        except TelegramError:
            pass
    return False


async def deliver_tracked(bot: Any, s: Services, pkg: dict[str, Any]) -> bool:
    """يسلّم مع حماية كاملة: أي فشل أو استثناء يُسجَّل بتراجع تصاعدي فلا يحجب غيره ولا يتكرر كل دقيقة."""
    try:
        ok = await deliver(bot, s, pkg)
    except Exception as exc:  # noqa: BLE001
        log.warning("delivery crashed for %s: %s", pkg.get("submission_id"), exc)
        ok = False
    if not ok:
        try:
            await s.store.rpc("delivery_failed", {"p_sub": pkg["submission_id"]})
        except Exception as exc:  # noqa: BLE001
            log.warning("delivery_failed rpc: %s", exc)
    return ok


async def finalize(update: Update, context: ContextTypes.DEFAULT_TYPE, aid: int) -> None:
    s, chat, tg = svc(context), update.effective_chat.id, update.effective_user.id
    res = await s.store.rpc("finalize_submission", {"p_tg": tg, "p_assignment": aid})
    st = res["status"]
    if st == "no_files":
        return await _say(context.bot, chat, "لم ترسل أي ملف بعد. أرسل ملفاتك (أو نصك إن كان مسموحًا) ثم اضغط «إنهاء التسليم».", _session_kb(aid))
    context.user_data.pop("sub", None)
    if st == "closed":
        return await _say(context.bot, chat, "انتهى وقت هذا الواجب ولم يعد يقبل تسليمات.")
    if st != "ok":
        return await _say(context.bot, chat, "تعذّر إنهاء التسليم.")
    pkg = res["package"]
    claimed = await s.store.rpc("claim_delivery", {"p_sub": pkg["submission_id"]})
    delivered = False if claimed is False else await deliver_tracked(context.bot, s, pkg)
    late = " (متأخر)" if pkg["is_late"] else ""
    note = "" if delivered else "\nسيصل للدكتور تلقائيًا بمجرد جاهزية المجتمع."
    await _say(context.bot, chat, f"تم تسليم واجبك ✅{late}\nعدد العناصر: {len(pkg['files'])}{note}\n"
                                  "تستطيع تعديل إرسال الملف قبل إغلاق الواجب من التطبيق أو بالضغط على «عرض الواجب».",
               kb([[btn("📝 عرض الواجب", f"a:d:{aid}")], [btn("🔙 قائمة الواجبات", "m:asg")]]))


# ---------------- مرفقات الواجب (للمشرفين) ----------------
async def start_attach(bot: Any, chat_id: int, draft_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["attach"] = draft_id
    await _say(bot, chat_id, "أرسل مرفقات الواجب الآن (أي صيغة، ملفًا أو أكثر) ثم اضغط «نشر الواجب».",
               kb([[btn("🚀 نشر الواجب", f"ad:pub:{draft_id}")]]))


async def on_attach_media(update: Update, context: ContextTypes.DEFAULT_TYPE, m: dict[str, Any]) -> None:
    s, draft = svc(context), context.user_data["attach"]
    res = await s.store.rpc("add_draft_attachment", {"p_actor": update.effective_user.id, "p_draft": draft, "p_file": m})
    if res["status"] != "ok":
        context.user_data.pop("attach", None)
        return await _say(context.bot, update.effective_chat.id, "انتهت مسودة الواجب أو بلغت الحد الأقصى (10 مرفقات). ابدأ من التطبيق.")
    group = update.effective_message.media_group_id
    if group and context.user_data.get("att_group") == group:
        return
    context.user_data["att_group"] = group
    await _say(context.bot, update.effective_chat.id, f"أُرفق الملف ✅ (المجموع {res['count']})",
               kb([[btn("🚀 نشر الواجب", f"ad:pub:{draft}")]]))


async def publish(update: Update, context: ContextTypes.DEFAULT_TYPE, draft_id: int) -> None:
    s, chat = svc(context), update.effective_chat.id
    try:
        res = await s.store.rpc("publish_assignment", {"p_actor": update.effective_user.id, "p_draft": draft_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("publish failed: %s", exc)
        return await _say(context.bot, chat, "تعذّر النشر: لا تملك صلاحية على هذه المجموعة.")
    context.user_data.pop("attach", None)
    if res["status"] == "ok":
        return await _say(context.bot, chat, f"تم نشر الواجب رقم {res['number']} ✅ وسيصل الإشعار للطلاب.")
    await _say(context.bot, chat, {"no_draft": "المسودة منتهية أو نُشرت سابقًا.",
                                   "due_in_past": "الموعد أصبح في الماضي. أنشئ الواجب من جديد."}.get(res["status"], "تعذّر النشر."))


async def on_received(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int) -> None:
    """زر «تم الاستلام» في مجتمع الدكتور: للدكتور صاحب الواجب أو المطوّر فقط."""
    q = update.callback_query
    res = await svc(context).store.rpc("mark_received", {"p_tg": update.effective_user.id, "p_sub": sub_id})
    if res["status"] != "ok":
        return await q.answer("هذا الزر للدكتور المسؤول عن الواجب فقط.", show_alert=True)
    await q.answer("تم تسجيل الاستلام ✅")
    try:
        await q.edit_message_reply_markup(kb([[btn("✅ تم الاستلام", "noop")]]))
    except TelegramError:
        pass


# ---------------- موجّه الأزرار ----------------
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str], profile: dict[str, Any] | None = None) -> None:
    s, bot = svc(context), context.bot
    chat, tg = update.effective_chat.id, update.effective_user.id
    head, action = parts[0], parts[1] if len(parts) > 1 else ""
    aid = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if head == "a" and action in ("all", "s", "p"):
        await show_list(bot, s, chat, tg)
    elif head == "a" and action == "d":
        await show_detail(bot, s, chat, tg, aid)
    elif head == "a" and action == "att":
        await send_files(bot, chat, await s.store.rpc("assignment_file_list", {"p_tg": tg, "p_id": aid}))
    elif head == "a" and action == "my":
        await send_files(bot, chat, await s.store.rpc("my_submission_files", {"p_tg": tg, "p_assignment": aid}))
    elif head == "sub" and action == "go":
        await start_submit_direct(bot, s, chat, tg, aid, context)
    elif head == "sub" and action == "fin":
        await finalize(update, context, aid)
    elif head == "sub" and action == "cancel":
        context.user_data.pop("sub", None)
        await s.store.rpc("cancel_submission_pending", {"p_tg": tg, "p_assignment": aid})
        await _say(bot, chat, "أُلغي التسليم ولم يتغيّر شيء.")
    elif head == "ad" and action == "pub":
        await publish(update, context, aid)

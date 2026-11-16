"""المرحلة 3 داخل البوت:
- شؤوني: حضوري (النظري 12 محاضرة)، باركود بطاقتي (Code 128)، تظلماتي (تقديم وتعديل قبل الإغلاق)، تسليماتي واسترجاع الملفات.
- المساعد الذكي (من دليل البوت وبيانات الطالب نفسه بحد يومي 5 للطالب و2 للزائر) والبحث الذكي في الملفات.
- البلاغات والشكاوى والاستفسارات وردّ المطوّر."""
from __future__ import annotations

import io
import logging
from html import escape
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bot.services import Services, svc
from bot.ui import btn, kb, say
from core import messages
from core.assignments import STATUS_AR, parse_ts, student_status
from core.assistant import (
    TICKET_CATEGORIES,
    ask_assistant,
    smart_search_files,
    validate_ticket,
    validate_ticket_reply,
)
from core.barcode import render_barcode_png
from core.grievances import PART_AR, validate_grievance_reason
from core.permissions import can
from core.timeutil import format_datetime_ar, now_utc
from core import cohort

log = logging.getLogger("phase3")


# =====================================================================
# 1) قائمة «شؤوني» في البوت
# =====================================================================
async def show_affairs_menu(bot: Any, chat_id: int) -> None:
    rows = [
        [btn("⚖️ تظلماتي", "af:grv"), btn("💬 الدعم والمساعد", "m:ai")],
        [btn("📋 حضوري (النظري)", "af:att"), btn("🪪 باركود بطاقتي", "af:bar")],
        [btn("📁 تسليماتي واسترجاع ملفاتي", "af:sub")],
    ]
    await say(bot, chat_id, "🎓 <b>شؤوني</b>\nاختر الخدمة المطلوبة:", kb(rows))


async def show_my_attendance(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    rows = await s.store.rpc("my_attendance", {"p_tg": tg_id})
    if not rows:
        return await say(bot, chat_id, "لا توجد بيانات حضور مسجّلة لمجموعتك بعد.")
    lines = ["📋 <b>حضوري — المحاضرات النظرية (12 محاضرة لكل مادة)</b>\n"]
    for r in rows:
        held = int(r.get("held") or 0)
        pres = int(r.get("present") or 0)
        ab = int(r.get("absent") or 0)
        if held > 0:
            pct = round((pres * 100) / held)
            lines.append(f"• <b>{escape(r['subject'])}</b>: حاضر {pres} · غائب {ab} من {held} ({pct}%)")
        else:
            lines.append(f"• <b>{escape(r['subject'])}</b>: لم تُعقد جلسات بعد")
    await say(bot, chat_id, "\n".join(lines))


async def send_my_barcode(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    res = await s.store.rpc("request_barcode", {"p_tg": tg_id, "p_enqueue": False})
    st = res.get("status")
    if st == "daily_limit_reached":
        return await say(bot, chat_id, f"⚠️ استنفدت الحد اليومي لتوليد الباركود ({res.get('limit', 3)} مرات يوميًا).")
    if st != "ok":
        return await say(bot, chat_id, "هذه الميزة متاحة لطلاب الدفعة المربوطين بأرقامهم الأكاديمية.")
    png_bytes = render_barcode_png(
        res["academic_no"],
        res.get("full_name") or "",
        res.get("theory_group_id") or "",
    )
    caption = messages.barcode_caption(res) + f"\n📊 المتبقي لك اليوم: {res.get('remaining', 0)}/{res.get('limit', 3)}"
    await bot.send_photo(chat_id=chat_id, photo=io.BytesIO(png_bytes), caption=caption, parse_mode="HTML")


async def show_my_submissions(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    rows = await s.store.rpc("my_assignments", {"p_tg": tg_id, "p_limit": 30})
    if not rows:
        return await say(bot, chat_id, "لا توجد واجبات في سجلك حتى الآن.")
    now = now_utc()
    lines = ["📁 <b>سجل تسليماتي وحالات الواجبات</b>\n"]
    buttons = []
    for r in rows[:12]:
        st = student_status(now, r)
        late = " (متأخر)" if r.get("is_late") else ""
        rec = " · استلمه الدكتور ✅" if r.get("received_at") else ""
        lines.append(f"• <b>{escape(r['subject'])}</b> · واجب {r['number']}: {STATUS_AR[st]}{late}{rec}")
        if st == "submitted":
            buttons.append([btn(f"📥 استرجاع ملفات: {r['subject']} (واجب {r['number']})", f"a:my:{r['id']}")])
    await say(bot, chat_id, "\n".join(lines), kb(buttons))


# =====================================================================
# 2) التظلمات داخل البوت (عرض، تقديم، وتعديل قبل الإغلاق)
# =====================================================================
async def show_my_grievances(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    res = await s.store.rpc("my_grievances", {"p_tg": tg_id})
    cycles = res.get("cycles") or []
    if not cycles:
        return await say(
            bot,
            chat_id,
            "⚖️ لا توجد دورات تظلمات متاحة لمجموعتك حاليًا.\nعندما يفتح المندوب دورة تظلمات سيصلك إشعار فوري هنا.",
            kb([[btn("💬 التواصل مع الدعم", "m:ai")]]),
        )
    lines = ["⚖️ <b>تظلماتي</b>\nاختر دورة مفتوحة لتقديم تظلمك أو تعديله قبل الإغلاق:"]
    buttons = []
    for c in cycles[:10]:
        mine = c.get("my_grievance")
        tag = "✅ مقدَّم" if mine else ("🟢 مفتوح" if c.get("is_open") else "🔒 مغلق")
        label = f"{tag} · {c['subject']} ({PART_AR.get(c['part'], '')})"
        if c.get("is_open"):
            buttons.append([btn(label, f"grv:go:{c['id']}")])
        elif mine:
            lines.append(f"\n• <b>{escape(c['subject'])}</b> (مغلق): {escape(mine['reason'][:80])}")
    buttons.append([btn("💬 الدعم والمساعد الذكي", "m:ai")])
    await say(bot, chat_id, "\n".join(lines), kb(buttons))


async def start_grievance_input(
    bot: Any, s: Services, chat_id: int, tg_id: int, cycle_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    res = await s.store.rpc("my_grievances", {"p_tg": tg_id})
    st = res.get("student") or {}
    cycles = res.get("cycles") or []
    cyc = next((c for c in cycles if int(c["id"]) == cycle_id), None)
    if not cyc:
        return await say(bot, chat_id, "دورة التظلم هذه غير متاحة لمجموعتك.")
    if not cyc.get("is_open"):
        return await say(bot, chat_id, "انتهى وقت التقديم والتعديل لهذه الدورة.")

    context.user_data["await"] = f"grv:{cycle_id}"
    mine = cyc.get("my_grievance")
    prev = f"\n\n📝 <b>تظلمك الحالي:</b>\n{escape(mine['reason'])}" if mine else ""
    text = (
        f"⚖️ <b>{'تعديل تظلم' if mine else 'تقديم تظلم'} — {escape(cyc['subject'])} ({PART_AR.get(cyc['part'], '')})</b>\n"
        f"👤 الطالب: {escape(st.get('full_name', ''))}\n"
        f"🆔 الرقم الأكاديمي: <code>{escape(st.get('academic_no', ''))}</code>\n"
        f"👥 المجموعة: {escape(cyc.get('student_group', ''))}\n"
        f"⏰ الإغلاق: {format_datetime_ar(parse_ts(cyc['closes_at']))}"
        f"{prev}\n\n"
        "✍️ أرسل الآن <b>سبب التظلم فقط</b> في رسالة نصية:"
    )
    await say(bot, chat_id, text, kb([[btn("إلغاء", "grv:cancel")]]))


# =====================================================================
# 3) المساعد الذكي، البحث الذكي في الملفات، والبلاغات
# =====================================================================
async def show_ai_menu(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    ctx = await s.store.rpc("assistant_context", {"p_tg": tg_id})
    rem, lim = ctx.get("remaining", 0), ctx.get("limit", 10)
    rows = [
        [btn("🤖 اسأل المساعد الذكي", "ai:ask"), btn("🔎 البحث الذكي في الملفات", "ai:search")],
        [btn("📩 إرسال بلاغ (عطل / شكوى / استفسار)", "ai:tkt")],
    ]
    await say(
        bot,
        chat_id,
        f"💬 <b>المساعد الذكي والدعم</b>\n"
        f"يردّ المساعد الذكي من دليل البوت ومن بياناتك المسجّلة.\n"
        f"📊 رصيدك المتبقي اليوم: <b>{rem} من {lim}</b> رسائل.",
        kb(rows),
    )


async def handle_awaiting_text(update: Update, context: ContextTypes.DEFAULT_TYPE, awaiting: str, prof: dict[str, Any]) -> bool:
    """يعالج النصوص المرتبطة بحالات المرحلة 3 (تظلم، بحث ذكي، سؤال للمساعد، بلاغ، رد مطوّر). يعيد True إن عُولجت."""
    s, chat, tg = svc(context), update.effective_chat.id, update.effective_user
    text = (update.effective_message.text or "").strip()

    # 1) كتابة/تعديل سبب التظلم
    if awaiting.startswith("grv:"):
        cycle_id = int(awaiting.split(":")[1])
        clean_reason, err = validate_grievance_reason(text)
        if err:
            await say(context.bot, chat, "يرجى كتابة سبب التظلم بوضوح (من 3 إلى 1000 حرف).")
            return True
        res = await s.store.rpc("upsert_grievance", {"p_tg": tg.id, "p_cycle": cycle_id, "p_reason": clean_reason})
        context.user_data.pop("await", None)
        if res.get("status") == "ok":
            msg = "تم حفظ تعديل تظلمك ✅" if res.get("updated") else "تم تسجيل تظلمك بنجاح ✅"
            await say(context.bot, chat, f"{msg}\nتستطيع تعديله في أي وقت قبل إغلاق الدورة من «شؤوني ← تظلماتي».")
        elif res.get("status") == "closed":
            await say(context.bot, chat, "انتهى وقت التقديم لهذه الدورة.")
        else:
            await say(context.bot, chat, "تعذّر حفظ التظلم.")
        return True

    # 2) البحث الذكي في الملفات
    if awaiting == "ai_search":
        context.user_data.pop("await", None)
        subjects = prof.get("subjects") or []
        ranked, _criteria, _used_ai = await smart_search_files(s.store.rpc, tg.id, text, subjects, limit=8, store=s.store)
        if not ranked:
            await say(
                context.bot,
                chat,
                "🔎 لم أجد ملفًا مطابقًا لطلبك.\nجرّب كتابة اسم المادة ونوع الملف (مثال: <i>ملخص شبكات Module 9</i>).",
                kb([[btn("🔎 بحث آخر", "ai:search"), btn("📚 تصفح الملفات", "m:files")]]),
            )
            return True
        buttons = [[btn(f"📄 {r['subject']} · {r['title']}", f"fl:g:{r['id']}")] for r in ranked]
        buttons.append([btn("🔎 بحث ذكي آخر", "ai:search")])
        await say(context.bot, chat, f"📚 وجدت <b>{len(ranked)}</b> ملفات مطابقة:", kb(buttons))
        return True

    # 3) سؤال للمساعد الذكي
    if awaiting == "ai_ask":
        context.user_data.pop("await", None)
        ctx = await s.store.rpc("assistant_context", {"p_tg": tg.id})
        reply = await ask_assistant(text, ctx, now_utc(), store=s.store, allow_ai=int(ctx.get("remaining") or 0) > 0)
        if reply.get("blocked"):
            await say(
                context.bot,
                chat,
                "⚠️ انتهى رصيد رسائلك اليومي مع المساعد الذكي.\nالأسئلة الجاهزة (واجباتي، حضوري، الباركود...) ما زالت متاحة، أو أرسل بلاغًا للمطوّر:",
                kb([[btn("🤖 سؤال آخر", "ai:ask"), btn("📩 إرسال بلاغ للمطوّر", "ai:tkt")]]),
            )
            return True
        rec = await s.store.rpc(
            "record_assistant_exchange",
            {"p_tg": tg.id, "p_question": text, "p_answer": reply["answer"], "p_counted": bool(reply.get("counted"))},
        )
        rem = rec.get("remaining", 0)
        lim = rec.get("limit", 10)
        rows = [[btn("🤖 سؤال آخر", "ai:ask"), btn("📩 إرسال بلاغ للمطوّر", "ai:tkt")]]
        await say(
            context.bot,
            chat,
            f"🤖 {escape(reply['answer'])}\n\n<small>المتبقي لك اليوم: {rem}/{lim}</small>",
            kb(rows),
        )
        return True

    # 4) إرسال بلاغ (عطل / شكوى / استفسار)
    if awaiting.startswith("tkt_new:"):
        cat = awaiting.split(":")[1]
        clean, err = validate_ticket(cat, text)
        if err:
            await say(context.bot, chat, "يرجى كتابة تفاصيل البلاغ (4 أحرف على الأقل).")
            return True
        _, body_clean = clean
        res = await s.store.rpc("create_ticket", {"p_tg": tg.id, "p_category": cat, "p_body": body_clean})
        context.user_data.pop("await", None)
        if res.get("status") == "ok":
            await say(context.bot, chat, f"تم إرسال بلاغك رقم #{res['id']} للمطوّر ✅\nسيصلك إشعار هنا فور الرد عليه.")
        else:
            await say(context.bot, chat, "تعذّر إرسال البلاغ.")
        return True

    # 5) ردّ المطوّر على بلاغ من داخل البوت
    if awaiting.startswith("tkt_rep:"):
        ticket_id = int(awaiting.split(":")[1])
        clean_rep, err = validate_ticket_reply(text)
        if err:
            await say(context.bot, chat, "يرجى كتابة رد واضح (حرفين على الأقل).")
            return True
        res = await s.store.rpc("reply_ticket", {"p_actor": tg.id, "p_ticket_id": ticket_id, "p_reply": clean_rep})
        context.user_data.pop("await", None)
        if res.get("status") == "ok":
            await say(context.bot, chat, f"تم إرسال الرد على البلاغ #{ticket_id} للمستخدم ✅")
        else:
            await say(context.bot, chat, "تعذّر إرسال الرد.")
        return True

    return False


# =====================================================================
# 4) موجّه الأزرار للمرحلة 3
# =====================================================================
async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str], prof: dict[str, Any]) -> None:
    s, bot = svc(context), context.bot
    chat, tg = update.effective_chat.id, update.effective_user.id
    head = parts[0]
    action = parts[1] if len(parts) > 1 else ""
    perms = prof.get("perms") or []

    if head == "m" and action == "affairs":
        if not can(perms, "affairs.use"):
            return await say(bot, chat, f"هذه الميزة خاصة ب{cohort.get().students_noun}")
        return await show_affairs_menu(bot, chat)

    if head == "m" and action == "ai":
        if not can(perms, "assistant.use"):
            return
        return await show_ai_menu(bot, s, chat, tg)

    if head == "af":
        if not can(perms, "affairs.use"):
            return await say(bot, chat, f"هذه الميزة خاصة ب{cohort.get().students_noun}")
        if action == "att":
            return await show_my_attendance(bot, s, chat, tg)
        if action == "bar":
            return await send_my_barcode(bot, s, chat, tg)
        if action == "grv":
            return await show_my_grievances(bot, s, chat, tg)
        if action == "sub":
            return await show_my_submissions(bot, s, chat, tg)

    if head == "grv":
        if action == "cancel":
            context.user_data.pop("await", None)
            return await say(bot, chat, "أُلغيت العملية.")
        if action == "go" and len(parts) > 2 and parts[2].isdigit():
            return await start_grievance_input(bot, s, chat, tg, int(parts[2]), context)

    if head == "ai":
        if not can(perms, "assistant.use"):
            return
        if action == "search":
            context.user_data["await"] = "ai_search"
            return await say(
                bot,
                chat,
                "🔎 <b>البحث الذكي في الملفات</b>\nاكتب ما تبحث عنه (مثال: <i>ملخص شبكات Module 9</i> أو <i>نموذج نصفي قواعد بيانات</i>):",
            )
        if action == "ask":
            context.user_data["await"] = "ai_ask"
            return await say(
                bot,
                chat,
                "🤖 <b>المساعد الذكي</b>\nاكتب سؤالك عن البوت أو عن واجباتك وحضورك وتظلماتك:",
            )
        if action == "tkt":
            rows = [[btn(label, f"tkt:cat:{code}")] for code, label in TICKET_CATEGORIES.items()]
            return await say(bot, chat, "📩 <b>إرسال بلاغ للمطوّر</b>\nاختر نوع البلاغ:", kb(rows))

    if head == "tkt":
        if action == "cat" and len(parts) > 2 and parts[2] in TICKET_CATEGORIES:
            cat = parts[2]
            context.user_data["await"] = f"tkt_new:{cat}"
            return await say(bot, chat, f"✍️ اكتب الآن تفاصيل <b>{TICKET_CATEGORIES[cat]}</b> في رسالة واحدة:")
        if action == "rep" and len(parts) > 2 and parts[2].isdigit():
            if not can(perms, "tickets.reply"):
                return await say(bot, chat, "هذا الإجراء للمطوّر فقط.")
            tid = int(parts[2])
            context.user_data["await"] = f"tkt_rep:{tid}"
            return await say(bot, chat, f"💬 اكتب ردك على البلاغ #{tid} ليصل للمستخدم فورًا:")

"""المرحلة 4 داخل البوت:
1) شارك: التصفح، التصويت في الاستفتاءات، واستقبال المشاركات المتعددة (نص/صورة/ملف) مع زر «إنهاء المشاركة».
2) اختبارات MCQ داخل البوت (كما كانت في النظام القديم تمامًا عبر استفتاءات تيليجرام التفاعلية Quiz Poll، إلى جانب وجودها في الـ Mini App):
   - عرض المواد المتوفرة واختبارات كل مادة.
   - عرض تفاصيل الاختبار وبدء الاختبار سؤالًا تلو الآخر عبر Poll.QUIZ وحساب النتيجة وحفظها وإعادة الاختبار.
   - رفع اختبار جديد من ملف Excel وحذف الاختبارات للمشرفين."""
from __future__ import annotations

import asyncio
import logging
import time
from html import escape
from typing import Any

from telegram import Poll, Update
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import ContextTypes

from bot.locks import per_user
from bot.media import send_by_kind
from bot.services import Services, svc
from bot.ui import btn, kb, say
from core.mcq import MCQ_EXCEL_COLUMNS, parse_mcq_excel
from core.permissions import can
from core import cohort

log = logging.getLogger("phase4")

POLL_QUESTION_MAX = 300
POLL_OPTION_MAX = 100


def _clip(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _finish_kb(cid: int):
    return kb([[btn("✅ إنهاء المشاركة", f"sh:fin:{cid}")], [btn("إلغاء", "sh:cancel")]])


# =====================================================================
# 1) شارك داخل البوت
# =====================================================================
async def show_sharek_list(bot: Any, s: Services, chat_id: int, tg_id: int) -> None:
    rows = await s.store.rpc("my_sharek", {"p_tg": tg_id})
    if not rows:
        return await say(bot, chat_id, "🗳 لا توجد استفتاءات أو حملات مشاركة مفتوحة لمجموعتك حاليًا.")
    buttons = []
    for c in rows[:10]:
        icon = "📊" if c["kind"] == "poll" else "📥"
        done = (c["kind"] == "poll" and c.get("my_vote") is not None) or (c["kind"] == "submission" and c.get("is_finished"))
        tag = " ✅" if done else ""
        buttons.append([btn(f"{icon} {c['title'][:42]}{tag}", f"sh:open:{c['id']}")])
    await say(bot, chat_id, "🗳 <b>شارك</b>\nاختر الاستفتاء أو حملة المشاركة:", kb(buttons))


async def open_campaign(bot: Any, s: Services, chat_id: int, tg_id: int, cid: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await s.store.rpc("my_sharek", {"p_tg": tg_id})
    c = next((x for x in (rows or []) if int(x["id"]) == cid), None)
    if not c:
        return await say(bot, chat_id, "هذه الحملة لم تعد متاحة.")

    if c["kind"] == "poll":
        if c.get("my_vote") is not None:
            opts = c.get("options") or []
            picked = opts[c["my_vote"]] if 0 <= int(c["my_vote"]) < len(opts) else ""
            return await say(bot, chat_id, f"📊 <b>{escape(c['title'])}</b>\n\nلقد صوّتَ مسبقًا: <b>{escape(picked)}</b> ✅")
        buttons = [[btn(opt, f"sh:v:{cid}:{i}")] for i, opt in enumerate(c.get("options") or [])]
        return await say(bot, chat_id, f"📊 <b>{escape(c['title'])}</b>\n\nاختر إجابتك (اختيار واحد نهائي):", kb(buttons))

    # حملة استقبال مشاركات متعددة
    if c.get("is_finished"):
        return await say(bot, chat_id, "لقد أنهيت إرسال مشاركتك في هذه الحملة مسبقًا ✅")

    context.user_data["sh_sub"] = cid
    for k in ("sub", "sub_text", "attach"):   # جلسة واحدة فقط: لا نخلط المشاركة بتسليم واجب
        context.user_data.pop(k, None)
    sent_n = int(c.get("my_items_count") or 0)
    extra = f"\nلديك حتى الآن <b>{sent_n}</b> عنصر مرسل." if sent_n else ""
    await say(
        bot,
        chat_id,
        f"📥 <b>{escape(c['title'])}</b>{extra}\n\n"
        "أرسل الآن <b>الصور والملفات</b> فقط (عنصرًا واحدًا أو عدة عناصر). "
        "النص تكتبه من التطبيق في شاشة «شارك».\n"
        "وعند الانتهاء اضغط «✅ إنهاء المشاركة».",
        _finish_kb(cid),
    )


async def on_sharek_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """البوت مخصّص لاستلام الصور والملفات فقط؛ نص المشاركة يُكتب داخل التطبيق (شارك)."""
    cid = context.user_data.get("sh_sub")
    if not cid:
        return False
    text = (update.effective_message.text or "").strip()
    if not text:
        return False
    await say(
        context.bot,
        update.effective_chat.id,
        "✍️ النص لا يُستقبل هنا. اكتب مشاركتك النصية من التطبيق (شاشة «شارك»).\n"
        "أما هنا في البوت فأرسل <b>الصور والملفات</b> فقط، ثم اضغط «✅ إنهاء المشاركة».",
        _finish_kb(cid),
    )
    return True


async def on_sharek_media(update: Update, context: ContextTypes.DEFAULT_TYPE, m: dict[str, Any]) -> bool:
    cid = context.user_data.get("sh_sub")
    if not cid:
        return False
    s, chat, tg = svc(context), update.effective_chat.id, update.effective_user.id
    raw_kind = str(m.get("media_kind") or "document").lower()
    kind = raw_kind if raw_kind in ("photo", "document", "video", "audio", "voice") else "document"
    caption = (getattr(update.effective_message, "caption", None) or "").strip() or None
    res = await s.store.rpc(
        "add_sharek_item",
        {
            "p_tg": tg,
            "p_campaign": cid,
            "p_item": {
                "item_kind": kind,
                "file_id": m["file_id"],
                "file_name": m.get("name"),
                "text_content": caption,
            },
        },
    )
    st = res.get("status")
    if st == "ok":
        group = getattr(update.effective_message, "media_group_id", None)
        if group and context.user_data.get("sh_group") == group:
            return True
        context.user_data["sh_group"] = group
        await say(context.bot, chat, f"استلمت المرفق ✅ (المجموع: {res['count']}).\nأرسل المزيد أو اضغط «إنهاء المشاركة».", _finish_kb(cid))
    else:
        context.user_data.pop("sh_sub", None)
        await say(context.bot, chat, "انتهت جلسة المشاركة أو اكتمل العدد المسموح.")
    return True


async def send_sharek_items_to_staff(bot: Any, s: Services, chat_id: int, actor_id: int, cid: int) -> None:
    res = await s.store.rpc("sharek_campaign_results", {"p_actor": actor_id, "p_campaign": cid, "p_group": None})
    camp = res.get("campaign") or {}
    subs = res.get("submissions") or []
    if not subs:
        return await say(bot, chat_id, f"📥 لا توجد ملفات أو مشاركات مستلمة في حملة «{escape(camp.get('title', ''))}».")
    await say(bot, chat_id, f"📥 <b>المشاركات المستلمة في «{escape(camp.get('title', ''))}»</b> ({len(subs)} عنصر):")
    for item in subs[:30]:
        header = (
            f"👤 <b>{escape(str(item.get('name') or ''))}</b> (<code>{escape(str(item.get('no') or ''))}</code>)\n"
            f"👥 المجموعة: {escape(str(item.get('theory') or ''))} · عملي {escape(str(item.get('practical') or ''))}"
        )
        fid = item.get("file_id")
        ikind = str(item.get("item_kind") or "text").lower()
        tcontent = str(item.get("text_content") or "").strip()
        if fid:
            cap = f"{header}\n\n💬 {escape(tcontent)}" if tcontent else header
            send_kind = ikind if ikind in ("photo", "video", "audio", "voice", "document") else "document"
            try:
                await send_by_kind(bot, chat_id, send_kind, fid, caption=cap)
            except BadRequest:
                alt_kind = "document" if send_kind == "photo" else "photo"
                try:
                    await send_by_kind(bot, chat_id, alt_kind, fid, caption=cap)
                except TelegramError:
                    pass
        elif tcontent:
            await say(bot, chat_id, f"{header}\n\n💬 {escape(tcontent)}")


async def on_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str], prof: dict[str, Any]) -> None:
    s, bot = svc(context), context.bot
    chat, tg = update.effective_chat.id, update.effective_user.id
    perms = prof.get("perms") or []
    if not can(perms, "sharek.participate"):
        return await say(bot, chat, f"هذه الميزة خاصة ب{cohort.get().students_noun}")

    if parts[0] == "m" and len(parts) > 1 and parts[1] == "sharek":
        return await show_sharek_list(bot, s, chat, tg)

    action = parts[1] if len(parts) > 1 else ""
    if action in ("open", "go") and len(parts) > 2 and parts[2].isdigit():
        return await open_campaign(bot, s, chat, tg, int(parts[2]), context)

    if action == "v" and len(parts) > 3 and parts[2].isdigit() and parts[3].isdigit():
        cid, idx = int(parts[2]), int(parts[3])
        res = await s.store.rpc("vote_sharek", {"p_tg": tg, "p_campaign": cid, "p_option_idx": idx})
        if res.get("status") == "ok":
            return await say(bot, chat, "تم تسجيل صوتك بنجاح ✅")
        if res.get("status") == "already_voted":
            return await say(bot, chat, "سبق لك التصويت في هذا الاستفتاء.")
        return await say(bot, chat, "هذا الاستفتاء لم يعد متاحًا.")

    if action == "fin" and len(parts) > 2 and parts[2].isdigit():
        cid = int(parts[2])
        res = await s.store.rpc("finish_sharek_submission", {"p_tg": tg, "p_campaign": cid})
        if res.get("status") == "no_items":
            return await say(bot, chat, "لم ترسل أي مشاركة بعد. أرسل نصًا أو صورة أو ملفًا ثم اضغط «إنهاء المشاركة».", _finish_kb(cid))
        context.user_data.pop("sh_sub", None)
        if res.get("status") == "ok":
            return await say(bot, chat, f"تم إنهاء واعتماد مشاركتك ({res.get('count', 1)} عنصر) ✅ شكرًا لمشاركتك!")
        return await say(bot, chat, "تعذّر إنهاء المشاركة.")

    if action == "staff" and len(parts) > 2 and parts[2].isdigit() and can(perms, "sharek.publish"):
        return await send_sharek_items_to_staff(bot, s, chat, tg, int(parts[2]))

    if action == "cancel":
        context.user_data.pop("sh_sub", None)
        return await say(bot, chat, "أوقفت إرسال المشاركة مؤقتًا. يمكنك استكمالها لاحقًا قبل إنهائها.")


# =====================================================================
# 2) اختبارات MCQ داخل البوت (بالأسلوب التفاعلي القديم عبر Poll.QUIZ)
# =====================================================================
async def show_mcq_subjects(bot: Any, s: Services, chat_id: int, tg_id: int, prof: dict[str, Any]) -> None:
    """يعرض المواد التي تتوفر لها اختبارات MCQ، مع زر رفع اختبار جديد للمشرفين."""
    exams = await s.store.rpc("list_mcq_exams", {"p_tg": tg_id, "p_subject": None})
    perms = prof.get("perms") or []
    can_up = can(perms, "mcq.upload")

    seen: dict[int, str] = {}
    for e in exams or []:
        sid = int(e["subject_id"])
        if sid not in seen:
            seen[sid] = e.get("subject") or f"مادة {sid}"

    buttons = [[btn(f"📚 {name}", f"mcq:s:{sid}")] for sid, name in seen.items()]
    if can_up:
        buttons.append([btn("➕ رفع اختبار MCQ (Excel)", "mcq:up")])
    buttons.append([btn("🔙 القائمة الرئيسية", "m:home")])

    if not seen:
        msg = "📭 لا توجد اختبارات متاحة حالياً."
        if can_up:
            msg += "\nيمكنك رفع اختبار جديد من الزر بالأسفل:"
        return await say(bot, chat_id, msg, kb(buttons))

    await say(bot, chat_id, "📝 اختر المادة:", kb(buttons))


async def show_mcq_exams_for_subject(bot: Any, s: Services, chat_id: int, tg_id: int, subject_id: int) -> None:
    exams = await s.store.rpc("list_mcq_exams", {"p_tg": tg_id, "p_subject": subject_id})
    if not exams:
        return await say(bot, chat_id, "📭 لا توجد اختبارات لهذه المادة حالياً.", kb([[btn("🔙 رجوع", "m:mcq")]]))
    buttons = [[btn(e["title"], f"mcq:v:{e['id']}")] for e in exams]
    buttons.append([btn("🔙 رجوع", "m:mcq")])
    await say(bot, chat_id, "📝 اختر الاختبار:", kb(buttons))


async def show_mcq_detail(bot: Any, s: Services, chat_id: int, tg_id: int, exam_id: int, prof: dict[str, Any]) -> None:
    res = await s.store.rpc("get_mcq_exam", {"p_tg": tg_id, "p_exam": exam_id})
    if res.get("status") != "ok":
        return await say(bot, chat_id, "⚠️ هذا الاختبار غير متاح.", kb([[btn("🔙 رجوع", "m:mcq")]]))
    exam = res["exam"]
    questions = res.get("questions") or []
    desc = exam.get("description") or "لا يوجد وصف"
    text = (
        f"📝 اسم الاختبار: {escape(exam['title'])}\n\n"
        f"📚 المادة: {escape(exam.get('subject') or '')}\n\n"
        f"📄 الوصف: {escape(desc)}\n\n"
        f"❓ عدد الأسئلة: {len(questions)}"
    )
    rows = [[btn("▶️ بدء الاختبار", f"mcq:start:{exam_id}")]]
    if can(prof.get("perms") or [], "mcq.upload"):
        rows.append([btn("🗑 حذف الاختبار", f"mcq:delc:{exam_id}")])
    rows.append([btn("🔙 رجوع", f"mcq:s:{exam['subject_id']}")])
    await say(bot, chat_id, text, kb(rows))


async def start_mcq_exam(
    bot: Any,
    s: Services,
    chat_id: int,
    tg_id: int,
    exam_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    is_retry: bool = False,
) -> None:
    res = await s.store.rpc("get_mcq_exam", {"p_tg": tg_id, "p_exam": exam_id})
    if res.get("status") != "ok":
        return await say(bot, chat_id, "⚠️ هذا الاختبار غير متاح.")
    questions = res.get("questions") or []
    if not questions:
        return await say(bot, chat_id, "⚠️ لا توجد أسئلة في هذا الاختبار.")

    context.user_data["active_exam"] = {
        "exam_id": exam_id,
        "questions": questions,
        "current_index": 0,
        "correct_count": 0,
        "chat_id": chat_id,
        "user_id": tg_id,
        "started_at": time.time(),
    }
    await say(
        bot,
        chat_id,
        "🔄 بدأت إعادة الاختبار." if is_retry else "▶️ سيبدأ الاختبار الآن، أجب على كل سؤال بالضغط على الخيار المناسب.",
    )
    await send_next_mcq_question(bot, context, chat_id, tg_id)


async def send_next_mcq_question(bot: Any, context: ContextTypes.DEFAULT_TYPE, chat_id: int, tg_id: int) -> None:
    exam_state = context.user_data.get("active_exam")
    if not exam_state:
        return
    idx = exam_state["current_index"]
    questions = exam_state["questions"]
    if idx >= len(questions):
        await finish_mcq_exam(bot, context, chat_id, tg_id)
        return

    q = questions[idx]
    raw_opts = q.get("options") or [q.get("option1", ""), q.get("option2", ""), q.get("option3", ""), q.get("option4", "")]
    options = [_clip(opt, POLL_OPTION_MAX) or f"Option {i + 1}" for i, opt in enumerate(raw_opts[:4])]
    full_question = f"({idx + 1}/{len(questions)}) {q['question']}"

    try:
        if len(full_question) > POLL_QUESTION_MAX:
            await bot.send_message(chat_id=chat_id, text=full_question)
        message = await bot.send_poll(
            chat_id=chat_id,
            question=_clip(full_question, POLL_QUESTION_MAX),
            options=options,
            type=Poll.QUIZ,
            correct_option_id=int(q.get("correct_index", 0)),
            is_anonymous=False,
        )
    except (TimedOut, NetworkError) as exc:
        log.warning("تعذّر إرسال السؤال %s: %s", idx + 1, exc)
        await bot.send_message(chat_id=chat_id, text="⚠️ حدثت مشكلة اتصال مؤقتة، أعد تشغيل الاختبار من القائمة.")
        context.user_data.pop("active_exam", None)
        return
    except BadRequest as exc:
        log.error("سؤال غير صالح (index=%s): %s", idx, exc)
        exam_state["current_index"] += 1
        exam_state["skipped"] = exam_state.get("skipped", 0) + 1   # لا يُحتسب في المقام ظلمًا
        await send_next_mcq_question(bot, context, chat_id, tg_id)
        return

    exam_state["poll_id"] = message.poll.id   # الحالة في user_data (تُحفظ عبر إعادة التشغيل، ولا تسرّب ذاكرة)
    exam_state["chat_id"] = chat_id


async def finish_mcq_exam(bot: Any, context: ContextTypes.DEFAULT_TYPE, chat_id: int, tg_id: int) -> None:
    exam_state = context.user_data.get("active_exam")
    if not exam_state:
        return
    total = max(1, len(exam_state["questions"]) - int(exam_state.get("skipped", 0)))
    correct = exam_state["correct_count"]
    exam_id = exam_state["exam_id"]
    elapsed = max(1, int(time.time() - float(exam_state.get("started_at") or time.time())))
    context.user_data.pop("active_exam", None)

    try:
        await svc(context).store.rpc(
            "submit_mcq_attempt",
            {
                "p_tg": tg_id,
                "p_exam": exam_id,
                "p_score": correct,
                "p_total": total,
                "p_elapsed": elapsed,
                "p_timer_enabled": False,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("تعذّر حفظ نتيجة الاختبار في السجل: %s", exc)

    text = f"🎉 انتهى الاختبار.\n\n📊 درجتك:\n\n{correct} / {total}"
    markup = kb([
        [btn("🔄 إعادة الاختبار", f"mcq:retry:{exam_id}")],
        [btn("🔙 قائمة الاختبارات", "m:mcq")],
    ])
    await say(bot, chat_id, text, markup)


@per_user
async def on_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالج إجابات استفتاءات الـ Quiz في تيليجرام لإرسال السؤال التالي تلقائيًا."""
    poll_answer = update.poll_answer
    if not poll_answer:
        return
    exam_state = context.user_data.get("active_exam")
    if not exam_state or exam_state.get("poll_id") != poll_answer.poll_id:
        return   # إجابة قديمة/مكررة أو اختبار انتهى
    exam_state["poll_id"] = None
    poll_info = {"user_id": poll_answer.user.id, "chat_id": exam_state.get("chat_id") or poll_answer.user.id}
    idx = exam_state["current_index"]
    questions = exam_state["questions"]
    if idx >= len(questions):
        return
    q = questions[idx]
    if poll_answer.option_ids and poll_answer.option_ids[0] == int(q.get("correct_index", 0)):
        exam_state["correct_count"] += 1
    exam_state["current_index"] += 1
    await send_next_mcq_question(context.bot, context, poll_info["chat_id"], poll_info["user_id"])


# ---------------- رفع وحذف اختبارات MCQ من داخل البوت ----------------
async def on_mcq_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str], prof: dict[str, Any]) -> None:
    s, bot = svc(context), context.bot
    chat, tg = update.effective_chat.id, update.effective_user.id
    perms = prof.get("perms") or []
    if not can(perms, "mcq.take"):
        return await say(bot, chat, "لا تملك صلاحية الوصول للاختبارات.")

    if parts[0] == "m" and len(parts) > 1 and parts[1] == "mcq":
        context.user_data.pop("mcq_up", None)
        return await show_mcq_subjects(bot, s, chat, tg, prof)

    action = parts[1] if len(parts) > 1 else ""
    arg = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    if action == "s" and arg:
        return await show_mcq_exams_for_subject(bot, s, chat, tg, arg)
    if action == "v" and arg:
        return await show_mcq_detail(bot, s, chat, tg, arg, prof)
    if action == "start" and arg:
        return await start_mcq_exam(bot, s, chat, tg, arg, context, is_retry=False)
    if action == "retry" and arg:
        return await start_mcq_exam(bot, s, chat, tg, arg, context, is_retry=True)

    if action == "delc" and arg:
        if not can(perms, "mcq.upload"):
            return await say(bot, chat, "لا تملك صلاحية حذف الاختبارات.")
        res = await s.store.rpc("get_mcq_exam", {"p_tg": tg, "p_exam": arg})
        if res.get("status") != "ok":
            return await say(bot, chat, "⚠️ الاختبار غير موجود.")
        title = res["exam"]["title"]
        return await say(
            bot,
            chat,
            f"⚠️ هل أنت متأكد من رغبتك في حذف الاختبار «{escape(title)}»؟\n\n"
            "سيؤدي هذا إلى إخفائه مع جميع أسئلته ونتائجه.",
            kb([[btn("✅ نعم، تأكيد الحذف", f"mcq:del:{arg}")], [btn("❌ إلغاء", f"mcq:v:{arg}")]]),
        )

    if action == "del" and arg:
        if not can(perms, "mcq.upload"):
            return await say(bot, chat, "لا تملك صلاحية حذف الاختبارات.")
        res = await s.store.rpc("delete_mcq_exam", {"p_actor": tg, "p_exam": arg})
        if res.get("status") == "ok":
            return await say(bot, chat, "🗑 تم حذف الاختبار بنجاح.", kb([[btn("🔙 رجوع للاختبارات", "m:mcq")]]))
        return await say(bot, chat, "تعذّر حذف الاختبار.")

    if action == "up":
        if not can(perms, "mcq.upload"):
            return await say(bot, chat, "لا تملك صلاحية رفع الاختبارات.")
        subjects = prof.get("subjects") or []
        rows = [[btn(f"📚 {sub['name']}", f"mcq:ups:{sub['id']}")] for sub in subjects]
        rows.append([btn("🔙 إلغاء", "m:mcq")])
        return await say(bot, chat, "📚 اختر المادة لرفع اختبار MCQ جديد:", kb(rows))

    if action == "ups" and arg:
        if not can(perms, "mcq.upload"):
            return await say(bot, chat, "لا تملك صلاحية رفع الاختبارات.")
        context.user_data["mcq_up"] = {"step": "name", "subject_id": arg}
        return await say(bot, chat, "📝 أدخل اسم الاختبار:", kb([[btn("🔙 إلغاء", "m:mcq")]]))


async def on_mcq_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    up = context.user_data.get("mcq_up")
    if not up:
        return False
    chat = update.effective_chat.id
    text = (update.effective_message.text or "").strip()
    if not text:
        return False

    if up.get("step") == "name":
        up["title"] = text[:160]
        up["step"] = "desc"
        await say(context.bot, chat, "📄 أدخل وصف الاختبار (أو أرسل «-» لتخطي الوصف):")
        return True

    if up.get("step") == "desc":
        up["description"] = "" if text in ("-", "لا يوجد", "تخطي") else text[:600]
        up["step"] = "file"
        await say(
            context.bot,
            chat,
            "📎 أرسل الآن ملف Excel (`.xlsx`) الخاص بالأسئلة.\n\n"
            "يجب أن يحتوي الملف على الأعمدة التالية بالضبط:\n"
            f"<code>{' | '.join(MCQ_EXCEL_COLUMNS)}</code>",
        )
        return True

    return False


async def on_mcq_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    up = context.user_data.get("mcq_up")
    if not up or up.get("step") != "file":
        return False
    chat, tg = update.effective_chat.id, update.effective_user.id
    doc = update.effective_message.document
    if not doc or not (doc.file_name or "").lower().endswith((".xlsx", ".xls")):
        await say(context.bot, chat, "❌ يرجى إرسال ملف Excel بصيغة `.xlsx`.")
        return True
    if (doc.file_name or "").lower().endswith(".xls"):
        await say(context.bot, chat, "❌ صيغة `.xls` القديمة غير مدعومة، احفظ الملف بصيغة `.xlsx` وأعد الإرسال.")
        return True

    if (doc.file_size or 0) > 5 * 1024 * 1024:   # حدّ تنزيل البوت 20MB؛ ملف أسئلة لا يحتاج أكثر من 5MB
        await say(context.bot, chat, "❌ حجم الملف كبير (الحد 5MB). قلّل عدد الصفوف أو احذف الصور من الملف.")
        return True
    status_msg = await say(context.bot, chat, "⏳ جارٍ تحميل الملف وفحصه...")
    try:
        tg_file = await doc.get_file()
        raw_bytes = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001
        log.warning("فشل تحميل ملف MCQ Excel: %s", exc)
        await status_msg.edit_text("❌ تعذّر تحميل الملف، أعد إرساله.")
        return True

    questions, errors = await asyncio.to_thread(parse_mcq_excel, raw_bytes)
    if errors or not questions:
        shown = "\n".join(errors[:10]) if errors else "لم يتم العثور على أسئلة صالحة."
        await status_msg.edit_text(f"❌ وُجدت أخطاء في الملف، صحّحها وأعد الإرسال:\n\n{shown}")
        return True

    await status_msg.edit_text(f"⏳ جارٍ حفظ {len(questions)} سؤال...")
    try:
        res = await svc(context).store.rpc(
            "create_mcq_exam",
            {
                "p_actor": tg,
                "p_subject": up["subject_id"],
                "p_title": up["title"],
                "p_description": up.get("description") or None,
                "p_duration": 15,
                "p_questions": questions,
            },
        )
    except Exception as exc:  # noqa: BLE001
        log.error("فشل حفظ اختبار MCQ: %s", exc)
        await status_msg.edit_text("❌ فشل حفظ الاختبار، حاول مرة أخرى.")
        return True

    context.user_data.pop("mcq_up", None)
    if res.get("status") == "ok":
        await status_msg.edit_text(
            f"✅ تم حفظ الاختبار بنجاح ({len(questions)} سؤال).",
            reply_markup=kb([[btn("🧪 عرض الاختبارات", "m:mcq")], [btn("🔙 القائمة الرئيسية", "m:home")]]),
        )
    else:
        await status_msg.edit_text("❌ تعذّر حفظ الاختبار.")
    return True

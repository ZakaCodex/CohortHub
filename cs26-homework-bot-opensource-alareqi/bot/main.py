"""البوت — Polling على Orihost.
في البوت: الدخول والربط + نظام الملفات + نظام الواجبات (يعرض غير المسلَّمة فقط ويدعم التعديل المباشر من التطبيق) + نظام اختبارات MCQ التفاعلي عبر استفتاءات تيليجرام (إلى جانب الـ Mini App) + الإشعارات + استقبال المرفقات من الـ Mini App + الربط السريع للمجتمعات بأزرار /where."""
from __future__ import annotations

import logging
import os
from html import escape
from typing import Any

from telegram import MenuButtonWebApp, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    TypeHandler,
    PersistenceInput,
    PicklePersistence,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PollAnswerHandler,
    filters,
)

from bot import assignments, files, media, phase4, texts
from bot.locks import per_user
from bot.services import Services, svc
from bot.ui import btn, kb, kb_confirm, kb_entry, kb_menu, say, webapp_btn
from bot.worker import Worker
from core import cohort
from core.config import load_settings
from core.flood import NOTICE, OK, FloodGuard
from core.permissions import can
from core.store import StoreError
from core.text import clean_name, normalize_academic_no

log = logging.getLogger("bot")


async def send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, prof: dict[str, Any], edit_msg: Any = None) -> None:
    s = svc(context)
    st = prof.get("student") or {}
    roles = [r["role"] for r in prof.get("roles", [])]
    text = texts.menu(prof["user"].get("name") or "", prof["user"]["kind"],
                      st.get("theory_group_id"), st.get("practical_group_id"), roles)
    markup = kb_menu(s.settings.web_app_url, prof["user"]["kind"], prof.get("perms", []))
    if edit_msg is not None and hasattr(edit_msg, "edit_text"):
        try:
            await edit_msg.edit_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            return
        except Exception:  # noqa: BLE001
            pass
    await say(context.bot, update.effective_chat.id, text, markup)


async def send_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await say(context.bot, update.effective_chat.id, texts.welcome_new(update.effective_user.first_name or ""), kb_entry())


async def _redirect_to_miniapp(bot: Any, chat_id: int, web_url: str, title: str, view: str) -> None:
    w = webapp_btn(f"📱 فتح «{title}» في التطبيق", web_url, view)
    markup = kb([[w]]) if w else None
    await say(bot, chat_id, f"✨ ميزة <b>{escape(title)}</b> متاحة بالكامل داخل التطبيق (Mini App).\nاضغط الزر بالأسفل للانتقال إليها مباشرة:", markup)


@per_user
async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s, chat, tg = svc(context), update.effective_chat.id, update.effective_user
    ud = context.user_data
    for k in ("await", "sub", "attach", "up", "sh_sub", "active_exam", "mcq_up"):
        ud.pop(k, None)
    try:
        await s.store.rpc("unblock_user", {"p_tg": tg.id})
    except StoreError:
        pass
    prof = await s.profile(tg, force=True)
    kind = prof["user"]["kind"]
    if kind == "banned":
        return await say(context.bot, chat, texts.BANNED)
    if kind == "new":
        return await send_entry(update, context)

    payload = context.args[0] if context.args else ""
    kind_p, _, ident = payload.partition("_")
    if ident.isdigit():
        n = int(ident)
        if kind_p == "up" and can(prof["perms"], "files.upload"):
            return await files.start_upload(context.bot, chat, n, context)
        if kind_p == "ad" and can(prof["perms"], "assignments.create"):
            return await assignments.start_attach(context.bot, chat, n, context)
        if kind_p == "sub" and can(prof["perms"], "assignments.submit"):
            return await assignments.start_submit_direct(context.bot, s, chat, tg.id, n, context)
        if kind_p == "a" and can(prof["perms"], "assignments.submit"):
            return await assignments.show_detail(context.bot, s, chat, tg.id, n)
        if kind_p == "grv" and can(prof["perms"], "affairs.use"):
            return await _redirect_to_miniapp(context.bot, chat, s.settings.web_app_url, "تظلماتي", "affairs-grv")
        if kind_p == "sh" and can(prof["perms"], "sharek.participate"):
            # استقبال ملفات/صور المشاركة في البوت عند التحويل من التطبيق
            return await phase4.open_campaign(context.bot, s, chat, tg.id, n, context)
    await send_menu(update, context, prof)


@per_user
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data or ""
    s, tg, chat = svc(context), update.effective_user, update.effective_chat.id
    parts = data.split(":")

    if parts[0] == "recv":
        return await assignments.on_received(update, context, int(parts[1]))
    if parts[0] == "wh":
        return await on_where_cb(update, context, parts)
    await q.answer()
    if parts[0] == "noop":
        return
    prof = await s.profile(tg)
    if prof["user"]["kind"] == "banned":
        return await say(context.bot, chat, texts.BANNED)
    if prof["user"]["kind"] == "student":
        context.user_data.pop("await", None)

    try:
        if data in ("start:link", "link:retry"):
            if prof["user"]["kind"] == "student":
                await say(context.bot, chat, texts.already_linked())
                return await send_menu(update, context, prof)
            context.user_data["await"] = "acad"
            await say(context.bot, chat, texts.ASK_ACADEMIC_NO if data == "start:link" else texts.LINK_CANCELLED)
        elif data == "start:guest":
            if prof["user"]["kind"] == "student":
                return await send_menu(update, context, prof)
            context.user_data["await"] = "guest_name"
            await say(context.bot, chat, texts.ASK_GUEST_NAME)
        elif data.startswith("link:ok:"):
            await confirm_link(update, context, normalize_academic_no(parts[2]))
        elif data == "m:home":
            context.user_data.pop("mcq_up", None)
            await send_menu(update, context, prof, edit_msg=q.message)
        elif data == "m:files" and can(prof["perms"], "files.view"):
            await files.show_subjects(context.bot, chat, prof, edit_msg=q.message)
        elif data == "m:asg" and can(prof["perms"], "assignments.submit"):
            await assignments.show_subjects(context.bot, s, chat, tg.id, prof)
        elif (data == "m:mcq" or parts[0] == "mcq") and can(prof["perms"], "mcq.take"):
            await phase4.on_mcq_cb(update, context, parts, prof)
        elif parts[0] == "sh" and len(parts) > 1 and parts[1] in ("fin", "cancel", "open", "go", "v", "staff"):
            # إنهاء أو إلغاء جلسة إرسال ملفات مشاركة في «شارك» أو استعراض المشاركات للمشرف
            await phase4.on_cb(update, context, parts, prof)
        elif data == "m:sharek" or parts[0] == "sh":
            await _redirect_to_miniapp(context.bot, chat, s.settings.web_app_url, "شارك", "sharek")
        elif data == "m:affairs" or parts[0] in ("af", "grv"):
            await _redirect_to_miniapp(context.bot, chat, s.settings.web_app_url, "شؤوني والتظلمات", "affairs")
        elif data == "m:ai" or parts[0] in ("ai", "tkt"):
            await _redirect_to_miniapp(context.bot, chat, s.settings.web_app_url, "المساعد الذكي والدعم", "assistant")
        elif parts[0] == "fl" and can(prof["perms"], "files.view"):
            await files.on_cb(update, context, parts, prof)
        elif parts[0] in ("a", "sub") and can(prof["perms"], "assignments.submit"):
            await assignments.on_cb(update, context, parts, prof)
        elif parts[0] == "ad" and can(prof["perms"], "assignments.create"):
            await assignments.on_cb(update, context, parts, prof)
        else:
            await say(context.bot, chat, texts.LOCKED_MSG)   # لا ردّ صامت عند ميزة مقفلة/غير مصرّح بها
    except StoreError as exc:
        log.warning("callback %s failed: %s", data, exc)
        await say(context.bot, chat, texts.GENERIC_ERROR if not exc.forbidden else "لا تملك صلاحية لهذا الإجراء.")


async def confirm_link(update: Update, context: ContextTypes.DEFAULT_TYPE, no: str | None) -> None:
    s, tg, chat = svc(context), update.effective_user, update.effective_chat.id
    if not no:
        return
    res = await s.store.link_student(tg.id, no, tg.username)
    context.user_data.pop("await", None)
    status = res["status"]
    if status == "ok":
        s.forget(tg.id)
        await say(context.bot, chat, texts.linked_ok(res["full_name"]))
        await send_menu(update, context, await s.profile(tg, force=True))
    elif status == "already_linked":
        s.forget(tg.id)
        await say(context.bot, chat, texts.already_linked())
        await send_menu(update, context, await s.profile(tg, force=True))
    elif status == "taken":
        await say(context.bot, chat, texts.taken(s.developer_contact))
    elif status == "rate_limited":
        await say(context.bot, chat, texts.RATE_LIMITED)
    else:
        await say(context.bot, chat, texts.NOT_FOUND)


@per_user
async def on_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/backup — للمطوّر فقط: يرسل نسخة احتياطية فورية إلى محادثته (النسخة الأسبوعية تلقائية)."""
    s, tg, chat = svc(context), update.effective_user, update.effective_chat.id
    prof = await s.profile(tg)
    if not can(prof["perms"], "roles.manage"):
        return
    worker = context.application.bot_data.get("worker")
    if worker is None:
        return await say(context.bot, chat, "العامل غير جاهز، أعد المحاولة بعد لحظات.")
    await say(context.bot, chat, "⏳ جارٍ تجهيز النسخة الاحتياطية…")
    try:
        from core.timeutil import now_utc, to_sanaa
        await worker.run_backup([tg.id], to_sanaa(now_utc()).date().isoformat())
    except Exception as exc:  # noqa: BLE001
        log.warning("manual backup failed: %s", exc)
        await say(context.bot, chat, "تعذّر إنشاء النسخة. راجع سجلات البوت، وتأكد من تشغيل 0010_backup.sql.")


@per_user
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s, tg, chat = svc(context), update.effective_user, update.effective_chat.id
    text = update.effective_message.text or ""
    prof = await s.profile(tg)
    kind = prof["user"]["kind"]
    if kind == "banned":
        return await say(context.bot, chat, texts.BANNED)

    if kind == "student":
        context.user_data.pop("await", None)

    awaiting = context.user_data.get("await")
    if awaiting is None and kind == "new" and normalize_academic_no(text):
        awaiting = "acad"

    if awaiting == "acad":
        no = normalize_academic_no(text)
        if not no:
            return await say(context.bot, chat, texts.BAD_ACADEMIC_NO)
        res = await s.store.lookup_student(tg.id, no)
        if res["status"] == "ok":
            return await say(context.bot, chat, texts.confirm_link(res["full_name"], res["theory_group_id"], res["practical_group_id"]), kb_confirm(no))
        if res["status"] == "already_linked":
            context.user_data.pop("await", None)
            s.forget(tg.id)
            await say(context.bot, chat, texts.already_linked())
            return await send_menu(update, context, await s.profile(tg, force=True))
        if res["status"] == "taken":
            return await say(context.bot, chat, texts.taken(s.developer_contact))
        if res["status"] == "rate_limited":
            return await say(context.bot, chat, texts.RATE_LIMITED)
        return await say(context.bot, chat, texts.NOT_FOUND)

    if awaiting == "guest_name":
        name = clean_name(text)
        if not name:
            return await say(context.bot, chat, texts.BAD_GUEST_NAME)
        res = await s.store.register_guest(tg.id, name, tg.username)
        if res["status"] == "ok":
            context.user_data.pop("await", None)
            s.forget(tg.id)
            await say(context.bot, chat, texts.guest_ok(name))
            return await send_menu(update, context, await s.profile(tg, force=True))
        if res["status"] == "is_student":
            context.user_data.pop("await", None)
            s.forget(tg.id)
            return await send_menu(update, context, await s.profile(tg, force=True))
        return await say(context.bot, chat, texts.BAD_GUEST_NAME)

    if context.user_data.get("sub") and await assignments.on_submission_text(update, context):
        return

    if context.user_data.get("mcq_up") and await phase4.on_mcq_text(update, context):
        return

    if context.user_data.get("sh_sub") and await phase4.on_sharek_text(update, context):
        return

    if kind == "new":
        return await send_entry(update, context)
    await send_menu(update, context, prof)


@per_user
async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """الملفات الواردة: تسليم واجب، أو رفع اختبار MCQ من Excel، أو مشاركة في شارك، أو مرفقات واجب قيد الإنشاء، أو رفع ملف للمجتمع."""
    m = media.extract(update.effective_message)
    if not m:
        return
    s, tg, chat = svc(context), update.effective_user, update.effective_chat.id
    prof = await s.profile(tg)
    if prof["user"]["kind"] == "banned":
        return
    if prof["user"]["kind"] == "new":
        return await send_entry(update, context)
    if prof["user"]["kind"] == "student":
        context.user_data.pop("await", None)
    ud = context.user_data
    if ud.get("sub"):
        return await assignments.on_submission_media(update, context, m)
    if ud.get("mcq_up") and await phase4.on_mcq_file(update, context):
        return
    if ud.get("sh_sub"):
        await phase4.on_sharek_media(update, context, m)
        return
    if ud.get("attach"):
        return await assignments.on_attach_media(update, context, m)
    if ud.get("up") and can(prof["perms"], "files.upload"):
        await files.handle_upload(update, context, m)
        return
    if can(prof["perms"], "files.upload"):
        if await files.handle_upload(update, context, m, silent_if_no_draft=True):
            return
        await say(context.bot, chat, "لا يوجد رفع جارٍ. ابدأ من التطبيق: الملفات ← رفع ملف.")
        return
    # لا نُلحق أي ملف بمشاركة (شارك) تلقائيًا: لا يُقبل الملف إلا ضمن جلسة بدأها الطالب صراحةً
    # (تسليم واجب أو مشاركة)، حتى لا يذهب ملف واجب إلى مشاركة مفتوحة.
    await say(context.bot, chat, "لا أنتظر ملفًا الآن.\n"
                                 "• للتسليم: افتح الواجب من «📝 الواجبات» ثم «ابدأ التسليم».\n"
                                 "• للمشاركة: افتح «شارك» من التطبيق ثم «إرسال صور/ملفات عبر البوت».")


# ---------------- ربط المجتمعات والـ Topics بأزرار مباشرة عبر /where ----------------
def _where_main_kb() -> Any:
    return kb([
        [btn("📚 ربط بملفات ومحاضرات مادة", "wh:m:subj")],
        [btn("📣 ربط بإعلانات وواجبات مجموعة", "wh:m:not")],
        [btn("📌 ربط بتقارير مجموعة", "wh:m:rep")],
        [btn("👨‍🏫 ربط بتسليمات دكتور", "wh:m:doc")],
    ])


async def on_where(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/where داخل أي topic: يعرض chat_id وtopic_id مع أزرار ربط فورية بضغطة زر (للمطوّر فقط)."""
    prof = await svc(context).profile(update.effective_user)
    if not can(prof.get("perms", []), "communities.manage"):
        return
    msg = update.effective_message
    await say(
        context.bot,
        update.effective_chat.id,
        f"📍 <b>معرّفات هذا القسم (Topic):</b>\n"
        f"• chat_id: <code>{update.effective_chat.id}</code>\n"
        f"• topic_id: <code>{msg.message_thread_id or 'لا يوجد (عام)'}</code>\n\n"
        "👇 <b>اختر ما تريد ربط هذا الـ Topic به مباشرة بضغطة زر:</b>",
        _where_main_kb(),
        thread=msg.message_thread_id,
    )


async def on_where_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]) -> None:
    q = update.callback_query
    s, tg = svc(context), update.effective_user
    prof = await s.profile(tg)
    if not can(prof.get("perms", []), "communities.manage"):
        return await q.answer("هذا الزر للمطوّر فقط.", show_alert=True)
    await q.answer()
    chat_id = update.effective_chat.id
    topic_id = q.message.message_thread_id if q.message else None
    action = parts[1] if len(parts) > 1 else ""

    if action == "back":
        return await q.edit_message_reply_markup(_where_main_kb())

    if action == "m":
        kind = parts[2] if len(parts) > 2 else ""
        if kind == "subj":
            subjects = prof.get("subjects") or []
            rows = [[btn(f"📚 {sub['name']}", f"wh:set:subj:{sub['id']}")] for sub in subjects]
            rows.append([btn("🔙 رجوع", "wh:back")])
            return await q.edit_message_reply_markup(kb(rows))
        if kind == "not":
            rows = [[btn(f"📣 إعلانات وواجبات {g}", f"wh:set:not:{g}")] for g in cohort.get().theory_ids()]
            rows.append([btn("🔙 رجوع", "wh:back")])
            return await q.edit_message_reply_markup(kb(rows))
        if kind == "rep":
            rows = [[btn(f"📌 تقارير {g}", f"wh:set:rep:{g}")] for g in cohort.get().theory_ids()]
            rows.append([btn("🔙 رجوع", "wh:back")])
            return await q.edit_message_reply_markup(kb(rows))
        if kind == "doc":
            cat = await s.store.rpc("dev_catalog_and_roles", {"p_actor": tg.id})
            docs = cat.get("doctors") or []
            if not docs:
                return await q.answer("أضف دكتورًا أولًا من لوحة المطوّر في التطبيق.", show_alert=True)
            rows = [[btn(f"👨‍🏫 {d['name']}", f"wh:doc:{d['id']}")] for d in docs]
            rows.append([btn("🔙 رجوع", "wh:back")])
            return await q.edit_message_reply_markup(kb(rows))

    if action == "doc":
        doc_id = int(parts[2])
        ch = cohort.get()
        rows = [[btn(f"نظري {g}", f"wh:set:doc:{doc_id}:theory:{g}") for g in ch.theory_ids()]]
        for t in ch.theory_ids():       # صف عملي لكل مجموعة نظرية (أي عدد مجموعات/عملي)
            rows.append([btn(f"عملي {g}", f"wh:set:doc:{doc_id}:practical:{g}") for g in ch.practical_of(t)])
        rows.append([btn("🔙 رجوع", "wh:m:doc")])
        return await q.edit_message_reply_markup(kb(rows))

    if action == "set":
        target = parts[2]
        if target == "subj":
            sid = int(parts[3])
            subj_name = next((x["name"] for x in (prof.get("subjects") or []) if int(x["id"]) == sid), f"مادة {sid}")
            key, label = f"cohort:subject:{sid}", f"ملفات {subj_name}"
        elif target == "not":
            grp = parts[3]
            key, label = f"cohort:notices:{grp}", f"إعلانات وواجبات {grp}"
        elif target == "rep":
            grp = parts[3]
            key, label = f"cohort:theory:{grp}", f"تقارير {grp}"
        elif target == "doc":
            doc_id, part, grp = int(parts[3]), parts[4], parts[5]
            key, label = f"doctor:{doc_id}:{part}:{grp}", f"تسليمات دكتور #{doc_id} ({grp})"
        else:
            return
        await s.store.rpc(
            "dev_upsert_destination",
            {"p_actor": tg.id, "p_key": key, "p_chat": chat_id, "p_topic": topic_id, "p_label": label},
        )
        await q.edit_message_text(
            f"✅ <b>تم ربط هذا الـ Topic بنجاح!</b>\n"
            f"• الوجهة: <b>{escape(label)}</b>\n"
            f"• المفتاح: <code>{escape(key)}</code>\n"
            f"• chat_id: <code>{chat_id}</code> · topic_id: <code>{topic_id or 'عام'}</code>",
            parse_mode="HTML",
            reply_markup=_where_main_kb(),
        )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("خطأ غير متوقع", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(update.effective_chat.id, texts.GENERIC_ERROR)
        except Exception:  # noqa: BLE001
            pass


async def post_init(app: Application) -> None:
    s = Services()
    app.bot_data["svc"] = s
    try:
        s.cache.update_versions(await s.store.versions())
    except StoreError as exc:
        log.warning("تعذّر قراءة الإصدارات عند الإقلاع: %s", exc)
    s.start_watcher()
    worker = Worker(app.bot, s)
    worker.start()
    app.bot_data["worker"] = worker
    if s.settings.web_app_url:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="التطبيق", web_app=WebAppInfo(url=s.settings.web_app_url)))


async def post_shutdown(app: Application) -> None:
    w: Worker | None = app.bot_data.get("worker")
    if w:
        w.stop()
    s: Services | None = app.bot_data.get("svc")
    if s:
        await s.close()


def make_flood_gate(guard: FloodGuard):
    """بوابة تُنفَّذ قبل كل المعالجات: تُسقط سيل الطلبات من مستخدم واحد (إغراق) وتترك الاستخدام الطبيعي."""
    async def flood_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        if user is None or update.poll_answer is not None:
            return
        kind = "cb" if update.callback_query is not None else ("msg" if update.effective_message is not None else None)
        if kind is None:
            return
        verdict = guard.check(user.id, kind)
        if verdict == OK:
            return
        if verdict == NOTICE:
            log.warning("flood: dropping updates from user %s (%s)", user.id, kind)
            try:
                if update.callback_query is not None:
                    await update.callback_query.answer("الرجاء الانتظار قليلًا ⏳")
                else:
                    await context.bot.send_message(update.effective_chat.id, "⏳ طلبات كثيرة بسرعة. انتظر لحظات ثم حاول مجددًا.")
            except Exception:  # noqa: BLE001
                pass
        raise ApplicationHandlerStop
    return flood_gate


def build_app() -> Application:
    token = load_settings().bot_token
    # نحفظ user_data فقط (جلسات التسليم والاختبارات) كي لا تضيع عند إعادة تشغيل البوت
    persistence = PicklePersistence(
        filepath=os.getenv("BOT_STATE_FILE", "bot_state.pickle"),
        store_data=PersistenceInput(bot_data=False, chat_data=False, callback_data=False),
        update_interval=30,
    )
    app = (Application.builder().token(token).persistence(persistence).concurrent_updates(True)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    private = filters.ChatType.PRIVATE
    app.add_handler(TypeHandler(Update, make_flood_gate(FloodGuard.from_env())), group=-1)
    app.add_handler(CommandHandler("start", on_start, filters=private))
    app.add_handler(CommandHandler("where", on_where, filters=filters.ChatType.GROUPS))
    app.add_handler(CommandHandler("backup", on_backup, filters=private))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(PollAnswerHandler(phase4.on_poll_answer))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(private & ~filters.TEXT & ~filters.COMMAND
                                   & (filters.Document.ALL | filters.PHOTO | filters.VIDEO | filters.AUDIO
                                      | filters.VOICE | filters.ANIMATION | filters.VIDEO_NOTE), on_media))
    app.add_error_handler(on_error)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_app().run_polling(allowed_updates=["message", "callback_query", "poll_answer"], drop_pending_updates=False)


if __name__ == "__main__":
    main()

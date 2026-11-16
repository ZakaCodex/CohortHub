"""عامل الإرسال (طابور الإشعارات بمعدل آمن) والمجدول (إغلاق الواجبات والتظلمات، التذكيرات، التقارير الأسبوعية، إعادة التسليم، التنظيف).
كل شيء محفوظ في القاعدة وآمن للتكرار، فلا ضرر لو أُعيد تشغيل البوت أو شُغّلت نسختان."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import logging
import time
from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError, TimedOut

from bot import assignments
from bot.media import send_by_kind
from bot.services import Services
from bot.ui import btn, kb, say, webapp_btn
from core import messages
from core.attendance import build_attendance_xlsx
from core.backup import BACKUP_TABLES, backup_caption, build_backup_parts
from core.barcode import render_barcode_png
from core.doctor import build_doctor_assignment_xlsx, build_doctor_group_xlsx
from core import cohort
from core.ratelimit import TokenBucket
from core.reports import compile_weekly_report, study_week_start_date, weekly_report_days
from core.sharek import build_sharek_xlsx
from core.text import split_text
from core.timeutil import now_utc, to_sanaa

log = logging.getLogger("worker")


class Worker:
    def __init__(self, bot: Any, svc: Services) -> None:
        self.bot, self.svc = bot, svc
        self.bucket = TokenBucket(rate=25, burst=25)
        self._tasks: list[asyncio.Task] = []
        self._weekly_done: set[tuple[str, str]] = set()
        self._reminded: set[tuple[int, int]] = set()
        self._backup_task: asyncio.Task | None = None
        self._backup_done: str | None = None     # مفتاح الأسبوع المُنجز في هذه الجلسة
        self._backup_retry_at = 0.0              # لا نعيد المحاولة الفاشلة أكثر من مرة كل ساعة
        self._backup_bytes = 0

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._outbox_loop()), asyncio.create_task(self._scheduler_loop())]

    def stop(self) -> None:
        for t in self._tasks + ([self._backup_task] if self._backup_task else []):
            t.cancel()

    def _web_url(self) -> str:
        settings = getattr(self.svc, "settings", None)
        return getattr(settings, "web_app_url", "") or ""

    def _wbtn(self, text: str, view: str, fallback_cb: str = "m:home") -> InlineKeyboardButton:
        w = webapp_btn(text, self._web_url(), view)
        return w if w is not None else btn(text, fallback_cb)

    # ---------- طابور الإرسال ----------
    async def _outbox_loop(self) -> None:
        idle_until = 0.0
        loop = asyncio.get_running_loop()
        while True:
            try:
                rows = await self.svc.store.rpc("claim_outbox", {"p_limit": 25})
                if not rows:
                    await asyncio.sleep(0.5 if loop.time() < idle_until else 2.0)
                    continue
                idle_until = loop.time() + 30
                results = await asyncio.gather(*[self._send(r) for r in rows])
                await self.svc.store.rpc("finish_outbox_batch", {"p_results": list(results)})
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("outbox loop error: %s", exc)
                await asyncio.sleep(3)

    async def _send(self, row: dict[str, Any]) -> dict[str, Any]:
        await self.bucket.acquire()
        kind, p, chat, topic = row["kind"], row["payload"], row["chat_id"], row.get("topic_id")
        res: dict[str, Any] = {"id": row["id"]}
        wurl = self._web_url()
        try:
            msg = None
            if kind == "assignment_new":
                aid = p["assignment_id"]
                rows_kb = [[btn("📝 فتح الواجب", f"a:d:{aid}")]]
                if wurl:
                    rows_kb.append([self._wbtn("📱 فتح الواجب في التطبيق", f"asg:{aid}")])
                msg = await say(self.bot, chat, messages.assignment_new(p), kb(rows_kb))
            elif kind == "assignment_reminder":
                aid = p["assignment_id"]
                rows_kb = [[btn("📝 فتح الواجب", f"a:d:{aid}")]]
                if wurl:
                    rows_kb.append([self._wbtn("📱 فتح الواجب في التطبيق", f"asg:{aid}")])
                msg = await say(self.bot, chat, messages.assignment_reminder(p, now_utc()), kb(rows_kb))
            elif kind == "manual":
                msg = await say(self.bot, chat, messages.manual(p))
            elif kind == "assignment_post":
                link = f"https://t.me/{self.bot.username}?start=a_{p['assignment_id']}"
                markup = InlineKeyboardMarkup([[InlineKeyboardButton("📤 إرسال الواجب", url=link)]])
                msg = await say(self.bot, chat, messages.assignment_new(p), markup, thread=topic)
            elif kind == "file_send":
                try:
                    if not (p.get("src_chat") and p.get("src_msg")):
                        raise BadRequest("no source message")
                    msg = await self.bot.copy_message(chat, p["src_chat"], p["src_msg"])
                except BadRequest:
                    msg = await send_by_kind(self.bot, chat, p["media_kind"], p["file_id"])
            elif kind == "tg_delete":
                try:
                    await self.bot.delete_message(chat, p["message_id"])
                except BadRequest:
                    pass
            elif kind == "file_repost":
                msg = await send_by_kind(self.bot, chat, p["media_kind"], p["file_id"], p.get("caption"), topic)
                await self.svc.store.rpc("set_file_message", {"p_id": p["file_row"], "p_chat": chat, "p_topic": topic, "p_msg": msg.message_id})
            elif kind == "barcode_send":
                png_bytes = await asyncio.to_thread(
                    render_barcode_png,
                    str(p.get("academic_no") or ""),
                    str(p.get("full_name") or ""),
                    str(p.get("theory_group_id") or ""),
                )
                doc = io.BytesIO(png_bytes)
                doc.name = f"barcode_{p.get('academic_no', 'id')}.png"
                msg = await self.bot.send_photo(chat_id=chat, photo=doc, caption=messages.barcode_caption(p), parse_mode="HTML")
            elif kind == "grievance_open":
                cid = p.get("cycle_id")
                markup = kb([[self._wbtn("⚖️ تقديم التظلم في التطبيق", "affairs-grv", f"grv:go:{cid}" if cid else "m:affairs")]])
                msg = await say(self.bot, chat, messages.grievance_open(p), markup)
            elif kind == "grievance_compiled":
                cid = p.get("cycle_id")
                markup = kb([[self._wbtn("📋 عرض التجميع في التطبيق", f"grv-cycle:{cid}" if cid else "grv-admin")]]) if wurl else None
                msg = await say(self.bot, chat, messages.grievance_compiled(p), markup)
            elif kind == "ticket_new":
                tid = p.get("ticket_id")
                markup = kb([[self._wbtn("💬 الرد على البلاغ في التطبيق", "assistant-staff", f"tkt:rep:{tid}" if tid else "m:ai")]])
                msg = await say(self.bot, chat, messages.ticket_new(p), markup)
            elif kind == "ticket_reply":
                markup = kb([[self._wbtn("💬 فتح محادثة الدعم في التطبيق", "assistant", "m:ai")]]) if wurl else None
                msg = await say(self.bot, chat, messages.ticket_reply(p), markup)
            elif kind == "attendance_excel":
                actor_id = p.get("actor_id") or chat
                matrix = await self.svc.store.rpc(
                    "attendance_matrix",
                    {"p_actor": actor_id, "p_subject": p["subject_id"], "p_group": p["group"]},
                )
                xlsx_bytes = await asyncio.to_thread(build_attendance_xlsx, matrix)
                doc = io.BytesIO(xlsx_bytes)
                doc.name = f"attendance_{p['group']}_s{p['subject_id']}.xlsx"
                subj_name = (matrix.get("subject") or {}).get("name") or ""
                msg = await self.bot.send_document(
                    chat_id=chat,
                    document=doc,
                    caption=f"📊 كشف حضور (م1–م12): {subj_name} — {p['group']}",
                )
            elif kind == "sharek_new":
                cid = p.get("campaign_id")
                # نقل الطالب إلى «شارك» داخل الـ Mini App مباشرة
                markup = kb([[self._wbtn("🗳 فتح «شارك» في التطبيق", "sharek", f"sh:go:{cid}" if cid else "m:sharek")]])
                msg = await say(self.bot, chat, messages.sharek_new(p), markup)
            elif kind == "sharek_excel":
                actor_id = p.get("actor_id") or chat
                raw_grp = p.get("group")
                req_grp = None if not raw_grp or str(raw_grp).strip().upper() in ("ALL", "الجميع") else raw_grp
                results = await self.svc.store.rpc(
                    "sharek_campaign_results",
                    {"p_actor": actor_id, "p_campaign": p["campaign_id"], "p_group": req_grp},
                )
                xlsx_bytes = await asyncio.to_thread(build_sharek_xlsx, results)
                doc = io.BytesIO(xlsx_bytes)
                grp_tag = (req_grp or "all").lower()
                doc.name = f"sharek_{p['campaign_id']}_{grp_tag}.xlsx"
                msg = await self.bot.send_document(
                    chat_id=chat,
                    document=doc,
                    caption=f"📊 نتائج شارك #{p['campaign_id']} ({req_grp or 'الجميع'})",
                )
            elif kind == "sharek_items_view":
                # إرسال الصور والملفات والنصوص التي أرسلها الطلاب في «شارك» إلى المشرف في البوت
                actor_id = p.get("actor_id") or chat
                raw_grp = p.get("group")
                req_grp = None if not raw_grp or str(raw_grp).strip().upper() in ("ALL", "الجميع") else raw_grp
                results: dict[str, Any] = {}
                try:
                    results = await self.svc.store.rpc(
                        "sharek_campaign_results",
                        {"p_actor": actor_id, "p_campaign": p["campaign_id"], "p_group": req_grp},
                    ) or {}
                except Exception:  # noqa: BLE001
                    results = {}
                camp = results.get("campaign") or {}
                camp_title = camp.get("title") or p.get("title") or ""
                subs = results.get("submissions") or p.get("items") or []
                item_id = p.get("item_id")
                if item_id:
                    subs = [s for s in subs if int(s.get("id") or 0) == int(item_id)]
                if not subs:
                    msg = await say(self.bot, chat, f"📥 لا توجد ملفات أو مشاركات مستلمة في حملة «{escape(camp_title)}».")
                else:
                    msg = await say(
                        self.bot,
                        chat,
                        f"📥 <b>المشاركات المستلمة في «{escape(camp_title)}»</b> ({len(subs)} عنصر):",
                    )
                    for item in subs[:25]:
                        header = (
                            f"👤 <b>{escape(str(item.get('name') or ''))}</b> (<code>{escape(str(item.get('no') or ''))}</code>)\n"
                            f"👥 المجموعة: {escape(str(item.get('theory') or ''))} · عملي {escape(str(item.get('practical') or ''))}"
                        )
                        fid = item.get("file_id")
                        ikind = str(item.get("item_kind") or "text").lower()
                        tcontent = str(item.get("text_content") or "").strip()
                        for attempt in range(3):   # RetryAfter: ننتظر ونعيد هذا العنصر فقط (لا الدفعة كلها)
                            await self.bucket.acquire()
                            try:
                                if fid:
                                    cap = f"{header}\n\n💬 {escape(tcontent)}" if tcontent else header
                                    send_kind = ikind if ikind in ("photo", "video", "audio", "voice", "document") else "document"
                                    try:
                                        await send_by_kind(self.bot, chat, send_kind, fid, caption=cap)
                                    except BadRequest:
                                        alt_kind = "document" if send_kind == "photo" else "photo"
                                        await send_by_kind(self.bot, chat, alt_kind, fid, caption=cap)
                                elif tcontent:
                                    await say(self.bot, chat, f"{header}\n\n💬 {escape(tcontent)}")
                                break
                            except RetryAfter as e:
                                await asyncio.sleep(int(e.retry_after) + 1)
                        await asyncio.sleep(1.1)   # حدّ تيليجرام: ≈ رسالة/ثانية داخل المحادثة الواحدة
            elif kind == "doctor_excel":
                actor_id = p.get("actor_id") or chat
                data = await self.svc.store.rpc(
                    "doctor_assignment_export",
                    {"p_tg": actor_id, "p_assignment": p["assignment_id"]},
                )
                xlsx_bytes = await asyncio.to_thread(build_doctor_assignment_xlsx, data)
                asg = data.get("assignment") or {}
                doc = io.BytesIO(xlsx_bytes)
                doc.name = f"assignment_{asg.get('number', 1)}_{asg.get('group', 'group')}.xlsx"
                msg = await self.bot.send_document(
                    chat_id=chat,
                    document=doc,
                    caption=f"📊 كشف تسليم واجب {asg.get('number', '')} — {asg.get('subject', '')} ({asg.get('group', '')})",
                )
            elif kind == "doctor_group_excel":
                actor_id = p.get("actor_id") or chat
                data = await self.svc.store.rpc(
                    "doctor_group_export",
                    {"p_tg": actor_id, "p_group": p["group"], "p_doctor_id": p.get("doctor_id")},
                )
                xlsx_bytes = await asyncio.to_thread(build_doctor_group_xlsx, data)
                doc = io.BytesIO(xlsx_bytes)
                doc.name = f"assignments_{data.get('group', 'group')}.xlsx"
                msg = await self.bot.send_document(
                    chat_id=chat,
                    document=doc,
                    caption=f"📊 كشف تسليم الواجبات — مجموعة {data.get('group', '')} ({len(data.get('assignments') or [])} واجب)",
                )
            elif kind in ("report_post", "weekly_report_post"):
                for chunk in split_text(str(p.get("text") or ""), 3500):   # حدّ تيليجرام 4096 بعد الهروب
                    msg = await say(self.bot, chat, escape(chunk), thread=topic)
            elif kind == "report_reminder":
                markup = kb([[self._wbtn("📝 كتابة التقرير في التطبيق", "reports")]]) if wurl else None
                msg = await say(self.bot, chat, messages.report_reminder(p), markup)
            else:
                return {**res, "status": "failed", "error": f"unknown kind {kind}"}
            return {**res, "status": "sent", "mid": getattr(msg, "message_id", None)}
        except RetryAfter as e:
            return {**res, "status": "retry", "delay": int(e.retry_after) + 1, "error": "flood"}
        except Forbidden as e:
            return {**res, "status": "blocked" if chat > 0 else "failed", "error": str(e)[:200]}
        except BadRequest as e:
            return {**res, "status": "failed", "error": str(e)[:200]}
        except (TimedOut, NetworkError) as e:
            return {**res, "status": "retry", "delay": 5, "error": str(e)[:200]}
        except Exception as e:  # noqa: BLE001
            log.exception("send failed")
            return {**res, "status": "failed", "error": str(e)[:200]}

    # ---------- المجدول ----------
    async def _auto_weekly_reports(self, today_sanaa: dt.date) -> None:
        """ينشر التقرير الأسبوعي آليًا (مساء الخميس، ويُستدرك الجمعة إن فات) في topic كل مجموعة نظرية مرة واحدة.
        كل مجموعة معزولة عن غيرها: فشل إحداها لا يوقف البقية، والمنشور سابقًا لا يتكرر."""
        wstart = study_week_start_date(today_sanaa)
        for grp in cohort.get().theory_ids():
            if (grp, wstart.isoformat()) in self._weekly_done:
                continue
            try:
                dest = await self.svc.destination(f"cohort:theory:{grp}") or await self.svc.destination(f"cohort:notices:{grp}")
                if not dest:
                    continue   # لا وجهة بعد؛ نعاود لاحقًا ضمن النافذة دون استعلامات إضافية
                existing = await self.svc.store.select(
                    "weekly_reports",
                    {"select": "id,status,rendered_text", "theory_group_id": f"eq.{grp}", "week_start": f"eq.{wstart.isoformat()}"},
                )
                if existing and existing[0].get("status") == "published":
                    self._weekly_done.add((grp, wstart.isoformat()))
                    continue
                if existing and existing[0].get("rendered_text"):
                    text = existing[0]["rendered_text"]
                else:
                    daily = await self.svc.store.select(
                        "daily_reports",
                        {
                            "select": "report_date,rendered_text",
                            "theory_group_id": f"eq.{grp}",
                            "report_date": f"gte.{wstart.isoformat()}",
                            "order": "report_date.asc",
                        },
                    )
                    if not weekly_report_days(wstart, daily):
                        self._weekly_done.add((grp, wstart.isoformat()))
                        continue   # لا تقارير مكتوبة هذا الأسبوع: لا ننشر تقريرًا أسبوعيًا فارغًا
                    first = await self.svc.store.select(
                        "daily_reports", {"select": "report_date", "order": "report_date.asc", "limit": "1"})
                    first_date = dt.date.fromisoformat(str(first[0]["report_date"])[:10]) if first else None
                    text = compile_weekly_report(grp, wstart, daily, first_report_date=first_date)
                await self.svc.store.rpc("enqueue_outbox", {
                    "p_kind": "weekly_report_post", "p_chat": dest["chat_id"], "p_topic": dest.get("topic_id"),
                    "p_payload": {"group": grp, "week_start": wstart.isoformat(), "text": text},
                    "p_dedupe": f"auto_wrep:{grp}:{wstart.isoformat()}",
                })
                self._weekly_done.add((grp, wstart.isoformat()))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("weekly report %s failed: %s", grp, exc)

    # ---------- النسخة الاحتياطية ----------
    async def _developer_ids(self) -> list[int]:
        rows = await self.svc.store.select(
            "role_grants", {"select": "telegram_id", "role": "eq.developer", "revoked_at": "is.null"})
        return sorted({int(r["telegram_id"]) for r in rows or [] if r.get("telegram_id")})

    def _backup_due(self, now: dt.datetime) -> str | None:
        """مفتاح أسبوع النسخة إن حان وقتها: الجمعة بعد 3 فجرًا بتوقيت صنعاء (بعد نشر تقرير الخميس)."""
        if now.weekday() != 4 or now.hour < 3:
            return None
        iso = now.isocalendar()
        key = f"{iso[0]}-W{iso[1]:02d}"
        if key == self._backup_done or time.monotonic() < self._backup_retry_at:
            return None
        return key

    async def run_backup(self, recipients: list[int], label: str) -> int:
        """يصدّر كل الجداول ويرسلها zip للمطوّرين. يعيد عدد المستلمين الذين وصلتهم النسخة كاملة، ويرفع خطأ إن لم تصل لأحد."""
        dump: dict[str, list] = {}
        for table in BACKUP_TABLES:
            dump[table] = await self.svc.store.rpc("backup_export_table", {"p_table": table}) or []
        parts = await asyncio.to_thread(build_backup_parts, dump, now_utc().isoformat(), label)
        delivered = 0
        for uid in recipients:
            try:
                for i, (name, blob) in enumerate(parts, start=1):
                    doc = io.BytesIO(blob)
                    doc.name = name
                    await self.bot.send_document(chat_id=uid, document=doc,
                                                 caption=backup_caption(dump, label, i, len(parts)))
                    await asyncio.sleep(1.1)
                delivered += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — فشل مطوّر واحد (حظر البوت مثلًا) لا يمنع البقية
                log.warning("backup send to %s failed: %s", uid, exc)
        if not delivered:
            raise RuntimeError("backup not delivered to any developer")
        self._backup_bytes = sum(len(b) for _, b in parts)
        return delivered

    async def _weekly_backup(self, key: str, today: dt.date) -> None:
        try:
            if not await self.svc.store.rpc("backup_claim", {"p_key": key}):
                self._backup_done = key      # نسخة هذا الأسبوع أُخذت (أو قيد التنفيذ من نسخة أخرى)
                return
            try:
                devs = await self._developer_ids()
                if not devs:
                    raise RuntimeError("no developer accounts")
                self._backup_bytes = 0
                n = await self.run_backup(devs, today.isoformat())
                await self.svc.store.rpc("backup_finish", {"p_key": key, "p_status": "ok", "p_bytes": self._backup_bytes})
                self._backup_done = key
                log.info("weekly backup %s sent to %d developer(s)", key, n)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("weekly backup %s failed: %s", key, exc)
                self._backup_retry_at = time.monotonic() + 3600
                try:
                    await self.svc.store.rpc("backup_finish", {"p_key": key, "p_status": "failed"})
                except Exception:  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — مثل غياب 0010 بعد: نعاود بعد ساعة
            log.warning("weekly backup %s skipped: %s", key, exc)
            self._backup_retry_at = time.monotonic() + 3600

    async def _deliver_due(self) -> None:
        """التسليمات المستحقة فقط (حجز + تراجع تصاعدي من القاعدة)؛ فشل واحد لا يؤثر على غيره."""
        for sub_id in await self.svc.store.rpc("pending_deliveries", {"p_limit": 10}):
            try:
                pkg = await self.svc.store.rpc("submission_package", {"p_sub": sub_id})
                await assignments.deliver_tracked(self.bot, self.svc, pkg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("delivery %s failed: %s", sub_id, exc)

    async def _finish_stale_uploads(self) -> None:
        """طالب رفع ملفات ولم يضغط «إنهاء التسليم»: التسليم الأول يُنهى تلقائيًا بعد 10 دقائق، والتعديل يُذكَّر به فقط."""
        for item in await self.svc.store.rpc("stale_unfinalized", {"p_minutes": 10, "p_limit": 20}) or []:
            tg, aid = item["tg"], item["assignment"]
            try:
                if item.get("first"):
                    res = await self.svc.store.rpc("finalize_submission", {"p_tg": tg, "p_assignment": aid})
                    if res.get("status") != "ok":
                        continue
                    pkg = res["package"]
                    if await self.svc.store.rpc("claim_delivery", {"p_sub": pkg["submission_id"]}) is not False:
                        await assignments.deliver_tracked(self.bot, self.svc, pkg)
                    await say(self.bot, tg, "تم إنهاء تسليمك تلقائيًا ✅ لأنك رفعت الملفات ولم تضغط «إنهاء التسليم».\n"
                                           "يمكنك تعديله قبل إغلاق الواجب.", kb([[btn("📝 عرض الواجب", f"a:d:{aid}")]]))
                else:
                    if (tg, aid) in self._reminded:
                        continue   # تذكير واحد فقط
                    self._reminded.add((tg, aid))
                    await say(self.bot, tg, "⏳ لديك ملفات جديدة لم تُرسَل بعد. اضغط «إنهاء التسليم» ليحلّ تعديلك محل التسليم السابق.",
                              kb([[btn("✅ إنهاء التسليم", f"sub:fin:{aid}")], [btn("إلغاء", f"sub:cancel:{aid}")]]))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("stale upload %s/%s: %s", tg, aid, exc)

    async def _scheduler_loop(self) -> None:
        last_purge: dt.date | None = None
        tick = 0
        while True:
            tick += 1
            # كل خطوة معزولة: فشل واحدة لا يوقف الباقي
            steps = [self._deliver_due]          # كل 30 ثانية
            if tick % 2 == 0:                    # كل دقيقة (يخفّف الاستعلامات)
                steps += [self._run_due_work, self._finish_stale_uploads]
            for step in steps:
                try:
                    await step()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("scheduler step %s: %s", step.__name__, exc)
            try:
                now = to_sanaa(now_utc())
                # مساء الخميس (9م) ثم استدراك الجمعة إن فات النشر؛ المنشور سابقًا لا يتكرر (dedupe + ذاكرة)
                if (now.weekday() == 3 and now.hour >= 21) or now.weekday() == 4:
                    await self._auto_weekly_reports(now.date())
                key = self._backup_due(now)
                if key and (self._backup_task is None or self._backup_task.done()):
                    self._backup_task = asyncio.create_task(self._weekly_backup(key, now.date()))
                if now.hour == 4 and last_purge != now.date():
                    log.info("purge: %s", await self.svc.store.rpc("purge_old"))
                    last_purge = now.date()
                if len(self._reminded) > 5000:
                    self._reminded.clear()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("scheduler error: %s", exc)
            await asyncio.sleep(30)

    async def _run_due_work(self) -> None:
        out = await self.svc.store.rpc("run_due_work")
        if out and any(out.get(k) for k in ("closed", "reminders", "grievances_closed", "report_reminders")):
            log.info("due work: %s", out)

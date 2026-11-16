"""مسارات المرحلة 4 (الأخيرة):
1) التقارير اليومية والقوالب والتقرير الأسبوعي الآلي.
2) شارك (استفتاء 2–10 خيارات، استقبال مشاركات متعددة، النتائج، إرسال الصور/الملفات للمشرف في البوت، وتصدير Excel لكل مجموعة).
3) الاختبارات MCQ (التصفح، بدء الاختبار مع العدّاد الاختياري، الرفع من Excel، قالب Excel).
4) شاشة الدكتور (واجبات مجموعاته وتصدير Excel 1/0 لكل مجموعة منفردة).
5) الإشعارات اليدوية ولوحة المطوّر الشاملة (الطلاب، استيراد Excel، الزوار والحظر، الأدوار، المواد والدكاترة والمجتمعات، السلة، سجل النشاط، وصحة النظام)."""
from __future__ import annotations

import base64
import io
import re
from datetime import date
from html import escape
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from api.deps import build_bot_link, current_user, etag_response, settings, store
from core import cohort
from core.doctor import build_doctor_assignment_xlsx, build_doctor_group_xlsx
from core.files import file_title, hashtag
from core.mcq import build_mcq_template_xlsx, parse_mcq_excel
from core.reports import (
    compile_weekly_report,
    study_week_start_date,
    validate_and_render_daily_report,
    validate_template_slots,
)
from core.sharek import build_sharek_xlsx, validate_sharek_campaign
from core.text import clean_name, normalize_academic_no, normalize_practical, normalize_theory
from core.tg_auth import TgUser

router = APIRouter(prefix="/api")


def _no_store(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers={"Cache-Control": "no-store"})


def _bot_link(payload: str) -> str | None:
    name = settings().bot_username
    return f"https://t.me/{name}?start={payload}" if name else None


def _fail(res: dict[str, Any] | None, default: int = 400) -> None:
    if not isinstance(res, dict):
        return
    status = res.get("status")
    if status in (None, "ok"):
        return
    code = 404 if status == "not_found" else (403 if status == "forbidden" else default)
    raise HTTPException(code, status)


# =====================================================================
# 1) التقارير اليومية والقوالب والتقرير الأسبوعي
# =====================================================================
@router.get("/reports/workspace")
async def get_reports_workspace(
    request: Request,
    group: str | None = None,
    report_date: str | None = Query(default=None, alias="date"),
    user: TgUser = Depends(current_user),
) -> Response:
    res = await store().rpc("reports_workspace", {"p_actor": user.id, "p_group": group, "p_date": report_date})
    _fail(res)
    wstart = date.fromisoformat(str(res["week_start"])[:10])
    first = await store().select("daily_reports", {"select": "report_date", "order": "report_date.asc", "limit": "1"})
    first_date = date.fromisoformat(str(first[0]["report_date"])[:10]) if first else None
    res["compiled_weekly"] = compile_weekly_report(res["group"], wstart, res.get("week_reports") or [], first_report_date=first_date)
    return etag_response(request, res)


class ReportTemplateIn(BaseModel):
    group: str = Field(max_length=16)
    weekday: int = Field(ge=0, le=6)
    slots: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/reports/templates")
async def save_template(body: ReportTemplateIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean_slots, err = validate_template_slots(body.slots)
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "save_report_template",
        {"p_actor": user.id, "p_group": body.group.strip().upper(), "p_weekday": body.weekday, "p_slots": clean_slots},
    )
    _fail(res, 422)
    return _no_store(res)


@router.delete("/reports/templates/{group}/{weekday}")
async def delete_template(group: str, weekday: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc(
        "delete_report_template",
        {"p_actor": user.id, "p_group": group.strip().upper(), "p_weekday": weekday},
    )
    _fail(res)
    return {"ok": True}


class DailyReportIn(BaseModel):
    group: str = Field(max_length=16)
    report_date: str = Field(max_length=16)
    slots: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=1000)
    publish: bool = False


@router.post("/reports/daily")
async def save_daily(body: DailyReportIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_and_render_daily_report(body.group, body.report_date, body.slots, body.note)
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "save_daily_report",
        {
            "p_actor": user.id,
            "p_group": clean["group"],
            "p_date": clean["report_date"],
            "p_slots": clean["slots"],
            "p_note": clean["note"],
            "p_rendered": clean["rendered_text"],
            "p_publish": body.publish,
        },
    )
    _fail(res, 422)
    res["rendered_text"] = clean["rendered_text"]
    return _no_store(res)


class WeeklyReportIn(BaseModel):
    group: str = Field(max_length=16)
    week_start: str = Field(max_length=16)
    rendered_text: str = Field(min_length=10, max_length=12000)
    publish: bool = False


@router.post("/reports/weekly")
async def save_weekly(body: WeeklyReportIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    grp = normalize_theory(body.group)
    if not grp:
        raise HTTPException(422, "invalid_group")
    try:
        ws = date.fromisoformat(body.week_start[:10])
    except ValueError as exc:
        raise HTTPException(422, "invalid_date") from exc
    wstart = study_week_start_date(ws)
    res = await store().rpc(
        "save_weekly_report",
        {
            "p_actor": user.id,
            "p_group": grp,
            "p_week_start": wstart.isoformat(),
            "p_rendered": body.rendered_text.strip(),
            "p_publish": body.publish,
        },
    )
    _fail(res, 422)
    return _no_store(res)


# =====================================================================
# 2) شارك (الطالب + الإدارة + إرسال المشاركات للمشرف في البوت + تصدير Excel)
# =====================================================================
@router.get("/sharek")
async def sharek_list(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("my_sharek", {"p_tg": user.id})
    for r in rows:
        if r.get("kind") == "submission":
            r["bot_link"] = await build_bot_link(f"sh_{r['id']}")
    return etag_response(request, rows)


class SharekVoteIn(BaseModel):
    option_idx: int = Field(ge=0, le=9)


@router.post("/sharek/{cid}/vote")
async def sharek_vote(cid: int, body: SharekVoteIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("vote_sharek", {"p_tg": user.id, "p_campaign": cid, "p_option_idx": body.option_idx})
    _fail(res, 422)
    return _no_store(res)


class SharekTextIn(BaseModel):
    text: str = Field(max_length=3000)


@router.post("/sharek/{cid}/text")
async def sharek_add_text(cid: int, body: SharekTextIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    """إضافة نص المشاركة من داخل التطبيق (يحفظ الأسطر كما كُتبت). الصور والملفات تأتي من البوت."""
    text = body.text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise HTTPException(422, "empty_text")
    res = await store().rpc(
        "add_sharek_item",
        {"p_tg": user.id, "p_campaign": cid, "p_item": {"item_kind": "text", "text_content": text}},
    )
    _fail(res, 422)
    return _no_store(res)


@router.post("/sharek/{cid}/finish")
async def sharek_finish(cid: int, user: TgUser = Depends(current_user)) -> JSONResponse:
    """إنهاء المشاركة واعتمادها من التطبيق (بعد كتابة النص و/أو إرسال الملفات في البوت)."""
    res = await store().rpc("finish_sharek_submission", {"p_tg": user.id, "p_campaign": cid})
    _fail(res, 422)
    return _no_store(res)


@router.post("/sharek/{cid}/start-bot")
async def sharek_start_bot(cid: int, user: TgUser = Depends(current_user)) -> JSONResponse:
    rows = await store().rpc("my_sharek", {"p_tg": user.id})
    c = next((x for x in (rows or []) if int(x.get("id") or 0) == cid), None)
    link = await build_bot_link(f"sh_{cid}")
    token = settings().bot_token
    if c and token and not c.get("is_finished"):
        title = escape(str(c.get("title") or "مشاركة"))
        sent_n = int(c.get("my_items_count") or 0)
        extra = f"\nلديك حتى الآن <b>{sent_n}</b> عنصر مرسل." if sent_n else ""
        text = (
            f"📥 <b>{title}</b>{extra}\n\n"
            "أرسل الآن <b>الصور والملفات</b> فقط هنا في المحادثة (عنصرًا واحدًا أو عدة عناصر). "
            "النص تكتبه من التطبيق في شاشة «شارك».\n"
            "وعند الانتهاء اضغط «✅ إنهاء المشاركة»."
        )
        markup = {
            "inline_keyboard": [
                [{"text": "✅ إنهاء المشاركة", "callback_data": f"sh:fin:{cid}"}],
                [{"text": "إلغاء", "callback_data": "sh:cancel"}],
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": user.id, "text": text, "parse_mode": "HTML", "reply_markup": markup},
                )
        except Exception:  # noqa: BLE001
            pass
    return _no_store({"status": "ok", "bot_link": link})


@router.get("/staff/sharek")
async def staff_sharek_list(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("staff_sharek_campaigns", {"p_actor": user.id})
    return etag_response(request, rows)


class SharekCreateIn(BaseModel):
    kind: str = Field(max_length=16)
    group: str | None = Field(default=None, max_length=16)
    title: str = Field(max_length=1000)
    options: list[str] | str = Field(default_factory=list)


@router.post("/staff/sharek")
async def staff_sharek_create(body: SharekCreateIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_sharek_campaign(body.model_dump())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "create_sharek_campaign",
        {
            "p_actor": user.id,
            "p_kind": clean["kind"],
            "p_group": clean["group"],
            "p_title": clean["title"],
            "p_options": clean["options"],
        },
    )
    _fail(res, 422)
    return _no_store(res)


@router.delete("/staff/sharek/{cid}")
async def staff_sharek_hide(cid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc("hide_sharek_campaign", {"p_actor": user.id, "p_campaign": cid})
    _fail(res)
    return {"ok": True}


def _clean_sharek_group(group: str | None) -> str | None:
    if not group or str(group).strip().upper() in ("", "ALL", "الجميع"):
        return None
    return normalize_theory(group)


@router.get("/staff/sharek/{cid}")
async def staff_sharek_detail(
    cid: int,
    request: Request,
    group: str | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    grp = _clean_sharek_group(group)
    res = await store().rpc("sharek_campaign_results", {"p_actor": user.id, "p_campaign": cid, "p_group": grp})
    _fail(res)
    return etag_response(request, res)


@router.get("/staff/sharek/{cid}/export")
async def staff_sharek_export(
    cid: int,
    group: str | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    grp = _clean_sharek_group(group)
    res = await store().rpc("sharek_campaign_results", {"p_actor": user.id, "p_campaign": cid, "p_group": grp})
    _fail(res)
    resolved_grp = (res.get("campaign") or {}).get("group") or grp or "all"
    xlsx = build_sharek_xlsx(res)
    fname = f"sharek_{cid}_{str(resolved_grp).lower()}.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"},
    )


class SharekExportBotIn(BaseModel):
    group: str | None = None
    item_id: int | None = None


async def _tg_send_media_item(client: httpx.AsyncClient, token: str, chat_id: int, item: dict[str, Any]) -> bool:
    header = (
        f"👤 <b>{escape(str(item.get('name') or ''))}</b> (<code>{escape(str(item.get('no') or ''))}</code>)\n"
        f"👥 المجموعة: {escape(str(item.get('theory') or ''))} · عملي {escape(str(item.get('practical') or ''))}"
    )
    fid = item.get("file_id")
    ikind = str(item.get("item_kind") or "text").lower()
    tcontent = str(item.get("text_content") or "").strip()

    if fid:
        caption = f"{header}\n\n💬 {escape(tcontent)}" if tcontent else header
        method_map = {
            "photo": ("sendPhoto", "photo"),
            "video": ("sendVideo", "video"),
            "audio": ("sendAudio", "audio"),
            "voice": ("sendVoice", "voice"),
            "document": ("sendDocument", "document"),
        }
        primary = method_map.get(ikind, ("sendDocument", "document"))
        fallbacks = [primary]
        if primary[0] != "sendDocument":
            fallbacks.append(("sendDocument", "document"))
        if primary[0] != "sendPhoto":
            fallbacks.append(("sendPhoto", "photo"))

        for method, field in fallbacks:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/{method}",
                json={"chat_id": chat_id, field: fid, "caption": caption, "parse_mode": "HTML"},
            )
            if r.status_code == 200 and r.json().get("ok"):
                return True
        return False

    if tcontent:
        r = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": f"{header}\n\n💬 {escape(tcontent)}", "parse_mode": "HTML"},
        )
        return r.status_code == 200 and bool(r.json().get("ok"))
    return False


@router.post("/staff/sharek/{cid}/export-bot")
async def staff_sharek_export_bot(cid: int, body: SharekExportBotIn | None = None, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    grp = _clean_sharek_group(body.group if body else None)
    res = await store().rpc("sharek_campaign_results", {"p_actor": user.id, "p_campaign": cid, "p_group": grp})
    _fail(res)
    camp_grp = (res.get("campaign") or {}).get("group")
    eff_grp = None if not camp_grp or camp_grp in ("ALL", "الجميع") else camp_grp

    token = settings().bot_token
    if token:
        try:
            xlsx_bytes = build_sharek_xlsx(res)
            grp_tag = (eff_grp or "all").lower()
            fname = f"sharek_{cid}_{grp_tag}.xlsx"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": str(user.id), "caption": f"📊 نتائج شارك #{cid} ({eff_grp or 'الجميع'})"},
                    files={"document": (fname, xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                )
                if r.status_code == 200 and r.json().get("ok"):
                    return {"ok": True}
        except Exception:  # noqa: BLE001
            pass

    await store()._request(
        "POST",
        "/outbox",
        json={
            "kind": "sharek_excel",
            "chat_id": user.id,
            "payload": {"campaign_id": cid, "group": eff_grp, "actor_id": user.id},
        },
    )
    return {"ok": True}


@router.post("/staff/sharek/{cid}/send-items-bot")
async def staff_sharek_send_items_bot(cid: int, body: SharekExportBotIn | None = None, user: TgUser = Depends(current_user)) -> dict[str, Any]:
    grp = _clean_sharek_group(body.group if body else None)
    res = await store().rpc("sharek_campaign_results", {"p_actor": user.id, "p_campaign": cid, "p_group": grp})
    _fail(res)
    subs = res.get("submissions") or []
    if body and body.item_id:
        subs = [s for s in subs if int(s.get("id") or 0) == int(body.item_id)]
    if not subs:
        raise HTTPException(404, "no_submissions")

    camp_title = (res.get("campaign") or {}).get("title") or ""
    camp_grp = (res.get("campaign") or {}).get("group")
    eff_grp = None if not camp_grp or camp_grp in ("ALL", "الجميع") else camp_grp

    token = settings().bot_token
    sent_ids: set[int] = set()
    if token and len(subs) <= 10:
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": user.id,
                        "text": f"📥 <b>المشاركات المستلمة في «{escape(camp_title)}»</b> ({len(subs)} عنصر):",
                        "parse_mode": "HTML",
                    },
                )
                for item in subs:
                    if await _tg_send_media_item(client, token, user.id, item):
                        if item.get("id") is not None:
                            sent_ids.add(int(item["id"]))
            if len(sent_ids) == len(subs):
                return {"ok": True, "count": len(sent_ids), "direct": True}
        except Exception:  # noqa: BLE001
            pass

    remaining = [s for s in subs if int(s.get("id") or 0) not in sent_ids]
    if not remaining:
        return {"ok": True, "count": len(sent_ids), "direct": True}

    await store()._request(
        "POST",
        "/outbox",
        json={
            "kind": "sharek_items_view",
            "chat_id": user.id,
            "payload": {
                "campaign_id": cid,
                "title": camp_title,
                "group": eff_grp,
                "actor_id": user.id,
                "item_id": body.item_id if body else None,
                "items": remaining[:50],
            },
        },
    )
    return {"ok": True, "count": len(subs)}


# =====================================================================
# 3) الاختبارات MCQ (التصفح، الاختبار، والرفع من Excel)
# =====================================================================
@router.get("/mcq")
async def mcq_list(request: Request, subject: str | int | None = None, user: TgUser = Depends(current_user)) -> Response:
    subj_clean: int | None = None
    if subject not in (None, ""):
        try:
            subj_clean = int(subject)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid_subject") from exc
    rows = await store().rpc("list_mcq_exams", {"p_tg": user.id, "p_subject": subj_clean})
    return etag_response(request, rows)


@router.get("/mcq/template")
async def mcq_template() -> Response:
    xlsx = build_mcq_template_xlsx()
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="mcq_template.xlsx"', "Cache-Control": "public, max-age=3600"},
    )


@router.get("/mcq/{eid}")
async def mcq_detail(eid: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("get_mcq_exam", {"p_tg": user.id, "p_exam": eid})
    _fail(res)
    return etag_response(request, res)


class McqAttemptIn(BaseModel):
    score: int = Field(ge=0, le=500)
    total: int = Field(ge=1, le=500)
    elapsed_seconds: int = Field(default=0, ge=0, le=86400)
    timer_enabled: bool = True


@router.post("/mcq/{eid}/attempt")
async def mcq_submit_attempt(eid: int, body: McqAttemptIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc(
        "submit_mcq_attempt",
        {
            "p_tg": user.id,
            "p_exam": eid,
            "p_score": body.score,
            "p_total": body.total,
            "p_elapsed": body.elapsed_seconds,
            "p_timer_enabled": body.timer_enabled,
        },
    )
    _fail(res, 422)
    return _no_store(res)


class McqUploadIn(BaseModel):
    subject_id: int
    title: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=600)
    duration_minutes: int = Field(default=15, ge=1, le=180)
    excel_base64: str | None = Field(default=None, max_length=4_000_000)
    questions: list[dict[str, Any]] | None = None


@router.post("/mcq/upload")
async def mcq_upload(body: McqUploadIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    questions = body.questions
    if body.excel_base64:
        try:
            raw_bytes = base64.b64decode(body.excel_base64)
        except Exception as exc:
            raise HTTPException(422, "invalid_excel_base64") from exc
        parsed, errors = parse_mcq_excel(raw_bytes)
        if errors or not parsed:
            return _no_store({"status": "excel_errors", "errors": errors}, 422)
        questions = parsed

    if not questions:
        raise HTTPException(422, "no_questions")

    res = await store().rpc(
        "create_mcq_exam",
        {
            "p_actor": user.id,
            "p_subject": body.subject_id,
            "p_title": body.title,
            "p_description": body.description,
            "p_duration": body.duration_minutes,
            "p_questions": questions,
        },
    )
    _fail(res, 422)
    return _no_store(res)


@router.delete("/mcq/{eid}")
async def mcq_delete(eid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc("delete_mcq_exam", {"p_actor": user.id, "p_exam": eid})
    _fail(res)
    return {"ok": True}


# =====================================================================
# 4) شاشة الدكتور (واجبات مجموعاته وتصدير Excel: 1 سلّم، 0 لم يسلّم)
# =====================================================================
@router.get("/doctor/assignments")
async def doctor_assignments(
    request: Request,
    doctor_id: int | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    res = await store().rpc("doctor_dashboard", {"p_tg": user.id, "p_doctor_id": doctor_id})
    _fail(res)
    return etag_response(request, res)


@router.get("/doctor/assignments/{aid}")
async def doctor_assignment_detail(aid: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("doctor_assignment_export", {"p_tg": user.id, "p_assignment": aid})
    _fail(res)
    return etag_response(request, res)


@router.get("/doctor/assignments/{aid}/export")
async def doctor_export_excel(aid: int, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("doctor_assignment_export", {"p_tg": user.id, "p_assignment": aid})
    _fail(res)
    xlsx = build_doctor_assignment_xlsx(res)
    asg = res.get("assignment") or {}
    fname = f"assignment_{asg.get('number', 1)}_{asg.get('group', 'group')}.xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "Cache-Control": "no-store"},
    )


@router.post("/doctor/assignments/{aid}/export-bot")
async def doctor_export_bot(aid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc("doctor_assignment_export", {"p_tg": user.id, "p_assignment": aid})
    _fail(res)
    await store()._request(
        "POST",
        "/outbox",
        json={
            "kind": "doctor_excel",
            "chat_id": user.id,
            "payload": {"assignment_id": aid, "actor_id": user.id},
        },
    )
    return {"ok": True}


_GROUP_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


async def _doctor_group_data(group: str, doctor_id: int | None, user: TgUser) -> dict[str, Any]:
    if not _GROUP_RE.match(group):
        raise HTTPException(422, "invalid_group")
    res = await store().rpc("doctor_group_export", {"p_tg": user.id, "p_group": group, "p_doctor_id": doctor_id})
    _fail(res)
    if not res.get("assignments"):
        raise HTTPException(404, "no_assignments")
    return res


@router.get("/doctor/groups/{group}/export")
async def doctor_group_export_excel(group: str, doctor_id: int | None = None, user: TgUser = Depends(current_user)) -> Response:
    """ملف Excel لمجموعة واحدة: الرقم الأكاديمي | اسم الطالب | واجب 1 | واجب 2 ... (1 سلّم، 0 لم يسلّم)."""
    res = await _doctor_group_data(group, doctor_id, user)
    return Response(
        content=build_doctor_group_xlsx(res),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="assignments_{group}.xlsx"', "Cache-Control": "no-store"},
    )


@router.post("/doctor/groups/{group}/export-bot")
async def doctor_group_export_bot(group: str, doctor_id: int | None = None, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    await _doctor_group_data(group, doctor_id, user)   # يتحقق من الصلاحية ومن وجود واجبات قبل الجدولة
    await store()._request(
        "POST",
        "/outbox",
        json={
            "kind": "doctor_group_excel",
            "chat_id": user.id,
            "payload": {"group": group, "doctor_id": doctor_id, "actor_id": user.id},
        },
    )
    return {"ok": True}


# =====================================================================
# 5) الإشعارات اليدوية ولوحة المطوّر الشاملة
# =====================================================================
class ManualNotifyIn(BaseModel):
    group: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, max_length=140)
    body: str = Field(min_length=2, max_length=1500)


@router.post("/notify/manual")
async def notify_manual(body: ManualNotifyIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    grp = None if not body.group or body.group in ("ALL", "all") else normalize_theory(body.group)
    if body.group and body.group not in ("ALL", "all") and not grp:
        raise HTTPException(422, "invalid_group")
    res = await store().rpc(
        "send_manual_notification",
        {"p_actor": user.id, "p_group": grp, "p_title": body.title, "p_body": body.body},
    )
    _fail(res, 422)
    return _no_store(res)


@router.get("/dev/overview")
async def dev_overview(request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("dev_overview", {"p_actor": user.id})
    _fail(res)
    return etag_response(request, res)


@router.post("/dev/outbox/retry")
async def dev_outbox_retry(user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("dev_retry_failed_outbox", {"p_actor": user.id})
    _fail(res)
    return _no_store(res)


@router.get("/dev/students")
async def dev_students(
    request: Request,
    group: str | None = None,
    q: str | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    grp = normalize_theory(group) if group else None
    rows = await store().rpc("dev_list_students", {"p_actor": user.id, "p_group": grp, "p_q": q, "p_limit": 150})
    return etag_response(request, rows)


class DevStudentUpsertIn(BaseModel):
    academic_no: str = Field(max_length=30)
    full_name: str = Field(max_length=80)
    theory_group_id: str = Field(max_length=16)
    practical_group_id: str = Field(max_length=16)


@router.post("/dev/students")
async def dev_student_upsert(body: DevStudentUpsertIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    no = normalize_academic_no(body.academic_no)
    name = clean_name(body.full_name, max_len=80)
    th = normalize_theory(body.theory_group_id)
    pr = normalize_practical(body.practical_group_id)
    if not (no and name and th and pr):
        raise HTTPException(422, "invalid_student_data")
    res = await store().rpc(
        "dev_upsert_student",
        {"p_actor": user.id, "p_no": no, "p_name": name, "p_theory": th, "p_practical": pr},
    )
    _fail(res, 422)
    return _no_store(res)


class DevStudentsImportIn(BaseModel):
    excel_base64: str = Field(max_length=5_000_000)


@router.post("/dev/students/import")
async def dev_students_import(body: DevStudentsImportIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    try:
        raw = base64.b64decode(body.excel_base64)
        ws = load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
    except Exception as exc:
        raise HTTPException(422, "invalid_excel") from exc

    rows: list[dict[str, str]] = []
    for cells in ws.iter_rows(min_row=2, values_only=True):
        if not cells or not any(cells):
            continue
        no_raw, name_raw, th_raw, pr_raw = (list(cells) + [None] * 4)[:4]
        if isinstance(no_raw, float) and no_raw.is_integer():
            no_raw = int(no_raw)
        no = normalize_academic_no(str(no_raw or ""))
        name = clean_name(str(name_raw or ""), max_len=80)
        th, pr = normalize_theory(th_raw), normalize_practical(pr_raw)
        if no and name and th and pr:
            rows.append({"academic_no": no, "full_name": name, "theory": th, "practical": pr})

    if not rows:
        raise HTTPException(422, "no_valid_rows")
    res = await store().rpc("import_students", {"p_actor": user.id, "p_rows": rows[:500]})
    return _no_store(res)


class UnlinkIn(BaseModel):
    academic_no: str = Field(max_length=30)


@router.post("/dev/students/unlink")
async def dev_student_unlink(body: UnlinkIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    no = normalize_academic_no(body.academic_no)
    if not no:
        raise HTTPException(422, "invalid_academic_no")
    res = await store().rpc("unlink_student", {"p_actor": user.id, "p_no": no})
    _fail(res, 422)
    return _no_store(res)


@router.get("/dev/guests")
async def dev_guests(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("dev_list_guests", {"p_actor": user.id})
    return etag_response(request, rows)


class BanIn(BaseModel):
    telegram_id: int
    banned: bool
    reason: str | None = Field(default=None, max_length=200)


@router.post("/dev/users/ban")
async def dev_user_ban(body: BanIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc(
        "set_ban",
        {"p_actor": user.id, "p_target": body.telegram_id, "p_banned": body.banned, "p_reason": body.reason},
    )
    _fail(res, 422)
    return _no_store(res)


@router.get("/dev/catalog")
async def dev_catalog(request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("dev_catalog_and_roles", {"p_actor": user.id})
    _fail(res)
    return etag_response(request, res)


class RoleGrantIn(BaseModel):
    telegram_id: int
    role: str = Field(max_length=20)
    group: str | None = Field(default=None, max_length=16)


@router.post("/dev/roles")
async def dev_grant_role(body: RoleGrantIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    grp = None if body.role == "developer" else normalize_theory(body.group)
    res = await store().rpc(
        "grant_role",
        {"p_actor": user.id, "p_target": body.telegram_id, "p_role": body.role, "p_group": grp},
    )
    _fail(res, 422)
    return _no_store(res)


@router.delete("/dev/roles/{grant_id}")
async def dev_revoke_role(grant_id: int, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("revoke_role", {"p_actor": user.id, "p_grant_id": grant_id})
    _fail(res, 422)
    return _no_store(res)


class SubjectUpsertIn(BaseModel):
    name_ar: str = Field(min_length=2, max_length=80)
    hashtag: str | None = Field(default=None, max_length=80)
    sort: int = 0


@router.post("/dev/subjects")
async def dev_subject_upsert(body: SubjectUpsertIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    tag = hashtag(body.hashtag or body.name_ar)
    res = await store().rpc(
        "dev_upsert_subject",
        {"p_actor": user.id, "p_name": body.name_ar.strip(), "p_hashtag": tag, "p_sort": body.sort},
    )
    _fail(res, 422)
    return _no_store(res)


class DoctorUpsertIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=2, max_length=80)
    telegram_id: int | None = None


@router.post("/dev/doctors")
async def dev_doctor_upsert(body: DoctorUpsertIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc(
        "dev_upsert_doctor",
        {"p_actor": user.id, "p_id": body.id, "p_name": body.name.strip(), "p_tg": body.telegram_id},
    )
    _fail(res, 422)
    return _no_store(res)


class TeachingUpsertIn(BaseModel):
    subject_id: int
    part: str = Field(max_length=16)
    group: str | None = Field(default=None, max_length=16)
    groups: list[str] = Field(default_factory=list)
    doctor_id: int


@router.post("/dev/teaching")
async def dev_teaching_upsert(body: TeachingUpsertIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    """يربط الدكتور بالمادة لمجموعة واحدة أو عدة مجموعات أو «الكل» بضغطة واحدة."""
    raw_groups = list(body.groups)
    if body.group:
        raw_groups.append(body.group)
    if not raw_groups or "ALL" in raw_groups:
        target_groups = (
            cohort.get().theory_ids()
            if body.part == "theory"
            else cohort.get().practical_ids()
        )
    else:
        target_groups = []
        for g in raw_groups:
            norm = normalize_theory(g) if body.part == "theory" else normalize_practical(g)
            if not norm:
                raise HTTPException(422, "invalid_group")
            if norm not in target_groups:
                target_groups.append(norm)

    for grp in target_groups:
        res = await store().rpc(
            "dev_upsert_teaching",
            {"p_actor": user.id, "p_subject": body.subject_id, "p_part": body.part, "p_group": grp, "p_doctor": body.doctor_id},
        )
        _fail(res, 422)
    return _no_store({"status": "ok", "count": len(target_groups)})


@router.delete("/dev/teaching/{tid}")
async def dev_teaching_delete(tid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    await store().rpc("dev_delete_teaching", {"p_actor": user.id, "p_id": tid})
    return {"ok": True}


class DestinationUpsertIn(BaseModel):
    key: str = Field(min_length=3, max_length=80)
    chat_id: int
    topic_id: int | None = None
    label: str | None = Field(default=None, max_length=120)


@router.post("/dev/destinations")
async def dev_destination_upsert(body: DestinationUpsertIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc(
        "dev_upsert_destination",
        {"p_actor": user.id, "p_key": body.key.strip(), "p_chat": body.chat_id, "p_topic": body.topic_id, "p_label": body.label},
    )
    _fail(res, 422)
    return _no_store(res)


@router.delete("/dev/destinations")
async def dev_destination_delete(key: str = Query(min_length=1), user: TgUser = Depends(current_user)) -> dict[str, bool]:
    await store().rpc("dev_delete_destination", {"p_actor": user.id, "p_key": key})
    return {"ok": True}


@router.get("/dev/trash")
async def dev_trash(request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("dev_list_trash", {"p_actor": user.id})
    _fail(res)
    for f in res.get("files") or []:
        f["title"] = file_title(f)
    return etag_response(request, res)


@router.get("/dev/activity")
async def dev_activity(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("dev_list_activity", {"p_actor": user.id, "p_limit": 80})
    return etag_response(request, rows)

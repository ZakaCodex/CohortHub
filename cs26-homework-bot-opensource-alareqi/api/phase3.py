"""مسارات المرحلة 3:
1) الحضور (النظري فقط، 12 محاضرة، فتح/إغلاق الجلسة، المسح، أجهزة Open Ticket Scanner، التصحيح، وتصدير Excel).
2) شؤوني: حضوري، تسليماتي (مع استرجاع الملفات وتعديلها في البوت)، باركود بطاقتي (Code 128)، تظلماتي (مع التعديل قبل الإغلاق).
3) المساعد الذكي والبحث الذكي في الملفات (Gemini + الاحتياطي المحلي)، والدعم والبلاغات."""
from __future__ import annotations

import re

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import build_bot_link, current_user, etag_response, settings, store
from core.assignments import can_submit, student_status
from core.assistant import (
    ask_assistant,
    interpret_file_query_local,
    rank_files,
    search_params,
    smart_search_files,
    validate_ticket,
    validate_ticket_reply,
)
from core.attendance import (
    build_attendance_xlsx,
    extract_scanned_academic_no,
    generate_device_token,
    hash_device_token,
    validate_correction,
    validate_session_open,
)
from core.barcode import render_barcode_svg
from core.grievances import (
    format_compiled_grievances,
    validate_cycle_open,
    validate_grievance_reason,
)
from core.tg_auth import TgUser
from core.timeutil import UTC

router = APIRouter(prefix="/api")


def _now() -> datetime:
    return datetime.now(UTC)


def _no_store(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers={"Cache-Control": "no-store"})


def _fail(res: dict[str, Any] | None, default: int = 400) -> None:
    if not isinstance(res, dict):
        return
    status = res.get("status")
    if status in (None, "ok"):
        return
    code = 404 if status == "not_found" else (403 if status == "forbidden" else default)
    raise HTTPException(code, status)


# =====================================================================
# 1) شؤوني: تسليماتي واسترجاع الملفات، وباركود البطاقة الجامعية (Code 128)
# =====================================================================
@router.get("/affairs/submissions")
async def my_submissions(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("my_assignments", {"p_tg": user.id, "p_limit": 80})
    now = _now()
    base_a = await build_bot_link("a_")
    base_sub = await build_bot_link("sub_")
    prefix_a = base_a[:-2] if base_a and base_a.endswith("a_") else None
    prefix_sub = base_sub[:-4] if base_sub and base_sub.endswith("sub_") else None
    for r in rows:
        r["state"] = student_status(now, r)
        r["can_submit"] = can_submit(now, r)
        aid = r.get("id")
        if aid is not None:
            r["bot_link"] = f"{prefix_a}a_{aid}" if prefix_a else None
            r["edit_bot_link"] = f"{prefix_sub}sub_{aid}" if prefix_sub else None
    return etag_response(request, rows)


@router.post("/assignments/{aid}/my-files/send")
async def send_my_submission_files(aid: int, user: TgUser = Depends(current_user)) -> dict[str, Any]:
    """يرسل ملفات تسليم الطالب المحفوظة بـ file_id إلى محادثته مع البوت (استرجاع الملفات)."""
    files = await store().rpc("my_submission_files", {"p_tg": user.id, "p_assignment": aid})
    if not files:
        raise HTTPException(404, "no_submission_files")
    for f in files:
        await store(). _request(
            "POST",
            "/outbox",
            json={"kind": "file_send", "chat_id": user.id, "payload": f},
        )
    return {"ok": True, "count": len(files)}


@router.get("/barcode")
async def get_barcode(user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("barcode_status", {"p_tg": user.id})
    _fail(res)
    res["svg"] = render_barcode_svg(
        res["academic_no"],
        res.get("full_name") or "",
        res.get("theory_group_id") or "",
    )
    return _no_store(res)


@router.post("/barcode/send")
async def send_barcode(user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("request_barcode", {"p_tg": user.id, "p_enqueue": True})
    if res.get("status") == "daily_limit_reached":
        raise HTTPException(429, "daily_limit_reached")
    _fail(res)
    return _no_store(res)


# =====================================================================
# 2) الحضور (النظري فقط، 12 محاضرة) وأجهزة المسح والتصحيح والتصدير
# =====================================================================
@router.get("/attendance/mine")
async def attendance_mine(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("my_attendance", {"p_tg": user.id})
    return etag_response(request, rows)


@router.get("/staff/attendance")
async def staff_attendance(
    request: Request,
    group: str | None = None,
    subject: int | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    res = await store().rpc("attendance_overview", {"p_actor": user.id, "p_group": group, "p_subject": subject})
    _fail(res)
    return etag_response(request, res)


@router.get("/staff/attendance/sessions/{sid}")
async def staff_attendance_session(sid: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("attendance_session_detail", {"p_actor": user.id, "p_session": sid})
    _fail(res)
    return etag_response(request, res)


class SessionOpenIn(BaseModel):
    subject_id: int
    group: str = Field(max_length=16)
    lecture_no: int = Field(ge=1, le=12)
    delegate_absent: bool = False


@router.post("/staff/attendance/sessions")
async def open_session(body: SessionOpenIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_session_open(body.model_dump())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "open_attendance_session",
        {
            "p_actor": user.id,
            "p_subject": clean["subject_id"],
            "p_group": clean["group"],
            "p_lecture": clean["lecture_no"],
            "p_delegate_absent": clean["delegate_absent"],
        },
    )
    _fail(res, 422)
    return _no_store(res)


class SessionCloseIn(BaseModel):
    delegate_absent: bool = False


@router.post("/staff/attendance/sessions/{sid}/close")
async def close_session(sid: int, body: SessionCloseIn | None = None, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc(
        "close_attendance_session",
        {"p_actor": user.id, "p_session": sid, "p_delegate_absent": bool(body and body.delegate_absent)},
    )
    _fail(res, 422)
    return _no_store(res)


@router.post("/staff/attendance/sessions/{sid}/cancel")
async def cancel_session(sid: int, body: SessionCloseIn | None = None, user: TgUser = Depends(current_user)) -> JSONResponse:
    """التراجع عن جلسة فُتحت بالخطأ (مادة/محاضرة خاطئة) قبل إغلاقها، دون تسجيل أي غياب.
    لا تعمل على جلسة مغلقة أو سبق إغلاقها (بياناتها معتمدة)."""
    res = await store().rpc(
        "cancel_attendance_session",
        {"p_actor": user.id, "p_session": sid, "p_delegate_absent": bool(body and body.delegate_absent)},
    )
    _fail(res, 409)
    return _no_store(res)


class ScanIn(BaseModel):
    code: str = Field(max_length=256)


@router.post("/staff/attendance/sessions/{sid}/scan")
async def scan_in_session(sid: int, body: ScanIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    no = extract_scanned_academic_no(body.code)
    if not no:
        raise HTTPException(422, "invalid_academic_no")
    res = await store().rpc("scan_attendance", {"p_actor": user.id, "p_session": sid, "p_academic_no": no})
    if res.get("status") == "not_found":
        raise HTTPException(404, "not_found")
    return _no_store(res)


class CorrectionIn(BaseModel):
    subject_id: int
    group: str = Field(max_length=16)
    lecture_no: int = Field(ge=1, le=12)
    student_no: str = Field(max_length=30)
    status: str = Field(max_length=12)
    reason: str = Field(max_length=300)


@router.post("/staff/attendance/correct")
async def correct_attendance(body: CorrectionIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_correction(body.model_dump())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "fn_correct_attendance",
        {
            "p_actor": user.id,
            "p_subject": clean["subject_id"],
            "p_group": clean["group"],
            "p_lecture": clean["lecture_no"],
            "p_student_no": clean["student_no"],
            "p_status": clean["status"],
            "p_reason": clean["reason"],
        },
    )
    _fail(res, 422)
    return _no_store(res)


@router.get("/staff/attendance/matrix")
async def get_attendance_matrix(
    request: Request,
    subject: int = Query(gt=0),
    group: str = Query(min_length=2, max_length=16),
    user: TgUser = Depends(current_user),
) -> Response:
    res = await store().rpc("attendance_matrix", {"p_actor": user.id, "p_subject": subject, "p_group": group})
    _fail(res)
    return etag_response(request, res)


@router.get("/staff/attendance/export")
async def export_attendance_excel(
    subject: int = Query(gt=0),
    group: str = Query(min_length=2, max_length=16),
    user: TgUser = Depends(current_user),
) -> Response:
    matrix = await store().rpc("attendance_matrix", {"p_actor": user.id, "p_subject": subject, "p_group": group})
    _fail(matrix)
    xlsx_bytes = build_attendance_xlsx(matrix)
    filename = f"attendance_{group}_s{subject}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


class ExportBotIn(BaseModel):
    subject_id: int
    group: str = Field(max_length=16)


@router.post("/staff/attendance/export-bot")
async def export_attendance_to_bot(body: ExportBotIn, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    """يرسل ملف Excel للحضور مباشرة إلى محادثة المشرف في البوت."""
    matrix = await store().rpc("attendance_matrix", {"p_actor": user.id, "p_subject": body.subject_id, "p_group": body.group})
    _fail(matrix)
    await store()._request(
        "POST",
        "/outbox",
        json={
            "kind": "attendance_excel",
            "chat_id": user.id,
            "payload": {"subject_id": body.subject_id, "group": body.group, "actor_id": user.id},
        },
    )
    return {"ok": True}


class DeviceCreateIn(BaseModel):
    group: str = Field(max_length=16)
    label: str = Field(min_length=2, max_length=60)


@router.post("/staff/attendance/devices")
async def create_device(body: DeviceCreateIn, request: Request, user: TgUser = Depends(current_user)) -> JSONResponse:
    raw_token, token_hash, prefix = generate_device_token()
    res = await store().rpc(
        "create_scanner_device",
        {
            "p_actor": user.id,
            "p_group": body.group.strip().upper(),
            "p_label": body.label.strip(),
            "p_token_hash": token_hash,
            "p_token_prefix": prefix,
        },
    )
    _fail(res, 422)
    base = settings().web_app_url or str(request.base_url).rstrip("/")
    res["token"] = raw_token  # يظهر مرة واحدة فقط عند الإنشاء
    res["scanner_url"] = f"{base}/api/scanner/scan?key={raw_token}"
    return _no_store(res)


@router.delete("/staff/attendance/devices/{did}")
async def revoke_device(did: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc("revoke_scanner_device", {"p_actor": user.id, "p_device_id": did})
    _fail(res)
    return {"ok": True}


# نقطة المسح لأجهزة Open Ticket Scanner (تدعم GET وPOST ومفتاح الجهاز في الرابط أو الرأس أو الجسم)
_DEVICE_KEY_RE = re.compile(r"ots_[A-Za-z0-9_\-]{20,64}")   # الشكل الذي يولّده generate_device_token


async def _handle_external_scanner(
    request: Request,
    key: str | None,
    code: str | None,
    authorization: str,
    x_device_key: str,
) -> JSONResponse:
    body: dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
        except Exception:
            body = {}

    raw_token = (
        key
        or x_device_key
        or (authorization.partition(" ")[2] if authorization.lower().startswith("bearer ") else "")
        or str(body.get("key") or body.get("token") or "")
    ).strip()
    if not raw_token:
        return _no_store({"status": "error", "valid": False, "message": "مفتاح الجهاز مفقود"}, 401)
    if not _DEVICE_KEY_RE.fullmatch(raw_token):
        # مفتاح بشكل غير صحيح: نرفضه فورًا دون لمس قاعدة البيانات (يخفف أثر الإغراق/التخمين)
        return _no_store({"status": "error", "valid": False, "message": "مفتاح الجهاز غير صالح"}, 401)

    raw_code = (
        code
        or request.query_params.get("ticket")
        or request.query_params.get("barcode")
        or body.get("code")
        or body.get("ticket")
        or body.get("barcode")
        or body.get("academic_no")
        or body.get("student_university_id")
    )
    academic_no = extract_scanned_academic_no(raw_code)
    if not academic_no:
        return _no_store({"status": "invalid_code", "valid": False, "message": "الرمز الممسوح لا يحوي رقماً أكاديمياً صالحاً"}, 422)

    res = await store().rpc(
        "scan_by_device_token",
        {"p_token_hash": hash_device_token(raw_token), "p_academic_no": academic_no},
    )
    st = res.get("status")
    if st == "invalid_device":
        return _no_store({"status": st, "valid": False, "message": "مفتاح الجهاز غير صالح أو ملغى"}, 401)
    if st == "no_open_session":
        return _no_store({"status": st, "valid": False, "message": f"لا توجد جلسة حضور مفتوحة للمجموعة {res.get('group', '')}"}, 409)
    if st == "student_not_found":
        return _no_store({"status": st, "valid": False, "message": f"الرقم {academic_no} غير موجود"}, 404)
    if st == "wrong_group":
        stu = res.get("student") or {}
        return _no_store({
            "status": st,
            "valid": False,
            "message": f"الطالب {stu.get('name', '')} في {res.get('student_group')} وليس {res.get('expected_group')}",
            "student": stu,
        }, 409)

    stu = res.get("student") or {}
    msg = (
        f"✅ تم التحضير: {stu.get('name', '')} ({stu.get('no', '')})"
        if st == "ok"
        else f"⚠️ مسجّل مسبقاً: {stu.get('name', '')} ({stu.get('no', '')})"
    )
    return _no_store({
        "status": st,
        "valid": True,
        "already_scanned": st == "already_scanned",
        "message": msg,
        "student": stu,
        "subject": res.get("subject"),
        "lecture_no": res.get("lecture_no"),
        "present_count": res.get("present_count"),
    })


@router.get("/scanner/scan")
@router.post("/scanner/scan")
async def external_scanner_scan(
    request: Request,
    key: str | None = None,
    code: str | None = None,
    authorization: str = Header(default=""),
    x_device_key: str = Header(default=""),
) -> JSONResponse:
    return await _handle_external_scanner(request, key, code, authorization, x_device_key)


# =====================================================================
# 3) التظلمات (طالب + إدارة وتجميع)
# =====================================================================
@router.get("/grievances")
async def grievances_mine(request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("my_grievances", {"p_tg": user.id})
    _fail(res)
    return etag_response(request, res)


class GrievanceSubmitIn(BaseModel):
    reason: str = Field(max_length=1200)


@router.post("/grievances/{cycle_id}")
async def submit_or_edit_grievance(cycle_id: int, body: GrievanceSubmitIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean_reason, err = validate_grievance_reason(body.reason)
    if err:
        raise HTTPException(422, err)
    res = await store().rpc("upsert_grievance", {"p_tg": user.id, "p_cycle": cycle_id, "p_reason": clean_reason})
    _fail(res, 422)
    return _no_store(res)


@router.get("/staff/grievances")
async def staff_grievances_list(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("staff_grievance_cycles", {"p_actor": user.id})
    return etag_response(request, rows)


class GrievanceCycleOpenIn(BaseModel):
    subject_id: int
    part: str = Field(max_length=16)
    theory_group: str | None = Field(default=None, max_length=16)
    practical_groups: list[str] = Field(default_factory=list)
    closes_local: str = Field(max_length=25)
    title: str | None = Field(default=None, max_length=200)


@router.post("/staff/grievances")
async def staff_open_grievance_cycle(body: GrievanceCycleOpenIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_cycle_open(body.model_dump(), _now())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc(
        "open_grievance_cycle",
        {
            "p_actor": user.id,
            "p_subject": clean["subject_id"],
            "p_part": clean["part"],
            "p_theory_group": clean["theory_group"],
            "p_practical_groups": clean["practical_groups"],
            "p_closes_at": clean["closes_at"],
            "p_title": clean["title"],
        },
    )
    _fail(res, 422)
    return _no_store(res)


@router.get("/staff/grievances/{cycle_id}")
async def staff_grievance_detail(cycle_id: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("grievance_cycle_detail", {"p_actor": user.id, "p_cycle": cycle_id})
    _fail(res)
    cycle = res["cycle"]
    cycle["compiled_text"] = format_compiled_grievances(cycle)
    return etag_response(request, cycle)


@router.post("/staff/grievances/{cycle_id}/close")
async def staff_close_grievance(cycle_id: int, user: TgUser = Depends(current_user)) -> JSONResponse:
    res = await store().rpc("close_grievance_cycle", {"p_actor": user.id, "p_cycle": cycle_id})
    _fail(res, 422)
    cycle = res["cycle"]
    cycle["compiled_text"] = format_compiled_grievances(cycle)
    return _no_store(cycle)


@router.post("/staff/grievances/{cycle_id}/send-bot")
async def staff_send_compiled_grievance(cycle_id: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    res = await store().rpc("grievance_cycle_detail", {"p_actor": user.id, "p_cycle": cycle_id})
    _fail(res)
    await store()._request(
        "POST",
        "/outbox",
        json={"kind": "grievance_compiled", "chat_id": user.id, "payload": res["cycle"]},
    )
    return {"ok": True}


# =====================================================================
# 4) المساعد الذكي، البحث الذكي في الملفات، ونظام الدعم والبلاغات
# =====================================================================
@router.get("/assistant")
async def assistant_info(user: TgUser = Depends(current_user)) -> JSONResponse:
    ctx = await store().rpc("assistant_context", {"p_tg": user.id})
    return _no_store({
        "role": ctx.get("role"),
        "used": ctx.get("used", 0),
        "limit": ctx.get("limit", 10),
        "remaining": ctx.get("remaining", 0),
        "history": ctx.get("history", []),
        "attendance": ctx.get("attendance", []),
    })


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=600)


@router.post("/assistant/ask")
async def assistant_ask(body: AskIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    ctx = await store().rpc("assistant_context", {"p_tg": user.id})
    remaining = int(ctx.get("remaining") or 0)

    # الأسئلة الجاهزة والكاش مجانية؛ Gemini فقط عند وجود رصيد، ولا يُخصم الرصيد إلا إذا استُهلك Gemini فعلًا
    reply = await ask_assistant(body.question, ctx, _now(), store=store(), allow_ai=remaining > 0)
    if reply.get("blocked"):
        raise HTTPException(429, "daily_limit_reached")

    # إذا كان السؤال يبحث عن ملف لمادة معينة نرفق الملفات المطابقة (بالكلمات المفتاحية فقط، بلا Gemini)
    matched_files: list[dict[str, Any]] = []
    subjects = ctx.get("subjects") or []
    criteria = interpret_file_query_local(body.question, subjects)
    q = body.question
    if criteria.get("subject_id") and (criteria.get("content_type") or criteria.get("numbers") or any(w in q for w in ("ملف", "ملخص", "محاضر", "نموذج"))):
        try:
            candidates = await store().rpc("search_files_smart", search_params(user.id, criteria))
        except Exception:  # noqa: BLE001 — قاعدة لم تُطبَّق عليها 0008 بعد
            candidates = await store().rpc("search_files_smart", search_params(user.id, criteria, with_limit=False))
        matched_files = rank_files(candidates or [], criteria, limit=5)

    counted = bool(reply.get("counted"))
    rec = await store().rpc(
        "record_assistant_exchange",
        {"p_tg": user.id, "p_question": body.question, "p_answer": reply["answer"], "p_counted": counted},
    )
    if rec.get("status") == "limit_reached":
        raise HTTPException(429, "daily_limit_reached")

    return _no_store({
        "answer": reply["answer"],
        "needs_ticket": reply["needs_ticket"],
        "used_ai": reply["used_ai"],
        "files": matched_files,
        "attendance": ctx.get("attendance", []),
        "used": rec.get("used", 0),
        "limit": rec.get("limit", 10),
        "remaining": rec.get("remaining", 0),
    })


class SmartSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)


@router.post("/files/smart-search")
async def smart_file_search(body: SmartSearchIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    """البحث في الملفات: الكلمات المفتاحية أولًا (بلا توكنات)، ثم Gemini فقط إذا لم تكفِ النتائج."""
    subjects = await store().select(
        "subjects",
        {"select": "id,name:name_ar,tag:hashtag", "is_active": "eq.true", "deleted_at": "is.null", "order": "sort,id"},
    )
    ranked, criteria, used_ai = await smart_search_files(store().rpc, user.id, body.query, subjects, limit=15, store=store())
    return _no_store({"items": ranked, "criteria": criteria, "used_ai": used_ai})


@router.get("/tickets")
async def tickets_mine(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("my_tickets", {"p_tg": user.id})
    return etag_response(request, rows)


class TicketCreateIn(BaseModel):
    category: str = Field(max_length=20)
    body: str = Field(max_length=1600)


@router.post("/tickets")
async def ticket_create(body: TicketCreateIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_ticket(body.category, body.body)
    if err:
        raise HTTPException(422, err)
    cat, text = clean
    res = await store().rpc("create_ticket", {"p_tg": user.id, "p_category": cat, "p_body": text})
    _fail(res, 422)
    return _no_store(res)


@router.get("/staff/tickets")
async def staff_tickets_list(
    request: Request,
    status: str | None = None,
    user: TgUser = Depends(current_user),
) -> Response:
    rows = await store().rpc("list_tickets", {"p_actor": user.id, "p_status": status})
    return etag_response(request, rows)


class TicketReplyIn(BaseModel):
    reply: str = Field(max_length=1600)


@router.post("/staff/tickets/{tid}/reply")
async def staff_ticket_reply(tid: int, body: TicketReplyIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean_reply, err = validate_ticket_reply(body.reply)
    if err:
        raise HTTPException(422, err)
    res = await store().rpc("reply_ticket", {"p_actor": user.id, "p_ticket_id": tid, "p_reply": clean_reply})
    _fail(res, 422)
    return _no_store(res)

"""مسارات المرحلة 2: الملفات، الواجبات، الإشعارات، الإعدادات.
التحقق من الصحة هنا (core)، والصلاحيات والمنطق الذرّي في دوال SQL."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.deps import build_bot_link, current_user, etag_response, store
from core.assignments import can_submit, parse_ts, student_status, validate_assignment_meta
from core.files import file_title, validate_file_meta
from core.messages import render_notification
from core.tg_auth import TgUser
from core.timeutil import UTC

router = APIRouter(prefix="/api")


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(res: dict[str, Any], default: int = 400) -> None:
    """يحوّل حالة خطأ من SQL إلى استجابة HTTP."""
    status = res.get("status")
    if status in (None, "ok"):
        return
    code = 404 if status == "not_found" else default
    raise HTTPException(code, status)


# ---------------- الرئيسية والإشعارات والإعدادات ----------------
@router.get("/home")
async def home(request: Request, user: TgUser = Depends(current_user)) -> Response:
    data = await store().rpc("home_summary", {"p_tg": user.id})
    now = _now()
    open_items = []
    for it in data.get("items", []):
        st = student_status(now, it)
        if st in ("open", "late") and not it.get("submitted_at"):
            it["state"] = st
            open_items.append(it)
    data["items"] = open_items
    data["missed"] = []
    if "sharek" not in data:
        try:
            sh_rows = await store().rpc("my_sharek", {"p_tg": user.id}) or []
            data["sharek"] = [
                c for c in sh_rows
                if (c.get("kind") == "poll" and c.get("my_vote") is None)
                or (c.get("kind") == "submission" and not c.get("is_finished"))
            ][:4]
        except Exception:  # noqa: BLE001
            data["sharek"] = []
    return etag_response(request, data)


@router.get("/notifications")
async def notifications(request: Request, before: int | None = None, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("list_notifications", {"p_tg": user.id, "p_limit": 30, "p_before": before})
    for r in rows:
        r["title"], r["body"] = render_notification(r["kind"], r.get("payload") or {}, parse_ts(r["created_at"]))
    return etag_response(request, rows)


@router.post("/notifications/read")
async def notifications_read(user: TgUser = Depends(current_user)) -> dict[str, bool]:
    await store().rpc("mark_notifications_read", {"p_tg": user.id})
    return {"ok": True}


class SettingsIn(BaseModel):
    notify_assignment: bool | None = None
    notify_sharek: bool | None = None
    remind_24: bool | None = None
    remind_3: bool | None = None
    remind_1: bool | None = None


@router.get("/settings")
async def get_settings(user: TgUser = Depends(current_user)) -> JSONResponse:
    return JSONResponse(await store().rpc("get_user_settings", {"p_tg": user.id}), headers={"Cache-Control": "no-store"})


@router.put("/settings")
async def put_settings(body: SettingsIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    patch = body.model_dump(exclude_none=True)
    return JSONResponse(await store().rpc("set_user_settings", {"p_tg": user.id, "p_patch": patch}),
                        headers={"Cache-Control": "no-store"})


# ---------------- الملفات ----------------
@router.get("/files")
async def files(request: Request, subject: str | int | None = None, part: str | None = None, type: str | None = None,
                offset: int = 0, limit: int = 50, user: TgUser = Depends(current_user)) -> Response:
    part_clean = part.strip() if isinstance(part, str) and part.strip() else None
    type_clean = type.strip() if isinstance(type, str) and type.strip() else None
    subj_clean: int | None = None
    if subject not in (None, ""):
        try:
            subj_clean = int(subject)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "invalid_subject") from exc
    if part_clean not in (None, "theory", "practical"):
        raise HTTPException(422, "invalid_part")
    rows = await store().rpc("list_files", {"p_tg": user.id, "p_subject": subj_clean, "p_part": part_clean, "p_type": type_clean,
                                            "p_limit": min(max(limit, 1), 100), "p_offset": max(offset, 0)})
    for r in rows:
        r["title"] = file_title(r)
    return etag_response(request, rows)


class FileDraftIn(BaseModel):
    subject_id: int
    part: str
    content_type: str
    doctor_id: int | None = None
    exam_kind: str | None = None
    label_kind: str | None = None
    numbers: list[int] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=400)
    replaces_id: int | None = None


@router.post("/files/draft")
async def file_draft(body: FileDraftIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_file_meta(body.model_dump())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc("create_draft", {"p_actor": user.id, "p_kind": "file", "p_payload": clean})
    _fail(res, 422)
    link = await build_bot_link(f"up_{res['draft_id']}")
    return JSONResponse({"draft_id": res["draft_id"], "bot_link": link},
                        headers={"Cache-Control": "no-store"})


@router.post("/files/{file_id}/send")
async def file_send(file_id: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    """يرسل الملف إلى محادثة المستخدم مع البوت (الإرسال كله في البوت بـ file_id)."""
    _fail(await store().rpc("request_file_send", {"p_tg": user.id, "p_id": file_id}))
    return {"ok": True}


@router.delete("/files/{file_id}")
async def file_delete(file_id: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    _fail(await store().rpc("delete_file", {"p_actor": user.id, "p_id": file_id}))
    return {"ok": True}


@router.post("/files/{file_id}/restore")
async def file_restore(file_id: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    _fail(await store().rpc("restore_file", {"p_actor": user.id, "p_id": file_id}))
    return {"ok": True}


# ---------------- الواجبات: الطالب ----------------
async def _with_state_and_links(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
    return rows


@router.get("/assignments")
async def assignments(request: Request, user: TgUser = Depends(current_user)) -> Response:
    rows = await store().rpc("my_assignments", {"p_tg": user.id})
    return etag_response(request, await _with_state_and_links(rows or []))


@router.get("/assignments/{aid}")
async def assignment_detail(aid: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    d = await store().rpc("assignment_detail", {"p_tg": user.id, "p_id": aid})
    _fail(d)
    sub = d.get("submission") or {}
    now = _now()
    row = {"due_at": d["due_at"], "closes_at": d["closes_at"], "status": d["status_db"], "submitted_at": sub.get("submitted_at")}
    d["state"] = student_status(now, row)
    d["can_submit"] = can_submit(now, row)
    d["bot_link"] = await build_bot_link(f"a_{aid}")
    d["edit_bot_link"] = await build_bot_link(f"sub_{aid}")
    return etag_response(request, d)


# ---------------- الواجبات: الإدارة ----------------
class AssignmentDraftIn(BaseModel):
    subject_id: int
    part: str
    group: str
    due_local: str = Field(max_length=20)       # "2026-10-25 20:00" بتوقيت صنعاء
    description: str | None = Field(default=None, max_length=1000)
    allow_text: bool = False                     # السماح بإرسال نصوص/روابط في البوت بجانب الملفات


@router.post("/assignments/draft")
async def assignment_draft(body: AssignmentDraftIn, user: TgUser = Depends(current_user)) -> JSONResponse:
    clean, err = validate_assignment_meta(body.model_dump(), _now())
    if err:
        raise HTTPException(422, err)
    res = await store().rpc("create_draft", {"p_actor": user.id, "p_kind": "assignment", "p_payload": clean})
    _fail(res, 422)
    link = await build_bot_link(f"ad_{res['draft_id']}")
    return JSONResponse({"draft_id": res["draft_id"], "bot_link": link},
                        headers={"Cache-Control": "no-store"})


class PublishIn(BaseModel):
    draft_id: int


@router.post("/assignments/publish")
async def assignment_publish(body: PublishIn, user: TgUser = Depends(current_user)) -> dict[str, Any]:
    res = await store().rpc("publish_assignment", {"p_actor": user.id, "p_draft": body.draft_id})
    _fail(res, 422)
    return {"id": res["id"], "number": res["number"]}


@router.get("/staff/assignments")
async def staff_assignments(request: Request, user: TgUser = Depends(current_user)) -> Response:
    return etag_response(request, await store().rpc("staff_assignments", {"p_actor": user.id}))


@router.get("/staff/assignments/{aid}")
async def staff_roster(aid: int, request: Request, user: TgUser = Depends(current_user)) -> Response:
    res = await store().rpc("assignment_roster", {"p_actor": user.id, "p_id": aid})
    _fail(res)
    return etag_response(request, res)


class ExtendIn(BaseModel):
    due_local: str = Field(max_length=20)


@router.post("/assignments/{aid}/extend")
async def assignment_extend(aid: int, body: ExtendIn, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    from core.timeutil import parse_sanaa
    try:
        due = parse_sanaa(body.due_local)
    except ValueError as exc:
        raise HTTPException(422, "invalid_due") from exc
    _fail(await store().rpc("extend_assignment", {"p_actor": user.id, "p_id": aid, "p_new_due": due.isoformat()}), 422)
    return {"ok": True}


@router.post("/assignments/{aid}/close")
async def assignment_close(aid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    _fail(await store().rpc("close_assignment", {"p_actor": user.id, "p_id": aid}))
    return {"ok": True}


@router.delete("/assignments/{aid}")
async def assignment_delete(aid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    _fail(await store().rpc("delete_assignment", {"p_actor": user.id, "p_id": aid}))
    return {"ok": True}


@router.post("/assignments/{aid}/restore")
async def assignment_restore(aid: int, user: TgUser = Depends(current_user)) -> dict[str, bool]:
    _fail(await store().rpc("restore_assignment", {"p_actor": user.id, "p_id": aid}))
    return {"ok": True}

"""API على Vercel (FastAPI). كل طلب شخصي يتحقق من توقيع initData، والصلاحيات تُفحص في قاعدة البيانات."""
from __future__ import annotations

import logging

import json
import os
import sys

_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path
from pydantic import BaseModel, Field

from api.deps import current_user, etag_response, store
from api.phase2 import router as phase2_router
from api.phase3 import router as phase3_router
from api.phase4 import router as phase4_router
from core import cohort
from core.store import StoreError
from core.text import clean_name, normalize_academic_no
from core.tg_auth import TgUser

log = logging.getLogger("api")

app = FastAPI(title="Student Bot API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=400)
app.include_router(phase2_router)
app.include_router(phase3_router)
app.include_router(phase4_router)


@app.exception_handler(StoreError)
async def store_error_handler(request: Request, exc: StoreError) -> JSONResponse:
    if exc.forbidden:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    log.error("store error on %s: %s", request.url.path, exc)    # التفاصيل في سجلاتك فقط، لا للعميل
    return JSONResponse({"error": "upstream"}, status_code=502)


@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
    log.error("runtime error on %s: %s", request.url.path, exc)
    return JSONResponse({"error": "server_error"}, status_code=500)


@app.get("/api/health")
async def health() -> dict[str, object]:
    missing = [
        name for name in ("BOT_TOKEN", "SUPABASE_URL", "SUPABASE_SERVICE_KEY")
        if not os.getenv(name, "").strip()
    ]
    return {"ok": True, "env_ready": len(missing) == 0, "missing_env": missing}


@app.get("/api/bootstrap")
async def bootstrap(request: Request, user: TgUser = Depends(current_user)) -> Response:
    data = await store().bootstrap(user.id, user.full_name, user.username)
    if isinstance(data, dict):
        data = {**data, "cohort": cohort.get().public()}   # هوية الدفعة وعدد المجموعات للواجهة
    return etag_response(request, data)


@app.get("/api/versions")
async def versions(request: Request, _: TgUser = Depends(current_user)) -> Response:
    return etag_response(request, await store().versions())


@app.get("/api/public/catalog")
async def public_catalog() -> Response:
    """بيانات عامة غير شخصية فقط، فتُخزَّن على حافة Vercel."""
    subjects = await store().select("subjects", {
        "select": "id,name_ar,hashtag", "is_active": "eq.true", "deleted_at": "is.null", "order": "sort,id"})
    groups = await store().select("practical_groups", {"select": "code,theory_group_id", "order": "sort"})
    body = json.dumps({"subjects": subjects, "practical_groups": groups}, ensure_ascii=False, separators=(",", ":"))
    return Response(body, media_type="application/json",
                    headers={"Cache-Control": "public, s-maxage=300, stale-while-revalidate=3600"})


class LookupIn(BaseModel):
    academic_no: str = Field(max_length=40)


class GuestIn(BaseModel):
    name: str = Field(max_length=80)


def _no_store(data) -> JSONResponse:
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@app.post("/api/link/lookup")
async def link_lookup(body: LookupIn, user: TgUser = Depends(current_user)) -> Response:
    no = normalize_academic_no(body.academic_no)
    if not no:
        raise HTTPException(422, "invalid_academic_no")
    return _no_store(await store().lookup_student(user.id, no))


@app.post("/api/link/confirm")
async def link_confirm(body: LookupIn, user: TgUser = Depends(current_user)) -> Response:
    no = normalize_academic_no(body.academic_no)
    if not no:
        raise HTTPException(422, "invalid_academic_no")
    return _no_store(await store().link_student(user.id, no, user.username))


@app.post("/api/guest")
async def guest(body: GuestIn, user: TgUser = Depends(current_user)) -> Response:
    name = clean_name(body.name)
    if not name:
        raise HTTPException(422, "invalid_name")
    return _no_store(await store().register_guest(user.id, name, user.username))


_PUBLIC_DIR = Path(_ROOT_DIR) / "public"


@app.get("/")
@app.get("/index.html")
async def serve_index() -> Response:
    return FileResponse(
        _PUBLIC_DIR / "index.html",
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "public, max-age=0, must-revalidate"},
    )


@app.get("/sw.js")
async def serve_sw() -> Response:
    return FileResponse(
        _PUBLIC_DIR / "sw.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=0, must-revalidate"},
    )


@app.get("/assets/{asset_path:path}")
async def serve_assets(asset_path: str) -> Response:
    target = (_PUBLIC_DIR / "assets" / asset_path).resolve()
    assets_root = (_PUBLIC_DIR / "assets").resolve()
    if not str(target).startswith(str(assets_root)) or not target.is_file():
        raise HTTPException(404, "not_found")
    media = "text/css; charset=utf-8" if target.suffix == ".css" else "application/javascript; charset=utf-8"
    return FileResponse(
        target,
        media_type=media,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )

"""نقطة دخول Vercel لـ FastAPI مع تشخيص آمن لأي خطأ إقلاع."""
from __future__ import annotations

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_HERE) if os.path.basename(_HERE) == "api" else _HERE
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

try:
    from api.app import app  # noqa: E402
except Exception as _boot_exc:  # noqa: BLE001
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    _tb = traceback.format_exc()
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def _boot_error(path: str) -> JSONResponse:
        return JSONResponse(
            {
                "ok": False,
                "boot_error": str(_boot_exc),
                "error_type": type(_boot_exc).__name__,
                "root_dir": _ROOT_DIR,
                "root_files": sorted(os.listdir(_ROOT_DIR)) if os.path.isdir(_ROOT_DIR) else [],
                "traceback": _tb.splitlines()[-10:],
            },
            status_code=500,
        )

__all__ = ["app"]

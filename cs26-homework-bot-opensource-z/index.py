"""Vercel FastAPI entrypoint.

Vercel detects FastAPI applications from supported root-level entrypoints.
The application implementation remains in api/app.py.
"""
from __future__ import annotations

import os
import sys

_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from api.app import app  # noqa: E402

__all__ = ["app"]

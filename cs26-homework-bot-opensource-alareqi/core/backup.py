"""نسخة احتياطية أسبوعية لقاعدة البيانات: تجميع الجداول في ملفات zip (JSON لكل جدول) تُرسل للمطوّر،
وقراءتها لاحقًا للاسترجاع. لا اعتماد على تيليجرام هنا (منطق صرف قابل للاختبار)."""
from __future__ import annotations

import io
import json
import zlib
import zipfile
from typing import Any, Iterable, Mapping, Sequence

# نفس ترتيب backup_tables() في 0010_backup.sql (يحترم المفاتيح الأجنبية)
BACKUP_TABLES: list[str] = [
    "app_settings", "data_versions", "theory_groups", "practical_groups", "subjects", "doctors", "teaching",
    "tg_destinations", "students", "users", "role_grants", "role_permissions", "activity_log", "link_attempts",
    "user_settings", "notifications", "drafts", "files", "assignments", "assignment_files", "submissions",
    "submission_files", "submission_deliveries", "reminders_sent", "attendance_sessions", "scanner_devices",
    "attendance_records", "attendance_corrections", "barcode_logs", "grievance_cycles", "grievances",
    "assistant_logs", "support_tickets", "report_templates", "daily_reports", "weekly_reports",
    "report_reminders_sent", "sharek_campaigns", "sharek_votes", "sharek_items", "sharek_sessions",
    "mcq_exams", "mcq_questions", "mcq_attempts",
]

PART_LIMIT = 40 * 1024 * 1024   # حدّ رفع البوت 50MB؛ نترك هامشًا
FORMAT_VERSION = 1


def _table_json(rows: Sequence[Any]) -> bytes:
    return json.dumps(list(rows), ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def plan_parts(sizes: Mapping[str, int], limit: int = PART_LIMIT, order: Iterable[str] | None = None) -> list[list[str]]:
    """يوزّع الجداول على أجزاء لا يتجاوز حجمها المضغوط limit (جدول أكبر من الحد يأخذ جزءًا وحده)."""
    parts: list[list[str]] = []
    cur: list[str] = []
    cur_size = 0
    for name in (order if order is not None else sizes):
        if name not in sizes:
            continue
        sz = sizes[name]
        if cur and cur_size + sz > limit:
            parts.append(cur)
            cur, cur_size = [], 0
        cur.append(name)
        cur_size += sz
    if cur:
        parts.append(cur)
    return parts or [[]]


def build_backup_parts(
    dump: Mapping[str, Sequence[Any]],
    created_at: str,
    label: str,
    limit: int = PART_LIMIT,
) -> list[tuple[str, bytes]]:
    """يعيد [(اسم_الملف، محتوى_zip), ...]. كل جزء يحمل manifest.json بعدد صفوف كل جدول."""
    blobs = {t: _table_json(dump[t]) for t in BACKUP_TABLES if t in dump}
    sizes = {t: len(zlib.compress(b, 6)) for t, b in blobs.items()}
    groups = plan_parts(sizes, limit, order=BACKUP_TABLES)
    counts = {t: len(dump[t]) for t in blobs}

    out: list[tuple[str, bytes]] = []
    for i, names in enumerate(groups, start=1):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            manifest = {
                "format": FORMAT_VERSION,
                "created_at": created_at,
                "label": label,
                "part": i,
                "parts": len(groups),
                "tables_in_part": names,
                "row_counts": counts,
            }
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for name in names:
                zf.writestr(f"{name}.json", blobs[name])
        out.append((f"backup_{label}_part{i}of{len(groups)}.zip", buf.getvalue()))
    return out


def backup_caption(dump: Mapping[str, Sequence[Any]], label: str, part: int, parts: int) -> str:
    def n(t: str) -> int:
        return len(dump.get(t) or [])
    head = f"🗄️ نسخة احتياطية أسبوعية — {label}"
    if parts > 1:
        head += f" (الجزء {part}/{parts})"
    return (
        f"{head}\n"
        f"الطلاب: {n('students')} · الواجبات: {n('assignments')} · التسليمات: {n('submissions')} · "
        f"سجلات الحضور: {n('attendance_records')}\n"
        "احتفظ بها في مكان آمن؛ تحتوي بيانات الطلاب ولا تُحوَّل لأحد."
    )


def read_backup_zips(blobs: Iterable[bytes]) -> dict[str, list[Any]]:
    """يدمج أجزاء النسخة (ملفات zip) في {جدول: صفوف} للاسترجاع."""
    merged: dict[str, list[Any]] = {}
    for blob in blobs:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            for name in zf.namelist():
                if name == "manifest.json" or not name.endswith(".json"):
                    continue
                merged.setdefault(name[:-5], []).extend(json.loads(zf.read(name).decode("utf-8")))
    return merged

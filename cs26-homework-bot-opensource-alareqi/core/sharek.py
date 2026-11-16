"""منطق ميزة «شارك» (مدمجة):
- استفتاء باختيار واحد نهائي (2–10 خيارات).
- أو استقبال مشاركات متعددة (نص/صورة/ملف) تُنهى مرة واحدة.
- تصدير النتائج إلى Excel لكل مجموعة."""
from __future__ import annotations

import io
import re
from typing import Any, Mapping

from core.attendance import _build_xlsx_minimal
from core.text import normalize_theory


def clean_multiline(value: Any) -> str:
    """ينظّف النص مع الحفاظ على الأسطر (بما فيها الأسطر الفارغة) كما كتبها المستخدم:
    يوحّد نهايات الأسطر، ويقصّ الفراغات الزائدة داخل كل سطر وفي طرفي النص فقط."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[^\S\n]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(lines).strip()


def validate_sharek_campaign(meta: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    kind = str(meta.get("kind") or "").strip()
    if kind not in ("poll", "submission"):
        return None, "invalid_kind"

    raw_grp = str(meta.get("group") or meta.get("theory_group") or "").strip()
    if raw_grp in ("", "ALL", "all"):
        group = None
    else:
        group = normalize_theory(raw_grp)
        if not group:
            return None, "invalid_group"

    title = clean_multiline(meta.get("title"))
    if len(title) < 3:
        return None, "title_too_short"
    if len(title) > 1000:
        return None, "title_too_long"

    options: list[str] = []
    if kind == "poll":
        raw_opts = meta.get("options") or []
        if isinstance(raw_opts, str):
            raw_opts = [line.strip() for line in raw_opts.splitlines() if line.strip()]
        if not isinstance(raw_opts, (list, tuple)):
            return None, "invalid_options"
        seen: set[str] = set()
        for o in raw_opts:
            clean_o = re.sub(r"\s+", " ", str(o or "")).strip()
            if clean_o and clean_o not in seen:
                seen.add(clean_o)
                options.append(clean_o[:100])
        if len(options) < 2 or len(options) > 10:
            return None, "invalid_options_count"

    return {
        "kind": kind,
        "group": group,
        "title": title,
        "options": options,
    }, None


def build_sharek_xlsx(results: Mapping[str, Any]) -> bytes:
    """يبني ملف Excel (RTL) لنتائج حملة شارك (سواء كانت استفتاءً أو استقبال مشاركات)."""
    camp = results.get("campaign") or {}
    kind = camp.get("kind") or "poll"
    grp = camp.get("group") or "ALL"

    if kind == "poll":
        headers = ["الرقم الأكاديمي", "الاسم", "المجموعة النظرية", "المجموعة العملية", "الخيار المختار", "الحالة"]
        table_rows: list[list[Any]] = [headers]
        for v in results.get("votes") or []:
            opted = v.get("option_label")
            table_rows.append([
                v.get("no", ""),
                v.get("name", ""),
                v.get("theory", ""),
                v.get("practical", ""),
                opted if opted is not None else "—",
                "شارك" if opted is not None else "لم يشارك",
            ])
        sheet_title = f"استفتاء {camp.get('id', '')} - {grp}"
    else:
        headers = ["الرقم الأكاديمي", "الاسم", "المجموعة النظرية", "المجموعة العملية", "نوع المشاركة", "المحتوى / اسم الملف", "أنهى المشاركة؟"]
        table_rows = [headers]
        kind_ar = {"text": "نص", "photo": "صورة", "document": "ملف", "video": "فيديو", "audio": "صوت", "voice": "تسجيل"}
        for s in results.get("submissions") or []:
            table_rows.append([
                s.get("no", ""),
                s.get("name", ""),
                s.get("theory", ""),
                s.get("practical", ""),
                kind_ar.get(s.get("item_kind") or "", "ملف"),
                s.get("text_content") or s.get("file_name") or s.get("file_id") or "",
                "نعم" if s.get("finished") else "لا",
            ])
        sheet_title = f"مشاركات {camp.get('id', '')} - {grp}"

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except Exception:
        return _build_xlsx_minimal(sheet_title, table_rows)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title[:31]
    ws.sheet_view.rightToLeft = True

    header_fill = PatternFill("solid", fgColor="103A2B")
    header_font = Font(bold=True, color="F2FAF4", size=11)
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")

    for r_idx, row in enumerate(table_rows, start=1):
        ws.append(row)
        for c_idx in range(1, len(row) + 1):
            cell = ws.cell(row=r_idx, column=c_idx)
            if r_idx == 1:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center
            else:
                cell.alignment = right if c_idx in (2, 5, 6) else center

    for col_letter, width in [("A", 16), ("B", 28), ("C", 14), ("D", 14), ("E", 24), ("F", 24), ("G", 14)]:
        ws.column_dimensions[col_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

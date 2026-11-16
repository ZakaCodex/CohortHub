"""منطق الحضور (المحاضرات النظرية فقط، 12 محاضرة لكل مادة):
التحقق من الجلسات، مفاتيح أجهزة المسح (Open Ticket Scanner)، تصحيح الحضور، وتصدير Excel (م1–م12)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import zipfile
from typing import Any, Mapping
from xml.sax.saxutils import escape as xml_escape

from core.text import normalize_academic_no, normalize_theory
from core import cohort

MAX_LECTURES = 12


def generate_device_token() -> tuple[str, str, str]:
    """يولّد مفتاح جهاز مسح يظهر مرة واحدة فقط: (المفتاح الخام، بصمة SHA-256، البادئة للعرض)."""
    raw = "ots_" + secrets.token_urlsafe(24)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, digest, raw[:10]


def hash_device_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").strip().encode("utf-8")).hexdigest()


def extract_scanned_academic_no(raw: object) -> str | None:
    """يستخرج الرقم الأكاديمي من نص الباركود/QR الممسوح (يدعم النص المباشر أو JSON أو روابط استعلام)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    direct = normalize_academic_no(text)
    if direct:
        return direct
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for k in ("academic_no", "student_university_id", "university_id", "code", "barcode", "ticket", "id", "text"):
                    if k in obj:
                        cand = normalize_academic_no(str(obj[k]))
                        if cand:
                            return cand
        except ValueError:
            pass
    m = re.search(r"\b(\d{4,15})\b", text)
    return normalize_academic_no(m.group(1)) if m else None


def validate_session_open(meta: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        subject_id = int(meta.get("subject_id"))
    except (TypeError, ValueError):
        return None, "invalid_subject"
    group = normalize_theory(meta.get("group"))
    if not group:
        return None, "invalid_group"
    try:
        lecture_no = int(meta.get("lecture_no"))
    except (TypeError, ValueError):
        return None, "invalid_lecture"
    if not (1 <= lecture_no <= MAX_LECTURES):
        return None, "invalid_lecture"
    delegate_absent = bool(meta.get("delegate_absent", False))
    return {
        "subject_id": subject_id,
        "group": group,
        "lecture_no": lecture_no,
        "delegate_absent": delegate_absent,
    }, None


def validate_correction(meta: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    try:
        subject_id = int(meta.get("subject_id"))
    except (TypeError, ValueError):
        return None, "invalid_subject"
    group = normalize_theory(meta.get("group"))
    if not group:
        return None, "invalid_group"
    try:
        lecture_no = int(meta.get("lecture_no"))
    except (TypeError, ValueError):
        return None, "invalid_lecture"
    if not (1 <= lecture_no <= MAX_LECTURES):
        return None, "invalid_lecture"
    student_no = normalize_academic_no(str(meta.get("student_no") or ""))
    if not student_no:
        return None, "invalid_academic_no"
    status = str(meta.get("status") or "").strip()
    if status not in ("present", "absent"):
        return None, "invalid_status"
    reason = re.sub(r"\s+", " ", str(meta.get("reason") or "")).strip()
    if len(reason) < 2 or len(reason) > 300:
        return None, "reason_required"
    return {
        "subject_id": subject_id,
        "group": group,
        "lecture_no": lecture_no,
        "student_no": student_no,
        "status": status,
        "reason": reason,
    }, None


def _build_xlsx_minimal(sheet_title: str, rows: list[list[Any]]) -> bytes:
    """مولّد XLSX قياسي خفيف بدون مكتبات خارجية في حال عدم توفر openpyxl على الخادم."""
    def col_letter(idx: int) -> str:
        res = ""
        n = idx + 1
        while n > 0:
            n, rem = divmod(n - 1, 26)
            res = chr(65 + rem) + res
        return res

    sheet_rows: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{col_letter(c_idx)}{r_idx}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                txt = xml_escape(str(val if val is not None else ""))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{txt}</t></is></c>')
        sheet_rows.append(f'<row r="{r_idx}">{"".join(cells)}</row>')

    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView rightToLeft="1" workbookViewId="0"/></sheetViews>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{xml_escape(sheet_title[:31])}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    wb_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", wb_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
    return buf.getvalue()


def build_attendance_xlsx(matrix: Mapping[str, Any]) -> bytes:
    """يبني ملف Excel (RTL) لحضور المادة (الرقم الأكاديمي، الاسم، م1–م12، الحضور، الغياب، النسبة)."""
    subject_name = (matrix.get("subject") or {}).get("name") or "المادة"
    group = matrix.get("group") or cohort.get().theory_ids()[0]
    headers = ["الرقم الأكاديمي", "الاسم", *[f"م{i}" for i in range(1, MAX_LECTURES + 1)], "الحضور", "الغياب", "النسبة %"]
    table_rows: list[list[Any]] = [headers]

    for st in matrix.get("students") or []:
        lecs = list(st.get("lectures") or [])
        lecs = (lecs + [None] * MAX_LECTURES)[:MAX_LECTURES]
        present = sum(1 for v in lecs if v == 1)
        absent = sum(1 for v in lecs if v == 0)
        held = present + absent
        pct = round((present * 100.0) / held, 1) if held else 0.0
        cells = [st.get("academic_no", ""), st.get("full_name", "")]
        for v in lecs:
            cells.append("" if v is None else int(v))
        cells.extend([present, absent, pct])
        table_rows.append(cells)

    sheet_title = f"{subject_name[:18]} - {group}"
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
                cell.alignment = right if c_idx == 2 else center

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 30
    for c_idx in range(3, 15):
        col = ws.cell(row=1, column=c_idx).column_letter
        ws.column_dimensions[col].width = 6
    for c_idx in range(15, 18):
        col = ws.cell(row=1, column=c_idx).column_letter
        ws.column_dimensions[col].width = 11

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

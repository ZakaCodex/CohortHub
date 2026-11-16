"""منطق شاشة الدكتور:
تصدير ملف Excel لواجبات الطلاب لكل مجموعة منفردة (1 للذي سلّم، 0 للذي لم يسلّم)."""
from __future__ import annotations

import io
from typing import Any, Mapping

from core.attendance import _build_xlsx_minimal


def build_doctor_assignment_xlsx(data: Mapping[str, Any]) -> bytes:
    """يبني ملف Excel (RTL) لواجب مجموعة منفردة: (1 سلّم، 0 لم يسلّم)."""
    asg = data.get("assignment") or {}
    subj = asg.get("subject") or "المادة"
    grp = asg.get("group") or "المجموعة"
    num = asg.get("number") or 1

    headers = ["الرقم الأكاديمي", "اسم الطالب", "المجموعة", "التسليم (1=سلّم، 0=لم يسلّم)", "ملاحظة"]
    table_rows: list[list[Any]] = [headers]

    for st in data.get("students") or []:
        submitted = 1 if int(st.get("submitted") or 0) == 1 else 0
        note = ""
        if submitted:
            note = "سُلّم متأخرًا" if st.get("is_late") else "سُلّم في الموعد"
            if st.get("received"):
                note += " · مستلَم"
        table_rows.append([
            st.get("academic_no", ""),
            st.get("full_name", ""),
            grp,
            submitted,
            note,
        ])

    sheet_title = f"واجب {num} - {grp}"
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
                cell.alignment = right if c_idx in (2, 5) else center

    for col_letter, width in [("A", 16), ("B", 30), ("C", 14), ("D", 24), ("E", 20)]:
        ws.column_dimensions[col_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _group_headers(assignments: list[Mapping[str, Any]]) -> list[str]:
    """عنوان عمود لكل واجب: «واجب N»، ويُضاف اسم المادة فقط إذا اختلفت المواد داخل المجموعة."""
    multi = len({a.get("subject") for a in assignments}) > 1
    out: list[str] = []
    for a in assignments:
        label = f"واجب {a.get('number', '')}"
        out.append(f"{a.get('subject')} - {label}" if multi and a.get("subject") else label)
    return out


def build_doctor_group_xlsx(data: Mapping[str, Any]) -> bytes:
    """ملف Excel (RTL) لمجموعة واحدة: الرقم الأكاديمي | اسم الطالب | واجب 1 | واجب 2 | ...
    القيمة 1 إذا سلّم الطالب الواجب و0 إذا لم يسلّم."""
    grp = str(data.get("group") or "المجموعة")
    asgs = list(data.get("assignments") or [])
    n = len(asgs)

    table_rows: list[list[Any]] = [["الرقم الأكاديمي", "اسم الطالب", *_group_headers(asgs)]]
    for st in data.get("students") or []:
        marks = [1 if int(m or 0) == 1 else 0 for m in (st.get("marks") or [])][:n]
        marks += [0] * (n - len(marks))
        table_rows.append([st.get("academic_no", ""), st.get("full_name", ""), *marks])

    sheet_title = grp[:31]
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return _build_xlsx_minimal(sheet_title, table_rows)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
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
                cell.fill, cell.font, cell.alignment = header_fill, header_font, center
            else:
                cell.alignment = right if c_idx == 2 else center

    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 30
    for c_idx in range(3, 3 + n):
        ws.column_dimensions[get_column_letter(c_idx)].width = 16
    ws.freeze_panes = "C2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

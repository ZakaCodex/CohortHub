"""منطق اختبارات الاختيار من متعدد (MCQ):
- استيراد الأسئلة من ملف Excel بالأعمدة المعتمدة:
  السؤال | الخيار 1 | الخيار 2 | الخيار 3 | الخيار 4 | الإجابة الصحيحة
- يقبل في عمود «الإجابة الصحيحة» إما نص الخيار الصحيح نفسه أو رقم الخيار (1–4).
- بناء قالب Excel جاهز للتعبئة."""
from __future__ import annotations

import io
import re
from typing import Any, Mapping

from core.attendance import _build_xlsx_minimal

MCQ_EXCEL_COLUMNS = ["السؤال", "الخيار 1", "الخيار 2", "الخيار 3", "الخيار 4", "الإجابة الصحيحة"]
MAX_QUESTIONS = 500
EMPTY_ROWS_STOP = 20


def parse_mcq_excel(xlsx_bytes: bytes) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """يقرأ ملف Excel ويعيد (قائمة الأسئلة النظيفة، قائمة الأخطاء)."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    except Exception:
        return None, ["تعذّرت قراءة ملف Excel. تأكد من حفظه بصيغة .xlsx صالحة."]

    try:
        it = wb.active.iter_rows(values_only=True)
        header_row = next(it, None)
        if not header_row:
            return None, ["الملف فارغ."]
        header = [re.sub(r"\s+", " ", str(h).strip()) if h is not None else "" for h in header_row[:6]]
        if header != MCQ_EXCEL_COLUMNS:
            return None, [
                "أعمدة الملف غير مطابقة. يجب أن تكون بالترتيب التالي:\n" + " | ".join(MCQ_EXCEL_COLUMNS)
            ]

        questions: list[dict[str, Any]] = []
        errors: list[str] = []
        empty_streak = 0

        for row_no, row in enumerate(it, start=2):
            if not row or all(c is None or str(c).strip() == "" for c in row):
                empty_streak += 1
                if empty_streak >= EMPTY_ROWS_STOP:
                    break
                continue
            empty_streak = 0
            cells = (list(row) + [None] * 6)[:6]
            q_raw, o1, o2, o3, o4, ans_raw = [
                re.sub(r"\s+", " ", str(c).strip()) if c is not None else "" for c in cells
            ]
            if not q_raw or not o1 or not o2 or not o3 or not o4:
                errors.append(f"السطر {row_no}: السؤال أو أحد الخيارات الأربعة فارغ.")
                continue
            options = [o1, o2, o3, o4]
            correct_idx: int | None = None
            if ans_raw in options:
                correct_idx = options.index(ans_raw)
            elif ans_raw in ("1", "2", "3", "4", "١", "٢", "٣", "٤"):
                idx_map = {"1": 0, "2": 1, "3": 2, "4": 3, "١": 0, "٢": 1, "٣": 2, "٤": 3}
                correct_idx = idx_map[ans_raw]
            elif ans_raw in ("الخيار 1", "الخيار 2", "الخيار 3", "الخيار 4"):
                correct_idx = int(ans_raw[-1]) - 1

            if correct_idx is None:
                errors.append(f"السطر {row_no}: «الإجابة الصحيحة» لا تطابق أيًّا من الخيارات الأربعة.")
                continue

            questions.append({
                "question": q_raw[:500],
                "options": [opt[:200] for opt in options],
                "correct_index": correct_idx,
            })
            if len(questions) >= MAX_QUESTIONS:
                break

        if errors:
            return None, errors[:15]
        if not questions:
            return None, ["لم يُعثر على أي أسئلة صالحة في الملف."]
        return questions, []
    finally:
        wb.close()


def build_mcq_template_xlsx() -> bytes:
    """يبني ملف قالب Excel جاهز لرفع أسئلة MCQ."""
    rows = [
        MCQ_EXCEL_COLUMNS,
        ["ما البروتوكول المسؤول عن توجيه الحزم في طبقة الشبكة؟", "IP", "TCP", "HTTP", "DNS", "IP"],
        ["أي أمر في SQL يُستخدم لاسترجاع البيانات من جدول؟", "INSERT", "SELECT", "UPDATE", "DELETE", "SELECT"],
    ]
    return _build_xlsx_minimal("قالب_اختبار_MCQ", rows)

"""منطق الملفات: التحقق من البيانات، الهاشتاجات، صيغة رسالة المجتمع، وعنوان الملف للطالب."""
from __future__ import annotations

import re
from typing import Any, Mapping

PARTS = {"theory": "نظري", "practical": "عملي"}
TYPES = {
    "lecture": "محاضرة",
    "summary": "ملخص",
    "exam": "نموذج",
    "exercises": "تمارين",
    "translation": "ترجمة",
    "reference": "مرجع",
    "benefit": "فائدة",
}
TYPES_PLURAL = {
    "lecture": "محاضرات",
    "summary": "ملخصات",
    "exam": "نماذج",
    "exercises": "تمارين",
    "translation": "ترجمات",
    "reference": "مراجع",
    "benefit": "فوائد",
}
EXAMS = {"midterm": "نصفي", "final": "نهائي"}
# اسم الوسم في الرسالة، اسم السطر
LABELS = {
    "lecture": ("المحاضرة", "رقم المحاضرة"),
    "lecture_en": ("Lecture", "رقم الـ Lecture"),
    "lab": ("Lab", "رقم الـ Lab"),
    "module": ("Module", "رقم الـ Module"),
    "chapter": ("Chapter", "رقم الـ Chapter"),
}
NEEDS_NUMBER = {"summary", "exercises", "translation"}
MAX_LECTURES, MAX_OTHER = 12, 20
MAX_DESCRIPTION = 300


def hashtag(text: str) -> str:
    """'د. أحمد المنصوري' → 'د_أحمد_المنصوري' (الوسوم لا تقبل مسافات ولا نقاطًا)."""
    return re.sub(r"[^\w]+", "_", (text or "").strip(), flags=re.UNICODE).strip("_")


def validate_file_meta(meta: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """يعيد (بيانات نظيفة, None) أو (None, رمز الخطأ)."""
    try:
        subject_id = int(meta.get("subject_id"))
    except (TypeError, ValueError):
        return None, "invalid_subject"
    part, ctype = meta.get("part"), meta.get("content_type")
    if part not in PARTS:
        return None, "invalid_part"
    if ctype not in TYPES:
        return None, "invalid_type"

    exam_kind = meta.get("exam_kind") or None
    if ctype == "exam":
        if exam_kind not in EXAMS:
            return None, "exam_kind_required"
    else:
        exam_kind = None

    raw_numbers = meta.get("numbers") or []
    if not isinstance(raw_numbers, (list, tuple)):
        return None, "invalid_numbers"
    try:
        numbers = sorted({int(n) for n in raw_numbers})
    except (TypeError, ValueError):
        return None, "invalid_numbers"
    label = meta.get("label_kind") or None
    if numbers:
        if label not in LABELS:
            return None, "label_required"
        limit = MAX_LECTURES if label in ("lecture", "lecture_en") else MAX_OTHER
        if numbers[0] < 1 or numbers[-1] > limit:
            return None, "number_out_of_range"
    else:
        label = None
        if ctype in NEEDS_NUMBER:
            return None, "number_required"

    desc = re.sub(r"[ \t]+", " ", str(meta.get("description") or "")).strip()
    if len(desc) > MAX_DESCRIPTION:
        return None, "description_too_long"

    clean: dict[str, Any] = {"subject_id": subject_id, "part": part, "content_type": ctype, "numbers": numbers}
    if exam_kind:
        clean["exam_kind"] = exam_kind
    if label:
        clean["label_kind"] = label
    if desc:
        clean["description"] = desc
    if meta.get("doctor_id") not in (None, ""):
        try:
            doc_id = int(meta["doctor_id"])
            if doc_id < 1:
                return None, "invalid_doctor"
            clean["doctor_id"] = doc_id
        except (TypeError, ValueError):
            return None, "invalid_doctor"
    if meta.get("replaces_id"):
        try:
            clean["replaces_id"] = int(meta["replaces_id"])
        except (TypeError, ValueError):
            return None, "invalid_replaces"
    return clean, None


def build_caption(meta: Mapping[str, Any], subject_tag: str, doctor_name: str | None) -> str:
    """رسالة المجتمع: وسوم المادة والدكتور ورقم المحاضرة/الفصل ونوع المحتوى (+ نصفي/نهائي) والوصف."""
    lines = [
        f"🔹 نـوع المحتوى : #{hashtag(TYPES[meta['content_type']])}",
        f"🔹 اسـم المقرر : #{hashtag(subject_tag)}",
    ]
    numbers = meta.get("numbers") or []
    if numbers and meta.get("label_kind") in LABELS:
        tag, title = LABELS[meta["label_kind"]]
        lines.append(f"🔹 {title}: " + " ".join(f"#{tag}_{n}" for n in numbers))
    if doctor_name:
        lines.append(f"🔹 مُدرس المقرر : #{hashtag(doctor_name)}")
    lines.append(f"🔹 نوع المحاضرة : #{PARTS[meta['part']]}")
    if meta.get("exam_kind"):
        lines.append(f"🔹 نوع النموذج : #{EXAMS[meta['exam_kind']]}")
    caption = "\n".join(lines)
    if meta.get("description"):
        caption += "\n\n" + meta["description"]
    return caption[:1024]


def file_title(f: Mapping[str, Any]) -> str:
    """ما يراه الطالب: ما حُدِّد عند الرفع فقط (النوع، الرقم، نصفي/نهائي) دون تسمية حرة."""
    title = TYPES.get(f.get("content_type"), "")
    if f.get("exam_kind") in EXAMS:
        title += " " + EXAMS[f["exam_kind"]]
    numbers = f.get("numbers") or []
    if numbers and f.get("label_kind") in LABELS:
        tag = LABELS[f["label_kind"]][0]
        title += f" · {tag} " + "، ".join(str(n) for n in numbers)
    return title.strip()

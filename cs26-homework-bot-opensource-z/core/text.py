"""تنظيف مدخلات المستخدم."""
from __future__ import annotations

import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\ufeff]")


def normalize_digits(text: str) -> str:
    return text.translate(_DIGITS)


def normalize_academic_no(text: str) -> str | None:
    """يقبل أرقامًا عربية/هندية ومسافات، ويعيد الرقم أو None إن لم يكن صالحًا (4–15 رقمًا)."""
    cleaned = _ZERO_WIDTH.sub("", normalize_digits(text or "")).strip()
    cleaned = re.sub(r"[\s\-_]", "", cleaned)
    return cleaned if re.fullmatch(r"\d{4,15}", cleaned) else None


def clean_name(text: str, max_len: int = 40) -> str | None:
    cleaned = re.sub(r"\s+", " ", _ZERO_WIDTH.sub("", text or "")).strip()
    if len(cleaned) < 2 or len(cleaned) > max_len:
        return None
    if re.search(r"[<>]", cleaned):
        return None
    return cleaned


def normalize_theory(value: object) -> str | None:
    """'1' | 'G1' | 'cs2_g1' | 'CS2_G1' → 'CS2_G1' (حسب إعداد الدفعة في cohort.json)."""
    from core import cohort
    return cohort.get().normalize_theory(normalize_digits(str(value or "")).strip().upper().replace(" ", ""))


def normalize_practical(value: object) -> str | None:
    """'2' | 'G2' → 'G2' (حسب إعداد الدفعة في cohort.json)."""
    from core import cohort
    return cohort.get().normalize_practical(normalize_digits(str(value or "")).strip().upper().replace(" ", ""))


TG_MESSAGE_LIMIT = 4096
TG_CAPTION_LIMIT = 1024


def split_text(text: str, limit: int = 4000) -> list[str]:
    """يقسّم نصًا طويلًا إلى أجزاء ≤ limit عند حدود الأسطر (ثم المسافات) كي لا تتجاوز حدّ تيليجرام (4096)."""
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:  # سطر واحد أطول من الحد: نقطعه عند آخر مسافة
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            head, line = line[:cut], line[cut:].lstrip(" ")
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if not cur:
            cur = line
        elif len(cur) + 1 + len(line) <= limit:
            cur += "\n" + line
        else:
            chunks.append(cur)
            cur = line
    if cur:
        chunks.append(cur)
    return chunks


def fit_caption(caption: str | None, limit: int = TG_CAPTION_LIMIT - 24) -> str | None:
    """حدّ تعليق الوسائط 1024 حرفًا؛ نقصّ عند آخر سطر كامل كي لا ينكسر وسم HTML."""
    if caption is None or len(caption) <= limit:
        return caption
    cut = caption.rfind("\n", 0, limit)
    cut = cut if cut > 0 else limit
    return caption[:cut].rstrip() + "\n…"

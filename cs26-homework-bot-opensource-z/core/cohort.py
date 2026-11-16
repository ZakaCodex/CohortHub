"""هوية الدفعة وتوزيع مجموعاتها — مصدر واحد للحقيقة بدل الأسماء الثابتة في الكود.

لتشغيل المشروع لدفعة أخرى عدّل `cohort.json` في جذر المشروع (أو مرّر JSON كاملًا في المتغير COHORT_CONFIG_JSON)
ثم ولّد بيانات المجموعات لقاعدة البيانات بـ `python scripts/setup_cohort.py`. القيم الافتراضية أدناه هي دفعة CS26
الحالية تمامًا، فغياب الملف لا يغيّر سلوك النسخة العاملة.

الاصطلاح: المجموعات النظرية  <prefix>1 … <prefix>N  (مثل CS2_G1)،
والعملية G1 … Gk مرقّمة تسلسليًا عبر كل المجموعات النظرية (G1-G3 للأولى، G4-G6 للثانية…).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "specialization": "علوم الحاسوب",
    "batch": "26",
    "level": "",
    "theory_prefix": "CS2_G",
    "practical_per_theory": [3, 3, 3],
}

MAX_THEORY, MAX_PRACTICAL = 12, 60


@dataclass(frozen=True)
class Cohort:
    specialization: str
    batch: str
    level: str
    theory_prefix: str
    practical_per_theory: tuple[int, ...]

    # ---- العرض ----
    @property
    def title(self) -> str:          # «دفعة علوم الحاسوب 26»
        return f"دفعة {self.specialization} {self.batch}".strip()

    @property
    def student_noun(self) -> str:   # «طالب علوم الحاسوب دفعة 26»
        return f"طالب {self.specialization} دفعة {self.batch}".strip()

    @property
    def students_noun(self) -> str:  # «طلاب علوم الحاسوب دفعة 26»
        return f"طلاب {self.specialization} دفعة {self.batch}".strip()

    @property
    def level_line(self) -> str:
        return self.level

    # ---- المجموعات ----
    def theory_ids(self) -> list[str]:
        return [f"{self.theory_prefix}{i}" for i in range(1, len(self.practical_per_theory) + 1)]

    def practical_map(self) -> list[tuple[str, str]]:
        """[(G1, CS2_G1), (G2, CS2_G1), …] بالترتيب."""
        out: list[tuple[str, str]] = []
        n = 0
        for t_idx, count in enumerate(self.practical_per_theory, start=1):
            for _ in range(count):
                n += 1
                out.append((f"G{n}", f"{self.theory_prefix}{t_idx}"))
        return out

    def practical_ids(self) -> list[str]:
        return [code for code, _ in self.practical_map()]

    def practical_of(self, theory_id: str) -> list[str]:
        return [code for code, t in self.practical_map() if t == theory_id]

    def is_theory(self, value: object) -> bool:
        return str(value or "") in self.theory_ids()

    def is_practical(self, value: object) -> bool:
        return str(value or "") in self.practical_ids()

    def normalize_theory(self, digits_text: str) -> str | None:
        """'1' | 'G1' | 'cs2_g1' | 'CS2_G1' → 'CS2_G1' (ضمن عدد المجموعات النظرية)."""
        base = self.theory_prefix[:-1] if self.theory_prefix.upper().endswith("G") else self.theory_prefix
        base = base.rstrip("_")
        m = re.fullmatch(rf"(?:{re.escape(base.upper())}_?)?G?(\d{{1,2}})", digits_text)
        if not m:
            return None
        n = int(m.group(1))
        return f"{self.theory_prefix}{n}" if 1 <= n <= len(self.practical_per_theory) else None

    def normalize_practical(self, digits_text: str) -> str | None:
        m = re.fullmatch(r"G?(\d{1,2})", digits_text)
        if not m:
            return None
        n = int(m.group(1))
        return f"G{n}" if 1 <= n <= sum(self.practical_per_theory) else None

    def public(self) -> dict[str, Any]:
        """ما يحتاجه التطبيق المصغّر لعرض هوية الدفعة (لا أسرار)."""
        return {
            "title": self.title, "student_noun": self.student_noun,
            "specialization": self.specialization, "batch": self.batch, "level": self.level,
            "theory": self.theory_ids(),
            "practical": [{"id": c, "theory": t} for c, t in self.practical_map()],
        }


def parse_config(raw: dict[str, Any]) -> Cohort:
    """يتحقق من الإعداد ويكمله بالقيم الافتراضية. يرفع RuntimeError برسالة واضحة عند الخطأ."""
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS}}
    spec = re.sub(r"\s+", " ", str(cfg["specialization"] or "")).strip()
    batch = re.sub(r"\s+", " ", str(cfg["batch"] or "")).strip()
    level = re.sub(r"\s+", " ", str(cfg["level"] or "")).strip()
    prefix = str(cfg["theory_prefix"] or "").strip().upper()
    per = cfg["practical_per_theory"]
    if not spec or len(spec) > 60:
        raise RuntimeError("cohort.json: «specialization» مطلوب (حتى 60 حرفًا)")
    if len(batch) > 20 or len(level) > 40:
        raise RuntimeError("cohort.json: «batch» أو «level» أطول من المسموح")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,15}", prefix):
        raise RuntimeError("cohort.json: «theory_prefix» أحرف إنجليزية كبيرة/أرقام/_ فقط، مثل CS2_G")
    if (not isinstance(per, list) or not per or len(per) > MAX_THEORY
            or any((not isinstance(x, int)) or isinstance(x, bool) or x < 1 for x in per)):
        raise RuntimeError("cohort.json: «practical_per_theory» قائمة أعداد صحيحة موجبة، عنصر لكل مجموعة نظرية (مثل [3,3,3])")
    if sum(per) > MAX_PRACTICAL:
        raise RuntimeError(f"cohort.json: مجموع المجموعات العملية يتجاوز {MAX_PRACTICAL}")
    return Cohort(spec, batch, level, prefix, tuple(per))


def load_cohort() -> Cohort:
    raw_json = os.getenv("COHORT_CONFIG_JSON", "").strip()
    if len(raw_json) >= 2 and raw_json[0] == raw_json[-1] and raw_json[0] in ("'", '"'):
        raw_json = raw_json[1:-1].strip()
    if raw_json:
        try:
            return parse_config(json.loads(raw_json))
        except ValueError as exc:
            raise RuntimeError(f"COHORT_CONFIG_JSON ليس JSON صالحًا: {exc}") from exc
    path = Path(os.getenv("COHORT_CONFIG", "") or _ROOT / "cohort.json")
    if path.is_file():
        try:
            return parse_config(json.loads(path.read_text("utf-8")))
        except ValueError as exc:
            raise RuntimeError(f"{path.name} ليس JSON صالحًا: {exc}") from exc
    return parse_config({})


_current: Cohort | None = None


def get() -> Cohort:
    global _current
    if _current is None:
        _current = load_cohort()
    return _current


def set_for_tests(c: Cohort | None) -> None:
    global _current
    _current = c

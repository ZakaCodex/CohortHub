"""منطق التظلمات: التحقق من فتح دورة تظلمات، التحقق من سبب التظلم، وتجميع التظلمات بصيغة موحدة للمندوب."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Mapping

from core.assignments import parse_ts
from core.text import normalize_practical, normalize_theory
from core.timeutil import format_datetime_ar, parse_sanaa

PART_AR = {"theory": "نظري", "practical": "عملي"}


def validate_cycle_open(meta: Mapping[str, Any], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """يتحقق من بيانات فتح دورة التظلمات (المادة، نظري/عملي، المجموعة/الدفعة، الموعد بتوقيت صنعاء)."""
    try:
        subject_id = int(meta.get("subject_id"))
    except (TypeError, ValueError):
        return None, "invalid_subject"

    part = str(meta.get("part") or "").strip()
    if part not in ("theory", "practical"):
        return None, "invalid_part"

    raw_group = str(meta.get("theory_group") or meta.get("group") or "").strip()
    if raw_group in ("", "all", "ALL"):
        theory_group = None
    else:
        theory_group = normalize_theory(raw_group)
        if not theory_group:
            return None, "invalid_group"

    raw_pg = meta.get("practical_groups") or []
    practical_groups: list[str] = []
    if part == "practical" and isinstance(raw_pg, (list, tuple)):
        for g in raw_pg:
            code = normalize_practical(g)
            if not code:
                return None, "invalid_practical_group"
            if code not in practical_groups:
                practical_groups.append(code)

    try:
        closes_at = parse_sanaa(str(meta.get("closes_local") or meta.get("due_local") or ""))
    except ValueError:
        return None, "invalid_due"
    if closes_at < now + timedelta(minutes=5):
        return None, "due_in_past"
    if closes_at > now + timedelta(days=180):
        return None, "due_too_far"

    title = re.sub(r"\s+", " ", str(meta.get("title") or "")).strip()
    if len(title) > 160:
        return None, "title_too_long"

    return {
        "subject_id": subject_id,
        "part": part,
        "theory_group": theory_group,
        "practical_groups": practical_groups,
        "closes_at": closes_at.isoformat(),
        "title": title or None,
    }, None


def validate_grievance_reason(reason: object) -> tuple[str | None, str | None]:
    cleaned = re.sub(r"\s+", " ", str(reason or "")).strip()
    if len(cleaned) < 3:
        return None, "reason_too_short"
    if len(cleaned) > 1000:
        return None, "reason_too_long"
    return cleaned, None


def format_compiled_grievances(pkg: Mapping[str, Any]) -> str:
    """يجمع التظلمات بالصيغة المعتمدة: الطالب، الرقم الأكاديمي، المجموعة، سبب التظلم."""
    subj = pkg.get("subject") or ""
    part = PART_AR.get(pkg.get("part") or "", "")
    grp = pkg.get("theory_group_id") or "الدفعة كاملة"
    title = f" ({pkg['title']})" if pkg.get("title") else ""
    items = pkg.get("items") or []

    lines = [
        f"📋 كشف تظلمات: {subj} — {part}{title}",
        f"👥 النطاق: {grp} · العدد: {len(items)}",
    ]
    if pkg.get("closes_at"):
        dt = parse_ts(pkg["closes_at"])
        if dt:
            lines.append(f"⏰ أُغلق: {format_datetime_ar(dt)}")
    lines.append("─────────────")

    if not items:
        lines.append("لا توجد تظلمات مسجّلة في هذه الدورة.")
        return "\n".join(lines)

    for idx, it in enumerate(items, start=1):
        lines.append(
            f"{idx}) الطالب: {it.get('name', '')}\n"
            f"   الرقم الأكاديمي: {it.get('no', '')}\n"
            f"   المجموعة: {it.get('group', '')}\n"
            f"   سبب التظلم: {it.get('reason', '')}"
        )
        if idx < len(items):
            lines.append("─────────────")
    return "\n".join(lines)

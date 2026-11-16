"""منطق التقارير اليومية والقوالب والتقرير الأسبوعي الآلي.
- يمنع اجتماع محاضرة نظرية وعملية لنفس المجموعة في نفس الفترة.
- يمنع النقطة الفارغة في مخرجات التقرير.
- يجمّع التقرير الأسبوعي برقم الأسبوع، ويكتب الأيام المكتوبة فقط (لا أيام فارغة)."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from core.text import normalize_practical, normalize_theory
from core.timeutil import MONTHS_AR, WEEKDAYS_AR

# أيام الدراسة الرسمية: السبت (5) إلى الخميس (3) — الخميس يوم جامعي
STUDY_DAYS: list[tuple[int, str]] = [
    (5, "السبت"),
    (6, "الأحد"),
    (0, "الاثنين"),
    (1, "الثلاثاء"),
    (2, "الأربعاء"),
    (3, "الخميس"),
]
STUDY_WEEKDAY_SET = {w for w, _ in STUDY_DAYS}

LECTURE_ORDINALS = [
    "الأولى",
    "الثانية",
    "الثالثة",
    "الرابعة",
    "الخامسة",
    "السادسة",
]

SEPARATOR = "─────────────"


def format_report_date_ar(d: date) -> str:
    """الثلاثاء 8 سبتمبر"""
    return f"{WEEKDAYS_AR[d.weekday()]} {d.day} {MONTHS_AR[d.month - 1]}"


def study_week_start_date(d: date) -> date:
    """يعيد تاريخ يوم السبت لبداية الأسبوع الدراسي الذي يقع فيه التاريخ d."""
    offset = (d.weekday() - 5) % 7
    return d - timedelta(days=offset)


def clean_bullets(text: object) -> list[str]:
    """ينظف أسطر «ما تم أخذه» ويحذف أي نقطة فارغة («• » بلا نص)."""
    if not text:
        return []
    lines: list[str] = []
    for raw in str(text).splitlines():
        cleaned = re.sub(r"^[\s•\-\*▪■]+", "", raw).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned:
            lines.append(cleaned)
    return lines


def assignment_lines(items: list[str]) -> list[str]:
    """التكليف قد يكون عدة نقاط/أسئلة: سطر واحد يبقى «• التكليف: …»، وأكثر من سطر يُعرض كنقاط فرعية."""
    if not items:
        return []
    if len(items) == 1:
        return [f"• التكليف: {items[0]}"]
    return ["• التكليف:"] + [f"   ◦ {it}" for it in items]


def validate_template_slots(slots: object) -> tuple[list[dict[str, Any]] | None, str | None]:
    """يتحقق من خانات القالب: كل خانة من نوع واحد فقط (theory | practical_all | practical_split)."""
    if not isinstance(slots, list):
        return None, "invalid_slots"
    if len(slots) > len(LECTURE_ORDINALS):
        return None, "too_many_slots"

    clean_slots: list[dict[str, Any]] = []
    for idx, raw in enumerate(slots, start=1):
        if not isinstance(raw, Mapping):
            return None, "invalid_slot"
        mode = str(raw.get("mode") or "").strip()
        if mode not in ("theory", "practical_all", "practical_split"):
            return None, "invalid_slot_mode"

        if mode in ("theory", "practical_all"):
            try:
                subject_id = int(raw.get("subject_id"))
            except (TypeError, ValueError):
                return None, "invalid_subject"
            clean_slots.append({
                "slot": idx,
                "mode": mode,
                "subject_id": subject_id,
                "subject": str(raw.get("subject") or "").strip(),
                "doctor": str(raw.get("doctor") or "").strip(),
            })
        else:
            raw_items = raw.get("items") or []
            if not isinstance(raw_items, list) or not raw_items:
                return None, "empty_split_items"
            seen_groups: set[str] = set()
            items: list[dict[str, Any]] = []
            for it in raw_items:
                if not isinstance(it, Mapping):
                    return None, "invalid_split_item"
                grp = normalize_practical(it.get("group"))
                if not grp or grp in seen_groups:
                    return None, "invalid_practical_group"
                seen_groups.add(grp)
                try:
                    sid = int(it.get("subject_id"))
                except (TypeError, ValueError):
                    return None, "invalid_subject"
                items.append({
                    "group": grp,
                    "subject_id": sid,
                    "subject": str(it.get("subject") or "").strip(),
                    "doctor": str(it.get("doctor") or "").strip(),
                })
            clean_slots.append({"slot": idx, "mode": "practical_split", "items": items})
    return clean_slots, None


def validate_and_render_daily_report(
    group_raw: object,
    date_str: object,
    slots_raw: object,
    note_raw: object = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """يتحقق من بيانات التقرير اليومي ويبني النص المعتمد مع منع النقاط الفارغة ومنع التقاء نظري وعملي لنفس المجموعة."""
    group = normalize_theory(group_raw)
    if not group:
        return None, "invalid_group"
    try:
        rdate = date.fromisoformat(str(date_str or "").strip()[:10])
    except ValueError:
        return None, "invalid_date"
    if not isinstance(slots_raw, list) or not slots_raw:
        return None, "empty_slots"
    if len(slots_raw) > len(LECTURE_ORDINALS):
        return None, "too_many_slots"

    blocks: list[str] = []
    clean_slots: list[dict[str, Any]] = []

    for idx, raw in enumerate(slots_raw):
        if not isinstance(raw, Mapping):
            return None, "invalid_slot"
        ordinal = LECTURE_ORDINALS[idx]
        mode = str(raw.get("mode") or "theory").strip()
        if mode not in ("theory", "practical_all", "practical_split"):
            return None, "invalid_slot_mode"

        if mode in ("theory", "practical_all"):
            subj = re.sub(r"\s+", " ", str(raw.get("subject") or "")).strip()
            doc = re.sub(r"\s+", " ", str(raw.get("doctor") or "")).strip()
            topics = clean_bullets(raw.get("topics") or raw.get("taken") or "")
            hw_items = clean_bullets(raw.get("assignment") or raw.get("homework") or "")
            if not subj or not topics:
                continue

            subj_title = f"{subj} _ عملي" if mode == "practical_all" else subj
            head = f"🕗 المحاضرة {ordinal} | {subj_title}" + (f" | {doc}" if doc else "")
            lines = [head]
            for t in topics:
                lines.append(f"• {t}")
            lines.extend(assignment_lines(hw_items))
            blocks.append("\n".join(lines))
            clean_slots.append({
                "slot": idx + 1,
                "mode": mode,
                "subject_id": raw.get("subject_id"),
                "subject": subj,
                "doctor": doc,
                "topics": "\n".join(topics),
                "assignment": "\n".join(hw_items) or None,
            })
        else:
            raw_items = raw.get("items") or []
            if not isinstance(raw_items, list):
                return None, "invalid_split_items"
            sub_blocks: list[str] = []
            clean_items: list[dict[str, Any]] = []
            seen_groups: set[str] = set()
            for it in raw_items:
                if not isinstance(it, Mapping):
                    continue
                pgrp = normalize_practical(it.get("group"))
                if not pgrp or pgrp in seen_groups:
                    return None, "invalid_practical_group"
                seen_groups.add(pgrp)
                subj = re.sub(r"\s+", " ", str(it.get("subject") or "")).strip()
                doc = re.sub(r"\s+", " ", str(it.get("doctor") or "")).strip()
                topics = clean_bullets(it.get("topics") or it.get("taken") or "")
                hw_items = clean_bullets(it.get("assignment") or it.get("homework") or "")
                if not subj or not topics:
                    continue
                item_lines = [f"■ {subj} | {pgrp} عملي :"]
                for t in topics:
                    item_lines.append(f"• {t}")
                item_lines.extend(assignment_lines(hw_items))
                sub_blocks.append("\n".join(item_lines))
                clean_items.append({
                    "group": pgrp,
                    "subject_id": it.get("subject_id"),
                    "subject": subj,
                    "doctor": doc,
                    "topics": "\n".join(topics),
                    "assignment": "\n".join(hw_items) or None,
                })
            if sub_blocks:
                blocks.append(f"🕗 المحاضرة {ordinal}:\n\n" + "\n\n\n".join(sub_blocks))
                clean_slots.append({"slot": idx + 1, "mode": "practical_split", "items": clean_items})

    if not blocks:
        return None, "empty_report_content"

    note_bullets = clean_bullets(note_raw)
    note_clean = "\n".join(note_bullets) if note_bullets else None

    out_parts = [f"📌 تقرير يوم {format_report_date_ar(rdate)} {group}", SEPARATOR]
    for b in blocks:
        out_parts.append(b)
        out_parts.append(SEPARATOR)

    if note_bullets:
        note_section = "📌 ملاحظة:\n" + "\n".join(f"• {n}" for n in note_bullets)
        out_parts.append(note_section)
    else:
        out_parts.pop()

    rendered = "\n\n".join(out_parts)
    return {
        "group": group,
        "report_date": rdate.isoformat(),
        "slots": clean_slots,
        "note": note_clean,
        "rendered_text": rendered,
    }, None


def weekly_report_number(week_start: date, first_report_date: date | None) -> int:
    """رقم الأسبوع الدراسي: الأسبوع الذي كُتب فيه أول تقرير هو الأسبوع 1 (للدفعة كلها)، ثم 2، 3 …"""
    if first_report_date is None:
        return 1
    first_week = study_week_start_date(first_report_date)
    return max(1, (week_start - first_week).days // 7 + 1)


def weekly_report_days(week_start: date, daily_reports: Sequence[Mapping[str, Any]]) -> int:
    """عدد أيام هذا الأسبوع (السبت–الخميس) التي كُتب لها تقرير فعلًا."""
    days = {(week_start + timedelta(days=i)).isoformat() for i in range(6)}
    return len({str(r.get("report_date") or "")[:10] for r in daily_reports
                if str(r.get("report_date") or "")[:10] in days and str(r.get("rendered_text") or "").strip()})


def compile_weekly_report(
    group: str,
    week_start: date,
    daily_reports: Sequence[Mapping[str, Any]],
    first_report_date: date | None = None,
) -> str:
    """يجمّع التقرير الأسبوعي لمجموعة نظرية: «الأسبوع N — المجموعة» ثم تقارير الأيام المكتوبة فقط
    (الأيام التي لم يُكتب فيها تقرير لا تظهر إطلاقًا)."""
    by_date: dict[str, str] = {}
    for r in daily_reports:
        d_str = str(r.get("report_date") or "")[:10]
        txt = str(r.get("rendered_text") or "").strip()
        if d_str and txt:
            by_date[d_str] = txt

    written = [by_date[iso] for iso in ((week_start + timedelta(days=i)).isoformat() for i in range(6)) if iso in by_date]
    header = f"📚 الأسبوع {weekly_report_number(week_start, first_report_date)} — {group}"
    sections = [header, SEPARATOR]
    for i, txt in enumerate(written):
        sections.append(txt)
        if i < len(written) - 1:
            sections.append("═════════════════════")

    return "\n\n".join(sections)

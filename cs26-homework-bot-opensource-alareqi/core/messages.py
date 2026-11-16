"""صياغة رسائل الإشعارات والتسليم والتظلمات والبلاغات وشارك والتقارير (HTML لتيليجرام).
تُستخدم في البوت وفي مركز الإشعارات داخل التطبيق."""
from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import Any, Mapping

from core.assignments import parse_ts
from core.grievances import format_compiled_grievances
from core.timeutil import format_datetime_ar

PART_AR = {"theory": "نظري", "practical": "عملي"}
TICKET_CAT_AR = {"bug": "بلاغ عطل", "complaint": "شكوى", "inquiry": "استفسار"}


def humanize_remaining(delta: timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{max(minutes, 1)} دقيقة"
    hours = round(minutes / 60)
    if hours == 1:
        return "ساعة"
    if hours == 2:
        return "ساعتان"
    if hours < 11:
        return f"{hours} ساعات"
    return f"{hours} ساعة"


def assignment_new(p: Mapping[str, Any]) -> str:
    lines = ["📢 <b>تم نشر واجب جديد</b>", "",
             f"📚 المادة: {escape(p['subject'])} ({PART_AR.get(p['part'], '')})",
             f"👥 المجموعة: {escape(p['group'])}",
             f"🔢 رقم الواجب: {p['number']}"]
    if p.get("description"):
        lines.append(f"📝 الوصف: {escape(p['description'])}")
    lines.append(f"⏰ آخر موعد: {format_datetime_ar(parse_ts(p['due_at']))}")
    if p.get("has_attachment"):
        lines.append("📎 مع مرفق")
    if p.get("allow_text"):
        lines.append("✍️ يقبل ملفات ونصوصًا وروابط")
    return "\n".join(lines)


def assignment_reminder(p: Mapping[str, Any], at: datetime) -> str:
    left = humanize_remaining(parse_ts(p["due_at"]) - at)
    return (f"⏰ <b>تذكير بواجب</b>\n\n"
            f"واجب {p['number']} — {escape(p['subject'])} ({PART_AR.get(p['part'], '')})\n"
            f"يتبقى نحو {left} على الموعد ({format_datetime_ar(parse_ts(p['due_at']))}) ولم تسلّمه بعد.")


def manual(p: Mapping[str, Any]) -> str:
    title = f"📣 <b>{escape(p['title'])}</b>\n\n" if p.get("title") else "📣 "
    return title + escape(p.get("body", ""))


def grievance_open(p: Mapping[str, Any]) -> str:
    lines = [
        "⚖️ <b>تم فتح دورة تظلمات</b>",
        "",
        f"📚 المادة: {escape(str(p.get('subject') or ''))} ({PART_AR.get(str(p.get('part') or ''), '')})",
        f"👥 المجموعة: {escape(str(p.get('group') or 'الدفعة'))}",
    ]
    if p.get("title"):
        lines.append(f"📌 البيان: {escape(str(p['title']))}")
    dt = parse_ts(p.get("closes_at"))
    if dt:
        lines.append(f"⏰ آخر موعد للتقديم والتعديل: {format_datetime_ar(dt)}")
    lines.append("\nيمكنك تقديم تظلمك أو تعديله قبل الإغلاق من «شؤوني ← تظلماتي».")
    return "\n".join(lines)


def grievance_compiled(p: Mapping[str, Any]) -> str:
    return escape(format_compiled_grievances(p))


def ticket_new(p: Mapping[str, Any]) -> str:
    cat = TICKET_CAT_AR.get(str(p.get("category") or ""), "بلاغ")
    sender = escape(str(p.get("sender_name") or "مستخدم"))
    extra = f" · <code>{escape(str(p['academic_no']))}</code>" if p.get("academic_no") else ""
    grp = f" ({escape(str(p['group']))})" if p.get("group") else ""
    return (
        f"📩 <b>{cat} جديد #{p.get('ticket_id', '')}</b>\n"
        f"👤 المرسل: {sender}{extra}{grp}\n\n"
        f"{escape(str(p.get('body') or ''))}"
    )


def ticket_reply(p: Mapping[str, Any]) -> str:
    cat = TICKET_CAT_AR.get(str(p.get("category") or ""), "بلاغك")
    return (
        f"💬 <b>رد المطوّر على {cat} #{p.get('ticket_id', '')}</b>\n\n"
        f"{escape(str(p.get('reply') or ''))}"
    )


def barcode_caption(p: Mapping[str, Any]) -> str:
    return (
        f"🪪 <b>باركود البطاقة الجامعية (Code 128)</b>\n"
        f"👤 الطالب: {escape(str(p.get('full_name') or ''))}\n"
        f"🆔 الرقم الأكاديمي: <code>{escape(str(p.get('academic_no') or ''))}</code>\n"
        f"👥 المجموعة: {escape(str(p.get('theory_group_id') or ''))} · عملي {escape(str(p.get('practical_group_id') or ''))}"
    )


def sharek_new(p: Mapping[str, Any]) -> str:
    is_sub = p.get("kind") == "submission"
    head = "📥 <b>حملة مشاركة جديدة في «شارك»</b>" if is_sub else "📊 <b>استفتاء جديد في «شارك»</b>"
    grp = escape(str(p.get("group") or "الجميع"))
    body = escape(str(p.get("title") or ""))
    footer = "\n\nاكتب مشاركتك من التطبيق (شاشة «شارك»)، وأرسل الصور والملفات عبر البوت." if is_sub else "\n\nاختر إجابتك (اختيار واحد نهائي):"
    return f"{head}\n👥 النطاق: {grp}\n\n{body}{footer}"


def report_reminder(p: Mapping[str, Any]) -> str:
    grp = escape(str(p.get("group") or ""))
    d = escape(str(p.get("date") or ""))
    return (
        f"📝 <b>تذكير مسائي بالتقرير اليومي</b>\n\n"
        f"لم يُحفظ التقرير اليومي للمجموعة <b>{grp}</b> لتاريخ {d} حتى الآن.\n"
        "يرجى تعبئة التقرير من التطبيق («التقرير اليومي») ليدخل في الحصاد الأسبوعي."
    )


def render_notification(kind: str, payload: Mapping[str, Any], at: datetime) -> tuple[str, str]:
    """(عنوان قصير، نص) لمركز الإشعارات داخل التطبيق — نص عادي بلا HTML."""
    if kind == "assignment_new":
        body = f"{payload['subject']} · واجب {payload['number']} · آخر موعد {format_datetime_ar(parse_ts(payload['due_at']))}"
        return "واجب جديد", body
    if kind == "assignment_reminder":
        left = humanize_remaining(parse_ts(payload["due_at"]) - at)
        return "تذكير بواجب", f"{payload['subject']} · واجب {payload['number']} · يتبقى نحو {left}"
    if kind == "manual":
        return payload.get("title") or "إعلان", payload.get("body", "")
    if kind == "grievance_open":
        dt = parse_ts(payload.get("closes_at"))
        when = f" · حتى {format_datetime_ar(dt)}" if dt else ""
        return "فتح دورة تظلمات", f"{payload.get('subject', '')} ({PART_AR.get(payload.get('part', ''), '')}){when}"
    if kind == "grievance_compiled":
        items = payload.get("items") or []
        return "تجميع التظلمات جاهز", f"{payload.get('subject', '')} · عدد التظلمات: {len(items)}"
    if kind == "ticket_reply":
        cat = TICKET_CAT_AR.get(str(payload.get("category") or ""), "بلاغك")
        return f"رد على {cat}", str(payload.get("reply") or "")
    if kind == "ticket_new":
        cat = TICKET_CAT_AR.get(str(payload.get("category") or ""), "بلاغ")
        return f"{cat} جديد #{payload.get('ticket_id', '')}", f"{payload.get('sender_name', '')}: {payload.get('body', '')[:80]}"
    if kind == "sharek_new":
        lbl = "مشاركة جديدة في شارك" if payload.get("kind") == "submission" else "استفتاء جديد في شارك"
        return lbl, str(payload.get("title") or "")[:120]
    if kind == "report_reminder":
        return "تذكير بالتقرير اليومي", f"لم يُكتب تقرير اليوم للمجموعة {payload.get('group', '')}"
    return "إشعار", ""


def submission_header(pkg: Mapping[str, Any]) -> str:
    st, a = pkg["student"], pkg["assignment"]
    late = " — متأخر" if pkg.get("is_late") else ""
    redo = " (تسليم معدَّل)" if pkg.get("revision", 1) > 1 else ""
    group = a["grp"] if a["part"] == "theory" else f"{st['practical']} عملي"
    return (f"📥 <b>تسليم واجب {a['number']}{late}</b>{redo}\n"
            f"👤 الطالب: {escape(st['name'])}\n"
            f"🆔 الرقم الأكاديمي: <code>{escape(st['no'])}</code>\n"
            f"👥 المجموعة: {escape(group)}\n"
            f"📚 المادة: {escape(a['subject'])}\n"
            f"🔢 رقم الواجب: {a['number']}\n"
            f"📎 عدد الملفات: {len(pkg['files'])}")

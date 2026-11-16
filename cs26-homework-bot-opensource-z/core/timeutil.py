"""كل الأوقات بتوقيت صنعاء (UTC+3 ثابت، بلا توقيت صيفي). نخزّن UTC ونعرض صنعاء."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

SANAA = timezone(timedelta(hours=3), "Asia/Aden")
UTC = timezone.utc

WEEKDAYS_AR = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]  # Monday=0
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
# أيام الدراسة: السبت إلى الخميس (الخميس يوم جامعي)
STUDY_WEEKDAYS = {5, 6, 0, 1, 2, 3}


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_sanaa(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("يجب أن يحمل الوقت منطقة زمنية")
    return dt.astimezone(SANAA)


def sanaa_local_to_utc(d: date, t: time) -> datetime:
    """يحوّل (تاريخ، وقت) أدخلهما المستخدم بتوقيت صنعاء إلى UTC للتخزين."""
    return datetime.combine(d, t, tzinfo=SANAA).astimezone(UTC)


def parse_sanaa(value: str) -> datetime:
    """'2026-10-21 17:30' أو '2026-10-21T17:30' بتوقيت صنعاء → UTC."""
    value = value.strip().replace("T", " ")
    dt = datetime.strptime(value, "%Y-%m-%d %H:%M")
    return dt.replace(tzinfo=SANAA).astimezone(UTC)


def format_time_ar(dt: datetime) -> str:
    """8:00 م — صيغة 12 ساعة بتوقيت صنعاء."""
    local = to_sanaa(dt)
    hour12 = local.hour % 12 or 12
    suffix = "ص" if local.hour < 12 else "م"
    return f"{hour12}:{local.minute:02d} {suffix}"


def format_date_ar(dt: datetime, with_weekday: bool = True) -> str:
    """الثلاثاء 8 سبتمبر"""
    local = to_sanaa(dt)
    base = f"{local.day} {MONTHS_AR[local.month - 1]}"
    return f"{WEEKDAYS_AR[local.weekday()]} {base}" if with_weekday else base


def format_datetime_ar(dt: datetime) -> str:
    return f"{format_date_ar(dt)} · {format_time_ar(dt)}"


def is_study_day(dt: datetime) -> bool:
    return to_sanaa(dt).weekday() in STUDY_WEEKDAYS

"""نصوص البوت (عربي فقط). قصيرة عمدًا، وكلها تُرسل كرسائل جديدة."""
from html import escape
from core import cohort

LOCKED_MSG = f"هذه الميزة خاصة ب{cohort.get().student_noun}"


def welcome_new(name: str) -> str:
    return (f"أهلًا {escape(name)} 👋\n"
            f"هذا بوت {cohort.get().title}: الملفات والواجبات والحضور وكل ما يخص الدفعة.\n\n"
            "كيف تريد الدخول؟")


ASK_ACADEMIC_NO = "أرسل رقمك الأكاديمي (أرقام فقط)."
ASK_GUEST_NAME = "اكتب اسمك لندخلك كزائر (من حرفين إلى 40 حرفًا)."
BAD_ACADEMIC_NO = "الرقم غير صالح. أرسل رقمك الأكاديمي بالأرقام فقط."
BAD_GUEST_NAME = "الاسم غير صالح. اكتب اسمًا من حرفين إلى 40 حرفًا بلا رموز."
NOT_FOUND = "هذا الرقم غير موجود في قائمة الدفعة. تأكد منه وأرسله مرة أخرى."
LINK_CANCELLED = "تمام. أرسل رقمك الأكاديمي الصحيح."
BANNED = "تعذّر الدخول إلى هذا الحساب."
RATE_LIMITED = "محاولات كثيرة. انتظر عشر دقائق ثم حاول مرة أخرى."
GENERIC_ERROR = "تعذّر تنفيذ الطلب الآن. حاول بعد قليل."
UNKNOWN_TEXT = "لم أفهم رسالتك. افتح التطبيق من الزر أدناه."


def taken(contact: str | None) -> str:
    base = "هذا الرقم مرتبط بحساب آخر."
    return base + (f"\nإن كان رقمك فعلًا، تواصل مع المطوّر: {escape(contact)}" if contact
                   else "\nإن كان رقمك فعلًا، تواصل مع المطوّر.")


def confirm_link(full_name: str, theory: str, practical: str) -> str:
    return ("هل هذه بياناتك؟\n\n"
            f"👤 <b>{escape(full_name)}</b>\n"
            f"المجموعة النظرية: <b>{escape(theory)}</b>\n"
            f"المجموعة العملية: <b>{escape(practical)}</b>")


def linked_ok(full_name: str) -> str:
    return f"تم الربط ✅\nأهلًا {escape(full_name)}"


def already_linked() -> str:
    return "حسابك مربوط بالفعل."


def guest_ok(name: str) -> str:
    return (f"أهلًا {escape(name)} 👋\n"
            "كزائر تستطيع تصفّح الملفات والاختبارات واستخدام المساعد الذكي (5 رسائل يوميًا). "
            "باقي الميزات ظاهرة لكنها مقفلة.")


def menu(name: str, kind: str, theory: str | None, practical: str | None, roles: list[str]) -> str:
    role_ar = {"delegate": "مندوب", "deputy": "نائب مندوب", "committee": "لجنة علمية", "developer": "مطوّر"}
    tags = [role_ar[r] for r in roles if r in role_ar]
    lines = [f"أهلًا {escape(name)}"]
    if kind == "student" and theory:
        lines.append(f"المجموعة: {escape(theory)} · العملية {escape(practical or '')}")
    elif kind == "guest":
        lines.append("دخول كزائر")
    elif kind == "doctor":
        lines.append("حساب دكتور")
    if tags:
        lines.append("الصفة: " + "، ".join(tags))
    return "\n".join(lines)

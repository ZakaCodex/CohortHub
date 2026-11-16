"""المساعد الذكي والبحث الذكي في الملفات ونظام البلاغات والدعم:
1) البحث الذكي في الملفات (منطق Gemini من المشروع السابق مع الفهم المحلي الاحتياطي بالكلمات المفتاحية).
2) المساعد الذكي للرد من دليل البوت ومن بيانات الطالب نفسه (واجباته، حضوره، تظلماته، مجموعته).
3) التحقق من بلاغات الدعم (عطل / شكوى / استفسار) وردود المطوّر."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx

from core.assignments import STATUS_AR, parse_ts, student_status
from core.files import file_title
from core.timeutil import UTC, format_datetime_ar
from core import cohort

log = logging.getLogger("assistant")

TICKET_CATEGORIES = {
    "bug": "بلاغ عطل",
    "complaint": "شكوى",
    "inquiry": "استفسار",
}

# ------------------------------------------------------------------
# تطبيع النصوص العربية والإنجليزية ومطابقة الكلمات المفتاحية (من search_utils.py)
# ------------------------------------------------------------------
_TASHKEEL = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
_SEPARATORS = re.compile(r"[_\-\.\(\)\[\]\{\}/\\,;:!?\"'«»،؛؟+=*&^%$#@~`|<>]+")
_SPACES = re.compile(r"\s+")
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

_ORDINALS = {
    "اول": "1", "اولي": "1", "first": "1",
    "ثاني": "2", "ثانيه": "2", "second": "2",
    "ثالث": "3", "ثالثه": "3", "third": "3",
    "رابع": "4", "رابعه": "4", "fourth": "4",
    "خامس": "5", "خامسه": "5", "fifth": "5",
    "سادس": "6", "سادسه": "6", "sixth": "6",
    "سابع": "7", "سابعه": "7", "seventh": "7",
    "ثامن": "8", "ثامنه": "8", "eighth": "8",
    "تاسع": "9", "تاسعه": "9", "ninth": "9",
    "عاشر": "10", "عاشره": "10", "tenth": "10",
    "حادي": "11", "ثاني عشر": "12",
}

SUBJECT_ALIASES: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("جبر", "خطي", "linear", "algebra"), ("جبر", "خطي")),
    (("قواعد", "بيانات", "database", "databases", "db", "sql"), ("قواعد", "بيانات")),
    (("برمجه", "هدفيه", "شيئيه", "كائنيه", "oop", "object"), ("برمجه", "هدفيه")),
    (("شبكات", "شبكه", "network", "networks"), ("شبكات",)),
    (("معماريه", "تنظيم", "architecture", "organization"), ("معماريه",)),
    (("مهارات", "تواصل", "اتصال", "communication"), ("تواصل",)),
    (("اساسيات", "مقدمه", "intro", "introduction"), ("اساسيات",)),
    (("ويب", "web"), ("ويب",)),
    (("ثقافه", "culture", "اسلاميه"), ("ثقافه",)),
    (("امن", "امنيه", "معلومات", "security", "cyber"), ("امن", "امنيه")),
]

CONTENT_TYPE_HINTS: dict[str, tuple[str, ...]] = {
    "exam": ("نموذج", "نماذج", "اختبار", "اختبارات", "امتحان", "امتحانات", "سنوات", "سابقه", "exam", "exams", "midterm", "final", "past"),
    "summary": ("ملخص", "ملخصات", "تلخيص", "summary", "summaries"),
    "exercises": ("تمرين", "تمارين", "تدريب", "اسئله", "شيت", "sheet", "exercises", "exercise"),
    "translation": ("ترجمه", "ترجمات", "مترجم", "translation", "translated"),
    "reference": ("مرجع", "مراجع", "كتاب", "كتب", "مصدر", "مصادر", "reference", "references", "book"),
    "benefit": ("فائده", "فائدة", "فوائد", "معلومه", "نصيحه", "تلميحه", "benefit", "benefits", "tip", "tips"),
    "lecture": ("محاضره", "محاضرات", "شابتر", "تشابتر", "موديول", "لاب", "lecture", "lectures", "lec", "chapter", "module", "lab"),
}

STOPWORDS = {
    "اريد", "ابي", "ابغي", "ابغا", "اطلب", "هات", "هاتلي", "وين", "فين", "هل", "يوجد", "توجد",
    "عندكم", "عندك", "لدي", "لديكم", "لو", "سمحت", "من", "في", "على", "عن", "الى", "ملف", "ملفات",
    "ممكن", "اعطني", "ارسل", "ارسلي", "لي", "ماده", "ما", "هو", "هي", "و", "او", "كل", "جميع",
    "الخاص", "الخاصه", "خاص", "دكتور", "د", "استاذ", "ان", "بحث", "ابحث",
    "نظري", "نظريه", "عملي", "عمليه", "theory", "practical", "lab", "theoretical",
    "نصفي", "نهائي", "مد", "ميد", "فاينل", "midterm", "final",
    "محاضره", "محاضرات", "ملخص", "ملخصات", "نموذج", "نماذج", "تمارين", "تمرين", "ترجمه", "ترجمات", "مرجع", "مراجع", "فائده", "فوائد",
    "chapter", "module", "lecture", "شابتر", "تشابتر", "موديول", "فصل", "رقم",
}


_HINT_WORDS: set[str] = set()   # كلمات أنواع المحتوى (تُملأ بعد تعريف _strip_al)


def normalize_text(text: object) -> str:
    if not text:
        return ""
    s = str(text).lower().translate(_ARABIC_INDIC)
    s = _TASHKEEL.sub("", s)
    s = re.sub("[أإآٱ]", "ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = _SEPARATORS.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def _strip_al(token: str) -> str:
    for p in ("بال", "وال", "كال", "فال", "لل", "ال"):
        if token.startswith(p) and len(token) - len(p) >= 2:
            return token[len(p):]
    return token


_HINT_WORDS |= {_strip_al(normalize_text(h)) for hints in CONTENT_TYPE_HINTS.values() for h in hints}


def match_tokens(text: object) -> list[str]:
    tokens: list[str] = []
    for tok in normalize_text(text).split():
        stripped = _strip_al(tok)
        tokens.append(_ORDINALS.get(stripped, stripped))
    return tokens


def match_form(text: object) -> str:
    return " ".join(match_tokens(text))


def _match_subject_id(query_tokens: list[str], query_form: str, subjects: Sequence[Mapping[str, Any]]) -> int | None:
    if not subjects:
        return None
    best_id: int | None = None
    best_score = 0
    qset = set(query_tokens)

    for s in subjects:
        sid = int(s["id"])
        name_tokens = match_tokens(s.get("name", ""))
        tag_tokens = match_tokens(str(s.get("tag", "")).replace("_", " "))
        cand_tokens = set(name_tokens) | set(tag_tokens)

        # إضافة المرادفات المعروفة للمادة
        for triggers, targets in SUBJECT_ALIASES:
            if any(t in cand_tokens for t in targets):
                cand_tokens |= set(triggers)

        score = 0
        name_form = " ".join(name_tokens)
        if name_form and name_form in query_form:
            score += 10
        overlap = qset & cand_tokens
        score += len(overlap) * 4
        if score > best_score:
            best_score = score
            best_id = sid

    return best_id if best_score >= 4 else None


def interpret_file_query_local(query: str, subjects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """يفهم طلب البحث عن ملف محلياً بالكلمات المفتاحية (سريع، ويعمل كاحتياطي دائم لـ Gemini)."""
    tokens = match_tokens(query)
    form = " ".join(tokens)
    tset = set(tokens)

    subject_id = _match_subject_id(tokens, form, subjects)

    # الجزء: نظري / عملي
    part: str | None = None
    if tset & {"نظري", "نظريه", "theory", "theoretical"}:
        part = "theory"
    elif tset & {"عملي", "عمليه", "practical", "lab", "لاب"}:
        part = "practical"

    # نوع النموذج: نصفي / نهائي
    exam_kind: str | None = None
    if tset & {"نصفي", "مد", "ميد", "midterm", "mid"}:
        exam_kind = "midterm"
    elif tset & {"نهائي", "فاينل", "final"}:
        exam_kind = "final"

    # نوع المحتوى
    content_type: str | None = None
    for ctype in ("exam", "summary", "exercises", "translation", "reference", "benefit", "lecture"):
        hints = {_strip_al(normalize_text(h)) for h in CONTENT_TYPE_HINTS[ctype]}
        if tset & hints:
            content_type = ctype
            break
    if exam_kind and not content_type:
        content_type = "exam"

    # الأرقام المطلوبة (1..20)
    numbers = sorted({int(t) for t in tokens if t.isdigit() and 1 <= int(t) <= 20})

    # الكلمات المفتاحية المتبقية للبحث في الوصف واسم الملف والدكتور
    subj_words: set[str] = set()
    if subject_id is not None:
        for s in subjects:
            if int(s["id"]) == subject_id:
                subj_words |= set(match_tokens(s.get("name", ""))) | set(match_tokens(s.get("tag", "")))
        for triggers, _ in SUBJECT_ALIASES:
            if subj_words & set(triggers):
                subj_words |= set(triggers)

    stop = {_strip_al(normalize_text(w)) for w in STOPWORDS} | _HINT_WORDS | subj_words
    keywords = [t for t in tokens if not t.isdigit() and len(t) >= 2 and t not in stop][:6]

    return {
        "subject_id": subject_id,
        "part": part,
        "content_type": content_type,
        "exam_kind": exam_kind,
        "numbers": numbers,
        "keywords": keywords,
    }


def score_file_row(row: Mapping[str, Any], criteria: Mapping[str, Any]) -> int:
    """يحسب درجة تطابق ملف مع معايير البحث."""
    score = 1
    if criteria.get("subject_id") and row.get("subject_id") == criteria["subject_id"]:
        score += 10
    if criteria.get("part") and row.get("part") == criteria["part"]:
        score += 5
    if criteria.get("content_type") and row.get("content_type") == criteria["content_type"]:
        score += 6
    if criteria.get("exam_kind") and row.get("exam_kind") == criteria["exam_kind"]:
        score += 5

    req_nums = set(criteria.get("numbers") or [])
    row_nums = set(row.get("numbers") or [])
    if req_nums:
        overlap = req_nums & row_nums
        if overlap:
            score += 8 * len(overlap)
        else:
            score -= 3

    score += keyword_hits(row, criteria) * 4
    return score


def keyword_hits(row: Mapping[str, Any], criteria: Mapping[str, Any]) -> int:
    """عدد الكلمات المفتاحية المتبقية من الطلب التي وُجدت في وصف الملف/اسمه/الدكتور/عنوانه."""
    kws = criteria.get("keywords") or []
    if not kws:
        return 0
    haystack = " ".join([
        str(row.get("description") or ""),
        str(row.get("file_name") or ""),
        str(row.get("doctor_name") or ""),
        file_title(row),
    ])
    htoks = set(match_tokens(haystack))
    hform = " ".join(htoks)
    return sum(1 for kw in kws if kw in htoks or (len(kw) >= 3 and kw in hform))


def rank_files(rows: Sequence[Mapping[str, Any]], criteria: Mapping[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    scored: list[tuple[int, dict[str, Any]]] = []
    kws = criteria.get("keywords") or []
    has_filter = bool(criteria.get("subject_id") or criteria.get("content_type") or criteria.get("numbers"))
    for r in rows:
        item = dict(r)
        item["title"] = file_title(item)
        sc = score_file_row(item, criteria)
        item["score"] = sc
        item["kw_hits"] = keyword_hits(item, criteria)
        if not has_filter and kws and sc <= 1:
            continue
        scored.append((sc, item))
    scored.sort(key=lambda x: (x[0], x[1].get("id", 0)), reverse=True)
    return [it for _, it in scored[:limit]]


# ------------------------------------------------------------------
# عميل Gemini الاختياري عبر HTTP (بلا مكتبات ثقيلة، مع تدوير المفاتيح والاحتياطي الفوري)
# ------------------------------------------------------------------
_GEMINI_MODELS = ("gemini-2.5-flash", "gemini-2.0-flash")
_key_idx = 0
_key_cooldown: dict[str, float] = {}


def _gemini_keys() -> list[str]:
    raw = os.getenv("GEMINI_API_KEYS", "") or os.getenv("GEMINI_API_KEY", "")
    return [k.strip() for k in re.split(r"[,;\s]+", raw) if k.strip()]


async def _call_gemini_json(system_prompt: str, user_prompt: str, timeout: float = 4.0, max_tokens: int = 600) -> dict[str, Any] | None:
    global _key_idx
    keys = _gemini_keys()
    if not keys:
        return None
    now = time.monotonic()
    for model in _GEMINI_MODELS:
        for step in range(len(keys)):
            idx = (_key_idx + step) % len(keys)
            key = keys[idx]
            if now < _key_cooldown.get(key, 0.0):
                continue
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            payload: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
            }
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code == 429:
                    _key_cooldown[key] = time.monotonic() + 60.0
                    _key_idx = (idx + 1) % len(keys)
                    continue
                if resp.status_code in (401, 403):
                    _key_cooldown[key] = time.monotonic() + 600.0
                    _key_idx = (idx + 1) % len(keys)
                    continue
                if resp.status_code >= 400:
                    break
                data = resp.json()
                text = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
                s_idx, e_idx = cleaned.find("{"), cleaned.rfind("}")
                if s_idx != -1 and e_idx > s_idx:
                    _key_idx = idx
                    return json.loads(cleaned[s_idx:e_idx + 1])
            except Exception as exc:  # noqa: BLE001
                log.debug("gemini fallback (%s): %s", model, exc)
                break
    return None


# ------------------------------------------------------------------
# كاش ردود Gemini: ذاكرة العملية + جدول ai_cache الدائم (مشترك بين Vercel والبوت وكل الطلاب)
# السؤال المكرر (بعد التطبيع) لا يستهلك أي توكن.
# ------------------------------------------------------------------
_mem_cache: dict[str, tuple[float, Any]] = {}
_MEM_TTL, _MEM_MAX = 6 * 3600.0, 500


def cache_key(kind: str, text: str, salt: str = "") -> str:
    return f"{kind}:" + hashlib.sha1(f"{match_form(text)}|{salt}".encode("utf-8")).hexdigest()[:32]


def _mem_put(key: str, value: Any) -> None:
    if len(_mem_cache) >= _MEM_MAX:
        _mem_cache.pop(next(iter(_mem_cache)), None)
    _mem_cache[key] = (time.monotonic(), value)


async def cache_get(store: Any, key: str) -> Any:
    hit = _mem_cache.get(key)
    if hit and time.monotonic() - hit[0] < _MEM_TTL:
        return hit[1]
    if store is not None:
        try:
            value = await store.rpc("ai_cache_get", {"p_key": key})
        except Exception as exc:  # noqa: BLE001
            log.debug("ai_cache_get: %s", exc)
            value = None
        if value is not None:
            _mem_put(key, value)
            return value
    return None


async def cache_put(store: Any, key: str, kind: str, value: Any) -> None:
    _mem_put(key, value)
    if store is not None:
        try:
            await store.rpc("ai_cache_put", {"p_key": key, "p_kind": kind, "p_value": value})
        except Exception as exc:  # noqa: BLE001
            log.debug("ai_cache_put: %s", exc)


async def interpret_file_query_ai(query: str, subjects: Sequence[Mapping[str, Any]], local: dict[str, Any], store: Any = None) -> dict[str, Any]:
    """الفهم عبر Gemini (مع كاش دائم)؛ عند أي فشل يعود الفهم المحلي."""
    subj_lines = "\n".join(f"- {s['id']}: {s['name']} ({s.get('tag', '')})" for s in subjects)
    key = cache_key("fq", query, hashlib.sha1(subj_lines.encode("utf-8")).hexdigest()[:8])
    ai_raw = await cache_get(store, key)
    if not isinstance(ai_raw, dict):
        sys_prompt = (
            f"أنت مساعد بحث ملفات دراسية لبوت {cohort.get().title}.\n"
            "حوّل طلب الطالب إلى JSON بالمفاتيح التالية فقط:\n"
            '{"subject_id": number|null, "part": "theory"|"practical"|null, '
            '"content_type": "lecture"|"summary"|"exam"|"exercises"|"translation"|"reference"|"benefit"|null, '
            '"exam_kind": "midterm"|"final"|null, "numbers": [numbers 1..20], "keywords": [strings]}\n'
            f"قائمة المواد المتاحة:\n{subj_lines}"
        )
        ai_raw = await _call_gemini_json(sys_prompt, f"طلب الطالب: {query}", timeout=3.5, max_tokens=200)
        if not isinstance(ai_raw, dict):
            return local
        await cache_put(store, key, "file_query", ai_raw)

    valid_ids = {int(s["id"]) for s in subjects}
    sid = ai_raw.get("subject_id")
    try:
        sid = int(sid) if sid is not None and int(sid) in valid_ids else local["subject_id"]
    except (TypeError, ValueError):
        sid = local["subject_id"]

    part = ai_raw.get("part") if ai_raw.get("part") in ("theory", "practical") else local["part"]
    ctype = ai_raw.get("content_type") if ai_raw.get("content_type") in CONTENT_TYPE_HINTS else local["content_type"]
    ekind = ai_raw.get("exam_kind") if ai_raw.get("exam_kind") in ("midterm", "final") else local["exam_kind"]
    nums = local["numbers"]
    if isinstance(ai_raw.get("numbers"), list):
        try:
            parsed_nums = sorted({int(x) for x in ai_raw["numbers"] if 1 <= int(x) <= 20})
            if parsed_nums:
                nums = parsed_nums
        except (TypeError, ValueError):
            pass
    kws = local["keywords"]
    if isinstance(ai_raw.get("keywords"), list):
        ai_kws = [str(k).strip() for k in ai_raw["keywords"] if isinstance(k, str) and str(k).strip()]
        if ai_kws:
            kws = [t for k in ai_kws for t in match_tokens(k)][:6]   # نفس تطبيع الفهرس كي تطابق الكلمات
    return {
        "subject_id": sid,
        "part": part,
        "content_type": ctype,
        "exam_kind": ekind,
        "numbers": nums,
        "keywords": kws,
    }


KEYWORD_CANDIDATE_LIMIT = 400   # الافتراضي في SQL = 60 فقط؛ نرفعه عند البحث بكلمات كي لا تُهمَل الملفات الأقدم


def search_params(tg_id: int, c: Mapping[str, Any], with_limit: bool = True) -> dict[str, Any]:
    params = {
        "p_tg": tg_id,
        "p_subject": c.get("subject_id"),
        "p_part": c.get("part"),
        "p_type": c.get("content_type"),
        "p_exam_kind": c.get("exam_kind"),
        "p_numbers": c.get("numbers") or None,
    }
    if with_limit and c.get("keywords"):
        params["p_limit"] = KEYWORD_CANDIDATE_LIMIT
    return params


_search_params = search_params


def results_confident(ranked: Sequence[Mapping[str, Any]], crit: Mapping[str, Any]) -> bool:
    """هل نتائج الكلمات المفتاحية كافية دون Gemini؟"""
    if not ranked:
        return False
    top = ranked[0]
    structural = bool(crit.get("part") or crit.get("content_type") or crit.get("exam_kind") or crit.get("numbers"))
    if crit.get("subject_id"):
        if structural or not crit.get("keywords"):
            return True
        return int(top.get("kw_hits") or 0) > 0
    return int(top.get("kw_hits") or 0) > 0 or (structural and int(top.get("score") or 0) >= 6)


async def smart_search_files(rpc: Any, tg_id: int, query: str, subjects: Sequence[Mapping[str, Any]],
                             limit: int = 12, store: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    """بحث الملفات: الكلمات المفتاحية أولًا (بلا توكنات)، ثم Gemini فقط إذا لم تكفِ النتائج.
    يعيد (النتائج، المعايير، هل_استُخدم_Gemini)."""
    async def run(crit: Mapping[str, Any]) -> list[dict[str, Any]]:
        params = search_params(tg_id, crit)
        try:
            cands = await rpc("search_files_smart", params)
        except Exception:   # noqa: BLE001 — قاعدة لم تُطبَّق عليها 0008 بعد: نعيد بالسلوك القديم (60 ملفًا)
            if "p_limit" not in params:
                raise
            cands = await rpc("search_files_smart", search_params(tg_id, crit, with_limit=False))
        if not cands and crit.get("subject_id"):   # لا ملف بهذا الرقم/النوع: نوسّع لكل ملفات المادة
            cands = await rpc("search_files_smart", {"p_tg": tg_id, "p_subject": crit["subject_id"]})
        return rank_files(cands or [], crit, limit=limit)

    local = interpret_file_query_local(query, subjects)
    ranked = await run(local)
    if results_confident(ranked, local) or not _gemini_keys():
        return ranked, local, False

    ai_crit = await interpret_file_query_ai(query, subjects, local, store)
    if ai_crit == local:
        return ranked, local, False
    ai_ranked = await run(ai_crit)
    if ai_ranked and (not ranked or int(ai_ranked[0].get("score") or 0) >= int(ranked[0].get("score") or 0)):
        return ai_ranked, ai_crit, True
    return ranked, local, True


# ------------------------------------------------------------------
# دليل البوت المعتمد والرد الذكي من الدليل + بيانات الطالب نفسه
# ------------------------------------------------------------------
BOT_GUIDE = """
الدليل العملي الشامل للطالب — منظومة {cohort_title} (البوت + التطبيق المصغّر Mini App):

1. نظرة عامة والتكامل بين البوت والـ Mini App:
   - تعمل المنظومة عبر واجهتين متكاملتين ترتبطان بقاعدة بيانات موحّدة لحظياً:
     • محادثة البوت (Telegram Bot): تختص باستقبال وإرسال الملفات الفعلية، وعرض الواجبات المفتوحة غير المسلَّمة فقط لمجموعتك، واستقبال ملفات التسليم أو التعديل، وخوض اختبارات الـ MCQ الكلاسيكية عبر استفتاءات تيليجرام (Quiz Polls)، واستلام صورة باركود البطاقة الجامعية (Code 128)، واستقبال ملفات وصور المشاركات في «شارك».
     • التطبيق المصغّر (Mini App): يوفر شاشات تفاعلية متكاملة للفلترة الفورية (0ms)، وتصفّح كامل الواجبات (الكل / مفتوحة / مسلّمة / فائتة) وتعديل إرسال الملف واسترجاع ملفاتك السابقة، ومتابعة الحضور النظري (12 محاضرة)، وتقديم وتعديل التظلمات، والتصويت في استفتاءات «شارك»، ومحادثة المساعد الذكي والدعم الفني بملء الشاشة الكاملة دون ظهور شريط التنقل السفلي.
   - قسم «يهمّني الآن» في الواجهة الرئيسية للتطبيق يعرض لك الواجبات المفتوحة غير المسلّمة، ودورات التظلمات المفتوحة، وحملات «شارك» المفتوحة فقط.

2. الدخول لأول مرة وربط الرقم الأكاديمي أو الدخول كزائر:
   - بدء الاستخدام: أرسل الأمر `/start` في محادثة البوت أو اضغط زر «فتح التطبيق الشامل (Mini App)».
   - اختيار صفة الدخول: اضغط «أنا طالب في الدفعة» لربط حسابك الأكاديمي، أو اختر «دخول كزائر» لتصفّح المحتوى العام.
   - التحقق والتأكيد: أدخل رقمك الأكاديمي بالأرقام الإنجليزية أو العربية (مثال: 20260017)؛ يعرض لك النظام بطاقة تأكيد تتضمن اسمك الكامل ومجموعتك النظرية ({theory_groups}) ومجموعتك العملية ({practical_range}). اضغط «نعم، هذه بياناتي» لإتمام الربط.
   - حماية الحساب من الانتحال: لا يقبل النظام ربط رقم أكاديمي مرتبط بحساب تيليجرام آخر؛ وفي حال ظهور رسالة بأن الرقم مرتبط مسبقاً أو استنفاد محاولات التحقق، تواصل مع المطوّر عبر تذاكر الدعم لفك الارتباط القديم.
   - ترقية حساب الزائر إلى طالب: يستطيع الزائر في أي وقت تحويل حسابه إلى طالب بالضغط على زر «أنا طالب: ربط حسابي».

3. نظام الواجبات الدراسية (التصفح، التسليم، التعديل، والاسترجاع):
   - أولاً: تسليم الواجبات غير المسلَّمة من داخل البوت مباشرة:
     • عند إرسال `/start` والضغط على «الواجبات» في البوت، يعرض لك البوت فوراً الواجبات المفتوحة التي لم تسلّمها بعد والخاصة بمجموعتك النظرية والعملية فقط بصيغة: (اسم المادة - نظري/عملي).
     • خطوات إرسال الحل: اضغط على اسم الواجب لعرض تفاصيله وآخر موعد للتسليم بتوقيت صنعاء وزر «المرفق»، ثم اضغط «ابدأ التسليم».
     • أرسل ملفاتك في محادثة البوت دفعة واحدة أو في رسائل متتالية (بحد أقصى 20 ملفاً للواجب الواحد). يقبل البوت كافة المستندات والملفات المضغوطة والبرمجية (PDF, DOCX, XLSX, ZIP, RAR, PY, CPP وغيرها)، بينما يرفض الصور العادية ويطلب منك تحويلها إلى ملف PDF أو مستند.
     • اعتماد التسليم النهائي: بعد رفع ملفاتك، يجب الضغط على زر «✅ إنهاء التسليم»؛ عندها فقط يعتمد النظام تسليمك ويرسله مع بياناتك الأكاديمية كـ Caption مدمج بالملف إلى قسم مجموعتك في مجتمع الدكتور.
   - ثانياً: إدارة الواجبات وتعديل الملف المرسل من داخل الـ Mini App:
     • داخل تبويب «الواجبات» في التطبيق يمكنك التبديل الفوري بين أربع قوائم: الكل، مفتوحة، مسلّمة، وفائتة.
     • تعديل إرسال الملف في حال الخطأ: إذا أرسلت ملفاً بالخطأ وأردت استبداله بالواجب الصحيح، افتح التطبيق المصغّر وانتقل إلى تبويب «الواجبات ← مسلّمة» (أو افتح تفاصيل الواجب أو شاشة «شؤوني ← تسليماتي»)، ثم اضغط على زر «تعديل إرسال الملف في البوت».
     • آلية الاستبدال التلقائي لدى الدكتور: بمجرد الضغط على زر التعديل، يفتح التطبيق محادثة البوت مباشرة في وضع استقبال الملف البديل؛ أرسل ملفك الصحيح واضغط «إنهاء التسليم»، وسيقوم البوت تلقائياً بحذف رسائل تسليمك القديم من مجتمع الدكتور وإرسال الملف الجديد مكانها برقم مراجعة محدّث.
     • استرجاع ملفاتك وتأكيد استلام الدكتور: يمكنك في أي وقت استعادة نسخة من ملفاتك المسلّمة بالضغط على «استرجاع ملفات تسليمي في البوت»، كما ستظهر لك شارة «استلمه الدكتور» فور ضغط الدكتور على زر الاستلام في مجتمعه.

4. نظام الملفات والمحاضرات والبحث الذكي (AI Smart Search):
   - في البوت: يتم تصفّح الملفات (مادة ← نظري/عملي ← نوع المحتوى ← الملف) مع تعديل نفس الرسالة الحالية دون إرسال رسائل جديدة تملأ المحادثة.
   - في التطبيق: يقع شريط البحث الذكي وزر البحث مباشرة بجوار عنوان «الملفات» في أعلى الشاشة، ويعمل بطريقتين:
     1) فلترة نصية فورية (0ms) أثناء الكتابة.
     2) البحث الذكي بالذكاء الاصطناعي عند الضغط على زر البحث أو Enter (مثل: ملخص شبكات Module 9 أو نموذج نصفي قواعد بيانات).
   - فلاتر التصفية السريعة الثلاثة:
     • المادة: شرائح أفقية لاختيار «كل المواد» أو مادة دراسية بعينها.
     • نوع الملف: شرائح مزوّدة بأيقونات توضيحية تشمل: الكل، محاضرة، ملخص، نموذج، تمارين، ترجمة، مراجع، وفائدة.
     • شريط القسم: مفتاح ثلاثي للتبديل بين الكل ونظري وعملي.
   - إرسال الملف إلى محادثتك بضغطة واحدة: بالضغط على الزر الدائري للإرسال (الملوّن حسب لونك الرئيسي المختار) يصلك الملف فوراً في محادثة البوت.

5. نظام اختبارات الاختيار من متعدد (MCQs) في التطبيق والبوت:
   - يعمل بأسلوبين متوازيين ويحفظان نتائجك في نفس السجل الأكاديمي:
     • داخل الـ Mini App (قسم «الاختبارات»): بطاقات تفاعلية حديثة تدعم النصوص الإنجليزية والعربية بسلاسة، مع إمكانية تفعيل أو إيقاف العدّاد الزمني (Timer) وخلط الأسئلة، ومؤشر دائري للنسبة المئوية، وشاشة مراجعة الأخطاء (Review Mistakes) بالتفصيل.
     • داخل البوت («اختبارات MCQ»): النظام الكلاسيكي عبر استفتاءات تيليجرام التفاعلية (Quiz Polls) التي تُرسل سؤالاً تلو الآخر مع تصحيح فوري ورسالة ختامية بالدرجة النهائية وزر «إعادة الاختبار».

6. قسم «شؤوني» (الحضور، الباركود، التظلمات، التسليمات، والإعدادات):
   - بطاقة «حضوري» (سجل المحاضرات النظرية 1–12): تعرض ملخص حضورك النظري الإجمالي ونسبتك المئوية، وشبكة المحاضرات الاثنتي عشرة (م1 إلى م12) ملونة بوضوح: أخضر (حاضر)، أحمر (غائب)، برتقالي (جلسة مفتوحة الآن)، أو رمادي (لم تُعقد).
   - بطاقة «باركود بطاقتي» (Code 128): حل عملي عند نسيان البطاقة الجامعية؛ اضغط «إرسال صورة الباركود في البوت» لتصلك صورة عالية الوضوح جاهزة للمسح المباشر بجهاز التحضير، بحد أقصى 3 مرات يومياً.
   - بطاقة «تظلماتي»: عندما يفتح المندوب دورة تظلمات لمجموعتك، لا تحتاج لكتابة اسمك أو رقمك الأكاديمي (تُرفق تلقائياً)؛ اكتب سبب التظلم فقط واضغط «إرسال التظلم»، ويمكنك تعديل سبب تظلمك في أي وقت طالما كانت الدورة مفتوحة.
   - بطاقة «تسليماتي»: الأرشيف الكامل للواجبات مع توقيت تسليمك وحالة استلام الدكتور وأزرار تعديل إرسال الملف واسترجاع ملفاتك.
   - بطاقة «الإعدادات»: اختيار مظهر التطبيق (تلقائي / نهاري / ليلي)، وتغيير اللون الأساسي لواجهة التطبيق بين 5 ألوان، والتحكم بمفاتيح الإشعارات وتذكيرات الواجبات (قبل الموعد بـ 24 ساعة، و3 ساعات، وساعة واحدة).

7. قسم «شارك» (الاستفتاءات وحملات استقبال المشاركات):
   - الاستفتاءات (Polls): اختر الخيار المناسب واضغط تصويت؛ يُسجّل صوتك لمرة واحدة نهائية وتظهر نسب التصويت فوراً.
   - حملات استقبال المشاركات (Submissions): عند الضغط على زر المشاركة في التطبيق، ينقلك النظام إلى محادثة البوت لإرسال مشاركتك (نص أو صورة أو مستند واحد أو عدة ملفات متتالية)، وبعد الانتهاء اضغط زر «✅ إنهاء المشاركة» لاعتمادها رسمياً.

8. المساعد الذكي والدعم الفني (شاشة المحادثة الكاملة):
   - تفتح بملء الشاشة الكاملة دون ظهور شريط التنقل السفلي (Navbar)، وتعرض بطاقة الحضور البصرية عند السؤال عن الحضور.
   - يملك كل طالب رصيداً يومياً قدره 10 أسئلة للمساعد الذكي (و5 أسئلة للزائر) للأسئلة التي تستهلك الذكاء الاصطناعي، بينما الأسئلة الجاهزة والشائعة مجانية وغير محدودة.
   - للتواصل البشري المباشر مع الإدارة أو الإبلاغ عن مشكلة: اضغط على أيقونة السماعة (🎧) في أعلى يسار الشاشة، وحدد التصنيف (بلاغ عطل / شكوى / استفسار)، واكتب رسالتك لتصل للمطوّر ويظهر الرد داخل نفس المحادثة مع إشعار فوري في البوت.

9. الأسئلة الشائعة وحلول المشكلات السريعة للطالب (FAQ):
   - لماذا لا يظهر الواجب الذي سلّمته عند الضغط على «الواجبات» في البوت؟ لأن قائمة «الواجبات» في البوت صُمّمت لتعرض لك الواجبات المفتوحة غير المسلَّمة فقط حتى تركز على ما لم تسلّمه بعد. لاستعراض واجباتك المسلّمة أو تعديلها، افتح الـ Mini App ← تبويب «الواجبات» ← شريحة «مسلّمة» (أو «شؤوني ← تسليماتي»).
   - أرسلت ملف الواجب في البوت لكن لم تصلني رسالة تأكيد التسليم؟ مجرد إرسال الملف في المحادثة يضيفه كملف مؤقت في الجلسة؛ يجب عليك دائماً الضغط على زر «✅ إنهاء التسليم» أسفل رسالة البوت لاعتماد التسليم وإرساله لمجتمع الدكتور.
   - لماذا رفض البوت استلام صورة حل الواجب؟ الصور العادية غير مقبولة في تسليمات الواجبات؛ حوّل صور الحل إلى ملف PDF واحد أو ضعها داخل ملف Word أو أرشيف ZIP ثم أرسله للبوت.
   - كيف أتأكد أن ملفي وصل للدكتور فعلاً؟ بمجرد ظهور رسالة «تم تسليم واجبك» يُرسل البوت ملفك إلى قسم مجموعتك في مجتمع الدكتور، وعندما يضغط الدكتور زر الاستلام ستظهر لك شارة «استلمه الدكتور» في تفاصيل الواجب وفي قسم «تسليماتي».
   - نسيت بطاقتي الجامعية يوم المحاضرة النظرية، ماذا أفعل؟ افتح التطبيق المصغّر ← «شؤوني» ← «باركود بطاقتي» ← اضغط «إرسال صورة الباركود في البوت»، ثم اعرض الصورة من هاتفك للمندوب ليمسحها بجهاز التحضير.
"""
def get_bot_guide() -> str:
    ch = cohort.get()
    return BOT_GUIDE.strip().format(
        cohort_title=ch.title,
        theory_groups=" أو ".join(ch.theory_ids()),
        practical_range=f"G1 إلى G{len(ch.practical_ids())}",
    )


BOT_GUIDE = get_bot_guide()


def _summarize_student_context(ctx: Mapping[str, Any], now: datetime) -> str:
    st = ctx.get("student")
    if not st:
        return "المستخدم الحالي: زائر (غير مربوط برقم أكاديمي)."

    lines = [
        f"الطالب: {st.get('full_name')} | الرقم الأكاديمي: {st.get('academic_no')} | "
        f"المجموعة النظرية: {st.get('theory_group_id')} | العملية: {st.get('practical_group_id')}"
    ]
    asgs = ctx.get("assignments") or []
    open_list, late_list, sub_count, missed_count = [], [], 0, 0
    for a in asgs:
        state = student_status(now, a)
        if state == "submitted":
            sub_count += 1
        elif state == "open":
            open_list.append(f"{a['subject']} (واجب {a['number']} - آخر موعد {format_datetime_ar(parse_ts(a['due_at']))})")
        elif state == "late":
            late_list.append(f"{a['subject']} (واجب {a['number']} - متأخر)")
        elif state == "missed":
            missed_count += 1

    lines.append(f"الواجبات المسلّمة: {sub_count} | المفتوحة غير المسلّمة: {len(open_list)} | المتأخرة القابلة للتسليم: {len(late_list)} | الفائتة: {missed_count}")
    if open_list:
        lines.append("تفاصيل الواجبات المفتوحة: " + " ؛ ".join(open_list[:4]))
    if late_list:
        lines.append("تفاصيل الواجبات المتأخرة: " + " ؛ ".join(late_list[:3]))

    att = ctx.get("attendance") or []
    att_parts = []
    for row in att:
        held = int(row.get("held") or 0)
        pres = int(row.get("present") or 0)
        ab = int(row.get("absent") or 0)
        if held > 0:
            pct = round((pres * 100) / held)
            att_parts.append(f"{row['subject']}: حاضر {pres}، غائب {ab} من {held} ({pct}%)")
    if att_parts:
        lines.append("الحضور النظري: " + " | ".join(att_parts))
    else:
        lines.append("الحضور النظري: لم تُسجّل جلسات حضور مغلقة بعد.")

    grvs = [g for g in (ctx.get("grievances") or []) if g.get("is_open")]
    if grvs:
        lines.append("دورات التظلم المفتوحة حالياً: " + " ، ".join(f"{g['subject']} ({'قدّمت تظلماً' if g.get('my_grievance') else 'لم تقدّم بعد'})" for g in grvs))
    else:
        lines.append("دورات التظلم المفتوحة حالياً: لا توجد دورة تظلمات مفتوحة الآن.")

    return "\n".join(lines)


GENERIC_PREFIX = f"أهلاً بك في المساعد الذكي ل{cohort.get().title}"

# أنماط تدل على أن النص يحتوي بيانات خاصة بطالب بعينه — يُمنع تخزينها أو استرجاعها من الكاش المشترك (ai_cache)
_PERSONAL_ANSWER_PATTERNS = re.compile(
    r"(?:20\d{5,}|الرقم الأكاديمي\s*:|الطالب\s*:|ملخص واجباتك|ملخص حضورك|حالتك الحالية|" + re.escape(cohort.get().theory_prefix) + r"\d{1,2}\s*·\s*المجموعة العملية)",
    re.UNICODE,
)

_PERSONAL_QUESTION_TOKENS = {
    "واجبي", "واجباتي", "حضوري", "غيابي", "غياباتي", "نسبتي", "تظلمي", "تظلماتي",
    "تسليمي", "تسليماتي", "مجموعتي", "رقمي", "بياناتي", "حالتي", "بطاقتي", "ملخصي",
    "درجتي", "درجاتي", "غبت",
}


def _contains_personal_data(text: str) -> bool:
    """يتحقق من عدم احتواء الرد المشترك على أي بيانات شخصية لطالب آخر."""
    if not text:
        return False
    return bool(_PERSONAL_ANSWER_PATTERNS.search(text))


def _is_personal_question(toks: set[str]) -> bool:
    """هل السؤال يطلب بيانات الطالب الشخصية؟"""
    return bool(toks & _PERSONAL_QUESTION_TOKENS)


def _faq_answer(toks: set[str], ctx: Mapping[str, Any]) -> str | None:
    """الأسئلة الشائعة المبرمجة من الدليل المعتمد (صفر توكنات، ولا تخلط بيانات أي طالب بآخر)."""
    if toks & {"شكرا", "مشكور", "يعطيك", "يسلمو", "تسلم", "جزاك", "thanks", "thx"}:
        return "العفو 🌿 أنا هنا إن احتجت أي شيء آخر عن الواجبات أو الحضور أو الملفات."
    if len(toks) <= 3 and toks & {"مرحبا", "اهلا", "هلا", "سلام", "صباح", "مساء", "هاي", "hi", "hello"}:
        return "أهلاً بك 👋 اسألني عن واجباتك، حضورك، الملفات الدراسية، التظلمات، شارك، أو باركود بطاقتك."
    if toks & {"ربط", "اربط", "اربطه", "ربطت", "لينك", "تسجيل", "اسجل", "سجل"} and toks & {"حساب", "حسابي", "رقم", "رقمي", "اكاديمي", "اكاديميه", "طالب"}:
        return (
            "🔗 لربط حسابك كطالب:\n"
            "1. أرسل `/start` في البوت أو افتح التطبيق المصغّر واضغط «أنا طالب في الدفعة».\n"
            "2. أدخل رقمك الأكاديمي (بالأرقام الإنجليزية أو العربية)، وسيعرض النظام اسمك الكامل ومجموعتيك النظرية والعملية؛ اضغط «نعم، هذه بياناتي» لإتمام الربط.\n"
            "3. إذا ظهر أن الرقم مرتبط بحساب آخر، اضغط أيقونة السماعة (🎧) في الدعم وأرسل بلاغاً للمطوّر لفك الارتباط."
        )
    if toks & {"زائر", "زوار", "ضيف", "guest"}:
        return (
            "👤 الزائر يدخل باسمه فقط، ويستطيع تصفح الملفات والمراجع والاختبارات وسؤال المساعد الذكي (5 رسائل يوميًا).\n"
            "ويمكن للزائر في أي وقت ترقية حسابه إلى طالب بالضغط على زر «أنا طالب: ربط حسابي»."
        )
    if toks & {"رصيد", "رسالتان", "رسائل", "حد"} and toks & {"مساعد", "يومي", "يوميا", "يوميه", "اليوم", "كم", "باقي", "متبقي"}:
        lim = ctx.get("limit")
        used, rem = ctx.get("used"), ctx.get("remaining")
        extra = f"\nاستخدمت اليوم {used} وبقي لك {rem} من {lim}." if lim is not None and used is not None else ""
        return (
            "🤖 رصيد المساعد اليومي: 10 رسائل للطالب و5 للزائر، ويتجدد كل يوم بتوقيت صنعاء.\n"
            "الأسئلة الجاهزة والشائعة (الواجبات، الحضور، الباركود، التظلمات، الإعدادات، الملفات، شارك...) مجانية ولا تُحتسب من الرصيد؛ "
            "يُحتسب فقط ما يستهلك الذكاء الاصطناعي." + extra
        )
    if toks & {"صور", "صوره", "سكرين", "سكرينشوت", "تصوير", "صورة"} and not (toks & {"باركود", "بطاقه", "بطاقتي", "شارك"}):
        return (
            "🖼️ الصور العادية غير مقبولة في تسليمات الواجبات.\n"
            "حوّل صور الحل إلى ملف PDF واحد أو ضعها داخل ملف Word أو أرشيف ZIP، ثم أرسله للبوت واضغط «✅ إنهاء التسليم»."
        )
    if toks & {"اختبار", "اختبارات", "كويز", "mcq", "امتحان"} and not (toks & {"نصفي", "نهائي", "نموذج", "ملف", "ملفات"}):
        return (
            "🧠 نظام اختبارات الـ MCQ يعمل بأسلوبين متوازيين يحفظان نتيجتك في نفس السجل:\n"
            "• في التطبيق (قسم «الاختبارات»): بطاقات تفاعلية تدعم الإنجليزية والعربية مع عدّاد زمني اختياري، وخلط الأسئلة، وشاشة مراجعة الأخطاء (Review Mistakes).\n"
            "• في البوت («اختبارات MCQ»): استفتاءات تيليجرام التفاعلية (Quiz Polls) سؤالاً تلو الآخر مع تصحيح فوري وزر «إعادة الاختبار»."
        )
    if toks & {"شارك", "استفتاء", "استفتاءات"}:
        return (
            "🗳 قسم «شارك» يشمل نوعين من الحملات:\n"
            "• الاستفتاءات (Polls): اختر الخيار المناسب داخل التطبيق واضغط تصويت (اختيار واحد نهائي وتظهر النسب فوراً).\n"
            "• حملات استقبال المشاركات (Submissions): اضغط «📤 الانتقال للبوت لإرسال مشاركتي»، ثم أرسل مشاركتك (نص أو صورة أو ملف) في البوت واضغط «✅ إنهاء المشاركة»."
        )
    if (toks & {"يظهر", "تظهر", "اختفي", "مختفي", "وين"}) and (toks & {"سلمته", "سلمت", "المسلم", "مسلم"}) and (toks & {"واجب", "واجبات"}):
        return (
            "📌 قائمة «الواجبات» في البوت صُمّمت لتعرض لك الواجبات المفتوحة غير المسلَّمة فقط حتى تركز على ما لم تسلّمه بعد.\n"
            "لاستعراض واجباتك المسلّمة أو تعديلها أو استرجاع ملفاتها، افتح الـ Mini App ← تبويب «الواجبات» ← شريحة «مسلّمة» (أو «شؤوني ← تسليماتي»)."
        )
    if (toks & {"تاكيد", "تأكيد", "رساله"}) and (toks & {"تسليم", "سلمت", "ارسلت"}) and (toks & {"واجب", "ملف"}):
        return (
            "⚠️ مجرد إرسال الملف في محادثة البوت يضيفه كملف مؤقت في الجلسة؛ يجب عليك دائماً الضغط على زر «✅ إنهاء التسليم» أسفل رسالة البوت لاعتماد التسليم وإرساله لمجتمع الدكتور."
        )
    if (toks & {"وصل", "استلم", "استلمه", "دكتور"}) and (toks & {"ملفي", "ملف", "واجب", "واجبي", "تسليم", "تسليمي"}):
        return (
            "✅ بمجرد ظهور رسالة «تم تسليم واجبك ✅» يُرسل البوت ملفك مع بياناتك إلى قسم مجموعتك في مجتمع الدكتور.\n"
            "وعندما يضغط الدكتور زر الاستلام ستظهر لك شارة «استلمه الدكتور» في تفاصيل الواجب وفي قسم «شؤوني ← تسليماتي»."
        )
    return None


def answer_from_guide_and_data_local(question: str, ctx: Mapping[str, Any], now: datetime | None = None) -> tuple[str, bool]:
    """يجيب محلياً بدقة وسرعة من بيانات الطالب نفسه فقط ومن دليل البوت المعتمد. يعيد (الإجابة، هل_يقترح_بلاغ)."""
    now = now or datetime.now(UTC)
    toks = set(match_tokens(question))
    q_norm = normalize_text(question)
    st = ctx.get("student")

    # 0) «كيف أربط رقمي الأكاديمي؟» سؤال شرح لا بلاغ (إلا إن ذكر فك/شكوى/عطل)
    if toks & {"ربط", "اربط", "اربطه", "ربطت"} and not toks & {"فك", "شكوي", "عطل", "مشكله", "بلاغ", "خربان", "معلق"}:
        faq_link = _faq_answer(toks, ctx)
        if faq_link:
            return (faq_link, False)

    # 0.5) أسئلة الـ FAQ المحددة عن سبب عدم ظهور الواجب المسلّم في البوت أو تأكيد وصوله للدكتور
    if ((toks & {"يظهر", "تظهر", "اختفي", "مختفي"} and toks & {"سلمته", "سلمت", "مسلم"})
        or (toks & {"وصل", "استلم", "استلمه"} and toks & {"دكتور"})):
        faq_sp = _faq_answer(toks, ctx)
        if faq_sp:
            return (faq_sp, False)

    # 1) إذا كان المستخدم يطلب رفع شكوى أو بلاغ أو فك ارتباط
    if toks & {"شكوي", "عطل", "مشكله", "بلاغ", "خربان", "معلق", "فك", "ارتباط", "تغيير", "مرتبط"} or (
        "رقمي" in toks and toks & {"ماخذ", "ماخوذ", "مربوط", "مشغول", "غلط", "خطا"}
    ):
        return (
            "لتقديم بلاغ عطل أو شكوى أو طلب فك ارتباط الرقم الأكاديمي، اضغط على أيقونة السماعة (🎧) في أعلى يسار شاشة الدعم "
            "(أو زر «📩 إرسال بلاغ للمطوّر» في البوت)، وحدد التصنيف واكتب التفاصيل ليصل للمطوّر فوراً ويرد عليك داخل نفس المحادثة.",
            True,
        )

    # 2) أسئلة عن بيانات الطالب الشخصية (مجموعتي، رقمي، بياناتي)
    if toks & {"مجموعتي", "مجموعه", "رقمي", "بياناتي", "حسابي"} and not (toks & {"واجب", "واجبات", "حضور", "تظلم"}):
        if not st:
            return (
                "أنت تتصفح حالياً كزائر. لربط حسابك كطالب وعرض مجموعتك النظرية والعملية، افتح الرئيسية واضغط «أنا طالب: ربط حسابي» أو أرسل `/start` في البوت.",
                False,
            )
        return (
            f"👤 الاسم: {st['full_name']}\n"
            f"🆔 الرقم الأكاديمي: {st['academic_no']}\n"
            f"👥 المجموعة النظرية: {st['theory_group_id']} · المجموعة العملية: {st['practical_group_id']}",
            False,
        )

    # 3) أسئلة عن الواجبات والتسليمات
    if toks & {"واجب", "واجبات", "واجباتي", "واجبي", "تسليم", "تسليماتي", "تسليمي", "سلمت", "موعد", "متاخر"}:
        if toks & {"كيف", "طريقه", "صيغه", "صور", "صوره", "تعديل", "اعدل", "استبدال", "استرجاع", "غلط", "خطا"}:
            return (
                "📝 طريقة تسليم وتعديل واسترجاع الواجبات:\n"
                "1. التسليم الجديد: من «📝 الواجبات» في البوت تظهر لك الواجبات المفتوحة غير المسلّمة لمجموعتك بصيغة (اسم المادة - نظري/عملي)؛ اختر الواجب واضغط «ابدأ التسليم»، وأرسل ملفاتك (PDF, Word, ZIP وغيرها عدا الصور) ثم اضغط وجوباً على «✅ إنهاء التسليم».\n"
                "2. تعديل ملف أُرسل بالخطأ: افتح التطبيق المصغّر ← تبويب «الواجبات ← مسلّمة» (أو «شؤوني ← تسليماتي») ← اضغط «تعديل إرسال الملف في البوت»، ثم أرسل الملف الصحيح واضغط «إنهاء التسليم»؛ سيحذف البوت تسليمك القديم من عند الدكتور ويرسل الجديد مكانه.\n"
                "3. استرجاع ملفاتك وتأكيد الاستلام: من «شؤوني ← تسليماتي» اضغط «استرجاع ملفاتي في البوت»، وستظهر لك شارة «استلمه الدكتور» فور ضغط الدكتور على زر الاستلام.",
                False,
            )
        if not st:
            return ("ميزة الواجبات خاصة بطلاب الدفعة. اربط رقمك الأكاديمي أولاً لتظهر لك واجبات مجموعتك.", False)

        asgs = ctx.get("assignments") or []
        if not asgs:
            return (f"لا توجد واجبات مسجّلة لمجموعتك ({st['theory_group_id']} / {st['practical_group_id']}) حتى الآن.", False)

        open_items, late_items, sub_items = [], [], []
        for a in asgs:
            state = student_status(now, a)
            if state == "open":
                open_items.append(f"• {a['subject']} · واجب {a['number']} (آخر موعد: {format_datetime_ar(parse_ts(a['due_at']))})")
            elif state == "late":
                late_items.append(f"• {a['subject']} · واجب {a['number']} (متأخر — لا يزال التسليم متاحاً)")
            elif state == "submitted":
                sub_items.append(f"• {a['subject']} · واجب {a['number']} ({STATUS_AR[state]})")

        parts = [f"📊 ملخص واجباتك ({len(sub_items)} مسلَّم من أصل {len(asgs)}):"]
        if open_items:
            parts.append("🟢 واجبات مفتوحة بانتظار التسليم:\n" + "\n".join(open_items[:5]))
        if late_items:
            parts.append("🟠 واجبات متأخرة يمكنك تسليمها الآن:\n" + "\n".join(late_items[:5]))
        if not open_items and not late_items:
            parts.append("🎉 لا يوجد عليك أي واجب مفتوح غير مسلَّم حالياً!")
        return ("\n\n".join(parts), False)

    # 4) أسئلة عن الحضور والغياب
    if toks & {"حضور", "حضوري", "غياب", "غيابي", "غائب", "غبت", "حاضر", "نسبه", "نسبتي", "تحضير", "معرضه"}:
        if toks & {"كيف", "جهاز", "مسح", "سكانر", "scanner"}:
            return (
                "📋 نظام الحضور النظري:\n"
                "• يؤخذ الحضور للمحاضرات النظرية (12 محاضرة لكل مادة).\n"
                "• يفتح المندوب (أو النائب/اللجنة عند غيابه) جلسة الحضور ويمسح البطاقة الجامعية أو باركود البطاقة.\n"
                "• تعرض بطاقة «شؤوني ← حضوري» شبكة المحاضرات (م1–م12) ملونة: أخضر (حاضر)، أحمر (غائب)، برتقالي (جلسة مفتوحة الآن)، رمادي (لم تُعقد).",
                False,
            )
        if not st:
            return ("سجل الحضور متاح لطلاب الدفعة بعد ربط الرقم الأكاديمي.", False)

        att = ctx.get("attendance") or []
        lines = ["📋 ملخص حضورك في المحاضرات النظرية (12 محاضرة لكل مادة):"]
        any_held = False
        for r in att:
            held = int(r.get("held") or 0)
            pres = int(r.get("present") or 0)
            ab = int(r.get("absent") or 0)
            if held > 0:
                any_held = True
                pct = round((pres * 100) / held)
                lines.append(f"• {r['subject']}: حاضر {pres} · غائب {ab} (من {held}) — النسبة {pct}%")
            else:
                lines.append(f"• {r['subject']}: لم تُعقد جلسات بعد")
        if not any_held:
            lines.append("لم تُسجّل جلسات حضور مغلقة لمجموعتك حتى الآن.")
        return ("\n".join(lines), False)

    # 5) أسئلة عن الباركود والبطاقة
    if toks & {"باركود", "بطاقه", "بطاقتي", "نسيت", "كود", "code", "128"}:
        return (
            "🪪 باركود البطاقة الجامعية (Code 128):\n"
            "إذا نسيت بطاقتك الجامعية يوم المحاضرة النظرية، افتح التطبيق المصغّر ← «شؤوني» ← «باركود بطاقتي» ← اضغط «إرسال صورة الباركود في البوت».\n"
            "ستصلك صورة باركود قياسية عالية الوضوح لرقمك الأكاديمي مع اسمك ومجموعتك جاهزة للمسح المباشر بجهاز التحضير (بحد أقصى 3 مرات يومياً).",
            False,
        )

    # 6) أسئلة عن التظلمات
    if toks & {"تظلم", "تظلمات", "تظلماتي", "تظلمي", "اعتراض", "درجه", "درجات"}:
        if not st:
            return ("نظام التظلمات خاص بطلاب الدفعة المربوطين بأرقامهم الأكاديمية.", False)
        grvs = [g for g in (ctx.get("grievances") or []) if g.get("is_open")]
        info = (
            "⚖️ نظام التظلمات:\n"
            "• عندما يفتح المندوب دورة تظلمات لمجموعتك، يصلك إشعار فوري وتظهر لك في «شؤوني ← تظلماتي».\n"
            "• لا تحتاج لكتابة اسمك أو رقمك الأكاديمي (يرفقها النظام تلقائياً)؛ اكتب سبب التظلم فقط واضغط «إرسال التظلم».\n"
            "• تستطيع الضغط على «تعديل التظلم قبل الإغلاق» لتحديث سبب تظلمك في أي وقت طالما كانت الدورة مفتوحة."
        )
        if grvs:
            open_txt = "\n\n🟢 دورات التظلم المفتوحة لك الآن:\n" + "\n".join(
                f"• {g['subject']} ({'قدّمت تظلمك — يمكنك تعديله' if g.get('my_grievance') else 'مفتوحة للتقديم'})"
                for g in grvs
            )
            return (info + open_txt, False)
        return (info + "\n\nلا توجد دورة تظلمات مفتوحة لمجموعتك في هذه اللحظة.", False)

    # 6.5) الأسئلة الشائعة المبرمجة
    faq = _faq_answer(toks, ctx)
    if faq:
        return (faq, False)

    # 7) أسئلة عن الملفات والمراجع والفوائد والبحث الذكي
    if toks & {"ملف", "ملفات", "ملخص", "ملخصات", "محاضره", "محاضرات", "نموذج", "نماذج", "ترجمه", "تمارين", "مرجع", "مراجع", "فائده", "فوائد", "شابتر", "موديول", "امتحانات"} or "بحث" in q_norm:
        return (
            "📚 نظام الملفات والمحاضرات والبحث الذكي:\n"
            "• من التطبيق: افتح تبويب «الملفات»؛ يمكنك التصفية الفورية (0ms) بالمادة ونوع الملف (محاضرة، ملخص، نموذج، تمارين، ترجمة، مراجع، فائدة) والقسم (نظري/عملي)، ثم اضغط زر الإرسال الدائري ليصلك الملف فوراً في البوت.\n"
            "• البحث الذكي: اكتب ما تبحث عنه في الشريط العلوي بجوار كلمة «الملفات» (مثل: «ملخص شبكات Module 9» أو «مرجع قواعد بيانات») واضغط زر البحث.\n"
            "• من البوت: اضغط «📚 الملفات» وتنقّل بين المادة والقسم ونوع المحتوى في نفس الرسالة.",
            False,
        )

    # 8) أسئلة عن الإشعارات أو المظهر أو اللون
    if toks & {"اشعار", "اشعارات", "تنبيه", "تذكير", "مظهر", "ليلي", "نهاري", "لون", "اعدادات"}:
        return (
            "⚙️ الإعدادات (المظهر والتذكيرات):\n"
            "من «شؤوني ← الإعدادات» (أو تبويب «المزيد») يمكنك:\n"
            "• اختيار مظهر التطبيق (تلقائي / نهاري / ليلي) وتغيير اللون الأساسي لواجهة التطبيق بين 5 ألوان.\n"
            "• التحكم بمفاتيح الإشعارات: إشعار نشر واجب جديد، إشعار «شارك»، وتذكيرات الواجبات قبل الموعد بـ 24 ساعة، و3 ساعات، وساعة واحدة (تصل فقط لمن لم يسلّم الواجب بعد).",
            False,
        )

    # رد عام شامل بملخص الطالب الحالي وحده ودليل الاستخدام
    summary = _summarize_student_context(ctx, now)
    return (
        f"{GENERIC_PREFIX} 🤖\n\n"
        f"📌 حالتك الحالية:\n{summary}\n\n"
        "يمكنك سؤالي عن:\n"
        "• واجباتك المفتوحة ومواعيد تسليمها أو طريقة التسليم والتعديل واسترجاع الملفات\n"
        "• سجل حضورك ونسبتك في المحاضرات النظرية (1–12)\n"
        "• البحث في الملفات والمحاضرات والملخصات والنماذج والمراجع والفوائد\n"
        "• توليد باركود بطاقتك الجامعية (Code 128) أو التظلمات أو «شارك»\n"
        "• أو اضغط أيقونة السماعة (🎧) في الأعلى لمراسلة الدعم والمطوّر مباشرة.",
        True,
    )


def answer_local_intent(question: str, ctx: Mapping[str, Any], now: datetime | None = None) -> tuple[str, bool, bool]:
    """مثل answer_from_guide_and_data_local لكن يضيف علم «هل طابق نيّة معروفة؟» (False = الرد العام الاحتياطي)."""
    ans, ticket = answer_from_guide_and_data_local(question, ctx, now)
    return ans, ticket, not ans.startswith(GENERIC_PREFIX)


_GUIDE_HASH = hashlib.sha1((BOT_GUIDE + "|v3_privacy").encode("utf-8")).hexdigest()[:8]


async def ask_assistant(question: str, ctx: Mapping[str, Any], now: datetime | None = None,
                        store: Any = None, allow_ai: bool = True) -> dict[str, Any]:
    """يرد على سؤال المستخدم مع ضمان عدم تسريب أي بيانات خاصة بطلاب آخرين في الأسئلة المكررة:
    (1) النيّات والأسئلة الجاهزة المحلية تُجاب فوراً (وببيانات الطالب السائل وحده عند السؤال عن حالته).
    (2) الأسئلة العامة المتكررة تُقرأ وتُحفظ في كاش مشترك بشرط خلوّ السؤال والرد تماماً من أي بيانات شخصية.
    (3) Gemini يجيب بالاعتماد على دليل الطالب المعتمد دون تضمين بيانات طلاب آخرين."""
    now = now or datetime.now(UTC)
    clean_q = re.sub(r"\s+", " ", (question or "")).strip()[:500]
    base = {"needs_ticket": False, "used_ai": False, "counted": False, "blocked": False}
    if not clean_q:
        return {**base, "answer": "اكتب سؤالك وسأجيبك من دليل البوت ومن بياناتك المسجّلة."}

    local_ans, local_ticket, matched = answer_local_intent(clean_q, ctx, now)
    base = {**base, "answer": local_ans, "needs_ticket": local_ticket}
    toks = set(match_tokens(clean_q))

    # إذا طابق نيّة محلية أو كان السؤال شخصياً يخص بيانات الطالب السائل، نعيد إجابته الخاصة مباشرة ولا نستخدم الكاش المشترك أبداً
    if matched or _is_personal_question(toks) or not _gemini_keys():
        return base

    is_student = bool(ctx.get("student"))
    key = cache_key("ask", clean_q, f"{_GUIDE_HASH}:{'s' if is_student else 'g'}")
    cached = await cache_get(store, key)
    if isinstance(cached, dict) and isinstance(cached.get("answer"), str) and cached["answer"].strip():
        cand_ans = cached["answer"].strip()[:1800]
        if not _contains_personal_data(cand_ans):
            return {**base, "answer": cand_ans, "needs_ticket": bool(cached.get("needs_ticket", local_ticket)),
                    "used_ai": True, "cached": True}

    if not allow_ai:
        return {**base, "blocked": True}

    who = "طالب في الدفعة" if is_student else "زائر"
    sys_prompt = (
        f"أنت المساعد الذكي لبوت وتطبيق {cohort.get().title}.\n"
        "أجب بالعربية في 3 جمل كحد أقصى بالاعتماد حصراً على (الدليل العملي الشامل للطالب) أدناه. لا تخترع معلومات.\n"
        "تنبيه خصوصية صارم: هذا رد عام مشترك للأسئلة المتكررة؛ يُمنع منعاً باتاً ذكر أي اسم طالب أو رقم أكاديمي أو مجموعة أو درجات أو حضور خاص بأي طالب.\n"
        "إن كان السؤال يطلب بيانات المستخدم الشخصية (واجباته/حضوره/مجموعته/تظلماته) فوجّهه لأقسام «الواجبات» و«شؤوني ← حضوري / تظلماتي / تسليماتي».\n"
        "إن كان السؤال شكوى أو عطلاً أو طلب فك ارتباط رقم أكاديمي أو تعذّرت الإجابة فاجعل needs_ticket=true ووجّهه لأيقونة السماعة (🎧) لإرسال بلاغ للمطوّر.\n"
        'أعد JSON فقط: {"answer": "النص", "needs_ticket": boolean}\n'
        f"صفة السائل: {who}\n\n=== الدليل العملي الشامل للطالب ===\n{BOT_GUIDE}"
    )
    ai_res = await _call_gemini_json(sys_prompt, f"سؤال المستخدم: {clean_q}", timeout=4.0, max_tokens=400)
    if isinstance(ai_res, dict) and isinstance(ai_res.get("answer"), str) and ai_res["answer"].strip():
        ans_clean = ai_res["answer"].strip()[:1800]
        value = {"answer": ans_clean, "needs_ticket": bool(ai_res.get("needs_ticket", local_ticket))}
        if not _contains_personal_data(ans_clean):
            await cache_put(store, key, "ask", value)
        return {**base, **value, "used_ai": True, "counted": True}
    return base


def validate_ticket(category: object, body: object) -> tuple[tuple[str, str] | None, str | None]:
    cat = str(category or "").strip()
    if cat not in TICKET_CATEGORIES:
        return None, "invalid_category"
    text = re.sub(r"\s+", " ", str(body or "")).strip()
    if len(text) < 4:
        return None, "body_too_short"
    if len(text) > 1500:
        return None, "body_too_long"
    return (cat, text), None


def validate_ticket_reply(reply: object) -> tuple[str | None, str | None]:
    text = re.sub(r"\s+", " ", str(reply or "")).strip()
    if len(text) < 2:
        return None, "reply_too_short"
    if len(text) > 1500:
        return None, "reply_too_long"
    return text, None

"""اختبارات توفير توكنات Gemini: أسئلة جاهزة، كاش، رصيد، وبحث ملفات بالكلمات المفتاحية أولًا."""
import asyncio
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from tests import stubs

stubs.install()

from core import assistant  # noqa: E402
from core.assistant import answer_local_intent, ask_assistant, smart_search_files  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SUBJECTS = [
    {"id": 1, "name": "شبكات الحاسوب", "tag": "شبكات_الحاسوب"},
    {"id": 2, "name": "مبادئ قواعد البيانات", "tag": "قواعد_بيانات"},
]
GUEST = {"student": None, "limit": 2, "used": 0, "remaining": 2}


def run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.cache, self.calls = {}, []

    async def rpc(self, name, params=None):
        self.calls.append(name)
        if name == "ai_cache_get":
            return self.cache.get(params["p_key"])
        if name == "ai_cache_put":
            self.cache[params["p_key"]] = params["p_value"]
        return None


class Gemini:
    """بديل لاستدعاء Gemini يعدّ المرات ويعيد ردًا ثابتًا."""
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def __call__(self, *a, **kw):
        self.calls += 1
        return self.reply


class FreeAnswersTests(unittest.TestCase):
    def setUp(self):
        assistant._mem_cache.clear()
        self.env = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_chip_questions_are_answered_locally(self):
        for q in ["كيف أربط رقمي الأكاديمي؟", "أين أجد الملخصات والنماذج؟", "كيف أتصفح الملفات والاختبارات؟",
                  "شكرا لك", "السلام عليكم", "هل أقدر أرسل صور؟", "كيف تشتغل الاختبارات؟", "كم رصيد المساعد اليومي؟"]:
            _, _, matched = answer_local_intent(q, GUEST, NOW)
            self.assertTrue(matched, q)

    def test_complaint_with_unlink_still_goes_to_ticket(self):
        _, ticket, matched = answer_local_intent("لدي مشكلة وأريد فك ارتباط رقمي", GUEST, NOW)
        self.assertTrue(matched and ticket)

    def test_local_answer_never_calls_gemini_and_is_not_counted(self):
        g = Gemini({"answer": "x"})
        with mock.patch.object(assistant, "_call_gemini_json", g):
            r = run(ask_assistant("شكرا", GUEST, NOW))
        self.assertEqual(g.calls, 0)
        self.assertFalse(r["counted"] or r["used_ai"])

    def test_unmatched_calls_gemini_once_then_cache_serves_everyone(self):
        g, st = Gemini({"answer": "الجواب", "needs_ticket": False}), FakeStore()
        with mock.patch.object(assistant, "_call_gemini_json", g):
            r1 = run(ask_assistant("ما هو عاصمة فرنسا؟", GUEST, NOW, store=st))
            assistant._mem_cache.clear()   # عملية/نسخة أخرى: يجب أن يخدمه الكاش الدائم
            r2 = run(ask_assistant("ما هو عاصمة   فرنسا", GUEST, NOW, store=st))
        self.assertEqual(g.calls, 1)
        self.assertTrue(r1["counted"])
        self.assertFalse(r2["counted"])
        self.assertEqual(r2["answer"], "الجواب")

    def test_blocked_when_no_balance_and_no_cache(self):
        g = Gemini({"answer": "x"})
        with mock.patch.object(assistant, "_call_gemini_json", g):
            r = run(ask_assistant("ما هو عاصمة فرنسا؟", GUEST, NOW, store=FakeStore(), allow_ai=False))
        self.assertTrue(r["blocked"])
        self.assertEqual(g.calls, 0)

    def test_cached_answer_served_even_without_balance(self):
        g, st = Gemini({"answer": "الجواب", "needs_ticket": False}), FakeStore()
        with mock.patch.object(assistant, "_call_gemini_json", g):
            run(ask_assistant("ما هو عاصمة فرنسا؟", GUEST, NOW, store=st))
            r = run(ask_assistant("ما هو عاصمة فرنسا؟", GUEST, NOW, store=st, allow_ai=False))
        self.assertFalse(r["blocked"])
        self.assertFalse(r["counted"])

    def test_student_and_guest_do_not_share_cache(self):
        g, st = Gemini({"answer": "الجواب"}), FakeStore()
        student = {**GUEST, "student": {"academic_no": "1", "full_name": "x", "theory_group_id": "A", "practical_group_id": "B"}}
        with mock.patch.object(assistant, "_call_gemini_json", g):
            run(ask_assistant("ما هو عاصمة فرنسا؟", GUEST, NOW, store=st))
            run(ask_assistant("ما هو عاصمة فرنسا؟", student, NOW, store=st))
        self.assertEqual(g.calls, 2)


class FileSearchTests(unittest.TestCase):
    FILES = [
        {"id": 1, "subject_id": 1, "subject": "شبكات الحاسوب", "part": "theory", "content_type": "summary", "numbers": [9], "label_kind": "module", "description": "ملخص التشفير"},
        {"id": 2, "subject_id": 1, "subject": "شبكات الحاسوب", "part": "theory", "content_type": "lecture", "numbers": [1], "label_kind": "lecture"},
        {"id": 3, "subject_id": 2, "subject": "مبادئ قواعد البيانات", "part": "theory", "content_type": "lecture", "numbers": [3], "label_kind": "lecture", "description": "التطبيع normalization"},
    ]

    def setUp(self):
        assistant._mem_cache.clear()
        self.env = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"})
        self.env.start()
        self.rpc_calls = []

        async def rpc(name, params=None):
            self.rpc_calls.append(params)
            return [f for f in self.FILES if not params.get("p_subject") or f["subject_id"] == params["p_subject"]]

        self.rpc = rpc

    def tearDown(self):
        self.env.stop()

    def search(self, q, g):
        with mock.patch.object(assistant, "_call_gemini_json", g):
            return run(smart_search_files(self.rpc, 5, q, SUBJECTS, store=FakeStore()))

    def test_clear_query_uses_keywords_only(self):
        g = Gemini({})
        ranked, crit, used_ai = self.search("ملخص شبكات Module 9", g)
        self.assertEqual(ranked[0]["id"], 1)
        self.assertFalse(used_ai)
        self.assertEqual(g.calls, 0)

    def test_keyword_hit_in_description_needs_no_ai(self):
        g = Gemini({})
        ranked, _, used_ai = self.search("التطبيع", g)
        self.assertEqual(ranked[0]["id"], 3)
        self.assertEqual(g.calls, 0)
        self.assertFalse(used_ai)

    def test_gemini_only_when_keywords_find_nothing(self):
        g = Gemini({"subject_id": 2, "part": None, "content_type": "lecture", "exam_kind": None, "numbers": [3], "keywords": []})
        ranked, crit, used_ai = self.search("ابغى شي عن الجداول العلائقية", g)
        self.assertEqual(g.calls, 1)
        self.assertTrue(used_ai)
        self.assertEqual(ranked[0]["id"], 3)

    def test_ai_failure_falls_back_to_local_results(self):
        g = Gemini(None)
        ranked, _, _ = self.search("شي غريب جدا", g)
        self.assertIsInstance(ranked, list)



    def test_keyword_search_raises_candidate_limit(self):
        self.search("التطبيع", Gemini({}))
        self.assertEqual(self.rpc_calls[0].get("p_limit"), assistant.KEYWORD_CANDIDATE_LIMIT)

    def test_structured_only_search_keeps_default_limit(self):
        self.search("ملخص شبكات 9", Gemini({}))
        self.assertNotIn("p_limit", self.rpc_calls[0])

    def test_falls_back_when_db_lacks_p_limit(self):
        calls = []

        async def rpc(name, params=None):
            calls.append(dict(params))
            if "p_limit" in params:
                raise RuntimeError("function search_files_smart(p_limit) does not exist")
            return list(self.FILES)

        with mock.patch.object(assistant, "_call_gemini_json", Gemini({})):
            ranked, _, _ = run(smart_search_files(rpc, 5, "التطبيع", SUBJECTS, store=FakeStore()))
        self.assertEqual(ranked[0]["id"], 3)
        self.assertEqual(len(calls), 2)
        self.assertNotIn("p_limit", calls[1])

    def test_past_questions_map_to_exam_without_noise_keywords(self):
        c = assistant.interpret_file_query_local("اريد اسئلة سنوات سابقة", SUBJECTS)
        self.assertEqual(c["content_type"], "exam")
        self.assertEqual(c["keywords"], [])
        c = assistant.interpret_file_query_local("اختبار نصفي شبكات", SUBJECTS)
        self.assertEqual(c["keywords"], [])

    def test_dead_async_interpreter_removed(self):
        self.assertFalse(hasattr(assistant, "interpret_file_query"))

    def test_personal_data_never_cached_or_returned_to_other_students(self):
        # إذا أعاد Gemini بالخطأ نصًا يحوي رقمًا أكاديميًا أو بيانات طالب، لا يُحفظ في الكاش المشترك
        g, st = Gemini({"answer": "الطالب: أحمد | الرقم الأكاديمي: 20260099", "needs_ticket": False}), FakeStore()
        with mock.patch.object(assistant, "_call_gemini_json", g):
            run(ask_assistant("سؤال عام غريب", GUEST, NOW, store=st))
            self.assertEqual(st.cache, {})

    def test_guide_faq_questions_answered_locally(self):
        for q in [
            "لماذا لا يظهر الواجب الذي سلمته في البوت؟",
            "أرسلت ملف الواجب لكن لم تصلني رسالة تأكيد التسليم",
            "كيف أتأكد أن ملفي وصل للدكتور؟",
            "ما هو قسم شارك؟",
        ]:
            _, _, matched = answer_local_intent(q, GUEST, NOW)
            self.assertTrue(matched, q)


if __name__ == "__main__":
    unittest.main()

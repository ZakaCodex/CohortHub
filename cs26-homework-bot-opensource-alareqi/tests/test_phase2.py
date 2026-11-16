import asyncio
import time
import unittest
from datetime import datetime, timedelta, timezone

from core.assignments import can_submit, student_status, validate_assignment_meta
from core.files import build_caption, file_title, hashtag, validate_file_meta
from core.messages import (assignment_new, assignment_reminder, humanize_remaining, render_notification,
                           submission_header)
from core.ratelimit import TokenBucket
from core.timeutil import parse_sanaa

UTC = timezone.utc


class FileTests(unittest.TestCase):
    def ok(self, **kw):
        base = {"subject_id": 1, "part": "theory", "content_type": "lecture"}
        base.update(kw)
        clean, err = validate_file_meta(base)
        self.assertIsNone(err, err)
        return clean

    def bad(self, expected, **kw):
        base = {"subject_id": 1, "part": "theory", "content_type": "lecture"}
        base.update(kw)
        clean, err = validate_file_meta(base)
        self.assertIsNone(clean)
        self.assertEqual(err, expected)

    def test_hashtag(self):
        self.assertEqual(hashtag("د. أحمد المنصوري"), "د_أحمد_المنصوري")
        self.assertEqual(hashtag("أمنية_معلومات"), "أمنية_معلومات")
        self.assertEqual(hashtag("  شبكات الحاسوب  "), "شبكات_الحاسوب")

    def test_required_numbers(self):
        for t in ("summary", "exercises", "translation"):
            self.bad("number_required", content_type=t)
        self.ok(content_type="lecture")           # المحاضرة بلا رقم مسموحة
        self.ok(content_type="summary", numbers=[3], label_kind="module")

    def test_exam_needs_kind_and_others_drop_it(self):
        self.bad("exam_kind_required", content_type="exam")
        self.assertEqual(self.ok(content_type="exam", exam_kind="final")["exam_kind"], "final")
        self.assertNotIn("exam_kind", self.ok(content_type="lecture", exam_kind="final"))

    def test_ranges_lectures_12_others_20(self):
        self.bad("number_out_of_range", numbers=[13], label_kind="lecture")
        self.assertEqual(self.ok(numbers=[12], label_kind="lecture")["numbers"], [12])
        self.assertEqual(self.ok(numbers=[12], label_kind="lecture_en")["numbers"], [12])
        self.bad("number_out_of_range", numbers=[13], label_kind="lecture_en")
        self.assertEqual(self.ok(numbers=[20], label_kind="module")["numbers"], [20])
        self.bad("number_out_of_range", numbers=[21], label_kind="chapter")
        self.bad("number_out_of_range", numbers=[0], label_kind="lab")
        self.bad("label_required", numbers=[1])

    def test_numbers_dedup_sorted(self):
        self.assertEqual(self.ok(numbers=[13, 9, 9], label_kind="module")["numbers"], [9, 13])

    def test_doctor_id_optional_validation(self):
        self.assertEqual(self.ok(part="practical", doctor_id=3)["doctor_id"], 3)
        self.assertNotIn("doctor_id", self.ok(part="theory"))
        self.bad("invalid_doctor", doctor_id="abc")
        self.bad("invalid_doctor", doctor_id=0)

    def test_invalid_basics(self):
        self.bad("invalid_part", part="x")
        self.bad("invalid_type", content_type="x")
        self.bad("invalid_subject", subject_id="abc")
        self.bad("description_too_long", description="ا" * 301)

    def test_caption_matches_agreed_format(self):
        meta = self.ok(content_type="summary", numbers=[9, 13], label_kind="module", description="مهم للنصفي")
        cap = build_caption(meta, "شبكات_الحاسوب", "د. خالد الهاشمي")
        lines = cap.split("\n")
        self.assertEqual(lines[0], "🔹 نـوع المحتوى : #ملخص")
        self.assertEqual(lines[1], "🔹 اسـم المقرر : #شبكات_الحاسوب")
        self.assertEqual(lines[2], "🔹 رقم الـ Module: #Module_9 #Module_13")
        self.assertEqual(lines[3], "🔹 مُدرس المقرر : #د_خالد_الهاشمي")
        self.assertEqual(lines[4], "🔹 نوع المحاضرة : #نظري")
        self.assertTrue(cap.endswith("\n\nمهم للنصفي"))

    def test_caption_exam_and_practical(self):
        meta = self.ok(part="practical", content_type="exam", exam_kind="midterm")
        cap = build_caption(meta, "قواعد_بيانات", None)
        self.assertIn("#نموذج", cap)
        self.assertIn("#عملي", cap)
        self.assertIn("#نصفي", cap)
        self.assertNotIn("رقم", cap)
        self.assertNotIn("مُدرس", cap)

    def test_title_shows_only_what_was_set(self):
        self.assertEqual(file_title({"content_type": "summary", "numbers": [9, 13], "label_kind": "module"}), "ملخص · Module 9، 13")
        self.assertEqual(file_title({"content_type": "exam", "exam_kind": "final", "numbers": []}), "نموذج نهائي")
        self.assertEqual(file_title({"content_type": "lecture", "numbers": [3], "label_kind": "lecture"}), "محاضرة · المحاضرة 3")


class AssignmentTests(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    def a(self, **kw):
        base = {"due_at": (self.NOW + timedelta(hours=5)).isoformat(),
                "closes_at": (self.NOW + timedelta(hours=17)).isoformat(), "status": "open", "submitted_at": None}
        base.update(kw)
        return base

    def test_statuses(self):
        self.assertEqual(student_status(self.NOW, self.a()), "open")
        self.assertEqual(student_status(self.NOW + timedelta(hours=6), self.a()), "late")
        self.assertEqual(student_status(self.NOW + timedelta(hours=18), self.a()), "missed")
        self.assertEqual(student_status(self.NOW, self.a(status="closed")), "missed")
        self.assertEqual(student_status(self.NOW + timedelta(hours=30), self.a(submitted_at=self.NOW.isoformat())), "submitted")

    def test_can_submit_until_internal_close(self):
        self.assertTrue(can_submit(self.NOW + timedelta(hours=6), self.a()))
        self.assertFalse(can_submit(self.NOW + timedelta(hours=18), self.a()))
        self.assertFalse(can_submit(self.NOW, self.a(status="closed")))

    def test_validate(self):
        ok, err = validate_assignment_meta({"subject_id": 2, "part": "practical", "group": "G4",
                                            "due_local": "2026-10-25 20:00", "description": " حل التمارين "}, self.NOW)
        self.assertIsNone(err)
        self.assertEqual(ok["due_at"], "2026-10-25T17:00:00+00:00")   # صنعاء = UTC+3
        self.assertEqual(ok["description"], "حل التمارين")

    def test_validate_allow_text_defaults_to_false(self):
        base = {"subject_id": 2, "part": "theory", "group": "CS2_G1", "due_local": "2026-10-25 20:00"}
        clean, _ = validate_assignment_meta(base, self.NOW)
        self.assertNotIn("allow_text", clean)                      # الافتراضي: ملفات فقط
        clean, _ = validate_assignment_meta({**base, "allow_text": True}, self.NOW)
        self.assertIs(clean["allow_text"], True)
        clean, _ = validate_assignment_meta({**base, "allow_text": "true"}, self.NOW)   # لا نقبل إلا boolean صريحًا
        self.assertNotIn("allow_text", clean)

    def test_validate_errors(self):
        base = {"subject_id": 2, "part": "theory", "group": "CS2_G1", "due_local": "2026-10-25 20:00"}
        for patch, code in [({"group": "G1"}, "invalid_group"), ({"part": "practical", "group": "CS2_G1"}, "invalid_group"),
                            ({"group": "CS2_G4"}, "invalid_group"), ({"due_local": "garbage"}, "invalid_due"),
                            ({"due_local": "2026-10-21 15:01"}, "due_in_past"), ({"due_local": "2028-01-01 10:00"}, "due_too_far"),
                            ({"subject_id": "x"}, "invalid_subject"), ({"description": "x" * 801}, "description_too_long")]:
            clean, err = validate_assignment_meta({**base, **patch}, self.NOW)
            self.assertEqual((clean, err), (None, code), patch)


class MessageTests(unittest.TestCase):
    P = {"assignment_id": 7, "number": 3, "subject": "شبكات الحاسوب", "part": "theory", "group": "CS2_G1",
         "description": "حل <تمارين> الموديولات", "due_at": "2026-10-25T17:00:00+00:00", "has_attachment": True}

    def test_new_message_has_all_agreed_fields_and_escapes(self):
        t = assignment_new(self.P)
        for part in ("تم نشر واجب جديد", "شبكات الحاسوب", "CS2_G1", "رقم الواجب: 3", "آخر موعد", "8:00 م", "&lt;تمارين&gt;", "مع مرفق"):
            self.assertIn(part, t)

    def test_no_grace_leak_anywhere(self):
        for text in (assignment_new(self.P), assignment_reminder(self.P, datetime(2026, 10, 25, 14, 0, tzinfo=UTC))):
            for word in ("مهلة", "12 ساعة", "إغلاق آلي"):
                self.assertNotIn(word, text)

    def test_remaining(self):
        self.assertEqual(humanize_remaining(timedelta(hours=3)), "3 ساعات")
        self.assertEqual(humanize_remaining(timedelta(hours=2, minutes=5)), "ساعتان")
        self.assertEqual(humanize_remaining(timedelta(minutes=40)), "40 دقيقة")
        self.assertEqual(humanize_remaining(timedelta(hours=23, minutes=50)), "24 ساعة")

    def test_render_notification(self):
        title, body = render_notification("assignment_reminder", self.P, datetime(2026, 10, 25, 14, 0, tzinfo=UTC))
        self.assertEqual(title, "تذكير بواجب")
        self.assertIn("3 ساعات", body)
        self.assertEqual(render_notification("manual", {"title": "اجتماع", "body": "غدًا"}, datetime.now(UTC)), ("اجتماع", "غدًا"))

    def test_submission_header(self):
        pkg = {"is_late": True, "revision": 2, "files": [1, 2],
               "student": {"no": "20260017", "name": "منى أحمد", "practical": "G2", "theory": "CS2_G1"},
               "assignment": {"number": 3, "part": "practical", "grp": "G2", "subject": "قواعد البيانات"}}
        h = submission_header(pkg)
        for part in ("تسليم واجب 3 — متأخر", "تسليم معدَّل", "منى أحمد", "20260017", "G2 عملي", "قواعد البيانات", "عدد الملفات: 2"):
            self.assertIn(part, h)


class BucketTests(unittest.TestCase):
    def test_rate_is_bounded(self):
        async def run():
            b = TokenBucket(rate=50, burst=5)
            t0 = time.monotonic()
            await asyncio.gather(*[b.acquire() for _ in range(25)])
            return time.monotonic() - t0
        elapsed = asyncio.run(run())
        self.assertGreater(elapsed, 0.3)    # 20 رمزًا بعد الدفعة الأولى ÷ 50/ث ≈ 0.4ث
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()

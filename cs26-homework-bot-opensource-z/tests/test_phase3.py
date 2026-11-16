"""اختبارات المرحلة 3:
- الباركود (Code 128 PNG + SVG)
- الحضور النظري (التحقق، مفاتيح أجهزة المسح Open Ticket Scanner، تصدير Excel م1–م12)
- التظلمات (فتح دورة، التحقق من السبب، التجميع الموحد للمندوب)
- المساعد الذكي والبحث الذكي في الملفات والبلاغات
- تدفقات البوت وعامل الإرسال (Worker) للمرحلة 3."""
from __future__ import annotations

import asyncio
import io
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from openpyxl import load_workbook

from tests import stubs

stubs.install()

from bot import phase3  # noqa: E402
from bot.worker import Worker  # noqa: E402
from core.assistant import (
    answer_from_guide_and_data_local,
    ask_assistant,
    interpret_file_query_local,
    rank_files,
    validate_ticket,
    validate_ticket_reply,
)
from core.attendance import (
    _build_xlsx_minimal,
    build_attendance_xlsx,
    extract_scanned_academic_no,
    generate_device_token,
    hash_device_token,
    validate_correction,
    validate_session_open,
)
from core.barcode import _pure_png_barcode, encode_code128, render_barcode_png, render_barcode_svg
from core.grievances import format_compiled_grievances, validate_cycle_open, validate_grievance_reason
from core.messages import grievance_open, render_notification, ticket_new, ticket_reply
from tests.test_bot_flows import FakeBot, FakeStore, FakeSvc, run

UTC = timezone.utc


class FakeBotWithPhoto(FakeBot):
    async def send_photo(self, **kw):
        return self._next("send_photo", **kw)


class BarcodeTests(unittest.TestCase):
    def test_encode_code128_valid_and_deterministic(self):
        bits = encode_code128("20260017")
        self.assertTrue(set(bits) <= {"0", "1"})
        self.assertTrue(bits.startswith("0" * 10 + "11010010000"))  # quiet zone + Start B (104)
        self.assertTrue(bits.endswith("1100011101011" + "0" * 10))    # Stop (106) + quiet zone
        with self.assertRaises(ValueError):
            encode_code128("")
        with self.assertRaises(ValueError):
            encode_code128("عربي")

    def test_svg_and_png_generation(self):
        svg = render_barcode_svg("20260017", "سارة محمد العلي", "CS2_G1")
        self.assertIn("<svg", svg)
        self.assertIn("20260017 · CS2_G1", svg)
        self.assertIn("سارة محمد العلي", svg)

        png = render_barcode_png("20260017", "سارة محمد العلي", "CS2_G1")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 500)

        pure = _pure_png_barcode("20260017", "CS2_G1")
        self.assertTrue(pure.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(pure), 300)


class AttendanceCoreTests(unittest.TestCase):
    def test_validate_session_open(self):
        clean, err = validate_session_open({"subject_id": 2, "group": "1", "lecture_no": 12, "delegate_absent": True})
        self.assertIsNone(err)
        self.assertEqual(clean, {"subject_id": 2, "group": "CS2_G1", "lecture_no": 12, "delegate_absent": True})

        for bad, code in [
            ({"subject_id": "x", "group": "CS2_G1", "lecture_no": 1}, "invalid_subject"),
            ({"subject_id": 1, "group": "G5", "lecture_no": 1}, "invalid_group"),
            ({"subject_id": 1, "group": "CS2_G1", "lecture_no": 0}, "invalid_lecture"),
            ({"subject_id": 1, "group": "CS2_G1", "lecture_no": 13}, "invalid_lecture"),
        ]:
            c, e = validate_session_open(bad)
            self.assertEqual((c, e), (None, code))

    def test_validate_correction(self):
        clean, err = validate_correction({
            "subject_id": 1, "group": "CS2_G2", "lecture_no": 5,
            "student_no": "٢٠٢٦٠٠١٧", "status": "present", "reason": "  عذر طبي  ",
        })
        self.assertIsNone(err)
        self.assertEqual(clean["student_no"], "20260017")
        self.assertEqual(clean["reason"], "عذر طبي")

        self.assertEqual(
            validate_correction({"subject_id": 1, "group": "CS2_G1", "lecture_no": 3, "student_no": "20260017", "status": "present", "reason": "א"})[1],
            "reason_required",
        )

    def test_device_token_and_scanner_extract(self):
        raw, digest, prefix = generate_device_token()
        self.assertTrue(raw.startswith("ots_"))
        self.assertEqual(hash_device_token(raw), digest)
        self.assertEqual(raw[:10], prefix)

        self.assertEqual(extract_scanned_academic_no("20260017"), "20260017")
        self.assertEqual(extract_scanned_academic_no("٢٠٢٦٠٠١٧"), "20260017")
        self.assertEqual(extract_scanned_academic_no('{"ticket": "20260017"}'), "20260017")
        self.assertEqual(extract_scanned_academic_no("https://scanner.app/check?code=20260099"), "20260099")
        self.assertIsNone(extract_scanned_academic_no("invalid"))

    def test_build_attendance_xlsx(self):
        matrix = {
            "subject": {"id": 1, "name": "شبكات الحاسوب"},
            "group": "CS2_G1",
            "students": [
                {"academic_no": "20260017", "full_name": "منى أحمد", "lectures": [1, 1, 0, 1] + [None] * 8},
                {"academic_no": "20260021", "full_name": "خالد سالم", "lectures": [0, 0, 0, 0] + [None] * 8},
            ],
        }
        xlsx = build_attendance_xlsx(matrix)
        wb = load_workbook(io.BytesIO(xlsx))
        ws = wb.active
        self.assertTrue(ws.sheet_view.rightToLeft)
        self.assertEqual(ws.cell(1, 1).value, "الرقم الأكاديمي")
        self.assertEqual(ws.cell(1, 3).value, "م1")
        self.assertEqual(ws.cell(1, 14).value, "م12")
        self.assertEqual(ws.cell(2, 1).value, "20260017")
        self.assertEqual(ws.cell(2, 15).value, 3)     # الحضور
        self.assertEqual(ws.cell(2, 16).value, 1)     # الغياب
        self.assertEqual(ws.cell(2, 17).value, 75.0)  # النسبة

        minimal = _build_xlsx_minimal("اختبار", [["الرقم", "الاسم"], ["20260017", "منى"]])
        wb2 = load_workbook(io.BytesIO(minimal))
        self.assertEqual(wb2.active.cell(2, 1).value, "20260017")


class GrievanceTests(unittest.TestCase):
    NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    def test_validate_cycle_open(self):
        clean, err = validate_cycle_open({
            "subject_id": 1, "part": "practical", "theory_group": "CS2_G1",
            "practical_groups": ["1", "G2"], "closes_local": "2026-10-25 20:00", "title": "درجات العملي",
        }, self.NOW)
        self.assertIsNone(err)
        self.assertEqual(clean["practical_groups"], ["G1", "G2"])
        self.assertEqual(clean["closes_at"], "2026-10-25T17:00:00+00:00")

        clean_all, err2 = validate_cycle_open({
            "subject_id": 2, "part": "theory", "theory_group": "ALL", "closes_local": "2026-10-25 20:00",
        }, self.NOW)
        self.assertIsNone(err2)
        self.assertIsNone(clean_all["theory_group"])

    def test_validate_reason_and_format_compiled(self):
        self.assertEqual(validate_grievance_reason("  خطأ في جمع الدرجات  "), ("خطأ في جمع الدرجات", None))
        self.assertEqual(validate_grievance_reason("أ")[1], "reason_too_short")

        pkg = {
            "subject": "شبكات الحاسوب",
            "part": "theory",
            "theory_group_id": "CS2_G1",
            "title": "النصفي",
            "closes_at": "2026-10-25T17:00:00+00:00",
            "items": [
                {"name": "منى أحمد", "no": "20260017", "group": "CS2_G1", "reason": "السؤال الثاني لم يُصحح"},
            ],
        }
        txt = format_compiled_grievances(pkg)
        for piece in ("شبكات الحاسوب", "منى أحمد", "20260017", "CS2_G1", "السؤال الثاني لم يُصحح"):
            self.assertIn(piece, txt)


class AssistantAndSearchTests(unittest.TestCase):
    SUBJECTS = [
        {"id": 1, "name": "شبكات الحاسوب", "tag": "شبكات_الحاسوب"},
        {"id": 2, "name": "مبادئ قواعد البيانات", "tag": "قواعد_بيانات"},
        {"id": 3, "name": "البرمجة الهدفية", "tag": "برمجة_هدفية"},
    ]

    def test_interpret_file_query_local(self):
        c1 = interpret_file_query_local("أريد ملخص الشبكات Module 9 و 13", self.SUBJECTS)
        self.assertEqual(c1["subject_id"], 1)
        self.assertEqual(c1["content_type"], "summary")
        self.assertEqual(c1["numbers"], [9, 13])

        c2 = interpret_file_query_local("نموذج اختبار نصفي قواعد البيانات عملي", self.SUBJECTS)
        self.assertEqual(c2["subject_id"], 2)
        self.assertEqual(c2["content_type"], "exam")
        self.assertEqual(c2["exam_kind"], "midterm")
        self.assertEqual(c2["part"], "practical")

        c3 = interpret_file_query_local("المحاضرة الثالثة OOP نظري", self.SUBJECTS)
        self.assertEqual(c3["subject_id"], 3)
        self.assertEqual(c3["content_type"], "lecture")
        self.assertEqual(c3["numbers"], [3])

    def test_rank_files(self):
        candidates = [
            {"id": 1, "subject_id": 1, "subject": "شبكات الحاسوب", "part": "theory", "content_type": "lecture", "numbers": [1], "label_kind": "lecture"},
            {"id": 2, "subject_id": 1, "subject": "شبكات الحاسوب", "part": "theory", "content_type": "summary", "numbers": [9, 13], "label_kind": "module", "description": "مهم"},
        ]
        criteria = interpret_file_query_local("ملخص شبكات 9", self.SUBJECTS)
        ranked = rank_files(candidates, criteria)
        self.assertEqual(ranked[0]["id"], 2)

    def test_assistant_answers_from_student_data_and_guide(self):
        now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
        ctx = {
            "student": {"academic_no": "20260017", "full_name": "منى أحمد", "theory_group_id": "CS2_G1", "practical_group_id": "G2"},
            "assignments": [
                {"id": 11, "number": 3, "subject": "شبكات الحاسوب", "part": "theory", "grp": "CS2_G1",
                 "due_at": (now + timedelta(hours=5)).isoformat(), "closes_at": (now + timedelta(hours=17)).isoformat(),
                 "status": "open", "submitted_at": None},
            ],
            "attendance": [
                {"subject_id": 1, "subject": "شبكات الحاسوب", "present": 4, "absent": 1, "held": 5},
            ],
            "grievances": [
                {"id": 1, "subject": "شبكات الحاسوب", "part": "theory", "is_open": True, "my_grievance": None},
            ],
        }
        ans_asg, _ = answer_from_guide_and_data_local("ما هي واجباتي المفتوحة؟", ctx, now)
        self.assertIn("شبكات الحاسوب", ans_asg)
        self.assertIn("واجب 3", ans_asg)

        ans_att, _ = answer_from_guide_and_data_local("كم نسبة حضوري؟", ctx, now)
        self.assertIn("80%", ans_att)

        ans_bar, _ = answer_from_guide_and_data_local("نسيت بطاقتي الجامعية كيف اطلع الباركود؟", ctx, now)
        self.assertIn("Code 128", ans_bar)

        ans_bug, needs_tkt = answer_from_guide_and_data_local("لدي شكوى وعطل في فك ارتباط رقمي", ctx, now)
        self.assertTrue(needs_tkt)
        self.assertIn("بلاغ", ans_bug)

        res = run(ask_assistant("ما هي مجموعتي؟", ctx, now))
        self.assertIn("CS2_G1", res["answer"])

    def test_ticket_validation_and_notifications(self):
        self.assertEqual(validate_ticket("bug", "  التطبيق لا يفتح الكاميرا  "), (("bug", "التطبيق لا يفتح الكاميرا"), None))
        self.assertEqual(validate_ticket("other", "نص طويل")[1], "invalid_category")
        self.assertEqual(validate_ticket_reply("تم الحل"), ("تم الحل", None))

        now = datetime.now(UTC)
        self.assertEqual(render_notification("grievance_open", {"subject": "شبكات", "part": "theory"}, now)[0], "فتح دورة تظلمات")
        self.assertEqual(render_notification("ticket_reply", {"category": "bug", "reply": "تم الإصلاح"}, now), ("رد على بلاغ عطل", "تم الإصلاح"))
        self.assertIn("تم فتح دورة تظلمات", grievance_open({"subject": "شبكات", "part": "theory", "group": "CS2_G1"}))
        self.assertIn("بلاغ عطل جديد #5", ticket_new({"ticket_id": 5, "category": "bug", "sender_name": "منى", "body": "عطل"}))
        self.assertIn("رد المطوّر", ticket_reply({"ticket_id": 5, "category": "bug", "reply": "تم إصلاحه"}))


class Phase3BotAndWorkerTests(unittest.TestCase):
    def test_worker_sends_barcode_grievance_ticket_and_excel(self):
        store = FakeStore({
            "attendance_matrix": {
                "status": "ok",
                "subject": {"id": 1, "name": "شبكات"},
                "group": "CS2_G1",
                "students": [{"academic_no": "20260017", "full_name": "منى", "lectures": [1] * 12}],
            }
        })
        bot = FakeBotWithPhoto()
        w = Worker(bot, FakeSvc(store))

        # 1) باركود
        r1 = run(w._send({"id": 1, "kind": "barcode_send", "chat_id": 77, "payload": {"academic_no": "20260017", "full_name": "منى", "theory_group_id": "CS2_G1", "practical_group_id": "G2"}}))
        self.assertEqual(r1["status"], "sent")
        self.assertEqual(bot.sent[-1][0], "send_photo")

        # 2) فتح تظلمات
        r2 = run(w._send({"id": 2, "kind": "grievance_open", "chat_id": 77, "payload": {"cycle_id": 9, "subject": "شبكات", "part": "theory", "group": "CS2_G1"}}))
        self.assertEqual(r2["status"], "sent")
        self.assertEqual(bot.sent[-1][1]["reply_markup"].rows[0][0].kw["callback_data"], "grv:go:9")

        # 3) إرسال ملف Excel للحضور
        r3 = run(w._send({"id": 3, "kind": "attendance_excel", "chat_id": 77, "payload": {"subject_id": 1, "group": "CS2_G1", "actor_id": 77}}))
        self.assertEqual(r3["status"], "sent")
        self.assertEqual(bot.sent[-1][0], "send_document")

    def test_bot_grievance_and_assistant_text_flows(self):
        store = FakeStore({
            "upsert_grievance": {"status": "ok", "id": 1, "updated": False},
            "assistant_context": {"remaining": 4, "limit": 5, "student": {"academic_no": "20260017", "full_name": "منى", "theory_group_id": "CS2_G1", "practical_group_id": "G2"}, "assignments": [], "attendance": [], "grievances": []},
            "record_assistant_exchange": {"status": "ok", "remaining": 3, "limit": 5},
            "create_ticket": {"status": "ok", "id": 42},
        })
        bot = FakeBotWithPhoto()
        context = NS(bot=bot, user_data={"await": "grv:3"}, application=NS(bot_data={"svc": FakeSvc(store)}))
        update = NS(effective_user=NS(id=77), effective_chat=NS(id=77), effective_message=NS(text="خطأ في رصد درجة العملي"))

        handled = run(phase3.handle_awaiting_text(update, context, "grv:3", {}))
        self.assertTrue(handled)
        self.assertIn("تم تسجيل تظلمك", bot.sent[-1][1]["text"])

        context.user_data["await"] = "ai_ask"
        update.effective_message.text = "ما هي مجموعتي؟"
        run(phase3.handle_awaiting_text(update, context, "ai_ask", {}))
        self.assertIn("CS2_G1", bot.sent[-1][1]["text"])

        context.user_data["await"] = "tkt_new:bug"
        update.effective_message.text = "لا أستطيع فتح صفحة الاختبارات"
        run(phase3.handle_awaiting_text(update, context, "tkt_new:bug", {}))
        self.assertIn("#42", bot.sent[-1][1]["text"])


if __name__ == "__main__":
    unittest.main()

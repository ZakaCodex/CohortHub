"""اختبارات المرحلة 4 (الأخيرة):
- التقارير اليومية والقوالب والتقرير الأسبوعي الآلي ومنع النقطة الفارغة.
- شارك (استفتاء 2–10 خيارات، مشاركات متعددة، إرسال الصور والملفات المستلمة للمشرف في البوت، وتصدير Excel).
- الاختبارات MCQ (قراءة قالب Excel والتحقق من الأعمدة والإجابات).
- شاشة الدكتور (تصدير Excel: 1 سلّم، 0 لم يسلّم لكل مجموعة منفردة).
- تدفقات البوت وعامل الإرسال (Worker) للمرحلة 4."""
from __future__ import annotations

import io
import unittest
from datetime import date, timedelta
from types import SimpleNamespace as NS

from openpyxl import Workbook, load_workbook

from tests import stubs

stubs.install()

from bot import phase4  # noqa: E402
from bot.worker import Worker  # noqa: E402
from core.doctor import build_doctor_assignment_xlsx, build_doctor_group_xlsx  # noqa: E402
from core.mcq import MCQ_EXCEL_COLUMNS, build_mcq_template_xlsx, parse_mcq_excel  # noqa: E402
from core.messages import render_notification, report_reminder, sharek_new  # noqa: E402
from core.reports import (  # noqa: E402
    STUDY_DAYS,
    clean_bullets,
    compile_weekly_report,
    study_week_start_date,
    validate_and_render_daily_report,
    weekly_report_days,
    weekly_report_number,
    validate_template_slots,
)
from core.sharek import build_sharek_xlsx, validate_sharek_campaign  # noqa: E402
from core.timeutil import now_utc  # noqa: E402
from tests.test_bot_flows import FakeBot, FakeStore, FakeSvc, run  # noqa: E402


class FakeBotWithPhoto(FakeBot):
    async def send_photo(self, **kw):
        self.sent.append(("send_photo", kw))
        return NS(message_id=len(self.sent) + 100)


class ReportsTests(unittest.TestCase):
    def test_thursday_included_in_study_days_and_week_starts_saturday(self):
        days = dict(STUDY_DAYS)
        self.assertIn(5, days)  # السبت
        self.assertIn(3, days)  # الخميس
        self.assertEqual(len(STUDY_DAYS), 6)
        # الثلاثاء 2026-09-08 يقع في الأسبوع الذي يبدأ السبت 2026-09-05
        self.assertEqual(study_week_start_date(date(2026, 9, 8)), date(2026, 9, 5))
        # الخميس 2026-09-10 يقع في نفس الأسبوع الذي يبدأ السبت 2026-09-05
        self.assertEqual(study_week_start_date(date(2026, 9, 10)), date(2026, 9, 5))

    def test_clean_bullets_prevents_empty_points(self):
        raw = "• تم أخذ رتبة المصفوفات\n•   \n-\n* موضوع المحددات\n   "
        self.assertEqual(clean_bullets(raw), ["تم أخذ رتبة المصفوفات", "موضوع المحددات"])

    def test_daily_report_matches_exact_agreed_format(self):
        slots = [
            {
                "mode": "practical_all",
                "subject": "جبر خطي",
                "doctor": "أ. منى فرج",
                "topics": "• تم أخذ رتبة المصفوفات\n• ",
            },
            {
                "mode": "theory",
                "subject": "البرمجة هدفية",
                "doctor": "د. أحمد المنصوري",
                "topics": "تم أخذ موضوع ال Inheritance",
            },
            {
                "mode": "practical_split",
                "items": [
                    {"group": "G2", "subject": "شبكات الحاسوب", "topics": "تم أخذ إعدادات السويتش"},
                    {"group": "G3", "subject": "مبادئ قواعد البيانات", "topics": "تم أخذ أوامر إنشاء وتعديل الجداول"},
                ],
            },
            {
                "mode": "theory",
                "subject": "شبكات الحاسوب",
                "doctor": "د. خالد الهاشمي",
                "topics": "تم اخذ module 9 & 13",
                "assignment": "حل تمارين ال modules",
            },
        ]
        note = "يوم الخميس اختبار مادة مبادئ قواعد البيانات عملي G1 G2 G3"
        clean, err = validate_and_render_daily_report("CS2_G1", "2026-09-08", slots, note)
        self.assertIsNone(err)
        txt = clean["rendered_text"]

        for expected in (
            "📌 تقرير يوم الثلاثاء 8 سبتمبر CS2_G1",
            "🕗 المحاضرة الأولى | جبر خطي _ عملي | أ. منى فرج",
            "• تم أخذ رتبة المصفوفات",
            "🕗 المحاضرة الثانية | البرمجة هدفية | د. أحمد المنصوري",
            "• تم أخذ موضوع ال Inheritance",
            "🕗 المحاضرة الثالثة:",
            "■ شبكات الحاسوب | G2 عملي :",
            "■ مبادئ قواعد البيانات | G3 عملي :",
            "🕗 المحاضرة الرابعة | شبكات الحاسوب | د. خالد الهاشمي",
            "• التكليف: حل تمارين ال modules",
            "📌 ملاحظة:\n• يوم الخميس اختبار مادة مبادئ قواعد البيانات عملي G1 G2 G3",
        ):
            self.assertIn(expected, txt)
        # التأكد من عدم وجود أي نقطة فارغة
        for line in txt.splitlines():
            self.assertNotEqual(line.strip(), "•")

    def test_template_validation_and_weekly_compilation_skips_empty_days(self):
        slots, err = validate_template_slots([
            {"mode": "theory", "subject_id": 1, "subject": "شبكات"},
            {"mode": "practical_split", "items": [{"group": "G1", "subject_id": 2}, {"group": "G2", "subject_id": 3}]},
        ])
        self.assertIsNone(err)
        self.assertEqual(len(slots), 2)
        self.assertEqual(validate_template_slots([{"mode": "invalid"}])[1], "invalid_slot_mode")

        wstart = date(2026, 9, 5)  # السبت
        weekly = compile_weekly_report("CS2_G1", wstart, [
            {"report_date": "2026-09-08", "rendered_text": "📌 تقرير يوم الثلاثاء 8 سبتمبر CS2_G1\n• محاضرة"},
        ])
        self.assertTrue(weekly.startswith("📚 الأسبوع 1 — CS2_G1"))
        self.assertNotIn("الحصاد", weekly)
        self.assertIn("تقرير يوم الثلاثاء 8 سبتمبر", weekly)
        self.assertNotIn("يوم فارغ", weekly)   # الأيام التي لم يُكتب فيها تقرير لا تظهر

    def test_weekly_report_writes_only_written_days_in_order(self):
        wstart = date(2026, 10, 24)   # السبت
        daily = [
            {"report_date": "2026-10-28", "rendered_text": "📌 تقرير الأربعاء\n• ج"},
            {"report_date": "2026-10-25", "rendered_text": "📌 تقرير الأحد\n• أ"},
        ]
        weekly = compile_weekly_report("CS2_G1", wstart, daily)
        self.assertLess(weekly.index("تقرير الأحد"), weekly.index("تقرير الأربعاء"))
        for bad in ("فارغ", "أيام بلا تقرير", "لم يحن", "السبت", "الاثنين", "الثلاثاء", "الخميس"):
            self.assertNotIn(bad, weekly)
        self.assertEqual(weekly.count("═════════════════════"), 1)   # فاصل واحد بين يومين فقط

    def test_weekly_number_counts_from_week_of_first_report(self):
        first = date(2026, 10, 7)                      # الأربعاء: أول تقرير → الأسبوع (السبت 3 أكتوبر) هو الأسبوع 1
        self.assertEqual(weekly_report_number(date(2026, 10, 3), first), 1)
        self.assertEqual(weekly_report_number(date(2026, 10, 10), first), 2)
        self.assertEqual(weekly_report_number(date(2026, 10, 24), first), 4)
        self.assertEqual(weekly_report_number(date(2026, 10, 24), None), 1)
        txt = compile_weekly_report("CS2_G2", date(2026, 10, 24),
                                    [{"report_date": "2026-10-25", "rendered_text": "📌 x\n• y"}], first_report_date=first)
        self.assertTrue(txt.startswith("📚 الأسبوع 4 — CS2_G2"))

    def test_weekly_report_days_counts_only_this_week_with_text(self):
        wstart = date(2026, 10, 24)
        daily = [
            {"report_date": "2026-10-25", "rendered_text": "x"},
            {"report_date": "2026-10-26", "rendered_text": "  "},      # نص فارغ لا يُعدّ
            {"report_date": "2026-11-01", "rendered_text": "next week"},   # أسبوع آخر
        ]
        self.assertEqual(weekly_report_days(wstart, daily), 1)
        self.assertEqual(weekly_report_days(wstart, []), 0)


class SharekTests(unittest.TestCase):
    def test_validate_sharek_poll_and_submission(self):
        clean, err = validate_sharek_campaign({
            "kind": "poll",
            "group": "CS2_G1",
            "title": "ما اليوم الأنسب؟",
            "options": "الاثنين\nالأربعاء\nالاثنين",  # يحذف المكرر
        })
        self.assertIsNone(err)
        self.assertEqual(clean["options"], ["الاثنين", "الأربعاء"])

        self.assertEqual(
            validate_sharek_campaign({"kind": "poll", "group": "ALL", "title": "سؤال", "options": ["واحد فقط"]})[1],
            "invalid_options_count",
        )
        self.assertEqual(
            validate_sharek_campaign({"kind": "poll", "group": "ALL", "title": "سؤال", "options": [f"خيار {i}" for i in range(11)]})[1],
            "invalid_options_count",
        )

        sub_clean, err2 = validate_sharek_campaign({"kind": "submission", "group": "ALL", "title": "أرسل ملخصك"})
        self.assertIsNone(err2)
        self.assertIsNone(sub_clean["group"])

    def test_sharek_title_keeps_blank_lines(self):
        raw = "السؤال الأول:\r\n\r\nأ) خيار   أول  \n\n\nب) خيار ثانٍ\n"
        clean, err = validate_sharek_campaign({"kind": "submission", "group": "ALL", "title": raw})
        self.assertIsNone(err)
        self.assertEqual(clean["title"], "السؤال الأول:\n\nأ) خيار أول\n\n\nب) خيار ثانٍ")

    def test_report_assignment_single_line_unchanged(self):
        slot = {"mode": "theory", "subject": "شبكات", "doctor": "د. س", "topics": "شرح الفصل 2",
                "assignment": "حل التمرين 3"}
        clean, err = validate_and_render_daily_report("CS2_G1", "2026-09-08", [slot], None)
        self.assertIsNone(err)
        self.assertIn("• التكليف: حل التمرين 3", clean["rendered_text"])
        self.assertEqual(clean["slots"][0]["assignment"], "حل التمرين 3")

    def test_report_assignment_multiple_points_and_questions(self):
        hw = "- حل التمرين 3\n\n• أجب عن الأسئلة 1 و2\n   مراجعة الفصل   الأول\n"
        theory = {"mode": "theory", "subject": "شبكات", "topics": "شرح", "assignment": hw}
        split = {"mode": "practical_split", "items": [
            {"group": "G1", "subject": "قواعد", "topics": "SQL", "assignment": "سؤال 1\nسؤال 2"},
            {"group": "G2", "subject": "قواعد", "topics": "SQL", "assignment": "سؤال واحد"}]}
        clean, err = validate_and_render_daily_report("CS2_G1", "2026-09-08", [theory, split], None)
        self.assertIsNone(err)
        txt = clean["rendered_text"]
        self.assertIn("• التكليف:\n   ◦ حل التمرين 3\n   ◦ أجب عن الأسئلة 1 و2\n   ◦ مراجعة الفصل الأول", txt)
        self.assertIn("• التكليف:\n   ◦ سؤال 1\n   ◦ سؤال 2", txt)
        self.assertIn("• التكليف: سؤال واحد", txt)
        self.assertEqual(clean["slots"][0]["assignment"], "حل التمرين 3\nأجب عن الأسئلة 1 و2\nمراجعة الفصل الأول")

    def test_report_without_assignment_has_no_assignment_line(self):
        slot = {"mode": "theory", "subject": "شبكات", "topics": "شرح", "assignment": "  \n  "}
        clean, _ = validate_and_render_daily_report("CS2_G1", "2026-09-08", [slot], None)
        self.assertNotIn("التكليف", clean["rendered_text"])
        self.assertIsNone(clean["slots"][0]["assignment"])

    def test_build_sharek_xlsx(self):
        poll_res = {
            "campaign": {"id": 3, "kind": "poll", "group": "CS2_G1"},
            "votes": [
                {"no": "20260017", "name": "منى", "theory": "CS2_G1", "practical": "G2", "option_label": "الأربعاء"},
                {"no": "20260021", "name": "خالد", "theory": "CS2_G1", "practical": "G1", "option_label": None},
            ],
        }
        wb = load_workbook(io.BytesIO(build_sharek_xlsx(poll_res)))
        ws = wb.active
        self.assertEqual(ws.cell(2, 5).value, "الأربعاء")
        self.assertEqual(ws.cell(2, 6).value, "شارك")
        self.assertEqual(ws.cell(3, 6).value, "لم يشارك")


class McqAndDoctorTests(unittest.TestCase):
    def test_mcq_template_and_parser(self):
        tpl = build_mcq_template_xlsx()
        questions, errors = parse_mcq_excel(tpl)
        self.assertEqual(errors, [])
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["correct_index"], 0)
        self.assertEqual(questions[1]["correct_index"], 1)

        # اختبار قبول رقم الخيار الصحيح (1-4) أيضاً
        wb = Workbook()
        ws = wb.active
        ws.append(MCQ_EXCEL_COLUMNS)
        ws.append(["سؤال تجريبي؟", "أ", "ب", "ج", "د", "3"])
        buf = io.BytesIO()
        wb.save(buf)
        qs2, err2 = parse_mcq_excel(buf.getvalue())
        self.assertEqual(err2, [])
        self.assertEqual(qs2[0]["correct_index"], 2)

    def test_doctor_assignment_xlsx_has_1_and_0(self):
        data = {
            "assignment": {"id": 11, "number": 3, "subject": "شبكات الحاسوب", "part": "theory", "group": "CS2_G1"},
            "students": [
                {"academic_no": "20260017", "full_name": "منى أحمد", "submitted": 1, "is_late": False, "received": True},
                {"academic_no": "20260021", "full_name": "خالد سالم", "submitted": 0, "is_late": False, "received": False},
            ],
        }
        wb = load_workbook(io.BytesIO(build_doctor_assignment_xlsx(data)))
        ws = wb.active
        self.assertTrue(ws.sheet_view.rightToLeft)
        self.assertEqual(ws.cell(2, 1).value, "20260017")
        self.assertEqual(ws.cell(2, 4).value, 1)  # 1 للذي سلّم
        self.assertEqual(ws.cell(3, 1).value, "20260021")
        self.assertEqual(ws.cell(3, 4).value, 0)  # 0 للذي لم يسلّم

    def test_doctor_group_xlsx_columns_and_marks(self):
        data = {
            "group": "CS2_G1",
            "assignments": [
                {"id": 1, "number": 1, "subject": "OOP", "part": "theory"},
                {"id": 2, "number": 2, "subject": "OOP", "part": "theory"},
                {"id": 3, "number": 3, "subject": "OOP", "part": "theory"},
            ],
            "students": [
                {"academic_no": "20260017", "full_name": "منى أحمد", "marks": [1, 1, 0]},
                {"academic_no": "20260021", "full_name": "خالد سالم", "marks": [0, 1]},   # أقصر من المتوقع: يُكمَّل بأصفار
            ],
        }
        ws = load_workbook(io.BytesIO(build_doctor_group_xlsx(data))).active
        self.assertTrue(ws.sheet_view.rightToLeft)
        self.assertEqual([c.value for c in ws[1]], ["الرقم الأكاديمي", "اسم الطالب", "واجب 1", "واجب 2", "واجب 3"])
        self.assertEqual([c.value for c in ws[2]], ["20260017", "منى أحمد", 1, 1, 0])
        self.assertEqual([c.value for c in ws[3]], ["20260021", "خالد سالم", 0, 1, 0])

    def test_doctor_group_xlsx_adds_subject_only_when_mixed(self):
        data = {"group": "G1", "assignments": [
            {"number": 1, "subject": "OOP"}, {"number": 1, "subject": "Networks"}], "students": []}
        ws = load_workbook(io.BytesIO(build_doctor_group_xlsx(data))).active
        self.assertEqual([c.value for c in ws[1]][2:], ["Networks - واجب 1", "OOP - واجب 1"][::-1])


class Phase4BotAndWorkerTests(unittest.TestCase):
    def test_worker_handles_sharek_reports_and_doctor_excel(self):
        store = FakeStore({
            "sharek_campaign_results": {
                "status": "ok",
                "campaign": {"id": 2, "kind": "submission", "title": "ملخصات قواعد البيانات", "group": "CS2_G1"},
                "votes": [{"no": "20260017", "name": "منى", "theory": "CS2_G1", "practical": "G2", "option_label": "نعم"}],
                "submissions": [
                    {"id": 1, "no": "20260017", "name": "منى", "theory": "CS2_G1", "practical": "G2", "item_kind": "photo", "file_id": "PH_1"},
                    {"id": 2, "no": "20260021", "name": "خالد", "theory": "CS2_G1", "practical": "G1", "item_kind": "document", "file_id": "DOC_1"},
                ],
            },
            "doctor_assignment_export": {
                "status": "ok",
                "assignment": {"id": 5, "number": 2, "subject": "OOP", "group": "CS2_G1"},
                "students": [{"academic_no": "20260017", "full_name": "منى", "submitted": 1}],
            },
        })
        bot = FakeBotWithPhoto()
        svc = FakeSvc(store)
        svc.settings = NS(web_app_url="https://app.example.com")
        w = Worker(bot, svc)

        # 1) إشعار شارك يوجه الطالب إلى الـ Mini App مباشرة (?view=sharek)
        r1 = run(w._send({"id": 1, "kind": "sharek_new", "chat_id": 77, "payload": {"campaign_id": 2, "kind": "poll", "group": "CS2_G1", "title": "سؤال؟", "options": ["نعم", "لا"]}}))
        self.assertEqual(r1["status"], "sent")
        btn_obj = bot.sent[-1][1]["reply_markup"].rows[0][0]
        self.assertIn("view=sharek", btn_obj.kw["web_app"].kw["url"])

        # 2) نشر تقرير يومي في topic المجموعة
        r2 = run(w._send({"id": 2, "kind": "report_post", "chat_id": -1001, "topic_id": 21, "payload": {"text": "📌 تقرير يوم الخميس"}}))
        self.assertEqual(r2["status"], "sent")
        self.assertEqual(bot.sent[-1][1]["message_thread_id"], 21)

        # 3) إرسال ملف Excel للدكتور (1/0)
        r3 = run(w._send({"id": 3, "kind": "doctor_excel", "chat_id": 99, "payload": {"assignment_id": 5, "actor_id": 99}}))
        self.assertEqual(r3["status"], "sent")
        self.assertEqual(bot.sent[-1][0], "send_document")

        # 4) إرسال ملف Excel لشارك
        r4 = run(w._send({"id": 4, "kind": "sharek_excel", "chat_id": 99, "payload": {"campaign_id": 2, "group": "CS2_G1", "actor_id": 99}}))
        self.assertEqual(r4["status"], "sent")
        self.assertEqual(bot.sent[-1][0], "send_document")

        # 5) إرسال الصور والملفات المستلمة في شارك إلى المشرف في البوت (sharek_items_view)
        r5 = run(w._send({"id": 5, "kind": "sharek_items_view", "chat_id": 99, "payload": {"campaign_id": 2, "group": "CS2_G1", "actor_id": 99}}))
        self.assertEqual(r5["status"], "sent")
        sent_methods = [m[0] for m in bot.sent[-3:]]
        self.assertIn("send_photo", sent_methods)
        self.assertIn("send_document", sent_methods)

    def test_worker_sends_doctor_group_excel(self):
        store = FakeStore({"doctor_group_export": {
            "status": "ok", "group": "CS2_G1",
            "assignments": [{"id": 5, "number": 1, "subject": "OOP", "part": "theory"}],
            "students": [{"academic_no": "20260017", "full_name": "منى", "marks": [1]}],
        }})
        bot = FakeBotWithPhoto()
        w = Worker(bot, FakeSvc(store))
        r = run(w._send({"id": 9, "kind": "doctor_group_excel", "chat_id": 99,
                         "payload": {"group": "CS2_G1", "doctor_id": None, "actor_id": 99}}))
        self.assertEqual(r["status"], "sent")
        self.assertEqual(bot.sent[-1][0], "send_document")

    def test_bot_sharek_multi_item_submission_flow(self):
        store = FakeStore({
            "add_sharek_item": {"status": "ok", "count": 2},
            "finish_sharek_submission": {"status": "ok", "count": 2},
        })
        bot = FakeBot()
        context = NS(bot=bot, user_data={"sh_sub": 4}, application=NS(bot_data={"svc": FakeSvc(store)}))
        upd = NS(effective_user=NS(id=77), effective_chat=NS(id=77), effective_message=NS(text="ملخص الفصل الأول"), callback_query=None)

        # البوت لا يستقبل النص؛ يوجّه الطالب لكتابته في التطبيق ولا يحفظ شيئًا
        self.assertTrue(run(phase4.on_sharek_text(upd, context)))
        self.assertIn("الصور والملفات", bot.sent[-1][1]["text"])
        self.assertEqual([c for c in store.calls if c[0] == "add_sharek_item"], [])

        self.assertTrue(run(phase4.on_sharek_media(upd, context, {"media_kind": "photo", "file_id": "P1", "name": None})))
        self.assertIn("استلمت المرفق", bot.sent[-1][1]["text"])

        run(phase4.on_cb(upd, context, ["sh", "fin", "4"], {"perms": [{"k": "sharek.participate", "s": "own"}]}))
        self.assertIsNone(context.user_data.get("sh_sub"))
        self.assertIn("تم إنهاء واعتماد مشاركتك", bot.sent[-1][1]["text"])

    def test_messages_and_notifications(self):
        now = now_utc()
        self.assertEqual(render_notification("sharek_new", {"kind": "poll", "title": "استطلاع"}, now), ("استفتاء جديد في شارك", "استطلاع"))
        self.assertEqual(render_notification("report_reminder", {"group": "CS2_G1"}, now)[0], "تذكير بالتقرير اليومي")
        self.assertIn("تذكير مسائي", report_reminder({"group": "CS2_G1", "date": "2026-10-22"}))
        self.assertIn("استفتاء جديد", sharek_new({"kind": "poll", "group": "CS2_G1", "title": "سؤال"}))


if __name__ == "__main__":
    unittest.main()

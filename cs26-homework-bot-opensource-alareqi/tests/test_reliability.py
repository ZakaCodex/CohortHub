"""اختبارات إصلاحات الموثوقية: حدود تيليجرام، قفل المستخدم، التسليم المتتبَّع، التقرير الأسبوعي، التسليم التلقائي."""
import asyncio
import datetime as dt
import unittest

from tests import stubs

stubs.install()

from telegram.error import BadRequest  # noqa: E402

from bot import assignments  # noqa: E402
from bot.locks import per_user  # noqa: E402
from bot.ui import say  # noqa: E402
from bot.worker import Worker  # noqa: E402
from core.text import fit_caption, split_text  # noqa: E402
from tests.test_bot_flows import PKG, FakeBot, FakeStore, FakeSvc, NS, ctx, run, upd  # noqa: E402


class TelegramLimitsTests(unittest.TestCase):
    def test_split_text_respects_limit_and_keeps_content(self):
        text = "\n".join(f"سطر {i} " + "ك" * 90 for i in range(300))
        parts = split_text(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4000 for p in parts))
        self.assertEqual("\n".join(parts), text)

    def test_split_text_single_huge_line(self):
        parts = split_text("كلمة " * 3000)
        self.assertTrue(all(len(p) <= 4000 for p in parts))

    def test_say_splits_and_puts_markup_on_last_part_only(self):
        bot = FakeBot()
        run(say(bot, 1, "\n".join("س" * 100 for _ in range(120)), markup="KB"))
        self.assertGreater(len(bot.sent), 1)
        self.assertTrue(all(len(kw["text"]) <= 4096 for _, kw in bot.sent))
        self.assertIsNone(bot.sent[0][1]["reply_markup"])
        self.assertEqual(bot.sent[-1][1]["reply_markup"], "KB")

    def test_caption_fits_1024(self):
        cap = fit_caption("سطر\n" * 600)
        self.assertLessEqual(len(cap), 1024)
        self.assertEqual(fit_caption("قصير"), "قصير")

    def test_long_weekly_report_is_posted_in_parts(self):
        bot, store = FakeBot(), FakeStore()
        w = Worker(bot, FakeSvc(store))
        text = "\n".join("📌 " + "ك" * 80 for _ in range(150))
        res = run(w._send({"id": 1, "kind": "weekly_report_post", "chat_id": -1001, "topic_id": 5, "payload": {"text": text}}))
        self.assertEqual(res["status"], "sent")
        self.assertGreater(len(bot.sent), 1)
        self.assertTrue(all(len(kw["text"]) <= 4096 for _, kw in bot.sent))


class PerUserLockTests(unittest.TestCase):
    def test_same_user_serialized_different_users_parallel(self):
        order = []

        @per_user
        async def h(update, context, tag):
            order.append(("in", tag))
            await asyncio.sleep(0.05)
            order.append(("out", tag))

        def u(uid):
            return NS(effective_user=NS(id=uid))

        async def main():
            await asyncio.gather(h(u(1), None, "a"), h(u(1), None, "b"), h(u(2), None, "c"))

        run(main())
        # للمستخدم 1: لا تداخل (a تنتهي قبل بدء b)
        ia, oa, ib = order.index(("in", "a")), order.index(("out", "a")), order.index(("in", "b"))
        self.assertLess(oa, ib)
        self.assertLess(order.index(("in", "c")), oa)   # المستخدم 2 لم ينتظر

    def test_locks_are_cleaned_up(self):
        from bot import locks

        @per_user
        async def h(update, context):
            return 1

        run(h(NS(effective_user=NS(id=9)), None))
        self.assertNotIn(9, locks._locks)


class DeliveryTrackingTests(unittest.TestCase):
    def test_missing_destination_records_failure_backoff(self):
        store = FakeStore()
        ok = run(assignments.deliver_tracked(FakeBot(), FakeSvc(store, {}), {**PKG, "old_deliveries": []}))
        self.assertFalse(ok)
        self.assertEqual(len(store.called("delivery_failed")), 1)

    def test_crash_in_delivery_does_not_propagate(self):
        class Boom(FakeSvc):
            async def destination(self, key):
                raise RuntimeError("db down")

        store = FakeStore()
        ok = run(assignments.deliver_tracked(FakeBot(), Boom(store), {**PKG, "old_deliveries": []}))
        self.assertFalse(ok)
        self.assertEqual(len(store.called("delivery_failed")), 1)

    def test_success_does_not_record_failure(self):
        store = FakeStore()
        svc = FakeSvc(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": 8}})
        bot = FakeBot()
        bot.send_document = lambda **kw: asyncio.sleep(0, NS(message_id=7))
        ok = run(assignments.deliver_tracked(bot, svc, {**PKG, "old_deliveries": []}))
        self.assertTrue(ok)
        self.assertEqual(store.called("delivery_failed"), [])

    def test_too_many_keeps_session_and_offers_finish(self):
        store = FakeStore({"add_submission_file": {"status": "too_many"}})
        c = ctx(store, user_data={"sub": 7})
        run(assignments.on_submission_media(upd(), c, {"media_kind": "document", "mime": "x/y", "file_id": "x", "unique_id": "u"}))
        self.assertEqual(c.user_data.get("sub"), 7)
        self.assertIsNotNone(c.bot.sent[0][1]["reply_markup"])


class WorkerSchedulingTests(unittest.TestCase):
    def test_weekly_report_enqueued_once_via_rpc_and_isolated_per_group(self):
        store = FakeStore({"enqueue_outbox": True})

        async def select(table, params=None):
            if table == "daily_reports":
                return [{"report_date": "2026-10-25", "rendered_text": "📌 تقرير الأحد\n• محاضرة"}]
            return []

        store.select = select
        dests = {f"cohort:theory:CS2_G{i}": {"chat_id": -100 - i, "topic_id": i} for i in (1, 3)}   # G2 بلا وجهة
        w = Worker(FakeBot(), FakeSvc(store, dests))
        run(w._auto_weekly_reports(dt.date(2026, 10, 29)))
        run(w._auto_weekly_reports(dt.date(2026, 10, 29)))   # استدعاء ثانٍ: لا تكرار
        keys = [p["p_dedupe"] for p in store.called("enqueue_outbox")]
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(k.startswith("auto_wrep:") for k in keys))
        text = store.called("enqueue_outbox")[0]["p_payload"]["text"]
        self.assertTrue(text.startswith("📚 الأسبوع 1 — CS2_G1"))   # أول تقرير 25 أكتوبر → الأسبوع 1
        self.assertNotIn("الحصاد", text)

    def test_week_without_any_daily_report_is_not_posted(self):
        store = FakeStore({"enqueue_outbox": True})

        async def select(table, params=None):
            return []

        store.select = select
        dests = {"cohort:theory:CS2_G1": {"chat_id": -101, "topic_id": 1}}
        w = Worker(FakeBot(), FakeSvc(store, dests))
        run(w._auto_weekly_reports(dt.date(2026, 10, 29)))
        self.assertEqual(store.called("enqueue_outbox"), [])

    def test_stale_first_submission_is_auto_finalized_and_delivered(self):
        store = FakeStore({
            "stale_unfinalized": [{"tg": 10, "assignment": 7, "first": True}],
            "finalize_submission": {"status": "ok", "package": {**PKG, "old_deliveries": []}},
            "claim_delivery": True,
        })
        bot = FakeBot()
        bot.send_document = lambda **kw: asyncio.sleep(0, NS(message_id=7))
        w = Worker(bot, FakeSvc(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": 8}}))
        run(w._finish_stale_uploads())
        self.assertEqual(len(store.called("finalize_submission")), 1)
        self.assertTrue(any("تلقائيًا" in kw.get("text", "") for _, kw in bot.sent))

    def test_stale_edit_gets_single_reminder_without_finalizing(self):
        store = FakeStore({"stale_unfinalized": [{"tg": 10, "assignment": 7, "first": False}]})
        bot = FakeBot()
        w = Worker(bot, FakeSvc(store))
        run(w._finish_stale_uploads())
        run(w._finish_stale_uploads())
        self.assertEqual(store.called("finalize_submission"), [])
        self.assertEqual(len(bot.sent), 1)


if __name__ == "__main__":
    unittest.main()



class FloodGuardTests(unittest.TestCase):
    def setUp(self):
        from core.flood import FloodGuard
        self.t = [0.0]
        self.guard = FloodGuard(msg_limit=30, cb_limit=60, window=10.0, notice_gap=30.0, clock=lambda: self.t[0])

    def test_student_can_request_many_files_quickly(self):
        # ٢٥ طلب ملف (ضغطة زر) في ثانيتين: استخدام طبيعي يجب ألا يُحجب
        for i in range(25):
            self.t[0] += 0.08
            self.assertEqual(self.guard.check(7, "cb"), "ok")

    def test_album_of_twenty_files_is_fine(self):
        for _ in range(20):
            self.assertEqual(self.guard.check(7, "msg"), "ok")

    def test_flood_is_dropped_with_single_notice(self):
        verdicts = [self.guard.check(9, "cb") for _ in range(200)]
        self.assertEqual(verdicts[:60], ["ok"] * 60)
        self.assertEqual(verdicts[60], "notice")
        self.assertEqual(set(verdicts[61:]), {"drop"})

    def test_recovers_after_window_and_users_are_independent(self):
        for _ in range(100):
            self.guard.check(9, "msg")
        self.assertEqual(self.guard.check(10, "msg"), "ok")      # مستخدم آخر لا يتأثر
        self.t[0] += 11
        self.assertEqual(self.guard.check(9, "msg"), "ok")       # يعود طبيعيًا بعد هدوء النافذة

    def test_messages_and_buttons_are_counted_separately(self):
        for _ in range(30):
            self.guard.check(5, "msg")
        self.assertNotEqual(self.guard.check(5, "msg"), "ok")
        self.assertEqual(self.guard.check(5, "cb"), "ok")


class ScannerKeyShapeTests(unittest.TestCase):
    def test_generated_tokens_match_precheck(self):
        import re
        from core.attendance import generate_device_token
        pattern = re.compile(r"ots_[A-Za-z0-9_\-]{20,64}")
        for _ in range(50):
            raw, _, _ = generate_device_token()
            self.assertTrue(pattern.fullmatch(raw))
        self.assertFalse(pattern.fullmatch("abc"))
        self.assertFalse(pattern.fullmatch("ots_short"))

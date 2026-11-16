"""اختبارات النسخة الاحتياطية الأسبوعية: التجميع والتقسيم، موعد التشغيل، الإرسال للمطوّرين، الاسترجاع، وتطابق الجداول مع الـ migrations."""
from __future__ import annotations

import datetime as dt
import glob
import io
import re
import unittest
import zipfile
from pathlib import Path

from tests import stubs

stubs.install()

from bot.worker import Worker  # noqa: E402
from core.backup import BACKUP_TABLES, build_backup_parts, plan_parts, read_backup_zips  # noqa: E402
from scripts.restore_backup import restore  # noqa: E402
from tests.test_bot_flows import FakeBot, FakeStore, FakeSvc, run  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def sample_dump():
    d = {t: [] for t in BACKUP_TABLES}
    d["students"] = [{"academic_no": "26160051", "full_name": "عبدالرحمن", "theory_group_id": "CS2_G1"}]
    d["assignments"] = [{"id": 1, "number": 1}, {"id": 2, "number": 2}]
    d["submissions"] = [{"id": 10, "assignment_id": 1, "student_no": "26160051", "submitted_at": "2026-10-25T10:00:00+00:00"}]
    return d


class DevStore(FakeStore):
    def __init__(self, devs, responses=None):
        super().__init__(responses)
        self.devs = devs

    async def select(self, table, params=None):
        self.calls.append((f"select:{table}", params))
        return [{"telegram_id": d} for d in self.devs]


class PartialBot(FakeBot):
    """يفشل الإرسال لمحادثة محددة فقط (مثل مطوّر حظر البوت)."""
    def __init__(self, bad_chat):
        super().__init__()
        self.bad_chat = bad_chat

    async def send_document(self, **kw):
        if kw.get("chat_id") == self.bad_chat:
            raise RuntimeError("blocked")
        return await super().send_document(**kw)


def backup_store(devs, claim=True):
    return DevStore(devs, {
        "backup_claim": claim,
        "backup_export_table": lambda p: [{"id": 1, "t": p["p_table"]}],
        "backup_finish": None,
    })


class BuildTests(unittest.TestCase):
    def test_zip_roundtrip_keeps_arabic_and_counts(self):
        parts = build_backup_parts(sample_dump(), "2026-10-30T00:00:00+00:00", "2026-10-30")
        self.assertEqual(len(parts), 1)
        name, blob = parts[0]
        self.assertEqual(name, "backup_2026-10-30_part1of1.zip")
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertIn("manifest.json", zf.namelist())
            self.assertIn("submissions.json", zf.namelist())
        data = read_backup_zips([blob])
        self.assertEqual(data["students"][0]["full_name"], "عبدالرحمن")
        self.assertEqual(len(data["assignments"]), 2)
        self.assertEqual(set(data), set(BACKUP_TABLES))

    def test_split_into_parts_covers_every_table_once(self):
        big = sample_dump()
        big["activity_log"] = [{"id": i, "note": f"n{i}-" + "x" * 50} for i in range(400)]
        parts = build_backup_parts(big, "t", "L", limit=1)   # حدّ ضئيل: جزء لكل جدول
        self.assertEqual(len(parts), len(BACKUP_TABLES))
        merged = read_backup_zips([b for _, b in parts])
        self.assertEqual(len(merged["activity_log"]), 400)
        self.assertEqual(merged["students"], big["students"])
        self.assertEqual(parts[0][0], f"backup_L_part1of{len(BACKUP_TABLES)}.zip")

    def test_plan_parts_groups_greedily_in_order(self):
        sizes = {"a": 6, "b": 3, "c": 5, "d": 20}
        self.assertEqual(plan_parts(sizes, 10, order=["a", "b", "c", "d"]), [["a", "b"], ["c"], ["d"]])

    def test_tables_match_migrations(self):
        created = set()
        for f in glob.glob(str(ROOT / "supabase/migrations/*.sql")):
            created |= set(re.findall(r"create table if not exists\s+([a-z_0-9]+)", Path(f).read_text("utf8"), re.I))
        expected = created - {"outbox", "ai_cache", "backup_runs"}
        self.assertEqual(set(BACKUP_TABLES), expected)
        sql = (ROOT / "supabase/migrations/0010_backup.sql").read_text("utf8")
        arr = re.search(r"select array\[(.*?)\];", sql, re.S).group(1)
        self.assertEqual(re.findall(r"'([a-z_0-9]+)'", arr), BACKUP_TABLES)   # نفس الترتيب في SQL وبايثون


class SchedulerTests(unittest.TestCase):
    def w(self):
        return Worker(FakeBot(), FakeSvc(backup_store([1])))

    def test_due_only_friday_after_3am(self):
        w = self.w()
        self.assertIsNone(w._backup_due(dt.datetime(2026, 10, 30, 2, 59)))    # الجمعة قبل 3
        self.assertEqual(w._backup_due(dt.datetime(2026, 10, 30, 3, 0)), "2026-W44")
        self.assertIsNone(w._backup_due(dt.datetime(2026, 10, 29, 23, 0)))    # الخميس
        self.assertIsNone(w._backup_due(dt.datetime(2026, 10, 31, 12, 0)))    # السبت
        w._backup_done = "2026-W44"
        self.assertIsNone(w._backup_due(dt.datetime(2026, 10, 30, 9, 0)))     # أُنجزت هذا الأسبوع


class WeeklyBackupTests(unittest.TestCase):
    def test_sends_zip_to_every_developer_and_marks_ok(self):
        store, bot = backup_store([11, 22]), FakeBot()
        w = Worker(bot, FakeSvc(store))
        run(w._weekly_backup("2026-W44", dt.date(2026, 10, 30)))
        docs = [kw for op, kw in bot.sent if op == "send_document"]
        self.assertEqual(sorted(d["chat_id"] for d in docs), [11, 22])
        self.assertTrue(docs[0]["document"].name.startswith("backup_2026-10-30"))
        self.assertIn("نسخة احتياطية أسبوعية", docs[0]["caption"])
        self.assertEqual(store.called("backup_finish")[-1]["p_status"], "ok")
        self.assertEqual(w._backup_done, "2026-W44")
        self.assertEqual(len(store.called("backup_export_table")), len(BACKUP_TABLES))

    def test_already_claimed_week_sends_nothing(self):
        store, bot = backup_store([11], claim=False), FakeBot()
        w = Worker(bot, FakeSvc(store))
        run(w._weekly_backup("2026-W44", dt.date(2026, 10, 30)))
        self.assertEqual(bot.sent, [])
        self.assertEqual(w._backup_done, "2026-W44")
        self.assertEqual(store.called("backup_export_table"), [])

    def test_no_developer_marks_failed_and_delays_retry(self):
        store, bot = backup_store([]), FakeBot()
        w = Worker(bot, FakeSvc(store))
        run(w._weekly_backup("2026-W44", dt.date(2026, 10, 30)))
        self.assertEqual(bot.sent, [])
        self.assertEqual(store.called("backup_finish")[-1]["p_status"], "failed")
        self.assertIsNone(w._backup_done)
        self.assertGreater(w._backup_retry_at, 0)

    def test_one_blocked_developer_does_not_stop_the_others(self):
        store, bot = backup_store([11, 22]), PartialBot(bad_chat=11)
        w = Worker(bot, FakeSvc(store))
        run(w._weekly_backup("2026-W44", dt.date(2026, 10, 30)))
        self.assertEqual([kw["chat_id"] for op, kw in bot.sent if op == "send_document"], [22])
        self.assertEqual(store.called("backup_finish")[-1]["p_status"], "ok")

    def test_all_deliveries_failing_marks_failed(self):
        store, bot = backup_store([11]), PartialBot(bad_chat=11)
        w = Worker(bot, FakeSvc(store))
        run(w._weekly_backup("2026-W44", dt.date(2026, 10, 30)))
        self.assertEqual(store.called("backup_finish")[-1]["p_status"], "failed")
        self.assertIsNone(w._backup_done)


class RestoreTests(unittest.TestCase):
    def test_restores_in_fk_order_and_counts_rows(self):
        order = []

        class S:
            async def rpc(self, name, params=None):
                order.append(params["p_table"])
                return len(params["p_rows"])

        data = sample_dump()
        out = run(restore(S(), data, batch=1))
        self.assertEqual(out, {"students": 1, "assignments": 2, "submissions": 1})
        self.assertLess(order.index("students"), order.index("submissions"))
        self.assertLess(order.index("assignments"), order.index("submissions"))

    def test_retries_table_that_failed_on_fk_until_dependency_arrives(self):
        calls = {"submissions": 0}

        class S:
            async def rpc(self, name, params=None):
                if params["p_table"] == "submissions":
                    calls["submissions"] += 1
                    if calls["submissions"] == 1:
                        raise RuntimeError("23503 foreign key violation")
                return len(params["p_rows"])

        out = run(restore(S(), sample_dump()))
        self.assertEqual(out["submissions"], 1)
        self.assertEqual(calls["submissions"], 2)

    def test_gives_up_when_no_progress(self):
        class S:
            async def rpc(self, name, params=None):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            run(restore(S(), sample_dump()))


if __name__ == "__main__":
    unittest.main()

"""اختبار تدفقات البوت بنسخ وهمية من تيليجرام والقاعدة (لا شبكة)."""
import asyncio
import unittest
from types import SimpleNamespace as NS

from tests import stubs

stubs.install()

from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError  # noqa: E402

from bot import assignments, files  # noqa: E402
from bot.services import svc  # noqa: E402
from bot.worker import Worker  # noqa: E402


class FakeStore:
    def __init__(self, responses=None):
        self.calls, self.responses = [], responses or {}

    async def rpc(self, name, params=None):
        self.calls.append((name, params))
        r = self.responses.get(name)
        return r(params) if callable(r) else r

    def called(self, name):
        return [p for n, p in self.calls if n == name]


class FakeSvc:
    def __init__(self, store, dests=None):
        self.store, self._dests = store, dests or {}

    async def destination(self, key):
        return self._dests.get(key)


class FakeBot:
    username = "test_bot"

    def __init__(self, fail=None):
        self.sent, self.deleted, self.fail, self._id = [], [], fail or {}, 100

    def _next(self, op, **kw):
        if op in self.fail:
            raise self.fail[op]
        self._id += 1
        self.sent.append((op, kw))
        return NS(message_id=self._id)

    async def send_message(self, chat_id, text, **kw):
        return self._next("send_message", chat_id=chat_id, text=text, **kw)

    async def send_document(self, **kw):
        return self._next("send_document", **kw)

    async def copy_message(self, *a, **kw):
        return self._next("copy_message", a=a, **kw)

    async def delete_message(self, chat_id, message_id):
        if "delete_message" in self.fail:
            raise self.fail["delete_message"]
        self.deleted.append((chat_id, message_id))


def run(coro):
    return asyncio.run(coro)


def worker(bot, store=None):
    return Worker(bot, FakeSvc(store or FakeStore()))


ASG = {"assignment_id": 7, "number": 3, "subject": "شبكات", "part": "theory", "group": "CS2_G1",
       "due_at": "2026-10-25T17:00:00+00:00", "has_attachment": False}


class WorkerSendTests(unittest.TestCase):
    def row(self, kind, payload, chat=555, topic=None):
        return {"id": 1, "kind": kind, "chat_id": chat, "topic_id": topic, "payload": payload}

    def test_assignment_new_sends_text_with_open_button(self):
        bot = FakeBot()
        res = run(worker(bot)._send(self.row("assignment_new", ASG)))
        self.assertEqual(res["status"], "sent")
        op, kw = bot.sent[0]
        self.assertIn("تم نشر واجب جديد", kw["text"])
        self.assertEqual(kw["reply_markup"].rows[0][0].kw["callback_data"], "a:d:7")

    def test_community_post_goes_to_topic_with_deeplink(self):
        bot = FakeBot()
        run(worker(bot)._send(self.row("assignment_post", ASG, chat=-1001, topic=42)))
        _, kw = bot.sent[0]
        self.assertEqual((kw["chat_id"], kw["message_thread_id"]), (-1001, 42))
        self.assertIn("start=a_7", kw["reply_markup"].rows[0][0].kw["url"])

    def test_blocked_user_vs_failed_group(self):
        self.assertEqual(run(worker(FakeBot({"send_message": Forbidden("blocked")}))._send(self.row("manual", {"body": "x"})))["status"], "blocked")
        self.assertEqual(run(worker(FakeBot({"send_message": Forbidden("kicked")}))._send(self.row("manual", {"body": "x"}, chat=-100)))["status"], "failed")

    def test_flood_retry_and_bad_request(self):
        res = run(worker(FakeBot({"send_message": RetryAfter(7)}))._send(self.row("manual", {"body": "x"})))
        self.assertEqual((res["status"], res["delay"]), ("retry", 8))
        self.assertEqual(run(worker(FakeBot({"send_message": BadRequest("bad")}))._send(self.row("manual", {"body": "x"})))["status"], "failed")

    def test_file_send_copies_then_falls_back_to_file_id(self):
        p = {"src_chat": -1001, "src_msg": 9, "media_kind": "document", "file_id": "FID"}
        bot = FakeBot()
        run(worker(bot)._send(self.row("file_send", p)))
        self.assertEqual(bot.sent[0][0], "copy_message")
        bot2 = FakeBot({"copy_message": BadRequest("message to copy not found")})
        res = run(worker(bot2)._send(self.row("file_send", p)))
        self.assertEqual((res["status"], bot2.sent[0][0]), ("sent", "send_document"))

    def test_tg_delete_ignores_missing_message(self):
        res = run(worker(FakeBot({"delete_message": BadRequest("not found")}))._send(self.row("tg_delete", {"message_id": 5}, chat=-100)))
        self.assertEqual(res["status"], "sent")

    def test_repost_records_new_message_ref(self):
        store, bot = FakeStore(), FakeBot()
        p = {"file_row": 11, "media_kind": "document", "file_id": "F", "caption": "cap"}
        run(worker(bot, store)._send(self.row("file_repost", p, chat=-100, topic=3)))
        call = store.called("set_file_message")[0]
        self.assertEqual((call["p_id"], call["p_chat"], call["p_topic"]), (11, -100, 3))


PKG = {"submission_id": 5, "is_late": False, "revision": 1, "dest_key": "doctor:2:theory:CS2_G1",
       "student": {"no": "20260017", "name": "منى", "practical": "G2", "theory": "CS2_G1"},
       "assignment": {"number": 3, "part": "theory", "grp": "CS2_G1", "subject": "شبكات"},
       "files": [{"media_kind": "document", "file_id": "A", "name": "a.pdf"}, {"media_kind": "document", "file_id": "B", "name": "b.zip"}],
       "old_deliveries": [{"chat_id": -1002, "message_id": 70}]}


class DeliveryTests(unittest.TestCase):
    def test_delivers_files_with_caption_to_doctor_topic_and_replaces_old(self):
        store, bot = FakeStore(), FakeBot()
        svc = FakeSvc(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": 8}})
        self.assertTrue(run(assignments.deliver(bot, svc, PKG)))
        self.assertEqual(bot.deleted, [(-1002, 70)])
        self.assertEqual([op for op, _ in bot.sent], ["send_document", "send_document"])
        self.assertTrue(all(kw.get("message_thread_id") == 8 for _, kw in bot.sent))
        self.assertIn("منى", bot.sent[0][1]["caption"])
        self.assertEqual(bot.sent[0][1]["parse_mode"], "HTML")
        self.assertEqual(bot.sent[-1][1]["reply_markup"].rows[0][0].kw["callback_data"], "recv:5")
        rec = store.called("record_delivery")[0]
        self.assertEqual(len(rec["p_msgs"]), 2)

    def test_partial_failure_cleans_up_and_stays_pending(self):
        store = FakeStore()

        class Flaky(FakeBot):
            async def send_document(self, **kw):
                if kw["document"] == "B":
                    raise TelegramError("boom")
                return await super().send_document(**kw)
        bot = Flaky()
        svc = FakeSvc(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": None}})
        self.assertFalse(run(assignments.deliver(bot, svc, PKG)))
        self.assertEqual(len(bot.deleted), 2)       # القديمة (70) + الملف الأول
        self.assertEqual(store.called("record_delivery"), [])

    def test_missing_destination_defers(self):
        store = FakeStore()
        self.assertFalse(run(assignments.deliver(FakeBot(), FakeSvc(store, {}), PKG)))
        self.assertEqual(store.calls, [])


def ctx(store, dests=None, user_data=None, bot=None):
    return NS(bot=bot or FakeBot(), user_data=user_data if user_data is not None else {},
              application=NS(bot_data={"svc": FakeSvc(store, dests)}))


def upd(msg_id=1, group=None, user=77, chat=77):
    return NS(effective_user=NS(id=user), effective_chat=NS(id=chat),
              effective_message=NS(message_id=msg_id, media_group_id=group), callback_query=None)


class UploadTests(unittest.TestCase):
    PREP = {"status": "ok", "draft_id": 4, "subject": {"id": 2, "name": "شبكات الحاسوب", "tag": "شبكات_الحاسوب"},
            "doctor": {"id": 3, "name": "د. خالد الهاشمي"}, "duplicate": None,
            "payload": {"subject_id": 2, "part": "theory", "content_type": "summary", "numbers": [9, 13], "label_kind": "module"}}
    M = {"media_kind": "document", "file_id": "F1", "unique_id": "U1", "name": "s.pdf", "mime": "application/pdf", "size": 10}

    def test_posts_caption_to_subject_topic_and_registers(self):
        store = FakeStore({"prepare_file": self.PREP, "register_file": {"status": "ok", "file_id": 9, "old": None}})
        c = ctx(store, {"cohort:subject:2": {"chat_id": -1001, "topic_id": 55}})
        run(files.handle_upload(upd(msg_id=31), c, self.M))
        op, kw = c.bot.sent[0]
        self.assertEqual(op, "copy_message")
        self.assertEqual((kw["chat_id"], kw["message_thread_id"], kw["message_id"]), (-1001, 55, 31))
        self.assertIn("#Module_9 #Module_13", kw["caption"])
        self.assertIn("#د_خالد_الهاشمي", kw["caption"])
        reg = store.called("register_file")[0]
        self.assertEqual((reg["p_post"]["chat_id"], reg["p_post"]["topic_id"]), (-1001, 55))
        self.assertIn("تم رفع الملف", c.bot.sent[-1][1]["text"])

    def test_duplicate_warns_once_then_accepts(self):
        prep = {**self.PREP, "duplicate": 3}
        store = FakeStore({"prepare_file": prep, "register_file": {"status": "ok", "file_id": 9}})
        c = ctx(store, {"cohort:subject:2": {"chat_id": -1001, "topic_id": None}})
        run(files.handle_upload(upd(), c, self.M))
        self.assertEqual(store.called("register_file"), [])
        self.assertIn("مرفوع سابقًا", c.bot.sent[0][1]["text"])
        run(files.handle_upload(upd(), c, self.M))
        self.assertEqual(len(store.called("register_file")), 1)

    def test_no_community_still_saves_with_warning(self):
        store = FakeStore({"prepare_file": self.PREP, "register_file": {"status": "ok", "file_id": 9}})
        c = ctx(store, {})
        run(files.handle_upload(upd(), c, self.M))
        self.assertEqual(store.called("register_file")[0]["p_post"], {"caption": store.called("register_file")[0]["p_post"]["caption"]})
        self.assertIn("لم يُضبط مجتمع", c.bot.sent[-1][1]["text"])

    def test_replacement_deletes_old_community_message(self):
        store = FakeStore({"prepare_file": self.PREP,
                           "register_file": {"status": "ok", "file_id": 9, "old": {"chat_id": -1001, "message_id": 12}}})
        c = ctx(store, {"cohort:subject:2": {"chat_id": -1001, "topic_id": None}})
        run(files.handle_upload(upd(), c, self.M))
        self.assertEqual(c.bot.deleted, [(-1001, 12)])

    def test_without_draft_explains(self):
        c = ctx(FakeStore({"prepare_file": {"status": "no_draft"}}))
        run(files.handle_upload(upd(), c, self.M))
        self.assertIn("لا يوجد رفع جارٍ", c.bot.sent[0][1]["text"])


class SubmissionTests(unittest.TestCase):
    def test_images_are_rejected_even_as_documents(self):
        store = FakeStore()
        for m in ({"media_kind": "photo", "mime": "image/jpeg"}, {"media_kind": "document", "mime": "image/png"}):
            c = ctx(store, user_data={"sub": 7})
            run(assignments.on_submission_media(upd(), c, {**m, "file_id": "x", "unique_id": "u"}))
            self.assertIn("الصور غير مقبولة", c.bot.sent[0][1]["text"])
        self.assertEqual(store.called("add_submission_file"), [])

    def test_pdf_accepted_and_album_replies_once(self):
        store = FakeStore({"add_submission_file": {"status": "ok", "pending": 1}})
        c = ctx(store, user_data={"sub": 7})
        m = {"media_kind": "document", "mime": "application/pdf", "file_id": "x", "unique_id": "u"}
        run(assignments.on_submission_media(upd(group="G"), c, m))
        run(assignments.on_submission_media(upd(group="G"), c, m))
        self.assertEqual(len(store.called("add_submission_file")), 2)
        self.assertEqual(len(c.bot.sent), 1)

    def test_closed_assignment_ends_session(self):
        store = FakeStore({"add_submission_file": {"status": "closed"}})
        c = ctx(store, user_data={"sub": 7})
        run(assignments.on_submission_media(upd(), c, {"media_kind": "document", "mime": "x/y", "file_id": "x", "unique_id": "u"}))
        self.assertNotIn("sub", c.user_data)
        self.assertIn("انتهى وقت", c.bot.sent[0][1]["text"])

    def test_finalize_delivers_and_reports_late(self):
        pkg = {**PKG, "is_late": True, "old_deliveries": []}
        store = FakeStore({"finalize_submission": {"status": "ok", "package": pkg}})
        c = ctx(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": 8}}, user_data={"sub": 7})
        run(assignments.finalize(upd(), c, 7))
        self.assertNotIn("sub", c.user_data)
        last = c.bot.sent[-1][1]["text"]
        self.assertIn("تم تسليم واجبك", last)
        self.assertIn("(متأخر)", last)
        self.assertNotIn("سيصل للدكتور تلقائيًا", last)

    def test_finalize_without_destination_keeps_pending_and_tells_student(self):
        store = FakeStore({"finalize_submission": {"status": "ok", "package": PKG}})
        c = ctx(store, {}, user_data={"sub": 7})
        run(assignments.finalize(upd(), c, 7))
        self.assertIn("سيصل للدكتور تلقائيًا", c.bot.sent[-1][1]["text"])

    def test_finalize_without_files(self):
        c = ctx(FakeStore({"finalize_submission": {"status": "no_files"}}), user_data={"sub": 7})
        run(assignments.finalize(upd(), c, 7))
        self.assertIn("لم ترسل أي ملف", c.bot.sent[0][1]["text"])


def tupd(text, user=77, chat=77):
    return NS(effective_user=NS(id=user), effective_chat=NS(id=chat),
              effective_message=NS(message_id=1, media_group_id=None, text=text), callback_query=None)


class TextSubmissionTests(unittest.TestCase):
    """خيار «السماح بإرسال نصوص»: الواجبات القديمة (بدون الخيار) تبقى ملفات فقط."""

    def test_old_assignment_rejects_text_without_touching_db(self):
        store = FakeStore()
        c = ctx(store, user_data={"sub": 7})        # لا sub_text → واجب لا يقبل النص
        self.assertTrue(run(assignments.on_submission_text(tupd("https://example.com"), c)))
        self.assertIn("الملفات فقط", c.bot.sent[0][1]["text"])
        self.assertEqual(store.called("add_submission_file"), [])

    def test_allowed_assignment_saves_text_and_links(self):
        store = FakeStore({"add_submission_file": {"status": "ok", "pending": 1}})
        c = ctx(store, user_data={"sub": 7, "sub_text": True})
        self.assertTrue(run(assignments.on_submission_text(tupd("  حلّي هنا https://example.com/x  "), c)))
        call = store.called("add_submission_file")[0]
        self.assertEqual(call["p_file"]["media_kind"], "text")
        self.assertEqual(call["p_file"]["file_id"], "حلّي هنا https://example.com/x")
        self.assertIn("استلمت النص", c.bot.sent[0][1]["text"])

    def test_server_refusal_turns_text_off_but_keeps_session(self):
        store = FakeStore({"add_submission_file": {"status": "text_not_allowed"}})
        c = ctx(store, user_data={"sub": 7, "sub_text": True})
        run(assignments.on_submission_text(tupd("نص"), c))
        self.assertEqual(c.user_data.get("sub"), 7)
        self.assertFalse(c.user_data["sub_text"])

    def test_too_long_text_is_refused_locally(self):
        store = FakeStore()
        c = ctx(store, user_data={"sub": 7, "sub_text": True})
        run(assignments.on_submission_text(tupd("س" * 3001), c))
        self.assertEqual(store.called("add_submission_file"), [])
        self.assertIn("طويل", c.bot.sent[0][1]["text"])

    def test_no_session_is_not_handled(self):
        c = ctx(FakeStore(), user_data={})
        self.assertFalse(run(assignments.on_submission_text(tupd("مرحبا"), c)))

    def test_start_session_remembers_allow_text(self):
        detail = {"status": "ok", "number": 3, "subject": "شبكات", "due_at": "2099-01-01T10:00:00+00:00",
                  "closes_at": "2099-01-01T10:30:00+00:00", "status_db": "open", "submission": None}
        for flag, expect in ((True, True), (None, False)):
            store = FakeStore({"assignment_detail": {**detail, **({"allow_text": True} if flag else {})}})
            c = ctx(store, user_data={})
            run(assignments.start_submit_direct(c.bot, svc(c), 77, 77, 7, c))
            self.assertEqual(c.user_data["sub_text"], expect)

    def test_text_item_is_delivered_as_message_with_header(self):
        pkg = {**PKG, "old_deliveries": [], "files": [{"media_kind": "text", "file_id": "الحل <b>هنا</b>", "name": "x"}]}
        store = FakeStore({"record_delivery": None})
        c = ctx(store, {"doctor:2:theory:CS2_G1": {"chat_id": -1002, "topic_id": 8}})
        self.assertTrue(run(assignments.deliver(c.bot, svc(c), pkg)))
        op, kw = c.bot.sent[-1]
        self.assertEqual(op, "send_message")
        self.assertIn("&lt;b&gt;هنا&lt;/b&gt;", kw["text"])     # النص يُهرَّب فلا يكسر HTML
        self.assertEqual(kw["message_thread_id"], 8)


class SessionSeparationTests(unittest.TestCase):
    """جلسة تسليم الواجب وجلسة المشاركة لا تختلطان، ولا يُلحَق ملف بشارك تلقائيًا."""

    def test_no_auto_sharek_attachment_helper_left(self):
        from bot import phase4
        self.assertFalse(hasattr(phase4, "try_auto_sharek_media"))

    def test_opening_sharek_clears_assignment_session(self):
        from bot import phase4
        store = FakeStore({"my_sharek": [{"id": 5, "kind": "submission", "title": "س", "is_finished": False,
                                          "my_items_count": 0}]})
        c = ctx(store, user_data={"sub": 7, "sub_text": True})
        run(phase4.open_campaign(c.bot, svc(c), 77, 77, 5, c))
        self.assertEqual(c.user_data.get("sh_sub"), 5)
        self.assertNotIn("sub", c.user_data)
        self.assertNotIn("sub_text", c.user_data)

    def test_starting_submission_clears_sharek_session(self):
        detail = {"status": "ok", "number": 3, "subject": "شبكات", "due_at": "2099-01-01T10:00:00+00:00",
                  "closes_at": "2099-01-01T10:30:00+00:00", "status_db": "open", "submission": None}
        c = ctx(FakeStore({"assignment_detail": detail}), user_data={"sh_sub": 5})
        run(assignments.start_submit_direct(c.bot, svc(c), 77, 77, 7, c))
        self.assertEqual(c.user_data.get("sub"), 7)
        self.assertNotIn("sh_sub", c.user_data)


class ReceivedTests(unittest.TestCase):
    def _upd(self, answers):
        async def answer(text=None, show_alert=False):
            answers.append((text, show_alert))

        async def edit(markup):
            answers.append(("edited", markup.rows[0][0].kw["callback_data"]))
        q = NS(answer=answer, edit_message_reply_markup=edit)
        return NS(callback_query=q, effective_user=NS(id=9), effective_chat=NS(id=-1))

    def test_doctor_marks_received(self):
        out = []
        run(assignments.on_received(self._upd(out), ctx(FakeStore({"mark_received": {"status": "ok"}})), 5))
        self.assertEqual(out[0][0], "تم تسجيل الاستلام ✅")
        self.assertEqual(out[1], ("edited", "noop"))

    def test_others_get_alert(self):
        out = []
        run(assignments.on_received(self._upd(out), ctx(FakeStore({"mark_received": {"status": "forbidden"}})), 5))
        self.assertTrue(out[0][1])
        self.assertEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()

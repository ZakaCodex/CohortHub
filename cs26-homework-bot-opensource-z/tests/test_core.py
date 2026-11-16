import asyncio
import hashlib
import hmac
import json
import time
import unittest
from datetime import date, datetime, time as dtime, timezone
from urllib.parse import urlencode

from core.cache import VersionedCache
from core.permissions import allowed_groups, can, practical_to_theory
from core.text import clean_name, normalize_academic_no
from core.timeutil import (format_date_ar, format_datetime_ar, format_time_ar, is_study_day,
                           parse_sanaa, sanaa_local_to_utc, to_sanaa)
from core.tg_auth import AuthError, validate_init_data

TOKEN = "123456:TEST_TOKEN_NOT_REAL"


def make_init_data(user: dict, auth_date: int, token: str = TOKEN) -> str:
    pairs = {"auth_date": str(auth_date), "query_id": "AAH", "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class TimeTests(unittest.TestCase):
    def test_sanaa_is_utc_plus_3(self):
        utc = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(to_sanaa(utc).hour, 17)

    def test_local_to_utc_roundtrip(self):
        u = sanaa_local_to_utc(date(2026, 9, 8), dtime(20, 0))
        self.assertEqual((u.hour, u.day), (17, 8))
        self.assertEqual(parse_sanaa("2026-09-08 20:00"), u)

    def test_midnight_crosses_day(self):
        u = sanaa_local_to_utc(date(2026, 9, 8), dtime(1, 30))
        self.assertEqual((u.day, u.hour, u.minute), (7, 22, 30))

    def test_arabic_formatting(self):
        dt = parse_sanaa("2026-09-08 20:00")
        self.assertEqual(format_time_ar(dt), "8:00 م")
        self.assertEqual(format_date_ar(dt), "الثلاثاء 8 سبتمبر")
        self.assertEqual(format_datetime_ar(parse_sanaa("2026-10-21 00:05")), "الأربعاء 21 أكتوبر · 12:05 ص")
        self.assertEqual(format_time_ar(parse_sanaa("2026-10-21 12:00")), "12:00 م")

    def test_study_days_include_thursday_exclude_friday(self):
        self.assertTrue(is_study_day(parse_sanaa("2026-10-22 10:00")))   # الخميس
        self.assertFalse(is_study_day(parse_sanaa("2026-10-23 10:00")))  # الجمعة
        self.assertTrue(is_study_day(parse_sanaa("2026-10-24 10:00")))   # السبت

    def test_naive_rejected(self):
        with self.assertRaises(ValueError):
            to_sanaa(datetime(2026, 1, 1))


class TextTests(unittest.TestCase):
    def test_academic_no(self):
        self.assertEqual(normalize_academic_no("٢٠٢٦٠٠٠١"), "20260001")
        self.assertEqual(normalize_academic_no(" 2026 0001 "), "20260001")
        self.assertEqual(normalize_academic_no("\u200f20260001\u200f"), "20260001")
        self.assertIsNone(normalize_academic_no("abc"))
        self.assertIsNone(normalize_academic_no("12"))
        self.assertIsNone(normalize_academic_no("1" * 16))

    def test_name(self):
        self.assertEqual(clean_name("  أحمد   علي "), "أحمد علي")
        self.assertIsNone(clean_name("أ"))
        self.assertIsNone(clean_name("<script>"))
        self.assertIsNone(clean_name("x" * 41))


class AuthTests(unittest.TestCase):
    USER = {"id": 777, "first_name": "منى", "username": "mona"}

    def test_valid(self):
        now = int(time.time())
        u = validate_init_data(make_init_data(self.USER, now), TOKEN)
        self.assertEqual((u.id, u.username, u.full_name), (777, "mona", "منى"))

    def test_wrong_token(self):
        with self.assertRaises(AuthError):
            validate_init_data(make_init_data(self.USER, int(time.time())), "999:OTHER")

    def test_tampered_user(self):
        data = make_init_data(self.USER, int(time.time()))
        tampered = data.replace("777", "778")
        with self.assertRaises(AuthError):
            validate_init_data(tampered, TOKEN)

    def test_expired(self):
        old = int(time.time()) - 3 * 24 * 3600
        with self.assertRaises(AuthError):
            validate_init_data(make_init_data(self.USER, old), TOKEN, max_age=24 * 3600)

    def test_missing(self):
        for bad in ("", "a=b", "hash=abc"):
            with self.assertRaises(AuthError):
                validate_init_data(bad, TOKEN)


PERMS_DELEGATE_G1 = [
    {"k": "files.view", "s": "any", "g": None, "c": None},
    {"k": "assignments.create", "s": "group", "g": "CS2_G1", "c": None},
    {"k": "attendance.session", "s": "group", "g": "CS2_G1", "c": None},
]
PERMS_DEPUTY_G2 = [{"k": "attendance.session", "s": "group", "g": "CS2_G2", "c": "delegate_absent"}]
PERMS_DEV = [{"k": "assignments.create", "s": "any", "g": None, "c": None}]


class PermissionTests(unittest.TestCase):
    def test_group_scope(self):
        self.assertTrue(can(PERMS_DELEGATE_G1, "assignments.create", theory_group="CS2_G1"))
        self.assertFalse(can(PERMS_DELEGATE_G1, "assignments.create", theory_group="CS2_G2"))

    def test_no_target_means_has_in_any_scope(self):
        self.assertTrue(can(PERMS_DELEGATE_G1, "assignments.create"))
        self.assertFalse(can(PERMS_DELEGATE_G1, "roles.manage"))

    def test_conditional_deputy(self):
        self.assertFalse(can(PERMS_DEPUTY_G2, "attendance.session", theory_group="CS2_G2"))
        self.assertTrue(can(PERMS_DEPUTY_G2, "attendance.session", theory_group="CS2_G2", delegate_absent=True))
        self.assertFalse(can(PERMS_DEPUTY_G2, "attendance.session", theory_group="CS2_G1", delegate_absent=True))

    def test_any_scope(self):
        self.assertTrue(can(PERMS_DEV, "assignments.create", theory_group="CS2_G3"))

    def test_allowed_groups(self):
        groups = ["CS2_G1", "CS2_G2", "CS2_G3"]
        self.assertEqual(allowed_groups(PERMS_DELEGATE_G1, "assignments.create", groups), ["CS2_G1"])
        self.assertEqual(allowed_groups(PERMS_DEV, "assignments.create", groups), groups)
        self.assertEqual(allowed_groups([], "assignments.create", groups), [])

    def test_practical_to_theory(self):
        pg = [{"id": "G1", "theory": "CS2_G1"}, {"id": "G4", "theory": "CS2_G2"}]
        self.assertEqual(practical_to_theory("G4", pg), "CS2_G2")
        self.assertIsNone(practical_to_theory("G9", pg))


class CacheTests(unittest.TestCase):
    def test_version_invalidation_and_single_flight(self):
        async def run():
            cache = VersionedCache(ttl=60)
            cache.update_versions({"roles": 1})
            calls = 0

            async def loader():
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                return {"n": calls}

            a, b = await asyncio.gather(cache.get_or_load("u1", "roles", loader),
                                        cache.get_or_load("u1", "roles", loader))
            self.assertEqual(calls, 1)
            self.assertIs(a, b)
            await cache.get_or_load("u1", "roles", loader)
            self.assertEqual(calls, 1)
            self.assertEqual(cache.update_versions({"roles": 2}), ["roles"])
            fresh = await cache.get_or_load("u1", "roles", loader)
            self.assertEqual(fresh["n"], 2)
            self.assertEqual(cache.update_versions({"roles": 2}), [])
        asyncio.run(run())

    def test_ttl(self):
        async def run():
            cache = VersionedCache(ttl=0.02)
            n = 0

            async def loader():
                nonlocal n
                n += 1
                return n
            await cache.get_or_load("k", "s", loader)
            await asyncio.sleep(0.05)
            self.assertEqual(await cache.get_or_load("k", "s", loader), 2)
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()


class GroupNormalizeTests(unittest.TestCase):
    def test_theory(self):
        from core.text import normalize_theory
        for v in ("CS2_G1", "cs2_g1", "G1", "1", "١", " g 1 "):
            self.assertEqual(normalize_theory(v), "CS2_G1", v)
        self.assertIsNone(normalize_theory("G4"))
        self.assertIsNone(normalize_theory(""))
        self.assertIsNone(normalize_theory(None))

    def test_practical(self):
        from core.text import normalize_practical
        self.assertEqual(normalize_practical("g9"), "G9")
        self.assertEqual(normalize_practical(5), "G5")
        self.assertIsNone(normalize_practical("G0"))
        self.assertIsNone(normalize_practical("x"))


class ImportReadTests(unittest.TestCase):
    def test_read_template(self):
        import importlib.util, pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        spec = importlib.util.spec_from_file_location("imp", root / "scripts" / "import_students.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rows, problems = mod.read_rows(str(root / "scripts" / "students_template.xlsx"))
        self.assertEqual(problems, [])
        self.assertEqual(rows[1], {"academic_no": "20269002", "full_name": "مثال: اسم طالب ثانٍ",
                                   "theory": "CS2_G2", "practical": "G5"})

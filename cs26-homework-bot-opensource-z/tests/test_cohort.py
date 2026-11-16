import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import cohort  # noqa: E402
from core.assignments import validate_assignment_meta  # noqa: E402
from core.text import normalize_practical, normalize_theory  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OTHER = {"specialization": "هندسة البرمجيات", "batch": "27", "level": "المستوى الأول",
         "theory_prefix": "SE1_G", "practical_per_theory": [2, 3, 2, 3]}


class DefaultCohortTests(unittest.TestCase):
    """القيم الافتراضية = دفعة CS26 الحالية تمامًا (لا تغيّر النسخة العاملة)."""

    def setUp(self):
        cohort.set_for_tests(None)

    def test_default_groups_match_live_cohort(self):
        c = cohort.get()
        self.assertEqual(c.theory_ids(), ["CS2_G1", "CS2_G2", "CS2_G3"])
        self.assertEqual(c.practical_ids(), [f"G{i}" for i in range(1, 10)])
        self.assertEqual(c.practical_of("CS2_G2"), ["G4", "G5", "G6"])
        self.assertEqual(c.title, "دفعة علوم الحاسوب 26")
        self.assertEqual(c.student_noun, "طالب علوم الحاسوب دفعة 26")

    def test_repo_cohort_json_equals_defaults(self):
        file_cfg = cohort.parse_config(json.loads((ROOT / "cohort.json").read_text("utf-8")))
        self.assertEqual(file_cfg, cohort.parse_config({}))

    def test_old_normalization_behaviour_unchanged(self):
        for raw, expect in (("1", "CS2_G1"), ("G2", "CS2_G2"), ("cs2_g3", "CS2_G3"), ("CS2_G1", "CS2_G1"),
                            ("٣", "CS2_G3"), ("4", None), ("0", None), ("x", None)):
            self.assertEqual(normalize_theory(raw), expect, raw)
        for raw, expect in (("2", "G2"), ("g9", "G9"), ("١", "G1"), ("10", None), ("0", None)):
            self.assertEqual(normalize_practical(raw), expect, raw)

    def test_seed_sql_matches_the_original_hardcoded_seed(self):
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_cohort.py"), "--stdout"],
                             capture_output=True, text=True, check=True).stdout
        for needle in ("('CS2_G1', 1)", "('CS2_G3', 3)", "('G1', 'CS2_G1', 1)", "('G6', 'CS2_G2', 6)", "('G9', 'CS2_G3', 9)"):
            self.assertIn(needle, out)


class OtherCohortTests(unittest.TestCase):
    def setUp(self):
        cohort.set_for_tests(cohort.parse_config(OTHER))

    def tearDown(self):
        cohort.set_for_tests(None)

    def test_uneven_practical_distribution(self):
        c = cohort.get()
        self.assertEqual(c.theory_ids(), ["SE1_G1", "SE1_G2", "SE1_G3", "SE1_G4"])
        self.assertEqual(c.practical_of("SE1_G1"), ["G1", "G2"])
        self.assertEqual(c.practical_of("SE1_G2"), ["G3", "G4", "G5"])
        self.assertEqual(len(c.practical_ids()), 10)
        self.assertEqual(c.title, "دفعة هندسة البرمجيات 27")

    def test_normalization_follows_config(self):
        self.assertEqual(normalize_theory("4"), "SE1_G4")
        self.assertEqual(normalize_theory("se1_g2"), "SE1_G2")
        self.assertIsNone(normalize_theory("5"))
        self.assertIsNone(normalize_theory("CS2_G1"))
        self.assertEqual(normalize_practical("10"), "G10")
        self.assertIsNone(normalize_practical("11"))

    def test_assignment_validation_follows_config(self):
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        due = (now + timedelta(days=2)).strftime("%Y-%m-%d 20:00")
        ok, err = validate_assignment_meta({"subject_id": 1, "part": "theory", "group": "SE1_G4", "due_local": due}, now)
        self.assertIsNone(err)
        _, err = validate_assignment_meta({"subject_id": 1, "part": "theory", "group": "CS2_G1", "due_local": due}, now)
        self.assertEqual(err, "invalid_group")
        ok, err = validate_assignment_meta({"subject_id": 1, "part": "practical", "group": "G10", "due_local": due}, now)
        self.assertIsNone(err)

    def test_public_payload_for_mini_app(self):
        pub = cohort.get().public()
        self.assertEqual(pub["theory"][0], "SE1_G1")
        self.assertEqual(pub["practical"][2], {"id": "G3", "theory": "SE1_G2"})
        self.assertEqual(pub["level"], "المستوى الأول")


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_bad_configs(self):
        bad = [
            {"specialization": ""},
            {"theory_prefix": "cs2 g"},
            {"theory_prefix": "1CS"},
            {"practical_per_theory": []},
            {"practical_per_theory": [3, 0]},
            {"practical_per_theory": [3, "x"]},
            {"practical_per_theory": [True]},
            {"practical_per_theory": [30, 31]},
        ]
        for cfg in bad:
            with self.subTest(cfg=cfg):
                with self.assertRaises(RuntimeError):
                    cohort.parse_config(cfg)

    def test_env_json_overrides_file(self):
        os.environ["COHORT_CONFIG_JSON"] = json.dumps(OTHER, ensure_ascii=False)
        try:
            self.assertEqual(cohort.load_cohort().theory_prefix, "SE1_G")
        finally:
            del os.environ["COHORT_CONFIG_JSON"]
        os.environ["COHORT_CONFIG_JSON"] = "{oops"
        try:
            with self.assertRaises(RuntimeError):
                cohort.load_cohort()
        finally:
            del os.environ["COHORT_CONFIG_JSON"]


if __name__ == "__main__":
    unittest.main()

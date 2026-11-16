#!/usr/bin/env python3
"""يولّد ملف SQL لبيانات مجموعات الدفعة من COHORT_CONFIG_JSON أو cohort.json (أو ملف تحدده).

    python scripts/setup_cohort.py                      # يقرأ COHORT_CONFIG_JSON أو cohort.json ويكتب supabase/seed_groups.sql
    python scripts/setup_cohort.py --stdout             # يطبع أوامر SQL على الشاشة دون تعديل أي ملف في المستودع (موصى به عند عمل Fork)
    python scripts/setup_cohort.py --config my.json     # قراءة إعداد من ملف خارجي محدد

شغّل الـ SQL الناتج في Supabase SQL Editor بعد ترحيلات supabase/migrations مرة واحدة. آمن لإعادة التشغيل:
يضيف المجموعات الناقصة فقط ولا يحذف ولا يعدّل أي مجموعة موجودة أو أي بيانات.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.cohort import parse_config  # noqa: E402


def build_sql(cfg) -> str:
    theory = cfg.theory_ids()
    lines = [
        f"-- مجموعات {cfg.title}" + (f" — {cfg.level}" if cfg.level else ""),
        "-- مولَّد من إعداد الدفعة بواسطة scripts/setup_cohort.py (آمن لإعادة التشغيل، يضيف الناقص فقط).",
        "",
        "insert into theory_groups (id, sort) values",
        ",\n".join(f"  ('{t}', {i})" for i, t in enumerate(theory, start=1)),
        "  on conflict do nothing;",
        "",
        "insert into practical_groups (code, theory_group_id, sort) values",
    ]
    rows = [f"  ('{code}', '{t}', {i})" for i, (code, t) in enumerate(cfg.practical_map(), start=1)]
    lines += [",\n".join(rows), "  on conflict do nothing;", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="")
    ap.add_argument("--out", default=str(ROOT / "supabase" / "seed_groups.sql"))
    ap.add_argument("--stdout", action="store_true")
    args = ap.parse_args()
    try:
        raw_env = os.getenv("COHORT_CONFIG_JSON", "").strip()
        if not args.config and raw_env:
            cfg = parse_config(json.loads(raw_env))
        else:
            cfg_path = Path(args.config) if args.config else (ROOT / "cohort.json")
            cfg = parse_config(json.loads(cfg_path.read_text("utf-8")))
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"خطأ في ملف الإعداد: {exc}", file=sys.stderr)
        return 1
    sql = build_sql(cfg)
    if args.stdout:
        print(sql)
    else:
        Path(args.out).write_text(sql, "utf-8")
        print(f"تم: {args.out}\n{cfg.title} — {len(cfg.theory_ids())} نظرية، {len(cfg.practical_ids())} عملية")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

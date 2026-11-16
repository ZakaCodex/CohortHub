"""استيراد الطلاب من Excel إلى القاعدة (للمطوّر).
الأعمدة بالترتيب: الرقم الأكاديمي | اسم الطالب | المجموعة النظرية | المجموعة العملية
الاستخدام:
    python scripts/import_students.py students.xlsx --actor 123456789 [--dry-run]
(--actor = معرّف تيليجرام الخاص بك كمطوّر؛ الدالة ترفض أي معرّف ليس مطوّرًا)
يحتاج متغيرات SUPABASE_URL وSUPABASE_SERVICE_KEY (وBOT_TOKEN فقط ليمرّ تحميل الإعدادات)."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook  # noqa: E402

from core.text import clean_name, normalize_academic_no, normalize_practical, normalize_theory  # noqa: E402


def read_rows(path: str) -> tuple[list[dict], list[str]]:
    ws = load_workbook(path, read_only=True, data_only=True).active
    rows, problems = [], []
    for n, cells in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(cells):
            continue
        no_raw, name_raw, th_raw, pr_raw = (list(cells) + [None] * 4)[:4]
        if isinstance(no_raw, float) and no_raw.is_integer():
            no_raw = int(no_raw)
        no = normalize_academic_no(str(no_raw or ""))
        name = clean_name(str(name_raw or ""), max_len=80)
        theory, practical = normalize_theory(th_raw), normalize_practical(pr_raw)
        why = [m for m, ok in (("رقم أكاديمي غير صالح", no), ("اسم غير صالح", name),
                               ("مجموعة نظرية غير صالحة", theory), ("مجموعة عملية غير صالحة", practical)) if not ok]
        if why:
            problems.append(f"السطر {n}: " + "، ".join(why))
            continue
        rows.append({"academic_no": no, "full_name": name, "theory": theory, "practical": practical})
    return rows, problems


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--actor", type=int, required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows, problems = read_rows(args.file)
    print(f"صالح: {len(rows)} | مرفوض قبل الإرسال: {len(problems)}")
    for p in problems[:30]:
        print("  ", p)
    if args.dry_run or not rows:
        return

    from core.config import load_settings
    from core.store import Store
    s = load_settings()
    store = Store(s.supabase_url, s.supabase_service_key, timeout=30)
    try:
        ins = upd = 0
        for i in range(0, len(rows), 200):
            res = await store.rpc("import_students", {"p_actor": args.actor, "p_rows": rows[i:i + 200]})
            ins, upd = ins + res["inserted"], upd + res["updated"]
            for e in res["errors"]:
                print("   خطأ من القاعدة:", e)
        print(f"تمت الإضافة: {ins} | التحديث: {upd}")
    finally:
        await store.aclose()


if __name__ == "__main__":
    asyncio.run(main())

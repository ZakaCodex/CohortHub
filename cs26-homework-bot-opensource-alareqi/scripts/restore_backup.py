"""استرجاع نسخة احتياطية (ملفات zip التي أرسلها البوت) إلى قاعدة Supabase.

الاستخدام (بعد تشغيل 0001..0010 على القاعدة الجديدة):
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/restore_backup.py backup_2026-10-30_part1of1.zip [part2.zip ...]
    python scripts/restore_backup.py --dry-run backup_*.zip     # يعرض عدد صفوف كل جدول دون كتابة

- الجداول تُرفع بترتيب المفاتيح الأجنبية، وما فشل لسبب الترتيب يُعاد في جولة لاحقة.
- لا يستبدل ما هو موجود (on conflict do nothing)، فالتشغيل المتكرر آمن.
- قيم id الأصلية تُحفظ وتُضبط العدّادات تلقائيًا في الدالة backup_restore_table.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.backup import BACKUP_TABLES, read_backup_zips  # noqa: E402


async def restore(store: Any, data: dict[str, list], batch: int = 500, passes: int = 4) -> dict[str, int]:
    """يرفع الجداول بالترتيب؛ جدول فشل (مثلًا مفتاح أجنبي لجدول لم يصل بعد) يُعاد في الجولة التالية.
    يعيد {جدول: عدد الصفوف المُدرجة فعلًا}."""
    inserted: dict[str, int] = {}
    pending = [t for t in BACKUP_TABLES if data.get(t)]
    last_err: dict[str, str] = {}
    for _ in range(passes):
        failed: list[str] = []
        for table in pending:
            rows = data[table]
            done = 0
            try:
                for i in range(0, len(rows), batch):
                    done += int(await store.rpc("backup_restore_table", {"p_table": table, "p_rows": rows[i:i + batch]}) or 0)
            except Exception as exc:  # noqa: BLE001
                last_err[table] = str(exc)[:200]
                failed.append(table)
                continue
            inserted[table] = inserted.get(table, 0) + done
        if not failed:
            return inserted
        if failed == pending:   # لا تقدّم في هذه الجولة
            break
        pending = failed
    if pending:
        details = "; ".join(f"{t}: {last_err.get(t, '?')}" for t in pending)
        raise RuntimeError(f"تعذّر استرجاع الجداول: {details}")
    return inserted


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zips", nargs="+", help="ملفات zip (كل أجزاء النسخة)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=500)
    args = ap.parse_args()

    data = read_backup_zips(Path(p).read_bytes() for p in args.zips)
    print(f"الجداول في النسخة: {len(data)}")
    for t in BACKUP_TABLES:
        if t in data:
            print(f"  {t}: {len(data[t])} صف")
    if args.dry_run:
        return 0

    from core.config import load_settings
    from core.store import Store
    s = load_settings()
    store = Store(s.supabase_url, s.supabase_service_key, max(s.http_timeout, 60))
    try:
        inserted = await restore(store, data, batch=args.batch)
    finally:
        await store.aclose()
    print("تم الاسترجاع:")
    for t, n in inserted.items():
        print(f"  {t}: أُدرج {n}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))

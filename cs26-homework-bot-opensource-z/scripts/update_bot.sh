#!/usr/bin/env bash
# سكربت تحديث البوت على الخادم (Orihost / VPS) بعد عمل Sync fork في GitHub
set -euo pipefail

echo "🔄 جاري سحب آخر التحديثات من المستودع..."
git pull --ff-only

echo "📦 التحقق من الحزم البرمجية..."
pip install -q -r requirements-bot.txt

echo "✅ تم تحديث الكود بنجاح. أعد تشغيل خدمة البوت الآن (أو أوقف العملية الحالية وشغّل: python -m bot.main)."

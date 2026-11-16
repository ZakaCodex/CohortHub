"""يربط index.html وsw.js بنسخة ملفات الواجهة (hash) كي يُحمَّل الجديد فقط عند تغيّر الملفات.
شغّله قبل كل نشر:  python scripts/bump_assets.py"""
import hashlib
import pathlib
import re

root = pathlib.Path(__file__).resolve().parent.parent / "public"
h = hashlib.sha1()
for name in ("assets/app.css", "assets/app.js", "assets/features.js", "assets/phase4.js"):
    p = root / name
    if p.exists():
        h.update(p.read_bytes())
v = h.hexdigest()[:8]

index = root / "index.html"
index.write_text(re.sub(r"(assets/(?:app|features|phase4)\.(?:css|js))\?v=\w+", rf"\1?v={v}", index.read_text("utf8")), "utf8")
sw = root / "sw.js"
sw.write_text(re.sub(r"const V = '[^']*'", f"const V = 'bs2-shell-{v}'", sw.read_text("utf8")), "utf8")
print("assets version:", v)

"""فحص الصلاحيات. المصفوفة نفسها بيانات في جدول role_permissions (لا كود)،
وهذا الملف يقيّم القائمة الفعلية التي يعيدها app_bootstrap للمستخدم.

كل صلاحية فعلية: {k: المفتاح, s: own|group|any, g: المجموعة النظرية أو None, c: شرط أو None}
المجموعة (group) = مجموعة نظرية + عملياتها التسع/الثلاث التابعة لها.
"""
from __future__ import annotations

from typing import Iterable, Mapping

Perm = Mapping[str, object]


def practical_to_theory(practical_code: str, practical_groups: Iterable[Mapping[str, str]]) -> str | None:
    for g in practical_groups:
        if g["id"] == practical_code:
            return g["theory"]
    return None


def can(perms: Iterable[Perm], key: str, *, theory_group: str | None = None,
        delegate_absent: bool = False) -> bool:
    """هل يملك المستخدم الصلاحية key؟
    theory_group: المجموعة النظرية المستهدفة (للصلاحيات ذات النطاق group).
    delegate_absent: يُمرَّر True عند غياب المندوب لتفعيل الصلاحيات المشروطة به.
    إن لم تُمرَّر المجموعة نعيد True إذا كان للمستخدم الصلاحية في أي نطاق (للإظهار في الواجهة).
    """
    for p in perms:
        if p.get("k") != key:
            continue
        cond = p.get("c")
        if cond == "delegate_absent" and not delegate_absent:
            continue
        scope = p.get("s")
        if scope in ("any", "own"):
            return True
        if scope == "group":
            if theory_group is None or p.get("g") == theory_group:
                return True
    return False


def allowed_groups(perms: Iterable[Perm], key: str, all_theory: Iterable[str],
                   delegate_absent: bool = False) -> list[str]:
    """المجموعات النظرية التي يستطيع المستخدم العمل عليها بهذه الصلاحية (للمنتقيات)."""
    result: list[str] = []
    for g in all_theory:
        if can(perms, key, theory_group=g, delegate_absent=delegate_absent):
            result.append(g)
    return result

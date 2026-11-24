# -*- coding: utf-8 -*-
"""
first_aid/supplements.py - חבילות הרחבה למאגר העזרה הראשונה: תוספת בלבד, עם גרסאות ו-rollback.

- המאגר המקורי (content/first_aid_guides_he.json) לא נדרס ולא משתנה. חבילת הרחבה נשמרת בתיקייה משלה:
  content/supplements/<package_id>/<version>/, ו-content/supplements/active.json קובע איזו גרסה פעילה
  (ומה היו הגרסאות הקודמות - לחזרה אחורה).
- מזהה שכבר קיים במאגר (או בחבילה שנטענה לפני) - **לא** נכנס; נרשם כהתנגשות לבדיקה. המקור תמיד גובר.
- הסכמה של חבילת 02 שונה מהמקורית (keywords / recognition / actions / setting / sources כ-URL): כאן היא
  מתורגמת למבנה הפנימי בלי לשנות טקסט. שדות חסרים לא "מומצאים": אין קבוצת גיל -> age_groups ריק ("לא צוין").
- חבילה פגומה בזמן ריצה -> נטענת הגרסה הקודמת מההיסטוריה; אין - מדלגים. המאגר המקורי ממשיך לעבוד.
"""

import json
import os
import re
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SUPP_DIR = os.path.join(HERE, "content", "supplements")
ACTIVE_PATH = os.path.join(SUPP_DIR, "active.json")
POLICY_PATH = os.path.join(HERE, "supplement_policy.json")
SUPP_JSON = "first_aid_supplement_he.json"
SUPP_SOURCES_MD = "SOURCES_AND_MEDICAL_REVIEW.md"
REQUIRED = {"id": str, "title": str, "keywords": list, "urgency": str, "recognition": list, "actions": list,
            "do_not": list, "emergency": str, "sources": list}


def load_policy(path=POLICY_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"urgency_map": {}, "default_review_status": "pending_medical_review", "packages": {}}


def read_active(path=ACTIVE_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d.get("packages"), dict) else {"packages": {}}
    except (OSError, ValueError, AttributeError):
        return {"packages": {}}


def write_active(data, path=ACTIVE_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def normalize_setting(v):
    """setting מגיע לפעמים כרשימה ולפעמים כמחרוזת "בית/רחוב/טיול" (בחבילה 02) - מאחדים לרשימה."""
    if isinstance(v, str):
        v = re.split(r"[/,،;]", v)
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def validate_raw(g):
    if not isinstance(g, dict):
        return ["המדריך אינו אובייקט"]
    probs = [f"חסר/שגוי {k}" for k, t in REQUIRED.items() if not isinstance(g.get(k), t)]
    for k in ("keywords", "recognition", "actions", "do_not", "sources"):
        if isinstance(g.get(k), list) and not all(isinstance(x, str) and x.strip() for x in g[k]):
            probs.append(f"{k} מכיל ערך שאינו טקסט")
    if isinstance(g.get("actions"), list) and not g["actions"]:
        probs.append("אין צעדים")
    if not str(g.get("id", "")).strip() or not str(g.get("title", "")).strip():
        probs.append("מזהה/כותרת ריקים")
    return probs


def parse_sources_md(md_text):
    """שורות "- Title: URL" -> {url: title}."""
    out = {}
    for line in md_text.splitlines():
        m = re.match(r"^\s*-\s*(.+?):\s*(https?://\S+)\s*$", line)
        if m:
            out[m.group(2)] = m.group(1).strip()
    return out


def convert(g, meta, policy, pkg_policy, version, source_titles):
    """מדריך מחבילת הרחבה -> מבנה פנימי (טקסטים כלשונם)."""
    umap = policy.get("urgency_map", {})
    safety = g["id"] in pkg_policy.get("safety_procedures", [])
    setting = normalize_setting(g.get("setting"))
    sources = []
    for url in g["sources"]:
        host = urllib.parse.urlparse(url).hostname or url
        sources.append({"id": url, "title": source_titles.get(url) or host, "url": url})
    notes = g.get("notes")
    return {
        "id": g["id"], "title": g["title"],
        "category": pkg_policy.get("category_safety" if safety else "category_medical", "חבילת הרחבה"),
        "urgency": umap.get(g["urgency"], "high"),
        "summary": g["recognition"][0] if g["recognition"] else g["emergency"],
        "search_terms": list(g["keywords"]) + setting,
        "age_groups": [],
        "call_emergency_now": g["emergency"],
        "recognition": list(g["recognition"]),
        "what_to_do": list(g["actions"]), "do_not": list(g["do_not"]), "red_flags": [],
        "important_notes": [notes] if isinstance(notes, str) and notes.strip() else [],
        "source_ids": [s["id"] for s in sources],
        "medical_review_status": g.get("review_status") or policy.get("default_review_status", "pending_medical_review"),
        "content_updated": meta.get("updated") or "",
        "setting": setting,
        "kind": "safety" if safety else "medical",
        "package_id": meta.get("package_id"), "package_version": version,
        "official_dynamic": (pkg_policy.get("official_dynamic") or {}).get(g["id"]),
        "authority_review": (pkg_policy.get("authority_review") or {}).get(g["id"]),
        "overlaps": (pkg_policy.get("overlaps_with_base") or {}).get(g["id"]),
        "_sources": sources,
    }


def load_version(pkg_id, version, policy, supp_dir=SUPP_DIR):
    """-> (meta, [converted guides], errors). זורק ContentError אם החבילה לא שמישה כלל."""
    d = os.path.join(supp_dir, pkg_id, version)
    with open(os.path.join(d, SUPP_JSON), encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("guides"), list):
        raise ValueError("מבנה חבילה לא תקין (חסר guides)")
    meta = {k: v for k, v in data.items() if k != "guides"}
    titles = {}
    try:
        with open(os.path.join(d, SUPP_SOURCES_MD), encoding="utf-8") as fh:
            titles = parse_sources_md(fh.read())
    except OSError:
        pass
    pkg_policy = (policy.get("packages") or {}).get(pkg_id, {})
    out, errors, seen = [], [], set()
    for g in data["guides"]:
        probs = validate_raw(g)
        if not probs and g["id"] in seen:
            probs = ["מזהה כפול בתוך החבילה"]
        if probs:
            errors.append(f"{pkg_id}/{version}: {(g or {}).get('id', '?') if isinstance(g, dict) else '?'}: "
                          + "; ".join(probs))
            continue
        seen.add(g["id"])
        out.append(convert(g, meta, policy, pkg_policy, version, titles))
    if not out:
        raise ValueError("אין בחבילה אף מדריך תקין")
    return meta, out, errors


def load_active(policy=None, active=None, supp_dir=SUPP_DIR):
    """-> [(pkg_id, version, meta, guides)], errors. גרסה פגומה -> הגרסה הקודמת בהיסטוריה."""
    policy = policy or load_policy()
    active = active or read_active(os.path.join(supp_dir, "active.json"))
    loaded, errors = [], []
    for pkg_id, st in sorted(active.get("packages", {}).items()):
        candidates = [st.get("active")] + [v for v in reversed(st.get("history", [])) if v != st.get("active")]
        for i, ver in enumerate(v for v in candidates if v):
            try:
                meta, guides, errs = load_version(pkg_id, ver, policy, supp_dir)
            except (OSError, ValueError) as exc:
                errors.append(f"{pkg_id}/{ver} לא נטענה ({exc})" + (" - מנסה גרסה קודמת" if i == 0 else ""))
                continue
            errors.extend(errs)
            if i:
                errors.append(f"{pkg_id}: נטענה גרסה קודמת {ver} במקום {st.get('active')}")
            loaded.append((pkg_id, ver, meta, guides))
            break
    return loaded, errors

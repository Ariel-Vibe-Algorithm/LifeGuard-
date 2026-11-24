# -*- coding: utf-8 -*-
"""
build_first_aid.py - עדכון מאגר העזרה הראשונה (למתחזק/ת הפרויקט). רץ מקומית, בלי רשת.

  python tools/build_first_aid.py --from "C:\\path\\LifeGuard_First_Aid_Offline"   # ייבוא חבילה חדשה
  python tools/build_first_aid.py --check                                          # רק אימות של מה שבמאגר
  python tools/build_first_aid.py --supplement "C:\path\LifeGuard_First_Aid_Supplement_02"  # חבילת הרחבה (תוספת)
  python tools/build_first_aid.py --rollback lifeguard_first_aid_supplement_02           # חזרה לגרסה הקודמת

חבילת הרחבה: נשמרת ב-first_aid/content/supplements/<package_id>/<version>/ (גרסה קיימת לא נדרסת), נבדקת,
ורק אם תקינה - מופעלת ב-active.json. המאגר המקורי לא נוגע. מזהים שכבר קיימים - לא נכנסים (דוח התנגשויות).

מה הוא עושה:
1. מעתיק מהחבילה: content/first_aid_guides_he.json, content/first_aid_guides_he.md, SOURCES_AND_REVIEW.md,
   manifest.json, README.md -> first_aid/content/ (התוכן נפרד מהקוד - מבקר/ת רפואי/ת מעדכן/ת רק את החבילה).
2. מפיק first_aid/content/sources.json מתוך SOURCES_AND_REVIEW.md (מזהה, כותרת, קישור, תיאור).
3. מאמת כל מדריך (שדות חובה, דחיפות, קבוצות גיל, מקורות קיימים, תאריך, סטטוס אישור רפואי) - שגיאה = יציאה
   עם קוד 1 והקבצים במאגר לא מוחלפים.
4. בונה מחדש first_aid/content/search_index_he.json מהמדריכים (תמיד תואם ל-JSON הראשי).
5. לא מסיר ולא משנה medical_review_status - מדריך בלי סטטוס = שגיאה.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from first_aid import library as L  # noqa: E402

DEST = L.CONTENT_DIR
PACK_FILES = (("content/first_aid_guides_he.json", "first_aid_guides_he.json"),
              ("content/first_aid_guides_he.md", "first_aid_guides_he.md"),
              ("SOURCES_AND_REVIEW.md", "SOURCES_AND_REVIEW.md"),
              ("manifest.json", "manifest.json"), ("README.md", "README.md"))


def parse_sources(md_text):
    """'## ID – Publisher – Title' + שורת URL + שורת תיאור -> רשימת מקורות."""
    out = []
    blocks = re.split(r"^## ", md_text, flags=re.M)
    for b in blocks[1:]:
        lines = [x.strip() for x in b.strip().splitlines() if x.strip()]
        head = lines[0]
        m = re.match(r"^([A-Z0-9_]+)\s+[–-]\s+(.+)$", head)
        if not m:
            continue
        url = next((x for x in lines[1:] if x.startswith("http")), None)
        desc = next((x for x in lines[1:] if not x.startswith("http")), "")
        out.append({"id": m.group(1), "title": m.group(2), "url": url, "description": desc})
    return out


def build_index(data):
    return {"schema_version": data.get("schema_version"), "language": data.get("language"),
            "updated": data.get("updated"),
            "items": [{k: g[k] for k in ("id", "title", "category", "urgency", "search_terms")}
                      for g in data["guides"]]}


def check(guides_path, sources_path):
    lib = L.load(guides_path, sources_path, supplements=False)
    with open(guides_path, encoding="utf-8") as fh:
        n_raw = len(json.load(fh).get("guides", []))
    problems = list(lib.errors)
    if len(lib.guides) != n_raw:
        problems.append(f"{n_raw - len(lib.guides)} מדריכים נפסלו")
    if not lib.meta.get("medical_review_required", True) is True:
        problems.append("medical_review_required הוסר - אסור בלי אישור מתועד")
    return lib, problems


SUPP_FILES = ("content/first_aid_supplement_he.json", "content/first_aid_supplement_he.md",
              "SOURCES_AND_MEDICAL_REVIEW.md", "manifest.json", "README.md", "CLAUDE_SUPPLEMENT_PROMPT.md")


def import_supplement(src, supp_dir=None):
    """-> (קוד יציאה, דוח). תוספת בלבד: לא נוגע במאגר המקורי, לא דורס גרסה קיימת."""
    from first_aid import supplements as SP
    supp_dir = supp_dir or SP.SUPP_DIR
    with open(os.path.join(src, "manifest.json"), encoding="utf-8") as fh:
        man = json.load(fh)
    pkg, ver = man.get("package_id"), str(man.get("version") or "")
    if not pkg or not re.match(r"^[A-Za-z0-9_.\-]+$", pkg) or not re.match(r"^[0-9][0-9A-Za-z_.\-]*$", ver):
        return 1, ["manifest.json: חסר package_id/version תקינים"]
    dest = os.path.join(supp_dir, pkg, ver)
    tmp = tempfile.mkdtemp()
    for rel in SUPP_FILES:
        if os.path.exists(os.path.join(src, rel)):
            shutil.copy2(os.path.join(src, rel), os.path.join(tmp, os.path.basename(rel)))
    tmp_root = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp_root, pkg))
    shutil.copytree(tmp, os.path.join(tmp_root, pkg, ver))
    policy = SP.load_policy()
    try:
        meta, guides, errs = SP.load_version(pkg, ver, policy, tmp_root)
    except (OSError, ValueError) as exc:
        return 1, [f"החבילה לא תקינה: {exc} - לא יובאה"]
    report = [f"חבילה {pkg} גרסה {ver}: {len(guides)} מדריכים תקינים"] + [f"⚠️ {e}" for e in errs]
    if errs:
        return 1, report + ["❌ יש מדריכים פגומים - החבילה לא הופעלה (תקנו ונסו שוב)"]
    base = L.load(supplements=False)
    active = SP.read_active(os.path.join(supp_dir, "active.json"))
    other_ids = set()
    for p_id, st in active.get("packages", {}).items():
        if p_id != pkg and st.get("active"):
            try:
                other_ids |= {g["id"] for g in SP.load_version(p_id, st["active"], policy, supp_dir)[1]}
            except (OSError, ValueError):
                pass
    dups = [g["id"] for g in guides if g["id"] in base.by_id or g["id"] in other_ids]
    report.append(f"יתווספו {len(guides) - len(dups)} מדריכים; {len(dups)} מזהים כבר קיימים ולא ייטענו: {dups}")
    overlaps = [(g["id"], g["overlaps"]) for g in guides if g.get("overlaps")]
    if overlaps:
        report.append("נושאים חופפים (מזהה שונה - שניהם יוצגו, לבדיקה רפואית): "
                      + ", ".join(f"{a}~{b}" for a, b in overlaps))
    pending = sum(1 for g in guides if "pending" in g["medical_review_status"])
    report.append(f"סטטוס אישור: {pending}/{len(guides)} ממתינים לבדיקה רפואית")
    if os.path.exists(dest):
        same = all(open(os.path.join(dest, f), "rb").read() == open(os.path.join(tmp, f), "rb").read()
                   for f in os.listdir(tmp) if os.path.exists(os.path.join(dest, f)))
        if not same:
            return 1, report + [f"❌ הגרסה {ver} כבר קיימת עם תוכן שונה - העלו מספר גרסה (לא דורסים)"]
    else:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(tmp, dest)
    st = active["packages"].setdefault(pkg, {"active": None, "history": []})
    if st.get("active") and st["active"] != ver and st["active"] not in st["history"]:
        st["history"].append(st["active"])
    if ver not in st["history"]:
        st["history"].append(ver)
    st["active"] = ver
    SP.write_active(active, os.path.join(supp_dir, "active.json"))
    return 0, report + [f"✅ הופעלה גרסה {ver} ({dest})"]


def rollback(pkg, supp_dir=None):
    from first_aid import supplements as SP
    supp_dir = supp_dir or SP.SUPP_DIR
    active = SP.read_active(os.path.join(supp_dir, "active.json"))
    st = active.get("packages", {}).get(pkg)
    if not st or not st.get("active"):
        return 1, [f"החבילה {pkg} לא פעילה"]
    hist = [v for v in st.get("history", []) if v != st["active"]]
    if hist:
        prev = hist[-1]
        st["active"] = prev
        msg = f"✅ {pkg}: חזרה לגרסה {prev}"
    else:
        st["active"] = None
        msg = f"✅ {pkg}: אין גרסה קודמת - החבילה הושבתה (המאגר המקורי ממשיך לעבוד)"
    SP.write_active(active, os.path.join(supp_dir, "active.json"))
    return 0, [msg]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", help="תיקיית חבילת התוכן (LifeGuard_First_Aid_Offline)")
    ap.add_argument("--supplement", help="תיקיית חבילת הרחבה (תוספת בלבד)")
    ap.add_argument("--rollback", help="package_id של חבילת הרחבה לחזרה לגרסה קודמת")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.supplement or a.rollback:
        code, report = import_supplement(a.supplement) if a.supplement else rollback(a.rollback)
        for line in report:
            print(line)
        return code
    if a.src:
        tmp = tempfile.mkdtemp()
        for rel, name in PACK_FILES:
            p = os.path.join(a.src, rel)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(tmp, name))
        with open(os.path.join(tmp, "SOURCES_AND_REVIEW.md"), encoding="utf-8") as fh:
            sources = parse_sources(fh.read())
        with open(os.path.join(tmp, "sources.json"), "w", encoding="utf-8") as fh:
            json.dump({"generated_from": "SOURCES_AND_REVIEW.md", "sources": sources}, fh, ensure_ascii=False, indent=1)
        lib, problems = check(os.path.join(tmp, "first_aid_guides_he.json"), os.path.join(tmp, "sources.json"))
        if problems:
            print("❌ החבילה לא תקינה - המאגר לא עודכן:")
            for p in problems:
                print("  -", p)
            return 1
        with open(os.path.join(tmp, "first_aid_guides_he.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        with open(os.path.join(tmp, "search_index_he.json"), "w", encoding="utf-8") as fh:
            json.dump(build_index(data), fh, ensure_ascii=False, indent=1)
        os.makedirs(DEST, exist_ok=True)
        for name in os.listdir(tmp):
            shutil.copy2(os.path.join(tmp, name), os.path.join(DEST, name))
        print(f"✅ יובאו {len(lib.guides)} מדריכים, {len(sources)} מקורות -> {DEST}")
        return 0
    lib, problems = check(L.GUIDES_PATH, L.SOURCES_PATH)
    full = L.load()
    for p in full.packages:
        print(f"חבילת הרחבה {p['package_id']} {p['version']}: נוספו {p['added']}/{p['total']}")
    for c in full.conflicts:
        print(f"  ↔ {c['id']}: {c['reason']}")
    problems += [e for e in full.errors if e not in lib.errors]
    with open(L.GUIDES_PATH, encoding="utf-8") as fh:
        idx_ok = build_index(json.load(fh)) == json.load(open(L.INDEX_PATH, encoding="utf-8"))
    if not idx_ok:
        problems.append("search_index_he.json לא תואם למדריכים - הריצו עם --from")
    for p in problems:
        print("❌", p)
    print(f"{'✅' if not problems else '❌'} {len(lib.guides)} מדריכים, {len(lib.sources)} מקורות")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

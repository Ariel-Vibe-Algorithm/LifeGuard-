# -*- coding: utf-8 -*-
"""
first_aid/library.py - מאגר עזרה ראשונה אופליין: טעינה, אימות, חיפוש, וגיל.

עקרונות (CLAUDE_IMPLEMENTATION_PROMPT.md של חבילת התוכן):
- **אופליין בלבד.** המודול לא מייבא שום ספריית רשת. התוכן נטען מקבצים מקומיים בתיקייה content/.
- **התוכן לא נוצר ולא משתנה כאן.** הטקסטים מוצגים כפי שהם (טיוטה שמחייבת אישור רפואי). אין AI בזמן ריצה.
- **אימות מבנה.** מדריך פגום מדולג (ומדווח), השאר ממשיכים לעבוד. קובץ פגום לגמרי = רשימה ריקה + שגיאה -
  המסך עדיין מציג 101.
- **גיל.** כשהטכניקה שונה לפי גיל (חנק, החייאה) ולא ידוע הגיל - שואלים לפני שמציגים צעדים.

חיפוש: אינדקס בזיכרון שנבנה מה-JSON. נרמול עברית (ניקוד, גרש/גרשיים, אותיות סופיות, כתיב מלא/חסר,
אותיות שימוש בתחילת מילה, רבים/נקבה), התאמת שגיאות הקלדה (מרחק עריכה), ומילים נרדפות/ביטויים מדוברים/
ערבית/אנגלית מקובץ נפרד (synonyms.json - אוצר מילים לחיפוש, לא הוראות רפואיות).
"""

import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CONTENT_DIR = os.path.join(HERE, "content")
GUIDES_PATH = os.path.join(CONTENT_DIR, "first_aid_guides_he.json")
INDEX_PATH = os.path.join(CONTENT_DIR, "search_index_he.json")
SOURCES_PATH = os.path.join(CONTENT_DIR, "sources.json")
SYNONYMS_PATH = os.path.join(HERE, "synonyms.json")

URGENCIES = ("critical", "high", "medium", "low")
URGENCY_HE = {"critical": "מסכן חיים", "high": "דחוף", "medium": "בינוני", "low": "לא דחוף"}
URGENCY_BOOST = {"critical": 0.35, "high": 0.2, "medium": 0.05, "low": 0.0}
AGE_GROUPS = ("adult", "child", "child_1_plus", "infant_under_1", "pregnant_person", "newborn")
AGE_HE = {"adult": "מבוגר", "child": "ילד", "child_1_plus": "ילד מעל גיל שנה", "infant_under_1": "תינוק מתחת לגיל שנה",
          "pregnant_person": "הריון", "newborn": "יילוד"}
REQUIRED = {"id": str, "title": str, "category": str, "urgency": str, "summary": str, "search_terms": list,
            "age_groups": list, "call_emergency_now": str, "what_to_do": list, "do_not": list, "red_flags": list,
            "source_ids": list, "medical_review_status": str, "content_updated": str}
LIST_OF_STR = ("search_terms", "age_groups", "what_to_do", "do_not", "red_flags", "source_ids", "important_notes")

# גיל שנבחר במסך -> קבוצות הגיל במדריכים
AGE_CHOICES = (("adult", "מבוגר / נער"), ("child", "ילד מעל גיל שנה"), ("infant", "תינוק מתחת לגיל שנה"))
AGE_MATCH = {"adult": {"adult"}, "child": {"child", "child_1_plus"}, "infant": {"infant_under_1"}}
# משפחות שבהן הטכניקה שונה לפי גיל: לא מציגים צעדים לפני שיודעים את הגיל
AGE_FAMILIES = {
    "choking": {"adult": "choking_adult_child", "child": "choking_adult_child", "infant": "choking_infant"},
    "cpr": {"adult": "cpr_adult", "child": "cpr_child_infant", "infant": "cpr_child_infant"},
}
# מדריך שמכיל בתוכו הנחיות שונות לילד ולתינוק (בצעדים מסומנים "בילד:" / "בתינוק:")
MIXED_AGE_NOTE = {
    "cpr_child_infant": {"child": "בצעדים יש הנחיות נפרדות - פעלו לפי מה שמסומן \"בילד\".",
                         "infant": "בצעדים יש הנחיות נפרדות - פעלו לפי מה שמסומן \"בתינוק\"."},
}


class ContentError(Exception):
    pass


# ---------------------------------------------------------------- נרמול
_NIQQUD = re.compile(r"[֑-ׇ]")
_FINALS = str.maketrans({"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"})
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_PREFIXES2 = ("וה", "שה", "כש", "מה", "לה", "בה", "וב", "ול", "ומ", "וש", "שב", "של", "שמ")
_PREFIXES1 = ("ו", "ה", "ב", "ל", "מ", "ש", "כ")
_SUFFIXES = ("ים", "ות", "ה", "ת", "י")
_ARABIC_DIACRITICS = re.compile(r"[ً-ٰٟ]")


def normalize(text):
    t = unicodedata.normalize("NFKC", str(text or "")).lower()
    t = _NIQQUD.sub("", t)
    t = _ARABIC_DIACRITICS.sub("", t).replace("ـ", "")
    t = t.replace("״", "").replace("׳", "").replace('"', "").replace("'", "")
    t = _PUNCT.sub(" ", t).replace("_", " ")
    t = t.translate(_FINALS)
    t = re.sub(r"וו+", "ו", t)          # כתיב מלא/חסר: כווייה / כוויה / כויה
    t = re.sub(r"יי+", "י", t)
    for a, b in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _is_hebrew(tok):
    return any("א" <= c <= "ת" for c in tok)


def stems(tok):
    """צורות בסיס אפשריות למילה: בלי אותיות שימוש בתחילה ובלי סיומות רבים/נקבה."""
    out = {tok}
    if not _is_hebrew(tok):
        if len(tok) > 4 and tok.endswith("s"):
            out.add(tok[:-1])
        if tok.startswith("ال") and len(tok) > 4:
            out.add(tok[2:])
        return out
    bases = {tok}
    for p in _PREFIXES2:
        if tok.startswith(p) and len(tok) - 2 >= 3:
            bases.add(tok[2:])
    for p in _PREFIXES1:
        if tok.startswith(p) and len(tok) - 1 >= 3:
            bases.add(tok[1:])
    for b in bases:
        out.add(b)
        for s in _SUFFIXES:
            if b.endswith(s) and len(b) - len(s) >= 3:
                out.add(b[: -len(s)])
    return out


def _edit_distance(a, b, limit=2):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def tokens(text):
    return [t for t in normalize(text).split() if len(t) >= 2 or t.isdigit()]


# ---------------------------------------------------------------- טעינה ואימות
def validate_guide(g, known_sources=None):
    """-> רשימת בעיות (ריקה = תקין)."""
    problems = []
    if not isinstance(g, dict):
        return ["המדריך אינו אובייקט"]
    for k, typ in REQUIRED.items():
        if k not in g:
            problems.append(f"חסר שדה {k}")
        elif not isinstance(g[k], typ):
            problems.append(f"שדה {k} מסוג שגוי")
    for k in LIST_OF_STR:
        if isinstance(g.get(k), list) and not all(isinstance(x, str) and x.strip() for x in g[k]):
            problems.append(f"שדה {k} מכיל ערך שאינו טקסט")
    if g.get("urgency") not in URGENCIES:
        problems.append(f"דחיפות לא מוכרת: {g.get('urgency')}")
    if isinstance(g.get("age_groups"), list):
        bad = [a for a in g["age_groups"] if a not in AGE_GROUPS]
        if bad or not g["age_groups"]:
            problems.append(f"קבוצות גיל לא תקינות: {bad or 'ריק'}")
    for k in ("what_to_do", "title", "medical_review_status"):
        if not g.get(k):
            problems.append(f"שדה {k} ריק")
    if known_sources is not None and isinstance(g.get("source_ids"), list):
        missing = [s for s in g["source_ids"] if s not in known_sources]
        if missing:
            problems.append(f"מקורות לא מוכרים: {missing}")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(g.get("content_updated", ""))):
        problems.append("content_updated אינו תאריך YYYY-MM-DD")
    return problems


def load(guides_path=GUIDES_PATH, sources_path=SOURCES_PATH, synonyms_path=SYNONYMS_PATH, supplements=True,
         supp_dir=None):
    """-> Library. לא זורק על תוכן פגום - Library.errors מפרט; מדריכים פגומים מדולגים.
    supplements: חבילות הרחבה פעילות (first_aid/supplements.py) נוספות *אחרי* המאגר המקורי - תוספת בלבד."""
    errors = []
    meta, raw = {}, []
    try:
        with open(guides_path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not isinstance(data.get("guides"), list):
            raise ContentError("מבנה הקובץ לא תקין (חסר guides)")
        meta = {k: v for k, v in data.items() if k != "guides"}
        raw = data["guides"]
    except (OSError, ValueError, ContentError) as exc:
        errors.append(f"לא ניתן לטעון את קובץ המדריכים: {exc}")
    sources = {}
    try:
        with open(sources_path, encoding="utf-8") as fh:
            sources = {s["id"]: s for s in json.load(fh).get("sources", []) if isinstance(s, dict) and s.get("id")}
    except (OSError, ValueError, AttributeError) as exc:
        errors.append(f"קובץ המקורות לא נטען: {exc}")
    synonyms = {}
    try:
        with open(synonyms_path, encoding="utf-8") as fh:
            synonyms = json.load(fh).get("phrases", {})
    except (OSError, ValueError, AttributeError) as exc:
        errors.append(f"קובץ מילים נרדפות לא נטען: {exc}")
    guides, seen = [], set()
    for g in raw:
        probs = validate_guide(g, set(sources) if sources else None)
        if not probs and g["id"] in seen:
            probs = ["מזהה כפול"]
        if probs:
            errors.append(f"{(g or {}).get('id', '?') if isinstance(g, dict) else '?'}: " + "; ".join(probs))
            continue
        seen.add(g["id"])
        guides.append(g)
    packages, conflicts = [], []
    if supplements:
        from first_aid import supplements as SP
        loaded, serr = SP.load_active(supp_dir=supp_dir or SP.SUPP_DIR)
        errors.extend(serr)
        nums = dict(meta.get("emergency_numbers") or {})
        for pkg_id, ver, pmeta, pguides in loaded:
            added = 0
            for g in pguides:
                if g["id"] in seen:     # המקור (או חבילה קודמת) גובר - לא דורסים
                    conflicts.append({"id": g["id"], "package": pkg_id, "version": ver,
                                      "reason": "מזהה קיים - נשמר המדריך הקיים, הגרסה מהחבילה לא נטענה"})
                    continue
                for src in g.pop("_sources"):
                    sources.setdefault(src["id"], src)
                seen.add(g["id"])
                guides.append(g)
                added += 1
            for k, v in (pmeta.get("emergency_numbers") or {}).items():
                key = "poison_center" if k == "israel_poison_center_rambam" else k
                nums.setdefault(key, v)
            packages.append({"package_id": pkg_id, "version": ver, "added": added, "total": len(pguides),
                             "updated": pmeta.get("updated")})
        if nums:
            meta["emergency_numbers"] = nums
    lib = Library(guides, meta, sources, synonyms, errors)
    lib.packages, lib.conflicts = packages, conflicts
    return lib


# ---------------------------------------------------------------- גיל
_INFANT = ("תינוק", "תינוקת", "תינוקות", "בייבי", "baby", "infant", "رضيع", "رضيعه")
_CHILD = ("ילד", "ילדה", "ילדים", "פעוט", "פעוטה", "child", "kid", "toddler", "طفل", "طفله", "ولد")
_ADULT = ("מבוגר", "מבוגרת", "אישה", "גבר", "אבא", "אמא", "סבא", "סבתא", "בעלי", "אשתי", "adult", "man", "woman",
          "رجل", "امراه")


_AGE_WORDS = {normalize(w) for w in _INFANT + _CHILD + _ADULT}


def detect_age(query):
    """גיל מתוך החיפוש ("תינוק נחנק" / "ילד בן 3" / "בן 4 חודשים"). None = לא ידוע (לא מנחשים)."""
    q = normalize(query)
    toks = q.split()
    st = set()
    for t in toks:
        st |= stems(t)
    if re.search(r"\b\d{1,2} ?חודש", q) or "חודשים" in q:
        return "infant"
    if any(normalize(w) in st for w in _INFANT):
        return "infant"
    m = re.search(r"\b(?:בנ|בת) (\d{1,2})\b", q)
    if m:
        n = int(m.group(1))
        return "child" if 1 <= n <= 8 else None
    if any(normalize(w) in st for w in _CHILD):
        return "child"
    if any(normalize(w) in st for w in _ADULT):
        return "adult"
    return None


# ---------------------------------------------------------------- אינדקס וחיפוש
MIN_RELATIVE_SCORE = 0.35
AGE_SPECIFIC_BOOST = 1.5
FIELD_WEIGHTS = (("title", 5.0), ("search_terms", 4.0), ("summary", 2.0), ("category", 1.5), ("setting", 1.0),
                 ("body", 0.7))


class Library:
    def __init__(self, guides, meta=None, sources=None, synonyms=None, errors=None):
        self.guides = guides
        self.by_id = {g["id"]: g for g in guides}
        self.meta = meta or {}
        self.sources = sources or {}
        self.errors = list(errors or [])
        self.packages, self.conflicts = [], []
        self.synonyms = {normalize(k): [normalize(x) for x in v] for k, v in (synonyms or {}).items()}
        self._index = {g["id"]: self._index_guide(g) for g in guides}
        self._phrases = {g["id"]: [normalize(x) for x in [g["title"]] + g["search_terms"]] for g in guides}

    @staticmethod
    def _field_texts(g):
        body = (g["what_to_do"] + g["do_not"] + g["red_flags"] + g.get("important_notes", [])
                + g.get("recognition", []) + [g["call_emergency_now"]])
        return {"title": [g["title"]], "search_terms": g["search_terms"], "summary": [g["summary"]],
                "category": [g["category"]], "setting": g.get("setting", []), "body": body}

    def _index_guide(self, g):
        idx = {}
        for field, texts in self._field_texts(g).items():
            st = {}
            for text in texts:
                for t in tokens(text):
                    for s in stems(t):
                        st.setdefault(s, t)
            idx[field] = st
        return idx

    def _expand(self, q):
        """ביטויים מדוברים / ערבית / אנגלית -> מונחי חיפוש נוספים (במשקל מופחת)."""
        extra = []
        padded = f" {q} "
        for phrase, terms in self.synonyms.items():
            if f" {phrase} " in padded:
                extra.extend(terms)
        return extra

    @staticmethod
    def _token_match(qt, stems_index):
        """-> איכות התאמה 0..1 של מילת חיפוש מול מילים בשדה."""
        qs = stems(qt)
        if qs & stems_index.keys():
            return 1.0
        best = 0.0
        for s in stems_index:
            for q in qs:
                # התאמת תחילית רק ממילה של 4 אותיות ומעלה: "ירי" לא יתאים ל"ירידה", "חבל" לא ל"חבלה"
                if len(q) >= 4 and (s.startswith(q) or q.startswith(s)) and min(len(s), len(q)) >= 4:
                    best = max(best, 0.8)
                elif len(q) >= 4 and len(s) >= 4:
                    d = _edit_distance(q, s, 2)
                    if d <= (1 if len(q) < 7 else 2):
                        best = max(best, 0.65)
        return best

    def search(self, query, age=None, limit=8):
        """
        -> {"results": [{"id","title","urgency","urgency_he","summary","score","age_note"}], "ambiguous": bool,
            "age": גיל שזוהה/נבחר, "query_norm": ...}. תוצאות רק לשאילתה לא ריקה.
        """
        qn = normalize(query)
        q_tokens = [t for t in qn.split() if len(t) >= 2]
        if not q_tokens:
            return {"results": [], "ambiguous": False, "age": age, "query_norm": qn}
        age = age or detect_age(query)
        # מילות גיל ("תינוק", "ילד בן 3") הן מסננת, לא נושא: לא נספרות בניקוד (אחרת "חום לתינוק" מעלה גם חנק בתינוק)
        topic = [t for t in q_tokens if not (stems(t) & _AGE_WORDS) and not t.isdigit()
                 and t not in ("בנ", "בת", "חודש", "חודשים", "שנה", "שנים")]
        q_tokens = topic or q_tokens
        extra = [t for term in self._expand(qn) for t in term.split()]
        scored = []
        for gid, idx in self._index.items():
            total, strong = 0.0, False
            for weight_mul, toks in ((1.0, q_tokens), (0.8, extra)):
                for qt in toks:
                    best = 0.0
                    for field, w in FIELD_WEIGHTS:
                        m = self._token_match(qt, idx[field])
                        if m:
                            best = max(best, w * m)
                            if field != "body" and m >= 0.65:
                                strong = True
                    total += best * weight_mul
            if len(q_tokens) >= 2 and any(qn in p or p in qn for p in self._phrases[gid] if len(p) >= 4):
                total += 6.0
                strong = True
            if not strong:
                continue          # התאמה רק בגוף הטקסט ("אל"/"מים") אינה תוצאה
            g = self.by_id[gid]
            score = total / max(1, len(q_tokens)) * (1 + URGENCY_BOOST[g["urgency"]])
            if age and "adult" not in g["age_groups"] and AGE_MATCH.get(age, set()) & set(g["age_groups"]):
                score *= AGE_SPECIFIC_BOOST     # מדריך שנכתב במיוחד לגיל הזה (למשל חום בילדים ותינוקות)
            scored.append((score, URGENCIES.index(g["urgency"]), gid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        if scored:   # התאמות חלשות (פחות מ-35% מהמובילה) לא מוצגות - פחות רעש ברגע לחוץ
            scored = [x for x in scored if x[0] >= MIN_RELATIVE_SCORE * scored[0][0]]
        results = []
        for score, _, gid in scored[:limit]:
            g = self.by_id[gid]
            results.append({"id": gid, "title": g["title"], "urgency": g["urgency"],
                            "urgency_he": URGENCY_HE[g["urgency"]], "summary": g["summary"],
                            "score": round(score, 2), "age_note": self.age_note(gid, age)})
        if age:   # בתוך משפחה תלוית-גיל: המדריך של הגיל שנבחר קודם, והאחר לא מוצג
            keep = set()
            for fam in AGE_FAMILIES.values():
                if any(r["id"] in fam.values() for r in results):
                    keep.add(fam[age])
            fam_ids = {i for fam in AGE_FAMILIES.values() for i in fam.values()}
            results = [r for r in results if r["id"] not in fam_ids or r["id"] in keep]
        ambiguous = len(results) >= 2 and results[1]["score"] >= 0.8 * results[0]["score"]
        return {"results": results, "ambiguous": ambiguous, "age": age, "query_norm": qn}

    # ---------- גיל
    def family_of(self, gid):
        for name, fam in AGE_FAMILIES.items():
            if gid in fam.values():
                return name
        return None

    def needs_age(self, gid, age):
        """האם לשאול גיל לפני הצגת הצעדים (משפחה שבה הטכניקה שונה לפי גיל, והגיל לא ידוע)."""
        return age is None and (self.family_of(gid) is not None or gid in MIXED_AGE_NOTE)

    def resolve(self, gid, age):
        """מדריך + גיל -> המדריך הנכון לגיל (למשל חנק + תינוק -> choking_infant)."""
        fam = self.family_of(gid)
        if fam and age in AGE_FAMILIES[fam]:
            return AGE_FAMILIES[fam][age]
        return gid

    def age_note(self, gid, age):
        g = self.by_id.get(gid)
        if not g or not age:
            return None
        if gid in MIXED_AGE_NOTE and age in MIXED_AGE_NOTE[gid]:
            return MIXED_AGE_NOTE[gid][age]
        if not g["age_groups"]:
            return None          # חבילת הרחבה בלי קבוצת גיל - לא מנחשים (מוצג "לא צוין")
        if not (AGE_MATCH.get(age, set()) & set(g["age_groups"])):
            return (f"המדריך נכתב עבור: {', '.join(AGE_HE[a] for a in g['age_groups'])}. "
                    f"ל{dict(AGE_CHOICES)[age]} - הזעיקו מד״א 101 ופעלו לפי הנחיות המוקד.")
        return None

    # ---------- תצוגה
    def guide(self, gid):
        return self.by_id.get(gid)

    def sources_for(self, gid):
        g = self.by_id[gid]
        return [self.sources.get(s, {"id": s, "title": s, "url": None}) for s in g["source_ids"]]

    @property
    def emergency_numbers(self):
        return self.meta.get("emergency_numbers") or {"mda": "101"}

    @property
    def disclaimer(self):
        return self.meta.get("disclaimer") or ("מידע עזרה ראשונה כללי. אינו תחליף ל-101, לטיפול מקצועי "
                                               "או להכשרה מעשית.")


_LIB = {"lib": None}


def library():
    if _LIB["lib"] is None:
        _LIB["lib"] = load()
    return _LIB["lib"]

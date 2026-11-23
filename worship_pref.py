# -*- coding: utf-8 -*-
"""
worship_pref.py - לאיזה מקום תפילה להפנות בסכנה, **רק כשאין מקום בטוח אחר קרוב** (map_module._choose).

למה העדפה ולא "מגזר": המטרה היא לדעת לאן מתאים להפנות (בית כנסת / מסגד / כנסייה), לא לתייג אנשים. השאלה
אופציונלית, ניתנת לשינוי בכל עת, והתשובה נשמרת **רק במכשיר** (מוצפנת ב-DPAPI של Windows) - היא לא נשלחת
לשרת ולא לאנשי הקשר. העדפה דתית היא מידע רגיש לפי חוק הגנת הפרטיות, ולכן מינימום: ערך אחד, מקומי.

ברירת מחדל ("synagogue") = ההתנהגות שהייתה לפני השינוי (בתי כנסת היו בין המקומות הציבוריים).
"""
import config

OPTIONS = {
    "synagogue": "בית כנסת",
    "mosque": "מסגד",
    "church": "כנסייה",
    "any": "כל מקום תפילה",
    "none": "לא להפנות למקומות תפילה",
}
DEFAULT = "synagogue"
_KEY = "worship_fallback"


def worship_ids():
    return {c["id"] for c in config.SAFE_PLACE_CATEGORIES if c.get("worship")}


def allowed_ids(pref):
    """אילו קטגוריות מקום תפילה מותר להציע לפי ההעדפה."""
    pref = pref if pref in OPTIONS else DEFAULT
    if pref == "none":
        return set()
    if pref == "any":
        return worship_ids()
    return {pref}


def categories_for(pref):
    """רשימת הקטגוריות לחיפוש: כל הלא-דתיות + מקומות התפילה שמותרים."""
    ok = allowed_ids(pref)
    return [c for c in config.SAFE_PLACE_CATEGORIES if not c.get("worship") or c["id"] in ok]


def get(username):
    if not username:
        return DEFAULT
    try:
        import incident_store
        import storage
        raw = incident_store.get_setting(username, _KEY, None)
        if not raw:
            return DEFAULT
        value = storage.unprotect_text(raw)
        return value if value in OPTIONS else DEFAULT
    except Exception:  # noqa: BLE001 - העדפה לא זמינה = ברירת המחדל, לא כשל בחירום
        return DEFAULT


def save(username, value):
    if value not in OPTIONS:
        raise ValueError(f"ערך לא מוכר: {value}")
    import incident_store
    import storage
    incident_store.set_setting(username, _KEY, storage.protect_text(value))
    return value

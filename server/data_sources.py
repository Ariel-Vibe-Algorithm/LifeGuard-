# -*- coding: utf-8 -*-
"""
data_sources.py - שכבת נתונים אחידה: רישום כל המקורות החיצוניים, עטיפת רשומה אחידה, ומטמון מקומי.

עקרונות (research/data_sources_report.md):
- כל מקור חיצוני ש-LifeGuard משתמש בו רשום כאן, עם רישיון, תנאים (+ תאריך בדיקה), מגבלת קצב,
  שם משתנה הסביבה של המפתח (**לעולם לא המפתח עצמו**), האם שימוש מסחרי מותר, ייחוס וכיסוי בישראל.
- "שכבה חינמית" ≠ "מותר בייצור מסחרי": commercial_ok הוא True רק כשהתנאים אומרים זאת במפורש.
- סוג המידע (kind) מבדיל בין: מאומת-רשמי / מידע פתוח-קהילתי / הערכה / דיווח משתמש / חדשות לא מאומתות.
  אין כאן שום נתון פשיעה/תאורה/בטיחות ממוצא - רק מטא-דאטה על מקורות אמיתיים.
- מפתחות API רק בשרת (משתני סביבה / קובץ סוד בפריסה). אף מפתח לא נכנס לקוד לקוח (server/web, Windows).
- GET /v1/data-sources הוא קריאה בלבד: רישום + מצב אחרון שנמדד. **אין בו קריאות רשת** - בדיקה חיה רק
  ביוזמת מנהל/ת (POST /v1/admin/data-sources/check) או CLI (python -m server.data_sources --check).
  שום חלק כאן לא נמצא בנתיב ה-SOS.
- מטמון: קובץ SQLite נפרד בתיקיית הנתונים של השרת (ליד מסד הנתונים; לא תיקיית data/ של המאגר), עם TTL
  לכל מקור. מקור שאסור/מסוכן לשמור (התרעות בזמן אמת) - TTL=0, לא נשמר.
"""

import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "LifeGuard-Server/1.0"

KINDS = ("verified_official", "open_data_community", "estimate", "user_report", "news_unverified")
RELIABILITY = ("high", "medium", "low")
KIND_HE = {"verified_official": "נתון רשמי מאומת", "open_data_community": "מידע פתוח / קהילתי",
           "estimate": "הערכה (מודל / זיהוי אוטומטי)", "user_report": "דיווח משתמשים - לא מאומת",
           "news_unverified": "דווח בתקשורת, לא מאומת"}
CHECKED = "2026-11-11"     # תאריך בדיקת התנאים והנקודות החיות (research/data_sources_report.md)


def _m(*p):
    return os.path.join(HERE, *p)


# ---------------------------------------------------------------- registry
# כל שדה חובה: id, name, kind, licence, terms_url, terms_checked_at, rate_limit, key_env, commercial_ok,
# attribution, coverage. שדות אופציונליים: reliability, cache_ttl_s, used_by, probe, local_file, url_env,
# usable_in_israel, notes.
SOURCES = [
    {
        "id": "oref_alerts", "name": "פיקוד העורף - התרעות (דרך מראה מורשית)",
        "kind": "verified_official", "reliability": "high",
        "licence": "אין הסכם API רשמי; מקור מחייב: אפליקציית פיקוד העורף / oref.org.il / 104",
        "terms_url": "https://www.oref.org.il/", "terms_checked_at": CHECKED,
        "rate_limit": "סקר כל 5 שנ' לכל השרת (מטמון 5 שנ' בזיכרון)",
        "key_env": None, "url_env": "LIFEGUARD_OREF_ALERTS_URL", "commercial_ok": "unknown",
        "attribution": "פיקוד העורף", "cache_ttl_s": 0,
        "coverage": "כל הארץ. הנקודה הציבורית חסומה גיאוגרפית מחוץ לישראל לפי תיעוד קהילתי; כבויה עד שמוגדרת מראה.",
        "used_by": ["server/national.py"], "usable_in_israel": "requires_config",
    },
    {
        "id": "siren", "name": "Siren (siren.co.il) - ממשק קהילתי להתרעות פיקוד העורף",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "תנאי שימוש Siren (מרץ 2026): חינם לכולם כולל מסחרי; ייחוס גלוי עם קישור פעיל חובה",
        "terms_url": "https://siren.co.il/terms", "terms_checked_at": CHECKED,
        "rate_limit": "מפתח פרטי: 1,000 בקשות / 15 דק' (REST); 429 + Retry-After בחריגה",
        "key_env": "LIFEGUARD_SIREN_KEY", "commercial_ok": True,
        "attribution": "התרעות: Siren - https://siren.co.il (מקור קהילתי, לא רשמי)", "cache_ttl_s": 0,
        "coverage": "כל הארץ (שמות יישובים קנוניים). מקור משלים בלבד - התנאים אוסרים להסתמך עליו כמקור יחיד/עיקרי.",
        "used_by": ["server/national.py"], "usable_in_israel": "requires_key",
        "probe": {"url": "https://api.siren.co.il/", "expect": 200},
        "notes": "רק מפתח פרטי (pr_) בשרת; מפתח ציבורי (pb_) אסור בשימוש בצד שרת לפי סעיף 5.",
    },
    {
        "id": "official_shelters", "name": "מקלטים ציבוריים - מאגרים עירוניים רשמיים (ת\"א, ב\"ש, חיפה, ירושלים)",
        "kind": "verified_official", "reliability": "high",
        "licence": "ב\"ש: data.gov.il Other (Open); ירושלים: ODbL; ת\"א/חיפה GIS: רישיון השרת לא אומת",
        "terms_url": "https://data.gov.il/he/terms-of-use", "terms_checked_at": CHECKED,
        "rate_limit": "חד-פעמי בבנייה (research_tools/build_safety_layers.py)",
        "key_env": None, "commercial_ok": "unknown",
        "attribution": "עיריות ת\"א-יפו, באר שבע, חיפה, ירושלים (לכל מקלט - המקור שלו)",
        "cache_ttl_s": None, "local_file": _m("models", "safety_layers.json.gz"),
        "coverage": "4 ערים בלבד. מחוץ להן - אין נתוני מקלטים (לא ממציאים).",
        "used_by": ["server/national.py", "server/safety_intel.py"], "usable_in_israel": "partial",
    },
    {
        "id": "cbs_ped_accidents", "name": "הלמ\"ס - תאונות דרכים עם נפגעים (PUF), הולכי רגל",
        "kind": "verified_official", "reliability": "high",
        "licence": "data.gov.il Other (Open) + רישיון הלמ\"ס 2.1 (מסחרי מותר, ייחוס)",
        "terms_url": "https://www.cbs.gov.il/he/Pages/רישיון-שימוש.aspx", "terms_checked_at": CHECKED,
        "rate_limit": "CKAN datastore_search בדפדוף; חד-פעמי בבנייה",
        "key_env": None, "commercial_ok": True,
        "attribution": "הלשכה המרכזית לסטטיסטיקה (הלמ\"ס), תאונות דרכים עם נפגעים - PUF",
        "cache_ttl_s": None, "local_file": _m("models", "safety_layers.json.gz"),
        "coverage": "כל הארץ, X/Y ב-ITM לכל תאונה (2018-2022 בקבצים המקוצרים). רמת דיוק: נקודת תאונה.",
        "used_by": ["server/safety_intel.py"], "usable_in_israel": True,
        "probe": {"url": "https://data.gov.il/api/3/action/package_show?id=teunot2022", "expect": 200},
    },
    {
        "id": "streetlights_gis", "name": "עמודי תאורה עירוניים (GIS ת\"א שכבה 543, data.gov.il street-light-br7)",
        "kind": "verified_official", "reliability": "medium",
        "licence": "ב\"ש: data.gov.il Other (Open); ת\"א GIS: לא אומת שרישיון ה-Open Data חל על שרת ה-GIS",
        "terms_url": "https://data.gov.il/dataset/street-light-br7", "terms_checked_at": CHECKED,
        "rate_limit": "חד-פעמי בבנייה", "key_env": None, "commercial_ok": "unknown",
        "attribution": "עיריית תל אביב-יפו (GIS), עיריית באר שבע (data.gov.il)",
        "cache_ttl_s": None, "local_file": _m("models", "street_lights_grid.json.gz"),
        "coverage": "ת\"א וב\"ש בלבד. מיקום עמוד ≠ עמוד דולק (אין נתוני תקלות 106).",
        "used_by": ["context_risk.py", "server/safety_intel.py"], "usable_in_israel": "partial",
        "probe": {"url": "https://data.gov.il/api/3/action/package_show?id=street-light-br7", "expect": 200},
    },
    {
        "id": "police_crime_statarea", "name": "משטרת ישראל - נתוני פשיעה לפי אזור סטטיסטי",
        "kind": "verified_official", "reliability": "medium",
        "licence": "data.gov.il Other (Open); אסור להצליב לפגיעה בפרטיות",
        "terms_url": "https://data.gov.il/dataset/crime_records_data", "terms_checked_at": CHECKED,
        "rate_limit": "CKAN datastore_search בדפדוף; חד-פעמי בבנייה",
        "key_env": None, "commercial_ok": True,
        "attribution": "משטרת ישראל, נתוני פשיעה (data.gov.il)",
        "cache_ttl_s": None, "local_file": _m("models", "risk_factors_grid.json.gz"),
        "coverage": "כל הארץ ברמת אזור סטטיסטי (StatisticAreaKod) - **אין קואורדינטות**. להצגה אזורית בלבד.",
        "used_by": ["server/risk_factors_layer.py"], "usable_in_israel": True,
        "probe": {"url": "https://data.gov.il/api/3/action/package_show?id=crime_records_data", "expect": 200},
    },
    {
        "id": "osm_safe_places", "name": "OpenStreetMap - תחנות משטרה / בתי חולים (+ data.gov.il תחנות כיבוי)",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "OSM: ODbL 1.0 (share-alike למאגר נגזר); תחנות כיבוי: data.gov.il Other (Open)",
        "terms_url": "https://www.openstreetmap.org/copyright", "terms_checked_at": CHECKED,
        "rate_limit": "חד-פעמי בבנייה (tools/build_safe_places.py)", "key_env": None, "commercial_ok": True,
        "attribution": "© OpenStreetMap contributors (ODbL); כבאות והצלה לישראל (data.gov.il)",
        "cache_ttl_s": None, "local_file": _m("safe_places", "safe_places_il.json"),
        "coverage": "כל הארץ; שלמות תלויה במתנדבי OSM.",
        "used_by": ["map_module.py", "safe_places/"], "usable_in_israel": True,
    },
    {
        "id": "overpass", "name": "Overpass API (OSM) - overpass-api.de + מראות",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "נתוני OSM: ODbL 1.0", "terms_url": "https://dev.overpass-api.de/overpass-doc/en/preface/commons.html",
        "terms_checked_at": CHECKED,
        "rate_limit": "~10,000 בקשות/יום ו-~1GB/יום למשתמש; 429 בחריגת slots; לשימוש כבד - שרת עצמי",
        "key_env": None, "commercial_ok": "unknown",
        "attribution": "© OpenStreetMap contributors (ODbL)", "cache_ttl_s": 7 * 86400,
        "coverage": "כל הארץ. ב-2026-11-11 כל 4 המראות היו עמוסים/לא זמינים (502/504/timeout).",
        "used_by": ["map_module.py", "tools/build_safe_places.py"], "usable_in_israel": "unreliable",
        "probe": {"url": "https://overpass-api.de/api/status", "expect": 200},
    },
    {
        "id": "nominatim", "name": "Nominatim (OSM) - גיאוקידוד",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "ODbL 1.0", "terms_url": "https://operations.osmfoundation.org/policies/nominatim/",
        "terms_checked_at": CHECKED,
        "rate_limit": "מקסימום 1 בקשה/שנ' לכל האפליקציה; בלי bulk/autocomplete; UA מזהה חובה; מטמון חובה",
        "key_env": None, "commercial_ok": "unknown",
        "attribution": "© OpenStreetMap contributors (ODbL)", "cache_ttl_s": 30 * 86400,
        "coverage": "כל הארץ, עברית. החיפוש ההפוך והישיר החזירו כתובות נכונות ב-3 נקודות הבדיקה.",
        "used_by": ["manual_location.py", "map_module.py"], "usable_in_israel": True,
        "probe": {"url": "https://nominatim.openstreetmap.org/status?format=json", "expect": 200},
        "notes": "המגבלה היא לכל האפליקציה (כל המשתמשים יחד). לייצור: proxy עם מטמון או מופע עצמי.",
    },
    {
        "id": "osrm_fossgis_foot", "name": "OSRM foot - routing.openstreetmap.de (FOSSGIS)",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "נתוני OSM: ODbL; מסלולים: OSRM", "terms_url": "https://routing.openstreetmap.de/about.html",
        "terms_checked_at": CHECKED, "rate_limit": "מקסימום 1 בקשה/שנ'; בלי scraping / שימוש כבד",
        "key_env": None, "commercial_ok": "unknown",
        "attribution": "© OpenStreetMap contributors; routing: FOSSGIS / OSRM", "cache_ttl_s": 600,
        "coverage": "כל העולם כולל ישראל; פרופיל הליכה אמיתי (4.6 קמ\"ש בבדיקה).",
        "used_by": ["map_module.py", "server/routing.py (FootAlternatives)", "server/safety_intel.py"], "usable_in_israel": True,
        "notes": "alternatives=3 למצבי מסלול (מהיר/בטוח/מאוזן); FOSSGIS רושמים כל בקשה; כיבוי: LIFEGUARD_PUBLIC_ROUTING=0.",
        "probe": {"url": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/34.7818,32.0853;34.7940,32.0700"
                         "?overview=false", "expect": 200},
    },
    {
        "id": "osrm_demo", "name": "OSRM demo - router.project-osrm.org (לא בשימוש)",
        "kind": "open_data_community", "reliability": "low",
        "licence": "נתוני OSM: ODbL", "terms_url": "https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy",
        "terms_checked_at": CHECKED, "rate_limit": "ללא מספר; 'שימוש מופרז אסור'", "key_env": None,
        "commercial_ok": False, "attribution": "© OpenStreetMap contributors; OSRM", "cache_ttl_s": 0,
        "coverage": "שרת רכב בלבד: /foot/ מחזיר בדיוק את מסלול הרכב (35.9 קמ\"ש) - לא שמיש להליכה.",
        "used_by": [], "usable_in_israel": False,
    },
    {
        "id": "osrm_self", "name": "OSRM עצמי (LIFEGUARD_OSRM_URL) - זמני הגעה של מתנדבים",
        "kind": "open_data_community", "reliability": "high",
        "licence": "נתוני OSM: ODbL", "terms_url": "https://www.openstreetmap.org/copyright",
        "terms_checked_at": CHECKED, "rate_limit": "שרת שלנו", "key_env": None,
        "url_env": "LIFEGUARD_OSRM_URL", "commercial_ok": True,
        "attribution": "© OpenStreetMap contributors; OSRM", "cache_ttl_s": 0,
        "coverage": "לפי קובץ המפה שנטען; בלי שרת - הערכת קו אוויר מסומנת 'משוער'.",
        "used_by": ["server/routing.py", "server/safety_intel.py"], "usable_in_israel": "requires_config",
    },
    {
        "id": "mapillary", "name": "Mapillary Graph API v4 - כיסוי תמונות רחוב וזיהוי עמודי תאורה",
        "kind": "estimate", "reliability": "low",
        "licence": "תמונות: CC BY-SA 4.0 (ברירת מחדל, סעיף 3b); שימוש מסחרי בשירות - רק למטרות בסעיף 12",
        "terms_url": "https://www.mapillary.com/terms", "terms_checked_at": CHECKED,
        "rate_limit": "חיפוש: 10,000/דק' לאפליקציה; Entity: 60,000/דק'; אריחים: 50,000/יום",
        "key_env": "MAPILLARY_TOKEN", "commercial_ok": "unknown",
        "attribution": "לוגו Mapillary + קישור; לכל תמונה: מזהה, יוצר/ת, CC BY-SA 4.0",
        "cache_ttl_s": 7 * 86400,
        "coverage": "תלוי בתורמים; בלי token לא נבדק (graph.mapillary.com החזיר 'Invalid OAuth 2.0 Access Token').",
        "used_by": ["server/mapillary_client.py", "research_tools/mapillary.py"], "usable_in_israel": "requires_key",
        "probe": {"url": "https://graph.mapillary.com/", "expect": None},
    },
    {
        "id": "open_meteo", "name": "Open-Meteo - מזג אוויר",
        "kind": "open_data_community", "reliability": "medium",
        "licence": "נתונים: CC BY 4.0; ה-API החינמי לשימוש לא-מסחרי בלבד",
        "terms_url": "https://open-meteo.com/en/terms", "terms_checked_at": CHECKED,
        "rate_limit": "פחות מ-10,000/יום, 5,000/שעה, 600/דק'", "key_env": None, "commercial_ok": False,
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0)", "cache_ttl_s": 720,
        "coverage": "כל הארץ (החזיר טמפ' עדכנית ל-3 נקודות הבדיקה).",
        "used_by": ["server/weather.py", "server/safety_intel.py"], "usable_in_israel": True,
        "probe": {"url": "https://api.open-meteo.com/v1/forecast?latitude=32.0853&longitude=34.7818&current=is_day",
                  "expect": 200},
        "notes": "לשימוש מסחרי: תוכנית בתשלום או מופע עצמי (קוד פתוח). הקשר בלבד (affects_score=False); כיבוי: LIFEGUARD_WEATHER=0.",
    },
    {
        "id": "gdelt_news", "name": "GDELT + RSS מפרסמים - דיווחי תקשורת",
        "kind": "news_unverified", "reliability": "low",
        "licence": "GDELT: שימוש חופשי כולל מסחרי עם ציטוט וקישור; כותרות/כתבות: זכויות המפרסמים",
        "terms_url": "https://www.gdeltproject.org/about.html", "terms_checked_at": CHECKED,
        "rate_limit": "לא מפורסם ל-DOC API; קובץ מקומי (LIFEGUARD_NEWS_FILE), עדכון חיצוני",
        "key_env": None, "url_env": "LIFEGUARD_NEWS_FILE", "commercial_ok": "unknown",
        "attribution": "GDELT Project - https://www.gdeltproject.org/ ; כל פריט מקשר למפרסם", "cache_ttl_s": None,
        "coverage": "חלקית ומוטה (כיסוי תקשורתי). היעדר דיווח ≠ בטוח.",
        "used_by": ["server/news_layer.py"], "usable_in_israel": "partial",
        "notes": "RSS של מפרסמים - רק באישור בכתב מהמפרסם לפני ייצור.",
    },
    {
        "id": "user_reports", "name": "דיווחי משתמשים (תצפיות רחוב)",
        "kind": "user_report", "reliability": "low",
        "licence": "תוכן משתמשים - לפי מדיניות הפרטיות של LifeGuard", "terms_url": "PRIVACY_HE.md",
        "terms_checked_at": CHECKED, "rate_limit": "מגבלות abuse בשרת", "key_env": None, "commercial_ok": True,
        "attribution": "דיווח משתמשים", "cache_ttl_s": None,
        "coverage": "רק היכן שמשתמשים דיווחו.", "used_by": ["server/safety_intel.py"], "usable_in_israel": True,
    },
]
BY_ID = {s["id"]: s for s in SOURCES}
REQUIRED = ("id", "name", "kind", "licence", "terms_url", "terms_checked_at", "rate_limit", "key_env",
            "commercial_ok", "attribution", "coverage")
PUBLIC_FIELDS = REQUIRED + ("reliability", "cache_ttl_s", "used_by", "usable_in_israel", "notes", "url_env")


def source(source_id):
    return BY_ID[source_id]


# ---------------------------------------------------------------- secrets (server-side only)
def secret(env_name):
    """ערך מפתח: משתנה סביבה, או קובץ סוד (<ENV>_FILE, למשל docker secret). לעולם לא נכנס לפלט."""
    if not env_name:
        return None
    v = (os.environ.get(env_name) or "").strip()
    if v:
        return v
    path = (os.environ.get(env_name + "_FILE") or "").strip()
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip() or None
        except OSError:
            return None
    return None


# ---------------------------------------------------------------- uniform record
def record(source_id, data, *, fetched_at=None, source_updated_at=None, precision_m=None, reliability=None,
           kind=None, attribution=None):
    """עטיפה אחידה לכל נתון חיצוני חדש. kind/reliability ברירת מחדל - מהרישום."""
    src = BY_ID[source_id]
    kind = kind or src["kind"]
    reliability = reliability or src.get("reliability", "low")
    if kind not in KINDS:
        raise ValueError(f"kind לא מוכר: {kind}")
    if reliability not in RELIABILITY:
        raise ValueError(f"reliability לא מוכר: {reliability}")
    return {"source_id": source_id, "source_name": src["name"],
            "fetched_at": time.time() if fetched_at is None else float(fetched_at),
            "source_updated_at": source_updated_at, "precision_m": precision_m,
            "reliability": reliability, "kind": kind, "kind_label": KIND_HE[kind],
            "attribution": attribution if attribution is not None else src["attribution"], "data": data}


# ---------------------------------------------------------------- cache + last-check store
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache(source_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    fetched_at REAL NOT NULL, source_updated_at REAL, PRIMARY KEY(source_id, key));
CREATE TABLE IF NOT EXISTS checks(source_id TEXT PRIMARY KEY, ok INTEGER NOT NULL, detail TEXT,
    checked_at REAL NOT NULL);
"""


def default_cache_path(db_path=None):
    """LIFEGUARD_DATA_CACHE, אחרת ליד מסד הנתונים של השרת (server_data/ או /data בדוקר) - לא data/ של המאגר."""
    p = os.environ.get("LIFEGUARD_DATA_CACHE", "").strip()
    if p:
        return p
    if not db_path or db_path == ":memory:":
        return ":memory:"
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "data_cache.sqlite")


class DataCache:
    """מטמון קבוע לנתונים שמותר לשמור. TTL לפי המקור: None = ללא תפוגה, 0 = אסור לשמור."""

    def __init__(self, path=":memory:", clock=time.time):
        self.path, self.clock = path, clock
        self._lock = threading.Lock()
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.executescript(_SCHEMA)

    @staticmethod
    def ttl(source_id):
        return BY_ID[source_id].get("cache_ttl_s")

    def put(self, source_id, key, value, source_updated_at=None):
        if self.ttl(source_id) == 0:
            return False                          # מקור שאסור לשמור (התרעות בזמן אמת)
        with self._lock, self._con:
            self._con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                              (source_id, str(key), json.dumps(value, ensure_ascii=False), self.clock(),
                               source_updated_at))
        return True

    def get(self, source_id, key, allow_stale=False):
        """-> {"value","fetched_at","source_updated_at","stale"} או None. stale רק אם allow_stale (מצב offline)."""
        with self._lock:
            row = self._con.execute("SELECT value, fetched_at, source_updated_at FROM cache WHERE source_id=? AND key=?",
                                    (source_id, str(key))).fetchone()
        if not row:
            return None
        ttl = self.ttl(source_id)
        stale = ttl is not None and self.clock() - row[1] > ttl
        if stale and not allow_stale:
            return None
        return {"value": json.loads(row[0]), "fetched_at": row[1], "source_updated_at": row[2], "stale": stale}

    def purge_expired(self):
        n = 0
        for sid, s in BY_ID.items():
            ttl = s.get("cache_ttl_s")
            if ttl:
                with self._lock, self._con:
                    n += self._con.execute("DELETE FROM cache WHERE source_id=? AND fetched_at<?",
                                           (sid, self.clock() - ttl)).rowcount
        return n

    def record_check(self, source_id, ok, detail=""):
        with self._lock, self._con:
            self._con.execute("INSERT OR REPLACE INTO checks VALUES (?,?,?,?)",
                              (source_id, 1 if ok else 0, str(detail)[:200], self.clock()))

    def last_check(self, source_id):
        with self._lock:
            r = self._con.execute("SELECT ok, detail, checked_at FROM checks WHERE source_id=?", (source_id,)).fetchone()
        return {"ok": bool(r[0]), "detail": r[1], "checked_at": r[2]} if r else None

    def close(self):
        with self._lock:
            self._con.close()


def cache_for(svc):
    """מטמון אחד לכל שירות (נוצר בעצלות; לא נוגע במיגרציות של מסד הנתונים הראשי)."""
    c = getattr(svc, "_data_cache", None)
    if c is None:
        c = svc._data_cache = DataCache(default_cache_path(getattr(svc, "db_path", None)))
    return c


# ---------------------------------------------------------------- status
def configured_state(src):
    """(state, detail) בלי רשת: not_configured אם חסר מפתח/כתובת/קובץ מקומי."""
    if src.get("key_env") and not secret(src["key_env"]):
        return "not_configured", f"חסר {src['key_env']} בשרת"
    if src.get("url_env") and not (os.environ.get(src["url_env"]) or "").strip():
        return "not_configured", f"חסר {src['url_env']} בשרת"
    if src.get("local_file") and not os.path.exists(src["local_file"]):
        return "not_configured", "קובץ השכבה המקומי לא נבנה בשרת הזה"
    return "configured", None


def status(src, cache=None):
    state, detail = configured_state(src)
    out = {"state": state, "detail": detail, "key_present": bool(src.get("key_env")) and bool(secret(src["key_env"])),
           "last_check": None}
    if src.get("local_file") and os.path.exists(src["local_file"]):
        out["local_snapshot_at"] = os.path.getmtime(src["local_file"])
    lc = cache.last_check(src["id"]) if cache is not None else None
    if lc:
        out["last_check"] = lc
        if state == "configured":
            out["state"] = "reachable" if lc["ok"] else "unreachable"
    return out


def report(cache=None):
    """רישום + מצב אחרון. **בלי רשת** - בטוח לנתיב ציבורי. אף ערך מפתח לא נכלל (רק שם המשתנה)."""
    items = []
    for s in SOURCES:
        pub = {k: s.get(k) for k in PUBLIC_FIELDS}
        pub["kind_label"] = KIND_HE[s["kind"]]
        pub["status"] = status(s, cache)
        items.append(pub)
    return {"sources": items, "kinds": KIND_HE, "terms_checked_at": CHECKED,
            "note": "שכבה חינמית אינה בהכרח מותרת לייצור מסחרי - ראו commercial_ok ו-terms_url לכל מקור. "
                    "אין נתוני פשיעה/תאורה/בטיחות מומצאים: כל נתון מסומן במקור, בסוג ובאמינות."}


def _probe_one(src, timeout):
    p = src.get("probe")
    req = urllib.request.Request(p["url"], headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception as e:  # noqa: BLE001 - כל כישלון רשת = unreachable, עם הסיבה
        return False, f"{type(e).__name__}: {str(e)[:120]}"
    expect = p.get("expect")
    if expect is None:                       # נקודת קצה שדורשת מפתח: כל תשובת HTTP = השרת עונה
        return True, f"HTTP {code}"
    return code == expect, f"HTTP {code}"


def check_all(cache, timeout=8.0, only=None):
    """בדיקה חיה (מנהל/ת / CLI בלבד). מקור לא מוגדר - לא נשלחת אליו שום בקשה."""
    results, threads = {}, []

    def run(s):
        ok, detail = _probe_one(s, timeout)
        cache.record_check(s["id"], ok, detail)
        results[s["id"]] = {"ok": ok, "detail": detail}

    for s in SOURCES:
        if only and s["id"] not in only:
            continue
        if not s.get("probe"):
            continue
        if configured_state(s)[0] == "not_configured" and s.get("key_env"):
            results[s["id"]] = {"skipped": "not_configured"}
            continue
        t = threading.Thread(target=run, args=(s,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout + 2)
    return results


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="LifeGuard external data sources: registry and live check")
    ap.add_argument("--check", action="store_true", help="probe every configured source (network)")
    ap.add_argument("--cache", default=None, help="cache sqlite path (default: next to the server DB)")
    a = ap.parse_args(argv)
    db = os.environ.get("LIFEGUARD_SERVER_DB") or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                                "server_data", "lifeguard_server.db")
    cache = DataCache(a.cache or default_cache_path(db))
    if a.check:
        print(json.dumps(check_all(cache), ensure_ascii=False, indent=1))
    print(json.dumps(report(cache), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
israel_layer.py - שכבת הנתונים הישראלית של LifeGuard (מחקר בלבד, לא חלק מהאפליקציה).

מחשב מחדש אותות רלוונטיים לבטיחות מנתונים פתוחים ראשוניים:
  1. פשיעה (משטרת ישראל, data.gov.il `crime_records_data`) -> תיקים לאזור סטטיסטי לשנה,
     מנורמל לאוכלוסיית מפקד 2022 (הלמ"ס), יחס לחציון העיר, וסיווג לפי ספים מפורשים.
  2. תאונות עם נפגעים (הלמ"ס PUF `2023-puf`) -> פגיעות בהולכי רגל כנקודות WGS84.
  3. עמודי תאורה: תל אביב-יפו (ArcGIS IView2 שכבה 543) ובאר שבע (`street-light-br7`).

עקרונות:
- כל רשת נשמרת ב-research/samples/israel/cache/ (gzip), כך שהרצה חוזרת היא offline.
- בקשות מנומסות: השהיה קטנה, timeout, ניסיונות חוזרים, User-Agent כללי בלי פרטים אישיים.
- אין ניסיון להסיק מיקום מדויק של עבירות מעבר למה שהמשטרה מפרסמת; אין שימוש בשדות דת/מוצא/הכנסה.
- תיקים בלי אזור סטטיסטי לא נזרקים: הם נספרים כ"רמת עיר בלבד" ומדווחים לכל עיר.

שימוש:
  python -m research_tools.israel_layer build [--years 2024 2025] [--offline] [--skip-lights] [--skip-accidents]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- נתיבים וקבועים

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(ROOT, "research", "samples", "israel")
CACHE_DIR = os.path.join(SAMPLES_DIR, "cache")
DERIVED_DIR = os.path.join(SAMPLES_DIR, "derived")
DATASETS_DIR = os.path.join(ROOT, "research", "datasets")
SUMMARY_CSV = os.path.join(DATASETS_DIR, "crime_by_stat_area_summary.csv")
USER_SIGNAL_CSV = os.path.join(DATASETS_DIR, "neighborhood_crime_signal.csv")
SUMMARY_MAX_BYTES = 1_000_000

USER_AGENT = "LifeGuard-Research/1.0"
REQUEST_DELAY_S = 0.5
REQUEST_TIMEOUT_S = 120
REQUEST_RETRIES = 4

CKAN_SEARCH = "https://data.gov.il/api/3/action/datastore_search"
CKAN_PAGE = 50000  # נבדק: data.gov.il מחזיר עד 50,000 רשומות בעמוד

# משטרה - crime_records_data (resource לכל שנה)
CRIME_RESOURCES = {
    2021: "3f71fd16-25b8-4cfe-8661-e6199db3eb12",
    2022: "a59f3e9e-a7fe-4375-97d0-76cea68382c1",
    2023: "32aacfc9-3524-4fba-a282-3af052380244",
    2024: "5fc13c50-b6f3-4712-b831-a75e0f91a17e",
    2025: "e311b6a1-be5a-4a82-8298-f3afbee07b6b",
}
CRIME_FIELDS = ["_id", "FictiveIDNumber", "Year", "Quarter", "YeshuvKod",
                "StatisticAreaKod", "StatisticGroupKod"]
DEFAULT_YEARS = (2024, 2025)

# הלמ"ס - מפקד 2022, "נתונים נבחרים לפי יישובים ואזורים סטטיסטיים".
# רק שדות אוכלוסייה. בכוונה לא נשלפים שדות דת/מוצא/הכנסה.
CENSUS_RESOURCE = "9a9e085f-3bc8-41df-b15f-be0daaf99e30"
CENSUS_FIELDS = ["_id", "LocNameHeb", "LocalityCode", "StatArea", "StatAreaCmb", "pop_approx"]

# הלמ"ס - תאונות דרכים עם נפגעים PUF (מאגר 2023-puf)
PUF_RESOURCES = {
    2020: "70a93f04-2ffe-4b02-a062-a818600a5b67",
    2021: "6957e7c2-6d68-4332-bbc8-2ee8d5ba6bd6",
    2022: "ede3f02a-f9aa-4a6f-9eca-4c11a06f0043",
    2023: "ae0f1679-139f-4e69-a869-a60d5d76518b",
    2024: "05d14adb-fe54-49f7-b7ce-f30348e2d959",
}
PUF_FIELDS = ["pk_teuna_fikt", "SEMEL_YISHUV", "SHNAT_TEUNA", "HODESH_TEUNA", "SHAA",
              "YOM_LAYLA", "HUMRAT_TEUNA", "SUG_TEUNA", "TEURA", "STATUS_IGUN", "X", "Y"]

# באר שבע - street-light-br7
BR7_LIGHTS_WGS84 = "e40306c7-ee78-4c93-82e8-65d747e80865"   # lat/lon
BR7_LIGHTS_ITM = "9247d5a2-a458-403c-9e6f-eeeaf967f1d8"     # אותו מאגר ב-ITM (lat=Y, lon=X)

# תל אביב-יפו - ArcGIS
TLV_LIGHTS_LAYER = "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/543"

# ערים: סמל יישוב -> שם (סמלי הלמ"ס, זהים ל-YeshuvKod של המשטרה ול-LocalityCode של המפקד)
TARGET_CITIES = {
    5000: "תל אביב-יפו",
    3000: "ירושלים",
    4000: "חיפה",
    7900: "פתח תקווה",
    9000: "באר שבע",
    # "more if cheap" - הנתונים נשלפים ארצית בכל מקרה
    8300: "ראשון לציון",
    7400: "נתניה",
    70: "אשדוד",
    6600: "חולון",
    8600: "רמת גן",
}

# ---------------------------------------------------------------- ספים (ניתנים לשינוי)

# הכלל שמשתמע מ-18 השורות המספריות בטבלת המשתמש: ratio >= lower -> label (מלמעלה למטה)
DEFAULT_BANDS: Tuple[Tuple[float, str], ...] = (
    (3.0, "חריג מאוד"),
    (2.0, "גבוה"),
    (1.0, "מעל החציון"),
    (0.0, "נמוך מהחציון"),
)
# הצעה בלבד (לא ברירת מחדל): פס "סביב החציון" [0.9, 1.1)
AROUND_MEDIAN_BAND: Tuple[float, float, str] = (0.9, 1.1, "סביב החציון")

# ממצא (2026-11-06): קודי האזורים של המשטרה הם ברובם בגרסה ישנה (נראה כמו אזורי 2008, למשל
# 5000:349 "הצפון הישן (26)"), ואילו מפקד 2022 ממוספר מחדש בחלק מהערים (5000:1011 וכו').
# ה-join נעשה לפי קוד בלבד; חפיפה גאוגרפית בין קוד ישן לקוד זהה ב-2022 **לא אומתה**.
POP_JOIN_NOTE = "census2022_by_code_unverified_geography"

MIN_POP_FOR_RATE = 1000      # המשטרה מפרסמת אזור סטטיסטי רק אם יש בו >1,000 תושבים
SMALL_POP_FLAG = 2000        # שיעור לא יציב מתחת לזה
LOW_COUNT_FLAG = 20          # פחות מ-20 תיקים בשנה בממוצע -> רעש סטטיסטי גבוה
HIGH_RATIO_REVIEW = 3.0      # ratio גבוה -> לבדוק שימושי קרקע/מבקרים
# היוריסטיקה שקופה לפי שם האזור הסטטיסטי (לא מדידה של מבקרים!)
VISITOR_KEYWORDS = ("מרכז", "שוק", "נמל", "תעשי", "עסקים", "מלאכה", "תחנה מרכזית",
                    "חוף", "קרית הממשלה", "קריית הממשלה", "העיר העתיקה", "מתחם")


# ================================================================= רשת + cache

def _cache_path(source: str, url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, source, h + ".json.gz")


def http_get_json(url: str, source: str, offline: bool = False,
                  delay: float = REQUEST_DELAY_S, retries: int = REQUEST_RETRIES,
                  timeout: float = REQUEST_TIMEOUT_S) -> dict:
    """GET עם cache מקומי (gzip). offline=True: רק מה-cache, אחרת שגיאה."""
    path = _cache_path(source, url)
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)["body"]
    if offline:
        raise FileNotFoundError(f"offline: אין cache עבור {url}")
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            time.sleep(delay * (1 + attempt))
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump({"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "body": body}, f, ensure_ascii=False)
            os.replace(tmp, path)
            return body
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            last_err = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break
    raise RuntimeError(f"נכשל: {url}: {last_err}")


def ckan_search_all(resource_id: str, source: str, fields: Optional[Sequence[str]] = None,
                    filters: Optional[dict] = None, offline: bool = False,
                    page: int = CKAN_PAGE, log: Callable[[str], None] = print) -> List[dict]:
    """שליפת כל הרשומות מ-datastore_search בדפדוף limit/offset (ממוין לפי _id ליציבות)."""
    out: List[dict] = []
    offset = 0
    total = None
    while True:
        q = {"resource_id": resource_id, "limit": page, "offset": offset, "sort": "_id"}
        if fields:
            q["fields"] = ",".join(fields)
        if filters:
            q["filters"] = json.dumps(filters, ensure_ascii=False)
        body = http_get_json(CKAN_SEARCH + "?" + urllib.parse.urlencode(q), source, offline)
        if not body.get("success"):
            raise RuntimeError(f"CKAN error for {resource_id}: {body.get('error')}")
        res = body["result"]
        recs = res.get("records", [])
        total = res.get("total", total)
        out.extend(recs)
        log(f"  [{source}] {resource_id[:8]} offset={offset} +{len(recs)} ({len(out)}/{total})")
        if not recs or (total is not None and len(out) >= total):
            break
        offset += len(recs)
    return out


def ckan_distinct(resource_id: str, source: str, fields: Sequence[str],
                  offline: bool = False) -> List[dict]:
    q = {"resource_id": resource_id, "distinct": "true", "fields": ",".join(fields),
         "limit": 20000}
    body = http_get_json(CKAN_SEARCH + "?" + urllib.parse.urlencode(q), source, offline)
    return body["result"]["records"]


def arcgis_query_all(layer_url: str, source: str, out_fields: str, order_field: str,
                     out_sr: int = 4326, offline: bool = False,
                     log: Callable[[str], None] = print) -> List[dict]:
    meta = http_get_json(layer_url + "?f=json", source, offline)
    page = int(meta.get("maxRecordCount") or 1000)
    feats: List[dict] = []
    offset = 0
    while True:
        q = {"where": "1=1", "outFields": out_fields, "returnGeometry": "true",
             "outSR": out_sr, "orderByFields": order_field, "resultOffset": offset,
             "resultRecordCount": page, "f": "json"}
        body = http_get_json(layer_url + "/query?" + urllib.parse.urlencode(q), source, offline)
        if "error" in body:
            raise RuntimeError(f"ArcGIS error: {body['error']}")
        got = body.get("features", [])
        feats.extend(got)
        if offset % (page * 10) == 0:
            log(f"  [{source}] offset={offset} total={len(feats)}")
        if not got or not body.get("exceededTransferLimit", False) and len(got) < page:
            break
        offset += len(got)
    return feats


# ================================================================= ITM -> WGS84

# EPSG:2039 Israel 1993 / Israeli TM Grid (GRS80)
_GRS80_A = 6378137.0
_GRS80_F = 1 / 298.257222101
_WGS84_A = 6378137.0
_WGS84_F = 1 / 298.257223563
_ITM_LAT0 = math.radians(31 + 44 / 60 + 3.817 / 3600)
_ITM_LON0 = math.radians(35 + 12 / 60 + 16.261 / 3600)
_ITM_K0 = 1.0000067
_ITM_X0 = 219529.584
_ITM_Y0 = 626907.390
# EPSG transformation "Israel 1993 to WGS 84 (2)" (coordinate-frame rotation), accuracy ~0.5 m.
# אלה הפרמטרים שה-PROJ pipeline בוחר כברירת מחדל ל-2039->4326.
_HELMERT = dict(tx=23.772, ty=17.49, tz=17.859, rx=0.3132, ry=1.85274, rz=-1.67299, s_ppm=-5.4262)


def _meridian_arc(phi: float, a: float, e2: float) -> float:
    e4, e6 = e2 * e2, e2 * e2 * e2
    return a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * phi)
                + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * phi)
                - (35 * e6 / 3072) * math.sin(6 * phi))


def _tm_inverse(x: float, y: float) -> Tuple[float, float]:
    a, f = _GRS80_A, _GRS80_F
    e2 = f * (2 - f)
    m = _meridian_arc(_ITM_LAT0, a, e2) + (y - _ITM_Y0) / _ITM_K0
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    p1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
          + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
          + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    ep2 = e2 / (1 - e2)
    c1 = ep2 * math.cos(p1) ** 2
    t1 = math.tan(p1) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(p1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(p1) ** 2) ** 1.5
    d = (x - _ITM_X0) / (n1 * _ITM_K0)
    lat = p1 - (n1 * math.tan(p1) / r1) * (
        d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = _ITM_LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                       + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5 / 120
                       ) / math.cos(p1)
    return lat, lon


def _to_ecef(lat: float, lon: float, a: float, f: float) -> Tuple[float, float, float]:
    e2 = f * (2 - f)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    return (n * math.cos(lat) * math.cos(lon), n * math.cos(lat) * math.sin(lon),
            n * (1 - e2) * math.sin(lat))


def _from_ecef(x: float, y: float, z: float, a: float, f: float) -> Tuple[float, float]:
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(8):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + h)))
    return lat, lon


def itm_to_wgs84(x: float, y: float) -> Tuple[float, float]:
    """ITM (EPSG:2039, easting x, northing y, מטרים) -> (lat, lon) WGS84 במעלות.

    Pure-Python: TM הפוך על GRS80 + Helmert 7 פרמטרים (coordinate frame).
    אומת מול PROJ ומול זוגות ITM/WGS84 שמפרסמת עיריית באר שבע (הפרש < 1 מ"מ).
    """
    lat, lon = _tm_inverse(float(x), float(y))
    X, Y, Z = _to_ecef(lat, lon, _GRS80_A, _GRS80_F)
    h = _HELMERT
    k = math.radians(1 / 3600)
    rx, ry, rz = h["rx"] * k, h["ry"] * k, h["rz"] * k
    s = 1 + h["s_ppm"] * 1e-6
    X2 = h["tx"] + s * (X + rz * Y - ry * Z)
    Y2 = h["ty"] + s * (-rz * X + Y + rx * Z)
    Z2 = h["tz"] + s * (ry * X - rx * Y + Z)
    la, lo = _from_ecef(X2, Y2, Z2, _WGS84_A, _WGS84_F)
    return math.degrees(la), math.degrees(lo)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ================================================================= פשיעה - טרנספורמציות טהורות

def stat_number(stat_area_kod, yeshuv_kod) -> Optional[int]:
    """StatisticAreaKod של המשטרה = סמל יישוב * 10000 + מספר אזור סטטיסטי.
    מחזיר את מספר האזור, או None אם אין אזור / הקוד לא תואם ליישוב."""
    if stat_area_kod in (None, "", 0) or yeshuv_kod in (None, ""):
        return None
    try:
        sak, yk = int(stat_area_kod), int(yeshuv_kod)
    except (TypeError, ValueError):
        return None
    if sak // 10000 != yk:
        return None
    n = sak % 10000
    return n if n > 0 else None


def build_population_index(census_rows: Iterable[dict]) -> Dict[Tuple[int, int], dict]:
    """מפקד 2022: (סמל יישוב, מספר אזור) -> {"unit": מפתח יחידה, "pop": אוכלוסיית היחידה, "name": שם יישוב}.

    חלק מהאזורים מאוחדים בפרסום (StatAreaCmb כמו "2413+2414") עם אוכלוסייה משותפת.
    במקרה כזה כל האזורים המאוחדים ממופים לאותה יחידה, והשיעור מחושב ליחידה כולה.
    שורות ברמת יישוב (StatArea ריק) לא נכנסות לאינדקס.
    """
    idx: Dict[Tuple[int, int], dict] = {}
    for r in census_rows:
        loc, sa = r.get("LocalityCode"), r.get("StatArea")
        if loc in (None, "") or sa in (None, ""):
            continue
        try:
            loc_i = int(float(loc))
        except (TypeError, ValueError):
            continue
        cmb = str(r.get("StatAreaCmb") or sa).strip()
        parts = []
        for p in cmb.replace(" ", "").split("+"):
            try:
                parts.append(int(float(p)))
            except ValueError:
                pass
        if not parts:
            continue
        pop = r.get("pop_approx")
        try:
            pop = float(pop) if pop not in (None, "") else None
        except (TypeError, ValueError):
            pop = None
        unit = f"{loc_i}:" + "+".join(str(p) for p in sorted(parts))
        for p in parts:
            idx[(loc_i, p)] = {"unit": unit, "pop": pop, "name": r.get("LocNameHeb")}
    return idx


def aggregate_crime(records: Iterable[dict], unit_of: Optional[Callable[[int, int], str]] = None,
                    groups: Optional[Iterable[int]] = None) -> dict:
    """צבירת רשומות עבירה לתיקים.

    יחידת רשומה = עבירה בתוך תיק; תיק נספר פעם אחת (FictiveIDNumber ייחודי).
    - per_unit[(yeshuv, unit, year)] = מספר תיקים ייחודיים עם לפחות עבירה אחת באזור.
    - city[(yeshuv, year)]: records, records_no_sa, cases, cases_city_only.
      "city_only" = תיק שאף עבירה שלו (בקבוצות שנבחרו) לא שויכה לאזור סטטיסטי.
      תיקים אלה **לא נזרקים** - הם נשארים בספירה העירונית.
    unit_of(yeshuv, stat_no) -> מפתח יחידה (ברירת מחדל "yeshuv:stat").
    groups: קבוצות StatisticGroupKod לכלול (None = הכל).
    """
    gset = set(int(g) for g in groups) if groups is not None else None
    unit_of = unit_of or (lambda y, s: f"{y}:{s}")
    unit_cases: Dict[Tuple[int, str, int], set] = defaultdict(set)
    city_records: Dict[Tuple[int, int], int] = defaultdict(int)
    city_records_no_sa: Dict[Tuple[int, int], int] = defaultdict(int)
    case_has_sa: Dict[Tuple[int, int], Dict[str, bool]] = defaultdict(dict)
    for r in records:
        if gset is not None:
            g = r.get("StatisticGroupKod")
            if g is None or int(g) not in gset:
                continue
        yk = r.get("YeshuvKod")
        if yk in (None, ""):
            yk = -1  # רשומה בלי יישוב (מועצה אזורית וכו') - נשמרת תחת -1
        yk = int(yk)
        year = int(r.get("Year"))
        cid = r.get("FictiveIDNumber")
        key = (yk, year)
        city_records[key] += 1
        sn = stat_number(r.get("StatisticAreaKod"), yk)
        cases = case_has_sa[key]
        if sn is None:
            city_records_no_sa[key] += 1
            cases.setdefault(cid, False)
        else:
            cases[cid] = True
            unit_cases[(yk, unit_of(yk, sn), year)].add(cid)
    city = {}
    for key, n in city_records.items():
        cs = case_has_sa[key]
        city[key] = {
            "records": n,
            "records_no_sa": city_records_no_sa.get(key, 0),
            "cases": len(cs),
            "cases_city_only": sum(1 for v in cs.values() if not v),
        }
    per_unit = {k: len(v) for k, v in unit_cases.items()}
    return {"per_unit": per_unit, "city": city}


def compute_unit_rates(per_unit: Dict[Tuple[int, str, int], int], unit_pop: Dict[str, Optional[float]],
                       years: Sequence[int], min_pop: float = MIN_POP_FOR_RATE) -> List[dict]:
    """שיעור תיקים לאלף תושבים לשנה (ממוצע על פני השנים) לכל יחידה.

    unit_pop: יחידה -> אוכלוסייה (None אם לא ידוע/חסוי). יחידות מהאינדקס בלי אף תיק נכללות עם 0.
    """
    years = sorted(set(int(y) for y in years))
    by_unit: Dict[Tuple[int, str], Dict[int, int]] = defaultdict(dict)
    for (yk, unit, year), n in per_unit.items():
        if year in years:
            by_unit[(yk, unit)][year] = n
    for unit in unit_pop:
        yk = int(unit.split(":")[0])
        by_unit.setdefault((yk, unit), {})
    rows = []
    for (yk, unit), per_year in by_unit.items():
        total = sum(per_year.values())
        mean_annual = total / len(years) if years else 0.0
        pop = unit_pop.get(unit)
        rate = None
        status = "ok"
        if unit not in unit_pop:
            status = "no_census_match"
        elif pop is None:
            status = "no_population"
        elif pop < min_pop:
            status = "pop_below_min"
        elif total == 0:
            status = "no_police_records"
        if pop:
            rate = mean_annual / pop * 1000.0
        row = {"yeshuv": yk, "unit": unit, "pop": pop, "cases_total": total,
               "cases_mean_annual": mean_annual, "rate_per_1000": rate, "status": status}
        for y in years:
            row[f"cases_{y}"] = per_year.get(y, 0)
        rows.append(row)
    return rows


def median_of(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def add_ratios(rows: List[dict], key: str = "yeshuv", out: str = "ratio_city_median") -> Dict[int, Optional[float]]:
    """מוסיף יחס לחציון של היחידות התקינות (status == ok) באותה עיר. מחזיר חציון לכל עיר."""
    groups: Dict[int, List[float]] = defaultdict(list)
    for r in rows:
        if r["status"] == "ok" and r["rate_per_1000"] is not None:
            groups[r[key]].append(r["rate_per_1000"])
    medians = {k: median_of(v) for k, v in groups.items()}
    for r in rows:
        m = medians.get(r[key])
        r["city_median_rate"] = m
        r[out] = (r["rate_per_1000"] / m) if (m and r["rate_per_1000"] is not None
                                               and r["status"] == "ok") else None
    return medians


def categorize(ratio: Optional[float], bands: Sequence[Tuple[float, str]] = DEFAULT_BANDS,
               around_median: Optional[Tuple[float, float, str]] = None) -> str:
    """סיווג יחס לחציון לפי ספים מפורשים. ratio >= lower -> label, בדיקה מהגבוה לנמוך.

    around_median=(lo, hi, label): אם ניתן ו-lo <= ratio < hi, מחזיר label (נבדק ראשון).
    ratio None -> "לא זמין".
    """
    if ratio is None or (isinstance(ratio, float) and math.isnan(ratio)):
        return "לא זמין"
    if around_median is not None:
        lo, hi, label = around_median
        if lo <= ratio < hi:
            return label
    for lower, label in sorted(bands, key=lambda b: -b[0]):
        if ratio >= lower:
            return label
    return "לא זמין"


def quality_flags(row: dict, area_name: str = "", small_pop: float = SMALL_POP_FLAG,
                  low_count: float = LOW_COUNT_FLAG, high_ratio: float = HIGH_RATIO_REVIEW,
                  keywords: Sequence[str] = VISITOR_KEYWORDS) -> List[str]:
    flags = []
    pop = row.get("pop")
    if pop is not None and pop < small_pop:
        flags.append("small_population")
    if row.get("cases_mean_annual", 0) < low_count:
        flags.append("low_case_count")
    ratio = row.get("ratio_city_median")
    if ratio is not None and ratio >= high_ratio:
        flags.append("high_ratio_check_land_use")
    if area_name and any(k in area_name for k in keywords):
        flags.append("visitor_traffic_name_heuristic")
    return flags


def infer_band_rule(rows: Sequence[Tuple[float, str]]) -> Dict[str, Tuple[float, float]]:
    """לכל תווית: (min, max) של היחסים שקיבלו אותה. משמש לאימות הכלל מול טבלת המשתמש."""
    out: Dict[str, List[float]] = defaultdict(list)
    for ratio, label in rows:
        out[label].append(ratio)
    return {k: (min(v), max(v)) for k, v in out.items()}


# ================================================================= תאונות - טרנספורמציות טהורות

SEVERITY = {1: "קטלנית", 2: "קשה", 3: "קלה"}
LIGHTING = {1: "אור יום רגיל", 2: "ראות מוגבלת עקב מזג אוויר", 3: "לילה פעלה תאורה",
            4: "קיימת תאורה בלתי תקינה / לא פועלת", 5: "לילה לא קיימת תאורה", 6: "לילה לא ידוע",
            7: "לילה תאורה תקינה עם ראות מוגבלת", 8: "לילה תאורה לא תקינה עם ראות מוגבלת",
            9: "לילה ללא תאורה עם ראות מוגבלת", 10: "דמדומים", 11: "יום לא ידוע"}
DAY_NIGHT = {1: "יום", 5: "לילה"}


def shaa_to_time(code) -> Optional[str]:
    """קוד SHAA (טבלה 93 בהלמ"ס: 96 רבעי שעה, 1=00:00-00:14) -> 'HH:MM-HH:MM'."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    if not 1 <= c <= 96:
        return None
    start = (c - 1) * 15
    end = start + 14
    return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d}"


def shaa_to_hour_band(code, width: int = 3) -> Optional[str]:
    """קוד SHAA -> פס שעות ברוחב width (למשל '21-24')."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    if not 1 <= c <= 96:
        return None
    hour = (c - 1) // 4
    lo = hour - hour % width
    return f"{lo:02d}-{lo + width:02d}"


def _int_or_none(v):
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def extract_pedestrian_accidents(rows: Iterable[dict]) -> Tuple[List[dict], dict]:
    """SUG_TEUNA=1 (פגיעה בהולך רגל) עם X/Y -> נקודות WGS84. מחזיר (נקודות, מונים)."""
    pts: List[dict] = []
    stats = defaultdict(int)
    for r in rows:
        if _int_or_none(r.get("SUG_TEUNA")) != 1:
            continue
        stats["pedestrian_total"] += 1
        x, y = r.get("X"), r.get("Y")
        try:
            x, y = float(x), float(y)
        except (TypeError, ValueError):
            stats["no_coordinates"] += 1
            continue
        if not (100000 < x < 300000 and 350000 < y < 820000):
            stats["coordinates_out_of_range"] += 1
            continue
        lat, lon = itm_to_wgs84(x, y)
        sev = _int_or_none(r.get("HUMRAT_TEUNA"))
        teura = _int_or_none(r.get("TEURA"))
        dn = _int_or_none(r.get("YOM_LAYLA"))
        igun = _int_or_none(r.get("STATUS_IGUN"))
        stats[f"status_igun_{igun}"] += 1
        pts.append({
            "accident_id": r.get("pk_teuna_fikt"),
            "year": _int_or_none(r.get("SHNAT_TEUNA")),
            "month": _int_or_none(r.get("HODESH_TEUNA")),
            "severity_code": sev, "severity": SEVERITY.get(sev, ""),
            "time_quarter_hour": shaa_to_time(r.get("SHAA")) or "",
            "hour_band_3h": shaa_to_hour_band(r.get("SHAA")) or "",
            "day_night": DAY_NIGHT.get(dn, ""),
            "lighting_code": teura, "lighting": LIGHTING.get(teura, ""),
            "status_igun": igun,
            "yishuv_code": _int_or_none(r.get("SEMEL_YISHUV")),
            "itm_x": x, "itm_y": y,
            "lat": round(lat, 7), "lon": round(lon, 7),
        })
    stats["with_coordinates"] = len(pts)
    return pts, dict(stats)


# ================================================================= תאורה - טרנספורמציות טהורות

def normalize_tlv_lights(features: Iterable[dict]) -> List[dict]:
    out = []
    for f in features:
        g = f.get("geometry") or {}
        a = f.get("attributes") or {}
        if g.get("x") is None or g.get("y") is None:
            continue
        out.append({"source": "tlv_gis_543", "id": a.get("oid_amud"),
                    "pole_type": a.get("t_sug") or "", "lamps": a.get("ms_panasim"),
                    "lat": round(float(g["y"]), 7), "lon": round(float(g["x"]), 7)})
    return out


def normalize_br7_lights(records: Iterable[dict]) -> List[dict]:
    out = []
    for r in records:
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (29 < lat < 34 and 34 < lon < 36):
            continue
        out.append({"source": "br7_street_light", "id": r.get("_id"),
                    "pole_type": r.get("LED") or "", "lamps": None,
                    "lat": round(lat, 7), "lon": round(lon, 7)})
    return out


# ================================================================= cross-check מול טבלת המשתמש

# מיפוי שכונה (מטבלת המשתמש) -> תבניות בשמות האזורים הסטטיסטיים של המשטרה.
# המיפוי לפי שם בלבד ולכן משוער; האזורים שנמצאו בפועל מדווחים בפלט.
NEIGHBORHOOD_PATTERNS = {
    (5000, "פלורנטין"): ["פלורנטין"],
    (5000, "התקווה"): ["התקווה"],
    (5000, "יפו ג' ונווה גולן"): ["יפו ג", "נווה גולן"],
    (5000, "יפו ד' (גבעת התמרים)"): ["יפו ד", "גבעת התמרים"],
    (5000, "המשתלה"): ["משתלה"],
    (7900, "הדר המושבות הוותיקה"): ["הדר המושבות"],
    (7900, "כפר גנים א'"): ["כפר גנים א"],
    (7900, "עמישב"): ["עמישב"],
    (7900, "המרכז השקט"): ["המרכז השקט"],
    (7900, "נווה גנים"): ["נווה גנים"],
    (4000, "הדר מרכז"): ["הדר מרכז"],
    (4000, "ואדי ניסנאס"): ["ואדי ניסנאס", "ואדי ניסנס"],
    (4000, "ואדי סאליב (עיר תחתית מזרח)"): ["ואדי סאליב", "עיר תחתית"],
    (4000, "אחוזה"): ["אחוזה"],
    (4000, "כרמליה"): ["כרמליה"],
    (3000, "בית הכרם"): ["בית הכרם"],
    (3000, "מחנה יהודה (לב העיר)"): ["מחנה יהודה", "לב העיר"],
    (3000, "נחלאות ושערי חסד"): ["נחלאות", "שערי חסד"],
    (3000, "רחביה"): ["רחביה"],
}
CITY_NAME_TO_CODE = {"תל אביב-יפו": 5000, "ירושלים": 3000, "חיפה": 4000, "פתח תקווה": 7900,
                     "באר שבע": 9000}


def match_areas(names: Dict[int, str], yeshuv: int, patterns: Sequence[str]) -> List[int]:
    """מחזיר StatisticAreaKod של היישוב שהשם שלהם מכיל אחת התבניות."""
    out = []
    for kod, name in names.items():
        if kod // 10000 == yeshuv and name and any(p in name for p in patterns):
            out.append(kod)
    return sorted(out)


# ================================================================= build

def _write_csv(path: str, rows: List[dict], fields: Optional[List[str]] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


def _r(v, n=3):
    return None if v is None else round(v, n)


def build_crime(years: Sequence[int], offline: bool, log=print) -> dict:
    log(f"== פשיעה: שנים {list(years)}")
    records: List[dict] = []
    area_names: Dict[int, str] = {}
    yeshuv_names: Dict[int, str] = {}
    group_names: Dict[int, str] = {}
    fetch_status = {}
    for y in years:
        rid = CRIME_RESOURCES[y]
        try:
            recs = ckan_search_all(rid, "police_crime", CRIME_FIELDS, offline=offline, log=log)
            bad_year = sum(1 for r in recs if int(r.get("Year") or 0) != y)
            fetch_status[y] = {"rows": len(recs), "rows_other_year_field": bad_year}
            records.extend(recs)
            for d in ckan_distinct(rid, "police_crime", ["StatisticAreaKod", "StatisticArea"], offline):
                if d.get("StatisticAreaKod"):
                    area_names[int(d["StatisticAreaKod"])] = d.get("StatisticArea") or ""
            for d in ckan_distinct(rid, "police_crime", ["YeshuvKod", "Yeshuv"], offline):
                if d.get("YeshuvKod") is not None:
                    yeshuv_names[int(d["YeshuvKod"])] = d.get("Yeshuv") or ""
            for d in ckan_distinct(rid, "police_crime", ["StatisticGroupKod", "StatisticGroup"], offline):
                if d.get("StatisticGroupKod") is not None:
                    group_names[int(d["StatisticGroupKod"])] = (d.get("StatisticGroup") or "").strip()
        except Exception as e:  # noqa: BLE001 - מדווחים וממשיכים
            fetch_status[y] = {"error": str(e)}
            log(f"  !! שנה {y} נכשלה: {e}")
    ok_years = [y for y in years if "error" not in fetch_status.get(y, {"error": 1})]

    census = ckan_search_all(CENSUS_RESOURCE, "cbs_census2022", CENSUS_FIELDS, offline=offline, log=log)
    pidx = build_population_index(census)
    unit_pop = {v["unit"]: v["pop"] for v in pidx.values()}

    def unit_of(yk, sn):
        hit = pidx.get((yk, sn))
        return hit["unit"] if hit else f"{yk}:{sn}"

    agg = aggregate_crime(records, unit_of=unit_of)
    rows = compute_unit_rates(agg["per_unit"], unit_pop, ok_years)
    medians = add_ratios(rows)
    # חציון ארצי (לבדיקת השערה לגבי טבלת המשתמש)
    nat_median = median_of(r["rate_per_1000"] for r in rows if r["status"] == "ok")

    # שמות אזורים לכל יחידה
    def unit_name(unit: str) -> str:
        yk, parts = unit.split(":")
        names = []
        for p in parts.split("+"):
            nm = area_names.get(int(yk) * 10000 + int(p))
            if nm:
                names.append(nm)
        return " + ".join(names)

    for r in rows:
        r["yeshuv_name"] = yeshuv_names.get(r["yeshuv"], "")
        r["area_name"] = unit_name(r["unit"])
        r["category_default"] = categorize(r.get("ratio_city_median"))
        r["category_with_around_median"] = categorize(r.get("ratio_city_median"),
                                                      around_median=AROUND_MEDIAN_BAND)
        r["ratio_national_median"] = (r["rate_per_1000"] / nat_median
                                      if (nat_median and r["status"] == "ok") else None)
        r["category_vs_national_median"] = categorize(r["ratio_national_median"])
        r["flags"] = ";".join(quality_flags(r, r["area_name"]))
        r["pop_join"] = POP_JOIN_NOTE if r["pop"] is not None else ""

    # סיכום עירוני (כל היישובים) - כולל תיקים בלי אזור סטטיסטי
    city_rows = []
    by_y: Dict[int, dict] = defaultdict(lambda: defaultdict(int))
    for (yk, year), c in agg["city"].items():
        if year not in ok_years:
            continue
        for k, v in c.items():
            by_y[yk][k] += v
    census_city_pop = {}
    for c in census:
        if c.get("StatArea") in (None, "") and c.get("LocalityCode") not in (None, ""):
            try:
                census_city_pop[int(float(c["LocalityCode"]))] = float(c["pop_approx"]) if c.get("pop_approx") else None
            except (TypeError, ValueError):
                pass
    unit_counts = defaultdict(lambda: defaultdict(int))
    for r in rows:
        unit_counts[r["yeshuv"]][r["status"]] += 1
    for yk, c in by_y.items():
        pop = census_city_pop.get(yk)
        nyears = len(ok_years) or 1
        city_rows.append({
            "yeshuv": yk, "yeshuv_name": yeshuv_names.get(yk, ""), "years": "-".join(map(str, ok_years)),
            "records": c["records"], "records_no_stat_area": c["records_no_sa"],
            "pct_records_no_stat_area": _r(100 * c["records_no_sa"] / c["records"], 1) if c["records"] else None,
            "cases": c["cases"], "cases_city_level_only": c["cases_city_only"],
            "pct_cases_city_level_only": _r(100 * c["cases_city_only"] / c["cases"], 1) if c["cases"] else None,
            "census_pop_2022": pop,
            "city_rate_all_cases_per_1000": _r(c["cases"] / nyears / pop * 1000, 2) if pop else None,
            "units_ok": unit_counts[yk]["ok"],
            "units_pop_below_min": unit_counts[yk]["pop_below_min"],
            "units_no_police_records": unit_counts[yk]["no_police_records"],
            "units_no_census_match": unit_counts[yk]["no_census_match"],
            "units_no_population": unit_counts[yk]["no_population"],
            "city_median_unit_rate": _r(medians.get(yk), 3),
        })
    city_rows.sort(key=lambda r: -r["records"])
    return {"rows": rows, "city_rows": city_rows, "medians": medians, "national_median": nat_median,
            "area_names": area_names, "group_names": group_names, "fetch_status": fetch_status,
            "years": ok_years, "n_records": len(records), "census_rows": len(census),
            "pop_index": pidx}


def crosscheck_user_table(crime: dict, path: str = USER_SIGNAL_CSV) -> List[dict]:
    """השוואה בין טבלת המשתמש (bait-israeli, מקור משני) לבין החישוב מהנתונים הראשוניים."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        user = list(csv.DictReader(f))
    rows_by_unit = {r["unit"]: r for r in crime["rows"]}
    out = []
    for u in user:
        yk = CITY_NAME_TO_CODE.get(u["city"])
        pats = NEIGHBORHOOD_PATTERNS.get((yk, u["neighborhood"]), [])
        kods = match_areas(crime["area_names"], yk, pats) if yk else []
        pidx = crime["pop_index"]
        units = sorted({(pidx[(yk, k % 10000)]["unit"] if (yk, k % 10000) in pidx else f"{yk}:{k % 10000}")
                        for k in kods})
        sel = [rows_by_unit[x] for x in units if x in rows_by_unit and rows_by_unit[x]["status"] == "ok"]
        pop = sum(r["pop"] for r in sel) if sel else None
        cases = sum(r["cases_mean_annual"] for r in sel) if sel else None
        rate = cases / pop * 1000 if pop else None
        med = crime["medians"].get(yk)
        ratio = rate / med if (rate is not None and med) else None
        out.append({
            "city": u["city"], "neighborhood": u["neighborhood"],
            "user_rate": u["cases_per_1000_per_year"], "user_ratio": u["stated_ratio"],
            "user_signal": u["stated_signal"],
            "matched_stat_areas": " | ".join(f"{k}:{crime['area_names'].get(k, '')}" for k in kods),
            "units_used": len(sel), "pop_2022": pop, "cases_mean_annual": _r(cases, 1),
            "primary_rate_per_1000": _r(rate, 2), "primary_ratio_city_median": _r(ratio, 3),
            "primary_category_default": categorize(ratio),
            "primary_category_with_around_median": categorize(ratio, around_median=AROUND_MEDIAN_BAND),
            "unit_ratios": " | ".join(f"{r['area_name']}={_r(r['ratio_city_median'], 2)}" for r in sel),
        })
    return out


def build_accidents(offline: bool, log=print) -> dict:
    log("== תאונות הולכי רגל (PUF)")
    pts_all: List[dict] = []
    stats = {}
    for y, rid in sorted(PUF_RESOURCES.items()):
        try:
            rows = ckan_search_all(rid, "cbs_puf", None, offline=offline, log=log)
            pts, st = extract_pedestrian_accidents(rows)
            st["rows"] = len(rows)
            stats[y] = st
            pts_all.extend(pts)
        except Exception as e:  # noqa: BLE001
            stats[y] = {"error": str(e)}
            log(f"  !! PUF {y} נכשל: {e}")
    # בדיקת שפיות: נקודות עם סמל יישוב 5000 צריכות ליפול בתיבת תל אביב-יפו
    tlv = [p for p in pts_all if p["yishuv_code"] == 5000]
    in_box = sum(1 for p in tlv if 32.02 <= p["lat"] <= 32.15 and 34.73 <= p["lon"] <= 34.86)
    return {"points": pts_all, "stats": stats,
            "sanity_tlv": {"tlv_points": len(tlv), "in_tlv_bbox": in_box}}


def build_lights(offline: bool, log=print) -> dict:
    log("== עמודי תאורה")
    res = {}
    try:
        feats = arcgis_query_all(TLV_LIGHTS_LAYER, "tlv_gis", "oid_amud,t_sug,ms_panasim",
                                 "oid_amud", 4326, offline, log)
        res["tlv"] = normalize_tlv_lights(feats)
        # אימות המרה: אותם 5 עמודים ב-outSR=2039 (Esri) מול ההמרה שלנו
        q = {"where": "oid_amud<=5", "outFields": "oid_amud", "returnGeometry": "true",
             "outSR": 2039, "orderByFields": "oid_amud", "f": "json"}
        itm = http_get_json(TLV_LIGHTS_LAYER + "/query?" + urllib.parse.urlencode(q), "tlv_gis", offline)
        wgs = {p["id"]: p for p in res["tlv"] if p["id"] is not None and p["id"] <= 5}
        errs = []
        for f in itm.get("features", []):
            oid = f["attributes"]["oid_amud"]
            if oid in wgs:
                la, lo = itm_to_wgs84(f["geometry"]["x"], f["geometry"]["y"])
                errs.append(haversine_m(la, lo, wgs[oid]["lat"], wgs[oid]["lon"]))
        res["tlv_itm_check_m"] = errs
    except Exception as e:  # noqa: BLE001
        res["tlv_error"] = str(e)
        log(f"  !! TLV נכשל: {e}")
    try:
        recs = ckan_search_all(BR7_LIGHTS_WGS84, "br7_lights", None, offline=offline, log=log)
        res["br7"] = normalize_br7_lights(recs)
        itm = ckan_search_all(BR7_LIGHTS_ITM, "br7_lights", None, offline=offline, log=log)
        by_id = {r["_id"]: r for r in recs}
        errs = []
        for r in itm:
            w = by_id.get(r["_id"])
            if not w:
                continue
            try:
                la, lo = itm_to_wgs84(float(r["lon"]), float(r["lat"]))  # בקובץ ה-ITM: lon=X, lat=Y
                errs.append(haversine_m(la, lo, float(w["lat"]), float(w["lon"])))
            except (TypeError, ValueError):
                pass
        res["br7_itm_check_m"] = errs
        res["br7_raw_rows"] = len(recs)
    except Exception as e:  # noqa: BLE001
        res["br7_error"] = str(e)
        log(f"  !! באר שבע נכשל: {e}")
    return res


CRIME_UNIT_FIELDS = ["yeshuv", "yeshuv_name", "unit", "area_name", "pop", "cases_total",
                     "cases_mean_annual", "rate_per_1000", "city_median_rate", "ratio_city_median",
                     "category_default", "category_with_around_median", "ratio_national_median",
                     "category_vs_national_median",
                     "status", "flags", "pop_join"]


def _round_row(r: dict) -> dict:
    o = dict(r)
    for k in ("cases_mean_annual", "rate_per_1000", "city_median_rate", "ratio_city_median",
              "ratio_national_median"):
        o[k] = _r(o.get(k), 3)
    return o


def _stats(vals: List[float]) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {"n": len(s), "max_m": max(s), "median_m": statistics.median(s), "p99_m": s[int(0.99 * (len(s) - 1))]}


def build(years: Sequence[int] = DEFAULT_YEARS, offline: bool = False, skip_lights: bool = False,
          skip_accidents: bool = False, log=print) -> dict:
    os.makedirs(DERIVED_DIR, exist_ok=True)
    summary: dict = {"years": list(years), "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}

    crime = build_crime(years, offline, log)
    ytag = "-".join(map(str, crime["years"]))
    year_cols = [f"cases_{y}" for y in crime["years"]]
    fields = CRIME_UNIT_FIELDS[:6] + year_cols + CRIME_UNIT_FIELDS[6:]
    all_rows = sorted((_round_row(r) for r in crime["rows"]),
                      key=lambda r: (r["yeshuv"], -(r["rate_per_1000"] or -1)))
    _write_csv(os.path.join(DERIVED_DIR, f"crime_units_all_{ytag}.csv"), all_rows, fields)
    _write_csv(os.path.join(DERIVED_DIR, f"crime_city_summary_{ytag}.csv"), crime["city_rows"])
    target_rows = [r for r in all_rows if r["yeshuv"] in TARGET_CITIES]
    tmp_summary = os.path.join(DERIVED_DIR, "crime_by_stat_area_summary.csv")
    _write_csv(tmp_summary, target_rows, fields)
    size = os.path.getsize(tmp_summary)
    if size < SUMMARY_MAX_BYTES:
        _write_csv(SUMMARY_CSV, target_rows, fields)
        summary["summary_csv"] = {"path": SUMMARY_CSV, "bytes": size, "rows": len(target_rows)}
    else:
        summary["summary_csv"] = {"path": tmp_summary, "bytes": size, "rows": len(target_rows),
                                  "note": "גדול מ-1MB, נשאר ב-derived"}
    xc = crosscheck_user_table(crime)
    _write_csv(os.path.join(DERIVED_DIR, f"crosscheck_user_table_{ytag}.csv"), xc)

    summary["crime"] = {
        "fetch_status": crime["fetch_status"], "years_used": crime["years"],
        "n_records": crime["n_records"], "census_rows": crime["census_rows"],
        "national_median_unit_rate": crime["national_median"],
        "group_names": crime["group_names"],
        "cities": [c for c in crime["city_rows"] if c["yeshuv"] in TARGET_CITIES],
        "national": {
            "records": sum(c["records"] for c in crime["city_rows"]),
            "records_no_stat_area": sum(c["records_no_stat_area"] for c in crime["city_rows"]),
            "cases": sum(c["cases"] for c in crime["city_rows"]),
            "cases_city_level_only": sum(c["cases_city_level_only"] for c in crime["city_rows"]),
        },
        "crosscheck": xc,
    }
    tops = {}
    for yk in TARGET_CITIES:
        ok = [r for r in all_rows if r["yeshuv"] == yk and r["status"] == "ok"]
        ok.sort(key=lambda r: -r["rate_per_1000"])
        pick = ["unit", "area_name", "pop", "cases_mean_annual", "rate_per_1000",
                "ratio_city_median", "category_default", "flags"]
        tops[yk] = {"top5": [{k: r[k] for k in pick} for r in ok[:5]],
                    "bottom5": [{k: r[k] for k in pick} for r in ok[-5:]],
                    "category_counts": dict(sorted(
                        {c: sum(1 for r in ok if r["category_default"] == c)
                         for c in {r["category_default"] for r in ok}}.items()))}
    summary["crime"]["tops"] = tops

    if not skip_accidents:
        acc = build_accidents(offline, log)
        _write_csv(os.path.join(DERIVED_DIR, "pedestrian_accidents_2020-2024.csv"), acc["points"])
        gj = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
             "properties": {k: v for k, v in p.items() if k not in ("lat", "lon", "itm_x", "itm_y")}}
            for p in acc["points"]]}
        with open(os.path.join(DERIVED_DIR, "pedestrian_accidents_2020-2024.geojson"), "w", encoding="utf-8") as f:
            json.dump(gj, f, ensure_ascii=False)
        pts = acc["points"]
        summary["accidents"] = {
            "per_year": acc["stats"], "sanity_tlv": acc["sanity_tlv"], "total_points": len(pts),
            "by_severity": {s: sum(1 for p in pts if p["severity"] == s) for s in SEVERITY.values()},
            "by_day_night": {s: sum(1 for p in pts if p["day_night"] == s) for s in ("יום", "לילה", "")},
            "night_lighting": {l: sum(1 for p in pts if p["day_night"] == "לילה" and p["lighting"] == l)
                               for l in sorted({p["lighting"] for p in pts if p["day_night"] == "לילה"})},
            "by_city": {TARGET_CITIES[c]: sum(1 for p in pts if p["yishuv_code"] == c) for c in TARGET_CITIES},
            "by_city_anchored_igun1": {TARGET_CITIES[c]: sum(1 for p in pts if p["yishuv_code"] == c and p["status_igun"] == 1)
                                       for c in TARGET_CITIES},
        }

    if not skip_lights:
        li = build_lights(offline, log)
        lights_summary = {}
        for key, fname in (("tlv", "streetlights_tel_aviv.csv"), ("br7", "streetlights_beer_sheva.csv")):
            if key in li:
                _write_csv(os.path.join(DERIVED_DIR, fname), li[key])
                lats = [p["lat"] for p in li[key]]
                lons = [p["lon"] for p in li[key]]
                lights_summary[key] = {"points": len(li[key]),
                                       "bbox": [min(lons), min(lats), max(lons), max(lats)] if lats else None}
            if f"{key}_error" in li:
                lights_summary[key] = {"error": li[f"{key}_error"]}
        lights_summary["tlv_itm_check_m"] = _stats(li.get("tlv_itm_check_m", []))
        lights_summary["br7_itm_check_m"] = _stats(li.get("br7_itm_check_m", []))
        lights_summary["br7_raw_rows"] = li.get("br7_raw_rows")
        summary["lights"] = lights_summary

    with open(os.path.join(DERIVED_DIR, f"build_summary_{ytag}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    log(f"== סיום. פלטים ב-{DERIVED_DIR}")
    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research_tools.israel_layer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="שליפה (עם cache) + חישוב כל הפלטים")
    b.add_argument("--years", type=int, nargs="+", default=list(DEFAULT_YEARS),
                   choices=sorted(CRIME_RESOURCES))
    b.add_argument("--offline", action="store_true", help="רק מה-cache, בלי רשת")
    b.add_argument("--skip-lights", action="store_true")
    b.add_argument("--skip-accidents", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        build(args.years, args.offline, args.skip_lights, args.skip_accidents)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())

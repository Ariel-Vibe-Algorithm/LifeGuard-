# -*- coding: utf-8 -*-
"""
risk_factors.py - גורמי סיכון סביבתיים לבטיחות אישית ברחוב (מחקר בלבד, לא חלק מהאפליקציה).

מה זה בונה:
  1. שכבות גורמים (OSM Overpass + GIS עירוני של ת"א) לארבע ערים: ת"א-יפו, ירושלים, חיפה, באר שבע:
     ברים/פאבים/מועדונים, חנויות אלכוהול, עסקים פתוחים בלילה (לפי opening_hours), תחנות אוטובוס,
     תחנות רכבת/רק"ל, כספומטים, פארקים (פוליגונים), תחנות משטרה, מבנים נטושים (כיסוי OSM דל),
     פעילות מסחרית כללית (מסעדות/קפה/מכולות - "בקרת פעילות"), + עמודי תאורה ותאונות הולכי רגל
     מהשכבה הקיימת (research/samples/israel/derived).
  2. רשת ~100 מ' (0.001°) עם ספירות לכל גורם, קוד אזור סטטיסטי 2011 לכל תא, ודגלי כיסוי.
  3. **חיבור נתוני הפשיעה של המשטרה לפוליגונים**: קודי האזורים של המשטרה תואמים לגרסת
     האזורים הסטטיסטיים 2011 של הלמ"ס (לא 2022 - ראו israel_layer_report.md §2.3). הלמ"ס מפרסמת
     את פוליגוני 2011 ואת האוכלוסייה עליהם בשירותי ArcGIS ציבוריים (ISRAEL_CBS_GIS).
     סל עבירות "בטיחות אישית ברחוב" (StatisticTypeKod) נשלף מ-data.gov.il בסינון לארבע הערים.
  4. אימות: האם הגורמים מסבירים את מספר התיקים לאזור (בינומי שלילי, עם אוכלוסייה ושטח כמשתנים,
     אפקט קבוע לעיר), מעבר לתאונות/תאורה ומעבר ל"פעילות מסחרית" כללית.

עקרונות (כמו israel_layer):
- כל בקשת רשת נשמרת ב-cache (gzip) תחת research/samples/risk_factors/cache; --offline = רק cache.
- User-Agent כללי, השהיה בין בקשות, ניסיונות חוזרים. בלי פרטים אישיים.
- **לא נשלפים** שדות דת/מוצא/הכנסה. המדד החברתי-כלכלי נשלף רק כמשתנה בקרה לבדיקת רגישות
  באימות - הוא לא נכנס לרשת ולא מיועד להצגה או לניקוד.
- מיקומי מצלמות לא נשלפים (החלטת פרטיות של הפרויקט).
- תוצרים: research/samples/risk_factors/ ו-models/risk_factors_grid.json.gz - שניהם ב-gitignore.

שימוש:
  python -m research_tools.risk_factors build [--offline]
  python -m research_tools.risk_factors validate
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from research_tools import israel_layer as IL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "research", "samples", "risk_factors")
CACHE_DIR = os.path.join(OUT_DIR, "cache")
GRID_OUT = os.path.join(ROOT, "models", "risk_factors_grid.json.gz")
DERIVED = IL.DERIVED_DIR

USER_AGENT = "LifeGuard-Research/1.0"
REQUEST_DELAY_S = 1.0
OVERPASS_DELAY_S = 5.0
REQUEST_TIMEOUT_S = 240
REQUEST_RETRIES = 4

CELL_DEG = 0.001  # ~111 מ' צפון-דרום, ~95 מ' מזרח-מערב בקו רוחב 32

CITIES = {
    5000: {"name": "תל אביב-יפו", "en": "tel_aviv"},
    3000: {"name": "ירושלים", "en": "jerusalem"},
    4000: {"name": "חיפה", "en": "haifa"},
    9000: {"name": "באר שבע", "en": "beer_sheva"},
}
LIGHT_CITIES = {5000: "streetlights_tel_aviv.csv", 9000: "streetlights_beer_sheva.csv"}

# ---------------------------------------------------------------- מקורות

CBS_AGOL = "https://services2.arcgis.com/xMRYm7cNgdR5RN6F/arcgis/rest/services"
# פוליגוני אזורים סטטיסטיים 2011 (הגרסה שהמשטרה משייכת אליה) - בעלים: ISRAEL_CBS_GIS
CBS_SA2011 = CBS_AGOL + "/Statistical_Areas_2011/FeatureServer/0"
# מדד חברתי-כלכלי 2021 לפי אזור סטטיסטי 2011 - כולל אוכלוסייה ו"תפקוד עיקרי" של האזור.
CBS_SOEC2021 = CBS_AGOL + "/SOEC_Stat11_2021/FeatureServer/27"
# רק השדות האלה נשלפים. בכוונה בלי Religion_*, שכר/הכנסה/השכלה וכו'.
SOEC_FIELDS = ["SEMEL_YISH", "STAT11", "YISHUV_STA", "Stat11_Unite", "Main_Function_Code",
               "Pop_Total", "אוכלוסיית_המדד", "ערך_מדד"]

TLV_GIS = "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer"
TLV_LAYERS = {
    "tlv_business_licenses": (964, "t_hesber_mahut_esek"),  # מאגר עסקים ברשיון או בהיתר
    "tlv_night_zones": (575, "shem,k_sug,t_sug"),           # מדיניות לילה - אזורי בילוי
    "tlv_atm": (690, "oid_ATM"),                             # כספומטים
    "tlv_bus_stops": (956, "stop_id"),                       # תחנות אוטובוס (משרד התחבורה)
    "tlv_parks": (551, "sug_gan"),                           # שטחים ירוקים וגנים ציבוריים
    "tlv_police": (595, "shem_mitkan"),                      # תחנות משטרה
}
# אזורי בילוי ת"א: קוד סוג -> שעת סגירה מותרת (שעות אחרי חצות; 0 = חצות)
TLV_NIGHT_ZONE_CLOSE = {1: 1, 2: 2, 3: 3, 4: 5, 5: 5, 6: 0}

OVERPASS_ENDPOINTS = ("https://overpass-api.de/api/interpreter",
                      "https://overpass.kumi.systems/api/interpreter")

# משטרה: סוגי עבירה רלוונטיים לבטיחות אישית ברחוב (StatisticTypeKod).
# הנתונים לא מבחינים בין אלימות ברחוב לאלימות במשפחה/בבית - הסתייגות מרכזית.
POLICE_TYPES = {
    203: "קטטות והפרעות ברחובות",
    401: "חבלה גופנית חמורה",
    404: "תקיפה (למעט עובדי ציבור)",
    504: "מעשה מגונה בכח",
    506: "מעשה מגונה בפומבי",
    701: "שוד בנסיבות חמורות",
    714: "כייסות",
}
OUTCOMES = {
    "personal": (203, 401, 404, 504, 506, 701, 714),  # הסל הראשי
    "assault": (401, 404),
    "robbery_theft_person": (701, 714),
    "street_brawl": (203,),
}

FACTORS = ["bar", "nightclub", "alcohol_shop", "late_venue", "hours_known", "commercial_poi",
           "atm", "bus_stop", "rail_station", "police", "abandoned", "park",
           "lights", "ped_acc", "bar_licensed_tlv", "atm_tlv", "bus_stop_tlv", "park_tlv",
           "night_zone_close"]


# ================================================================= רשת + cache

def _cache_path(source: str, url: str, cache_dir: Optional[str] = None) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return os.path.join(cache_dir or CACHE_DIR, source, h + ".json.gz")


def fetch_json(url: str, source: str, offline: bool = False, cache_dir: Optional[str] = None,
               delay: float = REQUEST_DELAY_S, retries: int = REQUEST_RETRIES,
               timeout: float = REQUEST_TIMEOUT_S) -> dict:
    """GET JSON עם cache מקומי. offline=True: רק cache (אחרת FileNotFoundError)."""
    path = _cache_path(source, url, cache_dir)
    if os.path.exists(path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)["body"]
    if offline:
        raise FileNotFoundError(f"offline: אין cache עבור {url[:160]}")
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            time.sleep(delay * (1 + 2 * attempt))
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump({"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "body": body},
                          f, ensure_ascii=False)
            os.replace(tmp, path)
            return body
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code in (400, 401, 403, 404):
                break
    raise RuntimeError(f"נכשל: {url[:160]}: {last}")


def arcgis_all(layer: str, source: str, where: str = "1=1", out_fields: str = "*",
               geometry: bool = True, offline: bool = False, extra: Optional[dict] = None,
               log: Callable[[str], None] = print) -> List[dict]:
    """שליפת כל הישויות משכבת ArcGIS בדפדוף resultOffset. גאומטריה ב-WGS84."""
    meta = fetch_json(layer + "?f=json", source, offline)
    page = int(meta.get("maxRecordCount") or 1000)
    out: List[dict] = []
    offset = 0
    while True:
        q = {"where": where, "outFields": out_fields, "returnGeometry": "true" if geometry else "false",
             "outSR": 4326, "resultOffset": offset, "resultRecordCount": page, "f": "json"}
        if extra:
            q.update(extra)
        body = fetch_json(layer + "/query?" + urllib.parse.urlencode(q), source, offline)
        if "error" in body:
            raise RuntimeError(f"ArcGIS error {source}: {body['error']}")
        got = body.get("features", [])
        out.extend(got)
        if not got or (not body.get("exceededTransferLimit") and len(got) < page):
            break
        offset += len(got)
    log(f"  [{source}] {len(out)} ישויות")
    return out


def ckan_all(resource_id: str, source: str, fields: Sequence[str], filters: dict,
             offline: bool = False, page: int = 50000, log: Callable[[str], None] = print) -> List[dict]:
    out: List[dict] = []
    offset = 0
    while True:
        q = {"resource_id": resource_id, "limit": page, "offset": offset, "sort": "_id",
             "fields": ",".join(fields), "filters": json.dumps(filters, ensure_ascii=False)}
        body = fetch_json(IL.CKAN_SEARCH + "?" + urllib.parse.urlencode(q), source, offline)
        if not body.get("success"):
            raise RuntimeError(f"CKAN error {resource_id}: {body.get('error')}")
        recs = body["result"].get("records", [])
        total = body["result"].get("total")
        out.extend(recs)
        log(f"  [{source}] {resource_id[:8]} +{len(recs)} ({len(out)}/{total})")
        if not recs or (total is not None and len(out) >= total):
            break
        offset += len(recs)
    return out


def overpass(query: str, source: str, offline: bool = False, log: Callable[[str], None] = print) -> dict:
    last: Optional[Exception] = None
    for ep in OVERPASS_ENDPOINTS:
        url = ep + "?" + urllib.parse.urlencode({"data": query})
        # מפתח cache לפי השאילתה בלבד (לא לפי ה-endpoint)
        key = "overpass:" + query
        path = _cache_path(source, key)
        if os.path.exists(path):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)["body"]
        if offline:
            raise FileNotFoundError("offline: אין cache לשאילתת Overpass")
        try:
            body = fetch_json(url, source + "_raw", False, delay=OVERPASS_DELAY_S)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump({"url": key, "endpoint": ep, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "body": body}, f, ensure_ascii=False)
            raw = _cache_path(source + "_raw", url)
            if os.path.exists(raw):
                os.remove(raw)
            return body
        except RuntimeError as e:
            last = e
            log(f"  Overpass {ep} נכשל: {e}")
    raise RuntimeError(f"Overpass נכשל: {last}")


# ================================================================= גאומטריה

def cell_of(lat: float, lon: float) -> Tuple[int, int]:
    return int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG))


def cell_center(key: Tuple[int, int]) -> Tuple[float, float]:
    return (key[0] + 0.5) * CELL_DEG, (key[1] + 0.5) * CELL_DEG


def point_in_rings(lat: float, lon: float, rings: Sequence[Sequence[Sequence[float]]]) -> bool:
    """even-odd על כל הטבעות (ArcGIS: חיצוניות + חורים; כך גם מולטי-פוליגון). נקודות ב-[lon, lat]."""
    inside = False
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if (yi > lat) != (yj > lat):
                x = (xj - xi) * (lat - yi) / (yj - yi) + xi
                if lon < x:
                    inside = not inside
            j = i
    return inside


def rings_bbox(rings) -> Tuple[float, float, float, float]:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return min(ys), min(xs), max(ys), max(xs)  # s, w, n, e


def rings_area_km2(rings) -> float:
    """שטח בקמ"ר (הטלה מקומית שוות-מרחק; מספיק לאזורים של עד כמה ק"מ). חורים מנוכים לפי כיוון."""
    pts = [p for r in rings for p in r]
    if not pts:
        return 0.0
    lat0 = math.radians(sum(p[1] for p in pts) / len(pts))
    kx, ky = 111320.0 * math.cos(lat0), 110574.0
    total = 0.0
    for r in rings:
        s = 0.0
        for i in range(len(r)):
            x1, y1 = r[i][0] * kx, r[i][1] * ky
            x2, y2 = r[(i + 1) % len(r)][0] * kx, r[(i + 1) % len(r)][1] * ky
            s += x1 * y2 - x2 * y1
        total += s / 2.0
    return abs(total) / 1e6


def haversine_m(lat1, lon1, lat2, lon2):
    return IL.haversine_m(lat1, lon1, lat2, lon2)


class PolygonIndex:
    """אינדקס פשוט (bbox) לשאילתת point-in-polygon."""

    def __init__(self, items: Iterable[Tuple[object, list]]):
        self.items = [(k, rings, rings_bbox(rings)) for k, rings in items if rings]

    def find(self, lat: float, lon: float):
        for k, rings, (s, w, n, e) in self.items:
            if s <= lat <= n and w <= lon <= e and point_in_rings(lat, lon, rings):
                return k
        return None

    def contains_any(self, lat: float, lon: float) -> bool:
        return self.find(lat, lon) is not None


# ================================================================= OSM

_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})(\+)?")
_OPEN_END = re.compile(r"(\d{1,2}):(\d{2})\+")
LATE_MIN = 23 * 60  # 23:00 - גם השעה שבה נאסרת מכירת אלכוהול קמעונאית בישראל (חוק רישוי עסקים, 2010)


def opening_hours_late(oh: Optional[str]) -> Optional[bool]:
    """האם המקום פתוח אחרי 23:00 בחלק מימי השבוע, לפי תג opening_hours של OSM.

    True / False / None (אין תג או שלא ניתן לפענח). מפענח רק טווחי HH:MM-HH:MM, "24/7" ו-"HH:MM+".
    טווח שחוצה חצות (סיום <= התחלה) או מסתיים ב-23:00 ומעלה נחשב "פתוח מאוחר".
    """
    if not oh or not str(oh).strip():
        return None
    s = str(oh).strip()
    if "24/7" in s:
        return True
    found = False
    for m in _RANGE.finditer(s):
        found = True
        a = int(m.group(1)) * 60 + int(m.group(2))
        b = int(m.group(3)) * 60 + int(m.group(4))
        if m.group(5) or b <= a or b >= LATE_MIN:
            return True
    if _OPEN_END.search(s):
        return True
    if found:
        return False
    if re.fullmatch(r"(?i)\s*off\s*|\s*closed\s*", s):
        return False
    return None


ON_PREMISE = {"bar", "pub", "biergarten"}
NIGHTCLUB = {"nightclub", "stripclub"}
ALCOHOL_SHOPS = {"alcohol", "wine", "beverages"}
COMMERCIAL_AMENITY = {"restaurant", "cafe", "fast_food", "pharmacy", "fuel", "ice_cream"}
COMMERCIAL_SHOP = {"convenience", "supermarket", "kiosk", "bakery", "deli"}
RAIL = {"station", "halt", "tram_stop", "subway_entrance"}


def classify_osm(tags: dict) -> List[str]:
    """תגיות OSM -> רשימת גורמים. ישות אחת יכולה לתרום לכמה גורמים (בנק עם atm=yes -> atm)."""
    t = tags or {}
    am, shop = t.get("amenity"), t.get("shop")
    out = []
    if am in ON_PREMISE:
        out.append("bar")
    if am in NIGHTCLUB:
        out.append("nightclub")
    if shop in ALCOHOL_SHOPS:
        out.append("alcohol_shop")
    if am == "atm" or (am == "bank" and t.get("atm") == "yes"):
        out.append("atm")
    if t.get("highway") == "bus_stop" or (t.get("public_transport") == "platform" and t.get("bus") == "yes"):
        out.append("bus_stop")
    if t.get("railway") in RAIL and t.get("disused") != "yes":
        out.append("rail_station")
    if am == "police":
        out.append("police")
    if (t.get("building") in ("ruins", "abandoned") or t.get("abandoned") == "yes"
            or "abandoned:building" in t or "disused:building" in t or t.get("ruins") == "yes"):
        out.append("abandoned")
    venue = (am in ON_PREMISE | NIGHTCLUB | COMMERCIAL_AMENITY) or (shop in COMMERCIAL_SHOP | ALCOHOL_SHOPS)
    if am in COMMERCIAL_AMENITY or shop in COMMERCIAL_SHOP:
        out.append("commercial_poi")
    if venue:
        late = opening_hours_late(t.get("opening_hours"))
        if late is not None:
            out.append("hours_known")
        if late:
            out.append("late_venue")
    return out


def overpass_poi_query(bbox: Tuple[float, float, float, float]) -> str:
    s, w, n, e = (round(v, 4) for v in bbox)
    return f"""[out:json][timeout:240][bbox:{s},{w},{n},{e}];
(
  nwr["amenity"~"^(bar|pub|biergarten|nightclub|stripclub|restaurant|cafe|fast_food|pharmacy|fuel|ice_cream|atm|bank|police)$"];
  nwr["shop"~"^(alcohol|wine|beverages|convenience|supermarket|kiosk|bakery|deli)$"];
  node["highway"="bus_stop"];
  nwr["public_transport"="platform"]["bus"="yes"];
  nwr["railway"~"^(station|halt|tram_stop|subway_entrance)$"];
  nwr["building"~"^(ruins|abandoned)$"];
  nwr["abandoned"="yes"];
  nwr["abandoned:building"];
  nwr["disused:building"];
);
out center tags qt;"""


def overpass_parks_query(bbox: Tuple[float, float, float, float]) -> str:
    s, w, n, e = (round(v, 4) for v in bbox)
    return f"""[out:json][timeout:240][bbox:{s},{w},{n},{e}];
(way["leisure"="park"]; relation["leisure"="park"];);
out geom qt;"""


KEEP_TAGS = ("amenity", "shop", "highway", "public_transport", "bus", "railway", "building", "abandoned",
             "abandoned:building", "disused:building", "ruins", "atm", "opening_hours", "disused")


def parse_osm_points(body: dict) -> List[dict]:
    """אלמנטים של Overpass (out center) -> נקודות עם גורמים. שמות לא נשמרים."""
    pts = []
    seen = set()
    for el in body.get("elements", []):
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags") or {}
        f = classify_osm(tags)
        if f:
            pts.append({"lat": lat, "lon": lon, "factors": f, "osm": f"{el['type'][0]}{el['id']}"})
    return pts


def parse_osm_park_polygons(body: dict) -> List[list]:
    """פארקים: way סגור -> טבעת; relation -> כל member סגור (outer/inner) כטבעת (even-odd)."""
    polys = []
    for el in body.get("elements", []):
        if el.get("type") == "way":
            g = el.get("geometry") or []
            if len(g) >= 4 and g[0] == g[-1]:
                polys.append([[[p["lon"], p["lat"]] for p in g]])
        elif el.get("type") == "relation":
            rings = []
            for m in el.get("members", []):
                g = m.get("geometry") or []
                if len(g) >= 4 and g[0] == g[-1]:
                    rings.append([[p["lon"], p["lat"]] for p in g])
            if rings:
                polys.append(rings)
    return polys


# ================================================================= מקורות עירוניים (ת"א)

def is_licensed_bar(desc: Optional[str]) -> bool:
    """רישיון עסק בת"א שעיקרו הגשת משקאות משכרים (פריט 4.8 / פאב / בר) - לא מסעדה (4.2)."""
    if not desc:
        return False
    d = str(desc)
    if d.lstrip().startswith("4.2") or "ושאינו עסק שעיקר פעילותו" in d:
        return False
    return (d.lstrip().startswith("4.8") or "פאב" in d or "מסבאה" in d or
            ("משקאות משכרים" in d and "צריכה במקום" in d))


def tlv_close_hour(k_sug) -> Optional[int]:
    try:
        return TLV_NIGHT_ZONE_CLOSE.get(int(k_sug))
    except (TypeError, ValueError):
        return None


def _esri_point(f: dict) -> Optional[Tuple[float, float]]:
    g = f.get("geometry") or {}
    if g.get("y") is None or g.get("x") is None:
        return None
    return g["y"], g["x"]


# ================================================================= פשיעה לפי אזור 2011

def aggregate_police_by_type(records: Iterable[dict], outcomes: Dict[str, Sequence[int]] = OUTCOMES,
                             years: Optional[Sequence[int]] = None) -> Dict[str, dict]:
    """רשומות (YeshuvKod, StatisticAreaKod, StatisticTypeKod, FictiveIDNumber, Year) ->
    {"yeshuv:stat": {outcome: מספר תיקים ייחודיים}} + "city_only" לכל עיר (תיקים בלי אזור).
    תיק נספר פעם אחת לכל outcome גם אם יש בו כמה עבירות."""
    ys = set(int(y) for y in years) if years else None
    sets: Dict[str, Dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in records:
        if ys is not None and int(r.get("Year") or 0) not in ys:
            continue
        try:
            yk = int(r.get("YeshuvKod"))
            tk = int(r.get("StatisticTypeKod"))
        except (TypeError, ValueError):
            continue
        sn = IL.stat_number(r.get("StatisticAreaKod"), yk)
        unit = f"{yk}:{sn}" if sn is not None else f"{yk}:city_only"
        cid = r.get("FictiveIDNumber")
        for name, types in outcomes.items():
            if tk in types:
                sets[unit][name].add(cid)
    return {u: {k: len(v) for k, v in d.items()} for u, d in sets.items()}


def empirical_bayes_rates(counts: Sequence[float], exposure: Sequence[float]) -> List[Optional[float]]:
    """החלקת שיעורים (Marshall 1991, global EB): מושך שיעורים של אזורים קטנים לכיוון הממוצע.
    counts/exposure באותן יחידות (למשל תיקים ו-שנות-אדם/1000). מחזיר שיעור מוחלק לכל אזור."""
    pairs = [(c, e) for c, e in zip(counts, exposure) if e and e > 0]
    if not pairs:
        return [None] * len(counts)
    tot_c = sum(c for c, _ in pairs)
    tot_e = sum(e for _, e in pairs)
    m = tot_c / tot_e
    n = len(pairs)
    ebar = tot_e / n
    s2 = sum(e * (c / e - m) ** 2 for c, e in pairs) / tot_e
    a = max(s2 - m / ebar, 0.0)
    out = []
    for c, e in zip(counts, exposure):
        if not e or e <= 0:
            out.append(None)
            continue
        w = a / (a + m / e) if (a + m / e) > 0 else 0.0
        out.append(w * (c / e) + (1 - w) * m)
    return out


# ================================================================= רשת

def build_city_cells(polys: Dict[str, list], city: int) -> Dict[Tuple[int, int], str]:
    """כל התאים שמרכזם בתוך אזור סטטיסטי של העיר -> קוד האזור ("yeshuv:stat")."""
    idx = PolygonIndex((k, r) for k, r in polys.items() if int(k.split(":")[0]) == city)
    if not idx.items:
        return {}
    s = min(b[0] for _, _, b in idx.items)
    w = min(b[1] for _, _, b in idx.items)
    n = max(b[2] for _, _, b in idx.items)
    e = max(b[3] for _, _, b in idx.items)
    (y0, x0), (y1, x1) = cell_of(s, w), cell_of(n, e)
    cells = {}
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            lat, lon = cell_center((y, x))
            k = idx.find(lat, lon)
            if k is not None:
                cells[(y, x)] = k
    return cells


def add_points(grid: Dict[Tuple[int, int], Dict[str, int]], cells: Dict[Tuple[int, int], str],
               points: Iterable[Tuple[float, float, Sequence[str]]]) -> int:
    """מוסיף ספירות לתאים שבתוך העיר. מחזיר כמה נקודות נפלו בתוך התאים."""
    n = 0
    for lat, lon, factors in points:
        k = cell_of(lat, lon)
        if k not in cells:
            continue
        n += 1
        c = grid.setdefault(k, {})
        for f in factors:
            c[f] = c.get(f, 0) + 1
    return n


def mark_polygons(grid, cells, polygons: Iterable[list], factor: str, value: int = 1,
                  reducer: Callable[[int, int], int] = max) -> int:
    """מסמן תאים שמרכזם בתוך פוליגון (פארק / אזור בילוי)."""
    n = 0
    for rings in polygons:
        s, w, nn, e = rings_bbox(rings)
        (y0, x0), (y1, x1) = cell_of(s, w), cell_of(nn, e)
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if (y, x) not in cells:
                    continue
                lat, lon = cell_center((y, x))
                if point_in_rings(lat, lon, rings):
                    c = grid.setdefault((y, x), {})
                    c[factor] = reducer(c.get(factor, 0), value) if factor in c else value
                    n += 1
    return n


def factors_near(grid: Dict[Tuple[int, int], Dict[str, int]], lat: float, lon: float, ring: int = 1) -> Dict[str, int]:
    """סכום הגורמים בתא ובשכנים (ring=1 -> 3x3 תאים, ~300x300 מ'). פארק/אזור בילוי: מקסימום."""
    cy, cx = cell_of(lat, lon)
    out: Dict[str, int] = {}
    for dy in range(-ring, ring + 1):
        for dx in range(-ring, ring + 1):
            c = grid.get((cy + dy, cx + dx))
            if not c:
                continue
            for k, v in c.items():
                if k in ("park", "park_tlv", "night_zone_close"):
                    out[k] = max(out.get(k, 0), v)
                else:
                    out[k] = out.get(k, 0) + v
    return out


def load_grid(path: str = GRID_OUT) -> Optional[dict]:
    """טוען את הרשת הקומפקטית -> {"cells": {(y,x): {factor: n}}, "meta": {(y,x): (city, stat11)},
    "stat_areas": ..., "coverage": ...}. חסר קובץ -> None (כמו LightGrid: "לא זמין", לא ניחוש)."""
    if not os.path.exists(path):
        return None
    with gzip.open(path, "rt", encoding="utf-8") as f:
        d = json.load(f)
    names = d["fields"][2:]
    cells, meta = {}, {}
    for k, v in d["cells"].items():
        key = tuple(int(x) for x in k.split(":"))
        meta[key] = (v[0], v[1])
        c = {n: x for n, x in zip(names, v[2:]) if x}
        if c:
            cells[key] = c
    return {"cells": cells, "meta": meta, "stat_areas": d.get("stat_areas", {}),
            "coverage": d.get("coverage", {}), "cell_deg": d.get("cell_deg", CELL_DEG)}


def area_context(g: dict, lat: float, lon: float) -> Optional[dict]:
    """אזור סטטיסטי 2011 של הנקודה + סיכום הפשיעה שלו (אם האזור "ok"). ברמת אזור, לא רחוב."""
    m = g["meta"].get(cell_of(lat, lon))
    if not m:
        return None
    return dict(g["stat_areas"].get(f"{m[0]}:{m[1]}") or {}, city=m[0], stat11=m[1])


# ================================================================= בנייה

def _read_csv_points(path: str) -> List[Tuple[float, float]]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((float(r["lat"]), float(r["lon"])))
            except (KeyError, TypeError, ValueError):
                pass
    return out


def fetch_stat_areas(offline: bool, log=print) -> Tuple[Dict[str, list], Dict[str, dict], dict]:
    where = "SEMEL_YISH IN (" + ",".join(str(c) for c in CITIES) + ")"
    feats = arcgis_all(CBS_SA2011, "cbs_sa2011", where, "SEMEL_YISH,STAT11,YISHUV_STA", True, offline,
                       {"geometryPrecision": 6, "maxAllowableOffset": 0.00002}, log)
    polys: Dict[str, list] = {}
    for f in feats:
        a = f.get("attributes") or {}
        rings = (f.get("geometry") or {}).get("rings")
        if not rings or a.get("STAT11") is None:
            continue
        key = f"{int(a['SEMEL_YISH'])}:{int(a['STAT11'])}"
        polys.setdefault(key, []).extend(rings)
    attrs = arcgis_all(CBS_SOEC2021, "cbs_soec2021", where, ",".join(SOEC_FIELDS), False, offline, None, log)
    rows = [f.get("attributes") or {} for f in attrs]
    unit_of = unit_map(rows)
    info: Dict[str, dict] = {}
    for a in rows:
        if a.get("STAT11") is None or a.get("Pop_Total") is None:
            continue
        key = unit_of[f"{int(a['SEMEL_YISH'])}:{int(a['STAT11'])}"]
        info[key] = {"pop": a.get("Pop_Total"), "pop_index": a.get("אוכלוסיית_המדד"),
                     "main_function": a.get("Main_Function_Code"), "unite": a.get("Stat11_Unite"),
                     "_ses_index": a.get("ערך_מדד")}
    for raw in polys:
        unit_of.setdefault(raw, raw)
    upolys: Dict[str, list] = defaultdict(list)
    for raw, rings in polys.items():
        upolys[unit_of[raw]].extend(rings)
    meta = {"polygons_2011": len(polys), "attribute_rows": len(rows), "units": len(upolys),
            "merged_units": sum(1 for u in upolys if "+" in u)}
    return dict(upolys), info, meta, unit_of


def unit_map(rows: Iterable[dict]) -> Dict[str, str]:
    """הלמ"ס מאחדת חלק מהאזורים בפרסום (Stat11_Unite="712+713"): האוכלוסייה מופיעה בשורת הראש,
    ולשאר החברים אין אוכלוסייה. מחזיר "yeshuv:stat" -> מפתח יחידה "yeshuv:head+other..." (הראש ראשון)."""
    out: Dict[str, str] = {}
    for a in rows:
        if a.get("STAT11") is None:
            continue
        y, s = int(a["SEMEL_YISH"]), int(a["STAT11"])
        u = str(a.get("Stat11_Unite") or "").replace(" ", "")
        parts = []
        for p in u.split("+"):
            try:
                parts.append(int(float(p)))
            except ValueError:
                pass
        if len(parts) > 1 and s in parts and a.get("Pop_Total") is not None:
            key = f"{y}:" + "+".join([str(s)] + [str(p) for p in parts if p != s])
            for p in parts:
                out[f"{y}:{p}"] = key
        else:
            out.setdefault(f"{y}:{s}", f"{y}:{s}")
    return out


def unit_counts(police_counts: Dict[str, dict], unit_of: Dict[str, str]) -> Dict[str, dict]:
    """ספירות משטרה לפי קוד גולמי -> לפי יחידה (סכום החברים). קודים בלי יחידה נשארים כמו שהם."""
    out: Dict[str, Counter] = defaultdict(Counter)
    for raw, c in police_counts.items():
        out[unit_of.get(raw, raw)].update(c)
    return {k: dict(v) for k, v in out.items()}


def fetch_police(years: Sequence[int], offline: bool, log=print) -> Tuple[List[dict], Dict[int, str]]:
    recs: List[dict] = []
    names: Dict[int, str] = {}
    flt = {"YeshuvKod": sorted(CITIES), "StatisticTypeKod": sorted(POLICE_TYPES)}
    for y in years:
        rid = IL.CRIME_RESOURCES[y]
        recs.extend(ckan_all(rid, "police_types", ["YeshuvKod", "StatisticAreaKod", "StatisticTypeKod",
                                                    "FictiveIDNumber", "Year"], flt, offline, log=log))
        q = {"resource_id": rid, "distinct": "true", "fields": "StatisticAreaKod,StatisticArea", "limit": 20000,
             "filters": json.dumps({"YeshuvKod": sorted(CITIES)})}
        body = fetch_json(IL.CKAN_SEARCH + "?" + urllib.parse.urlencode(q), "police_names", offline)
        for d in body["result"]["records"]:
            if d.get("StatisticAreaKod"):
                names[int(d["StatisticAreaKod"])] = d.get("StatisticArea") or ""
    return recs, names


def fetch_osm(bbox, city_en: str, offline: bool, log=print) -> Tuple[List[dict], List[list], str]:
    poi = overpass(overpass_poi_query(bbox), f"osm_{city_en}", offline, log)
    parks = overpass(overpass_parks_query(bbox), f"osm_{city_en}", offline, log)
    base = (poi.get("osm3s") or {}).get("timestamp_osm_base", "")
    return parse_osm_points(poi), parse_osm_park_polygons(parks), base


def fetch_tlv(offline: bool, log=print) -> Dict[str, List[dict]]:
    out = {}
    for name, (lid, fields) in TLV_LAYERS.items():
        try:
            out[name] = arcgis_all(f"{TLV_GIS}/{lid}", name, "1=1", fields, True, offline, None, log)
        except Exception as e:  # noqa: BLE001 - שכבה עירונית חסרה = "לא זמין", לא כשל
            log(f"  !! {name}: {e}")
            out[name] = []
    return out


def _unit_parts(unit: str) -> Tuple[int, List[int]]:
    y, rest = unit.split(":")
    return int(y), [int(p) for p in rest.split("+")]


def stat_area_table(polys, info, police_counts, names, cells_by_city, grid, years) -> List[dict]:
    """שורה לכל אזור סטטיסטי 2011: אוכלוסייה, שטח, תיקים (לפי outcome), שיעורים, יחס לחציון, דגלים,
    וסכומי הגורמים מתוך התאים שבאזור."""
    nyears = len(years)
    agg: Dict[str, Counter] = defaultdict(Counter)
    ncells: Counter = Counter()
    csum: Dict[str, List[float]] = defaultdict(lambda: [0.0, 0.0])
    for city, cells in cells_by_city.items():
        for k, unit in cells.items():
            ncells[unit] += 1
            la, lo = cell_center(k)
            csum[unit][0] += la
            csum[unit][1] += lo
            for f, v in (grid.get(k) or {}).items():
                if f in ("park", "park_tlv"):
                    agg[unit][f + "_cells"] += 1
                elif f == "night_zone_close":
                    agg[unit]["night_zone_cells"] += 1
                else:
                    agg[unit][f] += v
    rows = []
    for unit, rings in polys.items():
        yk, members = _unit_parts(unit)
        sn = members[0]
        inf = info.get(unit, {})
        pc = police_counts.get(unit, {})
        pop = inf.get("pop")
        nm = " + ".join(n for n in (names.get(yk * 10000 + m, "") for m in members) if n)
        row = {"yeshuv": yk, "city": CITIES[yk]["name"], "unit": unit, "stat11": sn,
               "area_name": nm, "pop": pop,
               "main_function": inf.get("main_function"), "area_km2": round(rings_area_km2(rings), 4),
               "cells": ncells[unit], "in_police_data": unit in police_counts,
               "lat": round(csum[unit][0] / ncells[unit], 5) if ncells[unit] else None,
               "lon": round(csum[unit][1] / ncells[unit], 5) if ncells[unit] else None}
        for o in OUTCOMES:
            row[f"cases_{o}"] = pc.get(o, 0)
        row["cases_mean_annual"] = row["cases_personal"] / nyears
        row["rate_personal_per_1000"] = (row["cases_mean_annual"] / pop * 1000) if pop else None
        for f in FACTORS:
            if f in ("park", "park_tlv"):
                row[f + "_cells"] = agg[unit].get(f + "_cells", 0)
            elif f == "night_zone_close":
                row["night_zone_cells"] = agg[unit].get("night_zone_cells", 0)
            else:
                row[f] = agg[unit].get(f, 0)
        row["_ses_index"] = inf.get("_ses_index")
        rows.append(row)
    # סטטוס, יחס לחציון העיר, EB ודגלים - באותו היגיון כמו israel_layer
    for r in rows:
        if not r["pop"]:
            r["status"] = "no_population"
        elif r["pop"] < IL.MIN_POP_FOR_RATE:
            r["status"] = "pop_below_min"
        elif not r["in_police_data"]:
            r["status"] = "no_police_records"
        else:
            r["status"] = "ok"
    for city in CITIES:
        ok = [r for r in rows if r["yeshuv"] == city and r["status"] == "ok"]
        med = IL.median_of(r["rate_personal_per_1000"] for r in ok)
        eb = empirical_bayes_rates([r["cases_mean_annual"] for r in ok], [r["pop"] / 1000 for r in ok])
        eb_med = IL.median_of(eb)
        for r, e in zip(ok, eb):
            r["eb_rate_per_1000"] = e
            r["eb_ratio_city_median"] = (e / eb_med) if (e is not None and eb_med) else None
        for r in rows:
            if r["yeshuv"] != city:
                continue
            r["city_median_rate"] = med
            r["ratio_city_median"] = (r["rate_personal_per_1000"] / med) if (r["status"] == "ok" and med) else None
            r["category_default"] = IL.categorize(r["ratio_city_median"])
            r["flags"] = ";".join(IL.quality_flags(r, r["area_name"]) +
                                  (["non_residential_area"] if r["main_function"] not in (None, 1) else []))
    return rows


def build(offline: bool = False, years: Sequence[int] = IL.DEFAULT_YEARS, log=print) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    log("== אזורים סטטיסטיים 2011 (הלמ\"ס)")
    polys, info, sa_meta, unit_of = fetch_stat_areas(offline, log)
    log("== משטרה: סל בטיחות אישית לפי אזור")
    precs, names = fetch_police(years, offline, log)
    raw_counts = aggregate_police_by_type(precs, years=years)
    police_counts = unit_counts(raw_counts, unit_of)

    grid: Dict[Tuple[int, int], Dict[str, int]] = {}
    cells_by_city: Dict[int, Dict[Tuple[int, int], str]] = {}
    coverage: Dict[int, dict] = {}
    sources = []
    lights_by_city = {c: _read_csv_points(os.path.join(DERIVED, fn)) for c, fn in LIGHT_CITIES.items()}
    accidents = []
    acc_path = os.path.join(DERIVED, "pedestrian_accidents_2020-2024.csv")
    if os.path.exists(acc_path):
        with open(acc_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    accidents.append((float(r["lat"]), float(r["lon"]), ["ped_acc"]))
                except (TypeError, ValueError, KeyError):
                    pass
    tlv = fetch_tlv(offline, log)
    osm_counts: Dict[int, Counter] = {}
    for city, meta in CITIES.items():
        log(f"== {meta['name']}")
        cells = build_city_cells(polys, city)
        cells_by_city[city] = cells
        if not cells:
            coverage[city] = {"cells": 0, "error": "no stat-area polygons"}
            continue
        lats = [cell_center(k)[0] for k in cells]
        lons = [cell_center(k)[1] for k in cells]
        bbox = (min(lats) - 0.002, min(lons) - 0.002, max(lats) + 0.002, max(lons) + 0.002)
        cov = {"cells": len(cells), "bbox": [round(v, 4) for v in bbox]}
        try:
            pts, parks, base = fetch_osm(bbox, meta["en"], offline, log)
            inside = add_points(grid, cells, ((p["lat"], p["lon"], p["factors"]) for p in pts))
            cnt = Counter(f for p in pts if cell_of(p["lat"], p["lon"]) in cells for f in p["factors"])
            osm_counts[city] = cnt
            park_cells = mark_polygons(grid, cells, parks, "park")
            venues = sum(cnt[f] for f in ("bar", "nightclub", "alcohol_shop", "commercial_poi"))
            cov.update(osm=True, osm_base=base, osm_points_in_city=inside, osm_counts=dict(cnt),
                       osm_park_polygons=len(parks), park_cells=park_cells,
                       opening_hours_share=round(cnt["hours_known"] / venues, 3) if venues else None)
        except Exception as e:  # noqa: BLE001
            cov.update(osm=False, osm_error=str(e)[:200])
        if city in LIGHT_CITIES:
            n = add_points(grid, cells, ((la, lo, ["lights"]) for la, lo in lights_by_city[city]))
            cov.update(lights=bool(n), lights_points=n)
        else:
            cov.update(lights=False)
        cov["ped_acc_points"] = add_points(grid, cells, accidents)
        if city == 5000:
            muni = Counter()
            pts = []
            for f in tlv["tlv_business_licenses"]:
                p = _esri_point(f)
                if p and is_licensed_bar((f.get("attributes") or {}).get("t_hesber_mahut_esek")):
                    pts.append((p[0], p[1], ["bar_licensed_tlv"]))
            for name, factor in (("tlv_atm", "atm_tlv"), ("tlv_bus_stops", "bus_stop_tlv")):
                for f in tlv[name]:
                    p = _esri_point(f)
                    if p:
                        pts.append((p[0], p[1], [factor]))
            for la, lo, fs in pts:
                muni[fs[0]] += 1
            add_points(grid, cells, pts)
            parks_tlv = [f["geometry"]["rings"] for f in tlv["tlv_parks"] if (f.get("geometry") or {}).get("rings")]
            muni["park_tlv_cells"] = mark_polygons(grid, cells, parks_tlv, "park_tlv")
            for f in tlv["tlv_night_zones"]:
                rings = (f.get("geometry") or {}).get("rings")
                h = tlv_close_hour((f.get("attributes") or {}).get("k_sug"))
                if rings and h is not None:
                    # שעת סגירה + 1 כדי ש"חצות" (0) לא ייבלע כ"אין"
                    muni["night_zone_cells"] += mark_polygons(grid, cells, [rings], "night_zone_close", h + 1)
            muni["tlv_police_stations"] = len(tlv["tlv_police"])
            muni["business_licenses_total"] = len(tlv["tlv_business_licenses"])
            cov.update(tlv_municipal=True, tlv_counts=dict(muni))
        else:
            cov.update(tlv_municipal=False)
        coverage[city] = cov
        log(f"   cells={len(cells)} {json.dumps({k: v for k, v in cov.items() if k != 'bbox'}, ensure_ascii=False)[:400]}")

    rows = stat_area_table(polys, info, police_counts, names, cells_by_city, grid, years)
    # כיסוי ה-join: כמה מתיקי הסל עם קוד אזור נופלים באזור 2011 שיש לו פוליגון / אוכלוסייה תקינה
    join = {}
    for city in CITIES:
        tot = sum(v.get("personal", 0) for u, v in police_counts.items()
                  if u.startswith(f"{city}:") and not u.endswith("city_only"))
        city_only = police_counts.get(f"{city}:city_only", {}).get("personal", 0)
        in_poly = sum(r["cases_personal"] for r in rows if r["yeshuv"] == city)
        in_ok = sum(r["cases_personal"] for r in rows if r["yeshuv"] == city and r["status"] == "ok")
        join[city] = {"cases_with_area": tot, "cases_city_only": city_only,
                      "pct_with_area_matched_polygon": round(100 * in_poly / tot, 1) if tot else None,
                      "pct_with_area_in_ok_unit": round(100 * in_ok / tot, 1) if tot else None,
                      "units_ok": sum(1 for r in rows if r["yeshuv"] == city and r["status"] == "ok"),
                      "units_total": sum(1 for r in rows if r["yeshuv"] == city)}
    sources = [
        {"id": "cbs_sa2011", "name": "הלמ\"ס - אזורים סטטיסטיים 2011 (ArcGIS ISRAEL_CBS_GIS)", "url": CBS_SA2011,
         "license": "רישיון השימוש של הלמ\"ס; \"המפה אינה מהווה אסמכתא למיקום הגבולות\""},
        {"id": "cbs_soec2021", "name": "הלמ\"ס - מדד חברתי-כלכלי 2021 לפי א\"ס 2011 (רק אוכלוסייה + תפקוד; "
                                       "ערך המדד לבקרת רגישות באימות בלבד)", "url": CBS_SOEC2021,
         "license": "רישיון השימוש של הלמ\"ס"},
        {"id": "police_types", "name": "משטרת ישראל - נתוני פשיעה, סל בטיחות אישית", "years": list(years),
         "types": POLICE_TYPES, "license": "data.gov.il (Other/Open)"},
        {"id": "osm", "name": "OpenStreetMap (Overpass)", "license": "ODbL - ייחוס: © OpenStreetMap contributors",
         "osm_base": {c: coverage[c].get("osm_base") for c in coverage}},
        {"id": "tlv_gis", "name": "עיריית ת\"א-יפו GIS (שכבות 964, 575, 690, 956, 551, 595)", "url": TLV_GIS,
         "license": "לא אומת לשכבות gisn (כמו שכבות התאורה/מקלטים)"},
        {"id": "street_lights", "name": "עמודי תאורה ת\"א/ב\"ש (israel_layer)", "license": "ראו israel_layer_report §7"},
        {"id": "cbs_ped_accidents", "name": "הלמ\"ס PUF - תאונות הולכי רגל 2020-2024", "license": "רישיון הלמ\"ס"},
    ]
    # ---- פלטים
    fields = [k for k in rows[0].keys() if not k.startswith("_")] if rows else []
    _write_csv(os.path.join(OUT_DIR, "stat_areas_2011_factors.csv"), rows, fields)
    _write_csv(os.path.join(OUT_DIR, "_validation_input.csv"), rows, list(rows[0].keys()) if rows else [])
    geo = {"type": "FeatureCollection", "features": []}
    for r in rows:
        props = {k: r[k] for k in ("unit", "city", "area_name", "pop", "status", "cases_personal",
                                   "rate_personal_per_1000", "ratio_city_median", "eb_ratio_city_median",
                                   "category_default", "flags") if k in r}
        geo["features"].append({"type": "Feature", "properties": props,
                                "geometry": {"type": "Polygon", "coordinates": polys[r["unit"]]}})
    with open(os.path.join(OUT_DIR, "stat_areas_2011.geojson"), "w", encoding="utf-8") as f:
        json.dump(geo, f, ensure_ascii=False)
    grid_out = write_grid(grid, cells_by_city, rows, coverage, sources, years)
    summary = {"built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1),
               "years": list(years), "stat_areas": sa_meta, "police_records": len(precs),
               "police_join": join, "coverage": coverage, "grid": grid_out, "sources": sources}
    with open(os.path.join(OUT_DIR, "build_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
    log(json.dumps(join, ensure_ascii=False))
    return summary


def write_grid(grid, cells_by_city, rows, coverage, sources, years, path: str = GRID_OUT) -> dict:
    """רשת קומפקטית: לכל תא [עיר, אזור 2011, ...ספירות לפי FACTORS]. כוללת סיכום אזורי (פשיעה)."""
    cells = {}
    for city, cs in cells_by_city.items():
        for k, unit in cs.items():
            c = grid.get(k) or {}
            cells[f"{k[0]}:{k[1]}"] = [city, _unit_parts(unit)[1][0]] + [int(c.get(f, 0)) for f in FACTORS]
    areas = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        # מפתח = "עיר:קוד ראש" (כמו בתאים); unit מפרט אזורים מאוחדים
        areas[f"{r['yeshuv']}:{r['stat11']}"] = {"unit": r["unit"], "pop": r["pop"], "cases_2y": r["cases_personal"],
                            "rate": round(r["rate_personal_per_1000"], 3),
                            "ratio": round(r["ratio_city_median"], 3) if r["ratio_city_median"] else None,
                            "eb_ratio": round(r["eb_ratio_city_median"], 3) if r.get("eb_ratio_city_median") else None,
                            "flags": r["flags"]}
    out = {"version": 1, "built_at": time.time(), "cell_deg": CELL_DEG, "fields": ["city", "stat11"] + FACTORS,
           "years": list(years), "cells": cells, "stat_areas": areas,
           "coverage": {str(c): {k: v for k, v in cov.items() if k not in ("bbox",)} for c, cov in coverage.items()},
           "sources": sources,
           "notes": "ספירות לתא ~100 מ'. coverage מגדיר איפה יש נתונים (lights רק ת\"א/ב\"ש; *_tlv רק ת\"א). "
                    "stat_areas: תיקי משטרה בסל בטיחות אישית לאזור סטטיסטי 2011 - ברמת אזור, לא רחוב."}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": path, "bytes": os.path.getsize(path), "cells": len(cells), "areas_ok": len(areas)}


def _write_csv(path, rows, fields):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})


# ================================================================= סטטיסטיקה (בלי statsmodels)

def poisson_irls(y, X, offset=None, iters=100, tol=1e-10):
    import numpy as np
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    off = np.zeros(len(y)) if offset is None else np.asarray(offset, float)
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(max(y.mean(), 1e-6)) - off.mean()
    for _ in range(iters):
        eta = X @ beta + off
        mu = np.exp(np.clip(eta, -30, 30))
        z = eta - off + (y - mu) / mu
        W = mu
        XtW = X.T * W
        new = np.linalg.solve(XtW @ X + 1e-9 * np.eye(X.shape[1]), XtW @ z)
        if np.max(np.abs(new - beta)) < tol:
            beta = new
            break
        beta = new
    return beta


def nb2_fit(y, X, offset=None, clusters=None):
    """בינומי שלילי NB2 (Var = mu + alpha*mu^2) ב-MLE. מחזיר dict עם beta, se, alpha, ll, aic, n.
    clusters: מזהה אשכול לכל תצפית -> גם se_cluster (sandwich, אשכולות מרחביים), כי השאריות
    מתואמות מרחבית ו-se רגיל מקטין את אי-הוודאות."""
    import numpy as np
    from scipy import optimize, special
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    off = np.zeros(len(y)) if offset is None else np.asarray(offset, float)
    p = X.shape[1]

    def unpack(th):
        return th[:p], th[p]

    def negll(th):
        b, la = unpack(th)
        a = math.exp(la)
        r = 1.0 / a
        mu = np.exp(np.clip(X @ b + off, -30, 30))
        ll = (special.gammaln(y + r) - special.gammaln(r) - special.gammaln(y + 1)
              + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu)))
        return -ll.sum()

    def grad(th):
        b, la = unpack(th)
        a = math.exp(la)
        r = 1.0 / a
        mu = np.exp(np.clip(X @ b + off, -30, 30))
        gb = X.T @ ((y - mu) / (1 + a * mu))
        dr = (special.digamma(y + r) - special.digamma(r) + np.log(r / (r + mu)) + 1 - (r + y) / (r + mu)).sum()
        gla = dr * (-r)
        return -np.concatenate([gb, [gla]])

    b0 = poisson_irls(y, X, off)
    th0 = np.concatenate([b0, [math.log(0.5)]])
    res = optimize.minimize(negll, th0, jac=grad, method="BFGS", options={"gtol": 1e-7, "maxiter": 5000})
    th = res.x
    # Hessian נומרי מהגרדיאנט האנליטי
    k = len(th)
    H = np.zeros((k, k))
    for i in range(k):
        h = 1e-5 * max(1.0, abs(th[i]))
        e = np.zeros(k)
        e[i] = h
        H[:, i] = (grad(th + e) - grad(th - e)) / (2 * h)
    H = (H + H.T) / 2
    try:
        cov = np.linalg.inv(H)
        se = np.sqrt(np.clip(np.diag(cov), 0, None))
    except np.linalg.LinAlgError:
        se = np.full(k, float("nan"))
    ll = -res.fun
    se_cl = None
    if clusters is not None:
        b, la = unpack(th)
        a = math.exp(la)
        r = 1.0 / a
        mu = np.exp(np.clip(X @ b + off, -30, 30))
        sb = X * ((y - mu) / (1 + a * mu))[:, None]
        sl = -r * (special.digamma(y + r) - special.digamma(r) + np.log(r / (r + mu)) + 1 - (r + y) / (r + mu))
        S = np.column_stack([sb, sl])
        groups = defaultdict(list)
        for i, g in enumerate(clusters):
            groups[g].append(i)
        G = len(groups)
        meat = np.zeros((k, k))
        for idx in groups.values():
            sg = S[idx].sum(axis=0)
            meat += np.outer(sg, sg)
        try:
            Hi = np.linalg.inv(H)
            Vc = Hi @ meat @ Hi * (G / max(G - 1, 1))
            se_cl = np.sqrt(np.clip(np.diag(Vc), 0, None))[:p]
        except np.linalg.LinAlgError:
            se_cl = None
    # BFGS מדווח לעיתים "precision loss" קרוב לאופטימום; ההתכנסות נבדקת לפי הגרדיאנט עצמו
    gmax = float(np.max(np.abs(grad(th))))
    return {"beta": th[:p], "se": se[:p], "alpha": math.exp(th[p]), "ll": ll, "aic": 2 * (k) - 2 * ll,
            "n": len(y), "converged": bool(res.success) or gmax < 1e-3, "grad_max": gmax, "se_cluster": se_cl,
            "n_clusters": len(set(clusters)) if clusters is not None else None}


def lr_test(ll_small, ll_big, df=1):
    from scipy import stats
    stat = max(0.0, 2 * (ll_big - ll_small))
    return stat, float(stats.chi2.sf(stat, df))


def spearman(x, y):
    from scipy import stats
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 5:
        return None, None, len(pairs)
    xs, ys = zip(*pairs)
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None, len(pairs)
    r = stats.spearmanr(xs, ys)
    return float(r.statistic if hasattr(r, "statistic") else r[0]), float(r.pvalue), len(pairs)


# ================================================================= אימות

# גורמים שנבדקים (ספירה באזור -> log1p, מתוקנן). park/night zone: שיעור תאים.
TEST_FACTORS = {
    "bar_nightclub": lambda r: r["bar"] + r["nightclub"],
    "alcohol_shop": lambda r: r["alcohol_shop"],
    "late_venue": lambda r: r["late_venue"],
    "atm": lambda r: r["atm"],
    "bus_stop": lambda r: r["bus_stop"],
    "rail_station": lambda r: r["rail_station"],
    "park_share": lambda r: r["park_cells"] / max(r["cells"], 1),
    "abandoned": lambda r: r["abandoned"],
    "police_station": lambda r: r["police"],
    "commercial_poi": lambda r: r["commercial_poi"],
    # ת"א בלבד (שכבות עירוניות)
    "bar_licensed_tlv": lambda r: r["bar_licensed_tlv"],
    "night_zone_share": lambda r: r["night_zone_cells"] / max(r["cells"], 1),
    "atm_tlv": lambda r: r["atm_tlv"],
    "bus_stop_tlv": lambda r: r["bus_stop_tlv"],
    "park_tlv_share": lambda r: r["park_tlv_cells"] / max(r["cells"], 1),
}
SHARE_FACTORS = {"park_share", "night_zone_share", "park_tlv_share"}
TLV_ONLY = {"bar_licensed_tlv", "night_zone_share", "atm_tlv", "bus_stop_tlv", "park_tlv_share"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_validation_rows(path: Optional[str] = None) -> List[dict]:
    path = path or os.path.join(OUT_DIR, "_validation_input.csv")
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        d = dict(r)
        for k, v in r.items():
            if k in ("city", "unit", "area_name", "status", "flags", "category_default", "in_police_data"):
                continue
            n = _num(v)
            d[k] = n if n is not None else (None if v == "" else v)
        d["yeshuv"] = int(float(r["yeshuv"]))
        out.append(d)
    return out


def _z(vals):
    m = statistics.mean(vals)
    s = statistics.pstdev(vals) or 1.0
    return [(v - m) / s for v in vals], m, s


def design(rows, factor_names: Sequence[str], cities: Sequence[int], with_lights=False, with_ses=False,
           with_activity=False):
    """X: const, city FE, log(pop), log(area), log1p(ped_acc) [+ lights/km2] [+ activity] [+ SES] + factors.
    כל המשתנים הרציפים מתוקננים (z), כך ש-IRR = לכל סטיית תקן של log(1+ספירה)."""
    cols, names = [], []
    cols.append([1.0] * len(rows)); names.append("const")
    for c in cities[1:]:
        cols.append([1.0 if r["yeshuv"] == c else 0.0 for r in rows]); names.append(f"city_{c}")
    base = [("log_pop", lambda r: math.log(r["pop"])), ("log_area", lambda r: math.log(max(r["area_km2"], 0.01))),
            ("log1p_ped_acc", lambda r: math.log1p(r["ped_acc"]))]
    if with_lights:
        base.append(("lights_per_km2", lambda r: math.log1p(r["lights"] / max(r["area_km2"], 0.01))))
    if with_activity:
        base.append(("log1p_commercial_poi", lambda r: math.log1p(r["commercial_poi"])))
    if with_ses:
        base.append(("ses_index", lambda r: r["_ses_index"]))
    for name, fn in base:
        z, _, _ = _z([fn(r) for r in rows]); cols.append(z); names.append(name)
    for f in factor_names:
        fn = TEST_FACTORS[f]
        raw = [fn(r) for r in rows]
        vals = raw if f in SHARE_FACTORS else [math.log1p(v) for v in raw]
        z, _, _ = _z(vals); cols.append(z); names.append(f)
    import numpy as np
    return np.array(cols).T, names


def _fit_summary(fit, names, which):
    i = names.index(which)
    b, se = float(fit["beta"][i]), float(fit["se"][i])
    out = {"irr_per_sd": round(math.exp(b), 3), "ci95": [round(math.exp(b - 1.96 * se), 3),
                                                         round(math.exp(b + 1.96 * se), 3)]}
    if fit.get("se_cluster") is not None:
        from scipy import stats
        sc = float(fit["se_cluster"][i])
        out["ci95_cluster"] = [round(math.exp(b - 1.96 * sc), 3), round(math.exp(b + 1.96 * sc), 3)]
        out["p_cluster"] = round(float(2 * stats.norm.sf(abs(b / sc))), 4) if sc > 0 else None
    return out


CLUSTER_DEG = 0.015  # אשכול מרחבי ~1.5 ק"מ לפי מרכז האזור


def spatial_clusters(rows) -> List[tuple]:
    return [(r["yeshuv"], int(math.floor((r.get("lat") or 0) / CLUSTER_DEG)),
             int(math.floor((r.get("lon") or 0) / CLUSTER_DEG))) for r in rows]


def validate(rows: Optional[List[dict]] = None, outcome: str = "personal", log=print) -> dict:
    rows = rows if rows is not None else load_validation_rows()
    data = [r for r in rows if r.get("status") == "ok" and r.get("pop") and r.get("area_km2")]
    cities = [c for c in CITIES if any(r["yeshuv"] == c for r in data)]
    ycol = f"cases_{outcome}"
    out = {"outcome": outcome, "types": [POLICE_TYPES[t] for t in OUTCOMES[outcome]], "n_units": len(data),
           "units_per_city": {CITIES[c]["name"]: sum(1 for r in data if r["yeshuv"] == c) for c in cities},
           "spearman": {}, "nb_pooled": {}, "nb_pooled_vs_activity": {}, "nb_ses_sensitivity": {},
           "nb_lights_subset": {}, "per_city_sign": {}, "base_model": {}}
    # 1) Spearman: שיעור לאלף מול צפיפות הגורם (לקמ"ר), לכל עיר
    for c in cities:
        sub = [r for r in data if r["yeshuv"] == c]
        rate = [r[ycol] / len(IL.DEFAULT_YEARS) / r["pop"] * 1000 for r in sub]
        res = {}
        for f, fn in TEST_FACTORS.items():
            if f in TLV_ONLY and c != 5000:
                continue
            dens = [fn(r) if f in SHARE_FACTORS else fn(r) / r["area_km2"] for r in sub]
            rho, p, n = spearman(dens, rate)
            res[f] = {"rho": None if rho is None else round(rho, 3), "p": None if p is None else round(p, 4), "n": n}
        for extra, fn in (("ped_acc", lambda r: r["ped_acc"] / r["area_km2"]),
                          ("lights", lambda r: r["lights"] / r["area_km2"])):
            if extra == "lights" and c not in LIGHT_CITIES:
                continue
            rho, p, n = spearman([fn(r) for r in sub], rate)
            res[extra] = {"rho": None if rho is None else round(rho, 3), "p": None if p is None else round(p, 4), "n": n}
        out["spearman"][CITIES[c]["name"]] = res
    y = [r[ycol] for r in data]
    cl = spatial_clusters(data)
    out["n_spatial_clusters"] = len(set(cl))
    # 2) מודל בסיס ו-NB לכל גורם בנפרד (מול בסיס, ומול בסיס + פעילות מסחרית)
    Xb, nb_names = design(data, [], cities)
    base = nb2_fit(y, Xb)
    Xa, na = design(data, [], cities, with_activity=True)
    base_act = nb2_fit(y, Xa)
    Xs, ns = design(data, [], cities, with_activity=True, with_ses=True)
    base_ses = nb2_fit(y, Xs) if all(r.get("_ses_index") is not None for r in data) else None
    out["base_model"] = {"aic": round(base["aic"], 1), "alpha": round(base["alpha"], 3),
                         "coef": {n: round(math.exp(float(b)), 3) for n, b in zip(nb_names, base["beta"])
                                  if n.startswith("log")},
                         "activity_aic": round(base_act["aic"], 1)}
    for f in TEST_FACTORS:
        if f == "commercial_poi" or f in TLV_ONLY:
            continue
        X, names = design(data, [f], cities)
        fit = nb2_fit(y, X, clusters=cl)
        stat, p = lr_test(base["ll"], fit["ll"])
        out["nb_pooled"][f] = dict(_fit_summary(fit, names, f), lr_p=round(p, 4), d_aic=round(fit["aic"] - base["aic"], 1))
        X, names = design(data, [f], cities, with_activity=True)
        fit2 = nb2_fit(y, X, clusters=cl)
        stat, p = lr_test(base_act["ll"], fit2["ll"])
        out["nb_pooled_vs_activity"][f] = dict(_fit_summary(fit2, names, f), lr_p=round(p, 4),
                                               d_aic=round(fit2["aic"] - base_act["aic"], 1))
        if base_ses is not None:
            X, names = design(data, [f], cities, with_activity=True, with_ses=True)
            fit3 = nb2_fit(y, X, clusters=cl)
            stat, p = lr_test(base_ses["ll"], fit3["ll"])
            out["nb_ses_sensitivity"][f] = dict(_fit_summary(fit3, names, f), lr_p=round(p, 4))
        # עקביות בין ערים: אותו מודל בכל עיר בנפרד
        signs = {}
        for c in cities:
            sub = [r for r in data if r["yeshuv"] == c]
            fn = TEST_FACTORS[f]
            if len(set(fn(r) for r in sub)) < 3:
                signs[CITIES[c]["name"]] = None
                continue
            X, names = design(sub, [f], [c])
            fc = nb2_fit([r[ycol] for r in sub], X, clusters=spatial_clusters(sub))
            s = _fit_summary(fc, names, f)
            signs[CITIES[c]["name"]] = s["irr_per_sd"]
        out["per_city_sign"][f] = signs
    # 3) תת-מדגם עם תאורה (ת"א + ב"ש): האם לתאורה יש אות, והאם הגורמים שורדים אותה
    lsub = [r for r in data if r["yeshuv"] in LIGHT_CITIES]
    lc = [c for c in cities if c in LIGHT_CITIES]
    if len(lsub) > 30:
        yl = [r[ycol] for r in lsub]
        X0, n0 = design(lsub, [], lc)
        f0 = nb2_fit(yl, X0)
        X1, n1 = design(lsub, [], lc, with_lights=True)
        f1 = nb2_fit(yl, X1, clusters=spatial_clusters(lsub))
        stat, p = lr_test(f0["ll"], f1["ll"])
        out["nb_lights_subset"]["lights_per_km2"] = dict(_fit_summary(f1, n1, "lights_per_km2"), lr_p=round(p, 4),
                                                        n=len(lsub))
        for f in ("bar_nightclub", "bus_stop", "atm", "alcohol_shop", "late_venue"):
            X2, n2 = design(lsub, [f], lc, with_lights=True)
            f2 = nb2_fit(yl, X2, clusters=spatial_clusters(lsub))
            stat, p = lr_test(f1["ll"], f2["ll"])
            out["nb_lights_subset"][f] = dict(_fit_summary(f2, n2, f), lr_p=round(p, 4))
    # 4) מודל משולב עם כל הגורמים המועמדים (מול בסיס + פעילות)
    cand = ["bar_nightclub", "alcohol_shop", "atm", "bus_stop", "rail_station", "park_share"]
    X, names = design(data, cand, cities, with_activity=True)
    full = nb2_fit(y, X, clusters=cl)
    stat, p = lr_test(base_act["ll"], full["ll"], df=len(cand))
    out["nb_joint"] = {"factors": {f: _fit_summary(full, names, f) for f in cand + ["log1p_commercial_poi"]},
                       "lr_p_all_factors": round(p, 6), "d_aic": round(full["aic"] - base_act["aic"], 1)}
    # 5) אוטוקורלציה מרחבית בשאריות המודל המשולב (Moran's I, 5 שכנים קרובים באותה עיר)
    if all(r.get("lat") is not None for r in data):
        import numpy as np
        mu = np.exp(X @ full["beta"])
        resid = (np.asarray(y, float) - mu) / np.sqrt(mu + full["alpha"] * mu ** 2)
        out["residual_morans_i"] = morans_i_knn(resid, [(r["lat"], r["lon"], r["yeshuv"]) for r in data])
    # 6) ת"א בלבד: מקורות עירוניים רשמיים (רישיונות עסק "בר/פאב", אזורי בילוי, כספומטים, תחנות משרד התחבורה)
    tsub = [r for r in data if r["yeshuv"] == 5000 and r.get("bar_licensed_tlv") is not None]
    if len(tsub) > 30:
        yt = [r[ycol] for r in tsub]
        Xt0, nt0 = design(tsub, [], [5000], with_activity=True)
        ft0 = nb2_fit(yt, Xt0)
        res_t = {"n": len(tsub)}
        for f in ("bar_licensed_tlv", "night_zone_share", "atm_tlv", "bus_stop_tlv", "park_tlv_share",
                  "bar_nightclub", "atm", "bus_stop"):
            Xt, nt = design(tsub, [f], [5000], with_activity=True)
            ft = nb2_fit(yt, Xt, clusters=spatial_clusters(tsub))
            stat, p = lr_test(ft0["ll"], ft["ll"])
            res_t[f] = dict(_fit_summary(ft, nt, f), lr_p=round(p, 4))
        out["tlv_municipal"] = res_t
    return out


def morans_i_knn(values, coords, k: int = 5, perms: int = 499, seed: int = 1) -> dict:
    """Moran's I עם משקלות kNN (שורות מנורמלות) בתוך כל עיר; p לפי פרמוטציות בתוך העיר."""
    import numpy as np
    v = np.asarray(values, float)
    n = len(v)
    nbrs = []
    for i, (la, lo, c) in enumerate(coords):
        d = sorted((haversine_m(la, lo, la2, lo2), j) for j, (la2, lo2, c2) in enumerate(coords) if j != i and c2 == c)
        nbrs.append([j for _, j in d[:k]])

    def stat(x):
        z = x - x.mean()
        num = sum(z[i] * z[nb].mean() for i, nb in enumerate(nbrs) if nb)
        return float(num / (z ** 2).sum())

    obs = stat(v)
    rng = np.random.default_rng(seed)
    groups = defaultdict(list)
    for i, (_, _, c) in enumerate(coords):
        groups[c].append(i)
    ge = 0
    for _ in range(perms):
        x = v.copy()
        for idx in groups.values():
            x[idx] = rng.permutation(v[idx])
        ge += stat(x) >= obs
    return {"I": round(obs, 3), "p_perm": round((ge + 1) / (perms + 1), 4), "k": k, "n": n}


def run_validation(log=print) -> dict:
    rows = load_validation_rows()
    res = {o: validate(rows, o, log) for o in OUTCOMES}
    path = os.path.join(OUT_DIR, "validation_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    log(json.dumps(res["personal"], ensure_ascii=False, indent=1)[:6000])
    return res


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--offline", action="store_true")
    b.add_argument("--years", nargs="+", type=int, default=list(IL.DEFAULT_YEARS))
    sub.add_parser("validate")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        build(a.offline, a.years)
    else:
        run_validation()
    return 0


if __name__ == "__main__":
    sys.exit(main())

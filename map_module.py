# -*- coding: utf-8 -*-
"""
map_module.py
מוצא את המקום הבטוח הקרוב ביותר (לפי סדר עדיפות ב-config.SAFE_PLACE_CATEGORIES) ומחשב
אליו מסלול הליכה אמיתי.

שני backend-ים אפשריים, נבחר אוטומטית:
1. Google Places API + Directions API - נבחר קודם אם config.GOOGLE_MAPS_API_KEY מוגדר.
   אמין ומעודכן משמעותית יותר מהאלטרנטיבה החינמית, אך דורש מפתח API אישי (יש רמה חינמית
   נדיבה - כ-$200 קרדיט חודשי, ראה הוראות ב-README).
2. OpenStreetMap (Overpass API לאיתור מקומות + OSRM לניתוב) - חינמי לגמרי, ללא מפתח,
   אך שרתים ציבוריים משותפים לכל העולם, לעיתים עמוסים/חסומים/לא עדכניים באזורים מסוימים.
   נבחר אוטומטית אם אין מפתח Google, או כגיבוי אם קריאת Google נכשלת.
"""

import math

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def _headers():
    """
    Overpass API מבקש במפורש (במדיניות השימוש שלהם) User-Agent תיאורי שמזהה את האפליקציה,
    במקום ברירת המחדל הגנרית של requests ("python-requests/x.x") שלעיתים מוגבלת/מסוננת
    כתעבורת בוט חשודה. זו לא "התחזות" - זה בדיוק מה ש-Overpass מבקש שיעשו.
    """
    import config
    return {"User-Agent": config.HTTP_USER_AGENT}


# ==================== כללי ====================

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ==================== Google Places / Directions ====================

GOOGLE_PLACES_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
GOOGLE_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"


def _find_safe_place_google(lat, lon, categories, radius_m, api_key, log_callback=None):
    """מחזיר dict מקום או None. status=='REQUEST_DENIED' עוצר לגמרי (מפתח שגוי/לא מופעל)."""
    for cat in categories:
        google_type = cat.get("google_type")
        if not google_type:
            continue  # לקטגוריה הזו אין מקבילה ישירה ב-Google Places (למשל "מתנ"ס")
        params = {"location": f"{lat},{lon}", "radius": radius_m, "type": google_type, "key": api_key}
        try:
            resp = requests.get(GOOGLE_PLACES_URL, params=params, headers=_headers(), timeout=8)
            data = resp.json()
        except Exception as e:
            if log_callback:
                log_callback(f"⚠️ שגיאת רשת ב-Google Places ({cat['label']}): {e}")
            continue

        status = data.get("status")
        if status == "OK" and data.get("results"):
            best, best_dist = None, None
            for place in data["results"]:
                loc = place["geometry"]["location"]
                d = _haversine_m(lat, lon, loc["lat"], loc["lng"])
                if best_dist is None or d < best_dist:
                    best_dist, best = d, place
            name = best.get("name", cat["label"])
            if log_callback:
                log_callback(f"📍 (Google Places) נמצא: {name} ({cat['label']}) - כ-{best_dist:.0f} מ'")
            loc = best["geometry"]["location"]
            return {
                "name": name, "lat": loc["lat"], "lon": loc["lng"],
                "type": cat["id"], "type_label": cat["label"], "distance_m": best_dist,
            }
        elif status == "REQUEST_DENIED":
            if log_callback:
                log_callback(f"❌ Google Places דחה את הבקשה ({data.get('error_message', '')}) - "
                              f"בדוק שמפתח ה-API תקין ושה-Places API מופעל בפרויקט ב-Google Cloud")
            return "DENIED"  # סימון מיוחד - אין טעם לנסות שוב עם אותו מפתח שגוי
        elif status == "OVER_QUERY_LIMIT":
            if log_callback:
                log_callback("⚠️ חריגה ממכסת Google Places (חינמית/יומית) - נופל ל-OpenStreetMap")
            return "DENIED"
        # ZERO_RESULTS או סטטוס אחר - פשוט ממשיכים לקטגוריה הבאה
        continue
    return None


def _get_walking_route_google(orig_lat, orig_lon, dest_lat, dest_lon, api_key, log_callback=None):
    params = {
        "origin": f"{orig_lat},{orig_lon}", "destination": f"{dest_lat},{dest_lon}",
        "mode": "walking", "key": api_key,
    }
    try:
        resp = requests.get(GOOGLE_DIRECTIONS_URL, params=params, headers=_headers(), timeout=8)
        data = resp.json()
        if data.get("status") != "OK" or not data.get("routes"):
            if log_callback and data.get("status") not in (None, "OK"):
                log_callback(f"⚠️ Google Directions לא החזיר מסלול (status={data.get('status')})")
            return None
        leg = data["routes"][0]["legs"][0]
        points = [(step["start_location"]["lat"], step["start_location"]["lng"]) for step in leg["steps"]]
        points.append((leg["end_location"]["lat"], leg["end_location"]["lng"]))
        return {"points": points, "distance_m": leg["distance"]["value"], "duration_s": leg["duration"]["value"]}
    except Exception as e:
        if log_callback:
            log_callback(f"⚠️ שגיאת רשת ב-Google Directions: {e}")
        return None


# ==================== OpenStreetMap: Overpass / Nominatim + OSRM ====================
#
# באג (נמצא 2026-11-08): השאילתה ביקשה 8 קטגוריות ברדיוס 5 ק"מ (כולל בתי כנסת/סופרים/בתי מרקחת) - אלפי
# תוצאות בעיר -> כל שרתי Overpass נתקעו (timeout), אחד אחרי השני, 12 שנ' כל אחד, ובסוף "לא נמצא".
# בנוסף Overpass הציבורי לפעמים פשוט לא זמין. עכשיו:
# 1. שאילתה קלה: משטרה/בי"ח/כיבוי ברדיוס המלא; מקומות ציבוריים (דלק/מרקחת/מתנ"ס) רק עד 1.2 ק"מ.
# 2. כל שרתי Overpass במקביל + Nominatim במקביל (חיפוש מובנה amenity=..., ≤1 בקשה לשנייה לפי המדיניות).
# 3. מטמון מקומי של מקומות שנמצאו - SOS חוזר באותו אזור עובד גם בלי אינטרנט.
# 4. בחירה: משטרה/בי"ח/כיבוי עד 2 ק"מ קודם; אחרת המקום הציבורי הקרוב (לא משטרה במרחק 5 ק"מ
#    כשתחנת דלק פתוחה במרחק 100 מ'); אחרת הרשמי הקרוב ברדיוס.

import json
import os
import threading
import time

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
# באג קודם: router.project-osrm.org (שרת הדמו) מריץ *רק* פרופיל רכב ומתעלם מ-"foot" -
# זמני ה"הליכה" שהוצגו היו בפועל זמני נסיעה (פי ~5 קצרים מהמציאות). שרת FOSSGIS של
# OpenStreetMap Germany מריץ פרופיל הליכה אמיתי, ב-HTTPS.
OSRM_WALK_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/foot"

OFFICIAL_IDS = ("police", "hospital", "fire_station")
OFFICIAL_PREFERRED_M = 2000          # רשמי עד 2 ק"מ עדיף על כל מקום ציבורי
PUBLIC_RADIUS_M = 1200               # מקום ציבורי פתוח רלוונטי רק אם הוא באמת קרוב
SEARCH_DEADLINE_S = 10               # SOS: לא מחכים יותר מזה לרשת
OVERPASS_TIMEOUT_S = 9
NOMINATIM_GAP_S = 1.05               # מדיניות Nominatim: לכל היותר בקשה אחת לשנייה
CACHE_MAX_PLACES = 3000


def _cache_path():
    import config
    return os.path.join(config.DATA_DIR, "cache", "safe_places_cache.json")


_cache_lock = threading.Lock()


def _cache_load():
    """מקומות שנמצאו בעבר - מסומנים "cache" (לא מציגים אותם כתוצאה חיה מהרשת)."""
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            return [dict(p, source="cache") for p in json.load(fh)]
    except (OSError, ValueError, TypeError):
        return []


def _cache_add(places):
    """שומר מקומות שנמצאו (שם, סוג, מיקום, מקור) - לשימוש בלי אינטרנט. בלי שום מידע על המשתמש/ת."""
    if not places:
        return
    with _cache_lock:
        try:
            with open(_cache_path(), encoding="utf-8") as fh:
                cur = json.load(fh)
        except (OSError, ValueError):
            cur = []
        seen = {(p["type"], round(p["lat"], 5), round(p["lon"], 5)) for p in cur}
        for p in places:
            k = (p["type"], round(p["lat"], 5), round(p["lon"], 5))
            if k not in seen:
                seen.add(k)
                cur.append({k2: p.get(k2) for k2 in ("name", "lat", "lon", "type", "type_label", "source", "emergency")})
        cur = cur[-CACHE_MAX_PLACES:]
        try:
            os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
            tmp = _cache_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cur, fh, ensure_ascii=False)
            os.replace(tmp, _cache_path())
        except OSError:
            pass


def _element_coords(el):
    """node מחזיק lat/lon ישירות. way/relation דורשים 'out center' לקבל el['center']['lat'/'lon']."""
    if "lat" in el and "lon" in el:
        return el["lat"], el["lon"]
    center = el.get("center")
    if center and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    return None


def _build_overpass_query(categories, lat, lon, radius_m):
    """שאילתה קלה אחת: רשמיים ברדיוס המלא, ציבוריים רק בקרבת מקום. nwr - כי הרבה תחנות משטרה/בתי
    חולים ממופים כ-way (מתאר מבנה) ולא כנקודה."""
    clauses = []
    for cat in categories:
        r = radius_m if cat["id"] in OFFICIAL_IDS else min(radius_m, PUBLIC_RADIUS_M)
        tag_filters = "".join(f'["{k}"="{v}"]' for k, v in cat["tags"].items())
        clauses.append(f'  nwr{tag_filters}(around:{r},{lat},{lon});')
    body = "\n".join(clauses)
    return f"[out:json][timeout:{OVERPASS_TIMEOUT_S}];\n(\n{body}\n);\nout center 80;"


def _match_category(tags, categories):
    for cat in categories:
        if all(tags.get(k) == v for k, v in cat["tags"].items()):
            return cat
    return None


def _query_overpass(query, log_callback=None, deadline_s=SEARCH_DEADLINE_S):
    """כל השרתים במקביל - הראשון שמחזיר תשובה תקינה. dict תגובה או None."""
    result, done = {}, threading.Event()
    left = [len(OVERPASS_URLS)]
    lock = threading.Lock()

    def one(url):
        try:
            resp = requests.post(url, data={"data": query}, headers=_headers(), timeout=OVERPASS_TIMEOUT_S + 2)
            if resp.status_code == 200:
                data = resp.json()
                with lock:
                    result.setdefault("data", data)
                done.set()
                return
            if log_callback:
                log_callback(f"⚠️ שרת Overpass ({url}) החזיר {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            if log_callback:
                log_callback(f"⚠️ שרת Overpass ({url}) לא זמין: {type(e).__name__}")
        finally:
            with lock:
                left[0] -= 1
                if left[0] == 0:
                    done.set()

    for url in OVERPASS_URLS:
        threading.Thread(target=one, args=(url,), daemon=True).start()
    done.wait(deadline_s)
    return result.get("data")


# ---------------- בתי חולים: רק כלליים עם מיון ----------------
# "בית חולים" ב-OSM כולל גם מרפאות, גן ילדים, בריאות הנפש, גריאטריה, גמילה ושיקום - לא מקום בטוח בלילה.
ER_HOSPITAL_NAMES = ("איכילוב", "סוראסקי", "שיבא", "תל השומר", "בילינסון", "ביילינסון", "רבין", "השרון", "אסף הרופא",
                     "שמיר", "מאיר", "וולפסון", "שערי צדק", "הדסה", "רמב\"ם", "רמבם", "בני ציון", "כרמל", "העמק",
                     "פוריה", "זיו", "הגליל", "נהריה", "הלל יפה", "לניאדו", "קפלן", "ברזילי", "אסותא אשדוד",
                     "סורוקה", "יוספטל", "מעייני הישועה", "האנגלי", "סנט ג'וזף", "מקאסד", "נצרת", "הצרפתי",
                     "האיטלקי", "בניין המיון", "מיון")
NOT_ER = ("נפש", "גריאטר", "גמילה", "החלמה", "שיקום", "מרפאה", "מרפאות", "גן ילדים", "מכון", "קופ\"ח", "קופת חולים",
          "מכבי", "כללית", "לאומית", "מאוחדת", "סיעוד", "הוספיס", "פסיכיאטר", "רטורנו", "Eye", "العيون", "Smile",
          "Dental", "שיניים")


def is_er_hospital(name, emergency=None):
    n = name or ""
    if emergency == "no" or any(b in n for b in NOT_ER):
        return False
    return emergency == "yes" or any(k in n for k in ER_HOSPITAL_NAMES)


def _usable(p):
    return p["type"] != "hospital" or is_er_hospital(p.get("name"), p.get("emergency"))


# ---------------- רשימה מובנית (בלי אינטרנט, מהפעלה ראשונה) ----------------
# safe_places/safe_places_il.json - tools/build_safe_places.py: תחנות כיבוי (data.gov.il, רשמי),
# תחנות משטרה ובתי חולים עם מיון (OpenStreetMap, ODbL) לכל הארץ.
_BUNDLED = None


def _bundled_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, getattr(__import__("config"), "BASE_DIR", here)):
        p = os.path.join(base, "safe_places", "safe_places_il.json")
        if os.path.exists(p):
            return p
    return None


def _bundled_places():
    global _BUNDLED
    if _BUNDLED is None:
        import config
        labels = {c["id"]: c["label"] for c in config.SAFE_PLACE_CATEGORIES}
        out = []
        try:
            with open(_bundled_path() or "", encoding="utf-8") as fh:
                for p in json.load(fh).get("places", []):
                    if p.get("type") in labels:
                        out.append({"name": p["name"], "lat": p["lat"], "lon": p["lon"], "type": p["type"],
                                    "type_label": labels[p["type"]], "emergency": p.get("emergency"),
                                    "source": "bundled_official" if p.get("official") else "bundled_osm"})
        except (OSError, ValueError, KeyError):
            out = []
        _BUNDLED = out
    return _BUNDLED


def _overpass_candidates(lat, lon, categories, radius_m, log_callback=None):
    data = _query_overpass(_build_overpass_query(categories, lat, lon, radius_m), log_callback)
    if data is None:
        return None
    out = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        cat = _match_category(tags, categories)
        coords = _element_coords(el)
        if cat is None or coords is None:
            continue
        out.append({"name": tags.get("name:he") or tags.get("name") or cat["label"], "lat": coords[0],
                    "lon": coords[1], "type": cat["id"], "type_label": cat["label"], "source": "osm_overpass",
                    "emergency": tags.get("emergency")})
    return out


def _nominatim_candidates(lat, lon, categories, radius_m, log_callback=None, stop=None, skip_official=False):
    """חיפוש מובנה ב-Nominatim (amenity=...) בתוך תיבה סביב הנקודה. רשמיים קודם; ציבוריים רק אם אין רשמי
    קרוב. סדרתי עם רווח של שנייה (מדיניות השימוש). extratags - כדי לדעת אם לבית החולים יש מיון."""
    out, ok = [], False
    for cat in categories:
        amenity = cat["tags"].get("amenity")
        if not amenity or len(cat["tags"]) > 1 or (stop is not None and stop.is_set()):
            continue                         # בתי כנסת (שני תגים), סופרמרקט (shop) - לא בחיפוש הזה
        public = cat["id"] not in OFFICIAL_IDS
        if skip_official and not public:
            continue                         # הרשמיים כבר ברשימה המובנית (ארצית) - חוסכים בקשות ושניות
        if public and not skip_official and any(_haversine_m(lat, lon, p["lat"], p["lon"]) <= OFFICIAL_PREFERRED_M
                          for p in out if _usable(p)):
            break                            # יש כבר רשמי קרוב - אין צורך בעוד בקשות
        r = min(radius_m, PUBLIC_RADIUS_M) if public else radius_m
        dlat, dlon = r / 111000, r / (111000 * max(0.2, math.cos(math.radians(lat))))
        try:
            resp = requests.get(NOMINATIM_SEARCH_URL, params={
                "amenity": amenity, "format": "jsonv2", "limit": 10, "bounded": 1, "accept-language": "he",
                "extratags": 1, "viewbox": f"{lon - dlon},{lat + dlat},{lon + dlon},{lat - dlat}"},
                headers=_headers(), timeout=6)
            rows = resp.json() if resp.status_code == 200 else None
        except Exception as e:  # noqa: BLE001
            if log_callback:
                log_callback(f"⚠️ Nominatim לא זמין: {type(e).__name__}")
            rows = None
        if rows is None:
            break
        ok = True
        for row in rows:
            try:
                plat, plon = float(row["lat"]), float(row["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append({"name": row.get("name") or cat["label"], "lat": plat, "lon": plon, "type": cat["id"],
                        "type_label": cat["label"], "source": "osm_nominatim",
                        "emergency": (row.get("extratags") or {}).get("emergency")})
        time.sleep(NOMINATIM_GAP_S)
    return out if ok else None


OSRM_TABLE_URL = "https://routing.openstreetmap.de/routed-foot/table/v1/foot"
WALK_FACTOR = 1.3                    # בלי מרחק הליכה אמיתי: הליכה ≈ 1.3 × קו אווירי


def _walk_table(lat, lon, places, timeout=6):
    """מרחק וזמן הליכה אמיתיים (OSRM table, בקשה אחת לכל המועמדים) -> [(מטרים, שניות) או None]."""
    if not places:
        return []
    coords = ";".join([f"{lon:.6f},{lat:.6f}"] + [f"{p['lon']:.6f},{p['lat']:.6f}" for p in places])
    try:
        resp = requests.get(f"{OSRM_TABLE_URL}/{coords}", params={"sources": 0, "annotations": "duration,distance"},
                            headers=_headers(), timeout=timeout)
        data = resp.json()
        if data.get("code") != "Ok":
            return None
        dist, dur = data["distances"][0][1:], data["durations"][0][1:]
        return [(d, t) if d is not None and t is not None else None for d, t in zip(dist, dur)]
    except Exception:  # noqa: BLE001
        return None


def _choose(lat, lon, candidates, categories, radius_m, walk=False, log_callback=None):
    """
    רשמי (משטרה / בי"ח עם מיון / כיבוי) קרוב -> הקרוב שבהם; אחרת מקום ציבורי קרוב; אחרת הרשמי הקרוב ברדיוס.
    walk=True: הדירוג לפי מרחק הליכה אמיתי (OSRM) - לא לפי קו אווירי שחוצה כביש מהיר / ואדי / גדר.
    """
    known = {c["id"] for c in categories}
    seen, scored = set(), []
    for p in candidates:
        key = (p["type"], round(p["lat"], 4), round(p["lon"], 4))
        if p["type"] not in known or key in seen or not _usable(p):
            continue
        seen.add(key)
        d = _haversine_m(lat, lon, p["lat"], p["lon"])
        if d <= radius_m:
            scored.append([d, p])
    worship_ids = {c["id"] for c in categories if c.get("worship")}
    official = sorted((x for x in scored if x[1]["type"] in OFFICIAL_IDS), key=lambda x: x[0])[:6]
    public = sorted((x for x in scored if x[1]["type"] not in OFFICIAL_IDS and x[1]["type"] not in worship_ids
                     and x[0] <= PUBLIC_RADIUS_M), key=lambda x: x[0])[:4]
    # מקום תפילה (לפי העדפת המשתמש/ת, worship_pref.py) - רק כשאין רשמי או ציבורי אחר בקרבת מקום
    worship = sorted((x for x in scored if x[1]["type"] in worship_ids and x[0] <= PUBLIC_RADIUS_M),
                     key=lambda x: x[0])[:3]
    short = official + public + worship
    if not short:
        return None
    walks = _walk_table(lat, lon, [x[1] for x in short]) if walk else None
    for i, x in enumerate(short):
        w = walks[i] if walks and i < len(walks) else None
        x.append(w[0] if w else x[0] * WALK_FACTOR)                  # x[2] = מרחק הליכה (אמיתי או אומדן)
        x.append(w[1] if w else None)                                # x[3] = שניות הליכה (OSRM) או None
    official.sort(key=lambda x: x[2])
    public.sort(key=lambda x: x[2])
    worship.sort(key=lambda x: x[2])
    fallback = False
    if official and official[0][2] <= OFFICIAL_PREFERRED_M * WALK_FACTOR:
        best = official[0]
    elif public and public[0][2] <= PUBLIC_RADIUS_M * WALK_FACTOR:
        best = public[0]
    elif worship and worship[0][2] <= PUBLIC_RADIUS_M * WALK_FACTOR:
        best, fallback = worship[0], True
    elif official:
        best = official[0]
    else:
        best = (public or worship)[0]
        fallback = not public
    d, p, walk_m, walk_s = best
    out = dict(p, distance_m=d, walk_m=round(walk_m), walk_measured=walk_s is not None)
    if fallback:
        out["fallback_note"] = "אין מקום בטוח אחר בקרבת מקום - מקום תפילה לפי ההעדפה שלך"
    if walk_s is not None:
        out["walk_s"] = round(walk_s)
    return out


def _find_safe_place_osm(lat, lon, categories, radius_m, log_callback=None, online=True):
    offline = _bundled_places() + _cache_load()
    if not online:
        # מיידי, בלי רשת: רשימה מובנית + מקומות שנמצאו בעבר. הדירוג לפי קו אווירי × 1.3
        return _choose(lat, lon, offline, categories, radius_m)
    box, stop = {}, threading.Event()
    # כבר יש רשמי קרוב ברשימה המובנית? אז הרשת רק משלימה (מקום ציבורי קרוב יותר / עדכון) - לא מחכים ל-Overpass
    offline_close = any(p["type"] in OFFICIAL_IDS and _usable(p) and
                        _haversine_m(lat, lon, p["lat"], p["lon"]) <= OFFICIAL_PREFERRED_M for p in offline)

    def run(name, fn):
        kw = {"stop": stop, "skip_official": offline_close} if name == "nominatim" else {}
        box[name] = fn(lat, lon, categories, radius_m, log_callback, **kw)

    threads = [threading.Thread(target=run, args=(n, f), daemon=True)
               for n, f in (("overpass", _overpass_candidates), ("nominatim", _nominatim_candidates))]
    for t in threads:
        t.start()
    end = time.time() + SEARCH_DEADLINE_S
    while time.time() < end:
        if not threads[0].is_alive() and box.get("overpass") is not None:
            break                            # Overpass ענה - הכי מלא (כל הקטגוריות בשאילתה אחת)
        if not threads[1].is_alive() and (not threads[0].is_alive() or offline_close or any(
                p["type"] in OFFICIAL_IDS and _usable(p) and
                _haversine_m(lat, lon, p["lat"], p["lon"]) <= OFFICIAL_PREFERRED_M
                for p in box.get("nominatim") or [])):
            break                            # Nominatim סיים ויש רשמי קרוב - לא מחכים ל-Overpass תקוע
        if not any(t.is_alive() for t in threads):
            break
        time.sleep(0.1)
    stop.set()
    online_c = [p for name in ("overpass", "nominatim") for p in (box.get(name) or [])]
    if online_c:
        _cache_add(online_c)
    reached = any(box.get(n) is not None for n in ("overpass", "nominatim"))
    best = _choose(lat, lon, online_c + offline, categories, radius_m, walk=True, log_callback=log_callback)
    if best is not None and best["source"] == "cache" and log_callback:
        log_callback("ℹ️ מקום בטוח שנשמר מחיפוש קודם (ייתכן שאינו מעודכן)")
    if best is None:
        if log_callback:
            log_callback("⚠️ לא נמצא מקום בטוח מוכר בסביבה" if reached else
                         "⚠️ שירותי המפות (Overpass/Nominatim) לא זמינים כרגע ואין מקום שמור לאזור הזה")
        return None
    if log_callback:
        log_callback(f"📍 ({best['source']}) נמצא: {best['name']} ({best['type_label']}) - "
                     f"כ-{best['walk_m']:.0f} מ' הליכה" + ("" if best.get("walk_measured") else " (אומדן)"))
    return best


def _get_walking_route_osm(orig_lat, orig_lon, dest_lat, dest_lon, log_callback=None):
    url = f"{OSRM_WALK_URL}/{orig_lon},{orig_lat};{dest_lon},{dest_lat}?overview=full&geometries=geojson"
    try:
        resp = requests.get(url, headers=_headers(), timeout=8)
        data = resp.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            if log_callback:
                log_callback("⚠️ OSRM לא מצא מסלול הליכה בין הנקודות")
            return None
        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]  # רשימת [lon, lat]
        points = [(lat, lon) for lon, lat in coords]
        return {"points": points, "distance_m": route["distance"], "duration_s": route["duration"]}
    except Exception as e:
        if log_callback:
            log_callback(f"⚠️ שגיאת חישוב מסלול הליכה (OSRM): {e}")
        return None


# חלופות הליכה (מצבי מסלול מהיר / בטוח יותר / מאוזן). FOSSGIS (routing.openstreetmap.de) - מדיניות השימוש
# (נבדקה 2026-11-11, https://routing.openstreetmap.de/about.html): "One request per second max",
# "Use a valid user agent", "No scraping, no heavy usage", הצגת ייחוס OpenStreetMap. לכן: בקשה אחת לשנייה לכל
# היותר, מטמון 10 דק' לאותו זוג נקודות, timeout קצר. OSRM מחזיר זמן ומרחק בלבד - לא בטיחות.
ALT_CACHE_TTL_S = 600
ALT_TIMEOUT_S = 6
ALT_MIN_GAP_S = 1.0
ROUTING_ATTRIBUTION = "ניתוב: OSRM (FOSSGIS) · © OpenStreetMap contributors"
_alt_cache = {}
_alt_lock = threading.Lock()
_alt_next = [0.0]


def get_walking_alternatives(orig_lat, orig_lon, dest_lat, dest_lon, log_callback=None):
    """עד 3 חלופות הליכה -> [{points, distance_m, duration_s}] (לפי סדר OSRM, לא ממוין) או None."""
    if not HAS_REQUESTS:
        return None
    key = tuple(round(v, 5) for v in (orig_lat, orig_lon, dest_lat, dest_lon))
    now = time.time()
    with _alt_lock:
        hit = _alt_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
        wait = _alt_next[0] - now
        _alt_next[0] = max(_alt_next[0], now) + ALT_MIN_GAP_S
    if wait > 0:
        time.sleep(min(wait, 3.0))
    url = (f"{OSRM_WALK_URL}/{orig_lon:.5f},{orig_lat:.5f};{dest_lon:.5f},{dest_lat:.5f}"
           "?alternatives=3&overview=full&geometries=geojson&steps=false")
    try:
        data = requests.get(url, headers=_headers(), timeout=ALT_TIMEOUT_S).json()
    except Exception as e:  # noqa: BLE001
        if log_callback:
            log_callback(f"⚠️ שגיאת חישוב חלופות הליכה (OSRM): {e}")
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    out = []
    for rt in data["routes"][:3]:
        pts = [(c[1], c[0]) for c in (rt.get("geometry") or {}).get("coordinates") or []]
        if len(pts) >= 2 and isinstance(rt.get("duration"), (int, float)):
            out.append({"points": pts, "distance_m": float(rt.get("distance") or 0), "duration_s": float(rt["duration"])})
    if not out:
        return None
    with _alt_lock:
        _alt_cache[key] = (time.time() + ALT_CACHE_TTL_S, out)
        if len(_alt_cache) > 128:
            _alt_cache.pop(min(_alt_cache, key=lambda k: _alt_cache[k][0]), None)
    return out


# ==================== ממשק ציבורי - בוחר backend אוטומטית ====================

def find_nearest_safe_place(lat, lon, radius_m=None, log_callback=None, online=True, worship=None):
    """
    מחפש את המקום הבטוח המתאים.
    online=False: מיידי ובלי רשת - רשימה מובנית (כיבוי רשמי + משטרה + בי"ח עם מיון) ומטמון.
    online=True: Google Places (אם מוגדר מפתח), אחרת OpenStreetMap (Overpass + Nominatim במקביל) יחד עם
    הרשימה המובנית, מדורג לפי מרחק הליכה אמיתי.
    מחזיר dict {name, lat, lon, type, type_label, distance_m, walk_m, source, ...} או None.
    """
    import config
    import worship_pref
    radius_m = radius_m or config.SAFE_PLACE_SEARCH_RADIUS_M
    # worship: העדפת מקום התפילה (worship_pref.OPTIONS); None = ברירת המחדל (בית כנסת, כמו קודם)
    categories = worship_pref.categories_for(worship)

    if not online:
        return _find_safe_place_osm(lat, lon, categories, radius_m, log_callback, online=False)
    if not HAS_REQUESTS:
        if log_callback:
            log_callback("⚠️ requests לא מותקן - חיפוש מקום בטוח ברשת מושבת")
        return _find_safe_place_osm(lat, lon, categories, radius_m, log_callback, online=False)

    if config.GOOGLE_MAPS_API_KEY:
        result = _find_safe_place_google(lat, lon, categories, radius_m, config.GOOGLE_MAPS_API_KEY, log_callback)
        if result == "DENIED":
            pass  # נופלים ל-OSM
        elif result is not None:
            return dict(result, source="google")
        elif log_callback:
            log_callback("ℹ️ Google Places לא מצא תוצאה - מנסה OpenStreetMap כגיבוי")

    return _find_safe_place_osm(lat, lon, categories, radius_m, log_callback)


def get_walking_route(orig_lat, orig_lon, dest_lat, dest_lon, log_callback=None):
    """מחזיר מסלול הליכה אמיתי. Google Directions קודם (אם מוגדר מפתח), אחרת OSRM."""
    import config
    if not HAS_REQUESTS:
        return None

    if config.GOOGLE_MAPS_API_KEY:
        result = _get_walking_route_google(orig_lat, orig_lon, dest_lat, dest_lon, config.GOOGLE_MAPS_API_KEY, log_callback)
        if result is not None:
            return result

    return _get_walking_route_osm(orig_lat, orig_lon, dest_lat, dest_lon, log_callback)


def find_safe_route(lat, lon, log_callback=None, online=True, worship=None):
    """מקום בטוח + מסלול הליכה אליו. online=False: מקום בלבד, מיידי ובלי רשת (המסלול יחושב בשלב הרשת)."""
    place = find_nearest_safe_place(lat, lon, log_callback=log_callback, online=online, worship=worship)
    if place is None:
        return None
    route = get_walking_route(lat, lon, place["lat"], place["lon"], log_callback=log_callback) if online else None
    return {"place": place, "route": route}

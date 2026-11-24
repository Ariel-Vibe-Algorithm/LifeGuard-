# -*- coding: utf-8 -*-
"""
build_safe_places.py - רשימת מקומות בטוחים מובנית באפליקציה (עובדת בלי אינטרנט, מהפעלה ראשונה).

מקורות (כל אחד עם רישיון ומועד):
1. תחנות כיבוי והצלה - data.gov.il, "תחנות כיבוי והצלה בישראל - 2026" (Other (Open)), קואורדינטות רשמיות.
2. בתי חולים עם מנחת (= בתי החולים הכלליים הגדולים, עם חדר מיון) - data.gov.il "מנחתי בתי חולים" (שמות בלבד);
   המיקום מ-Nominatim (OSM), בקשה אחת לשנייה, חד-פעמי בבנייה.
3. תחנות משטרה + בתי חולים - OpenStreetMap (ODbL): Overpass לכל הארץ כשזמין; אחרת מטמון המחקר
   (research/samples/risk_factors/cache - ת"א, ירושלים, חיפה, באר שבע).
אין רשימה רשמית פתוחה של תחנות משטרה עם קואורדינטות (נבדק 2026-11-08: רק גבולות ושיוך יישובים).

    python -m tools.build_safe_places [--no-overpass] [--no-nominatim]
"""

import argparse
import glob
import gzip
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "safe_places", "safe_places_il.json")
UA = {"User-Agent": "LifeGuard-Research/1.0"}
CKAN = "https://data.gov.il/api/3/action/datastore_search"
FIRE_2026 = "7f3009dd-b299-462c-8a6d-9c645b68059f"
HELIPADS = "d848ff09-3b60-4db9-a932-740e3ec73389"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
IL_BBOX = (29.45, 34.2, 33.35, 35.95)


def ckan_all(rid):
    out, off = [], 0
    while True:
        r = requests.get(CKAN, params={"resource_id": rid, "limit": 500, "offset": off}, headers=UA, timeout=30).json()
        recs = r["result"]["records"]
        out += recs
        off += len(recs)
        if not recs or off >= r["result"]["total"]:
            return out


def fire_stations(log):
    out = []
    for r in ckan_all(FIRE_2026):
        try:
            lat, lon = float(r["Geo_Lat"]), float(r["Geo_Lon"])
        except (TypeError, ValueError, KeyError):
            continue
        if not (IL_BBOX[0] <= lat <= IL_BBOX[2] and IL_BBOX[1] <= lon <= IL_BBOX[3]):
            continue
        out.append({"name": f"תחנת כיבוי והצלה {r['Station']}", "lat": round(lat, 6), "lon": round(lon, 6),
                    "type": "fire_station", "address": r.get("Address"), "official": True, "src": "fire_gov_2026"})
    log(f"fire stations (data.gov.il 2026): {len(out)}")
    return out


def er_hospitals(log, use_nominatim=True):
    # ברשימת המנחתים יש גם מנחתים שאינם בתי חולים (מד"א רמלה, חוות רם, בית שמש, כרמיאל) - רק בתי חולים כלליים
    names = sorted({r["Name"].strip() for r in ckan_all(HELIPADS) if r.get("Name")
                    and _er_hospital(r["Name"], None)})
    out = []
    if not use_nominatim:
        log(f"hospitals with helipad: {len(names)} names (not geocoded - --no-nominatim)")
        return out
    for n in names:
        q = n if "בית חולים" in n or "מרכז רפואי" in n else f"בית חולים {n}"
        try:
            rows = requests.get(NOMINATIM, params={"q": q, "format": "jsonv2", "limit": 3, "countrycodes": "il",
                                                   "accept-language": "he"}, headers=UA, timeout=15).json()
        except (requests.RequestException, ValueError):
            rows = []
        hit = next((x for x in rows if x.get("type") == "hospital" or x.get("category") == "amenity"), None) or \
            (rows[0] if rows else None)
        if hit:
            out.append({"name": n, "lat": round(float(hit["lat"]), 6), "lon": round(float(hit["lon"]), 6),
                        "type": "hospital", "emergency": "yes", "official": True, "src": "helipad_gov+nominatim"})
        else:
            log(f"  ! not geocoded: {n}")
        time.sleep(1.1)                      # מדיניות Nominatim
    log(f"ER hospitals (helipad list, geocoded): {len(out)}/{len(names)}")
    return out


# "בית חולים" ב-OSM כולל גם מרפאות, גן ילדים(!), בריאות הנפש, גריאטריה, גמילה ושיקום - לא מקום בטוח בלילה.
# נשמרים רק: emergency=yes, או שם של בית חולים כללי עם מיון (רשימת מנחתי משרד הבריאות + בתי חולים כלליים מוכרים).
ER_HOSPITAL_NAMES = ("איכילוב", "סוראסקי", "שיבא", "תל השומר", "בילינסון", "רבין", "השרון", "אסף הרופא", "שמיר",
                     "מאיר", "וולפסון", "שערי צדק", "הדסה", "רמב\"ם", "רמבם", "בני ציון", "כרמל", "העמק", "פוריה",
                     "זיו", "הגליל", "נהריה", "הלל יפה", "לניאדו", "קפלן", "ברזילי", "אסותא אשדוד", "סורוקה",
                     "יוספטל", "המרכז הרפואי לגליל", "מעייני הישועה", "בית חולים אנגלי", "האנגלי", "סנט ג'וזף",
                     "אל-מקאסד", "המוקאסד", "נצרת", "הצרפתי", "האיטלקי")
NOT_ER = ("נפש", "גריאטר", "גמילה", "החלמה", "שיקום", "מרפאה", "מרפאות", "גן ילדים", "מכון", "קופ\"ח", "קופת חולים",
          "מכבי", "כללית", "לאומית", "מאוחדת", "סיעוד", "הוספיס", "פסיכיאטר", "ילדים חולים", "רטורנו", "Eye", "العيون",
          "Smile", "Dental", "שיניים")


def _er_hospital(name, emergency):
    n = name or ""
    if any(b in n for b in NOT_ER):
        return False
    return emergency == "yes" or any(k in n for k in ER_HOSPITAL_NAMES)


def _osm_elements_to_places(els, src):
    out = []
    for el in els:
        t = el.get("tags") or {}
        a = t.get("amenity")
        if a not in ("police", "hospital"):
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        if lat is None:
            continue
        name = t.get("name:he") or t.get("name") or ("תחנת משטרה" if a == "police" else "בית חולים")
        if a == "hospital" and (t.get("emergency") == "no" or not _er_hospital(name, t.get("emergency"))):
            continue
        out.append({"name": name,
                    "lat": round(lat, 6), "lon": round(lon, 6), "type": a, "emergency": t.get("emergency"),
                    "official": False, "src": src})
    return out


def osm_overpass(log):
    q = ('[out:json][timeout:120];area["ISO3166-1"="IL"][admin_level=2]->.il;'
         '(nwr["amenity"="police"](area.il);nwr["amenity"="hospital"](area.il););out center tags;')
    for url in OVERPASS * 2:                 # Overpass הציבורי לא יציב - סבב שני
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=150)
            if r.status_code == 200:
                out = _osm_elements_to_places(r.json().get("elements", []), "osm_overpass")
                log(f"OSM police+hospitals nationwide (Overpass {url}): {len(out)}")
                return out
            log(f"  Overpass {url}: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            log(f"  Overpass {url}: {type(e).__name__}")
    return None


def osm_research_cache(log):
    out = []
    for f in glob.glob(os.path.join(ROOT, "research", "samples", "risk_factors", "cache", "osm_*", "*.json.gz")):
        try:
            d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        except (OSError, ValueError):
            continue
        body = d.get("body") if isinstance(d.get("body"), dict) else d       # מטמון המחקר עוטף את התשובה
        out += _osm_elements_to_places(body.get("elements") or [], "osm_research_cache_2026-11-07")
    log(f"OSM police (research cache, 4 cities): {len(out)}")
    return out


def _previous_nationwide():
    try:
        with open(OUT, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return None
    if not str(d.get("coverage", "")).startswith("nationwide"):
        return None
    return [p for p in d["places"] if str(p.get("src", "")).startswith("osm")
            and (p["type"] != "hospital" or _er_hospital(p["name"], p.get("emergency")))]


def dedup(places, m=60):
    import math
    out = []
    for p in places:
        dup = False
        for q in out:
            if q["type"] == p["type"] and abs(q["lat"] - p["lat"]) < 0.001 and \
                    math.hypot((q["lat"] - p["lat"]) * 111000, (q["lon"] - p["lon"]) * 94000) < m:
                dup = True
                if p.get("official") and not q.get("official"):
                    q.update(p)
                break
        if not dup:
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-overpass", action="store_true")
    ap.add_argument("--no-nominatim", action="store_true")
    a = ap.parse_args(argv)
    log = print
    osm = None if a.no_overpass else osm_overpass(log)
    # בתי חולים מרשימת המנחתים רק אם אין כבר בית חולים מ-OSM בטווח 1.5 ק"מ (מיקום OSM מדויק יותר מחיפוש שם)
    heli = [h for h in er_hospitals(log, not a.no_nominatim)
            if not any(o["type"] == "hospital" and abs(o["lat"] - h["lat"]) < 0.0135 and abs(o["lon"] - h["lon"]) < 0.016
                       for o in (osm or []))]
    places = fire_stations(log) + heli
    if osm is None:
        prev = _previous_nationwide()
        if prev is not None:                 # לא דורסים רשימה ארצית טובה ברשימה חלקית
            log(f"Overpass unavailable - keeping previous nationwide OSM places ({len(prev)})")
            osm, coverage = prev, "nationwide (OSM from previous build)"
        else:
            osm = osm_research_cache(log)
            coverage = "police: Tel Aviv, Jerusalem, Haifa, Be'er Sheva only (Overpass unavailable at build)"
    else:
        coverage = "nationwide"
    places = dedup(places + osm)
    doc = {"built_at": time.strftime("%Y-%m-%d"), "coverage": coverage, "count": len(places),
           "sources": [
               {"id": "fire_gov_2026", "name": "תחנות כיבוי והצלה בישראל - 2026, data.gov.il", "license": "Other (Open)"},
               {"id": "helipad_gov+nominatim", "name": "מנחתי בתי חולים, data.gov.il + מיקום מ-OpenStreetMap",
                "license": "Other (Open) / ODbL"},
               {"id": "osm", "name": "OpenStreetMap contributors", "license": "ODbL"}],
           "places": places}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"-> {OUT}: {len(places)} places ({os.path.getsize(OUT) // 1024} KB), coverage: {coverage}")


if __name__ == "__main__":
    sys.exit(main())

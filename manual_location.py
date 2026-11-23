# -*- coding: utf-8 -*-
"""
manual_location.py - מיקום שהמשתמש/ת כותב/ת בעצמו/ה בזמן SOS (אין GPS / רק מיקום IP משוער).

מה אפשר לכתוב:
- קואורדינטות: "32.0853, 34.7818"
- קישור מפה: Google Maps (?q= / @lat,lon / !3d..!4d..), OpenStreetMap (mlat/mlon או #map=z/lat/lon),
  Waze (ll=), geo:lat,lon
- כתובת חופשית: "דיזנגוף 50 תל אביב" -> חיפוש כתובת ב-OpenStreetMap (Nominatim). זה שולח את הטקסט
  לשירות חיצוני - רק מה שהמשתמש/ת כתב/ה, ורק בלחיצה. אם החיפוש נכשל - הטקסט עצמו עדיין נשלח
  לאנשי הקשר כהודעה (תיאור כמו "קומה 3 ליד הכניסה האחורית" שימושי גם בלי קואורדינטות).

קדימות בזמן אירוע (engine): GPS אמיתי > מיקום שהוזן ידנית > מיקום IP משוער.
"""

import re
import time
import urllib.parse

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "LifeGuard/1.0 (personal safety app)"
GEOCODE_TIMEOUT_S = 6

_NUM = r"(-?\d{1,3}(?:\.\d+)?)"
_PATTERNS = [
    re.compile(r"!3d" + _NUM + r"!4d" + _NUM),                      # Google: נקודת המקום עצמה
    re.compile(r"[?&](?:q|query|ll|destination|center)=" + _NUM + r"(?:,|%2C)\s*" + _NUM, re.I),
    re.compile(r"@" + _NUM + r"," + _NUM),                           # Google: מרכז המפה
    re.compile(r"mlat=" + _NUM + r"&mlon=" + _NUM),                   # OSM marker
    re.compile(r"#map=\d+/" + _NUM + r"/" + _NUM),                    # OSM view
    re.compile(r"geo:" + _NUM + r"," + _NUM, re.I),
    re.compile(r"^\s*" + _NUM + r"\s*[,\s]\s*" + _NUM + r"\s*$"),    # "32.08, 34.78"
]


def _valid(lat, lon):
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def parse_coordinates(text):
    """טקסט/קישור -> (lat, lon) או None. לא פונה לרשת."""
    t = urllib.parse.unquote(str(text or "").strip())
    for rx in _PATTERNS:
        m = rx.search(t)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if _valid(lat, lon):
                return lat, lon
    return None


def geocode(text, session=None, timeout=GEOCODE_TIMEOUT_S):
    """כתובת -> {"lat","lon","display"} או None. Nominatim (OSM), מועדף ישראל, בלי לזרוק חריגות."""
    q = str(text or "").strip()
    if len(q) < 3:
        return None
    s = session or requests
    try:
        r = s.get(NOMINATIM_URL, params={"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "il",
                                         "accept-language": "he"},
                  headers={"User-Agent": USER_AGENT}, timeout=timeout)
        rows = r.json() if r.status_code == 200 else []
        if not rows:   # לא נמצא בישראל - ניסיון בלי הגבלת מדינה (בחו"ל)
            r = s.get(NOMINATIM_URL, params={"q": q, "format": "jsonv2", "limit": 1, "accept-language": "he"},
                      headers={"User-Agent": USER_AGENT}, timeout=timeout)
            rows = r.json() if r.status_code == 200 else []
        if rows:
            lat, lon = float(rows[0]["lat"]), float(rows[0]["lon"])
            if _valid(lat, lon):
                return {"lat": lat, "lon": lon, "display": rows[0].get("display_name", "")[:200],
                        "kind": rows[0].get("addresstype") or rows[0].get("type")}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return None


# כתובת ברמת בניין/רחוב מדויקת יותר מעיר שלמה - הדיוק המשוער לפי סוג התוצאה
_KIND_ACCURACY = {"house": 30, "building": 30, "amenity": 50, "shop": 50, "road": 150, "neighbourhood": 600,
                  "suburb": 1500, "quarter": 800, "city": 5000, "town": 3000, "village": 1500}


def resolve(text, geocoder=geocode):
    """
    טקסט המשתמש/ת -> dict שמתאים ל-engine/gateway:
      {"available", "lat", "lon", "is_precise", "accuracy_m_estimate", "source", "manual_text", "display",
       "timestamp"}  או  {"available": False, "manual_text": ...} אם לא נמצאו קואורדינטות.
    """
    text = str(text or "").strip()[:300]
    now = time.time()
    c = parse_coordinates(text)
    if c:
        return {"available": True, "lat": c[0], "lon": c[1], "is_precise": True, "accuracy_m_estimate": 30,
                "source": "manual", "manual_text": text, "display": f"{c[0]:.5f}, {c[1]:.5f}", "timestamp": now,
                "city": "הוזן ידנית"}
    g = geocoder(text) if text else None
    if g:
        acc = _KIND_ACCURACY.get(g.get("kind"), 300)
        return {"available": True, "lat": g["lat"], "lon": g["lon"], "is_precise": acc <= 150,
                "accuracy_m_estimate": acc, "source": "manual_address", "manual_text": text,
                "display": g.get("display") or text, "timestamp": now, "city": "הוזן ידנית"}
    return {"available": False, "manual_text": text, "source": "manual_text", "timestamp": now}

# -*- coding: utf-8 -*-
"""
mapillary_client.py - Mapillary Graph API v4 בצד השרת בלבד: תצפיות מספריות נגזרות, עם ייחוס.

- ה-token רק בשרת: MAPILLARY_TOKEN או קובץ סוד MAPILLARY_TOKEN_FILE (docker secret). אף פעם לא נשלח ללקוח,
  לא נכתב ללוג/לתשובה, ולא נשלח לשרתי התמונות (ה-URL שלהן חתום). בלי token: not_configured ושום בקשה לא נשלחת.
- **שום תמונה לא מורדת או נשמרת בשרת.** נשמרים רק מספרים נגזרים מהמטא-דאטה ומהזיהויים של Mapillary
  (כיסוי תמונות, טריות, עמודי תאורה שזוהו אוטומטית) + ייחוס לכל תמונה (מזהה, יוצר/ת, רישיון).
  ניתוח פיקסלים (תאורה מהתמונה) נשאר בכלי המחקר research_tools/mapillary.py.
- kind=estimate, reliability=low: זיהוי אוטומטי מתמונות תורמים; היעדר זיהוי ≠ היעדר עמודים.
- אותם כללי אבטחה כמו בלקוח המחקר: header OAuth, בלי מעקב אחרי הפניות (redirect) כדי שה-token לא ידלוף,
  תיבת חיפוש קטנה (Mapillary: bbox < 0.01 מעלות רבועות), תגובה מוגבלת בגודל, timeout קצר.
- תנאי Mapillary (https://www.mapillary.com/terms, נבדק 2026-11-11): תוכן משתמשים CC BY-SA 4.0 (סעיף 3b);
  ייחוס עם לוגו + קישור (סעיף 11); שימוש מסחרי רק למטרות שבסעיף 12 - לבדיקה משפטית לפני ייצור מסחרי.
"""

import json
import math
import time
import urllib.parse
import urllib.request

from server import data_sources as DS

API = "https://graph.mapillary.com"
LICENSE = "CC BY-SA 4.0"
SOURCE_ID = "mapillary"
UA = "LifeGuard-Server/1.0"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_S = 6
MAX_RADIUS_M = 300
MAX_ATTRIBUTION = 20
STREET_LIGHT = "object--street-light"


class MapillaryError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None       # הפניה -> HTTPError; ה-token לא נשלח ליעד אחר


def bbox_around(lat, lon, radius_m):
    radius_m = max(10.0, min(float(radius_m), MAX_RADIUS_M))
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(0.1, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _dist_m(lat1, lon1, lat2, lon2):
    la1, lo1, la2, lo2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


class MapillaryClient:
    def __init__(self, cache=None, token=None, base_url=API, timeout=TIMEOUT_S, opener=None):
        self._token = token if token is not None else DS.secret("MAPILLARY_TOKEN")
        self.base, self.timeout = base_url.rstrip("/"), timeout
        self.cache = cache
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    @property
    def configured(self):
        return bool(self._token)

    def __repr__(self):            # ה-token לא מופיע גם ב-repr / בלוגים
        return f"<MapillaryClient configured={self.configured}>"

    def _get(self, path, params):
        if not self._token:
            raise MapillaryError("not_configured")
        url = f"{self.base}/{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Authorization": f"OAuth {self._token}",
                                                   "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as r:
                data = r.read(MAX_BYTES + 1)
        except Exception as exc:  # noqa: BLE001 - כל כישלון נרשם כ-unreachable, בלי ה-token
            if self.cache is not None:
                self.cache.record_check(SOURCE_ID, False, type(exc).__name__)
            raise MapillaryError(f"Mapillary לא זמין: {type(exc).__name__}") from None
        if len(data) > MAX_BYTES:
            raise MapillaryError("תגובה גדולה מדי")
        if self.cache is not None:
            self.cache.record_check(SOURCE_ID, True, "HTTP 200")
        return json.loads(data.decode("utf-8"))

    def _cached(self, kind, lat, lon, radius_m, fetch):
        key = f"{kind}:{round(lat, 4)}:{round(lon, 4)}:{int(radius_m)}"
        if self.cache is not None:
            hit = self.cache.get(SOURCE_ID, key)
            if hit:
                rec = hit["value"]
                rec["cached"] = True
                return rec
        rec = fetch()
        if self.cache is not None:
            self.cache.put(SOURCE_ID, key, rec, rec.get("source_updated_at"))
        return rec

    # ------------------------------------------------------------ derived observations
    def coverage(self, lat, lon, radius_m=150):
        """כמה תמונות רחוב (לא פנורמה) יש בסביבה, מתי צולמו, והמרחק לקרובה. אין הורדת תמונה."""
        radius_m = min(float(radius_m), MAX_RADIUS_M)

        def fetch():
            b = bbox_around(lat, lon, radius_m)
            rows = self._get("images", {"fields": "id,captured_at,computed_geometry,creator,is_pano",
                                        "bbox": ",".join(f"{x:.6f}" for x in b), "limit": 500}).get("data", [])
            pts = []
            for r in rows:
                g = (r.get("computed_geometry") or {}).get("coordinates")
                if r.get("is_pano") or not g:
                    continue
                d = _dist_m(lat, lon, g[1], g[0])
                if d <= radius_m:
                    pts.append((d, r))
            pts.sort(key=lambda x: x[0])
            caps = [r["captured_at"] / 1000.0 for _, r in pts if isinstance(r.get("captured_at"), (int, float))]
            attribution = [{"image_id": str(r["id"]), "creator": (r.get("creator") or {}).get("username"),
                            "license": LICENSE, "url": f"https://www.mapillary.com/app/?pKey={r['id']}"}
                           for _, r in pts[:MAX_ATTRIBUTION]]
            data = {"image_count": len(pts), "radius_m": radius_m,
                    "nearest_image_m": round(pts[0][0], 1) if pts else None,
                    "newest_capture_at": max(caps) if caps else None, "oldest_capture_at": min(caps) if caps else None,
                    "note": "כיסוי תמונות בלבד - לא מדד בטיחות."}
            return DS.record(SOURCE_ID, data, source_updated_at=max(caps) if caps else None, precision_m=radius_m,
                             attribution={"text": "Mapillary (CC BY-SA 4.0)", "images": attribution,
                                          "logo_required": True, "link": "https://www.mapillary.com/"})
        return self._cached("coverage", lat, lon, radius_m, fetch)

    def street_lights(self, lat, lon, radius_m=150):
        """עמודי תאורה שזוהו אוטומטית ע"י Mapillary בתמונות (map features). הערכה בלבד."""
        radius_m = min(float(radius_m), MAX_RADIUS_M)

        def fetch():
            b = bbox_around(lat, lon, radius_m)
            rows = self._get("map_features", {"fields": "id,object_value,geometry,first_seen_at,last_seen_at",
                                              "object_values": STREET_LIGHT,
                                              "bbox": ",".join(f"{x:.6f}" for x in b), "limit": 500}).get("data", [])
            pts, seen = [], []
            for r in rows:
                g = (r.get("geometry") or {}).get("coordinates")
                if r.get("object_value") != STREET_LIGHT or not g:
                    continue
                d = _dist_m(lat, lon, g[1], g[0])
                if d <= radius_m:
                    pts.append({"feature_id": str(r["id"]), "lat": round(g[1], 6), "lon": round(g[0], 6),
                                "distance_m": round(d, 1)})
                    ls = r.get("last_seen_at")
                    if isinstance(ls, (int, float)):
                        seen.append(ls / 1000.0)
                    elif isinstance(ls, str):
                        try:
                            seen.append(time.mktime(time.strptime(ls[:19], "%Y-%m-%dT%H:%M:%S")))
                        except ValueError:
                            pass
            area_km2 = math.pi * (radius_m / 1000.0) ** 2
            data = {"detected_street_lights": len(pts), "radius_m": radius_m,
                    "per_km2": round(len(pts) / area_km2, 1) if area_km2 else None,
                    "features": sorted(pts, key=lambda p: p["distance_m"])[:50],
                    "note": "זיהוי אוטומטי מתמונות תורמים: היעדר זיהוי אינו אומר שאין תאורה, "
                            "וזיהוי עמוד אינו אומר שהוא דולק."}
            return DS.record(SOURCE_ID, data, source_updated_at=max(seen) if seen else None, precision_m=20,
                             attribution={"text": "Mapillary (CC BY-SA 4.0) - זיהוי אובייקטים אוטומטי",
                                          "logo_required": True, "link": "https://www.mapillary.com/"})
        return self._cached("lights", lat, lon, radius_m, fetch)

    def observe(self, lat, lon, radius_m=150):
        """שתי התצפיות יחד; בלי token - not_configured בלי שום בקשת רשת."""
        if not self.configured:
            return {"status": "not_configured", "source_id": SOURCE_ID,
                    "text": "Mapillary לא מוגדר בשרת הזה (חסר MAPILLARY_TOKEN)"}
        out = {"status": "ok", "source_id": SOURCE_ID}
        try:
            out["coverage"] = self.coverage(lat, lon, radius_m)
            out["street_lights"] = self.street_lights(lat, lon, radius_m)
        except MapillaryError as exc:
            return {"status": "unreachable", "source_id": SOURCE_ID, "text": str(exc)}
        return out

# -*- coding: utf-8 -*-
"""
routing.py - זמן הגעה בהליכה למתנדבים (במקום מרחק בקו אוויר).

פרטיות: המיקום של אדם במצוקה לא נשלח לשירות ניתוב חיצוני. ניתוב אמיתי רק מול שרת OSRM *שלנו*
(LIFEGUARD_OSRM_URL, למשל קונטיינר ב-docker-compose עם מפת ישראל מ-OSM). בלי שרת כזה - הערכה:
קו אוויר x מקדם עיקוף / מהירות הליכה, ומסומנת "משוער".

OSRM table API: GET {url}/table/v1/foot/{lon,lat;lon,lat;...}?sources=0&annotations=duration,distance

חלופות הליכה לתכנון מסלול (foot_alternatives) - לא בזמן SOS, אלא כשמשתמש/ת מבקש/ת לתכנן מסלול:
1. LIFEGUARD_OSRM_URL (OSRM שלנו) אם מוגדר.
2. אחרת השרת הציבורי של FOSSGIS: https://routing.openstreetmap.de/routed-foot (פרופיל הליכה אמיתי).
   *לא* router.project-osrm.org - שרת הדמו שם מריץ רק רכב ומתעלם מ-"foot".
   מדיניות שימוש (נבדקה 2026-11-11, https://routing.openstreetmap.de/about.html ותנאי FOSSGIS
   https://fossgis.de/arbeitsgruppen/osm-server/nutzungsbedingungen):
   "One request per second max", "Use a valid user agent", "No scraping, no heavy usage",
   "Display the required attribution" (© OpenStreetMap contributors), והבקשות נשמרות בלוג השרת שלהם.
   -> כאן: לכל היותר בקשה אחת לשנייה (נעילה גלובלית), User-Agent תיאורי וגנרי, timeout קצר, מטמון 10 דק',
      מפסק אחרי כשל, וקואורדינטות מעוגלות ל-5 ספרות. כיבוי: LIFEGUARD_PUBLIC_ROUTING=0.
OSRM מחזיר מרחק וזמן בלבד - הוא לא אומר כלום על בטיחות. הבטיחות מגיעה רק מהשכבות של LifeGuard.
"""

import json
import math
import os
import threading
import time
import urllib.request

WALK_SPEED_MPS = 1.3          # ~4.7 קמ"ש
DETOUR_FACTOR = 1.3           # רשת רחובות אינה קו ישר (ממוצע עירוני טיפוסי)
TIMEOUT_S = 1.5               # בחירום לא מחכים לניתוב: אם איטי - נופלים להערכה
MAX_DESTS = 8


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def estimate(origin, dest):
    d = haversine_m(origin, dest) * DETOUR_FACTOR
    return {"seconds": d / WALK_SPEED_MPS, "meters": d, "source": "estimate"}


class Router:
    def __init__(self, base_url=None, timeout=TIMEOUT_S):
        self.base = (base_url if base_url is not None else os.environ.get("LIFEGUARD_OSRM_URL", "")).rstrip("/")
        self.timeout = timeout
        self.last_error = None
        self._down_until = 0.0          # circuit breaker: שרת ניתוב תקוע לא מעכב כל SOS (סקירה 2)

    @property
    def configured(self):
        return bool(self.base)

    def walking_from(self, origin, dests):
        """זמני הליכה *מ*כל מתנדב *אל* האירוע. dests: [(lat, lon)] -> [dict]. תמיד מחזיר תוצאה."""
        dests = list(dests)[:MAX_DESTS]
        if not dests:
            return []
        import time as _t
        if self.configured and _t.time() >= self._down_until:
            try:
                # המתנדבים הם המקורות, האירוע הוא היעד (כיווני רחובות חד-סטריים פחות רלוונטיים להליכה,
                # אבל זה הכיוון הנכון)
                pts = dests + [origin]
                coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in pts)
                n = len(dests)
                url = (f"{self.base}/table/v1/foot/{coords}?sources={';'.join(map(str, range(n)))}"
                       f"&destinations={n}&annotations=duration,distance")
                req = urllib.request.Request(url, headers={"User-Agent": "LifeGuard-Server/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read(1_000_000).decode("utf-8"))
                if data.get("code") != "Ok":
                    raise ValueError(data.get("code"))
                out = []
                for i, dest in enumerate(dests):
                    s = data["durations"][i][0]
                    m = (data.get("distances") or [[None]] * n)[i][0]
                    out.append({"seconds": float(s), "meters": float(m) if m is not None else None, "source": "osrm"}
                               if s is not None else estimate(origin, dest))
                self.last_error = None
                return out
            except Exception as exc:  # noqa: BLE001 - ניתוב הוא שיפור, לא תנאי: נופלים להערכה
                self.last_error = str(exc)[:200]
                self._down_until = _t.time() + 60      # דקה בלי לנסות - ישר להערכה
        return [estimate(origin, d) for d in dests]


# ---------------------------------------------------------------- חלופות הליכה (תכנון מסלול)
FOSSGIS_FOOT_URL = "https://routing.openstreetmap.de/routed-foot"
FOSSGIS_POLICY_URL = "https://routing.openstreetmap.de/about.html"
ROUTING_ATTRIBUTION = "ניתוב: OSRM · © OpenStreetMap contributors (ODbL)"
ALT_USER_AGENT = "LifeGuard-Server/1.0 (non-commercial personal-safety app)"
ALT_TIMEOUT_S = 5.0
ALT_CACHE_TTL_S = 600
ALT_CACHE_MAX = 256
PUBLIC_MIN_GAP_S = 1.0        # מדיניות FOSSGIS: "One request per second max"
PUBLIC_MAX_QUEUE_S = 3.0      # אם התור ארוך מזה - לא מחכים, מחזירים "עמוס"
MAX_ALTERNATIVES = 3


class RoutingUnavailable(Exception):
    def __init__(self, reason, detail=""):
        super().__init__(detail or reason)
        self.reason = reason          # not_configured / disabled / busy / error / no_route
        self.detail = detail


def public_routing_enabled():
    return os.environ.get("LIFEGUARD_PUBLIC_ROUTING", "1").strip().lower() not in ("0", "off", "false", "no")


def parse_osrm_routes(data, limit=MAX_ALTERNATIVES):
    """תשובת OSRM route -> [{points, distance_m, duration_s}]. OSRM ממיין לפי weight, לא לפי זמן."""
    if not isinstance(data, dict) or data.get("code") != "Ok":
        return []
    out = []
    for rt in (data.get("routes") or [])[:limit]:
        coords = ((rt.get("geometry") or {}).get("coordinates")) or []
        pts = [(float(c[1]), float(c[0])) for c in coords if isinstance(c, (list, tuple)) and len(c) >= 2]
        if len(pts) >= 2 and isinstance(rt.get("duration"), (int, float)):
            out.append({"points": pts, "distance_m": float(rt.get("distance") or 0.0),
                        "duration_s": float(rt["duration"])})
    return out


class FootAlternatives:
    """עד 3 חלופות הליכה בין שתי נקודות. מטמון, קצב, מפסק. http_get(url, timeout) -> dict (ניתן להחלפה)."""

    def __init__(self, http_get=None, clock=time.time, sleep=time.sleep):
        self.http_get = http_get or self._http_get
        self.clock = clock
        self.sleep = sleep
        self._cache = {}
        self._lock = threading.Lock()
        self._gap_lock = threading.Lock()
        self._next_public = 0.0
        self._down_until = {}
        self.calls = []

    @staticmethod
    def _http_get(url, timeout):
        req = urllib.request.Request(url, headers={"User-Agent": ALT_USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(10_000_000).decode("utf-8"))

    @staticmethod
    def _url(base, a, b):
        return (f"{base}/route/v1/foot/{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}"
                "?alternatives=3&overview=full&geometries=geojson&steps=false")

    def get(self, a, b):
        """-> (routes, source) כש-source הוא "osrm" (שרת עצמי) או "fossgis". כשל -> RoutingUnavailable."""
        key = (round(a[0], 5), round(a[1], 5), round(b[0], 5), round(b[1], 5))
        now = self.clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1], hit[2]
        own = os.environ.get("LIFEGUARD_OSRM_URL", "").rstrip("/")
        if own:
            candidates = [("osrm", own)]
        elif public_routing_enabled():
            candidates = [("fossgis", FOSSGIS_FOOT_URL)]
        else:
            raise RoutingUnavailable("disabled", "OSRM עצמי לא מוגדר והניתוב הציבורי (FOSSGIS) כבוי בשרת הזה")
        last = None
        for source, base in candidates:
            if self._down_until.get(source, 0) > now:
                last = RoutingUnavailable("error", "שרת הניתוב נכשל לאחרונה - מנסים שוב בעוד דקה")
                continue
            if source == "fossgis":
                with self._gap_lock:
                    wait = self._next_public - self.clock()
                    if wait > PUBLIC_MAX_QUEUE_S:
                        raise RoutingUnavailable("busy", "יותר מדי בקשות ניתוב - נסו שוב בעוד כמה שניות")
                    self._next_public = max(self._next_public, self.clock()) + PUBLIC_MIN_GAP_S
                if wait > 0:
                    self.sleep(wait)
            try:
                url = self._url(base, a, b)
                self.calls.append(url)
                data = self.http_get(url, ALT_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001
                self._down_until[source] = self.clock() + 60
                last = RoutingUnavailable("error", f"{type(exc).__name__}: {str(exc)[:120]}")
                continue
            routes = parse_osrm_routes(data)
            if not routes:
                last = RoutingUnavailable("no_route", "שרת הניתוב לא מצא מסלול הליכה בין הנקודות")
                continue
            with self._lock:
                self._cache[key] = (self.clock() + ALT_CACHE_TTL_S, routes, source)
                if len(self._cache) > ALT_CACHE_MAX:
                    for k in sorted(self._cache, key=lambda k: self._cache[k][0])[:len(self._cache) - ALT_CACHE_MAX]:
                        self._cache.pop(k, None)
            return routes, source
        raise last or RoutingUnavailable("error")


_DEFAULT_ALTS = None


def foot_alternatives(a, b):
    global _DEFAULT_ALTS
    if _DEFAULT_ALTS is None:
        _DEFAULT_ALTS = FootAlternatives()
    return _DEFAULT_ALTS.get(a, b)

# -*- coding: utf-8 -*-
"""
rides.py - מצב נסיעה בטוחה (Safe Ride): מונית, נסיעה שיתופית, אוטובוס, רכבת, רכב פרטי.

נבנה על "ליווי הביתה" הקיים (escorts): אותם צופים, אותו טיימר שנאכף בשרת, אותה זיהוי "אין קליטה".
נוסף: יעד כנקודה, סוג נסיעה, פרטי רכב, מסלול צפוי, מסלול בפועל, וזיהוי חריגות:

1. **סטייה מהמסלול** (deviation)
   - עם מסלול כביש (OSRM עצמי במצב car: LIFEGUARD_OSRM_CAR_URL): מרחק מהמסלול > סף סוג הנסיעה + עד
     פעמיים דיוק ה-GPS, ב-3 מיקומים רצופים לפחות לאורך 60 שניות לפחות.
   - **מסלול חלופי לגיטימי:** אם בזמן הסטייה המרחק ליעד ממשיך לקטון (150 מ' בדקה) - זה נהג
     שבחר דרך אחרת / עקף פקק. לא שואלים; מעדכנים "מסלול חלופי".
   - בלי שרת ניתוב (או ברכבת, שאין לה מסלול כביש): רק "התרחקות מהיעד" - המרחק ליעד גדל ביותר
     מ-max(800 מ', 30% מהמרחק ההתחלתי) מעבר למינימום שנראה, לאורך 90 שניות לפחות.
2. **עצירה חריגה** (stopped): כל המיקומים בחלון בתוך רדיוס max(40 מ', פעמיים הדיוק), רחוק מהיעד.
   חלון לפי סוג: רכב 4 דק' (רמזור/פקק זוחל לא נכנסים), אוטובוס 7, רכבת 10 (תחנות/עיכובים).
3. **איחור** (late): עבר זמן ההגעה הצפוי:
   - עדיין מתקדמים ליעד → הארכה אוטומטית לפי קצב ההתקדמות (עד פעמיים, עד 20 דק' כל פעם) + עדכון
     לצופים "מתעכבת בדרך אבל מתקדמת" - בלי אזעקה.
   - לא מתקדמים → בדיקה מול הנוסע/ת.
4. **ליד היעד** (near_destination): בתוך 150 מ' ועצר/ה - "הגעת בשלום?" (אישור סיום). לא אזעקה.

הסלמה: חריגה → הודעת "הכל בסדר?" לנוסע/ת (escort_check, 90 שניות לענות; בנסיעה שקטה - טקסט ניטרלי)
+ עדכון מידע לצופים. "אני בסדר" → החריגה נסגרת ולא נשאלת שוב 10 דקות. "צריך/ה עזרה" או אין תשובה →
קריאת חירום מקושרת (שקטה אם הנסיעה שקטה) והצופים מקבלים התראת חירום.

שגיאות GPS: מיקום בדיוק גרוע מ-100 מ' לא משמש לסטייה/עצירה (רק "חי"); הסף גדל עם הדיוק.
"""

import json
import math
import secrets
import time
import urllib.request

RIDE_TYPES = ("taxi", "rideshare", "bus", "train", "private")
RIDE_LABELS = {"taxi": "מונית", "rideshare": "נסיעה שיתופית", "bus": "אוטובוס", "train": "רכבת",
               "private": "רכב פרטי"}
PROFILES = {
    "taxi": {"dev_m": 300, "stop_s": 240},
    "rideshare": {"dev_m": 300, "stop_s": 240},
    "private": {"dev_m": 300, "stop_s": 240},
    "bus": {"dev_m": 450, "stop_s": 420},
    "train": {"dev_m": None, "stop_s": 600},
}
BAD_ACCURACY_M = 100
DEV_MIN_FIXES, DEV_MIN_SPAN_S = 3, 60
PROGRESS_WINDOW_S, PROGRESS_MIN_M = 180, 300
ALT_ROUTE_MIN_M = 150           # בדקה של סטייה: התקרבות של 150 מ' ליעד = דרך חלופית
AWAY_MIN_M, AWAY_FRACTION, AWAY_SPAN_S = 800, 0.3, 90
NEAR_DEST_M, NEAR_DEST_STOP_S = 150, 120
ANOMALY_RESPONSE_S = 90
SNOOZE_S = 600
LATE_MAX_EXTENSIONS, LATE_MAX_EXTEND_S = 2, 1200
ALARM_KINDS = ("deviation", "stopped", "late")
TRACK_KEEP = 600

MIGRATION = """
ALTER TABLE escorts ADD COLUMN kind TEXT NOT NULL DEFAULT 'walk';
ALTER TABLE escorts ADD COLUMN ride_type TEXT;
ALTER TABLE escorts ADD COLUMN vehicle TEXT;
ALTER TABLE escorts ADD COLUMN dest_lat REAL;
ALTER TABLE escorts ADD COLUMN dest_lon REAL;
ALTER TABLE escorts ADD COLUMN route TEXT;
ALTER TABLE escorts ADD COLUMN route_source TEXT;
ALTER TABLE escorts ADD COLUMN start_remaining_m REAL;
ALTER TABLE escorts ADD COLUMN anomaly TEXT;
ALTER TABLE escorts ADD COLUMN snooze TEXT NOT NULL DEFAULT '{}';
ALTER TABLE escorts ADD COLUMN late_extensions INTEGER NOT NULL DEFAULT 0;
ALTER TABLE escorts ADD COLUMN near_dest_asked INTEGER NOT NULL DEFAULT 0;
CREATE TABLE escort_track(
    id INTEGER PRIMARY KEY AUTOINCREMENT, escort_id TEXT NOT NULL, t REAL NOT NULL, lat REAL NOT NULL,
    lon REAL NOT NULL, accuracy_m REAL, speed_mps REAL);
CREATE INDEX IF NOT EXISTS idx_track_escort ON escort_track(escort_id, t);
"""


# ---------------------------------------------------------------- גאומטריה (טהור)
def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


def _xy(p, lat0):
    return (p[1] * 111320 * math.cos(math.radians(lat0)), p[0] * 110540)


def _seg_dist(p, a, b, lat0):
    px, py = _xy(p, lat0)
    ax, ay = _xy(a, lat0)
    bx, by = _xy(b, lat0)
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), t


def distance_to_route(p, route):
    """מרחק (מ') מנקודה לקו המסלול."""
    if not route:
        return None
    if len(route) == 1:
        return haversine_m(p, route[0])
    return min(_seg_dist(p, route[i], route[i + 1], p[0])[0] for i in range(len(route) - 1))


def remaining_along(p, route):
    """מרחק שנותר לאורך המסלול מהנקודה הקרובה עליו ועד סופו."""
    if not route or len(route) < 2:
        return haversine_m(p, route[-1]) if route else None
    best = None
    for i in range(len(route) - 1):
        d, t = _seg_dist(p, route[i], route[i + 1], p[0])
        if best is None or d < best[0]:
            best = (d, i, t)
    d, i, t = best
    seg = haversine_m(route[i], route[i + 1])
    rest = sum(haversine_m(route[j], route[j + 1]) for j in range(i + 1, len(route) - 1))
    return d + seg * (1 - t) + rest


def simplify(route, max_points=200):
    if len(route) <= max_points:
        return route
    step = len(route) / (max_points - 1)
    return [route[int(i * step)] for i in range(max_points - 1)] + [route[-1]]


# ---------------------------------------------------------------- זיהוי חריגות (טהור)
def _good(track):
    return [f for f in track if f.get("accuracy_m") is None or f["accuracy_m"] <= BAD_ACCURACY_M]


def remaining_m(fix, dest, route):
    p = (fix["lat"], fix["lon"])
    if route and len(route) >= 2:
        return remaining_along(p, route)
    return haversine_m(p, dest)


def progressing(track, dest, route, now, window=PROGRESS_WINDOW_S, min_m=PROGRESS_MIN_M):
    """האם המרחק ליעד קטן לפחות min_m בחלון האחרון."""
    recent = [f for f in _good(track) if f["t"] >= now - window]
    if len(recent) < 2:
        return False
    return remaining_m(recent[0], dest, route) - remaining_m(recent[-1], dest, route) >= min_m


def detect(ride, track, now):
    """
    ride: {ride_type, dest:(lat,lon), route:[(lat,lon)]|None, route_source, start_remaining_m, deadline,
           snooze:{kind: until}, near_dest_asked}
    track: [{t, lat, lon, accuracy_m, speed_mps}] בסדר זמן.
    -> (kind, details) או (None, None). "progress_late" = מאחרים אבל מתקדמים (הארכה אוטומטית).
    """
    good = _good(track)
    if not good:
        return None, None
    prof = PROFILES.get(ride.get("ride_type"), PROFILES["taxi"])
    dest, route = ride["dest"], ride.get("route")
    snooze = ride.get("snooze") or {}
    last = good[-1]
    rem = remaining_m(last, dest, route)
    to_dest = haversine_m((last["lat"], last["lon"]), dest)

    def snoozed(kind):
        return snooze.get(kind, 0) > now

    # 4. ליד היעד ועצר/ה
    if to_dest <= NEAR_DEST_M + min(last.get("accuracy_m") or 0, 50) and not ride.get("near_dest_asked"):
        window = [f for f in good if f["t"] >= now - NEAR_DEST_STOP_S]
        if window and now - window[0]["t"] >= NEAR_DEST_STOP_S * 0.8 and \
                all(haversine_m((f["lat"], f["lon"]), dest) <= NEAR_DEST_M + 60 for f in window):
            return "near_destination", {"remaining_m": rem}
    near_dest = to_dest <= NEAR_DEST_M * 2

    # 1. סטייה
    if not near_dest and not snoozed("deviation"):
        # כל המיקומים בחלון האחרון (לפחות DEV_MIN_FIXES לאורך DEV_MIN_SPAN_S) - כולם מחוץ למסלול
        recent = [f for f in good if f["t"] >= now - DEV_MIN_SPAN_S]
        span = recent[-1]["t"] - recent[0]["t"] if len(recent) >= DEV_MIN_FIXES else 0
        span = span if span >= DEV_MIN_SPAN_S * 0.9 else 0          # שעון/קצב דיווח לא מדויק לגמרי
        if route and len(route) >= 2 and ride.get("route_source") == "osrm" and prof["dev_m"]:
            offs = [distance_to_route((f["lat"], f["lon"]), route) for f in recent]
            limits = [prof["dev_m"] + min(2 * (f.get("accuracy_m") or 20), 150) for f in recent]
            if len(recent) >= DEV_MIN_FIXES and span and all(o > lim for o, lim in zip(offs, limits)):
                # מסלול חלופי: *בזמן* הסטייה ממשיכים להתקרב ליעד (קו אוויר) - נהג שעקף פקק, לא חטיפה
                closer = (haversine_m((recent[0]["lat"], recent[0]["lon"]), dest)
                          - haversine_m((recent[-1]["lat"], recent[-1]["lon"]), dest))
                if closer >= ALT_ROUTE_MIN_M:
                    return "alternative_route", {"off_route_m": round(min(offs))}
                return "deviation", {"off_route_m": round(min(offs)), "remaining_m": rem}
        else:
            # בלי מסלול כביש: רק התרחקות מהיעד (בקו אוויר)
            start = ride.get("start_remaining_m") or rem
            seen_min = min(haversine_m((f["lat"], f["lon"]), dest) for f in good)
            away = [f for f in good if f["t"] >= now - AWAY_SPAN_S * 2]
            limit = max(AWAY_MIN_M, AWAY_FRACTION * start)
            if len(away) >= DEV_MIN_FIXES and away[-1]["t"] - away[0]["t"] >= AWAY_SPAN_S and \
                    all(haversine_m((f["lat"], f["lon"]), dest) - seen_min > limit for f in away[-DEV_MIN_FIXES:]):
                return "deviation", {"moving_away_m": round(to_dest - seen_min), "remaining_m": rem}

    # 2. עצירה חריגה
    if not near_dest and not snoozed("stopped"):
        stop_s = prof["stop_s"]
        window = [f for f in good if f["t"] >= now - stop_s]
        if len(window) >= 3 and now - window[0]["t"] >= stop_s * 0.9:
            c = (sum(f["lat"] for f in window) / len(window), sum(f["lon"] for f in window) / len(window))
            accs = sorted((f.get("accuracy_m") or 20) for f in window)
            radius = max(40, 2 * accs[len(accs) // 2])
            if all(haversine_m(c, (f["lat"], f["lon"])) <= radius for f in window):
                return "stopped", {"minutes": round((now - window[0]["t"]) / 60), "remaining_m": rem}

    # 3. איחור
    if now > ride["deadline"] and not near_dest and not snoozed("late"):
        if progressing(good, dest, route, now, window=300, min_m=PROGRESS_MIN_M):
            return "progress_late", {"remaining_m": rem}
        return "late", {"remaining_m": rem}
    return None, None


def eta_seconds(track, dest, route, now, fallback_speed=None):
    good = [f for f in _good(track) if f["t"] >= now - 300]
    if not good:
        return None
    rem = remaining_m(good[-1], dest, route)
    speed = None
    if len(good) >= 2 and good[-1]["t"] > good[0]["t"]:
        moved = remaining_m(good[0], dest, route) - rem
        if moved > 50:
            speed = moved / (good[-1]["t"] - good[0]["t"])
    speed = speed or fallback_speed
    return rem / speed if speed else None


TEXTS = {
    "deviation": "הנסיעה סוטה מהמסלול הצפוי. הכל בסדר?",
    "stopped": "הרכב עומד במקום כבר כמה דקות. הכל בסדר?",
    "late": "עבר זמן ההגעה הצפוי ואין התקדמות ליעד. הכל בסדר?",
    "near_destination": "נראה שהגעת ליעד. הגעת בשלום?",
}
WATCH_TEXTS = {
    "deviation": "הנסיעה של {name} סוטה מהמסלול - בודקים איתה/איתו עכשיו",
    "stopped": "הרכב של {name} עומד במקום כמה דקות - בודקים איתה/איתו עכשיו",
    "late": "{name} מאחר/ת ואין התקדמות ליעד - בודקים איתה/איתו עכשיו",
}
NEUTRAL_TEXT = "תזכורת מ-LifeGuard - פתח/י את האפליקציה"


def clean_vehicle(v):
    if not isinstance(v, dict):
        return {}
    return {k: str(v.get(k) or "").strip()[:60] for k in ("plate", "description", "driver") if v.get(k)}


# ---------------------------------------------------------------- שירות
class RideMixin:
    def _car_route(self, origin, dest):
        """מסלול כביש מ-OSRM עצמי (car). פרטיות: בלי שירות חיצוני. נכשל/לא מוגדר -> None."""
        import os
        base = os.environ.get("LIFEGUARD_OSRM_CAR_URL", "").rstrip("/")
        if not base:
            return None
        try:
            url = (f"{base}/route/v1/driving/{origin[1]:.6f},{origin[0]:.6f};{dest[1]:.6f},{dest[0]:.6f}"
                   "?overview=full&geometries=geojson")
            req = urllib.request.Request(url, headers={"User-Agent": "LifeGuard-Server/1.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                data = json.loads(r.read(5_000_000).decode("utf-8"))
            if data.get("code") != "Ok" or not data.get("routes"):
                return None
            rt = data["routes"][0]
            return {"points": [(c[1], c[0]) for c in rt["geometry"]["coordinates"]],
                    "duration_s": float(rt.get("duration") or 0), "distance_m": float(rt.get("distance") or 0)}
        except Exception:  # noqa: BLE001 - ניתוב הוא שיפור; בלעדיו: "התרחקות מהיעד"
            return None

    def _ride_init(self, eid, ride_type, destination_point, vehicle, location):
        if ride_type not in RIDE_TYPES:
            raise self._err(400, "invalid_ride_type", "סוג נסיעה לא תקין")
        if not isinstance(destination_point, dict):
            raise self._err(400, "destination_required", "בנסיעה בטוחה נדרש יעד (נקודה)")
        dlat, dlon = self._valid_coords(destination_point.get("lat"), destination_point.get("lon"))
        route, source, start_rem = None, None, None
        if location and location.get("lat") is not None:
            origin = (float(location["lat"]), float(location["lon"]))
            if ride_type != "train":
                r = self._car_route(origin, (dlat, dlon))
                if r:
                    route, source = simplify(r["points"], 400), "osrm"
            if route is None:
                route, source = [origin, (dlat, dlon)], "straight_line"
            start_rem = remaining_m({"lat": origin[0], "lon": origin[1]}, (dlat, dlon),
                                    route if source == "osrm" else None)
        self._x("""UPDATE escorts SET kind='ride', ride_type=?, vehicle=?, dest_lat=?, dest_lon=?, route=?,
                   route_source=?, start_remaining_m=? WHERE id=?""",
                (ride_type, json.dumps(clean_vehicle(vehicle), ensure_ascii=False), dlat, dlon,
                 json.dumps(route) if route else None, source, start_rem, eid))
        if location and location.get("lat") is not None:
            self._ride_track(eid, location)

    def _ride_track(self, eid, loc, now=None):
        def num(k, lo, hi):
            try:
                v = float(loc.get(k))
                return v if lo <= v <= hi else None
            except (TypeError, ValueError):
                return None
        self._x("INSERT INTO escort_track(escort_id, t, lat, lon, accuracy_m, speed_mps) VALUES (?,?,?,?,?,?)",
                (eid, now or time.time(), float(loc["lat"]), float(loc["lon"]), num("accuracy_m", 0, 100000),
                 num("speed_mps", 0, 120)))
        self._x("""DELETE FROM escort_track WHERE escort_id=? AND id NOT IN
                   (SELECT id FROM escort_track WHERE escort_id=? ORDER BY id DESC LIMIT ?)""", (eid, eid, TRACK_KEEP))

    def _ride_struct(self, e):
        route = json.loads(e["route"]) if e["route"] else None
        return {"ride_type": e["ride_type"], "dest": (e["dest_lat"], e["dest_lon"]),
                "route": [tuple(p) for p in route] if route and e["route_source"] == "osrm" else None,
                "route_source": e["route_source"], "start_remaining_m": e["start_remaining_m"],
                "deadline": e["deadline"], "snooze": json.loads(e["snooze"] or "{}"),
                "near_dest_asked": e["near_dest_asked"]}

    def _track(self, eid):
        return self._q("SELECT t, lat, lon, accuracy_m, speed_mps FROM escort_track WHERE escort_id=? ORDER BY t",
                       (eid,))

    def _ride_evaluate(self, eid, now=None):
        now = now or time.time()
        e = self._escort_row(eid)
        if not e or e["kind"] != "ride" or e["status"] != "active" or e["dest_lat"] is None:
            return
        cur = json.loads(e["anomaly"]) if e["anomaly"] else None
        if cur and cur.get("kind") in ALARM_KINDS:
            return                     # כבר שואלים - הטיימר של החריגה (tick) מטפל
        ride = self._ride_struct(e)
        track = self._track(eid)
        kind, info = detect(ride, track, now)
        name = self._display(e["owner"])
        watchers = [w["username"] for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (eid,))]
        if kind == "alternative_route":
            if not ride["snooze"].get("alt_noted"):
                ride["snooze"]["alt_noted"] = now + SNOOZE_S
                self._x("UPDATE escorts SET snooze=? WHERE id=?", (json.dumps(ride["snooze"]), eid))
                for w in watchers:
                    self._notify(w, "escort_update", {"escort_id": eid, "event": "alternative_route",
                                                      "text": f"{name} בדרך חלופית - ממשיכה/ממשיך להתקרב ליעד"})
            return
        if kind == "progress_late":
            if e["late_extensions"] < LATE_MAX_EXTENSIONS:
                eta = eta_seconds(track, ride["dest"], ride["route"], now) or 600
                ext = min(LATE_MAX_EXTEND_S, max(300, eta * 1.3))
                self._x("UPDATE escorts SET deadline=?, late_extensions=late_extensions+1, overdue_notified=0 "
                        "WHERE id=? AND status='active'", (now + ext, eid))
                for w in watchers:
                    self._notify(w, "escort_update", {"escort_id": eid, "event": "running_late",
                                                      "text": f"{name} מתעכב/ת בדרך אבל מתקדם/ת ליעד - "
                                                              f"זמן ההגעה הוארך ב-{round(ext / 60)} דק'"})
                return
            kind = "late"           # הוארך כבר פעמיים - שואלים
        if kind is None:
            return
        if kind == "near_destination":
            self._x("UPDATE escorts SET near_dest_asked=1 WHERE id=?", (eid,))
            self._raise_anomaly(e, kind, info, now, watchers, inform_watchers=False)
            return
        self._raise_anomaly(e, kind, info, now, watchers)

    def _raise_anomaly(self, e, kind, info, now, watchers, inform_watchers=True):
        silent = e["mode"] == "silent"
        anomaly = {"kind": kind, "since": now, "respond_by": now + ANOMALY_RESPONSE_S, "info": info,
                   "text": TEXTS[kind]}
        self._x("UPDATE escorts SET anomaly=? WHERE id=?", (json.dumps(anomaly, ensure_ascii=False), e["id"]))
        self._notify(e["owner"], "escort_check", {"escort_id": e["id"], "kind": kind, "silent": silent,
                                                  "text": NEUTRAL_TEXT if silent else TEXTS[kind],
                                                  "respond_by": anomaly["respond_by"]})
        if inform_watchers and kind in WATCH_TEXTS:
            text = WATCH_TEXTS[kind].format(name=self._display(e["owner"]))
            for w in watchers:
                self._notify(w, "escort_update", {"escort_id": e["id"], "event": "anomaly", "kind": kind,
                                                  "text": text})

    def _ride_tick(self, now):
        for e in self._q("SELECT id FROM escorts WHERE status='active' AND kind='ride'"):
            self._ride_evaluate(e["id"], now)
        for e in self._q("SELECT * FROM escorts WHERE status='active' AND kind='ride' AND anomaly IS NOT NULL"):
            a = json.loads(e["anomaly"])
            if now < a["respond_by"]:
                continue
            if a["kind"] in ALARM_KINDS:
                self._ride_escalate(e, f"אין תשובה לבדיקה: {TEXTS[a['kind']].split('.')[0]}", e["mode"])
            else:      # ליד היעד ולא ענו - לא אזעקה; הטיימר הרגיל של הליווי ממשיך לשמור
                self._x("UPDATE escorts SET anomaly=NULL WHERE id=?", (e["id"],))
                for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (e["id"],)):
                    self._notify(w["username"], "escort_update", {
                        "escort_id": e["id"], "event": "unconfirmed_arrival",
                        "text": f"{self._display(e['owner'])} הגיע/ה לאזור היעד אבל עוד לא אישר/ה הגעה"})

    def _ride_escalate(self, e, reason, mode):
        cur = self._x("UPDATE escorts SET status='escalated', ended_at=?, anomaly=NULL WHERE id=? AND status='active'",
                      (time.time(), e["id"]))
        if not cur.rowcount:
            r = self._escort_row(e["id"])
            return self._incident_view_for_owner(r["incident_id"], e["owner"]) if r["incident_id"] else None
        loc = None
        if e["last_lat"] is not None:
            loc = {"lat": e["last_lat"], "lon": e["last_lon"], "is_precise": bool(e["last_precise"]),
                   "source": "ride_last_known", "captured_at": e["last_loc_at"]}
        vehicle = json.loads(e["vehicle"] or "{}")
        vtxt = " · ".join(f"{k}: {v}" for k, v in (("רכב", vehicle.get("plate")), ("תיאור", vehicle.get("description")),
                                                    ("נהג", vehicle.get("driver"))) if v)
        reasons = [f"נסיעה בטוחה ({RIDE_LABELS.get(e['ride_type'], '')}): {reason}"] + ([vtxt] if vtxt else [])
        inc = self.create_incident(e["owner"], f"ride:{e['id']}:{secrets.token_hex(4)}", mode=mode,
                                   emergency_type="security", severity="high", location=loc,
                                   profile={"display_name": self._display(e["owner"])},
                                   risk={"level": "MEDIUM", "score": None, "reasons": reasons},
                                   trigger="server_timer")
        self._event(inc["id"], "ride_escalated", "נסיעה בטוחה: " + reason + (f" ({vtxt})" if vtxt else ""))
        self._x("UPDATE escorts SET incident_id=? WHERE id=?", (inc["id"], e["id"]))
        name = self._display(e["owner"])
        for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (e["id"],)):
            self._notify(w["username"], "escort_alarm", {"escort_id": e["id"], "incident_id": inc["id"],
                                                         "text": f"🚨 {name}: {reason} - נפתחה קריאת חירום"})
        return inc

    def _incident_view_for_owner(self, iid, owner):
        return self._view_for(iid, owner, include_medical=True)

    def escort_checkin(self, user, eid, status):
        e, role = self._escort_access(user, eid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל/ת הנסיעה")
        if status not in ("ok", "help"):
            raise self._err(400, "invalid_status", "סטטוס לא תקין")
        if e["status"] != "active":
            raise self._err(409, "closed", "הנסיעה הסתיימה")
        if status == "help":
            inc = self._ride_escalate(e, "הנוסע/ת ביקש/ה עזרה", e["mode"])
            return {"incident": inc}
        a = json.loads(e["anomaly"]) if e["anomaly"] else None
        snooze = json.loads(e["snooze"] or "{}")
        if a:
            snooze[a["kind"]] = time.time() + SNOOZE_S
        self._x("UPDATE escorts SET anomaly=NULL, snooze=? WHERE id=?", (json.dumps(snooze), eid))
        if a and a["kind"] in WATCH_TEXTS:
            for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (eid,)):
                self._notify(w["username"], "escort_update", {
                    "escort_id": eid, "event": "anomaly_cleared",
                    "text": f"✅ {self._display(user)} אישר/ה שהכל בסדר"})
        return self._escort_view(eid, user)

    def escort_sos(self, user, eid, mode):
        e, role = self._escort_access(user, eid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל/ת הנסיעה")
        if e["status"] != "active":
            raise self._err(409, "closed", "הנסיעה הסתיימה")
        return {"incident": self._ride_escalate(e, "הופעל SOS מתוך הנסיעה",
                                                "silent" if mode == "silent" else "loud")}

    def _ride_view(self, e, out, user):
        if e["kind"] != "ride":
            out["kind"] = "walk"
            return
        out.update(kind="ride", ride_type=e["ride_type"], ride_label=RIDE_LABELS.get(e["ride_type"]),
                   vehicle=json.loads(e["vehicle"] or "{}"), route_source=e["route_source"],
                   destination_point={"lat": e["dest_lat"], "lon": e["dest_lon"]} if e["dest_lat"] is not None else None)
        route = json.loads(e["route"]) if e["route"] else None
        out["route"] = [[round(p[0], 6), round(p[1], 6)] for p in simplify(route)] if route else None
        a = json.loads(e["anomaly"]) if e["anomaly"] else None
        if a:
            silent_owner = e["mode"] == "silent" and user == e["owner"]
            out["anomaly"] = {"kind": a["kind"], "since": a["since"], "respond_by": a["respond_by"],
                              "text": NEUTRAL_TEXT if silent_owner else a["text"]}
        else:
            out["anomaly"] = None
        out["progress"] = None
        if e["dest_lat"] is not None and e["status"] == "active":
            track = self._track(e["id"])
            if track:
                ride = self._ride_struct(e)
                rem = remaining_m(track[-1], ride["dest"], ride["route"])
                eta = eta_seconds(track, ride["dest"], ride["route"], time.time())
                out["progress"] = {"remaining_m": round(rem), "eta_minutes": round(eta / 60) if eta else None}

# -*- coding: utf-8 -*-
"""
safety_intel.py - מודיעין בטיחות רחובות + בטיחות מסלול חזויה (שלב 3).

עיקרון-על: **אומדן לעולם לא מוצג כעובדה מאומתת.** לכל פריט: type (official / estimate / user_report),
source, confidence, last_update, verified.

שכבות:
- official: עמודי תאורה עירוניים (context_risk.LightGrid - ת"א, ב"ש), תאונות הולכי רגל עם קואורדינטות
  (למ"ס 2020-2024), מקלטים ציבוריים (ת"א/ב"ש/חיפה) - research_tools/build_safety_layers.py.
  עמוד תאורה רשום ≠ תאורה שפועלת בפועל; תאונת דרכים ≠ פשיעה. נאמר בכל פריט.
- estimate: הערכות מודל מצילומי מסלולי הליכה (street_observations) - "הערכה, לא נבדק".
- user_report: דיווחי משתמשים. unverified → corroborated (3 מדווחים אמינים שונים ב-50 מ', בתוקף) →
  verified (מנהל/ת בדק/ה). לכל דיווח תוקף (הטרדה 30 יום, תאורה 60, תשתית 180). זהות המדווח לא נחשפת.
- פשיעה: קיימת רק ברמת אזור סטטיסטי בלי גבולות גאוגרפיים - **לא זמינה ברמת רחוב** (נאמר במפורש).

בטיחות מסלול: דגימה כל 25 מ' לאורך המסלול; לכל מקטע 100 מ': רחוב חשוך (בלילה, לפי השמש במסלול),
ריכוז תאונות הולכי רגל, דיווחים פעילים (משקל לפי סטטוס). מחזיר ניקוד לכל מסלול, מקטעים בעייתיים עם
נימוק, מהימנות הנתונים (כיסוי), והמלצה על חלופה רק אם היא בטוחה משמעותית ולא ארוכה מדי.

מצבי מסלול (modes) - מחושבים תמיד לכל החלופות (עד 3), הלקוח בוחר איזה להציג:
- fastest: זמן ההליכה הקצר ביותר (זמן מ-OSRM; בלי זמן - אורך / 1.3 מ'/שנ', מסומן "estimate").
- safest: risk_per_km הנמוך ביותר מבין החלופות שארוכות לכל היותר פי SAFEST_MAX_RATIO (1.5) מזמן המהיר.
  שוויון -> פחות risk_points -> הקצר יותר. אם כולן שוות - המהיר.
- balanced: מינימום של  cost = minutes + BALANCED_MIN_PER_POINT * risk_points  (1.0 דק' לנקודת סיכון),
  מאותה קבוצת חלופות (≤ פי 1.5), שוויון -> הקצר יותר.
  כלומר: מוכנים ללכת עד 2 דק' יותר כדי לעקוף מקטע חשוך אחד של 100 מ' בלילה (2 נק'), ועד 4 דק' כדי לעקוף
  מקטע עם דיווח מאומת (עד 4 נק'), אבל לא יותר מפי 1.5 מהמהיר.
OSRM נותן מרחק וזמן בלבד - הוא לעולם לא "ציון בטיחות". הניקוד מגיע רק מהשכבות של LifeGuard. אין מילה "בטוח"
לבד: "בטוח יותר לפי הנתונים" + הרכיבים. חלופה אחת -> "אין חלופה".

מזג אוויר (server/weather.py, Open-Meteo): הקשר בלבד, לא נכלל בניקוד - ראה הנימוק שם.
"""

import gzip
import json
import math
import os
import time

from server import news_layer as NL
from server import risk_factors_layer as RF
from server import routing as ROUTING
from server import weather as WX

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYERS_PATH = os.path.join(HERE, "models", "safety_layers.json.gz")

REPORT_KINDS = {
    "dark_street": ("רחוב חשוך / תאורה לא עובדת", 60, True),
    "broken_light": ("פנס תקול", 60, True),
    "harassment": ("הטרדה", 30, True),
    "unsafe_crossing": ("מעבר חצייה מסוכן", 180, True),
    "isolated_area": ("אזור מבודד / ללא תנועה בלילה", 180, True),
    "safe_place": ("מקום בטוח / פתוח בלילה", 180, False),
}
STATUS_WEIGHT = {"verified": 1.0, "corroborated": 0.7, "unverified": 0.3}
CORROBORATE_REPORTERS = 3
REPORT_RADIUS_M = 50
REPORTS_PER_DAY = 10
REPORTER_MIN_AGE_S = 3 * 86400
SAMPLE_M = 25
SEGMENT_M = 100
MAX_ROUTE_M = 30_000
SAFEST_MAX_RATIO = 1.5            # safest/balanced: עד פי 1.5 מזמן ההליכה של המהיר
BALANCED_MIN_PER_POINT = 1.0      # balanced: cost = minutes + 1.0 * risk_points
# ניקוד מקטע 100 מ' (_score_route). שמות קבועים - דף הניהול מציג אותם מכאן (server/admin_dashboard.py)
W_DARK_SEGMENT = 2.0              # לילה + רוב הדגימות במקטע בלי עמוד תאורה רשום
W_TRAFFIC_HOTSPOT = 0.5           # מוקד תאונות הולכי רגל (פי 2 אם 2+ תאונות קשות)
W_REPORT = 2.0                    # דיווחי משתמשים: W_REPORT * min(סכום משקלי הסטטוס, REPORT_WEIGHT_CAP)
REPORT_WEIGHT_CAP = 2.0
LEVEL_HIGH_PER_KM = 8             # נק' סיכון לק"מ: >= 8 גבוה, >= 3 בינוני
LEVEL_MEDIUM_PER_KM = 3
MODES = ("fastest", "safest", "balanced")
LEVEL_HE = {"high": "גבוה", "medium": "בינוני", "low": "נמוך"}
COMPONENT_HE = {"lighting": "חושך", "traffic": "מוקדי תאונות", "reports": "דיווחים", "nightlife": "בילוי לילי",
                "transit": "תחנה", "news": "תקשורת (לא מאומת)"}

MIGRATION = """
CREATE TABLE street_reports(
    id INTEGER PRIMARY KEY AUTOINCREMENT, reporter TEXT NOT NULL, kind TEXT NOT NULL,
    lat REAL NOT NULL, lon REAL NOT NULL, note TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'unverified' CHECK(status IN ('unverified','verified','rejected')),
    decided_at REAL, decided_note TEXT);
CREATE INDEX IF NOT EXISTS idx_sreport_geo ON street_reports(lat, lon);
CREATE INDEX IF NOT EXISTS idx_sreport_user ON street_reports(reporter, created_at);
"""


def _level(per_km):
    return "high" if per_km >= LEVEL_HIGH_PER_KM else ("medium" if per_km >= LEVEL_MEDIUM_PER_KM else "low")


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(min(1.0, h)))


# מוקדי תאונות הולכי רגל - כלל v2 (research/hotspot_v2.md; ניסוי הוגן ב-research/accident_forecast_v1.md):
# דירוג לפי מספר התאונות **בתא עצמו** (~100 מ'), לא סכום 3x3 (~300 מ') שמדלל את האות. "מוקד" = התאים העליונים
# עד תקציב שטח קבוע מהאזור הנבחן. זה תיעוד של העבר ולא תחזית: כמחצית מהתאונות בשנה נתונה קורות בתאים בלי היסטוריה.
HOTSPOT_RULE = "per_cell_area_budget_v2"
HOTSPOT_AREA_FRACTION = 0.025     # 2.5% מהאזור הנבחן - בערך השטח שהכלל הקודם סימן (2.57%)
HOTSPOT_MIN_COUNT = 1             # נבדק גם 2: אז התקציב לא מתמלא והכלל הפסיד ב-2022/2023 (research/hotspot_v2.md)
SI_PCT = f"{HOTSPOT_AREA_FRACTION:.1%}".replace(".0%", "%")
HOTSPOT_BLOCK = 10                # אזור נבחן = ריבועי 10x10 תאים (0.01° ≈ 1 ק"מ) שהייתה בהם תאונה


def hotspot_cells(cells, frac=HOTSPOT_AREA_FRACTION, min_count=HOTSPOT_MIN_COUNT, block=HOTSPOT_BLOCK):
    """cells: {(y, x): [סה"כ, קשות, בחושך]} -> קבוצת התאים המסומנים כמוקד.
    סדר: מספר התאונות בתא, ואז קשות, ואז תאונות בתאים השכנים (שובר שוויון בלבד), ואז מיקום (דטרמיניסטי)."""
    if not cells:
        return set()
    blocks = {(y // block, x // block) for (y, x) in cells}
    budget = int(frac * len(blocks) * block * block)

    def ring(k):
        y, x = k
        return sum((cells.get((y + dy, x + dx)) or (0,))[0]
                   for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx)
    elig = [k for k, v in cells.items() if v[0] >= min_count]
    elig.sort(key=lambda k: (-cells[k][0], -cells[k][1], -ring(k), k))
    return set(elig[:budget])


class Layers:
    """שכבות רשמיות מקומיות. חסר קובץ = "לא זמין" (לא ניחוש)."""

    def __init__(self, data=None, built_at=None):
        d = data or {}
        acc = d.get("accidents") or {}
        self.acc_cell = acc.get("cell_deg", 0.001)
        self.acc = {tuple(map(int, k.split(":"))): v for k, v in (acc.get("cells") or {}).items()}
        self.acc_years = acc.get("years")
        self.shelters = d.get("shelters") or []
        self.sources = d.get("sources") or []
        self.built_at = built_at or d.get("built_at")
        self.available = bool(data)
        self.acc_filter = acc.get("filter")            # למשל "status_igun=1" (עיגון מדויק בלבד)
        self.hotspots = hotspot_cells(self.acc)
        self.hotspot_min = min((self.acc[k][0] for k in self.hotspots), default=None)

    def _cell(self, lat, lon):
        return int(math.floor(lat / self.acc_cell)), int(math.floor(lon / self.acc_cell))

    def is_hotspot(self, lat, lon):
        return self._cell(lat, lon) in self.hotspots

    def accidents_in_cell(self, lat, lon):
        """(סה"כ, קשות, בחושך) בתא עצמו (~100 מ')."""
        c = self.acc.get(self._cell(lat, lon))
        return tuple(c[:3]) if c else (0, 0, 0)

    def hotspot_info(self):
        return {"rule": HOTSPOT_RULE, "area_fraction": HOTSPOT_AREA_FRACTION, "min_count": HOTSPOT_MIN_COUNT,
                "cells": len(self.hotspots), "min_count_in_hotspots": self.hotspot_min, "filter": self.acc_filter,
                "years": self.acc_years}

    @classmethod
    def load(cls, path=LAYERS_PATH):
        if not os.path.exists(path):
            return cls()
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return cls(json.load(fh))
        except (OSError, ValueError):
            return cls()

    def accidents_near(self, lat, lon):
        """תאונות בתא ובשכנים הישירים (~150 מ'): (סה"כ, קשות, בחושך)."""
        cy, cx = int(math.floor(lat / self.acc_cell)), int(math.floor(lon / self.acc_cell))
        tot = sev = night = 0
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                c = self.acc.get((cy + dy, cx + dx))
                if c:
                    tot, sev, night = tot + c[0], sev + c[1], night + c[2]
        return tot, sev, night

    def shelters_near(self, lat, lon, n=5, max_m=3000):
        out = []
        dlat = max_m / 111000
        for s in self.shelters:
            if abs(s["lat"] - lat) > dlat or abs(s["lon"] - lon) > dlat * 1.3:
                continue
            d = haversine_m((lat, lon), (s["lat"], s["lon"]))
            if d <= max_m:
                out.append(dict(s, distance_m=round(d)))
        return sorted(out, key=lambda s: s["distance_m"])[:n]

    def source(self, sid):
        return next((s for s in self.sources if s.get("id") == sid), {})


def sample_route(route, step=SAMPLE_M):
    """נקודות כל step מ' לאורך הקו (כולל ההתחלה והסוף) + המרחק המצטבר."""
    pts = [(route[0][0], route[0][1], 0.0)]
    done = 0.0
    for a, b in zip(route, route[1:]):
        seg = haversine_m(a, b)
        if seg <= 0:
            continue
        t = step - (done % step) if done % step else step
        while t < seg:
            f = t / seg
            pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, done + t))
            t += step
        done += seg
    last = route[-1]
    if pts[-1][2] < done:
        pts.append((last[0], last[1], done))
    return pts, done


def balanced_cost(r):
    """הנוסחה של מצב "מאוזן": דקות הליכה + 1.0 דק' לכל נקודת סיכון של LifeGuard."""
    return r["duration_s"] / 60 + BALANCED_MIN_PER_POINT * r["risk_points"]


def _less_components(r, base):
    """רכיבי הסיכון ש-r נמוך בהם מ-base (לנימוק "למה")."""
    rc, bc = r.get("components") or {}, base.get("components") or {}
    return [COMPONENT_HE.get(k, k) for k in bc
            if k in rc and bc[k]["per_km"] - rc[k]["per_km"] > 1e-9]


def choose_modes(routes):
    """routes: תוצאות _score_route עם index ו-duration_s. -> {mode: {...}} + caveats. פונקציה טהורה."""
    if not routes:
        return {}
    fastest = min(routes, key=lambda r: (r["duration_s"], r["risk_per_km"], r["index"]))
    cap = fastest["duration_s"] * SAFEST_MAX_RATIO
    eligible = [r for r in routes if r["duration_s"] <= cap + 1e-6]
    too_long = [r for r in routes if r["duration_s"] > cap + 1e-6]
    safest = min(eligible, key=lambda r: (r["risk_per_km"], r["risk_points"], r["duration_s"], r["index"]))
    balanced = min(eligible, key=lambda r: (round(balanced_cost(r), 6), r["duration_s"], r["index"]))
    all_equal = len({r["risk_per_km"] for r in routes}) == 1
    single = len(routes) == 1

    def base(r):
        return {"index": r["index"], "duration_s": round(r["duration_s"]), "minutes": round(r["duration_s"] / 60),
                "length_m": r["length_m"], "level": r["level"], "risk_per_km": r["risk_per_km"],
                "risk_points": r["risk_points"],
                "extra_minutes": round((r["duration_s"] - fastest["duration_s"]) / 60),
                "reliability": (r.get("data") or {}).get("reliability")}

    def vs_fastest(r, label):
        if r is fastest:
            return None
        less = _less_components(r, fastest)
        return (f"{label}: {r['risk_per_km']:g} נק' סיכון/ק\"מ לעומת {fastest['risk_per_km']:g} במהיר"
                + (f" (פחות: {', '.join(less)})" if less else "")
                + f" · +{max(0, round((r['duration_s'] - fastest['duration_s']) / 60))} דק'")

    out = {}
    out["fastest"] = dict(base(fastest), text=f"המהיר ביותר: כ-{round(fastest['duration_s'] / 60)} דק' · "
                                              f"סיכון {LEVEL_HE.get(fastest['level'], fastest['level'])} לפי הנתונים")
    for mode, r in (("safest", safest), ("balanced", balanced)):
        if single:
            text = "אין חלופה - נמצא מסלול הליכה אחד בלבד"
        elif r is fastest and all_equal:
            text = "לכל החלופות אותו ניקוד סיכון לפי הנתונים - נבחר המהיר"
        elif r is fastest:
            text = "המסלול המהיר הוא גם בעל הסיכון הנמוך ביותר לפי הנתונים" if mode == "safest" else \
                   "המסלול המהיר הוא גם המאוזן (זמן + סיכון)"
        else:
            text = vs_fastest(r, "בטוח יותר לפי הנתונים" if r["risk_per_km"] < fastest["risk_per_km"]
                              else "פחות נקודות סיכון בסך הכול")
        if mode == "balanced" and not single:
            text += (f" · ניקוד מאוזן: {round(balanced_cost(r), 1):g} (= {r['duration_s'] / 60:.0f} דק' + "
                     f"{BALANCED_MIN_PER_POINT:g}×{r['risk_points']:g} נק')"
                     + ("" if r is fastest else f", במהיר: {round(balanced_cost(fastest), 1):g}"))
        if mode == "safest" and any(t["risk_per_km"] < safest["risk_per_km"] for t in too_long):
            text += f" · חלופה עם פחות סיכון ארוכה פי יותר מ-{SAFEST_MAX_RATIO:g} - לא נבחרה"
        out[mode] = dict(base(r), text=text)
    caveats = []
    for mode in ("safest", "balanced"):
        r = next(x for x in routes if x["index"] == out[mode]["index"])
        if r is not fastest and (r.get("data") or {}).get("lighting_coverage", 0) + 0.3 < \
                (fastest.get("data") or {}).get("lighting_coverage", 0):
            caveats.append(f"למסלול ה{'בטוח יותר' if mode == 'safest' else 'מאוזן'} יש פחות כיסוי נתוני תאורה - "
                           "ניקוד נמוך יכול לנבוע גם מחוסר נתונים")
    return {"modes": out, "alternatives": len(routes), "single_route": single, "all_equal_risk": all_equal,
            "safest_max_ratio": SAFEST_MAX_RATIO,
            "balanced_formula": f"minutes + {BALANCED_MIN_PER_POINT:g} * risk_points",
            "caveats": sorted(set(caveats))}


class SafetyIntelMixin:
    def _layers(self):
        cur = getattr(self, "_safety_layers", None)
        if cur is None:
            cur = self._safety_layers = Layers.load()
        return cur

    def _risk_factors(self):
        cur = getattr(self, "_rf_layer", None)
        if cur is None:
            cur = self._rf_layer = RF.RiskFactors.load()
        return cur

    def _news(self):
        cur = getattr(self, "_news_layer", None)
        if cur is None:
            cur = self._news_layer = NL.NewsLayer.from_env()
        return cur

    def _weather(self):
        cur = getattr(self, "_weather_src", None)
        if cur is None:
            cur = self._weather_src = WX.Weather.from_env()
        return cur

    def _alt_router(self):
        cur = getattr(self, "_foot_router", None)
        if cur is None:
            cur = self._foot_router = ROUTING.FootAlternatives()
        return cur

    def _light_grid(self):
        try:
            import context_risk
            return context_risk._grid(), os.path.getmtime(context_risk.GRID_PATH) \
                if os.path.exists(context_risk.GRID_PATH) else None
        except ImportError:
            return None, None

    # ---------------------------------------------------------------- דיווחים
    def add_street_report(self, user, kind, lat, lon, note=""):
        if kind not in REPORT_KINDS:
            raise self._err(400, "invalid_kind", "סוג דיווח לא מוכר")
        lat, lon = self._valid_coords(lat, lon)
        now = time.time()
        n = self._q("SELECT COUNT(*) AS n FROM street_reports WHERE reporter=? AND created_at>?",
                    (user, now - 86400), one=True)["n"]
        if n >= REPORTS_PER_DAY:
            raise self._err(429, "too_many_reports", "יותר מדי דיווחים היום")
        # פרטיות: עיגול ל-~10 מ' (לא נחשף מאיפה בדיוק דיווח/ה), הערה קצרה
        cur = self._x("INSERT INTO street_reports(reporter, kind, lat, lon, note, created_at, expires_at) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (user, kind, round(lat, 4), round(lon, 4), str(note or "").strip()[:200], now,
                       now + REPORT_KINDS[kind][1] * 86400))
        return {"id": cur.lastrowid, "kind": kind, "status": "unverified",
                "expires_at": now + REPORT_KINDS[kind][1] * 86400}

    def my_street_reports(self, user):
        return [{"id": r["id"], "kind": r["kind"], "label": REPORT_KINDS[r["kind"]][0], "lat": r["lat"],
                 "lon": r["lon"], "note": r["note"], "created_at": r["created_at"], "expires_at": r["expires_at"],
                 "status": r["status"]}
                for r in self._q("SELECT * FROM street_reports WHERE reporter=? ORDER BY id DESC LIMIT 200", (user,))]

    def delete_street_report(self, user, rid):
        cur = self._x("DELETE FROM street_reports WHERE id=? AND reporter=?", (int(rid), user))
        if not cur.rowcount:
            raise self._err(404, "not_found", "הדיווח לא נמצא")
        return {"ok": True}

    def admin_decide_report(self, rid, approve, note=""):
        cur = self._x("UPDATE street_reports SET status=?, decided_at=?, decided_note=? WHERE id=?",
                      ("verified" if approve else "rejected", time.time(), str(note or "")[:200], int(rid)))
        if not cur.rowcount:
            raise self._err(404, "not_found", "הדיווח לא נמצא")
        return {"ok": True}

    def _active_reports(self, lat, lon, radius_m, now=None):
        now = now or time.time()
        d = radius_m / 111000 + 0.001
        rows = self._q("""SELECT r.*, u.created_at AS acct_created FROM street_reports r JOIN users u
                          ON u.username=r.reporter WHERE r.status!='rejected' AND r.expires_at>? AND
                          r.lat BETWEEN ? AND ? AND r.lon BETWEEN ? AND ?""",
                       (now, lat - d, lat + d, lon - d * 1.3, lon + d * 1.3))
        return [r for r in rows if haversine_m((lat, lon), (r["lat"], r["lon"])) <= radius_m]

    def _report_status(self, r, all_rows, now=None):
        """verified / corroborated (3 מדווחים אמינים שונים, אותו סוג, 50 מ') / unverified."""
        if r["status"] == "verified":
            return "verified", None
        now = now or time.time()
        credible = {x["reporter"] for x in all_rows
                    if x["kind"] == r["kind"] and x["status"] != "rejected"
                    and now - (x["acct_created"] or now) >= REPORTER_MIN_AGE_S
                    and haversine_m((r["lat"], r["lon"]), (x["lat"], x["lon"])) <= REPORT_RADIUS_M}
        if len(credible) >= CORROBORATE_REPORTERS:
            return "corroborated", len(credible)
        return "unverified", len(credible)

    # ---------------------------------------------------------------- מודיעין נקודתי
    def street_intel(self, user, lat, lon, radius_m=150, include_news=False):
        lat, lon = self._valid_coords(lat, lon)
        radius_m = max(30, min(float(radius_m or 150), 1000))
        layers = self._layers()
        items, unavailable = [], []
        grid, grid_mtime = self._light_grid()
        if grid is not None and grid.covered(lat, lon):
            n = grid.poles_near(lat, lon)
            items.append({"layer": "lighting", "type": "official", "source": "עמודי תאורה עירוניים (GIS ת\"א / ב\"ש)",
                          "confidence": "high", "verified": True, "last_update": grid_mtime,
                          "value": {"poles_60m": n},
                          "text": (f"{n} עמודי תאורה רשומים בטווח 60 מ'" if n else
                                   "אין עמוד תאורה רשום בטווח 60 מ'") + " (רישום ≠ תאורה שפועלת)"})
        else:
            unavailable.append({"layer": "lighting", "reason": "אין נתוני תאורה עירוניים לאזור הזה"})
        if layers.available:
            tot, sev, night = layers.accidents_near(lat, lon)
            yrs = layers.acc_years or ["?", "?"]
            hot = layers.is_hotspot(lat, lon)
            precise = layers.acc_filter == "status_igun=1"
            items.append({"layer": "pedestrian_accidents", "type": "official",
                          "source": f"למ\"ס - תאונות הולכי רגל {yrs[0]}-{yrs[1]}", "confidence": "high",
                          "verified": True, "last_update": layers.built_at,
                          "value": {"total": tot, "severe": sev, "dark": night, "hotspot": hot},
                          "text": f"{tot} תאונות הולכי רגל ב-~150 מ' ({sev} קשות/קטלניות, {night} בחושך) - "
                                  "סיכון תנועה, לא פשיעה. "
                                  + (f"הנקודה הזו בין {SI_PCT} מהשטח עם הכי הרבה תאונות. " if hot else "")
                                  + "תיעוד עבר, לא תחזית: כמחצית מהתאונות קורות במקומות בלי תאונות קודמות, "
                                    "כך שהיעדר תאונות רשומות ≠ בטוח. "
                                  + ("רק תאונות עם עיגון מדויק" if precise else "רק תאונות עם קואורדינטות")})
            for s in layers.shelters_near(lat, lon, n=3, max_m=max(radius_m, 1000)):
                src = layers.source(s["source"])
                items.append({"layer": "shelter", "type": "official", "source": src.get("name", s["source"]),
                              "confidence": "high" if "לא אומת" not in src.get("license", "") else "medium",
                              "verified": True, "last_update": layers.built_at,
                              "value": {k: s.get(k) for k in ("lat", "lon", "name", "address", "usable", "distance_m")},
                              "text": f"{s['name']} · {s['distance_m']} מ'" + (f" · {s['address']}" if s.get("address") else "")})
        else:
            unavailable.append({"layer": "pedestrian_accidents", "reason": "שכבת התאונות לא נבנתה בשרת הזה"})
        rf = self._risk_factors()
        area = rf.area_context(lat, lon) if rf.available else None
        if area:
            item = RF.area_item(area, rf.years)
            item["last_update"] = rf.built_at
            items.append(item)
        else:
            unavailable.append({"layer": "crime", "reason": "נתוני פשיעה רשמיים קיימים רק ברמת אזור סטטיסטי, ורק "
                                                             "בערים שנבנתה להן שכבה - לא ברמת רחוב"})
        if rf.available and rf.city_at(lat, lon) is not None:
            f = rf.flags_at(lat, lon)
            city = rf.city_at(lat, lon)
            if f["nightlife"]:
                items.append({"layer": "nightlife", "type": "official" if f.get("nightlife_official") else "estimate",
                              "source": ("רישוי עסקים / אזורי בילוי - עיריית ת\"א" if f.get("nightlife_official") else
                                         f"OpenStreetMap ({rf.osm_snapshot(city) or '?'}) {RF.OSM_ATTRIBUTION}"),
                              "confidence": "medium" if f.get("nightlife_official") else "low", "verified": False,
                              "last_update": rf.built_at, "affects_route": "night",
                              "text": "אזור בילוי פעיל בשעות הלילה (ברים/מועדונים בקרבת מקום). במקומות כאלה נרשמים "
                                      "יותר אירועי אלימות בלילות סוף שבוע"})
            if f["transit"]:
                items.append({"layer": "transit", "type": "estimate",
                              "source": f"OpenStreetMap ({rf.osm_snapshot(city) or '?'}) {RF.OSM_ATTRIBUTION}",
                              "confidence": "low", "verified": False, "last_update": rf.built_at,
                              "affects_route": "evening", "text": "תחנת רכבת/רק\"ל בקרבת מקום"})
        # הערכות מודל (צילומי מסלול) - תמיד "הערכה"
        d = radius_m / 111000
        obs = self._q("""SELECT attributes, observed_at FROM street_observations WHERE kind='user_video' AND lat
                         BETWEEN ? AND ? AND lon BETWEEN ? AND ? ORDER BY observed_at DESC LIMIT 50""",
                      (lat - d, lat + d, lon - d * 1.3, lon + d * 1.3)) if self._has_table("street_observations") else []
        if obs:
            attrs = [json.loads(o["attributes"] or "{}") for o in obs]
            lit = [(a.get("model_scores") or {}).get("lighting") for a in attrs]
            lit = [x for x in lit if isinstance(x, (int, float))]
            model = attrs[0].get("model") or "CLIP"
            items.append({"layer": "street_vision", "type": "estimate",
                          "source": f"הערכת מודל מצילומי מסלול ({model})",
                          "confidence": "low", "verified": False, "last_update": obs[0]["observed_at"],
                          "value": {"observations": len(obs), "lighting_mean": round(sum(lit) / len(lit), 2) if lit else None},
                          "text": f"{len(obs)} תצפיות מצילומי מסלול - הערכה אוטומטית, לא נבדקה"})
        rows = self._active_reports(lat, lon, radius_m)
        all_rows = self._active_reports(lat, lon, radius_m + REPORT_RADIUS_M)
        for r in rows:
            st, n = self._report_status(r, all_rows)
            items.append({"layer": "report", "type": "user_report", "source": "דיווח משתמשים",
                          "confidence": {"verified": "high", "corroborated": "medium"}.get(st, "low"),
                          "verified": st == "verified", "status": st, "reporters": n,
                          "last_update": r["created_at"], "expires_at": r["expires_at"],
                          "value": {"kind": r["kind"], "lat": r["lat"], "lon": r["lon"]},
                          "text": REPORT_KINDS[r["kind"]][0] + {"verified": " (אומת)",
                                                               "corroborated": f" (דווח ע\"י {n} משתמשים)",
                                                               "unverified": " (דיווח יחיד, לא אומת)"}[st]})
        news = self._news()
        if include_news and news.available:
            for it, w in news.near_points(lat, lon, radius_m):
                items.append(NL.NewsLayer.public(it, w))
            for city, its in news.city_context(lat, lon).items():
                cats = sorted({i.get("category_he") or "" for i in its} - {""})
                items.append({"layer": "news_city", "type": "news_report", "verified": False, "confidence": "low",
                              "label": NL.LABEL_HE, "source": "דיווחים בתקשורת (GDELT / RSS מפרסמים)",
                              "last_update": max(NL._ts(i["published_at"]) for i in its),
                              "value": {"city": city, "count": len(its), "categories": cats},
                              "links": [i.get("url") for i in its][:5],
                              "text": ("דיווח אחד" if len(its) == 1 else f"{len(its)} דיווחים")
                                      + f" בתקשורת ב{city} בשבוע האחרון ({', '.join(cats)}) - "
                                      f"ברמת עיר, לא מיקום מדויק. {NL.LABEL_HE}"})
        elif include_news:
            unavailable.append({"layer": "news", "reason": "שכבת הדיווחים מהתקשורת לא מופעלת בשרת הזה"})
        wx = self._weather().get(lat, lon)
        if wx.get("status") == "ok":
            items.append(self._weather_item(wx))
        elif wx.get("status") != "not_applicable":
            unavailable.append({"layer": "weather", "reason": wx.get("reason") or "מזג אוויר לא זמין"})
        return {"lat": lat, "lon": lon, "radius_m": radius_m, "items": items, "unavailable": unavailable,
                "disclaimer": "מידע עזר בלבד. נתונים רשמיים מתארים רישום (עמוד, תאונה), לא מצב בזמן אמת; "
                              "הערכות ודיווחים לא מאומתים מסומנים ככאלה."}

    def _has_table(self, name):
        return bool(self._q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,), one=True))

    @staticmethod
    def _weather_item(wx):
        return {"layer": "weather", "type": "forecast", "source": f"{WX.SOURCE} (מודל מזג אוויר)",
                "confidence": "medium", "verified": False, "last_update": wx.get("fetched_at"),
                "valid_at": wx.get("valid_at"), "affects_route": "context_only", "label": WX.LABEL_HE,
                "attribution": WX.ATTRIBUTION, "value": wx.get("conditions"),
                "hazards": [h["kind"] for h in wx.get("hazards") or []],
                "text": wx.get("text", "") + " (" + WX.LABEL_HE + ")"}

    # ---------------------------------------------------------------- בטיחות מסלול
    def _foot_alternatives(self, a, b):
        """-> (routes, source). routes = [{points, distance_m, duration_s}]. כשל -> 503 עם הסבר (בלי לנחש)."""
        try:
            return self._alt_router().get(a, b)
        except ROUTING.RoutingUnavailable as exc:
            if exc.reason == "disabled":
                msg = "אין שרת ניתוב: OSRM עצמי לא מוגדר והניתוב הציבורי כבוי - שלחו מסלולים מוכנים לבדיקה"
            elif exc.reason == "no_route":
                msg = "שרת הניתוב (OSRM) לא מצא מסלול הליכה בין הנקודות"
            elif exc.reason == "busy":
                msg = "שרת הניתוב הציבורי (OSRM של FOSSGIS) מוגבל לבקשה בשנייה - נסו שוב בעוד רגע"
            else:
                msg = "שרת הניתוב (OSRM) לא זמין כרגע - שלחו מסלולים מוכנים לבדיקה"
            raise self._err(503, "routing_unavailable", msg)

    def _clean_routes(self, routes, source):
        """מסלול = רשימת נקודות, או {points, duration_s?, distance_m?}. -> [(pts, duration_s|None, dur_source)]."""
        clean = []
        for rt in list(routes)[:3]:
            raw, dur = rt, None
            if isinstance(rt, dict):
                raw = rt.get("points") or []
                d = rt.get("duration_s")
                if isinstance(d, (int, float)) and not isinstance(d, bool) and 0 < d < 48 * 3600:
                    dur = float(d)
            pts = [self._valid_coords(p[0], p[1]) for p in raw][:5000]
            if len(pts) < 2:
                raise self._err(400, "invalid_route", "מסלול צריך לפחות 2 נקודות")
            clean.append((pts, dur, source if dur is not None else "estimate"))
        if not clean:
            raise self._err(400, "invalid", "נדרשים מסלולים או נקודת מוצא ויעד")
        return clean

    def route_safety(self, user, routes=None, origin=None, dest=None, at=None, include_news=False):
        at = float(at) if at else time.time()
        source = "client"
        if not routes:
            if not (origin and dest):
                raise self._err(400, "invalid", "נדרשים מסלולים או נקודת מוצא ויעד")
            a = self._valid_coords(origin.get("lat"), origin.get("lon"))
            b = self._valid_coords(dest.get("lat"), dest.get("lon"))
            routes, source = self._foot_alternatives(a, b)
        clean = self._clean_routes(routes, source)
        import context_risk
        mid = clean[0][0][len(clean[0][0]) // 2]
        wx_src = self._weather()
        t0 = time.time()
        wx = wx_src.get(mid[0], mid[1], at=at, wait=0)          # מתחיל להביא ברקע, במקביל לניקוד
        phase = context_risk.phase(context_risk.solar_elevation(mid[0], mid[1], at))
        night = phase == "night"
        news = self._news() if include_news and self._news().available else None
        results = [self._score_route(pts, night, at, phase, news) for pts, _, _ in clean]
        for i, (r, (pts, dur, dsrc)) in enumerate(zip(results, clean)):
            r["index"] = i
            r["duration_s"] = round(dur if dur is not None else r["length_m"] / ROUTING.WALK_SPEED_MPS)
            r["duration_source"] = dsrc
            if source != "client":       # הלקוח לא שלח גאומטריה - מחזירים אותה כדי שיוכל לצייר
                r["geometry"] = [[round(p[0], 6), round(p[1], 6)] for p in pts]
        if wx.get("status") == "pending":
            wx = wx_src.get(mid[0], mid[1], at=at, wait=max(0.0, WX.WAIT_S - (time.time() - t0)))
        sel = choose_modes(results)
        best = min(results, key=lambda r: (r["risk_per_km"], r["length_m"]))
        shortest = min(results, key=lambda r: r["length_m"])
        rec = None
        if best is not shortest and best["risk_per_km"] <= shortest["risk_per_km"] * 0.8 - 0.05 \
                and best["length_m"] <= shortest["length_m"] * 1.3:
            extra = round((best["length_m"] - shortest["length_m"]) / 1.3 / 60)
            rec = {"index": best["index"], "text": f"מסלול {best['index'] + 1} בטוח יותר לפי הנתונים "
                                                    f"(כ-{max(1, extra)} דק' הליכה נוספות)"}
        return {"phase": phase, "at": at, "route_source": source, "routes": results, "recommendation": rec,
                "modes": sel["modes"], "alternatives": sel["alternatives"], "single_route": sel["single_route"],
                "all_equal_risk": sel["all_equal_risk"], "caveats": sel["caveats"],
                "mode_rules": {"safest_max_ratio": SAFEST_MAX_RATIO, "balanced_formula": sel["balanced_formula"],
                               "note": "זמן/מרחק מ-OSRM; סיכון רק משכבות LifeGuard. OSRM אינו ציון בטיחות."},
                "routing_attribution": ROUTING.ROUTING_ATTRIBUTION if source != "client" else None,
                "weather": wx,
                "disclaimer": "הערכה לפי נתונים רשמיים (תאורה רשומה, תאונות הולכי רגל), אזורי בילוי ותחנות "
                              "(רישוי עסקים / OpenStreetMap) ודיווחים. אין נתוני פשיעה ברמת רחוב. לא ערובה לבטיחות.",
                "attribution": RF.OSM_ATTRIBUTION if self._risk_factors().available else None,
                "news": ({"label": NL.LABEL_HE, "attribution": NL.ATTRIBUTION, "bias_note": NL.BIAS_NOTE,
                          # ברמת עיר: הקשר בלבד (לא נקודה ולא ניקוד) - "N דיווחים בעיר בשבוע האחרון"
                          "city_context": [{"city": c, "count": len(its),
                                            "categories": sorted({i.get("category_he") or "" for i in its} - {""})}
                                           for c, its in news.city_context(mid[0], mid[1], now=at).items()]}
                         if news else None)}

    def _score_route(self, route, night, at, phase=None, news=None):
        pts, length = sample_route(route)
        if length > MAX_ROUTE_M:
            raise self._err(400, "route_too_long", "מסלול ארוך מדי לבדיקה (עד 30 ק\"מ)")
        grid, _ = self._light_grid()
        layers = self._layers()
        lat0 = sum(p[0] for p in pts) / len(pts)
        lon0 = sum(p[1] for p in pts) / len(pts)
        span = max(haversine_m((lat0, lon0), (p[0], p[1])) for p in pts) + REPORT_RADIUS_M
        reports = self._active_reports(lat0, lon0, span)
        all_rows = reports
        rep_status = {r["id"]: self._report_status(r, all_rows)[0] for r in reports}
        rf = self._risk_factors()
        evening = night or phase == "twilight"
        weekend = night and RF.weekend_late(at)
        rf_cities = set()
        covered = dark = 0
        segs = {}
        for la, lo, dist in pts:
            si = int(dist // SEGMENT_M)
            s = segs.setdefault(si, {"from_m": si * SEGMENT_M, "dark": 0, "samples": 0, "acc": 0, "severe": 0,
                                     "hot": False, "reports": set(), "lat": la, "lon": lo, "nightlife": False, "transit": False,
                                     "news": 0.0})
            s["samples"] += 1
            if grid is not None and grid.covered(la, lo):
                covered += 1
                if grid.poles_near(la, lo) == 0:
                    dark += 1
                    s["dark"] += 1
            if layers.available and layers.is_hotspot(la, lo):
                # כלל v2: מוקד = התא עצמו (~100 מ') בין התאים המובילים; לא סכום שכנים (~300 מ') שמדלל את האות
                tot, sev, _ = layers.accidents_in_cell(la, lo)
                s["hot"] = True
                s["acc"] = max(s["acc"], tot)
                s["severe"] = max(s["severe"], sev)
            if rf.available and evening:
                f = rf.flags_at(la, lo)
                if f["covered"]:          # גורם בלי כיסוי לא נספר (כמו תאורה)
                    rf_cities.add(rf.city_at(la, lo))
                    s["nightlife"] |= f["nightlife"]
                    s["transit"] |= f["transit"]
            if news is not None:
                s["news"] = max(s["news"], news.segment_weight(la, lo, at))
            for r in reports:
                if REPORT_KINDS[r["kind"]][2] and haversine_m((la, lo), (r["lat"], r["lon"])) <= REPORT_RADIUS_M:
                    s["reports"].add(r["id"])
        total_risk, flagged = 0.0, []
        comp = {"lighting": 0.0, "traffic": 0.0, "reports": 0.0, "nightlife": 0.0, "transit": 0.0}
        if news is not None:
            comp["news"] = 0.0
        for si in sorted(segs):
            s = segs[si]
            risk, why = 0.0, []
            if night and s["dark"] >= max(1, s["samples"] // 2):
                risk += W_DARK_SEGMENT
                comp["lighting"] += W_DARK_SEGMENT
                why.append("רחוב חשוך (אין עמודי תאורה רשומים)")
            if s["hot"]:
                # תאונות = סיכון תנועה, לא תקיפה: משקל חצי ביחס לחושך/דיווחים
                t = W_TRAFFIC_HOTSPOT * (1 + (1 if s["severe"] >= 2 else 0))
                risk += t
                comp["traffic"] += t
                yrs = layers.acc_years or ["?", "?"]
                why.append(f"בין המקומות עם הכי הרבה תאונות הולכי רגל ({s['acc']} בטווח ~100 מ' ב-{yrs[0]}–{yrs[1]}; "
                           "תיעוד עבר, לא תחזית)" + (" כולל קשות" if s["severe"] >= 2 else ""))
            if night and s["nightlife"]:
                # חלש מחושך במכוון: הראיות חלשות מה-RCT של התאורה; האות המקומי ~10% לסטיית תקן
                t = RF.W_NIGHTLIFE_WEEKEND if weekend else RF.W_NIGHTLIFE
                risk += t
                comp["nightlife"] += t
                why.append("אזור בילוי לילי פעיל (ברים/מועדונים)" + (" - לילה של סוף שבוע" if weekend else ""))
            if evening and s["transit"]:
                risk += RF.W_TRANSIT
                comp["transit"] += RF.W_TRANSIT
                why.append("תחנת רכבת/רק\"ל (גורם ניסיוני)")
            if s["news"]:
                # דיווח בתקשורת ברמת רחוב/שכונה: 0.1 (פחות מדיווח משתמש לא מאומת), לא מאומת
                risk += s["news"]
                comp["news"] += s["news"]
                why.append("דווח בתקשורת על אירוע אלימות בקרבת מקום (לא מאומת)")
            w = sum(STATUS_WEIGHT[rep_status[i]] for i in s["reports"])
            if w:
                risk += W_REPORT * min(w, REPORT_WEIGHT_CAP)
                comp["reports"] += W_REPORT * min(w, REPORT_WEIGHT_CAP)
                kinds = {next(r["kind"] for r in reports if r["id"] == i) for i in s["reports"]}
                why.append("דיווחים: " + ", ".join(REPORT_KINDS[k][0] for k in kinds)
                           + ("" if all(rep_status[i] != "unverified" for i in s["reports"]) else " (חלקם לא מאומתים)"))
            total_risk += risk
            if risk:
                flagged.append({"from_m": s["from_m"], "to_m": s["from_m"] + SEGMENT_M, "risk": risk,
                                "lat": round(s["lat"], 6), "lon": round(s["lon"], 6), "reasons": why})
        km = max(length / 1000, 0.1)
        # הרמה המוצגת: גורם ניסיוני (תחנה) לא נספר; בילוי לבדו לכל היותר "בינוני" (הראיות לו חלשות מחושך) -
        # שניהם עדיין משפיעים על השוואת המסלולים (risk_per_km)
        news_pts = comp.get("news", 0.0)
        level_per_km = ((total_risk - comp["transit"] - comp["nightlife"] - news_pts) / km
                        + min(comp["nightlife"] / km, RF.NIGHTLIFE_LEVEL_CAP_PER_KM)
                        + min(news_pts / km, NL.NEWS_LEVEL_CAP_PER_KM))
        coverage = covered / len(pts)
        reliability = "high" if coverage >= 0.8 and layers.available else ("partial" if coverage >= 0.3 else "low")
        return {"length_m": round(length), "risk_points": round(total_risk, 2), "risk_per_km": round(total_risk / km, 2),
                "level": _level(level_per_km),
                "components": {k: {"per_km": round(v / km, 2),
                                   "level": _level(v / km)}
                               for k, v in comp.items()},
                "segments": sorted(flagged, key=lambda s: -s["risk"])[:10],
                "data": {"lighting_coverage": round(coverage, 2), "dark_share": round(dark / len(pts), 2),
                         "accidents_layer": layers.available, "reports_considered": len(reports),
                         "crime": "לא זמין ברמת רחוב", "reliability": reliability,
                         # OSM לא מעלה את מהימנות המסלול; רק מציין איפה גורמי הבילוי/תחנות נבדקו
                         "factors_coverage": sorted({rf.coverage_label(c) for c in rf_cities}) if rf_cities else
                         (["none"] if evening else ["not_applicable_daytime"])}}

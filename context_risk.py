# -*- coding: utf-8 -*-
"""
context_risk.py - הקשר של זמן ותאורה להערכת הסכנה: בלילה, וברחוב חשוך, אותו צליל חשוד מסוכן יותר.

עקרונות:
- **הקשר, לא ראיה.** לילה וחושך הם נסיבות - לא הוכחה שקורה משהו. לכן הם מעלים את הניקוד (ובעקבותיו
  את הרמה, את קיצור חלון האימות ואת העדיפות בתור המתנדבים), אבל לא נספרים כ"מקור ראיה בלתי תלוי",
  ולעולם לא מספיקים לבדם ל-SOS אוטומטי (risk_module).
- **לילה לפי השמש, לא לפי השעון.** גובה השמש מחושב למיקום ולתאריך (אלגוריתם NOAA מקוצר, דיוק של
  פחות מחצי מעלה): בדצמבר בתל אביב חשוך כבר ב-17:00, ביוני רק אחרי 20:00. דמדומים = השמש בין 0 ל-6
  מעלות מתחת לאופק.
- **תאורה לפי נתונים, לא לפי ניחוש.** עמודי תאורה עירוניים (תל אביב ובאר שבע - research_tools/
  build_light_grid.py). באזור בלי נתונים: "לא ידוע", בלי תוספת. מיקום משוער (IP) לא משמש לתאורה -
  רק לזמן (רדיוס של קילומטרים לא אומר כלום על רחוב).
"""

import gzip
import json
import math
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_PATH = os.path.join(HERE, "models", "street_lights_grid.json.gz")
CELL_DEG = 0.0005             # ~50 מ'
COVER_DEG = 0.01              # ~1 ק"מ: אזורים שיש בהם נתוני עמודים בכלל
LIGHT_RADIUS_M = 60
FRESH_SECONDS = 600           # מיקום לתאורה: עד 10 דקות

SCORE_NIGHT = 1               # לילה (השמש יותר מ-6 מעלות מתחת לאופק)
SCORE_DARK_STREET = 1         # לילה + אין עמוד תאורה בטווח 60 מ' (באזור שיש בו נתונים)
SCORE_LATE_NIGHT = 0          # 00:00-05:00 - נרשם בנימוקים; בלי ניקוד נוסף עד שיהיו נתוני אמת


def solar_elevation(lat, lon, epoch):
    """גובה השמש במעלות (NOAA מקוצר). חיובי = מעל האופק."""
    d = epoch / 86400.0 + 2440587.5 - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    eps = math.radians(23.439 - 0.00000036 * d)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = math.radians(gmst * 15 + lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def phase(elevation):
    if elevation > 0:
        return "day"
    if elevation > -6:
        return "twilight"
    return "night"


class LightGrid:
    """ספירת עמודי תאורה ברשת של ~50 מ' (נבנה מראש מנתונים עירוניים פתוחים)."""

    def __init__(self, cells, cover):
        self.cells, self.cover = cells, cover

    @classmethod
    def load(cls, path=GRID_PATH):
        if not os.path.exists(path):
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            d = json.load(fh)
        return cls({tuple(map(int, k.split(":"))): v for k, v in d["cells"].items()},
                   {tuple(map(int, k.split(":"))) for k in d["cover"]})

    @staticmethod
    def build(points):
        cells, cover = {}, set()
        for lat, lon in points:
            k = (int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG)))
            cells[k] = cells.get(k, 0) + 1
            cover.add((int(math.floor(lat / COVER_DEG)), int(math.floor(lon / COVER_DEG))))
        return LightGrid(cells, cover)

    def to_json(self):
        return {"cell_deg": CELL_DEG, "cover_deg": COVER_DEG,
                "cells": {f"{a}:{b}": n for (a, b), n in self.cells.items()},
                "cover": [f"{a}:{b}" for a, b in sorted(self.cover)]}

    def covered(self, lat, lon):
        """יש נתוני עמודים באזור (או באזורים הסמוכים). פארק חשוך בתוך העיר (אזור של ק"מ בלי אף עמוד)
        הוא בדיוק המקום ש"חשוך" אמור לזהות - ולא "אין נתונים"."""
        cy, cx = int(math.floor(lat / COVER_DEG)), int(math.floor(lon / COVER_DEG))
        return any((cy + dy, cx + dx) in self.cover for dy in (-1, 0, 1) for dx in (-1, 0, 1))

    def poles_near(self, lat, lon, radius_m=LIGHT_RADIUS_M):
        """הערכת מספר עמודים ברדיוס (לפי תאים שמרכזם בטווח)."""
        cy, cx = int(math.floor(lat / CELL_DEG)), int(math.floor(lon / CELL_DEG))
        reach = int(math.ceil(radius_m / (CELL_DEG * 111320))) + 1
        n = 0
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                c = self.cells.get((cy + dy, cx + dx))
                if not c:
                    continue
                clat, clon = (cy + dy + 0.5) * CELL_DEG, (cx + dx + 0.5) * CELL_DEG
                dm = math.hypot((clat - lat) * 111320, (clon - lon) * 111320 * math.cos(math.radians(lat)))
                if dm <= radius_m + CELL_DEG * 111320 / 2:
                    n += c
        return n


_GRID = {"loaded": False, "grid": None}


def _grid():
    if not _GRID["loaded"]:
        _GRID["loaded"] = True
        try:
            _GRID["grid"] = LightGrid.load()
        except Exception:  # noqa: BLE001 - קובץ פגום = אין נתוני תאורה (לא תקלה)
            _GRID["grid"] = None
    return _GRID["grid"]


def assess(location=None, now=None, local_hour=None, grid=None):
    """
    location: {"lat","lon","is_precise"} או None. -> dict:
      phase, elevation, lighting (lit/dim/dark/unknown), poles, score, reasons
    """
    now = now if now is not None else time.time()
    out = {"phase": None, "elevation": None, "lighting": "unknown", "poles": None, "score": 0, "reasons": []}
    if not location or location.get("lat") is None:
        return out
    lat, lon = float(location["lat"]), float(location["lon"])
    elev = solar_elevation(lat, lon, now)
    ph = phase(elev)
    out.update(phase=ph, elevation=round(elev, 1))
    hour = local_hour if local_hour is not None else time.localtime(now).tm_hour
    if ph == "night":
        out["score"] += SCORE_NIGHT
        out["reasons"].append("לילה (חשוך בחוץ) - הסיכון גבוה יותר")
        if 0 <= hour < 5:
            out["score"] += SCORE_LATE_NIGHT
            out["reasons"].append("שעות הלילה המאוחרות")
    elif ph == "twilight":
        out["reasons"].append("דמדומים")
    g = grid if grid is not None else _grid()
    # תאורה רק ממיקום מדויק *וטרי*: רחוב מלפני שעה לא אומר כלום על הרחוב שבו נמצאים עכשיו
    fresh = now - float(location.get("timestamp") or now) <= FRESH_SECONDS
    if g is not None and location.get("is_precise") and fresh and g.covered(lat, lon):
        n = g.poles_near(lat, lon)
        out["poles"] = n
        out["lighting"] = "dark" if n == 0 else ("dim" if n == 1 else "lit")
        if ph == "night" and n == 0:
            out["score"] += SCORE_DARK_STREET
            out["reasons"].append(f"אין עמודי תאורה עירוניים בטווח {LIGHT_RADIUS_M} מ' - רחוב חשוך")
        elif ph == "night" and n >= 2:
            out["reasons"].append("רחוב מואר (לפי עמודי התאורה העירוניים)")
    return out

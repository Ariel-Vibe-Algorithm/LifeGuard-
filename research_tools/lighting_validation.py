# -*- coding: utf-8 -*-
"""
lighting_validation.py - אימות סיווג התאורה ברחובות מול אמת-מידה אובייקטיבית: עמודי התאורה
העירוניים (תל אביב 52K, באר שבע 30K - research_tools/israel_layer.py).

השאלה המחקרית: האם מה שהמערכת "רואה" (בהירות שנמדדה בסרטון, הערכת CLIP "מואר") מתאים לכמות
עמודי התאורה סביב הנקודה? אם לא - אין להשתמש בסיווג התאורה, ובטח לא להציג אותו.

שיטה:
- לכל תצפית לילה (19:00-05:00 שעון ישראל) סופרים עמודי תאורה ברדיוס R (ברירת מחדל 30 מ').
- מתאם ספירמן (דירוגי, עמיד לחריגים) בין צפיפות העמודים לבין: בהירות נמדדת, הערכת CLIP.
- רווח סמך 95% ב-bootstrap **לפי הליכה** (תצפיות מאותה הליכה תלויות זו בזו).
- השוואת קבוצות: בלי עמוד בטווח מול 2+ עמודים.
מגבלות: עמוד קיים != עמוד דולק; נתוני ה-GIS אינם מלאים; תצפיות לילה רק מאזורים עם נתוני עמודים.
"""

import csv
import math
import os
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DERIVED = os.path.join(os.path.dirname(HERE), "research", "samples", "israel", "derived")
LIGHT_FILES = ("streetlights_tel_aviv.csv", "streetlights_beer_sheva.csv")
ISRAEL_UTC_OFFSET_H = 2          # שעון חורף; בקיץ 3 - הבדל זניח לסינון "לילה" גס


class LightIndex:
    """אינדקס רשת פשוט (~50 מ') לספירת עמודים ברדיוס - בלי תלות חיצונית."""

    def __init__(self, points, cell_deg=0.0005):
        self.cell = cell_deg
        self.grid = defaultdict(list)
        for lat, lon in points:
            self.grid[(int(lat // cell_deg), int(lon // cell_deg))].append((lat, lon))
        self.n = len(points)

    @classmethod
    def from_csv(cls, paths=None):
        pts = []
        for p in paths or [os.path.join(DERIVED, f) for f in LIGHT_FILES]:
            if not os.path.exists(p):
                continue
            with open(p, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    try:
                        pts.append((float(r["lat"]), float(r["lon"])))
                    except (TypeError, ValueError, KeyError):
                        continue
        return cls(pts)

    def count_within(self, lat, lon, radius_m=30.0):
        cy, cx = int(lat // self.cell), int(lon // self.cell)
        reach = int(math.ceil(radius_m / (self.cell * 111320 * max(0.2, math.cos(math.radians(lat)))))) + 1
        n = 0
        for dy in range(-reach, reach + 1):
            for dx in range(-reach, reach + 1):
                for plat, plon in self.grid.get((cy + dy, cx + dx), ()):
                    if _dist_m(lat, lon, plat, plon) <= radius_m:
                        n += 1
        return n

    def covers(self, lat, lon, margin_m=300.0):
        """האם יש בכלל נתוני עמודים באזור (אחרת אפס עמודים = "אין נתונים", לא "חשוך")."""
        return self.count_within(lat, lon, margin_m) > 0


def _dist_m(a1, o1, a2, o2):
    la1, lo1, la2, lo2 = map(math.radians, (a1, o1, a2, o2))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def is_night(epoch):
    if epoch is None:
        return False
    hour = (int(epoch // 3600) + ISRAEL_UTC_OFFSET_H) % 24
    return hour >= 19 or hour < 5


def spearman(x, y):
    from scipy.stats import rankdata
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 3:
        return None
    # דירוג ממוצע לערכים שווים: ספירות עמודים חוזרות הרבה (0,1,2...) - שבירת שוויון שרירותית מעוותת
    rx, ry = rankdata(x), rankdata(y)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def bootstrap_by_group(x, y, groups, n=1000, seed=0):
    """רווח סמך 95% למתאם, כשדוגמים *הליכות שלמות* (לא נקודות בודדות)."""
    rng = np.random.default_rng(seed)
    x, y, groups = np.asarray(x, float), np.asarray(y, float), np.asarray(groups)
    uniq = np.unique(groups)
    if len(uniq) < 3:
        return None
    idx = {g: np.flatnonzero(groups == g) for g in uniq}
    vals = []
    for _ in range(n):
        pick = np.concatenate([idx[g] for g in rng.choice(uniq, len(uniq), replace=True)])
        v = spearman(x[pick], y[pick])
        if v is not None:
            vals.append(v)
    return (float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))) if vals else None


def validate(observations, index, radius_m=30.0):
    """observations: dicts עם lat, lon, observed_at, luminance, attributes.lighting, walk_id."""
    rows = []
    skipped = {"not_night": 0, "no_location": 0, "no_light_data": 0}
    for o in observations:
        if o.get("lat") is None:
            skipped["no_location"] += 1
            continue
        if not is_night(o.get("observed_at")):
            skipped["not_night"] += 1
            continue
        if not index.covers(o["lat"], o["lon"]):
            skipped["no_light_data"] += 1
            continue
        rows.append({"poles": index.count_within(o["lat"], o["lon"], radius_m), "lum": o.get("luminance"),
                     "clip": (o.get("attributes") or {}).get("lighting"), "walk": str(o.get("walk_id"))})
    out = {"n": len(rows), "walks": len({r["walk"] for r in rows}), "radius_m": radius_m, "skipped": skipped}
    if len(rows) < 3:
        out["warning"] = "אין מספיק תצפיות לילה באזורים עם נתוני עמודי תאורה"
        return out
    poles = [r["poles"] for r in rows]
    groups = [r["walk"] for r in rows]
    for key in ("lum", "clip"):
        vals = [r[key] for r in rows]
        ok = [i for i, v in enumerate(vals) if v is not None]
        out[key] = {"spearman": spearman([poles[i] for i in ok], [vals[i] for i in ok]),
                    "ci95_by_walk": bootstrap_by_group([poles[i] for i in ok], [vals[i] for i in ok],
                                                       [groups[i] for i in ok])}
        dark = [vals[i] for i in ok if poles[i] == 0]
        lit = [vals[i] for i in ok if poles[i] >= 2]
        out[key]["mean_no_pole"] = float(np.mean(dark)) if dark else None
        out[key]["mean_2plus_poles"] = float(np.mean(lit)) if lit else None
    if out["walks"] < 5:
        out["warning"] = f"רק {out['walks']} הליכות - רווח הסמך לא אמין"
    return out

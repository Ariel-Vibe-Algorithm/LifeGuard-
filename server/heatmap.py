# -*- coding: utf-8 -*-
"""
heatmap.py - "מפת סביבה": גורמי סביבה ברמת רחוב לשכבת מפה (design/DESIGN_SYSTEM_HE.md).

לא "מפת סיכון שכונתית" ולא אזורים באדום: נתוני פשיעה קיימים רק ברמת אזור סטטיסטי ומוטים (מבקרים, דיווח),
ותיוג שכונה כ"מסוכנת" יוצר סטיגמה (research/risk_factors_report.md §5.4). מה שכן - לכל תא של ~100 מ':
- lighting: עמודי תאורה רשומים בטווח 60 מ' (רק איפה שיש נתוני GIS עירוניים; אחרת None = "אין נתונים").
  רישום ≠ תאורה שעובדת בפועל.
- accidents: תאונות הולכי רגל בתא ובשכנים (למ"ס, 5 שנים) + האם התא עצמו בין 2.5% מהשטח עם הכי הרבה תאונות
  (כלל v2, research/hotspot_v2.md). תיעוד עבר, לא תחזית.
- nightlife: אזור בילוי פעיל - רק בלילה (ביום None), אותו כלל כמו בדירוג המסלולים.
- reports: דיווחי משתמשים פעילים שאומתו או אוששו (דיווח יחיד לא מאומת לא מוצג על המפה).
לכל שכבה: מקור, סוג (רשמי / הערכה / דיווח משתמשים), מועד עדכון. אין ניקוד מסכם לשכונה.
"""

import time

from server import risk_factors_layer as RF

CELL_DEG = 0.001                 # ~111 x ~94 מ' בישראל
MAX_SPAN_DEG = 0.03              # ~3.3 x ~2.8 ק"מ לבקשה - מהיר ולא "מפה של כל העיר"
MAX_CELLS = 1200


class HeatmapMixin:
    def map_environment(self, user, bbox, at=None):
        try:
            s, w, n, e = (float(x) for x in str(bbox).split(","))
        except (TypeError, ValueError):
            raise self._err(400, "invalid_bbox", "bbox צריך להיות minlat,minlon,maxlat,maxlon")
        s, n = min(s, n), max(s, n)
        w, e = min(w, e), max(w, e)
        self._valid_coords(s, w)
        self._valid_coords(n, e)
        if n - s > MAX_SPAN_DEG or e - w > MAX_SPAN_DEG:
            raise self._err(400, "bbox_too_large", "האזור גדול מדי - התקרב/י במפה (עד ~3 ק\"מ)")
        at = float(at) if at else time.time()
        import context_risk
        phase = context_risk.phase(context_risk.solar_elevation((s + n) / 2, (w + e) / 2, at))
        night = phase == "night"
        grid, grid_mtime = self._light_grid()
        layers = self._layers()
        rf = self._risk_factors()
        reports = self._active_reports((s + n) / 2, (w + e) / 2, radius_m=2500) if hasattr(self, "_active_reports") else []
        rep_ok = []
        for r in reports:
            st, _n = self._report_status(r, reports)
            if st in ("verified", "corroborated") and s <= r["lat"] <= n and w <= r["lon"] <= e:
                rep_ok.append((r["lat"], r["lon"], st, r["kind"]))
        cells, lit_cov, acc_on, nl_on = [], False, layers.available, rf.available
        y0, x0 = int(s // CELL_DEG), int(w // CELL_DEG)
        y1, x1 = int(n // CELL_DEG), int(e // CELL_DEG)
        if (y1 - y0 + 1) * (x1 - x0 + 1) > MAX_CELLS:
            raise self._err(400, "bbox_too_large", "האזור גדול מדי - התקרב/י במפה")
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                lat, lon = (y + 0.5) * CELL_DEG, (x + 0.5) * CELL_DEG
                cell = {"lat": round(lat, 5), "lon": round(lon, 5)}
                if grid is not None and grid.covered(lat, lon):
                    lit_cov = True
                    cell["lighting"] = grid.poles_near(lat, lon)
                else:
                    cell["lighting"] = None
                if acc_on:
                    tot, sev, _dark = layers.accidents_near(lat, lon)
                    cell["accidents"] = tot
                    cell["accident_hotspot"] = layers.is_hotspot(lat, lon)     # כלל v2 - התא עצמו
                else:
                    cell["accidents"] = None
                if nl_on and night:
                    f = rf.flags_at(lat, lon)
                    cell["nightlife"] = bool(f["nightlife"]) if f["covered"] else None
                else:
                    cell["nightlife"] = None
                cell["reports"] = sum(1 for (rl, ro, _st, _k) in rep_ok
                                      if abs(rl - lat) <= CELL_DEG / 2 and abs(ro - lon) <= CELL_DEG / 2)
                cells.append(cell)
        return {
            "bbox": [s, w, n, e], "cell_deg": CELL_DEG, "at": at, "phase": phase, "cells": cells,
            "layers": {
                "lighting": {"available": lit_cov, "kind": "official", "label": "עמודי תאורה רשומים (60 מ')",
                             "source": "GIS עירוני - ת\"א / באר שבע", "updated": grid_mtime,
                             "note": "רישום עמוד ≠ תאורה שעובדת בפועל; אין נתונים מחוץ לערים המכוסות"},
                "accidents": {"available": acc_on, "kind": "official", "label": "תאונות הולכי רגל (~150 מ', 5 שנים)",
                              "source": "למ\"ס", "updated": layers.built_at, "hotspot": layers.hotspot_info(),
                              "note": "סיכון תנועה, לא פשיעה. תיעוד עבר, לא תחזית - היעדר סימון ≠ בטוח"},
                "nightlife": {"available": bool(nl_on and night), "kind": "official+estimate",
                              "label": "אזור בילוי פעיל (בלילה בלבד)", "source": "רישוי עסקים ת\"א / OpenStreetMap",
                              "updated": rf.built_at, "attribution": RF.OSM_ATTRIBUTION,
                              "note": None if night else "מוצג רק בשעות הלילה"},
                "reports": {"available": True, "kind": "user_report", "label": "דיווחים מאומתים או מאוששים",
                            "source": "דיווחי משתמשים", "note": "דיווח יחיד לא מאומת לא מוצג על המפה"},
            },
            "disclaimer": "גורמי סביבה ברמת רחוב - לא ציון סכנה לשכונה. אין נתוני פשיעה ברמת רחוב.",
        }

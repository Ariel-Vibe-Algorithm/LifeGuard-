# -*- coding: utf-8 -*-
"""
build_safety_layers.py - שכבות נתונים רשמיים לשרת: תאונות הולכי רגל (רשת) + מקלטים ציבוריים.

  python -m research_tools.build_safety_layers            # בונה models/safety_layers.json.gz
  python -m research_tools.build_safety_layers --offline  # רק מה-cache המקומי
  python -m research_tools.build_safety_layers --accidents-only  # רק שכבת התאונות, בלי רשת (שומר מקלטים קיימים)

תאונות: רק STATUS_IGUN=1 (עיגון מדויק). סטטוסים אחרים מעוגנים כנראה למרכז רחוב/יישוב - 678 תאונות מסטטוס 2 נופלות על
214 נקודות בלבד, ו"מוקד" שם הוא ארטיפקט של עיגון (research/accident_forecast_v1.md). הכמות שהוצאה נרשמת בקובץ.

מקורות (כולם רשמיים/עירוניים; לכל רשומה נשמר המקור):
- תאונות הולכי רגל עם קואורדינטות, למ"ס (קובץ derived שנבנה ע"י israel_layer build) - רשת 100 מ':
  מספר תאונות, קשות/קטלניות, בחושך. **לא** "פשיעה" ולא "בטיחות אישית" - סיכון תנועה להולכי רגל.
- מקלטים ציבוריים:
  - תל אביב-יפו GIS שכבה 592 (כולל "כשיר לשימוש" - pail)
  - באר שבע data.gov.il shelters-br7 (Other/Open)
  - חיפה GIS "מקומות מיגון" (Haifa_Sec_Public/MapServer/1) - **רישיון לא אומת**, מסומן בכל רשומה
  ירושלים (ODbL, XLSX בלבד) - לא נטען אוטומטית; מסומן כ"לא זמין" בדוח.
התוצר לא נכנס ל-git (רישיונות השכבות העירוניות לא אומתו כולם) - כמו street_lights_grid.
"""

import argparse
import gzip
import json
import math
import os
import time
import urllib.parse

from research_tools import israel_layer as IL

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "models", "safety_layers.json.gz")
ACCIDENTS = os.path.join(HERE, "research", "samples", "israel", "derived", "pedestrian_accidents_2020-2024.geojson")
CELL_DEG = 0.001
PRECISE_STATUS = (1,)

TLV_SHELTERS = "https://gisn.tel-aviv.gov.il/arcgis/rest/services/IView2/MapServer/592"
HAIFA_SHELTERS = "https://gisserver.haifa.muni.il/arcgiswebadaptor/rest/services/PublicSite/Haifa_Sec_Public/MapServer/1"
BR7_PACKAGE = "shelters-br7"


def build_accidents(path=ACCIDENTS):
    with open(path, encoding="utf-8") as fh:
        gj = json.load(fh)
    cells, years, excluded = {}, set(), {}
    for f in gj["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        if p.get("status_igun") not in PRECISE_STATUS:
            excluded[str(p.get("status_igun"))] = excluded.get(str(p.get("status_igun")), 0) + 1
            continue
        k = f"{int(math.floor(lat / CELL_DEG))}:{int(math.floor(lon / CELL_DEG))}"
        c = cells.setdefault(k, [0, 0, 0])
        c[0] += 1
        if p.get("severity_code") in (1, 2):
            c[1] += 1
        if p.get("day_night") == "לילה":
            c[2] += 1
        years.add(p.get("year"))
    yrs = sorted(y for y in years if y)
    kept = sum(c[0] for c in cells.values())
    return {"cell_deg": CELL_DEG, "cells": cells, "years": [yrs[0], yrs[-1]] if yrs else None,
            "count": kept, "filter": "status_igun=1", "excluded_by_status": excluded,
            "total_in_source": len(gj["features"])}


def _arcgis_points(layer, source, fields, order, log):
    feats = IL.arcgis_query_all(layer, source, fields, order, log=log)
    out = []
    for f in feats:
        g = f.get("geometry") or {}
        if g.get("y") is None:
            continue
        out.append((g["y"], g["x"], f.get("attributes") or {}))
    return out


def fetch_shelters(log=print):
    shelters, sources = [], []
    try:
        pts = _arcgis_points(TLV_SHELTERS, "tlv_gis", "*", "oid_mitkan", log)
        for lat, lon, a in pts:
            usable = a.get("pail")
            shelters.append({"lat": round(lat, 6), "lon": round(lon, 6), "city": "תל אביב-יפו",
                             "name": str(a.get("t_sug") or "מקלט ציבורי"),
                             "address": str(a.get("Full_Address") or "").strip(),
                             "usable": None if usable in (None, "") else str(usable),
                             "source": "tlv_gis_592"})
        sources.append({"id": "tlv_gis_592", "name": "עיריית תל אביב-יפו - GIS שכבה 592 (מקלטים)",
                        "url": TLV_SHELTERS, "license": "TLV Open Data (לא אומת לשכבות gisn)", "count": len(pts)})
    except Exception as exc:  # noqa: BLE001
        sources.append({"id": "tlv_gis_592", "error": str(exc)[:200], "count": 0})
    try:
        pkg = IL.http_get_json("https://data.gov.il/api/3/action/package_show?" +
                               urllib.parse.urlencode({"id": BR7_PACKAGE}), "br7_shelters")
        res = next(r for r in pkg["result"]["resources"] if r.get("datastore_active"))
        recs = IL.ckan_search_all(res["id"], "br7_shelters", log=log)
        for r in recs:
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            shelters.append({"lat": round(lat, 6), "lon": round(lon, 6), "city": "באר שבע",
                             "name": f"מקלט {r.get('name') or ''}".strip(), "address": "", "usable": None,
                             "source": "br7_data_gov_il"})
        sources.append({"id": "br7_data_gov_il", "name": "עיריית באר שבע - data.gov.il shelters-br7",
                        "url": "https://data.gov.il/dataset/shelters-br7", "license": "Other (Open)",
                        "count": len(recs), "updated": res.get("last_modified")})
    except Exception as exc:  # noqa: BLE001
        sources.append({"id": "br7_data_gov_il", "error": str(exc)[:200], "count": 0})
    try:
        pts = _arcgis_points(HAIFA_SHELTERS, "haifa_gis", "*", "OBJECTID", log)
        for lat, lon, a in pts:
            shelters.append({"lat": round(lat, 6), "lon": round(lon, 6), "city": "חיפה",
                             "name": str(a.get("Migun_Type") or "מקום מיגון"),
                             "address": str(a.get("Migun_FullAddress") or "").strip(), "usable": None,
                             "source": "haifa_gis_sec1"})
        sources.append({"id": "haifa_gis_sec1", "name": "עיריית חיפה - GIS מקומות מיגון",
                        "url": HAIFA_SHELTERS, "license": "לא אומת", "count": len(pts)})
    except Exception as exc:  # noqa: BLE001
        sources.append({"id": "haifa_gis_sec1", "error": str(exc)[:200], "count": 0})
    sources.append({"id": "jerusalem_datacity", "name": "עיריית ירושלים - public-shelters (ODbL)",
                    "url": "https://jerusalem.datacity.org.il/dataset/public-shelters", "count": 0,
                    "error": "XLSX בלבד - לא נטען אוטומטית בגרסה זו"})
    return shelters, sources


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--accidents-only", action="store_true",
                    help="בונה מחדש רק את שכבת התאונות בקובץ הקיים - בלי פנייה לרשת")
    a = ap.parse_args(argv)
    if a.accidents_only:
        with gzip.open(OUT, "rt", encoding="utf-8") as fh:
            out = json.load(fh)
        acc = build_accidents()
        out["accidents"] = acc
        out["built_at"] = time.time()
        for src in out.get("sources") or []:
            if src.get("id") == "cbs_pedestrian_accidents":
                src.update(years=acc["years"], count=acc["count"], filter=acc["filter"],
                           excluded_by_status=acc["excluded_by_status"])
        with gzip.open(OUT, "wt", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
        print(f"accidents: {acc['count']} (מתוך {acc['total_in_source']}, הוצאו {acc['excluded_by_status']}) "
              f"in {len(acc['cells'])} cells -> {OUT}")
        return
    if a.offline:
        IL_get = IL.http_get_json
        IL.http_get_json = lambda url, source, offline=False, **kw: IL_get(url, source, True, **kw)
    acc = build_accidents()
    shelters, sources = fetch_shelters()
    out = {"built_at": time.time(), "accidents": acc, "shelters": shelters,
           "sources": [{"id": "cbs_pedestrian_accidents", "name": "למ\"ס - תאונות הולכי רגל עם קואורדינטות",
                        "years": acc["years"], "count": acc["count"], "license": "data.gov.il (Open)"}] + sources}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False)
    print(f"accidents: {acc['count']} in {len(acc['cells'])} cells; shelters: {len(shelters)}")
    for s in sources:
        print(" ", s.get("id"), s.get("count"), s.get("error", ""))
    print(OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()

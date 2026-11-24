# -*- coding: utf-8 -*-
"""
מוקדי תאונות הולכי רגל - כלל v2 (server/safety_intel.hotspot_cells; research/hotspot_v2.md):
- דירוג לפי התא עצמו, לא סכום שכנים; תקציב שטח קבוע; דטרמיניסטי.
- שכן של מוקד איננו מוקד (הכלל הקודם "מרח" מוקד על ~300 מ').
- מסלול/מפת חום/מידע רחוב משתמשים באותו כלל, והניסוח אומר "תיעוד עבר, לא תחזית" ו"היעדר סימון ≠ בטוח".
- הבנייה משאירה רק עיגון מדויק ורושמת כמה הוצאו.
- הכלל עמד בקריטריון שנקבע מראש (אם הניסוי הורץ).
"""
import gzip
import json
import os

import pytest

from server import safety_intel as SI
from test_heatmap import svc, NOON  # noqa: F401 - שירות עם משתמשים ותאורה מבודדת

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cells():
    c = {(32080, 34780): [9, 2, 3], (32080, 34781): [1, 0, 0], (32081, 34780): [1, 0, 0],   # מוקד + שכנים
         (32100, 34800): [4, 0, 1], (32100, 34801): [4, 1, 0]}                                  # שוויון -> קשות קודם
    for j in range(300):
        c[(32200 + j, 34900)] = [1, 0, 0]
    return c


def test_ranks_by_own_cell_with_area_budget_and_deterministic_ties():
    cells = _cells()
    blocks = {(y // 10, x // 10) for y, x in cells}
    budget = int(0.025 * len(blocks) * 100)
    got = SI.hotspot_cells(cells)
    assert len(got) == min(budget, len(cells))
    assert {(32080, 34780), (32100, 34801), (32100, 34800)} <= got
    small = SI.hotspot_cells(cells, frac=2 / (len(blocks) * 100))
    assert small == {(32080, 34780), (32100, 34801)}               # 9, ואז מבין ה-4: זה עם התאונה הקשה
    assert SI.hotspot_cells(cells) == SI.hotspot_cells(dict(reversed(list(cells.items()))))
    assert SI.hotspot_cells({}) == set()
    assert SI.hotspot_cells(cells, min_count=5) == {(32080, 34780)}


def _layers(cells):
    return SI.Layers({"built_at": 1, "accidents": {"cell_deg": 0.001, "years": [2020, 2024], "filter": "status_igun=1",
                                                   "cells": {f"{y}:{x}": v for (y, x), v in cells.items()}}})


def test_neighbour_of_a_hotspot_is_not_a_hotspot():
    cells = _cells()
    L = _layers(cells)
    frac = 1 / (len({(y // 10, x // 10) for y, x in cells}) * 100)
    L.hotspots = SI.hotspot_cells(cells, frac=frac)
    assert L.is_hotspot(32.0805, 34.7805)
    assert not L.is_hotspot(32.0805, 34.7815)                      # התא השכן (~95 מ') - לא מוקד
    assert L.accidents_near(32.0805, 34.7815)[0] >= 10            # אבל המידע "ב-~150 מ'" עדיין מוצג
    assert L.accidents_in_cell(32.0805, 34.7805) == (9, 2, 3)
    info = L.hotspot_info()
    assert info["rule"] == SI.HOTSPOT_RULE and info["filter"] == "status_igun=1"


def test_route_and_street_wording_is_history_not_forecast(svc):  # noqa: F811
    svc._safety_layers = _layers(_cells())
    r = svc.street_intel("dana", 32.0805, 34.7805, 150)
    acc = next(i for i in r["items"] if i["layer"] == "pedestrian_accidents")
    assert acc["value"]["hotspot"] is True
    assert "לא תחזית" in acc["text"] and "≠ בטוח" in acc["text"] and "עיגון מדויק" in acc["text"]
    route = [[32.0795, 34.7805], [32.0815, 34.7805]]                # חוצה את תא המוקד
    seg = svc._score_route(route, night=False, at=NOON)
    why = " ".join(w for s in seg["segments"] for w in s["reasons"])
    assert "לא תחזית" in why and seg["components"]["traffic"]["per_km"] > 0
    far = svc._score_route([[32.0795, 34.7825], [32.0815, 34.7825]], night=False, at=NOON)   # ~200 מ' משם
    assert far["components"]["traffic"]["per_km"] == 0          # לא "מרוח" על השכנים


def test_heatmap_marks_only_the_cell_itself(svc):  # noqa: F811
    cells = _cells()
    L = _layers(cells)
    L.hotspots = {(32080, 34780)}
    svc._safety_layers = L
    out = svc.map_environment("dana", "32.0792,34.7792,32.0818,34.7818", at=NOON)
    hot = [(c["lat"], c["lon"]) for c in out["cells"] if c["accident_hotspot"]]
    assert hot == [(32.0805, 34.7805)]
    assert out["layers"]["accidents"]["hotspot"]["rule"] == SI.HOTSPOT_RULE
    assert "≠ בטוח" in out["layers"]["accidents"]["note"]


def test_builder_keeps_only_precise_geocoding(tmp_path):
    from research_tools import build_safety_layers as B
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [34.78, 32.08]},
              "properties": {"status_igun": st, "year": 2023, "severity_code": 3, "day_night": "יום"}}
             for st in (1, 1, 2, 3)]
    p = tmp_path / "acc.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    acc = B.build_accidents(str(p))
    assert acc["count"] == 2 and acc["total_in_source"] == 4 and acc["filter"] == "status_igun=1"
    assert acc["excluded_by_status"] == {"2": 1, "3": 1}


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "research", "hotspot_v2.json")), reason="הניסוי לא הורץ")
def test_shipped_rule_is_the_measured_and_accepted_one():
    out = json.load(open(os.path.join(ROOT, "research", "hotspot_v2.json"), encoding="utf-8"))
    assert out["accepted"] is True and out["rule"] == SI.HOTSPOT_RULE
    assert out["params"] == {"area_fraction": SI.HOTSPOT_AREA_FRACTION, "min_count": SI.HOTSPOT_MIN_COUNT,
                             "block": SI.HOTSPOT_BLOCK}
    for r in out["results"]:                                          # ניצחון באותו שטח בכל שנה
        assert r["diff_v2_equal_area_minus_legacy"]["ci95"][0] > 0

# -*- coding: utf-8 -*-
"""בדיקות offline ל-research_tools.risk_factors (fixtures קטנים בתוך הקובץ, בלי רשת)."""
import gzip
import json
import math
import random

import pytest

from research_tools import risk_factors as rf


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(rf.urllib.request, "urlopen", boom)


# ---------------------------------------------------------------- opening_hours / OSM

@pytest.mark.parametrize("oh,expected", [
    ("24/7", True),
    ("Mo-Th 18:00-02:00; Fr 10:00-03:00", True),   # חוצה חצות
    ("Su-Th 08:00-23:00", True),                    # עד 23:00 = מאוחר
    ("Su-Th 08:00-22:30; Fr 08:00-14:00", False),
    ("Mo-Fr 20:00+", True),
    ("off", False),
    ("by appointment", None),
    ("", None),
    (None, None),
])
def test_opening_hours_late(oh, expected):
    assert rf.opening_hours_late(oh) is expected


def test_classify_osm():
    assert rf.classify_osm({"amenity": "pub", "opening_hours": "18:00-03:00"}) == ["bar", "hours_known", "late_venue"]
    assert rf.classify_osm({"amenity": "nightclub"}) == ["nightclub"]
    assert rf.classify_osm({"shop": "alcohol", "opening_hours": "09:00-21:00"}) == ["alcohol_shop", "hours_known"]
    assert rf.classify_osm({"amenity": "bank", "atm": "yes"}) == ["atm"]
    assert rf.classify_osm({"amenity": "bank"}) == []
    assert rf.classify_osm({"highway": "bus_stop"}) == ["bus_stop"]
    assert rf.classify_osm({"railway": "tram_stop"}) == ["rail_station"]
    assert rf.classify_osm({"railway": "station", "disused": "yes"}) == []
    assert rf.classify_osm({"building": "ruins"}) == ["abandoned"]
    assert rf.classify_osm({"amenity": "cafe"}) == ["commercial_poi"]
    assert rf.classify_osm({"amenity": "police"}) == ["police"]
    assert rf.classify_osm({"amenity": "bench"}) == []


OVERPASS_FIXTURE = {
    "osm3s": {"timestamp_osm_base": "2026-11-07T00:00:00Z"},
    "elements": [
        {"type": "node", "id": 1, "lat": 32.0705, "lon": 34.7705, "tags": {"amenity": "bar", "name": "X"}},
        {"type": "way", "id": 2, "center": {"lat": 32.0715, "lon": 34.7715}, "tags": {"amenity": "atm"}},
        {"type": "node", "id": 1, "lat": 32.0705, "lon": 34.7705, "tags": {"amenity": "bar"}},  # כפילות
        {"type": "node", "id": 3, "lat": 32.0705, "lon": 34.7705, "tags": {"amenity": "bench"}},
        {"type": "relation", "id": 4, "tags": {"amenity": "bar"}},  # בלי מיקום
    ],
}


def test_parse_osm_points_dedup_and_no_names():
    pts = rf.parse_osm_points(OVERPASS_FIXTURE)
    assert [p["factors"] for p in pts] == [["bar"], ["atm"]]
    assert all("name" not in p for p in pts)


def test_parse_osm_park_polygons():
    sq = [{"lat": 0, "lon": 0}, {"lat": 0, "lon": 1}, {"lat": 1, "lon": 1}, {"lat": 1, "lon": 0}, {"lat": 0, "lon": 0}]
    body = {"elements": [
        {"type": "way", "id": 1, "geometry": sq},
        {"type": "way", "id": 2, "geometry": sq[:3]},  # לא סגור -> מדולג
        {"type": "relation", "id": 3, "members": [{"role": "outer", "geometry": sq}]},
    ]}
    polys = rf.parse_osm_park_polygons(body)
    assert len(polys) == 2
    assert rf.point_in_rings(0.5, 0.5, polys[0])


# ---------------------------------------------------------------- גאומטריה

SQUARE = [[34.0, 32.0], [34.0, 32.01], [34.01, 32.01], [34.01, 32.0], [34.0, 32.0]]  # [lon, lat]
HOLE = [[34.004, 32.004], [34.006, 32.004], [34.006, 32.006], [34.004, 32.006], [34.004, 32.004]]


def test_point_in_rings_with_hole():
    assert rf.point_in_rings(32.002, 34.002, [SQUARE])
    assert not rf.point_in_rings(32.005, 34.005, [SQUARE, HOLE])
    assert rf.point_in_rings(32.002, 34.002, [SQUARE, HOLE])
    assert not rf.point_in_rings(32.02, 34.002, [SQUARE])


def test_area_km2_square_and_hole():
    a = rf.rings_area_km2([SQUARE])
    expected = (0.01 * 110574) * (0.01 * 111320 * math.cos(math.radians(32.005))) / 1e6
    assert abs(a - expected) / expected < 0.01
    with_hole = rf.rings_area_km2([SQUARE, HOLE])  # ArcGIS: חור בכיוון הפוך
    assert with_hole < a


def _two_area_polys():
    # שני אזורים סמוכים בעיר 5000, כל אחד 0.004x0.004 מעלות
    a = [[[34.770, 32.070], [34.770, 32.074], [34.774, 32.074], [34.774, 32.070], [34.770, 32.070]]]
    b = [[[34.774, 32.070], [34.774, 32.074], [34.778, 32.074], [34.778, 32.070], [34.774, 32.070]]]
    return {"5000:101": a, "5000:102": b, "4000:5": a}


def test_build_city_cells_and_points():
    polys = _two_area_polys()
    cells = rf.build_city_cells(polys, 5000)
    assert len(cells) == 32  # 4x8 תאים של 0.001
    assert set(cells.values()) == {"5000:101", "5000:102"}
    grid = {}
    n = rf.add_points(grid, cells, [(32.0705, 34.7705, ["bar"]), (32.0705, 34.7705, ["bar", "late_venue"]),
                                    (31.0, 34.0, ["bar"])])
    assert n == 2
    assert grid[rf.cell_of(32.0705, 34.7705)] == {"bar": 2, "late_venue": 1}
    park = [[[34.7700, 32.0700], [34.7700, 32.0720], [34.7720, 32.0720], [34.7720, 32.0700], [34.7700, 32.0700]]]
    assert rf.mark_polygons(grid, cells, [park], "park") == 4
    near = rf.factors_near(grid, 32.0715, 34.7715)
    assert near["bar"] == 2 and near["park"] == 1


# ---------------------------------------------------------------- משטרה

def test_aggregate_police_by_type_counts_cases_once():
    recs = [
        {"YeshuvKod": 5000, "StatisticAreaKod": 50000349, "StatisticTypeKod": 404, "FictiveIDNumber": "A", "Year": 2024},
        {"YeshuvKod": 5000, "StatisticAreaKod": 50000349, "StatisticTypeKod": 401, "FictiveIDNumber": "A", "Year": 2024},
        {"YeshuvKod": 5000, "StatisticAreaKod": 50000349, "StatisticTypeKod": 701, "FictiveIDNumber": "B", "Year": 2025},
        {"YeshuvKod": 5000, "StatisticAreaKod": None, "StatisticTypeKod": 404, "FictiveIDNumber": "C", "Year": 2025},
        {"YeshuvKod": 5000, "StatisticAreaKod": 50000349, "StatisticTypeKod": 404, "FictiveIDNumber": "D", "Year": 2021},
        {"YeshuvKod": 5000, "StatisticAreaKod": 30000349, "StatisticTypeKod": 404, "FictiveIDNumber": "E", "Year": 2024},
    ]
    out = rf.aggregate_police_by_type(recs, years=[2024, 2025])
    assert out["5000:349"] == {"personal": 2, "assault": 1, "robbery_theft_person": 1}
    # תיק בלי אזור + תיק שקוד האזור שלו לא שייך ליישוב -> רמת עיר בלבד
    assert out["5000:city_only"]["personal"] == 2


def test_empirical_bayes_shrinks_small_areas():
    counts = [100, 100, 100, 100, 3]
    expo = [10, 10, 10, 10, 0.1]  # אזור קטן מאוד עם שיעור 30 לעומת 10
    eb = rf.empirical_bayes_rates(counts, expo)
    assert abs(eb[0] - 10) < 1.5
    assert eb[4] < 30 and eb[4] > 9
    assert rf.empirical_bayes_rates([1], [0]) == [None]


def test_stat_area_table_status_ratio_flags():
    polys = _two_area_polys()
    del polys["4000:5"]
    info = {"5000:101": {"pop": 4000, "main_function": 1, "_ses_index": 0.1},
            "5000:102": {"pop": 1500, "main_function": 2, "_ses_index": -0.2}}
    counts = {"5000:101": {"personal": 40}, "5000:102": {"personal": 90}}
    cells = {5000: rf.build_city_cells(polys, 5000)}
    grid = {}
    rf.add_points(grid, cells[5000], [(32.0705, 34.7705, ["bar"])])
    rows = rf.stat_area_table(polys, info, counts, {50000101: "שכונה", 50000102: "אזור תעשייה"}, cells, grid, [2024, 2025])
    by = {r["unit"]: r for r in rows}
    assert by["5000:101"]["rate_personal_per_1000"] == pytest.approx(5.0)
    assert by["5000:102"]["rate_personal_per_1000"] == pytest.approx(30.0)
    assert by["5000:101"]["bar"] == 1 and by["5000:102"]["bar"] == 0
    assert "small_population" in by["5000:102"]["flags"]
    assert "non_residential_area" in by["5000:102"]["flags"]
    assert "visitor_traffic_name_heuristic" in by["5000:102"]["flags"]
    assert all(r["status"] == "ok" for r in rows)


# ---------------------------------------------------------------- מקורות עירוניים

def test_is_licensed_bar():
    assert rf.is_licensed_bar("4.8 - משקאות משכרים -פאב, בר, מסבאה וכל עסק שעיקר פעילותו הגשתם לצורך צריכה במקום ההגשה.")
    assert rf.is_licensed_bar("פאב- משקאות משכרים - הגשתם לצריכה במקום הגשתם.")
    assert not rf.is_licensed_bar("4.2א - מסעדה - ... ולרבות הגשת משקאות משכרים לצריכה במקום. ושאינו עסק שעיקר פעילותו הגשת משקאות משכרים")
    assert not rf.is_licensed_bar("מספרה.")
    assert not rf.is_licensed_bar(None)


def test_tlv_close_hour():
    assert rf.tlv_close_hour(1) == 1 and rf.tlv_close_hour(4) == 5 and rf.tlv_close_hour(6) == 0
    assert rf.tlv_close_hour(None) is None


# ---------------------------------------------------------------- cache / offline

def test_fetch_json_offline(tmp_path):
    with pytest.raises(FileNotFoundError):
        rf.fetch_json("https://example.invalid/x", "src", offline=True, cache_dir=str(tmp_path))
    p = rf._cache_path("src", "https://example.invalid/x", str(tmp_path))
    import os
    os.makedirs(os.path.dirname(p))
    with gzip.open(p, "wt", encoding="utf-8") as f:
        json.dump({"url": "u", "body": {"ok": 1}}, f)
    assert rf.fetch_json("https://example.invalid/x", "src", offline=True, cache_dir=str(tmp_path)) == {"ok": 1}


# ---------------------------------------------------------------- סטטיסטיקה

def _synthetic(n=400, beta=0.4, seed=7):
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    mu = np.exp(1.5 + beta * x)
    alpha = 0.5
    lam = rng.gamma(1 / alpha, alpha * mu)
    y = rng.poisson(lam)
    X = np.column_stack([np.ones(n), x])
    return y, X


def test_nb2_recovers_coefficients():
    y, X = _synthetic()
    fit = rf.nb2_fit(y, X)
    assert fit["converged"]
    assert abs(fit["beta"][1] - 0.4) < 3 * fit["se"][1]
    assert 0.3 < fit["alpha"] < 0.8
    b = rf.poisson_irls(y, X)
    assert abs(b[1] - 0.4) < 0.15


def test_lr_test_and_spearman():
    stat, p = rf.lr_test(-100.0, -90.0)
    assert stat == pytest.approx(20.0) and p < 1e-4
    rho, p, n = rf.spearman([1, 2, 3, 4, 5, None], [2, 4, 6, 8, 10, 3])
    assert rho == pytest.approx(1.0) and n == 5
    assert rf.spearman([1, 1, 1, 1, 1], [1, 2, 3, 4, 5])[0] is None


def _fake_rows(n_per_city=60, seed=3):
    """אזורים סינתטיים: מספר ה-bar מגדיל את התיקים (IRR מוגדר), שאר הגורמים רעש."""
    rnd = random.Random(seed)
    rows = []
    for city in (5000, 9000):
        for i in range(n_per_city):
            pop = rnd.randint(1500, 9000)
            area = rnd.uniform(0.2, 1.5)
            bars = rnd.choice([0, 0, 0, 1, 2, 4, 8, 15])
            mu = pop / 1000 * 6 * math.exp(0.35 * math.log1p(bars))
            y = sum(1 for _ in range(int(mu * 3)) if rnd.random() < 1 / 3)
            r = {"yeshuv": city, "status": "ok", "pop": pop, "area_km2": area, "cells": int(area * 100),
                 "cases_personal": y, "cases_assault": y // 2, "cases_robbery_theft_person": y // 5,
                 "cases_street_brawl": y // 10, "_ses_index": rnd.gauss(0, 1),
                 "ped_acc": rnd.randint(0, 20), "lights": rnd.randint(50, 500), "bar": bars, "nightclub": 0,
                 "alcohol_shop": rnd.randint(0, 3), "late_venue": rnd.randint(0, 5), "atm": rnd.randint(0, 6),
                 "bus_stop": rnd.randint(0, 15), "rail_station": rnd.choice([0, 0, 1]),
                 "park_cells": rnd.randint(0, 10), "abandoned": rnd.choice([0, 0, 1]), "police": rnd.choice([0, 0, 0, 1]),
                 "commercial_poi": rnd.randint(0, 40), "lat": 31.2 + 0.8 * (city == 5000) + rnd.uniform(0, 0.08),
                 "lon": 34.75 + rnd.uniform(0, 0.08), "bar_licensed_tlv": bars * 2, "night_zone_cells": rnd.randint(0, 5),
                 "atm_tlv": rnd.randint(0, 9), "bus_stop_tlv": rnd.randint(0, 15), "park_tlv_cells": rnd.randint(0, 9)}
            rows.append(r)
    return rows


def test_validate_detects_planted_effect():
    pytest.importorskip("scipy")
    res = rf.validate(_fake_rows(), "personal", log=lambda *_: None)
    assert res["n_units"] == 120
    bar = res["nb_pooled"]["bar_nightclub"]
    assert bar["irr_per_sd"] > 1.1 and bar["lr_p"] < 0.01
    assert set(res["spearman"]) == {"תל אביב-יפו", "באר שבע"}
    assert "lights_per_km2" in res["nb_lights_subset"]
    assert "lr_p_all_factors" in res["nb_joint"]
    assert "p_cluster" in bar and res["n_spatial_clusters"] > 2
    assert res["tlv_municipal"]["n"] == 60
    assert 0 <= res["residual_morans_i"]["p_perm"] <= 1


def test_morans_i_detects_spatial_pattern():
    pytest.importorskip("numpy")
    coords = [(32.0 + 0.001 * i, 34.8, 5000) for i in range(40)]
    smooth = [math.sin(i / 6.0) for i in range(40)]
    res = rf.morans_i_knn(smooth, coords, k=3, perms=199)
    assert res["I"] > 0.5 and res["p_perm"] < 0.05


def test_unit_map_and_counts_merge_published_combined_areas():
    rows = [
        {"SEMEL_YISH": 5000, "STAT11": 712, "Stat11_Unite": "712+713", "Pop_Total": 3957},
        {"SEMEL_YISH": 5000, "STAT11": 713, "Stat11_Unite": None, "Pop_Total": None},
        {"SEMEL_YISH": 5000, "STAT11": 349, "Stat11_Unite": "349", "Pop_Total": 2000},
        {"SEMEL_YISH": 4000, "STAT11": 713, "Stat11_Unite": "713+221+231", "Pop_Total": 4936},
    ]
    um = rf.unit_map(rows)
    assert um["5000:712"] == um["5000:713"] == "5000:712+713"
    assert um["5000:349"] == "5000:349"
    assert um["4000:231"] == "4000:713+221+231"
    counts = rf.unit_counts({"5000:712": {"personal": 3}, "5000:713": {"personal": 2, "assault": 1},
                             "5000:999": {"personal": 1}}, um)
    assert counts["5000:712+713"] == {"personal": 5, "assault": 1}
    assert counts["5000:999"] == {"personal": 1}
    assert rf._unit_parts("4000:713+221+231") == (4000, [713, 221, 231])


def test_write_and_load_grid_roundtrip(tmp_path):
    polys = _two_area_polys()
    del polys["4000:5"]
    cells = {5000: rf.build_city_cells(polys, 5000)}
    grid = {}
    rf.add_points(grid, cells[5000], [(32.0705, 34.7705, ["bar"]), (32.0715, 34.7765, ["rail_station"])])
    rows = [{"yeshuv": 5000, "stat11": 101, "unit": "5000:101", "status": "ok", "pop": 4000, "cases_personal": 40,
             "rate_personal_per_1000": 5.0, "ratio_city_median": 0.5, "eb_ratio_city_median": 0.6, "flags": ""},
            {"yeshuv": 5000, "stat11": 102, "unit": "5000:102", "status": "pop_below_min", "pop": 900}]
    path = str(tmp_path / "g.json.gz")
    info = rf.write_grid(grid, cells, rows, {5000: {"cells": 32, "bbox": [0, 0, 0, 0]}}, [], [2024, 2025], path=path)
    assert info["cells"] == 32 and info["areas_ok"] == 1
    g = rf.load_grid(path)
    assert g["cells"][rf.cell_of(32.0705, 34.7705)] == {"bar": 1}
    ctx = rf.area_context(g, 32.0705, 34.7705)
    assert ctx["unit"] == "5000:101" and ctx["eb_ratio"] == 0.6
    assert rf.area_context(g, 32.0715, 34.7765) == {"city": 5000, "stat11": 102}  # לא "ok" -> בלי נתוני פשיעה
    assert rf.area_context(g, 30.0, 34.0) is None
    assert "bbox" not in g["coverage"]["5000"]
    assert rf.load_grid(str(tmp_path / "missing.json.gz")) is None

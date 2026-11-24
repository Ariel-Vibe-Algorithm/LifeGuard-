# -*- coding: utf-8 -*-
"""בדיקות offline לפונקציות הטרנספורמציה של research_tools.israel_layer (בלי רשת)."""
import pytest

from research_tools import israel_layer as il


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access in tests")
    monkeypatch.setattr(il.urllib.request, "urlopen", boom)


# ---------------------------------------------------------------- ITM -> WGS84

def test_itm_matches_beer_sheva_published_pair():
    # עיריית באר שבע, street-light-br7: אותו עמוד ב-CSV-ITM (X=183130.4556, Y=573153.9988)
    # וב-CSV רגיל (31.24940572717438, 34.823091612210746)
    lat, lon = il.itm_to_wgs84(183130.4556, 573153.9988)
    assert il.haversine_m(lat, lon, 31.24940572717438, 34.823091612210746) < 0.05


def test_itm_second_pair_and_ramat_gan_sanity():
    lat, lon = il.itm_to_wgs84(183048.1654000003, 573147.6689999998)
    assert il.haversine_m(lat, lon, 31.24934607392553, 34.8222279763118) < 0.05
    # תאונה ברמת גן (SEMEL_YISHUV=8600) מדגימת ה-PUF
    lat, lon = il.itm_to_wgs84(184053, 663193)
    assert 32.04 < lat < 32.09 and 34.80 < lon < 34.85


def test_itm_origin_is_near_grid_origin():
    # נקודת המוצא של רשת ITM היא 31°44'03.817" 35°12'16.261" על דאטום Israel 1993.
    # ב-WGS84 (PROJ 2039->4326, "Israel 1993 to WGS 84 (2)") היא 31.7347630365, 35.2052137819
    # כלומר כ-78 מ' מהערך המקורי. ההפרש נובע מסיבובי ה-Helmert.
    lat, lon = il.itm_to_wgs84(219529.584, 626907.390)
    assert il.haversine_m(lat, lon, 31.7347630365087, 35.205213781923) < 0.01
    lat0 = 31 + 44 / 60 + 3.817 / 3600
    lon0 = 35 + 12 / 60 + 16.261 / 3600
    assert 60 < il.haversine_m(lat, lon, lat0, lon0) < 100


# ---------------------------------------------------------------- קודי אזור ואוכלוסייה

def test_stat_number():
    assert il.stat_number(50000224, 5000) == 224
    assert il.stat_number(700211, 70) == 211
    assert il.stat_number(None, 5000) is None
    assert il.stat_number("", 5000) is None
    assert il.stat_number(30002413, 5000) is None  # לא שייך ליישוב


def test_population_index_handles_combined_areas():
    census = [
        {"LocalityCode": "3000", "StatArea": None, "StatAreaCmb": None, "pop_approx": 1005920},
        {"LocalityCode": "3000", "StatArea": "111", "StatAreaCmb": "111", "pop_approx": 2980},
        {"LocalityCode": "3000", "StatArea": "2413", "StatAreaCmb": "2413+2414", "pop_approx": 6230},
        {"LocalityCode": "3000", "StatArea": "999", "StatAreaCmb": "999", "pop_approx": None},
    ]
    idx = il.build_population_index(census)
    assert idx[(3000, 111)] == {"unit": "3000:111", "pop": 2980.0, "name": None}
    assert idx[(3000, 2413)]["unit"] == idx[(3000, 2414)]["unit"] == "3000:2413+2414"
    assert idx[(3000, 2414)]["pop"] == 6230.0
    assert idx[(3000, 999)]["pop"] is None
    assert len(idx) == 4  # שורת היישוב לא נכנסת


# ---------------------------------------------------------------- צבירה

def _rec(cid, sa, year=2025, yk=5000, g=700):
    return {"FictiveIDNumber": cid, "Year": year, "YeshuvKod": yk, "StatisticAreaKod": sa,
            "StatisticGroupKod": g}


def test_aggregate_counts_cases_not_offences_and_keeps_missing_sa():
    recs = [
        _rec("A", 50000224), _rec("A", 50000224),          # תיק אחד, 2 עבירות
        _rec("B", 50000224),
        _rec("C", 50000611),
        _rec("D", None), _rec("D", None),                  # רמת עיר בלבד
        _rec("E", 50000611), _rec("E", None),              # תיק עם עבירה רגישה בלי אזור + עבירה עם אזור
        _rec("F", 50000224, year=2024),
    ]
    agg = il.aggregate_crime(recs)
    pu = agg["per_unit"]
    assert pu[(5000, "5000:224", 2025)] == 2
    assert pu[(5000, "5000:611", 2025)] == 2
    assert pu[(5000, "5000:224", 2024)] == 1
    c = agg["city"][(5000, 2025)]
    assert c["records"] == 8
    assert c["records_no_sa"] == 3
    assert c["cases"] == 5            # A B C D E
    assert c["cases_city_only"] == 1  # רק D; E שויך לאזור
    assert agg["city"][(5000, 2024)]["cases"] == 1


def test_aggregate_group_filter_and_unit_mapping():
    recs = [_rec("A", 30002413, yk=3000, g=400), _rec("B", 30002414, yk=3000, g=400),
            _rec("C", 30002414, yk=3000, g=10000)]
    unit_of = lambda y, s: "3000:2413+2414" if s in (2413, 2414) else f"{y}:{s}"
    agg = il.aggregate_crime(recs, unit_of=unit_of, groups=[400])
    assert agg["per_unit"] == {(3000, "3000:2413+2414", 2025): 2}
    assert agg["city"][(3000, 2025)]["records"] == 2


def test_rates_ratios_and_statuses():
    per_unit = {(1, "1:1", 2024): 10, (1, "1:1", 2025): 30,   # mean 20 / 2000 -> 10
                (1, "1:2", 2024): 40, (1, "1:2", 2025): 40,   # 40 / 2000 -> 20
                (1, "1:3", 2024): 60, (1, "1:3", 2025): 60,   # 60 / 2000 -> 30
                (1, "1:9", 2025): 5}                          # לא במפקד
    pops = {"1:1": 2000, "1:2": 2000, "1:3": 2000, "1:4": 500, "1:5": 3000, "1:6": None}
    rows = {r["unit"]: r for r in il.compute_unit_rates(per_unit, pops, [2024, 2025])}
    assert rows["1:1"]["rate_per_1000"] == pytest.approx(10.0)
    assert rows["1:4"]["status"] == "pop_below_min"
    assert rows["1:5"]["status"] == "no_police_records"
    assert rows["1:6"]["status"] == "no_population"
    assert rows["1:9"]["status"] == "no_census_match"
    meds = il.add_ratios(list(rows.values()))
    assert meds[1] == pytest.approx(20.0)                     # רק יחידות ok נכנסות לחציון
    assert rows["1:3"]["ratio_city_median"] == pytest.approx(1.5)
    assert rows["1:5"]["ratio_city_median"] is None


# ---------------------------------------------------------------- סיווג

@pytest.mark.parametrize("ratio,label", [
    (4.09, "חריג מאוד"), (3.0, "חריג מאוד"), (2.9999, "גבוה"), (2.0, "גבוה"),
    (1.98, "מעל החציון"), (1.0, "מעל החציון"), (1.048, "מעל החציון"),
    (0.9999, "נמוך מהחציון"), (0.34, "נמוך מהחציון"), (0.0, "נמוך מהחציון"), (None, "לא זמין"),
])
def test_categorize_default_bands(ratio, label):
    assert il.categorize(ratio) == label


def test_categorize_around_median_proposal():
    am = il.AROUND_MEDIAN_BAND
    assert il.categorize(1.048, around_median=am) == "סביב החציון"
    assert il.categorize(0.9, around_median=am) == "סביב החציון"
    assert il.categorize(0.8999, around_median=am) == "נמוך מהחציון"
    assert il.categorize(1.1, around_median=am) == "מעל החציון"
    assert il.categorize(3.2, around_median=am) == "חריג מאוד"


def test_default_bands_reproduce_user_table():
    rows = [(4.09, "חריג מאוד"), (2.63, "גבוה"), (1.98, "מעל החציון"), (1.83, "מעל החציון"),
            (0.53, "נמוך מהחציון"), (0.67, "נמוך מהחציון"), (0.69, "נמוך מהחציון"),
            (0.75, "נמוך מהחציון"), (0.80, "נמוך מהחציון"), (3.61, "חריג מאוד"),
            (1.68, "מעל החציון"), (3.25, "חריג מאוד"), (0.85, "נמוך מהחציון"),
            (0.34, "נמוך מהחציון"), (0.84, "נמוך מהחציון"), (1.37, "מעל החציון"),
            (0.71, "נמוך מהחציון")]
    for r, lab in rows:
        assert il.categorize(r) == lab
    rule = il.infer_band_rule(rows)
    assert rule["חריג מאוד"] == (3.25, 4.09)
    assert rule["נמוך מהחציון"][1] == 0.85


def test_quality_flags():
    row = {"pop": 1500, "cases_mean_annual": 12, "ratio_city_median": 3.4}
    f = il.quality_flags(row, "שוק הכרמל")
    assert set(f) == {"small_population", "low_case_count", "high_ratio_check_land_use",
                      "visitor_traffic_name_heuristic"}
    assert il.quality_flags({"pop": 9000, "cases_mean_annual": 300, "ratio_city_median": 1.0},
                            "רמת אביב") == []


def test_match_areas():
    names = {30000611: "רחביה (צפון)", 30000612: "רחביה (דרום)", 50000611: "רחביה", 30000100: "תלפיות"}
    assert il.match_areas(names, 3000, ["רחביה"]) == [30000611, 30000612]


# ---------------------------------------------------------------- תאונות ותאורה

def test_shaa_codes():
    assert il.shaa_to_time(1) == "00:00-00:14"
    assert il.shaa_to_time(37) == "09:00-09:14"
    assert il.shaa_to_time(96) == "23:45-23:59"
    assert il.shaa_to_time(99) is None
    assert il.shaa_to_hour_band(37) == "09-12"
    assert il.shaa_to_hour_band(96) == "21-24"
    assert il.shaa_to_hour_band(None) is None


def test_extract_pedestrian_accidents():
    rows = [
        {"pk_teuna_fikt": 1, "SUG_TEUNA": 1, "X": 184053, "Y": 663193, "SHNAT_TEUNA": 2024,
         "HODESH_TEUNA": 1, "SHAA": 39, "YOM_LAYLA": 1, "HUMRAT_TEUNA": 3, "TEURA": 1,
         "STATUS_IGUN": 1, "SEMEL_YISHUV": 8600},
        {"pk_teuna_fikt": 2, "SUG_TEUNA": 1, "X": None, "Y": None, "STATUS_IGUN": 9},
        {"pk_teuna_fikt": 3, "SUG_TEUNA": 2, "X": 184053, "Y": 663193},
        {"pk_teuna_fikt": 4, "SUG_TEUNA": "1", "X": "180000", "Y": "665000", "HUMRAT_TEUNA": 1,
         "YOM_LAYLA": 5, "TEURA": 5, "SHAA": 90, "STATUS_IGUN": 2},
    ]
    pts, st = il.extract_pedestrian_accidents(rows)
    assert st["pedestrian_total"] == 3 and st["no_coordinates"] == 1 and st["with_coordinates"] == 2
    p = pts[0]
    assert p["severity"] == "קלה" and p["day_night"] == "יום" and p["hour_band_3h"] == "09-12"
    assert p["time_quarter_hour"] == "09:30-09:44"
    assert pts[1]["severity"] == "קטלנית" and pts[1]["lighting"] == "לילה לא קיימת תאורה"


def test_normalize_lights():
    tlv = il.normalize_tlv_lights([
        {"attributes": {"oid_amud": 1, "t_sug": "מרובע", "ms_panasim": 1},
         "geometry": {"x": 34.755789672726351, "y": 32.044761407568501}},
        {"attributes": {"oid_amud": 2}, "geometry": None},
    ])
    assert len(tlv) == 1 and tlv[0]["lat"] == pytest.approx(32.0447614)
    br7 = il.normalize_br7_lights([
        {"_id": 1, "LED": "פנס לד קיים", "lat": 31.2494, "lon": 34.8230},
        {"_id": 2, "lat": 573153.9, "lon": 183130.4},   # ITM בטעות -> נזרק
    ])
    assert [p["id"] for p in br7] == [1]


def test_cache_offline_raises_without_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(il, "CACHE_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        il.http_get_json("https://example.invalid/x", "t", offline=True)

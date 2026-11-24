# -*- coding: utf-8 -*-
"""
גורמי סיכון סביבתיים בבחירת מסלול (research/risk_factors_report.md §5):
אזור בילוי לילי (0.75, ובלילות סוף שבוע 1.0) ותחנת רכבת/רק"ל (0.25, ניסיוני) - בלילה/ערב בלבד;
פשיעה אזורית מהמשטרה - להצגה בלבד, אף פעם לא משפיעה על המסלול; ניסוח בלי "שכונה מסוכנת".
רשת סינתטית - לא תלוי בקובץ המקומי (gitignored).
"""
import datetime as dt
import math

import pytest

from server import risk_factors_layer as RF
from server.lifeguard_server import LifeGuardService

FIELDS = ["city", "stat11", "bar", "nightclub", "rail_station", "bar_licensed_tlv", "night_zone_close"]
BAR_ST = (32.0750, 34.7700)          # רחוב ברים (ת"א)
QUIET = (32.0750, 34.8000)           # רחוב שקט
STATION = (32.0900, 34.7700)

# 2026-11-05 (חמישי) 23:30 שעון ישראל / 2026-11-04 (רביעי) 23:30 - שניהם לילה
THU_NIGHT = dt.datetime(2026, 11, 5, 21, 30, tzinfo=dt.timezone.utc).timestamp()
WED_NIGHT = dt.datetime(2026, 11, 4, 21, 30, tzinfo=dt.timezone.utc).timestamp()
NOON = dt.datetime(2026, 11, 5, 10, 0, tzinfo=dt.timezone.utc).timestamp()


def _key(lat, lon):
    return f"{math.floor(lat / 0.001)}:{math.floor(lon / 0.001)}"


def _grid(flags="", ratio=2.4):
    cells = {}
    for lat in [32.0700 + i * 0.001 for i in range(25)]:
        for lon in [34.7650 + j * 0.001 for j in range(40)]:
            cells[_key(lat, lon)] = [5000, 343, 0, 0, 0, 0, 0]
    cells[_key(*BAR_ST)] = [5000, 343, 4, 1, 0, 0, 0]
    cells[_key(*STATION)] = [5000, 343, 0, 0, 1, 0, 0]
    return {"cell_deg": 0.001, "fields": FIELDS, "years": [2024, 2025], "built_at": 1.0, "cells": cells,
            "stat_areas": {"5000:343": {"unit": "5000:343", "pop": 2259, "cases_2y": 60, "rate": 13.0,
                                        "ratio": ratio, "eb_ratio": ratio, "flags": flags}},
            "coverage": {"5000": {"tlv_municipal": True, "osm_base": "2026-11-07T21:03:17Z"}}}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OSRM_URL", raising=False)
    s = LifeGuardService(str(tmp_path / "rf.db"))
    s._rf_layer = RF.RiskFactors(_grid())
    from server import safety_intel as SI
    s._safety_layers = SI.Layers()                     # בלי שכבות אחרות: רק הגורמים החדשים
    import context_risk as CR
    monkeypatch.setattr(CR, "_grid", lambda: None)
    s.register("dana", "StrongPass!2026")
    yield s
    s.close()


def _line(p, east_m=300):
    return [list(p), [p[0], p[1] + east_m / 94000]]


def test_nightlife_weighs_more_on_weekend_nights_and_never_by_day(svc):
    bar, quiet = _line(BAR_ST), _line(QUIET)
    thu = svc.route_safety("dana", routes=[bar, quiet], at=THU_NIGHT)
    assert thu["phase"] == "night"
    b, q = thu["routes"]
    assert b["components"]["nightlife"]["per_km"] > 0 and q["components"]["nightlife"]["per_km"] == 0
    assert any("בילוי" in " ".join(sg["reasons"]) and "סוף שבוע" in " ".join(sg["reasons"]) for sg in b["segments"])
    wed = svc.route_safety("dana", routes=[bar], at=WED_NIGHT)["routes"][0]
    assert 0 < wed["risk_points"] < b["risk_points"]                          # 0.75 < 1.0 לכל מקטע
    noon = svc.route_safety("dana", routes=[bar], at=NOON)
    assert noon["phase"] == "day" and noon["routes"][0]["risk_points"] == 0   # ביום - לא גורם סיכון
    assert b["data"]["factors_coverage"] == ["official+osm"] and thu["attribution"].startswith("©")


def test_nightlife_stays_weaker_than_a_dark_street(svc):
    # מקטע 100 מ' בבילוי (1.0 בסוף שבוע) < רחוב חשוך (2): הראיות לבילוי חלשות מה-RCT של התאורה
    assert RF.W_NIGHTLIFE_WEEKEND < 2 and RF.W_NIGHTLIFE < RF.W_NIGHTLIFE_WEEKEND and RF.W_TRANSIT < RF.W_NIGHTLIFE


def test_nightlife_alone_never_makes_a_route_high(svc):
    long_bar = [[BAR_ST[0], BAR_ST[1] - 0.0004], [BAR_ST[0], BAR_ST[1] + 0.0004]]   # כולו בתוך רחוב הברים
    r = svc.route_safety("dana", routes=[long_bar], at=THU_NIGHT)["routes"][0]
    assert r["risk_per_km"] >= 8 and r["level"] == "medium"      # משפיע על השוואה, אבל לא "גבוה" לבד


def test_zoning_permit_alone_is_not_nightlife():
    """אזור בילוי עירוני = היתר לסגור מאוחר, לא ברים בפועל (נמצא: מסמן שכונות מגורים ברמת אביב)."""
    g = _grid()
    g["cells"][_key(*QUIET)] = [5000, 343, 0, 0, 0, 0, 3]
    rf = RF.RiskFactors(g)
    assert not rf.flags_at(*QUIET)["nightlife"]
    g["cells"][_key(*QUIET)] = [5000, 343, 0, 0, 0, 1, 3]          # + בר ברישוי
    assert RF.RiskFactors(g).flags_at(*QUIET)["nightlife"]


def test_transit_is_small_and_experimental(svc):
    r = svc.route_safety("dana", routes=[_line(STATION, 150)], at=WED_NIGHT)["routes"][0]
    assert 0 < r["components"]["transit"]["per_km"] and r["level"] == "low"
    assert any("ניסיוני" in " ".join(sg["reasons"]) for sg in r["segments"])


def test_area_crime_is_display_only_and_carefully_worded(svc):
    intel = svc.street_intel("dana", *QUIET)
    area = next(i for i in intel["items"] if i["layer"] == "area_crime")
    assert area["affects_route"] is False and "לא מידע על הרחוב" in area["text"] and "מבקרים" in area["text"]
    assert "מסוכנ" not in area["text"] and "שכונה" not in area["text"]
    assert not any(u["layer"] == "crime" for u in intel["unavailable"])
    # שני מסלולים באותו אזור "גבוה" - הניקוד לא מושפע מהפשיעה האזורית
    assert svc.route_safety("dana", routes=[_line(QUIET)], at=WED_NIGHT)["routes"][0]["risk_points"] == 0
    bar = {i["layer"]: i for i in svc.street_intel("dana", *BAR_ST)["items"]}
    assert bar["nightlife"]["type"] == "estimate" and "OpenStreetMap" in bar["nightlife"]["source"]


def test_unstable_area_is_flagged_low_confidence(svc):
    svc._rf_layer = RF.RiskFactors(_grid(flags="small_population;low_case_count"))
    area = next(i for i in svc.street_intel("dana", *QUIET)["items"] if i["layer"] == "area_crime")
    assert area["confidence"] == "low" and "לא יציב" in area["text"]


def test_outside_covered_cities_nothing_is_guessed(svc):
    far = (31.5, 34.6)
    r = svc.route_safety("dana", routes=[_line(far)], at=THU_NIGHT)["routes"][0]
    assert r["risk_points"] == 0 and r["data"]["factors_coverage"] == ["none"]
    assert any(u["layer"] == "crime" for u in svc.street_intel("dana", *far)["unavailable"])
    svc._rf_layer = RF.RiskFactors()                   # אין קובץ בכלל
    assert svc.route_safety("dana", routes=[_line(BAR_ST)], at=THU_NIGHT)["routes"][0]["risk_points"] == 0


@pytest.mark.parametrize("utc,local_hour,weekend", [
    ((2026, 11, 5, 21, 30), 23, True),     # חמישי 23:30 (חורף, +2)
    ((2026, 11, 6, 1, 0), 3, True),        # שישי 03:00
    ((2026, 11, 8, 2, 0), 4, True),        # ראשון 04:00 - סוף מוצ"ש
    ((2026, 11, 8, 21, 0), 23, False),     # ראשון 23:00
    ((2026, 11, 4, 23, 0), 1, False),      # חמישי 01:00 = לילה של רביעי
    ((2026, 7, 2, 20, 0), 23, True),       # חמישי 23:00 בקיץ (+3)
])
def test_israel_time_and_weekend_nights(utc, local_hour, weekend):
    at = dt.datetime(*utc, tzinfo=dt.timezone.utc).timestamp()
    assert RF.israel_local(at).hour == local_hour and RF.weekend_late(at) is weekend


def test_dst_fallback_rule_without_tzdata(monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError()) if n == "zoneinfo" else real(n, *a, **k))
    # 2026: שעון קיץ מ-27/3 (שישי) 02:00 עד 25/10 (ראשון) 02:00
    h = lambda *u: RF.israel_local(dt.datetime(*u, tzinfo=dt.timezone.utc).timestamp()).hour  # noqa: E731
    assert h(2026, 3, 26, 23, 0) == 1 and h(2026, 3, 27, 1, 0) == 4
    assert h(2026, 10, 24, 22, 0) == 1 and h(2026, 10, 25, 0, 0) == 2

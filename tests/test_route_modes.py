# -*- coding: utf-8 -*-
"""
מצבי מסלול (מהיר / בטוח יותר / מאוזן), חלופות הליכה (OSRM עצמי / FOSSGIS) ומזג אוויר (Open-Meteo) כהקשר.
הכול offline: HTTP מדומה. OSRM נותן זמן/מרחק בלבד; הסיכון רק משכבות LifeGuard; מזג אוויר לא נכנס לניקוד.
"""
import json
import threading
import time

import pytest

import context_risk as CR
from server import routing as R
from server import safety_intel as SI
from server import weather as W
from server.lifeguard_server import LifeGuardService

PW = "StrongPass!2026"
T_NIGHT = 1797800000          # 2026-12, לילה בישראל
T_DAY = 1781000000            # 2026-06, צהריים


# ---------------------------------------------------------------- choose_modes (פונקציה טהורה)
def _r(i, minutes, per_km, pts=None, length=None, comp=None, cov=1.0):
    length = length or minutes * 78
    return {"index": i, "duration_s": minutes * 60, "risk_per_km": per_km,
            "risk_points": pts if pts is not None else round(per_km * length / 1000, 2), "length_m": length,
            "level": "high" if per_km >= 8 else ("medium" if per_km >= 3 else "low"),
            "components": comp or {"lighting": {"per_km": per_km, "level": "low"}},
            "data": {"lighting_coverage": cov, "reliability": "high"}}


def _no_bare_safe(text):
    # "בטוח" מותר רק כ"בטוח יותר לפי הנתונים"
    return text.count("בטוח") == text.count("בטוח יותר לפי הנתונים")


def test_single_route_says_no_alternative():
    s = SI.choose_modes([_r(0, 12, 5)])
    assert s["single_route"] and s["alternatives"] == 1
    assert all(s["modes"][m]["index"] == 0 for m in SI.MODES)
    assert "אין חלופה" in s["modes"]["safest"]["text"] and "אין חלופה" in s["modes"]["balanced"]["text"]


def test_fastest_is_min_duration_not_osrm_order():
    s = SI.choose_modes([_r(0, 15, 0), _r(1, 12, 0)])        # OSRM ממיין לפי weight - לא לפי זמן
    assert s["modes"]["fastest"]["index"] == 1


def test_all_equal_risk_picks_fastest_and_says_so():
    s = SI.choose_modes([_r(0, 14, 2.0), _r(1, 11, 2.0), _r(2, 13, 2.0)])
    assert s["all_equal_risk"]
    assert s["modes"]["safest"]["index"] == s["modes"]["balanced"]["index"] == 1
    assert "אותו ניקוד סיכון" in s["modes"]["safest"]["text"]


def test_ties_break_to_fewer_points_then_shorter_then_lower_index():
    s = SI.choose_modes([_r(0, 10, 3.0, pts=3.0), _r(1, 10, 3.0, pts=3.0)])
    assert s["modes"]["fastest"]["index"] == s["modes"]["safest"]["index"] == 0
    s = SI.choose_modes([_r(0, 10, 3.0, pts=3.0), _r(1, 12, 3.0, pts=2.0)])   # אותו per_km, פחות נקודות
    assert s["modes"]["safest"]["index"] == 1
    assert "פחות נקודות סיכון" in s["modes"]["safest"]["text"]


def test_safest_respects_length_cap_and_explains():
    comp_f = {"lighting": {"per_km": 9.0, "level": "high"}, "reports": {"per_km": 1.0, "level": "low"}}
    comp_s = {"lighting": {"per_km": 1.0, "level": "low"}, "reports": {"per_km": 1.0, "level": "low"}}
    routes = [_r(0, 10, 10.0, comp=comp_f), _r(1, 14, 2.0, comp=comp_s), _r(2, 16, 0.0)]   # 16 > 1.5x10
    s = SI.choose_modes(routes)
    m = s["modes"]["safest"]
    assert SI.SAFEST_MAX_RATIO == 1.5 and m["index"] == 1 and m["extra_minutes"] == 4
    assert "בטוח יותר לפי הנתונים" in m["text"] and "חושך" in m["text"] and "דיווחים" not in m["text"]
    assert "לא נבחרה" in m["text"]
    for mode in SI.MODES:
        assert _no_bare_safe(s["modes"][mode]["text"])
    # בדיוק פי 1.5 - נכלל
    assert SI.choose_modes([_r(0, 10, 10.0), _r(1, 15, 0.0)])["modes"]["safest"]["index"] == 1


def test_balanced_formula_minutes_plus_one_per_point():
    a, b, c = _r(0, 10, 0, pts=6.0), _r(1, 13, 0, pts=1.0), _r(2, 14, 0, pts=0.0)
    # עלויות: 16, 14, 14 -> שוויון בין b ל-c -> הקצר (b)
    s = SI.choose_modes([a, b, c])
    assert SI.balanced_cost(a) == 16 and SI.balanced_cost(b) == 14 == SI.balanced_cost(c)
    assert s["modes"]["balanced"]["index"] == 1 and s["balanced_formula"] == "minutes + 1 * risk_points"
    assert "ניקוד מאוזן: 14" in s["modes"]["balanced"]["text"]
    # הפרש קטן בסיכון לא מצדיק עיקוף ארוך
    s = SI.choose_modes([_r(0, 10, 0, pts=1.0), _r(1, 14, 0, pts=0.0)])
    assert s["modes"]["balanced"]["index"] == 0 and s["modes"]["safest"]["index"] == 1


def test_lower_score_from_missing_data_is_flagged():
    s = SI.choose_modes([_r(0, 10, 6.0, cov=0.9), _r(1, 12, 0.0, cov=0.2)])
    assert s["modes"]["safest"]["index"] == 1 and any("חוסר נתונים" in c for c in s["caveats"])


# ---------------------------------------------------------------- route_safety עם שכבות
def _grid():
    # עמודי תאורה לאורך lon=34.7830 בלבד (אזור מכוסה); lon=34.7800 חשוך
    return CR.LightGrid.build([(32.07 + i * 0.0003, 34.7830) for i in range(80)])


DARK = [[32.0705, 34.7800], [32.0805, 34.7800]]                                  # ~1.1 ק"מ
LIT = [[32.0705, 34.7800], [32.0705, 34.7830], [32.0805, 34.7830], [32.0805, 34.7800]]   # ~1.7 ק"מ


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OSRM_URL", raising=False)
    s = LifeGuardService(str(tmp_path / "rm.db"))
    from server import risk_factors_layer as RF
    s._safety_layers = SI.Layers()
    s._rf_layer = RF.RiskFactors()
    monkeypatch.setattr(CR, "_grid", _grid)
    s.register("dana", PW)
    yield s
    s.close()


def test_route_safety_modes_at_night(svc):
    res = svc.route_safety("dana", routes=[{"points": DARK, "duration_s": 840}, {"points": LIT, "duration_s": 1200}],
                           at=T_NIGHT)
    d, l = res["routes"]
    assert res["phase"] == "night" and d["risk_per_km"] > l["risk_per_km"]
    assert d["duration_s"] == 840 and d["duration_source"] == "client"
    m = res["modes"]
    assert m["fastest"]["index"] == 0 and m["safest"]["index"] == 1
    assert "בטוח יותר לפי הנתונים" in m["safest"]["text"] and "חושך" in m["safest"]["text"]
    want = min(res["routes"], key=lambda r: r["duration_s"] / 60 + r["risk_points"])["index"]
    assert m["balanced"]["index"] == want
    assert res["mode_rules"]["safest_max_ratio"] == 1.5 and "OSRM אינו ציון בטיחות" in res["mode_rules"]["note"]
    assert "geometry" not in d                       # הלקוח שלח את המסלולים - לא מחזירים אותם
    assert res["weather"]["status"] == "disabled"    # conftest: בלי רשת


def test_route_safety_by_day_all_equal_and_plain_lists_still_work(svc):
    res = svc.route_safety("dana", routes=[DARK, LIT], at=T_DAY)
    assert res["all_equal_risk"] and res["modes"]["safest"]["index"] == res["modes"]["fastest"]["index"] == 0
    assert res["routes"][0]["duration_source"] == "estimate"
    assert res["routes"][0]["duration_s"] == round(res["routes"][0]["length_m"] / R.WALK_SPEED_MPS)


def _osrm_json(*routes):
    return {"code": "Ok", "routes": [{"distance": dist, "duration": dur, "weight": dur,
                                      "geometry": {"type": "LineString",
                                                   "coordinates": [[p[1], p[0]] for p in pts]}}
                                     for pts, dist, dur in routes]}


class FakeHTTP:
    def __init__(self, payload=None, exc=None):
        self.payload, self.exc, self.urls = payload, exc, []

    def __call__(self, url, timeout):
        self.urls.append(url)
        assert timeout <= 6
        if self.exc:
            raise self.exc
        return self.payload


def test_origin_dest_uses_public_fossgis_foot_router(svc, monkeypatch):
    monkeypatch.setenv("LIFEGUARD_PUBLIC_ROUTING", "1")
    http = FakeHTTP(_osrm_json((LIT, 1700, 1250), (DARK, 1110, 800)))
    svc._foot_router = R.FootAlternatives(http_get=http, sleep=lambda s: None)
    res = svc.route_safety("dana", origin={"lat": 32.0705, "lon": 34.78}, dest={"lat": 32.0805, "lon": 34.78},
                           at=T_NIGHT)
    assert res["route_source"] == "fossgis" and "OpenStreetMap" in res["routing_attribution"]
    url = http.urls[0]
    assert url.startswith("https://routing.openstreetmap.de/routed-foot/route/v1/foot/")
    assert "alternatives=3" in url and "project-osrm" not in url
    assert res["modes"]["fastest"]["index"] == 1 and res["routes"][1]["duration_source"] == "fossgis"
    assert res["routes"][0]["geometry"][0] == [32.0705, 34.78]
    # מטמון: אותה בקשה שוב - בלי HTTP
    svc.route_safety("dana", origin={"lat": 32.0705, "lon": 34.78}, dest={"lat": 32.0805, "lon": 34.78}, at=T_NIGHT)
    assert len(http.urls) == 1


def test_single_route_from_router_says_no_alternative(svc, monkeypatch):
    monkeypatch.setenv("LIFEGUARD_PUBLIC_ROUTING", "1")
    svc._foot_router = R.FootAlternatives(http_get=FakeHTTP(_osrm_json((DARK, 1110, 800))), sleep=lambda s: None)
    res = svc.route_safety("dana", origin={"lat": 32.0705, "lon": 34.78}, dest={"lat": 32.0805, "lon": 34.78})
    assert res["single_route"] and "אין חלופה" in res["modes"]["safest"]["text"]


def test_self_hosted_osrm_wins_over_public(svc, monkeypatch):
    monkeypatch.setenv("LIFEGUARD_OSRM_URL", "http://osrm.local:5000/")
    http = FakeHTTP(_osrm_json((DARK, 1110, 800), (LIT, 1700, 1250)))
    svc._foot_router = R.FootAlternatives(http_get=http, sleep=lambda s: None)
    res = svc.route_safety("dana", origin={"lat": 32.0705, "lon": 34.78}, dest={"lat": 32.0805, "lon": 34.78})
    assert res["route_source"] == "osrm" and http.urls[0].startswith("http://osrm.local:5000/route/v1/foot/")


def test_no_routing_gives_clear_error_and_breaker(svc, monkeypatch):
    od = dict(origin={"lat": 32.0705, "lon": 34.78}, dest={"lat": 32.0805, "lon": 34.78})
    monkeypatch.setenv("LIFEGUARD_PUBLIC_ROUTING", "0")
    with pytest.raises(Exception) as e:
        svc.route_safety("dana", **od)
    assert e.value.status == 503 and "OSRM" in e.value.message and "מסלולים מוכנים" in e.value.message
    monkeypatch.setenv("LIFEGUARD_PUBLIC_ROUTING", "1")
    http = FakeHTTP(exc=OSError("timed out"))
    svc._foot_router = R.FootAlternatives(http_get=http, sleep=lambda s: None)
    for _ in range(2):
        with pytest.raises(Exception) as e:
            svc.route_safety("dana", **od)
        assert e.value.status == 503 and "OSRM" in e.value.message
    assert len(http.urls) == 1                      # מפסק: לא מציפים שרת שנפל
    http2 = FakeHTTP({"code": "NoRoute", "routes": []})
    svc._foot_router = R.FootAlternatives(http_get=http2, sleep=lambda s: None)
    with pytest.raises(Exception) as e:
        svc.route_safety("dana", **od)
    assert "לא מצא מסלול" in e.value.message


def test_public_router_rate_limit_one_per_second():
    clock = [1000.0]
    slept = []
    alt = R.FootAlternatives(http_get=FakeHTTP(_osrm_json((DARK, 1110, 800))), clock=lambda: clock[0],
                             sleep=slept.append)
    import os
    old = os.environ.pop("LIFEGUARD_OSRM_URL", None)
    os.environ["LIFEGUARD_PUBLIC_ROUTING"] = "1"
    try:
        alt.get((32.07, 34.78), (32.08, 34.78))
        alt.get((32.07, 34.78), (32.09, 34.78))     # זוג אחר מיד אחרי - ממתין שנייה
        assert slept and slept[-1] == pytest.approx(1.0)
        alt._next_public = clock[0] + 5              # תור ארוך -> "עמוס" במקום להמתין
        with pytest.raises(R.RoutingUnavailable) as e:
            alt.get((32.07, 34.78), (32.10, 34.78))
        assert e.value.reason == "busy"
    finally:
        os.environ["LIFEGUARD_PUBLIC_ROUTING"] = "0"
        if old is not None:
            os.environ["LIFEGUARD_OSRM_URL"] = old


# ---------------------------------------------------------------- מזג אוויר
NOW = 1794404700.0


def _payload(precip=0.0, code=1, vis=40000.0, gust=20.0, temp=21.5, t=NOW):
    return {"latitude": 32.08, "longitude": 34.78,
            "current": {"time": t, "interval": 900, "temperature_2m": temp, "precipitation": precip, "rain": precip,
                        "weather_code": code, "wind_gusts_10m": gust, "is_day": 0, "visibility": vis},
            "hourly": {"time": [t - 900 + 3600 * i for i in range(6)], "precipitation": [precip] * 6,
                       "precipitation_probability": [80] * 6, "weather_code": [code] * 6, "visibility": [vis] * 6,
                       "wind_gusts_10m": [gust] * 6, "is_day": [0] * 6, "temperature_2m": [temp] * 6}}


class FakeFetch:
    def __init__(self, payload=None, exc=None, block=None):
        self.payload, self.exc, self.block, self.calls = payload, exc, block, []

    def __call__(self, lat, lon, timeout):
        self.calls.append((lat, lon))
        if self.block is not None:
            self.block.wait(5)
        if self.exc:
            raise self.exc
        return self.payload


def test_hazard_thresholds_are_standard_and_never_invented():
    kinds = lambda **c: [h["kind"] for h in W.hazards(c)]   # noqa: E731
    assert kinds(precipitation=7.6) == ["heavy_rain"] and kinds(weather_code=65) == ["heavy_rain"]
    assert kinds(precipitation=3.0) == ["rain"] and kinds(precipitation=0.3) == ["light_rain"]
    assert kinds(weather_code=45) == ["low_visibility"] and kinds(visibility=900) == ["low_visibility"]
    assert kinds(wind_gusts_10m=65) == ["gale"] and kinds(wind_gusts_10m=52) == ["strong_wind"]
    assert kinds(weather_code=95) == ["thunderstorm"]
    assert kinds(weather_code=1, precipitation=0, visibility=30000, wind_gusts_10m=20, temperature_2m=22) == []
    t = " ".join(h["text"] for h in W.hazards({"weather_code": 65, "precipitation": 9.2, "visibility": 800}))
    assert "גשם כבד" in t and "חלקים" in t and "ראות נמוכה" in t


def test_weather_cache_per_cell_and_ttl():
    clock = [NOW]
    f = FakeFetch(_payload(precip=9.0, code=65, vis=800))
    w = W.Weather(fetch=f, clock=lambda: clock[0])
    a = w.get(32.0801, 34.7801)
    assert a["status"] == "ok" and a["source"] == "Open-Meteo" and "Open-Meteo.com" in a["attribution"]
    assert a["license"] == "CC BY 4.0" and a["fetched_at"] == NOW and a["valid_at"] == NOW
    assert a["affects_score"] is False and "גשם כבד" in a["text"] and a["text"].startswith("עכשיו")
    assert {h["kind"] for h in a["hazards"]} >= {"heavy_rain", "low_visibility"} and a["next_hours"]
    w.get(32.0805, 34.7805)                         # אותו תא ~1 ק"מ
    assert len(f.calls) == 1
    w.get(32.0950, 34.7801)                         # תא אחר
    assert len(f.calls) == 2
    clock[0] += W.TTL_S + 1                          # פג תוקף
    f.payload = _payload(t=clock[0])
    assert w.get(32.0801, 34.7801)["status"] == "ok" and len(f.calls) == 3


def test_weather_failure_is_negative_cached_and_never_raises():
    clock = [NOW]
    f = FakeFetch(exc=OSError("network down"))
    w = W.Weather(fetch=f, clock=lambda: clock[0])
    r = w.get(32.08, 34.78)
    assert r["status"] == "unavailable" and "OSError" in r["error"]
    assert w.get(32.08, 34.78)["status"] == "unavailable" and len(f.calls) == 1
    clock[0] += W.FAIL_TTL_S + 1
    w.get(32.08, 34.78)
    assert len(f.calls) == 2
    f.exc, f.payload = None, {"garbage": True}
    clock[0] += W.FAIL_TTL_S + 1
    assert w.get(32.08, 34.78)["status"] == "unavailable"


def test_weather_not_applicable_or_disabled_makes_no_request():
    f = FakeFetch(_payload())
    w = W.Weather(fetch=f, clock=lambda: NOW)
    assert w.get(32.08, 34.78, at=NOW - 86400 * 30)["status"] == "not_applicable"
    assert w.get(32.08, 34.78, at=NOW + 86400)["status"] == "not_applicable"
    assert W.Weather(fetch=f, enabled=False).get(32.08, 34.78)["status"] == "disabled"
    assert f.calls == []
    hourly = w.get(32.08, 34.78, at=NOW + 3 * 3600)              # תחזית לשעה הרלוונטית
    assert hourly["status"] == "ok" and hourly["kind"] == "hourly" and "תחזית" in hourly["text"]


def test_slow_weather_never_blocks_route_safety(svc):
    gate = threading.Event()
    f = FakeFetch(_payload(precip=9.0, code=65), block=gate)
    svc._weather_src = W.Weather(fetch=f)
    t0 = time.time()
    res = svc.route_safety("dana", routes=[DARK, LIT])
    took = time.time() - t0
    gate.set()
    assert res["weather"]["status"] == "pending" and took < W.WAIT_S + 1.5
    assert res["routes"][0]["length_m"] > 1000 and res["modes"]["fastest"]["index"] == 0


def test_weather_failure_and_success_never_change_risk(svc):
    base = svc.route_safety("dana", routes=[DARK, LIT], at=T_NIGHT)          # conftest: disabled
    now = time.time()
    svc._weather_src = W.Weather(fetch=FakeFetch(exc=OSError("down")))
    bad = svc.route_safety("dana", routes=[DARK, LIT], at=T_NIGHT)
    assert bad["weather"]["status"] == "not_applicable"                       # T_NIGHT רחוק מעכשיו - בלי בקשה
    bad = svc.route_safety("dana", routes=[DARK, LIT], at=now)
    assert bad["weather"]["status"] == "unavailable"
    svc._weather_src = W.Weather(fetch=FakeFetch(_payload(precip=12.0, code=82, vis=500, gust=70, t=now)))
    good = svc.route_safety("dana", routes=[DARK, LIT], at=now)
    assert good["weather"]["status"] == "ok" and good["weather"]["affects_score"] is False
    assert "גשם כבד" in good["weather"]["text"] and "הקשר בלבד" in good["weather"]["label"]
    day_base = svc.route_safety("dana", routes=[DARK, LIT], at=now)          # אותו זמן, מזג אוויר מהמטמון
    svc._weather_src = W.Weather(enabled=False)
    no_wx = svc.route_safety("dana", routes=[DARK, LIT], at=now)
    assert [r["risk_points"] for r in day_base["routes"]] == [r["risk_points"] for r in no_wx["routes"]]
    assert [r["risk_points"] for r in good["routes"]] == [r["risk_points"] for r in no_wx["routes"]]
    assert base["routes"][0]["risk_points"] > 0


def test_street_intel_weather_item_is_labelled_context(svc):
    svc._weather_src = W.Weather(fetch=FakeFetch(_payload(precip=9.0, code=65, t=time.time())))
    r = svc.street_intel("dana", 32.08, 34.78)
    wx = [i for i in r["items"] if i["layer"] == "weather"]
    assert wx and wx[0]["type"] == "forecast" and wx[0]["verified"] is False
    assert wx[0]["affects_route"] == "context_only" and "Open-Meteo" in wx[0]["source"]
    assert wx[0]["last_update"] and "Open-Meteo.com" in wx[0]["attribution"] and "גשם כבד" in wx[0]["text"]
    svc._weather_src = W.Weather(fetch=FakeFetch(exc=OSError("x")))
    r = svc.street_intel("dana", 32.0, 34.9)
    assert not [i for i in r["items"] if i["layer"] == "weather"]
    assert any(u["layer"] == "weather" for u in r["unavailable"])


# ---------------------------------------------------------------- לקוח Windows
class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_client_alternatives_use_fossgis_foot_and_cache(monkeypatch):
    import map_module
    seen = []
    monkeypatch.setattr(map_module, "_alt_cache", {})
    monkeypatch.setattr(map_module, "_alt_next", [0.0])

    def fake_get(url, headers=None, timeout=None):
        seen.append(url)
        assert headers["User-Agent"] and timeout <= 8
        return _Resp(_osrm_json((LIT, 1700, 1250), (DARK, 1110, 800)))
    monkeypatch.setattr(map_module.requests, "get", fake_get)
    alts = map_module.get_walking_alternatives(32.0705, 34.78, 32.0805, 34.78)
    assert len(alts) == 2 and alts[1]["duration_s"] == 800 and alts[0]["points"][0] == (32.0705, 34.78)
    assert "routing.openstreetmap.de/routed-foot" in seen[0] and "alternatives=3" in seen[0]
    assert map_module.get_walking_alternatives(32.0705, 34.78, 32.0805, 34.78) == alts and len(seen) == 1

    def boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(map_module.requests, "get", boom)
    assert map_module.get_walking_alternatives(32.0, 34.7, 32.1, 34.8) is None


def test_desktop_mode_texts(monkeypatch, svc):
    import app_screens
    import map_module
    alts = [{"points": [tuple(p) for p in DARK], "duration_s": 840, "distance_m": 1110},
            {"points": [tuple(p) for p in LIT], "duration_s": 1200, "distance_m": 1700}]
    monkeypatch.setattr(map_module, "get_walking_alternatives", lambda *a, **k: alts)
    r = app_screens.fetch_route_modes(None, {"lat": 32.07, "lon": 34.78}, {"lat": 32.08, "lon": 34.78})
    assert r["alts"] == alts and "חיבור לשרת" in r["error"] and "res" not in r

    class Client:
        token = "t"

        def route_options(self, alternatives, at=None, include_news=False):
            return json.loads(json.dumps(svc.route_safety("dana", routes=[
                {"points": [list(p) for p in a["points"]], "duration_s": a["duration_s"]} for a in alternatives],
                at=T_NIGHT)))
    r = app_screens.fetch_route_modes(Client(), {"lat": 32.07, "lon": 34.78}, {"lat": 32.08, "lon": 34.78})
    res = r["res"]
    line = app_screens.mode_summary_line(res, "safest")
    assert "בטוח יותר לפי הנתונים" in line and "דק'" in line and "סיכון" in line
    assert _no_bare_safe(line)
    txt = app_screens.route_safety_text(dict(res, weather={"status": "ok", "label": W.LABEL_HE,
                                                           "text": "עכשיו: גשם כבד (9 מ\"מ/שעה) - משטחים חלקים",
                                                           "attribution": W.ATTRIBUTION}), index=1)
    assert "גשם כבד" in txt and "Open-Meteo.com" in txt and "הקשר בלבד" in txt and "פשיעה" in txt
    one = app_screens.mode_summary_line({"modes": {"safest": dict(res["modes"]["safest"], text="אין חלופה - x")},
                                         "single_route": True}, "safest")
    assert "אין חלופה" in one

# -*- coding: utf-8 -*-
"""
שלב 2 - נסיעה בטוחה: זיהוי סטייה / עצירה חריגה / איחור / הגעה, בלי התראות שווא משגיאות GPS, פקקים,
תחנות אוטובוס ומסלולים חלופיים לגיטימיים; הסלמה: בדיקה → (אין תשובה / "צריך עזרה") → קריאת חירום.
"""
import json
import threading
import time

import pytest

from server import rides as R
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient

PW = "StrongPass!2026"
START = (32.0700, 34.7800)
DEST = (32.1100, 34.7800)          # ~4.4 ק"מ צפונה
M_PER_DEG = 111_000


def north(lat0, meters):
    return lat0 + meters / M_PER_DEG


def track(points, t0=0.0, step=15.0, acc=10.0):
    return [{"t": t0 + i * step, "lat": la, "lon": lo, "accuracy_m": acc, "speed_mps": None}
            for i, (la, lo) in enumerate(points)]


def ride(ride_type="taxi", route=None, source="straight_line", deadline=1e12, **kw):
    r = {"ride_type": ride_type, "dest": DEST, "route": route, "route_source": source,
         "start_remaining_m": R.haversine_m(START, DEST), "deadline": deadline, "snooze": {}, "near_dest_asked": 0}
    r.update(kw)
    return r


# ---------------------------------------------------------------- טהור
def test_normal_ride_toward_destination_is_quiet():
    pts = [(north(START[0], i * 150), START[1]) for i in range(20)]          # 10 מ'/ש' צפונה
    tr = track(pts)
    assert R.detect(ride(), tr, tr[-1]["t"]) == (None, None)


def test_moving_away_from_destination_without_road_route():
    pts = [(north(START[0], -i * 120), START[1]) for i in range(16)]         # דרומה, הרחק מהיעד
    tr = track(pts)
    kind, info = R.detect(ride(), tr, tr[-1]["t"])
    assert kind == "deviation" and info["moving_away_m"] > 800


def test_gps_jumps_with_bad_accuracy_are_ignored():
    pts = [(north(START[0], i * 30), START[1]) for i in range(10)]
    tr = track(pts)
    tr += track([(north(START[0], -2000), START[1])] * 4, t0=tr[-1]["t"] + 15, acc=400)   # קפיצה "רחוקה" לא מדויקת
    assert R.detect(ride(), tr, tr[-1]["t"])[0] is None


def _road():
    # מסלול כביש: צפונה עד 32.09 ואז ממשיך צפונה ליעד
    return [(north(START[0], i * 200), START[1]) for i in range(int(R.haversine_m(START, DEST) / 200) + 1)] + [DEST]


def test_off_road_route_deviation_needs_three_fixes_over_a_minute():
    road = _road()
    east = START[1] + 900 / (M_PER_DEG * 0.85)                              # ~900 מ' מזרחה מהמסלול
    base = [(north(START[0], i * 100), START[1]) for i in range(5)]
    off = [(north(START[0], 500), east)] * 5                                 # נתקע מזרחה (לא מתקדם)
    tr = track(base + off[:2])
    assert R.detect(ride(route=road, source="osrm"), tr, tr[-1]["t"])[0] is None   # 2 מיקומים - לא מספיק
    tr = track(base + off)
    assert R.detect(ride(route=road, source="osrm"), tr, tr[-1]["t"])[0] == "deviation"


def test_legit_alternative_route_still_approaching_destination_is_not_an_alarm():
    road = _road()
    east = START[1] + 900 / (M_PER_DEG * 0.85)
    pts = [(north(START[0], i * 150), east) for i in range(16)]             # כביש מקביל, מתקרב ליעד
    tr = track(pts)
    assert R.detect(ride(route=road, source="osrm"), tr, tr[-1]["t"])[0] == "alternative_route"


def test_stop_thresholds_differ_by_vehicle_and_traffic_crawl_is_fine():
    p = (north(START[0], 1000), START[1])
    jitter = [(p[0] + (i % 3) * 0.00005, p[1]) for i in range(22)]          # ~5.5 דק' במקום, רעש של מטרים
    tr = track(jitter)
    assert R.detect(ride("taxi"), tr, tr[-1]["t"])[0] == "stopped"
    assert R.detect(ride("bus"), tr, tr[-1]["t"])[0] is None                  # תחנה/המתנה - עד 7 דק'
    crawl = track([(north(START[0], 1000 + i * 30), START[1]) for i in range(22)])   # 2 מ'/ש' בפקק
    assert R.detect(ride("taxi"), crawl, crawl[-1]["t"])[0] is None
    long_bus = track(jitter * 2)
    assert R.detect(ride("bus"), long_bus, long_bus[-1]["t"])[0] == "stopped"


def test_late_but_progressing_extends_instead_of_alarming():
    pts = [(north(START[0], i * 100), START[1]) for i in range(25)]
    tr = track(pts)
    now = tr[-1]["t"]
    assert R.detect(ride(deadline=now - 60), tr, now)[0] == "progress_late"
    still = track([(north(START[0], 2000) + (i % 2) * 0.0003, START[1]) for i in range(25)])
    kind = R.detect(ride("bus", deadline=still[-1]["t"] - 60), still, still[-1]["t"])[0]
    assert kind in ("late", "stopped")


def test_near_destination_asks_for_safe_arrival_and_snooze_works():
    near = (DEST[0] - 80 / M_PER_DEG, DEST[1])
    tr = track([near] * 10)
    assert R.detect(ride(), tr, tr[-1]["t"])[0] == "near_destination"
    assert R.detect(ride(near_dest_asked=1), tr, tr[-1]["t"])[0] is None        # שואלים פעם אחת
    away = track([(north(START[0], -i * 120), START[1]) for i in range(16)])
    assert R.detect(ride(snooze={"deviation": 1e12}), away, away[-1]["t"])[0] is None


def test_geometry_helpers():
    road = [(32.0, 34.0), (32.01, 34.0)]
    assert abs(R.distance_to_route((32.005, 34.001), road) - 94) < 5
    assert abs(R.remaining_along((32.005, 34.0), road) - R.haversine_m((32.005, 34.0), (32.01, 34.0))) < 2
    assert len(R.simplify([(i, i) for i in range(1000)], 200)) == 200
    assert R.clean_vehicle({"plate": "12-345-67", "driver": "x" * 500, "evil": 1}) == \
        {"plate": "12-345-67", "driver": "x" * 60}


# ---------------------------------------------------------------- שירות
@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OSRM_CAR_URL", raising=False)
    s = LifeGuardService(str(tmp_path / "r.db"))
    for u in ("dana", "mom", "eve"):
        s.register(u, PW)
    s._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
         "VALUES ('dana','mom','accepted',0,0)")
    yield s
    s.close()


def _notes(s, user, ntype):
    return [json.loads(r["payload"]) for r in s._q("SELECT payload FROM notifications WHERE username=? AND type=? "
                                                   "ORDER BY id", (user, ntype))]


def _start(s, mode="loud", ride_type="taxi"):
    return s.start_escort("dana", 30, "הביתה", mode, {"lat": START[0], "lon": START[1], "is_precise": True},
                          kind="ride", ride_type=ride_type, destination_point={"lat": DEST[0], "lon": DEST[1]},
                          vehicle={"plate": "12-345-67", "description": "טויוטה לבנה"})


def _feed_away(s, eid, now):
    for i in range(16):
        s._ride_track(eid, {"lat": north(START[0], -i * 120), "lon": START[1], "accuracy_m": 10},
                      now=now - (15 - i) * 15)


def _future(n=16):
    """זמן "עכשיו" מדומה אחרי מיקום ההתחלה האמיתי, כך שהמסלול המדומה הוא האחרון."""
    return time.time() + n * 15 + 5


def test_ride_validation_creates_nothing_on_bad_input(svc):
    with pytest.raises(Exception):
        svc.start_escort("dana", 30, kind="ride", ride_type="spaceship", destination_point={"lat": 1, "lon": 1})
    with pytest.raises(Exception):
        svc.start_escort("dana", 30, kind="ride", ride_type="taxi", destination_point=None)
    with pytest.raises(Exception):
        svc.start_escort("dana", 30, kind="ride", ride_type="taxi", destination_point={"lat": 999, "lon": 1})
    assert not svc._q("SELECT 1 FROM escorts")


def test_ride_view_for_owner_and_watcher(svc):
    v = _start(svc)
    assert v["kind"] == "ride" and v["ride_type"] == "taxi" and v["route_source"] == "straight_line"
    assert v["vehicle"]["plate"] == "12-345-67" and v["destination_point"]["lat"] == DEST[0]
    assert v["progress"]["remaining_m"] > 4000 and v["anomaly"] is None
    w = svc.get_escort("mom", v["id"])
    assert w["vehicle"]["plate"] == "12-345-67" and "watchers" not in w
    with pytest.raises(Exception):
        svc.get_escort("eve", v["id"])
    walk = svc.start_escort("eve", 20, "בית")
    assert walk["kind"] == "walk" and "anomaly" not in walk                   # ליווי רגיל לא השתנה


def test_deviation_check_ok_clears_and_snoozes(svc):
    v = _start(svc)
    now = _future()
    _feed_away(svc, v["id"], now)
    svc._ride_evaluate(v["id"], now)
    chk = _notes(svc, "dana", "escort_check")
    assert chk and chk[-1]["kind"] == "deviation" and "סוטה" in chk[-1]["text"]
    assert _notes(svc, "mom", "escort_update")[-1]["event"] == "anomaly"
    assert svc.get_escort("dana", v["id"])["anomaly"]["kind"] == "deviation"
    with pytest.raises(Exception):
        svc.escort_checkin("mom", v["id"], "ok")                                 # רק הנוסע/ת
    out = svc.escort_checkin("dana", v["id"], "ok")
    assert out["anomaly"] is None and out["status"] == "active"
    assert _notes(svc, "mom", "escort_update")[-1]["event"] == "anomaly_cleared"
    svc._ride_evaluate(v["id"], now + 5)
    assert len(_notes(svc, "dana", "escort_check")) == 1                         # לא שואלים שוב 10 דק'


def test_no_answer_escalates_to_incident_with_vehicle_details(svc):
    v = _start(svc, mode="silent")
    now = _future()
    _feed_away(svc, v["id"], now)
    svc._ride_evaluate(v["id"], now)
    chk = _notes(svc, "dana", "escort_check")[-1]
    assert chk["silent"] and chk["text"] == R.NEUTRAL_TEXT                       # נסיעה שקטה: טקסט ניטרלי
    assert svc.get_escort("dana", v["id"])["anomaly"]["text"] == R.NEUTRAL_TEXT
    assert "סוטה" in svc.get_escort("mom", v["id"])["anomaly"]["text"]           # הצופה יודע/ת למה
    svc._ride_tick(now + R.ANOMALY_RESPONSE_S - 5)
    assert svc._escort_row(v["id"])["status"] == "active"
    svc._ride_tick(now + R.ANOMALY_RESPONSE_S + 1)
    e = svc._escort_row(v["id"])
    assert e["status"] == "escalated" and e["incident_id"]
    inc = svc._incident_row(e["incident_id"])
    assert inc["mode"] == "silent"
    assert any("12-345-67" in r for r in json.loads(inc["risk_reasons"]))
    assert _notes(svc, "mom", "escort_alarm")[-1]["incident_id"] == e["incident_id"]
    assert not _notes(svc, "dana", "escort_alarm")                               # שקט: כלום אצל הבעלים


def test_help_checkin_and_discreet_sos(svc):
    v = _start(svc)
    out = svc.escort_checkin("dana", v["id"], "help")
    assert out["incident"]["id"] and svc._escort_row(v["id"])["status"] == "escalated"
    v2 = svc.start_escort("dana", 30, mode="loud", location={"lat": START[0], "lon": START[1]}, kind="ride",
                          ride_type="bus", destination_point={"lat": DEST[0], "lon": DEST[1]})
    sos = svc.escort_sos("dana", v2["id"], "silent")
    assert svc._incident_row(sos["incident"]["id"])["mode"] == "silent"
    with pytest.raises(Exception):
        svc.escort_sos("dana", v2["id"], "loud")                                  # כבר הסתיימה


def test_progressing_late_ride_gets_extension_not_alarm(svc):
    v = _start(svc)
    now = _future(25)
    svc._x("UPDATE escorts SET deadline=? WHERE id=?", (now - 30, v["id"]))
    for i in range(25):
        svc._ride_track(v["id"], {"lat": north(START[0], i * 100), "lon": START[1], "accuracy_m": 10},
                        now=now - (24 - i) * 15)
    svc.features_tick(now)
    e = svc._escort_row(v["id"])
    assert e["status"] == "active" and e["deadline"] > now and e["late_extensions"] == 1
    assert _notes(svc, "mom", "escort_update")[-1]["event"] == "running_late"
    assert not _notes(svc, "dana", "escort_check")


def test_near_destination_unanswered_is_not_an_alarm(svc):
    v = _start(svc)
    now = _future(10)
    near = DEST[0] - 80 / M_PER_DEG
    for i in range(10):
        svc._ride_track(v["id"], {"lat": near, "lon": DEST[1], "accuracy_m": 10}, now=now - (9 - i) * 15)
    svc._ride_evaluate(v["id"], now)
    assert _notes(svc, "dana", "escort_check")[-1]["kind"] == "near_destination"
    svc._ride_tick(now + R.ANOMALY_RESPONSE_S + 1)
    assert svc._escort_row(v["id"])["status"] == "active"
    assert _notes(svc, "mom", "escort_update")[-1]["event"] == "unconfirmed_arrival"
    done = svc.escort_action("dana", v["id"], "arrived")
    assert done["status"] == "arrived"


def test_safe_ride_over_http(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_OSRM_CAR_URL", raising=False)
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "h.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = BackendClient(f"http://127.0.0.1:{httpd.server_address[1]}")
        c.login_or_register("dana", PW)
        v = c.start_ride(25, "rideshare", {"lat": DEST[0], "lon": DEST[1]}, "עבודה",
                         location={"lat": START[0], "lon": START[1], "is_precise": True},
                         vehicle={"plate": "1"})
        assert v["kind"] == "ride" and v["ride_type"] == "rideshare"
        c.escort_location(v["id"], {"lat": north(START[0], 200), "lon": START[1], "is_precise": True,
                                    "accuracy_m": 8, "speed_mps": 9})
        assert c.escort_checkin(v["id"], "ok")["status"] == "active"
        assert c.escort_sos(v["id"], "loud")["incident"]["mode"] == "loud"
    finally:
        httpd.shutdown()
        httpd.service.close()


def test_osrm_car_route_is_used_when_configured(svc, monkeypatch):
    road = [[START[1], START[0]], [START[1], north(START[0], 2000)], [DEST[1], DEST[0]]]
    monkeypatch.setenv("LIFEGUARD_OSRM_CAR_URL", "http://osrm-car.invalid")

    class Resp:
        def __init__(self, data):
            self.data = data
        def read(self, n):
            return json.dumps(self.data).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    seen = []

    def fake(req, timeout):
        seen.append(req.full_url)
        return Resp({"code": "Ok", "routes": [{"geometry": {"coordinates": road}, "duration": 600,
                                               "distance": 4400}]})
    monkeypatch.setattr(R.urllib.request, "urlopen", fake)
    v = _start(svc)
    assert v["route_source"] == "osrm" and len(v["route"]) == 3 and "/route/v1/driving/" in seen[0]
    t = svc.start_escort("eve", 30, location={"lat": START[0], "lon": START[1]}, kind="ride", ride_type="train",
                         destination_point={"lat": DEST[0], "lon": DEST[1]})
    assert t["route_source"] == "straight_line" and len(seen) == 1             # רכבת: בלי מסלול כביש

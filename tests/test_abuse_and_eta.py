# -*- coding: utf-8 -*-
"""
הגנה על תור המתנדבים מניצול + בחירת מתנדב לפי זמן הליכה.
שרת LifeGuard אמיתי; OSRM מוחלף בשרת HTTP מקומי אמיתי בפורמט table API (אין OSRM בסביבת הבדיקה).
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server import abuse as A
from server import routing as R
from test_dispatch_queue import NEAR, _vols
from test_features_e2e import TLV, _c, _connect, _err, _start, _vol  # noqa: F401

HIGH = {"level": "HIGH", "score": 7, "reasons": ["x"]}


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path)
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


# ------------------------------------------------------------------ ניצול לרעה
def test_assess_rules():
    assert A.assess(0, 0, 0) == {"limited": False, "capped": False, "reasons": [],
                                 "remaining_this_hour": A.VOLUNTEERS_PER_HOUR_CAP}
    capped = A.assess(0, 0, A.VOLUNTEERS_PER_HOUR_CAP + 5)
    assert A.allowed_slots(3, 0, capped) == 1 and A.allowed_slots(3, 2, capped) == 0   # תמיד לפחות אחד לאירוע חדש
    assert A.allowed_slots(3, 0, A.assess(0, 0, A.VOLUNTEERS_PER_HOUR_CAP - 2)) == 2
    assert A.assess(A.QUICK_CANCELS_TO_LIMIT, 0, 0)["limited"]
    assert A.assess(0, A.REPORTS_TO_LIMIT, 0)["limited"]
    assert A.assess(0, 1, A.VOLUNTEERS_PER_HOUR_CAP)["capped"] and not A.assess(0, 1, 0)["limited"]
    assert A.effective_level("HIGH", True) == "LOW" and A.effective_level("HIGH", False) == "HIGH"


def test_quick_cancels_remove_priority_privileges_but_never_block_help(srv):
    httpd, url = srv
    for i in range(4):
        _vol(url, f"vol{i}", {**NEAR, "lat": 32.086 + i * 0.001})
    eve, mom = _c(url, "eve"), _c(url, "mom")
    _connect(eve, mom)
    for i in range(A.QUICK_CANCELS_TO_LIMIT):
        inc = eve.create_incident(f"q{i}", location=TLV, risk=HIGH)
        assert len(_vols(inc)) == 3                         # לפני: HIGH -> 3 מתנדבים במקביל
        eve.update_incident(inc["id"], status="cancelled")  # ביטול מיידי, אחרי שהוזעקו
    inc = eve.create_incident("real", location=TLV, risk=HIGH)
    view = eve.get_incident(inc["id"])
    assert len(_vols(view)) == 1                            # עדיין מתנדב אחד - חירום לא נחסם
    assert [r["username"] for r in view["recipients"] if r["role"] == "trusted"] == ["mom"]
    assert any(e["kind"] == "dispatch_limited" for e in view["timeline"])      # שקוף לבעל האירוע
    assert mom.get_incident(inc["id"])["risk"]["level"] == "HIGH"              # המגיבים רואים את הדיווח כפי שהוא


def test_volunteer_false_alarm_reports(srv):
    httpd, url = srv
    v1, v2, v3 = (_vol(url, n, {**NEAR, "lat": 32.086 + i * 0.001}) for i, n in enumerate(("vol1", "vol2", "vol3")))
    eve, stranger = _c(url, "eve"), _c(url, "stranger")
    inc = eve.create_incident("a", location=TLV, risk=HIGH)
    assert _err(lambda: stranger.report_incident(inc["id"], "false_alarm")).status == 404
    assert _err(lambda: v1.report_incident(inc["id"], "made_up")).code == "invalid_kind"
    v1.report_incident(inc["id"], "false_alarm")
    v1.report_incident(inc["id"], "abusive")               # אותו מתנדב - נספר פעם אחת
    assert not httpd.service.owner_abuse("eve")["limited"]
    v2.report_incident(inc["id"], "false_alarm", "אין אף אחד במקום")
    assert httpd.service.owner_abuse("eve")["limited"]
    eve.update_incident(inc["id"], status="resolved")
    nxt = eve.create_incident("b", location=TLV, risk=HIGH)
    assert len(_vols(nxt)) == 1                              # בלי פנייה מקבילה


def test_hourly_cap_stops_harassing_volunteers(srv, monkeypatch):
    httpd, url = srv
    monkeypatch.setattr(A, "VOLUNTEERS_PER_HOUR_CAP", 2)
    for i in range(4):
        _vol(url, f"vol{i}", {**NEAR, "lat": 32.086 + i * 0.001})
    eve = _c(url, "eve")
    first = eve.create_incident("a", location=TLV, risk=HIGH)
    assert len(_vols(first)) == 2                            # המכסה נגמרת בדרך (3 -> 2)
    eve.update_incident(first["id"], status="resolved")
    second = eve.create_incident("b", location=TLV, risk=HIGH)
    assert len(_vols(second)) == 1                           # עדיין אחד לכל אירוע חדש
    httpd.service.run_escalation_tick()
    assert len(_vols(eve.get_incident(second["id"]))) == 1   # ולא יותר


# ------------------------------------------------------------------ זמן הגעה בהליכה
@pytest.fixture
def fake_osrm():
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            calls.append(self.path)
            coords = self.path.split("/table/v1/foot/")[1].split("?")[0].split(";")
            n = len(coords) - 1
            # המתנדב הראשון ברשימה "מעבר לכביש מהיר": קרוב באוויר, 25 דקות הליכה. השאר: 5 דקות.
            durs = [[1500.0]] + [[300.0]] * (n - 1)
            body = json.dumps({"code": "Ok", "durations": durs, "distances": [[1900.0]] + [[380.0]] * (n - 1)})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode())

    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_address[1]}", calls
    s.shutdown()


def test_estimate_without_router_never_calls_out():
    r = R.Router(base_url="")
    assert not r.configured
    out = r.walking_from((32.0853, 34.7818), [(32.0890, 34.7818)])
    assert out[0]["source"] == "estimate" and 350 < out[0]["seconds"] < 450


def test_volunteer_chosen_by_walking_time_not_straight_line(srv, fake_osrm):
    httpd, url = srv
    base, calls = fake_osrm
    httpd.service.router = R.Router(base_url=base)
    _vol(url, "across_highway", {**TLV, "lat": 32.0858})        # ~50 מ' באוויר
    _vol(url, "walkable", {**TLV, "lat": 32.0890})              # ~400 מ'
    dana = _c(url, "dana")
    inc = dana.create_incident("a", location=TLV)
    assert _vols(inc) == ["walkable"]
    assert calls and "/table/v1/foot/" in calls[0] and "destinations=" in calls[0]
    rec = httpd.service._q("SELECT eta_seconds, eta_source FROM recipients WHERE incident_id=? AND role='volunteer'",
                           (inc["id"],), one=True)
    assert rec["eta_seconds"] == 300.0 and rec["eta_source"] == "osrm"
    walker = _c(url, "walkable")
    view = walker.get_incident(inc["id"])
    assert view["eta_minutes"] == 5 and view["eta_source"] == "osrm"
    # לבעל האירוע: אין מרחק/זמן לפני שהמתנדב קיבל (נגד איתור מתנדבים), אחרי קבלה - טווח מעוגל
    owner_rec = next(r for r in dana.get_incident(inc["id"])["recipients"] if r["role"] == "volunteer")
    assert owner_rec["distance_km"] is None and owner_rec["eta_minutes"] is None and owner_rec["distance_band_km"] is None
    walker.respond(inc["id"], "accept")
    owner_rec = next(r for r in dana.get_incident(inc["id"])["recipients"] if r["role"] == "volunteer")
    assert owner_rec["distance_band_km"] == 0.5 and owner_rec["eta_minutes"] == 5 and owner_rec["distance_km"] is None


def test_router_down_falls_back_to_estimate_and_still_dispatches(srv):
    httpd, url = srv
    httpd.service.router = R.Router(base_url="http://127.0.0.1:9", timeout=0.3)   # אין שרת
    _vol(url, "vol", NEAR)
    inc = _c(url, "dana").create_incident("a", location=TLV)
    rec = httpd.service._q("SELECT username, eta_source FROM recipients WHERE incident_id=? AND role='volunteer'",
                           (inc["id"],), one=True)
    assert rec["username"] == "vol" and rec["eta_source"] == "estimate"
    assert httpd.service.router.last_error

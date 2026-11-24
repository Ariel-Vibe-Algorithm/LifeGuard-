# -*- coding: utf-8 -*-
"""מיקום ידני ב-SOS: פענוח קואורדינטות/קישורים, חיפוש כתובת, קדימות GPS > ידני > IP, והגעה לשרת."""
import threading
import time

import pytest

import manual_location as ML
from test_ui_navigation import shared_root  # noqa: F401


@pytest.mark.parametrize("text,expected", [
    ("32.0853, 34.7818", (32.0853, 34.7818)),
    ("32.0853 34.7818", (32.0853, 34.7818)),
    ("https://www.google.com/maps?q=32.0853,34.7818", (32.0853, 34.7818)),
    ("https://www.google.com/maps/place/X/@32.08,34.78,17z/data=!3d32.0853!4d34.7818", (32.0853, 34.7818)),
    ("https://www.google.com/maps/@32.0853,34.7818,15z", (32.0853, 34.7818)),
    ("https://www.openstreetmap.org/?mlat=32.0853&mlon=34.7818#map=17/32.0853/34.7818", (32.0853, 34.7818)),
    ("https://www.openstreetmap.org/#map=17/32.0853/34.7818", (32.0853, 34.7818)),
    ("https://waze.com/ul?ll=32.0853%2C34.7818&navigate=yes", (32.0853, 34.7818)),
    ("geo:32.0853,34.7818", (32.0853, 34.7818)),
])
def test_coordinates_and_map_links(text, expected):
    assert ML.parse_coordinates(text) == expected


@pytest.mark.parametrize("text", ["דיזנגוף 50 תל אביב", "קומה 3", "999, 999", "0, 0", ""])
def test_not_coordinates(text):
    assert ML.parse_coordinates(text) is None


def test_resolve_coordinates_address_and_text_only():
    r = ML.resolve("32.0853, 34.7818", geocoder=lambda t: pytest.fail("no network for coordinates"))
    assert r["available"] and r["source"] == "manual" and r["is_precise"]
    a = ML.resolve("דיזנגוף 50 תל אביב", geocoder=lambda t: {"lat": 32.078, "lon": 34.774, "display": "דיזנגוף 50",
                                                              "kind": "house"})
    assert a["available"] and a["source"] == "manual_address" and a["is_precise"] and a["accuracy_m_estimate"] == 30
    city = ML.resolve("תל אביב", geocoder=lambda t: {"lat": 32.08, "lon": 34.78, "kind": "city"})
    assert city["available"] and not city["is_precise"]                      # עיר שלמה ≠ מיקום מדויק
    t = ML.resolve("ליד הקיוסק בפארק", geocoder=lambda t: None)
    assert not t["available"] and t["manual_text"] == "ליד הקיוסק בפארק"      # הטקסט עדיין נשלח


def test_geocode_handles_network_errors():
    class Boom:
        def get(self, *a, **k):
            import requests
            raise requests.ConnectionError("offline")
    assert ML.geocode("דיזנגוף 50", session=Boom()) is None


def test_engine_priority_manual_over_ip_but_gps_wins(temp_env, user, monkeypatch):
    import engine as em
    from engine import LifeGuardEngine
    monkeypatch.setattr(em.map_module, "find_safe_route", lambda *a, **k: None)     # בלי רשת
    eng = LifeGuardEngine(username=user)
    try:
        eng.current_event = {"event_id": "lg-t-1", "kind": "SOS", "locations": []}
        sent, msgs = [], []
        eng.gateway.update_location_now = lambda eid, loc: sent.append(loc)
        eng.gateway.send_message = lambda eid, text: msgs.append(text)
        r = eng.set_manual_location("רחוב הרצל 5", resolver=lambda t: ML.resolve(
            t, geocoder=lambda q: {"lat": 31.25, "lon": 34.79, "display": "הרצל 5, באר שבע", "kind": "house"}))
        assert r["available"] and sent[0]["lat"] == 31.25 and "הרצל 5" in msgs[0]
        ip = {"available": True, "lat": 32.0, "lon": 34.8, "is_precise": False, "source": "ip"}
        assert eng._effective_location(ip)["source"] == "manual_address"         # ידני עדיף על IP
        gps = {"available": True, "lat": 31.26, "lon": 34.80, "is_precise": True, "source": "GNSS/NMEA"}
        assert eng._effective_location(gps) is gps                                # GPS אמיתי גובר
        eng.set_manual_location("ליד הקיוסק", resolver=lambda t: ML.resolve(t, geocoder=lambda q: None))
        assert "ליד הקיוסק" in msgs[-1] and len(sent) == 1                        # טקסט בלבד - בלי קואורדינטות
    finally:
        eng.shutdown()


def _route_engine(user, monkeypatch, calls):
    import engine as em
    from engine import LifeGuardEngine
    place = {"name": "תחנת משטרה", "type_label": "משטרה", "lat": 31.252, "lon": 34.791, "distance_m": 300}
    monkeypatch.setattr(em.map_module, "find_safe_route",
                        lambda lat, lon, online=True, **k: (calls.append((lat, lon)) if online else None)
                        or {"place": place, "route": None})
    monkeypatch.setattr(em.map_module, "get_walking_route", lambda *a, **k: None)    # בלי רשת
    eng = LifeGuardEngine(username=user)
    eng.current_event = {"event_id": "lg-t-2", "kind": "SOS", "locations": []}
    eng.gateway.update_location_now = lambda eid, loc: None
    eng.gateway.send_message = lambda eid, text: None
    routes = []
    eng.on_route_update = routes.append
    return eng, routes


def _wait(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end and not pred():
        time.sleep(0.02)
    return pred()


def test_typed_address_triggers_safe_route_from_that_address(temp_env, user, monkeypatch):
    """באג: כשאין GPS וכותבים כתובת - לא חושב מסלול למקום בטוח (חושב פעם אחת בהפעלה, ורק מ-GPS)."""
    calls = []
    eng, routes = _route_engine(user, monkeypatch, calls)
    try:
        eng.set_manual_location("הרצל 5 באר שבע", resolver=lambda t: ML.resolve(
            t, geocoder=lambda q: {"lat": 31.25, "lon": 34.79, "display": "הרצל 5, באר שבע", "kind": "house"}))
        assert _wait(lambda: routes), "לא חושב מסלול אחרי הזנת כתובת"
        assert calls == [(31.25, 34.79)]                                        # מהכתובת - לא מ-IP
        assert routes[-1]["result"]["place"]["name"] == "תחנת משטרה"
        assert routes[-1]["manual"] and routes[-1]["accuracy_m"] == 30          # מסומן "מהכתובת שכתבת"
        # רענון המסלול בהמשך (כמו בהפעלה) משתמש בכתובת שהוזנה כשיש רק IP
        ip = {"available": True, "lat": 32.0, "lon": 34.8, "is_precise": False, "source": "ip"}
        eng._find_safe_route_for_location(ip)
        assert calls[-1] == (31.25, 34.79)
    finally:
        eng.shutdown()


def test_city_level_address_asks_for_street_instead_of_misleading_route(temp_env, user, monkeypatch):
    calls = []
    eng, routes = _route_engine(user, monkeypatch, calls)
    try:
        eng.set_manual_location("באר שבע", resolver=lambda t: ML.resolve(
            t, geocoder=lambda q: {"lat": 31.25, "lon": 34.79, "kind": "city"}))
        assert _wait(lambda: routes)
        assert calls == [] and routes[-1]["result"] is None and "רחוב ומספר" in routes[-1]["reason"]
        # רק IP (בלי כתובת): הסבר ברור מה לעשות, בלי מסלול ממרכז העיר
        eng._manual_location = None
        eng._find_safe_route_for_location({"available": True, "lat": 32, "lon": 34.8, "is_precise": False,
                                           "source": "ip"})
        assert calls == [] and "כתבו כתובת" in routes[-1]["reason"]
    finally:
        eng.shutdown()


def test_route_label_says_it_came_from_typed_address(shared_root):  # noqa: F811
    import types
    from main import SosWindow
    fake = types.SimpleNamespace(route_text_label=None, map_widget=None, _dest_marker=None,
                                 engine=types.SimpleNamespace(log=lambda *a: None))
    import tkinter as tk
    fake.route_text_label = tk.Label(shared_root)
    place = {"name": "תחנת משטרה", "type_label": "משטרה", "lat": 1, "lon": 2, "distance_m": 300}
    SosWindow.update_route(fake, {"result": {"place": place, "route": None}, "manual": True, "accuracy_m": 30})
    assert "מהכתובת שכתבת" in fake.route_text_label.cget("text") and "תחנת משטרה" in fake.route_text_label.cget("text")
    SosWindow.update_route(fake, {"result": None, "reason": "הכתובת שכתבת כללית מדי"})
    assert "כללית מדי" in fake.route_text_label.cget("text")
    fake.route_text_label.destroy()


def test_manual_location_reaches_responders_through_real_server(tmp_path):
    from alert_gateway import AlertGateway
    from backend_client import BackendClient
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "m.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        dana, mom = BackendClient(url), BackendClient(url)
        dana.login_or_register("dana", "StrongPass!2026")
        mom.login_or_register("mom", "StrongPass!2026")
        dana.invite("mom")
        mom.respond_invite("dana", True)
        gw = AlertGateway("dana", dana, poll_seconds=0.2)
        gw.open_incident("lg-dana-1", mode="loud")
        loc = ML.resolve("32.0853, 34.7818")
        gw.update_location_now("lg-dana-1", loc)
        gw.send_message("lg-dana-1", "📍 המיקום שלי (כתבתי ידנית): 32.0853, 34.7818 - קומה 2")
        end = time.time() + 15
        view = None
        while time.time() < end:
            sid = gw.status("lg-dana-1").get("server_id")
            if sid:
                view = mom.get_incident(sid)
                if view.get("location") and view.get("messages"):
                    break
            time.sleep(0.2)
        assert view and view["location"]["source"] == "manual" and abs(view["location"]["lat"] - 32.0853) < 1e-6
        assert any("קומה 2" in m["text"] for m in view["messages"])
        gw.shutdown()
    finally:
        httpd.shutdown()
        httpd.service.close()

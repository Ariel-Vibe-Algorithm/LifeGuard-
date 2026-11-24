# -*- coding: utf-8 -*-
"""
המנוע האמיתי (כפתור SOS / כפייה / ביטול) מול שרת LifeGuard אמיתי:
- SOS מגיע לאפליקציה של איש הקשר המהימן ולמתנדב, והמצב במסך מבוסס על אישורי שרת.
- מיקום חי מגיע לנמענים.
- ביטול נסגר בשרת.
- מצב כפייה: רק מהימנים, בלי שום דבר גלוי.
- שרת לא זמין בתחילת האירוע -> מוצג "לא נשלח", ואז נשלח אוטומטית כשהשרת עולה.
"""
import socket
import threading
import time

import pytest

import alert_gateway
from backend_client import BackendClient
from engine import LifeGuardEngine, State
from server.lifeguard_server import create_server

HOME = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "available": True, "source": "GNSS/NMEA",
        "accuracy_m_estimate": 6, "city": "GPS fix", "accuracy": "GPS", "timestamp": None}


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start(tmp_path, port):
    httpd = create_server("127.0.0.1", port, str(tmp_path / "srv.db"), ack_timeout_seconds=30, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    return None


@pytest.fixture
def quiet_engine(monkeypatch, user):
    import engine as em
    for name, fn in (("siren_module", "play_siren"), ("audio_module", "record_loop"),
                     ("camera_module", "capture_loop"), ("voice_warning_module", "warning_loop")):
        monkeypatch.setattr(getattr(em, name), fn, lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location", lambda *a, **k: dict(HOME, timestamp=time.time()))
    monkeypatch.setattr(em.map_module, "find_safe_route", lambda *a, **k: None)
    monkeypatch.setattr(alert_gateway, "LOCATION_MIN_INTERVAL_SECONDS", 0.2)

    def make(url, on_delivery=None):
        client = BackendClient(url)
        client.login_or_register(user, "StrongPass!2026", "Tester")
        gw = alert_gateway.AlertGateway(user, client, poll_seconds=0.3)
        return LifeGuardEngine(user, gateway=gw, on_delivery_update=on_delivery)
    return make


def _contacts(url, owner_engine):
    mom = BackendClient(url)
    mom.login_or_register("mom", "StrongPass!2026", "Mom")
    vol = BackendClient(url)
    vol.login_or_register("vol", "StrongPass!2026", "Volunteer")
    vol.set_volunteer(True, radius_km=5)
    vol.update_presence({"lat": 32.0870, "lon": 34.7820, "is_precise": True})
    owner = owner_engine.gateway.client
    owner.invite("mom")
    mom.respond_invite(owner.username, True)
    return mom, vol


def test_sos_button_reaches_trusted_and_volunteer_with_confirmed_delivery(tmp_path, quiet_engine):
    port = _free_port()
    httpd = _start(tmp_path, port)
    url = f"http://127.0.0.1:{port}"
    updates = []
    eng = quiet_engine(url, on_delivery=updates.append)
    mom, vol = _contacts(url, eng)
    got = []
    mom.start_stream(got.append)

    eng.trigger(force_panic=True, source="button")
    assert eng.state == State.ACTIVE_SOS
    # מסירה ל-mom מאושרת ע"י השרת רק אחרי שהאפליקציה שלה אישרה קבלה
    assert _wait(lambda: any(n["type"] == "incident_new" for n in got))
    st = _wait(lambda: (lambda s: s if s["delivered"] >= 1 else None)(eng.get_delivery_status()))
    assert st, eng.get_delivery_status()
    # המתנדב מקבל ומאשר -> המסך אצל המשתמש מציג אישור אנושי
    inc_id = st["server_id"]
    # הערכת הסכנה מהמכשיר הגיעה לשרת: מוצגת לאיש הקשר, לא לבעלת האירוע
    assert mom.get_incident(inc_id)["risk"]["level"] == "HIGH"
    assert "risk" not in eng.gateway.client.get_incident(inc_id)
    vol.respond(inc_id, "accept")
    st = _wait(lambda: (lambda s: s if s["human_ack"] else None)(eng.get_delivery_status()))
    assert st and st["level"] == "ok" and "Volunteer" in st["text"]
    # הממשק מקבל עדכון (אסינכרוני: ה-worker מעדכן את המצב, רושם לציר הזמן ורק אז קורא ל-callback.
    # בדיקה מיידית כאן נכשלה ב-~1 מתוך 12 הרצות תחת עומס - race בבדיקה, לא באפליקציה)
    assert _wait(lambda: any(u["level"] == "ok" for u in updates)), updates

    # מיקום חי: הזזת המשתמש מגיעה ל-mom
    import engine as em
    em.location_module.get_location = lambda *a, **k: dict(HOME, lat=32.0900, timestamp=time.time())
    assert _wait(lambda: mom.get_incident(inc_id)["location"]["lat"] == 32.0900)

    # ביטול: האירוע נסגר בשרת ו-mom מקבלת הודעת סגירה
    assert eng.cancel_with_password("StrongPass!2026")
    assert _wait(lambda: mom.get_incident(inc_id)["status"] == "cancelled")
    assert _wait(lambda: any(n["type"] == "incident_closed" for n in got))
    mom.stop_stream()
    eng.shutdown()
    httpd.shutdown()


def test_duress_sends_silent_alert_to_trusted_only(tmp_path, quiet_engine):
    port = _free_port()
    httpd = _start(tmp_path, port)
    url = f"http://127.0.0.1:{port}"
    visible = []
    eng = quiet_engine(url)
    eng.on_log = visible.append
    mom, vol = _contacts(url, eng)
    visible.clear()
    eng.activate_duress_mode(source="typed")
    assert eng.state == State.SILENT_TRACKING
    inbox = _wait(lambda: mom.inbox())
    assert inbox and inbox[0]["mode"] == "silent"
    assert vol.inbox() == []                      # מתנדב זר לא מוזעק במצוקה שקטה
    assert not any("שרת" in m or "התקבל" in m for m in visible)  # כלום לא גלוי במסך
    eng.end_silent_tracking()
    assert _wait(lambda: mom.get_incident(inbox[0]["id"])["status"] == "resolved")
    eng.shutdown()
    httpd.shutdown()


def test_server_down_then_up_alert_is_sent_late_and_never_claimed_before(tmp_path, quiet_engine):
    port = _free_port()
    first = _start(tmp_path, port)
    url = f"http://127.0.0.1:{port}"
    eng = quiet_engine(url)            # התחברות כשהשרת עוד זמין
    mom, _ = _contacts(url, eng)
    first.shutdown()
    first.server_close()
    first.service.close()

    eng.trigger(force_panic=True, source="button")
    st = _wait(lambda: (lambda s: s if s["level"] == "critical" and s["server_id"] is None else None)(
        eng.get_delivery_status()), 4)
    assert st and "לא הגיעה לשרת" in st["text"]   # כנות: עדיין לא נשלח

    second = _start(tmp_path, port)    # אותו DB - השרת "חוזר"
    st = _wait(lambda: (lambda s: s if s["server_id"] else None)(eng.get_delivery_status()), 10)
    assert st, "ההתראה לא נשלחה אחרי שהשרת חזר"
    assert _wait(lambda: mom.inbox())
    eng.cancel_with_password("StrongPass!2026")
    eng.shutdown()
    second.shutdown()


def test_safety_timer_is_mirrored_to_server_escort_and_expiry_links_incident(tmp_path, quiet_engine):
    """טיימר הבטיחות המקומי יוצר ליווי בשרת (העוקבים רואים מיקום), הגעה נסגרת בשרת,
    ותום הזמן במכשיר פותח אירוע שמקושר לליווי - בלי אירוע כפול מהשרת."""
    port = _free_port()
    httpd = _start(tmp_path, port)
    url = f"http://127.0.0.1:{port}"
    eng = quiet_engine(url)
    mom, _vol = _contacts(url, eng)
    try:
        ok, _ = eng.start_safety_timer(15, destination="הביתה")
        assert ok
        watching = _wait(lambda: mom.escorts()["watching"])
        assert watching and watching[0]["destination"] == "הביתה" and watching[0]["location"]["lat"] == HOME["lat"]
        eid = watching[0]["id"]
        assert eng.confirm_safe_arrival()[0]
        assert _wait(lambda: mom.get_escort(eid)["status"] == "arrived")

        eng.start_safety_timer(15)
        eid2 = _wait(lambda: eng._escort_id)
        assert eid2 and eid2 != eid
        eng._safety_timer_deadline = time.time() + 0.3  # קיצור הזמן במכשיר בלבד
        assert _wait(lambda: eng.state == State.ACTIVE_SOS)
        st = _wait(lambda: (lambda s: s if s.get("server_id") else None)(eng.get_delivery_status()))
        esc = _wait(lambda: (lambda e: e if e["status"] == "escalated" else None)(mom.get_escort(eid2)))
        assert esc and esc["incident_id"] == st["server_id"]
        assert len(httpd.service._q("SELECT id FROM incidents")) == 1
        eng.cancel_with_password("StrongPass!2026")
    finally:
        eng.shutdown()
        httpd.shutdown()


def test_arrival_confirmed_before_server_created_escort_is_sent_as_arrived(tmp_path, quiet_engine):
    """באג שנמצא: 'הגעתי' לפני שהשרת סיים ליצור את הליווי נשלח כ'בוטל' (העוקבים לא ראו הגעה)."""
    port = _free_port()
    httpd = _start(tmp_path, port)
    url = f"http://127.0.0.1:{port}"
    eng = quiet_engine(url)
    mom, _vol = _contacts(url, eng)
    client = eng.gateway.client
    real = client.start_escort

    def slow(*a, **k):
        time.sleep(0.6)  # השרת איטי
        return real(*a, **k)
    client.start_escort = slow
    try:
        for n, (action, expected) in enumerate((("arrive", "arrived"), ("cancel", "cancelled")), start=1):
            eng.start_safety_timer(15)
            time.sleep(0.1)
            (eng.confirm_safe_arrival if action == "arrive" else eng.cancel_safety_timer)()
            st = _wait(lambda: (lambda rows: rows[n - 1]["status"] if len(rows) >= n
                                and rows[n - 1]["status"] != "active" else None)(
                httpd.service._q("SELECT status FROM escorts ORDER BY started_at")))
            assert st == expected, (action, st)
    finally:
        eng.shutdown()
        httpd.shutdown()

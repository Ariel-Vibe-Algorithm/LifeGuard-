# -*- coding: utf-8 -*-
"""
בדיקות קצה-לקצה מול שרת LifeGuard אמיתי (HTTP + SSE + escalation thread) על פורט זמני.
תרחיש: dana בסכנה; mom איש קשר מהימן; vol_near / vol_far מתנדבים; stranger זר.
"""
import threading
import time

import pytest

from backend_client import BackendClient, BackendError
from server.lifeguard_server import create_server

TLV = (32.0853, 34.7818)
NEAR = (32.0880, 34.7830)   # ~0.3 ק"מ
FAR = (32.1050, 34.7900)    # ~2.3 ק"מ


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=1.0, tick_seconds=0.2)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.service.close()


def _client(url, name):
    c = BackendClient(url)
    c.login_or_register(name, "StrongPass!2026", name.title())
    return c


def _loc(lat_lon, precise=True):
    return {"lat": lat_lon[0], "lon": lat_lon[1], "is_precise": precise, "source": "GNSS/NMEA",
            "accuracy_m_estimate": 8}


@pytest.fixture
def world(server):
    dana, mom, near, far, stranger = (_client(server, n) for n in ("dana", "mom", "vol_near", "vol_far", "stranger"))
    dana.invite("mom")
    mom.respond_invite("dana", True)
    for c, pos in ((near, NEAR), (far, FAR)):
        c.set_volunteer(True, radius_km=5, skills=["first_aid"])
        c.update_presence(_loc(pos))
    dana.update_presence(_loc(TLV))
    return {"dana": dana, "mom": mom, "near": near, "far": far, "stranger": stranger, "url": server}


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.1)
    return None


def _recipient(view, username):
    return next((r for r in view["recipients"] if r["username"] == username), None)


def test_trusted_contact_requires_consent(server):
    a, b = _client(server, "alice"), _client(server, "bob")
    a.invite("bob")  # טרם אושר
    inc = a.create_incident("c1", location={"lat": 32, "lon": 34, "is_precise": True})
    assert _recipient(inc, "bob") is None  # בלי הסכמה - לא מקבל מיקום/התראה


def test_sos_reaches_trusted_and_nearest_volunteer_with_server_confirmed_delivery(world):
    dana, mom, near = world["dana"], world["mom"], world["near"]
    inc = dana.create_incident("evt-1", emergency_type="medical", severity="high",
                               location={**_loc(TLV), "accuracy_m": 8},
                               profile={"display_name": "Dana", "medical_notes": "אלרגיה לפניצילין"})
    assert _recipient(inc, "mom")["state"] == "notified"
    assert _recipient(inc, "vol_near")["state"] == "notified"     # הקרוב ביותר נבחר
    assert _recipient(inc, "vol_far") is None                      # עדיין לא
    # עד שהאפליקציה של mom לא אישרה קבלה - לא מוצג "נמסר"
    got = []
    mom.start_stream(lambda n: got.append(n))
    assert _wait(lambda: any(n["type"] == "incident_new" for n in got))
    view = _wait(lambda: (lambda v: v if _recipient(v, "mom")["state"] == "delivered" else None)(dana.get_incident(inc["id"])))
    assert view, "המסירה ל-mom לא אושרה ע\"י השרת"
    # mom (מהימנה) רואה מיקום מדויק ומידע רפואי מיד
    mv = mom.get_incident(inc["id"])
    assert mv["full_access"] and mv["location"]["lat"] == TLV[0]
    # סקירת אבטחה: מידע רפואי לא נשמר בפרופיל הגלוי ולא מועתק להתראות - רק בערוץ המוצפן (/me/medical)
    assert "medical_notes" not in mv["profile"] and mv["profile"]["display_name"] == "Dana"
    mom.stop_stream()


def test_volunteer_sees_only_approximate_location_until_accepting(world):
    dana, near = world["dana"], world["near"]
    inc = dana.create_incident("evt-2", location=_loc(TLV))
    v = near.get_incident(inc["id"])
    assert v["full_access"] is False
    assert v["location"]["approximate"] is True and v["location"]["lat"] != TLV[0]
    assert "profile" not in v
    accepted = near.respond(inc["id"], "accept")
    assert accepted["full_access"] is True and accepted["location"]["lat"] == TLV[0]
    owner_view = dana.get_incident(inc["id"])
    assert _recipient(owner_view, "vol_near")["state"] == "accepted"


def test_escalates_to_next_volunteer_when_no_response(world):
    dana, far = world["dana"], world["far"]
    inc = dana.create_incident("evt-3", location=_loc(TLV))
    # ack timeout בבדיקה = שנייה אחת. vol_near לא מגיב -> הסלמה ל-vol_far
    view = _wait(lambda: (lambda v: v if _recipient(v, "vol_far") else None)(dana.get_incident(inc["id"])), 6)
    assert view, "לא הייתה הסלמה למתנדב הבא"
    assert _recipient(view, "vol_near")["state"] == "expired"
    with pytest.raises(BackendError):  # מתנדב שעבר הסלמה מאבד גישה
        world["near"].get_incident(inc["id"])
    far.respond(inc["id"], "accept")
    time.sleep(1.5)  # אחרי קבלה - אין הסלמה נוספת
    final = dana.get_incident(inc["id"])
    assert [r["username"] for r in final["recipients"] if r["role"] == "volunteer"] == ["vol_near", "vol_far"]


def test_decline_escalates_immediately(world):
    dana, near = world["dana"], world["near"]
    inc = dana.create_incident("evt-4", location=_loc(TLV))
    near.respond(inc["id"], "decline")
    view = dana.get_incident(inc["id"])
    assert _recipient(view, "vol_far") is not None


def test_live_location_updates_reach_recipients(world):
    dana, mom = world["dana"], world["mom"]
    inc = dana.create_incident("evt-5", location=_loc(TLV))
    got = []
    mom.start_stream(lambda n: got.append(n))
    _wait(lambda: got)
    dana.update_location(inc["id"], {"lat": 32.09, "lon": 34.79, "is_precise": True, "source": "GNSS"})
    assert _wait(lambda: any(n["type"] == "incident_update" and n["payload"]["event"] == "location" for n in got))
    assert mom.get_incident(inc["id"])["location"]["lat"] == 32.09
    mom.stop_stream()


def test_stranger_cannot_access_incident(world):
    inc = world["dana"].create_incident("evt-6", location=_loc(TLV))
    with pytest.raises(BackendError) as e:
        world["stranger"].get_incident(inc["id"])
    assert e.value.status == 404  # לא חושפים אפילו את קיום האירוע
    with pytest.raises(BackendError):
        world["stranger"].update_location(inc["id"], _loc(TLV))
    with pytest.raises(BackendError):
        world["mom"].update_incident(inc["id"], status="cancelled")  # רק הבעלים


def test_idempotent_create_on_retry(world):
    a = world["dana"].create_incident("same-id", location=_loc(TLV))
    b = world["dana"].create_incident("same-id", location=_loc(TLV))
    assert a["id"] == b["id"]


def test_silent_mode_notifies_trusted_only_until_escalated(world):
    dana = world["dana"]
    inc = dana.create_incident("evt-7", mode="silent", location=_loc(TLV))
    roles = {r["role"] for r in inc["recipients"]}
    assert roles == {"trusted"}
    view = dana.update_incident(inc["id"], mode="loud")
    assert any(r["role"] == "volunteer" for r in view["recipients"])


def test_closing_incident_revokes_volunteer_access_and_notifies(world):
    dana, near, mom = world["dana"], world["near"], world["mom"]
    inc = dana.create_incident("evt-8", location=_loc(TLV))
    near.respond(inc["id"], "accept")
    dana.update_incident(inc["id"], status="resolved")
    with pytest.raises(BackendError):
        near.get_incident(inc["id"])
    assert mom.get_incident(inc["id"])["status"] == "resolved"   # מהימנים שומרים סיכום
    assert any(n["type"] == "incident_closed" for n in mom.poll())


def test_unauthenticated_and_bad_password(server):
    c = BackendClient(server)
    with pytest.raises(BackendError):
        c.inbox()
    _client(server, "eve")
    with pytest.raises(BackendError):
        BackendClient(server).login("eve", "wrong-password")


def test_insecure_remote_url_is_refused():
    with pytest.raises(BackendError):
        BackendClient("http://example.com:8765")


def test_street_intel_never_returns_fabricated_scores(world):
    r = world["dana"].street_intel("34.7,32.0,34.8,32.1")
    assert r["status"] == "no_data" and r["segments"] == []


def test_messages_require_acceptance_for_volunteers(world):
    dana, near = world["dana"], world["near"]
    inc = dana.create_incident("evt-9", location=_loc(TLV))
    with pytest.raises(BackendError):
        near.send_message(inc["id"], "אני בדרך")
    near.respond(inc["id"], "accept")
    near.send_message(inc["id"], "אני בדרך")
    assert dana.get_incident(inc["id"])["messages"][0]["text"] == "אני בדרך"

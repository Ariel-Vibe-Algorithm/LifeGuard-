# -*- coding: utf-8 -*-
"""
מתנדבים שלא חייבים להיות מד"א: שיחת תמיכה מרחוק (בלי שם ובלי מיקום של הפונה), הזעקת רשויות (מאומת/ת,
רואה מיקום ומדווח/ת מה עשה/תה), בקשת שיחה שאינה חירום, והזעקה אוטומטית כשאף אחד לא קיבל.
"""
import json
import threading
import time

import pytest

from server import helpers as HP
from server.lifeguard_server import LifeGuardService, create_server
from backend_client import BackendClient

PW = "StrongPass!2026"
LOC = {"lat": 32.08, "lon": 34.78, "is_precise": True}


@pytest.fixture
def svc(tmp_path):
    s = LifeGuardService(str(tmp_path / "h.db"))
    for u in ("dana", "mom", "talker", "talker2", "liaison", "newbie", "medic"):
        s.register(u, PW)
    s._x("UPDATE users SET created_at=?", (time.time() - 10 * 86400,))
    s._x("UPDATE users SET created_at=? WHERE username='newbie'", (time.time(),))
    for u in ("talker", "talker2", "newbie"):
        s.set_volunteer(u, True, 5)
        s.set_volunteer_roles(u, ["remote_support"], ["he"])
    s.set_volunteer("liaison", True, 5)
    s.set_volunteer_roles("liaison", ["authority_liaison"])
    yield s
    s.close()


def _helpers(s, iid, kind=None):
    q = "SELECT username, kind, state FROM helpers WHERE incident_id=?" + (" AND kind=?" if kind else "")
    return s._q(q, (iid, kind) if kind else (iid,))


def test_roles_and_eligibility_without_any_organization(svc):
    r = svc.volunteer_roles("talker")
    assert r["roles"] == ["remote_support"] and r["effective"]["remote_support"] == "active"
    assert "3 ימים" in svc.volunteer_roles("newbie")["effective"]["remote_support"]      # חשבון חדש
    assert "ממתין לאימות" in svc.volunteer_roles("liaison")["effective"]["authority_liaison"]
    svc.request_verification("liaison", "אימות זהות ידני", "ת.ז נבדקה")               # לא ארגון
    svc.admin_verify("liaison", True, "נבדקה תעודה מזהה")
    assert svc.volunteer_roles("liaison")["effective"]["authority_liaison"] == "active"
    with pytest.raises(Exception):
        svc.set_volunteer_roles("talker", ["astronaut"])


def test_support_request_is_not_an_emergency_and_hides_identity(svc):
    svc._x("INSERT INTO relationships(owner, contact, status, created_at, responded_at) "
           "VALUES ('dana','mom','accepted',0,0)")
    svc.set_volunteer("medic", True, 5)
    svc.update_presence("medic", 32.081, 34.781, is_precise=True)
    v = svc.create_support_request("dana", "קשה לי הערב", "he")
    iid = v["id"]
    assert v["hotlines"] and any(h["number"] == "1201" for h in v["hotlines"])
    assert svc._incident_row(iid)["activation"] == "support"
    assert not svc._q("SELECT 1 FROM recipients WHERE incident_id=?", (iid,))   # בלי אנשי קשר / מתנדבים בשטח
    svc.run_dispatch_queue()
    assert not svc._q("SELECT 1 FROM recipients WHERE incident_id=?", (iid,))
    offered = {h["username"] for h in _helpers(svc, iid)}
    assert offered <= {"talker", "talker2"} and offered and "newbie" not in offered
    first = sorted(offered)[0]
    hv = svc.get_incident(first, iid)
    assert hv["my_role"] == "helper" and hv["owner"] is None and "location" not in hv
    assert "dana" not in json.dumps(hv, ensure_ascii=False) and svc._display("dana") not in json.dumps(hv, ensure_ascii=False)
    with pytest.raises(Exception):
        svc.add_message(first, iid, "שלום")                                     # לפני קבלה
    svc.helper_respond(first, iid, "accept")
    other = (offered - {first})
    if other:
        with pytest.raises(Exception):
            svc.helper_respond(other.pop(), iid, "accept")                      # כבר נלקח
    svc.add_message(first, iid, "אני כאן איתך")
    svc.add_message("dana", iid, "תודה")
    msgs = svc.get_incident(first, iid)["messages"]
    assert [m["sender_display"] for m in msgs][0] == "הפונה" and all(m["sender"] != "dana" for m in msgs)
    owner_view = svc.get_incident("dana", iid)
    assert owner_view["helpers"][0]["state"] == "accepted" and owner_view["hotlines"]
    with pytest.raises(Exception):
        svc.request_helper("dana", iid, "authority_liaison")                     # לא חירום
    again = svc.create_support_request("dana")                                  # בקשה אחת פתוחה - חוזרים אליה
    assert again["id"] == iid and again["resumed"]
    assert svc._q("SELECT COUNT(*) AS n FROM incidents WHERE owner='dana' AND activation='support'", one=True)["n"] == 1
    svc.update_incident("dana", iid, {"status": "resolved"})
    with pytest.raises(Exception):
        svc.get_incident(first, iid)                                            # נסגר - אין גישה


def test_liaison_sees_location_reports_actions_and_is_audited(svc):
    svc.request_verification("liaison", "אימות זהות ידני", "123")
    svc.admin_verify("liaison", True)
    inc = svc.create_incident("dana", "e1", mode="silent", location=LOC)
    svc.request_helper("dana", inc["id"], "authority_liaison", "לא יכולה לדבר")
    assert [h["username"] for h in _helpers(svc, inc["id"], "authority_liaison")] == ["liaison"]
    before = svc.get_incident("liaison", inc["id"])
    assert "location" not in before and before["owner"] is None                # לפני קבלה - כלום
    svc.helper_respond("liaison", inc["id"], "accept")
    v = svc.get_incident("liaison", inc["id"])
    assert v["location"]["lat"] == 32.08 and v["owner_display"] == svc._display("dana")
    assert "called_police" in v["liaison_actions"] and v["guidance"]
    svc.helper_respond("liaison", inc["id"], "called_police")
    events = [e["kind"] for e in svc._q("SELECT kind FROM incident_events WHERE incident_id=?", (inc["id"],))]
    assert "liaison_called_police" in events
    owner = svc.get_incident("dana", inc["id"])["helpers"][0]
    assert owner["last_action"] == "called_police" and "משטרה" in owner["last_action_label"]
    hist = svc.privacy_history("dana")["events"]
    assert any(e["action"] == "authority_contacted" for e in hist)
    assert any(e["action"] == "location_access" and e["via"] == "authority_liaison" for e in hist)
    with pytest.raises(Exception):
        svc.helper_respond("talker", inc["id"], "called_police")
    svc.release_helper("dana", inc["id"], "liaison")
    with pytest.raises(Exception):
        svc.get_incident("liaison", inc["id"])


def test_unverified_liaison_never_receives_requests(svc):
    inc = svc.create_incident("dana", "e2", location=LOC)
    svc.request_helper("dana", inc["id"], "authority_liaison")
    assert _helpers(svc, inc["id"], "authority_liaison") == []
    assert any(e["kind"] == "helper_none" for e in svc._q("SELECT kind FROM incident_events WHERE incident_id=?",
                                                          (inc["id"],)))


def test_remote_support_inside_emergency_and_expiry_reoffers(svc):
    inc = svc.create_incident("dana", "e3", location=LOC)
    svc.request_helper("dana", inc["id"], "remote_support")
    first = {h["username"] for h in _helpers(svc, inc["id"])}
    svc._x("UPDATE helpers SET notified_at=? WHERE incident_id=?", (time.time() - 3600, inc["id"]))
    svc.helpers_tick()
    states = {h["username"]: h["state"] for h in _helpers(svc, inc["id"])}
    assert all(states[u] == "expired" for u in first)
    hv_owner = svc.get_incident("dana", inc["id"])
    assert all(h["state"] != "notified" or h["username"] not in first for h in hv_owner["helpers"])


def test_auto_liaison_when_nobody_accepted_for_90s(svc):
    svc.request_verification("liaison", "אימות זהות", "12")
    svc.admin_verify("liaison", True)
    inc = svc.create_incident("dana", "e4", location=LOC)
    svc.helpers_tick(time.time() + 10)
    assert _helpers(svc, inc["id"]) == []                                       # עוד לא עברו 90 שנ'
    svc.helpers_tick(time.time() + HP.LIAISON_AUTO_AFTER_S + 5)
    assert [h["username"] for h in _helpers(svc, inc["id"], "authority_liaison")] == ["liaison"]
    svc.helpers_tick(time.time() + HP.LIAISON_AUTO_AFTER_S + 10)
    assert len(_helpers(svc, inc["id"], "authority_liaison")) == 1             # לא כפול
    mid = svc.create_incident("dana", "e5", location=LOC, trigger="sensor", risk={"level": "MEDIUM", "confidence": 0.5})
    svc.helpers_tick(time.time() + HP.LIAISON_AUTO_AFTER_S + 5)
    assert _helpers(svc, mid["id"]) == []                                       # לא מאושר - לא אוטומטי


def test_helpers_over_http(tmp_path):
    httpd = create_server("127.0.0.1", 0, db_path=str(tmp_path / "w.db"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        dana, talker = BackendClient(url), BackendClient(url)
        dana.login_or_register("dana", PW)
        talker.login_or_register("talker", PW)
        httpd.service._x("UPDATE users SET created_at=?", (time.time() - 10 * 86400,))
        talker.set_volunteer(True)
        assert talker.set_volunteer_roles(["remote_support"], ["he", "ar"])["effective"]["remote_support"] == "active"
        v = dana.create_support_request("צריכה לדבר", "he")
        inbox = talker.inbox()
        assert inbox and inbox[0]["my_role"] == "helper" and inbox[0]["support_request"]
        talker.helper_respond(v["id"], "accept")
        talker.send_message(v["id"], "שלום, אני כאן")
        assert dana.get_incident(v["id"])["helpers"][0]["state"] == "accepted"
    finally:
        httpd.shutdown()
        httpd.service.close()


def test_remote_only_volunteer_never_gets_on_scene_dispatch(svc):
    """פער שנמצא: שיבוץ בשטח התעלם מהתפקידים - מתנדב/ת 'שיחה בלבד' עם מיקום היה/תה מקבל/ת קריאת הגעה."""
    svc.update_presence("talker", 32.0801, 34.7801, is_precise=True)          # remote_support בלבד
    svc.set_volunteer("medic", True, 5)
    svc.update_presence("medic", 32.0802, 34.7802, is_precise=True)           # on_scene (ברירת מחדל)
    inc = svc.create_incident("dana", "e9", location=LOC)
    svc.run_dispatch_queue()
    got = {r["username"] for r in svc._q("SELECT username FROM recipients WHERE incident_id=?", (inc["id"],))}
    assert "medic" in got and "talker" not in got
    svc.update_presence("dana", 32.08, 34.78, is_precise=True)
    summary = [v["display_name"] for v in svc.community_summary("dana", radius_km=5)["volunteers"]]
    assert svc._display("medic") in summary and svc._display("talker") not in summary


def test_owner_sees_requested_kinds_and_liaison_sees_last_action(svc):
    svc.request_verification("liaison", "אימות זהות", "123")
    svc.admin_verify("liaison", True)
    inc = svc.create_incident("dana", "e10", location=LOC)
    svc.request_helper("dana", inc["id"], "authority_liaison")
    assert svc.get_incident("dana", inc["id"])["helper_requests"] == ["authority_liaison"]
    svc.helper_respond("liaison", inc["id"], "accept")
    svc.helper_respond("liaison", inc["id"], "called_mda")
    v = svc.get_incident("liaison", inc["id"])
    assert v["last_action"] == "called_mda" and "מד" in v["last_action_label"]
    n = len(svc._q("SELECT 1 FROM notifications WHERE username='liaison' AND type='incident_update'"))
    svc.add_message("dana", inc["id"], "תודה")
    svc._broadcast_update(inc["id"], "status", {"text": "סוד"})
    rows = svc._q("SELECT payload FROM notifications WHERE username='liaison' AND type='incident_update'")
    assert len(rows) > n and "סוד" not in rows[-1]["payload"]                  # רק "משהו השתנה", בלי פרטים


def test_roles_report_availability_and_resumed_note_is_kept(svc):
    svc.register("offduty", PW)
    r = svc.set_volunteer_roles("offduty", ["remote_support"])
    assert r["available"] is False                                   # נוצר כלא זמין/ה - הלקוח מציג זאת
    assert svc.volunteer_roles("talker")["available"] is True
    first = svc.create_support_request("dana", "הודעה ראשונה")
    again = svc.create_support_request("dana", "עוד משהו שרציתי להגיד")
    assert again["id"] == first["id"] and again["resumed"]
    texts = [m["text"] for m in svc.get_incident("dana", first["id"])["messages"]]
    assert "עוד משהו שרציתי להגיד" in texts                         # לא נבלע בשקט


def test_unknown_trigger_with_confidence_is_automatic_not_manual(svc):
    """פער שנמצא בעבודת השעון: trigger לא מוכר (למשל watch_fall) הפך ל'ידני' = הפעלה מלאה בלי בדיקת ביטחון."""
    with pytest.raises(Exception):
        svc.create_incident("dana", "w1", location=LOC, trigger="some_new_detector",
                            risk={"level": "HIGH", "confidence": 0.2})            # ביטחון נמוך -> 422
    mid = svc.create_incident("dana", "w2", location=LOC, trigger="watch_fall", risk={"level": "HIGH", "confidence": 0.5})
    assert svc._incident_row(mid["id"])["activation"] == "unconfirmed"
    svc.update_incident("dana", mid["id"], {"status": "resolved"})
    btn = svc.create_incident("dana", "w3", location=LOC, trigger="button")          # לחיצה מלקוח ישן
    assert svc._incident_row(btn["id"])["activation"] == "manual"
    svc.update_incident("dana", btn["id"], {"status": "resolved"})
    old = svc.create_incident("dana", "w4", location=LOC, trigger="legacy_thing")    # לא מוכר, בלי ביטחון
    assert svc._incident_row(old["id"])["activation"] == "manual"                  # לא חוסמים SOS של אדם

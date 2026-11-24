# -*- coding: utf-8 -*-
"""
שרשרת צלצול לאנשי קשר (הקרוב ראשון, לא ענה -> הבא, אין אנשי קשר -> מתנדבים) + הצ'אט המשופר.
שרת אמיתי, לקוחות אמיתיים.
"""
import time

import pytest

from server import ring as RG
from test_dispatch_queue import NEAR, _vols
from test_features_e2e import TLV, _c, _connect, _err, _start, _vol

FAR = {**TLV, "lat": 32.13}          # ~5 ק"מ
CLOSE = {**TLV, "lat": 32.0880}      # ~300 מ'


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path, contact_ring_seconds=60)
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


def _family(url):
    dana = _c(url, "dana")
    mom, sis, dad = _c(url, "mom"), _c(url, "sis"), _c(url, "dad")
    for c in (mom, sis, dad):
        _connect(dana, c)
    mom.update_presence(FAR)
    sis.update_presence(CLOSE)
    return dana, mom, sis, dad          # ל-dad אין מיקום ידוע


def _notes(httpd, user, ntype, iid):
    import json
    return [json.loads(r["payload"]) for r in httpd.service._q(
        "SELECT payload FROM notifications WHERE username=? AND type=? AND incident_id=? ORDER BY id", (user, ntype, iid))]


def _ring_state(httpd, iid):
    return {r["username"]: (r["state"], r["ring_status"]) for r in httpd.service._q(
        "SELECT username, state, ring_status FROM recipients WHERE incident_id=? AND role='trusted'", (iid,))}


def test_order_contacts_pure():
    now = 1000.0
    cs = [{"username": "far", "lat": 32.13, "lon": 34.78, "updated_at": now, "connected_at": 1},
          {"username": "close", "lat": 32.088, "lon": 34.78, "updated_at": now, "connected_at": 3},
          {"username": "unknown", "lat": None, "lon": None, "updated_at": None, "connected_at": 0},
          {"username": "stale", "lat": 32.0853, "lon": 34.7818, "updated_at": now - RG.FRESH_SECONDS - 1,
           "connected_at": 2}]
    assert RG.order_contacts(cs, (32.0853, 34.7818), now) == ["close", "far", "unknown", "stale"]
    assert RG.order_contacts(cs, None, now) == ["unknown", "far", "stale", "close"]   # בלי מיקום - לפי ותק


def test_nearest_contact_rings_first_others_do_not_know_yet(srv):
    httpd, url = srv
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="silent", location=TLV)
    assert _ring_state(httpd, inc["id"]) == {"sis": ("notified", "ringing"), "mom": ("queued", "queued"),
                                             "dad": ("queued", "queued")}
    assert sis.get_incident(inc["id"])["my_ring_status"] == "ringing"   # אפליקציה שעלתה מחדש יודעת שמצלצלים
    assert _notes(httpd, "sis", "incident_new", inc["id"])[0]["ring"] is True
    for other in (mom, dad):                        # עוד לא תורם - אין גישה, אין התראה
        assert _err(lambda: other.get_incident(inc["id"])).status == 404
        assert _notes(httpd, other.username, "incident_new", inc["id"]) == []
    assert not _vols(dana.get_incident(inc["id"]))  # מצוקה שקטה + אנשי קשר בשרשרת -> עוד לא מתנדבים


def test_no_answer_moves_to_next_then_volunteers(srv):
    httpd, url = srv
    httpd.service.ring_seconds = 0.3
    _vol(url, "vol", NEAR)
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="silent", location=TLV)
    order = []
    end = time.time() + 10
    while time.time() < end:
        httpd.service.run_escalation_tick()
        st = _ring_state(httpd, inc["id"])
        ringing = [u for u, (_, rs) in st.items() if rs == "ringing"]
        if ringing and ringing[0] not in order:
            order.append(ringing[0])
        if _vols(dana.get_incident(inc["id"])):
            break
        time.sleep(0.1)
    assert order == ["sis", "mom", "dad"]                       # לפי קרבה, ובלי מיקום - אחרון
    assert _vols(dana.get_incident(inc["id"])) == ["vol"]      # אף אחד לא ענה -> מתנדבים
    assert [n["event"] for n in _notes(httpd, "sis", "incident_update", inc["id"]) if n.get("event") == "ring_stopped"]
    assert sis.get_incident(inc["id"])["status"] == "active"   # מי שלא ענה עדיין רואה ויכול לענות
    kinds = [e["kind"] for e in dana.get_incident(inc["id"])["timeline"]]
    assert kinds.count("ring_timeout") == 3 and "ring_exhausted" in kinds


def test_answer_stops_the_chain_and_family_is_informed_quietly(srv):
    httpd, url = srv
    _vol(url, "vol", NEAR)
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="silent", location=TLV)
    sis.respond(inc["id"], "accept")
    st = _ring_state(httpd, inc["id"])
    assert st["sis"] == ("accepted", "answered")
    assert st["mom"] == ("notified", "informed") and st["dad"] == ("notified", "informed")
    assert _notes(httpd, "mom", "incident_new", inc["id"])[0]["ring"] is False      # בלי צפצוף
    assert _notes(httpd, "sis", "incident_update", inc["id"])[-1]["event"] == "ring_stopped"
    httpd.service.ring_seconds = 0.01
    httpd.service.run_escalation_tick()
    assert not _vols(dana.get_incident(inc["id"]))           # איש קשר מטפל - לא שולחים מתנדבים לשקט


def test_decline_rings_next_immediately(srv):
    httpd, url = srv
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="silent", location=TLV)
    sis.respond(inc["id"], "decline")
    assert _ring_state(httpd, inc["id"])["mom"] == ("notified", "ringing")


def test_loud_sos_still_alerts_volunteers_in_parallel(srv):
    httpd, url = srv
    _vol(url, "vol", NEAR)
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="loud", location=TLV)
    assert _vols(inc) == ["vol"] and _ring_state(httpd, inc["id"])["sis"][1] == "ringing"


def test_closing_does_not_notify_contacts_who_never_knew(srv):
    httpd, url = srv
    dana, mom, sis, dad = _family(url)
    inc = dana.create_incident("e", mode="silent", location=TLV)
    dana.update_incident(inc["id"], status="cancelled")
    assert _notes(httpd, "sis", "incident_closed", inc["id"])
    assert not _notes(httpd, "mom", "incident_closed", inc["id"])


# ------------------------------------------------------------------ צ'אט
def test_chat_quick_replies_read_receipts_and_permissions(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    vol = _vol(url, "vol", NEAR)
    inc = dana.create_incident("c", location=TLV)
    m1 = dana.send_message(inc["id"], quick="cant_talk")["id"]
    assert _err(lambda: dana.send_message(inc["id"], quick="on_my_way")).code == "invalid_quick"   # של מגיבים
    assert _err(lambda: vol.send_message(inc["id"], "hi")).code == "accept_first"
    assert _err(lambda: vol.mark_read(inc["id"], m1)).code == "accept_first"
    view = mom.get_incident(inc["id"])
    assert view["messages"][0]["text"] == "אני לא יכול/ה לדבר - רק הודעות"
    assert view["messages"][0]["role"] == "owner" and view["messages"][0]["read_by"] == []
    assert "on_my_way" in view["quick_replies"] and "cant_talk" not in view["quick_replies"]
    mom.mark_read(inc["id"], m1)
    assert dana.get_incident(inc["id"])["messages"][0]["read_by"] == ["Mom"]          # שם תצוגה
    mom.send_message(inc["id"], quick="on_my_way")
    vol.respond(inc["id"], "accept")
    vol.send_message(inc["id"], "אני ליד הכניסה")
    msgs = dana.get_incident(inc["id"])["messages"]
    assert [(m["sender"], m["role"]) for m in msgs] == [("dana", "owner"), ("mom", "trusted"), ("vol", "volunteer")]
    assert msgs[1]["quick"] == "on_my_way" and msgs[1]["text"] == "אני בדרך אליך"


def test_silent_owner_gets_quiet_chat_notifications_and_rate_limit(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    inc = dana.create_incident("s", mode="silent", location=TLV)
    mom.send_message(inc["id"], quick="stay_hidden")
    owner_msgs = [n for n in _notes(httpd, "dana", "incident_update", inc["id"]) if n.get("event") == "message"]
    assert owner_msgs and all(n.get("quiet") for n in owner_msgs)
    mom_msgs = [n for n in _notes(httpd, "mom", "incident_update", inc["id"]) if n.get("event") == "message"]
    assert mom_msgs == []                          # לא מקבלים התראה על הודעה של עצמנו
    for i in range(httpd.service.MESSAGES_PER_MINUTE - 1):
        mom.send_message(inc["id"], f"m{i}")
    assert _err(lambda: mom.send_message(inc["id"], "one too many")).code == "slow_down"
    dana.update_incident(inc["id"], status="resolved")
    assert _err(lambda: dana.send_message(inc["id"], "x")).code == "closed"

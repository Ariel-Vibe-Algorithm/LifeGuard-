# -*- coding: utf-8 -*-
"""
תור עדיפויות למתנדבים לפי רמת סכנה + מד הסכנה מוצג רק למגיבים.
שרת אמיתי (HTTP + SQLite) ולקוחות אמיתיים - בלי mocks.
"""
import pytest

from server import dispatch as D
from test_features_e2e import PW, TLV, _c, _connect, _start, _vol  # noqa: F401

NEAR = {**TLV, "lat": 32.086}


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path)
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


def _vols(view):
    return [r["username"] for r in view["recipients"] if r["role"] == "volunteer"]


# ------------------------------------------------------------------ האלגוריתם (פונקציות טהורות)
def test_priority_orders_by_danger_and_ages_waiting_incidents():
    high = D.priority("HIGH", 6, "physical_danger", "high", "loud", True, 0)
    medium = D.priority("MEDIUM", 3, "unknown", "medium", "loud", True, 0)
    low = D.priority("LOW", 0, "unknown", "low", "loud", True, 0)
    assert high > medium > low
    # שקט וללא אנשי קשר - דחוף יותר מאותו אירוע גלוי עם אנשי קשר
    assert D.priority("MEDIUM", 3, "unknown", "medium", "silent", False, 0) > medium
    # הזדקנות: אירוע נמוך שממתין 10 דקות עוקף אירוע בינוני חדש - אף אחד לא נדחק לנצח
    assert D.priority("LOW", 0, "unknown", "low", "loud", True, 600) > medium
    assert D.priority("LOW", 0, "unknown", "low", "loud", True, 60) < medium
    assert D.priority("LOW", 0, "unknown", "low", "loud", True, 10 ** 6) - low == D.AGING_MAX
    assert D.fanout("HIGH") == 3 and D.fanout("MEDIUM") == 2 and D.fanout(None) == 1
    assert D.band(high)[0] in ("critical", "high") and D.band(low)[0] == "low"
    assert [i for i, _ in D.plan([(40, "a", 1), (90, "b", 2), (40, "c", 1)])] == ["b", "a", "c"]


def test_clean_risk_rejects_garbage():
    assert D.clean_risk(None) is None and D.clean_risk({"level": "EVIL"}) is None
    lvl, score, reasons = D.clean_risk({"level": "high", "score": "999", "reasons": ["x" * 500] * 20})
    assert lvl == "HIGH" and score == D.SCORE_SCALE * 2 and len(reasons) == 8 and len(reasons[0]) == 200
    assert D.clean_risk({"level": "LOW", "score": "abc"})[1] is None


# ------------------------------------------------------------------ מד הסכנה: רק למגיבים
def test_risk_hidden_from_person_in_distress_but_shown_to_responders(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    vol = _vol(url, "vol", NEAR)
    risk = {"level": "HIGH", "score": 5, "reasons": ["מילות מצוקה זוהו: הצילו"]}
    inc = dana.create_incident("r1", location=TLV, risk=risk)
    assert "risk" not in inc and "risk" not in dana.get_incident(inc["id"])      # בעל האירוע לא רואה מד
    m = mom.get_incident(inc["id"])["risk"]
    assert m["level"] == "HIGH" and m["score"] == 5 and m["reasons"] == risk["reasons"] and m["band"]
    v = vol.get_incident(inc["id"])["risk"]
    assert v["level"] == "HIGH" and "reasons" not in v                             # לפני קבלה - בלי נימוקים
    assert "reasons" in vol.respond(inc["id"], "accept")["risk"]                  # אחרי קבלה - מלא
    # עדכון הערכה מהמכשיר מגיע למגיבים, ועדיין לא לבעלים
    dana.update_incident(inc["id"], risk={"level": "MEDIUM", "score": 3, "reasons": []})
    assert mom.get_incident(inc["id"])["risk"]["level"] == "MEDIUM"
    assert "risk" not in dana.get_incident(inc["id"])


# ------------------------------------------------------------------ התור
def test_high_danger_fans_out_to_several_volunteers_low_to_one(srv):
    httpd, url = srv
    for i in range(4):
        _vol(url, f"vol{i}", {**NEAR, "lat": 32.086 + i * 0.001})
    hi = _c(url, "high").create_incident("h", location=TLV, risk={"level": "HIGH", "score": 6, "reasons": []})
    assert len(_vols(hi)) == D.fanout("HIGH") == 3
    lo = _c(url, "lowu").create_incident("l", location=TLV, risk={"level": "LOW", "score": 0, "reasons": []})
    assert len(_vols(lo)) == 1                     # המתנדב היחיד שנשאר פנוי


def test_busy_volunteer_not_offered_second_incident_and_queue_waits(srv):
    httpd, url = srv
    vol = _vol(url, "vol", NEAR)
    a = _c(url, "aaa").create_incident("a", location=TLV)
    vol.respond(a["id"], "accept")
    b_owner = _c(url, "bbb")
    b = b_owner.create_incident("b", location=TLV)
    assert _vols(b) == []                          # המתנדב מטפל ב-a
    events = [e["kind"] for e in b_owner.get_incident(b["id"])["timeline"]]
    httpd.service.run_escalation_tick()
    httpd.service.run_escalation_tick()
    again = [e["kind"] for e in b_owner.get_incident(b["id"])["timeline"]]
    assert events.count("no_volunteer") == again.count("no_volunteer") == 1   # מדווח פעם אחת, לא בכל סבב
    _c(url, "aaa").update_incident(a["id"], status="resolved")
    httpd.service.run_escalation_tick()            # המתנדב התפנה -> משובץ לאירוע שחיכה בתור
    assert _vols(b_owner.get_incident(b["id"])) == ["vol"]


def test_more_urgent_incident_takes_volunteer_who_has_not_yet_accepted(srv):
    httpd, url = srv
    vol = _vol(url, "vol", NEAR)
    low_owner = _c(url, "low")
    low = low_owner.create_incident("l", location=TLV, risk={"level": "LOW", "score": 0, "reasons": []})
    assert _vols(low) == ["vol"]
    urgent = _c(url, "urgent").create_incident("u", location=TLV, emergency_type="physical_danger",
                                               severity="critical", risk={"level": "HIGH", "score": 7, "reasons": []})
    assert _vols(urgent) == ["vol"]                # הועבר לאירוע הדחוף
    assert [r["state"] for r in low_owner.get_incident(low["id"])["recipients"]] == ["reassigned"]
    assert vol.get_incident(urgent["id"])["my_role"] == "volunteer"
    with pytest.raises(Exception):
        vol.get_incident(low["id"])                # ההצעה הישנה כבר לא בתוקף
    assert vol.inbox()[0]["id"] == urgent["id"]


def test_accepted_volunteer_is_never_taken_away(srv):
    httpd, url = srv
    vol = _vol(url, "vol", NEAR)
    low = _c(url, "low").create_incident("l", location=TLV, risk={"level": "LOW", "score": 0, "reasons": []})
    vol.respond(low["id"], "accept")
    urgent = _c(url, "urgent").create_incident("u", location=TLV, emergency_type="physical_danger",
                                               severity="critical", risk={"level": "HIGH", "score": 7, "reasons": []})
    assert _vols(urgent) == []
    assert vol.get_incident(low["id"])["my_state"] == "accepted"


def test_queue_serves_most_urgent_waiting_incident_first(srv):
    httpd, url = srv
    owners = {}
    for name, lvl in (("lowa", "LOW"), ("highb", "HIGH"), ("medc", "MEDIUM")):
        c = _c(url, name)
        owners[name] = (c, c.create_incident(name, location=TLV, risk={"level": lvl, "score": 0, "reasons": []})["id"])
    vol = _vol(url, "vol", NEAR)                   # מתנדב אחד מתפנה לשלושה אירועים שממתינים
    httpd.service.run_escalation_tick()
    got = [n for n, (c, iid) in owners.items() if _vols(c.get_incident(iid))]
    assert got == ["highb"]
    first = vol.inbox()[0]
    assert first["id"] == owners["highb"][1]
    assert first["owner"] is None and first["owner_display"] == "מישהו בקרבת מקום"   # לפני קבלה - לא מי


def test_queue_tick_during_silent_creation_never_leaks_to_volunteers(srv):
    """באג שנמצא בהרצה מלאה: סבב תור שרץ *בין* יצירת אירוע שקט לבין צירוף אנשי הקשר ראה
    'שקט בלי אנשי קשר' ושלח את המצוקה השקטה למתנדבים. הסבב חייב לחכות לסיום היצירה."""
    import threading
    httpd, url = srv
    svc = httpd.service
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    _vol(url, "vol", NEAR)
    orig = svc._add_recipient
    raced = []

    def add_recipient_with_concurrent_tick(iid, username, role, *a, **k):
        if role == "trusted" and not raced:
            t = threading.Thread(target=svc.run_dispatch_queue)   # "סבב שנייה" מקביל, בדיוק ברגע הזה
            t.start()
            t.join(0.5)
            raced.append(t)
        return orig(iid, username, role, *a, **k)

    svc._add_recipient = add_recipient_with_concurrent_tick
    try:
        inc = dana.create_incident("s1", mode="silent", location=TLV)
    finally:
        svc._add_recipient = orig
    raced[0].join(5)
    roles = [r["role"] for r in dana.get_incident(inc["id"])["recipients"]]
    assert roles == ["trusted"], roles

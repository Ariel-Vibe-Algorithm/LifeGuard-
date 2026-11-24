# -*- coding: utf-8 -*-
"""ממשק היכולות החדשות מול שרת אמיתי: ליווי (עוקב/ת), בדיקת מוכנות, מידע רפואי, אימות מתנדב,
דירוג מתנדב אחרי אירוע, וטיימר הבטיחות שמסונכרן לליווי בשרת."""
import time

import features_ui
from test_safety_circle_ui import (_button, _close, _has, _make_app, _other, _wait_ui, live,  # noqa: F401
                                   shared_root)

TLV = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "source": "GNSS"}


def _connect(owner, contact_client):
    contact_client.respond_invitation(owner.invite(contact_client.username)["id"], True)


def test_escort_watch_card_and_readiness_card(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    mom, dad = _other(live, "mom"), _other(live, "dad")
    app = _make_app(user, monkeypatch, root)
    try:
        home = app.screens["home"]
        # mom בדרך הביתה ו-user מלווה אותה
        _connect(mom, app.client)
        esc = mom.start_escort(25, "מהעבודה", location=TLV)
        assert _wait_ui(root, lambda: home.escort_watch._shown and _has(home.escort_watch.list, "Mom"))
        assert _has(home.escort_watch.list, "מהעבודה") and _has(home.escort_watch.list, "מיקום אחרון")
        assert _wait_ui(root, lambda: "בדרך" in app.toast.cget("text"))
        mom.escort_action(esc["id"], "arrived")
        assert _wait_ui(root, lambda: not home.escort_watch._shown), "הכרטיס נשאר אחרי הגעה"
        # בדיקת מוכנות: user -> dad
        app.client.invite("dad")
        dad.respond_invitation(dad.circle()["received_invitations"][0]["id"], True)
        home.readiness.refresh()
        assert _wait_ui(root, lambda: "עוד לא בוצעה" in home.readiness.status.cget("text"))
        home.readiness.run()
        assert _wait_ui(root, lambda: _has(home.readiness.results, "Dad") and "0/1" in home.readiness.status.cget("text"))
        got = []
        dad.start_stream(got.append)
        try:
            assert _wait_ui(root, lambda: any(n["type"] == "readiness_check" for n in got))
            home.readiness.refresh()
            assert _wait_ui(root, lambda: "1/1" in home.readiness.status.cget("text"), 8)
        finally:
            dad.stop_stream()
    finally:
        _close(app, root)


def test_medical_window_and_volunteer_verification(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    app = _make_app(user, monkeypatch, root)
    try:
        app._open_medical()
        win = app._windows["medical"]
        assert _wait_ui(root, lambda: "עדיין לא נשמר" in win.status.cget("text"))
        win.vars["blood_type"].set("A+")
        win.vars["allergies"].set("אגוזים")
        win.share.set(True)
        win.save()
        assert _wait_ui(root, lambda: "נשמר בשרת" in win.status.cget("text"))
        m = app.client.get_medical()
        assert m["blood_type"] == "A+" and m["allergies"] == "אגוזים" and m["share"] is True
        win.destroy()
        # אימות מתנדב מהמסך
        app.show_screen("community")
        scr = app.screens["community"]
        assert _wait_ui(root, lambda: "לא מאומת" in scr.trust.chip.cget("text"))
        scr.trust.org_var.set("מד\"א")
        scr.trust.ref_var.set("MDA-4471")
        scr.trust.request()
        assert _wait_ui(root, lambda: "בבדיקה" in scr.trust.chip.cget("text"))
        assert app.client.me()["volunteer"]["verification"] == "pending"
    finally:
        _close(app, root)


def test_volunteer_feedback_window_after_incident(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    vol = _other(live, "vol")
    vol.set_volunteer(True, 5)
    vol.update_presence({**TLV, "lat": 32.086})
    app = _make_app(user, monkeypatch, root)
    try:
        inc = app.client.create_incident("fb-1", location=TLV)
        vol.respond(inc["id"], "accept")
        app.client.update_incident(inc["id"], status="resolved")
        features_ui.offer_volunteer_feedback(app, inc["id"])
        win = _wait_ui(root, lambda: next((w for w in root.winfo_children()
                                           if isinstance(w, features_ui.VolunteerFeedbackWindow)), None))
        assert win is not None and win.rows[0][0] == "vol"
        win.rows[0][1].set(5)
        win.send()
        assert _wait_ui(root, lambda: vol.me()["volunteer"]["ratings"] == 1, 6)
        assert vol.me()["volunteer"]["avg_rating"] == 5
    finally:
        _close(app, root)


def test_safety_timer_dialog_flow_creates_server_escort(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    mom = _other(live, "mom")
    app = _make_app(user, monkeypatch, root)
    try:
        _connect(app.client, mom)
        ok, _ = app.engine.start_safety_timer(20, destination="לתחנה")
        assert ok
        esc = _wait_ui(root, lambda: mom.escorts()["watching"])
        assert esc and esc[0]["destination"] == "לתחנה" and esc[0]["owner"] == user
        app._confirm_safe_arrival()
        assert _wait_ui(root, lambda: mom.get_escort(esc[0]["id"])["status"] == "arrived")
    finally:
        _close(app, root)

# -*- coding: utf-8 -*-
"""
מעגל הביטחון - בדיקות ממשק מול שרת אמיתי: האפליקציה (LifeGuardApp) מתחברת לשרת על פורט זמני,
המשתמש פועל דרך המסך, והצד השני (לקוח BackendClient אמיתי) מגיב. המסך חייב לשקף את מצב השרת
שמגיע דרך זרם ההתראות - בלי רענון ידני.
"""
import threading
import time
import tkinter as tk

import pytest

import auth_module
import incident_store
from backend_client import BackendClient

from test_ui_navigation import shared_root  # noqa: F401 - fixture משותף (שורש Tk אחד לסשן)

PW = "StrongPass!2026"


def _wait_ui(root, pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


def _texts(widget):
    out = []
    for w in widget.winfo_children():
        try:
            out.append(str(w.cget("text")))
        except tk.TclError:
            pass
        out.extend(_texts(w))
    return out


def _has(widget, needle):
    return any(needle in t for t in _texts(widget))


def _button(widget, needle):
    for w in widget.winfo_children():
        if isinstance(w, tk.Button) and needle in str(w.cget("text")):
            return w
        found = _button(w, needle)
        if found:
            return found
    return None


@pytest.fixture
def live(tmp_path, monkeypatch):
    import config
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=30, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(config, "SERVER_URL", url)
    yield url
    httpd.shutdown()
    httpd.service.close()


def _make_app(user, monkeypatch, root):
    import main
    import engine as em
    monkeypatch.setattr(main, "HAS_KEYBOARD", False)
    monkeypatch.setattr(em.LifeGuardEngine, "start_voice_trigger", lambda self: None)
    monkeypatch.setattr(em.LifeGuardEngine, "start_live_monitor", lambda self: None)
    monkeypatch.setattr(main.connectivity_module, "start_monitor", lambda *a, **k: None)
    monkeypatch.setattr(main.LifeGuardApp, "_refresh_emergency_number", lambda self: None)
    monkeypatch.setattr(main.LifeGuardApp, "_run_startup_health_check", lambda self: None)
    monkeypatch.setattr(main.location_module, "get_location", lambda *a, **k: {"available": False})
    root.deiconify()
    app = main.LifeGuardApp(root, user, PW)
    assert _wait_ui(root, lambda: app.client is not None), app.server_error
    return app


def _close(app, root):
    app._alive = False
    app.client.stop_stream()
    app.engine.shutdown()
    for child in root.winfo_children():
        child.destroy()
    root.withdraw()


def _other(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name.title())
    return c


def test_circle_screen_invite_accept_disconnect_live(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    mom = _other(live, "mom")
    app = _make_app(user, monkeypatch, root)
    try:
        app.show_screen("circle")
        scr = app.screens["circle"]
        assert _wait_ui(root, lambda: scr.data is not None)
        assert _has(scr.contacts_list, "אין אנשי קשר מחוברים")
        assert "מתנדבים" in scr.banner.cget("text")  # הסבר על SOS בלי אנשי קשר
        # הזמנה דרך המסך
        scr.username_var.set("@mom")
        scr.invite_username()
        assert _wait_ui(root, lambda: _has(scr.sent_list, "@mom") and _has(scr.sent_list, "ממתין לאישור"))
        assert "נשלחה" in scr.status.cget("text")
        inv = mom.circle()["received_invitations"]
        assert [i["from"] for i in inv] == [user]
        assert not _has(scr.contacts_list, "@mom")  # לא מחובר לפני אישור
        # mom מאשרת במכשיר שלה -> המסך מתעדכן דרך הזרם, בלי רענון ידני
        mom.respond_invitation(inv[0]["id"], True)
        assert _wait_ui(root, lambda: _has(scr.contacts_list, "@mom")), "המסך לא התעדכן אחרי האישור"
        assert _wait_ui(root, lambda: "אישר/ה" in app.toast.cget("text"))
        assert _wait_ui(root, lambda: "1 אנשי קשר מחוברים" in app.reach_label.cget("text"))
        # ניתוק בשתי לחיצות
        btn = _button(scr.contacts_list, "נתק")
        btn.invoke()
        _wait_ui(root, lambda: False, 0.3)
        assert mom.circle()["i_am_contact_for"] != [], "ניתוק בלחיצה אחת - בלי אישור"
        btn.invoke()
        assert _wait_ui(root, lambda: mom.circle()["i_am_contact_for"] == [])
        assert _wait_ui(root, lambda: _has(scr.contacts_list, "אין אנשי קשר מחוברים"))
    finally:
        _close(app, root)


def test_circle_screen_receives_invitation_and_phone_code(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    dana = _other(live, "dana")
    app = _make_app(user, monkeypatch, root)
    try:
        app.show_screen("circle")
        scr = app.screens["circle"]
        dana.invite(user)
        assert _wait_ui(root, lambda: _has(scr.received_list, "Dana")), "הזמנה נכנסת לא הוצגה"
        _button(scr.received_list, "אשר").invoke()
        assert _wait_ui(root, lambda: _has(scr.for_list, "@dana"))
        assert [c["username"] for c in dana.circle()["my_contacts"]] == [user]
        # הזמנה לפי טלפון -> קוד חד-פעמי מוצג פעם אחת ומועתק ללוח
        scr.phone_var.set("050-111-2233")
        scr.phone_name_var.set("Grandma")
        scr.invite_phone()
        assert _wait_ui(root, lambda: _has(scr.share_box, "קוד ההזמנה:"))
        code = next(t for t in _texts(scr.share_box) if t.startswith("קוד ההזמנה:")).split(": ")[1]
        _button(scr.share_box, "העתק").invoke()
        assert code in root.clipboard_get()
        assert _wait_ui(root, lambda: _has(scr.sent_list, "Grandma") and _has(scr.sent_list, "2233"))
        # סבתא מתקינה, נרשמת, ומזינה את הקוד
        grandma = _other(live, "grandma")
        assert grandma.redeem_code(code)["status"] == "connected"
        assert _wait_ui(root, lambda: _has(scr.contacts_list, "@grandma"))
        # שליחה כפולה נחסמת ומוצגת שגיאה מהשרת
        scr.username_var.set("dana")
        scr.invite_username()
        assert _wait_ui(root, lambda: "נשלחה ל-@dana" in scr.status.cget("text"))
        scr.username_var.set("dana")
        scr.invite_username()
        assert _wait_ui(root, lambda: "כבר נשלחה" in scr.status.cget("text"))
        assert len([i for i in dana.circle()["received_invitations"] if i["from"] == user]) == 1
    finally:
        _close(app, root)


def test_invite_code_from_registration_is_offered_after_first_connect(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    dana = _other(live, "dana")
    code = dana.invite_code()["code"]
    incident_store.set_setting(user, "pending_invite_code", code.replace("-", ""))
    auth_module.set_phone(user, "052-999-0000")
    app = _make_app(user, monkeypatch, root)
    try:
        scr = app.screens["circle"]
        assert _wait_ui(root, lambda: app.current_screen == "circle" and _has(scr.code_box, "Dana"))
        # הקוד לא נצרך אוטומטית - המשתמש מאשר בעצמו
        assert dana.circle()["my_contacts"] == []
        _button(scr.code_box, "אשר").invoke()
        assert _wait_ui(root, lambda: _has(scr.for_list, "@dana"))
        assert incident_store.get_setting(user, "pending_invite_code", "") == ""
        assert app.client.me()["phone_last4"] == "0000"  # הטלפון מההרשמה סונכרן לשרת
    finally:
        _close(app, root)


def test_legacy_local_contacts_are_invited_only_once(live, user, monkeypatch, shared_root):  # noqa: F811
    root = shared_root
    mom = _other(live, "mom")
    auth_module.update_contacts(user, [{"name": "Mom", "account": "mom"}])
    app = _make_app(user, monkeypatch, root)
    try:
        inv = _wait_ui(root, lambda: mom.circle()["received_invitations"])
        mom.respond_invitation(inv[0]["id"], False)
        _close(app, root)
        app = _make_app(user, monkeypatch, root)  # כניסה נוספת: מי שדחה לא מקבל הזמנה חוזרת
        _wait_ui(root, lambda: False, 1.5)
        assert mom.circle()["received_invitations"] == []
    finally:
        _close(app, root)


def test_os_notifications_only_in_background_and_never_in_silent_mode(live, user, monkeypatch, shared_root,  # noqa: F811
                                                                      os_toasts):
    import main
    root = shared_root
    monkeypatch.setattr("alert_window._beep", lambda: None)
    dana = _other(live, "dana")
    app = _make_app(user, monkeypatch, root)
    try:
        dana.invite(user)
        app.client.respond_invite("dana", True)
        fg = {"v": True}
        monkeypatch.setattr(main.LifeGuardApp, "_app_in_foreground", lambda self: fg["v"])
        inc1 = dana.create_incident("fg", location={"lat": 32.08, "lon": 34.78, "is_precise": True})
        assert _wait_ui(root, lambda: inc1["id"] in app._alert_windows)
        assert not [t for t in os_toasts if "title" in t], "התראת מערכת כשהאפליקציה מול המשתמש"
        dana.update_incident(inc1["id"], status="resolved")
        assert _wait_ui(root, lambda: {"removed": inc1["id"][-16:]} in os_toasts)  # נמחק ממרכז ההודעות
        fg["v"] = False  # ממוזער / ברקע
        inc2 = dana.create_incident("bg", emergency_type="medical",
                                    location={"lat": 32.08, "lon": 34.78, "is_precise": True})
        toast = _wait_ui(root, lambda: next((t for t in os_toasts if t.get("tag") == inc2["id"][-16:]), None))
        assert toast and toast["urgent"] and "Dana" in toast["title"] and "רפואי" in toast["body"]
        dana.update_incident(inc2["id"], status="resolved")
        # מעקב שקט: שום התראה גלויה, גם לא של Windows
        app.engine.activate_duress_mode(source="typed")
        _wait_ui(root, lambda: False, 0.5)
        n = len(os_toasts)
        inc3 = dana.create_incident("silent", location={"lat": 32.08, "lon": 34.78, "is_precise": True})
        _wait_ui(root, lambda: inc3["id"] in app._pending_alerts)
        _wait_ui(root, lambda: False, 0.5)
        assert [t for t in os_toasts[n:] if "title" in t] == [], "דליפה במצב שקט: התראת Windows"
        app.engine.end_silent_tracking()
    finally:
        _close(app, root)


def test_registration_form_allows_no_contacts(temp_env, shared_root):  # noqa: F811
    import login_ui
    root = shared_root
    done = []
    win = login_ui.LoginWindow(root, lambda u, p: done.append(u))
    win.reg_username_var.set("solo_user")
    win.reg_password_var.set(PW)
    win.reg_password_confirm_var.set(PW)
    win.reg_voice_word_var.set("red siren")
    win.reg_silent_phrase_var.set("blue lantern")
    win.reg_cancel_secret_var.set("green garden")
    win.tos_accepted_var.set(True)
    win._do_register()
    try:
        assert auth_module.username_exists("solo_user"), win.register_error_label.cget("text")
        assert auth_module.get_contacts("solo_user") == []
    finally:
        for child in root.winfo_children():
            child.destroy()
        root.withdraw()

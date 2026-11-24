# -*- coding: utf-8 -*-
"""מסכי Windows למתנדבים שלא בשטח: חלון הזעקת רשויות, חלון "צריך/ה לדבר", סטטוס ב-SOS, הגדרת תפקידים.
שרת אמיתי (create_server) - בלי מוקים לשרת."""
import threading
import time
import tkinter as tk

import pytest

from test_ring_chat_ui import PW, TLV, _app, _c, _pump
from test_ui_navigation import shared_root  # noqa: F401
from server.lifeguard_server import create_server


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "h.db"), tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.service.close()


def _age_accounts(httpd):
    httpd.service._x("UPDATE users SET created_at=?", (time.time() - 10 * 86400,))


def test_liaison_alert_window_hides_then_shows_location_and_reports(server, shared_root):  # noqa: F811
    from alert_window import AlertWindow
    httpd, url = server
    dana, liaison = _c(url, "dana"), _c(url, "liaison")
    liaison.set_volunteer(True)
    liaison.set_volunteer_roles(["authority_liaison"])
    httpd.service.request_verification("liaison", "אימות זהות", "123")
    httpd.service.admin_verify("liaison", True)
    inc = dana.create_incident("ui", location=TLV, mode="silent")
    dana.request_helper(inc["id"], "authority_liaison", "לא יכולה לדבר")
    app = _app(liaison, "liaison")
    win = AlertWindow(shared_root, app, inc["id"], alert_sound=False)
    try:
        assert _pump(shared_root, app, lambda: "להזעיק רשויות" in win.title_label.cget("text"))
        assert "יוצגו רק אחרי" in win.loc_label.cget("text") and "Dana" not in win.profile_label.cget("text")
        assert not win.nav_row.winfo_ismapped()
        win.respond("accept")
        assert _pump(shared_root, app, lambda: "32.08530" in win.loc_label.cget("text"))
        assert "Dana" in win.profile_label.cget("text")
        assert _pump(shared_root, app, lambda: win.helper_row.winfo_ismapped())
        win.respond("called_police")
        assert _pump(shared_root, app, lambda: (dana.get_incident(inc["id"])["helpers"][0].get("last_action")
                                                == "called_police"))
    finally:
        win.destroy()


def test_support_window_full_flow(server, shared_root):  # noqa: F811
    import support_ui
    httpd, url = server
    dana, talker = _c(url, "dana"), _c(url, "talker")
    _age_accounts(httpd)
    talker.set_volunteer(True)
    talker.set_volunteer_roles(["remote_support"])
    app = _app(dana, "dana")
    win = support_ui.SupportWindow(shared_root, app)
    try:
        win.note_var.set("קשה לי הערב")
        win.start()
        assert _pump(shared_root, app, lambda: "נשלחה ל-1" in win.status.cget("text"))
        iid = win.iid
        offer = talker.inbox()[0]
        assert offer["my_role"] == "helper" and "dana" not in str(offer).lower()
        talker.helper_respond(iid, "accept")
        talker.send_message(iid, "אני כאן")
        win.refresh()
        assert _pump(shared_root, app, lambda: "הצטרף/ה" in win.status.cget("text"))
        assert _pump(shared_root, app, lambda: "אני כאן" in win.chat.text.get("1.0", "end"))
        win.end()
        assert _pump(shared_root, app, lambda: "הסתיימה" in win.status.cget("text"))
        assert dana.get_incident(iid)["status"] == "resolved"
    finally:
        win.destroy()


def test_support_window_offline_shows_hotlines_only(shared_root):  # noqa: F811
    import support_ui
    app = _app(None, "dana")
    win = support_ui.SupportWindow(shared_root, app)
    try:
        assert str(win.start_btn.cget("state")) == "disabled"
        assert "לא מחובר" in win.status.cget("text")
        assert "1201" in support_ui.hotlines_text()
    finally:
        win.destroy()


def test_support_status_only_from_server_state():
    import support_ui
    assert "אין כרגע" in support_ui.support_status({"status": "active", "helpers": []})[0]
    assert "ל-2" in support_ui.support_status({"status": "active", "helpers": [
        {"state": "notified"}, {"state": "notified"}]})[0]
    assert "הצטרף" in support_ui.support_status({"status": "active", "helpers": [{"state": "accepted"}]})[0]


def test_sos_helpers_summary():
    from main import SosWindow
    lab = {"authority_liaison": "הזעקת רשויות", "remote_support": "שיחת תמיכה מרחוק"}
    h = lambda k, s, **kw: {"kind": k, "kind_label": lab[k], "state": s, "display_name": "Lia", **kw}  # noqa: E731
    assert SosWindow.helpers_summary([], {"authority_liaison"})["text"].startswith("⚠️ אין כרגע")
    t = SosWindow.helpers_summary([h("authority_liaison", "notified")])["text"]
    assert "ממתינים" in t and "✅" not in t                     # לא "מישהו איתך" בלי קבלה אמיתית
    t = SosWindow.helpers_summary([h("authority_liaison", "accepted", last_action_label="הזעיק/ה משטרה (100)"),
                                   h("remote_support", "notified")])["text"]
    assert "Lia" in t and "משטרה" in t and "ממתינים" in t


def test_privacy_labels_for_helper_audit():
    import privacy_ui as P
    assert "משטרה" in P.describe({"action": "authority_contacted", "actor_display": "Lia",
                                  "details": {"action": "called_police"}})
    assert "אוטומטית" in P.describe({"action": "helper_requested",
                                     "details": {"kind": "authority_liaison", "auto": True}})
    assert P.VIA_HE["authority_liaison"]
    for a in ("helper_requested", "helper_accepted", "authority_contacted", "authority_contact_failed",
              "helper_released", "support_requested"):
        assert a in P.ACTION_HE


def test_volunteer_role_settings_saved_on_server(server, shared_root):  # noqa: F811
    import features_ui
    httpd, url = server
    talker = _c(url, "talker")
    app = _app(talker, "talker")
    frame = tk.Frame(shared_root)
    sec = features_ui.VolunteerTrustSection(frame, app)
    try:
        sec.role_vars["remote_support"].set(True)
        sec.lang_vars["ar"].set(True)
        sec.save_roles()
        assert _pump(shared_root, app, lambda: "remote_support" in talker.volunteer_roles()["roles"])
        r = talker.volunteer_roles()
        assert set(r["languages"]) == {"he", "ar"}
        assert _pump(shared_root, app, lambda: "3 ימים" in sec.role_status["remote_support"].cget("text"))
        for v in sec.role_vars.values():
            v.set(False)
        sec.save_roles()                                           # אף תפקיד - לא נשלח
        assert "לפחות" in sec.roles_msg.cget("text")
    finally:
        frame.destroy()

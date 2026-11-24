# -*- coding: utf-8 -*-
"""חלון ההתראה: צפצוף חוזר עד מענה / עד שהשרת עובר לבא; צ'אט עם הודעות מהירות ואישורי קריאה. שרת אמיתי."""
import threading
import time
import types

import pytest

from backend_client import BackendClient
from server.lifeguard_server import create_server
from test_ui_navigation import shared_root  # noqa: F401

PW = "StrongPass!2026"
TLV = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "source": "GNSS"}


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), tick_seconds=0.2, contact_ring_seconds=60)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.service.close()


def _c(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name.title())
    return c


def _app(client, name):
    posted = []
    return types.SimpleNamespace(username=name, client=client, _post=lambda fn, *a: posted.append((fn, a)),
                                 _posted=posted, show_screen=lambda s: None)


def _pump(root, app, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        while app._posted:
            fn, a = app._posted.pop(0)
            fn(*a)
        root.update()
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_ringing_alert_and_chat(server, shared_root, monkeypatch):  # noqa: F811
    import alert_window
    from alert_window import AlertWindow
    httpd, url = server
    beeps = []
    monkeypatch.setattr(alert_window, "_beep", lambda: beeps.append(time.time()))
    monkeypatch.setattr(AlertWindow, "RING_INTERVAL_MS", 100)
    dana, mom = _c(url, "dana"), _c(url, "mom")
    mom.respond_invitation(dana.invite("mom")["id"], True)
    inc = dana.create_incident("ui", location=TLV)
    app = _app(mom, "mom")
    win = AlertWindow(shared_root, app, inc["id"], alert_sound=True)
    try:
        assert _pump(shared_root, app, lambda: len(beeps) >= 3), "הצפצוף לא חוזר"
        win.stop_ringing("עברנו לאיש הקשר הבא")
        n = len(beeps)
        _pump(shared_root, app, lambda: False, 0.5)
        assert len(beeps) == n and "עברנו" in win.action_status.cget("text")       # נעצר
        win.start_ringing()
        _pump(shared_root, app, lambda: len(beeps) > n, 2)
        win.respond("accept")                                                      # מענה עוצר צלצול
        n = len(beeps)
        _pump(shared_root, app, lambda: False, 0.5)
        assert len(beeps) == n
        # צ'אט: בעלת האירוע שולחת הודעה מהירה -> מופיעה אצל mom עם תפקיד, ו-mom "קוראת"
        mid = dana.send_message(inc["id"], quick="hiding")["id"]
        win.refresh()
        assert _pump(shared_root, app, lambda: "אני מסתתר/ת" in win.chat.text.get("1.0", "end"))
        assert "(במצוקה)" in win.chat.text.get("1.0", "end")
        assert _pump(shared_root, app, lambda: dana.get_incident(inc["id"])["messages"][0]["read_by"] == ["Mom"])
        assert "on_my_way" in dict(win.chat._quick_codes)                         # כפתורי מגיב
        win.chat.send(quick="on_my_way")
        assert _pump(shared_root, app, lambda: any(m["text"] == "אני בדרך אליך"
                                                   for m in dana.get_incident(inc["id"])["messages"]))
        assert mid
    finally:
        win.destroy()

# -*- coding: utf-8 -*-
"""
החלפת סיסמה מההגדרות: מוחלפת בשרת *ובמחשב* יחד (אחרת החשבון בשרת "מתנתק" בכניסה הבאה - באג קודם),
ומנתקת כל מכשיר אחר (מי שגילה את הסיסמה). שרת אמיתי + חלון ההגדרות האמיתי.
"""
import threading
import time
import types

import pytest

import auth_module
import incident_store
from backend_client import BackendClient, BackendError
from server.lifeguard_server import create_server
from test_ui_navigation import shared_root  # noqa: F401

OLD, NEW = "StrongPass!2026", "Brand-New!Pass77"


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), tick_seconds=0.5)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.service.close()


def _pump(root, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_password_change_updates_server_and_local_and_kicks_other_devices(server, user, shared_root,  # noqa: F811
                                                                            monkeypatch):
    import agent_bridge
    from settings_ui import SettingsWindow
    provisioned = []
    monkeypatch.setattr(agent_bridge, "provision", lambda url, u, pw: provisioned.append(pw) or True)
    me = BackendClient(server)
    me.login_or_register(user, OLD, user)
    incident_store.set_setting(user, "server_account", "1")
    other = BackendClient(server)
    other.login(user, OLD, "someone else's laptop")
    engine = types.SimpleNamespace(gateway=types.SimpleNamespace(client=me), log=lambda m: None)
    win = SettingsWindow(shared_root, engine, user)
    try:
        win.gate_password_var.set(OLD)
        win._try_unlock()
        win.new_password_var.set(NEW)
        win.confirm_password_var.set(NEW)
        win._save_password()
        assert _pump(shared_root, lambda: "עודכנה" in win.password_status.cget("text")
                     or "נכשלה" in win.password_status.cget("text")), win.password_status.cget("text")
        assert "נותקו 1" in win.password_status.cget("text")
        assert auth_module.verify_password(user, NEW) and not auth_module.verify_password(user, OLD)
        with pytest.raises(BackendError):
            other.circle()                                    # המכשיר של "מישהו אחר" נותק
        me.circle()                                           # שלי ממשיך
        BackendClient(server).login(user, NEW, "x")           # כניסה הבאה לשרת עובדת עם החדשה
        with pytest.raises(BackendError):
            BackendClient(server).login(user, OLD, "y")
        assert provisioned == [NEW]                           # סוכן הרקע חובר מחדש
    finally:
        win.destroy()


def test_password_not_changed_locally_when_server_unreachable(user, shared_root):  # noqa: F811
    from settings_ui import SettingsWindow
    incident_store.set_setting(user, "server_account", "1")
    engine = types.SimpleNamespace(gateway=types.SimpleNamespace(client=None), log=lambda m: None)
    win = SettingsWindow(shared_root, engine, user)
    try:
        win.gate_password_var.set(OLD)
        win._try_unlock()
        win.new_password_var.set(NEW)
        win.confirm_password_var.set(NEW)
        win._save_password()
        assert "יש להתחבר לשרת" in win.password_status.cget("text")
        assert auth_module.verify_password(user, OLD)          # לא נוצר פער בין המחשב לשרת
    finally:
        win.destroy()

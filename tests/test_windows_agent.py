# -*- coding: utf-8 -*-
"""
סוכן הרקע ל-Windows (lifeguard_agent.py + agent_bridge.py) - מול שרת LifeGuard אמיתי בתוך התהליך
(HTTP + SSE + SQLite). ההודעות נאספות ע"י os_toasts (לא מוקפצות על המסך), חוץ מבדיקת real_toast.
"""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

import pytest

import agent_bridge
import config
import lifeguard_agent
import windows_notify
from backend_client import BackendClient
from server.lifeguard_server import create_server

PW = "StrongPass!2026"
TLV = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "source": "GNSS"}

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows בלבד")


@pytest.fixture(autouse=True)
def os_toasts(monkeypatch, request):
    """כמו ב-conftest, אבל רושם גם את launch (ה-URI שנפתח בלחיצה)."""
    sent = []
    if request.node.get_closest_marker("real_toast") is None:
        monkeypatch.setattr(windows_notify, "notify",
                            lambda title, body, urgent=False, tag="lg", wait=False, launch=None: sent.append(
                                {"title": title, "body": body, "urgent": urgent, "tag": tag, "launch": launch}))
        monkeypatch.setattr(windows_notify, "remove", lambda tag=None: sent.append({"removed": tag}))
    return sent


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=60, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield url, str(tmp_path / "srv.db")
    httpd.shutdown()
    httpd.service.close()


def _client(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name.title())
    return c


def _wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


def _circle(url):
    """dana + bob, bob הוא איש קשר לחירום של dana. bob מחובר גם באפליקציה (סשן נפרד)."""
    dana, bob = _client(url, "dana"), _client(url, "bob")
    inv = dana.invite("bob")
    bob.respond_invitation(inv["id"], True)
    return dana, bob


class Running:
    """Agent אמיתי ב-thread, עם is_app_running מוזרק."""

    def __init__(self, app_running=lambda: False):
        self.agent = lifeguard_agent.Agent(is_app_running=app_running, poll_seconds=0.1, retry_seconds=0.2)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.agent.run, args=(self.stop_event,), daemon=True)
        self.thread.start()

    def connected(self):
        return _wait(lambda: self.agent.client is not None and self.agent.client.stream_state.get("connected"))

    def stop(self):
        self.stop_event.set()
        self.thread.join(5)


def _toasts_for(sent, tag):
    return [t for t in sent if t.get("tag") == tag]


# ------------------------------------------------------------------ provision
def test_provision_stores_encrypted_token_and_separate_server_session(server, temp_env):
    url, db = server
    _client(url, "bob")
    assert agent_bridge.provision(url, "bob", PW)
    path = os.path.join(temp_env["data"], "agent_session.json")
    raw = open(path, encoding="utf-8").read()
    sess = agent_bridge.load_session()
    assert sess and sess["username"] == "bob" and sess["base_url"] == url
    assert sess["token"] not in raw, "הטוקן נשמר גלוי בקובץ"
    assert json.loads(raw)["token"].startswith("dpapi:")
    assert BackendClient(url, token=sess["token"]).me()["username"] == "bob"
    with sqlite3.connect(db) as con:
        devices = sorted(r[0] for r in con.execute("SELECT device FROM sessions WHERE username='bob'"))
    assert devices == ["LifeGuard Windows", "LifeGuard Windows Agent"]

    # provision חוזר מבטל את הטוקן הקודם של הסוכן (לא מצטברים סשנים)
    assert agent_bridge.provision(url, "bob", PW)
    with sqlite3.connect(db) as con:
        n = con.execute("SELECT COUNT(*) FROM sessions WHERE username='bob' AND device='LifeGuard Windows Agent'")
        assert n.fetchone()[0] == 1
    assert not agent_bridge.provision(url, "bob", "wrong-password")
    assert agent_bridge.load_session()["token"] != sess["token"]

    agent_bridge.clear_session(revoke=True)
    assert agent_bridge.load_session() is None and not os.path.exists(path)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM sessions WHERE device='LifeGuard Windows Agent'").fetchone()[0] == 0


def test_not_provisioned_agent_exits_quietly(temp_env, os_toasts):
    agent = lifeguard_agent.Agent(is_app_running=lambda: False)
    assert agent.step() is False and agent.stop_reason == "not_provisioned"
    assert os_toasts == []


# ------------------------------------------------------------------ SOS -> toast
def test_sos_while_app_closed_shows_urgent_toast_and_marks_delivered(server, temp_env, os_toasts):
    url, _ = server
    dana, _bob = _circle(url)
    assert agent_bridge.provision(url, "bob", PW)
    lifeguard_agent.setup_logging(temp_env["data"])
    run = Running()
    try:
        assert run.connected(), "הסוכן לא התחבר לזרם"
        inc = dana.create_incident("sos-1", mode="loud", location=TLV)
        iid = inc["id"]
        toast = _wait(lambda: _toasts_for(os_toasts, iid[-16:]))
        assert toast, f"לא הוצגה הודעה: {os_toasts}"
        t = toast[0]
        assert t["urgent"] is True
        assert t["title"] == "🚨 Dana צריך/ה עזרה"
        assert t["launch"] == f"lifeguard://open?incident={iid}"
        assert "32.08" not in t["body"]  # בלי מיקום בהודעה
        bob_state = _wait(lambda: next((r["state"] for r in dana.get_incident(iid)["recipients"]
                                        if r["username"] == "bob" and r["state"] == "delivered"), None))
        assert bob_state == "delivered"
        # הזמנת המעגל שלא התקבלה באף מכשיר הוצגה בהפעלה הראשונה (sync_cursor), כהודעה רגילה
        inv = [x for x in os_toasts if x.get("title") == "📨 הזמנה למעגל הביטחון"]
        assert inv and inv[0]["urgent"] is False
        # הלוג לא מכיל טוקן או תוכן
        log = open(os.path.join(temp_env["data"], "agent.log"), encoding="utf-8").read() \
            if os.path.exists(os.path.join(temp_env["data"], "agent.log")) else ""
        assert agent_bridge.load_session()["token"] not in log and "Dana" not in log
    finally:
        run.stop()


def test_app_running_agent_stays_silent_including_silent_tracking(server, temp_env, os_toasts):
    """האפליקציה רצה (גם במצב מעקב שקט/כפייה, שבו היא מחזיקה את ה-mutex) -> הסוכן לא מציג כלום
    ולא מתחבר בכלל לזרם."""
    url, _ = server
    dana, _bob = _circle(url)
    assert agent_bridge.provision(url, "bob", PW)
    name = rf"Local\LifeGuard.AppTest.{uuid.uuid4().hex}"
    assert not agent_bridge.is_app_running(name)
    import win32event
    h = win32event.CreateMutex(None, False, name)  # "האפליקציה" - mutex אמיתי בין-תהליכי
    try:
        assert agent_bridge.is_app_running(name)
        run = Running(app_running=lambda: agent_bridge.is_app_running(name))
        try:
            time.sleep(0.5)
            iid = dana.create_incident("sos-silent", mode="silent", location=TLV)["id"]
            time.sleep(1.5)
            assert os_toasts == [], "הסוכן הציג הודעה כשהאפליקציה רצה"
            assert run.agent.client is None
        finally:
            run.stop()
    finally:
        h.Close()
    assert not agent_bridge.is_app_running(name)
    # האפליקציה "נסגרה" בלי לקבל את האירוע -> הוא לא התקבל באף מכשיר ולכן מוצג עכשיו
    run = Running()
    try:
        assert _wait(lambda: _toasts_for(os_toasts, iid[-16:]))
    finally:
        run.stop()


def test_mark_app_running_holds_named_mutex():
    name = rf"Local\LifeGuard.AppTest.{uuid.uuid4().hex}"
    try:
        assert agent_bridge.mark_app_running(name)
        assert agent_bridge.is_app_running(name)
        # בדיקה מתהליך אחר - שם ה-mutex משותף בין תהליכים באותו session
        code = ("import sys; sys.path.insert(0, r'%s'); import agent_bridge; "
                "print(agent_bridge.is_app_running(r'%s'))" % (os.path.dirname(agent_bridge.__file__), name))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == "True", out.stderr
    finally:
        agent_bridge.release_app_running()
    assert not agent_bridge.is_app_running(name)


# ------------------------------------------------------------------ cursor / restart
def test_restart_does_not_retoast_and_resumes_from_saved_cursor(server, temp_env, os_toasts):
    url, _ = server
    dana, _bob = _circle(url)
    assert agent_bridge.provision(url, "bob", PW)
    run = Running()
    try:
        assert run.connected()
        iid = dana.create_incident("sos-r", location=TLV)["id"]
        assert _wait(lambda: _toasts_for(os_toasts, iid[-16:]))
    finally:
        run.stop()
    state = json.load(open(os.path.join(temp_env["data"], "agent_state.json"), encoding="utf-8"))
    assert state["last_id"] > 0 and state["username"] == "bob"
    os_toasts.clear()
    # בזמן שהסוכן כבוי מגיעה בדיקת "test" - אחרי הפעלה מחדש: היא כן, האירוע הישן לא
    dana.notify_trusted("בדיקה", test=True)
    run = Running()
    try:
        assert run.connected()
        assert _wait(lambda: [t for t in os_toasts if t.get("title") == "🧪 בדיקת LifeGuard"])
        time.sleep(0.8)
        assert not _toasts_for(os_toasts, iid[-16:]), "אירוע ישן הוקפץ שוב אחרי הפעלה מחדש"
        assert not [t for t in os_toasts if t.get("title") == "📨 הזמנה למעגל הביטחון"]
    finally:
        run.stop()


def test_incident_closed_removes_toast(server, temp_env, os_toasts):
    url, _ = server
    dana, _bob = _circle(url)
    assert agent_bridge.provision(url, "bob", PW)
    run = Running()
    try:
        assert run.connected()
        iid = dana.create_incident("sos-c", location=TLV)["id"]
        assert _wait(lambda: _toasts_for(os_toasts, iid[-16:]))
        dana.update_incident(iid, status="resolved")
        assert _wait(lambda: {"removed": iid[-16:]} in os_toasts), os_toasts
    finally:
        run.stop()


# ------------------------------------------------------------------ 401
def test_revoked_token_clears_session_shows_relogin_and_stops(server, temp_env, os_toasts):
    url, _ = server
    _client(url, "bob")
    assert agent_bridge.provision(url, "bob", PW)
    BackendClient(url, token=agent_bridge.load_session()["token"]).logout()  # ביטול בשרת
    agent = lifeguard_agent.Agent(is_app_running=lambda: False)
    stop = threading.Event()
    th = threading.Thread(target=agent.run, args=(stop,), daemon=True)
    th.start()
    th.join(10)
    assert not th.is_alive(), "הסוכן לא נעצר אחרי 401"
    assert agent.stop_reason == "unauthorized"
    assert agent_bridge.load_session() is None
    assert not os.path.exists(os.path.join(temp_env["data"], "agent_session.json"))
    relogin = [t for t in os_toasts if t.get("title") == lifeguard_agent.RELOGIN_TITLE]
    assert len(relogin) == 1 and relogin[0]["urgent"] is False


# ------------------------------------------------------------------ toast rules (יחידה)
class _FakeClient:
    def __init__(self, mode):
        self.mode, self._last_id = mode, 0

    def get_incident(self, iid):
        return {"id": iid, "mode": self.mode}


@pytest.mark.parametrize("mode,expect", [("silent", False), ("loud", True)])
def test_own_escort_alarm_respects_silent_mode(temp_env, os_toasts, mode, expect):
    agent = lifeguard_agent.Agent(is_app_running=lambda: False)
    agent.username = "bob"
    iid = "inc_" + "a" * 32
    agent._handle(_FakeClient(mode), {"id": 1, "type": "escort_alarm",
                                      "payload": {"escort_id": "esc_" + "b" * 32, "incident_id": iid}})
    assert bool(_toasts_for(os_toasts, iid[-16:])) is expect


def test_escort_overdue_and_watcher_alarm_are_urgent(temp_env, os_toasts):
    agent = lifeguard_agent.Agent(is_app_running=lambda: False)
    agent.username = "bob"
    eid, iid = "esc_" + "c" * 32, "inc_" + "d" * 32
    agent._handle(_FakeClient("loud"), {"id": 1, "type": "escort_overdue", "payload": {"escort_id": eid, "text": "x"}})
    agent._handle(_FakeClient("loud"), {"id": 2, "type": "escort_alarm",
                                        "payload": {"escort_id": eid, "incident_id": iid, "text": "🚨 Dana לא אישר/ה"}})
    agent._handle(_FakeClient("loud"), {"id": 3, "type": "incident_update", "payload": {"incident_id": iid}})
    over, alarm = os_toasts
    assert over["urgent"] and over["body"] == "הכל בסדר? פתח/י את LifeGuard ואשר/י הגעה"
    assert over["launch"] == f"lifeguard://open?escort={eid}"
    assert alarm["urgent"] and alarm["launch"] == f"lifeguard://open?incident={iid}"
    # מזהה עוין בתוכן ההתראה לא נכנס ל-URI
    agent._handle(_FakeClient("loud"), {"id": 4, "type": "incident_new",
                                        "payload": {"id": "../../x\" --evil", "owner": "dana", "owner_display": "D"}})
    assert os_toasts[-1]["launch"] == "lifeguard://open"


# ------------------------------------------------------------------ lifeguard:// parsing
def test_parse_open_arg_accepts_only_strict_ids():
    iid, eid = "inc_" + "0123456789abcdef" * 2, "esc_" + "fedcba9876543210" * 2
    p = agent_bridge.parse_open_arg
    assert p(["main.py", "--open", f"lifeguard://open?incident={iid}"]) == {"incident": iid}
    assert p(["main.py", "--open", f"lifeguard://open/?incident={iid}"]) == {"incident": iid}
    assert p(["main.py", f"--open=LIFEGUARD://open?escort={eid}"]) == {"escort": eid}
    for junk in ["lifeguard://open?incident=../../x", "lifeguard://open?incident=inc_zz",
                 f"lifeguard://open?incident={iid}&escort={eid}", f"lifeguard://open?incident={iid}&x=1",
                 f"lifeguard://open?incident={iid}&incident={iid}", f"lifeguard://open?incident={iid.upper()}",
                 f"lifeguard://open?incident={iid}0", f"lifeguard://open?incident={iid}#frag",
                 f"lifeguard://evil?incident={iid}", f"lifeguard://open/x?incident={iid}",
                 f"http://open?incident={iid}", f"lifeguard://open?incident=%69nc_{iid[4:]}",
                 f"lifeguard://open?incident={iid} --data-dir x", f"lifeguard://user@open?incident={iid}",
                 f"lifeguard://open?id={iid}", "lifeguard://open", "lifeguard://open?", ""]:
        assert p(["main.py", "--open", junk]) == {}, junk
    assert p(["main.py"]) == {} and p(["main.py", "--open"]) == {} and p(None) == {}
    assert p(["main.py", "--open", f"lifeguard://open?incident={iid}", "--open",
              f"lifeguard://open?incident={iid}"]) == {}


# ------------------------------------------------------------------ registry (מפתחות זמניים)
def test_autostart_registry_roundtrip_with_throwaway_value():
    import winreg
    name = "LifeGuardAgentTest_" + uuid.uuid4().hex[:8]
    try:
        assert not agent_bridge.is_autostart_installed(value_name=name)
        agent_bridge.install_autostart(value_name=name)
        assert agent_bridge.is_autostart_installed(value_name=name)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, agent_bridge.RUN_KEY) as k:
            value, kind = winreg.QueryValueEx(k, name)
        assert kind == winreg.REG_SZ
        assert value.lower().startswith('"') and "pythonw.exe" in value.lower()
        assert value.endswith(f'"{os.path.join(os.path.dirname(agent_bridge.__file__), "lifeguard_agent.py")}"')
        assert agent_bridge.uninstall_autostart(value_name=name)
        assert not agent_bridge.is_autostart_installed(value_name=name)
    finally:
        agent_bridge.uninstall_autostart(value_name=name)
    assert not agent_bridge.is_autostart_installed(value_name=name)


def test_protocol_registry_roundtrip_with_throwaway_scheme():
    import winreg
    scheme = "lifeguard-test-" + uuid.uuid4().hex[:8]
    base = rf"Software\Classes\{scheme}"
    try:
        agent_bridge.register_protocol(scheme=scheme)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            assert winreg.QueryValueEx(k, "URL Protocol")[0] == ""
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\shell\open\command") as k:
            cmd = winreg.QueryValueEx(k, "")[0]
        main_py = os.path.join(os.path.dirname(agent_bridge.__file__), "main.py")
        assert "pythonw.exe" in cmd.lower() and f'"{main_py}" --open "%1"' in cmd
    finally:
        agent_bridge.unregister_protocol(scheme=scheme)
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)


# ------------------------------------------------------------------ real toast with launch
def _history_count():
    ps = ("[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]"
          f" > $null; ([Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory('{windows_notify.APP_ID}')).Count")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=30)
    return int((out.stdout or "0").strip() or 0)


def test_launch_xml_and_rejects_foreign_uris():
    uri = "lifeguard://open?incident=inc_" + "a" * 32
    xml = windows_notify.build_xml("t", "b", urgent=True, launch=uri)
    assert f'launch="{uri}" activationType="protocol"' in xml
    assert f'<action activationType="protocol" arguments="{uri}"' in xml
    assert windows_notify.build_xml("t", "b") == windows_notify.build_xml("t", "b", launch=None)
    for bad in ["javascript:alert(1)", "https://evil.example/", 'lifeguard://open" x="', "lifeguard://open\n"]:
        assert "launch=" not in windows_notify.build_xml("t", "b", launch=bad), bad


@pytest.mark.real_toast
def test_real_toast_with_launch_uri_is_accepted_by_windows():
    before = _history_count()
    uri = "lifeguard://open?incident=inc_" + "e" * 32
    assert windows_notify.notify("LifeGuard · בדיקת סוכן", "בדיקת לחיצה - נמחקת מיד", tag="agentselftest",
                                 wait=True, launch=uri), windows_notify.status()
    assert _history_count() == before + 1
    windows_notify.remove("agentselftest")
    assert _wait(lambda: _history_count() == before, 10)

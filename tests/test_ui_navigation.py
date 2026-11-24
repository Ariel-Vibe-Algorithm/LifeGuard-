# -*- coding: utf-8 -*-
"""
בדיקות ניווט UI: בונה את המסך הראשי ואת כל החלונות החדשים מול משתמש זמני, ומוודא שאין
קריסות בזמן ריצה. בלי מיקרופון/קיצורי מקלדת/רשת.

שורש Tk אחד לכל הסשן (כמו באפליקציה האמיתית): יצירה והריסה חוזרת של Tk() באותו תהליך
נכשלת לעיתים ב-Tcl ("invalid command name tcl_findLibrary") - מגבלה ידועה של Tcl, לא של LifeGuard.
"""
import time
import tkinter as tk

import pytest


@pytest.fixture(scope="session")
def shared_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def app(user, monkeypatch, shared_root):
    root = shared_root
    import main
    import engine as em
    monkeypatch.setattr(main, "HAS_KEYBOARD", False)
    monkeypatch.setattr(em.LifeGuardEngine, "start_voice_trigger", lambda self: None)
    monkeypatch.setattr(em.LifeGuardEngine, "start_live_monitor", lambda self: None)
    monkeypatch.setattr(main.connectivity_module, "start_monitor", lambda *a, **k: None)
    monkeypatch.setattr(main.LifeGuardApp, "_refresh_emergency_number", lambda self: None)
    monkeypatch.setattr(main.LifeGuardApp, "_run_startup_health_check", lambda self: None)
    for name in ("siren_module", "audio_module", "camera_module", "voice_warning_module"):
        mod = getattr(em, name)
        for fn in ("play_siren", "record_loop", "capture_loop", "warning_loop"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location",
                        lambda *a, **k: {"available": False, "is_precise": False})
    a = main.LifeGuardApp(root, user)
    yield a, root
    a._alive = False
    try:
        a.engine.shutdown()
    except Exception:
        pass
    _pump(root, 0.2)
    for child in root.winfo_children():  # ניקוי החלון המשותף לקראת הבדיקה הבאה
        try:
            child.destroy()
        except tk.TclError:
            pass
    root.withdraw()


def _pump(root, seconds=0.4):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def test_main_window_builds(app):
    a, root = app
    _pump(root)
    assert a.status_label.winfo_exists()


def test_open_safety_center_and_select_scenario(app):
    a, root = app
    a._open_safety_center()
    _pump(root)
    win = a._windows["safety"]
    assert win.winfo_exists()
    # פתיחה חוזרת מחזירה את אותו חלון (singleton)
    a._open_safety_center()
    assert a._windows["safety"] is win


def test_open_history_and_health(app):
    a, root = app
    a._open_history()
    a._open_health()
    _pump(root, 0.3)
    assert a._windows["history"].winfo_exists()
    assert a._windows["health"].winfo_exists()


def test_sos_window_shows_honest_delivery_status(app):
    a, root = app
    a.engine.trigger(force_panic=True, source="button")
    _pump(root, 1.0)
    assert a.sos_window is not None and a.sos_window.winfo_exists()
    text = a.sos_window.delivery_label.cget("text")
    assert "לא נשלחה" in text or "עדיין לא" in text  # לעולם לא "נשלח" כשלא נשלח
    # מד הסכנה לא מוצג לאדם שבמצוקה - רק סטטוס העזרה
    assert not hasattr(a.sos_window, "event_gauge")
    from gauge_widget import RiskGauge
    stack = [a.sos_window]
    while stack:
        w = stack.pop()
        assert not isinstance(w, RiskGauge), "מד סכנה במסך ה-SOS"
        stack.extend(w.winfo_children())
    help_text = a.sos_window.help_status_label.cget("text")
    assert help_text and "HIGH" not in help_text and "סכנה" not in help_text
    # במצב SOS פעיל: צ'אט (הודעות מהירות) והנחיות מוצגים בפועל (באג: pack(before=) על רכיב לא ארוז)
    assert a.sos_window.chat is not None and a.sos_window.chat.winfo_manager() == "pack"
    # צ'אט במרכז; פירוט הסיטואציה + עזרה ראשונה בלשוניות בעמודה השמאלית (מתחת למפה)
    sw = a.sos_window
    assert sw.chat.master is not sw.map_frame and sw.left_tabs.winfo_manager() == "pack"
    assert sw._left_tab == "guidance" and sw._left_panels["guidance"][0].winfo_manager() == "pack"
    sw.show_left_tab("first_aid")
    _pump(root, 0.3)
    fa = sw._left_panels["first_aid"][0]
    assert fa.winfo_manager() == "pack" and sw._left_panels["guidance"][0].winfo_manager() == ""
    fa.search("דימום חזק")
    assert fa._last_results and fa._last_results["results"]                     # עובד בלי רשת
    sw.show_left_tab("guidance")
    a.engine.cancel_with_password("StrongPass!2026")
    _pump(root, 0.5)
    assert a.sos_window is None


def test_countdown_window_has_live_counter_and_send_now(app, monkeypatch):
    a, root = app
    import engine as em
    monkeypatch.setattr(em.risk_module, "assess_risk",
                        lambda **k: {"level": "LOW", "score": 0, "reasons": []})
    a.engine.trigger(source="button")
    _pump(root, 1.5)
    assert a.engine.state.name == "COUNTDOWN"
    assert a.sos_window.countdown_label.cget("text").isdigit()
    a.sos_window._send_now()
    _pump(root, 0.6)
    assert a.engine.state.name == "ACTIVE_SOS"
    a.engine.cancel_with_password("StrongPass!2026")
    _pump(root, 0.3)


def test_silent_mode_shows_nothing(app):
    a, root = app
    _pump(root, 0.3)
    before = a.log_text.get("1.0", "end")
    a.engine.activate_duress_mode(source="typed")
    _pump(root, 0.8)
    assert a.engine.state.name == "SILENT_TRACKING"
    assert a.sos_window is None  # שום מסך SOS
    assert "ממתין" in a.status_label.cget("text")  # נראה בדיוק כמו IDLE
    new_log = a.log_text.get("1.0", "end")[len(before) - 1:]
    # באג שתוקן: בעבר נכתב כאן "אירוע נרשם במרכז הקהילה" - חשיפה גלויה של המצב השקט
    for leak in ("קהילה", "שקט", "כפייה", "SOS", "מעקב"):
        assert leak not in new_log, f"דליפה ליומן הגלוי: {leak}"
    a.engine.end_silent_tracking()


# ---------------------------------------------------------------- UI + שרת אמיתי
@pytest.fixture
def live_server(tmp_path, monkeypatch):
    import threading
    import config
    from server.lifeguard_server import create_server
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=30, tick_seconds=0.2)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(config, "SERVER_URL", url)
    yield url
    httpd.shutdown()


def test_recipient_app_gets_alert_window_and_accepts(live_server, user, monkeypatch, shared_root):
    """dana לוחצת SOS (לקוח) -> באפליקציה של tester נפתח חלון התראה -> 'אני מטפל/ת' -> dana רואה אישור."""
    from backend_client import BackendClient
    import main
    import engine as em
    root = shared_root
    monkeypatch.setattr(main, "HAS_KEYBOARD", False)
    monkeypatch.setattr(em.LifeGuardEngine, "start_voice_trigger", lambda self: None)
    monkeypatch.setattr(em.LifeGuardEngine, "start_live_monitor", lambda self: None)
    monkeypatch.setattr(main.connectivity_module, "start_monitor", lambda *a, **k: None)
    monkeypatch.setattr(main.LifeGuardApp, "_refresh_emergency_number", lambda self: None)
    monkeypatch.setattr(main.LifeGuardApp, "_run_startup_health_check", lambda self: None)
    monkeypatch.setattr(main.location_module, "get_location",
                        lambda *a, **k: {"available": True, "is_precise": True, "lat": 32.08, "lon": 34.78,
                                         "source": "GNSS", "timestamp": time.time()})
    monkeypatch.setattr("alert_window._beep", lambda: None)
    root.deiconify()  # חלון גלוי כמו אצל משתמש אמיתי (חלון מוסתר = מצב "כיבוי מדומה" -> בלי פופאפים)
    app = main.LifeGuardApp(root, user, "StrongPass!2026")
    assert _wait_ui(root, lambda: app.client is not None), app.server_error
    dana = BackendClient(live_server)
    dana.login_or_register("dana", "StrongPass!2026", "Dana")
    dana.invite(user)
    app.client.respond_invite("dana", True)
    inc = dana.create_incident("ui-evt", emergency_type="medical",
                               location={"lat": 32.081, "lon": 34.781, "is_precise": True},
                               risk={"level": "HIGH", "score": 6, "reasons": ["מילות מצוקה זוהו: הצילו"]})
    assert "risk" not in inc                       # בעלת האירוע לא מקבלת את המד
    win = _wait_ui(root, lambda: app._alert_windows.get(inc["id"]))
    assert win is not None and win.winfo_exists(), "חלון ההתראה לא נפתח"
    assert _wait_ui(root, lambda: "Dana" in win.title_label.cget("text"))
    # אצל איש הקשר: מד סכנה + עדיפות + הנימוקים
    assert _wait_ui(root, lambda: win.risk_card.winfo_ismapped())
    assert "הצילו" in win.risk_text.cget("text") and "עדיפות" in win.risk_text.cget("text")
    win.respond("accept")
    view = _wait_ui(root, lambda: (lambda v: v if any(r["state"] == "accepted" for r in v["recipients"]) else None)(
        dana.get_incident(inc["id"])))
    assert view, "האישור לא הגיע לבעלת האירוע"
    # מסך הקהילה מציג את ההתראה הפעילה ואת קשר האמון
    app.show_screen("community")
    assert _wait_ui(root, lambda: app.screens["community"]._last_summary is not None)
    app._alive = False
    app.client.stop_stream()
    app.engine.shutdown()
    for child in root.winfo_children():
        child.destroy()
    root.withdraw()


def _wait_ui(root, pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


def test_hebrew_text_is_rendered_rtl_but_reads_back_clean(shared_root):
    """פיסוק בסוף משפט עברי לא קופץ להתחלה: כל טקסט עברי מוטמע RTL; cget מחזיר את המקור."""
    import tkinter as tk
    import ui_theme as T
    RLE, PDF, NL = "‫", "‬", "\n"
    lbl = tk.Label(shared_root, text="הכל בסדר?")
    raw = lbl.tk.call(lbl._w, "cget", "-text")
    assert raw.startswith(RLE) and raw.endswith(PDF)
    assert lbl.cget("text") == "הכל בסדר?"
    two = "שורה א:" + NL + "LifeGuard שורה ב."
    lbl.configure(text=two)
    raw = lbl.tk.call(lbl._w, "cget", "-text")
    assert raw.count(RLE) == 2 and lbl.cget("text") == two
    lbl.configure(text="English only")
    assert lbl.tk.call(lbl._w, "cget", "-text") == "English only"          # לא נוגעים בטקסט לועזי
    assert T.rtl(T.rtl("שלום")) == T.rtl("שלום")                          # לא כפול
    lbl.destroy()

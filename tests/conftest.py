# -*- coding: utf-8 -*-
"""
conftest.py - בידוד מלא של כל בדיקה מהנתונים האמיתיים.
כל בדיקה מקבלת תיקיית data זמנית משלה; אין שום סיכון לגעת ב-users.json/האירועים האמיתיים.
"""
import gc
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import incident_store
import auth_module


@pytest.fixture(autouse=True)
def no_real_microphone(monkeypatch):
    """בדיקות לעולם לא פותחות את המיקרופון האמיתי (ההאזנה הקולית נבדקת דרך _on_audio_chunk)."""
    import voice_trigger_module
    monkeypatch.setattr(voice_trigger_module, "HAS_SR", False)
    voice_trigger_module._MIC_RELEASED.set()
    yield
    voice_trigger_module._MIC_RELEASED.set()


def pytest_configure(config):
    config.addinivalue_line("markers", "real_toast: שולח התראת Windows אמיתית")


@pytest.fixture(autouse=True)
def os_toasts(monkeypatch, request):
    """בדיקות לא מקפיצות התראות Windows על המסך: הקריאות נאספות לרשימה ונבדקות.
    בדיקה שמסומנת real_toast שולחת התראה אמיתית (ראה test_windows_notify.py)."""
    import windows_notify
    sent = []
    if request.node.get_closest_marker("real_toast") is None:
        monkeypatch.setattr(windows_notify, "notify",
                            lambda title, body, urgent=False, tag="lg", wait=False, launch=None: sent.append(
                                {"title": title, "body": body, "urgent": urgent, "tag": tag, "launch": launch}))
        monkeypatch.setattr(windows_notify, "remove", lambda tag=None: sent.append({"removed": tag}))
    return sent


@pytest.fixture(autouse=True)
def no_real_agent_side_effects(monkeypatch, request):
    """בדיקות ממשק (LifeGuardApp) לא מפעילות תהליך סוכן אמיתי ולא כותבות ל-Run/Classes ברג'יסטרי
    של המשתמש. test_windows_agent.py בודק את הפונקציות האלה בעצמו (עם שמות מפתח זמניים)."""
    if request.node.fspath.basename == "test_windows_agent.py":
        return
    import agent_bridge
    for fn in ("install_autostart", "uninstall_autostart", "ensure_agent_running", "register_protocol",
               "mark_app_running"):
        monkeypatch.setattr(agent_bridge, fn, lambda *a, **k: None)


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    audio = data / "audio"
    photos = data / "photos"
    for d in (data, audio, photos):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", str(data))
    monkeypatch.setattr(config, "USERS_DB_FILE", str(data / "users.json"))
    monkeypatch.setattr(config, "EVENTS_DB_FILE", str(data / "events.json"))
    monkeypatch.setattr(config, "LOG_DIR", str(data))
    monkeypatch.setattr(config, "AUDIO_DIR", str(audio))
    monkeypatch.setattr(config, "PHOTOS_DIR", str(photos))
    monkeypatch.setattr(auth_module, "LAST_STORAGE_ERROR", None, raising=False)
    incident_store.close()
    monkeypatch.setattr(incident_store, "DB_PATH", str(data / "lifeguard.db"))
    yield {"data": str(data), "audio": str(audio), "photos": str(photos)}
    incident_store.close()


@pytest.fixture
def user(temp_env):
    ok, msg = auth_module.register_user(
        "tester", "StrongPass!2026",
        [{"name": "Mom", "account": "mom@example.test", "role": "family"}],
        voice_secret_word="red siren", silent_alarm_phrase="blue lantern",
        secret_cancel_phrase="green garden", duress_phrase="silver window",
        terms_accepted=True,
    )
    assert ok, msg
    return "tester"


@pytest.fixture(autouse=True)
def _accounts_not_new(monkeypatch):
    """כל חשבונות הבדיקה "חדשים". ההגבלה על חשבון חדש (server/abuse.py) נבדקת במפורש ב-
    test_security_regressions; בשאר הבדיקות היא כבויה כדי לבדוק את ההתנהגות הרגילה."""
    from server import abuse
    monkeypatch.setattr(abuse, "NEW_ACCOUNT_HOURS", 0)


@pytest.fixture(autouse=True)
def _volunteer_identity_not_required(monkeypatch, request):
    """אימות זהות חובה למתנדבים (server/volunteer_identity.py) נבדק במפורש ב-test_volunteer_identity.py
    (כולל שיבוץ, תמיכה מרחוק ורשימת "מתנדבים בסביבה"); בשאר הבדיקות הוא כבוי כדי לבדוק את זרימות השיבוץ
    עצמן בלי להעלות ת"ז לכל מתנדב/ת בדיקה."""
    if request.node.fspath.basename == "test_volunteer_identity.py":
        return
    from server import volunteer_identity
    monkeypatch.setattr(volunteer_identity, "ID_REQUIRED", False)


@pytest.fixture(autouse=True)
def _finalize_tk_garbage_on_main_thread():
    """אובייקטי Tk שנשארו מבדיקות ממשק (אריחי מפה, Variable) משתחררים ב-GC. אם ה-GC רץ ב-thread של
    השרת, ה-__del__ שלהם קורא ל-Tk מ-thread אחר - וב-Windows הקריאה ממתינה ללולאת האירועים של ה-thread
    הראשי. הבקשה נתקעה עד timeout של הלקוח (6 שניות) - כך נכשלה בכל הרצה מלאה בדיקה אקראית אחרת.
    איסוף אחרי כל בדיקה, ב-thread הראשי, מונע את זה."""
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def _never_spawn_local_server(monkeypatch, request):
    """בדיקות לא מפעילות שרת אמיתי ברקע (local_server.ensure_running) כשהשרת "לא זמין" בכוונה.
    test_local_server.py בודק את ההפעלה עצמה עם popen מדומה."""
    if request.node.fspath.basename == "test_local_server.py":
        return
    import local_server
    monkeypatch.setattr(local_server, "_python_for_server", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_windows_location(monkeypatch):
    """בדיקות לא קוראות לשירות המיקום האמיתי של Windows (איטי, ומחזיר את המיקום האמיתי של המשתמש/ת).
    test_windows_location.py בודק את הפענוח עם runner מדומה."""
    monkeypatch.setenv("LIFEGUARD_DISABLE_WINDOWS_LOCATION", "1")


@pytest.fixture(autouse=True)
def _no_live_weather_or_public_routing(monkeypatch):
    """בדיקות לא פונות ל-Open-Meteo או לנתב הציבורי של FOSSGIS (server/weather.py, server/routing.py).
    test_route_modes.py מפעיל אותם במפורש עם HTTP מדומה."""
    monkeypatch.setenv("LIFEGUARD_WEATHER", "0")
    monkeypatch.setenv("LIFEGUARD_PUBLIC_ROUTING", "0")

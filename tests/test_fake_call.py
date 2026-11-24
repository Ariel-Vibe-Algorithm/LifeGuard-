# -*- coding: utf-8 -*-
"""שיחה מזויפת (fake_call.py): הגדרות בפרופיל, ביטוי קולי שנבדק אחרי ביטויי החירום, מסך שיחה נכנסת."""
import io
import time
import wave

import pytest

import fake_call as FC
from test_ui_navigation import shared_root, _pump  # noqa: F401


def test_validate_requires_distinct_phrase_from_sos():
    ok = dict(FC.DEFAULTS, enabled=True, phrase="תפוח ירוק", caller_name="אמא")
    assert FC.validate(ok, ["הצילו הצילו", "ביטול עכשיו"]) == []
    clash = dict(ok, phrase="הצילו")
    assert any("שונה מביטויי החירום" in e for e in FC.validate(clash, ["הצילו הצילו"]))
    assert FC.validate(dict(ok, phrase="א"), []) and FC.validate(dict(ok, caller_name=""), [])
    assert FC.validate(dict(ok, delay_s=7), []) and FC.validate(dict(ok, ringtone="x"), [])


def test_save_and_load_roundtrip(temp_env, user):
    s = FC.save_settings(user, {"enabled": True, "phrase": " איפה המפתחות ", "caller_name": "דנה",
                                "caller_label": "עבודה", "delay_s": 5, "ringtone": "soft"})
    assert s["phrase"] == "איפה המפתחות"
    got = FC.load_settings(user)
    assert got["enabled"] and got["caller_name"] == "דנה" and got["delay_s"] == 5
    with pytest.raises(ValueError):
        FC.save_settings(user, dict(got, delay_s=999))


def _listener(calls, **phr):
    import voice_trigger_module as V
    logs = []
    lst = V.VoiceTriggerListener(secret_word=phr.get("loud", "הצילו הצילו"), silent_phrase="שקט שקט",
                                 cancel_phrase="ביטול ביטול", fake_call_phrase="איפה המפתחות",
                                 on_triggered=lambda: calls.append("loud"),
                                 on_silent_triggered=lambda: calls.append("silent"),
                                 on_cancel_triggered=lambda: calls.append("cancel"),
                                 on_fake_call=lambda: calls.append("fake"), log_callback=logs.append)
    return lst, logs


def test_voice_phrase_triggers_fake_call_after_sos_phrases():
    calls = []
    lst, logs = _listener(calls)
    lst._handle_transcripts(["רגע איפה המפתחות שלי"])
    assert calls == ["fake"] and logs == []                     # בלי רישום ליומן
    calls.clear()
    lst._handle_transcripts(["הצילו הצילו איפה המפתחות"])         # חירום באותו תמלול - גובר
    assert calls == ["loud"]


def test_engine_only_shows_fake_call_when_idle(temp_env, user):
    from engine import LifeGuardEngine, State
    eng = LifeGuardEngine(username=user)
    try:
        hits = []
        eng.on_fake_call = lambda linked=False: hits.append(1)
        eng._handle_fake_call_phrase()
        assert hits == [1]
        assert eng.state == State.IDLE                             # בלי "גם התראה שקטה" - שום אירוע
        eng.state = State.SILENT_TRACKING                          # במעקב שקט / SOS - לא מפריעים
        eng._handle_fake_call_phrase()
        assert hits == [1]
    finally:
        eng.state = State.IDLE
        eng.shutdown()


def test_ringtone_is_valid_wav():
    data = FC.ringtone_wav("classic", seconds=1.0)
    with wave.open(io.BytesIO(data)) as w:
        assert w.getframerate() == 16000 and abs(w.getnframes() / 16000 - 1.0) < 0.01
    silent = FC.ringtone_wav("vibrate", seconds=0.5)
    with wave.open(io.BytesIO(silent)) as w:
        assert set(w.readframes(w.getnframes())) == {0}


def test_scheduler_shows_incoming_call_accept_and_end(shared_root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(FC._Ringer, "start", lambda self: None)     # בלי צליל בבדיקות
    settings = dict(FC.DEFAULTS, enabled=True, phrase="איפה המפתחות", caller_name="אמא", delay_s=0)
    sch = FC.FakeCallScheduler(shared_root, lambda: settings)
    assert sch.trigger() is True
    assert sch.trigger() is False                                   # שיחה אחת בכל פעם
    end = time.time() + 3
    while sch.window is None and time.time() < end:
        _pump(shared_root, 0.05)
    w = sch.window
    assert w is not None and w.winfo_exists()
    w.accept_btn.invoke()
    _pump(shared_root, 1.2)
    assert w.answered_at is not None and ":" in w.timer_label.cget("text")
    w.end_btn.invoke()
    _pump(shared_root, 0.1)
    assert sch.window is None and sch.trigger() is True              # אפשר שוב אחרי שהסתיימה
    _pump(shared_root, 0.3)
    sch.cancel()


# ---------------------------------------------------------------- שיחה + התראה שקטה
@pytest.fixture
def quiet_engine(user, monkeypatch):
    import engine as em
    monkeypatch.setattr(em.siren_module, "play_siren", lambda *a, **k: None)
    monkeypatch.setattr(em.audio_module, "record_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.camera_module, "capture_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.voice_warning_module, "warning_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location", lambda *a, **k: {"available": False, "is_precise": False})
    monkeypatch.setattr(em.location_module, "build_live_map_link", lambda *a, **k: None)
    monkeypatch.setattr(em, "connectivity_module",
                        type("C", (), {"status": staticmethod(lambda: {"online": True}),
                                       "check_now": staticmethod(lambda: {"online": True})})())
    eng = em.LifeGuardEngine(user)
    yield eng
    if eng.state == em.State.SILENT_TRACKING:
        eng.end_silent_tracking()
    eng.shutdown()


def _link_on(user):
    FC.save_settings(user, dict(FC.DEFAULTS, enabled=True, phrase="איפה המפתחות", with_silent_alert=True))


def test_linked_phrase_starts_silent_alert_and_decline_cancels_it(quiet_engine, user):
    from engine import State
    _link_on(user)
    got = []
    quiet_engine.on_fake_call = lambda linked=False: got.append(linked)
    quiet_engine._handle_fake_call_phrase()
    assert got == [True] and quiet_engine.state == State.SILENT_TRACKING
    assert quiet_engine.current_event["trigger_source"] == "fake_call"
    assert quiet_engine.fake_call_declined() is not False         # ניתוק -> ההתראה יורדת
    assert quiet_engine.state != State.SILENT_TRACKING


def test_decline_never_cancels_a_silent_alert_it_did_not_start(quiet_engine, user):
    from engine import State
    _link_on(user)
    quiet_engine.activate_duress_mode(source="typed")              # התראה שקטה אמיתית (כפייה)
    time.sleep(0.2)
    assert quiet_engine.state == State.SILENT_TRACKING
    got = []
    quiet_engine.on_fake_call = lambda linked=False: got.append(linked)
    quiet_engine._handle_fake_call_phrase()                         # לא IDLE -> לא מציגים שיחה
    assert got == []
    assert quiet_engine.fake_call_declined() is False
    assert quiet_engine.state == State.SILENT_TRACKING              # ההתראה האמיתית נשארת


def _show_call(root, sch, link):
    assert sch.trigger(link_silent=link) is True
    end = time.time() + 3
    while sch.window is None and time.time() < end:
        _pump(root, 0.05)
    assert sch.window is not None
    return sch.window


def _linked_scheduler(root, monkeypatch, declined):
    monkeypatch.setattr(FC._Ringer, "start", lambda self: None)
    settings = dict(FC.DEFAULTS, enabled=True, phrase="איפה המפתחות", delay_s=0, with_silent_alert=True)
    return FC.FakeCallScheduler(root, lambda: settings, on_declined=lambda: declined.append(1),
                                run_bg=lambda fn: fn())


def test_window_decline_calls_on_declined_but_answer_and_timeout_do_not(shared_root, monkeypatch):  # noqa: F811
    declined = []
    sch = _linked_scheduler(shared_root, monkeypatch, declined)
    w = _show_call(shared_root, sch, link=True)
    w.decline_btn.invoke()                                          # ניתוק לפני מענה
    _pump(shared_root, 0.1)
    assert declined == [1] and w.outcome == "declined"

    w = _show_call(shared_root, sch, link=True)
    w.accept_btn.invoke()                                           # מענה
    _pump(shared_root, 0.1)
    w.decline()                                                     # Esc / סגירה אחרי מענה = סיום רגיל
    _pump(shared_root, 0.1)
    assert declined == [1] and w.outcome == "answered"

    w = _show_call(shared_root, sch, link=True)
    w.no_answer()                                                   # 45 שנ' בלי תגובה
    _pump(shared_root, 0.1)
    assert declined == [1] and w.outcome == "no_answer"


def test_unlinked_call_decline_does_not_touch_alerts(shared_root, monkeypatch):  # noqa: F811
    declined = []
    sch = _linked_scheduler(shared_root, monkeypatch, declined)
    w = _show_call(shared_root, sch, link=False)
    w.decline_btn.invoke()
    _pump(shared_root, 0.1)
    assert declined == [] and w.outcome == "declined"

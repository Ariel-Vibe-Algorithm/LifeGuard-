# -*- coding: utf-8 -*-
"""
חיסכון בחשמל + דיוק: ברגיל זיהוי דיבור מקומי בלבד (בלי רשת); בזמן סכנה Google רץ *במקביל* כתמלול נוסף,
לא מעכב את המקומי יותר מהזמן הקצוב, ולא נקרא כשאין סכנה או כשהמשתמש/ת כיבה זאת.
"""
import time

import speech_local as S


class FakeRec:
    def __init__(self, text="תעזרו לי", delay=0.0):
        self.text, self.delay, self.calls = text, delay, 0

    def recognize_google(self, audio, language=None):
        self.calls += 1
        time.sleep(self.delay)
        return self.text


def _local(monkeypatch, text="שלום"):
    monkeypatch.setattr(S, "is_available", lambda lang: True)
    monkeypatch.setattr(S, "audio_data_to_float", lambda a: ([0.0] * 10, 16000))
    monkeypatch.setattr(S, "transcribe", lambda samples, rate, lang: text)


def test_idle_is_local_only(monkeypatch):
    _local(monkeypatch)
    rec = FakeRec()
    assert S.recognize_speech(object(), ["he"], recognizer=rec) == ["שלום"] and rec.calls == 0


def test_danger_adds_google_in_parallel(monkeypatch):
    _local(monkeypatch)
    rec = FakeRec("תעזרו לי")
    out = S.recognize_speech(object(), ["he"], recognizer=rec, cloud_assist=True)
    assert out == ["שלום", "תעזרו לי"] and rec.calls == 1


def test_slow_or_failing_cloud_never_blocks_local(monkeypatch):
    _local(monkeypatch)
    monkeypatch.setattr(S, "CLOUD_ASSIST_TIMEOUT_S", 0.2)
    t = time.time()
    assert S.recognize_speech(object(), ["he"], recognizer=FakeRec(delay=3), cloud_assist=True) == ["שלום"]
    assert time.time() - t < 1.0

    class Boom:
        def recognize_google(self, *a, **k):
            raise OSError("no network")
    assert S.recognize_speech(object(), ["he"], recognizer=Boom(), cloud_assist=True) == ["שלום"]


def test_user_setting_and_engine_states(temp_env, user, monkeypatch):
    import incident_store
    from engine import LifeGuardEngine, State
    assert S.cloud_assist_enabled(user) is True                        # ברירת מחדל: פעיל
    eng = LifeGuardEngine(username=user)
    try:
        assert eng._cloud_speech_now() is False                         # רגיל -> מקומי בלבד (חיסכון)
        for st in (State.COUNTDOWN, State.ACTIVE_SOS, State.SILENT_TRACKING):
            eng.state = st
            assert eng._cloud_speech_now() is True
        incident_store.set_setting(user, "cloud_speech_in_danger", "0")  # כיבוי אישי
        assert eng._cloud_speech_now() is False
    finally:
        eng.state = State.IDLE
        eng.shutdown()


def test_listener_passes_danger_flag(monkeypatch):
    import voice_trigger_module as V
    seen = []
    monkeypatch.setattr(V.speech_local, "recognize_speech",
                        lambda *a, **k: seen.append(k.get("cloud_assist")) or [])
    lst = V.VoiceTriggerListener(secret_word="a b c", silent_phrase="d e f", cancel_phrase="g h i",
                                 danger_fn=lambda: True)
    lst._running = True
    lst._personal_hits = lambda audio: ()
    lst._on_audio_chunk(None, object())
    lst.danger_fn = lambda: False
    lst._on_audio_chunk(None, object())
    assert seen == [True, False]

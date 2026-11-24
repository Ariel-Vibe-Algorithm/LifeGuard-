# -*- coding: utf-8 -*-
"""בדיקות מצב כפייה (duress) ברמת המנוע, ובדיקות קישוריות."""
import time

import pytest

from engine import LifeGuardEngine, State


@pytest.fixture
def engine(user, monkeypatch):
    import engine as em
    monkeypatch.setattr(em.siren_module, "play_siren", lambda *a, **k: None)
    monkeypatch.setattr(em.audio_module, "record_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.camera_module, "capture_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.voice_warning_module, "warning_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location",
                        lambda *a, **k: {"available": False, "is_precise": False})
    monkeypatch.setattr(em.location_module, "build_live_map_link", lambda *a, **k: None)
    monkeypatch.setattr(em, "connectivity_module",
                        type("C", (), {"status": staticmethod(lambda: {"online": True}),
                                       "check_now": staticmethod(lambda: {"online": True})})())
    return LifeGuardEngine(user)


def test_duress_typed_during_active_sos_goes_silent(engine):
    engine.trigger(force_panic=True, source="button")
    time.sleep(0.2)
    # הקלדת ביטוי הכפייה "מבטלת" כלפי חוץ אך עוברת למעקב שקט
    result = engine.cancel_with_password("silver window")
    assert result is True
    assert engine.state == State.SILENT_TRACKING
    engine.end_silent_tracking()


def test_duress_from_idle_starts_silent_tracking(engine):
    engine.activate_duress_mode(source="typed")
    time.sleep(0.2)
    assert engine.state == State.SILENT_TRACKING
    engine.end_silent_tracking()


def test_real_password_ends_silent_tracking(engine):
    engine.activate_duress_mode(source="typed")
    time.sleep(0.2)
    assert engine.state == State.SILENT_TRACKING
    assert engine.cancel_with_password("StrongPass!2026") is True
    assert engine.state == State.IDLE


def test_silent_tracking_does_not_log_to_visible_log(user, monkeypatch):
    import engine as em
    for name in ("siren_module", "audio_module", "camera_module", "voice_warning_module"):
        mod = getattr(em, name)
        for fn in ("play_siren", "record_loop", "capture_loop", "warning_loop"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location",
                        lambda *a, **k: {"available": False, "is_precise": False})
    visible = []
    eng = LifeGuardEngine(user, on_log=lambda m: visible.append(m))
    visible.clear()
    eng.activate_duress_mode(source="typed")
    time.sleep(0.3)
    # שום הודעה שמסגירה מעקב שקט לא הודפסה ליומן הגלוי
    joined = " ".join(visible)
    assert "שקט" not in joined and "כפייה" not in joined and "מעקב" not in joined
    eng.end_silent_tracking()


def test_connectivity_check_returns_structure():
    import connectivity_module as c
    st = c.check_now()
    assert "online" in st and "checked_at" in st and "detail" in st
    assert isinstance(st["online"], bool)

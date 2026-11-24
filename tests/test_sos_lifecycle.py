# -*- coding: utf-8 -*-
"""בדיקות מחזור חיים של SOS: הפעלה, ביטול, מניעת כפילות, מצב FEEDBACK, פאניקה."""
import time

import pytest

from engine import LifeGuardEngine, State


@pytest.fixture
def engine(user, monkeypatch):
    # מנטרלים תופעות לוואי אמיתיות (סירנה/הקלטה/מצלמה/מיקום/רשת) כדי שהבדיקות יהיו מהירות ודטרמיניסטיות.
    import engine as engine_mod
    monkeypatch.setattr(engine_mod.siren_module, "play_siren", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod.audio_module, "record_loop", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod.camera_module, "capture_loop", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod.voice_warning_module, "warning_loop", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod.location_module, "get_location",
                        lambda *a, **k: {"available": False, "is_precise": False})
    monkeypatch.setattr(engine_mod.location_module, "build_live_map_link", lambda *a, **k: None)
    monkeypatch.setattr(engine_mod, "connectivity_module",
                        type("C", (), {"status": staticmethod(lambda: {"online": True}),
                                       "check_now": staticmethod(lambda: {"online": True})})())
    # ללא חיבור לשרת: בדיוק התרחיש שבו אסור להציג "נשלח"
    return LifeGuardEngine(user)


def test_panic_activates_immediately_without_countdown(engine):
    assert engine.trigger(force_panic=True, source="button") is True
    time.sleep(0.3)
    assert engine.state == State.ACTIVE_SOS


def test_duplicate_trigger_is_ignored(engine):
    assert engine.trigger(force_panic=True, source="button") is True
    time.sleep(0.2)
    # טריגר שני בזמן אירוע פעיל חייב להידחות (מניעת אירוע כפול)
    assert engine.trigger(force_panic=True, source="button") is False
    engine._stop_active_sos()


def test_cancel_with_correct_password(engine):
    engine.trigger(force_panic=True, source="button")
    time.sleep(0.2)
    assert engine.cancel_with_password("StrongPass!2026") is True
    assert engine.state == State.FEEDBACK


def test_cancel_with_wrong_password_keeps_event_active(engine):
    engine.trigger(force_panic=True, source="button")
    time.sleep(0.2)
    assert engine.cancel_with_password("wrong") is False
    assert engine.state == State.ACTIVE_SOS
    engine._stop_active_sos()


def test_trigger_works_even_when_feedback_pending(engine):
    """באג שתוקן: טריגר נבלע כשחלון הדירוג היה פתוח (state=FEEDBACK)."""
    engine.trigger(force_panic=True, source="button")
    time.sleep(0.2)
    engine.cancel_with_password("StrongPass!2026")
    assert engine.state == State.FEEDBACK  # לא דורגנו עדיין
    # טריגר חדש חייב לעבוד, לא להיבלע
    assert engine.trigger(force_panic=True, source="button") is True
    time.sleep(0.2)
    assert engine.state == State.ACTIVE_SOS
    engine._stop_active_sos()


def test_delivery_status_never_claims_sent_without_channel(engine):
    engine.trigger(force_panic=True, source="button")
    time.sleep(0.3)
    status = engine.get_delivery_status()
    assert status["accepted"] == 0
    assert status["level"] == "critical"
    assert "התקשר" in status["text"]  # ממליץ להתקשר בעצמך
    engine._stop_active_sos()

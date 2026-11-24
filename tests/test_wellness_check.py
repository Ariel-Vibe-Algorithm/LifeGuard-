# -*- coding: utf-8 -*-
""""הכל בסדר?": זיהוי עצירה פתאומית, טריגרים (נפילה / עצירה / אין קליטה), תשובה, ו-SOS כשאין תשובה."""
import time
import types

import pytest

import wellness_check as W
from test_ui_navigation import app, shared_root  # noqa: F401

LAT, LON = 32.0853, 34.7818
DEG_PER_M = 1 / 111320.0


def _track(speeds_mps, dt=5.0, t0=0.0):
    """רצף מיקומים צפונה לפי מהירויות (מ'/שנ') לכל מקטע."""
    fixes, lat, t = [(t0, LAT, LON)], LAT, t0
    for v in speeds_mps:
        t += dt
        lat += v * dt * DEG_PER_M
        fixes.append((t, lat, LON))
    return fixes


def test_detect_abrupt_stop():
    assert W.detect_abrupt_stop(_track([10, 10, 10, 0, 0, 0, 0, 0]))          # רכב -> עמידה 25 שנ'
    assert not W.detect_abrupt_stop(_track([10, 10, 10, 0, 0]))               # עמידה קצרה מדי
    assert not W.detect_abrupt_stop(_track([1.3] * 10))                       # הליכה רגילה
    assert not W.detect_abrupt_stop(_track([10, 10, 0, 0, 1.3, 0, 0, 0]))    # המשיך ללכת - לא "תקוע"
    assert not W.detect_abrupt_stop(_track([0, 0, 0, 0]))


class FakeEngine:
    def __init__(self):
        self.state = types.SimpleNamespace(name="IDLE")
        self.triggered, self.logs = [], []

    def trigger(self, force_panic=False, source="manual"):
        self.triggered.append((force_panic, source))

    def log(self, t):
        self.logs.append(t)


def _mon(clock=None, checkin=0.3):
    eng = FakeEngine()
    asked = []
    m = W.WellnessMonitor(eng, ask=lambda reason, deadline, answer: asked.append((reason, deadline, answer)),
                          clock=clock or time.time, checkin_seconds=checkin)
    return m, eng, asked


def test_fall_asks_and_no_answer_triggers_sos():
    m, eng, asked = _mon()
    m.on_sensor("fall", 0.4)                       # ביטחון נמוך - לא שואלים
    assert not asked
    m.on_sensor("fall", 0.9)
    assert asked and asked[0][0] == "זוהתה נפילה"
    m.on_sensor("impact", 0.95)                    # כבר יש שאלה פתוחה - לא שואלים שוב
    assert len(asked) == 1
    time.sleep(0.6)
    assert eng.triggered == [(True, "wellness_check")]


def test_im_ok_cancels_and_quiets_further_prompts():
    now = [1000.0]
    m, eng, asked = _mon(clock=lambda: now[0], checkin=60)
    m.on_sensor("fall", 0.9)
    asked[0][2](True)                              # "אני בסדר"
    assert eng.triggered == [] and m.pending is None
    m.on_sensor("fall", 0.9)
    assert len(asked) == 1                         # שקט 5 דקות
    now[0] += W.COOLDOWN_SECONDS + 1
    m.on_sensor("fall", 0.9)
    assert len(asked) == 2
    asked[1][2](False)                             # "אני צריך/ה עזרה"
    assert eng.triggered == [(True, "wellness_check")]


def test_no_prompt_during_active_event_or_silent_tracking():
    for st in ("ACTIVE_SOS", "SILENT_TRACKING", "COUNTDOWN"):
        m, eng, asked = _mon()
        eng.state.name = st
        m.on_sensor("fall", 0.99)
        assert not asked, st


def test_abrupt_stop_from_locations_and_signal_loss():
    m, eng, asked = _mon(checkin=60)
    for f in _track([10, 10, 10, 0, 0, 0, 0, 0]):
        m.on_location(*f)
    assert asked and asked[0][0] == "עצירה פתאומית"
    m.answer(True)
    now = [0.0]
    m2, eng2, asked2 = _mon(clock=lambda: now[0], checkin=60)
    m2.on_connectivity(False, watching=False)      # בלי ליווי פעיל - לא מדאיג
    now[0] += W.SIGNAL_LOSS_SECONDS + 1
    m2.on_connectivity(False, watching=False)
    assert not asked2
    m2.on_connectivity(False, watching=True)
    now[0] += W.SIGNAL_LOSS_SECONDS - 10
    m2.on_connectivity(False, watching=True)
    assert not asked2
    now[0] += 20
    m2.on_connectivity(False, watching=True)
    assert asked2 and asked2[0][0] == "אין קליטה כבר כמה דקות"
    m2.on_connectivity(True, watching=True)        # הקליטה חזרה - מאפס
    assert m2.offline_since is None


def test_real_app_fall_signal_opens_prompt_and_ok_closes_it(app):
    """מנוע + ממשק אמיתיים: אות נפילה -> חלון 'הכל בסדר?' -> 'אני בסדר' -> אין SOS."""
    a, root = app
    root.deiconify()                    # חלון גלוי כמו אצל משתמש (מוסתר = "כיבוי מדומה" -> אסור להציג כלום)
    a.engine.report_sensor_signal("fall", 0.95)
    end = time.time() + 5
    while time.time() < end and not getattr(a, "_wellness_window", None):
        root.update()
        time.sleep(0.05)
    win = a._wellness_window
    assert win.winfo_exists() and a.engine.wellness.pending["reason"] == "fall"
    a.engine.wellness.answer(True)
    root.update()
    assert a.engine.state.name == "IDLE" and a.engine.wellness.pending is None
    win.destroy()
    root.withdraw()

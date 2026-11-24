# -*- coding: utf-8 -*-
"""
assessment_ledger (פנקס הערכות + חוקר טעויות):
- הרשומה לא מכילה אודיו, מיקום, מילים שזוהו או נימוקים בטקסט חופשי; התוכן מוצפן במנוחה.
- התווית מגיעה רק ממקור בלתי תלוי: דירוג (חזקה) או ביטול (חלשה, לא בחישוב כברירת מחדל). כפייה ≠ ביטול.
- בידוד בין משתמשים, מחיקה עם האירוע, ומדיניות שמירה.
- הדו"ח: סיווג טעויות (שווא / החמצה / החמצה בגלל קלט חסר), דיוק לפי מקור עם רווח סמך, בלי אחוז כשמעט נתונים.
- המנוע רושם כל הערכה ומקשר אותה לאירוע - גם כשהאירוע בוטל בזמן ההערכה.
"""
import json
import time
from unittest import mock

import pytest

import assessment_ledger as L
import incident_store as S


def _result(level="MEDIUM", events=(), sources=("acoustic_event",), quality="ok", keywords=(), score=3):
    return {
        "level": level, "score": score, "automatic_sos_eligible": level == "HIGH",
        "reasons": ["נימוק עם שם רחוב הרצל 5"], "noise_level": 0.12, "keywords_found": list(keywords),
        "independent_sources": ["acoustic_anomaly"], "assessment_kind": "heuristic_multi_signal_v1",
        "models": {"audio_intel": "hazard_audio_v1", "distress_head": "v2"},
        "audio_intel": {"quality": {"status": quality, "rms": 0.1},
                        "events": [{"category": c, "severity_prior": "high", "score": 2.5, "threshold": 2.0,
                                    "margin": 0.5, "near_threshold": c == "glass_break",
                                    "detection_confidence": 0.7, "fa_per_hour_at_score": 0.3, "model": "head"}
                                   for c in events],
                        "suspected_unvalidated": [], "distress": {"detected": False, "score": -1.0, "probability": 0.1}},
        "context": {"phase": "night", "lighting": "dark", "score": 1, "reasons": ["רחוב חשוך"], "poles": 0},
        "sensor_signals": [{"signal": "fall", "confidence": 0.8, "metadata": {"lat": 32.1, "lon": 34.8}}],
        "confidence": {"value": 0.4, "sources": list(sources)},
        "timestamp": time.time(),
    }


def _incident(owner, rating=None, outcome=None):
    iid = S.create_incident(owner, "sos", "button")
    if rating or outcome:
        S.update_incident(owner, iid, rating=rating, outcome=outcome, ended_at=time.time())
    return iid


# ---------------------------------------------------------------- פרטיות
def test_record_has_no_audio_location_words_or_free_text_and_is_encrypted(user):
    iid = _incident(user)
    L.record(user, iid, _result(events=["gunshot"], keywords=["הצילו", "הצילו (זוהה בקול שלך)"]))
    raw = S._conn().execute("SELECT record FROM assessments").fetchone()["record"]
    assert "gunshot" not in raw and "hazard_audio" not in raw          # מוצפן במנוחה
    rec = L.entries(user)[0]["record"]
    text = json.dumps(rec, ensure_ascii=False)
    for forbidden in ("הרצל", "הצילו", "32.1", "34.8", "רחוב חשוך", "emb", "reasons"):
        assert forbidden not in text, forbidden
    assert rec["keywords"] == {"count": 2, "personal": True}
    assert rec["audio"]["events"][0]["category"] == "gunshot" and rec["models"]["audio_intel"] == "hazard_audio_v1"
    assert rec["sensors"] == ["fall"] and rec["context"] == {"phase": "night", "lighting": "dark", "score": 1}


# ---------------------------------------------------------------- תוויות
def test_labels_only_from_independent_sources(user):
    cases = {"true_event": (1, "strong"), "felt_danger": (1, "strong"), "false_alarm": (0, "strong")}
    for rating, expected in cases.items():
        assert L.label_for({"rating": rating, "outcome": None})[:2] == expected
    assert L.label_for({"rating": None, "outcome": "cancelled_during_countdown"})[:2] == (0, "weak")
    # כפייה ממשיכה כמעקב שקט - אסור שתיראה כ"בוטל" ותילמד כהתראת שווא
    assert L.label_for({"rating": None, "outcome": "silent_tracking_stopped_by_account_owner"})[0] is None
    assert L.label_for({"rating": None, "outcome": "stopped_by_user"})[0] is None
    assert L.label_for(None)[0] is None
    # דירוג גובר על ביטול
    assert L.label_for({"rating": "true_event", "outcome": "cancelled_during_countdown"})[:2] == (1, "strong")


def test_label_follows_the_incident_after_rating(user):
    iid = _incident(user)
    L.record(user, iid, _result())
    assert L.entries(user)[0]["label"] is None
    S.update_incident(user, iid, rating="false_alarm")
    e = L.entries(user)[0]
    assert (e["label"], e["label_strength"]) == (0, "strong")


# ---------------------------------------------------------------- בידוד, מחיקה, שמירה
def test_owner_isolation_delete_and_retention(user):
    mine = _incident(user)
    L.record(user, mine, _result())
    L.record("other", _incident("other"), _result())
    assert len(L.entries(user)) == 1 and len(L.entries("other")) == 1
    S.delete_incident(user, mine)
    assert L.entries(user) == [] and len(L.entries("other")) == 1
    old = time.time() - 400 * 86400
    L.record(user, None, _result(), ts=old)                               # בלי אירוע (בוטל בזמן ההערכה)
    S.set_retention_days(user, 180)
    S.purge_expired(user)
    assert L.entries(user) == []


# ---------------------------------------------------------------- דו"ח טעויות
def test_report_classifies_errors_and_withholds_percentages_on_little_data(user):
    def add(level, rating=None, outcome=None, events=(), sources=("acoustic_event",), quality="ok"):
        L.record(user, _incident(user, rating, outcome), _result(level, events, sources, quality))
    add("MEDIUM", "false_alarm", events=["glass_break"])                  # שווא
    add("LOW", "true_event", sources=("manual",))                         # החמצה
    add("UNKNOWN", "true_event", sources=("manual",), quality="unavailable")  # החמצה בגלל קלט
    add("HIGH", "felt_danger", events=["gunshot"], sources=("manual", "acoustic_event"))
    add("MEDIUM", outcome="cancelled_during_countdown", events=["gunshot"])   # תווית חלשה
    add("MEDIUM")                                                         # לא דורג
    rep = L.report(user)
    assert rep["total"] == 6 and rep["labeled_used"] == 4
    assert rep["outcomes"]["false_alarm"] == 1 and rep["outcomes"]["miss"] == 1
    assert rep["outcomes"]["miss_no_input"] == 1 and rep["outcomes"]["true_high"] == 1
    assert rep["outcomes"]["weak_false_alarm"] == 1 and rep["outcomes"]["unlabeled"] == 1
    kinds = sorted(e["kind"] for e in rep["errors"])
    assert kinds == ["false_alarm", "miss", "miss_no_input"]
    fa = next(e for e in rep["errors"] if e["kind"] == "false_alarm")
    assert fa["near_threshold"] == ["glass_break"] and "audio:glass_break" in fa["sources"]
    assert rep["by_source"]["acoustic_event"] == {"fired": 2, "true": 1, "false": 1, "precision": None, "ci95": None}
    assert any("אין עדיין אימות" in x for x in rep["limits"])
    weak = L.report(user, include_weak=True)
    assert weak["labeled_used"] == 5 and weak["outcomes"]["false_alarm"] == 2
    text = L.format_report(rep)
    assert "התראת שווא" in text and "תווית חלשה" in text and "מגבלות" in text


def test_report_gives_precision_with_interval_once_enough_data(user):
    for i in range(8):
        L.record(user, _incident(user, "true_event" if i < 2 else "false_alarm"), _result(sources=("loud_noise",)))
    a = L.report(user)["by_source"]["loud_noise"]
    assert a["fired"] == 8 and a["precision"] == pytest.approx(0.25)
    lo, hi = a["ci95"]
    assert 0 < lo < 0.25 < hi < 1


def test_wilson_matches_known_value():
    lo, hi = L.wilson(2, 8)
    assert lo == pytest.approx(0.0715, abs=1e-3) and hi == pytest.approx(0.5907, abs=1e-3)


# ---------------------------------------------------------------- שילוב במנוע
@pytest.fixture
def quiet_engine(user, monkeypatch):
    import engine as em
    monkeypatch.setattr(em.siren_module, "play_siren", lambda *a, **k: None)
    monkeypatch.setattr(em.audio_module, "record_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.camera_module, "capture_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.voice_warning_module, "warning_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location", lambda *a, **k: {"available": False, "is_precise": False})
    monkeypatch.setattr(em.location_module, "get_last_known", lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "build_live_map_link", lambda *a, **k: None)
    eng = em.LifeGuardEngine(user)
    yield eng
    eng.shutdown()


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end and not pred():
        time.sleep(0.02)
    return pred()


def test_engine_records_each_assessment_and_cancel_gives_weak_label(quiet_engine, user):
    import engine as em
    with mock.patch.object(em.risk_module, "assess_risk", return_value=_result("MEDIUM", ["gunshot"])):
        assert quiet_engine.trigger(source="button")
        assert _wait(lambda: len(L.entries(user)) == 1)
        assert _wait(lambda: quiet_engine.state == em.State.COUNTDOWN)
        iid = quiet_engine.current_event["event_id"]
        assert quiet_engine.cancel_with_password("StrongPass!2026")
    e = L.entries(user)[0]
    assert e["incident_id"] == iid and e["record"]["trigger"] == "button"
    assert (e["label"], e["label_strength"]) == (0, "weak")


def test_engine_duress_during_countdown_is_not_labelled_cancelled(quiet_engine, user):
    import engine as em
    with mock.patch.object(em.risk_module, "assess_risk", return_value=_result("MEDIUM")):
        assert quiet_engine.trigger(source="button")
        assert _wait(lambda: quiet_engine.state == em.State.COUNTDOWN)
        assert quiet_engine.cancel_with_password("silver window")        # ביטוי הכפייה - "נראה" כמו ביטול
        assert _wait(lambda: quiet_engine.state == em.State.SILENT_TRACKING)
    assert L.entries(user)[0]["label"] is None
    quiet_engine.end_silent_tracking()
    assert L.entries(user)[0]["label"] is None


def test_ledger_failure_never_blocks_the_event(quiet_engine, user):
    import engine as em
    with mock.patch.object(em.risk_module, "assess_risk", return_value=_result("MEDIUM")), \
         mock.patch.object(L, "record", side_effect=RuntimeError("disk full")):
        assert quiet_engine.trigger(source="button")
        assert _wait(lambda: quiet_engine.state == em.State.COUNTDOWN)
        assert quiet_engine.cancel_with_password("StrongPass!2026")


# ---------------------------------------------------------------- מסך היסטוריית האירועים
from test_ui_navigation import shared_root  # noqa: E402,F401


def test_history_window_shows_ledger_report(shared_root, user):  # noqa: F811
    import incident_history_ui
    L.record(user, _incident(user, "false_alarm"), _result("MEDIUM", ["glass_break"]))
    w = incident_history_ui.IncidentHistoryWindow(shared_root, user)
    try:
        w.pw.set("StrongPass!2026")
        w._unlock()
        w._show_ledger_report()
        text = w.timeline.get("1.0", "end")
        assert "פנקס הערכות: 1 הערכות" in text and "התראת שווא" in text
    finally:
        w.destroy()

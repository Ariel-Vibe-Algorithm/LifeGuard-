# -*- coding: utf-8 -*-
"""
התראות סכנה אינטראקטיביות (hazard_alerts.py, hazard_monitor.py, hazard_alert_ui.py, engine):
- ניהול חכם: אותו אירוע מקובץ; חומרה עולה -> עדכון; שקט אחרי סגירה/שווא; משפחה אחרת = אירוע חדש.
- בטיחות: זיהוי לעולם לא מפעיל SOS ולא פונה לאנשי קשר / חירום בעצמו; במעקב שקט / SOS פעיל אין חלון.
- כל פעולה: SOS רק אחרי אישור; אנשי קשר - תשובת שרת אמיתית או שגיאה; חיוג - tel: או הודעה כנה; שווא + סיבה.
- העדפות: ברירת מחדל = חלונות פעילים; כיבוי -> הזיהוי ממשיך ונרשם בלי חלון; נשמר מוצפן ושורד הפעלה מחדש.
- פרטיות: בלי אודיו ובלי מיקום ברשומה; משוב שווא = user_reported, לא משנה ספים.
"""
import time
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pytest

import hazard_alerts as HA
import hazard_monitor as HM
import incident_store
from test_ui_navigation import shared_root  # noqa: F401


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _det(cat="gunshot", sev="high", conf=0.7):
    return {"category": cat, "severity": sev, "confidence": conf, "label_he": cat, "explanation": "x",
            "meta": {"score": 3.0, "model_version": "v"}}


# ---------------------------------------------------------------- ניהול חכם
def test_grouping_update_cooldown_and_independent_events():
    c = Clock()
    m = HA.AlertManager(clock=c)
    kind, a = m.decide(_det("impact", "low"))
    assert kind == "new"
    c.t += 30
    assert m.decide(_det("glass_break", "medium"))[0] == "update"        # אותה משפחה, חומרה עלתה
    assert a["severity"] == "medium" and a["count"] == 2
    c.t += 10
    assert m.decide(_det("impact", "low"))[0] == "grouped"
    c.t += 25
    k2, b = m.decide(_det("gunshot", "high"))                            # משפחה אחרת = אירוע עצמאי חדש
    assert k2 == "new" and b["id"] != a["id"]
    m.close(a, "dismiss")
    c.t += 60
    assert m.decide(_det("impact", "low"))[0] == "cooldown"
    assert m.decide(_det("glass_break", "high"))[0] == "new"             # חומרה גבוהה מהשקט - עוקפת
    m.close(b, "false_alarm")
    c.t += HA.COOLDOWN_DISMISS_S + 1
    assert m.decide(_det("gunshot", "high"))[0] == "cooldown"            # שווא = שקט ארוך יותר
    c.t += HA.COOLDOWN_FALSE_S
    assert m.decide(_det("gunshot", "high"))[0] == "new"


def test_rate_limit_between_independent_popups():
    c = Clock()
    m = HA.AlertManager(clock=c)
    m.decide(_det("gunshot", "high"))
    c.t += 5
    assert m.decide(_det("alarm", "medium"))[0] == "rate_limited"
    c.t += HA.MIN_GAP_ANY_S
    assert m.decide(_det("alarm", "medium"))[0] == "new"


# ---------------------------------------------------------------- העדפות
def test_prefs_default_on_encrypted_and_persist(temp_env):
    assert HA.load_prefs("dana")["popups"] is True and HA.load_prefs("dana")["min_severity"] == "medium"
    HA.save_prefs("dana", {"popups": False, "sound": False, "min_severity": "high", "language": "en", "vibration": True})
    raw = incident_store.get_setting("dana", "hazard_alert_prefs")
    assert "popups" not in raw                                            # מוצפן
    incident_store.close()                                                # "הפעלה מחדש"
    p = HA.load_prefs("dana")
    assert p == {"popups": False, "sound": False, "vibration": False, "min_severity": "high", "language": "en"}
    with pytest.raises(ValueError):
        HA.save_prefs("dana", {"min_severity": "extreme"})


# ---------------------------------------------------------------- המוניטור
def _report(events=(), distress=None, quality="ok"):
    return {"model_version": "hazard_audio_v1", "quality": {"status": quality}, "distress": distress,
            "events": [{"category": c, "severity_prior": s, "detection_confidence": 0.8, "label_he": c, "score": 5.0,
                        "threshold": 4.0, "model": "head", "near_threshold": False} for c, s in events]}


def _monitor(temp_user, rep, can=(True, None), prefs=None):
    shown = []
    mon = HM.HazardMonitor(temp_user, can_show=lambda: can, on_alert=lambda k, a: shown.append((k, a)),
                           analyze=lambda x, sr: rep, prefs_fn=lambda: prefs or dict(HA.DEFAULTS))
    return mon, shown


def _audio(sec=1.5):
    return (np.random.default_rng(0).standard_normal(int(16000 * sec)) * 0.05).astype(np.float32)


def test_monitor_shows_records_and_never_triggers_sos(temp_env):
    mon, shown = _monitor("dana", _report([("gunshot", "high"), ("siren", "info")]))
    out = mon.process(_audio())
    assert [k for k, *_ in out] == ["new"] and shown[0][1]["category"] == "gunshot"   # סירנה = מידע, לא חלון
    rows = HA.list_alerts("dana")
    assert rows[0]["shown"] == 1 and rows[0]["record"]["meta"]["model_version"] == "hazard_audio_v1"
    assert "lat" not in str(rows[0]["record"]) and "audio" not in rows[0]["record"]
    mon.process(_audio())
    assert len(shown) == 1                                                # אותו אירוע - לא חלון שני


def test_popups_disabled_detection_still_runs_and_is_recorded(temp_env):
    mon, shown = _monitor("dana", _report([("gunshot", "high")]), prefs=dict(HA.DEFAULTS, popups=False))
    mon.process(_audio())
    assert shown == []
    r = HA.list_alerts("dana")[0]
    assert r["shown"] == 0 and r["record"]["not_shown_reason"] == "popups_disabled"


def test_min_severity_and_silent_tracking_and_digital_silence(temp_env):
    mon, shown = _monitor("dana", _report([("impact", "low")]), prefs=dict(HA.DEFAULTS, min_severity="medium"))
    mon.process(_audio())
    assert shown == [] and HA.list_alerts("dana")[0]["record"]["not_shown_reason"] == "below_min_severity"
    mon2, shown2 = _monitor("eli", _report([("gunshot", "high")]), can=(False, "silent_tracking"))
    mon2.process(_audio())
    assert shown2 == [] and HA.list_alerts("eli")[0]["record"]["not_shown_reason"] == "silent_tracking"
    mon3, shown3 = _monitor("noa", _report([("gunshot", "high")], quality="unavailable"))
    assert mon3.process(_audio()) == [] and shown3 == []
    mon4, _ = _monitor("noa", _report([("gunshot", "high")]))
    assert mon4.process(_audio(0.2)) == []                                # קצר מדי


def test_distress_voice_from_distress_head(temp_env):
    mon, shown = _monitor("dana", _report([], distress={"detected": True, "probability": 0.9, "score": 7.0}))
    mon.process(_audio())
    assert shown[0][1]["category"] == "distress_voice" and "שמחה" in shown[0][1]["explanation"]


def test_feed_never_blocks_listener():
    mon = HM.HazardMonitor("x", analyze=lambda x, sr: time.sleep(1))
    t = time.time()
    for _ in range(50):
        mon.feed(b"\x00\x00" * 16000)
    assert time.time() - t < 0.5 and mon.stats["skipped_busy"] >= 49


# ---------------------------------------------------------------- המנוע
@pytest.fixture
def eng(user, monkeypatch):
    import engine as em
    monkeypatch.setattr(em.siren_module, "play_siren", lambda *a, **k: None)
    monkeypatch.setattr(em.audio_module, "record_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.camera_module, "capture_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.voice_warning_module, "warning_loop", lambda *a, **k: None)
    monkeypatch.setattr(em.location_module, "get_location", lambda *a, **k: {"available": False, "is_precise": False})
    monkeypatch.setattr(em.location_module, "build_live_map_link", lambda *a, **k: None)
    e = em.LifeGuardEngine(user)
    yield e
    e.shutdown()


def _alert(eng, cat="gunshot"):
    """אירוע טרי (בלי לעבור דרך הגבלת הקצב של המנהל - היא נבדקת למעלה)."""
    m = HA.AlertManager()
    kind, a = m.decide(_det(cat))
    eng.hazard_monitor.manager.open[a["family"]] = a
    HA.record_alert(eng.username, a, shown=True)
    return a


def test_engine_can_show_only_when_idle(eng):
    from engine import State
    assert eng._hazard_can_show() == (True, None)
    eng.state = State.SILENT_TRACKING
    assert eng._hazard_can_show() == (False, "silent_tracking")
    eng.state = State.ACTIVE_SOS
    assert eng._hazard_can_show()[0] is False
    eng.state = State.IDLE


def test_engine_actions_record_and_contacts_need_server(eng):
    a = _alert(eng)
    with pytest.raises(RuntimeError, match="לא מחובר"):
        eng.hazard_notify_contacts(a)                                     # אין "נשלח" מדומה
    assert HA.list_alerts(eng.username)[0]["response"] is None
    fake = SimpleNamespace(notify_trusted=lambda text: {"sent_to": ["mom"], "notification_ids": {"mom": 5}},
                           notification_status=lambda ids: {"mom": True})
    with mock.patch.object(eng.gateway, "connected", return_value=True), mock.patch.object(eng.gateway, "client", fake):
        res = eng.hazard_notify_contacts(a)
        assert res["sent_to"] == ["mom"] and eng.hazard_delivery_status({"mom": 5}) == {"mom": True}
    assert HA.list_alerts(eng.username)[0]["response"] == "contacts"
    b = _alert(eng, "alarm")
    eng.hazard_respond(b, "false_alarm", "horn")
    r = next(x for x in HA.list_alerts(eng.username) if x["id"] == b["id"])
    assert (r["response"], r["reason"], r["label_strength"]) == ("false_alarm", "horn", "user_reported")
    summary = HA.feedback_summary(eng.username)
    assert summary["alarm"]["false_alarm"] == 1 and summary["alarm"]["reasons"] == {"horn": 1}
    import assessment_ledger
    assert "התראות סכנה מ-AI" in assessment_ledger.format_report(assessment_ledger.report(eng.username))


def test_engine_sos_from_alert_is_normal_manual_flow(eng):
    from engine import State
    a = _alert(eng)
    with mock.patch.object(eng, "trigger", return_value=True) as trig:
        assert eng.hazard_activate_sos(a) is True
    trig.assert_called_once_with(source="hazard_alert")
    import risk_feedback
    assert "hazard_alert" in risk_feedback.MANUAL_TRIGGERS
    assert eng.state == State.IDLE


def test_detection_alone_never_calls_trigger(eng):
    with mock.patch.object(eng, "trigger") as trig:
        eng.hazard_monitor._analyze = lambda x, sr: _report([("gunshot", "high"), ("explosion", "high")])
        eng.hazard_monitor.process(_audio())
    trig.assert_not_called()


# ---------------------------------------------------------------- החלון
@pytest.fixture
def win_factory(shared_root, eng):  # noqa: F811
    wins = []

    def make(confirm=True, connected=False, dialer=None, lang="he"):
        app = SimpleNamespace(engine=eng, username=eng.username, emergency_numbers={"general": "112"},
                              screens={}, show_screen=lambda n: None)
        import hazard_alert_ui
        with mock.patch.object(eng.gateway, "connected", return_value=connected):
            w = hazard_alert_ui.HazardAlertWindow(shared_root, app, _alert(eng),
                                                  prefs=dict(HA.DEFAULTS, sound=False, language=lang),
                                                  confirm=lambda t, x: confirm, dialer=dialer)
        wins.append(w)
        return w, app
    yield make
    for w in wins:
        if w.winfo_exists():
            w.destroy()


def test_window_shows_details_and_all_actions(win_factory):
    w, _ = win_factory()
    assert set(w.buttons) == {"sos", "safe_place", "contacts", "call", "false_alarm", "dismiss"}
    assert "גבוהה" in w.sev_chip.cget("text") and "70%" in w.conf_chip.cget("text")
    assert str(w.buttons["contacts"].cget("state")) == "disabled"          # לא מחובר -> לא מוצג כזמין
    assert "לא מחובר" in w.buttons["contacts"].cget("text")


def test_window_sos_requires_confirmation(win_factory):
    w, app = win_factory(confirm=False)
    with mock.patch.object(app.engine, "trigger") as trig:
        w.activate_sos()
        trig.assert_not_called()
    w2, app2 = win_factory(confirm=True)
    with mock.patch.object(app2.engine, "trigger", return_value=True) as trig:
        w2.activate_sos()
        trig.assert_called_once()


def test_window_call_reports_missing_dialer_honestly(win_factory):
    def no_dialer(n):
        raise OSError("no handler")
    w, _ = win_factory(dialer=no_dialer)
    w.call_emergency()
    assert "אין במחשב הזה אפליקציית חיוג" in w.status.cget("text") and "112" in w.status.cget("text")
    dialed = []
    w2, _ = win_factory(dialer=dialed.append)
    w2.call_emergency()
    assert dialed == ["112"]


def test_window_false_alarm_with_reason_and_dismiss(win_factory):
    w, app = win_factory()
    w.show_reasons()
    w.reason_var.set("music_tv")
    aid = w.alert["id"]
    w.false_alarm()
    r = next(x for x in HA.list_alerts(app.username) if x["id"] == aid)
    assert r["response"] == "false_alarm" and r["reason"] == "music_tv"
    w2, app2 = win_factory()
    aid2 = w2.alert["id"]
    w2.dismiss()
    assert next(x for x in HA.list_alerts(app2.username) if x["id"] == aid2)["response"] == "dismiss"


def test_window_english_ltr(win_factory):
    w, _ = win_factory(lang="en")
    assert w.anchor == "w" and "Activate SOS" in w.buttons["sos"].cget("text")


def test_settings_section_saves_and_reloads(shared_root, temp_env):  # noqa: F811
    import tkinter as tk
    import settings_ui
    frame = tk.Frame(shared_root)
    obj = SimpleNamespace(username="dana", engine=None)
    obj._save_hazard_prefs = lambda: settings_ui.SettingsWindow._save_hazard_prefs(obj)
    labels = []
    try:
        settings_ui.SettingsWindow._build_hazard_section(obj, frame, lambda t: labels.append(t))
        assert labels == ["התראות סכנה מ-AI"] and obj.hz_popups.get() is True       # ברירת מחדל: פעיל
        obj.hz_popups.set(False)
        obj.hz_sev.set(HA.MIN_SEVERITY_OPTIONS["high"])
        obj.hz_lang.set("English")
        settings_ui.SettingsWindow._save_hazard_prefs(obj)
        assert "נשמר" in obj.hz_status.cget("text")
        p = HA.load_prefs("dana")
        assert (p["popups"], p["min_severity"], p["language"]) == (False, "high", "en")
        texts = " ".join(w.cget("text") for w in frame.winfo_children() if isinstance(w, tk.Label))
        assert "נשארות פעילות תמיד" in texts and "לא פעיל" in texts                 # אין מאזין -> נאמר בגלוי
    finally:
        frame.destroy()


def _soft_report(d_score, scream=0.0):
    r = _report([])
    r["distress"] = {"detected": False, "score": d_score, "probability": 0.6}
    r["category_scores"] = {"scream": scream}
    return r


def test_shout_sequence_rule_needs_k_soft_windows(temp_env):
    rule = {"version": "shout_sequence_v1", "k": 3, "t_low": 3.0, "s_low": 0.3, "span_s": 9.0}
    mon, shown = _monitor("dana", _soft_report(3.5))
    mon.sequence_rule = rule
    mon.process(_audio(1.5), now=100.0)
    mon.process(_audio(1.5), now=103.0)
    assert shown == []                                                   # שתי "כמעט-צעקות" - עדיין לא
    mon.process(_audio(1.5), now=106.0)
    assert len(shown) == 1 and shown[0][1]["label_he"] == "צעקות חוזרות" and shown[0][1]["severity"] == "medium"
    quiet, shown2 = _monitor("eli", _soft_report(1.0))
    quiet.sequence_rule = rule
    for t in (0, 3, 6, 9):
        quiet.process(_audio(1.5), now=float(t))
    assert shown2 == []                                                  # מתחת לסף הנמוך - אף פעם
    far, shown3 = _monitor("noa", _soft_report(3.5))
    far.sequence_rule = rule
    for t in (0, 60, 120):
        far.process(_audio(1.5), now=float(t))
    assert shown3 == []                                                  # מפוזר בזמן - לא רצף
    one, shown4 = _monitor("gal", _soft_report(3.5))
    one.sequence_rule = rule
    one.process(_audio(4.5), now=50.0)                                   # מקטע ארוך אחד = 3 חלונות
    assert len(shown4) == 1
    none, shown5 = _monitor("ron", _soft_report(3.5))
    none.sequence_rule = None
    for t in (0, 3, 6):
        none.process(_audio(1.5), now=float(t))
    assert shown5 == []                                                  # בלי קובץ כלל - כבוי

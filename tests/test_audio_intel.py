# -*- coding: utf-8 -*-
"""
audio_intel (מנוע אירועי סכנה קוליים v1):
- איכות קלט: שקט דיגיטלי = מיקרופון לא עובד (UNKNOWN), לא "שקט ובטוח".
- שילוב בהערכת הסיכון: אירוע בחומרה גבוהה מגיע לכל היותר ל-MEDIUM; אותו מיקרופון = מקור אחד (בלי ספירה כפולה);
  סירנה = מידע בלבד; חשד לא מאומת לא נכנס לניקוד; danger_probability תמיד None.
- כרטיס המודל: אין חפיפת מעלים, מדדים על eval לכל קטגוריה, וקריטריוני קבלה.
- עקביות: המסלול בזמן ריצה (wav -> YAMNet -> ראש) נותן את אותו ציון כמו המדידה מהמטמון (אם FSD50K קיים).
- שער champion/challenger חוסם נסיגה.
"""
import json
import os
from unittest import mock

import numpy as np
import pytest

import audio_intel as AI
import config
import risk_feedback as RF
import risk_module
import sensor_module

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "models", "hazard_audio.npz")
FSD = os.path.join(ROOT, "research", "samples", "fsd50k")


def _event(cat, severity, conf=0.8):
    return {"category": cat, "label_he": cat, "score": 1.0, "threshold": 0.5, "detected": True, "at_seconds": 0.0,
            "detection_confidence": conf, "fa_per_hour_at_score": 0.3, "severity_prior": severity, "margin": 0.5,
            "near_threshold": False, "model": "head", "validated_on": "FSD50K eval"}


def _report(events=(), status="ok", distress=None, suspected=()):
    return {"engine": AI.VERSION, "model_version": "hazard_audio_v1", "quality": {"status": status, "reason": "x"},
            "events": list(events), "suspected_unvalidated": list(suspected), "top": list(events),
            "distress": distress, "danger_probability": None, "danger_probability_reason": "r"}


def _assess(report, rms=0.05, ai=None, keywords=()):
    sensor_module.clear_sensor_signals()
    audio = (lambda *a, **k: (np.full(16000 * 2, rms, np.float32), 16000))
    with mock.patch.object(risk_module, "record_audio", audio), \
         mock.patch.object(risk_module, "analyze_audio_events", return_value=report), \
         mock.patch.object(risk_module, "classify_distress_audio", return_value=ai), \
         mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=list(keywords)):
        return risk_module.assess_risk(sensor_signals=[])


# ---------------------------------------------------------------- איכות קלט
def test_quality_flags_digital_silence_clipping_and_short_audio():
    assert AI.quality(np.zeros(32000, np.float32), 16000)["status"] == "unavailable"
    assert AI.quality(None, None)["status"] == "unavailable"
    assert AI.quality(np.full(8000, 0.1, np.float32), 16000)["status"] == "degraded"          # 0.5 ש'
    clipped = np.where(np.arange(32000) % 10 == 0, 1.0, 0.1).astype(np.float32)               # 10% בתקרה
    assert "רוויה" in AI.quality(clipped, 16000)["reason"]
    rng = np.random.default_rng(0)
    assert AI.quality((rng.standard_normal(32000) * 0.02).astype(np.float32), 16000)["status"] == "ok"


def test_digital_silence_means_unknown_not_safe():
    res = _assess(_report(status="unavailable"), rms=0.0)
    assert res["level"] == "UNKNOWN" and res["noise_level"] is None
    assert any("איכות הקלט" in r for r in res["reasons"])


# ---------------------------------------------------------------- שילוב בהערכת הסיכון
def test_high_severity_event_alone_is_medium_never_auto_sos():
    res = _assess(_report([_event("gunshot", "high")]))
    assert res["level"] == "MEDIUM" and not res["automatic_sos_eligible"]
    assert res["independent_sources"] == ["acoustic_anomaly"]
    assert res["audio_intel"]["events"][0]["category"] == "gunshot"
    assert res["audio_intel"]["danger_probability"] is None
    assert res["models"]["audio_intel"] == "hazard_audio_v1"
    assert any("ירי" in r or "gunshot" in r for r in res["reasons"])


def test_same_microphone_is_one_source_no_double_counting():
    ai = {"score": 9.0, "detected": True, "probability": 0.99, "at_seconds": 0.0}
    both = _assess(_report([_event("gunshot", "high")], distress=ai), ai=ai)
    only_ai = _assess(_report([], distress=ai), ai=ai)
    assert both["score"] == only_ai["score"]                 # המקסימום, לא סכום
    assert both["independent_sources"] == ["acoustic_anomaly"]


def test_gunshot_plus_keyword_is_two_sources_but_still_needs_more_for_high():
    res = _assess(_report([_event("gunshot", "high")]), keywords=["help"])
    assert set(res["independent_sources"]) == {"acoustic_anomaly", "speech_keyword"}
    assert res["level"] in ("MEDIUM", "HIGH")
    assert res["level"] != "HIGH" or res["score"] >= config.RISK_THRESHOLD_HIGH


def test_siren_and_fireworks_add_no_points_but_explain():
    base = _assess(_report([]))
    res = _assess(_report([_event("siren", "info"), _event("fireworks", "low")]))
    assert res["score"] == base["score"]
    assert any("סירנה" in r for r in res["reasons"]) and any("זיקוקים" in r for r in res["reasons"])


def test_unvalidated_suspicion_is_reported_but_not_scored():
    base = _assess(_report([]))
    res = _assess(_report([], suspected=[{"category": "braking", "label_he": "בלימה", "score": 0.7, "note": "n"}]))
    assert res["score"] == base["score"] and any("חשד לא מאומת" in r for r in res["reasons"])


def test_feedback_learning_gets_acoustic_event_as_microphone_source():
    r = {"audio_intel": {"events": [_event("glass_break", "medium")]}, "noise_level": 0.5,
         "independent_sources": ["acoustic_anomaly"]}
    assert RF.fired_sources(r) == ["acoustic_event"]           # לא גם loud_noise - מיקרופון אחד
    r_info = {"audio_intel": {"events": [_event("siren", "info")]}, "noise_level": 0.01}
    assert RF.fired_sources(r_info) == []
    assert "acoustic_event" in RF.PRIOR_PRECISION


def test_engine_unavailable_returns_none_and_assessment_continues(monkeypatch):
    monkeypatch.setattr(AI, "_model_path", lambda: None)
    AI._reset_for_tests()
    try:
        assert AI.analyze(np.ones(16000, np.float32) * 0.1, 16000) is None
        assert AI.status()["available"] is False
    finally:
        AI._reset_for_tests()


# ---------------------------------------------------------------- כרטיס המודל וקריטריוני קבלה
needs_model = pytest.mark.skipif(not os.path.exists(MODEL), reason="models/hazard_audio.npz לא קיים")


@needs_model
def test_model_card_is_measured_and_meets_acceptance_criteria():
    card = json.loads(str(np.load(MODEL, allow_pickle=False)["card"][0]))
    assert card["meta"]["uploader_overlap"] == 0                       # dev/eval בלתי תלויים
    assert card["meta"]["dense_check_err"] < 1e-3                       # zero-shot משחזר את YAMNet
    assert set(card["categories"]) >= {"gunshot", "explosion", "glass_break", "siren", "alarm", "scream"}
    for cat, c in card["categories"].items():
        e = c["metrics"][c["champion"]]["eval"]
        assert c["champion"] in ("zero_shot", "head")
        assert np.isfinite(c["threshold"])
        # קריטריוני קבלה v1 (research/hazard_audio_v1.md): הפרדה טובה מאקראי, והתראות שווא על eval
        # בסדר גודל של התקציב שנקבע על dev (לא פי 3 ומעלה)
        assert e["auc"] >= 0.80, (cat, e["auc"])
        assert e["fa_per_hour"] <= 3 * card["budget_fa_per_hour_dev"], (cat, e["fa_per_hour"])
    for u in card["unvalidated"].values():
        assert "לא נכנס לניקוד" in u["note"]


@needs_model
def test_runtime_report_structure_on_real_audio():
    rng = np.random.default_rng(1)
    x = (rng.standard_normal(16000 * 2) * 0.01).astype(np.float32)     # רעש רקע חלש
    rep = AI.analyze(x, 16000)
    if rep is None:
        pytest.skip("YAMNet לא זמין")
    assert rep["quality"]["status"] == "ok" and rep["danger_probability"] is None
    assert len(rep["top"]) == 3 and all(0 <= t["detection_confidence"] <= 1 for t in rep["top"])
    assert all(e["detected"] for e in rep["events"])
    compact = AI.compact(rep)
    assert "emb" not in json.dumps(compact) and set(compact["quality"]) >= {"status", "rms"}


@needs_model
@pytest.mark.skipif(not os.path.exists(os.path.join(FSD, "eval_features.npz")), reason="FSD50K לא קיים")
def test_runtime_path_matches_offline_measurement():
    """אותו קטע: הציון מהמטמון (שעליו נמדד המודל) == הציון בזמן ריצה מ-wav. אחרת המדידה לא מייצגת."""
    import csv
    from research_tools import acoustic_eval as E
    from research_tools import yamnet_detector as Y
    from research_tools.split_zip import SplitZip
    card = json.loads(str(np.load(MODEL, allow_pickle=False)["card"][0]))
    feats = np.load(os.path.join(FSD, "eval_features.npz"), allow_pickle=False)
    fnames = [str(f) for f in feats["fnames"]]
    gun = [r["fname"] for r in csv.DictReader(open(os.path.join(FSD, "meta", "FSD50K.ground_truth", "eval.csv"),
                                                   encoding="utf-8")) if "Gunshot_and_gunfire" in r["labels"]][:3]
    assert AI.load()
    c = card["categories"]["gunshot"]
    with SplitZip(os.path.join(FSD, "raw", "FSD50K.eval_audio.zip")) as z:
        names = {os.path.basename(n)[:-4]: n for n in z.names() if n.endswith(".wav")}
        for f in gun:
            x, sr = E._read_wav_bytes(z.read(names[f]), resample_to=None)
            scores, emb_rt = Y.Yamnet().analyze(Y.to_16k(x, sr), Y.TARGET_SR)
            i = fnames.index(f)
            emb_off = feats["emb"][feats["offsets"][i]:feats["offsets"][i + 1]].astype(np.float32)
            rt = AI._category_frames("gunshot", c, scores, emb_rt).max()
            scores_off = None
            if c["champion"] == "zero_shot":
                from research_tools import hazard_audio_train as T
                W, b = T.yamnet_dense()
                scores_off = Y._sigmoid(emb_off @ W.T + b)
            off = AI._category_frames("gunshot", c, scores_off, emb_off).max()
            assert abs(float(rt) - float(off)) < 0.05 * max(1.0, abs(float(off))), (f, rt, off)


# ---------------------------------------------------------------- שער champion/challenger
def test_gate_blocks_regressions(tmp_path):
    from research_tools import hazard_audio_train as T

    def card(recall, fa):
        m = {"eval": {"recall": recall, "fa_per_hour": fa}}
        return {"categories": {"gunshot": {"champion": "head", "metrics": {"head": m}}}}

    def write(name, c):
        p = tmp_path / name
        np.savez(p, card=np.array([json.dumps(c)]))
        return str(p)
    old = write("old.npz", card(0.50, 0.8))
    assert T.gate(old, write("same.npz", card(0.50, 0.8)))[0]
    assert not T.gate(old, write("worse_recall.npz", card(0.40, 0.8)))[0]
    assert not T.gate(old, write("more_fa.npz", card(0.55, 2.0)))[0]
    ok, probs = T.gate(old, write("missing.npz", {"categories": {}}))
    assert not ok and "נעלמה" in probs[0]


# ---------------------------------------------------------------- צליל חריג (OOD) - מידע בלבד
def _anomaly_model(tmp_path, method="mahalanobis", thr=4.0):
    rng = np.random.default_rng(0)
    comp = np.zeros((1024, 2), np.float32)
    comp[0, 0] = comp[1, 1] = 1.0
    card = {"version": "audio_anomaly_v1", "method": method, "hp": {"dims": 2, "k": 2}, "threshold": thr}
    p = tmp_path / "audio_anomaly.npz"
    np.savez(p, card=np.array([json.dumps(card)]), mean=np.zeros(1024, np.float32), comp=comp,
             scale=np.ones(2, np.float32), bank=rng.standard_normal((50, 2)).astype(np.float16))
    return str(p)


@pytest.mark.parametrize("method", ["mahalanobis", "knn"])
def test_anomaly_flags_far_frames_and_is_none_without_model(tmp_path, monkeypatch, method):
    AI._reset_for_tests()
    monkeypatch.setattr(AI, "_anomaly_path", lambda: None)
    assert AI.anomaly(np.zeros((3, 1024), np.float32)) is None
    AI._reset_for_tests()
    monkeypatch.setattr(AI, "_anomaly_path", lambda: _anomaly_model(tmp_path, method))
    try:
        near = AI.anomaly(np.zeros((3, 1024), np.float32))
        far = np.zeros((3, 1024), np.float32)
        far[1, 0] = 10.0
        out = AI.anomaly(far)
        assert near["unusual"] is False and out["unusual"] is True and out["score"] > near["score"]
        assert "לא נכנס לניקוד" in out["note"]
    finally:
        AI._reset_for_tests()


def test_unusual_sound_is_explained_but_adds_no_points():
    base = _assess(_report([]))
    rep = _report([])
    rep["anomaly"] = {"model": "audio_anomaly_v1", "unusual": True, "score": 9.0, "threshold": 4.0, "note": "n"}
    res = _assess(rep)
    assert res["score"] == base["score"] and res["level"] == base["level"]
    assert any("צליל חריג" in r for r in res["reasons"])
    assert res["audio_intel"]["anomaly"]["unusual"] is True
    assert AI.hazard_points(rep) == 0


def test_anomaly_research_window_rate_and_recorded_decision():
    from research_tools import audio_anomaly as A
    flags = np.array([0, 0, 1, 0, 0, 0], bool)                         # קטע אחד, 6 מסגרות
    off = np.array([0, 6])
    # חלונות של 2: (0,1)(1,2)(2,3)(3,4)(4,5) -> 2 מתוך 5 נוגעים במסגרת המסומנת
    assert A.window_hit_rate(flags, off, np.array([True]), 2) == pytest.approx(0.4)
    assert A.NOVEL.isdisjoint(A.KNOWN) and A.AMBIGUOUS.isdisjoint(A.KNOWN | A.NOVEL)
    p = os.path.join(ROOT, "research", "audio_anomaly_v1.json")
    if not os.path.exists(p):
        pytest.skip("הניסוי לא הורץ")
    out = json.load(open(p, encoding="utf-8"))
    assert out["uploader_overlap"] == 0
    # ההחלטה עקבית: מודל זמן-ריצה קיים אם ורק אם עמד בכל הקריטריונים
    assert out["accepted"] == all(c["pass"] for c in out["checks"].values())
    assert os.path.exists(os.path.join(ROOT, "models", "audio_anomaly.npz")) == out["accepted"]

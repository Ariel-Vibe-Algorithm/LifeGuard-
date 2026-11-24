# -*- coding: utf-8 -*-
"""YAMNet אמיתי (TFLite): מסגור, עיבוד בחלקים זהה לעיבוד מלא, גלאים, מסווג, מטמון, והערכה מהירה."""
import os

import numpy as np
import pytest

from research_tools import acoustic_eval as E

Y = pytest.importorskip("research_tools.yamnet_detector")
if not os.path.exists(Y.EMB_MODEL):
    pytest.skip("מודל YAMNet לא הורד (research/models/yamnet)", allow_module_level=True)

SR = 16000
TESTCLIPS = os.path.join(os.path.dirname(Y.MODEL_DIR), "testclips")


@pytest.fixture(scope="module")
def model():
    return Y.Yamnet()


def noise(seconds, seed=0, amp=0.1):
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * amp).astype(np.float32)


def test_frame_count_matches_model(model):
    for n in (100, 15360, 23040, 23041, 80000, 160000):
        s, e = model.analyze(noise(n / SR), SR)
        assert len(s) == len(e) == Y.n_frames(n), n
        assert s.shape[1] == 521 and e.shape[1] == 1024


def test_chunked_analysis_equals_single_pass(model):
    x = noise(40, seed=3)
    _, full = model.analyze(x, SR)
    small = Y.Yamnet(chunk_frames=7)               # הרבה גבולות חלקים
    _, chunked = small.analyze(x, SR)
    assert chunked.shape == full.shape
    assert np.abs(chunked - full).max() < 1e-4


def test_resampling_to_16k_keeps_duration():
    x = noise(2.0)[: 2 * SR]
    y = Y.to_16k(np.interp(np.linspace(0, len(x) - 1, 2 * 44100), np.arange(len(x)), x), 44100)
    assert abs(len(y) - 2 * SR) <= 1


def test_zero_shot_detector_on_real_clip(model):
    from research_tools.acoustic_eval import _read_wav_bytes
    with open(os.path.join(TESTCLIPS, "1-187207-A-20.wav"), "rb") as fh:   # ESC-50 crying_baby
        x, sr = _read_wav_bytes(fh.read())
    det = Y.YamnetClassDetector(model=model)
    t, s = det.frame_scores(x, sr)
    assert len(t) == len(s) == Y.n_frames(len(x)) and np.all((s >= 0) & (s <= 1))
    # על רעש לבן אין סיבה לצרחה/בכי - הציון נמוך משמעותית מאשר על בכי אמיתי
    _, s_noise = det.frame_scores(noise(5, amp=0.05), SR)
    assert s.max() > s_noise.max()
    with pytest.raises(ValueError):
        Y.YamnetClassDetector(model=model, classes=("NotAClass",))


def test_logistic_model_learns_and_roundtrips(tmp_path):
    rng = np.random.default_rng(0)
    X = np.concatenate([rng.normal(0, 1, (400, 16)), rng.normal(0, 1, (80, 16)) + 1.5]).astype(np.float32)
    y = np.r_[np.zeros(400), np.ones(80)]
    m = Y.LogisticModel(epochs=200).fit(X, y)
    p = m.predict_proba(X)
    assert p[y == 1].mean() > 0.8 and p[y == 0].mean() < 0.2      # איזון מחלקות: לא "תמיד לא"
    path = str(tmp_path / "m.npz")
    m.save(path)
    m2 = Y.LogisticModel.load(path)
    assert np.allclose(m2.predict_proba(X), p) and m2.info["n_pos"] == 80
    with pytest.raises(ValueError):
        Y.LogisticModel().fit(X, np.zeros(len(X)))


def test_event_frames_marks_loud_part_only():
    x = np.concatenate([noise(3, amp=0.002), noise(1, amp=0.5), noise(3, amp=0.002)])
    ev = Y.event_frames(x, Y.n_frames(len(x)))
    on = np.flatnonzero(ev) * Y.HOP_SECONDS
    assert ev.any() and on.min() > 1.5 and on.max() < 4.5


def test_feature_cache_reuses_results(tmp_path, model):
    cache = Y.FeatureCache(str(tmp_path), model=model)
    x = noise(3, seed=5)
    a = cache.get(x, SR)
    files = [f for _, _, fs in os.walk(str(tmp_path)) for f in fs]
    assert len(files) == 1
    b = cache.get(x, SR)
    assert np.array_equal(a["zero_shot"], b["zero_shot"]) and a["emb"].dtype == np.float16
    assert len(a["event"]) == len(a["emb"]) == Y.n_frames(len(x))


def test_fast_event_count_matches_reference():
    rng = np.random.default_rng(1)
    for _ in range(200):
        s = rng.random(rng.integers(1, 80))
        t, gap = rng.random(), int(rng.integers(0, 5))
        ref = len(E.events(s, t, hop_seconds=1.0, merge_gap_seconds=gap))
        assert E._count_events(s, t, gap) == ref


def test_precomputed_scores_and_breakdown_without_audio():
    clips = [E.Clip("p1", None, SR, True, "Screaming", {"seconds": 2}),
             E.Clip("p2", None, SR, True, "Crying_and_sobbing", {"seconds": 2}),
             E.Clip("n1", None, SR, False, "Laughter", {"seconds": 1800}),
             E.Clip("n2", None, SR, False, "Traffic", {"seconds": 1800})]
    scores = {"p1": np.array([0.1, 0.9, 0.2]), "p2": np.array([0.3, 0.4]),
              "n1": np.array([0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.5]), "n2": np.zeros(10)}
    res = E.evaluate(E.PrecomputedDetector("pre", scores, 1.0), clips, budgets=(2.0, 0.1), merge_gap_seconds=0)
    assert res["negative_hours"] == pytest.approx(1.0)
    b = res["recall_at_fa_budget"]["2.0"]                 # 2 התראות (שתיהן מצחוק) בשעה אחת
    assert b["recall"] == 1.0 and b["false_alarms"] == 2
    assert b["fa_by_label"] == {"Laughter": {"false_alarms": 2, "hours": 0.5}}
    assert b["recall_by_label"]["Crying_and_sobbing"] == {"detected": 1, "clips": 1}
    strict = res["recall_at_fa_budget"]["0.1"]            # אפס התראות -> רק הצרחה (0.9) מעל 0.5
    assert strict["recall"] == 0.5 and strict["recall_by_label"]["Screaming"]["detected"] == 1
    assert "Laughter" not in strict["fa_by_label"]
    assert "תקציב 2.0" in E.report_markdown(res)


def test_threshold_calibrated_on_one_set_applied_honestly_to_another():
    mk = lambda cid, pos, lab, sec: E.Clip(cid, None, SR, pos, lab, {"seconds": sec})  # noqa: E731
    val = [mk("vp", True, "Screaming", 2), mk("vn", False, "Laughter", 3600)]
    val_scores = {"vp": np.array([0.2, 0.8]), "vn": np.r_[np.zeros(50), 0.7, np.zeros(50)]}
    thr = E.threshold_for_budget(E.PrecomputedDetector("d", val_scores, 1.0), val, budget=0.5)
    assert 0.7 < thr <= 0.8                                  # מעל ההתראה היחידה בסט הכיול
    test = [mk("tp", True, "Screaming", 2), mk("tq", True, "Crying_and_sobbing", 2), mk("tn", False, "Cheering", 1800)]
    test_scores = {"tp": np.array([0.9]), "tq": np.array([0.75]), "tn": np.r_[0.95, np.zeros(9)]}
    r = E.evaluate_at(E.PrecomputedDetector("d", test_scores, 1.0), test, thr)
    assert r["recall"] == 0.5 and r["false_alarms"] == 1 and r["fa_per_hour"] == pytest.approx(2.0)
    assert r["fa_by_label"] == {"Cheering": {"false_alarms": 1, "hours": 0.5}}
    assert r["recall_ci95"][0] < 0.5 < r["recall_ci95"][1]


def test_mlp_logits_do_not_saturate_and_roundtrip(tmp_path):
    rng = np.random.default_rng(0)
    X = np.concatenate([rng.normal(0, 1, (300, 32)), rng.normal(0, 1, (60, 32)) + 3]).astype(np.float32)
    y = np.r_[np.zeros(300), np.ones(60)]
    m = Y.MLPModel(hidden=16, epochs=60, batch=32).fit(X, y)
    z = m.decision_function(X)
    assert len(np.unique(np.round(z[y == 1], 6))) > 50        # דירוג נשמר גם כשההסתברות ~1.0
    assert m.predict_proba(X)[y == 1].mean() > 0.9 and m.predict_proba(X)[y == 0].mean() < 0.1
    p = str(tmp_path / "mlp.npz")
    m.save(p)
    assert np.allclose(Y.MLPModel.load(p).decision_function(X), z, atol=1e-5)
    s = Y.smooth_scores(np.array([0, 10, 0, 10, 10, 0.0]), 2)
    assert s.argmax() == 3 and s[1] == 5                       # שיא בודד מוחלש, רצף נשמר
    assert Y.context_features(np.ones((4, 8))).shape == (4, 24)

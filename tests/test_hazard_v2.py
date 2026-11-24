# -*- coding: utf-8 -*-
"""
מנוע סכנה v2 (hazard_features.py, research_tools/hv2_*.py, audio_intel v2):
- מניפסט: אין דליפת סשן בין פיצולים, אין נתונים בלי זכות מסחרית באימון, Nonspeech7k/TAU/ESC למבחן בלבד.
- מאפיינים: אותו קוד באימון ובזמן ריצה; ממדים קבועים; חלונות של 1.5 ש'.
- תיוג: ירי לפי חותמת זמן; תיוג חלש -> שני החלונות המובילים (MIL); קטגוריות עמומות מושמטות.
- בחירת סף: אף פעם לא יותר חלונות שגויים מ-v1 על אותם חלונות validation.
- זמן ריצה: v2 רק לקטגוריות שבקובץ; בלי קובץ - v1 בדיוק כמו קודם; צליל לבדו עדיין לא SOS אוטומטי.
"""
import json
import os

import numpy as np
import pytest

import audio_intel as AI
import hazard_features as HF
from research_tools import hv2_data as D
from research_tools import hv2_train as TR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _row(id_, source, split, tags, session, commercial=True, **kw):
    return dict({"id": id_, "source": source, "split": split, "tags": tags, "session": session,
                 "commercial_ok": commercial, "ref": {"zip_split": "dev"}}, **kw)


def test_manifest_validation_catches_leaks_and_nc_training():
    ok = [_row("a", "gunshot_fit", "train", ["gunshot"], "recording:1"),
          _row("b", "gunshot_fit", "test", ["gunshot"], "recording:2")]
    assert D.validate(ok) == []
    leak = ok + [_row("c", "gunshot_fit", "test", ["gunshot"], "recording:1")]
    assert any("דליפת סשן" in p for p in D.validate(leak))
    nc = ok + [_row("d", "tau2020_mobile", "train", [], "tau:x", commercial=False)]
    assert any("בלי זכות מסחרית" in p for p in D.validate(nc))
    assert not D.trainable(_row("e", "fsd50k", "train", [], "u", commercial=False))
    assert D.trainable(_row("f", "fsd50k", "val", [], "u"))


def test_license_kind_parsing():
    assert D._license_kind("http://creativecommons.org/licenses/by-nc/3.0/") == "by-nc"
    assert D._license_kind("http://creativecommons.org/publicdomain/zero/1.0/") == "zero"
    assert D._license_kind("http://creativecommons.org/licenses/by/3.0/") == "by"
    assert D._license_kind("http://creativecommons.org/licenses/sampling+/1.0/") == "sampling+"


@pytest.mark.skipif(not os.path.exists(D.MANIFEST), reason="המניפסט לא נבנה")
def test_real_manifest_is_clean():
    rows = D.load_manifest()
    assert D.validate(rows) == []
    for r in rows:
        if r["source"] in ("tau2020_mobile", "esc50", "nonspeech7k"):
            assert r["split"] in ("test", "excluded_overlap") and not r["commercial_ok"]
        if D.trainable(r):
            assert r["license"] in ("zero", "by", "cc-by-4.0")


def test_features_shapes_and_windows():
    x = (np.random.default_rng(0).standard_normal(16000 * 4) * 0.05).astype(np.float32)
    ws = HF.windows(x)
    assert all(len(w) == HF.WINDOW for _, w in ws) and ws[-1][0] + HF.WINDOW == len(x)
    assert len(HF.windows(x[:8000])) == 1                                   # קצר -> מרופד לחלון אחד
    spec = HF.spectral_features(ws[0][1])
    assert spec.shape == (29,) and np.isfinite(spec).all()
    emb = np.random.default_rng(1).standard_normal((3, 1024)).astype(np.float32)
    assert HF.stage1_vector(emb).shape == (2048,)


def test_impulsive_vs_periodic_features():
    """ירי = אימפולס בודד עם דעיכה; תופים = אימפולסים מחזוריים. המאפיינים צריכים להבדיל."""
    rng = np.random.default_rng(0)
    t = np.arange(HF.WINDOW)
    shot = rng.standard_normal(HF.WINDOW).astype(np.float32) * 0.01
    shot[4000:4000 + 4000] += rng.standard_normal(4000).astype(np.float32) * np.exp(-np.arange(4000) / 400) * 0.9
    drums = rng.standard_normal(HF.WINDOW).astype(np.float32) * 0.01
    for k in range(0, HF.WINDOW - 2000, 4000):                              # 4 מכות בשנייה
        drums[k:k + 2000] += rng.standard_normal(2000).astype(np.float32) * np.exp(-np.arange(2000) / 300) * 0.6
    fs, fd = HF.spectral_features(shot), HF.spectral_features(drums)
    periodicity = 21 + 3
    assert fd[periodicity] > fs[periodicity]
    assert t is not None


def test_mil_and_timestamp_labelling():
    idx = {"g": {"id": "g", "source": "gunshot_fit", "tags": ["gunshot"], "gunshot_at_s": [1.0]},
           "f": {"id": "f", "source": "fsd50k", "tags": ["gunshot"]},
           "x": {"id": "x", "source": "fsd50k", "tags": ["explosion"]},
           "n": {"id": "n", "source": "fsd50k", "tags": ["music"]}}
    meta = [{"id": "g", "variant": "orig", "start": 0.0}, {"id": "g", "variant": "orig", "start": 2.0},
            {"id": "f", "variant": "orig", "start": 0.0}, {"id": "f", "variant": "orig", "start": 0.75},
            {"id": "f", "variant": "orig", "start": 1.5}, {"id": "x", "variant": "orig", "start": 0.0},
            {"id": "n", "variant": "orig", "start": 0.0}]
    zs = np.zeros((len(meta), len(D.TARGETS)), np.float32)
    ci = D.TARGETS.index("gunshot")
    zs[2:5, ci] = [0.9, 0.1, 0.8]
    y, hard, _ = TR.window_labels(meta, idx, "gunshot")
    y = TR.resolve_positives(y, meta, zs, idx, "gunshot", ci)
    assert list(y) == [1, -1, 1, -1, 1, -1, 0]       # חותמת זמן; MIL top-2; פיצוץ מושמט (עמום); מוזיקה שלילי


def test_operating_point_never_exceeds_v1_false_windows(monkeypatch):
    """בחירת הסף: מותר רק מה שלא מוסיף חלונות שגויים מעבר ל-v1 על אותם חלונות."""
    AI.load()
    cat = "gunshot"
    ci = D.TARGETS.index(cat)
    rng = np.random.default_rng(0)
    n = 400
    meta = [{"id": f"c{i}", "variant": "orig", "start": 0.0} for i in range(n)]
    idx = {f"c{i}": {"id": f"c{i}", "source": "fsd50k" if i % 2 else "sonyc_ust",
                     "tags": ["gunshot"] if i < 40 else [], "env": "street"} for i in range(n)}
    v1 = np.full((n, len(D.TARGETS)), -100.0, np.float32)
    v1[:10, ci] = 100.0                                                     # v1: 10 זיהויים, 0 שווא
    va = {"s1": rng.standard_normal((n, 2048)).astype(np.float16), "cur": np.zeros((n, 112), np.float16),
          "spec": np.zeros((n, 29), np.float32), "v1": v1, "zs": np.ones((n, len(D.TARGETS)), np.float32)}

    class FakeHead:
        def __init__(self, scores):
            self.s = scores

        def logit(self, X):
            return self.s
    s = np.concatenate([np.linspace(5, 1, 40), rng.uniform(-2, 3, n - 40)]).astype(np.float32)
    op = TR.select_operating_point(cat, {"h1": FakeHead(s), "h2": None}, va, meta, idx)
    for e, (k, nn) in op["false"].items():
        assert k <= op["v1_false"][e][0]
    assert op["hit"] >= op["v1_hit"] or op["hit"] == 0


# ---------------------------------------------------------------- זמן ריצה
def _fake_v2(tmp_path, cats=("gunshot",)):
    rng = np.random.default_rng(0)
    arrays, card = {}, {"version": "hazard_audio_v2_test", "categories": {}}
    for c in cats:
        for st, dim in (("s1", 2048), ("s2", 1 + 2 * len(HF.curated_idx()) + 29)):
            arrays[f"{c}__{st}__mean"] = np.zeros(dim, np.float32)
            arrays[f"{c}__{st}__std"] = np.ones(dim, np.float32)
            arrays[f"{c}__{st}__w"] = (rng.standard_normal(dim) * 0.001).astype(np.float32)
            arrays[f"{c}__{st}__b"] = np.array([5.0], np.float32)            # תמיד "מזהה"
        card["categories"][c] = {"t1": 0.0, "t2": 0.0, "two_stage": True, "platt": [1.0, 0.0]}
    arrays["card"] = np.array([json.dumps(card)])
    p = tmp_path / "hazard_audio_v2.npz"
    np.savez(p, **arrays)
    return str(p)


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "models", "hazard_audio.npz")), reason="אין מודל v1")
def test_runtime_uses_v2_only_for_listed_categories_and_v1_without_file(tmp_path, monkeypatch):
    x = (np.random.default_rng(2).standard_normal(24000) * 0.02).astype(np.float32)
    AI._reset_for_tests()
    monkeypatch.setattr(AI, "_v2_path", lambda: None)
    base = AI.analyze(x, 16000)
    if base is None:
        pytest.skip("YAMNet לא זמין")
    assert all(not r["model"].startswith("v2") for r in base["top"])
    AI._reset_for_tests()
    monkeypatch.setattr(AI, "_v2_path", lambda: _fake_v2(tmp_path))
    try:
        rep = AI.analyze(x, 16000)
        ev = {e["category"]: e for e in rep["events"]}
        assert ev["gunshot"]["model"] == "v2_two_stage" and ev["gunshot"]["detected"]
        assert "hazard_audio_v2_test" in rep["model_version"]
        assert all(r["model"] in ("head", "zero_shot") for r in rep["top"] if r["category"] != "gunshot")
        assert AI.hazard_points(rep) == 3                                   # ירי = 3 נקודות; לבד ≤ MEDIUM
    finally:
        AI._reset_for_tests()

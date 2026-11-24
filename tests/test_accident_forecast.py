# -*- coding: utf-8 -*-
"""
בדיקות לניסוי חיזוי התאונות (research_tools/accident_forecast.py) - על נתונים סינתטיים קטנים.
בודק: אין דליפה משנת המבחן, חשבון hit rate/PAI על דוגמה שחושבה ביד, bootstrap, התמדה > אחיד על שדה
מתמיד, שחזור כלל המוקד של השרת, ומבנה ה-JSON של הריצה האמיתית (אם הנתונים קיימים).
"""
import json
import os

import numpy as np
import pytest

from research_tools import accident_forecast as AF

SMALL_BBOX = (180_000, 600_000, 200_000, 620_000)   # 20x20 ק"מ - רשתות קטנות ומהירות


@pytest.fixture
def small_bbox(monkeypatch):
    monkeypatch.setattr(AF, "BBOX", SMALL_BBOX)
    monkeypatch.setattr(AF, "N_BOOT", 300)


def synthetic(seed=0, years=range(2020, 2025), n_hot=25, rate=3.0, background=40, persistent=True):
    """שדה סינתטי: מוקדים קבועים (או משתנים כל שנה אם persistent=False) + רעש רקע אחיד."""
    rng = np.random.default_rng(seed)
    x0, y0, x1, y1 = SMALL_BBOX
    hot = rng.uniform([x0 + 2000, y0 + 2000], [x1 - 2000, y1 - 2000], size=(n_hot, 2))
    cols = {k: [] for k in ("year", "x", "y")}
    for yr in years:
        h = hot if persistent else rng.uniform([x0 + 2000, y0 + 2000], [x1 - 2000, y1 - 2000], size=(n_hot, 2))
        for cx, cy in h:
            k = rng.poisson(rate)
            cols["x"] += list(cx + rng.normal(0, 30, k))
            cols["y"] += list(cy + rng.normal(0, 30, k))
            cols["year"] += [yr] * k
        cols["x"] += list(rng.uniform(x0 + 100, x1 - 100, background))
        cols["y"] += list(rng.uniform(y0 + 100, y1 - 100, background))
        cols["year"] += [yr] * background
    acc = {"year": np.array(cols["year"], np.int64), "x": np.array(cols["x"]), "y": np.array(cols["y"])}
    n = len(acc["year"])
    acc["lat"] = 31.5 + (acc["y"] - 600_000) / 110_900.0
    acc["lon"] = 35.0 + (acc["x"] - 190_000) / 94_800.0
    acc["status"] = np.ones(n, np.int64)
    acc["severe"] = rng.random(n) < 0.2
    acc["night"] = rng.random(n) < 0.3
    return acc


# ---------------------------------------------------------------- מתמטיקה של המדדים

def test_hit_rate_and_pai_hand_computed():
    score = np.array([3, 2, 2, 1, 0])
    y = np.array([2, 1, 0, 1, 0])
    # 5 תאונות מבחן, אחת מהן מחוץ לאזור. 40% מהשטח = 2 תאים: התא עם 3 (משקל 1) + חצי מקבוצת השוויון של 2.
    w = AF.capture_weights(score, 0.4)
    assert np.allclose(w, [1, 0.5, 0.5, 0, 0])
    hr = AF.hit_rate(score, y, 5, 0.4)
    assert hr == pytest.approx((2 + 0.5 * 1 + 0.5 * 0) / 5)        # 0.5
    assert AF.pai(hr, 0.4) == pytest.approx(1.25)
    # 20% = תא אחד בדיוק
    assert AF.hit_rate(score, y, 5, 0.2) == pytest.approx(2 / 5)
    assert AF.pai(AF.hit_rate(score, y, 5, 0.2), 0.2) == pytest.approx(2.0)


def test_uniform_hit_rate_equals_coverage():
    y = np.array([0, 3, 1, 0, 0, 2, 0, 0, 0, 4])
    for frac in (0.01, 0.05, 0.1, 0.37):
        assert AF.hit_rate(np.zeros(10), y, y.sum(), frac) == pytest.approx(frac)
        # 2 תאונות מחוץ לאזור -> PAI של אחיד = שיעור התאונות בתוך האזור
        assert AF.pai(AF.hit_rate(np.zeros(10), y, y.sum() + 2, frac), frac) == pytest.approx(10 / 12)


def test_capture_weights_budget_is_exact():
    rng = np.random.default_rng(3)
    s = rng.integers(0, 4, 1000).astype(float)
    for frac in (0.01, 0.05, 0.1, 0.5):
        assert AF.capture_weights(s, frac).sum() == pytest.approx(frac * 1000)


def test_poisson_ll_and_calibration():
    lam = np.array([0.5, 1.0, 2.0])
    y = np.array([0, 1, 3])
    exp = [-0.5, -1.0, 3 * np.log(2) - 2 - np.log(6)]
    assert np.allclose(AF.poisson_ll_cells(lam, y), exp)
    assert np.allclose(AF.poisson_dev_cells(lam, lam), 0)
    rows = AF.calibration(np.linspace(0, 1, 100), np.ones(100))
    deciles = [r for r in rows if r["bin"].startswith("D")]
    assert len(deciles) == 10
    assert sum(r["pred_sum"] for r in deciles) == pytest.approx(50.0)
    assert sum(r["obs_sum"] for r in deciles) == pytest.approx(100.0)
    # שוויונות: כל התאים עם אותו קצב חזוי נכנסים לאותו סל (לא נחתכים לפי סדר גיאוגרפי)
    lam = np.r_[np.zeros(90), np.ones(10)]
    rows = [r for r in AF.calibration(lam, np.r_[np.zeros(90), np.ones(10)]) if r["bin"].startswith("D")]
    assert [r["cells"] for r in rows] == [90, 10]
    assert rows[0]["bin"] == "D1-D5" and rows[1]["bin"] == "D6-D10"
    assert rows[1]["obs_sum"] == 10


def test_bootstrap_ci_contains_point_estimate():
    rng = np.random.default_rng(1)
    n = 400
    blocks = rng.integers(0, 60, n)
    W = (rng.random((n, 3)) < [0.1, 0.3, 0.6]).astype(float)
    rblocks = rng.integers(0, 80, 2000)
    boot = AF.Boot(blocks, rblocks, n_boot=500, seed=7)
    est, lo, hi = boot.ratio_acc(W)
    assert np.allclose(est, W.mean(0))
    assert (lo <= est).all() and (est <= hi).all() and (lo < hi).all()
    V = rng.normal(size=(2000, 2))
    e2, lo2, hi2 = boot.mean_region(V)
    assert (lo2 <= e2).all() and (e2 <= hi2).all()
    # אותה seed -> אותו רווח סמך (דטרמיניסטי)
    boot2 = AF.Boot(blocks, rblocks, n_boot=500, seed=7)
    assert np.allclose(boot2.ratio_acc(W)[1], lo) and np.allclose(boot2.ratio_acc(W)[2], hi)


def test_gaussian_smooth_preserves_mass():
    D = np.zeros((60, 60))
    D[30, 30] = 5
    D[10, 40] = 2
    G = AF.gaussian_smooth(D, 2.5)
    assert G.sum() == pytest.approx(7, rel=1e-5)
    assert G[30, 30] == G.max()


# ---------------------------------------------------------------- אין דליפה

def test_build_layers_rejects_years_outside_training(small_bbox):
    acc = synthetic()
    g = AF.Grid(500)
    hist = AF.take(acc, acc["year"] <= 2023)
    region = AF.region_from(g, hist)
    with pytest.raises(AssertionError):
        AF.build_layers(g, hist, [2020, 2021, 2022], region)


def test_tune_rejects_data_after_validation_year(small_bbox):
    acc = synthetic()
    with pytest.raises(AssertionError):
        AF.tune(acc, 2023, cells=(500,))


def test_no_leakage_test_year_never_used_for_fitting_or_tuning(small_bbox, monkeypatch):
    acc = synthetic(seed=5)
    seen_layers, seen_tune = [], []
    real_bl, real_tune = AF.build_layers, AF.tune

    def spy_bl(grid, hist, train_years, region, sigmas=()):
        seen_layers.append((int(hist["year"].max()), max(train_years)))
        return real_bl(grid, hist, train_years, region, sigmas)

    def spy_tune(acc_pre, val_year, *a, **k):
        seen_tune.append((int(acc_pre["year"].max()), val_year))
        return real_tune(acc_pre, val_year, *a, **k)

    monkeypatch.setattr(AF, "build_layers", spy_bl)
    monkeypatch.setattr(AF, "tune", spy_tune)
    res = AF.run_pipeline(acc, 2024, cells=(250, 500), full=True)
    assert seen_tune == [(2023, 2023)]
    assert seen_layers, "build_layers לא נקראה"
    for hist_max, train_max in seen_layers:
        assert hist_max < 2024 and train_max < 2024
    # בשלב הבחירה ההתאמה רואה רק עד 2022
    assert any(t == 2022 for _, t in seen_layers)
    assert res["selection"]["train_years"] == [2020, 2021, 2022]
    assert res["train_years"] == [2020, 2021, 2022, 2023]


def test_selection_does_not_depend_on_test_year_data(small_bbox):
    a = synthetic(seed=11)
    b = {k: v.copy() for k, v in a.items()}
    m = b["year"] == 2024
    rng = np.random.default_rng(99)                       # מחליפים לגמרי את שנת המבחן
    b["x"][m] = rng.uniform(181_000, 199_000, m.sum())
    b["y"][m] = rng.uniform(601_000, 619_000, m.sum())
    ra = AF.run_pipeline(a, 2024, cells=(250, 500), full=False)
    rb = AF.run_pipeline(b, 2024, cells=(250, 500), full=False)
    assert ra["selection"]["chosen"] == rb["selection"]["chosen"]
    assert ra["selection"]["primary_cell_m"] == rb["selection"]["primary_cell_m"]
    pc = ra["selection"]["primary_cell_m"]
    # ...אבל תוצאות המבחן כן משתנות (המבחן באמת משתמש בשנת המבחן)
    assert ra["by_cell"][pc]["models"]["persistence"]["hit_rate"]["0.05"]["est"] != \
        rb["by_cell"][pc]["models"]["persistence"]["hit_rate"]["0.05"]["est"]


# ---------------------------------------------------------------- התנהגות המודלים

def test_persistence_beats_uniform_on_persistent_field(small_bbox):
    acc = synthetic(seed=2, rate=4.0, background=30)
    res = AF.run_pipeline(acc, 2024, cells=(250, 500), full=True)
    pc = res["selection"]["primary_cell_m"]
    mods = res["by_cell"][pc]["models"]
    for c in ("0.01", "0.05", "0.1"):
        p, u = mods["persistence"]["hit_rate"][c], mods["uniform"]["hit_rate"][c]
        assert p["lo"] > u["est"] * 2, (c, p, u)
    assert mods["persistence"]["pai"]["0.05"]["est"] > 3
    assert mods["uniform"]["pai"]["0.05"]["est"] == pytest.approx(
        1 - res["by_cell"][pc]["share_test_outside_region"], abs=1e-9)
    # תחזית ספירות: התמדה עם prior טובה מאחיד
    assert mods["persistence"]["loglik_per_cell"]["est"] > mods["uniform"]["loglik_per_cell"]["est"]
    assert "calibration" in res["by_cell"][pc]
    assert 0 < res["app_rule"]["frac_of_region"] < 1


def test_persistence_does_not_help_on_random_field(small_bbox):
    acc = synthetic(seed=4, persistent=False, n_hot=0, background=200)
    res = AF.run_pipeline(acc, 2024, cells=(500,), full=False)
    mods = res["by_cell"][500]["models"]
    d = [c for c in res["comparisons_vs_persistence"] if c["a"] == "uniform" and abs(c["coverage"] - 0.05) < 1e-9][0]
    assert d["diff_hr"]["lo"] <= 0 <= d["diff_hr"]["hi"] or abs(d["diff_hr"]["est"]) < 0.03
    assert mods["persistence"]["pai"]["0.05"]["est"] < 2


def test_glm_fits_only_with_two_years(small_bbox):
    acc = synthetic(seed=6)
    g = AF.Grid(500)
    hist = AF.take(acc, acc["year"] == 2020)
    region = AF.region_from(g, hist)
    L = AF.build_layers(g, hist, [2020], region)
    assert AF.fit_predict({"family": "glm"}, L, region) is None
    hist = AF.take(acc, acc["year"] <= 2022)
    region = AF.region_from(g, hist)
    L = AF.build_layers(g, hist, [2020, 2021, 2022], region)
    fp = AF.fit_predict({"family": "glm"}, L, region)
    assert fp is not None and np.isfinite(fp["score"]).all() and fp["beta"][1] > 0


# ---------------------------------------------------------------- שחזור כלל השרת

def test_app_hotspot_matches_legacy_server_rule():
    """הכלל הקודם של השרת (לפני v2) - הגדרה ישירה, כדי שההשוואה ב-hotspot_v2.md תהיה מול מה שרץ בפועל."""
    rng = np.random.default_rng(8)
    lat = np.concatenate([32.08 + rng.normal(0, 0.0004, 30), 32.10 + rng.normal(0, 0.0003, 12),
                          rng.uniform(32.0, 32.2, 200)])
    lon = np.concatenate([34.78 + rng.normal(0, 0.0004, 30), 34.80 + rng.normal(0, 0.0003, 12),
                          rng.uniform(34.7, 34.9, 200)])
    cy, cx = AF._app_keys(lat, lon)
    acc = {}
    for a, b in zip(cy.tolist(), cx.tolist()):
        acc[(a, b)] = acc.get((a, b), 0) + 1

    def near(y, x):                                   # legacy Layers.accidents_near (3x3)
        return sum(acc.get((y + dy, x + dx), 0) for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    keys = {(y + dy, x + dx) for (y, x) in acc for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
    vals = sorted(near(y, x) for y, x in keys)
    thr = max(3, vals[int(len(vals) * 90 / 100)])     # legacy Layers._hotspot_threshold
    fy, fx, thr_af = AF.app_hotspot(lat, lon)
    assert thr_af == thr
    assert {k for k in keys if near(*k) >= thr} == set(zip(fy.tolist(), fx.tolist()))
    assert len(fy) > 0


# ---------------------------------------------------------------- הריצה האמיתית

@pytest.mark.skipif(not os.path.exists(AF.CSV_PATH), reason="קובץ התאונות לא קיים במכונה הזו")
def test_real_run_json_structure():
    if not os.path.exists(AF.OUT_JSON):
        pytest.skip("הריצה האמיתית עוד לא בוצעה: python -I research_tools/accident_forecast.py run")
    with open(AF.OUT_JSON, encoding="utf-8") as fh:
        o = json.load(fh)
    with open(AF.CSV_PATH, encoding="utf-8-sig") as fh:
        n_rows = sum(1 for _ in fh) - 1
    assert o["version"] == AF.VERSION
    assert o["data"]["rows"] == n_rows
    for k in ("config", "data", "main", "rolling", "sensitivity_all_status", "covariates"):
        assert k in o
    M = o["main"]
    assert M["test_year"] == AF.FINAL_TEST_YEAR
    assert max(M["train_years"]) < M["test_year"]
    assert M["selection"]["val_year"] == M["test_year"] - 1
    assert max(M["selection"]["train_years"]) < M["selection"]["val_year"]
    assert set(M["by_cell"]) == {str(c) for c in AF.CELL_SIZES_M}
    pc = str(M["selection"]["primary_cell_m"])
    mods = M["by_cell"][pc]["models"]
    for f in ("uniform", "persistence", "recency", "kde", "glm", "neighbor3x3"):
        assert f in mods
        for c in ("0.01", "0.05", "0.1"):
            h = mods[f]["hit_rate"][c]
            assert 0 <= h["lo"] <= h["est"] <= h["hi"] <= 1
            assert mods[f]["pai"][c]["est"] == pytest.approx(h["est"] / float(c), rel=1e-4)
        assert "loglik_per_cell" in mods[f] and "hit_rate_at_app_area" in mods[f]
    app = M["app_rule"]
    assert 0 < app["frac_of_region"] < 1 and 0 <= app["hit_rate"]["est"] <= 1
    assert 0 <= M["by_cell"][pc]["share_test_zero_history"] <= 1
    assert set(o["rolling"]) == {str(y) for y in AF.ROLLING_TEST_YEARS}
    for y, r in o["rolling"].items():
        assert r["val_year"] == int(y) - 1 and max(r["train_years"]) < int(y)
    assert os.path.exists(AF.OUT_REPORT)

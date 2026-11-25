# -*- coding: utf-8 -*-
"""Shared stream evaluator (research_tools/hazard_eval_stream.py) on synthetic arrays."""
import math

import numpy as np
import pytest

from research_tools import hazard_eval_stream as E


def _brute(ev, ns, clips, starts, thr):
    return int((np.asarray(ev) >= thr).sum()), E.stream_alarms(ns, clips, starts, thr)


def test_refractory_counts_one_alarm_per_9s_per_clip():
    # one clip, windows every 0.75 s, all above threshold for 20 s -> alarms at 0, 9, 18
    starts = np.arange(0, 20, 0.75)
    scores = np.ones_like(starts)
    assert E.stream_alarms(scores, ["a"] * len(starts), starts, 0.5) == 3
    # refractory is per clip: the same pattern in a second clip doubles the count
    clips = ["a"] * len(starts) + ["b"] * len(starts)
    assert E.stream_alarms(np.r_[scores, scores], clips, np.r_[starts, starts], 0.5) == 6
    # order of the input rows does not matter (windows are sorted by time inside each clip)
    perm = np.random.default_rng(0).permutation(len(starts))
    assert E.stream_alarms(scores[perm], ["a"] * len(starts), starts[perm], 0.5) == 3


def test_sweep_is_exact_against_brute_force():
    # refractory interaction: at 0.4 alarms at 0 s and 9.5 s; at 0.6 alarms at 8 s and 17 s
    starts, scores = np.array([0.0, 8.0, 9.5, 17.0]), np.array([0.5, 0.9, 0.9, 0.9])
    assert E.stream_alarms(scores, ["c"] * 4, starts, 0.4) == 2
    assert E.stream_alarms(scores, ["c"] * 4, starts, 0.6) == 2
    rng = np.random.default_rng(3)
    ev = rng.normal(1, 1, 40)
    ns = rng.normal(0, 1, 300)
    clips = [f"k{i // 12}" for i in range(300)]
    st = np.tile(np.arange(12) * 0.75 * 4, 25) + rng.uniform(0, 0.5, 300)
    sw = E.sweep(ev, ns, clips, st)
    # every unique score is a threshold (no quantile grid), plus +inf
    assert len(sw["thresholds"]) == len(np.unique(np.r_[ev, ns])) + 1
    assert np.isinf(sw["thresholds"][-1]) and sw["hits"][-1] == 0 and sw["alarms"][-1] == 0
    for i in range(0, len(sw["thresholds"]) - 1, 7):
        h, a = _brute(ev, ns, clips, st, sw["thresholds"][i])
        assert (sw["hits"][i], sw["alarms"][i]) == (h, a)
    assert sw["neg_hours"] == pytest.approx(300 * 0.75 / 3600)
    assert np.all(np.diff(sw["hits"]) <= 0) and np.all(np.diff(sw["alarms"]) <= 0)


def test_pick_threshold_respects_budget_and_maximises_hits():
    starts = np.arange(10) * 20.0               # 10 windows, 20 s apart -> no refractory interaction
    ns = np.linspace(0.0, 0.9, 10)
    ev = np.array([0.95, 0.85, 0.55, 0.1])
    sw = E.sweep(ev, ns, ["n"] * 10, starts)
    hours = 10 * 0.75 / 3600
    # budget of 2 alarms -> threshold must leave at most 2 negatives (0.8, 0.9) above it
    t = E.pick_threshold(sw, 2.0001 / hours)
    r = E.at_threshold(ev, ns, ["n"] * 10, starts, t)
    assert r["alarms"] <= 2 and r["hits"] == 2
    # zero budget -> above every negative
    t0 = E.pick_threshold(sw, 0.0)
    assert t0 > 0.9 and E.at_threshold(ev, ns, ["n"] * 10, starts, t0)["hits"] == 1


def test_wilson_and_poisson_intervals():
    lo, hi = E.wilson(15, 100)
    assert lo == pytest.approx(0.0927, abs=5e-4) and hi == pytest.approx(0.2329, abs=5e-4)
    assert E.wilson(0, 10)[0] == 0.0 and E.wilson(10, 10)[1] == pytest.approx(1.0)
    assert E.wilson(0, 0) == (0.0, 1.0)
    plo, phi = E.poisson_ci(0)
    assert plo == 0.0 and phi == pytest.approx(3.689, abs=1e-3)
    plo, phi = E.poisson_ci(5)
    assert plo == pytest.approx(1.6235, abs=1e-3) and phi == pytest.approx(11.668, abs=1e-3)


def test_grouped_holdout_never_reports_on_the_fitting_half():
    rng = np.random.default_rng(5)
    users = [f"u{i}" for i in range(40)]
    ev_users = [users[i % 40] for i in range(120)]
    ev = rng.normal(1.5, 1, 120)
    neg_users = [users[i % 40] for i in range(800)]
    ns = rng.normal(0, 1, 800)
    clips = [f"c{i}" for i in range(800)]
    st = np.zeros(800)
    r = E.grouped_holdout(ev, ev_users, ns, clips, st, neg_users, target_fa_h=5.0)
    halves = {u: E.user_half(u) for u in users}
    for f in r["folds"]:
        n_eval = sum(1 for u in ev_users if halves[u] != f["fit_half"])
        assert f["n_events"] == n_eval
    p = r["pooled"]
    assert p["n_events"] == 120 and p["neg_hours"] == pytest.approx(800 * 0.75 / 3600)
    assert 0 <= p["recall_ci95"][0] <= p["recall"] <= p["recall_ci95"][1] <= 1


def test_test_split_is_refused_without_final():
    with pytest.raises(SystemExit):
        E.load_split("test")
    assert not math.isnan(E.neg_hours(4800))

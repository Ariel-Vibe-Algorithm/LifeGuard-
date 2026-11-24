# -*- coding: utf-8 -*-
"""
hazard_operating_point.py - נקודת עבודה מכוילת לשטח למנוע אירועי הסכנה (אבן דרך 4, שיפור 1).

הבעיה (research/danger_audio_external_v1.md): הספים של hazard_audio_v1 נקבעו על FSD50K dev (שווא אחד לשעה).
על הקלטות אמיתיות המנוע כמעט לא טועה ברקע (0 חלונות עם נקודות שגויות ביותר מ-13 שעות) אבל מפספס את רוב
האירועים (ירי אמיתי: 26.6%). כלומר הסף שמרני מדי לתנאי שטח. כאן מורידים ספים **רק** לקטגוריות שמוסיפות נקודות,
ורק עד גבול שנמדד על נתונים שלא משמשים למבחן.

תכנון (נקבע לפני ההרצה):
- כיול - רק מאגרים ב-CC BY (לא NC): רקע SONYC מחיישני split=train; ירי gunshot_fit מחצי מההקלטות
  (לפי hash של מזהה ההקלטה - קטעים מאותה הקלטה לא נחצים בין כיול למבחן).
- לכל קטגוריה c: סף חדש = min(הסף הישן, max(t_bg, t_fsd)):
  - t_bg = הסף הנמוך ביותר שבו c מסמנת ≤ BG_BUDGET מחלונות הרקע של הכיול;
  - t_fsd = הסף הנמוך ביותר בטבלת השווא של FSD50K dev (בכרטיס) שבו שווא/שעה ≤ FSD_DEV_MAX_FA.
  לעולם לא מעלים סף.
- מבחן (לא נגעו בכיול): רקע SONYC מחיישני validate/test, TAU (טלפונים; NC - הערכה בלבד), ירי מהחצי השני,
  ESC-50 ו-Nonspeech7k (דומים קשים), ו-FSD50K eval דרך שער הרגרסיה הקיים (hazard_audio_train.gate).
- קבלה רק אם כולם מתקיימים:
  1. שער הרגרסיה על FSD50K eval עובר (recall לא יורד; שווא/שעה ≤ ישן×1.5+0.2);
  2. ברקע המבחן (SONYC val/test + TAU): שיעור החלונות עם נקודות שגויות ≤ TEST_BG_MAX, גבול עליון 95% ≤ 2×TEST_BG_MAX;
  3. בדומים קשים: שיעור הקטעים עם נקודות לא עולה ביותר מ-HARD_NEG_MAX_RISE;
  4. בירי של חצי המבחן: שיעור הקטעים שמקבלים נקודות עולה.
- אם התקבל: models/hazard_audio.npz מתעדכן לגרסה hazard_audio_v1.1 (אותם ראשים, ספים חדשים, מדדי eval מעודכנים),
  והגרסה הקודמת נשמרת כ-models/hazard_audio_v1.0.npz.

שימוש: python -I research_tools/hazard_operating_point.py [--apply]
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from research_tools import danger_audio_external as E  # noqa: E402
from research_tools import hazard_audio_train as T  # noqa: E402

MODEL = os.path.join(ROOT, "models", "hazard_audio.npz")
BACKUP = os.path.join(ROOT, "models", "hazard_audio_v1.0.npz")
OUT_REPORT = os.path.join(ROOT, "research", "hazard_operating_point_v1_1.md")
OUT_JSON = os.path.join(ROOT, "research", "hazard_operating_point_v1_1.json")
NEW_VERSION = "hazard_audio_v1.1"
BG_BUDGET = 0.0005            # לכל קטגוריה: ≤ 1 מכל 2000 חלונות רקע בכיול
FSD_DEV_MAX_FA = 2.0          # שווא/שעה על FSD50K dev (טבלת הכרטיס)
TEST_BG_MAX = 0.002           # כל הקטגוריות יחד: ≤ 0.2% מההערכות ברקע אמיתי מקבלות נקודות שגויות
HARD_NEG_MAX_RISE = 0.02


def log(m):
    print(m, flush=True)


def half(recording):
    return int(hashlib.sha256(recording.encode()).hexdigest(), 16) % 2      # 0 = כיול, 1 = מבחן


def load_sets():
    rows = {n: E.load_rows(n) for n in E.DATASETS}
    for n, rs in rows.items():
        if rs and any("scores" not in (r.get("clip") or (r.get("windows") or [{}])[0] or {}) for r in rs
                      if r.get("role") in ("positive", "hard_negative", "background")):
            raise RuntimeError(f"{n}: במטמון אין ציונים רציפים - יש להריץ מחדש את danger_audio_external")
    sonyc_bg = [r for r in rows["sonyc_ust"] if r["role"] == "background"]
    cal_bg = [w for r in sonyc_bg if r["meta"].get("split") == "train" for w in r["windows"] if w]
    test_bg = ([w for r in sonyc_bg if r["meta"].get("split") != "train" for w in r["windows"] if w] +
               [w for r in rows["tau2020_mobile"] if r["role"] == "background" for w in r["windows"] if w])
    gun = [r for r in rows["gunshot_fit"] if r["role"] == "positive"]
    cal_gun = [r for r in gun if half(r["meta"]["recording"]) == 0]
    test_gun = [r for r in gun if half(r["meta"]["recording"]) == 1]
    hard = [r for n in ("esc50", "nonspeech7k") for r in rows[n] if r["role"] == "hard_negative"]
    pos = {n: [r for r in rows[n] if r["role"] == "positive"] for n in ("esc50", "nonspeech7k", "sonyc_ust")}
    return {"cal_bg": cal_bg, "test_bg": test_bg, "cal_gun": cal_gun, "test_gun": test_gun, "hard": hard,
            "pos": pos, "tau_windows": sum(len(r["windows"]) for r in rows["tau2020_mobile"] if r["role"] == "background")}


def interp_fa_threshold(table, target, default):
    """הסף הנמוך ביותר שבו שווא/שעה ≤ target, לפי טבלת הכרטיס (נקודות מדגם גסות) - אינטרפולציה בין שתי
    הנקודות שחוצות את היעד, ליניארית בסף מול log(שווא). בלי חציה בטבלה - default."""
    table = sorted(table, key=lambda x: x[0])
    for (t0, f0), (t1, f1) in zip(table, table[1:]):
        if f0 > target >= f1:
            l0, l1, lt = np.log(f0 + 0.01), np.log(f1 + 0.01), np.log(target + 0.01)
            return float(t0 + (t1 - t0) * (l0 - lt) / (l0 - l1))
    if table and table[0][1] <= target:
        return float(table[0][0])
    return default


def pick_thresholds(card, cats, cal_bg):
    out = {}
    for c in cats:
        cc = card["categories"][c]
        old = float(cc["threshold"])
        s = np.sort(np.array([w["scores"][c] for w in cal_bg if c in w["scores"]], float))[::-1]
        k = int(np.floor(BG_BUDGET * len(s)))          # מותר k חלונות מעל הסף
        t_bg = float(s[k]) + 1e-6 if len(s) > k else old
        t_fsd = interp_fa_threshold(cc.get("fa_table") or [], FSD_DEV_MAX_FA, old)
        out[c] = {"old": old, "t_bg": t_bg, "t_fsd": t_fsd, "new": min(old, max(t_bg, t_fsd)),
                  "cal_windows": int(len(s))}
    return out


def points_with(view, thr, cats):
    """האם החלון/הקטע מקבל נקודות בספים thr (קטגוריות שמוסיפות נקודות בלבד)."""
    sc = (view or {}).get("scores") or {}
    return any(sc.get(c, -1e9) >= thr[c] for c in cats)


def fsd_eval_metrics(card, new_thr):
    """recall / שווא לשעה על FSD50K eval בספים החדשים - אותו חישוב כמו באימון (hazard_audio_train.evaluate)."""
    import audio_intel as AI
    AI.load()
    heads = AI._STATE["heads"]
    data = T.load_split("eval")
    emb = data["emb"]
    W = b = None
    out = {}
    for c, thr in new_thr.items():
        cc = card["categories"][c]
        if cc["champion"] == "head":
            mean, std, w, bb = heads[c]
            frames = np.concatenate([((np.asarray(emb[i:i + 50_000], np.float32) - mean) / std) @ w + bb
                                     for i in range(0, len(emb), 50_000)])
        else:
            if W is None:
                W, b = T.yamnet_dense()
            frames = T.zero_shot_frames(emb, W, b, cc["yam_idx"])
        a, pb = cc["platt"]
        out[c] = T.evaluate(frames, data["offsets"], T.roles(data, c), thr, a, pb)
    return out


def run(apply=False):
    import audio_intel as AI
    assert AI.load()
    card = json.loads(json.dumps(AI._STATE["card"]))
    cats = [c for c, cc in card["categories"].items() if AI.SEVERITY_POINTS.get(cc["severity_prior"], 0) > 0]
    S = load_sets()
    log(f"כיול: {len(S['cal_bg'])} חלונות רקע (SONYC train), {len(S['cal_gun'])} קטעי ירי; "
        f"מבחן: {len(S['test_bg'])} חלונות רקע (SONYC val/test + TAU {S['tau_windows']}), {len(S['test_gun'])} ירי")
    thr = pick_thresholds(card, cats, S["cal_bg"])
    old_t = {c: thr[c]["old"] for c in cats}
    new_t = {c: thr[c]["new"] for c in cats}
    for c in cats:
        log(f"{c}: {thr[c]['old']:.3f} -> {thr[c]['new']:.3f} (t_bg {thr[c]['t_bg']:.3f}, t_fsd {thr[c]['t_fsd']:.3f})")

    def rate(items, t, key="clip"):
        k = sum(points_with(r if key is None else r.get(key), t, cats) for r in items)
        return {"k": k, "n": len(items), "rate": k / max(1, len(items)), "ci": E.wilson(k, len(items))}
    res = {
        "test_bg_old": rate(S["test_bg"], old_t, None), "test_bg_new": rate(S["test_bg"], new_t, None),
        "cal_bg_new": rate(S["cal_bg"], new_t, None),
        "test_gun_old": rate(S["test_gun"], old_t), "test_gun_new": rate(S["test_gun"], new_t),
        "cal_gun_old": rate(S["cal_gun"], old_t), "cal_gun_new": rate(S["cal_gun"], new_t),
        "hard_old": rate(S["hard"], old_t), "hard_new": rate(S["hard"], new_t),
        "positives": {}}
    for n, rs in S["pos"].items():
        for tgt in sorted({t for r in rs for t in r["targets"] if t in cats}):
            sub = [r for r in rs if tgt in r["targets"]]
            res["positives"][f"{n}:{tgt}"] = {
                "old": rate(sub, {**old_t, **{c: 1e9 for c in cats if c != tgt}}),
                "new": rate(sub, {**new_t, **{c: 1e9 for c in cats if c != tgt}})}
    log("FSD50K eval בספים החדשים...")
    fsd = fsd_eval_metrics(card, new_t)
    new_card = json.loads(json.dumps(card))
    new_card["version"] = NEW_VERSION
    new_card["created"] = time.strftime("%Y-%m-%d")
    new_card["operating_point"] = {"method": "research_tools/hazard_operating_point.py", "bg_budget": BG_BUDGET,
                                   "fsd_dev_max_fa": FSD_DEV_MAX_FA, "calibration": "SONYC-UST train sensors + "
                                   "half of gunshot_fit recordings (CC BY only)", "previous": card["version"],
                                   "thresholds": thr}
    for c in cats:
        cc = new_card["categories"][c]
        cc["threshold"] = new_t[c]
        cc["metrics"][cc["champion"]]["eval"] = T._clean(fsd[c])
    tmp_new = MODEL + ".candidate.npz"
    _write_card(MODEL, tmp_new, new_card)
    ok_gate, problems = T.gate(MODEL, tmp_new)
    tb = res["test_bg_new"]
    checks = {
        "fsd_gate": (ok_gate, "; ".join(problems) or "עבר"),
        "test_background": (tb["rate"] <= TEST_BG_MAX and (tb["ci"][1] or 0) <= 2 * TEST_BG_MAX,
                            f"{100 * tb['rate']:.3f}% (עליון {100 * (tb['ci'][1] or 0):.3f}%)"),
        "hard_negatives": (res["hard_new"]["rate"] - res["hard_old"]["rate"] <= HARD_NEG_MAX_RISE,
                           f"{100 * res['hard_old']['rate']:.1f}% -> {100 * res['hard_new']['rate']:.1f}%"),
        "gunshot_test_improves": (res["test_gun_new"]["rate"] > res["test_gun_old"]["rate"],
                                  f"{100 * res['test_gun_old']['rate']:.1f}% -> {100 * res['test_gun_new']['rate']:.1f}%"),
    }
    accepted = all(v[0] for v in checks.values())
    out = T._clean({"created": time.strftime("%Y-%m-%d"), "from": card["version"], "to": NEW_VERSION,
                    "thresholds": thr, "results": res, "fsd_eval_new": fsd,
                    "fsd_eval_old": {c: card["categories"][c]["metrics"][card["categories"][c]["champion"]]["eval"]
                                     for c in cats},
                    "checks": {k: {"pass": bool(v[0]), "detail": v[1]} for k, v in checks.items()},
                    "accepted": accepted, "sizes": {k: len(v) for k, v in S.items() if isinstance(v, list)}})
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report(out)
    if accepted and apply:
        if not os.path.exists(BACKUP):
            shutil.copy2(MODEL, BACKUP)
        os.replace(tmp_new, MODEL)
        log(f"✓ התקבל והוחל: {MODEL} = {NEW_VERSION} (הקודם: {BACKUP})")
    else:
        os.remove(tmp_new)
        log("התקבל - להחלה הריצו עם --apply" if accepted else "לא התקבל - המודל לא שונה")
    return out


def _write_card(src, dst, card):
    d = np.load(src, allow_pickle=False)
    arrays = {k: d[k] for k in d.files if k != "card"}
    arrays["card"] = np.array([json.dumps(card, ensure_ascii=False)])
    np.savez_compressed(dst, **arrays)


HE = E.HE


def report(o):
    p = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    ci = lambda c: "—" if not c or c[0] is None else f"{100 * c[0]:.2f}–{100 * c[1]:.2f}%"  # noqa: E731
    r = o["results"]
    L = [f"# נקודת עבודה מכוילת לשטח - {o['from']} -> {o['to']}", "",
         f"נוצר אוטומטית ע\"י `research_tools/hazard_operating_point.py` ({o['created']}). כל מספר מהריצה.", "",
         "**למה:** על הקלטות אמיתיות (research/danger_audio_external_v1.md) המנוע כמעט לא טעה ברקע אבל פספס את רוב "
         "האירועים. כאן מורידים ספים רק לקטגוריות שמוסיפות נקודות, ורק עד גבול שנמדד על נתוני כיול נפרדים.", "",
         "**כיול** (CC BY בלבד): רקע SONYC מחיישני train + חצי מהקלטות הירי. **מבחן:** רקע SONYC מחיישנים אחרים, "
         "טלפונים של TAU, החצי השני של הירי, ESC-50 ו-Nonspeech7k, ו-FSD50K eval.", "",
         "## הספים", "", "| קטגוריה | ישן | חדש | גבול רקע (כיול) | גבול FSD50K dev |", "|---|---|---|---|---|"]
    for c, t in o["thresholds"].items():
        L.append(f"| {HE.get(c, c)} | {t['old']:.3f} | **{t['new']:.3f}** | {t['t_bg']:.3f} | {t['t_fsd']:.3f} |")
    L += ["", "## תוצאות על המבחן", "", "| מדד | לפני | אחרי |", "|---|---|---|",
          f"| ירי אמיתי (חצי המבחן) - קיבל נקודות | {p(r['test_gun_old']['rate'])} | **{p(r['test_gun_new']['rate'])}** "
          f"(95%: {ci(r['test_gun_new']['ci'])}) |",
          f"| רקע אמיתי - חלונות עם נקודות שגויות ({r['test_bg_new']['n']} חלונות) | {100 * r['test_bg_old']['rate']:.3f}% | "
          f"**{100 * r['test_bg_new']['rate']:.3f}%** (95%: {ci(r['test_bg_new']['ci'])}) |",
          f"| דומים קשים (צחוק, שיעול, צלילי סביבה) - קיבלו נקודות | {p(r['hard_old']['rate'])} | {p(r['hard_new']['rate'])} |"]
    for k, v in r["positives"].items():
        L.append(f"| {k} - זוהה | {p(v['old']['rate'])} | {p(v['new']['rate'])} (n={v['new']['n']}) |")
    L += ["", "### FSD50K eval (שער הרגרסיה)", "", "| קטגוריה | recall לפני | אחרי | שווא/שעה לפני | אחרי |", "|---|---|---|---|---|"]
    for c in o["thresholds"]:
        a, b = o["fsd_eval_old"][c], o["fsd_eval_new"][c]
        L.append(f"| {HE.get(c, c)} | {p(a['recall'])} | {p(b['recall'])} | {a['fa_per_hour']:.2f} | {b['fa_per_hour']:.2f} |")
    L += ["", "## קריטריוני קבלה (נקבעו מראש)", "", "| בדיקה | תוצאה | עבר |", "|---|---|---|"]
    for k, v in o["checks"].items():
        L.append(f"| {k} | {v['detail']} | {'✅' if v['pass'] else '❌'} |")
    L += ["", f"**החלטה:** {'התקבל - ' + o['to'] + ' נכנס לאפליקציה.' if o['accepted'] else 'לא התקבל - נשאר ' + o['from'] + '.'}",
          "", "## מגבלות",
          "- ירי במטווח ≠ ירי ברחוב; רקע SONYC מחיישן על בניין, TAU מטלפונים אבל לא בכיס.",
          "- צליל עדיין לבדו לכל היותר MEDIUM (חלון אימות) - הורדת הסף לא משנה את זה.",
          "- המבחן על FSD50K eval נעשה דרך השער הקיים; הכיול לא השתמש בו."]
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    log(f"דו\"ח: {OUT_REPORT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    run(ap.parse_args().apply)

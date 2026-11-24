# -*- coding: utf-8 -*-
"""
distress_experiments.py - שיפור מודל זיהוי המצוקה: השוואה הוגנת של גרסאות על אותם נתונים.

כל גרסה נמדדת באותה הערכה צולבת לפי מעלה (5 קבוצות) ובאותה מטריקה (recall בתקציב התראות שווא
לשעה), כך שההבדלים הם של המודל ולא של המזל בחלוקה.

גרסאות:
- lr          : רגרסיה לוגיסטית על embedding של מסגרת (הבסיס הקיים)
- lr_ctx      : + הקשר זמני (ממוצע/מקסימום של מסגרות שכנות)
- mlp         : רשת קטנה (שכבה נסתרת) על embedding
- mlp_ctx     : רשת קטנה + הקשר זמני
- *_hard      : משקל x3 לשליליים קשים (צחוק, עידוד, שירה, קהל, ילדים) - איפה שהמודל טועה
- *_s2        : החלקת ציונים לאורך 2 מסגרות (התראה רק על צליל מתמשך)

שימוש:
    python -m research_tools.distress_experiments                 # הערכה צולבת על FSD50K eval
    python -m research_tools.distress_experiments --train-dev      # אימון על dev, בדיקה על eval
"""

import argparse
import json
import os
import time

import numpy as np

from research_tools import acoustic_eval as E
from research_tools import distress_benchmark as B
from research_tools import yamnet_detector as Y

HARD_WEIGHT = 3.0
HARD_LABELS = set(B.HARD_NEGATIVE) - {"Speech", "Music"}
MAX_NEG_FRAMES = 80000


# ===================================================================== מטמון תכונות
def load_features(root, split="eval", log=print):
    """תכונות לכל קטע (embedding, תיוג מסגרות, RMS, zero-shot) - נשמרות לקובץ אחד לטעינה מהירה."""
    path = os.path.join(root, f"{split}_features.npz")
    rows = B.load_index(root, split)
    if os.path.exists(path):
        with np.load(path, allow_pickle=False) as d:
            # כל מערך נטען פעם אחת (גישה ל-d["emb"] בתוך הלולאה טוענת מחדש את כל הקובץ בכל פעם)
            arr = {k: d[k] for k in ("offsets", "fnames", "emb", "event", "rms", "zero_shot", "seconds")}
        off = arr["offsets"]
        feats = {}
        for i, f in enumerate(arr["fnames"]):
            a, b = off[i], off[i + 1]
            feats[str(f)] = {"emb": arr["emb"][a:b], "event": arr["event"][a:b], "rms": arr["rms"][a:b],
                             "zero_shot": arr["zero_shot"][a:b], "seconds": float(arr["seconds"][i])}
        if set(feats) >= {r["fname"] for r in rows}:
            return rows, feats
    feats = B.extract_features(root, rows, os.path.join(root, "cache"), log, split=split)
    names = [r["fname"] for r in rows]
    lens = [len(feats[n]["emb"]) for n in names]
    np.savez(path, fnames=np.array(names), offsets=np.concatenate([[0], np.cumsum(lens)]),
             seconds=np.array([feats[n]["seconds"] for n in names]),
             emb=np.concatenate([feats[n]["emb"] for n in names]).astype(np.float16),
             event=np.concatenate([feats[n]["event"] for n in names]),
             rms=np.concatenate([feats[n]["rms"] for n in names]).astype(np.float32),
             zero_shot=np.concatenate([feats[n]["zero_shot"] for n in names]).astype(np.float32))
    return rows, feats


# ===================================================================== אימון/חיזוי
def _x(feats, fname, ctx):
    e = feats[fname]["emb"].astype(np.float32)
    return Y.context_features(e) if ctx else e


def build_training(rows, feats, ctx, hard, rng, max_neg=MAX_NEG_FRAMES):
    Xp = [_x(feats, r["fname"], ctx)[feats[r["fname"]]["event"]] for r in rows if r["positive"]]
    neg = [r for r in rows if not r["positive"]]
    lens = np.array([len(feats[r["fname"]]["emb"]) for r in neg])
    clip_w = np.array([HARD_WEIGHT if (hard and r["label"] in HARD_LABELS) else 1.0 for r in neg], np.float32)
    frame_w = np.repeat(clip_w, lens)
    total = int(lens.sum())
    # דוגמים *אינדקסים* קודם ומחשבים תכונות רק למסגרות שנבחרו (אחרת: ~7GB עם הקשר זמני על dev).
    # הדגימה מעדיפה שליליים קשים כשמופעל, כדי שלא ילכו לאיבוד ב"ים" של צלילים קלים.
    if total > max_neg:
        # דגימה משוקללת בלי החזרה בשיטת Efraimidis-Spirakis (מפתח u^(1/w), לוקחים את הגדולים).
        # rng.choice(..., replace=False, p=...) של numpy איטי מאוד על מאות אלפי מסגרות (נתקע בפועל).
        keys = rng.random(total) ** (1.0 / frame_w)
        pick = np.sort(np.argpartition(-keys, max_neg - 1)[:max_neg])
    else:
        pick = np.arange(total)
    starts = np.concatenate([[0], np.cumsum(lens)])
    Xn, wn = [], []
    ci = np.searchsorted(starts, pick, side="right") - 1
    for c in np.unique(ci):
        local = pick[ci == c] - starts[c]
        Xn.append(_x(feats, neg[c]["fname"], ctx)[local])
        wn.append(np.full(len(local), clip_w[c], np.float32))
    Xp, Xn, wn = np.concatenate(Xp), np.concatenate(Xn), np.concatenate(wn)
    X = np.concatenate([Xp, Xn])
    y = np.r_[np.ones(len(Xp)), np.zeros(len(Xn))].astype(np.float32)
    w = np.r_[np.ones(len(Xp), np.float32), wn]
    return X, y, w


def make_model(kind):
    return Y.MLPModel(hidden=128, epochs=12, l2=1e-3) if kind == "mlp" else Y.LogisticModel(l2=1e-2, epochs=300)


def fit(kind, X, y, w):
    m = make_model(kind)
    return m.fit(X, y, sample_weight=w) if kind == "mlp" else m.fit(X, y)


def variant_scores(name, rows_train_folds, feats, seed=0):
    """name כמו 'mlp_ctx_hard_s2' -> ציוני out-of-fold לכל קטע."""
    parts = name.split("_")
    kind, ctx, hard = parts[0], "ctx" in parts, "hard" in parts
    width = next((int(p[1:]) for p in parts if p.startswith("s") and p[1:].isdigit()), 1)
    rng = np.random.default_rng(seed)
    out = {}
    for train_rows, test_rows in rows_train_folds:
        X, y, w = build_training(train_rows, feats, ctx, hard, rng)
        if kind == "lr" and hard:
            # ל-LR אין משקל לדוגמה - משכפלים את השליליים הקשים במקום
            rep = np.repeat(np.arange(len(y)), np.round(w).astype(int))
            X, y, w = X[rep], y[rep], np.ones(len(rep), np.float32)
        m = fit(kind, X, y, w)
        for r in test_rows:
            out[r["fname"]] = Y.smooth_scores(m.decision_function(_x(feats, r["fname"], ctx)), width)
    return out


def folds(rows, k=B.FOLDS):
    return [([r for r in rows if B.fold_of(r["uploader"]) != i], [r for r in rows if B.fold_of(r["uploader"]) == i])
            for i in range(k)]


def summarize(res):
    b = res["recall_at_fa_budget"]
    return {k: (round(v["recall"], 4) if v else None) for k, v in b.items()}


def run(root, variants, out_md, train_dev=False, log=print):
    rows, feats = load_features(root, "eval", log)
    clips = [E.Clip(r["fname"], None, Y.TARGET_SR, r["positive"], r["label"], {"seconds": feats[r["fname"]]["seconds"]})
             for r in rows]
    if train_dev:
        dev_rows, dev_feats = load_features(root, "dev", log)
        feats = {**dev_feats, **feats}
        plan = [(dev_rows, rows)]
        setup = f"אימון על FSD50K dev ({len(dev_rows)} קטעים, {sum(r['positive'] for r in dev_rows)} חיוביים), בדיקה על eval"
    else:
        plan = folds(rows)
        setup = f"הערכה צולבת 5 קבוצות לפי מעלה על FSD50K eval ({len(rows)} קטעים)"
    table = []
    for v in variants:
        t0 = time.time()
        sc = variant_scores(v, plan, feats)
        res = E.evaluate(E.PrecomputedDetector(v, sc, Y.HOP_SECONDS), clips, budgets=B.BUDGETS)
        s = summarize(res)
        table.append((v, s, res, time.time() - t0))
        log(f"{v:22} recall@1/h={s['1.0']}  @0.5/h={s['0.5']}  @0.1/h={s['0.1']}  ({time.time() - t0:.0f}s)")
    lines = ["# שיפור מודל זיהוי המצוקה - השוואת גרסאות", "", f"- מערך: {setup}",
             f"- חיוביים: {sum(r['positive'] for r in rows)} קטעים (Screaming/Shout/Yell/Crying). "
             f"שליליים: {sum(c.seconds for c in clips if not c.positive) / 3600:.1f} שעות", "",
             "| גרסה | recall @ 1 התראת שווא/שעה | @ 0.5/שעה | @ 0.1/שעה | רווח סמך 95% (@1/שעה) |",
             "|---|---|---|---|---|"]
    npos = sum(r["positive"] for r in rows)
    for v, s, res, _ in table:
        ci = E.wilson_interval(round((s["1.0"] or 0) * npos), npos)
        lines.append(f"| {v} | {s['1.0']:.1%} | {s['0.5']:.1%} | {s['0.1']:.1%} | {ci[0]:.0%}–{ci[1]:.0%} |")
    best = max(table, key=lambda t: (t[1]["1.0"] or 0, t[1]["0.1"] or 0))
    lines += ["", f"**הטובה ביותר:** `{best[0]}`", "", E.report_markdown(best[2])]
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return table


def augmented_set(root, split, rows, select, copies, seed, tag, log=print):
    """
    קטעים "בתנאי טלפון ברחוב" (research_tools/augment.py) -> (rows, feats) חדשים, עם מטמון לקובץ.
    הרקעים לקוחים רק מאותו split (ל-eval: רקעים שהמודל לא שמע באימון). תיוג המסגרות החיוביות
    מחושב מהאות הנקי, כי הרעש עלול להסתיר את האירוע בחישוב אנרגיה.
    """
    from research_tools import augment as AU
    from research_tools.split_zip import SplitZip
    path = os.path.join(root, f"{split}_{tag}_features.npz")
    rng = np.random.default_rng(seed)
    chosen = select(rows, rng)
    if os.path.exists(path):
        with np.load(path, allow_pickle=False) as d:
            arr = {k: d[k] for k in d.files}
        meta = json.loads(str(arr["meta"][0]))
        feats = {}
        for i, f in enumerate(arr["fnames"]):
            a, b = arr["offsets"][i], arr["offsets"][i + 1]
            feats[str(f)] = {"emb": arr["emb"][a:b], "event": arr["event"][a:b], "rms": arr["rms"][a:b],
                             "seconds": float(arr["seconds"][i])}
        return meta["rows"], feats
    street = [r for r in rows if not r["positive"] and any(l in AU.STREET_LABELS for l in r["labels"])]
    cache = Y.FeatureCache(os.path.join(root, "cache"))
    out_rows, feats, t0 = [], {}, time.time()
    with SplitZip(os.path.join(root, "raw", f"FSD50K.{split}_audio.zip")) as z:
        names = {os.path.basename(n)[:-4]: n for n in z.names() if n.endswith(".wav")}
        load = lambda f: Y.to_16k(*E._read_wav_bytes(z.read(names[f]), resample_to=None))  # noqa: E731
        bgs = [load(r["fname"]) for r in rng.choice(street, min(300, len(street)), replace=False)]
        rms_det = E.RmsDetector(window_seconds=0.96, hop_seconds=Y.HOP_SECONDS)
        for i, r in enumerate(chosen):
            clean = load(r["fname"])
            for k in range(copies):
                y, desc = AU.street_phone(clean, bgs, rng)
                f = cache.get(y, Y.TARGET_SR)
                fid = f"{r['fname']}#{tag}{k}"
                feats[fid] = {"emb": f["emb"], "event": Y.event_frames(clean, len(f["emb"])),
                              "rms": rms_det.frame_scores(y, Y.TARGET_SR)[1].astype(np.float32),
                              "seconds": len(y) / Y.TARGET_SR}
                out_rows.append({**r, "fname": fid, "aug": desc, "labels": list(r["labels"])})
            if (i + 1) % 500 == 0:
                log(f"  {tag}: {i + 1}/{len(chosen)} ({time.time() - t0:.0f}s)")
    names_ = [r["fname"] for r in out_rows]
    lens = [len(feats[n]["emb"]) for n in names_]
    np.savez(path, fnames=np.array(names_), offsets=np.concatenate([[0], np.cumsum(lens)]).astype(np.int64),
             seconds=np.array([feats[n]["seconds"] for n in names_]),
             emb=np.concatenate([feats[n]["emb"] for n in names_]).astype(np.float16),
             event=np.concatenate([feats[n]["event"] for n in names_]),
             rms=np.concatenate([feats[n]["rms"] for n in names_]).astype(np.float32),
             meta=np.array([json.dumps({"rows": out_rows}, ensure_ascii=False)]))
    return out_rows, feats


def select_train_aug(rows, rng, neg_ratio=1.0):
    """כל החיוביים של dev/train + אותו מספר שליליים (לא רחוב) - כדי שהרעש לא יהפוך לרמז."""
    from research_tools import augment as AU
    tr = [r for r in rows if r.get("split") == "train"]
    pos = [r for r in tr if r["positive"]]
    neg = [r for r in tr if not r["positive"] and not any(l in AU.STREET_LABELS for l in r["labels"])]
    return pos + list(rng.choice(neg, min(len(neg), int(len(pos) * neg_ratio)), replace=False))


def select_val(rows, rng):
    return [r for r in rows if r.get("split") == "val"]


def select_all(rows, rng):
    return list(rows)


def _clips(rows, feats):
    return [E.Clip(r["fname"], None, Y.TARGET_SR, r["positive"], r["label"], {"seconds": feats[r["fname"]]["seconds"]})
            for r in rows]


def _train_holdout(variant, train_rows, feats, val_rows, budget, log, calib_rows=None):
    parts = variant.split("_")
    kind, ctx, hard = parts[0], "ctx" in parts, "hard" in parts
    width = next((int(p[1:]) for p in parts if p.startswith("s") and p[1:].isdigit()), 1)
    X, y, w = build_training(train_rows, feats, ctx, hard, np.random.default_rng(0), max_neg=200000)
    log(f"אימון {variant}: {int(y.sum())} מסגרות חיוביות, {int(len(y) - y.sum())} שליליות")
    m = fit(kind, X, y, w)

    def det(rows, name):
        return E.PrecomputedDetector(name, {r["fname"]: Y.smooth_scores(m.decision_function(_x(feats, r["fname"], ctx)),
                                                                         width) for r in rows}, Y.HOP_SECONDS)
    # כיול על val נקי *ורועש* (רקעים מ-dev בלבד): סף שעומד בתקציב גם ברחוב, לא רק בשקט
    cal = calib_rows or val_rows
    thr = E.threshold_for_budget(det(cal, variant), _clips(cal, feats), budget)
    m.info.update({"variant": variant, "context": ctx, "smooth": width, "threshold_logit": round(float(thr), 4),
                   "calibrated_on": "FSD50K dev/val", "fa_budget_per_hour": budget})
    return m, thr, det


def holdout(root, variant, out_model, out_md, budget=1.0, log=print):
    """
    הפרוטוקול הכן: אימון על dev/train -> סף על dev/val (תקציב התראות שווא) -> מדידה *אחת* על eval
    בסף הקבוע - גם נקי וגם "טלפון ברחוב" (רקעים מ-eval בלבד). משווים מודל רגיל למודל עם העשרה,
    ואת עוצמת הרעש (המנגנון הישן). המודל שנשמר הוא בדיוק אחד מהמודלים שנמדדו.
    """
    dev_rows, dev_feats = load_features(root, "dev", log)
    ev_rows, ev_feats = load_features(root, "eval", log)
    noisy_rows, noisy_feats = augmented_set(root, "eval", ev_rows, select_all, 1, 123, "noisy", log)
    aug_rows, aug_feats = augmented_set(root, "dev", dev_rows, select_train_aug, 2, 0, "aug", log)
    valn_rows, valn_feats = augmented_set(root, "dev", dev_rows, select_val, 1, 7, "valnoisy", log)
    feats = {**dev_feats, **ev_feats, **noisy_feats, **aug_feats, **valn_feats}
    train = [r for r in dev_rows if r["split"] == "train"]
    val = [r for r in dev_rows if r["split"] == "val"]
    tests = {"נקי": ev_rows, "טלפון ברחוב": noisy_rows}
    results, models = [], {}
    rms = {r["fname"]: feats[r["fname"]]["rms"] for r in val + valn_rows + ev_rows + noisy_rows}
    rdet = E.PrecomputedDetector("עוצמת רעש (הקיים)", rms, Y.HOP_SECONDS)
    rthr = E.threshold_for_budget(rdet, _clips(val + valn_rows, feats), budget)
    for cond, rows in tests.items():
        results.append(("עוצמת רעש (הקיים)", cond, E.evaluate_at(rdet, _clips(rows, feats), rthr)))
    for name, tr in ((variant, train), (variant + " + העשרה", train + aug_rows)):
        m, thr, det = _train_holdout(variant, tr, feats, val, budget, log, calib_rows=val + valn_rows)
        models[name] = m
        for cond, rows in tests.items():
            results.append((name, cond, E.evaluate_at(det(rows, name), _clips(rows, feats), thr)))
    # בחירה: הכי טוב ב"טלפון ברחוב" בתנאי שבנקי לא חורג פי 2 מהתקציב
    def ok(name):
        clean = next(r for n, c, r in results if n == name and c == "נקי")
        return clean["fa_per_hour"] <= 2 * budget
    cands = [n for n in models if ok(n)] or list(models)
    chosen = max(cands, key=lambda n: next(r for nn, c, r in results if nn == n and c == "טלפון ברחוב")["recall"])
    m = models[chosen]
    res_c = {c: r for n, c, r in results if n == chosen}
    m.info.update({"trained_on": "FSD50K dev/train" + (" + augmented" if "העשרה" in chosen else ""),
                   "positive_labels": list(B.POSITIVE),
                   "eval_clean": {"recall": round(res_c["נקי"]["recall"], 4), "fa_per_hour": round(res_c["נקי"]["fa_per_hour"], 3)},
                   "eval_street_phone": {"recall": round(res_c["טלפון ברחוב"]["recall"], 4),
                                         "fa_per_hour": round(res_c["טלפון ברחוב"]["fa_per_hour"], 3)},
                   "license_note": "trained on FSD50K (CC0/CC-BY/CC-BY-NC/Sampling+) - non-commercial research use"})
    os.makedirs(os.path.dirname(out_model), exist_ok=True)
    m.save(out_model)
    lines = ["# מודל זיהוי מצוקה - בדיקה חיצונית (held-out)", "",
             f"- אימון: FSD50K dev/train ({len(train)} קטעים, {sum(r['positive'] for r in train)} חיוביים); "
             f"העשרה: {len(aug_rows)} קטעים בתנאי טלפון ברחוב (רעש תנועה/רוח/גשם/צעדים, ערוץ טלפון, כיס)",
             f"- כיול סף: dev/val נקי + רועש ({len(val)} + {len(valn_rows)} קטעים, רקעים מ-dev בלבד) - "
             f"תקציב {budget} התראת שווא לשעה. הסף לא נבחר על סט הבדיקה.",
             f"- בדיקה: FSD50K eval ({len(ev_rows)} קטעים, {sum(r['positive'] for r in ev_rows)} חיוביים), "
             "נקי וגם בתנאי טלפון ברחוב (רקעים מ-eval בלבד - לא נשמעו באימון)", "",
             "| גלאי | תנאי | recall (רווח סמך 95%) | התראות שווא לשעה |", "|---|---|---|---|"]
    for n, c, r in results:
        lo, hi = r["recall_ci95"]
        lines.append(f"| {n} | {c} | {r['recall']:.1%} ({lo:.0%}–{hi:.0%}) | {r['fa_per_hour']:.2f} |")
    lines += ["", f"**נבחר לאפליקציה:** {chosen} (`{os.path.basename(out_model)}`) - בדיוק המודל שנמדד כאן, עם הסף הזה.",
              "", "זוהו לפי סוג (טלפון ברחוב): " + ", ".join(
                  f"{k} {v['detected']}/{v['clips']}" for k, v in sorted(res_c["טלפון ברחוב"]["recall_by_label"].items())),
              "התראות שווא בעיקר מ (טלפון ברחוב): " + (", ".join(
                  f"{k} {v['false_alarms']}" for k, v in list(res_c["טלפון ברחוב"]["fa_by_label"].items())[:8]) or "אין"),
              "", "מגבלות: FSD50K אינו הקלטת טלפון אמיתית; ההעשרה היא הדמיה. נדרש אימות בשטח (ראו field_test)."]
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    log("\n".join(lines))
    return results


def train_final(root, variant, out_path, splits=("eval",), log=print):
    """מודל סופי לשימוש (על כל הנתונים הזמינים) + כרטיס מודל."""
    rows, feats = [], {}
    for sp in splits:
        r, f = load_features(root, sp, log)
        rows += r
        feats.update(f)
    parts = variant.split("_")
    kind, ctx, hard = parts[0], "ctx" in parts, "hard" in parts
    X, y, w = build_training(rows, feats, ctx, hard, np.random.default_rng(1))
    m = fit(kind, X, y, w)
    m.info.update({"variant": variant, "context": ctx, "smooth": next((int(p[1:]) for p in parts if p.startswith("s")
                                                                       and p[1:].isdigit()), 1),
                   "trained_on": list(splits), "positive_labels": list(B.POSITIVE)})
    m.save(out_path)
    return m


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--fsd50k", default=os.path.join("research", "samples", "fsd50k"))
    ap.add_argument("--variants", default="lr,lr_s2,lr_ctx,lr_ctx_s2,mlp,mlp_s2,mlp_ctx,mlp_ctx_s2")
    ap.add_argument("--train-dev", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--holdout", metavar="VARIANT", help="אימון dev/train, סף על dev/val, בדיקה על eval")
    a = ap.parse_args(argv)
    if a.holdout:
        holdout(a.fsd50k, a.holdout, os.path.join("models", "distress_head.npz"),
                a.out or os.path.join("research", "acoustic_model_holdout.md"))
        return
    out = a.out or os.path.join("research", "acoustic_model_improvement" + ("_dev" if a.train_dev else "") + ".md")
    run(a.fsd50k, a.variants.split(","), out, train_dev=a.train_dev)


if __name__ == "__main__":
    main()

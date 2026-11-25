# -*- coding: utf-8 -*-
"""
hv3_freesound.py - אימון מחדש של קטגוריות חלשות במנוע הסכנה הקולי על קטעי Freesound, עם שער לפני/אחרי.

הקטגוריות: צרחה, קול מצוקה (צרחה / צעקה / בכי), סירנה, אזעקה, חבטה, זיקוקים. אלה הקטגוריות שעדיין רצות על v1
(research/hazard_v2_benchmark.md) - ירי, פיצוץ ושבירת זכוכית כבר ב-v2 ולא נוגעים בהן.

אותו קוד בדיוק כמו v2 (כדי שמה שנמדד = מה שרץ באפליקציה):
- מאפייני חלון: research_tools/hv2_features.window_record.
- אימון: hv2_train.train_category (שלב 1 + שלב 2, שליליים קשים במשקל פי 3, MIL: שני החלונות המובילים בקטע חיובי).
  אופציונלי: MIL מעודן (סבב שני שבו החלונות החיוביים נבחרים לפי zero-shot + ציון הראש של הסבב הראשון);
  הבחירה בין רגיל למעודן נעשית על val בלבד.
- נקודת עבודה: hv2_train.select_operating_point על val.

ניקוי ואיזון (חדש):
- תוויות מ-freesound_collect.classify: עמום / וטו לתווית / מילים נרדפות בשליליים -> תגיות "excl:<cat>"
  שמתווספות ל-hv2_train.EXCL (החלון מושמט מהקטגוריה, לא שלילי).
- תקרה לקטעים לכל מעלה (לכל מחלקה, ובשליליים) - כדי שמעלה אחד לא יקבע את האימון או את ה-test.
- חלונות של קטע שלא נמצא באינדקס (עמום לפי הכללים החדשים / מעבר לתקרה) **מושמטים** - לא הופכים לשליליים.

השער (test, מעלים שלא הופיעו באימון ולא ב-FSD50K): קטגוריה נכנסת רק אם
  (א) מספיק אירועים ומעלים; (ב) השיפור בזיהוי מובהק: McNemar חד-צדדי p<0.05 על זוגות אירועים, וגם גבול תחתון
  של רווח-סמך 95% (bootstrap לפי מעלה) על הפרש הזיהוי > 0; (ג) זיהוי "כל חלון בקטע" לא נמוך מ-v1;
  (ד) חלונות שגויים (כל השליליים, וגם הקשים) לא יותר מ-v1.
כל הרצה על test נרשמת ב-test_log.jsonl; הרצה חוזרת אחרי שינוי מסומנת בדו"ח (test שנעשה בו שימוש חוזר).

שימוש:
  python -I research_tools/hv3_freesound.py features   # -> research_data/freesound/features_<split>.npz
  python -I research_tools/hv3_freesound.py train      # -> research_data/freesound/model_candidate.npz + דו"ח
  python -I research_tools/hv3_freesound.py promote [--allow-voice]
"""
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from research_tools import freesound_collect as C  # noqa: E402

OUT = C.OUT
CANDIDATE = os.path.join(OUT, "model_candidate.npz")
GATE_JSON = os.path.join(OUT, "gate.json")
TEST_LOG = os.path.join(OUT, "test_log.jsonl")
REPORT = os.path.join(ROOT, "research", "hazard_v3_freesound.md")
SHIPPED = os.path.join(ROOT, "models", "hazard_audio_v2.npz")
CATS = ("scream", "distress_voice", "siren", "alarm", "impact", "fireworks")
# קטגוריות שהציון שלהן משמש את כלל "צעקות חוזרות" (models/shout_sequence.json: t_low על סקאלת ראש המצוקה של v1,
# s_low על הסתברות YAMNet "Screaming" של v1). קידום שלהן ל-v2 משנה סקאלה -> הכלל חייב כיול מחדש.
VOICE_CATS = ("scream", "distress_voice")
MAX_WIN_POS, MAX_WIN_NEG = 12, 6
SEED = 11
CAP_POS_PER_USER = {"train": 20, "val": 8, "test": 8}     # קטעים חיוביים לכל מעלה, לכל מחלקה בקטלוג
CAP_NEG_PER_USER = {"train": 60, "val": 25, "test": 25}   # קטעים שליליים (בלי שום יעד) לכל מעלה
MIN_EVENTS, MIN_USERS = 30, 10
BOOT = 2000
ALPHA = 0.05

# תוויות הקטלוג -> הטקסונומיה של hv2 (hv2_data.TARGETS / HARD_NEG). חייב להתאים ל-C.CAT_OF
LABEL_MAP = {"scream": {"scream", "distress_voice"}, "shout": {"distress_voice"}, "crying": {"distress_voice"},
             "singing": {"music"}}
EXTRA_HARD = {"alarm": {"beep"}, "siren": {"beep"}, "scream": {"dog", "sports"}, "distress_voice": {"dog", "sports"},
              "impact": {"kitchen", "sports"}, "fireworks": {"rain_wind"}}
TARGET_LABELS = ("scream", "shout", "crying", "siren", "alarm", "impact", "fireworks", "gunshot", "explosion",
                 "glass_break")


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def _h(key, mod=2 ** 31):
    return int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % mod


def tags_of(r):
    out = set()
    for lab in r["labels"]:
        out |= LABEL_MAP.get(lab, {lab})
    return out


def all_rows():
    """כל הקטעים השמישים (לא עמומים, יש אודיו, יש תווית או החרגה) - לפני תקרת מעלה."""
    out = []
    for r in C.load_catalog():
        if r["ambiguous"] or not (r["labels"] or r["excl"]):
            continue
        if os.path.exists(os.path.join(C.AUDIO, f"{r['id']}.mp3")):
            out.append(r)
    return out


def rows():
    """אחרי תקרה לכל מעלה: לכל מחלקת יעד בקטלוג - עד CAP_POS קטעים למעלה; שליליים - עד CAP_NEG.
    הבחירה דטרמיניסטית (hash של מזהה הקטע), לא תלויה במודל."""
    rs = sorted(all_rows(), key=lambda r: _h(r["id"]))
    keep, seen = [], {}
    for r in rs:
        tl = [lab for lab in r["labels"] if lab in TARGET_LABELS]
        if tl:
            ok = all(seen.get((r["split"], r["user"], lab), 0) < CAP_POS_PER_USER[r["split"]] for lab in tl)
            if ok:
                for lab in tl:
                    seen[(r["split"], r["user"], lab)] = seen.get((r["split"], r["user"], lab), 0) + 1
        else:
            k = (r["split"], r["user"], "_neg")
            ok = seen.get(k, 0) < CAP_NEG_PER_USER[r["split"]]
            if ok:
                seen[k] = seen.get(k, 0) + 1
        if ok:
            keep.append(r)
    return keep


def decode(path):
    import soundfile as sf
    from research_tools import yamnet_detector as Y
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return Y.to_16k(x.mean(axis=1), sr)


# ================================================================= מאפיינים
def _init_worker():
    from research_tools import hv2_features as F
    F._init()


def select_windows(x, is_pos, rng):
    """חלונות hazard_features.windows (אותו גודל/קפיצה כמו בזמן ריצה). בקטע חיובי ארוך: החלונות החזקים
    ביותר (RMS) + 2 אקראיים - כדי שהאירוע הקצר (צרחה אחת בהקלטה של 30 ש') לא ייפול בדגימה אקראית."""
    import hazard_features as HF
    wins = HF.windows(x)
    cap = MAX_WIN_POS if is_pos else MAX_WIN_NEG
    if len(wins) <= cap:
        return wins
    if not is_pos:
        pick = rng.choice(len(wins), size=cap, replace=False)
    else:
        rms = np.array([float(np.sqrt(np.mean(w ** 2))) for _, w in wins])
        top = [int(i) for i in np.argsort(-rms)[:cap - 2]]
        rest = sorted(set(range(len(wins))) - set(top))
        pick = top + list(rng.choice(rest, size=min(2, len(rest)), replace=False))
    return [wins[i] for i in sorted(int(i) for i in pick)]


def _clip_task(r):
    from research_tools import hv2_features as F
    from research_tools import hv2_data as D
    try:
        x = decode(os.path.join(C.AUDIO, f"{r['id']}.mp3"))
    except Exception as exc:  # noqa: BLE001 - קובץ פגום -> מדלגים
        return r["id"], None, str(exc)
    if len(x) < 0.3 * 16000 or float(np.abs(x).max()) < 1e-4:
        return r["id"], None, "קצר מדי / שקט"
    is_pos = bool(tags_of(r) & set(D.TARGETS))
    rng = np.random.default_rng(r["id"])
    recs = [(s, F.window_record(w)) for s, w in select_windows(x, is_pos, rng)]
    return r["id"], recs, None


def features(workers=4):
    # מחלצים לכל הקטעים השמישים (לפני תקרה) - התקרה והתיוג מוחלים בזמן האימון, כך ששינוי כללים לא מחייב חילוץ מחדש
    rs = all_rows()
    log(f"קטעים עם אודיו: {len(rs)}")
    for split in ("train", "val", "test"):
        path = os.path.join(OUT, f"features_{split}.npz")
        if os.path.exists(path):
            log(f"{split}: קיים - מדלגים (למחוק את הקובץ כדי לחלץ מחדש עם בחירת החלונות החדשה)")
            continue
        sub = [r for r in rs if r["split"] == split]
        cols = {k: [] for k in ("s1", "cur", "spec", "v1", "zs", "rms")}
        meta, bad, t0 = [], 0, time.time()
        with cf.ProcessPoolExecutor(workers, initializer=_init_worker) as ex:
            for i, (cid, recs, err) in enumerate(ex.map(_clip_task, sub, chunksize=8), 1):
                if recs is None:
                    bad += 1
                else:
                    for start, (s1, cur, spec, v1, zs, rms) in recs:
                        for k, v in zip(cols, (s1, cur, spec, v1, zs, rms)):
                            cols[k].append(v)
                        meta.append({"id": cid, "variant": 0, "start": start / 16000})
                if i % 500 == 0:
                    log(f"  {split}: {i}/{len(sub)} קטעים, {len(meta)} חלונות ({time.time() - t0:.0f}s)")
        arrs = {k: np.stack(v) for k, v in cols.items()}
        arrs["meta"] = np.array([json.dumps(meta)])
        np.savez_compressed(path, **arrs)
        log(f"{split}: {len(sub) - bad} קטעים, {len(meta)} חלונות, {bad} דולגו")


def load(split, idx):
    """מערכים + מטא, **רק** לחלונות של קטעים שבאינדקס. hv2_train.window_labels מתייחס לקטע שאינו באינדקס
    כשלילי (תגיות ריקות) - כך שבלי הסינון, קטע שהפך לעמום / חרג מהתקרה היה נלמד כשלילי."""
    d = np.load(os.path.join(OUT, f"features_{split}.npz"), allow_pickle=False)
    meta = json.loads(str(d["meta"][0]))
    keep = np.array([m["id"] in idx for m in meta], bool)
    arrs = {k: d[k][keep] for k in d.files if k != "meta"}
    return arrs, [m for m, k in zip(meta, keep) if k], int((~keep).sum())


def index():
    return {r["id"]: {"tags": sorted(tags_of(r) | {f"excl:{c}" for c in r["excl"]}), "source": "freesound",
                      "env": "indoor_mixed", "user": r["user"], "license": r["license"]} for r in rows()}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:16]


# ================================================================= אימון + שער
def _setup_trainer():
    from research_tools import hv2_train as T2
    for cat, extra in EXTRA_HARD.items():
        T2.HARD[cat] = set(T2.HARD[cat]) | extra
    for cat in T2.TARGETS:
        T2.EXCL[cat] = set(T2.EXCL.get(cat, set())) | {f"excl:{cat}"}
    return T2


def _detect(model, op, d):
    l1 = model["h1"].logit(d["s1"].astype(np.float32))
    det = l1 >= op["t1"]
    if op["t2"] is not None:
        from research_tools import hv2_train as T2
        det &= model["h2"].logit(T2.verifier_matrix(l1, d)) >= op["t2"]
    return det


def _train_refined(cat, tr, tr_meta, idx, rng, T2):
    """MIL סבב שני: בחירת החלונות החיוביים לפי zero-shot + sigmoid(ציון הראש מהסבב הראשון).
    רק תוויות האימון משתנות; val/test נשארים עם הגדרת ה-zero-shot (ההגדרה לא תלויה במודל)."""
    m1 = T2.train_category(cat, tr, tr_meta, idx, rng)
    if m1 is None:
        return None, None
    ci = T2.TARGETS.index(cat)
    l1 = m1["h1"].logit(tr["s1"].astype(np.float32))
    zs2 = tr["zs"].copy()
    zs2[:, ci] = zs2[:, ci] + 1.0 / (1.0 + np.exp(-np.clip(l1, -30, 30)))
    m2 = T2.train_category(cat, dict(tr, zs=zs2), tr_meta, idx, rng)
    return m1, m2


def _mcnemar_one_sided(b, c):
    """P(X >= b) עבור X~Bin(b+c, 0.5): האם החדש מזהה אירועים ש-v1 מפספס יותר מאשר להפך."""
    from math import comb
    n = b + c
    if n == 0:
        return 1.0
    return float(sum(comb(n, k) for k in range(b, n + 1)) / 2 ** n)


def gate(cat, model, op, te, te_meta, idx, T2):
    y0, hard, ci = T2.window_labels(te_meta, idx, cat)
    y = T2.resolve_positives(y0.copy(), te_meta, te["zs"], idx, cat, ci)
    neg = y == 0
    v1 = te["v1"][:, ci] >= op["v1_threshold"]
    new = _detect(model, op, te)
    users = np.array([idx[m["id"]]["user"] for m in te_meta])
    # אירועים (MIL, כמו קודם): קטע חיובי זוהה אם אחד משני החלונות המובילים שלו זוהה
    ev = {}
    for i in np.flatnonzero(y == 1):
        ev.setdefault(te_meta[i]["id"], []).append(i)
    ev_ids = sorted(ev)
    hv1 = np.array([v1[ev[k]].any() for k in ev_ids], bool)
    hnew = np.array([new[ev[k]].any() for k in ev_ids], bool)
    ev_user = np.array([users[ev[k][0]] for k in ev_ids])
    # "כל חלון בקטע החיובי" (לא תלוי בבחירת ה-MIL, שנעשית לפי zero-shot - כלומר לפי ציון v1 עצמו בצרחה/זיקוקים)
    anyg = {}
    for i in np.flatnonzero(y0 == -2):
        anyg.setdefault(te_meta[i]["id"], []).append(i)
    any_v1 = int(sum(v1[ii].any() for ii in anyg.values()))
    any_new = int(sum(new[ii].any() for ii in anyg.values()))
    n = len(ev_ids)
    b, c = int((hnew & ~hv1).sum()), int((hv1 & ~hnew).sum())
    # bootstrap לפי מעלה: הפרש הזיהוי, והפרש החלונות השגויים
    rng = np.random.default_rng(SEED)
    uu = sorted(set(ev_user))
    neg_users = users[neg]
    fv1_by_u = {u: 0 for u in set(neg_users)}
    fnew_by_u = dict(fv1_by_u)
    for u, a1, a2 in zip(neg_users, v1[neg], new[neg]):
        fv1_by_u[u] += int(a1)
        fnew_by_u[u] += int(a2)
    nu = sorted(fv1_by_u)
    per_u = {u: (int(hnew[ev_user == u].sum()) - int(hv1[ev_user == u].sum()), int((ev_user == u).sum())) for u in uu}
    diffs, fdiffs = [], []
    for _ in range(BOOT):
        s = [uu[j] for j in rng.integers(len(uu), size=len(uu))] if uu else []
        tot = sum(per_u[u][1] for u in s)
        diffs.append(sum(per_u[u][0] for u in s) / tot if tot else 0.0)
        s2 = [nu[j] for j in rng.integers(len(nu), size=len(nu))] if nu else []
        fdiffs.append(sum(fnew_by_u[u] - fv1_by_u[u] for u in s2))
    lo, hi = (float(np.quantile(diffs, 0.025)), float(np.quantile(diffs, 0.975))) if uu else (0.0, 0.0)
    flo, fhi = (float(np.quantile(fdiffs, 0.025)), float(np.quantile(fdiffs, 0.975))) if nu else (0.0, 0.0)
    neg_hours = float(neg.sum()) * 0.75 / 3600          # קירוב: חלונות חופפים בחצי -> 0.75 ש' אודיו ייחודי לחלון
    res = {"n_events": n, "n_users": len(uu), "v1_hit": int(hv1.sum()), "new_hit": int(hnew.sum()),
           "new_only": b, "v1_only": c, "mcnemar_p": _mcnemar_one_sided(b, c),
           "recall_diff_ci95": [lo, hi], "clip_any_n": len(anyg), "clip_any_v1": any_v1, "clip_any_new": any_new,
           "neg_windows": int(neg.sum()), "neg_hours_approx": round(neg_hours, 2),
           "v1_false": int(v1[neg].sum()), "new_false": int(new[neg].sum()),
           "false_diff_ci95": [flo, fhi],
           "v1_false_per_h": round(int(v1[neg].sum()) / max(neg_hours, 1e-9), 2),
           "new_false_per_h": round(int(new[neg].sum()) / max(neg_hours, 1e-9), 2),
           "hard_windows": int((neg & hard).sum()), "v1_false_hard": int(v1[neg & hard].sum()),
           "new_false_hard": int(new[neg & hard].sum())}
    why = []
    if n < MIN_EVENTS or len(uu) < MIN_USERS:
        why.append(f"מעט מדי אירועים/מעלים ({n}/{len(uu)})")
    if not (res["mcnemar_p"] < ALPHA and lo > 0):
        why.append(f"שיפור לא מובהק (p={res['mcnemar_p']:.3f}, CI [{lo:+.1%}, {hi:+.1%}])")
    if any_new < any_v1:
        why.append(f"זיהוי כל-חלון נמוך מ-v1 ({any_new} מול {any_v1})")
    if res["new_false"] > res["v1_false"] or res["new_false_hard"] > res["v1_false_hard"]:
        why.append("יותר חלונות שגויים מ-v1")
    res["passed"] = not why
    res["fail_reasons"] = why
    return res


def _log_test_access(cfg):
    prev = []
    if os.path.exists(TEST_LOG):
        prev = [json.loads(line) for line in open(TEST_LOG, encoding="utf-8")]
    with open(TEST_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(cfg, at=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")
    return len(prev)


def train(refine=True):
    import audio_intel as AI
    import acoustic_ai_module as AC
    AI.load()
    AC.load()
    T2 = _setup_trainer()
    idx = index()
    (tr, tr_meta, dtr), (va, va_meta, dva), (te, te_meta, dte) = (load(s, idx) for s in ("train", "val", "test"))
    log(f"חלונות: train {len(tr_meta)} · val {len(va_meta)} · test {len(te_meta)} "
        f"(הושמטו - עמומים/מעבר לתקרה: {dtr} / {dva} / {dte})")
    rng = np.random.default_rng(SEED)
    arrays, card, results = {}, {"categories": {}, "training": {}}, {}
    for cat in CATS:
        if refine:
            m_plain, m_ref = _train_refined(cat, tr, tr_meta, idx, rng, T2)
        else:
            m_plain, m_ref = T2.train_category(cat, tr, tr_meta, idx, rng), None
        if m_plain is None:
            results[cat] = {"skipped": "מעט מדי חיוביים באימון"}
            log(f"{cat}: מעט מדי חיוביים")
            continue
        # בחירה בין MIL רגיל למעודן - על val בלבד
        cands = [("plain", m_plain)] + ([("refined", m_ref)] if m_ref is not None else [])
        best = None
        for name, m in cands:
            op = T2.select_operating_point(cat, m, va, va_meta, idx)
            if op is not None and (best is None or op["hit"] > best[2]["hit"]):
                best = (name, m, op)
        if best is None:
            results[cat] = {"skipped": "אין נקודת עבודה שלא מוסיפה שווא מול v1 ב-val"}
            log(f"{cat}: אין נקודת עבודה")
            continue
        mil, m, op = best
        g = gate(cat, m, op, te, te_meta, idx, T2)
        results[cat] = {"val": {k: op[k] for k in ("hit", "n", "false", "v1_hit", "v1_n", "v1_false")},
                        "test": g, "two_stage": op["t2"] is not None, "mil": mil,
                        "train": {k: m[k] for k in ("n_pos", "n_neg", "n_hard", "n_cand")}}
        log(f"{cat}: [{mil}] val {op['hit']}/{op['n']} (v1 {op['v1_hit']}) · test {g['new_hit']}/{g['n_events']} "
            f"(v1 {g['v1_hit']}, p={g['mcnemar_p']:.3f}) · שווא {g['new_false']} מול {g['v1_false']} · "
            f"{'✅ עבר' if g['passed'] else '❌ ' + '; '.join(g['fail_reasons'])}")
        arrays.update(m["h1"].arrays(f"{cat}__s1"))
        if m["h2"] is not None and op["t2"] is not None:
            arrays.update(m["h2"].arrays(f"{cat}__s2"))
        card["categories"][cat] = {"t1": op["t1"], "t2": op["t2"], "two_stage": op["t2"] is not None,
                                   "platt": op["platt"],
                                   "val": dict(results[cat]["val"], v1_threshold=op["v1_threshold"]),
                                   "test": g, "mil": mil}
    card["data"] = {"rules": C.rules_hash(), "train_windows": len(tr_meta), "val_windows": len(va_meta),
                    "test_windows": len(te_meta), "cap_pos_per_user": CAP_POS_PER_USER,
                    "cap_neg_per_user": CAP_NEG_PER_USER}
    arrays["card"] = np.array([json.dumps(card, ensure_ascii=False)])
    np.savez_compressed(CANDIDATE, **arrays)
    cfg = {"rules": C.rules_hash(), "candidate_sha": _sha(CANDIDATE), "refine": refine,
           "features_test_sha": _sha(os.path.join(OUT, "features_test.npz"))}
    n_prev = _log_test_access(cfg)
    results["_meta"] = dict(cfg, previous_test_runs=n_prev)
    json.dump(results, open(GATE_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    report(results, len(tr_meta), len(va_meta), len(te_meta))
    return results


def report(results, ntr, nva, nte):
    he = {"scream": "צרחה", "distress_voice": "קול מצוקה (צרחה / צעקה / בכי)", "siren": "סירנה", "alarm": "אזעקה",
          "impact": "חבטה / טריקה", "fireworks": "זיקוקים"}
    meta = results.get("_meta", {})
    L = ["# מנוע הסכנה הקולי - אימון מחדש על Freesound (לפני/אחרי)", "",
         f"נוצר ע\"י `research_tools/hv3_freesound.py` ({time.strftime('%Y-%m-%d')}). הנתונים: "
         "`research/freesound_dataset_summary.md`. חלונות של 1.5 ש': "
         f"train {ntr:,} · val {nva:,} · test {nte:,}. כללי תיוג: `{meta.get('rules')}`.", "",
         "**השער (test בלבד, מעלים שלא הופיעו באימון ולא ב-FSD50K):** קטגוריה עוברת רק אם השיפור בזיהוי מובהק "
         f"(McNemar חד-צדדי p<{ALPHA}, וגבול תחתון של CI 95% לפי מעלה > 0), יש לפחות {MIN_EVENTS} אירועים "
         f"ו-{MIN_USERS} מעלים, זיהוי \"כל חלון בקטע\" לא נמוך מ-v1, **וגם** אין יותר חלונות שגויים מ-v1 - "
         "לא על כל השליליים ולא על השליליים הקשים. נקודת העבודה וסוג ה-MIL נבחרו על val בלבד.", ""]
    if meta.get("previous_test_runs"):
        L += [f"> ⚠️ ה-test הזה כבר נמדד {meta['previous_test_runs']} פעמים לפני ההרצה הזו (`test_log.jsonl`). "
              "אם משהו שונה בעקבות תוצאות קודמות - המספרים אופטימיים.", ""]
    L += ["| קטגוריה | אירועים (מעלים) | זוהו v1 → חדש | CI 95% להפרש | p | כל-חלון v1 → חדש | "
          "חלונות שגויים v1 → חדש | לשעה (קירוב) v1 → חדש | בשליליים קשים v1 → חדש | ארכיטקטורה | עבר |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cat in CATS:
        r = results.get(cat, {})
        if "test" not in r:
            L.append(f"| {he[cat]} | — | — | — | — | — | — | — | — | — | ❌ {r.get('skipped', '')} |")
            continue
        g = r["test"]
        n = max(g["n_events"], 1)
        lo, hi = g["recall_diff_ci95"]
        L.append(f"| {he[cat]} | {g['n_events']} ({g['n_users']}) | {g['v1_hit'] / n:.1%} → **{g['new_hit'] / n:.1%}** | "
                 f"[{lo:+.1%}, {hi:+.1%}] | {g['mcnemar_p']:.3f} | {g['clip_any_v1']} → {g['clip_any_new']} "
                 f"(מתוך {g['clip_any_n']}) | {g['v1_false']} → {g['new_false']} (מתוך {g['neg_windows']:,}) | "
                 f"{g['v1_false_per_h']} → {g['new_false_per_h']} | "
                 f"{g['v1_false_hard']} → {g['new_false_hard']} (מתוך {g['hard_windows']:,}) | "
                 f"{'שני שלבים' if r['two_stage'] else 'שלב 1'}, MIL {r.get('mil')} | "
                 f"{'✅' if g['passed'] else '❌ ' + '; '.join(g['fail_reasons'])} |")
    L += ["", "## מגבלות", "",
          "- התיוג לפי תגיות המעלים ב-Freesound (חלש), אחרי כללי ניקוי (`freesound_collect.py audit`). "
          "המודל החדש אומן על אותו סוג תיוג שעליו הוא נבדק, ו-v1 לא - יתרון \"בית\" שהשער לא מבטל. "
          "מומלץ להאזין למדגם של אירועי ה-test (למשל 30 לכל קטגוריה) לפני קידום.",
          "- Freesound הוא לא טלפון בכיס ברחוב. \"לשעה\" כאן הוא קירוב על שליליי Freesound בלבד - לא התראות שווא בשטח.",
          "- בחירת חלונות ה-MIL ב-test לפי zero-shot של YAMNet; בצרחה ובזיקוקים v1 **הוא** ה-zero-shot - "
          "ההגדרה מטה לטובת v1 (שמרני), ולכן מדווח גם זיהוי \"כל חלון בקטע\".",
          "- ה-preview של Freesound הוא mp3 דחוס - דומה יותר לטלפון מהקובץ המקורי, אבל לא זהה.",
          "- מודל שעבר כאן לא נבדק מחדש על מבחני v2 (FSD50K eval, SONYC, TAU) - הנתונים האלה לא במחשב הזה.",
          "- צרחה / קול מצוקה: כלל \"צעקות חוזרות\" (models/shout_sequence.json) מכויל על הציונים של v1. "
          "קידום שלהן דורש כיול מחדש של הכלל (research_tools/shout_sequence.py)."]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


def _next_version(v):
    try:
        base, minor = v.rsplit(".", 1)
        return f"{base}.{int(minor) + 1}"
    except ValueError:
        return v + ".1"


def promote(allow_voice=False):
    """מיזוג הקטגוריות שעברו את השער לתוך המודל שבאפליקציה. שאר הקטגוריות והמודל הקיים - ללא שינוי."""
    res = json.load(open(GATE_JSON, encoding="utf-8"))
    meta = res.pop("_meta", {})
    if meta.get("candidate_sha") != _sha(CANDIDATE):
        sys.exit("model_candidate.npz לא תואם ל-gate.json (הרצת train אחרת?) - לא מקדמים")
    passed = [c for c, r in res.items() if r.get("test", {}).get("passed")]
    blocked = [c for c in passed if c in VOICE_CATS and not allow_voice]
    if blocked:
        log(f"לא מקודמות {blocked}: כלל 'צעקות חוזרות' (models/shout_sequence.json) מכויל על ציוני v1 "
            "(t_low על ראש המצוקה, s_low על הסתברות YAMNet). לכייל מחדש ואז להריץ עם --allow-voice")
        passed = [c for c in passed if c not in blocked]
    if not passed:
        log("אף קטגוריה לא עברה את השער - המודל באפליקציה לא משתנה")
        return []
    old = np.load(SHIPPED, allow_pickle=False)
    new = np.load(CANDIDATE, allow_pickle=False)
    card = json.loads(str(old["card"][0]))
    ncard = json.loads(str(new["card"][0]))
    arrays = {k: old[k] for k in old.files if k != "card"}
    for cat in passed:
        for k in [k for k in arrays if k.startswith(f"{cat}__")]:
            del arrays[k]                                  # בלי שאריות (למשל s2 ישן כשהחדש שלב 1)
        arrays.update({k: new[k] for k in new.files if k.startswith(f"{cat}__")})
        c = ncard["categories"][cat]
        assert {"t1", "t2", "two_stage", "platt"} <= set(c) and len(c["platt"]) == 2
        assert f"{cat}__s1__w" in arrays and (not c["two_stage"] or f"{cat}__s2__w" in arrays)
        card["categories"][cat] = c
    old_version = card["version"]
    card["version"] = _next_version(old_version)
    card["created"] = time.strftime("%Y-%m-%d")
    card.setdefault("freesound_update", {})
    card["freesound_update"] = {"categories": passed, "accepted_by": "research/hazard_v3_freesound.md",
                                "rules": meta.get("rules"), "candidate_sha": meta.get("candidate_sha"),
                                "data": "Freesound CC0 / CC BY, uploader-disjoint, no FSD50K clips"}
    arrays["card"] = np.array([json.dumps(card, ensure_ascii=False)])
    backup = SHIPPED.replace(".npz", f"_{old_version.replace('hazard_audio_', '')}_backup.npz")
    if not os.path.exists(backup):
        import shutil
        shutil.copy2(SHIPPED, backup)
    np.savez_compressed(SHIPPED, **arrays)
    log(f"עודכן {SHIPPED}: {passed} ({old_version} -> {card['version']}, גיבוי: {backup})")
    log("עכשיו: python tools/build_hazard_web.py ; python tools/build_android_hazard_assets.py ; "
        "pytest tests/test_pwa_hazard_parity.py (המודל ב-web/Android לא מתעדכן לבד)")
    return passed


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "features":
        features()
    elif cmd == "train":
        train(refine="--no-refine" not in sys.argv)
    elif cmd == "promote":
        promote(allow_voice="--allow-voice" in sys.argv)
    else:
        print(__doc__)

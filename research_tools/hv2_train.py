# -*- coding: utf-8 -*-
"""
hv2_train.py - אימון מנוע הסכנה v2 ובחירת נקודת עבודה לכל קטגוריה (על validation בלבד).

ארכיטקטורה (hazard_features.py):
- שלב 1 - מועמד רגיש: רגרסיה לוגיסטית על ממוצע+מקסימום embeddings של YAMNet בחלון של 1.5 ש'.
- שלב 2 - אימות הקשרי: רגרסיה לוגיסטית על [ציון שלב 1, הסתברויות YAMNet למחלקות מבלבלות, מאפיינים ספקטרליים
  רב-רזולוציה], מאומנת רק על חלונות שעברו את שלב 1 (המועמדים) - שם נמצאים הדומים הקשים.
- כל קטגוריה בנפרד, עם שליליים קשים במשקל כפול פי 3 (זיקוקים/תופים מול ירי, צופר מול אזעקה, קהל/צחוק מול צעקה...).

תיוג חלונות: ירי (gunshot_fit) - לפי חותמות הזמן של היריות; שאר המקורות (תיוג ברמת קטע) - שני החלונות עם ציון
zero-shot הגבוה ביותר בקטע (MIL), ושאר חלונות הקטע מושמטים (לא שליליים).

בחירת נקודת עבודה (validation בלבד, נקי - בלי העשרה): לכל קטגוריה, מבין "שלב 1 בלבד" ו"שני שלבים" ומבין ספים
- מקסימום זיהוי אירועים, בתנאי ששיעור החלונות השגויים לא עולה על v1 על **אותם חלונות** בשתי סביבות:
  "פנים" (FSD50K val - מוזיקה, דיבור, תופים, בית) ו"רחוב" (SONYC val). כך כל שיפור בזיהוי בא מהבחנה טובה יותר,
  לא מהורדת סף.

שימוש: python -I research_tools/hv2_train.py   -> research_data/hv2/model_v2_candidate.npz + research/hazard_v2_training.md
"""
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research_tools import hv2_data as D  # noqa: E402
from research_tools import hv2_features as F  # noqa: E402

CANDIDATE = os.path.join(F.OUT, "model_v2_candidate.npz")
REPORT = os.path.join(ROOT, "research", "hazard_v2_training.md")
TARGETS = D.TARGETS
EXCL = {"explosion": {"gunshot", "fireworks"}, "alarm": {"siren"}, "scream": {"distress_voice"},
        "gunshot": {"explosion"}, "fireworks": {"gunshot", "explosion"}, "siren": set(), "glass_break": set(),
        "impact": {"door_knock"}, "distress_voice": {"crowd_cheer", "children"}}
HARD = {"gunshot": {"fireworks", "drums_music", "door_knock", "thunder", "construction", "impact"},
        "explosion": {"fireworks", "thunder", "drums_music", "construction"},
        "fireworks": {"drums_music", "thunder", "impact", "door_knock"},
        "glass_break": {"door_knock", "impact", "construction", "music"},
        "siren": {"horn", "music", "alarm"}, "alarm": {"horn", "music", "siren"},
        "scream": {"laughter", "crowd_cheer", "children", "music", "speech"},
        "impact": {"door_knock", "construction", "drums_music"},
        "distress_voice": {"laughter", "crowd_cheer", "children", "music", "speech"}}
HARD_WEIGHT = 3.0
MAX_NEG = 80_000
MIL_TOP = 2
SEED = 7


def log(m):
    print(m, flush=True)


def _manifest_index():
    idx = {}
    for r in D.load_manifest():
        idx[r["id"]] = r
    return idx


def window_labels(meta, idx, cat):
    """-> y (1 חיובי / 0 שלילי / -1 מושמט), hard (bool), clip (מזהה+וריאנט), env."""
    ci = TARGETS.index(cat)
    n = len(meta)
    y = np.zeros(n, np.int8)
    hard = np.zeros(n, bool)
    for i, m in enumerate(meta):
        r = idx.get(m["id"])
        tags = set(r["tags"]) if r else set()
        if cat in tags:
            y[i] = -2                                 # מועמד לחיובי (נבחר למטה)
        elif tags & EXCL.get(cat, set()):
            y[i] = -1
        hard[i] = bool(tags & HARD.get(cat, set()))
    return y, hard, ci


def resolve_positives(y, meta, zs, idx, cat, ci):
    """מועמדים -> חיוביים: ירי לפי חותמת זמן; אחרים - MIL (שני החלונות המובילים בכל קטע/וריאנט)."""
    groups = {}
    for i in np.flatnonzero(y == -2):
        m = meta[i]
        r = idx[m["id"]]
        if r["source"] == "gunshot_fit" and cat == "gunshot":
            at = r.get("gunshot_at_s") or []
            y[i] = 1 if any(m["start"] - 0.05 <= a <= m["start"] + 1.45 for a in at) else -1
        else:
            groups.setdefault((m["id"], m["variant"]), []).append(i)
    for ii in groups.values():
        ii = sorted(ii, key=lambda i: -zs[i, ci])
        for k, i in enumerate(ii):
            y[i] = 1 if k < MIL_TOP else -1
    return y


def env_of(meta, idx):
    out = []
    for m in meta:
        r = idx.get(m["id"])
        if r is None:
            out.append("unknown")
        elif r["source"] == "fsd50k":
            out.append("indoor_mixed")
        elif r["source"] == "sonyc_ust":
            out.append("street")
        else:
            out.append(r["env"])
    return np.array(out)


class Head:
    """רגרסיה לוגיסטית מתוקננת (FastLogistic מאימון v1) - שמירה פשוטה (mean/std/w/b)."""

    def __init__(self, epochs=150, l2=1e-3):
        from research_tools import hazard_audio_train as T
        self.m = T.FastLogistic(l2=l2, epochs=epochs)

    def fit(self, X, y, w):
        Xw, yw = _expand_weights(X, y, w)
        self.m.fit(Xw, yw)
        return self

    def logit(self, X):
        X = (np.asarray(X, np.float32) - self.m.mean) / self.m.std
        return X @ self.m.w + self.m.b

    def arrays(self, prefix):
        return {f"{prefix}__mean": self.m.mean.astype(np.float32), f"{prefix}__std": self.m.std.astype(np.float32),
                f"{prefix}__w": self.m.w.astype(np.float32), f"{prefix}__b": np.array([self.m.b], np.float32)}


def _expand_weights(X, y, w):
    """משקל 3 לשליליים קשים = שכפול (FastLogistic לא מקבל משקלים לכל דוגמה)."""
    rep = np.where(w > 1.5, int(HARD_WEIGHT), 1)
    idx = np.repeat(np.arange(len(y)), rep)
    return X[idx], y[idx]


def train_category(cat, tr, tr_meta, idx, rng):
    y, hard, ci = window_labels(tr_meta, idx, cat)
    y = resolve_positives(y, tr_meta, tr["zs"], idx, cat, ci)
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    if len(pos) < 20:
        return None
    neg_hard, neg_easy = neg[hard[neg]], neg[~hard[neg]]
    keep_easy = rng.choice(neg_easy, size=min(len(neg_easy), max(0, MAX_NEG - len(neg_hard))), replace=False)
    sel = np.concatenate([pos, neg_hard, keep_easy])
    X1 = tr["s1"][sel].astype(np.float32)
    yy = (y[sel] == 1).astype(np.float32)
    ww = np.where(hard[sel] & (yy == 0), HARD_WEIGHT, 1.0)
    h1 = Head().fit(X1, yy, ww)
    l1 = h1.logit(tr["s1"].astype(np.float32))
    # שלב 2 על המועמדים: סף שתופס 97% מהחלונות החיוביים באימון
    t_cand = float(np.quantile(l1[pos], 0.03))
    cand = np.flatnonzero((l1 >= t_cand) & (y >= 0))
    V = verifier_matrix(l1, tr)
    yc = (y[cand] == 1).astype(np.float32)
    h2 = None
    if yc.sum() >= 20 and (1 - yc).sum() >= 20:
        h2 = Head().fit(V[cand], yc, np.where(hard[cand] & (yc == 0), HARD_WEIGHT, 1.0))
    return {"h1": h1, "h2": h2, "t_cand": t_cand, "n_pos": int(len(pos)), "n_neg": int(len(sel) - len(pos)),
            "n_hard": int(len(neg_hard)), "n_cand": int(len(cand))}


def verifier_matrix(l1, d):
    return np.concatenate([l1[:, None].astype(np.float32), d["cur"].astype(np.float32), d["spec"].astype(np.float32)],
                          axis=1)


def clip_events(y, meta, det):
    """זיהוי ברמת אירוע: קטע/וריאנט חיובי זוהה אם אחד מחלונותיו החיוביים זוהה."""
    groups = {}
    for i in np.flatnonzero(y == 1):
        groups.setdefault((meta[i]["id"], meta[i]["variant"]), []).append(i)
    if not groups:
        return 0, 0
    hit = sum(bool(det[ii].any()) for ii in groups.values())
    return hit, len(groups)


def select_operating_point(cat, model, va, va_meta, idx):
    """על validation: מקסימום זיהוי אירועים בתנאי ששיעור השווא בפנים וברחוב ≤ v1 על אותם חלונות."""
    import audio_intel as AI
    import acoustic_ai_module as AC
    y, _, ci = window_labels(va_meta, idx, cat)
    y = resolve_positives(y, va_meta, va["zs"], idx, cat, ci)
    env = env_of(va_meta, idx)
    v1s = va["v1"][:, ci]
    v1_thr = AC.threshold() if cat == "distress_voice" else float(AI._STATE["card"]["categories"][cat]["threshold"])
    v1_det = v1s >= v1_thr
    neg = y == 0
    lim = {}
    for e in ("indoor_mixed", "street"):
        m = neg & (env == e)
        lim[e] = (int(v1_det[m].sum()), int(m.sum()))
    hit1, n1 = clip_events(y, va_meta, v1_det)
    l1 = model["h1"].logit(va["s1"].astype(np.float32))
    l2 = model["h2"].logit(verifier_matrix(l1, va)) if model["h2"] is not None else None
    pos_l1 = l1[y == 1]
    best = None
    t1_grid = np.unique(np.quantile(pos_l1, np.linspace(0.0, 0.7, 36))) if len(pos_l1) else []
    for t1 in t1_grid:
        options = [(None, l1 >= t1)]
        if l2 is not None:
            c = l1 >= t1
            if c.any():
                for t2 in np.unique(np.quantile(l2[c], np.linspace(0.0, 0.98, 50))):
                    options.append((float(t2), c & (l2 >= t2)))
        for t2, det in options:
            ok = True
            fr = {}
            for e, (k1, n) in lim.items():
                m = neg & (env == e)
                k = int(det[m].sum())
                fr[e] = (k, n)
                if n and k > k1:          # לא יותר חלונות שגויים מ-v1 על אותם חלונות
                    ok = False
            if not ok:
                continue
            hit, n = clip_events(y, va_meta, det)
            key = (hit, -sum(k for k, _ in fr.values()), t2 is None)
            if best is None or key > best["key"]:
                best = {"key": key, "t1": float(t1), "t2": t2, "hit": hit, "n": n, "false": fr}
    if best is None:
        return None
    best.pop("key")
    best.update(v1_hit=hit1, v1_n=n1, v1_false=lim, v1_threshold=v1_thr)
    # כיול Platt על הציון הסופי (חלונות שעברו את שלב 1) - "ביטחון בזיהוי"
    from research_tools import hazard_audio_train as T
    final = l2 if best["t2"] is not None else l1
    m = (l1 >= best["t1"]) & (y >= 0)
    if m.sum() >= 20 and (y[m] == 1).any() and (y[m] == 0).any():
        a, b = T.platt(final[m], (y[m] == 1).astype(float))
        best["platt"] = [float(a), float(b)]
    else:
        best["platt"] = [1.0, 0.0]
    return best


def run():
    import audio_intel as AI
    import acoustic_ai_module as AC
    AI.load()
    AC.load()
    t0 = time.time()
    idx = _manifest_index()
    tr, tr_meta = F.load_split("train")
    va, va_meta = F.load_split("val")
    log(f"train: {len(tr_meta)} חלונות; val: {len(va_meta)} חלונות ({time.time() - t0:.0f}s)")
    rng = np.random.default_rng(SEED)
    arrays, card = {}, {"version": "hazard_audio_v2_candidate", "created": time.strftime("%Y-%m-%d"),
                        "window_s": 1.5, "categories": {}, "training": {}}
    for cat in TARGETS:
        m = train_category(cat, tr, tr_meta, idx, rng)
        if m is None:
            log(f"{cat}: מעט מדי חיוביים - מדלגים")
            continue
        op = select_operating_point(cat, m, va, va_meta, idx)
        if op is None:
            log(f"{cat}: אין נקודת עבודה שעומדת בתנאי השווא של v1 - מדלגים")
            continue
        arrays.update(m["h1"].arrays(f"{cat}__s1"))
        if m["h2"] is not None:
            arrays.update(m["h2"].arrays(f"{cat}__s2"))
        card["categories"][cat] = {"t1": op["t1"], "t2": op["t2"], "two_stage": op["t2"] is not None,
                                   "platt": op["platt"], "val": {k: op[k] for k in ("hit", "n", "false", "v1_hit", "v1_n",
                                                                                    "v1_false", "v1_threshold")}}
        card["training"][cat] = {k: m[k] for k in ("n_pos", "n_neg", "n_hard", "n_cand", "t_cand")}
        log(f"{cat}: val אירועים {op['hit']}/{op['n']} (v1 {op['v1_hit']}/{op['v1_n']}); "
            f"{'שני שלבים' if op['t2'] is not None else 'שלב 1'}; שווא {op['false']} מול v1 {op['v1_false']} "
            f"({time.time() - t0:.0f}s)")
    from hazard_features import CURATED
    card["curated"] = list(CURATED)
    card["data"] = {"train_windows": len(tr_meta), "val_windows": len(va_meta),
                    "licenses": "CC0 / CC BY only (FSD50K per-clip, SONYC-UST, Florida Tech gunshots)"}
    arrays["card"] = np.array([json.dumps(card, ensure_ascii=False)])
    np.savez_compressed(CANDIDATE, **arrays)
    report(card)
    log(f"מודל מועמד: {CANDIDATE}")
    return card


def report(card):
    L = ["# מנוע סכנה v2 - אימון ובחירת נקודת עבודה (validation)", "",
         f"נוצר ע\"י `research_tools/hv2_train.py` ({card['created']}). חלונות: אימון {card['data']['train_windows']}, "
         f"validation {card['data']['val_windows']}. רישוי נתוני האימון: {card['data']['licenses']}.", "",
         "התנאי לכל קטגוריה: לא יותר חלונות שגויים מ-v1 על אותם חלונות - בפנים (FSD50K val) וברחוב (SONYC val).", "",
         "| קטגוריה | ארכיטקטורה | אירועים שזוהו v2 | v1 | שווא פנים v2 / v1 | שווא רחוב v2 / v1 |", "|---|---|---|---|---|---|"]
    for cat, c in card["categories"].items():
        v = c["val"]
        fr = lambda d, e: f"{d[e][0]}/{d[e][1]}" if e in d else "—"  # noqa: E731
        L.append(f"| {cat} | {'שני שלבים' if c['two_stage'] else 'שלב 1'} | {v['hit']}/{v['n']} | {v['v1_hit']}/{v['v1_n']} | "
                 f"{fr(v['false'], 'indoor_mixed')} / {fr(v['v1_false'], 'indoor_mixed')} | "
                 f"{fr(v['false'], 'street')} / {fr(v['v1_false'], 'street')} |")
    L += ["", "**זה validation - לא הוכחה.** המבחן על נתונים שלא נגעו בהם: `research/hazard_v2_benchmark.md`."]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run()

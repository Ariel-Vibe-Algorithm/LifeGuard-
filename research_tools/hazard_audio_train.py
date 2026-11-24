# -*- coding: utf-8 -*-
"""
hazard_audio_train.py - אימון ומדידה של מנוע אירועי הסכנה הקוליים (audio_intel.py) על FSD50K.

לכל קטגוריה (ירי, פיצוץ, זיקוקים, שבירת זכוכית, סירנה, אזעקה, צרחה, חבטה) משווים שני מועמדים:
- **zero_shot** (champion ברירת מחדל): ההסתברות ש-YAMNet עצמו נותן למחלקות AudioSet המתאימות. בלי אימון.
- **head** (challenger): רגרסיה לוגיסטית קטנה מעל ה-embeddings של YAMNet (numpy בלבד, ~1K משקלים).

הפרדה בין אימון למדידה (בלי דליפה):
- **dev** של FSD50K: אימון + בחירת סף + בחירת champion + כיול (Platt), עם 5 קבוצות לפי מעלה ב-Freesound
  (out-of-fold - כל ציון חושב ע"י מודל שלא ראה את המעלה הזה).
- **eval** של FSD50K: מדידה בלבד, פעם אחת, אחרי שהכל נקבע. אין חפיפת מעלים בין dev ל-eval (נבדק בריצה).

מה נמדד על eval: AUC לפי קטע, recall בסף שנקבע על dev, התראות שווא לשעת אודיו שלילי (רווח Garwood),
ECE של ההסתברות המכוילת. "שלילי" = כל קטע ב-FSD50K שאין בו את הקטגוריה (כולל מוזיקה, דיבור, רחוב,
וגם סכנות אחרות - למשל זיקוקים הם שליליים לירי).

מגבלות (גם בדו"ח): FSD50K הוא Freesound - לא טלפון בכיס ברחוב; תוויות ברמת קטע (חלשות); ההסתברות
המכוילת תלויה בשכיחות ב-FSD50K ואינה "הסתברות לסכנה" בעולם.

שימוש:
  python -I research_tools/hazard_audio_train.py train          # -> models/hazard_audio.npz + research/hazard_audio_v1.md
  python -I research_tools/hazard_audio_train.py gate OLD NEW   # שער רגרסיה: NEW לא נופל מ-OLD
"""

import argparse
import csv
import datetime as _dt
import json
import math
import os
import sys
import pickle
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from research_tools import yamnet_detector as Y  # noqa: E402

FSD = os.path.join(ROOT, "research", "samples", "fsd50k")
OUT_MODEL = os.path.join(ROOT, "models", "hazard_audio.npz")
OUT_REPORT = os.path.join(ROOT, "research", "hazard_audio_v1.md")
CKPT_DIR = os.path.join(FSD, "hazard_ckpt")      # נקודת שמירה לכל קטגוריה (מחוץ למאגר) - ריצה שנקטעה ממשיכה
VERSION = "hazard_audio_v1"
HOP = Y.HOP_SECONDS
BUDGET_PER_HOUR = 1.0           # נקודת העבודה: ~1 התראת שווא לשעת אודיו שלילי (על dev)
MERGE_GAP_FRAMES = 2            # כמו acoustic_eval: פער < ~1 ש' מתאחד לאותה התראה
NEG_FRAMES = 40_000            # לינארי עם 1024 תכונות - 40K שליליים מספיקים (וזה פי ~4 מהר יותר)
TOPK_POS_FRAMES = 3
FOLDS = 5

# קטגוריה: תוויות FSD50K חיוביות, תוויות שמוחרגות (לא חיובי ולא שלילי - עמימות), מחלקות YAMNet ל-zero-shot,
# חומרה מוקדמת (מדיניות, לא נמדד), ותיאור.
CATEGORIES = {
    "gunshot":     dict(pos={"Gunshot_and_gunfire"}, excl=set(),
                        yam=("Gunshot, gunfire", "Machine gun", "Fusillade", "Artillery fire", "Cap gun"),
                        severity="high", he="ירי"),
    "explosion":   dict(pos={"Explosion", "Boom"}, excl={"Gunshot_and_gunfire", "Fireworks"},
                        yam=("Explosion", "Boom"), severity="high", he="פיצוץ"),
    "fireworks":   dict(pos={"Fireworks"}, excl=set(), yam=("Fireworks", "Firecracker"),
                        severity="low", he="זיקוקים / נפצים"),
    "glass_break": dict(pos={"Shatter"}, excl={"Glass"}, yam=("Shatter", "Breaking"),
                        severity="medium", he="שבירת זכוכית"),
    "siren":       dict(pos={"Siren"}, excl=set(),
                        yam=("Siren", "Civil defense siren", "Police car (siren)", "Ambulance (siren)",
                             "Fire engine, fire truck (siren)", "Emergency vehicle"),
                        severity="info", he="סירנה"),
    "alarm":       dict(pos={"Alarm"}, excl={"Siren", "Telephone", "Ringtone"},
                        yam=("Smoke detector, smoke alarm", "Fire alarm", "Alarm", "Car alarm", "Buzzer"),
                        severity="medium", he="אזעקה (אש / עשן / רכב)"),
    "scream":      dict(pos={"Screaming"}, excl={"Shout", "Yell"}, yam=("Screaming",),
                        severity="medium", he="צרחה"),
    "impact":      dict(pos={"Slam", "Thump_and_thud"}, excl=set(), yam=("Slam", "Thump, thud", "Smash, crash", "Bang"),
                        severity="low", he="חבטה / טריקה"),
}
# אין תוויות ב-FSD50K -> zero-shot בלבד, "לא מאומת": מדווח כחשד, לא מוסיף לניקוד הסיכון
UNVALIDATED = {
    "braking": dict(yam=("Skidding", "Tire squeal"), severity="medium", he="בלימת חירום / חריקת צמיגים"),
}


# ------------------------------------------------------------------ נתונים
def load_split(split):
    d = np.load(os.path.join(FSD, f"{split}_features.npz"), allow_pickle=False)
    labels = {}
    with open(os.path.join(FSD, "meta", "FSD50K.ground_truth", f"{split}.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            labels[r["fname"]] = set(r["labels"].split(","))
    info = json.load(open(os.path.join(FSD, "meta", "FSD50K.metadata", f"{split}_clips_info_FSD50K.json"),
                          encoding="utf-8"))
    fnames = [str(f) for f in d["fnames"]]
    return {"fnames": fnames, "offsets": d["offsets"], "emb": d["emb"], "seconds": d["seconds"],
            "labels": [labels[f] for f in fnames], "uploader": [info[f]["uploader"] for f in fnames]}


def yamnet_dense():
    """שכבת הסיווג של YAMNet (521x1024 + bias) מתוך ה-TFLite - כדי לחשב ציוני מחלקות מה-embeddings במטמון."""
    m = Y.Yamnet()
    W = b = None
    for t in m.it.get_tensor_details():
        shape = list(t["shape"])
        if shape == [521, 1024]:
            W = m.it.get_tensor(t["index"]).astype(np.float32)
        elif shape == [521] and b is None:
            b = m.it.get_tensor(t["index"]).astype(np.float32)
    if W is None or b is None:
        raise RuntimeError("לא נמצאה שכבת הסיווג של YAMNet במודל")
    return W, b


def verify_dense(W, b):
    """בודק שהשכבה שחולצה משחזרת את הציונים של YAMNet בפועל (על אודיו סינתטי)."""
    rng = np.random.default_rng(0)
    x = (rng.standard_normal(16000 * 3) * 0.05).astype(np.float32)
    x[16000:17000] += np.sin(np.arange(1000) * 0.3).astype(np.float32)
    scores, emb = Y.Yamnet().analyze(x, 16000)
    rec = Y._sigmoid(emb @ W.T + b)
    err = float(np.abs(rec - scores).max())
    if err > 1e-3:
        raise RuntimeError(f"שחזור ציוני YAMNet נכשל (שגיאה {err})")
    return err


def class_idx(names):
    all_names = Y.class_names()
    idx = [all_names.index(n) for n in names if n in all_names]
    missing = [n for n in names if n not in all_names]
    if missing:
        raise KeyError(f"מחלקות YAMNet לא קיימות: {missing}")
    return idx


def zero_shot_frames(emb, W, b, idx, chunk=50_000):
    out = np.empty(len(emb), np.float32)
    Wi, bi = W[idx].T, b[idx]
    for s in range(0, len(emb), chunk):
        out[s:s + chunk] = Y._sigmoid(emb[s:s + chunk].astype(np.float32) @ Wi + bi).max(axis=1)
    return out


def roles(data, cat):
    """1 = חיובי, 0 = שלילי, -1 = מוחרג (עמום)."""
    c = CATEGORIES[cat]
    r = np.zeros(len(data["fnames"]), np.int8)
    for i, labs in enumerate(data["labels"]):
        if labs & c["pos"]:
            r[i] = 1
        elif labs & c["excl"]:
            r[i] = -1
    return r


# ------------------------------------------------------------------ מטריקות
def clip_max(frame_scores, offsets):
    return np.maximum.reduceat(frame_scores, offsets[:-1]) if len(frame_scores) else np.zeros(0)


def fa_events(frame_scores, offsets, clip_mask, thr):
    """מספר התראות (רצפים מעל הסף, פער קצר מתאחד) בקטעים שבמסכה."""
    hit = np.flatnonzero(frame_scores >= thr)
    if len(hit) == 0:
        return 0
    clip = np.searchsorted(offsets, hit, side="right") - 1
    keep = clip_mask[clip]
    hit, clip = hit[keep], clip[keep]
    if len(hit) == 0:
        return 0
    new = np.ones(len(hit), bool)
    new[1:] = (clip[1:] != clip[:-1]) | (np.diff(hit) > MERGE_GAP_FRAMES)
    return int(new.sum())


def neg_hours(offsets, clip_mask):
    return float((np.diff(offsets)[clip_mask]).sum() * HOP / 3600.0)


def threshold_for_budget(frame_scores, offsets, neg_mask, budget, cmax):
    """הסף הנמוך ביותר שעומד בתקציב התראות השווא (חיפוש על ערכי שיא-קטע של השליליים)."""
    hours = neg_hours(offsets, neg_mask)
    allowed = budget * hours
    cands = np.unique(cmax[neg_mask])
    lo, hi = 0, len(cands) - 1
    best = float(cands[-1]) + 1e-6
    while lo <= hi:                                  # מספר ההתראות כמעט מונוטוני בסף
        mid = (lo + hi) // 2
        t = float(cands[mid]) + 1e-9
        if fa_events(frame_scores, offsets, neg_mask, t) <= allowed:
            best, hi = t, mid - 1
        else:
            lo = mid + 1
    return best


def auc(pos, neg):
    if len(pos) == 0 or len(neg) == 0:
        return None
    s = np.concatenate([pos, neg])
    order = s.argsort(kind="mergesort")
    ranks = np.empty(len(s)); ranks[order] = np.arange(1, len(s) + 1)
    # טיפול בתיקו: דירוג ממוצע
    _, inv, cnt = np.unique(s, return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt); avg = csum - (cnt - 1) / 2.0
    ranks = avg[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def garwood(k, hours):
    """רווח סמך פואסוני מדויק 95% לקצב לשעה (בלי scipy: חיפוש בינארי על ה-CDF)."""
    def cdf(k_, lam):
        term = math.exp(-lam); s = term
        for i in range(1, k_ + 1):
            term *= lam / i; s += term
        return s

    def solve(target, k_, upper):
        lo, hi = 0.0, max(10.0, 5.0 * (k_ + 5))
        for _ in range(100):
            mid = (lo + hi) / 2
            v = cdf(k_, mid) if upper else 1 - cdf(k_ - 1, mid)
            if (v > target) == upper:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    lo = 0.0 if k == 0 else solve(0.025, k, upper=False)
    hi = solve(0.025, k, upper=True)
    return (lo / hours, hi / hours)


def platt(scores, y, iters=500):
    """כיול p = sigmoid(a*s + b). יעדים מוחלקים (Platt 1999): t+ = (N+ + 1)/(N+ + 2), t- = 1/(N- + 2), ואופטימיזציה
    יציבה (L-BFGS עם עונש קטן על השיפוע). קודם: Newton בלי חיפוש קו - כשהנתונים כמעט מופרדים הצעדים התבדרו
    (שיפוע ~1e7 - "ביטחון" שקופץ מ-0 ל-1 בנקודה אחת; נמצא בבדיקות התאימות של אנדרואיד)."""
    from scipy.optimize import minimize
    s = np.asarray(scores, np.float64); y = np.asarray(y, np.float64)
    n_pos, n_neg = float((y > 0.5).sum()), float((y <= 0.5).sum())
    t = np.where(y > 0.5, (n_pos + 1) / (n_pos + 2), 1 / (n_neg + 2))
    mu, sd = float(s.mean()), float(s.std() + 1e-9)
    z = (s - mu) / sd

    def nll(v):
        a, b = v
        u = a * z + b
        loss = np.sum(np.logaddexp(0, u) - t * u) + 1e-3 * a * a
        p = 1 / (1 + np.exp(-np.clip(u, -60, 60)))
        return loss, np.array([np.sum((p - t) * z) + 2e-3 * a, np.sum(p - t)])
    r = minimize(nll, np.array([1.0, 0.0]), jac=True, method="L-BFGS-B", options={"maxiter": iters})
    a, b = r.x
    return float(a / sd), float(b - a * mu / sd)


def ece(prob, y, bins=10):
    prob = np.asarray(prob); y = np.asarray(y)
    e = 0.0
    for i in range(bins):
        m = (prob >= i / bins) & (prob < (i + 1) / bins if i < bins - 1 else prob <= 1)
        if m.any():
            e += m.mean() * abs(prob[m].mean() - y[m].mean())
    return float(e)


def fa_table(frame_scores, offsets, neg_mask, cmax, points=24):
    """טבלת "סף -> התראות שווא לשעה" (על dev) - להסבר בזמן ריצה: "בציון כזה, כמה שווא לשעה נמדדו"."""
    hours = neg_hours(offsets, neg_mask)
    qs = np.quantile(cmax[neg_mask], np.linspace(0.90, 0.9999, points))
    hi = float(cmax[neg_mask].max()) + 1e-6
    th = np.unique(np.concatenate([qs, [hi]]))
    return [(float(t), fa_events(frame_scores, offsets, neg_mask, float(t)) / hours) for t in th]


# ------------------------------------------------------------------ אימון
class FastLogistic(Y.LogisticModel):
    """אותו אלגוריתם בדיוק כמו Y.LogisticModel (משקלי מחלקות, L2, Adam), אבל float32 לאורך כל הדרך.
    במקור ה-sigmoid מחזיר float64 ו-numpy ממיר את כל מטריצת האימון ל-float64 בכל איטרציה - פי ~33 איטי
    יותר (נמדד). השמירה והטעינה זהות (mean/std/w/b)."""

    def fit(self, X, y, sample_groups=None):
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        if y.min() == y.max():
            raise ValueError("צריך גם דוגמאות חיוביות וגם שליליות")
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-6
        Z = (X - self.mean) / self.std
        pos = float(y.sum())
        wts = np.where(y > 0, len(y) / (2 * pos), len(y) / (2 * (len(y) - pos))).astype(np.float32)
        wts /= wts.sum()
        w = np.zeros(Z.shape[1], dtype=np.float32)
        b = np.float32(0.0)
        m_w = np.zeros_like(w); v_w = np.zeros_like(w); m_b = v_b = 0.0
        b1, b2, eps = 0.9, 0.999, 1e-8
        for t in range(1, self.epochs + 1):
            p = (1.0 / (1.0 + np.exp(-np.clip(Z @ w + b, -60, 60)))).astype(np.float32)
            g = (wts * (p - y)).astype(np.float32)
            gw = Z.T @ g + np.float32(self.l2) * w
            gb = float(g.sum())
            m_w = b1 * m_w + (1 - b1) * gw; v_w = b2 * v_w + (1 - b2) * gw * gw
            m_b = b1 * m_b + (1 - b1) * gb; v_b = b2 * v_b + (1 - b2) * gb * gb
            w = (w - self.lr * 0.02 * (m_w / (1 - b1 ** t)) / (np.sqrt(v_w / (1 - b2 ** t)) + eps)).astype(np.float32)
            b = np.float32(b - self.lr * 0.02 * (m_b / (1 - b1 ** t)) / (math.sqrt(v_b / (1 - b2 ** t)) + eps))
        self.w, self.b = w, float(b)
        self.info = {"n": int(len(y)), "n_pos": int(pos), "l2": self.l2, "epochs": self.epochs}
        return self


def train_head(emb, offsets, role, zs_frames, train_clip_mask, rng):
    """positives = top-k מסגרות (לפי zero-shot) מכל קטע חיובי (תוויות חלשות -> MIL פשוט)."""
    pos_rows, neg_clips = [], []
    for i in np.flatnonzero(train_clip_mask):
        a, b_ = offsets[i], offsets[i + 1]
        if role[i] == 1:
            k = min(TOPK_POS_FRAMES, b_ - a)
            pos_rows.extend((a + np.argsort(-zs_frames[a:b_])[:k]).tolist())
        elif role[i] == 0:
            neg_clips.append(i)
    neg_all = np.concatenate([np.arange(offsets[i], offsets[i + 1]) for i in neg_clips])
    neg_rows = rng.choice(neg_all, min(NEG_FRAMES, len(neg_all)), replace=False)
    rows = np.concatenate([np.asarray(pos_rows), neg_rows])
    y = np.concatenate([np.ones(len(pos_rows)), np.zeros(len(neg_rows))])
    clf = FastLogistic(l2=1e-3, epochs=300).fit(emb[rows].astype(np.float32), y)
    return clf


def score_frames(clf, emb, chunk=100_000):
    out = np.empty(len(emb), np.float32)
    for s in range(0, len(emb), chunk):
        out[s:s + chunk] = clf.decision_function(emb[s:s + chunk].astype(np.float32))
    return out


def evaluate(frames, offsets, role, thr, a, b):
    cmax = clip_max(frames, offsets)
    pos, neg = role == 1, role == 0
    k = int((cmax[pos] >= thr).sum())
    hours = neg_hours(offsets, neg)
    fa = fa_events(frames, offsets, neg, thr)
    prob = 1 / (1 + np.exp(-np.clip(a * cmax[pos | neg] + b, -60, 60)))
    return {"auc": auc(cmax[pos], cmax[neg]), "n_pos": int(pos.sum()), "recall": k / max(1, int(pos.sum())),
            "recall_ci": wilson(k, int(pos.sum())), "fa": fa, "neg_hours": hours, "fa_per_hour": fa / hours,
            "fa_ci": garwood(fa, hours), "ece": ece(prob, (role[pos | neg] == 1).astype(float))}


def run_train(log=print):
    t0 = time.time()
    dev, ev = load_split("dev"), load_split("eval")
    overlap = len(set(dev["uploader"]) & set(ev["uploader"]))
    log(f"dev {len(dev['fnames'])} קטעים, eval {len(ev['fnames'])}; חפיפת מעלים: {overlap}")
    if overlap:
        raise SystemExit("חפיפת מעלים בין dev ל-eval - המדידה לא תהיה בלתי תלויה")
    W, b = yamnet_dense()
    err = verify_dense(W, b)
    log(f"שכבת הסיווג של YAMNet חולצה ואומתה (שגיאה מקס' {err:.2e})")
    rng = np.random.default_rng(0)
    ups = sorted(set(dev["uploader"]))
    fold_of_up = {u: i % FOLDS for i, u in enumerate(rng.permutation(ups))}
    fold = np.array([fold_of_up[u] for u in dev["uploader"]])

    model, metrics = {}, {}
    os.makedirs(CKPT_DIR, exist_ok=True)
    for cat, c in CATEGORIES.items():
        ck = os.path.join(CKPT_DIR, f"{cat}.pkl")
        if os.path.exists(ck):
            with open(ck, "rb") as fh:
                model[cat], metrics[cat] = pickle.load(fh)
            log(f"{cat}: נטען מנקודת שמירה")
            continue
        log(f"{cat}: מתחיל ({time.time() - t0:.0f}s)")
        idx = class_idx(c["yam"])
        r_dev, r_ev = roles(dev, cat), roles(ev, cat)
        zs_dev = zero_shot_frames(dev["emb"], W, b, idx)
        zs_ev = zero_shot_frames(ev["emb"], W, b, idx)
        # head: ציוני OOF על dev
        oof = np.empty(len(dev["emb"]), np.float32)
        for f in range(FOLDS):
            train_mask = fold != f
            clf = train_head(dev["emb"], dev["offsets"], r_dev, zs_dev, train_mask, rng)
            for i in np.flatnonzero(fold == f):
                a_, b_ = dev["offsets"][i], dev["offsets"][i + 1]
                oof[a_:b_] = clf.decision_function(dev["emb"][a_:b_].astype(np.float32))
        cand = {}
        for name, frames_dev in (("zero_shot", zs_dev), ("head", oof)):
            cmax = clip_max(frames_dev, dev["offsets"])
            neg = r_dev == 0
            thr = threshold_for_budget(frames_dev, dev["offsets"], neg, BUDGET_PER_HOUR, cmax)
            m = r_dev >= 0
            a_p, b_p = platt(cmax[m], (r_dev[m] == 1).astype(float))
            dv = evaluate(frames_dev, dev["offsets"], r_dev, thr, a_p, b_p)
            cand[name] = {"thr": thr, "platt": (a_p, b_p), "dev": dv,
                          "fa_table": fa_table(frames_dev, dev["offsets"], neg, cmax)}
        champ = max(cand, key=lambda k: (cand[k]["dev"]["recall"], cand[k]["dev"]["auc"] or 0))
        final = train_head(dev["emb"], dev["offsets"], r_dev, zs_dev, np.ones(len(r_dev), bool), rng)
        ev_frames = {"zero_shot": zs_ev, "head": score_frames(final, ev["emb"])}
        for name in cand:
            cand[name]["eval"] = evaluate(ev_frames[name], ev["offsets"], r_ev, cand[name]["thr"], *cand[name]["platt"])
        model[cat] = {"champion": champ, "yam_idx": idx, "head": final, "cand": cand}
        metrics[cat] = {k: {"dev": v["dev"], "eval": v["eval"], "thr": v["thr"]} for k, v in cand.items()}
        with open(ck, "wb") as fh:
            pickle.dump((model[cat], metrics[cat]), fh)
        e = cand[champ]["eval"]
        log(f"{cat}: champion={champ} eval AUC {e['auc']:.3f} recall {e['recall']:.1%} "
            f"FA/h {e['fa_per_hour']:.2f} ({time.time() - t0:.0f}s)")
    unval = {k: {"yam_idx": class_idx(v["yam"])} for k, v in UNVALIDATED.items()}
    return model, metrics, unval, {"dev_clips": len(dev["fnames"]), "eval_clips": len(ev["fnames"]),
                                   "uploader_overlap": overlap, "dense_check_err": err,
                                   "dev_neg_hours_note": "per category, see metrics"}


# ------------------------------------------------------------------ שמירה / דו"ח / שער
def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    return o


def save(model, metrics, unval, meta, path=OUT_MODEL):
    arrays, card = {}, {"version": VERSION, "created": _dt.date.today().isoformat(),
                        "data": "FSD50K dev (train/threshold/calibration, uploader-grouped 5-fold OOF); "
                                "FSD50K eval (measurement only)", "budget_fa_per_hour_dev": BUDGET_PER_HOUR,
                        "frame_hop_s": HOP, "meta": meta, "categories": {}, "unvalidated": {}}
    for cat, m in model.items():
        ch = m["champion"]
        c = m["cand"][ch]
        h = m["head"]
        arrays[f"{cat}__mean"], arrays[f"{cat}__std"] = h.mean, h.std
        arrays[f"{cat}__w"], arrays[f"{cat}__b"] = h.w, np.array([h.b], np.float32)
        card["categories"][cat] = {
            "label_he": CATEGORIES[cat]["he"], "severity_prior": CATEGORIES[cat]["severity"],
            "champion": ch, "yamnet_classes": list(CATEGORIES[cat]["yam"]), "yam_idx": m["yam_idx"],
            "threshold": c["thr"], "platt": list(c["platt"]), "fa_table": c["fa_table"],
            "metrics": metrics[cat]}
    for cat, u in unval.items():
        card["unvalidated"][cat] = {"label_he": UNVALIDATED[cat]["he"], "severity_prior": UNVALIDATED[cat]["severity"],
                                    "yamnet_classes": list(UNVALIDATED[cat]["yam"]), "yam_idx": u["yam_idx"],
                                    "threshold": 0.5, "note": "אין תוויות לאימות - חשד בלבד, לא נכנס לניקוד"}
    arrays["card"] = np.array([json.dumps(_clean(card), ensure_ascii=False)])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, **arrays)
    return card


def pct(x):
    return f"{100 * x:.1f}%"


def report(card, path=OUT_REPORT):
    L = [f"# מנוע אירועי סכנה קוליים - {card['version']}", "",
         f"נוצר ב-`research_tools/hazard_audio_train.py` ‏({card['created']}). המודל: `models/hazard_audio.npz`.", "",
         "- **אימון, ספים, כיול ובחירת champion:** FSD50K dev, עם 5 קבוצות לפי מעלה (out-of-fold).",
         f"- **מדידה:** FSD50K eval בלבד. חפיפת מעלים בין dev ל-eval: **{card['meta']['uploader_overlap']}**.",
         f"- **נקודת עבודה:** {card['budget_fa_per_hour_dev']} התראת שווא לשעת אודיו שלילי, נקבעה על dev.",
         "- **שלילי** = כל קטע בלי הקטגוריה: מוזיקה, דיבור, רחוב, וגם סכנות אחרות. למשל זיקוקים הם שליליים לירי.",
         "- **המספרים אינם ביצועי שטח:** FSD50K הוא Freesound, לא טלפון בכיס. ההסתברות המכוילת תלויה בשכיחות ב-FSD50K, "
         "ואינה \"הסתברות לסכנה\".", "",
         "## תוצאות על eval (champion מסומן ב-★)", "",
         "| קטגוריה | חומרה (מדיניות) | מועמד | AUC | recall בסף | התראות שווא לשעה (95%) | שעות שליליות | ECE |",
         "|---|---|---|---|---|---|---|---|"]
    for cat, c in card["categories"].items():
        for name in ("zero_shot", "head"):
            e = c["metrics"][name]["eval"]
            star = " ★" if name == c["champion"] else ""
            L.append(f"| {c['label_he']} (`{cat}`) | {c['severity_prior']} | {name}{star} | {e['auc']:.3f} | "
                     f"{pct(e['recall'])} ({pct(e['recall_ci'][0])}–{pct(e['recall_ci'][1])}), n={e['n_pos']} | "
                     f"{e['fa_per_hour']:.2f} ({e['fa_ci'][0]:.2f}–{e['fa_ci'][1]:.2f}) | {e['neg_hours']:.1f} | {e['ece']:.3f} |")
    L += ["", "## אותו דבר על dev (out-of-fold) - לבדיקת עקביות", "",
          "| קטגוריה | מועמד | AUC | recall | התראות שווא לשעה |", "|---|---|---|---|---|"]
    for cat, c in card["categories"].items():
        for name in ("zero_shot", "head"):
            d = c["metrics"][name]["dev"]
            L.append(f"| `{cat}` | {name} | {d['auc']:.3f} | {pct(d['recall'])} | {d['fa_per_hour']:.2f} |")
    L += ["", "## לא מאומת (אין תוויות ב-FSD50K)", ""]
    for cat, u in card["unvalidated"].items():
        L.append(f"- **{u['label_he']} (`{cat}`):** מחלקות YAMNet {', '.join(u['yamnet_classes'])}. "
                 "מדווח כ\"חשד, לא מאומת\" ולא מוסיף לניקוד הסיכון.")
    io_path = path
    with open(io_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def load_card(path):
    d = np.load(path, allow_pickle=False)
    return json.loads(str(d["card"][0]))


def gate(old_path, new_path, max_recall_drop=0.02, max_fa_ratio=1.5):
    """שער champion/challenger: גרסה חדשה עוברת רק אם בכל קטגוריה ה-recall על eval לא ירד ביותר מ-2 נק'
    וקצב התראות השווא לא עלה פי 1.5 (+0.2 לשעה כמרווח לרעש במדגם קטן). מחזיר (ok, הסברים)."""
    old, new = load_card(old_path), load_card(new_path)
    problems = []
    for cat, o in old["categories"].items():
        n = new["categories"].get(cat)
        if n is None:
            problems.append(f"{cat}: הקטגוריה נעלמה בגרסה החדשה")
            continue
        oe = o["metrics"][o["champion"]]["eval"]
        ne = n["metrics"][n["champion"]]["eval"]
        if ne["recall"] < oe["recall"] - max_recall_drop:
            problems.append(f"{cat}: recall ירד {pct(oe['recall'])} -> {pct(ne['recall'])}")
        if ne["fa_per_hour"] > oe["fa_per_hour"] * max_fa_ratio + 0.2:
            problems.append(f"{cat}: התראות שווא עלו {oe['fa_per_hour']:.2f} -> {ne['fa_per_hour']:.2f} לשעה")
    return (not problems), problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--out", default=OUT_MODEL)
    t.add_argument("--report", default=OUT_REPORT)
    g = sub.add_parser("gate")
    g.add_argument("old")
    g.add_argument("new")
    a = ap.parse_args()
    if a.cmd == "train":
        model, metrics, unval, meta = run_train()
        card = save(model, metrics, unval, meta, a.out)
        report(card, a.report)
        print(f"נשמר: {a.out}, {a.report}")
    else:
        ok, probs = gate(a.old, a.new)
        print("PASS" if ok else "FAIL")
        for p in probs:
            print(" -", p)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

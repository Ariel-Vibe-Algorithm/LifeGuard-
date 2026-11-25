# -*- coding: utf-8 -*-
"""
hazard_eval_stream.py - מעריך משותף אחד למנוע הסכנה הקולי (מחקר בלבד, לא משנה את האפליקציה).

למה: בדיונים קודמים המספרים זזו פי 2-5 רק בגלל אופן המדידה (רשת ספים לפי קוונטילים, ספירת חלונות במקום
התראות, סט קטעים שונה). כאן כל מודל נמדד באותה דרך בדיוק:

- סריקת ספים מדויקת: כל ציון ייחודי הוא סף מועמד (לא קוונטילים). זיהוי = ציון >= סף.
- ספירת התראות שווא כזרם: בכל קטע שלילי, לפי סדר זמן, חלון מעל הסף מפעיל התראה רק אם עברו לפחות
  REFRACTORY_S (9 ש') מההתראה הקודמת באותו קטע. שווא/שעה (קירוב) = התראות / שעות אודיו שלילי,
  כששעות = מספר החלונות השליליים × WIN_HOP_S (0.75 ש' - חלון 1.5 ש' בחפיפה של חצי).
- זיהוי אירועים (recall) ביעד שווא/שעה, עם רווח סמך Wilson 95%; רווח סמך Poisson מדויק (Garwood) לשווא/שעה.
- סף מוחזק לפי מעלה: מחלקים את המעלים לשני חצאים (hash דטרמיניסטי), בוחרים סף על חצי אחד ומדווחים על השני
  (ולהפך). כך אף אירוע לא נמדד בסף שנבחר עליו.

תיוג: research_tools/hv3_freesound.index()/load() + hv2_train.window_labels/resolve_positives (אותו תיוג כמו
באימון ובשער של hv3, כולל ההחרגות "excl:<cat>"). אירוע = קטע חיובי; ברירת מחדל MIL (שני החלונות המובילים לפי
zero-shot, כמו בשער), או "any" (כל חלון בקטע).

נתונים: train/val בלבד. features_test.npz כבר "נוצל" (research/hazard_v3_freesound.md, test_log.jsonl) - נטען רק
עם --final, עם אזהרה, ונרשם ב-test_log.jsonl.

שימוש:
  python -I research_tools/hazard_eval_stream.py                 # val, v1 מול ראשי המועמד, 1 ו-5 שווא/שעה
  python -I research_tools/hazard_eval_stream.py --split train --events any --targets 1 2 5
  python -I research_tools/hazard_eval_stream.py --no-report     # בלי לכתוב research/hazard_eval_stream.md
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WIN_HOP_S = 0.75
REFRACTORY_S = 9.0
Z95 = 1.959963984540054
CATS = ("scream", "distress_voice", "siren", "alarm", "impact", "fireworks")
REPORT = os.path.join(ROOT, "research", "hazard_eval_stream.md")
REPORT_JSON = os.path.join(ROOT, "research", "hazard_eval_stream.json")
SEED = 2026


# ================================================================= סטטיסטיקה
def wilson(k, n, z=Z95):
    """רווח סמך Wilson לשיעור k/n. n=0 -> (0, 1)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def poisson_ci(k, alpha=0.05):
    """רווח סמך מדויק (Garwood) לממוצע Poisson בהינתן k ספירות."""
    from scipy.stats import chi2
    lo = 0.0 if k == 0 else float(chi2.ppf(alpha / 2, 2 * k) / 2)
    hi = float(chi2.ppf(1 - alpha / 2, 2 * k + 2) / 2)
    return lo, hi


# ================================================================= ספירת התראות כזרם
def _clip_order(clips, starts):
    """קבוצות אינדקסים לכל קטע, ממוינות לפי זמן התחלה."""
    groups = {}
    for i, c in enumerate(clips):
        groups.setdefault(c, []).append(i)
    starts = np.asarray(starts, float)
    return [np.array(sorted(ii, key=lambda i: starts[i]), int) for ii in groups.values()]


def _count_stream(s, t, thr, refractory):
    """התראות בקטע אחד (s, t ממוינים לפי זמן) בסף thr, עם זמן מת refractory."""
    n, last = 0, -math.inf
    for si, ti in zip(s, t):
        if si >= thr and ti - last >= refractory:
            n += 1
            last = ti
    return n


def stream_alarms(scores, clips, starts, thr, refractory=REFRACTORY_S):
    """סך ההתראות בכל הקטעים בסף אחד."""
    scores = np.asarray(scores, float)
    starts = np.asarray(starts, float)
    return sum(_count_stream(scores[ii], starts[ii], thr, refractory) for ii in _clip_order(clips, starts))


def neg_hours(n_windows, hop=WIN_HOP_S):
    return n_windows * hop / 3600.0


def sweep(event_scores, neg_scores, neg_clips, neg_starts, refractory=REFRACTORY_S, hop=WIN_HOP_S):
    """סריקה מדויקת: כל ציון ייחודי (אירועים + שליליים) הוא סף, ועוד +inf (שום דבר לא מזוהה).
    הספירה מחושבת במדויק לכל סף (לא באינטרפולציה): בכל קטע היא קבועה בין שני ציונים עוקבים של אותו קטע ->
    מערך הפרשים גלובלי. (ספירה חמדנית עם זמן מת היא המקסימום האפשרי לתת-הקבוצה שמעל הסף, ולכן לא עולה כשהסף עולה.)"""
    ev = np.sort(np.asarray(event_scores, float))
    ns = np.asarray(neg_scores, float)
    nt = np.asarray(neg_starts, float)
    thr = np.unique(np.concatenate([ev, ns, [np.inf]]))
    hits = len(ev) - np.searchsorted(ev, thr, side="left")
    diff = np.zeros(len(thr) + 1, np.int64)
    for ii in _clip_order(neg_clips, nt):
        s, t = ns[ii], nt[ii]
        prev = -1
        for v in np.unique(s):
            p = int(np.searchsorted(thr, v))
            f = _count_stream(s, t, v, refractory)
            diff[prev + 1] += f
            diff[p + 1] -= f
            prev = p
    alarms = np.cumsum(diff)[:len(thr)]
    hours = neg_hours(len(ns), hop)
    return {"thresholds": thr, "hits": hits.astype(int), "alarms": alarms.astype(int), "n_events": int(len(ev)),
            "neg_windows": int(len(ns)), "neg_hours": hours,
            "fa_per_h": alarms / hours if hours > 0 else np.where(alarms > 0, np.inf, 0.0)}


def pick_threshold(sw, target_fa_h):
    """הסף עם הכי הרבה אירועים שזוהו בתנאי שווא/שעה <= target; בשוויון - הסף הגבוה (פחות התראות)."""
    ok = np.flatnonzero(sw["fa_per_h"] <= target_fa_h + 1e-12)
    best = max(ok, key=lambda i: (sw["hits"][i], sw["thresholds"][i]))   # +inf תמיד עומד בתנאי
    return float(sw["thresholds"][best])


def summarize(hits, n, alarms, hours, thr=None):
    lo, hi = wilson(hits, n)
    plo, phi = poisson_ci(alarms)
    out = {"hits": int(hits), "n_events": int(n), "recall": hits / n if n else 0.0, "recall_ci95": [lo, hi],
           "alarms": int(alarms), "neg_hours": hours, "fa_per_h": alarms / hours if hours else 0.0,
           "fa_per_h_ci95": [plo / hours, phi / hours] if hours else [0.0, 0.0]}
    if thr is not None:
        out["threshold"] = thr
    return out


def at_threshold(event_scores, neg_scores, neg_clips, neg_starts, thr, refractory=REFRACTORY_S, hop=WIN_HOP_S):
    ev = np.asarray(event_scores, float)
    a = stream_alarms(neg_scores, neg_clips, neg_starts, thr, refractory)
    return summarize(int((ev >= thr).sum()), len(ev), a, neg_hours(len(neg_scores), hop), thr)


def at_budget(sw, target_fa_h):
    """ביצועים בתוך המדגם (הסף נבחר ונמדד על אותם נתונים - אופטימי)."""
    thr = pick_threshold(sw, target_fa_h)
    i = int(np.searchsorted(sw["thresholds"], thr))
    return summarize(sw["hits"][i], sw["n_events"], sw["alarms"][i], sw["neg_hours"], thr)


def user_half(user, seed=SEED):
    return int(hashlib.sha256(f"{seed}:{user}".encode()).hexdigest(), 16) % 2


def grouped_holdout(ev_scores, ev_users, neg_scores, neg_clips, neg_starts, neg_users, target_fa_h,
                    seed=SEED, refractory=REFRACTORY_S, hop=WIN_HOP_S):
    """סף נבחר על חצי מהמעלים ונמדד על החצי השני, בשני הכיוונים. 'pooled' = כל אירוע ושעה נמדדים פעם אחת,
    תמיד בסף שנבחר בלי לראות אותם."""
    ev_scores, neg_scores = np.asarray(ev_scores, float), np.asarray(neg_scores, float)
    neg_starts = np.asarray(neg_starts, float)
    neg_clips = np.asarray(neg_clips, dtype=object)
    eh = np.array([user_half(u, seed) for u in ev_users], int)
    nh = np.array([user_half(u, seed) for u in neg_users], int)
    folds, H, N, A, T = [], 0, 0, 0, 0.0
    for fit in (0, 1):
        ef, nf = eh == fit, nh == fit
        sw = sweep(ev_scores[ef], neg_scores[nf], neg_clips[nf], neg_starts[nf], refractory, hop)
        thr = pick_threshold(sw, target_fa_h)
        r = at_threshold(ev_scores[~ef], neg_scores[~nf], neg_clips[~nf], neg_starts[~nf], thr, refractory, hop)
        r["fit_half"] = fit
        r["fit_alarm_budget"] = int(math.floor(target_fa_h * sw["neg_hours"] + 1e-9))
        folds.append(r)
        H, N, A, T = H + r["hits"], N + r["n_events"], A + r["alarms"], T + r["neg_hours"]
    return {"folds": folds, "pooled": summarize(H, N, A, T)}


# ================================================================= נתונים (Freesound, train/val בלבד)
def load_split(split, final=False):
    """-> (arrays, meta, idx, T2). test רק עם final=True, עם אזהרה ורישום ב-test_log.jsonl."""
    from research_tools import hv3_freesound as H3
    if split == "test":
        if not final:
            raise SystemExit("סירוב: features_test.npz כבר נוצל (research/hazard_v3_freesound.md). "
                             "מותר רק עם --final, להערכה סופית אחת.")
        print("⚠️ אזהרה: ה-test של Freesound כבר נוצל - כל מספר עליו אופטימי ולא מהווה הוכחה. "
              "ההרצה נרשמת ב-test_log.jsonl.", flush=True)
        H3._log_test_access({"tool": "hazard_eval_stream", "rules": H3.C.rules_hash()})
    elif split not in ("train", "val"):
        raise SystemExit(f"split לא מוכר: {split}")
    T2 = H3._setup_trainer()        # אותן החרגות/שליליים קשים כמו באימון ובשער
    idx = H3.index()
    d, meta, _ = H3.load(split, idx)
    return d, meta, idx, T2


def prepare(cat, d, meta, idx, T2, scores, events="mil"):
    """ציוני חלונות -> (ציון אירוע לכל קטע חיובי, מעלה), ושליליים (ציון, קטע, התחלה, מעלה)."""
    y0, _, ci = T2.window_labels(meta, idx, cat)
    y = T2.resolve_positives(y0.copy(), meta, d["zs"], idx, cat, ci)
    pos_mask = (y == 1) if events == "mil" else (y0 == -2)
    scores = np.asarray(scores, float)
    ev = {}
    for i in np.flatnonzero(pos_mask):
        ev.setdefault(meta[i]["id"], []).append(i)
    ids = sorted(ev)
    neg = np.flatnonzero(y == 0)
    return {"ev_scores": np.array([scores[ev[k]].max() for k in ids], float),
            "ev_users": [idx[k]["user"] for k in ids],
            "neg_scores": scores[neg], "neg_clips": [meta[i]["id"] for i in neg],
            "neg_starts": np.array([meta[i]["start"] for i in neg], float),
            "neg_users": [idx[meta[i]["id"]]["user"] for i in neg]}


def _logit(npz, prefix, X):
    m, s, w, b = (npz[f"{prefix}__{k}"] for k in ("mean", "std", "w", "b"))
    return ((np.asarray(X, np.float32) - m) / s) @ w + float(b[0])


def model_scores(cat, d, T2):
    """ציונים לכל מודל: v1 (הציון הגולמי שבאפליקציה), ראש שלב 1 של המועמד, ומועמד דו-שלבי
    (ציון שלב 2 בחלונות שעברו את t1 מהכרטיס; אחרת -inf)."""
    from research_tools import hv3_freesound as H3
    ci = T2.TARGETS.index(cat)
    out = {"v1": d["v1"][:, ci].astype(float)}
    if not os.path.exists(H3.CANDIDATE):
        return out
    npz = np.load(H3.CANDIDATE, allow_pickle=False)
    card = json.loads(str(npz["card"][0]))["categories"].get(cat)
    if card is None or f"{cat}__s1__w" not in npz.files:
        return out
    l1 = _logit(npz, f"{cat}__s1", d["s1"])
    out["cand_s1"] = l1.astype(float)
    if card.get("two_stage") and f"{cat}__s2__w" in npz.files:
        l2 = _logit(npz, f"{cat}__s2", T2.verifier_matrix(l1, d))
        out["cand_2stage"] = np.where(l1 >= card["t1"], l2, -np.inf).astype(float)
    return out


def shipped_v1_threshold(cat):
    try:
        import audio_intel as AI
        import acoustic_ai_module as AC
        if cat == "distress_voice":
            return AC.threshold()
        AI.load()
        return float(AI._STATE["card"]["categories"][cat]["threshold"])
    except Exception:  # noqa: BLE001 - מידע משני בלבד
        return None


# ================================================================= CLI + דו"ח
def run(split="val", targets=(1.0, 5.0), events="mil", final=False, write=True):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    d, meta, idx, T2 = load_split(split, final)
    res = {"split": split, "events": events, "targets": list(targets), "refractory_s": REFRACTORY_S,
           "hop_s": WIN_HOP_S, "windows": len(meta), "created": time.strftime("%Y-%m-%d"), "categories": {}}
    for cat in CATS:
        P0 = None
        rc = {}
        for name, sc in model_scores(cat, d, T2).items():
            P = prepare(cat, d, meta, idx, T2, sc, events)
            P0 = P0 or P
            sw = sweep(P["ev_scores"], P["neg_scores"], P["neg_clips"], P["neg_starts"])
            r = {"in_sample": {}, "holdout": {}}
            for tg in targets:
                r["in_sample"][str(tg)] = at_budget(sw, tg)
                r["holdout"][str(tg)] = grouped_holdout(P["ev_scores"], P["ev_users"], P["neg_scores"], P["neg_clips"],
                                                        P["neg_starts"], P["neg_users"], tg)
            if name == "v1":
                t = shipped_v1_threshold(cat)
                if t is not None:
                    r["shipped"] = at_threshold(P["ev_scores"], P["neg_scores"], P["neg_clips"], P["neg_starts"], t)
            rc[name] = r
            h = r["holdout"][str(targets[0])]["pooled"]
            print(f"{cat:15s} {name:12s} @{targets[0]}/h held-out recall {h['recall']:.1%} "
                  f"[{h['recall_ci95'][0]:.1%}, {h['recall_ci95'][1]:.1%}] · FA/h {h['fa_per_h']:.2f}", flush=True)
        res["categories"][cat] = {"n_events": len(P0["ev_scores"]), "n_users": len(set(P0["ev_users"])),
                                  "neg_windows": len(P0["neg_scores"]),
                                  "neg_hours": neg_hours(len(P0["neg_scores"])), "models": rc}
    if write:
        with open(REPORT_JSON, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
        report(res)
    return res


HE = {"scream": "צרחה", "distress_voice": "קול מצוקה", "siren": "סירנה", "alarm": "אזעקה", "impact": "חבטה / טריקה",
      "fireworks": "זיקוקים"}
MODEL_HE = {"v1": "v1 (באפליקציה)", "cand_s1": "מועמד - שלב 1", "cand_2stage": "מועמד - שני שלבים"}


def _fmt(r):
    lo, hi = r["recall_ci95"]
    flo, fhi = r["fa_per_h_ci95"]
    return (f"{r['hits']}/{r['n_events']} = **{r['recall']:.1%}** [{lo:.1%}, {hi:.1%}] | "
            f"{r['alarms']} → {r['fa_per_h']:.2f} [{flo:.2f}, {fhi:.2f}]")


def report(res):
    tg = [str(t) for t in res["targets"]]
    L = ["# מעריך זרם משותף - מנוע הסכנה הקולי (Freesound)", "",
         f"נוצר ע\"י `research_tools/hazard_eval_stream.py` ({res['created']}). split: **{res['split']}** "
         f"({res['windows']:,} חלונות). אירועים: {'MIL - שני החלונות המובילים בקטע' if res['events'] == 'mil' else 'כל חלון בקטע'}. "
         "כל מספר מהריצה; JSON מלא: `research/hazard_eval_stream.json`.", "",
         "## שיטה (זהה לכל המודלים)", "",
         "- **סריקת ספים מדויקת** - כל ציון ייחודי הוא סף מועמד (לא רשת קוונטילים).",
         f"- **התראות שווא כזרם** - בכל קטע שלילי, לפי הזמן, התראה חדשה רק אחרי {res['refractory_s']:.0f} ש' מהקודמת. "
         f"שווא/שעה = התראות / (חלונות שליליים × {res['hop_s']} ש'). **קירוב**: מקטעים שליליים נדגמו עד 6 חלונות, "
         "ולכן הזמן המת כמעט לא נכנס לפעולה והשעות הן אודיו ייחודי משוער, לא הקלטה רציפה.",
         "- **רווחי סמך 95%** - Wilson לזיהוי, Poisson מדויק (Garwood) לשווא/שעה.",
         "- **סף מוחזק לפי מעלה** - המעלים מחולקים לשני חצאים (hash דטרמיניסטי); הסף נבחר על חצי אחד ונמדד על השני, "
         "בשני הכיוונים; \"מאוחד\" = כל אירוע ושעה נמדדו פעם אחת בסף שלא ראה אותם. זה המספר העיקרי.",
         "- **מודלים** - v1: הציון הגולמי שבאפליקציה היום. מועמד: `research_data/freesound/model_candidate.npz` "
         "(שלב 1 לבד, ושני שלבים: ציון שלב 2 בחלונות שעברו את t1 מהכרטיס). t1 וסוג ה-MIL של המועמד נבחרו על val - "
         "על val המועמד מקבל יתרון קל.", ""]
    for cat, c in res["categories"].items():
        L += [f"## {HE.get(cat, cat)}", "",
              f"{c['n_events']} אירועים ({c['n_users']} מעלים) · {c['neg_windows']:,} חלונות שליליים ≈ "
              f"{c['neg_hours']:.2f} שעות. בתקציב 1 שווא/שעה מותרות ≈ {math.floor(c['neg_hours']):d} התראות "
              "בכל המדגם (ובכל חצי - בערך חצי מזה), כך שרווחי הסמך רחבים.", "",
              "| מודל | יעד שווא/שעה | מוחזק (מאוחד): זוהו = זיהוי [CI 95%] | התראות → שווא/שעה [CI 95%] | "
              "בתוך המדגם (אופטימי): זיהוי | שווא/שעה |", "|---|---|---|---|---|---|"]
        for name, r in c["models"].items():
            for t in tg:
                h, s = r["holdout"][t]["pooled"], r["in_sample"][t]
                L.append(f"| {MODEL_HE.get(name, name)} | {t} | {_fmt(h)} | {s['recall']:.1%} | {s['fa_per_h']:.2f} |")
            if "shipped" in r:
                s = r["shipped"]
                L.append(f"| v1 בסף שבאפליקציה ({s['threshold']:.3f}) | — | {_fmt(s)} | — | — |")
        L.append("")
    L += ["## מגבלות", "",
          "- Freesound עם תיוג חלש של המעלים - לא טלפון בכיס ברחוב. שווא/שעה כאן אינו שיעור התראות בשטח.",
          "- val קטן (פחות משעה אודיו שלילי לקטגוריה) - ב-1 שווא/שעה הסף נקבע בפועל ע\"י 0-1 התראות, "
          "ולכן ההבדלים בין מודלים בתוך רווח הסמך אינם מובהקים. שווא/שעה המוחזק עולה לעתים על היעד - "
          "זה המחיר האמיתי של בחירת סף על חצי מהמעלים, ובדיוק מה שהמדידה בתוך המדגם מסתירה.",
          "- אירוע MIL נבחר לפי zero-shot של YAMNet; בצרחה ובזיקוקים זה v1 עצמו - מוטה לטובת v1 (שמרני למועמד).",
          "- אין כאן צבירה של 2 מתוך 6 חלונות - זה מדד של חלון בודד + זמן מת. צבירה תיבדק באותו מעריך בהמשך.",
          "- test (features_test.npz) לא נטען. הוא כבר נוצל, ומותר רק עם `--final` (נרשם ב-test_log.jsonl)."]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"דו\"ח: {REPORT}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="מעריך זרם משותף למנוע הסכנה הקולי (train/val בלבד)")
    ap.add_argument("--split", default="val", choices=("train", "val", "test"))
    ap.add_argument("--final", action="store_true", help="מתיר test (שכבר נוצל) - להערכה סופית אחת בלבד")
    ap.add_argument("--targets", type=float, nargs="+", default=[1.0, 5.0])
    ap.add_argument("--events", default="mil", choices=("mil", "any"))
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()
    run(a.split, tuple(a.targets), a.events, a.final, not a.no_report)

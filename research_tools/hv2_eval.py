# -*- coding: utf-8 -*-
"""
hv2_eval.py - מבחן לפני/אחרי: v1 (בייצור) מול v2 (מועמד), על נתוני test שלא נגעו בהם באימון או בבחירת ספים.

אותם חלונות בדיוק לשני המנועים. תיוג חלונות test: ירי - לפי חותמות זמן; שאר המקורות - MIL לפי ציון zero-shot של
YAMNet (בלתי תלוי בשני המודלים, כך שאין יתרון לאף אחד).

מדדים לכל קטגוריה: זיהוי אירועים (לפי מקור, מכשיר וסביבה), אירועים שהוחמצו, שיעור חלונות שגויים לכל סביבה
(פנים / מוזיקה וטלוויזיה / רחוב / רכב / מבנה ציבורי) והמרה לשווא לשעה, דיוק (precision) על המבחן המאוחד, כיול (ECE),
וזמן עיבוד לחלון.

קריטריון קבלה לכל קטגוריה (נקבע לפני ההרצה):
 A. זיהוי מאוחד v2 ≥ v1 − 2 נק', וגם על כל מאגר אמיתי בנפרד (ירי, רחוב, Nonspeech7k, ESC-50) v2 ≥ v1 − 2 נק';
 B. בכל סביבה: שיעור חלונות שגויים v2 ≤ v1 × 1.25 + 2/N (מרווח לרעש מדגם);
 C. שיפור ממשי: זיהוי מאוחד +2 נק' לפחות, או ירידה של 20% לפחות בשווא באחת הסביבות.
קטגוריה שעומדת ב-A, B ו-C עוברת ל-v2; אחרת נשארת v1 (champion/challenger לכל קטגוריה).

שימוש: python -I research_tools/hv2_eval.py   -> research/hazard_v2_benchmark.md + .json
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
from research_tools import hv2_train as TR  # noqa: E402

OUT_MD = os.path.join(ROOT, "research", "hazard_v2_benchmark.md")
OUT_JSON = os.path.join(ROOT, "research", "hazard_v2_benchmark.json")
WIN_PER_HOUR = 3600 / 1.5
REAL_SOURCES = ("gunshot_fit", "sonyc_ust", "nonspeech7k", "esc50")
HE = {"gunshot": "ירי", "explosion": "פיצוץ", "fireworks": "זיקוקים", "glass_break": "שבירת זכוכית", "siren": "סירנה",
      "alarm": "אזעקה", "scream": "צרחה", "impact": "חבטה", "distress_voice": "קול מצוקה (צרחה/צעקה/בכי)"}


def log(m):
    print(m, flush=True)


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def environments(meta, idx):
    """סביבת המבחן לכל חלון (שליליים): indoor_mixed / indoor_music_tv / street / vehicle / indoor_public."""
    out = []
    for m in meta:
        r = idx.get(m["id"])
        if r is None:
            out.append("unknown")
            continue
        if r["source"] == "fsd50k":
            out.append("indoor_music_tv" if set(r["tags"]) & {"music", "drums_music", "speech", "tv_like"}
                       else "indoor_mixed")
        elif r["source"] == "sonyc_ust":
            out.append("street")
        elif r["source"] == "tau2020_mobile":
            out.append(r["env"])
        else:
            out.append("other")
    return np.array(out)


def v2_decision(card, arrays, cat, d):
    c = card["categories"][cat]
    h1 = _head(arrays, f"{cat}__s1")
    l1 = h1(d["s1"].astype(np.float32))
    det = l1 >= c["t1"]
    final = l1
    if c["two_stage"]:
        l2 = _head(arrays, f"{cat}__s2")(TR.verifier_matrix(l1, d))
        det &= l2 >= c["t2"]
        final = l2
    a, b = c["platt"]
    prob = 1 / (1 + np.exp(-np.clip(a * final + b, -60, 60)))
    return det, prob


def _head(arrays, p):
    mean, std, w, b = arrays[p + "__mean"], arrays[p + "__std"], arrays[p + "__w"], float(arrays[p + "__b"][0])
    return lambda X: ((np.asarray(X, np.float32) - mean) / std) @ w + b


def ece(prob, y, bins=10):
    if len(prob) == 0:
        return None
    e, n = 0.0, len(prob)
    edges = np.linspace(0, 1, bins + 1)
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (prob >= lo) & (prob < hi if hi < 1 else prob <= hi)
        if m.any():
            e += m.sum() / n * abs(prob[m].mean() - y[m].mean())
    return float(e)


def events_by(y, meta, det, keyfn):
    groups = {}
    for i in np.flatnonzero(y == 1):
        k = (meta[i]["id"], meta[i]["variant"])
        groups.setdefault(k, []).append(i)
    out = {}
    for (cid, _), ii in groups.items():
        key = keyfn(cid)
        h, n = out.get(key, (0, 0))
        out[key] = (h + int(det[ii].any()), n + 1)
    return out


def latency(n=60):
    """זמן עיבוד לחלון (ms): YAMNet + v1 מול YAMNet + v2 (כולל מאפיינים ספקטרליים)."""
    import hazard_features as HF
    from research_tools import yamnet_detector as Y
    import audio_intel as AI
    AI.load()
    y = Y.shared_yamnet()
    rng = np.random.default_rng(0)
    xs = [(rng.standard_normal(24000) * 0.05).astype(np.float32) for _ in range(n)]
    t = time.perf_counter()
    for x in xs:
        s, e = y.analyze(x, 16000)
        for cat, c in AI._STATE["card"]["categories"].items():
            AI._category_frames(cat, c, s, e)
    v1 = (time.perf_counter() - t) / n * 1000
    t = time.perf_counter()
    for x in xs:
        s, e = y.analyze(x, 16000)
        v = HF.stage1_vector(e)
        HF.verifier_vector(0.0, s, x)
        _ = v
    v2 = (time.perf_counter() - t) / n * 1000
    return {"v1_ms": round(v1, 1), "v2_ms": round(v2, 1)}


def run():
    import audio_intel as AI
    import acoustic_ai_module as AC
    AI.load()
    AC.load()
    idx = TR._manifest_index()
    te, te_meta = F.load_split("test")
    z = np.load(TR.CANDIDATE, allow_pickle=False)
    card = json.loads(str(z["card"][0]))
    arrays = {k: z[k] for k in z.files if k != "card"}
    env = environments(te_meta, idx)
    src = np.array([(idx.get(m["id"]) or {}).get("source", "?") for m in te_meta])
    out = {"created": time.strftime("%Y-%m-%d"), "test_windows": len(te_meta), "categories": {},
           "latency": latency(), "env_windows": {e: int((env == e).sum()) for e in np.unique(env)}}
    for cat in D.TARGETS:
        if cat not in card["categories"]:
            continue
        y, _, ci = TR.window_labels(te_meta, idx, cat)
        y = TR.resolve_positives(y, te_meta, te["zs"], idx, cat, ci)
        v1_thr = AC.threshold() if cat == "distress_voice" else float(AI._STATE["card"]["categories"][cat]["threshold"])
        d1 = te["v1"][:, ci] >= v1_thr
        d2, prob2 = v2_decision(card, arrays, cat, te)
        res = {"arch": "two_stage" if card["categories"][cat]["two_stage"] else "stage1", "by_source": {},
               "by_device": {}, "false": {}}
        for name, det in (("v1", d1), ("v2", d2)):
            pooled = events_by(y, te_meta, det, lambda cid: "all").get("all", (0, 0))
            res[name] = {"recall": pooled[0] / max(1, pooled[1]), "hit": pooled[0], "n": pooled[1],
                         "ci": wilson(*pooled), "missed": pooled[1] - pooled[0]}
            for s, (h, n) in events_by(y, te_meta, det, lambda cid: (idx[cid]["source"])).items():
                res["by_source"].setdefault(s, {})[name] = {"hit": h, "n": n, "recall": h / n}
            for s, (h, n) in events_by(y, te_meta, det, lambda cid: (idx[cid]["device"])).items():
                res["by_device"].setdefault(s, {})[name] = {"hit": h, "n": n, "recall": h / n}
            neg = y == 0
            for e in np.unique(env[neg]):
                m = neg & (env == e)
                k = int(det[m].sum())
                res["false"].setdefault(e, {})[name] = {"k": k, "n": int(m.sum()), "rate": k / max(1, m.sum()),
                                                        "fa_per_hour": k / max(1, m.sum()) * WIN_PER_HOUR,
                                                        "ci": wilson(k, int(m.sum()))}
            fp = int((det & neg).sum())
            res[name]["precision_pooled"] = pooled[0] / max(1, pooled[0] + fp)
        cand = (y >= 0) & d2 | ((y == 1))
        res["v2"]["ece"] = ece(prob2[cand & (y >= 0)], (y[cand & (y >= 0)] == 1).astype(float))
        # קבלה
        A = res["v2"]["recall"] >= res["v1"]["recall"] - 0.02 and all(
            v["v2"]["recall"] >= v["v1"]["recall"] - 0.02 for s, v in res["by_source"].items()
            if s in REAL_SOURCES and "v1" in v and "v2" in v and v["v1"]["n"] >= 10)
        B = all(v["v2"]["rate"] <= v["v1"]["rate"] * 1.25 + 2 / max(1, v["v1"]["n"]) for v in res["false"].values())
        C = res["v2"]["recall"] >= res["v1"]["recall"] + 0.02 or any(
            v["v1"]["k"] >= 5 and v["v2"]["rate"] <= 0.8 * v["v1"]["rate"] for v in res["false"].values())
        res["accept"] = {"A_recall": bool(A), "B_false_alarms": bool(B), "C_improvement": bool(C),
                         "accepted": bool(A and B and C)}
        out["categories"][cat] = res
        log(f"{cat}: v1 {100 * res['v1']['recall']:.1f}% -> v2 {100 * res['v2']['recall']:.1f}% "
            f"(n={res['v1']['n']}); קבלה {res['accept']}")
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=float)
    report(out)
    return out


def _p(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def report(o):
    L = ["# מנוע סכנה v2 מול v1 - מבחן לפני/אחרי (test בלבד)", "",
         f"נוצר ע\"י `research_tools/hv2_eval.py` ({o['created']}). {o['test_windows']} חלונות מבחן של 1.5 ש', זהים לשני "
         "המנועים. אף חלון כאן לא שימש לאימון או לבחירת סף.", "",
         f"**זמן עיבוד לחלון (CPU):** v1 {o['latency']['v1_ms']} ms · v2 {o['latency']['v2_ms']} ms.", "",
         "## זיהוי אירועים ושווא", "",
         "| קטגוריה | ארכיטקטורה | זיהוי v1 | זיהוי v2 | הוחמצו v1 → v2 | precision v1 → v2 | ECE v2 | התקבל |",
         "|---|---|---|---|---|---|---|---|"]
    for cat, r in o["categories"].items():
        L.append(f"| {HE.get(cat, cat)} | {'שני שלבים' if r['arch'] == 'two_stage' else 'שלב 1'} | "
                 f"{_p(r['v1']['recall'])} | **{_p(r['v2']['recall'])}** | {r['v1']['missed']} → {r['v2']['missed']} "
                 f"(מתוך {r['v1']['n']}) | {_p(r['v1']['precision_pooled'])} → {_p(r['v2']['precision_pooled'])} | "
                 f"{'—' if r['v2'].get('ece') is None else round(r['v2']['ece'], 3)} | "
                 f"{'✅' if r['accept']['accepted'] else '❌ ' + ', '.join(k for k, v in r['accept'].items() if not v and k != 'accepted')} |")
    L += ["", "## לפי מקור (זיהוי אירועים)", "", "| קטגוריה | מקור | n | v1 | v2 |", "|---|---|---|---|---|"]
    for cat, r in o["categories"].items():
        for s, v in r["by_source"].items():
            if "v1" in v:
                L.append(f"| {HE.get(cat, cat)} | {s} | {v['v1']['n']} | {_p(v['v1']['recall'])} | {_p(v['v2']['recall'])} |")
    L += ["", "## לפי מכשיר", "", "| קטגוריה | מכשיר | n | v1 | v2 |", "|---|---|---|---|---|"]
    for cat, r in o["categories"].items():
        for s, v in r["by_device"].items():
            if "v1" in v and v["v1"]["n"] >= 10:
                L.append(f"| {HE.get(cat, cat)} | {s} | {v['v1']['n']} | {_p(v['v1']['recall'])} | {_p(v['v2']['recall'])} |")
    L += ["", "## שווא לפי סביבה (חלונות שגויים / חלונות; ≈ לשעה)", "",
          "| קטגוריה | סביבה | חלונות | v1 | v2 |", "|---|---|---|---|---|"]
    for cat, r in o["categories"].items():
        for e, v in r["false"].items():
            L.append(f"| {HE.get(cat, cat)} | {e} | {v['v1']['n']} | {v['v1']['k']} ({v['v1']['fa_per_hour']:.2f}/ש') | "
                     f"{v['v2']['k']} ({v['v2']['fa_per_hour']:.2f}/ש') |")
    L += ["", "## קריטריון קבלה (נקבע מראש)",
          "A. זיהוי מאוחד וכל מאגר אמיתי: v2 ≥ v1 − 2 נק'. B. בכל סביבה: שווא v2 ≤ v1 × 1.25 + 2/N. "
          "C. שיפור ממשי (+2 נק' זיהוי, או −20% שווא בסביבה כלשהי). קטגוריה שלא עומדת - נשארת v1.", "",
          "## מגבלות",
          "- \"לשעה\" = חלונות שגויים × 2400 (חלונות של 1.5 ש'); זה קצב **להערכה**, לא האזנה רציפה.",
          "- ירי במטווח ≠ ירי בעיר; צעקה ב-SONYC ≠ תמיד מצוקה; שום מאגר כאן אינו טלפון בכיס בזמן אירוע אמיתי.",
          "- העשרה מלאכותית שימשה לאימון בלבד - שום מספר כאן לא נמדד על אודיו מועשר."]
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    log(f"דו\"ח: {OUT_MD}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run()

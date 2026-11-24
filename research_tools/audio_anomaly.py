# -*- coding: utf-8 -*-
"""
audio_anomaly.py - ניסוי "צליל חריג לא מוכר" (OOD) על embeddings של YAMNet (אבן דרך 2, שלב 3b בתוכנית).

השאלה: האם מודל שלמד רק צלילים יומיומיים (בלי לראות אף צליל מתריע) יכול לסמן צלילים מתריעים
**מסוגים שאף גלאי לא אומן עליהם** (חריקת בלמים, ריסוק, בכי, נהמה, צעקה...) - ובאיזה מחיר של התראות שווא?
ואם כן - האם זה מוסיף משהו מעבר ל-8 הגלאים הקיימים (audio_intel)?

תכנון הוגן:
- dev של FSD50K מחולק לפי מעלה (uploader) לשני חצאים: A להתאמת המודל (יומיומי בלבד), B לבחירת היפר-פרמטרים
  ולקביעת הסף (1 שווא לשעה על היומיומי של B). eval נמדד פעם אחת בסוף. אין חפיפת מעלים בין dev ל-eval.
- היפר-פרמטרים נבחרים על B לפי הקטגוריות **המוכרות** (KNOWN). הקטגוריות החדשות (NOVEL) לא משמשות לשום
  בחירה - הן נמדדות רק על eval. כך "חדש" הוא באמת חדש.
- baseline: "האם אחד מ-8 הגלאים הקיימים מגיב" (בדיוק ההתנהגות בזמן ריצה).
- קריטריוני קבלה נקבעו מראש (ACCEPT) - לפני ההרצה. אם לא עומד - לא משלבים באפליקציה, ומדווחים.

שימוש: python -I research_tools/audio_anomaly.py run
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

from research_tools import hazard_audio_train as T  # noqa: E402

OUT_MODEL = os.path.join(ROOT, "models", "audio_anomaly.npz")
OUT_REPORT = os.path.join(ROOT, "research", "audio_anomaly_v1.md")
OUT_JSON = os.path.join(ROOT, "research", "audio_anomaly_v1.json")
VERSION = "audio_anomaly_v1"
SEED = 7
FIT_FRAMES = 120_000          # מסגרות יומיומיות מ-A להתאמת PCA
BANK_FRAMES = 20_000          # בנק השכנים (kNN) - גם גודל המודל בזמן ריצה
PCA_DIMS = (16, 32, 64, 128)
KNN_K = (1, 5, 20)
BUDGET = T.BUDGET_PER_HOUR
WINDOW_S = 1.5                # config.RISK_ASSESS_NOISE_SECONDS - אורך ההקלטה שהמנוע מנתח אחרי טריגר
WINDOW_FRAMES = 2             # מסגרות YAMNet (0.96 ש', קפיצה 0.48) בתוך 1.5 ש'

# צלילים שהגלאים הקיימים אומנו עליהם (כולל תוויות ההחרגה שלהם) - "מוכרים"
KNOWN = {"Gunshot_and_gunfire", "Explosion", "Boom", "Fireworks", "Shatter", "Siren", "Alarm", "Screaming",
         "Slam", "Thump_and_thud"}
# צלילים מתריעים שאף גלאי לא אומן עליהם - נמדדים רק על eval
NOVEL = {"Screech", "Crushing", "Crack", "Crying_and_sobbing", "Growling", "Yell", "Shout", "Fire"}
# עמומים - לא יומיומיים ולא "מתריעים" באופן ברור; מוצאים משני הצדדים כדי לא לזהם את המדידה
AMBIGUOUS = {"Vehicle_horn_and_car_horn_and_honking", "Thunder", "Thunderstorm", "Glass", "Telephone", "Ringtone",
             "Doorbell", "Crash_cymbal", "Bell", "Church_bell", "Gasp", "Breaking"}
# קריטריוני קבלה - נקבעו לפני ההרצה
ACCEPT = {"novel_auc_min": 0.75, "novel_recall_min": 0.20, "eval_fa_max": 3 * BUDGET, "only_ood_share_min": 0.10}


def log(msg):
    print(msg, flush=True)


def masks(data):
    alarm = KNOWN | NOVEL | AMBIGUOUS
    labels = data["labels"]
    every = np.array([not (ls & alarm) for ls in labels])
    known = np.array([bool(ls & KNOWN) for ls in labels])
    novel = np.array([bool(ls & NOVEL) and not (ls & KNOWN) for ls in labels])
    return every, known, novel


def uploader_halves(data, rng):
    ups = sorted(set(data["uploader"]))
    rng.shuffle(ups)
    a = set(ups[: len(ups) // 2])
    return np.array([u in a for u in data["uploader"]])


def frames_of(offsets, clip_mask):
    idx = [np.arange(offsets[i], offsets[i + 1]) for i in np.flatnonzero(clip_mask)]
    return np.concatenate(idx) if idx else np.zeros(0, int)


class Pca:
    def __init__(self, x, dims):
        self.mean = x.mean(0)
        xc = x - self.mean
        cov = (xc.T @ xc) / max(1, len(xc) - 1)
        vals, vecs = np.linalg.eigh(cov.astype(np.float64))
        order = vals.argsort()[::-1][:dims]
        self.comp = vecs[:, order].astype(np.float32)
        self.scale = np.sqrt(np.maximum(vals[order], 1e-8)).astype(np.float32)

    def z(self, x):
        return ((np.asarray(x, np.float32) - self.mean) @ self.comp) / self.scale


def maha(pca, emb, chunk=50_000):
    out = np.empty(len(emb), np.float32)
    for i in range(0, len(emb), chunk):
        z = pca.z(emb[i:i + chunk])
        out[i:i + chunk] = (z * z).sum(1)
    return out


def knn_multi(pca, bank_z, emb, ks, chunk=2000):
    """{k: ממוצע המרחק ל-k השכנים הקרובים בבנק} (במרחב ה-PCA המולבן) - חישוב מרחקים אחד לכל ה-k."""
    kmax = max(ks)
    out = {k: np.empty(len(emb), np.float32) for k in ks}
    bn = (bank_z * bank_z).sum(1)
    for i in range(0, len(emb), chunk):
        z = pca.z(emb[i:i + chunk])
        d2 = (z * z).sum(1)[:, None] + bn[None, :] - 2 * z @ bank_z.T
        np.maximum(d2, 0, out=d2)
        part = np.sort(np.partition(d2, kmax - 1, axis=1)[:, :kmax], axis=1)
        d = np.sqrt(part)
        for k in ks:
            out[k][i:i + chunk] = d[:, :k].mean(1)
    return out


def knn(pca, bank_z, emb, k, chunk=2000):
    return knn_multi(pca, bank_z, emb, (k,), chunk)[k]


def known_detectors(emb):
    """baseline: ציון "הגלאי הקיים הכי בטוח" (Platt) + האם אחד מהם עבר את הסף שלו - בדיוק כמו בזמן ריצה."""
    import audio_intel as AI
    assert AI.load(), "models/hazard_audio.npz חסר"
    card = AI._STATE["card"]
    W, b = T.yamnet_dense()
    conf = np.zeros(len(emb), np.float32)
    fired = np.zeros(len(emb), bool)
    scoring = np.zeros(len(emb), bool)        # רק גלאים שמוסיפים נקודות סיכון (חומרה high/medium)
    for i in range(0, len(emb), 50_000):
        e = np.asarray(emb[i:i + 50_000], np.float32)
        scores = None
        for cat, c in card["categories"].items():
            if c["champion"] == "zero_shot" and scores is None:
                scores = 1.0 / (1.0 + np.exp(-np.clip(e @ W.T + b, -60, 60)))
            f = AI._category_frames(cat, c, scores, e)
            a, bb = c["platt"]
            conf[i:i + len(e)] = np.maximum(conf[i:i + len(e)], 1.0 / (1.0 + np.exp(-np.clip(a * f + bb, -60, 60))))
            fired[i:i + len(e)] |= f >= float(c["threshold"])
            if AI.SEVERITY_POINTS.get(c["severity_prior"], 0) > 0:
                scoring[i:i + len(e)] |= f >= float(c["threshold"])
    return conf, fired, scoring


def window_hit_rate(flags, offsets, clip_mask, w):
    """החלק מכל חלונות ההערכה האפשריים (w מסגרות רצופות בתוך קטע) שבהם לפחות מסגרת אחת מסומנת - אמפירי."""
    hit = tot = 0
    for i in np.flatnonzero(clip_mask):
        f = flags[offsets[i]:offsets[i + 1]]
        n = len(f) - w + 1
        if n <= 0:
            continue
        c = np.convolve(f.astype(np.int32), np.ones(w, np.int32), mode="valid")
        hit += int((c > 0).sum())
        tot += n
    return hit / tot if tot else None


def measure(frame_scores, data, every, pos, thr):
    off = data["offsets"]
    cmax = T.clip_max(frame_scores, off)
    hours = T.neg_hours(off, every)
    fa = T.fa_events(frame_scores, off, every, thr)
    hit = int((cmax[pos] >= thr).sum())
    n = int(pos.sum())
    return {"auc": T.auc(cmax[pos], cmax[every]), "recall": hit / n if n else None, "recall_ci": T.wilson(hit, n),
            "n_pos": n, "fa_per_hour": fa / hours if hours else None, "fa_ci": T.garwood(fa, hours),
            "neg_hours": hours}


def run():
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    dev, ev = T.load_split("dev"), T.load_split("eval")
    log(f"dev {len(dev['fnames'])} קטעים, eval {len(ev['fnames'])} ({time.time() - t0:.0f}s)")
    assert not set(dev["uploader"]) & set(ev["uploader"]), "חפיפת מעלים בין dev ל-eval"
    d_every, d_known, d_novel = masks(dev)
    e_every, e_known, e_novel = masks(ev)
    half_a = uploader_halves(dev, rng)
    a_every, b_every, b_known = d_every & half_a, d_every & ~half_a, d_known & ~half_a
    log(f"יומיומי: dev-A {a_every.sum()}, dev-B {b_every.sum()}, eval {e_every.sum()}; "
        f"מוכר eval {e_known.sum()}; חדש eval {e_novel.sum()}")

    fit_idx = frames_of(dev["offsets"], a_every)
    fit_idx = rng.choice(fit_idx, size=min(FIT_FRAMES, len(fit_idx)), replace=False)
    fit = np.asarray(dev["emb"][np.sort(fit_idx)], np.float32)
    bank_idx = rng.choice(len(fit), size=min(BANK_FRAMES, len(fit)), replace=False)

    # ---- בחירה על dev-B: רק יומיומי מול מוכר (החדשים לא נוגעים בבחירה)
    b_clips = b_every | b_known
    b_frames = frames_of(dev["offsets"], b_clips)
    sub = {"offsets": None}
    sizes = np.diff(dev["offsets"])[b_clips]
    sub["offsets"] = np.concatenate([[0], np.cumsum(sizes)])
    b_emb = dev["emb"][b_frames]
    b_ev, b_kn = b_every[b_clips], b_known[b_clips]
    cands = []
    for dims in PCA_DIMS:
        pca = Pca(fit, dims)
        s = maha(pca, b_emb)
        cands.append(("mahalanobis", {"dims": dims}, pca, None, s))
        bank_z = pca.z(fit[bank_idx])
        for k, sk in knn_multi(pca, bank_z, b_emb, KNN_K).items():
            cands.append(("knn", {"dims": dims, "k": k}, pca, bank_z, sk))
        log(f"PCA {dims}: נבדק ({time.time() - t0:.0f}s)")
    tuning = []
    for name, hp, pca, bank_z, s in cands:
        cm = T.clip_max(s, sub["offsets"])
        tuning.append({"method": name, **hp, "auc_known_devB": T.auc(cm[b_kn], cm[b_ev])})
    best = {}
    for name in ("mahalanobis", "knn"):
        i = max((j for j, c in enumerate(cands) if c[0] == name), key=lambda j: tuning[j]["auc_known_devB"])
        name_, hp, pca, bank_z, s = cands[i]
        cm = T.clip_max(s, sub["offsets"])
        thr = T.threshold_for_budget(s, sub["offsets"], b_ev, BUDGET, cm)
        best[name] = {"hp": hp, "pca": pca, "bank_z": bank_z, "thr": thr, "auc_known_devB": tuning[i]["auc_known_devB"]}
        log(f"{name}: נבחר {hp}, AUC מוכר dev-B {tuning[i]['auc_known_devB']:.3f}, סף {thr:.3f}")

    # ---- eval: פעם אחת
    emb = ev["emb"]
    results, scores_eval = {}, {}
    for name, b in best.items():
        s = maha(b["pca"], emb) if name == "mahalanobis" else knn(b["pca"], b["bank_z"], emb, b["hp"]["k"])
        scores_eval[name] = s
        results[name] = {"novel": measure(s, ev, e_every, e_novel, b["thr"]),
                         "known": measure(s, ev, e_every, e_known, b["thr"]), "hp": b["hp"], "threshold": b["thr"],
                         "auc_known_devB": b["auc_known_devB"]}
        log(f"{name} eval: AUC חדש {results[name]['novel']['auc']:.3f}, recall חדש "
            f"{results[name]['novel']['recall']:.1%}, שווא/שעה {results[name]['novel']['fa_per_hour']:.2f}")
    conf, fired, scoring = known_detectors(emb)
    off = ev["offsets"]
    fired_clip = np.maximum.reduceat(fired.astype(np.int8), off[:-1]).astype(bool)
    hours = T.neg_hours(off, e_every)
    base = {"novel": {"auc": T.auc(T.clip_max(conf, off)[e_novel], T.clip_max(conf, off)[e_every]),
                      "recall": float(fired_clip[e_novel].mean()), "n_pos": int(e_novel.sum()),
                      "recall_ci": T.wilson(int(fired_clip[e_novel].sum()), int(e_novel.sum())),
                      "fa_per_hour": T.fa_events(fired.astype(np.float32), off, e_every, 0.5) / hours,
                      "fa_ci": T.garwood(T.fa_events(fired.astype(np.float32), off, e_every, 0.5), hours)}}
    results["known_detectors"] = base
    # קצב משולב של 8 הגלאים על אודיו יומיומי (כל גלאי לבדו כויל ל-~1/שעה - יחד הקצב גבוה יותר)
    n_sc = T.fa_events(scoring.astype(np.float32), off, e_every, 0.5)
    results["combined_fa_everyday"] = {
        "any_detector": base["novel"]["fa_per_hour"], "any_detector_ci": base["novel"]["fa_ci"],
        "scoring_detectors": n_sc / hours, "scoring_detectors_ci": T.garwood(n_sc, hours),
        "assessment_window_s": WINDOW_S,
        "p_scoring_per_assessment": window_hit_rate(scoring, off, e_every, WINDOW_FRAMES)}
    log(f"קצב משולב על יומיומי: כל גלאי {base['novel']['fa_per_hour']:.2f}/שעה, "
        f"גלאים שמוסיפים נקודות {n_sc / hours:.2f}/שעה")
    log(f"baseline (8 הגלאים): AUC חדש {base['novel']['auc']:.3f}, recall חדש {base['novel']['recall']:.1%}")

    per_label = {}
    for lab in sorted(NOVEL):
        m = np.array([lab in ls for ls in ev["labels"]]) & e_novel
        if m.sum() < 5:
            continue
        row = {"n": int(m.sum())}
        for name in best:
            cm = T.clip_max(scores_eval[name], off)
            row[name] = {"auc": T.auc(cm[m], cm[e_every]), "recall": float((cm[m] >= best[name]["thr"]).mean())}
        cmb = T.clip_max(conf, off)
        row["known_detectors"] = {"auc": T.auc(cmb[m], cmb[e_every]), "recall": float(fired_clip[m].mean())}
        per_label[lab] = row

    champ = max(best, key=lambda n: results[n]["novel"]["auc"] or 0)
    cm = T.clip_max(scores_eval[champ], off)
    ood_clip = cm >= best[champ]["thr"]
    only_ood = float((ood_clip & ~fired_clip)[e_novel].mean())
    r = results[champ]["novel"]
    checks = {"novel_auc": (r["auc"], r["auc"] >= ACCEPT["novel_auc_min"]),
              "novel_recall": (r["recall"], r["recall"] >= ACCEPT["novel_recall_min"]),
              "eval_fa": (r["fa_per_hour"], r["fa_per_hour"] <= ACCEPT["eval_fa_max"]),
              "only_ood_share": (only_ood, only_ood >= ACCEPT["only_ood_share_min"])}
    accepted = all(ok for _, ok in checks.values())
    out = {"version": VERSION, "created": time.strftime("%Y-%m-%d"), "seed": SEED, "budget_fa_per_hour": BUDGET,
           "sets": {"known": sorted(KNOWN), "novel": sorted(NOVEL), "ambiguous": sorted(AMBIGUOUS)},
           "counts": {"dev_A_everyday": int(a_every.sum()), "dev_B_everyday": int(b_every.sum()),
                      "dev_B_known": int(b_known.sum()), "eval_everyday": int(e_every.sum()),
                      "eval_known": int(e_known.sum()), "eval_novel": int(e_novel.sum()),
                      "eval_everyday_hours": hours},
           "tuning": tuning, "results": results, "per_label": per_label, "champion": champ,
           "only_ood_share_of_novel": only_ood, "accept": ACCEPT,
           "checks": {k: {"value": v, "pass": ok} for k, (v, ok) in checks.items()}, "accepted": accepted,
           "uploader_overlap": 0}
    out = T._clean(out)
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    if accepted:
        b = best[champ]
        np.savez_compressed(OUT_MODEL, card=np.array([json.dumps({k: out[k] for k in (
            "version", "created", "budget_fa_per_hour", "champion", "accept", "checks")} | {
            "method": champ, "hp": b["hp"], "threshold": b["thr"],
            "eval": results[champ]}, ensure_ascii=False)]),
            mean=b["pca"].mean.astype(np.float32), comp=b["pca"].comp.astype(np.float32),
            scale=b["pca"].scale.astype(np.float32),
            bank=(b["bank_z"].astype(np.float16) if b["bank_z"] is not None else np.zeros((0, 0), np.float16)))
        log(f"נשמר מודל: {OUT_MODEL}")
    elif os.path.exists(OUT_MODEL):
        log("לא עמד בקריטריונים - המודל הקיים לא מוחלף")
    report(out)
    log(f"דו\"ח: {OUT_REPORT} ({time.time() - t0:.0f}s)")
    return out


def _p(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def _f(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def report(out, path=OUT_REPORT):
    R = out["results"]
    L = [f"# צליל חריג לא מוכר (OOD) - {out['version']}", "",
         f"נוצר אוטומטית ע\"י `research_tools/audio_anomaly.py` ({out['created']}). כל מספר כאן מגיע מהריצה.", "",
         "## השאלה",
         "האם מודל שלמד **רק צלילים יומיומיים** יכול לסמן צלילים מתריעים מסוגים שאף גלאי לא אומן עליהם - "
         "ובאיזה מחיר של התראות שווא? והאם זה מוסיף משהו מעבר ל-8 הגלאים הקיימים?", "",
         "## תכנון",
         "- dev של FSD50K חולק לפי מעלה: **A** - התאמת המודל (יומיומי בלבד), **B** - בחירת היפר-פרמטרים (לפי "
         "הקטגוריות **המוכרות** בלבד) וסף של 1 שווא לשעה. eval - פעם אחת בסוף; אין חפיפת מעלים.",
         f"- **חדשים (נמדדים רק על eval, לא השפיעו על שום בחירה):** {', '.join(out['sets']['novel'])}.",
         f"- **מוכרים (מה שהגלאים אומנו עליו):** {', '.join(out['sets']['known'])}.",
         f"- **עמומים (הוצאו משני הצדדים):** {', '.join(out['sets']['ambiguous'])}.",
         f"- כמויות: יומיומי dev-A {out['counts']['dev_A_everyday']}, dev-B {out['counts']['dev_B_everyday']}, "
         f"eval {out['counts']['eval_everyday']} ({out['counts']['eval_everyday_hours']:.1f} שעות); "
         f"מוכר eval {out['counts']['eval_known']}; חדש eval {out['counts']['eval_novel']}.",
         "- **baseline:** \"האם אחד מ-8 הגלאים הקיימים מגיב\" - ההתנהגות בפועל בזמן ריצה.", "",
         "## תוצאות על eval (צלילים חדשים מול יומיומי)", "",
         "| שיטה | היפר-פרמטרים | AUC חדש | recall חדש (95%) | שווא/שעה (95%) | AUC מוכר |",
         "|---|---|---|---|---|---|"]
    for name in ("mahalanobis", "knn", "known_detectors"):
        r = R[name]
        n = r["novel"]
        known_auc = _f(r.get("known", {}).get("auc")) if "known" in r else "(אומנו עליהם)"
        hp = ", ".join(f"{k}={v}" for k, v in (r.get("hp") or {}).items()) or "ספי הגלאים"
        L.append(f"| {name} | {hp} | {_f(n['auc'])} | {_p(n['recall'])} ({_p(n['recall_ci'][0])}–"
                 f"{_p(n['recall_ci'][1])}) | {_f(n['fa_per_hour'], 2)} ({_f(n['fa_ci'][0], 2)}–"
                 f"{_f(n['fa_ci'][1], 2)}) | {known_auc} |")
    cf = R["combined_fa_everyday"]
    L += ["", "### ממצא לוואי: קצב השווא המשולב של 8 הגלאים הקיימים (על היומיומי של eval)",
          f"- כל גלאי כויל לבדו ל-~1 שווא לשעה. **יחד:** {cf['any_detector']:.2f} לשעה (95%: "
          f"{cf['any_detector_ci'][0]:.2f}–{cf['any_detector_ci'][1]:.2f}) שבהן לפחות גלאי אחד מגיב.",
          f"- **רק הגלאים שמוסיפים נקודות** (ירי, פיצוץ, שבירה, אזעקה, צרחה): {cf['scoring_detectors']:.2f} לשעה "
          f"(95%: {cf['scoring_detectors_ci'][0]:.2f}–{cf['scoring_detectors_ci'][1]:.2f}).",
          f"- המנוע רץ רק אחרי טריגר, על חלון של {cf['assessment_window_s']} שניות. נמדד ישירות על כל החלונות "
          f"האפשריים ביומיומי: {100 * cf['p_scoring_per_assessment']:.2f}% מהם מקבלים נקודות מצליל שגוי. "
          "והנקודות לבדן מגיעות לכל היותר ל-MEDIUM (חלון אימות).",
          "", "### לפי סוג צליל חדש (AUC / recall בסף)", "",
          "| צליל | n | mahalanobis | knn | 8 הגלאים |", "|---|---|---|---|---|"]
    for lab, row in out["per_label"].items():
        cells = [f"{_f(row[m]['auc'], 2)} / {_p(row[m]['recall'])}" for m in ("mahalanobis", "knn", "known_detectors")]
        L.append(f"| {lab} | {row['n']} | " + " | ".join(cells) + " |")
    L += ["", f"**נבחר:** {out['champion']}. מתוך הצלילים החדשים, {_p(out['only_ood_share_of_novel'])} סומנו רק ע\"י "
          "גלאי החריגות (אף אחד מ-8 הגלאים לא הגיב).", "",
          "## קריטריוני קבלה (נקבעו לפני ההרצה)", "", "| בדיקה | ערך | סף | עבר |", "|---|---|---|---|"]
    names = {"novel_auc": ("AUC חדש", f"≥ {out['accept']['novel_auc_min']}"),
             "novel_recall": ("recall חדש בסף", f"≥ {_p(out['accept']['novel_recall_min'])}"),
             "eval_fa": ("שווא/שעה על eval", f"≤ {out['accept']['eval_fa_max']}"),
             "only_ood_share": ("תרומה מעבר לגלאים", f"≥ {_p(out['accept']['only_ood_share_min'])}")}
    for k, c in out["checks"].items():
        v = c["value"]
        vs = _p(v) if k in ("novel_recall", "only_ood_share") else _f(v, 3)
        L.append(f"| {names[k][0]} | {vs} | {names[k][1]} | {'✅' if c['pass'] else '❌'} |")
    L += ["", "**החלטה:** " + ("עומד בקריטריונים - משולב ב-`audio_intel` כ**מידע בלבד** (\"צליל חריג - לבדיקה\"), "
                               "בלי נקודות סיכון ובלי השפעה על SOS." if out["accepted"] else
                               "**לא עומד בקריטריונים - לא משולב באפליקציה.** נשאר כמחקר."), "",
          "## מגבלות",
          "- \"חריג\" ≠ \"מסוכן\": המודל מסמן כל מה שלא דומה ליומיומי, כולל כלי נגינה נדירים או בעלי חיים.",
          "- \"יומיומי\" כאן = FSD50K (הקלטות Freesound), לא טלפון בכיס ברחוב. ההתפלגות בשטח שונה.",
          "- הקטגוריות החדשות נבחרו על ידינו כ\"מתריעות\"; זו הגדרה, לא אמת מוחלטת.",
          "- AUC מחושב על שיא-הקטע; קטעים עם כמה תוויות (למשל צעקה + דיבור) מקשים בשני הכיוונים.", "",
          "## בחירת היפר-פרמטרים (dev-B, מוכרים בלבד)", "", "| שיטה | dims | k | AUC מוכר dev-B |", "|---|---|---|---|"]
    for t in out["tuning"]:
        L.append(f"| {t['method']} | {t['dims']} | {t.get('k', '—')} | {_f(t['auc_known_devB'])} |")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        run()
    else:
        print(__doc__)

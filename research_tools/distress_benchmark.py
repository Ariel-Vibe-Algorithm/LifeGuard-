# -*- coding: utf-8 -*-
"""
distress_benchmark.py - השוואת גלאי מצוקה על FSD50K (eval): עוצמת רעש (המנגנון הקיים) מול YAMNet
zero-shot מול מסווג קטן מעל embeddings של YAMNet.

הרצה:
    python -m research_tools.distress_benchmark --fsd50k research/samples/fsd50k

מתודולוגיה (חשוב כדי שהמספרים יהיו הוגנים):
- חיובי = קטע שתויג ע"י בני אדם כ-Screaming / Shout / Yell / Crying_and_sobbing. התיוג ברמת קטע.
- שלילי = כל שאר הקטעים (~27 שעות של צלילים אמיתיים מגוונים מ-Freesound), כולל "שליליים קשים":
  צחוק, עידוד, קהל, ילדים מדברים, שירה, דיבור.
- המסווג נבדק בהערכה צולבת (5 קבוצות) שמחולקת לפי **מעלה הקובץ** (uploader): אותו אדם לא מופיע
  גם באימון וגם בבדיקה, כדי שהמודל לא "ילמד את המיקרופון של המעלה". כל ציון של המסווג הוא
  out-of-fold - על קטע שהמודל לא ראה באימון.
- כל הגלאים נמדדים באותה פונקציה (acoustic_eval.evaluate): recall מול התראות שווא לשעה.
מגבלות: Shout/Yell כוללים גם צעקות לא-מצוקתיות (ספורט, קריאה); FSD50K אינו הקלטת טלפון ברחוב;
התוצאה היא השוואה יחסית בין גלאים, לא הבטחת ביצועים בשטח.
"""

import argparse
import csv
import hashlib
import json
import os
import time

import numpy as np

from research_tools import acoustic_eval as E
from research_tools import yamnet_detector as Y
from research_tools.split_zip import SplitZip

POSITIVE = ("Screaming", "Crying_and_sobbing", "Shout", "Yell")
HARD_NEGATIVE = ("Laughter", "Giggle", "Chuckle_and_chortle", "Cheering", "Applause", "Crowd",
                 "Child_speech_and_kid_speaking", "Gasp", "Singing", "Whispering", "Chatter",
                 "Conversation", "Speech", "Music")
BUDGETS = (1.0, 0.5, 0.1)
FOLDS = 5
NEG_FRAMES_PER_FOLD = 60000


def label_clip(labels):
    for p in POSITIVE:
        if p in labels:
            return True, p
    for n in HARD_NEGATIVE:
        if n in labels:
            return False, n
    return False, "other"


def fold_of(uploader, k=FOLDS):
    return int(hashlib.sha1(uploader.encode("utf-8")).hexdigest(), 16) % k


def load_index(root, split="eval"):
    gt = os.path.join(root, "meta", "FSD50K.ground_truth", f"{split}.csv")
    info = json.load(open(os.path.join(root, "meta", "FSD50K.metadata", f"{split}_clips_info_FSD50K.json"),
                          encoding="utf-8"))
    rows = []
    with open(gt, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            labels = r["labels"].split(",")
            pos, lab = label_clip(labels)
            meta = info.get(r["fname"], {})
            rows.append({"fname": r["fname"], "positive": pos, "label": lab, "split": r.get("split") or split,
                         "labels": labels,
                         "uploader": meta.get("uploader", "?"), "license": meta.get("license", "?")})
    return rows


def extract_features(root, rows, cache_dir, log=print, split="eval"):
    """לכל קטע: ציוני RMS, zero-shot, embeddings ותיוג מסגרות. האודיו עצמו לא נשמר בזיכרון."""
    cache = Y.FeatureCache(cache_dir)
    rms_det = E.RmsDetector(window_seconds=0.96, hop_seconds=Y.HOP_SECONDS)   # אותו צעד כמו YAMNet
    feats = {}
    t0 = time.time()
    with SplitZip(os.path.join(root, "raw", f"FSD50K.{split}_audio.zip")) as z:
        names = {os.path.basename(n)[:-4]: n for n in z.names() if n.endswith(".wav")}
        for i, r in enumerate(rows):
            x, sr = E._read_wav_bytes(z.read(names[r["fname"]]), resample_to=None)
            x16 = Y.to_16k(x, sr)
            f = cache.get(x16, Y.TARGET_SR)
            feats[r["fname"]] = {"rms": rms_det.frame_scores(x16, Y.TARGET_SR)[1].astype(np.float32),
                                 "zero_shot": f["zero_shot"], "emb": f["emb"], "event": f["event"],
                                 "seconds": len(x16) / Y.TARGET_SR}
            if (i + 1) % 500 == 0:
                log(f"  {i + 1}/{len(rows)} קטעים ({time.time() - t0:.0f}s)")
    return feats


def train(rows, feats, rng, max_neg=NEG_FRAMES_PER_FOLD):
    Xp = [feats[r["fname"]]["emb"][feats[r["fname"]]["event"]] for r in rows if r["positive"]]
    Xn = np.concatenate([feats[r["fname"]]["emb"] for r in rows if not r["positive"]])
    if len(Xn) > max_neg:
        Xn = Xn[rng.choice(len(Xn), max_neg, replace=False)]
    Xp = np.concatenate(Xp)
    X = np.concatenate([Xp, Xn]).astype(np.float32)
    y = np.r_[np.ones(len(Xp)), np.zeros(len(Xn))]
    return Y.LogisticModel(l2=1e-2, epochs=300).fit(X, y)


def crossval_scores(rows, feats, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(FOLDS):
        tr = [r for r in rows if fold_of(r["uploader"]) != k]
        te = [r for r in rows if fold_of(r["uploader"]) == k]
        clf = train(tr, feats, rng)
        for r in te:
            out[r["fname"]] = clf.predict_proba(feats[r["fname"]]["emb"].astype(np.float32))
    return out


def run(root, out_md, log=print):
    rows = load_index(root)
    log(f"FSD50K eval: {len(rows)} קטעים, {sum(r['positive'] for r in rows)} חיוביים")
    feats = extract_features(root, rows, os.path.join(root, "cache"), log)
    clips = [E.Clip(r["fname"], None, Y.TARGET_SR, r["positive"], r["label"],
                    {"seconds": feats[r["fname"]]["seconds"]}) for r in rows]
    log("הערכה צולבת לפי מעלה...")
    oof = crossval_scores(rows, feats)
    dets = [E.PrecomputedDetector("rms_baseline (המנגנון הקיים)", {k: v["rms"] for k, v in feats.items()}, Y.HOP_SECONDS),
            E.PrecomputedDetector("yamnet_zero_shot", {k: v["zero_shot"] for k, v in feats.items()}, Y.HOP_SECONDS),
            E.PrecomputedDetector("yamnet_embed_lr (out-of-fold)", oof, Y.HOP_SECONDS)]
    results = [E.evaluate(d, clips, budgets=BUDGETS) for d in dets]
    # מודל סופי על כל הנתונים - לשימוש בהמשך (המספרים בדו"ח הם מההערכה הצולבת, לא ממנו)
    final = train(rows, feats, np.random.default_rng(1))
    final.info.update({"dataset": "FSD50K eval", "positive_labels": list(POSITIVE), "frame_hop_s": Y.HOP_SECONDS})
    model_path = os.path.join(os.path.dirname(Y.MODEL_DIR), "distress_lr_fsd50k.npz")
    final.save(model_path)
    md = report(rows, results)
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    slim = [{k: v for k, v in r.items() if k != "curve"} for r in results]
    with open(os.path.join(root, "results.json"), "w", encoding="utf-8") as fh:
        json.dump({"results": slim, "curves": {r["detector"]: r["curve"] for r in results}}, fh,
                  ensure_ascii=False, indent=1)
    log(md)
    return results


def report(rows, results):
    from collections import Counter
    lic = Counter(r["license"].rstrip("/").split("/")[-2] if "licenses" in r["license"] or "publicdomain" in r["license"]
                  else r["license"] for r in rows)
    pos = Counter(r["label"] for r in rows if r["positive"])
    hard = Counter(r["label"] for r in rows if not r["positive"])
    lines = ["# השוואת גלאי מצוקה קולית - FSD50K eval", "",
             "נוצר אוטומטית ע\"י `python -m research_tools.distress_benchmark`. מתודולוגיה ומגבלות: ראו docstring בקובץ.", "",
             f"- קטעים: {len(rows)} · חיוביים: {sum(pos.values())} ({', '.join(f'{k} {v}' for k, v in pos.items())})",
             f"- שליליים קשים: {', '.join(f'{k} {v}' for k, v in hard.most_common() if k != 'other')}; אחר: {hard['other']}",
             f"- הערכה צולבת: {FOLDS} קבוצות לפי מעלה הקובץ ב-Freesound ({len({r['uploader'] for r in rows})} מעלים)",
             f"- רישיונות הקטעים: {dict(lic)} (CC0/CC-BY/CC-BY-NC/Sampling+; שימוש מחקרי לא מסחרי, ייחוס לפי FSD50K)",
             "- תקציבי התראות שווא נמדדים על אודיו שלילי מגוון, לא על יום אמיתי עם טלפון בכיס.", ""]
    for r in results:
        lines.append(E.report_markdown(r))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="FSD50K distress detector benchmark")
    ap.add_argument("--fsd50k", default=os.path.join("research", "samples", "fsd50k"))
    ap.add_argument("--out", default=os.path.join("research", "acoustic_benchmark_fsd50k.md"))
    a = ap.parse_args(argv)
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # קונסולת Windows בקידוד cp1255
    except (AttributeError, ValueError):
        pass
    run(a.fsd50k, a.out)


if __name__ == "__main__":
    main()

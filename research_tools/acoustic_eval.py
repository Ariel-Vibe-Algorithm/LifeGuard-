# -*- coding: utf-8 -*-
"""
acoustic_eval.py - הערכה אחידה של גלאי מצוקה קולית.

למה: בלי מדידה אין דרך לדעת אם מודל חדש טוב יותר מהקיים. כל גלאי (המנגנון הנוכחי של עוצמת רעש,
YAMNet, מודל מכויל) נמדד על אותם נתונים ובאותן מטריקות:

- recall: אחוז קטעי המצוקה (positive) שבהם הגלאי התריע.
- false alarms / hour: התראות על אודיו שאינו מצוקה, לשעת אודיו. זו המטריקה שקובעת אם אנשים
  ישאירו את הזיהוי דולק. כל רצף מסגרות מעל הסף (עם מיזוג פערים קצרים) = התראה אחת.
- recall @ FA budget: הסף הטוב ביותר שעומד בתקציב התראות שווא נתון (למשל 1 ל-50 שעות), ומה
  ה-recall בו.

חשוב: המספרים תקפים רק לנתונים שנמדדו. סט קטן / מוקלט במעבדה לא מייצג חיים אמיתיים - הדו"ח
מציין כמה שעות אודיו שלילי ובכמה קטעים חיוביים הוא מבוסס.
"""

import csv
import json
import math
import os
import zipfile

import numpy as np

TARGET_SR = 16000
DEFAULT_BUDGETS = (1.0, 0.1, 0.02)   # התראות שווא לשעה: 1/שעה, 1 ל-10 שעות, 1 ל-50 שעות


# ===================================================================== נתונים
class Clip:
    __slots__ = ("id", "audio", "sr", "positive", "label", "meta")

    def __init__(self, cid, audio, sr, positive, label, meta=None):
        self.id, self.audio, self.sr, self.positive, self.label, self.meta = cid, audio, sr, positive, label, meta or {}

    @property
    def seconds(self):
        if self.audio is None:                 # קטע שהאודיו שלו שוחרר מהזיכרון אחרי חישוב ציונים
            return float(self.meta["seconds"])
        return len(self.audio) / float(self.sr)


def resample(x, sr, target=TARGET_SR):
    """דגימה מחדש לינארית (מספיק למדדי עוצמה; לגלאי נוירוני עדיף resample_poly כשיש scipy)."""
    if sr == target or len(x) == 0:
        return x.astype(np.float32)
    n = int(round(len(x) * target / sr))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _read_wav_bytes(data, resample_to=TARGET_SR):
    """resample_to=None -> קצב הדגימה המקורי (כדי שגלאי נוירוני יעשה resample איכותי בעצמו)."""
    import io
    import wave
    with wave.open(io.BytesIO(data), "rb") as w:
        ch, width, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if width == 2:
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 4:
        x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"רוחב דגימה לא נתמך: {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if resample_to is None:
        return x.astype(np.float32), sr
    return resample(x, sr, resample_to), resample_to


def load_collection(path, positive_groups=("positive",), include_groups=None):
    """תיקיית research_data/acoustic או zip שיוצא מ-export_training_set."""
    clips = []
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            rows = [json.loads(line) for line in z.read("manifest.jsonl").decode("utf-8").splitlines() if line]
            for r in rows:
                if include_groups and r["group"] not in include_groups:
                    continue
                x, sr = _read_wav_bytes(z.read(r["file"]))
                clips.append(Clip(r["file"], x, sr, r["group"] in positive_groups, r["label"], r))
        return clips
    pdir = os.path.join(path, "participants")
    for pid in sorted(os.listdir(pdir)):
        mpath = os.path.join(pdir, pid, "manifest.jsonl")
        if not os.path.exists(mpath):
            continue
        with open(mpath, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if include_groups and r["group"] not in include_groups:
                    continue
                with open(os.path.join(pdir, pid, r["file"]), "rb") as wf:
                    x, sr = _read_wav_bytes(wf.read())
                clips.append(Clip(f"{pid}/{r['file']}", x, sr, r["group"] in positive_groups, r["label"], r))
    return clips


def load_csv_manifest(csv_path, base_dir=None):
    """מאגר חיצוני: CSV עם עמודות path,label,positive (1/0). path יחסי ל-base_dir."""
    base = base_dir or os.path.dirname(os.path.abspath(csv_path))
    clips = []
    with open(csv_path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            with open(os.path.join(base, r["path"]), "rb") as wf:
                x, sr = _read_wav_bytes(wf.read())
            clips.append(Clip(r["path"], x, sr, str(r["positive"]).strip() in ("1", "true", "True"), r["label"], r))
    return clips


# ===================================================================== גלאים
class Detector:
    """ממשק: frame_scores(x, sr) -> (times_sec, scores). ציון גבוה = יותר חשד למצוקה."""
    name = "base"
    hop_seconds = 0.25

    def frame_scores(self, x, sr):
        raise NotImplementedError

    def scores_for(self, clip):
        return self.frame_scores(clip.audio, clip.sr)[1]


class PrecomputedDetector(Detector):
    """ציונים שחושבו מראש לפי מזהה קטע (למשל תחזיות out-of-fold בהערכה צולבת)."""

    def __init__(self, name, scores_by_id, hop_seconds):
        self.name, self.scores, self.hop_seconds = name, scores_by_id, hop_seconds

    def scores_for(self, clip):
        return self.scores[clip.id]


class RmsDetector(Detector):
    """המנגנון הקיים (risk_module): עוצמת RMS בחלון. בסיס להשוואה - לא גלאי מצוקה אמיתי."""
    name = "rms_baseline"

    def __init__(self, window_seconds=0.5, hop_seconds=0.25):
        self.window_seconds, self.hop_seconds = window_seconds, hop_seconds

    def frame_scores(self, x, sr):
        win, hop = int(self.window_seconds * sr), int(self.hop_seconds * sr)
        if len(x) < win:
            x = np.pad(x, (0, win - len(x)))
        n = 1 + (len(x) - win) // hop
        idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
        frames = x[idx]
        return np.arange(n) * self.hop_seconds, np.sqrt(np.mean(frames ** 2, axis=1))


# ===================================================================== מטריקות
def events(scores, threshold, hop_seconds, merge_gap_seconds=1.0, min_frames=1):
    """רצפי מסגרות מעל הסף -> רשימת (start_frame, end_frame). פער קצר מ-merge_gap מאוחד לאירוע אחד."""
    above = scores >= threshold
    out, start, last = [], None, None
    gap = max(0, int(round(merge_gap_seconds / hop_seconds)))
    for i, a in enumerate(above):
        if a:
            if start is None:
                start = i
            elif i - last - 1 > gap:
                out.append((start, last))
                start = i
            last = i
    if start is not None:
        out.append((start, last))
    return [e for e in out if e[1] - e[0] + 1 >= min_frames]


def _count_events(s, t, gap_frames):
    """כמו len(events(...)) עם min_frames=1, וקטורי (מהיר על שעות של אודיו)."""
    idx = np.flatnonzero(s >= t)
    if len(idx) == 0:
        return 0
    return 1 + int(np.count_nonzero(np.diff(idx) - 1 > gap_frames))


MAX_THRESHOLDS = 600


def evaluate(detector, clips, thresholds=None, budgets=DEFAULT_BUDGETS, merge_gap_seconds=1.0):
    scored = [(c, np.asarray(detector.scores_for(c), dtype=np.float64)) for c in clips]
    pos = [(c, s) for c, s in scored if c.positive]
    neg = [(c, s) for c, s in scored if not c.positive]
    neg_hours = sum(c.seconds for c, _ in neg) / 3600.0
    gap = max(0, int(round(merge_gap_seconds / detector.hop_seconds)))
    pos_peaks = np.array([float(s.max()) if len(s) else -np.inf for _, s in pos])
    neg_peaks = np.array([float(s.max()) if len(s) else -np.inf for _, s in neg])
    if thresholds is None:
        # recall משתנה רק בשיא של קטע חיובי, ומספר ההתראות - בשיאים של קטעים שליליים. לכן הספים
        # המועמדים כוללים את השיאים. (באג שנמצא בבדיקה: דגימת quantiles בלבד דילגה על הקטעים
        # החיוביים כשהם מעט מאוד מסגרות לעומת שעות של רקע.) בסט גדול - quantiles צפופים של השיאים.
        allv = np.concatenate([s for _, s in scored]) if scored else np.array([0.0])
        peaks = np.concatenate([pos_peaks, neg_peaks])
        peaks = peaks[np.isfinite(peaks)]
        if len(peaks) > MAX_THRESHOLDS:
            q = np.linspace(0, 1, MAX_THRESHOLDS // 2)
            parts = [np.quantile(p, q) for p in (pos_peaks[np.isfinite(pos_peaks)],
                                                 neg_peaks[np.isfinite(neg_peaks)]) if len(p)]
            peaks = np.concatenate(parts)
        thresholds = np.unique(np.concatenate([np.quantile(allv, np.linspace(0, 1, 101)), peaks,
                                               [float(np.max(allv)) + 1e-9]]))
    # כל השליליים במערך אחד, מופרדים ב"חור" ארוך מפער המיזוג - אירוע לא חוצה בין קטעים
    sep = np.full(gap + 2, -np.inf)
    neg_cat = np.concatenate([x for _, s in neg for x in (s, sep)]) if neg else np.array([])
    curve = []
    for t in thresholds:
        tp = int(np.count_nonzero(pos_peaks >= t))
        fa = _count_events(neg_cat, t, gap) if neg else 0
        curve.append({"threshold": float(t), "recall": tp / len(pos) if pos else None,
                      "false_alarms": fa, "fa_per_hour": fa / neg_hours if neg_hours else None,
                      "neg_clip_fpr": int(np.count_nonzero(neg_peaks >= t)) / len(neg) if neg else None})
    at_budget = {}
    for b in budgets:
        ok = [p for p in curve if p["fa_per_hour"] is not None and p["fa_per_hour"] <= b and p["recall"] is not None]
        # אותו recall -> הסף הגבוה יותר (פחות התראות שווא)
        best = max(ok, key=lambda p: (p["recall"], p["threshold"])) if ok else None
        if best is not None:
            best = dict(best, **_breakdown(best["threshold"], pos, neg, gap))
        at_budget[str(b)] = best
    by_label = {}
    for c, _ in scored:
        by_label.setdefault(c.label, {"clips": 0, "positive": c.positive, "seconds": 0.0})
        by_label[c.label]["clips"] += 1
        by_label[c.label]["seconds"] += c.seconds
    return {"detector": detector.name, "positive_clips": len(pos), "negative_clips": len(neg),
            "negative_hours": round(neg_hours, 4), "by_label": by_label, "curve": curve,
            "recall_at_fa_budget": at_budget,
            "warning": _sample_warning(len(pos), neg_hours)}


def evaluate_at(detector, clips, threshold, merge_gap_seconds=1.0):
    """ביצועים בסף *קבוע מראש* (שנבחר על נתונים אחרים) - המספר הכן לדיווח, בלי לבחור סף על סט הבדיקה."""
    scored = [(c, np.asarray(detector.scores_for(c), dtype=np.float64)) for c in clips]
    pos = [(c, s) for c, s in scored if c.positive]
    neg = [(c, s) for c, s in scored if not c.positive]
    gap = max(0, int(round(merge_gap_seconds / detector.hop_seconds)))
    hours = sum(c.seconds for c, _ in neg) / 3600.0
    tp = sum(1 for _, s in pos if len(s) and s.max() >= threshold)
    fa = sum(_count_events(s, threshold, gap) for _, s in neg)
    out = {"detector": detector.name, "threshold": float(threshold), "recall": tp / len(pos) if pos else None,
           "recall_ci95": wilson_interval(tp, len(pos)), "false_alarms": fa,
           "fa_per_hour": fa / hours if hours else None, "negative_hours": round(hours, 3),
           "positive_clips": len(pos)}
    out.update(_breakdown(threshold, pos, neg, gap))
    return out


def threshold_for_budget(detector, clips, budget):
    """הסף הנמוך ביותר שעומד בתקציב התראות השווא על הנתונים האלה (לכיול על סט אימות)."""
    res = evaluate(detector, clips, budgets=(budget,))
    p = res["recall_at_fa_budget"][str(budget)]
    return None if p is None else p["threshold"]


def _breakdown(t, pos, neg, gap):
    """בסף נתון: recall לכל סוג מצוקה, והתראות שווא לכל סוג צליל שלילי (איפה הגלאי טועה)."""
    rec, fa = {}, {}
    for c, s in pos:
        r = rec.setdefault(c.label, [0, 0])
        r[0] += int(len(s) > 0 and s.max() >= t)
        r[1] += 1
    for c, s in neg:
        f = fa.setdefault(c.label, [0, 0.0])
        f[0] += _count_events(s, t, gap)
        f[1] += c.seconds
    return {"recall_by_label": {k: {"detected": a, "clips": n} for k, (a, n) in rec.items()},
            "fa_by_label": {k: {"false_alarms": a, "hours": round(sec / 3600, 4)}
                            for k, (a, sec) in sorted(fa.items(), key=lambda kv: -kv[1][0]) if a}}


def _sample_warning(n_pos, neg_hours):
    parts = []
    if n_pos < 100:
        parts.append(f"רק {n_pos} קטעים חיוביים - רווח סמך רחב מאוד ל-recall")
    if neg_hours < 50:
        parts.append(f"רק {neg_hours:.2f} שעות אודיו שלילי - אי אפשר למדוד באמינות 1 התראת שווא ל-50 שעות")
    return "; ".join(parts) or None


def wilson_interval(k, n, z=1.96):
    """רווח סמך 95% לשיעור (ל-recall על מעט דוגמאות)."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def report_markdown(result):
    lines = [f"## {result['detector']}",
             f"- קטעים חיוביים: {result['positive_clips']} · קטעים שליליים: {result['negative_clips']} "
             f"({result['negative_hours']} שעות)"]
    if result["warning"]:
        lines.append(f"- ⚠️ {result['warning']}")
    lines.append("")
    lines.append("| תקציב התראות שווא לשעה | סף | recall | רווח סמך 95% | התראות שווא בפועל לשעה |")
    lines.append("|---|---|---|---|---|")
    for b, p in result["recall_at_fa_budget"].items():
        if p is None:
            lines.append(f"| {b} | — | אין סף שעומד בתקציב | — | — |")
            continue
        k = round(p["recall"] * result["positive_clips"])
        ci = wilson_interval(k, result["positive_clips"])
        lines.append(f"| {b} | {p['threshold']:.4g} | {p['recall']:.2%} | "
                     f"{ci[0]:.0%}-{ci[1]:.0%} | {p['fa_per_hour']:.3g} |")
    for b, p in result["recall_at_fa_budget"].items():
        if not p or "recall_by_label" not in p:
            continue
        rec = ", ".join(f"{k} {v['detected']}/{v['clips']}" for k, v in sorted(p["recall_by_label"].items()))
        fa = ", ".join(f"{k} {v['false_alarms']}" for k, v in list(p["fa_by_label"].items())[:8]) or "אין"
        lines.append(f"- תקציב {b}: זוהו לפי סוג: {rec}. התראות שווא בעיקר מ: {fa}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="LifeGuard acoustic detector evaluation")
    ap.add_argument("data", help="research_data/acoustic | export.zip | manifest.csv")
    ap.add_argument("--detector", default="rms", choices=("rms", "yamnet", "yamnet_lr"))
    ap.add_argument("--model", help="yamnet_lr: נתיב ל-.npz שנשמר ב-train_distress")
    ap.add_argument("--out", help="JSON output path")
    a = ap.parse_args(argv)
    clips = load_csv_manifest(a.data) if a.data.endswith(".csv") else load_collection(a.data)
    if a.detector == "rms":
        det = RmsDetector()
    else:
        from research_tools import yamnet_detector as Y
        det = (Y.YamnetClassDetector() if a.detector == "yamnet"
               else Y.EmbeddingClassifierDetector(Y.LogisticModel.load(a.model)))
    res = evaluate(det, clips)
    print(report_markdown(res))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()

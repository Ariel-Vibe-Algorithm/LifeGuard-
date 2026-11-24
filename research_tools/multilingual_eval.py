# -*- coding: utf-8 -*-
"""
multilingual_eval.py - מדידת גלאי המצוקה הקיימים על דאטה רב-לשוני (התראות שווא בדיבור רגיל לפי שפה,
recall על קולות מצוקה מבוימים, וזיהוי מילות מצוקה ב-ASR המקומי).

הרצה (מהשורש של המאגר):
    python -m research_tools.multilingual_eval features            # YAMNet/RMS לכל הקטעים (מטמון)
    python -m research_tools.multilingual_eval asr --lang he_il     # תמלול he/en/ru של FLEURS (ניתן לחידוש)
    python -m research_tools.multilingual_eval report               # טבלאות -> JSON + Markdown

נתונים (research_data/audio/, לא במאגר; ראו MANIFEST.json שם):
- google/fleurs test: he_il, ar_eg, ru_ru, am_et, en_us (CC-BY-4.0) - דיבור רגיל = שליליים.
- VIVAE (Zenodo 4066235, CC-BY-NC-4.0) - קולות לא-מילוליים מבוימים: fear/pain/anger (חיוביים)
  ו-achievement/pleasure/surprise (שליליים קשים: "צעקת שמחה").
- CREMA-D (ODbL, מראה myleslinder/crema-d) - דיבור רגשי מבוים באנגלית, 6 רגשות x עוצמה.
- RESD test (Aniemore/resd, MIT) - דיאלוגים רגשיים מאולתרים ברוסית.
- DataHive Arabic multi-dialect emotional demo (CC-BY-NC-4.0) - ערבית ב-4 דיאלקטים, angry/happy/neutral/sad.

עקרונות (כמו distress_benchmark):
- ספים *קבועים מראש* (שכוילו על FSD50K) - לא בוחרים סף על הנתונים האלה. זה המספר הכן.
- התראה = רצף מסגרות מעל הסף (מיזוג פערים < 1 שנ'); התראות לשעה עם רווח סמך פואסוני מדויק.
- recall לפי קטע (שיא הציון בקטע >= סף) עם רווח Wilson.
- דיבור מוקרא (FLEURS) הוא לא יום אמיתי עם טלפון בכיס: זה *חסם תחתון* לקושי.
"""

import argparse
import csv
import io
import json
import math
import os
import sys
import tarfile
import time
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from research_tools import acoustic_eval as E  # noqa: E402
from research_tools import yamnet_detector as Y  # noqa: E402

DATA = os.path.join(ROOT, "research_data", "audio")
CACHE = os.path.join(DATA, "cache")
SCORES = os.path.join(DATA, "scores")
FLEURS_LANGS = ("he_il", "ar_eg", "ru_ru", "am_et", "en_us")
GAP_FRAMES = max(0, int(round(1.0 / Y.HOP_SECONDS)))

# ספים קבועים - מקור כל סף מתועד (לא נבחרים על הנתונים כאן)
HEAD_PATH = os.path.join(ROOT, "models", "distress_head.npz")
LR_PATH = os.path.join(ROOT, "research", "models", "distress_lr_fsd50k.npz")
OPERATING_POINTS = [
    # (שם, מפתח ציון, סף, מקור)
    ("rms_app (RMS>=0.15)", "rms", 0.15, "config.LOUD_NOISE_RMS_THRESHOLD"),
    ("rms @FSD50K 1/h", "rms", 0.7499, "acoustic_benchmark_fsd50k.md"),
    ("yamnet_zero_shot @1/h", "zs", 0.8647, "acoustic_benchmark_fsd50k.md"),
    ("yamnet_zero_shot @0.1/h", "zs", 0.9933, "acoustic_benchmark_fsd50k.md"),
    ("distress_lr_fsd50k @1/h", "lr", 0.9977, "acoustic_benchmark_fsd50k.md (OOF)"),
    ("distress_lr_fsd50k @0.1/h", "lr", 0.9992, "acoustic_benchmark_fsd50k.md (OOF)"),
    ("distress_head (באפליקציה)", "head", None, "models/distress_head.npz threshold_logit"),
]


# ===================================================================== קריאת אודיו
def decode_audio(data):
    """bytes -> (float32 מונו, sr). WAV דרך acoustic_eval; FLAC/MP3 - לא נתמך בלי ספריות נוספות."""
    if data[:4] == b"RIFF":
        from scipy.io import wavfile                # גם WAV float (FLEURS) ו-24 ביט
        try:
            sr, x = wavfile.read(io.BytesIO(data))
        except ValueError:                          # קובץ קטוע (ה-data קצר מהמוצהר) - קוראים מה שיש
            sr, x = _read_truncated_wav(data)
            TRUNCATED.append(len(data))
        if x.dtype.kind == "i":
            x = x.astype(np.float32) / float(np.iinfo(x.dtype).max + 1)
        elif x.dtype.kind == "u":
            x = (x.astype(np.float32) - 128.0) / 128.0
        x = x.astype(np.float32)
        if x.ndim > 1:
            x = x.mean(axis=1)
        return x, int(sr)
    raise ValueError("פורמט אודיו לא נתמך (לא WAV): " + repr(data[:4]))


TRUNCATED = []


def _read_truncated_wav(data):
    import struct
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            tag, ch, sr, _br, align, bits = struct.unpack("<HHIIHH", body[:16])
            fmt = (tag, ch, sr, align, bits)
        elif cid == b"data" and fmt:
            tag, ch, sr, align, bits = fmt
            body = body[:len(body) - len(body) % align]
            dt = {(3, 32): np.float32, (3, 64): np.float64, (1, 16): np.int16, (1, 32): np.int32}[(tag, bits)]
            x = np.frombuffer(body, dtype=dt)
            return sr, (x.reshape(-1, ch) if ch > 1 else x)
        pos += 8 + size + (size & 1)
    raise ValueError("WAV פגום")


def _pyarrow():
    extra = os.environ.get("LG_PYLIB")             # pyarrow מותקן מחוץ ל-venv (--target)
    if extra and extra not in sys.path:
        sys.path.insert(0, extra)
    import pyarrow.parquet as pq
    return pq


def iter_fleurs(lang, split="test"):
    base = os.path.join(DATA, "fleurs", lang)
    meta = {}
    with open(os.path.join(base, f"{split}.tsv"), encoding="utf-8") as fh:
        for r in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            meta[r[1]] = {"sent_id": r[0], "ref": r[2], "gender": r[-1] if len(r) > 6 else None}
    with tarfile.open(os.path.join(base, f"{split}.tar.gz"), "r:gz") as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith(".wav"):
                continue
            fn = os.path.basename(m.name)
            x, sr = decode_audio(tf.extractfile(m).read())
            info = meta.get(fn, {})
            sfx = "" if split == "test" else "_" + split
            yield {"id": f"fleurs_{lang}{sfx}/{fn}", "dataset": f"fleurs_{lang}{sfx}", "lang": lang.split("_")[0],
                   "split": split,
                   "category": "speech", "positive": False, "ref": info.get("ref", ""),
                   "speaker_hint": info.get("gender")}, x, sr


VIVAE_POS = ("fear", "pain", "anger")


def iter_vivae():
    with zipfile.ZipFile(os.path.join(DATA, "vivae", "VIVAE.zip")) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".wav") and "__MACOSX" not in n]
        core = {os.path.basename(n) for n in names if "/core_set/" in n}
        for n in sorted(names):
            if "/full_set/" not in n:            # core_set הוא תת-קבוצה של full_set (אותם קבצים)
                continue
            parts = os.path.basename(n)[:-4].split("_")
            if len(parts) < 4:
                continue
            spk, emo, inten = parts[0], parts[1], parts[2]
            x, sr = decode_audio(z.read(n))
            yield {"id": "vivae/" + os.path.basename(n), "dataset": "vivae", "lang": "none",
                   "category": f"{emo}/{inten}", "emotion": emo, "intensity": inten, "speaker": spk,
                   "positive": emo in VIVAE_POS, "core": os.path.basename(n) in core}, x, sr


CREMA_EMO = {"ANG": "anger", "DIS": "disgust", "FEA": "fear", "HAP": "happy", "NEU": "neutral", "SAD": "sad"}
CREMA_INT = {"LO": "low", "MD": "medium", "HI": "high", "XX": "unspecified"}


def iter_cremad():
    with tarfile.open(os.path.join(DATA, "cremad", "crema_d.tar.gz"), "r:gz") as tf:
        for m in tf:
            if not m.isfile() or not m.name.endswith(".wav"):
                continue
            fn = os.path.basename(m.name)
            p = fn[:-4].split("_")
            if len(p) != 4:
                continue
            emo, inten = CREMA_EMO.get(p[2], p[2]), CREMA_INT.get(p[3], p[3])
            data = tf.extractfile(m).read()
            try:
                x, sr = decode_audio(data)
            except Exception:  # noqa: BLE001 - קובץ פגום ידוע ב-CREMA-D
                continue
            yield {"id": "cremad/" + fn, "dataset": "cremad", "lang": "en", "category": f"{emo}/{inten}",
                   "emotion": emo, "intensity": inten, "speaker": p[0], "positive": None}, x, sr


SKIPPED = {}


def _iter_parquet(path, audio_col, fields):
    pq = _pyarrow()
    t = pq.read_table(path)
    cols = t.column_names
    for i in range(t.num_rows):
        row = {c: t.column(c)[i].as_py() for c in cols if c != audio_col}
        a = t.column(audio_col)[i].as_py()
        data = a["bytes"] if isinstance(a, dict) else a
        try:
            x, sr = decode_audio(data)
        except ValueError:                          # MP4/WebM בודדים (אין ffmpeg) - נספרים ומדולגים
            SKIPPED[path] = SKIPPED.get(path, 0) + 1
            continue
        yield i, {k: row.get(v) for k, v in fields.items()}, row, x, sr


def iter_resd():
    for i, f, row, x, sr in _iter_parquet(os.path.join(DATA, "resd", "test.parquet"), "speech",
                                          {"emotion": "emotion", "name": "name"}):
        yield {"id": f"resd/{f['name'] or i}", "dataset": "resd", "lang": "ru", "category": str(f["emotion"]),
               "emotion": str(f["emotion"]), "positive": None}, x, sr


def iter_arabic():
    path = os.path.join(DATA, "arabic_emotional_demo", "train.parquet")
    pq = _pyarrow()
    names = pq.read_schema(path).names
    emo_col = next((c for c in ("recording_style", "emotion", "label") if c in names), None)
    for i, _f, row, x, sr in _iter_parquet(path, "audio", {}):
        emo = str(row.get(emo_col)).lower() if emo_col else "?"
        yield {"id": f"arabic_demo/{i}", "dataset": "arabic_demo", "lang": "ar", "category": emo,
               "emotion": emo, "dialect": row.get("dialect"), "speaker": row.get("speaker_id"),
               "positive": None}, x, sr


SOURCES = {**{f"fleurs_{l}": (lambda l=l: iter_fleurs(l)) for l in FLEURS_LANGS},
           **{f"fleurs_{l}_train": (lambda l=l: iter_fleurs(l, "train")) for l in FLEURS_LANGS},
           "vivae": iter_vivae, "cremad": iter_cremad, "resd": iter_resd, "arabic_demo": iter_arabic}


# ===================================================================== תכונות/ציונים
class Scorer:
    def __init__(self):
        self.cache = Y.FeatureCache(CACHE)
        self.rms = E.RmsDetector(window_seconds=0.96, hop_seconds=Y.HOP_SECONDS)
        self.lr = Y.LogisticModel.load(LR_PATH)
        d = np.load(HEAD_PATH, allow_pickle=False)
        self.head_info = json.loads(str(d["info"][0]))
        self.head = Y.MLPModel.load(HEAD_PATH) if "W1" in d.files else Y.LogisticModel.load(HEAD_PATH)

    def score(self, x, sr):
        x16 = Y.to_16k(x, sr)
        f = self.cache.get(x16, Y.TARGET_SR)
        emb = f["emb"].astype(np.float32)
        xh = Y.context_features(emb) if self.head_info.get("context") else emb
        head = Y.smooth_scores(self.head.decision_function(xh), int(self.head_info.get("smooth", 1)))
        rms = self.rms.frame_scores(x16, Y.TARGET_SR)[1]
        n = len(emb)
        rms = np.pad(rms, (0, max(0, n - len(rms))), mode="edge")[:n] if len(rms) else np.zeros(n)
        return {"rms": rms, "zs": f["zero_shot"].astype(np.float32), "lr": self.lr.predict_proba(emb),
                "head": head, "seconds": len(x16) / Y.TARGET_SR, "peak_abs": float(np.max(np.abs(x16))) if len(x16) else 0.0}


NORM_FRAME_RMS = 0.1    # ~-20 dBFS במסגרת החזקה בקטע (בקרה: האם ההבדל בין שפות הוא שפה או עוצמת הקלטה)


def level_normalize(x, sr):
    x16 = Y.to_16k(x, sr)
    n = Y.n_frames(len(x16))
    r = max(math.sqrt(float(np.mean(x16[i * Y.FRAME_HOP:i * Y.FRAME_HOP + Y.FRAME_WIN] ** 2)))
            for i in range(n)) if len(x16) else 0.0
    if r > 1e-6:
        x16 = np.clip(x16 * (NORM_FRAME_RMS / r), -1.0, 1.0).astype(np.float32)
    return x16, Y.TARGET_SR


def features(names, log=print, norm=False):
    os.makedirs(SCORES, exist_ok=True)
    sc = Scorer()
    for name in names:
        out = os.path.join(SCORES, name + ("_norm" if norm else "") + ".json")
        if os.path.exists(out):
            log(f"{name}: קיים, מדלג")
            continue
        rows, t0 = [], time.time()
        SKIPPED.clear()
        TRUNCATED.clear()
        for i, (meta, x, sr) in enumerate(SOURCES[name]()):
            if norm:
                x, sr = level_normalize(x, sr)
            s = sc.score(x, sr)
            meta.update(seconds=round(s["seconds"], 3), peak_abs=round(s["peak_abs"], 4), sr=sr,
                        scores={k: [round(float(v), 4) for v in s[k]] for k in ("rms", "zs", "lr", "head")})
            rows.append(meta)
            if (i + 1) % 250 == 0:
                log(f"  {name}: {i + 1} קטעים ({time.time() - t0:.0f}s)")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"dataset": name, "head_threshold": sc.head_info.get("threshold_logit"),
                       "skipped_non_wav": sum(SKIPPED.values()), "truncated_wav_read_partially": len(TRUNCATED), "clips": rows},
                      fh, ensure_ascii=False)
        log(f"{name}: {len(rows)} קטעים, {sum(r['seconds'] for r in rows) / 3600:.2f} שעות ({time.time() - t0:.0f}s)")


# ===================================================================== ASR / מילות מצוקה
def _asr_records(lang, prefix="asr"):
    """כל קובצי התמלול של השפה (כולל shards), בלי כפילויות."""
    import glob
    seen, out = set(), []
    for p in sorted(glob.glob(os.path.join(SCORES, f"{prefix}_fleurs_{lang}*.jsonl"))):
        with open(p, encoding="utf-8") as fh:
            for l in fh:
                if l.strip():
                    r = json.loads(l)
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        out.append(r)
    return out


AGC_TARGET_PEAK = 0.3


def agc(x16):
    """נרמול שיא פשוט (AGC): מודל ה-en מחזיר תמלול ריק על אודיו שקט (שיא < ~0.01) - נמדד ב-FLEURS en_us."""
    pk = float(np.max(np.abs(x16))) if len(x16) else 0.0
    return x16 * (AGC_TARGET_PEAK / pk) if pk > 1e-5 else x16


def asr_agc(lang, models=("en", "he"), log=print, shard=None):
    """אותו תמלול כמו asr() אבל אחרי agc() - נשמר בנפרד כ-hyp_<m>_agc (לבדיקת השפעת נרמול עוצמה)."""
    import zlib
    import speech_local as S
    sk, sn = (int(shard.split("/")[0]), int(shard.split("/")[1])) if shard else (0, 1)
    out = os.path.join(SCORES, f"agc_fleurs_{lang}" + (f".shard{sk}" if shard else "") + ".jsonl")
    done = {r["id"] for r in _asr_records(lang, prefix="agc")}
    n = 0
    with open(out, "a", encoding="utf-8") as fh:
        for meta, x, sr in iter_fleurs(lang):
            if meta["id"] in done or zlib.crc32(meta["id"].encode()) % sn != sk:
                continue
            x16 = agc(Y.to_16k(x, sr))
            rec = {"id": meta["id"]}
            for m in models:
                rec[f"hyp_{m}_agc"] = S.transcribe(x16, 16000, m)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            n += 1
    log(f"agc {lang}: +{n}")


def asr(lang, models=("he", "en", "ru"), limit=None, log=print, shard=None):
    import zlib
    import speech_local as S
    os.makedirs(SCORES, exist_ok=True)
    sk, sn = (int(shard.split("/")[0]), int(shard.split("/")[1])) if shard else (0, 1)
    out = os.path.join(SCORES, f"asr_fleurs_{lang}" + (f".shard{sk}" if shard else "") + ".jsonl")
    done = {r["id"] for r in _asr_records(lang)}
    models = [m for m in models if S.is_available(m)]
    t0, n = time.time(), 0
    with open(out, "a", encoding="utf-8") as fh:
        for meta, x, sr in iter_fleurs(lang):
            if meta["id"] in done or zlib.crc32(meta["id"].encode()) % sn != sk:
                continue
            x16 = Y.to_16k(x, sr)
            rec = {"id": meta["id"], "lang": meta["lang"], "seconds": round(len(x16) / 16000, 3), "ref": meta["ref"]}
            for m in models:
                t = time.perf_counter()
                rec["hyp_" + m] = S.transcribe(x16, 16000, m)
                rec["t_" + m] = round(time.perf_counter() - t, 3)
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            n += 1
            if n % 50 == 0:
                log(f"  asr {lang}: {n} ({time.time() - t0:.0f}s)")
            if limit and n >= limit:
                break
    log(f"asr {lang}: +{n} קטעים")


# ===================================================================== סטטיסטיקה
def poisson_ci(k, exposure, conf=0.95):
    """רווח סמך מדויק (Garwood) לקצב פואסוני: k אירועים ב-exposure שעות."""
    from scipy.stats import chi2
    a = 1 - conf
    lo = 0.0 if k == 0 else chi2.ppf(a / 2, 2 * k) / 2
    hi = chi2.ppf(1 - a / 2, 2 * k + 2) / 2
    return lo / exposure, hi / exposure


def events(scores, t):
    return E._count_events(np.asarray(scores, dtype=np.float64), t, GAP_FRAMES)


def group_stats(clips, key, t):
    hours = sum(c["seconds"] for c in clips) / 3600
    fired = sum(1 for c in clips if c["scores"][key] and max(c["scores"][key]) >= t)
    ev = sum(events(c["scores"][key], t) for c in clips)
    lo, hi = poisson_ci(ev, hours) if hours else (None, None)
    return {"clips": len(clips), "hours": round(hours, 3), "fired": fired, "fire_rate": fired / len(clips) if clips else None,
            "fire_ci": E.wilson_interval(fired, len(clips)), "events": ev,
            "per_hour": ev / hours if hours else None, "per_hour_ci": (lo, hi)}


def min_threshold_for_budget(clips, key, budget):
    """הסף הנמוך ביותר שבו התראות לשעה <= budget על הקטעים האלה (רק שליליים). None אם אין שעות."""
    hours = sum(c["seconds"] for c in clips) / 3600
    if not hours:
        return None
    peaks = sorted({max(c["scores"][key]) for c in clips if c["scores"][key]}, reverse=True)
    best = peaks[0] + 1e-6 if peaks else None
    for t in peaks:
        ev = sum(events(c["scores"][key], t) for c in clips)
        if ev / hours > budget:
            break
        best = t
    return best


def auc(pos, neg):
    """P(ציון קטע חיובי > ציון קטע שלילי) - Mann-Whitney, קשרים = חצי."""
    pos, neg = np.asarray(pos, float), np.sort(np.asarray(neg, float))
    if not len(pos) or not len(neg):
        return None
    lo = np.searchsorted(neg, pos, side="left")
    hi = np.searchsorted(neg, pos, side="right")
    return float(np.mean((lo + 0.5 * (hi - lo)) / len(neg)))


def load_scores(name):
    p = os.path.join(SCORES, name + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def operating_points(head_thr):
    return [(n, k, (head_thr if t is None else t), src) for n, k, t, src in OPERATING_POINTS]


def fmt_rate(s):
    lo, hi = s["per_hour_ci"]
    return f"{s['per_hour']:.2f} ({lo:.2f}–{hi:.2f})"


def fmt_frac(s):
    ci = s["fire_ci"]
    return f"{s['fire_rate']:.1%} ({ci[0]:.0%}–{ci[1]:.0%})" if ci else "—"


# ----------------------------------------------------------------- מילות מצוקה
EXTRA_KEYWORDS = {
    "he": ["הצילו", "תצילו אותי", "עזרו לי", "תעזבו אותי"],
    "ru": ["помогите", "помощь", "полиция", "отпусти", "не трогай"],
}


def keyword_eval(langs):
    import config
    import speech_local as S
    app_kw = list(config.DANGEROUS_KEYWORDS)
    app_models = S.languages_for_phrases(app_kw, primary=config.VOICE_TRIGGER_LANGUAGE)
    out = {"app_keywords": app_kw, "app_models": app_models, "per_lang": {}}
    for lang in langs:
        recs = _asr_records(lang)
        if not recs:
            continue
        agc_by_id = {r["id"]: r for r in _asr_records(lang, prefix="agc")}
        for r in recs:
            r.update({k: v for k, v in agc_by_id.get(r["id"], {}).items() if k != "id"})
        hours = sum(r["seconds"] for r in recs) / 3600
        res = {"clips": len(recs), "hours": round(hours, 3), "lists": {}}
        lists = {"app (he+en models, config.DANGEROUS_KEYWORDS)": (app_kw, app_models),
                 "he extra (הצילו...) on he model": (EXTRA_KEYWORDS["he"], ["he"]),
                 "ru candidates on ru model": (EXTRA_KEYWORDS["ru"], ["ru"]),
                 "app, en after AGC (he + en_agc)": (app_kw, ["he", "en_agc"])}
        for lname, (kws, models) in lists.items():
            rs = [r for r in recs if all(("hyp_" + m) in r for m in models)]
            if not rs:
                continue
            hours_l = sum(r["seconds"] for r in rs) / 3600
            hit_clips, asr_err_clips, words, err_words, examples = 0, 0, {}, {}, []
            for r in rs:
                texts = [r.get("hyp_" + m) or "" for m in models]
                got = S.find_keywords(texts, kws)
                if not got:
                    continue
                in_ref = set(S.find_keywords([r["ref"]], kws))
                hit_clips += 1
                for w in got:
                    words[w] = words.get(w, 0) + 1
                errs = [w for w in got if w not in in_ref]
                if errs:
                    asr_err_clips += 1
                    for w in errs:
                        err_words[w] = err_words.get(w, 0) + 1
                    if len(examples) < 6:
                        examples.append({"id": r["id"], "words": errs, "ref": r["ref"][:120],
                                         "hyp": {m: (r.get("hyp_" + m) or "")[:120] for m in models}})
            lo, hi = poisson_ci(hit_clips, hours_l)
            elo, ehi = poisson_ci(asr_err_clips, hours_l)
            res["lists"][lname] = {"keywords": kws, "models": models, "clips": len(rs), "hours": round(hours_l, 3),
                                   "hit_clips": hit_clips,
                                   "per_hour": hit_clips / hours_l, "per_hour_ci": (lo, hi),
                                   "asr_error_clips": asr_err_clips, "asr_error_per_hour": asr_err_clips / hours_l,
                                   "asr_error_ci": (elo, ehi), "words": words, "error_words": err_words,
                                   "examples": examples}
        # recall על מילים שבאמת נאמרו (רק כשהן בתמלול הייחוס)
        ref_pos = [r for r in recs if S.find_keywords([r["ref"]], app_kw)]
        if ref_pos:
            k = sum(1 for r in ref_pos if set(S.find_keywords([r["ref"]], app_kw)) <=
                    set(S.find_keywords([r.get("hyp_" + m) or "" for m in app_models], app_kw)))
            res["ref_keyword_clips"] = len(ref_pos)
            res["ref_keyword_detected"] = k
        # זמן עיבוד
        res["empty_transcripts"] = {m: sum(1 for r in recs if ("hyp_" + m) in r and not (r["hyp_" + m] or "").strip())
                                    for m in ("he", "en", "ru", "he_agc", "en_agc") if any(("hyp_" + m) in r for r in recs)}
        if any("hyp_en_agc" in r for r in recs) and ref_pos:
            k2 = sum(1 for r in ref_pos if "hyp_en_agc" in r and set(S.find_keywords([r["ref"]], app_kw)) <=
                     set(S.find_keywords([r.get("hyp_he") or "", r.get("hyp_en_agc") or ""], app_kw)))
            res["ref_keyword_detected_agc"] = k2
        res["rtf_under_parallel_load"] = {
            m: round(sum(r["t_" + m] for r in recs if "t_" + m in r) /
                     max(1e-9, sum(r["seconds"] for r in recs if "t_" + m in r)), 3) for m in ("he", "en", "ru")
            if any("t_" + m in r for r in recs)}
        out["per_lang"][lang] = res
    return out


# ===================================================================== דו"ח
def report(out_json, log=print):
    data = {n: load_scores(n) for n in SOURCES}
    data = {n: d for n, d in data.items() if d}
    head_thr = next(iter(data.values()))["head_threshold"]
    ops = operating_points(head_thr)
    res = {"operating_points": [{"name": n, "key": k, "threshold": t, "source": s} for n, k, t, s in ops],
           "negatives": {}, "negatives_test_only": {}, "categories": {}, "lang_thresholds": {}, "oracle": {}}
    # (א) דיבור רגיל לפי שפה
    for lang in FLEURS_LANGS:
        test = (data.get(f"fleurs_{lang}") or {"clips": []})["clips"]
        train = (data.get(f"fleurs_{lang}_train") or {"clips": []})["clips"]
        clips = test + train                        # שני הפיצולים הם דיבור רגיל = שליליים
        if not clips:
            continue
        res["negatives"][lang] = {n: group_stats(clips, k, t) for n, k, t, _ in ops}
        res["negatives_test_only"][lang] = {n: group_stats(test, k, t) for n, k, t, _ in ops} if test else None
        res["negatives"][lang]["_level"] = {"median_peak_abs": float(np.median([c["peak_abs"] for c in clips])),
                                            "median_rms_max": float(np.median([max(c["scores"]["rms"]) for c in clips]))}
        res["negatives"][lang]["_peaks"] = {k: {"q50": float(np.quantile(pk, 0.5)), "q99": float(np.quantile(pk, 0.99)),
                                                 "max": float(np.max(pk))}
                                             for k in ("zs", "lr", "head")
                                             for pk in [[max(c["scores"][k]) for c in clips]]}
        res["lang_thresholds"][lang] = {k: {str(b): min_threshold_for_budget(clips, k, b) for b in (1.0, 0.1)}
                                        for k in ("zs", "lr", "head")}
    # (ב) קטגוריות בדאטה הרגשי
    for name in ("vivae", "cremad", "resd", "arabic_demo"):
        d = data.get(name)
        if not d:
            continue
        cats = {}
        for c in d["clips"]:
            cats.setdefault(c["category"], []).append(c)
        if name in ("vivae", "cremad"):           # גם לפי רגש בלבד
            for c in d["clips"]:
                cats.setdefault(c["emotion"] + "/*", []).append(c)
        res["categories"][name] = {cat: {n: group_stats(cl, k, t) for n, k, t, _ in ops}
                                   for cat, cl in sorted(cats.items())}
    # (ג) "אורקל": סף שנבחר כך שיעמוד בתקציב על *דיבור רגיל בלבד* (כל FLEURS) - מה היה ה-recall אילו דיבור
    #     היה השלילי היחיד. חסם עליון אופטימי (מתעלם ממוזיקה/עידוד וכו' מ-FSD50K), לא מספר לדיווח.
    if "vivae" in data:
        fl = [c for l in FLEURS_LANGS for sfx in ("", "_train")
              for c in (data.get(f"fleurs_{l}{sfx}") or {"clips": []})["clips"]]
        pos = [c for c in data["vivae"]["clips"] if c["positive"] and c["intensity"] in ("strong", "peak")]
        happy = [c for c in data["vivae"]["clips"] if not c["positive"] and c["intensity"] in ("strong", "peak")]
        extra = {"cremad anger/high": [c for c in (data.get("cremad") or {"clips": []})["clips"]
                                       if c["category"] == "anger/high"],
                 "cremad fear/high": [c for c in (data.get("cremad") or {"clips": []})["clips"]
                                      if c["category"] == "fear/high"],
                 "arabic_demo angry": [c for c in (data.get("arabic_demo") or {"clips": []})["clips"]
                                       if c["category"] == "angry"],
                 "resd anger+fear": [c for c in (data.get("resd") or {"clips": []})["clips"]
                                     if c["category"] in ("anger", "fear")]}
        hours = sum(c["seconds"] for c in fl) / 3600
        for k in ("zs", "lr", "head"):
            row = {"fleurs_hours": round(hours, 2)}
            for b in (1.0, 0.1):
                t = min_threshold_for_budget(fl, k, b)
                row[str(b)] = {"threshold": t,
                               "vivae_distress_strong_peak": group_stats(pos, k, t)["fire_rate"],
                               "vivae_happy_strong_peak": group_stats(happy, k, t)["fire_rate"],
                               **{n: (group_stats(cl, k, t)["fire_rate"] if cl else None) for n, cl in extra.items()}}
            res["oracle"][k] = row
    # (ד) הפרדה (AUC לפי שיא הקטע): מצוקה מבוימת מול דיבור רגיל לפי שפה ומול "צעקות שמחה"
    if "vivae" in data:
        pos = [c for c in data["vivae"]["clips"] if c["positive"] and c["intensity"] in ("strong", "peak")]
        hard = [c for c in data["vivae"]["clips"] if not c["positive"] and c["intensity"] in ("strong", "peak")]
        res["auc"] = {}
        for k in ("rms", "zs", "lr", "head"):
            pp = [max(c["scores"][k]) for c in pos]
            row = {"vivae_positive_emotions": auc(pp, [max(c["scores"][k]) for c in hard])}
            for lang in FLEURS_LANGS:
                neg = [c for sfx in ("", "_train") for c in (data.get(f"fleurs_{lang}{sfx}") or {"clips": []})["clips"]]
                if neg:
                    row["fleurs_" + lang] = auc(pp, [max(c["scores"][k]) for c in neg])
            for name in ("cremad", "resd", "arabic_demo"):
                if name in data:
                    row[name] = auc(pp, [max(c["scores"][k]) for c in data[name]["clips"]])
            res["auc"][k] = row
    # (ה) בקרת עוצמה: אותם קטעים אחרי נרמול עוצמה (NORM_FRAME_RMS) - האם ההבדל בין שפות נעלם?
    nv = load_scores("vivae_norm")
    if nv:
        npos = [c for c in nv["clips"] if c["positive"] and c["intensity"] in ("strong", "peak")]
        res["norm_control"] = {}
        for lang in FLEURS_LANGS:
            d = load_scores(f"fleurs_{lang}_norm")
            if not d:
                continue
            cl = d["clips"]
            res["norm_control"][lang] = {
                "hours": round(sum(c["seconds"] for c in cl) / 3600, 3),
                "median_head_peak": float(np.median([max(c["scores"]["head"]) for c in cl])),
                "max_head_peak": float(np.max([max(c["scores"]["head"]) for c in cl])),
                "auc": {k: auc([max(c["scores"][k]) for c in npos], [max(c["scores"][k]) for c in cl])
                        for k in ("zs", "lr", "head")},
                "events": {n: group_stats(cl, k, t)["events"] for n, k, t, _ in ops if k != "rms"}}
        res["norm_control"]["vivae_recall"] = {n: group_stats(npos, k, t)["fire_rate"] for n, k, t, _ in ops if k != "rms"}
    res["keywords"] = keyword_eval(FLEURS_LANGS)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)
    log(f"נכתב {out_json}")
    return res


def tables_markdown(res):
    """טבלאות Markdown (לשילוב בדו"ח research/multilingual_audio_eval.md)."""
    L = []
    ops = [o["name"] for o in res["operating_points"]]
    L.append("### התראות שווא לשעה בדיבור רגיל (FLEURS test+train), לפי שפה - ספים קבועים מ-FSD50K")
    L.append("")
    L.append("| גלאי / נקודת עבודה | " + " | ".join(res["negatives"]) + " |")
    L.append("|---|" + "---|" * len(res["negatives"]))
    L.append("| שעות (קטעים) | " + " | ".join(f"{v[ops[0]]['hours']:.2f} ({v[ops[0]]['clips']})"
                                           for v in res["negatives"].values()) + " |")
    for n in ops:
        L.append(f"| {n} | " + " | ".join(fmt_rate(v[n]) for v in res["negatives"].values()) + " |")
    L.append("")
    L.append("ערך = התראות לשעה (רווח סמך פואסוני 95%).")
    L.append("")
    for name, cats in res["categories"].items():
        L.append(f"### {name}: שיעור קטעים שבהם הגלאי התריע (רווח Wilson 95%)")
        L.append("")
        sel = [n for n in ops if not n.startswith("rms @")]
        L.append("| קטגוריה | קטעים | שעות | " + " | ".join(sel) + " |")
        L.append("|---|---|---|" + "---|" * len(sel))
        for cat, st in cats.items():
            a = st[sel[0]]
            L.append(f"| {cat} | {a['clips']} | {a['hours']:.2f} | " + " | ".join(fmt_frac(st[n]) for n in sel) + " |")
        L.append("")
    names = {"zs": "yamnet_zero_shot", "lr": "distress_lr_fsd50k", "head": "distress_head"}
    if res.get("negatives"):
        L.append("### שיא ציון בדיבור רגיל לפי שפה (מרווח מהסף) - חציון / אחוזון 99 / מקסימום של שיא-קטע")
        L.append("")
        L.append("| גלאי (סף באפליקציה/FSD50K@1/h) | " + " | ".join(res["negatives"]) + " |")
        L.append("|---|" + "---|" * len(res["negatives"]))
        thr = {o["key"]: o["threshold"] for o in res["operating_points"] if "@0.1" not in o["name"]}
        for k in ("zs", "lr", "head"):
            L.append(f"| {names[k]} ({thr[k]:.4g}) | " + " | ".join(
                f"{v['_peaks'][k]['q50']:.3g} / {v['_peaks'][k]['q99']:.3g} / {v['_peaks'][k]['max']:.3g}"
                for v in res["negatives"].values()) + " |")
        L.append("")
    if res.get("auc"):
        L.append("### הפרדה (AUC לפי שיא-קטע): VIVAE fear/pain/anger (strong+peak) מול ...")
        L.append("")
        cols = list(next(iter(res["auc"].values())))
        L.append("| גלאי | " + " | ".join(cols) + " |")
        L.append("|---|" + "---|" * len(cols))
        for k, row in res["auc"].items():
            L.append(f"| {names.get(k, k)} | " + " | ".join("—" if row[c] is None else f"{row[c]:.2f}" for c in cols) + " |")
        L.append("")
    if res.get("oracle"):
        L.append("### אורקל: סף שנבחר על דיבור רגיל בלבד (כל FLEURS) - שיעור התרעה על קבוצות אחרות")
        L.append("")
        first = next(iter(res["oracle"].values()))
        cols = [c for c in first["1.0"] if c != "threshold"]
        L.append("| גלאי | תקציב/שעה | סף | " + " | ".join(cols) + " |")
        L.append("|---|---|---|" + "---|" * len(cols))
        for k, row in res["oracle"].items():
            for b in ("1.0", "0.1"):
                r = row[b]
                L.append(f"| {names[k]} | {b} | {r['threshold']:.4g} | " +
                         " | ".join("—" if r[c] is None else f"{r[c]:.1%}" for c in cols) + " |")
        L.append(f"\nשעות דיבור לכיול: {first['fleurs_hours']}")
        L.append("")
    nc = res.get("norm_control")
    if nc:
        L.append("### בקרת עוצמה: אחרי נרמול כל קטע לאותה עוצמה")
        L.append("")
        langs = [l for l in nc if l != "vivae_recall"]
        L.append("| שפה | שעות | חציון שיא distress_head | מקס' | AUC zs / lr / head | התראות (head באפליקציה) |")
        L.append("|---|---|---|---|---|---|")
        for l in langs:
            v = nc[l]
            hd = next(n for n in v["events"] if n.startswith("distress_head"))
            L.append(f"| {l} | {v['hours']:.2f} | {v['median_head_peak']:.2f} | {v['max_head_peak']:.2f} | "
                     f"{v['auc']['zs']:.2f} / {v['auc']['lr']:.2f} / {v['auc']['head']:.2f} | {v['events'][hd]} |")
        if "vivae_recall" in nc:
            L.append("\nrecall על VIVAE מנורמל (strong+peak): " +
                     ", ".join(f"{n} {r:.1%}" for n, r in nc["vivae_recall"].items()))
        L.append("")
    kw = res.get("keywords", {}).get("per_lang")
    if kw:
        L.append("### מילות מצוקה (ASR מקומי) על דיבור רגיל - קטעים עם התראה לשעה (רווח פואסוני 95%)")
        L.append("")
        L.append("| שפה | רשימה | שעות | קטעים עם מילה | לשעה | מתוכם טעות ASR | טעות ASR לשעה | מילים |")
        L.append("|---|---|---|---|---|---|---|---|")
        for lang, v in kw.items():
            for lname, s in v["lists"].items():
                lo, hi = s["per_hour_ci"]
                elo, ehi = s["asr_error_ci"]
                words = ", ".join(f"{w}×{n}" for w, n in sorted(s["words"].items(), key=lambda kv: -kv[1])) or "—"
                L.append(f"| {lang} | {lname} | {s['hours']:.2f} | {s['hit_clips']} | {s['per_hour']:.2f} ({lo:.2f}–{hi:.2f}) | "
                         f"{s['asr_error_clips']} | {s['asr_error_per_hour']:.2f} ({elo:.2f}–{ehi:.2f}) | {words} |")
        L.append("")
    return "\n".join(L)


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="LifeGuard multilingual distress-detector evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("features")
    f.add_argument("--only", nargs="*", default=None)
    f.add_argument("--norm", action="store_true", help="נרמול עוצמה לכל קטע (בקרה)")
    a = sub.add_parser("asr")
    a.add_argument("--lang", required=True, choices=FLEURS_LANGS)
    a.add_argument("--models", default="he,en,ru")
    a.add_argument("--limit", type=int, default=None)
    a.add_argument("--shard", default=None, help="k/n - רק קטעים ש-crc32(id)%%n==k")
    a.add_argument("--agc", action="store_true", help="תמלול מחדש אחרי נרמול שיא (hyp_<m>_agc)")
    r = sub.add_parser("report")
    r.add_argument("--out", default=os.path.join(SCORES, "multilingual_eval_results.json"))
    args = ap.parse_args(argv)
    if args.cmd == "features":
        names = args.only or [n for n in SOURCES]
        features([n for n in names if n in SOURCES], norm=args.norm)
    elif args.cmd == "asr":
        if args.agc:
            asr_agc(args.lang, tuple(args.models.split(",")), shard=args.shard)
        else:
            asr(args.lang, tuple(args.models.split(",")), args.limit, shard=args.shard)
    else:
        res = report(args.out)
        md = tables_markdown(res)
        with open(os.path.join(SCORES, "tables.md"), "w", encoding="utf-8") as fh:
            fh.write(md)
        print(md)


if __name__ == "__main__":
    main()

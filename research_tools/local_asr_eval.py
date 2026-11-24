# -*- coding: utf-8 -*-
"""
local_asr_eval.py - מדידה של זיהוי הדיבור המקומי (speech_local) על הקלטות עבריות אמיתיות.

נתונים: Google FLEURS he_il (CC-BY-4.0), פיצול dev, שהורד ל-research/samples/fleurs_he/
(dev.tsv + dev/*.wav; לא במאגר הקוד). מודד:
  - זמן טעינת מודל, latency לכל קליפ ו-RTF (שניות עיבוד לשנייה אודיו)
  - WER/CER אחרי נרמול speech_local.normalize_tokens
  - איתור מילות מצוקה (config.DANGEROUS_KEYWORDS): בקליפים שהתמלול-ייחוס שלהם מכיל מילה - האם זוהתה;
    בשאר - האם הופיעה התראת שווא (גם מהמודל האנגלי שרץ על אותו אודיו עברי)

שימוש:  .venv\\Scripts\\python -m research_tools.local_asr_eval [--n 40] [--export-test-clip]
"""

import argparse
import csv
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
import speech_local as S  # noqa: E402

FLEURS = os.path.join(ROOT, "research", "samples", "fleurs_he")
TEST_CLIP_DIR = os.path.join(ROOT, "research", "samples", "asr_he")
# קליפ dev שהתמלול שלו "לפי המשטרה, כנראה לא יוגש כתב אישום..." - משמש לבדיקת היחידה עם מודל אמיתי
TEST_CLIP_ID = "10835201480770434895.wav"


def load_wav(path):
    from scipy.io import wavfile
    rate, data = wavfile.read(path)
    data = data.astype(np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if np.abs(data).max(initial=0) > 1.5:          # PCM16
        data /= 32768.0
    if rate != S.SAMPLE_RATE:
        from scipy.signal import resample_poly
        data = resample_poly(data, S.SAMPLE_RATE, rate).astype(np.float32)
        rate = S.SAMPLE_RATE
    return data, rate


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def export_test_clip():
    import wave
    data, rate = load_wav(os.path.join(FLEURS, "dev", TEST_CLIP_ID))
    os.makedirs(TEST_CLIP_DIR, exist_ok=True)
    out = os.path.join(TEST_CLIP_DIR, "fleurs_he_dev_police.wav")
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    with open(os.path.join(TEST_CLIP_DIR, "SOURCE.txt"), "w", encoding="utf-8") as fh:
        fh.write("fleurs_he_dev_police.wav = google/fleurs he_il dev/" + TEST_CLIP_ID +
                 " (CC-BY-4.0, https://huggingface.co/datasets/google/fleurs), converted to PCM16 mono 16 kHz\n"
                 "reference: לפי המשטרה, כנראה לא יוגש כתב אישום נגד נהג הרכב שפגע בצלם.\n")
    print("exported", out)


def tts_keyword_check():
    """
    משפטי מצוקה מסונתזים אופליין (Windows OneCore TTS: he-IL Asaf, en-US) ב-research/samples/asr_tts/
    (list.tsv: id<TAB>lang<TAB>text). דיבור סינתטי ונקי - בדיקה אופטימית, לא תחליף להקלטות אמיתיות.
    מריץ את אותו צינור כמו האפליקציה: recognize_speech על he+en ו-find_keywords.
    """
    base = os.path.join(ROOT, "research", "samples", "asr_tts")
    langs = S.languages_for_phrases(config.DANGEROUS_KEYWORDS, primary=config.VOICE_TRIGGER_LANGUAGE)
    hits = pos = false_alarms = neg = 0
    for clip_id, _lang, text in csv.reader(open(os.path.join(base, "list.tsv"), encoding="utf-8"), delimiter="\t"):
        audio, rate = load_wav(os.path.join(base, clip_id + ".wav"))
        texts = S.recognize_speech(audio, langs)
        expected = set(S.find_keywords([text], config.DANGEROUS_KEYWORDS))
        got = set(S.find_keywords(texts, config.DANGEROUS_KEYWORDS))
        if expected:
            pos += 1
            hits += bool(expected & got)
        else:
            neg += 1
            false_alarms += bool(got)
        print(clip_id, "OK " if (expected & got) or (not expected and not got) else "MISS",
              sorted(expected), "->", sorted(got), "|", " || ".join(texts))
    print(f"TTS: keyword sentences detected {hits}/{pos}; false alarms {false_alarms}/{neg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40, help="number of clips (keyword clips always included)")
    ap.add_argument("--export-test-clip", action="store_true")
    ap.add_argument("--tts", action="store_true", help="keyword check on synthesized sentences")
    args = ap.parse_args()
    if args.export_test_clip:
        export_test_clip()
        return
    if args.tts:
        tts_keyword_check()
        return

    rows = list(csv.reader(open(os.path.join(FLEURS, "dev.tsv"), encoding="utf-8"), delimiter="\t"))
    rows = [r for r in rows if os.path.exists(os.path.join(FLEURS, "dev", r[1]))]
    kw_rows = [r for r in rows if S.find_keywords([r[2]], config.DANGEROUS_KEYWORDS)]
    others = [r for r in rows if r not in kw_rows]
    chosen = kw_rows + others[:max(0, args.n - len(kw_rows))]

    load = {}
    for lang in ("he", "en"):
        t = time.perf_counter()
        ok = S._load(lang) is not None
        load[lang] = round(time.perf_counter() - t, 2) if ok else None

    per_clip, tot_audio, tot_proc = [], 0.0, {"he": 0.0, "en": 0.0}
    w_err = w_tot = c_err = c_tot = 0
    for r in chosen:
        audio, rate = load_wav(os.path.join(FLEURS, "dev", r[1]))
        dur = len(audio) / rate
        tot_audio += dur
        hyps = {}
        for lang in ("he", "en"):
            if load[lang] is None:
                continue
            t = time.perf_counter()
            hyps[lang] = S.transcribe(audio, rate, lang)
            tot_proc[lang] += time.perf_counter() - t
        ref_tok, hyp_tok = S.normalize_tokens(r[2]), S.normalize_tokens(hyps.get("he", ""))
        w_err += edit_distance(ref_tok, hyp_tok)
        w_tot += len(ref_tok)
        c_err += edit_distance("".join(ref_tok), "".join(hyp_tok))
        c_tot += len("".join(ref_tok))
        per_clip.append({
            "file": r[1], "seconds": round(dur, 2), "ref": r[2], "hyp_he": hyps.get("he"),
            "hyp_en": hyps.get("en"),
            "kw_ref": S.find_keywords([r[2]], config.DANGEROUS_KEYWORDS),
            "kw_he": S.find_keywords([hyps.get("he", "")], config.DANGEROUS_KEYWORDS),
            "kw_he_en": S.find_keywords([h for h in hyps.values() if h], config.DANGEROUS_KEYWORDS),
        })

    pos = [c for c in per_clip if c["kw_ref"]]
    neg = [c for c in per_clip if not c["kw_ref"]]
    summary = {
        "clips": len(per_clip), "audio_seconds": round(tot_audio, 1),
        "load_seconds": load, "num_threads": getattr(config, "LOCAL_SPEECH_NUM_THREADS", 2),
        "rtf": {k: round(v / tot_audio, 3) for k, v in tot_proc.items() if load.get(k) is not None},
        "wer_he": round(w_err / max(w_tot, 1), 3), "cer_he": round(c_err / max(c_tot, 1), 3),
        "keyword_clips": len(pos),
        "keyword_hits_he": sum(set(c["kw_ref"]) <= set(c["kw_he"]) for c in pos),
        "false_alarm_clips_he": sum(bool(c["kw_he"]) for c in neg),
        "false_alarm_clips_he_plus_en": sum(bool(c["kw_he_en"]) for c in neg),
        "false_alarm_words_he_plus_en": sorted({k for c in neg for k in c["kw_he_en"]}),
    }
    out = os.path.join(FLEURS, "asr_eval_results.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "clips": per_clip}, fh, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for c in pos:
        print("KW", c["file"], c["kw_ref"], "->", c["kw_he"], "|", c["hyp_he"])
    print("written", out)


if __name__ == "__main__":
    main()

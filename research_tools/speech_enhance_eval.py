# -*- coding: utf-8 -*-
"""
speech_enhance_eval.py - האם הפחתת רעשים (speech_enhance.py) משפרת תמלול מקומי ברעש רחוב אמיתי?

- דיבור: FLEURS test (CC BY 4.0) בעברית ובאנגלית; הפניות לתמלול מהמאגר.
- רעש: הקלטות TAU אמיתיות מרחוב / אוטובוס / מטרו (NC - הערכה בלבד).
- תנאים: נקי, SNR 10 / 5 / 0 dB. לכל תנאי: WER בלי ועם הפחתת רעשים, באותו מודל ASR מקומי (speech_local).
- **אין כאן recall של "הצילו"**: אין הקלטות אמיתיות של קריאות מצוקה, ולא מחליפים אותן בהקלטות מלאכותיות.
  WER הוא מדד עקיף לכמה המילים מובנות למודל.
- כלל החלטה (נקבע מראש): מפעילים באפליקציה רק אם בשתי השפות WER משתפר ב-3 נק' לפחות בממוצע של SNR 0 ו-5,
  ולא מחמיר ביותר מנקודה אחת בתנאי נקי ו-SNR 10.

שימוש: python -I research_tools/speech_enhance_eval.py [N]
"""
import json
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT_MD = os.path.join(ROOT, "research", "speech_enhance_eval.md")
OUT_JSON = os.path.join(ROOT, "research", "speech_enhance_eval.json")
LANGS = {"he_il": "he", "en_us": "en"}
SNRS = (None, 10, 5, 0)
SEED = 3


def _norm(t):
    t = re.sub(r"[֑-ׇ]", "", t.lower())               # ניקוד
    t = re.sub(r"[^\w\s]", " ", t)
    return t.split()


def wer(ref, hyp):
    r, h = _norm(ref), _norm(hyp or "")
    if not r:
        return None
    d = np.arange(len(h) + 1)
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r), len(r)


def noises(rng, n=60):
    from research_tools import hv2_data as D
    from research_tools import danger_audio_external as E
    from research_tools import yamnet_detector as Y
    rows = [r for r in D.load_manifest() if r["source"] == "tau2020_mobile"
            and r.get("scene") in ("street_traffic", "street_pedestrian", "bus", "metro")]
    rng.shuffle(rows)
    out = []
    for r in rows[:n]:
        x, sr = E.read_wav(r["ref"]["path"])
        out.append(Y.to_16k(x, sr))
    return out


def run(n=100):
    import speech_enhance as SE
    import speech_local as SL
    from research_tools import augment as A
    from research_tools import multilingual_eval as M
    from research_tools import yamnet_detector as Y
    import config
    rng = np.random.default_rng(SEED)
    bank = noises(rng)
    res = {}
    t0 = time.time()
    for lang, code in LANGS.items():
        if not SL.is_available(code):
            res[lang] = {"error": f"אין מודל ASR מקומי ל-{code}"}
            continue
        acc = {str(s): {"noisy": [0, 0], "enh": [0, 0], "kw_noisy": 0, "kw_enh": 0} for s in SNRS}
        k = 0
        for meta, x, sr in M.iter_fleurs(lang):
            if k >= n:
                break
            if not meta["ref"]:
                continue
            k += 1
            x16 = Y.to_16k(x, sr)
            for s in SNRS:
                xn = x16 if s is None else A.mix_snr(x16, bank[int(rng.integers(len(bank)))], s, rng)
                for tag, sig in (("noisy", xn), ("enh", SE.enhance(xn))):
                    hyp = SL.transcribe(sig, 16000, code) or ""
                    w = wer(meta["ref"], hyp)
                    if w:
                        acc[str(s)][tag][0] += w[0] * w[1]
                        acc[str(s)][tag][1] += w[1]
                    acc[str(s)]["kw_" + tag] += len(SL.find_keywords([hyp], config.DANGEROUS_KEYWORDS))
            if k % 20 == 0:
                print(f"{lang}: {k} ({time.time() - t0:.0f}s)", flush=True)
        res[lang] = {"clips": k, "conditions": {s: {"wer_noisy": a["noisy"][0] / max(1, a["noisy"][1]),
                                                    "wer_enh": a["enh"][0] / max(1, a["enh"][1]),
                                                    "kw_false_noisy": a["kw_noisy"], "kw_false_enh": a["kw_enh"]}
                                                for s, a in acc.items()}}
    ok = True
    for lang, r in res.items():
        if "conditions" not in r:
            ok = False
            continue
        c = r["conditions"]
        gain_low = np.mean([c[s]["wer_noisy"] - c[s]["wer_enh"] for s in ("5", "0")])
        hurt_hi = max(c[s]["wer_enh"] - c[s]["wer_noisy"] for s in ("None", "10"))
        r["gain_low_snr"], r["hurt_high_snr"] = float(gain_low), float(hurt_hi)
        ok = ok and gain_low >= 0.03 and hurt_hi <= 0.01
    out = {"created": time.strftime("%Y-%m-%d"), "per_lang": res, "enable": bool(ok)}
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report(out)
    return out


def report(o):
    p = lambda x: f"{100 * x:.1f}%"  # noqa: E731
    L = ["# הפחתת רעשים לזרם הדיבור - האם משפרת תמלול ברעש רחוב אמיתי?", "",
         f"נוצר ע\"י `research_tools/speech_enhance_eval.py` ({o['created']}). דיבור: FLEURS test; רעש: TAU (רחוב, "
         "אוטובוס, מטרו). אותו מודל ASR מקומי עם ובלי הפחתת רעשים.", "",
         "| שפה | תנאי | WER בלי | WER עם | זיהוי מילת מצוקה בטעות (בלי / עם) |", "|---|---|---|---|---|"]
    for lang, r in o["per_lang"].items():
        if "conditions" not in r:
            L.append(f"| {lang} | — | {r.get('error')} | | |")
            continue
        for s, c in r["conditions"].items():
            L.append(f"| {lang} (n={r['clips']}) | {'נקי' if s == 'None' else f'SNR {s} dB'} | {p(c['wer_noisy'])} | "
                     f"{p(c['wer_enh'])} | {c['kw_false_noisy']} / {c['kw_false_enh']} |")
    L += ["", "**כלל החלטה (נקבע מראש):** מפעילים רק אם בשתי השפות WER משתפר ב-3 נק' לפחות בממוצע SNR 0/5 ולא מחמיר "
              "ביותר מנקודה בנקי / SNR 10.", f"**החלטה:** {'מופעל' if o['enable'] else 'לא מופעל'} (config.SPEECH_ENHANCE).",
          "", "**לא נמדד:** זיהוי \"הצילו\" אמיתי - אין הקלטות כאלה; WER הוא מדד עקיף בלבד."]
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 100)

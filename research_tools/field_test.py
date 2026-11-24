# -*- coding: utf-8 -*-
"""
field_test.py - ניסוי שטח: כמה התראות שווא בפועל ביום רגיל, בתנאים אמיתיים (טלפון/מחשב, רחוב, בית קפה).

למה לא פשוט להקליט יום שלם: הקלטה של שיחות של אחרים עלולה להיחשב האזנת סתר, והיא גם פגיעה בפרטיות.
לכן במצב חי (live) **שום אודיו לא נשמר**: המודל רץ על חלון של כמה שניות בזיכרון, ונרשמים רק
ציון לכל חלון ושעה. אחרי הניסוי המשתתף/ת מסמנ/ת לכל התראה "אמיתית/שווא" לפי השעה (מה קרה אז).

מצבים:
  live    : מיקרופון -> מודל -> יומן ציונים (JSONL). עוצר ב-Ctrl+C או אחרי --hours.
  offline : קבצי WAV ארוכים *של המשתתף/ת עצמו/ה* (בהסכמה) -> אותו יומן.
  report  : יומן(ים) + תיוגים -> התראות שווא לשעה/ליום, זיהוי אירועים מבוימים, השוואה לעוצמת רעש.

שימוש:
  python -m research_tools.field_test live --out research_data/field/p01_day1.jsonl --hours 8
  python -m research_tools.field_test report research_data/field/*.jsonl --labels labels.csv
"""

import argparse
import csv
import glob
import json
import os
import time

import numpy as np

WINDOW_S = 4.0            # חלון ניתוח (כמו בהערכת הסיכון באפליקציה + שוליים)
MERGE_GAP_S = 10.0        # התראות במרחק קטן מזה = אירוע אחד


def _detector():
    import acoustic_ai_module as A
    if not A.load():
        raise SystemExit(f"מודל הזיהוי לא זמין: {A.status().get('reason')}")
    return A


def score_window(A, audio, sr):
    r = A.analyze(audio, sr)
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    return {"score": r["score"] if r else None, "detected": bool(r and r["detected"]), "rms": round(rms, 5)}


def live(out, hours, device=None, log=print):
    import sounddevice as sd
    A = _detector()
    sr = 16000
    end = time.time() + hours * 3600
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    n = alarms = 0
    with open(out, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"mode": "live", "started": time.time(), "window_s": WINDOW_S,
                                       "model": A.status(), "audio_stored": False}}, ensure_ascii=False) + "\n")
        try:
            while time.time() < end:
                rec = sd.rec(int(WINDOW_S * sr), samplerate=sr, channels=1, dtype="float32", device=device)
                sd.wait()
                row = {"t": round(time.time(), 1), **score_window(A, rec.reshape(-1), sr)}
                del rec                                         # האודיו לא נשמר בשום מקום
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                n += 1
                alarms += row["detected"]
                if n % 150 == 0:
                    log(f"{n * WINDOW_S / 3600:.1f} שעות, {alarms} חלונות מעל הסף")
        except KeyboardInterrupt:
            pass
    return out


def offline(paths, out, start_epoch=None):
    from research_tools.acoustic_eval import _read_wav_bytes
    A = _detector()
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": {"mode": "offline", "files": [os.path.basename(p) for p in paths],
                                       "window_s": WINDOW_S, "model": A.status()}}, ensure_ascii=False) + "\n")
        t0 = start_epoch or 0.0
        for p in paths:
            with open(p, "rb") as wf:
                x, sr = _read_wav_bytes(wf.read(), resample_to=16000)
            w = int(WINDOW_S * sr)
            for i in range(0, max(1, len(x) - w + 1), w):
                fh.write(json.dumps({"t": round(t0 + i / sr, 1), "file": os.path.basename(p),
                                     **score_window(A, x[i:i + w], sr)}) + "\n")
            t0 += len(x) / sr
    return out


def load_log(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if "meta" not in d:
                rows.append(d)
    return rows


def alarm_events(rows, key="detected", merge_gap=MERGE_GAP_S):
    """חלונות רצופים מעל הסף -> אירועי התראה [(התחלה, סוף)]."""
    ev = []
    for r in rows:
        if r.get(key):
            if ev and r["t"] - ev[-1][1] <= merge_gap:
                ev[-1][1] = r["t"]
            else:
                ev.append([r["t"], r["t"]])
    return [tuple(e) for e in ev]


def load_labels(path):
    """CSV: start,end,label  (epoch או שניות מתחילת הקובץ; label: distress = אירוע מבוים אמיתי)."""
    out = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append((float(r["start"]), float(r["end"]), r["label"].strip()))
    return out


def report(log_paths, labels=None, rms_threshold=0.15):
    rows = [r for p in log_paths for r in load_log(p)]
    rows.sort(key=lambda r: r["t"])
    hours = len(rows) * WINDOW_S / 3600.0
    for r in rows:
        r["rms_alarm"] = r["rms"] >= rms_threshold
    out = {"hours": round(hours, 3), "windows": len(rows)}
    distress = [(a, b) for a, b, l in (labels or []) if l == "distress"]

    def summarize(key):
        ev = alarm_events(rows, key)
        hit = [d for d in distress if any(e0 <= d[1] + WINDOW_S and e1 >= d[0] - WINDOW_S for e0, e1 in ev)]
        false = [e for e in ev if not any(e[0] <= d[1] + WINDOW_S and e[1] >= d[0] - WINDOW_S for d in distress)]
        return {"alarms": len(ev), "false_alarms": len(false),
                "false_per_hour": round(len(false) / hours, 3) if hours else None,
                "false_per_16h_day": round(16 * len(false) / hours, 2) if hours else None,
                "staged_events": len(distress), "staged_detected": len(hit),
                "false_alarm_times": [time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e[0])) for e in false][:50]}
    out["model"] = summarize("detected")
    out["rms_baseline"] = summarize("rms_alarm")
    if hours < 8:
        out["warning"] = f"רק {hours:.1f} שעות - מעט מדי כדי להעריך התראות שווא ליום"
    return out


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="LifeGuard field test (no audio stored)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("live"); a.add_argument("--out", required=True); a.add_argument("--hours", type=float, default=8)
    a.add_argument("--device", default=None)
    b = sub.add_parser("offline"); b.add_argument("wavs", nargs="+"); b.add_argument("--out", required=True)
    c = sub.add_parser("report"); c.add_argument("logs", nargs="+"); c.add_argument("--labels")
    args = ap.parse_args(argv)
    if args.cmd == "live":
        live(args.out, args.hours, args.device)
    elif args.cmd == "offline":
        offline([p for w in args.wavs for p in glob.glob(w)], args.out)
    else:
        logs = [p for w in args.logs for p in glob.glob(w)]
        print(json.dumps(report(logs, load_labels(args.labels) if args.labels else None), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

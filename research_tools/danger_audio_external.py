# -*- coding: utf-8 -*-
"""
danger_audio_external.py - מבחן חיצוני למנוע האודיו (אבן דרך 4): הקלטות אמיתיות שלא מ-FSD50K.

השאלה: המספרים של אבן דרך 1 נמדדו על FSD50K (Freesound). מה קורה על:
- ירי אמיתי במטווח, מוקלט במכשירי קצה כולל טלפונים (gunshot_fit) - לא Freesound;
- רחוב אמיתי מחיישני NYC: צעקות, סירנות, אזעקות רכב ורקע (sonyc_ust) - לא Freesound;
- טלפונים אמיתיים ברחוב / מטרו / אוטובוס / פארק (tau2020_mobile, מכשירים a/b/c בלבד) - לא Freesound;
- צרחות ובכי מול צחוק / שיעול (nonspeech7k);
- שבירת זכוכית, סירנה, אזעקת שעון, בכי תינוק (esc50) - Freesound, ולכן כל קטע שמזהה ה-Freesound שלו
  מופיע ב-FSD50K **מוצא** (לא נמדד על מה שהמודל ראה).

המדידה משתמשת **בקוד שרץ באפליקציה** (audio_intel.analyze: YAMNet -> 8 גלאים + ראש המצוקה):
- הקלטה עם אירוע: כל הקטע נכנס למנוע; האם האירוע זוהה.
- רקע: חלונות של 1.5 ש' (בדיוק אורך ההקלטה אחרי טריגר); איזה חלק מהחלונות מקבל זיהוי שגוי.
אין תמלול דיבור (ברחוב יש עוברים ושבים). אין שמירה של אודיו מעבר לקבצי המאגר.

כלל החלטה שנקבע מראש: קטגוריה שמוסיפה נקודות ושיעור החלונות השגויים שלה ברקע אמיתי (טלפון/רחוב) > 1%
-> המלצה להוריד אותה ל"מידע בלבד". שינוי בפועל - רק אחרי החלטה.

שימוש: python -I research_tools/danger_audio_external.py run [שם ...]
        python -I research_tools/danger_audio_external.py report
"""
import csv
import io
import json
import math
import os
import sys
import tarfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

BASE = os.path.join(ROOT, "research_data", "audio")
CACHE = os.path.join(BASE, "cache", "danger_external")
FSD = os.path.join(ROOT, "research", "samples", "fsd50k")
OUT_REPORT = os.path.join(ROOT, "research", "danger_audio_external_v1.md")
OUT_JSON = os.path.join(ROOT, "research", "danger_audio_external_v1.json")
WINDOW_S = 1.5                     # config.RISK_ASSESS_NOISE_SECONDS
MAX_BACKGROUND_WINDOWS_PER_CLIP = 6
DEMOTE_WINDOW_RATE = 0.01          # כלל ההחלטה (ראו למעלה)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REAL_DEVICES = ("a", "b", "c")     # TAU: a = מיקרופון בינאורלי, b = Samsung Galaxy S7, c = iPhone SE; s1-s6 מדומים


def log(m):
    print(m, flush=True)


def read_wav(data_or_path):
    from scipy.io import wavfile
    sr, x = wavfile.read(io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else data_or_path)
    if x.dtype == np.int16:
        x = x.astype(np.float32) / 32768.0
    elif x.dtype == np.int32:                     # כולל 24 ביט (scipy מרחיב ל-int32)
        x = x.astype(np.float32) / 2147483648.0
    elif x.dtype == np.uint8:
        x = (x.astype(np.float32) - 128) / 128.0
    else:
        x = x.astype(np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, int(sr)


# ================================================================= מאגרים -> פריטים
def _fsd_ids():
    ids = set()
    for split in ("dev", "eval"):
        p = os.path.join(FSD, "meta", "FSD50K.ground_truth", f"{split}.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                ids |= {r["fname"] for r in csv.DictReader(fh)}
    return ids


def _walk(d, ext=".wav"):
    for root, _, files in os.walk(d):
        for f in sorted(files):
            if f.lower().endswith(ext):
                yield os.path.join(root, f)


def items_gunshot_fit():
    d = os.path.join(BASE, "gunshot_fit", "extracted")
    meta = {}
    for p in _walk(d, ".csv"):
        if "all-metadata" in os.path.basename(p):
            with open(p, encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    meta[(r.get("filename") or r.get("file_name") or r.get("uuid") or "").split(".")[0]] = r
    for p in _walk(d):
        stem = os.path.basename(p)[:-4]
        if "_chan" in stem:                      # ערוצים בודדים של מערך מיקרופונים - נלקח רק הממוצע/מונו
            continue
        firearm = os.path.basename(os.path.dirname(p))
        uuid = stem.split("_")[0]
        m = meta.get(stem) or meta.get(uuid) or {}
        mic = (m.get("microphone") or "").lower()
        dev = "phone" if ("internal mic" in mic or (m.get("device_manufacturer") or "") in ("Samsung", "Apple", "Google"))             else ("other_mic" if m else "unknown")
        yield {"id": f"gunshot_fit/{firearm}/{stem}", "path": p, "role": "positive", "targets": ["gunshot"],
               "group": dev, "meta": {"recording": uuid}}


def items_nonspeech7k():
    d = os.path.join(BASE, "nonspeech7k")
    labels = {}
    fsd = _fsd_ids()
    mp = os.path.join(d, "metadata of test set.csv")
    with open(mp, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if r["Augmentation type"].strip().lower() != "original":
                continue                       # עותקים מוגברים (רעש וכו') של אותו קטע - לא נספרים פעמיים
            overlap = "freesound" in r["source"].lower() and r["File_ID"].strip() in fsd
            labels[r["Filename"].strip()] = (r["Classname"].strip().lower(), overlap)
    for p in _walk(os.path.join(d, "extracted")):
        info = labels.get(os.path.basename(p))
        if info is None:
            continue
        lab, overlap = info
        if overlap:
            yield {"id": f"nonspeech7k/{lab}/{os.path.basename(p)}", "path": p, "role": "excluded_fsd_overlap",
                   "targets": [], "group": lab}
            continue
        if lab in ("screaming", "scream"):
            role, targets = "positive", ["scream", "distress"]
        elif lab in ("crying", "cry"):
            role, targets = "positive", ["distress"]
        else:
            role, targets = "hard_negative", []
        yield {"id": f"nonspeech7k/{lab}/{os.path.basename(p)}", "path": p, "role": role, "targets": targets,
               "group": lab}


ESC_TARGETS = {"glass_breaking": ["glass_break"], "siren": ["siren"], "crying_baby": ["distress"],
               "fireworks": ["fireworks"], "clock_alarm": ["alarm"]}
ESC_SKIP = {"door_wood_knock", "church_bells", "thunderstorm"}     # עמומים - לא חיובי ולא שלילי


def items_esc50():
    d = os.path.join(BASE, "esc50", "extracted", "ESC-50-master")
    fsd = _fsd_ids()
    with open(os.path.join(d, "meta", "esc50.csv"), encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        cat = r["category"]
        if cat in ESC_SKIP:
            continue
        overlap = r["src_file"] in fsd
        yield {"id": f"esc50/{cat}/{r['filename']}", "path": os.path.join(d, "audio", r["filename"]),
               "role": ("excluded_fsd_overlap" if overlap else
                        ("positive" if cat in ESC_TARGETS else "hard_negative")),
               "targets": ESC_TARGETS.get(cat, []), "group": cat}


SONYC_POS = {"7-2_person-or-small-group-shouting": ["distress"], "5-3_siren": ["siren"],
             "5-2_car-alarm": ["alarm"], "3-1_non-machinery-impact": ["impact"]}
SONYC_NOT_BACKGROUND = ("7-2_", "5-", "3-1_", "7-X_")    # צעקה, כל אות התרעה, חבטה, קול לא ידוע


def _sonyc_labels():
    by = {}
    with open(os.path.join(BASE, "sonyc_ust", "annotations.csv"), encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            by.setdefault(r["audio_filename"], []).append(r)
    out = {}
    for fn, rows in by.items():
        verified = [r for r in rows if r["annotator_id"] == "0"]
        use = verified or [r for r in rows if int(r["annotator_id"]) > 0]
        cols = [c[:-len("_presence")] for c in rows[0] if c.endswith("_presence") and "-" in c.split("_")[0]]
        lab = {}
        for c in cols:
            vals = [int(r[c + "_presence"]) for r in use if r[c + "_presence"] not in ("-1", "")]
            if vals:
                lab[c] = (sum(vals) >= 1) if verified else (sum(vals) * 2 > len(vals))
        out[fn] = {"labels": lab, "verified": bool(verified), "split": rows[0]["split"]}
    return out


def items_sonyc_ust():
    labels = _sonyc_labels()
    d = os.path.join(BASE, "sonyc_ust")
    for tf in sorted(f for f in os.listdir(d) if f.startswith("audio-") and f.endswith(".tar.gz")):
        with tarfile.open(os.path.join(d, tf), "r:gz") as t:
            for m in t:
                if not m.isfile() or not m.name.endswith(".wav"):
                    continue
                fn = os.path.basename(m.name)
                info = labels.get(fn)
                if info is None:
                    continue
                lab = info["labels"]
                targets = sorted({t_ for c, ts in SONYC_POS.items() if lab.get(c) for t_ in ts})
                busy = any(v and c.startswith(SONYC_NOT_BACKGROUND) for c, v in lab.items())
                role = "positive" if targets else ("ambiguous" if busy else "background")
                if role == "ambiguous":
                    continue
                yield {"id": f"sonyc/{fn}", "data": t.extractfile(m).read(), "role": role, "targets": targets,
                       "group": "verified" if info["verified"] else "crowd_majority",
                       "meta": {"split": info["split"]}}


def items_tau2020_mobile():
    d = os.path.join(BASE, "tau2020_mobile", "extracted")
    meta = None
    for p in _walk(d, ".csv"):
        if os.path.basename(p) == "meta.csv":
            meta = p
    scenes = {}
    with open(meta, encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            scenes[os.path.basename(r["filename"])] = (r["scene_label"], r.get("source_label", "?"))
    for p in _walk(d):
        sc = scenes.get(os.path.basename(p))
        if sc is None or sc[1] not in REAL_DEVICES:
            continue
        yield {"id": f"tau/{os.path.basename(p)}", "path": p, "role": "background", "targets": [],
               "group": f"{sc[0]}|{sc[1]}"}


DATASETS = {"gunshot_fit": items_gunshot_fit, "nonspeech7k": items_nonspeech7k, "esc50": items_esc50,
            "sonyc_ust": items_sonyc_ust, "tau2020_mobile": items_tau2020_mobile}


# ================================================================= המנוע (הקוד של האפליקציה)
def engine_view(rep):
    """דו"ח audio_intel -> מה שחשוב למדידה: אילו קטגוריות זוהו, האם ראש המצוקה זיהה, כמה נקודות."""
    import audio_intel as AI
    if rep is None:
        return None
    ev = sorted(e["category"] for e in rep.get("events") or [])
    d = rep.get("distress") or {}
    return {"events": ev, "distress": bool(d.get("detected")), "points": AI.hazard_points(rep),
            "quality": (rep.get("quality") or {}).get("status"),
            "scores": rep.get("category_scores") or {}, "distress_score": d.get("score")}


def analyze_item(it):
    import audio_intel as AI
    x, sr = read_wav(it["data"] if "data" in it else it["path"])
    out = {"id": it["id"], "role": it["role"], "targets": it["targets"], "group": it["group"],
           "seconds": round(len(x) / sr, 3), "meta": it.get("meta") or {}}
    if it["role"] in ("positive", "hard_negative"):
        out["clip"] = engine_view(AI.analyze(x, sr))
    if it["role"] == "background":
        n = int(WINDOW_S * sr)
        wins = [x[i:i + n] for i in range(0, len(x) - n + 1, n)][:MAX_BACKGROUND_WINDOWS_PER_CLIP]
        out["windows"] = [engine_view(AI.analyze(w, sr)) for w in wins]
    return out


def run(names):
    import audio_intel as AI
    assert AI.load(), "models/hazard_audio.npz חסר"
    os.makedirs(CACHE, exist_ok=True)
    for name in names:
        path = os.path.join(CACHE, f"{name}.jsonl")
        done = set()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                done = {json.loads(l)["id"] for l in fh if l.strip()}
        t0, n = time.time(), 0
        with open(path, "a", encoding="utf-8") as fh:
            for it in DATASETS[name]():
                if it["id"] in done:
                    continue
                if it["role"] == "excluded_fsd_overlap":
                    fh.write(json.dumps({"id": it["id"], "role": it["role"], "group": it["group"],
                                         "targets": it["targets"]}) + "\n")
                    continue
                try:
                    fh.write(json.dumps(analyze_item(it), ensure_ascii=False) + "\n")
                except Exception as exc:  # noqa: BLE001 - קובץ פגום לא עוצר את כל הריצה; נספר
                    fh.write(json.dumps({"id": it["id"], "role": "error", "error": str(exc)[:200]}) + "\n")
                n += 1
                if n % 200 == 0:
                    log(f"{name}: {n} ({time.time() - t0:.0f}s)")
        log(f"{name}: הסתיים, {n} חדשים ({time.time() - t0:.0f}s)")


# ================================================================= דו"ח
def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - r) / d), min(1.0, (c + r) / d))


def _hit(view, target):
    if view is None:
        return False
    return view["distress"] if target == "distress" else target in view["events"]


def load_rows(name):
    p = os.path.join(CACHE, f"{name}.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def summarize():
    import audio_intel as AI
    AI.load()
    card = AI._STATE["card"]
    cats = sorted(card["categories"])
    scoring = [c for c in cats if AI.SEVERITY_POINTS.get(card["categories"][c]["severity_prior"], 0) > 0]
    out = {"created": time.strftime("%Y-%m-%d"), "model_version": card["version"], "window_s": WINDOW_S,
           "datasets": {}, "fsd50k_eval_recall": {c: card["categories"][c]["metrics"][card["categories"][c]["champion"]]
                                                  ["eval"]["recall"] for c in cats},
           "scoring_categories": scoring, "demote_rule": DEMOTE_WINDOW_RATE}
    for name in DATASETS:
        rows = load_rows(name)
        if not rows:
            continue
        ds = {"n": len(rows), "excluded_fsd_overlap": sum(r["role"] == "excluded_fsd_overlap" for r in rows),
              "errors": sum(r["role"] == "error" for r in rows), "recall": {}, "hard_negative": {}, "background": {}}
        pos = [r for r in rows if r["role"] == "positive"]
        for tgt in sorted({t for r in pos for t in r["targets"]}):
            grps = sorted({r["group"] for r in pos if tgt in r["targets"]})
            for grp in [None] + (grps if len(grps) > 1 else []):
                rs = [r for r in pos if tgt in r["targets"] and (grp is None or r["group"] == grp)]
                if grp is not None and len(rs) < 15:
                    continue
                k = sum(_hit(r["clip"], tgt) for r in rs)
                any_pts = sum(bool(r["clip"]) and (r["clip"]["points"] > 0 or r["clip"]["distress"]) for r in rs)
                ds["recall"][f"{tgt}" + (f" | {grp}" if grp else "")] = {
                    "n": len(rs), "hit": k, "rate": k / len(rs), "ci": wilson(k, len(rs)),
                    "any_points_or_distress": any_pts / len(rs)}
        hn = [r for r in rows if r["role"] == "hard_negative"]
        for grp in [None] + sorted({r["group"] for r in hn}):
            rs = [r for r in hn if grp is None or r["group"] == grp]
            if not rs or (grp is not None and len(rs) < 15):
                continue
            pts = sum(bool(r["clip"]) and r["clip"]["points"] > 0 for r in rs)
            dis = sum(bool(r["clip"]) and r["clip"]["distress"] for r in rs)
            ds["hard_negative"][grp or "all"] = {"n": len(rs), "points_rate": pts / len(rs), "points_ci": wilson(pts, len(rs)),
                                                 "distress_rate": dis / len(rs), "distress_ci": wilson(dis, len(rs))}
        bg = [r for r in rows if r["role"] == "background"]
        groups = [None] + (sorted({r["group"].split("|")[0] for r in bg}) if name == "tau2020_mobile" else [])
        for grp in groups:
            wins = [w for r in bg if grp is None or r["group"].split("|")[0] == grp for w in r.get("windows") or [] if w]
            if not wins:
                continue
            n = len(wins)
            row = {"windows": n, "hours": n * WINDOW_S / 3600,
                   "any_event": sum(bool(w["events"]) for w in wins) / n,
                   "points": sum(w["points"] > 0 for w in wins) / n,
                   "points_ci": wilson(sum(w["points"] > 0 for w in wins), n),
                   "distress": sum(w["distress"] for w in wins) / n,
                   "distress_ci": wilson(sum(w["distress"] for w in wins), n),
                   "unavailable_input": sum(w["quality"] == "unavailable" for w in wins) / n,
                   "per_category": {c: sum(c in w["events"] for w in wins) / n for c in cats}}
            ds["background"][grp or "all"] = row
        out["datasets"][name] = ds
    # כלל ההחלטה: רקע אמיתי (טלפונים + רחוב)
    real = [w for name in ("tau2020_mobile", "sonyc_ust") for r in load_rows(name) if r["role"] == "background"
            for w in r.get("windows") or [] if w]
    out["decision"] = {}
    if real:
        for c in scoring + ["distress"]:
            k = sum((w["distress"] if c == "distress" else c in w["events"]) for w in real)
            rate = k / len(real)
            out["decision"][c] = {"windows": len(real), "false_windows": k, "rate": rate, "ci": wilson(k, len(real)),
                                  "recommend_demote": rate > DEMOTE_WINDOW_RATE}
    return out


def _p(x, d=1):
    return "—" if x is None else f"{100 * x:.{d}f}%"


def _ci(c, d=1):
    return "—" if not c or c[0] is None else f"{100 * c[0]:.{d}f}–{100 * c[1]:.{d}f}"


HE = {"gunshot": "ירי", "explosion": "פיצוץ", "fireworks": "זיקוקים", "glass_break": "שבירת זכוכית", "siren": "סירנה",
      "alarm": "אזעקה", "scream": "צרחה", "impact": "חבטה", "distress": "ראש המצוקה"}


def report(out=None):
    out = out or summarize()
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    L = ["# מבחן חיצוני למנוע האודיו - הקלטות אמיתיות שלא מ-FSD50K", "",
         f"נוצר אוטומטית ע\"י `research_tools/danger_audio_external.py` ({out['created']}), מודל {out['model_version']}. "
         "כל מספר כאן מהריצה. המדידה עוברת דרך `audio_intel.analyze` - הקוד שרץ באפליקציה.", "",
         "מקורות, רישיונות וייחוס: `research_data/audio/MANIFEST.json` (danger_sources). ESC-50: קטעים שמזהה "
         "ה-Freesound שלהם מופיע ב-FSD50K הוצאו.", ""]
    L += ["## 1. זיהוי אירועים אמיתיים (recall ברמת קטע)", "",
          "| מאגר | יעד (| קבוצה) | n | זוהה (95%) | FSD50K eval | המנוע הוסיף נקודות / מצוקה |", "|---|---|---|---|---|---|"]
    for name, ds in out["datasets"].items():
        for key, r in ds["recall"].items():
            tgt = key.split(" | ")[0]
            ref = out["fsd50k_eval_recall"].get(tgt)
            L.append(f"| {name} | {HE.get(tgt, tgt)}{(' | ' + key.split(' | ')[1]) if ' | ' in key else ''} | {r['n']} | "
                     f"{_p(r['rate'])} ({_ci(r['ci'])}) | {_p(ref) if ref is not None else '—'} | {_p(r['any_points_or_distress'])} |")
    L += ["", "## 2. דומים קשים (צחוק, שיעול, צלילי סביבה) - כמה פעמים המנוע טעה", "",
          "| מאגר | קבוצה | n | קיבל נקודות (95%) | ראש המצוקה (95%) |", "|---|---|---|---|---|"]
    for name, ds in out["datasets"].items():
        zero = [g for g, r in ds["hard_negative"].items() if g != "all" and not r["points_rate"] and not r["distress_rate"]]
        if zero:
            L.append(f"| {name} | {len(zero)} קבוצות בלי אף טעות | — | 0 | 0 |")
        for grp, r in ds["hard_negative"].items():
            if grp in zero:
                continue
            L.append(f"| {name} | {grp} | {r['n']} | {_p(r['points_rate'])} ({_ci(r['points_ci'])}) | "
                     f"{_p(r['distress_rate'])} ({_ci(r['distress_ci'])}) |")
    L += ["", f"## 3. רקע אמיתי - חלונות של {out['window_s']} ש' (כמו הערכה אחרי טריגר)", "",
          "| מאגר | סצנה | חלונות (שעות) | אירוע כלשהו | נקודות (95%) | ראש המצוקה (95%) | מיקרופון לא זמין |",
          "|---|---|---|---|---|---|---|"]
    for name, ds in out["datasets"].items():
        for grp, r in ds["background"].items():
            L.append(f"| {name} | {grp} | {r['windows']} ({r['hours']:.1f}) | {_p(r['any_event'], 2)} | "
                     f"{_p(r['points'], 2)} ({_ci(r['points_ci'], 2)}) | {_p(r['distress'], 2)} ({_ci(r['distress_ci'], 2)}) | "
                     f"{_p(r['unavailable_input'], 2)} |")
    if out["decision"]:
        L += ["", f"## 4. כלל ההחלטה (נקבע מראש): קטגוריה שמוסיפה נקודות עם > {_p(out['demote_rule'])} חלונות שגויים "
                  "ברקע אמיתי (טלפונים + רחוב) -> המלצה להוריד ל\"מידע בלבד\"", "",
              "| קטגוריה | חלונות שגויים | שיעור (95%) | המלצה |", "|---|---|---|---|"]
        for c, r in out["decision"].items():
            L.append(f"| {HE.get(c, c)} | {r['false_windows']} / {r['windows']} | {_p(r['rate'], 2)} ({_ci(r['ci'], 2)}) | "
                     + ("להוריד ל'מידע בלבד'" if r["recommend_demote"] else "להשאיר") + " |")
    L += ["", "## מגבלות",
          "- ירי במטווח ≠ ירי ברחוב עירוני (הדהודים, מרחק). צעקה ב-SONYC ≠ בהכרח מצוקה.",
          "- תוויות SONYC: מאומתות כשיש, אחרת רוב של 3 מתנדבים.",
          "- TAU: רק מכשירים אמיתיים (a/b/c); מכשירים מדומים (s1-s6) הוצאו.",
          "- Nonspeech7k ו-ESC-50 נאספו בחלקם מאותם אתרים כמו FSD50K; חפיפה מלאה נבדקה רק ל-ESC-50.",
          "- מאגרים ברישיון NC (TAU, ESC-50) שימשו להערכה בלבד, לא לאימון."]
    with open(OUT_REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    log(f"דו\"ח: {OUT_REPORT}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "run":
        run(sys.argv[2:] or list(DATASETS))
        report()
    else:
        report()

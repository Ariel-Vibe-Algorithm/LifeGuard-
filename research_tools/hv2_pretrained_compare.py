# -*- coding: utf-8 -*-
"""
hv2_pretrained_compare.py - השוואה הוגנת של מקודדי אודיו מאומנים-מראש מול YAMNet (המקודד באפליקציה)
לקטגוריות הסכנה של LifeGuard. מחקר בלבד: לא מוסיף מודל לאפליקציה ולא נוגע ב-models/.

מקודדים: YAMNet TFLite (בסיס), PANNs CNN14-16k, PANNs MobileNetV2, AST (MIT, AudioSet), CLAP (LAION, HTSAT unfused)
- לכולם אותו בדיקה (probe) לינארית; ל-CLAP גם zero-shot עם פרומפטים. BEATs: לא נכלל (ראו LICENCES).

שיטה (זהה לכל מקודד):
1. תת-מדגם קבוע (seed קבוע) מתוך research_data/manifests/hazard_v2_manifest.jsonl - בלי פיצול מחדש.
   אימון / כיול רק על split in (train, val) ו-commercial_ok; מבחן רק על split == test.
2. כל קטע: עד 10 ש' (אם ארוך יותר - חלון 10 ש' עם האנרגיה הגבוהה ביותר; כלל עיוור לתווית), מינימום 1 ש' (ריפוד אפסים).
   רקע (SONYC / TAU): חלונות של 1.5 ש' (= אורך ההקלטה באפליקציה אחרי טריגר).
   האודיו נשמר פעם אחת ב-32kHz (float16); כל מקודד מקבל resample (scipy, polyphase) לקצב הטבעי שלו.
3. כל מקודד רץ פעם אחת על הקטע; הייצוג המפורט-בזמן האחרון שלו מקובץ לסגמנטים של 1 ש';
   embedding לקטע = [ממוצע הסגמנטים, מקסימום הסגמנטים].
4. לכל קטגוריה: רגרסיה לוגיסטית (sklearn, איזון מחלקות, StandardScaler) על train; C נבחר לפי AUC על val;
   סף נבחר על val כך ש-<=1% משליליי ה-val עוברים אותו. מדווח על test: AUC (bootstrap 95%), recall בסף (Wilson 95%),
   שיעור התראות שווא על שליליים קשים ועל חלונות רקע (Wilson), לפי מקור / סביבה / מכשיר; והפרש מזווג מול YAMNet.

שימוש (בסביבת המחקר research_data/venv_models - לא ב-.venv של האפליקציה):
  python -I research_tools/hv2_pretrained_compare.py subset          # בניית תת-המדגם
  python -I research_tools/hv2_pretrained_compare.py audio           # חילוץ אודיו למטמון (ממשיך מאיפה שנעצר)
  python -I research_tools/hv2_pretrained_compare.py embed yamnet panns_cnn14_16k panns_mnv2 ast clap
  python -I research_tools/hv2_pretrained_compare.py latency
  python -I research_tools/hv2_pretrained_compare.py eval            # -> research/hazard_v2_pretrained_comparison.{md,json}
"""
import csv
import hashlib
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
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MANIFEST = os.path.join(ROOT, "research_data", "manifests", "hazard_v2_manifest.jsonl")
FSD = os.path.join(ROOT, "research", "samples", "fsd50k")
SONYC_DIR = os.path.join(ROOT, "research_data", "audio", "sonyc_ust")
WORK = os.path.join(ROOT, "research_data", "hv2_compare")
SUBSET = os.path.join(WORK, "subset.jsonl")
CORE = os.path.join(WORK, "core_keys.txt")
AUDIO_DIR = os.path.join(WORK, "audio_32k")
EMB_DIR = os.path.join(WORK, "emb_core")
LAT_JSON = os.path.join(WORK, "latency.json")
MODELS = os.path.join(ROOT, "research_data", "models_cache")
OUT_MD = os.path.join(ROOT, "research", "hazard_v2_pretrained_comparison.md")
OUT_JSON = os.path.join(ROOT, "research", "hazard_v2_pretrained_comparison.json")

SEED = 20261115
CACHE_SR = 32000            # כל המקודדים משתמשים בתדרים עד 14kHz לכל היותר -> 32kHz לא מאבד מידע
MAX_S, MIN_S, WIN_S, SEG_S = 10.0, 1.0, 1.5, 1.0
SHARD = 400
FP_BUDGET = 0.01            # סף: <=1% משליליי val
N_BOOT = 1000

CATS = ("gunshot", "explosion", "fireworks", "glass_break", "siren", "alarm", "scream", "distress_voice")
HARD = ("fireworks", "drums_music", "music", "horn", "crowd_cheer", "laughter", "construction", "traffic", "speech",
        "door_knock", "thunder")
KEY_HARD = {"gunshot": "fireworks", "explosion": "thunder", "fireworks": "drums_music", "glass_break": "door_knock",
            "siren": "horn", "alarm": "horn", "scream": "laughter", "distress_voice": "crowd_cheer"}
HE = {"gunshot": "ירי", "explosion": "פיצוץ", "fireworks": "זיקוקים", "glass_break": "שבירת זכוכית", "siren": "סירנה",
      "alarm": "אזעקה", "scream": "צרחה", "distress_voice": "קול מצוקה"}
# גדלי תת-המדגם (מתועדים בדו"ח)
CAP_POS_TRAIN = 600
N_HARD = {"train": 150, "val": 60, "test": 120}       # לכל תגית שלילי-קשה (קטעים בלי שום תגית יעד)
N_OTHER = {"train": 300, "val": 100, "test": 300}     # קטעי FSD50K אחרים (בלי יעד ובלי שלילי קשה)
N_BG = {"train": 300, "val": 150}                     # קטעי SONYC בלי שום תווית -> חלון 1.5 ש' אחד לקטע
N_TAU = 400                                           # TAU, טלפונים (b/c), רחוב / כלי רכב -> חלון אחד לקטע
SONYC_TEST_WINDOWS = 6                                # SONYC test: מעט קטעי רקע -> 6 חלונות לקטע
N_ESC_OTHER = 150
# core = מה שבאמת רץ על כל המקודדים (ראו build_core): ה-CPU היה עמוס בעבודה מקבילה, AST / CLAP / CNN14 איטיים מדי
CORE_POS = {"train": 150, "val": 60}
CORE_TEST_POS_PER_GROUP = 100         # לכל קטגוריה x מקור (fsd50k, gunshot_fit:phone, nonspeech7k, ...)
CORE_HARD = {"train": 40, "val": 35, "test": 50}
CORE_OTHER = {"train": 80, "val": 60, "test": 100}
CORE_BG = {"train": 80, "val": 100, "test": 100}       # לכל קבוצת רקע (sonyc_ust / tau:street / tau:vehicle)
CLIP_S = 5.0          # בזמן ה-embedding: חלון 5 ש' עם האנרגיה הגבוהה ביותר מתוך הקטע השמור (עד 10 ש'), לכל המקודדים

ENCODERS = ("yamnet", "panns_cnn14_16k", "panns_mnv2", "ast", "clap")
ENC_LABEL = {"yamnet": "YAMNet (בסיס)", "panns_cnn14_16k": "PANNs CNN14-16k", "panns_mnv2": "PANNs MobileNetV2",
             "ast": "AST", "clap": "CLAP (probe)", "clap_zeroshot": "CLAP zero-shot"}

# רישוי - נבדק ידנית מול המקור (ראו הדו"ח); מתועד כאן כדי שהדו"ח ייווצר מהסקריפט
LICENCES = {
    "yamnet": dict(weights="research/models/yamnet/yamnet_float_embeddings.tflite (TF Hub / Kaggle Models)",
                   licence="Apache-2.0", commercial="כן", data="AudioSet (תוויות CC BY 4.0; אודיו מ-YouTube)",
                   source="https://tfhub.dev/google/yamnet/1 ; tensorflow/models (Apache-2.0)"),
    "panns_cnn14_16k": dict(weights="Cnn14_16k_mAP=0.438.pth", licence="CC BY 4.0 (משקלים, Zenodo 3987831); MIT (קוד)",
                            commercial="כן, עם ייחוס", data="AudioSet",
                            source="https://zenodo.org/records/3987831 ; github.com/qiuqiangkong/audioset_tagging_cnn"),
    "panns_mnv2": dict(weights="MobileNetV2_mAP=0.383.pth", licence="CC BY 4.0 (משקלים, Zenodo 3987831); MIT (קוד)",
                       commercial="כן, עם ייחוס", data="AudioSet",
                       source="https://zenodo.org/records/3987831"),
    "ast": dict(weights="MIT/ast-finetuned-audioset-10-10-0.4593 (Hugging Face)", licence="BSD-3-Clause",
                commercial="כן", data="ImageNet (DeiT, אתחול) + AudioSet",
                source="https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593"),
    "clap": dict(weights="laion/clap-htsat-unfused (Hugging Face)", licence="Apache-2.0 (כרטיס המודל)",
                 commercial="המשקלים: כן. הסתייגות: נתוני האימון LAION-Audio-630K כוללים מקורות ברישיונות "
                            "לא-מסחריים (למשל BBC Sound Effects, חלק מ-Freesound) - סיכון משפטי לא פתור",
                 data="LAION-Audio-630K (Freesound, BBC SFX, Epidemic, AudioCaps/AudioSet ועוד)",
                 source="https://huggingface.co/laion/clap-htsat-unfused"),
    "beats": dict(weights="BEATs_iter3+ (AS2M)", licence="MIT (מאגר microsoft/unilm)", commercial="לא נבדק בפועל",
                  data="AudioSet", source="https://github.com/microsoft/unilm/tree/master/beats",
                  skipped="לא נכלל: המשקלים הרשמיים מופצים רק בקישורי OneDrive (1drv.ms); מהמכונה הזו החיבור נכשל "
                          "(TLS handshake / תעודה לא מהימנה) ולא עוקפים אימות תעודות. יש העתקים לא-רשמיים ב-Hugging Face "
                          "(צד שלישי, מקור/רישוי לא מאומת) - לא נעשה בהם שימוש. בנוסף נדרש קוד מודל חיצוני (BEATs.py)."),
}


def log(m):
    print(time.strftime("%H:%M:%S"), m, flush=True)


def _h(key, mod=10 ** 9):
    return int(hashlib.sha256(f"{SEED}:{key}".encode()).hexdigest(), 16) % mod


# ===================================================================== תת-מדגם
def _fsd_raw_labels():
    out = {}
    for split in ("dev", "eval"):
        with open(os.path.join(FSD, "meta", "FSD50K.ground_truth", f"{split}.csv"), encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[r["fname"]] = set(r["labels"].split(","))
    return out


def labels_for(row, raw):
    """תווית לכל קטגוריה: 1 חיובי, 0 שלילי, None מוחרג (עמום). כללי ההחרגה כמו hazard_audio_train (v1)."""
    tags = set(row["tags"])
    raw = raw or set()
    L = {}
    L["gunshot"] = int("gunshot" in tags)
    L["explosion"] = None if tags & {"gunshot", "fireworks"} else int("explosion" in tags)   # Explosion הוא אב של ירי/זיקוקים
    L["fireworks"] = int("fireworks" in tags)
    L["glass_break"] = 1 if "glass_break" in tags else (None if "Glass" in raw else 0)
    L["siren"] = int("siren" in tags)
    if "siren" in tags or raw & {"Telephone", "Ringtone", "Doorbell", "Bicycle_bell"}:
        L["alarm"] = None if "alarm" in tags or "siren" in tags else 0
    else:
        L["alarm"] = 0 if "horn" in tags else int("alarm" in tags)    # צופר = השלילי הקשה המרכזי
    if "scream" in tags:
        L["scream"] = 1
    elif "distress_voice" in tags and (row["source"] == "sonyc_ust" or raw & {"Shout", "Yell"}):
        L["scream"] = None
    else:
        L["scream"] = 0
    L["distress_voice"] = int("distress_voice" in tags)
    return L


def _src_group(r):
    s = r["source"]
    if s == "gunshot_fit":
        return f"gunshot_fit:{r['device']}"
    if s == "tau2020_mobile":
        return f"tau:{r['env']}"
    return s


def build_subset():
    rows = [json.loads(l) for l in open(MANIFEST, encoding="utf-8") if l.strip()]
    raw = _fsd_raw_labels()
    for r in rows:
        r["_L"] = labels_for(r, raw.get(r.get("freesound_id")) if r["source"] == "fsd50k" else None)
    rng = np.random.default_rng(SEED)
    items, seen = [], set()

    def add(r, role, kind="clip", offset=None, wi=0):
        key = r["id"] if kind == "clip" else f"{r['id']}#w{wi}"
        if key in seen:
            return
        seen.add(key)
        items.append({"key": key, "row_id": r["id"], "source": r["source"], "split": r["split"], "role": role,
                      "kind": kind, "offset": offset, "labels": r["_L"], "tags": r["tags"], "env": r["env"],
                      "device": r["device"], "group": _src_group(r), "ref": r["ref"],
                      "commercial_ok": r["commercial_ok"]})

    def sample(pool, n):
        pool = sorted(pool, key=lambda r: r["id"])
        if len(pool) <= n:
            return pool
        idx = rng.choice(len(pool), size=n, replace=False)
        return [pool[i] for i in sorted(idx)]

    no_target = lambda r: not (set(r["tags"]) & set(CATS))
    for split in ("train", "val", "test"):
        pool = [r for r in rows if r["split"] == split and (split == "test" or r["commercial_ok"])]
        for c in CATS:
            pos = [r for r in pool if r["_L"][c] == 1]
            for r in (sample(pos, CAP_POS_TRAIN) if split == "train" else pos):
                add(r, "positive")
        for t in HARD:
            if t == "fireworks":
                continue                       # זיקוקים כבר בפנים כקטגוריה (ומשמשים שלילי קשה לירי / פיצוץ)
            cand = [r for r in pool if t in r["tags"] and no_target(r) and r["source"] != "tau2020_mobile"]
            for r in sample(cand, N_HARD[split]):
                add(r, "hard_negative")
        other = [r for r in pool if r["source"] == "fsd50k" and not (set(r["tags"]) & (set(CATS) | set(HARD)))]
        for r in sample(other, N_OTHER[split]):
            add(r, "other_negative")
        if split in N_BG:
            bg = [r for r in pool if r["source"] == "sonyc_ust" and r.get("unlabeled_any")]
            for r in sample(bg, N_BG[split]):
                add(r, "background", "window", offset=-1.0)          # -1 = היסט אקראי-דטרמיניסטי בזמן החילוץ
        else:
            for r in sorted((r for r in pool if r["source"] == "sonyc_ust" and r.get("unlabeled_any")),
                            key=lambda r: r["id"]):
                for w in range(SONYC_TEST_WINDOWS):
                    add(r, "background", "window", offset=w * WIN_S, wi=w)
            tau = [r for r in pool if r["source"] == "tau2020_mobile" and r["device"] == "phone"
                   and r["env"] in ("street", "vehicle")]
            for r in sample(tau, N_TAU):
                add(r, "background", "window", offset=-1.0)
            esc = [r for r in pool if r["source"] == "esc50"]
            for r in esc:
                if r["tags"]:
                    add(r, "hard_negative" if no_target(r) else "positive")
            for r in sample([r for r in esc if not r["tags"]], N_ESC_OTHER):
                add(r, "other_negative")
            for r in (r for r in pool if r["source"] == "nonspeech7k"):
                add(r, "positive" if not no_target(r) else "hard_negative")
    # רצף לפי מקור -> חילוץ יעיל (tar של SONYC נקרא ברצף)
    order = {"fsd50k": 0, "gunshot_fit": 1, "esc50": 2, "nonspeech7k": 3, "tau2020_mobile": 4, "sonyc_ust": 5}
    items.sort(key=lambda it: (order[it["source"]], it["key"]))
    os.makedirs(WORK, exist_ok=True)
    with open(SUBSET, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    log(f"תת-מדגם: {len(items)} פריטים -> {SUBSET}")
    for k, v in subset_stats(items).items():
        log(f"  {k}: {v}")
    return items


def build_core():
    """תת-מדגם מצומצם ('core') מתוך subset.jsonl - כי ה-CPU היה עמוס בעבודה מקבילה ו-AST / CLAP / CNN14 איטיים מדי
    לכל 9.5K הפריטים. בחירה דטרמיניסטית לפי hash של המפתח (מקוננת: הגדלת המכסות רק מוסיפה פריטים)."""
    full = load_subset(core=False)
    rank = lambda it: _h("core:" + it["key"])
    chosen = set()

    def take(cands, n):
        for it in sorted(cands, key=rank)[:n]:
            chosen.add(it["key"])

    for split in ("train", "val", "test"):
        S = [it for it in full if it["split"] == split]
        for c in CATS:
            pos = [it for it in S if it["labels"][c] == 1]
            if split == "test":
                for g in sorted({it["group"] for it in pos}):
                    take([it for it in pos if it["group"] == g], CORE_TEST_POS_PER_GROUP)
            else:
                take(pos, CORE_POS[split])
        for t in HARD:
            take([it for it in S if it["role"] == "hard_negative" and t in it["tags"]], CORE_HARD[split])
        take([it for it in S if it["role"] == "hard_negative" and it["source"] == "nonspeech7k"], CORE_HARD[split])
        take([it for it in S if it["role"] == "other_negative"], CORE_OTHER[split])
        bg = [it for it in S if it["role"] == "background"]
        for g in sorted({it["group"] for it in bg}):
            take([it for it in bg if it["group"] == g], CORE_BG[split])
    with open(CORE, "w", encoding="utf-8") as fh:
        for it in full:
            if it["key"] in chosen:
                fh.write(it["key"] + "\n")
    items = load_subset()
    log(f"core: {len(items)} פריטים -> {CORE}")
    for k, v in subset_stats(items).items():
        log(f"  {k}: {v}")


def load_subset(core=True):
    with open(SUBSET, encoding="utf-8") as fh:
        items = [json.loads(l) for l in fh if l.strip()]
    if core and os.path.exists(CORE):
        keep = set(open(CORE, encoding="utf-8").read().split())
        items = [it for it in items if it["key"] in keep]
    return items


def subset_stats(items):
    from collections import Counter
    st = {"by_split_role": dict(Counter(f"{i['split']}|{i['role']}" for i in items))}
    for c in CATS:
        st[c] = {s: {"pos": sum(1 for i in items if i["split"] == s and i["labels"][c] == 1),
                     "neg": sum(1 for i in items if i["split"] == s and i["labels"][c] == 0),
                     "excluded": sum(1 for i in items if i["split"] == s and i["labels"][c] is None)}
                 for s in ("train", "val", "test")}
    st["train_val_all_commercial_ok"] = all(i["commercial_ok"] for i in items if i["split"] in ("train", "val"))
    return st


# ===================================================================== אודיו
def _read(it, zips, sonyc):
    from research_tools.danger_audio_external import read_wav
    ref = it["ref"]
    if it["source"] == "fsd50k":
        z = zips[ref["zip_split"]]
        return read_wav(z.read(z.byname[ref["fname"]]))
    if it["source"] == "sonyc_ust":
        return read_wav(sonyc[ref["member"]])
    return read_wav(ref["path"])


def _prep(x, sr, it):
    from scipy.signal import resample_poly
    if it["kind"] == "window":
        n = int(WIN_S * sr)
        if it["offset"] is not None and it["offset"] >= 0:
            s = int(it["offset"] * sr)
        else:
            s = int((_h(it["key"]) % 10_000) / 10_000 * max(0, len(x) - n))
        x = x[s:s + n]
    elif len(x) > MAX_S * sr:
        n, hop = int(MAX_S * sr), int(0.5 * sr)
        nb = len(x) // hop
        e = (x[:nb * hop].astype(np.float64) ** 2).reshape(nb, hop).sum(axis=1)      # אנרגיה לבלוקים של 0.5 ש'
        k = int(MAX_S / 0.5)
        cs = np.concatenate([[0.0], np.cumsum(e)])
        best = int(np.argmax(cs[k:] - cs[:-k])) if nb >= k else 0
        x = x[best * hop:best * hop + n]
    if sr != CACHE_SR:
        g = math.gcd(int(sr), CACHE_SR)
        x = resample_poly(x, CACHE_SR // g, int(sr) // g)
    if len(x) < MIN_S * CACHE_SR:
        x = np.concatenate([x, np.zeros(int(MIN_S * CACHE_SR) - len(x))])
    return np.asarray(x, dtype=np.float16)


def extract_audio(workers=6):
    from multiprocessing import Pool
    items = load_subset(core=False)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    todo = [k for k in range(0, len(items), SHARD)
            if not os.path.exists(os.path.join(AUDIO_DIR, f"shard_{k // SHARD:03d}.npz"))]
    t0 = time.time()
    with Pool(workers) as pool:
        for k in pool.imap_unordered(_audio_shard, todo):
            log(f"אודיו: shard {k // SHARD} ({time.time() - t0:.0f}s)")


_ZIPS = {}


def _audio_shard(k):
    from research_tools.split_zip import SplitZip
    items = load_subset(core=False)
    zips = _ZIPS
    for split in ("dev", "eval"):
        if split not in zips:
            z = SplitZip(os.path.join(FSD, "raw", f"FSD50K.{split}_audio.zip"))
            z.byname = {os.path.basename(n)[:-4]: n for n in z.names() if n.endswith(".wav")}
            zips[split] = z
    out = os.path.join(AUDIO_DIR, f"shard_{k // SHARD:03d}.npz")
    if True:
        chunk = items[k:k + SHARD]
        sonyc = {}
        need = {it["ref"]["member"]: it["ref"]["tar"] for it in chunk if it["source"] == "sonyc_ust"}
        for tf in sorted(set(need.values())):
            with tarfile.open(os.path.join(SONYC_DIR, tf), "r:gz") as t:
                for m in t:
                    if m.name in need:
                        sonyc[m.name] = t.extractfile(m).read()
        datas, offs, durs = [], [0], []
        for it in chunk:
            x, sr = _read(it, zips, sonyc)
            y = _prep(x, sr, it)
            durs.append(min(len(x) / sr, WIN_S if it["kind"] == "window" else MAX_S))
            datas.append(y)
            offs.append(offs[-1] + len(y))
        np.savez(out + ".tmp.npz", keys=np.array([it["key"] for it in chunk]), data=np.concatenate(datas),
                 offsets=np.array(offs, dtype=np.int64), dur=np.array(durs, dtype=np.float32))
        os.replace(out + ".tmp.npz", out)
    return k


def crop_clip(x, dur, sr=CACHE_SR, max_s=CLIP_S):
    """חלון max_s עם האנרגיה הגבוהה ביותר (בלוקים של 0.5 ש'; עיוור לתווית). חלונות רקע (1.5 ש') לא משתנים."""
    n = int(max_s * sr)
    if dur <= max_s + 1e-6 or len(x) <= n:
        return x, min(dur, max_s)
    hop = int(0.5 * sr)
    nb = len(x) // hop
    e = (x[:nb * hop].astype(np.float64) ** 2).reshape(nb, hop).sum(axis=1)
    k = int(max_s / 0.5)
    cs = np.concatenate([[0.0], np.cumsum(e)])
    best = int(np.argmax(cs[k:] - cs[:-k])) if nb >= k else 0
    return x[best * hop:best * hop + n], max_s


def iter_audio(skip=None):
    """(shard_index, keys, list of float32 arrays @32kHz, durations) - רק פריטי ה-core, אחרי חיתוך ל-CLIP_S."""
    keep = {it["key"] for it in load_subset()}
    for f in sorted(os.listdir(AUDIO_DIR)):
        if not (f.startswith("shard_") and f.endswith(".npz")) or ".tmp" in f:
            continue
        if skip is not None and skip(int(f[6:9])):
            continue
        d = np.load(os.path.join(AUDIO_DIR, f), allow_pickle=False)
        o, data, keys, durs = d["offsets"], d["data"], [str(k) for k in d["keys"]], d["dur"]   # פעם אחת בלבד
        sel = [i for i, k in enumerate(keys) if k in keep]
        if not sel:
            continue
        xs, ds = [], []
        for i in sel:
            x, du = crop_clip(data[o[i]:o[i + 1]].astype(np.float32), float(durs[i]))
            xs.append(x)
            ds.append(du)
        yield int(f[6:9]), [keys[i] for i in sel], xs, np.array(ds, dtype=np.float32)


def resample(x, sr_from, sr_to):
    from scipy.signal import resample_poly
    if sr_from == sr_to:
        return np.asarray(x, dtype=np.float32)
    g = math.gcd(sr_from, sr_to)
    return resample_poly(x, sr_to // g, sr_from // g).astype(np.float32)


def seg_pool(feats, centres, dur):
    """frames (T, D) + זמני מרכז -> סגמנטים של 1 ש' (ממוצע) -> [ממוצע, מקסימום] על הסגמנטים."""
    feats = np.asarray(feats, dtype=np.float32)
    nseg = max(1, int(math.ceil(dur / SEG_S - 1e-6)))
    idx = np.minimum(np.floor(np.asarray(centres) / SEG_S).astype(int), nseg - 1)
    segs = np.stack([feats[idx == s].mean(axis=0) for s in np.unique(idx)])
    return np.concatenate([segs.mean(axis=0), segs.max(axis=0)]), len(segs)


# ===================================================================== מקודדים
class YamnetEnc:
    name, sr = "yamnet", 16000

    def __init__(self, threads=None):
        from ai_edge_litert.interpreter import Interpreter
        from research_tools import yamnet_detector as Y
        self.Y = Y
        self.m = Y.Yamnet()
        if threads is not None:
            self.m.it = Interpreter(model_path=Y.EMB_MODEL, num_threads=threads)
            outs = self.m.it.get_output_details()
            self.m.inp = self.m.it.get_input_details()[0]["index"]
            self.m.out_scores, self.m.out_emb = outs[0]["index"], outs[1]["index"]
            self.m._len = None
        self.size_mb = os.path.getsize(Y.EMB_MODEL) / 1e6
        self.params = None

    def frames(self, x):
        _, e = self.m.analyze(x, self.sr)
        c = np.arange(len(e)) * self.Y.HOP_SECONDS + 0.48
        return e, c

    def embed(self, x, dur):
        e, c = self.frames(x)
        return seg_pool(e, c, dur)


EMB_THREADS = int(os.environ.get("LG_THREADS", "4"))     # ל-embedding: מעט חוטים (CPU משותף; 12 חוטים = קריסת ביצועים)
_TORCH_DEFAULT = {}


def _torch_threads(n):
    """n=None -> ברירת המחדל של PyTorch (כפי שנקבעה בטעינה)."""
    import torch
    _TORCH_DEFAULT.setdefault("n", torch.get_num_threads())
    torch.set_num_threads(n or _TORCH_DEFAULT["n"])
    return torch.get_num_threads()


class PannsEnc:
    """ארכיטקטורות PANNs (Kong et al. 2020) - שחזור של models.py מהמאגר (MIT) בשביל טעינת המשקלים מ-Zenodo.
    ספקטרוגרמה ו-log-mel מחושבים מהמטריצות השמורות במשקלים עצמם (torchlibrosa) - זהה למקור."""

    CFG = {"panns_cnn14_16k": dict(file="Cnn14_16k_mAP=0.438.pth", sr=16000, n_fft=512, hop=160),
           "panns_mnv2": dict(file="MobileNetV2_mAP=0.383.pth", sr=32000, n_fft=1024, hop=320)}

    def __init__(self, name):
        import torch
        self.name = name
        cfg = self.CFG[name]
        self.sr, self.n_fft, self.hop = cfg["sr"], cfg["n_fft"], cfg["hop"]
        path = os.path.join(MODELS, "panns", cfg["file"])
        # weights_only (בלי הרצת קוד מה-pickle); הקובץ של CNN14-16k מכיל גם מערכי numpy -> מתירים רק את בוני numpy
        from numpy._core.multiarray import _reconstruct
        allowed = [(_reconstruct, "numpy.core.multiarray._reconstruct"), np.ndarray, np.dtype,
                   (type(np.dtype(np.float32)), "numpy.dtype[float32]")]
        allowed += [type(np.dtype(t)) for t in (np.float32, np.float64, np.int64, np.int32)]
        with torch.serialization.safe_globals(allowed):
            sd = torch.load(path, map_location="cpu", weights_only=True)
        sd = sd.get("model", sd)
        self.wr = sd["spectrogram_extractor.stft.conv_real.weight"].float()
        self.wi = sd["spectrogram_extractor.stft.conv_imag.weight"].float()
        self.melW = sd["logmel_extractor.melW"].float()
        self.net = _panns_net(name)
        body = {k: v for k, v in sd.items() if not k.startswith(("spectrogram_extractor", "logmel_extractor"))}
        missing, unexpected = self.net.load_state_dict(body, strict=False)
        bad = [k for k in missing if "num_batches_tracked" not in k] + list(unexpected)
        if bad:
            raise RuntimeError(f"PANNs: מפתחות לא תואמים: {bad[:8]}")
        self.net.eval()
        self.size_mb = os.path.getsize(path) / 1e6
        self.params = sum(p.numel() for p in self.net.parameters())

    def _logmel(self, x):
        import torch
        import torch.nn.functional as F
        t = torch.from_numpy(np.asarray(x, dtype=np.float32))[None, None, :]
        t = F.pad(t, (self.n_fft // 2, self.n_fft // 2), mode="reflect")
        re, im = F.conv1d(t, self.wr, stride=self.hop), F.conv1d(t, self.wi, stride=self.hop)
        spec = (re ** 2 + im ** 2).transpose(1, 2)                       # (1, T, F)
        mel = torch.matmul(spec, self.melW)
        return (10.0 * torch.log10(torch.clamp(mel, min=1e-10)))[:, None]  # (1, 1, T, mel)

    def frames(self, x):
        import torch
        with torch.inference_mode():
            fmap = self.net.feature_map(self._logmel(x))                  # (1, C, T')
        fmap = fmap[0].T.numpy()                                           # (T', C)
        step = 32 * self.hop / self.sr
        return fmap, (np.arange(len(fmap)) + 0.5) * step

    def embed(self, x, dur):
        import torch
        fm, c = self.frames(x)
        nseg = max(1, int(math.ceil(dur / SEG_S - 1e-6)))
        idx = np.minimum(np.floor(c / SEG_S).astype(int), nseg - 1)
        segs = np.stack([fm[idx == s].max(axis=0) + fm[idx == s].mean(axis=0) for s in np.unique(idx)])
        with torch.inference_mode():
            e = torch.relu(self.net.fc1(torch.from_numpy(segs))).numpy()  # = ה-embedding של PANNs, לכל סגמנט
        return np.concatenate([e.mean(axis=0), e.max(axis=0)]), len(e)


def _panns_net(name):
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class ConvBlock(nn.Module):
        def __init__(self, i, o):
            super().__init__()
            self.conv1 = nn.Conv2d(i, o, 3, 1, 1, bias=False)
            self.conv2 = nn.Conv2d(o, o, 3, 1, 1, bias=False)
            self.bn1, self.bn2 = nn.BatchNorm2d(o), nn.BatchNorm2d(o)

        def forward(self, x, pool):
            x = F.relu_(self.bn1(self.conv1(x)))
            x = F.relu_(self.bn2(self.conv2(x)))
            return F.avg_pool2d(x, kernel_size=pool)

    class Cnn14(nn.Module):
        def __init__(self):
            super().__init__()
            self.bn0 = nn.BatchNorm2d(64)
            ch = [1, 64, 128, 256, 512, 1024, 2048]
            for i in range(6):
                setattr(self, f"conv_block{i + 1}", ConvBlock(ch[i], ch[i + 1]))
            self.fc1 = nn.Linear(2048, 2048)
            self.fc_audioset = nn.Linear(2048, 527)

        def feature_map(self, x):
            x = self.bn0(x.transpose(1, 3)).transpose(1, 3)
            for i in range(6):
                x = getattr(self, f"conv_block{i + 1}")(x, (2, 2) if i < 5 else (1, 1))
            return torch.mean(x, dim=3).float()

    class InvertedResidual(nn.Module):
        def __init__(self, inp, oup, stride, t):
            super().__init__()
            hid = round(inp * t)
            self.use_res = stride == 1 and inp == oup
            if t == 1:
                L = [nn.Conv2d(hid, hid, 3, 1, 1, groups=hid, bias=False), nn.AvgPool2d(stride), nn.BatchNorm2d(hid),
                     nn.ReLU6(inplace=True), nn.Conv2d(hid, oup, 1, 1, 0, bias=False), nn.BatchNorm2d(oup)]
            else:
                L = [nn.Conv2d(inp, hid, 1, 1, 0, bias=False), nn.BatchNorm2d(hid), nn.ReLU6(inplace=True),
                     nn.Conv2d(hid, hid, 3, 1, 1, groups=hid, bias=False), nn.AvgPool2d(stride), nn.BatchNorm2d(hid),
                     nn.ReLU6(inplace=True), nn.Conv2d(hid, oup, 1, 1, 0, bias=False), nn.BatchNorm2d(oup)]
            self.conv = nn.Sequential(*L)

        def forward(self, x):
            return x + self.conv(x) if self.use_res else self.conv(x)

    class MobileNetV2(nn.Module):
        def __init__(self):
            super().__init__()
            self.bn0 = nn.BatchNorm2d(64)
            feats = [nn.Sequential(nn.Conv2d(1, 32, 3, 1, 1, bias=False), nn.AvgPool2d(2), nn.BatchNorm2d(32),
                                   nn.ReLU6(inplace=True))]
            inp = 32
            for t, c, n, s in [[1, 16, 1, 1], [6, 24, 2, 2], [6, 32, 3, 2], [6, 64, 4, 2], [6, 96, 3, 2],
                               [6, 160, 3, 1], [6, 320, 1, 1]]:
                for i in range(n):
                    feats.append(InvertedResidual(inp, c, s if i == 0 else 1, t))
                    inp = c
            feats.append(nn.Sequential(nn.Conv2d(inp, 1280, 1, 1, 0, bias=False), nn.BatchNorm2d(1280),
                                       nn.ReLU6(inplace=True)))
            self.features = nn.Sequential(*feats)
            self.fc1 = nn.Linear(1280, 1024)
            self.fc_audioset = nn.Linear(1024, 527)

        def feature_map(self, x):
            x = self.bn0(x.transpose(1, 3)).transpose(1, 3)
            return torch.mean(self.features(x), dim=3).float()

    return Cnn14() if name == "panns_cnn14_16k" else MobileNetV2()


def _hf_env():
    os.environ.setdefault("HF_HOME", os.path.join(MODELS, "hf"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")


def _dir_size_mb(repo_id):
    from huggingface_hub import scan_cache_dir
    for r in scan_cache_dir().repos:
        if r.repo_id == repo_id:
            # רק קובץ המשקלים שנטען בפועל (יש מאגרים עם גם .bin וגם .safetensors)
            sizes = [f.size_on_disk for rev in r.revisions for f in rev.files
                     if f.file_name.endswith((".safetensors", ".bin"))]
            return min(sizes) / 1e6 if sizes else r.size_on_disk / 1e6
    return None


class AstEnc:
    """AST (fbank 128 של Kaldi, נרמול AudioSet). במקום לרפד כל קלט ל-1024 מסגרות (10.24 ש') - הקלט באורכו האמיתי,
    ו-positional embedding של ציר הזמן נחתך לאמצע (101 -> t), כמו בקוד המקורי של AST (ast_models.py) לקלט קצר.
    כך גם חלון 1.5 ש' עולה כמו 1.5 ש' (רלוונטי לטלפון). טוקני ה-patch (12 תדר x t זמן) ממוצעים על התדר -> רצף בצעד 0.1 ש'."""
    name, sr, repo = "ast", 16000, "MIT/ast-finetuned-audioset-10-10-0.4593"

    def __init__(self):
        _hf_env()
        from transformers import ASTFeatureExtractor, ASTModel
        self.fe = ASTFeatureExtractor.from_pretrained(self.repo)
        self.model = ASTModel.from_pretrained(self.repo).eval()
        self.params = sum(p.numel() for p in self.model.parameters())
        self.size_mb = _dir_size_mb(self.repo)
        cfg = self.model.config
        self.f_dim = (cfg.num_mel_bins - cfg.patch_size) // cfg.frequency_stride + 1
        self.t_full = (cfg.max_length - cfg.patch_size) // cfg.time_stride + 1
        pos = self.model.embeddings.position_embeddings.detach()          # (1, 2 + f*t, D)
        self.pos_cls = pos[:, :2]
        self.pos_patch = pos[:, 2:].reshape(1, self.f_dim, self.t_full, -1)

    def _forward(self, x):
        import torch
        n_frames = max(16, 1 + max(0, (len(x) - 400) // 160))
        n_frames = min(16 + int(math.ceil((n_frames - 16) / 10)) * 10,       # מספר שלם של צעדי patch
                       self.model.config.max_length)
        self.fe.max_length = n_frames
        inp = self.fe(np.asarray(x, dtype=np.float32), sampling_rate=self.sr, return_tensors="pt")["input_values"]
        emb = self.model.embeddings
        with torch.inference_mode():
            p = emb.patch_embeddings(inp)                                   # (1, f*t, D)
            t = p.shape[1] // self.f_dim
            s = max(0, self.t_full // 2 - t // 2)
            pp = self.pos_patch[:, :, s:s + t].reshape(1, self.f_dim * t, -1)
            h = torch.cat([emb.cls_token, emb.distillation_token, p], dim=1) + torch.cat([self.pos_cls, pp], dim=1)
            for layer in self.model.layers:                                 # = ASTModel.forward בלי מסכה (אין ריפוד)
                h = layer(h, None)
                h = h[0] if isinstance(h, tuple) else h
            h = self.model.layernorm(h)[:, 2:]
        h = h.reshape(self.f_dim, t, -1).mean(dim=0).numpy()                 # (t, D)
        return h, (np.arange(t) * 10 + 8) * 0.01

    def embed(self, x, dur):
        f, c = self._forward(x)
        return seg_pool(f, c, dur)


class ClapEnc:
    """CLAP (HTSAT, unfused) כפי שמופץ: 48kHz, 10 ש' (קטע קצר -> repeatpad). ל-probe: מפת התכונות האחרונה של
    HTSAT (לפני pooling) כרצף זמן -> סגמנטים; רק החלק שמכסה את ההופעה הראשונה של האודיו (לפני החזרה).
    ל-zero-shot: ה-embedding המוקרן (512) הרגיל של המודל לכל הקטע."""
    name, sr, repo = "clap", 48000, "laion/clap-htsat-unfused"

    def __init__(self):
        _hf_env()
        from transformers import ClapModel, ClapProcessor
        self.proc = ClapProcessor.from_pretrained(self.repo)
        self.model = ClapModel.from_pretrained(self.repo).eval()
        self.params = sum(p.numel() for p in self.model.audio_model.parameters()) + \
            sum(p.numel() for p in self.model.audio_projection.parameters())
        self.size_mb = _dir_size_mb(self.repo)

    def _audio_inputs(self, xs):
        return self.proc.feature_extractor([np.asarray(x, dtype=np.float32) for x in xs], sampling_rate=self.sr,
                                           return_tensors="pt", padding="repeatpad", truncation="rand_trunc")

    def batch_frames(self, xs):
        import torch
        inp = self._audio_inputs(xs)
        with torch.inference_mode():
            o = self.model.audio_model(input_features=inp["input_features"], is_longer=inp.get("is_longer"),
                                       output_hidden_states=False)
            proj = self.model.audio_projection(o.pooler_output)
            proj = torch.nn.functional.normalize(proj, dim=-1).numpy()
            h = o.last_hidden_state                                      # (B, C, F, T)
        h = h.mean(dim=2).transpose(1, 2).numpy()                        # (B, T, C)
        out = []
        for x, hh, p in zip(xs, h, proj):
            T = hh.shape[0]
            step = 10.0 / T
            c = (np.arange(T) + 0.5) * step
            dur = len(x) / self.sr
            valid = c < max(dur, step)
            out.append((hh[valid], c[valid], p))
        return out

    def embed_batch(self, xs, durs):
        res = []
        for (f, c, p), d in zip(self.batch_frames(xs), durs):
            v, n = seg_pool(f, c, d)
            res.append((v, n, p))
        return res

    def text_embeddings(self, prompts):
        import torch
        tok = self.proc.tokenizer(prompts, padding=True, return_tensors="pt")
        with torch.inference_mode():
            t = self.model.get_text_features(**tok)
        if not torch.is_tensor(t):
            t = t.pooler_output if hasattr(t, "pooler_output") else t[0]
        return torch.nn.functional.normalize(t, dim=-1).numpy(), float(self.model.logit_scale_a.exp())


def make_encoder(name):
    if name == "yamnet":
        return YamnetEnc()
    if name.startswith("panns"):
        return PannsEnc(name)
    if name == "ast":
        return AstEnc()
    if name == "clap":
        return ClapEnc()
    raise ValueError(name)


def embed(names):
    for name in names:
        if name == "yamnet":
            enc = YamnetEnc(threads=EMB_THREADS)
        else:
            _torch_threads(EMB_THREADS)
            enc = make_encoder(name)
        d = os.path.join(EMB_DIR, name)
        os.makedirs(d, exist_ok=True)
        t0, n_done = time.time(), 0
        part, nparts = (int(v) for v in os.environ.get("LG_PART", "0/1").split("/"))   # כמה תהליכים על אותו מקודד
        skip = lambda k: k % nparts != part or os.path.exists(os.path.join(d, f"shard_{k:03d}.npz"))
        for k, keys, xs, durs in iter_audio(skip):
            out = os.path.join(d, f"shard_{k:03d}.npz")
            xs = [resample(x, CACHE_SR, enc.sr) for x in xs]
            vecs, nsegs, projs = [], [], []
            if hasattr(enc, "embed_batch"):
                B = 8
                order = np.argsort([len(x) for x in xs])          # אורכים דומים יחד (לא משנה תוצאה - מרופד ל-10 ש')
                res = [None] * len(xs)
                for i in range(0, len(xs), B):
                    ii = order[i:i + B]
                    for j, r in zip(ii, enc.embed_batch([xs[j] for j in ii], [float(durs[j]) for j in ii])):
                        res[j] = r
                for r in res:
                    vecs.append(r[0]); nsegs.append(r[1])
                    if len(r) > 2:
                        projs.append(r[2])
            else:
                for x, du in zip(xs, durs):
                    v, n = enc.embed(x, float(du))
                    vecs.append(v); nsegs.append(n)
            extra = {"proj": np.stack(projs).astype(np.float32)} if projs else {}
            np.savez(out + ".tmp.npz", keys=np.array(keys), emb=np.stack(vecs).astype(np.float32),
                     nseg=np.array(nsegs, dtype=np.int16), **extra)
            os.replace(out + ".tmp.npz", out)
            n_done += len(keys)
            log(f"{name}: shard {k} ({n_done} פריטים, {time.time() - t0:.0f}s, "
                f"{(time.time() - t0) / max(1, n_done) * 1000:.0f} ms/פריט)")
        meta = {"params": enc.params, "size_mb": enc.size_mb, "sr": enc.sr, "threads": EMB_THREADS}
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        del enc


def load_emb(name):
    d = os.path.join(EMB_DIR, name)
    keys, embs, projs = [], [], []
    for f in sorted(os.listdir(d)):
        if f.startswith("shard_") and f.endswith(".npz") and ".tmp" not in f:
            z = np.load(os.path.join(d, f), allow_pickle=False)
            keys += [str(k) for k in z["keys"]]
            embs.append(z["emb"])
            if "proj" in z:
                projs.append(z["proj"])
    if not embs:
        return [], None, None
    return keys, np.concatenate(embs), (np.concatenate(projs) if projs else None)


# ===================================================================== latency
def latency(n_runs=30):
    rng = np.random.default_rng(SEED)
    res = {}
    for name in ENCODERS:
        res[name] = {}
        for threads in (1, None):
            if name == "yamnet":
                enc = YamnetEnc(threads=threads)          # None = ברירת המחדל של LiteRT (כמו באפליקציה)
                actual = threads or "litert_default"
            else:
                actual = _torch_threads(threads)
                enc = make_encoder(name)
            x = (rng.standard_normal(int(WIN_S * enc.sr)) * 0.05).astype(np.float32)
            run = (lambda: enc.embed(x, WIN_S)) if hasattr(enc, "embed") else (lambda: enc.embed_batch([x], [WIN_S]))
            for _ in range(3):
                run()
            ts = []
            for _ in range(n_runs):
                t = time.perf_counter(); run(); ts.append((time.perf_counter() - t) * 1000)
            key = "1_thread" if threads == 1 else "default_threads"
            res[name][key] = {"median_ms": float(np.median(ts)), "p95_ms": float(np.percentile(ts, 95)), "n": n_runs,
                              "threads": actual}
            res[name]["params"] = enc.params
            res[name]["size_mb"] = enc.size_mb
            log(f"latency {name} {key}: median {np.median(ts):.1f} ms, p95 {np.percentile(ts, 95):.1f} ms")
            del enc
    if res["yamnet"]["params"] is None:
        res["yamnet"]["params"] = 3_700_000      # YAMNet (MobileNetV1) - לפי התיעוד; ה-TFLite לא חושף ספירה
        res["yamnet"]["params_note"] = "לפי התיעוד של YAMNet (~3.7M)"
    _torch_threads(None)
    os.makedirs(WORK, exist_ok=True)
    load = _cpu_load()
    with open(LAT_JSON, "w", encoding="utf-8") as fh:
        json.dump({"window_s": WIN_S, "cpu_count": os.cpu_count(), "results": res, "cpu_load_pct_at_end": load,
                   "note": "CPU שולחני (Windows, PyTorch / LiteRT), כולל חילוץ תכונות מה-waveform; לא טלפון"}, fh,
                  ensure_ascii=False, indent=1)


def _cpu_load():
    """עומס CPU כולל במכונה (Windows) - כדי לתעד אם המדידה נעשתה תחת עומס של תהליכים אחרים."""
    import subprocess
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out)
    except Exception:                                           # noqa: BLE001
        return None


# ===================================================================== מדדים
def auc(pos, neg):
    from scipy.stats import rankdata
    if len(pos) == 0 or len(neg) == 0:
        return None
    r = rankdata(np.concatenate([pos, neg]))
    return float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": int(k), "n": int(n), "rate": p, "lo": max(0.0, c - h), "hi": min(1.0, c + h)}


def threshold_at_budget(neg_scores, budget=FP_BUDGET):
    s = np.sort(np.asarray(neg_scores))[::-1]
    k = int(math.floor(budget * len(s)))
    return float(s[k]) if k < len(s) else float(s[-1])    # חיובי אם score > thr -> לכל היותר k שליליים עוברים


def fit_probe(Xtr, ytr, Xva, yva):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(Xtr)
    Ztr, Zva = sc.transform(Xtr), sc.transform(Xva)
    best = None
    for C in (0.001, 0.01, 0.1):
        m = LogisticRegression(C=C, class_weight="balanced", max_iter=3000, random_state=SEED)
        m.fit(Ztr, ytr)
        a = auc(m.decision_function(Zva)[yva == 1], m.decision_function(Zva)[yva == 0])
        if best is None or a > best[0]:
            best = (a, C, m)
    return sc, best[2], best[1], best[0]


CLAP_PROMPTS = {
    "gunshot": ["a gunshot", "gunfire", "a pistol being fired", "a rifle shot"],
    "explosion": ["an explosion", "a loud blast", "a bomb exploding"],
    "fireworks": ["fireworks exploding", "firecrackers popping"],
    "glass_break": ["glass breaking", "a window shattering"],
    "siren": ["an emergency vehicle siren", "a police siren", "an ambulance siren wailing"],
    "alarm": ["an alarm beeping", "a smoke alarm", "a car alarm", "an alarm clock ringing"],
    "scream": ["a person screaming", "a woman screaming in fear"],
    "distress_voice": ["a person screaming", "someone shouting for help", "a person crying", "people yelling"],
}
CLAP_NEG_PROMPTS = ["a car horn honking", "music playing", "drums", "people talking", "a crowd cheering",
                    "people laughing", "construction noise", "traffic noise", "street ambience", "knocking on a door",
                    "thunder", "wind noise", "silence", "birds chirping", "a dog barking", "footsteps", "rain",
                    "an engine running", "a door slamming", "children playing"]


def clap_zeroshot_scores(proj):
    enc = ClapEnc()
    prompts = sorted({p for ps in CLAP_PROMPTS.values() for p in ps} | set(CLAP_NEG_PROMPTS))
    T, scale = enc.text_embeddings([f"This is a sound of {p}." for p in prompts])
    logits = scale * proj @ T.T
    logits -= logits.max(axis=1, keepdims=True)
    P = np.exp(logits)
    P /= P.sum(axis=1, keepdims=True)
    out = {}
    for c, ps in CLAP_PROMPTS.items():
        s = P[:, [prompts.index(p) for p in ps]].sum(axis=1)
        out[c] = np.log(np.clip(s, 1e-12, None)) - np.log(np.clip(1 - s, 1e-12, None))     # logit לדירוג
    return out, {"prompts": CLAP_PROMPTS, "negative_prompts": CLAP_NEG_PROMPTS,
                 "template": "This is a sound of {}.", "score": "סכום הסתברויות softmax של הפרומפטים של הקטגוריה"}


def evaluate():
    items = load_subset()
    key_i = {it["key"]: i for i, it in enumerate(items)}
    split = np.array([it["split"] for it in items])
    role = np.array([it["role"] for it in items])
    group = np.array([it["group"] for it in items])
    tags = [set(it["tags"]) for it in items]
    rng = np.random.default_rng(SEED)
    scores = {}       # (enc, cat) -> test scores (aligned to test idx)
    probe_info = {}
    avail = [e for e in ENCODERS if os.path.isdir(os.path.join(EMB_DIR, e))]
    clap_proj = None
    incomplete = {}
    for enc in ENCODERS:
        if enc not in avail:
            incomplete[enc] = 0
    for enc in avail:
        keys, E, P = load_emb(enc)
        if len(keys) != len(items):
            log(f"{enc}: חסרים embeddings ({len(keys)}/{len(items)}) - מדלג")
            incomplete[enc] = len(keys)
            continue
        X = E[np.array([key_i[k] for k in keys]).argsort()] if keys != [it["key"] for it in items] else E
        if P is not None:
            clap_proj = P if keys == [it["key"] for it in items] else P[np.array([key_i[k] for k in keys]).argsort()]
        for c in CATS:
            y = np.array([np.nan if it["labels"][c] is None else it["labels"][c] for it in items], dtype=float)
            tr, va, te = (split == "train") & ~np.isnan(y), (split == "val") & ~np.isnan(y), (split == "test") & ~np.isnan(y)
            sc, m, C, va_auc = fit_probe(X[tr], y[tr].astype(int), X[va], y[va].astype(int))
            s_va = m.decision_function(sc.transform(X[va]))
            s_all = np.full(len(items), np.nan)
            s_all[te] = m.decision_function(sc.transform(X[te]))
            thr = threshold_at_budget(s_va[y[va] == 0])
            scores[(enc, c)] = (s_all, thr)
            probe_info[(enc, c)] = {"C": C, "val_auc": va_auc, "n_train": int(tr.sum()), "n_train_pos": int(y[tr].sum()),
                                    "n_val": int(va.sum()), "n_val_pos": int(y[va].sum()), "threshold": thr,
                                    "val_fpr": float((s_va[y[va] == 0] > thr).mean()),
                                    "val_recall": float((s_va[y[va] == 1] > thr).mean())}
        log(f"{enc}: probes fitted")
    if clap_proj is not None:
        zs, zs_meta = clap_zeroshot_scores(clap_proj)
        for c in CATS:
            y = np.array([np.nan if it["labels"][c] is None else it["labels"][c] for it in items], dtype=float)
            va, te = (split == "val") & ~np.isnan(y), (split == "test") & ~np.isnan(y)
            s = zs[c]
            thr = threshold_at_budget(s[va & (y == 0)])
            s_all = np.full(len(items), np.nan)
            s_all[te] = s[te]
            scores[("clap_zeroshot", c)] = (s_all, thr)
            probe_info[("clap_zeroshot", c)] = {"threshold": thr, "val_auc": auc(s[va & (y == 1)], s[va & (y == 0)]),
                                                "val_recall": float((s[va & (y == 1)] > thr).mean()),
                                                "val_fpr": float((s[va & (y == 0)] > thr).mean())}
    else:
        zs_meta = None
    encs = [e for e in list(ENCODERS) + ["clap_zeroshot"] if (e, CATS[0]) in scores]

    # bootstrap indices משותפים לכל המקודדים (השוואה מזווגת)
    results = {}
    for c in CATS:
        y = np.array([np.nan if it["labels"][c] is None else it["labels"][c] for it in items], dtype=float)
        te = (split == "test") & ~np.isnan(y)
        pos_i = np.where(te & (y == 1))[0]
        neg_i = np.where(te & (y == 0))[0]
        clip_neg = neg_i[role[neg_i] != "background"]
        hard_i = np.array([i for i in clip_neg if tags[i] & set(HARD)], dtype=int)
        bg_i = neg_i[role[neg_i] == "background"]
        boot_p = rng.integers(0, len(pos_i), size=(N_BOOT, len(pos_i)))
        boot_n = rng.integers(0, len(clip_neg), size=(N_BOOT, len(clip_neg)))
        R = {"n_test_pos": int(len(pos_i)), "n_test_neg_clips": int(len(clip_neg)), "n_test_hard": int(len(hard_i)),
             "n_test_background_windows": int(len(bg_i)), "encoders": {}}
        boot_auc = {}
        for enc in encs:
            s, thr = scores[(enc, c)]
            sp, sn = s[pos_i], s[clip_neg]
            a = auc(sp, sn)
            ba = np.array([auc(sp[bp], sn[bn]) for bp, bn in zip(boot_p[:300], boot_n[:300])])  # 300 מספיק ל-CI
            br = np.array([(sp[bp] > thr).mean() for bp in boot_p])
            boot_auc[enc] = (ba, br)
            hit = s > thr
            r = {"auc": a, "auc_ci": [float(np.percentile(ba, 2.5)), float(np.percentile(ba, 97.5))],
                 "recall": wilson(hit[pos_i].sum(), len(pos_i)),
                 "fpr_all_clip_neg": wilson(hit[clip_neg].sum(), len(clip_neg)),
                 "fpr_hard": wilson(hit[hard_i].sum(), len(hard_i)),
                 "fpr_background": wilson(hit[bg_i].sum(), len(bg_i)),
                 "recall_by_group": {}, "fpr_hard_by_tag": {}, "fpr_background_by_group": {},
                 "fpr_clip_neg_by_group": {}, "probe": probe_info[(enc, c)]}
            for g in sorted(set(group[pos_i])):
                ii = pos_i[group[pos_i] == g]
                r["recall_by_group"][g] = wilson(hit[ii].sum(), len(ii))
            for t in HARD:
                ii = np.array([i for i in hard_i if t in tags[i]], dtype=int)
                if len(ii):
                    r["fpr_hard_by_tag"][t] = wilson(hit[ii].sum(), len(ii))
            for g in sorted(set(group[bg_i])):
                ii = bg_i[group[bg_i] == g]
                r["fpr_background_by_group"][g] = wilson(hit[ii].sum(), len(ii))
            for g in sorted(set(group[clip_neg])):
                ii = clip_neg[group[clip_neg] == g]
                r["fpr_clip_neg_by_group"][g] = wilson(hit[ii].sum(), len(ii))
            R["encoders"][enc] = r
        if "yamnet" in boot_auc:
            ya, yr = boot_auc["yamnet"]
            for enc in encs:
                if enc == "yamnet":
                    continue
                ea, er = boot_auc[enc]
                da, dr = ea - ya, er - yr
                R["encoders"][enc]["vs_yamnet"] = {
                    "d_auc": R["encoders"][enc]["auc"] - R["encoders"]["yamnet"]["auc"],
                    "d_auc_ci": [float(np.percentile(da, 2.5)), float(np.percentile(da, 97.5))],
                    "d_recall": R["encoders"][enc]["recall"]["rate"] - R["encoders"]["yamnet"]["recall"]["rate"],
                    "d_recall_ci": [float(np.percentile(dr, 2.5)), float(np.percentile(dr, 97.5))]}
        results[c] = R
        log(f"eval {c}: " + ", ".join(f"{e}={R['encoders'][e]['auc']:.3f}" for e in encs))
    lat = json.load(open(LAT_JSON, encoding="utf-8")) if os.path.exists(LAT_JSON) else None
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "seed": SEED, "fp_budget_val": FP_BUDGET,
           "subset": subset_stats(items), "n_items_core": len(items), "n_items_full": len(load_subset(core=False)),
           "subset_params": {
               "cap_pos_train": CAP_POS_TRAIN, "n_hard_per_tag": N_HARD, "n_other": N_OTHER, "n_bg_sonyc": N_BG,
               "n_tau_windows": N_TAU, "sonyc_test_windows_per_clip": SONYC_TEST_WINDOWS, "n_esc_other": N_ESC_OTHER,
               "max_clip_s": MAX_S, "min_clip_s": MIN_S, "window_s": WIN_S, "segment_s": SEG_S, "cache_sr": CACHE_SR,
               "core_pos": CORE_POS, "core_test_pos_per_group": CORE_TEST_POS_PER_GROUP, "core_hard": CORE_HARD,
               "core_other": CORE_OTHER, "core_bg_per_group": CORE_BG, "clip_s": CLIP_S,
               "embedding_threads": EMB_THREADS},
           "encoders": encs, "incomplete_encoders": incomplete, "licences": LICENCES, "latency": lat,
           "clap_zeroshot": zs_meta, "results": results}
    for enc in avail:
        mp = os.path.join(EMB_DIR, enc, "meta.json")
        if os.path.exists(mp):
            out.setdefault("encoder_meta", {})[enc] = json.load(open(mp, encoding="utf-8"))
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    report(out)
    log(f"-> {OUT_JSON}\n-> {OUT_MD}")
    return out


# ===================================================================== דו"ח
def _pct(w, d=1):
    return "-" if w is None else f"{100 * w['rate']:.{d}f}%"


def _pci(w, d=1):
    return "-" if w is None else f"{100 * w['rate']:.{d}f}% [{100 * w['lo']:.{d}f}-{100 * w['hi']:.{d}f}] ({w['k']}/{w['n']})"


def verdict(out):
    """כלל החלטה שנקבע מראש: מקודד 'עדיף' בקטגוריה אם ה-CI של ΔAUC מול YAMNet כולו מעל 0 וגם ה-CI של Δrecall כולו
    מעל 0 וגם Δrecall >= 5 נק' אחוז, ושיעור השווא ברקע לא גבוה מ-YAMNet ב-יותר מנק' אחוז אחת."""
    wins = {}
    for c, R in out["results"].items():
        y = R["encoders"].get("yamnet")
        for e, r in R["encoders"].items():
            v = r.get("vs_yamnet")
            if not v or y is None:
                continue
            bg_ok = (r["fpr_background"] is None or y["fpr_background"] is None or
                     r["fpr_background"]["rate"] <= y["fpr_background"]["rate"] + 0.01)
            if v["d_auc_ci"][0] > 0 and v["d_recall_ci"][0] > 0 and v["d_recall"] >= 0.05 and bg_ok:
                wins.setdefault(e, []).append(c)
    return wins


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


PHONE_MAX_PARAMS = 10e6       # "מתאים לטלפון כמקודד רציף" = סדר גודל של YAMNet (3.7M); נקבע מראש


def bottom_line(out):
    """שורה תחתונה - נגזרת מהמספרים (לא כתובה ביד), כדי שהדו"ח יתעדכן עם כל הרצה."""
    res, encs = out["results"], out["encoders"]
    lat = (out.get("latency") or {}).get("results", {})
    wins = verdict(out)
    y_auc = _mean([res[c]["encoders"]["yamnet"]["auc"] for c in CATS])
    y_rec = _mean([res[c]["encoders"]["yamnet"]["recall"]["rate"] for c in CATS])
    y_bg = _mean([(res[c]["encoders"]["yamnet"]["fpr_background"] or {}).get("rate") for c in CATS])
    y_ms = lat.get("yamnet", {}).get("1_thread", {}).get("median_ms")
    L = [f"- **YAMNet (בסיס, אותו probe):** AUC ממוצע על 8 הקטגוריות {y_auc:.3f}, recall ממוצע בסף של val "
         f"{100 * y_rec:.1f}%, שווא ממוצע על חלונות רקע {100 * y_bg:.2f}%."]
    for e in encs:
        if e == "yamnet":
            continue
        a = _mean([res[c]["encoders"][e]["auc"] for c in CATS])
        r = _mean([res[c]["encoders"][e]["recall"]["rate"] for c in CATS])
        bg = _mean([(res[c]["encoders"][e]["fpr_background"] or {}).get("rate") for c in CATS])
        sig_up = [c for c in CATS if res[c]["encoders"][e].get("vs_yamnet", {}).get("d_auc_ci", [0])[0] > 0]
        sig_dn = [c for c in CATS if res[c]["encoders"][e].get("vs_yamnet", {}).get("d_auc_ci", [0, 0])[1] < 0]
        cost = ""
        base = "clap" if e == "clap_zeroshot" else e
        if base in lat and y_ms:
            cost = (f" עלות: x{lat[base]['1_thread']['median_ms'] / y_ms:.1f} זמן לחלון (חוט 1) ו-"
                    f"{lat[base]['params'] / 1e6:.0f}M פרמטרים מול {lat['yamnet']['params'] / 1e6:.1f}M.")
        L.append(f"- **{ENC_LABEL[e]}:** AUC ממוצע {a:.3f} ({a - y_auc:+.3f}), recall ממוצע {100 * r:.1f}% "
                 f"({100 * (r - y_rec):+.1f} נק'), שווא ברקע {100 * bg:.2f}%. ΔAUC מובהק לטובתו: "
                 f"{', '.join(HE[c] for c in sig_up) or 'אין'}; לרעתו: {', '.join(HE[c] for c in sig_dn) or 'אין'}. "
                 f"עבר את כלל ההחלטה ב: {', '.join(HE[c] for c in wins.get(e, [])) or 'אף קטגוריה'}.{cost}")
    # המלצה
    phone_ok, heavy = [], []
    for e in wins:
        base = "clap" if e == "clap_zeroshot" else e
        # גודל, לא זמן: YAMNet נמדד ב-TFLite ושאר המקודדים ב-PyTorch - הזמן לא בר-השוואה ישירה בין סביבות ריצה
        p = lat.get(base, {}).get("params") or out.get("encoder_meta", {}).get(base, {}).get("params")
        if p is not None and p <= PHONE_MAX_PARAMS:
            phone_ok.append(e)
        else:
            heavy.append(e)
    L.append("")
    if not wins:
        L.append("**המלצה:** אין מקודד שמצדיק החלפה של YAMNet - אף אחד לא עבר את כלל ההחלטה באף קטגוריה, וכולם יקרים "
                 "יותר בטלפון. ההשקעה הבאה צריכה להיות בנתונים ובראשי הסיווג מעל YAMNet, לא במקודד.")
    else:
        for e in phone_ok:
            p = lat.get(e, {}).get("params") or out.get("encoder_meta", {}).get(e, {}).get("params")
            if len(wins[e]) >= 3:
                L.append(f"**המלצה:** {ENC_LABEL[e]} ({p / 1e6:.1f}M פרמטרים - בסדר הגודל של YAMNet) עבר את הכלל ב-"
                         f"{len(wins[e])}/{len(CATS)} קטגוריות ({', '.join(HE[c] for c in wins[e])}) - מועמד רציני "
                         "להחלפת המקודד. הצעד הבא: המרה ל-TFLite, מדידה בטלפון אמיתי, ואימון מחדש של כל הראשים מעליו.")
            else:
                L.append(f"**המלצה:** {ENC_LABEL[e]} ({p / 1e6:.1f}M פרמטרים - בסדר הגודל של YAMNet) עבר את הכלל רק ב-"
                         f"{', '.join(HE[c] for c in wins[e])} ({len(wins[e])}/{len(CATS)}). זה לא מצדיק החלפת "
                         "המקודד של כל המנוע (כולל אימון מחדש של כל הראשים וראש המצוקה) ולא הרצה של שני מקודדים "
                         "במקביל. שווה לשמור כאופציה: אם בעתיד ייאספו נתוני טלפון אמיתיים - לחזור על ההשוואה.")
        if heavy:
            L.append(("**המלצה:** " if not phone_ok else "") +
                     "; ".join(f"{ENC_LABEL[e]} טוב יותר ב-{', '.join(HE[c] for c in wins[e])}, אבל הוא כבד מדי "
                               "כמקודד רציף בטלפון" for e in heavy) +
                     ". השימוש הסביר בו - שלב אימות שני (בשרת או על המכשיר רק אחרי טריגר של YAMNet), או מורה "
                     "ל-distillation לראשי YAMNet; לא החלפה של המקודד באפליקציה.")
        rest = [c for c in CATS if not any(c in cs for cs in wins.values())]
        if rest:
            L.append(f"בשאר הקטגוריות ({', '.join(HE[c] for c in rest)}) אין יתרון מובהק לאף מקודד - YAMNet נשאר.")
    return L


def limitations(out):
    sp = out["subset_params"]
    lat = out.get("latency") or {}
    inc = [f"{ENC_LABEL[e]} ({n}/{out['n_items_core']} פריטים חושבו)" for e, n in
           (out.get("incomplete_encoders") or {}).items()]
    extra = [f"- **לא הושלם בזמן ולכן לא בטבלאות האיכות:** {', '.join(inc)}. ה-cache ניתן להמשך "
             "(`embed <name>` ממשיך מאותה נקודה) ואז `eval` מחדש. זמן הריצה / הגודל / הרישוי שלו כן נמדדו."] if inc else []
    return extra + [
        f"- **תת-מדגם מצומצם ({out['n_items_core']} פריטים):** בגלל עומס CPU של עבודה מקבילה. מספר החיוביים ב-test "
        "לחלק מהקטגוריות קטן (פיצוץ, סירנה) - ה-CI רחבים; ההשוואה מזווגת ולכן הוגנת, אבל הבדלים קטנים לא יזוהו.",
        f"- **אורך קטע {sp['clip_s']:.0f} ש':** חלון האנרגיה הגבוהה; זהה לכל המקודדים. רקע - חלונות 1.5 ש'.",
        "- **CLAP ראה כנראה חלק מנתוני המבחן:** LAION-Audio-630K כולל את Freesound - ממנו מגיעים FSD50K eval ו-ESC-50 "
        "(וחלק מ-Nonspeech7k). לא אומת אם הוסרה חפיפה. לכן תוצאות CLAP על מקורות אלה עלולות להיות אופטימיות; "
        "ההשוואה הנקייה עבורו היא על gunshot_fit, SONYC ו-TAU (טבלאות לפי מקור).",
        "- **כל המקודדים (כולל YAMNet) אומנו על AudioSet** (YouTube); אין חפיפה צפויה עם Freesound / SONYC / TAU / ירי.",
        "- **זמן ריצה:** נמדד על CPU שולחני תחת עומס של תהליכים אחרים "
        f"(עומס בסוף המדידה: {lat.get('cpu_load_pct_at_end')}%), ב-PyTorch fp32 ולא ב-TFLite / קוונטיזציה - "
        "לא מספר של טלפון. היחסים בין המקודדים הם המידע השימושי.",
        "- **probe לינארי בלבד:** מודד איכות ייצוג, לא את התקרה של fine-tuning מלא (שלא מתאים לטלפון ממילא).",
        "- **BEATs לא נמדד** (ראו רישוי): המשקלים הרשמיים זמינים רק דרך OneDrive, שלא נגיש מהמכונה הזו בלי לעקוף "
        "אימות TLS.",
    ]


def report(out):
    encs = out["encoders"]
    res = out["results"]
    L = ["# השוואת מקודדי אודיו מאומנים-מראש מול YAMNet - מנוע סכנה v2", "",
         f"נוצר אוטומטית ע\"י `research_tools/hv2_pretrained_compare.py` ({out['generated']}, seed {out['seed']}). "
         "כל מספר כאן מגיע מהסקריפט; הנתונים המלאים ב-`research/hazard_v2_pretrained_comparison.json`. "
         "מחקר בלבד - שום מודל לא נוסף לאפליקציה.", "",
         "## שאלה", "",
         "האם מקודד אודיו מאומן-מראש אחר נותן ייצוג טוב מספיק מ-YAMNet (שרץ היום באפליקציה) כדי להצדיק את העלות "
         "(זמן ריצה / גודל) בטלפון, ובאילו קטגוריות?", ""]
    L += ["## תקציר", ""] + bottom_line(out) + ["", "## שיטה", ""]
    sp = out["subset_params"]
    L += ["- **אותו תת-מדגם, אותו probe לכל המקודדים.** כל מקודד רץ פעם אחת על הקטע (עד "
         f"{sp['clip_s']:.0f} ש' - חלון האנרגיה הגבוהה, עיוור לתווית; בקצב הטבעי של המקודד - resample "
         "ב-scipy), הייצוג האחרון שלו שעדיין מפורט בזמן מקובץ לסגמנטים של 1 ש', ו-embedding הקטע = [ממוצע, מקסימום] על "
         "הסגמנטים. YAMNet: מסגרות 0.96 ש'. PANNs: מפת התכונות לפני ה-pooling (0.32 ש') -> max+mean לכל סגמנט -> fc1 "
         "(ה-embedding הרגיל של PANNs, לכל סגמנט). AST: טוקני ה-patch ממוצעים על התדר (0.1 ש'); הקלט באורכו האמיתי "
         "וה-positional embedding של ציר הזמן נחתך (כמו בקוד המקורי של AST לקלט קצר; נבדק: באורך מלא זהה ל-HF). "
         "CLAP: מפת התכונות האחרונה של HTSAT (0.31 ש'), רק ההופעה הראשונה של האודיו (CLAP חוזר על קטע קצר עד 10 ש').",
         f"- **probe:** רגרסיה לוגיסטית (sklearn, איזון מחלקות, StandardScaler), אימון על train בלבד; C מתוך "
         "{0.001, 0.01, 0.1} לפי AUC על val; **סף** על val כך ש-≤"
         f"{100 * out['fp_budget_val']:.0f}% משליליי ה-val עוברים. ב-test הסף קבוע (לא מכוילים על המבחן).",
         "- **כללי נתונים:** אימון / כיול רק על `split in (train, val)` ו-`commercial_ok`; הערכה רק על `split == test` "
         f"(אין פיצול מחדש). נבדק: כל פריטי train/val מותרים מסחרית = {out['subset']['train_val_all_commercial_ok']}.",
         "- **רקע:** חלונות 1.5 ש' (אורך ההקלטה באפליקציה אחרי טריגר) מקטעי SONYC בלי שום תווית ומ-TAU (טלפונים b/c, "
         "רחוב / כלי רכב). שאר השליליים והחיוביים - קטעים שלמים.",
         "- **מדדים ב-test:** AUC (חיוביים מול כל השליליים שהם קטעים; bootstrap 95%), recall בסף של val (Wilson 95%), "
         "שיעור שווא על שליליים קשים (קטעים עם תגית שלילי-קשה), על חלונות רקע, ולפי מקור. "
         "ההשוואה מול YAMNet מזווגת (אותם דגימות bootstrap).",
         "- **CLAP zero-shot:** בלי אימון - דמיון קוסינוס לפרומפטים (softmax על כל הפרומפטים, כולל פרומפטים שליליים "
         "כמו צופר / מוזיקה / תנועה); רק הסף נבחר על val באותו תקציב.", ""]

    # ---- טבלה ראשית
    L += ["## טבלה ראשית - AUC ו-recall ב-test (סף מ-val, ≤1% שווא ב-val)", "",
          "כל תא: **AUC** / recall. בסוגריים - AUC 95% bootstrap.", ""]
    L.append("| קטגוריה | חיוביים ב-test | " + " | ".join(ENC_LABEL[e] for e in encs) + " |")
    L.append("|---|---|" + "---|" * len(encs))
    for c in CATS:
        R = res[c]
        cells = []
        for e in encs:
            r = R["encoders"][e]
            cells.append(f"**{r['auc']:.3f}** / {_pct(r['recall'])} ({r['auc_ci'][0]:.3f}-{r['auc_ci'][1]:.3f})")
        L.append(f"| {HE[c]} ({c}) | {R['n_test_pos']} | " + " | ".join(cells) + " |")
    L += ["", "## התראות שווא ב-test (באותו סף)", "",
          "כל תא: שליליים קשים / חלונות רקע / השלילי הקשה המרכזי לקטגוריה.", ""]
    L.append("| קטגוריה (שלילי מרכזי) | " + " | ".join(ENC_LABEL[e] for e in encs) + " |")
    L.append("|---|" + "---|" * len(encs))
    for c in CATS:
        R = res[c]
        kh = KEY_HARD[c]
        cells = []
        for e in encs:
            r = R["encoders"][e]
            cells.append(f"{_pct(r['fpr_hard'])} / {_pct(r['fpr_background'], 2)} / {_pct(r['fpr_hard_by_tag'].get(kh))}")
        L.append(f"| {HE[c]} ({kh}) | " + " | ".join(cells) + " |")
    n_hard = res[CATS[0]]["n_test_hard"]
    n_bg = res[CATS[0]]["n_test_background_windows"]
    L += ["", f"(מכנים, למשל ל-{CATS[0]}: {n_hard} שליליים קשים, {n_bg} חלונות רקע; לכל קטגוריה ב-JSON.)", ""]

    # ---- הפרש מול YAMNet
    L += ["## הפרש מזווג מול YAMNet (ΔAUC, Δrecall; 95% bootstrap)", ""]
    others = [e for e in encs if e != "yamnet"]
    L.append("| קטגוריה | " + " | ".join(ENC_LABEL[e] for e in others) + " |")
    L.append("|---|" + "---|" * len(others))
    for c in CATS:
        cells = []
        for e in others:
            v = res[c]["encoders"][e].get("vs_yamnet")
            if v is None:
                cells.append("-")
                continue
            cells.append(f"{v['d_auc']:+.3f} [{v['d_auc_ci'][0]:+.3f},{v['d_auc_ci'][1]:+.3f}] / "
                         f"{100 * v['d_recall']:+.1f} [{100 * v['d_recall_ci'][0]:+.1f},{100 * v['d_recall_ci'][1]:+.1f}]")
        L.append(f"| {HE[c]} | " + " | ".join(cells) + " |")

    # ---- לפי מקור
    L += ["", "## recall לפי מקור / מכשיר (test)", "",
          "fsd50k = FSD50K eval (Freesound); gunshot_fit:phone / other_mic = ירי אמיתי במטווח (חצי המבחן); "
          "sonyc_ust = רחוב NYC; esc50; nonspeech7k.", ""]
    for c in CATS:
        groups = sorted({g for e in encs for g in res[c]["encoders"][e]["recall_by_group"]})
        L.append(f"**{HE[c]} ({c})**")
        L.append("")
        L.append("| מקור | " + " | ".join(ENC_LABEL[e] for e in encs) + " |")
        L.append("|---|" + "---|" * len(encs))
        for g in groups:
            L.append(f"| {g} | " + " | ".join(_pci(res[c]["encoders"][e]["recall_by_group"].get(g), 0) for e in encs) + " |")
        L.append("")
    L += ["## שווא על חלונות רקע לפי מקור (test, 1.5 ש')", ""]
    L.append("| קטגוריה | מקור | " + " | ".join(ENC_LABEL[e] for e in encs) + " |")
    L.append("|---|---|" + "---|" * len(encs))
    for c in CATS:
        for g in sorted(res[c]["encoders"][encs[0]]["fpr_background_by_group"]):
            L.append(f"| {HE[c]} | {g} | " + " | ".join(_pci(res[c]["encoders"][e]["fpr_background_by_group"].get(g))
                                                     for e in encs) + " |")
    L += ["", "## שווא על שליליים קשים לפי תגית (test)", ""]
    L.append("| קטגוריה | תגית | " + " | ".join(ENC_LABEL[e] for e in encs) + " |")
    L.append("|---|---|" + "---|" * len(encs))
    for c in CATS:
        for t in HARD:
            if t not in res[c]["encoders"][encs[0]]["fpr_hard_by_tag"]:
                continue
            L.append(f"| {HE[c]} | {t} | " + " | ".join(_pct(res[c]["encoders"][e]["fpr_hard_by_tag"].get(t))
                                                    for e in encs) + f" ({res[c]['encoders'][encs[0]]['fpr_hard_by_tag'][t]['n']}) |")

    # ---- latency / גודל / רישוי
    L += ["", "## זמן ריצה, גודל ורישוי", ""]
    lat = out.get("latency")
    if lat:
        L.append(f"זמן לחלון של {lat['window_s']} ש' (כולל חילוץ תכונות מה-waveform, בלי resample), {lat['note']}. "
                 f"median / p95 באלפיות שנייה, {lat['results']['yamnet']['1_thread']['n']} ריצות אחרי חימום. "
                 f"עומס CPU כולל במכונה בסוף המדידה: {lat.get('cpu_load_pct_at_end')}% (תהליכים אחרים רצו במקביל - "
                 "המספרים המוחלטים מנופחים; היחס בין המקודדים הוא העיקר).")
        L.append("")
        L.append("| מקודד | פרמטרים | קובץ משקלים (MB) | חוט 1 | ברירת מחדל (חוטים) | פי כמה מ-YAMNet (חוט 1) | רישוי משקלים | שימוש מסחרי |")
        L.append("|---|---|---|---|---|---|---|---|")
        y1 = lat["results"]["yamnet"]["1_thread"]["median_ms"]
        for e in ENCODERS:
            if e not in lat["results"]:
                continue
            r = lat["results"][e]
            p = r.get("params")
            dt = r["default_threads"]
            L.append(f"| {ENC_LABEL[e]} | {p / 1e6:.1f}M | {r['size_mb']:.0f} | {r['1_thread']['median_ms']:.0f} / "
                     f"{r['1_thread']['p95_ms']:.0f} | {dt['median_ms']:.0f} / {dt['p95_ms']:.0f} ({dt['threads']}) | "
                     f"x{r['1_thread']['median_ms'] / y1:.1f} | {LICENCES[e]['licence']} | {LICENCES[e]['commercial']} |")
        L.append("")
        L.append("הערות: YAMNet נמדד כ-TFLite (LiteRT) - כפי שרץ באפליקציה; השאר ב-PyTorch fp32 (לא הומרו ל-TFLite / "
                 "לא קוונטזו - בטלפון המספרים יהיו אחרים, אבל הסדר בין המקודדים צפוי להישמר). CLAP מרפד כל קלט ל-10 ש' "
                 "(repeatpad, כך אומן) - לכן חלון של 1.5 ש' עולה כמו 10 ש'. AST נמדד באורך הקלט האמיתי (חיתוך positional "
                 "embedding). גודל YAMNet = קובץ ה-TFLite; PANNs = קובץ ה-.pth (כולל ראש הסיווג); AST / CLAP = קובץ "
                 "המשקלים ב-HF (CLAP כולל גם את מקודד הטקסט). פרמטרים: CLAP = מקודד האודיו + ההטלה בלבד; YAMNet - "
                 f"{lat['results']['yamnet'].get('params_note', '')}.")
    L += ["", "**מקורות רישוי (נבדקו):**", ""]
    for e, d in LICENCES.items():
        L.append(f"- **{e}**: {d['weights']} - {d['licence']}; מסחרי: {d['commercial']}; נתוני אימון: {d['data']}; "
                 f"{d['source']}" + (f". **{d['skipped']}**" if d.get("skipped") else ""))

    # ---- תת-מדגם
    st = out["subset"]
    L += ["", "## תת-המדגם (זהה לכל המקודדים)", "",
          f"**שני שלבים.** (1) subset מלא ({out['n_items_full']} פריטים, seed קבוע): חיוביים - כל הזמינים, עד "
          f"{sp['cap_pos_train']} לקטגוריה ב-train (val ו-test - הכול); שליליים קשים "
          f"{sp['n_hard_per_tag']['train']}/{sp['n_hard_per_tag']['val']}/{sp['n_hard_per_tag']['test']} לתגית "
          f"(train/val/test; רק קטעים בלי תגית יעד); שליליים אחרים מ-FSD50K "
          f"{sp['n_other']['train']}/{sp['n_other']['val']}/{sp['n_other']['test']}; רקע SONYC "
          f"{sp['n_bg_sonyc']['train']}/{sp['n_bg_sonyc']['val']} חלונות, ב-test כל קטעי SONYC בלי תווית x "
          f"{sp['sonyc_test_windows_per_clip']} חלונות + {sp['n_tau_windows']} חלונות TAU (טלפון, רחוב / כלי רכב), "
          f"כל קטעי ESC-50 המתויגים + {sp['n_esc_other']} אחרים, וכל Nonspeech7k. "
          f"(2) **core = מה שנמדד בפועל ({out['n_items_core']} פריטים):** ה-CPU במכונה היה תפוס כמעט כולו בעבודה "
          "מקבילה אחרת (חילוץ תכונות של v2), ובקצב שנמדד AST / CNN14 על כל ה-subset היו לוקחים עשרות שעות. לכן נבחר "
          "תת-מדגם דטרמיניסטי (hash של המפתח): חיוביים "
          f"{sp['core_pos']['train']}/{sp['core_pos']['val']} לקטגוריה (train/val), ב-test עד "
          f"{sp['core_test_pos_per_group']} לכל קטגוריה x מקור (כך שכל מקור - FSD50K, ירי בטלפון / במיקרופון אחר, "
          f"SONYC, ESC-50, Nonspeech7k - מיוצג); שליליים קשים {sp['core_hard']['train']}/{sp['core_hard']['val']}/"
          f"{sp['core_hard']['test']} לתגית; אחרים {sp['core_other']['train']}/{sp['core_other']['val']}/"
          f"{sp['core_other']['test']}; רקע {sp['core_bg_per_group']['train']}"
          f"/{sp['core_bg_per_group']['val']}/{sp['core_bg_per_group']['test']} חלונות לכל קבוצת רקע. "
          "**סטייה מהבקשה:** לא כל חיוביי ה-test נכללו (ראו מספרים בטבלה), ואורך הקטע הוגבל ל-"
          f"{sp['clip_s']:.0f} ש'.", "",
          "| קטגוריה | train חיובי/שלילי | val חיובי/שלילי | test חיובי/שלילי | מוחרגים (עמומים) train/val/test |",
          "|---|---|---|---|---|"]
    for c in CATS:
        s = st[c]
        L.append(f"| {c} | {s['train']['pos']}/{s['train']['neg']} | {s['val']['pos']}/{s['val']['neg']} | "
                 f"{s['test']['pos']}/{s['test']['neg']} | {s['train']['excluded']}/{s['val']['excluded']}/"
                 f"{s['test']['excluded']} |")
    L += ["", "פריטים לפי פיצול ותפקיד: " + ", ".join(f"{k}={v}" for k, v in sorted(st["by_split_role"].items())), "",
          "החרגות (כמו ב-hazard_audio_v1): פיצוץ - קטעים עם ירי / זיקוקים מוחרגים (Explosion הוא אב שלהם ב-AudioSet); "
          "אזעקה - סירנה / טלפון / רינגטון / פעמון דלת / פעמון אופניים מוחרגים, צופר = שלילי; זכוכית - Glass בלי Shatter "
          "מוחרג; צרחה - צעקה (Shout / Yell, וצעקות SONYC) מוחרגות.", ""]

    # ---- שורה תחתונה
    wins = verdict(out)
    L += ["## כלל ההחלטה ותוצאתו", "",
          "כלל ההחלטה (נקבע לפני ההרצה): מקודד \"עדיף\" על YAMNet בקטגוריה רק אם ה-CI המזווג של ΔAUC כולו מעל 0, "
          "ה-CI של Δrecall (בסף של val) כולו מעל 0 וגם Δrecall ≥ 5 נקודות אחוז, ושיעור השווא על חלונות רקע לא עולה "
          "ביותר מנקודת אחוז.", ""]
    if not wins:
        L.append("- **אף מקודד לא עבר את הכלל באף קטגוריה.**")
    for e, cs in wins.items():
        L.append(f"- **{ENC_LABEL[e]}** עדיף ב: {', '.join(HE[c] for c in cs)} ({len(cs)}/{len(CATS)}).")
    L += ["", "## מגבלות", ""] + limitations(out)
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "subset":
        build_subset()
    elif cmd == "core":
        build_core()
    elif cmd == "audio":
        extract_audio()
    elif cmd == "embed":
        embed(sys.argv[2:] or ENCODERS)
    elif cmd == "latency":
        latency()
    elif cmd == "eval":
        evaluate()
    elif cmd == "report":
        report(json.load(open(OUT_JSON, encoding="utf-8")))
    else:
        print(__doc__)

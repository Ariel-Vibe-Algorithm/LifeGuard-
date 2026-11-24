# -*- coding: utf-8 -*-
"""
hv2_features.py - חילוץ מאפייני חלונות (hazard_features) לכל קטע במניפסט, כולל העשרה מציאותית, במקביל.

- חלון = 1.5 ש'; YAMNet רץ על כל חלון לבד - בדיוק כמו באפליקציה.
- לכל חלון נשמרים: שלב 1 (2048), YAMNet למחלקות מבלבלות (מקס + ממוצע), מאפיינים ספקטרליים רב-רזולוציה,
  ציוני v1 על אותו חלון (לבסיס ההשוואה), וציון zero-shot לכל יעד (לבחירת חלונות חיוביים בתיוג חלש).
- העשרה (רק לפיצול train, ותמיד גם לשליליים - אחרת המודל לומד "הדהוד = סכנה"):
  far_street: מרחק (הנחתה, בליעת תדרים גבוהים באוויר, הדהוד) + רקע רחוב אמיתי (SONYC train, CC BY) + ערוץ טלפון;
  indoor_tv: רקע מוזיקה/דיבור/תופים (FSD50K train, CC0/CC BY) + הדהוד חדר + טלפון;
  phone: ערוץ טלפון בלבד (+ לפעמים כיס).
  רקע ההעשרה נלקח רק מפיצול train (חיישני SONYC אחרים מ-val/test) - אין דליפה.
- ניתן לחידוש: מטמון לכל "חבילה" (shard); מה שכבר חושב לא מחושב שוב.

שימוש: python -I research_tools/hv2_features.py bank          # בנק רקעים להעשרה
        python -I research_tools/hv2_features.py extract SPLIT  # train / val / test
"""
import hashlib
import io
import json
import os
import sys
import tarfile
import time
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "research_data", "hv2")
BANK = os.path.join(OUT, "bg_bank.npz")
WORKERS = 10
SEED = 20261115
CAP_NEG_PER_TAG_TRAIN = 1500
CAP_NEG_PER_TAG_VAL = 400
RANDOM_OTHER = {"train": 3000, "val": 800}
WIN_POS, WIN_NEG = 10, 4          # מקסימום חלונות לקטע עם יעד / בלי
AUG_POS_COPIES = 2
AUG_NEG_CLIPS = 4000


def log(m):
    print(m, flush=True)


def _h(key, mod=1000):
    return int(hashlib.sha256(str(key).encode()).hexdigest(), 16) % mod


# ================================================================= קריאת אודיו
_ZIPS = {}


def read_row(r):
    """רשומת מניפסט -> (אודיו 16kHz float32). SONYC נקרא בנפרד (לפי tar)."""
    from research_tools import danger_audio_external as E
    from research_tools import yamnet_detector as Y
    ref = r["ref"]
    if "zip_split" in ref:
        from research_tools.split_zip import SplitZip
        sp = ref["zip_split"]
        if sp not in _ZIPS:
            z = SplitZip(os.path.join(ROOT, "research", "samples", "fsd50k", "raw", f"FSD50K.{sp}_audio.zip"))
            z.__enter__()
            names = {os.path.basename(n)[:-4]: n for n in z.names() if n.endswith(".wav")}
            _ZIPS[sp] = (z, names)
        z, names = _ZIPS[sp]
        x, sr = E.read_wav(z.read(names[ref["fname"]]))
    else:
        x, sr = E.read_wav(ref["path"])
    return Y.to_16k(x, sr)


# ================================================================= העשרה
def _reverb(x, rng, rt60, drr_db, sr=16000):
    n = int(min(rt60, 1.5) * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-6.9 * t / rt60).astype(np.float32)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    from scipy.signal import fftconvolve
    wet = fftconvolve(x, ir)[:len(x)].astype(np.float32)
    g = 10 ** (-drr_db / 20)
    return (x + g * wet * (np.sqrt(np.mean(x ** 2)) / (np.sqrt(np.mean(wet ** 2)) + 1e-9))).astype(np.float32)


def augment(x, kind, rng, bank):
    from scipy.signal import butter, sosfilt
    from research_tools import augment as A
    x = np.asarray(x, np.float32)
    info = {"kind": kind}
    if kind == "far_street":
        d = float(rng.uniform(8, 60))                           # מטרים
        fc = float(np.clip(8000 - 90 * d, 1800, 7500))           # בליעת תדרים גבוהים באוויר (קירוב)
        x = sosfilt(butter(4, fc, btype="low", fs=16000, output="sos"), x).astype(np.float32)
        x = _reverb(x, rng, rt60=float(rng.uniform(0.2, 0.9)), drr_db=float(10 - 0.25 * d))
        bg = bank["street"][int(rng.integers(len(bank["street"])))]
        snr = float(rng.uniform(-6, 12))
        info.update(distance_m=round(d), snr_db=round(snr, 1))
    elif kind == "indoor_tv":
        x = _reverb(x, rng, rt60=float(rng.uniform(0.3, 1.0)), drr_db=float(rng.uniform(0, 10)))
        bg = bank["indoor"][int(rng.integers(len(bank["indoor"])))]
        snr = float(rng.uniform(0, 20))
        info.update(snr_db=round(snr, 1))
    else:
        bg, snr = None, None
    if bg is not None:
        x = A.mix_snr(x, bg.astype(np.float32), snr, rng)
    x = A.phone_channel(x, rng)
    if rng.random() < 0.2:
        x = A.pocket(x, rng)
        info["pocket"] = True
    return x.astype(np.float32), info


def build_bank(log=log):
    """בנק רקעים (5 ש', 16kHz): רחוב מ-SONYC train (בלי יעד), פנים מ-FSD50K train (מוזיקה/דיבור/תופים, CC0/BY)."""
    from research_tools import hv2_data as D
    from research_tools import danger_audio_external as E
    from research_tools import yamnet_detector as Y
    rng = np.random.default_rng(SEED)
    rows = D.load_manifest()
    street_ids = {r["ref"]["member"]: r for r in rows if r["source"] == "sonyc_ust" and r["split"] == "train"
                  and not set(r["tags"]) & set(D.TARGETS)}
    street = []
    d = os.path.join(D.AUDIO, "sonyc_ust")
    for tf in sorted(f for f in os.listdir(d) if f.startswith("audio-") and f.endswith(".tar.gz")):
        with tarfile.open(os.path.join(d, tf), "r:gz") as t:
            for m in t:
                if m.name in street_ids and rng.random() < 0.5 and len(street) < 500:
                    x, sr = E.read_wav(t.extractfile(m).read())
                    x = Y.to_16k(x, sr)
                    street.append(x[:80000].astype(np.float16))
    indoor_rows = [r for r in rows if r["source"] == "fsd50k" and D.trainable(r) and r["split"] == "train"
                   and set(r["tags"]) & {"music", "speech", "drums_music", "tv_like", "crowd_cheer", "laughter"}
                   and not set(r["tags"]) & set(D.TARGETS)]
    rng.shuffle(indoor_rows)
    indoor = []
    for r in indoor_rows[:400]:
        x = read_row(r)
        if len(x) >= 32000:
            indoor.append(x[:80000].astype(np.float16))
    os.makedirs(OUT, exist_ok=True)
    pad = lambda xs: np.stack([np.pad(a, (0, 80000 - len(a))) for a in xs])  # noqa: E731
    np.savez_compressed(BANK, street=pad(street), indoor=pad(indoor))
    log(f"בנק רקעים: רחוב {len(street)}, פנים {len(indoor)} -> {BANK}")


# ================================================================= עובדים
_W = {}


def _init():
    import audio_intel as AI
    import acoustic_ai_module as AC
    from research_tools import yamnet_detector as Y
    from research_tools import hazard_audio_train as T
    import hazard_features as HF
    AI.load()
    AC.load()
    card = AI._STATE["card"]
    from research_tools import hv2_data as D
    yam_idx = {}
    for t in D.TARGETS:
        if t == "distress_voice":
            names = Y.class_names()
            yam_idx[t] = [names.index(c) for c in Y.DISTRESS_CLASSES]
        else:
            yam_idx[t] = card["categories"][t]["yam_idx"]
    W, b = T.yamnet_dense()
    _W.update(yam=Y.Yamnet(), AI=AI, AC=AC, card=card, yam_idx=yam_idx, HF=HF, D=D, W=W, b=b,
              bank=dict(np.load(BANK)) if os.path.exists(BANK) else None)


def window_record(x16):
    """חלון אחד -> כל המאפיינים + ציוני v1 (בדיוק כמו audio_intel.analyze על אותו חלון)."""
    HF, AI = _W["HF"], _W["AI"]
    scores, emb = _W["yam"].analyze(x16, 16000)
    s1 = HF.stage1_vector(emb)
    cur = np.asarray(scores, np.float32)[:, HF.curated_idx()]
    spec = HF.spectral_features(x16)
    v1 = []
    for cat in _W["D"].TARGETS:
        if cat == "distress_voice":
            d = _W["AC"].analyze_embeddings(emb)
            v1.append(d["score"] if d else -1e9)
        else:
            v1.append(float(AI._category_frames(cat, _W["card"]["categories"][cat], scores, emb).max()))
    zs = [float(np.asarray(scores)[:, _W["yam_idx"][t]].max()) for t in _W["D"].TARGETS]
    rms = float(np.sqrt(np.mean(x16 ** 2) + 1e-12))
    return s1.astype(np.float16), np.concatenate([cur.max(0), cur.mean(0)]).astype(np.float16), spec, \
        np.array(v1, np.float32), np.array(zs, np.float32), rms


def _windows_for(r, x, rng):
    HF = _W["HF"]
    is_pos = bool(set(r["tags"]) & set(_W["D"].TARGETS))
    return HF.windows(x, max_windows=WIN_POS if is_pos else WIN_NEG, rng=rng)


def task_clips(args):
    rows, aug_plan = args
    out = []
    for r in rows:
        rng = np.random.default_rng(_h(r["id"], 2 ** 31))
        try:
            x = read_row(r)
        except Exception as exc:  # noqa: BLE001
            out.append({"id": r["id"], "error": str(exc)[:200]})
            continue
        variants = [("orig", x, {})]
        for k, kind in enumerate(aug_plan.get(r["id"], [])):
            xa, info = augment(x, kind, np.random.default_rng(_h(f"{r['id']}|{k}", 2 ** 31)), _W["bank"])
            variants.append((f"aug{k}", xa, info))
        for vname, xv, info in variants:
            for start, w in _windows_for(r, xv, rng):
                out.append({"id": r["id"], "variant": vname, "start": start / 16000, "aug": info,
                            "feat": window_record(w)})
    return out


def task_sonyc_tar(args):
    tf, wanted, aug_plan = args
    from research_tools import danger_audio_external as E
    from research_tools import yamnet_detector as Y
    out = []
    d = os.path.join(_W["D"].AUDIO, "sonyc_ust")
    with tarfile.open(os.path.join(d, tf), "r:gz") as t:
        for m in t:
            r = wanted.get(m.name)
            if r is None:
                continue
            rng = np.random.default_rng(_h(r["id"], 2 ** 31))
            x, sr = E.read_wav(t.extractfile(m).read())
            x = Y.to_16k(x, sr)
            variants = [("orig", x, {})]
            for k, kind in enumerate(aug_plan.get(r["id"], [])):
                xa, info = augment(x, kind, np.random.default_rng(_h(f"{r['id']}|{k}", 2 ** 31)), _W["bank"])
                variants.append((f"aug{k}", xa, info))
            for vname, xv, info in variants:
                for start, w in _windows_for(r, xv, rng):
                    out.append({"id": r["id"], "variant": vname, "start": start / 16000, "aug": info,
                                "feat": window_record(w)})
    return out


# ================================================================= בחירת קטעים + העשרה
def select(rows, split):
    from research_tools import hv2_data as D
    rng = np.random.default_rng(SEED + {"train": 1, "val": 2, "test": 3}[split])
    if split == "test":
        sel = [r for r in rows if r["split"] == "test"]
    else:
        pool = [r for r in rows if r["split"] == split and D.trainable(r)]
        sel = [r for r in pool if set(r["tags"]) & set(D.TARGETS) or r["source"] != "fsd50k"]
        chosen = {r["id"] for r in sel}
        cap = CAP_NEG_PER_TAG_TRAIN if split == "train" else CAP_NEG_PER_TAG_VAL
        for tag in D.HARD_NEG:
            cand = [r for r in pool if tag in r["tags"] and r["id"] not in chosen]
            rng.shuffle(cand)
            for r in cand[:cap]:
                sel.append(r)
                chosen.add(r["id"])
        other = [r for r in pool if r["id"] not in chosen and r["source"] == "fsd50k"]
        rng.shuffle(other)
        sel += other[:RANDOM_OTHER[split]]
    plan = {}
    if split == "train":
        kinds = ("far_street", "indoor_tv", "phone")
        for r in sel:
            if set(r["tags"]) & set(D.TARGETS):
                plan[r["id"]] = [kinds[(_h(r["id"]) + k) % 3] for k in range(AUG_POS_COPIES)]
        negs = [r for r in sel if not set(r["tags"]) & set(D.TARGETS)]
        rng.shuffle(negs)
        for r in negs[:AUG_NEG_CLIPS]:
            plan[r["id"]] = [kinds[_h(r["id"]) % 3]]
    return sel, plan


def extract(split, log=log):
    from research_tools import hv2_data as D
    os.makedirs(os.path.join(OUT, split), exist_ok=True)
    rows = D.load_manifest()
    sel, plan = select(rows, split)
    done = set()
    for f in os.listdir(os.path.join(OUT, split)):
        if f.endswith(".done"):
            done.add(f[:-5])
    tasks = []
    other = [r for r in sel if r["source"] != "sonyc_ust"]
    other.sort(key=lambda r: r["id"])
    for i in range(0, len(other), 200):
        key = f"clips_{_h('|'.join(r['id'] for r in other[i:i + 200]), 10 ** 12)}"
        if key not in done:
            tasks.append(("clips", key, (other[i:i + 200], {r["id"]: plan[r["id"]] for r in other[i:i + 200]
                                                           if r["id"] in plan})))
    son = {}
    for r in sel:
        if r["source"] == "sonyc_ust":
            son.setdefault(r["ref"]["tar"], {})[r["ref"]["member"]] = r
    for tf, wanted in sorted(son.items()):
        key = f"sonyc_{tf.split('.')[0]}_{_h('|'.join(sorted(wanted)), 10 ** 12)}"
        if key not in done:
            tasks.append(("sonyc", key, (tf, wanted, {r["id"]: plan[r["id"]] for r in wanted.values()
                                                      if r["id"] in plan})))
    log(f"{split}: {len(sel)} קטעים, {sum(len(v) for v in plan.values())} עותקים מועשרים; "
        f"{len(tasks)} חבילות לחישוב ({len(done)} כבר במטמון)")
    t0 = time.time()
    with Pool(WORKERS, initializer=_init) as pool:
        jobs = [(key, pool.apply_async(task_clips if kind == "clips" else task_sonyc_tar, (payload,)))
                for kind, key, payload in tasks]
        for n, (key, job) in enumerate(jobs, 1):
            recs = job.get()
            _save_shard(os.path.join(OUT, split, key), recs)
            if n % 5 == 0 or n == len(jobs):
                log(f"  {split}: {n}/{len(jobs)} חבילות ({time.time() - t0:.0f}s)")
    log(f"{split}: הסתיים ({time.time() - t0:.0f}s)")


def _save_shard(path, recs):
    ok = [r for r in recs if "feat" in r]
    meta = [{k: r[k] for k in ("id", "variant", "start", "aug")} for r in ok]
    err = [r for r in recs if "error" in r]
    if ok:
        np.savez_compressed(path + ".npz", s1=np.stack([r["feat"][0] for r in ok]),
                            cur=np.stack([r["feat"][1] for r in ok]), spec=np.stack([r["feat"][2] for r in ok]),
                            v1=np.stack([r["feat"][3] for r in ok]), zs=np.stack([r["feat"][4] for r in ok]),
                            rms=np.array([r["feat"][5] for r in ok], np.float32),
                            meta=np.array([json.dumps(m) for m in meta]))
    with open(path + ".done", "w", encoding="utf-8") as fh:
        json.dump({"windows": len(ok), "errors": err[:20], "n_errors": len(err)}, fh, ensure_ascii=False)


def load_split(split):
    """כל החבילות של הפיצול -> מערכים מאוחדים + רשימת מטא-דאטה."""
    d = os.path.join(OUT, split)
    parts = sorted(f for f in os.listdir(d) if f.endswith(".npz"))
    arrs = {k: [] for k in ("s1", "cur", "spec", "v1", "zs", "rms")}
    meta = []
    for f in parts:
        z = np.load(os.path.join(d, f), allow_pickle=False)
        for k in arrs:
            arrs[k].append(z[k])
        meta += [json.loads(m) for m in z["meta"]]
    return {k: np.concatenate(v) if v else np.zeros(0) for k, v in arrs.items()}, meta


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "bank":
        build_bank()
    elif cmd == "extract":
        extract(sys.argv[2])
    else:
        print(__doc__)

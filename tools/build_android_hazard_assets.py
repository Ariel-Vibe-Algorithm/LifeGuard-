# -*- coding: utf-8 -*-
"""
build_android_hazard_assets.py - מייצא את מנוע הסכנה הקולי (audio_intel v1 + v2, ראש המצוקה, כלל "רצף צעקות")
לאפליקציית Android, ומייצר וקטורי תאימות (parity) שבדיקות ה-JVM משוות אליהם את המימוש ב-Kotlin.

מקור האמת הוא קובצי המודל במאגר - שום מספר לא מוקלד ידנית:
    models/hazard_audio.npz      -> v1: כרטיס + ראש לכל קטגוריה (champion head / zero_shot)
    models/hazard_audio_v2.npz   -> v2: שלב 1 + שלב 2 לכל קטגוריה, ספים, Platt, רשימת מחלקות YAMNet "מבלבלות"
    models/distress_head.npz     -> ראש המצוקה (רגרסיה לוגיסטית על context_features) + סף
    models/shout_sequence.json   -> כלל "רצף צעקות"
    research/models/yamnet/yamnet_class_map.csv -> אינדקסים של המחלקות המבלבלות (hazard_features.CURATED)

פלט:
    android/app/src/main/assets/hazard/hazard_models.json  (מטא-דאטה + טבלת מערכים)
    android/app/src/main/assets/hazard/hazard_models.bin   (float32 little-endian, ברצף)
    android/app/src/test/resources/hazard_parity.json      (וקטורי תאימות)

את YAMNet עצמו (research/models/yamnet/yamnet_float_embeddings.tflite) Gradle מעתיק בזמן הבנייה - לא כאן.

וקטורי התאימות: גלי קול סינתטיים דטרמיניסטיים + פלטי YAMNet *אמיתיים* שלהם (מחושבים כאן, ב-Python) כקלט קבוע,
וכל מה שלא תלוי ב-YAMNet מחושב בקוד ה-Python של האפליקציה עצמה (hazard_features, audio_intel.analyze עם YAMNet
"מוקלט", acoustic_ai_module, hazard_monitor + hazard_alerts). YAMNet עצמו רץ רק במכשיר.

    python tools/build_android_hazard_assets.py
"""
import base64
import csv
import hashlib
import json
import os
import sys
import types

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# config.py יוצר את data/ בייבוא. המחולל לא נוגע בנתוני משתמשים: config מינימלי עם BASE_DIR בלבד.
_cfg = types.ModuleType("config")
_cfg.BASE_DIR = ROOT
sys.modules["config"] = _cfg

import acoustic_ai_module as AC  # noqa: E402
import audio_intel as AI  # noqa: E402
import hazard_alerts as HA  # noqa: E402
import hazard_features as HF  # noqa: E402
import hazard_monitor as HM  # noqa: E402
from research_tools import yamnet_detector as Y  # noqa: E402

MODELS = os.path.join(ROOT, "models")
ASSET_DIR = os.path.join(ROOT, "android", "app", "src", "main", "assets", "hazard")
PARITY_OUT = os.path.join(ROOT, "android", "app", "src", "test", "resources", "hazard_parity.json")


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode("ascii")


class Blob:
    """מערכי float32 ברצף אחד; כל מערך מתואר ב-JSON כ-[offset, length] (ביחידות float)."""

    def __init__(self):
        self.parts, self.n = [], 0

    def add(self, a):
        a = np.asarray(a, dtype="<f4").reshape(-1)
        ref = [self.n, int(a.size)]
        self.parts.append(a)
        self.n += int(a.size)
        return ref

    def bytes(self):
        return np.concatenate(self.parts).astype("<f4").tobytes() if self.parts else b""


def _head(blob, arrays, prefix):
    return {"mean": blob.add(arrays[prefix + "__mean"]), "std": blob.add(arrays[prefix + "__std"]),
            "w": blob.add(arrays[prefix + "__w"]), "b": float(np.asarray(arrays[prefix + "__b"]).reshape(-1)[0])}


# ======================================================================= נכסי המודל
def build_assets():
    blob = Blob()
    p1 = os.path.join(MODELS, "hazard_audio.npz")
    d1 = np.load(p1, allow_pickle=False)
    card1 = json.loads(str(d1["card"][0]))
    # "order": סדר הקטגוריות בכרטיס (קובע את סדר השורות לפני המיון היציב לפי margin, כמו ב-audio_intel)
    v1 = {"version": card1["version"], "frame_hop_s": card1.get("frame_hop_s", Y.HOP_SECONDS),
          "order": list(card1["categories"]), "categories": {}, "unvalidated": {},
          "unvalidated_order": list(card1.get("unvalidated") or {})}
    for cat, c in card1["categories"].items():
        e = {k: c[k] for k in ("label_he", "severity_prior", "champion", "yam_idx", "threshold", "platt")}
        e["fa_table"] = c.get("fa_table") or []
        if c["champion"] == "head":
            e["head"] = _head(blob, d1, cat)
        v1["categories"][cat] = e
    for cat, u in (card1.get("unvalidated") or {}).items():
        v1["unvalidated"][cat] = {k: u[k] for k in ("label_he", "yam_idx", "threshold", "note")}

    p2 = os.path.join(MODELS, "hazard_audio_v2.npz")
    v2 = None
    if os.path.exists(p2):
        d2 = np.load(p2, allow_pickle=False)
        card2 = json.loads(str(d2["card"][0]))
        idx = [int(i) for i in HF.curated_idx()]
        v2 = {"version": card2["version"], "window_s": card2.get("window_s", HF.WINDOW_S), "curated_idx": idx,
              "order": list(card2["categories"]), "categories": {}}
        for cat, c in card2["categories"].items():
            e = {k: c[k] for k in ("t1", "t2", "two_stage", "platt")}
            e["s1"] = _head(blob, d2, f"{cat}__s1")
            if c["two_stage"]:
                e["s2"] = _head(blob, d2, f"{cat}__s2")
                assert blob.parts[-1].size == 1 + 2 * len(idx) + 29, "ממד שלב 2 לא תואם את hazard_features"
            v2["categories"][cat] = e

    pd = os.path.join(MODELS, "distress_head.npz")
    dd = np.load(pd, allow_pickle=False)
    if "W1" in dd.files:
        raise SystemExit("distress_head.npz הוא MLP - המימוש ב-Android תומך כרגע רק ברגרסיה לוגיסטית")
    info = json.loads(str(dd["info"][0]))
    distress = {"variant": info.get("variant"), "context": bool(info.get("context")), "smooth": int(info.get("smooth", 1)),
                "threshold_logit": float(info.get("threshold_logit", 6.0)),
                "head": {"mean": blob.add(dd["mean"]), "std": blob.add(dd["std"]), "w": blob.add(dd["w"]),
                         "b": float(np.asarray(dd["b"]).reshape(-1)[0])}}

    with open(os.path.join(MODELS, "shout_sequence.json"), encoding="utf-8") as fh:
        seq = json.load(fh)

    meta = {
        "format": "lifeguard_hazard_assets_v1",
        "generated_by": "tools/build_android_hazard_assets.py",
        "sources": {os.path.relpath(p, ROOT).replace("\\", "/"): _sha(p) for p in (p1, p2, pd)
                    if os.path.exists(p)},
        "audio_intel": {"engine": AI.VERSION, "near_threshold_fraction": AI.NEAR_THRESHOLD_FRACTION,
                        "digital_silence_peak": AI.DIGITAL_SILENCE_PEAK, "clip_level": AI.CLIP_LEVEL,
                        "clip_ratio_degraded": AI.CLIP_RATIO_DEGRADED, "min_seconds": AI.MIN_SECONDS},
        "yamnet": {"asset": "hazard/yamnet_float_embeddings.tflite", "sample_rate": Y.TARGET_SR,
                   "frame_win": Y.FRAME_WIN, "frame_hop": Y.FRAME_HOP, "num_classes": 521, "embedding_dim": 1024},
        "v1": v1, "v2": v2, "distress": distress, "shout_sequence": seq,
        "floats": blob.n,
    }
    os.makedirs(ASSET_DIR, exist_ok=True)
    with open(os.path.join(ASSET_DIR, "hazard_models.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    data = blob.bytes()
    with open(os.path.join(ASSET_DIR, "hazard_models.bin"), "wb") as fh:
        fh.write(data)
    print(f"assets: {blob.n} floats ({len(data) / 1024:.0f} KB) -> {os.path.relpath(ASSET_DIR, ROOT)}")
    return meta, d1, (np.load(p2, allow_pickle=False) if v2 else None), dd


# ======================================================================= גלי קול סינתטיים
def waveforms():
    sr, n = HF.SR, HF.WINDOW
    t = np.arange(n) / sr
    rng = np.random.default_rng(20261115)
    out = {}
    x = rng.standard_normal(n).astype(np.float32) * 0.01
    x[6000:10000] += rng.standard_normal(4000).astype(np.float32) * np.exp(-np.arange(4000) / 400).astype(np.float32) * 0.9
    out["impulse_decay"] = x
    x = rng.standard_normal(n).astype(np.float32) * 0.01
    for k in range(0, n - 2000, 4000):
        x[k:k + 2000] += rng.standard_normal(2000).astype(np.float32) * np.exp(-np.arange(2000) / 300).astype(np.float32) * 0.6
    out["periodic_drums"] = x
    out["horn_tone"] = (0.3 * np.sin(2 * np.pi * 420 * t) + 0.15 * np.sin(2 * np.pi * 840 * t)
                        + 0.003 * rng.standard_normal(n)).astype(np.float32)
    f = 900 + 400 * np.sin(2 * np.pi * 0.8 * t)
    out["siren_sweep"] = (0.25 * np.sin(2 * np.pi * np.cumsum(f) / sr) + 0.003 * rng.standard_normal(n)).astype(np.float32)
    out["quiet_noise"] = (np.cumsum(rng.standard_normal(n)) * 0.0004).astype(np.float32)
    out["quiet_noise"] -= np.float32(out["quiet_noise"].mean())
    out["short_padded"] = (0.2 * np.sin(2 * np.pi * 1500 * t[:14000]) * np.exp(-t[:14000] * 3)
                           + 0.01 * rng.standard_normal(14000)).astype(np.float32)
    out["clipped_square"] = np.clip(1.4 * np.sign(np.sin(2 * np.pi * 300 * t)) * (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t)),
                                    -1.0, 1.0).astype(np.float32)
    out["digital_silence"] = np.zeros(n, np.float32)
    return out


# ======================================================================= YAMNet "מוקלט" + וריאציות מכוונות
class FakeYam:
    """מחזיר פלט YAMNet קבוע (שחושב מראש) - כדי להריץ את audio_intel.analyze בדיוק כמו בזמן ריצה."""

    def __init__(self):
        self.out = None

    def analyze(self, x, sr=16000):
        return self.out


def _lin_push(e0, coef, const, target):
    """e = e0 + t*sign(coef): פותר את t כך ש-coef·e + const == target (פונקציה לינארית ב-e)."""
    s = np.sign(coef)
    base = float(coef @ e0 + const)
    t = (target - base) / float(np.abs(coef).sum())
    return (e0 + t * s).astype(np.float32)


def crafted(base_scores, base_emb, d1, d2, dd, meta):
    """מקרים שמפעילים כל ענף של המנוע (ראש v1, zero-shot, v2 דו-שלבי, v2 שעבר רק שלב 1, מצוקה, "כמעט-צעקה")."""
    v1c = meta["v1"]["categories"]
    out = {}

    def head_coef(arrays, prefix):
        w = arrays[prefix + "__w"].astype(np.float64)
        std = arrays[prefix + "__std"].astype(np.float64)
        mean = arrays[prefix + "__mean"].astype(np.float64)
        b = float(np.asarray(arrays[prefix + "__b"]).reshape(-1)[0])
        return w / std, b - float((mean / std) @ w)

    # --- v1 head (impact, low) ומ-zero-shot (scream, medium) + alarm (Platt "מדרגה") ---
    emb = base_emb.copy()
    sc = base_scores.copy()
    coef, const = head_coef(d1, "impact")
    emb[1] = _lin_push(emb[1].astype(np.float64), coef, const, v1c["impact"]["threshold"] + 2.0)
    coef, const = head_coef(d1, "alarm")
    emb[2] = _lin_push(emb[2].astype(np.float64), coef, const, v1c["alarm"]["threshold"] + 1.5)
    sc[0, v1c["scream"]["yam_idx"][0]] = 0.83
    out["crafted_v1_heads_zero_shot"] = (sc, emb)

    # --- v2: פיצוץ עובר את שני השלבים; ירי עובר רק את שלב 1 ---
    v2c = meta["v2"]["categories"]
    idx = np.array(meta["v2"]["curated_idx"])
    emb = base_emb.copy()
    sc = base_scores.copy()
    coef, const = head_coef(d2, "explosion__s1")
    # stage1 = [mean(emb), max(emb)]: כל המסגרות שוות -> mean == max == e
    c2 = coef[:1024] + coef[1024:]
    e = _lin_push(emb.mean(0).astype(np.float64), c2, const, v2c["explosion"]["t1"] + 6.0)
    emb[:] = e
    w2, b2 = head_coef(d2, "explosion__s2")
    k = len(idx)
    want = (w2[1:1 + k] + w2[1 + k:1 + 2 * k]) > 0
    sc[:, idx] = np.where(want, 0.97, 0.001).astype(np.float32)
    out["crafted_v2_explosion"] = (sc, emb)

    emb = base_emb.copy()
    sc = base_scores.copy()
    coef, const = head_coef(d2, "gunshot__s1")
    c2 = coef[:1024] + coef[1024:]
    emb[:] = _lin_push(emb.mean(0).astype(np.float64), c2, const, v2c["gunshot"]["t1"] + 1.0)
    w2, b2 = head_coef(d2, "gunshot__s2")
    want = (w2[1:1 + k] + w2[1 + k:1 + 2 * k]) > 0
    sc[:, idx] = np.where(want, 0.001, 0.9).astype(np.float32)     # הפוך: ההקשר "נגד" -> שלב 2 דוחה
    out["crafted_v2_gunshot_stage1_only"] = (sc, emb)

    # --- ראש המצוקה: context = [e, mean(שכנים), max(שכנים)] - כשכל המסגרות שוות, שלושתם e ---
    w = dd["w"].astype(np.float64)
    std = dd["std"].astype(np.float64)
    mean = dd["mean"].astype(np.float64)
    b = float(np.asarray(dd["b"]).reshape(-1)[0])
    coef3 = w / std
    coef = coef3[:1024] + coef3[1024:2048] + coef3[2048:]
    const = b - float((mean / std) @ w)
    thr = meta["distress"]["threshold_logit"]
    for name, target in (("crafted_distress_detected", thr + 2.0), ("crafted_distress_soft", thr - 1.5)):
        emb = base_emb.copy()
        emb[:] = _lin_push(emb.mean(0).astype(np.float64), coef, const, target)
        out[name] = (base_scores.copy(), emb)
    return out


def _report_json(rep):
    """דו"ח audio_intel להשוואה (בלי anomaly - אין מודל כזה במאגר; האנדרואיד לא מממש אותו)."""
    if rep is None:
        return None
    keep = ("engine", "model_version", "quality", "events", "top", "category_scores", "distress",
            "suspected_unvalidated", "danger_probability")
    return json.loads(json.dumps({k: rep.get(k) for k in keep}, default=float))


def _raw_rows(rep_scores, emb, x16, meta, d1, v2m):
    """ציונים לא מעוגלים (לבדיקת יציבות Platt "מדרגה") + logits של v2 לפי שלב."""
    raw = {}
    for cat, c in meta["v1"]["categories"].items():
        AI.load()
        fr = AI._category_frames(cat, AI._STATE["card"]["categories"][cat], rep_scores, emb)
        raw[cat] = {"frames": [float(v) for v in np.asarray(fr).reshape(-1)]}
    if v2m:
        A = v2m["arrays"]
        w = HF.windows(x16)[0][1]

        def head(p, X):
            return float(((np.asarray(X, np.float32) - A[p + "__mean"]) / A[p + "__std"]) @ A[p + "__w"] + A[p + "__b"][0])
        for cat, c in v2m["card"]["categories"].items():
            l1 = head(f"{cat}__s1", HF.stage1_vector(emb))
            r = {"l1": l1}
            if c["two_stage"]:
                r["l2"] = head(f"{cat}__s2", HF.verifier_vector(l1, rep_scores, w))
            raw.setdefault(cat, {}).update(v2=r)
    return raw


# ======================================================================= סקריפט מוניטור (מנהל ההתראות + רצף צעקות)
def _ev(cat, sev, conf=0.8, score=5.0, thr=4.0, margin=1.0, near=False):
    return {"category": cat, "severity_prior": sev, "detection_confidence": conf, "label_he": cat, "score": score,
            "threshold": thr, "model": "head", "near_threshold": near, "margin": margin}


def _rep(events=(), distress=None, quality="ok", scream=None):
    r = {"model_version": "hazard_audio_v1+hazard_audio_v2.1", "quality": {"status": quality}, "distress": distress,
         "events": list(events)}
    if scream is not None:
        r["category_scores"] = {"scream": scream}
    return r


def monitor_script(rule):
    """צעדים: (now, שניות קטע, דו"חות לכל חלון, העדפות, can_show) או סגירת אירוע. דו"ח לכל חלון בקטע."""
    soft = lambda p=0.6: {"detected": False, "score": round(rule["t_low"] + 0.4, 3), "probability": p}  # noqa: E731
    below = {"detected": False, "score": round(rule["t_low"] - 0.5, 3), "probability": 0.3}
    D = dict(HA.DEFAULTS)
    return [
        {"now": 1000.0, "seconds": 1.5, "reports": [_rep([_ev("gunshot", "high", 0.91), _ev("siren", "info")])]},
        {"now": 1010.0, "seconds": 1.5, "reports": [_rep([_ev("gunshot", "high", 0.95)])]},           # grouped
        {"now": 1015.0, "seconds": 1.5, "reports": [_rep([_ev("alarm", "medium", 0.7)])]},            # rate limited
        {"now": 1040.0, "seconds": 1.5, "reports": [_rep([_ev("impact", "low", 0.6)])]},              # new, below min
        {"now": 1070.0, "seconds": 1.5, "reports": [_rep([_ev("glass_break", "medium", 0.66)])]},     # update (break)
        {"close": "dismiss", "family": "break", "now": 1080.0},
        {"now": 1100.0, "seconds": 1.5, "reports": [_rep([_ev("impact", "low", 0.5)])]},              # cooldown
        {"close": "false_alarm", "family": "blast", "now": 1120.0, "reason": "fireworks"},
        {"now": 1500.0, "seconds": 1.5, "reports": [_rep([_ev("explosion", "high", 0.99)])]},         # cooldown (false)
        {"now": 2200.0, "seconds": 1.5, "reports": [_rep([_ev("explosion", "high", 0.99)])]},         # new again
        {"now": 2300.0, "seconds": 1.5, "reports": [_rep([], distress={"detected": True, "score": 7.25,
                                                                         "probability": 0.9993})]},
        {"close": "dismiss", "family": "voice", "now": 2310.0},
        {"close": "sos", "family": "blast", "now": 2310.0},
        # רצף צעקות: 3 "כמעט-צעקות" בתוך 9 שנ' (שלושה קטעים נפרדים) - אחרי השקט של "סגירה"
        {"now": 2700.0, "seconds": 1.5, "reports": [_rep([], distress=soft(0.61))]},
        {"now": 2703.0, "seconds": 1.5, "reports": [_rep([], distress=soft(0.64))]},
        {"now": 2706.0, "seconds": 1.5, "reports": [_rep([], distress=soft(0.7))], "prefs": dict(D, min_severity="high")},
        {"close": "dismiss", "family": "voice", "now": 2710.0},
        # צעקה (zero-shot) מעל s_low בשלושה חלונות של קטע אחד (4.5 שנ')
        {"now": 3100.0, "seconds": 4.5, "reports": [_rep([], distress=below, scream=rule["s_low"] + 0.05)] * 3,
         "can_show": [False, "silent_tracking"]},
        {"close": "dismiss", "family": "voice", "now": 3110.0},
        # מפוזר בזמן - לא רצף
        {"now": 3600.0, "seconds": 1.5, "reports": [_rep([], distress=soft())]},
        {"now": 3660.0, "seconds": 1.5, "reports": [_rep([], distress=soft())]},
        {"now": 3720.0, "seconds": 1.5, "reports": [_rep([], distress=soft())]},
        # חלונות קופצים כבויים: נרשם בלבד
        {"now": 4000.0, "seconds": 1.5, "reports": [_rep([_ev("gunshot", "high", 0.97, near=True)])],
         "prefs": dict(D, popups=False)},
        # איכות לא זמינה (שקט דיגיטלי) ומקטע קצר מדי
        {"now": 5000.0, "seconds": 1.5, "reports": [_rep([_ev("alarm", "medium")], quality="unavailable")]},
        {"now": 5100.0, "seconds": 0.2, "reports": []},
        # מקטע ארוך (6.9 שנ' -> נחתך ל-6, 4 חלונות): הדו"ח עם הכי הרבה אירועים + מיזוג לפי margin
        {"now": 6000.0, "seconds": 6.9, "reports": [
            _rep([_ev("alarm", "medium", 0.6, margin=0.4)]),
            _rep([_ev("alarm", "medium", 0.75, margin=1.2), _ev("fireworks", "low", 0.5, margin=0.1)]),
            _rep([], distress={"detected": True, "score": 6.9, "probability": 0.999}),
            _rep([_ev("fireworks", "low", 0.55, margin=0.3)])]},
    ]


def run_monitor_script(rule):
    steps = monitor_script(rule)
    clock = [0.0]
    queue = []
    shown, records = [], []
    state = {"prefs": dict(HA.DEFAULTS), "can": (True, None)}

    def fake_analyze(x, sr):
        return queue.pop(0)

    orig = HA.record_alert
    HA.record_alert = lambda owner, alert, shown, reason_not_shown=None: records.append(
        {"category": alert["category"], "severity": alert["severity"], "count": alert["count"],
         "confidence": alert["confidence"], "shown": bool(shown), "not_shown_reason": reason_not_shown,
         "label_he": alert.get("label_he"), "explanation": alert.get("explanation"), "meta": alert.get("meta")})
    try:
        mon = HM.HazardMonitor("parity", can_show=lambda: state["can"], analyze=fake_analyze,
                               on_alert=lambda k, a: shown.append({"kind": k, "category": a["category"],
                                                                   "severity": a["severity"], "count": a["count"]}),
                               prefs_fn=lambda: state["prefs"])
        mon.sequence_rule = rule
        mon.manager = HA.AlertManager(clock=lambda: clock[0])
        expected = []
        for st in steps:
            clock[0] = st["now"]
            if "close" in st:
                a = mon.manager.open.get(st["family"])
                if a is not None:
                    mon.manager.close(a, st["close"])
                expected.append({"closed": a is not None})
                continue
            state["prefs"] = st.get("prefs", dict(HA.DEFAULTS))
            state["can"] = tuple(st.get("can_show", (True, None)))
            x = np.full(int(st["seconds"] * 16000), 0.01, np.float32)
            n_win = 0
            if len(x) >= HM.MIN_SECONDS * 16000:          # אותה חלוקה כמו HazardMonitor.process
                xs = x[-int(HM.MAX_SECONDS * 16000):]
                n = int(HM.WINDOW_S * 16000)
                wins = [xs[i:i + n] for i in range(0, len(xs), n)]
                if len(wins) > 1 and len(wins[-1]) < n // 2:
                    wins.pop()
                n_win = len(wins)
            assert n_win == len(st["reports"]), (st["now"], n_win)
            queue[:] = list(st["reports"])
            out = _process(mon, x, st["now"])
            expected.append({"out": [{"kind": k, "category": a["category"], "severity": a["severity"],
                                      "confidence": a["confidence"], "count": a["count"], "label_he": a.get("label_he"),
                                      "explanation": a.get("explanation"), "show": show, "why": why,
                                      "meta": a.get("meta")} for k, a, show, why in out]})
    finally:
        HA.record_alert = orig
    return {"steps": steps, "expected": expected, "shown": shown, "records": records}


def _process(mon, x, now):
    """HazardMonitor.process מקבל AudioData (speech_local). כאן: אותו מסלול עם מערך float ישירות."""
    import speech_local
    orig = speech_local.audio_data_to_float
    speech_local.audio_data_to_float = lambda a: (a, 16000)
    try:
        return mon.process(x, now=now)
    finally:
        speech_local.audio_data_to_float = orig


# ======================================================================= main
def main():
    meta, d1, d2, dd = build_assets()
    AI._reset_for_tests()
    AC._reset_for_tests()
    if not AC.load():
        raise SystemExit("YAMNet / ראש המצוקה לא זמינים: " + str(AC._STATE["error"]))
    yam_real = AC.yamnet()
    fake = FakeYam()
    AC.yamnet = lambda: fake           # audio_intel משתמש ב-acoustic_ai_module.yamnet()
    v2m = AI._load_v2()

    cases = []
    waves = waveforms()
    real = {}
    for name, x in waves.items():
        sc, emb = (None, None) if name == "digital_silence" else yam_real.analyze(x, 16000)
        real[name] = (sc, emb)
        cases.append((name, x, sc, emb, None))
    base_sc, base_emb = real["quiet_noise"]
    for name, (sc, emb) in crafted(base_sc, base_emb, d1, d2, dd, meta).items():
        cases.append((name, waves["quiet_noise"], sc, emb, "quiet_noise"))

    out_cases = []
    for name, x, sc, emb, ref in cases:
        fake.out = (sc, emb) if sc is not None else (np.zeros((0, 521), np.float32), np.zeros((0, 1024), np.float32))
        rep = AI.analyze(x, 16000)
        win = HF.windows(x)[0][1]
        c = {"name": name, "samples": int(len(x)), **({"wave_ref": ref} if ref else {"wave": _b64(x)}),
             "spectral": [float(v) for v in HF.spectral_features(win)], "report": _report_json(rep)}
        if sc is not None:
            c.update(frames=int(len(sc)), scores=_b64(sc), emb=_b64(emb), raw=_raw_rows(sc, emb, x, meta, d1, v2m),
                     distress_direct=AC.analyze_embeddings(emb))
        out_cases.append(c)
        ev = [(e["category"], e["model"]) for e in (rep or {}).get("events") or []]
        d = (rep or {}).get("distress") or {}
        print(f"  {name:32s} q={((rep or {}).get('quality') or {}).get('status')} events={ev} "
              f"distress={d.get('score')}/{d.get('detected')}")
    _assert_not_borderline(out_cases, meta)

    rule = HM.load_sequence_rule()
    mon = run_monitor_script(rule)
    parity = {
        "generated_by": "tools/build_android_hazard_assets.py",
        "sources": meta["sources"],
        "constants": {"EVENT_GAP_S": HA.EVENT_GAP_S, "COOLDOWN_DISMISS_S": HA.COOLDOWN_DISMISS_S,
                      "COOLDOWN_FALSE_S": HA.COOLDOWN_FALSE_S, "COOLDOWN_ACTION_S": HA.COOLDOWN_ACTION_S,
                      "MIN_GAP_ANY_S": HA.MIN_GAP_ANY_S, "SEVERITY_RANK": HA.SEVERITY_RANK, "FAMILY": HA.FAMILY,
                      "FALSE_REASONS": HA.FALSE_REASONS, "REASONS_EN": HA.REASONS_EN, "RESPONSES": list(HA.RESPONSES),
                      "DEFAULTS": HA.DEFAULTS, "MIN_SEVERITY_OPTIONS": HA.MIN_SEVERITY_OPTIONS,
                      "LABELS_EN": HA.LABELS_EN, "EXPLAIN_EN": HA.EXPLAIN_EN,
                      "MONITOR": {"MIN_SECONDS": HM.MIN_SECONDS, "MAX_SECONDS": HM.MAX_SECONDS, "WINDOW_S": HM.WINDOW_S},
                      "explain": {c: HM._explain({"category": c, "label_he": "L"}) for c in
                                  ("gunshot", "explosion", "glass_break", "alarm", "scream", "impact", "fireworks",
                                   "siren", "other")},
                      "explain_near": HM._explain({"category": "gunshot", "near_threshold": True}),
                      "contacts_message": {lang: HA.contacts_message({"category": "gunshot", "label_he": "ירי",
                                                                       "first_ts": 1763200000.0}, lang)
                                           for lang in ("he", "en")},
                      "contacts_message_ts": 1763200000.0,
                      "contacts_message_tz_offset_s": __import__("time").localtime(1763200000.0).tm_gmtoff},
        "curated_names": list(HF.CURATED),
        "cases": out_cases,
        "monitor": mon,
    }
    os.makedirs(os.path.dirname(PARITY_OUT), exist_ok=True)
    with open(PARITY_OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(parity, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"parity: {len(out_cases)} cases, {len(mon['steps'])} monitor steps -> "
          f"{os.path.relpath(PARITY_OUT, ROOT)} ({os.path.getsize(PARITY_OUT) / 1024:.0f} KB)")


def _assert_not_borderline(cases, meta):
    tl = meta["shout_sequence"]["t_low"]
    sl = meta["shout_sequence"]["s_low"]
    thr = meta["distress"]["threshold_logit"]
    for c in cases:
        rep = c["report"] or {}
        for r in (rep.get("top") or []) + (rep.get("events") or []):
            assert abs(r["margin"]) > 1e-3, (c["name"], r["category"], r["margin"])
        d = rep.get("distress") or {}
        if d.get("score") is not None:
            assert abs(d["score"] - thr) > 1e-2 and abs(d["score"] - tl) > 1e-2, (c["name"], d)
        s = (rep.get("category_scores") or {}).get("scream")
        if s is not None:
            assert abs(s - sl) > 1e-3, (c["name"], s)
        for cat, r in (c.get("raw") or {}).items():
            v2 = r.get("v2") or {}
            cc = (meta["v2"] or {}).get("categories", {}).get(cat)
            if cc and v2:
                assert abs(v2["l1"] - cc["t1"]) > 1e-3, (c["name"], cat, v2)
                if "l2" in v2:
                    assert abs(v2["l2"] - cc["t2"]) > 1e-3, (c["name"], cat, v2)


if __name__ == "__main__":
    main()

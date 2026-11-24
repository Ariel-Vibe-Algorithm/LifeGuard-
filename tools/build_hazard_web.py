# -*- coding: utf-8 -*-
"""
build_hazard_web.py - ראשי הזיהוי של מנוע הסכנה (Windows) -> JSON ל-PWA, + וקטורי בדיקה ל-JS == Python.

קורא (בלי מספרים מוקלדים ידנית):
  models/hazard_audio.npz (v1), models/hazard_audio_v2.npz (v2: explosion / glass_break / gunshot),
  models/distress_head.npz, models/shout_sequence.json, research/models/yamnet/yamnet_class_map.csv
כותב:
  server/web/models/hazard_heads.json   - הכרטיסים + המערכים (float32 ב-base64, ערכים זהים לביט)
  tests/web_e2e/fixtures/hazard_parity.json - קלטים סינתטיים קבועים -> הפלט של קוד ה-Python האמיתי:
      to_16k (resample_poly), hazard_features.spectral_features, audio_intel.analyze (עם YAMNet "מזויף" שמחזיר
      scores/embeddings קבועים - כך נבדקים הראשים, v1/v2, ראש המצוקה וההחלטות), hazard_monitor.HazardMonitor.process
      (קיבוץ, שקט, הגבלת קצב, סינון חומרה, כלל רצף הצעקות, סיבות אי-הצגה), AlertManager ו-contacts_message.
  tests/test_pwa_hazard_parity.py מריץ את אותם קלטים ב-JS (server/web/hazard_engine.js + hazard_core.js) ומשווה.

הרצה (סביבת האפליקציה; לא כותב דבר ל-data/ - רישום ההתראות לפנקס מנוטרל כאן):
    .venv\\Scripts\\python.exe tools\\build_hazard_web.py
"""
import base64
import csv
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODELS = os.path.join(ROOT, "models")
OUT = os.path.join(ROOT, "server", "web", "models", "hazard_heads.json")
FIX = os.path.join(ROOT, "tests", "web_e2e", "fixtures", "hazard_parity.json")
CLASS_MAP = os.path.join(ROOT, "research", "models", "yamnet", "yamnet_class_map.csv")


def b64(a):
    return {"b64": base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode("ascii"), "n": int(np.size(a))}


BLOBS = {}


def ref(a):
    """fixture arrays are stored once (many cases share a waveform) and referenced by hash."""
    import hashlib
    raw = np.ascontiguousarray(a, dtype="<f4").tobytes()
    key = hashlib.sha1(raw).hexdigest()[:16]
    BLOBS.setdefault(key, base64.b64encode(raw).decode("ascii"))
    return {"ref": key, "n": int(np.size(a))}


def jsonable(o):
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    return o


def lin(d, p):
    return {"mean": b64(d[p + "mean"]), "std": b64(d[p + "std"]), "w": b64(d[p + "w"]), "b": float(d[p + "b"][0])}


# ================================================================= heads -> JSON
def build_heads():
    v1 = np.load(os.path.join(MODELS, "hazard_audio.npz"), allow_pickle=False)
    v2 = np.load(os.path.join(MODELS, "hazard_audio_v2.npz"), allow_pickle=False)
    dh = np.load(os.path.join(MODELS, "distress_head.npz"), allow_pickle=False)
    with open(os.path.join(MODELS, "shout_sequence.json"), encoding="utf-8") as fh:
        seq = json.load(fh)
    with open(CLASS_MAP, encoding="utf-8") as fh:
        names = [r["display_name"] for r in csv.DictReader(fh)]
    c1 = json.loads(str(v1["card"][0]))
    c2 = json.loads(str(v2["card"][0]))
    info = json.loads(str(dh["info"][0]))
    if "W1" in dh.files:
        raise SystemExit("distress head is an MLP - the PWA port supports the logistic head only")
    cats = {}
    for cat, c in c1["categories"].items():
        e = {k: c[k] for k in ("label_he", "severity_prior", "champion", "yam_idx", "threshold", "platt", "fa_table")}
        if c["champion"] == "head":
            e["head"] = lin(v1, f"{cat}__")
        cats[cat] = e
    import hazard_features as HF
    curated_idx = [names.index(c) for c in HF.CURATED if c in names]
    assert [names[i] for i in curated_idx] == [c for c in c2.get("curated", HF.CURATED) if c in names]
    heads2 = {}
    for cat, c in c2["categories"].items():
        heads2[cat] = {"s1": lin(v2, f"{cat}__s1__"), "s2": lin(v2, f"{cat}__s2__") if c["two_stage"] else None}
    out = {
        "generated_by": "tools/build_hazard_web.py",
        "note": "Detection heads of the Windows audio hazard engine (audio_intel.py v1 + v2, acoustic_ai_module distress "
                "head, shout-sequence rule). Arrays are float32 little-endian, base64. No audio, no user data.",
        "v1": {"version": c1["version"], "created": c1["created"], "frame_hop_s": c1.get("frame_hop_s", 0.48),
               "categories": cats, "unvalidated": c1.get("unvalidated", {})},
        "v2": {"version": c2["version"], "created": c2["created"], "window_s": c2.get("window_s", 1.5),
               "categories": {k: {kk: v[kk] for kk in ("t1", "t2", "two_stage", "platt")} for k, v in c2["categories"].items()},
               "curated_idx": curated_idx, "heads": heads2},
        "distress": {"variant": info.get("variant"), "context": bool(info.get("context")), "smooth": int(info.get("smooth", 1)),
                     "threshold_logit": float(info["threshold_logit"]), "head": lin(dh, ""),
                     "license_note": info.get("license_note")},
        "sequence": seq if {"k", "t_low", "s_low", "span_s"} <= set(seq) else None,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {os.path.relpath(OUT, ROOT)} ({os.path.getsize(OUT) / 1e3:.0f} KB)")
    return {"v1": v1, "c1": c1, "v2": v2, "c2": c2, "dh": dh, "info": info, "seq": seq}


# ================================================================= parity vectors
SR = 16000


def waves():
    rng = np.random.default_rng(7)
    t = np.arange(24000) / SR
    W = {}
    W["noise"] = (rng.standard_normal(24000) * 0.05).astype(np.float32)
    W["am_tone"] = (0.3 * np.sin(2 * np.pi * 1200 * t) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 4 * t)))
                    + rng.standard_normal(24000) * 0.005).astype(np.float32)
    imp = rng.standard_normal(24000) * 0.003
    for c in (2000, 14000):
        imp[c:c + 3000] += np.exp(-np.arange(3000) / 400.0) * rng.standard_normal(3000) * 0.9
    W["impulsive"] = imp.astype(np.float32)
    # (not a constant-amplitude tone: with identical frame energies the median split in spectral_features is a tie
    #  that float32 (numpy/scipy) and float64 (JS) break differently - real audio never has that)
    W["sweep"] = (0.4 * np.sin(2 * np.pi * (700 * t + 500 * np.sin(2 * np.pi * 0.7 * t))) * (0.75 + 0.25 * np.sin(2 * np.pi * 1.3 * t))
                  + rng.standard_normal(24000) * 0.01).astype(np.float32)
    drums = rng.standard_normal(24000) * 0.002
    for c in range(0, 24000, 8000):
        drums[c:c + 1200] += np.exp(-np.arange(1200) / 150.0) * np.sin(2 * np.pi * 90 * np.arange(1200) / SR) * 0.6
    W["periodic_drum"] = drums.astype(np.float32)
    burst = np.zeros(24000, np.float32)
    burst[9000:9400] = rng.standard_normal(400).astype(np.float32) * 0.5
    W["silence_burst"] = burst
    W["short_1s"] = (rng.standard_normal(16000) * 0.1).astype(np.float32)
    # clipped, but not perfectly stationary (spectral flux of a stationary tone is pure float rounding noise)
    clip = np.clip(np.sin(2 * np.pi * 300 * t) * 3.0 * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t)) + rng.standard_normal(24000) * 0.02,
                   -1, 1).astype(np.float32)
    W["clipped"] = clip
    return W


class FakeYam:
    def __init__(self):
        self.next = None

    def analyze(self, x, sr=16000):
        s, e = self.next
        return s.copy(), e.copy()


def push(base, target, heads, blocks):
    """embedding e (1024) so that a linear head over `blocks` copies of e (all frames equal) reaches `target`."""
    mean, std, w, b = heads
    D = len(base)
    g = np.zeros(D, np.float64)
    for k in range(blocks):
        g += w[k * D:(k + 1) * D].astype(np.float64) / std[k * D:(k + 1) * D]
    X = np.tile(base, blocks).astype(np.float32)
    cur = float(((X - mean) / std) @ w + b)
    t = (target - cur) / float(np.abs(g).sum())
    return (base + t * np.sign(g)).astype(np.float32)


def analyze_cases(M, W):
    import acoustic_ai_module
    import audio_intel
    from research_tools import yamnet_detector as Y
    assert acoustic_ai_module.load(), acoustic_ai_module._STATE["error"]
    fake = FakeYam()
    acoustic_ai_module._STATE["model"] = fake
    audio_intel._reset_for_tests()
    assert audio_intel.load()
    rng = np.random.default_rng(11)
    dh = M["dh"]
    base = dh["mean"][:1024].astype(np.float32)              # a typical YAMNet embedding (training mean)
    v1, v2 = M["v1"], M["v2"]

    def h1(cat):
        return v1[f"{cat}__mean"], v1[f"{cat}__std"], v1[f"{cat}__w"], float(v1[f"{cat}__b"][0])

    def h2(cat):
        return v2[f"{cat}__s1__mean"], v2[f"{cat}__s1__std"], v2[f"{cat}__s1__w"], float(v2[f"{cat}__s1__b"][0])
    dhead = dh["mean"], dh["std"], dh["w"], float(dh["b"][0])

    def scores(n, hot=None, level=0.02):
        s = (rng.random((n, 521)) * level).astype(np.float32)
        for idx, v in (hot or {}).items():
            s[:, idx] = v
        return s

    def frames(e, n=3, jitter=0.0):
        # frames differ slightly (x1.000, x1.002, ...): bit-identical frames make argmax a tie that numpy float32 and
        # JS float64 break differently. A scale (not noise) keeps near-zero-variance embedding dims where they are.
        E = np.stack([e * np.float32(1 + 0.002 * k) for k in range(n)]).astype(np.float32)
        if jitter:
            E = np.maximum(E + rng.standard_normal(E.shape).astype(np.float32) * jitter, 0)
        return E
    cases = [
        ("neutral", W["noise"], 16000, scores(3), frames(base, jitter=0.05)),
        ("gunshot_v2", W["impulsive"], 16000, scores(3, {421: 0.9, 422: 0.4, 426: 0.1}), frames(push(base, 14.0, h2("gunshot"), 2))),
        ("explosion_v2", W["impulsive"], 16000, scores(3, {420: 0.8, 430: 0.6}), frames(push(base, 12.0, h2("explosion"), 2))),
        ("glass_v2", W["impulsive"], 16000, scores(3, {437: 0.7, 464: 0.5}), frames(push(base, 9.0, h2("glass_break"), 2))),
        ("alarm_v1", W["am_tone"], 16000, scores(3, {382: 0.8, 393: 0.6}), frames(push(base, 12.0, h1("alarm"), 1))),
        ("impact_low", W["impulsive"], 16000, scores(3), frames(push(base, 11.0, h1("impact"), 1))),
        ("siren_info", W["sweep"], 16000, scores(3, {390: 0.9}), frames(push(base, 12.0, h1("siren"), 1))),
        ("scream_zero_shot", W["noise"], 16000, scores(3, {11: 0.62}), frames(base, jitter=0.02)),
        ("fireworks_zero_shot", W["periodic_drum"], 16000, scores(3, {426: 0.97, 427: 0.5}), frames(base, jitter=0.02)),
        ("distress_head", W["noise"], 16000, scores(3), frames(push(base, 9.0, dhead, 3))),
        ("distress_soft", W["noise"], 16000, scores(3), frames(push(base, 3.6, dhead, 3))),
        ("near_threshold_v1", W["am_tone"], 16000, scores(3), frames(push(base, 7.47, h1("alarm"), 1))),
        ("resampled_48k", np.repeat(W["impulsive"], 3)[:72000] * 0.8, 48000, scores(3, {421: 0.5}),
         frames(push(base, 14.0, h2("gunshot"), 2), jitter=0.03)),
        ("resampled_44k", np.interp(np.arange(66150) * (16000 / 44100), np.arange(24000), W["am_tone"]).astype(np.float32),
         44100, scores(3), frames(base, jitter=0.05)),
        ("short_1s", W["short_1s"], 16000, scores(2), frames(base, n=2, jitter=0.05)),
        ("clipped", W["clipped"], 16000, scores(3), frames(base, jitter=0.05)),
        ("digital_silence", np.zeros(24000, np.float32), 16000, scores(3), frames(base)),
        ("per_frame_varied", W["periodic_drum"], 16000, scores(3, {11: 0.3}),
         np.stack([base, push(base, 10.0, h1("impact"), 1), push(base, 9.0, dhead, 3)]).astype(np.float32)),
    ]
    out = []
    for name, x, sr, s, e in cases:
        x = np.asarray(x, np.float32)
        fake.next = (s, e)
        rep = audio_intel.analyze(x, sr)
        x16 = Y.to_16k(x, sr)
        internals = {"v1_frames": {}, "v2": {}}
        if rep["quality"]["status"] != "unavailable":
            for cat, c in audio_intel._STATE["card"]["categories"].items():
                internals["v1_frames"][cat] = audio_intel._category_frames(cat, c, s, e).tolist()
            import hazard_features as HF
            w = HF.windows(x16)[0][1]
            m2 = audio_intel._load_v2()
            for cat, c in m2["card"]["categories"].items():
                A = m2["arrays"]

                def head(p, X):
                    return float(((np.asarray(X, np.float32) - A[p + "__mean"]) / A[p + "__std"]) @ A[p + "__w"] + A[p + "__b"][0])
                l1 = head(f"{cat}__s1", HF.stage1_vector(e))
                l2 = head(f"{cat}__s2", HF.verifier_vector(l1, s, w)) if c["two_stage"] else None
                internals["v2"][cat] = {"l1": l1, "l2": l2}
        out.append({"name": name, "sr": sr, "audio": ref(x), "scores": ref(s), "emb": ref(e), "n": int(len(e)),
                    "report": jsonable(rep), "internals": jsonable(internals),
                    "events": [ev["category"] for ev in rep.get("events", [])]})
        print(f"  analyze {name:22s} quality={rep['quality']['status']:11s} events={[ev['category'] for ev in rep['events']]}"
              f" distress={(rep.get('distress') or {}).get('detected')}")
    return out


def monitor_cases(reports, rule):
    import hazard_alerts as HA
    import hazard_monitor as HM
    HA.record_alert = lambda *a, **k: None        # never touch the real database (data/)
    rep = {r["name"]: r["report"] for r in reports}

    def soft(score, prob=0.6, scream=0.0):
        return {"model_version": "hazard_audio_v1+hazard_audio_v2.1", "quality": {"status": "ok"}, "events": [],
                "distress": {"detected": False, "score": score, "probability": prob}, "category_scores": {"scream": scream}}
    scenarios = {
        "real_reports_default": {"prefs": dict(HA.DEFAULTS), "can": [True, None], "rule": rule, "steps": [
            {"now": 1000.0, "reports": [rep["gunshot_v2"]]},
            {"now": 1010.0, "reports": [rep["explosion_v2"]]},
            {"now": 1015.0, "reports": [rep["alarm_v1"]]},
            {"now": 1025.0, "reports": [rep["alarm_v1"]]},
            {"now": 1030.0, "reports": [rep["distress_head"]]},
            {"now": 1060.0, "reports": [rep["impact_low"]]},
            {"now": 1070.0, "reports": [rep["siren_info"]]},
            {"now": 1080.0, "close": {"family": "blast", "response": "dismiss"}},
            {"now": 1100.0, "reports": [rep["gunshot_v2"]]},
            {"now": 1200.0, "reports": [rep["glass_v2"], rep["neutral"], rep["scream_zero_shot"]]},
            {"now": 1300.0, "reports": [rep["digital_silence"]]},
            {"now": 1500.0, "close": {"family": "break", "response": "false_alarm"}},
            {"now": 1600.0, "reports": [rep["glass_v2"]]},
            {"now": 1500.0 + HA.COOLDOWN_FALSE_S + 1, "reports": [rep["glass_v2"]]},
        ]},
        "popups_off": {"prefs": dict(HA.DEFAULTS, popups=False), "can": [True, None], "rule": rule, "steps": [
            {"now": 50.0, "reports": [rep["gunshot_v2"]]}]},
        "min_high": {"prefs": dict(HA.DEFAULTS, min_severity="high"), "can": [True, None], "rule": rule, "steps": [
            {"now": 50.0, "reports": [rep["alarm_v1"]]}, {"now": 80.0, "reports": [rep["gunshot_v2"]]}]},
        "min_low": {"prefs": dict(HA.DEFAULTS, min_severity="low"), "can": [True, None], "rule": rule, "steps": [
            {"now": 50.0, "reports": [rep["impact_low"]]}, {"now": 75.0, "reports": [rep["fireworks_zero_shot"]]}]},
        "during_sos": {"prefs": dict(HA.DEFAULTS), "can": [False, "event_active"], "rule": rule, "steps": [
            {"now": 50.0, "reports": [rep["gunshot_v2"]]}]},
        "silent_tracking": {"prefs": dict(HA.DEFAULTS), "can": [False, "silent_tracking"], "rule": rule, "steps": [
            {"now": 50.0, "reports": [rep["distress_head"]]}]},
        "shout_sequence_real_rule": {"prefs": dict(HA.DEFAULTS), "can": [True, None], "rule": rule, "steps": [
            {"now": 100.0, "reports": [soft(rule["t_low"] + 0.3, 0.55)]},
            {"now": 101.5, "reports": [soft(1.0, 0.2, scream=rule["s_low"] + 0.01)]},
            {"now": 103.0, "reports": [soft(rule["t_low"] + 0.1, 0.7)]},
            {"now": 104.5, "reports": [soft(0.5, 0.1)]},
            {"now": 300.0, "reports": [soft(rule["t_low"] + 0.3), soft(rule["t_low"] + 0.2), soft(rule["t_low"] + 0.4)]},
        ]},
        "shout_sequence_spread": {"prefs": dict(HA.DEFAULTS), "can": [True, None], "rule": rule, "steps": [
            {"now": float(t), "reports": [soft(rule["t_low"] + 0.3)]} for t in (0, 30, 60, 90)]},
        "shout_sequence_below": {"prefs": dict(HA.DEFAULTS), "can": [True, None], "rule": rule, "steps": [
            {"now": float(t), "reports": [soft(rule["t_low"] - 0.01)]} for t in (0, 1.5, 3, 4.5)]},
        "no_rule": {"prefs": dict(HA.DEFAULTS), "can": [True, None], "rule": None, "steps": [
            {"now": float(t), "reports": [soft(5.0)]} for t in (0, 1.5, 3)]},
    }
    out = {}
    for name, sc in scenarios.items():
        clock = [0.0]
        mon = HM.HazardMonitor("parity", can_show=lambda sc=sc: tuple(sc["can"]), analyze=None,
                               prefs_fn=lambda sc=sc: dict(sc["prefs"]))
        mon.sequence_rule = sc["rule"]
        mon.manager.clock = lambda: clock[0]
        results = []
        for st in sc["steps"]:
            clock[0] = st["now"]
            if "close" in st:
                a = mon.manager.open.get(st["close"]["family"])
                if a:
                    mon.manager.close(a, st["close"]["response"])
                results.append({"closed": bool(a)})
                continue
            queue = list(st["reports"])
            mon._analyze = lambda x, sr, q=queue: q.pop(0)
            audio = np.zeros(int(SR * HM.WINDOW_S * len(st["reports"])), np.float32)
            res = mon.process(audio, now=st["now"])
            results.append([{"kind": k, "show": s, "why": w, **{f: a[f] for f in (
                "family", "category", "severity", "confidence", "count", "label_he", "explanation", "meta")}}
                for k, a, s, w in res])
        out[name] = {**sc, "results": jsonable(results)}
    return out


def manager_case():
    import hazard_alerts as HA
    clock = [1000.0]
    m = HA.AlertManager(clock=lambda: clock[0])

    def det(cat, sev, conf=0.7):
        return {"category": cat, "severity": sev, "confidence": conf, "label_he": cat, "explanation": "x", "meta": {}}
    steps = [("decide", 0, det("impact", "low")), ("decide", 30, det("glass_break", "medium")),
             ("decide", 10, det("impact", "low", 0.9)), ("decide", 25, det("gunshot", "high")),
             ("close", 0, ("break", "dismiss")), ("decide", 60, det("impact", "low")),
             ("decide", 0, det("glass_break", "high")), ("close", 0, ("blast", "false_alarm")),
             ("decide", HA.COOLDOWN_DISMISS_S + 1, det("gunshot", "high")), ("decide", HA.COOLDOWN_FALSE_S, det("gunshot", "high")),
             ("decide", 5, det("alarm", "medium")), ("decide", HA.MIN_GAP_ANY_S, det("alarm", "medium")),
             ("close", 0, ("signal", "contacts")), ("decide", HA.COOLDOWN_ACTION_S - 1, det("alarm", "medium")),
             ("decide", 2, det("alarm", "medium")), ("decide", HA.EVENT_GAP_S + 1, det("alarm", "medium"))]
    out = []
    for op, dt, arg in steps:
        clock[0] += dt
        if op == "close":
            a = m.open.get(arg[0])
            if a:
                m.close(a, arg[1])
            out.append({"op": "close", "dt": dt, "family": arg[0], "response": arg[1], "closed": bool(a)})
        else:
            k, a = m.decide(arg)
            out.append({"op": "decide", "dt": dt, "det": arg, "kind": k,
                        "alert": None if a is None else {f: a[f] for f in ("family", "category", "severity", "confidence", "count")}})
    return {"start": 1000.0, "steps": out}


def constants():
    import hazard_alerts as HA
    return {"EVENT_GAP_S": HA.EVENT_GAP_S, "COOLDOWN_DISMISS_S": HA.COOLDOWN_DISMISS_S, "COOLDOWN_FALSE_S": HA.COOLDOWN_FALSE_S,
            "COOLDOWN_ACTION_S": HA.COOLDOWN_ACTION_S, "MIN_GAP_ANY_S": HA.MIN_GAP_ANY_S, "SEVERITY_RANK": HA.SEVERITY_RANK,
            "MIN_SEVERITY_OPTIONS": HA.MIN_SEVERITY_OPTIONS, "FAMILY": HA.FAMILY, "FALSE_REASONS": HA.FALSE_REASONS,
            "RESPONSES": list(HA.RESPONSES), "DEFAULTS": HA.DEFAULTS, "STRINGS": HA.STRINGS, "LABELS_EN": HA.LABELS_EN,
            "EXPLAIN_EN": HA.EXPLAIN_EN, "REASONS_EN": HA.REASONS_EN}


def main():
    M = build_heads()
    from research_tools import yamnet_detector as Y
    import hazard_features as HF
    import hazard_alerts as HA
    W = waves()
    fx = {"generated_by": "tools/build_hazard_web.py", "constants": constants()}
    rng = np.random.default_rng(3)
    x48 = (np.sin(2 * np.pi * 1000 * np.arange(72000) / 48000) * 0.5 + rng.standard_normal(72000) * 0.05).astype(np.float32)
    x44 = (np.sin(2 * np.pi * 440 * np.arange(66150) / 44100) * 0.5 + rng.standard_normal(66150) * 0.05).astype(np.float32)
    x22 = (rng.standard_normal(11025) * 0.2).astype(np.float32)
    fx["resample"] = [{"sr": sr, "x": ref(x), "y": ref(Y.to_16k(x, sr))} for sr, x in ((48000, x48), (44100, x44), (22050, x22))]
    fx["spectral"] = []
    for name, x in W.items():
        w = HF.windows(x)[0][1]
        fx["spectral"].append({"name": name, "x": ref(w), "features": [float(v) for v in HF.spectral_features(w)]})
    reports = analyze_cases(M, W)
    fx["analyze"] = reports
    fx["monitor"] = monitor_cases(reports, M["seq"])
    fx["manager"] = manager_case()
    alert = {"category": "gunshot", "label_he": "ירי", "first_ts": 1763200000.0}
    fx["contacts_message"] = {"alert": alert, "he": HA.contacts_message(alert, "he"), "en": HA.contacts_message(alert, "en")}
    fx["blobs"] = BLOBS
    os.makedirs(os.path.dirname(FIX), exist_ok=True)
    with open(FIX, "w", encoding="utf-8") as fh:
        json.dump(jsonable(fx), fh, ensure_ascii=False)
    print(f"wrote {os.path.relpath(FIX, ROOT)} ({os.path.getsize(FIX) / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()

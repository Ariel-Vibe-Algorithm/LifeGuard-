# -*- coding: utf-8 -*-
"""
audio_intel.py - מנוע אירועי סכנה קוליים (Audio Intelligence Engine, v1). רץ במכשיר.

מה הוא עושה: על אותה הקלטה קצרה שכבר נעשית בהערכת הסיכון (האודיו ה*מקורי*, בלי הסרת רעשים - כדי
לא למחוק ירי/שבירה), מעבר YAMNet אחד מחזיר:
- **אירועים** - ירי, פיצוץ, זיקוקים, שבירת זכוכית, סירנה, אזעקה, צרחה, חבטה. לכל קטגוריה נבחר
  (champion/challenger על FSD50K dev) ראש מאומן או ציון YAMNet המקורי, בסף שנקבע ל-~1 התראת שווא
  לשעה, ונמדד על FSD50K eval (research/hazard_audio_v1.md).
- **מצוקה קולית** - הראש הקיים (acoustic_ai_module), מאותם embeddings.
- **איכות קלט** - שקט דיגיטלי (מיקרופון מושתק/תקול), רוויה (clipping), קטע קצר מדי.

אמינות - צירים נפרדים, לא מספר אחד:
- detection_confidence: הסתברות מכוילת (Platt) ש*הצליל* הוא מהקטגוריה, לפי השכיחות ב-FSD50K.
- fa_per_hour_at_score: כמה התראות שווא לשעה נמדדו (על dev) בציון כזה - "כמה נדיר הציון הזה ברעש רגיל".
- severity_prior: מדיניות (לא נלמד): ירי/פיצוץ high, שבירה/אזעקה/צרחה medium, זיקוקים/חבטה low, סירנה info.
- danger_probability: **None** בכוונה. אין לנו נתוני שטח שמאפשרים לומר "בהסתברות X יש סכנה" - ירי
  בטווח, זיקוקים בחתונה וסרט בטלוויזיה נשמעים אותו דבר. זה לא מוסתר: יש reason.
- quality / margin: האם הקלט תקין, וכמה הציון רחוק מהסף (קרוב לסף = לא בטוח).

עקרונות: אופציונלי (בלי מודל -> None, ההערכה ממשיכה כרגיל); לא שומר ולא שולח אודיו; לבדו אף פעם לא
מפעיל SOS (risk_module סופר אותו כאותו מקור ראיה כמו המיקרופון).
"""

import json
import os

import config

VERSION = "audio_intel_v1"
SEVERITY_POINTS = {"high": 3, "medium": 2, "low": 0, "info": 0}
NEAR_THRESHOLD_FRACTION = 0.1     # "קרוב לסף" = בתוך 10% מהטווח שבין הסף לשיא שנמדד בטבלה
DIGITAL_SILENCE_PEAK = 1e-6
CLIP_LEVEL = 0.999
CLIP_RATIO_DEGRADED = 0.01
MIN_SECONDS = 0.96                # מסגרת YAMNet אחת

_STATE = {"loaded": False, "card": None, "heads": None, "error": None}
_ANOM = {"loaded": False, "model": None}
_V2 = {"loaded": False, "model": None}


def _v2_path():
    base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "models", "hazard_audio_v2.npz")
    return p if os.path.exists(p) else None


def _load_v2():
    """מנוע v2 (research_tools/hv2_*.py): רק קטגוריות שעברו את מבחן הלפני/אחרי (hazard_v2_benchmark.md).
    שאר הקטגוריות נשארות v1. בלי קובץ - v1 בלבד."""
    if _V2["loaded"]:
        return _V2["model"]
    _V2["loaded"] = True
    try:
        import numpy as np
        p = _v2_path()
        if p:
            d = np.load(p, allow_pickle=False)
            card = json.loads(str(d["card"][0]))
            _V2["model"] = {"card": card, "arrays": {k: d[k] for k in d.files if k != "card"}}
    except Exception:  # noqa: BLE001 - v2 אופציונלי; כשל = v1
        _V2["model"] = None
    return _V2["model"]


def _v2_window_scores(m, cat, scores, emb, x16):
    """ציון v2 לחלון אחד: שלב 1 ואם צריך שלב 2 (hazard_features). -> (ציון סופי, סף, זוהה, ביטחון)."""
    import numpy as np
    import hazard_features as HF
    c, A = m["card"]["categories"][cat], m["arrays"]

    def head(p, X):
        return float(((np.asarray(X, np.float32) - A[p + "__mean"]) / A[p + "__std"]) @ A[p + "__w"] + A[p + "__b"][0])
    l1 = head(f"{cat}__s1", HF.stage1_vector(emb))
    final, thr, det = l1, c["t1"], l1 >= c["t1"]
    if c["two_stage"]:
        l2 = head(f"{cat}__s2", HF.verifier_vector(l1, scores, x16))
        final, thr = l2, c["t2"]
        det = det and l2 >= c["t2"]
    a, b = c["platt"]
    return final, thr, bool(det), _sigmoid(a * final + b)


def _anomaly_path():
    base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "models", "audio_anomaly.npz")
    return p if os.path.exists(p) else None


def _load_anomaly():
    """גלאי "צליל חריג" (research_tools/audio_anomaly.py). קיים רק אם עמד בקריטריוני הקבלה; אחרת None."""
    if _ANOM["loaded"]:
        return _ANOM["model"]
    _ANOM["loaded"] = True
    try:
        import numpy as np
        path = _anomaly_path()
        if path:
            d = np.load(path, allow_pickle=False)
            card = json.loads(str(d["card"][0]))
            _ANOM["model"] = {"card": card, "mean": d["mean"], "comp": d["comp"], "scale": d["scale"],
                              "bank": d["bank"].astype(np.float32)}
    except Exception:  # noqa: BLE001 - אופציונלי
        _ANOM["model"] = None
    return _ANOM["model"]


def anomaly(emb):
    """ציון "לא דומה לשום צליל יומיומי". מידע לבדיקה בלבד - לא נקודות סיכון ולא SOS. None אם אין מודל."""
    m = _load_anomaly()
    if m is None or emb is None or len(emb) == 0:
        return None
    import numpy as np
    card = m["card"]
    z = ((np.asarray(emb, np.float32) - m["mean"]) @ m["comp"]) / m["scale"]
    if card["method"] == "knn":
        bank = m["bank"]
        d2 = (z * z).sum(1)[:, None] + (bank * bank).sum(1)[None, :] - 2 * z @ bank.T
        k = int(card["hp"]["k"])
        d = np.sqrt(np.maximum(np.sort(d2, axis=1)[:, :k], 0))
        frames = d.mean(1)
    else:
        frames = (z * z).sum(1)
    score = float(frames.max())
    thr = float(card["threshold"])
    return {"model": card["version"], "method": card["method"], "score": round(score, 4),
            "threshold": round(thr, 4), "unusual": score >= thr,
            "note": "חריג = לא דומה לצלילים יומיומיים ולא בהכרח מסוכן; מידע לבדיקה בלבד, לא נכנס לניקוד"}


def _model_path():
    base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "models", "hazard_audio.npz")
    return p if os.path.exists(p) else None


def load():
    if _STATE["loaded"]:
        return _STATE["card"] is not None
    _STATE["loaded"] = True
    try:
        import numpy as np
        path = _model_path()
        if not path:
            raise FileNotFoundError("models/hazard_audio.npz לא נמצא")
        d = np.load(path, allow_pickle=False)
        card = json.loads(str(d["card"][0]))
        heads = {}
        for cat in card["categories"]:
            heads[cat] = (d[f"{cat}__mean"], d[f"{cat}__std"], d[f"{cat}__w"], float(d[f"{cat}__b"][0]))
        _STATE.update(card=card, heads=heads)
        return True
    except Exception as exc:  # noqa: BLE001 - חוסר זמינות אינו תקלה
        _STATE["error"] = str(exc)
        return False


def status():
    if not load():
        return {"available": False, "reason": _STATE["error"]}
    c = _STATE["card"]
    return {"available": True, "version": c["version"], "created": c["created"],
            "categories": sorted(c["categories"]), "unvalidated": sorted(c["unvalidated"])}


def quality(audio, sr):
    """בדיקת קלט - לפני כל מודל. status: ok / degraded / unavailable."""
    import numpy as np
    if audio is None or sr is None:
        return {"status": "unavailable", "reason": "אין הקלטה מהמיקרופון"}
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(x) == 0:
        return {"status": "unavailable", "reason": "ההקלטה ריקה"}
    peak = float(np.abs(x).max())
    rms = float(np.sqrt(np.mean(np.square(x))))
    seconds = len(x) / float(sr)
    clip = float(np.mean(np.abs(x) >= CLIP_LEVEL))
    q = {"seconds": round(seconds, 2), "rms": round(rms, 5), "peak": round(peak, 5), "clip_ratio": round(clip, 4)}
    if peak <= DIGITAL_SILENCE_PEAK:
        q.update(status="unavailable", reason="המיקרופון החזיר שקט דיגיטלי מוחלט - כנראה מושתק, חסום או תקול")
    elif seconds < MIN_SECONDS:
        q.update(status="degraded", reason=f"הקלטה קצרה מדי ({seconds:.2f} ש') לזיהוי אירועים")
    elif clip > CLIP_RATIO_DEGRADED:
        q.update(status="degraded", reason=f"רוויה במיקרופון ({clip:.1%} מהדגימות בתקרה) - צליל חזק מאוד; "
                                           "הסיווג פחות אמין")
    else:
        q.update(status="ok", reason="")
    return q


def _fa_at(table, score):
    """התראות שווא לשעה שנמדדו על dev בסף הגבוה ביותר שעדיין <= הציון (שמרני: קצב גבוה יותר)."""
    rate = None
    for thr, fa in table:
        if score >= thr:
            rate = fa
        else:
            break
    return rate


def _sigmoid(z):
    import math
    z = max(-60.0, min(60.0, float(z)))
    return 1.0 / (1.0 + math.exp(-z))


def _category_frames(cat, c, scores, emb):
    import numpy as np
    if c["champion"] == "head":
        mean, std, w, b = _STATE["heads"][cat]
        return ((np.asarray(emb, np.float32) - mean) / std) @ w + b
    return np.asarray(scores)[:, c["yam_idx"]].max(axis=1)


def analyze(audio, sr, log_callback=None):
    """
    מחזיר dict (ראו docstring של המודול), או None כשהמנוע לא זמין (אין מודל / אין YAMNet).
    שקט דיגיטלי מחזיר דו"ח עם quality.status == "unavailable" ובלי אירועים - כדי שהמערכת תדע שהמיקרופון
    לא עובד ולא תסיק "שקט = בטוח".
    """
    q = quality(audio, sr)
    if not load():
        return None
    import acoustic_ai_module
    yam = acoustic_ai_module.yamnet()
    if yam is None:
        return None
    report = {"engine": VERSION, "model_version": _STATE["card"]["version"], "quality": q, "events": [],
              "suspected_unvalidated": [], "top": [], "distress": None, "danger_probability": None,
              "danger_probability_reason": "לא ניתן לאמוד הסתברות לסכנה מצליל בלבד: אין נתוני שטח מאומתים, "
                                           "וירי / זיקוקים / סרט נשמעים דומה. זיהוי צליל ≠ סכנה."}
    if q["status"] == "unavailable":
        return report
    import numpy as np
    from research_tools import yamnet_detector as Y
    x16 = Y.to_16k(np.asarray(audio, dtype=np.float32).reshape(-1), sr)
    scores, emb = yam.analyze(x16, Y.TARGET_SR)
    if len(emb) == 0:
        return report
    report["distress"] = acoustic_ai_module.analyze_embeddings(emb)
    v2 = _load_v2()
    v2_scores = {}
    if v2:
        # v2 עובד על חלונות של 1.5 ש' (כמו באימון). הקלטה ארוכה יותר -> כל חלון בנפרד, ולוקחים את החזק (רצף זמני)
        import hazard_features as HF
        wins = HF.windows(x16)
        per_win = []
        for _, w in wins:
            ws, we = (scores, emb) if len(wins) == 1 else yam.analyze(w, Y.TARGET_SR)
            per_win.append((ws, we, w))
        for cat in v2["card"]["categories"]:
            best = max((_v2_window_scores(v2, cat, ws, we, w) for ws, we, w in per_win), key=lambda r: r[0] - r[1])
            v2_scores[cat] = best
        if "distress_voice" in v2_scores:
            f, t, det, conf = v2_scores["distress_voice"]
            report["distress"] = {"score": round(f, 3), "detected": det, "probability": round(conf, 4),
                                  "at_seconds": 0.0, "variant": "hazard_v2"}
        report["model_version"] = f"{_STATE['card']['version']}+{v2['card']['version']}"
    card = _STATE["card"]
    hop = card.get("frame_hop_s", Y.HOP_SECONDS)
    rows = []
    for cat, c in card["categories"].items():
        if cat in v2_scores:              # הקטגוריה עברה ל-v2 במבחן לפני/אחרי
            f, t, det, conf = v2_scores[cat]
            rows.append({
                "category": cat, "label_he": c["label_he"], "score": round(f, 4), "threshold": round(t, 4),
                "detected": det, "at_seconds": 0.0, "detection_confidence": round(conf, 4),
                "confidence_note": "מכויל על validation של v2 (FSD50K + SONYC + ירי אמיתי)",
                "fa_per_hour_at_score": None, "severity_prior": c["severity_prior"], "margin": round(f - t, 4),
                "near_threshold": abs(f - t) <= 0.5, "model": "v2_" + ("two_stage" if v2["card"]["categories"][cat]
                                                                       ["two_stage"] else "stage1"),
                "validated_on": "research/hazard_v2_benchmark.md"})
            continue
        frames = _category_frames(cat, c, scores, emb)
        i = int(np.argmax(frames))
        s = float(frames[i])
        thr = float(c["threshold"])
        top_thr = float(c["fa_table"][-1][0]) if c.get("fa_table") else thr
        span = max(1e-6, abs(top_thr - thr))
        a, b = c["platt"]
        rows.append({
            "category": cat, "label_he": c["label_he"], "score": round(s, 4), "threshold": round(thr, 4),
            "detected": s >= thr, "at_seconds": round(i * hop, 2),
            "detection_confidence": round(_sigmoid(a * s + b), 4),
            "confidence_note": "מכויל על FSD50K (שכיחות שם, לא בעולם)",
            "fa_per_hour_at_score": _fa_at(c.get("fa_table") or [], s),
            "severity_prior": c["severity_prior"], "margin": round(s - thr, 4),
            "near_threshold": abs(s - thr) <= NEAR_THRESHOLD_FRACTION * span,
            "model": c["champion"], "validated_on": "FSD50K eval",
        })
    rows.sort(key=lambda r: r["margin"], reverse=True)
    # הציון הרציף לכל קטגוריה - למחקר/כיול (research_tools/danger_audio_external.py); לא נשמר בפנקס (compact)
    report["category_scores"] = {r["category"]: r["score"] for r in rows}
    report["events"] = [r for r in rows if r["detected"]]
    report["top"] = rows[:3]
    report["anomaly"] = anomaly(emb)
    for cat, u in card.get("unvalidated", {}).items():
        s = float(np.asarray(scores)[:, u["yam_idx"]].max())
        if s >= float(u["threshold"]):
            report["suspected_unvalidated"].append({"category": cat, "label_he": u["label_he"], "score": round(s, 4),
                                                    "note": u["note"]})
    return report


def hazard_points(report):
    """נקודות סיכון מאירועים שזוהו (לפי חומרה מוקדמת). 0 אם אין דו"ח, איכות לא זמינה או רק אירועי מידע."""
    if not report or report.get("quality", {}).get("status") == "unavailable":
        return 0
    return max((SEVERITY_POINTS.get(e["severity_prior"], 0) for e in report.get("events", [])), default=0)


def reasons(report):
    """נימוקים קריאים בעברית - כולל אי-ודאות. בלי ניסוחים של "בטוח"/"מסוכן"."""
    out = []
    if not report:
        return out
    q = report.get("quality") or {}
    if q.get("status") in ("unavailable", "degraded") and q.get("reason"):
        out.append(f"איכות הקלט: {q['reason']}")
    for e in report.get("events", []):
        fa = e.get("fa_per_hour_at_score")
        fa_txt = f", ציון כזה נמדד ברעש רגיל ~{fa:.2f} פעמים לשעה" if fa is not None else ""
        near = " (קרוב לסף - לא ודאי)" if e.get("near_threshold") else ""
        out.append(f"זוהה צליל: {e['label_he']} (ביטחון בזיהוי הצליל {e['detection_confidence']:.0%}{fa_txt}){near}")
        if e["category"] == "fireworks":
            out.append("צליל של זיקוקים - לרוב לא ירי, אבל המודל לא מבדיל בוודאות")
        if e["category"] == "siren":
            out.append("סירנה בסביבה - ייתכן שכוחות חירום כבר בקרבת מקום (מידע בלבד, לא מעלה סיכון)")
    for s in report.get("suspected_unvalidated", []):
        out.append(f"חשד לא מאומת: {s['label_he']} (לא נכנס לניקוד)")
    an = report.get("anomaly") or {}
    if an.get("unusual") and not report.get("events"):
        out.append("צליל חריג שלא דומה לצלילים יומיומיים ולא לאף סוג מוכר - לבדיקה בלבד, לא משפיע על הניקוד")
    return out


def compact(report):
    """הגרסה שנשמרת עם האירוע: בלי אודיו ובלי embeddings - רק מטא-דאטה של הזיהוי."""
    if not report:
        return None
    keep = ("engine", "model_version", "quality", "events", "suspected_unvalidated", "top", "danger_probability",
            "anomaly")
    out = {k: report.get(k) for k in keep}
    d = report.get("distress")
    out["distress"] = None if not d else {k: d.get(k) for k in ("score", "detected", "probability")}
    return out


def _reset_for_tests():
    _STATE.update(loaded=False, card=None, heads=None, error=None)
    _ANOM.update(loaded=False, model=None)
    _V2.update(loaded=False, model=None)

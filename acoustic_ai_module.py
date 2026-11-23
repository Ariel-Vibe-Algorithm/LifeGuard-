# -*- coding: utf-8 -*-
"""
acoustic_ai_module.py - זיהוי צלילי מצוקה (צרחה/צעקה/בכי) במכשיר, כחלק מהערכת הסיכון.

YAMNet (TFLite) -> embedding לכל ~0.5 שנ' -> ראש מסווג קטן שאומן ונמדד על FSD50K
(research_tools/distress_experiments.py; התוצאות ב-research/acoustic_model_improvement*.md).

עקרונות:
- אופציונלי: אם הספרייה/המודלים חסרים - מחזיר None והערכת הסיכון ממשיכה כרגיל (עוצמת רעש).
  לעולם לא "ממציא" זיהוי.
- רץ על אותה הקלטה שכבר נעשית למדידת הרעש (המיקרופון לא נפתח פעמיים).
- זה *ראיה*, לא החלטה: המודל נספר כאותו מקור כמו המיקרופון, ולכן לבדו לעולם לא מפעיל SOS אוטומטי.
- האודיו לא נשמר ולא נשלח - רק ההסתברות.
"""

import json
import os

import config

_STATE = {"loaded": False, "model": None, "head": None, "info": None, "error": None}


def _paths():
    base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    cands_yam = [os.path.join(base, "models", "yamnet_float_embeddings.tflite"),
                 os.path.join(base, "research", "models", "yamnet", "yamnet_float_embeddings.tflite")]
    cands_head = [os.path.join(base, "models", "distress_head.npz"),
                  os.path.join(base, "research", "models", "distress_head.npz")]
    pick = lambda c: next((p for p in c if os.path.exists(p)), None)  # noqa: E731
    return pick(cands_yam), pick(cands_head)


def load():
    """טוען פעם אחת. מחזיר True אם הזיהוי זמין."""
    if _STATE["loaded"]:
        return _STATE["model"] is not None
    _STATE["loaded"] = True
    try:
        yam, head = _paths()
        if not yam or not head:
            raise FileNotFoundError("קובצי המודל לא נמצאו (models/ או research/models/)")
        from research_tools import yamnet_detector as Y
        import numpy as np
        d = np.load(head, allow_pickle=False)
        info = json.loads(str(d["info"][0]))
        clf = Y.MLPModel.load(head) if "W1" in d.files else Y.LogisticModel.load(head)
        _STATE.update(model=Y.Yamnet(model_path=yam), head=clf, info=info, Y=Y)
        return True
    except Exception as exc:  # noqa: BLE001 - חוסר בזמינות אינו תקלה; מדווח בנימוקים
        _STATE["error"] = str(exc)
        return False


def status():
    load()
    if _STATE["model"] is None:
        return {"available": False, "reason": _STATE["error"]}
    i = _STATE["info"] or {}
    return {"available": True, "variant": i.get("variant"), "threshold_logit": i.get("threshold_logit"),
            "trained_on": i.get("trained_on")}


def analyze(audio, sr):
    """
    audio: מונו float32. מחזיר dict או None כשלא זמין:
      score      - logit מקסימלי (אחרי החלקה) לאורך ההקלטה
      detected   - score >= הסף שכויל (נקודת עבודה של ~1 התראת שווא לשעה על FSD50K)
      probability, at_seconds
    """
    if not load() or audio is None or len(audio) == 0:
        return None
    import numpy as np
    _, emb = _STATE["model"].analyze(np.asarray(audio, dtype=np.float32).reshape(-1), sr)
    return analyze_embeddings(emb)


def yamnet():
    """מודל YAMNet הטעון (משותף עם audio_intel - מעבר אחד על האודיו לשני המודלים). None אם לא זמין."""
    return _STATE["model"] if load() else None


def analyze_embeddings(emb):
    """כמו analyze, אבל מ-embeddings של YAMNet שכבר חושבו. None אם הראש לא זמין או אין מסגרות."""
    if not load() or emb is None or len(emb) == 0:
        return None
    import numpy as np
    Y, info = _STATE["Y"], _STATE["info"] or {}
    x = Y.context_features(emb) if info.get("context") else emb
    s = Y.smooth_scores(_STATE["head"].decision_function(x), int(info.get("smooth", 1)))
    i = int(np.argmax(s))
    score = float(s[i])
    return {"score": round(score, 3), "detected": score >= threshold(),
            "probability": round(float(Y._sigmoid(score)), 4), "at_seconds": round(i * Y.HOP_SECONDS, 2)}


def threshold():
    """סף על ה-logit, שנקבע בהערכה (לא ניחוש). ברירת מחדל שמרנית אם אין בכרטיס המודל."""
    load()
    return float((_STATE["info"] or {}).get("threshold_logit", getattr(config, "ACOUSTIC_AI_THRESHOLD_LOGIT", 6.0)))


def _reset_for_tests():
    _STATE.update(loaded=False, model=None, head=None, info=None, error=None)

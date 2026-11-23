# -*- coding: utf-8 -*-
"""
risk_feedback.py - "ביטחון בזיהוי" נפרד מ"חומרת הסכנה", ולמידה ממשוב מאומת.

שני צירים שונים:
- **חומרה (level/score, risk_module)**: כמה מסוכן המצב *אם* הוא אמיתי. נקבעת בכללים שקופים ולא משתנה
  מלמידה - כדי שאף משוב לא "ילמד" את המערכת להתעלם מצעקה + מילות מצוקה.
- **ביטחון (confidence, כאן)**: כמה סביר שזה *באמת* אירוע ולא התראת שווא, לפי אילו מקורות הופעלו
  ולפי מה שקרה בפעם הקודמת שהם הופעלו אצל אותו משתמש/ת.

למידה: לכל מקור ראיה התפלגות Beta(α, β) על "כשהמקור הזה הופעל, האירוע היה אמיתי". מתחילים מ-prior
שמרני (PRIOR_PRECISION, בעוצמה של PRIOR_STRENGTH אירועים מדומים) ומעדכנים רק מדירוג שהמשתמש/ת נתן/ה
אחרי האירוע (אמת / הרגשתי בסכנה / שווא). השילוב בין מקורות: noisy-OR (כל מקור הוא הזדמנות בלתי תלויה
לכך שהאירוע אמיתי), עם תקרה 0.98 - אף פעם לא "ודאות".

מגבלות שנאמרות בגלוי:
- המקורות לא באמת בלתי תלויים (רעש חזק ומודל AI מאותו מיקרופון) - לכן מודל ה-AI והרעש מקבלים
  משקל של מקור אחד: במיקרופון נלקח המקסימום ולא שניהם.
- מעט משוב = הערכה קרובה ל-prior. עד 5 דירוגים למקור מוצג "ביטחון ראשוני (מעט משוב)".
- הקובץ מקומי, למשתמש/ת בלבד; לא נשלח לשרת. השרת מקבל רק את המספר הסופי.
"""

import json
import os
import threading

import config

# precision ראשוני: מתוך האירועים שבהם המקור הופעל, כמה היו אמיתיים. ערכים שמרניים - מבוססים על ספרות
# ועל ההערכה במחקר (research/acoustic_model_holdout.md: ה-AI ב-~1 התראת שווא לשעה) ולא על "הבטחה".
PRIOR_PRECISION = {
    "manual": 0.85,          # לחיצה/קיצור/פקודה קולית מפורשת - המשתמש/ת אמר/ה "אני בסכנה"
    "wearable": 0.85,
    "speech_keyword": 0.45,  # מילת מצוקה בתמלול (גם "הצילו" בסרט)
    "personal_help": 0.6,    # "הצילו" בקול של המשתמש/ת עצמו/ה (מודל אישי)
    "acoustic_ai": 0.3,
    "acoustic_event": 0.25,  # ירי/פיצוץ/שבירה/אזעקה (audio_intel) - זיקוקים וסרטים נשמעים דומה
    "loud_noise": 0.12,
    "motion_sensor": 0.35,
    "retrigger": 0.5,
}
PRIOR_STRENGTH = 10.0
MAX_CONFIDENCE = 0.98
FEW_FEEDBACK = 5
LABELS = {"true_event": 1, "felt_danger": 1, "false_alarm": 0}
# hazard_alert: המשתמש/ת אישר/ה SOS מתוך התראת AI - פעולה ידנית מפורשת (ה-AI רק הציע)
MANUAL_TRIGGERS = ("button", "hotkey", "panic_button", "voice", "manual", "silent_voice", "wearable", "hazard_alert")

_lock = threading.Lock()


def _path(username):
    safe = "".join(c for c in str(username or "anon") if c.isalnum() or c in "-_") or "anon"
    d = os.path.join(config.DATA_DIR, "risk_feedback")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{safe}.json")


def load(username):
    try:
        with open(_path(username), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) and isinstance(d.get("sources"), dict) else {"sources": {}, "events": 0}
    except (OSError, ValueError):
        return {"sources": {}, "events": 0}


def fired_sources(result, trigger=None):
    """הערכת risk_module (+ מקור ההפעלה) -> רשימת המקורות שהופעלו."""
    out = []
    if trigger in MANUAL_TRIGGERS:
        out.append("manual")
    result = result or {}
    ai = result.get("acoustic_ai") or {}
    noise = result.get("noise_level")
    events = [e for e in ((result.get("audio_intel") or {}).get("events") or [])
              if e.get("severity_prior") in ("high", "medium")]
    if ai.get("detected"):
        out.append("acoustic_ai")
    elif events:                      # אותו מיקרופון: מקור אחד בלבד (המקסימום), לא שלושה
        out.append("acoustic_event")
    elif noise is not None and noise >= config.LOUD_NOISE_RMS_THRESHOLD:
        out.append("loud_noise")
    kws = result.get("keywords_found") or []
    if any("זוהה בקול שלך" in k for k in kws):
        out.append("personal_help")
    if any("זוהה בקול שלך" not in k for k in kws):
        out.append("speech_keyword")
    srcs = set(result.get("independent_sources") or [])
    if "motion_sensor" in srcs:
        out.append("motion_sensor")
    if "wearable" in srcs:
        out.append("wearable")
    if "retrigger" in srcs:
        out.append("retrigger")
    return out


def reliability(stats, source):
    p = PRIOR_PRECISION.get(source, 0.3)
    s = (stats.get("sources") or {}).get(source) or {}
    a = p * PRIOR_STRENGTH + float(s.get("true", 0))
    b = (1 - p) * PRIOR_STRENGTH + float(s.get("false", 0))
    return a / (a + b), int(s.get("true", 0)) + int(s.get("false", 0))


def confidence(result, trigger=None, username=None, stats=None):
    """-> {"value": 0..1, "label", "sources": [...], "explanation": [...], "learned_from": n}"""
    stats = stats if stats is not None else load(username)
    sources = fired_sources(result, trigger)
    if not sources:
        return {"value": None, "label": "לא ידוע", "sources": [],
                "explanation": ["אין מקור ראיה פעיל - אין בסיס להערכת ביטחון"], "learned_from": 0}
    miss, expl, n_min = 1.0, [], None
    for s in sources:
        r, n = reliability(stats, s)
        miss *= (1 - r)
        n_min = n if n_min is None else min(n_min, n)
        expl.append(f"{_SOURCE_HE.get(s, s)}: {r:.0%}" + (f" (נלמד מ-{n} דירוגים)" if n else " (ערך התחלתי)"))
    value = min(MAX_CONFIDENCE, 1 - miss)
    label = "גבוה" if value >= 0.8 else ("בינוני" if value >= 0.5 else "נמוך")
    if (n_min or 0) < FEW_FEEDBACK:
        expl.append("ביטחון ראשוני - עדיין מעט משוב מאירועים קודמים")
    return {"value": round(value, 3), "label": label, "sources": sources, "explanation": expl,
            "learned_from": int(stats.get("events", 0))}


def record(username, sources, rating):
    """דירוג אחרי אירוע -> עדכון. מחזיר False אם הדירוג לא מתאים ללמידה."""
    y = LABELS.get(rating)
    if y is None or not sources:
        return False
    with _lock:
        stats = load(username)
        for s in dict.fromkeys(sources):
            if s not in PRIOR_PRECISION:
                continue
            e = stats["sources"].setdefault(s, {"true": 0, "false": 0})
            e["true" if y else "false"] += 1
        stats["events"] = int(stats.get("events", 0)) + 1
        tmp = _path(username) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stats, fh)
        os.replace(tmp, _path(username))
    return True


_SOURCE_HE = {"manual": "הפעלה ידנית", "wearable": "לחצן לביש", "speech_keyword": "מילות מצוקה",
              "personal_help": "\"הצילו\" בקול שלך", "acoustic_ai": "מודל צלילי מצוקה",
              "acoustic_event": "מודל אירועי סכנה (ירי / פיצוץ / שבירה / אזעקה)", "loud_noise": "רעש חזק",
              "motion_sensor": "חיישן תנועה", "retrigger": "הפעלה חוזרת"}

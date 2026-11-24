# -*- coding: utf-8 -*-
"""
dispatch.py - תור עדיפויות לפנייה למתנדבים לפי רמת הסכנה.

הבעיה: כמה אירועים פעילים בו-זמנית מתחרים על אותם מתנדבים קרובים. בלי תור, מי שנפתח ראשון
"תופס" את המתנדב גם אם אירוע אחר מסוכן יותר.

האלגוריתם (פונקציות טהורות - נבדקות ישירות ב-tests/test_dispatch_queue.py):
1. ציון עדיפות לכל אירוע שצריך מתנדבים:
   - רמת סכנה מההערכה במכשיר (HIGH/MEDIUM/LOW/UNKNOWN) + הניקוד המספרי שלה
   - סוג החירום (אלימות/ביטחון/רפואי/אש גבוהים יותר) וחומרה
   - מצוקה שקטה (+): ייתכן שהאדם לא יכול לדבר
   - אין אנשי קשר מחוברים (+): המתנדבים הם העזרה היחידה
   - הזדקנות (aging): כל 10 שניות המתנה מוסיפות נקודה, עד 45 - אירוע "נמוך" לא נדחק לנצח
2. פריסה (fan-out) לפי רמת הסכנה: בסכנה גבוהה פונים לכמה מתנדבים במקביל (הראשון שמקבל - מטפל),
   בסכנה נמוכה - אחד אחד, כדי לא "לשרוף" מתנדבים.
3. מעבר בתור מהעדיפות הגבוהה לנמוכה. מתנדב שכבר עסוק (קיבל אירוע, או ממתין לתשובתו) לא מוצע
   לאירוע נוסף - אלא אם האירוע החדש דחוף משמעותית (PREEMPT_MARGIN) והמתנדב עוד *לא קיבל*
   את הקודם: אז ההצעה הקודמת מועברת, והאירוע הקודם יחזור לתור ויקבל מתנדב אחר.
   מתנדב שכבר קיבל אירוע לעולם לא נלקח ממנו.
"""

LEVELS = ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
LEVEL_BASE = {"HIGH": 70, "MEDIUM": 45, "LOW": 25, "UNKNOWN": 40}
SCORE_SCALE = 8.0            # סקאלת הניקוד של risk_module (כמו max_score במד)
TYPE_WEIGHT = {"physical_danger": 15, "security": 15, "medical": 12, "fire": 12, "earthquake": 8,
               "road_accident": 8, "missing_person": 6, "lost": 3, "flood_weather": 5, "power_outage": 0,
               "unknown": 4}
SEVERITY_WEIGHT = {"critical": 15, "high": 10, "medium": 5, "low": 0, "unknown": 0}
SILENT_BONUS = 10
NO_CONTACTS_BONUS = 5
AGING_SECONDS_PER_POINT = 10.0
AGING_MAX = 45               # ~7.5 דקות המתנה: גם אירוע "נמוך" עוקף בסוף אירוע "בינוני" חדש
FANOUT = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNKNOWN": 1, None: 1}
PREEMPT_MARGIN = 20
CONTEXT_POINTS = 6           # לכל נקודת הקשר (לילה / רחוב חשוך) - רק לאירוע בלי הערכה מהמכשיר
CONFIDENCE_POINTS = 10       # ביטחון בזיהוי 0..1 -> -5..+5 (ציר נפרד מהחומרה)
SIGNAL_SOURCES = ("manual", "wearable", "speech_keyword", "personal_help", "acoustic_ai", "loud_noise",
                  "motion_sensor", "retrigger", "fall", "impact", "struggle", "sudden_stop", "no_signal",
                  "no_response")

BANDS = ((85, "critical", "קריטי"), (65, "high", "גבוה"), (45, "medium", "בינוני"), (0, "low", "נמוך"))


def clean_risk(risk):
    """קלט מהמכשיר -> (level, score, reasons) מאומתים. None אם אין הערכה."""
    if not isinstance(risk, dict):
        return None
    level = str(risk.get("level") or "").upper()
    if level not in LEVELS:
        return None
    score = risk.get("score")
    try:
        score = None if score is None else max(0.0, min(float(score), SCORE_SCALE * 2))
    except (TypeError, ValueError):
        score = None
    reasons = [str(r)[:200] for r in (risk.get("reasons") or []) if r][:8]
    return level, score, reasons


def clean_confidence(risk):
    """ביטחון בזיהוי מהמכשיר: מספר 0..1 או None (לא ניחוש)."""
    try:
        c = float((risk or {}).get("confidence"))
    except (TypeError, ValueError, AttributeError):
        return None
    return round(min(1.0, max(0.0, c)), 3) if c == c else None


def clean_signals(risk):
    out = []
    for s in (((risk or {}).get("signals") or [])[:8] if isinstance(risk, dict) else []):
        if not isinstance(s, dict) or s.get("source") not in SIGNAL_SOURCES:
            continue
        item = {"source": s["source"]}
        for k in ("confidence", "at"):
            try:
                if s.get(k) is not None:
                    item[k] = round(float(s[k]), 3)
            except (TypeError, ValueError):
                pass
        out.append(item)
    return out


def priority(level, score, emergency_type, severity, mode, has_contacts, waited_seconds):
    p = LEVEL_BASE.get(level or "UNKNOWN", LEVEL_BASE["UNKNOWN"])
    if score is not None:
        p += min(score, SCORE_SCALE) / SCORE_SCALE * 16
    p += TYPE_WEIGHT.get(emergency_type, 0)
    p += SEVERITY_WEIGHT.get(severity, 0)
    if mode == "silent":
        p += SILENT_BONUS
    if not has_contacts:
        p += NO_CONTACTS_BONUS
    p += min(AGING_MAX, max(0.0, waited_seconds) / AGING_SECONDS_PER_POINT)
    return round(p, 2)


def fanout(level):
    return FANOUT.get(level, 1)


def band(p):
    for threshold, key, label in BANDS:
        if p >= threshold:
            return key, label
    return BANDS[-1][1:]


def plan(waiting):
    """waiting: [(priority, incident_id, slots)] -> סדר הטיפול (עדיפות יורדת; שוויון -> הוותיק קודם לפי id
    שמגיע ממוין לפי created_at מבחוץ). מחזיר רשימת (incident_id, slots)."""
    order = sorted(enumerate(waiting), key=lambda t: (-t[1][0], t[0]))
    return [(w[1], w[2]) for _, w in order]

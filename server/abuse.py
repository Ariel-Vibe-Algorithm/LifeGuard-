# -*- coding: utf-8 -*-
"""
abuse.py - הגנה על תור המתנדבים מניצול לרעה.

הבעיה: רמת הסכנה מגיעה מהמכשיר. מי שרוצה "להעסיק" מתנדבים (או להטריד אותם) יכול לשלוח תמיד
HIGH, לפתוח ולבטל אירועים, ולמשוך עוד ועוד מתנדבים.

עיקרון-על: **לעולם לא חוסמים חירום.** אנשי הקשר תמיד מקבלים, ותמיד נשלח לפחות מתנדב אחד (אם יש).
מה שמוגבל הוא רק *היתרונות*: עדיפות גבוהה בתור, פנייה לכמה מתנדבים במקביל, והעברת מתנדב
מאירוע אחר - וזאת רק לחשבון עם דפוס חשוד בזמן האחרון, ובאופן שקוף (נרשם בציר הזמן).

אותות (7 ימים אחרונים):
- ביטולים מהירים: אירוע שבוטל תוך QUICK_CANCEL_SECONDS אחרי שכבר הוזעקו מתנדבים
- דיווחי מתנדבים "התראת שווא/ניצול" ממתנדבים שונים
מגבלה נוספת: כמה מתנדבים הוזעקו עבור אותו משתמש בשעה האחרונה (מניעת הטרדה המונית).
"""

WINDOW_SECONDS = 7 * 86400
QUICK_CANCEL_SECONDS = 120
QUICK_CANCELS_TO_LIMIT = 3
REPORTS_TO_LIMIT = 2
VOLUNTEERS_PER_HOUR_CAP = 15
REPORT_KINDS = ("false_alarm", "abusive")


MAX_ACTIVE_WITH_VOLUNTEERS = 2      # אירועים פעילים בו-זמנית שמקבלים מתנדבים (השאר: אנשי קשר בלבד)
CREATED_PER_HOUR_TO_LIMIT = 6


NEW_ACCOUNT_HOURS = 24


def assess(quick_cancels, distinct_reporters, volunteers_last_hour, created_last_hour=0, account_age_hours=None):
    """-> dict: limited (בלי יתרונות עדיפות), capped (לא מוזעקים מתנדבים נוספים כרגע), reasons."""
    reasons = []
    limited = False
    if account_age_hours is not None and account_age_hours < NEW_ACCOUNT_HOURS:
        # ריבוי חשבונות חדשים "תפס" את כל המתנדבים (סקירה 2). חשבון חדש מקבל עזרה רגילה - מתנדב אחד
        # לכל אירוע - אבל בלי עדיפות גבוהה, פנייה מקבילה או לקיחת מתנדב מאירוע של מישהו אחר.
        limited = True
        reasons.append("חשבון חדש (פחות מיממה)")
    if created_last_hour > CREATED_PER_HOUR_TO_LIMIT:
        limited = True
        reasons.append(f"נפתחו {created_last_hour} אירועים בשעה האחרונה")
    if quick_cancels >= QUICK_CANCELS_TO_LIMIT:
        limited = True
        reasons.append(f"{quick_cancels} אירועים בוטלו תוך דקות אחרי שהוזעקו מתנדבים (7 ימים)")
    if distinct_reporters >= REPORTS_TO_LIMIT:
        limited = True
        reasons.append(f"{distinct_reporters} מתנדבים דיווחו על התראות שווא (7 ימים)")
    capped = volunteers_last_hour >= VOLUNTEERS_PER_HOUR_CAP
    if capped:
        reasons.append(f"הוזעקו כבר {volunteers_last_hour} מתנדבים בשעה האחרונה")
    return {"limited": limited, "capped": capped, "reasons": reasons,
            "remaining_this_hour": max(0, VOLUNTEERS_PER_HOUR_CAP - volunteers_last_hour)}


def allowed_slots(wanted, already_notified_for_incident, ab):
    """כמה מתנדבים מותר להזעיק עכשיו לאירוע: לא יותר מהמכסה השעתית שנותרה - אבל אירוע שעוד לא
    הוזעק אליו אף מתנדב מקבל תמיד לפחות אחד (חירום לא נחסם)."""
    slots = min(wanted, ab["remaining_this_hour"])
    if already_notified_for_incident == 0:
        slots = max(slots, 1 if wanted > 0 else 0)
    return slots


def effective_level(level, limited):
    """חשבון מוגבל: הרמה שהמכשיר דיווח לא מקבלת משקל בתור (מוצגת למגיבים כפי שהיא, עם הערה)."""
    return "LOW" if limited else level

# -*- coding: utf-8 -*-
"""
hazard_alerts.py - התראות אינטראקטיביות על סכנה שה-AI זיהה: העדפות, ניהול חכם (בלי הצפה) ותיעוד תגובות.

עקרונות בטיחות:
- זיהוי AI לעולם לא מפעיל SOS ולא פונה לשירותי חירום בעצמו - רק מציע. כל פעולה משמעותית דורשת אישור מפורש.
- כיבוי החלונות הקופצים לא מכבה את הזיהוי: הוא ממשיך לרוץ ונרשם (בלי חלון). התראות קריטיות של האפליקציה
  (ספירה לאחור של SOS, "הכל בסדר?" אחרי נפילה, קריאות מאנשי הקשר) לא תלויות בהגדרה הזו.
- במעקב שקט / SOS פעיל / מצב כיבוי מדומה - שום חלון לא קופץ (נרשם בלבד).
- "התראת שווא" נרשמת כמשוב משתמש/ת **לא מאומת**. היא לא משנה ספים מיד; היא נכנסת לפנקס ההערכות
  (assessment_ledger) לחקירת טעויות ולאימון עתידי בפיקוח.
- לא נשמר אודיו ולא מיקום - רק מטא-דאטה של הזיהוי (קטגוריה, ציון, סף, גרסת מודל, איכות קלט).

ניהול חכם:
- זיהויים מאותה "משפחה" (פיצוץ/ירי/זיקוקים, קול מצוקה, שבירה/חבטה, אות התרעה) בתוך EVENT_GAP_S = אותו אירוע:
  לא חלון חדש; אם החומרה עלתה - החלון הקיים מתעדכן.
- אחרי "סגירה" - שקט COOLDOWN_DISMISS_S לאותה משפחה; אחרי "התראת שווא" - COOLDOWN_FALSE_S.
  זיהוי בחומרה גבוהה יותר מזו שנסגרה עוקף את השקט.
- משפחה אחרת = אירוע עצמאי חדש (אבל לא יותר מחלון אחד כל MIN_GAP_ANY_S, אלא אם החומרה גבוהה יותר).
"""
import json
import threading
import time
import uuid

EVENT_GAP_S = 120
COOLDOWN_DISMISS_S = 5 * 60
COOLDOWN_FALSE_S = 15 * 60
COOLDOWN_ACTION_S = 10 * 60
MIN_GAP_ANY_S = 20

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3}
MIN_SEVERITY_OPTIONS = {"high": "רק סכנה גבוהה", "medium": "בינונית ומעלה (מומלץ)", "low": "כל זיהוי"}
FAMILY = {"gunshot": "blast", "explosion": "blast", "fireworks": "blast", "scream": "voice",
          "distress_voice": "voice", "glass_break": "break", "impact": "break", "alarm": "signal", "siren": "signal"}
FALSE_REASONS = {"music_tv": "מוזיקה / טלוויזיה", "fireworks": "זיקוקים", "horn": "צופר רכב",
                 "construction": "עבודות / בנייה", "other": "אחר"}
RESPONSES = ("sos", "safe_place", "contacts", "call", "false_alarm", "dismiss")
DEFAULTS = {"popups": True, "sound": True, "vibration": False, "min_severity": "medium", "language": "he"}
_KEY = "hazard_alert_prefs"

# מחרוזות ממשק - עברית (RTL) ואנגלית (LTR)
STRINGS = {
    "he": {"title": "LifeGuard זיהה סכנה אפשרית", "question": "מה תרצה/י לעשות?", "sos": "🚨 הפעלת SOS",
           "safe_place": "📍 מקום בטוח", "contacts": "👨‍👩‍👧 התראה לאנשי הקשר", "call": "📞 חיוג לחירום",
           "false_alarm": "התראת שווא", "dismiss": "סגירה", "severity": "חומרה", "confidence": "ביטחון בזיהוי",
           "location": "מיקום", "no_location": "מיקום לא ידוע", "updated": "עודכן - זוהה שוב",
           "sev": {"high": "גבוהה", "medium": "בינונית", "low": "נמוכה", "info": "מידע"},
           "confirm_sos": "להפעיל עכשיו SOS? (ספירה לאחור עם אפשרות ביטול, כמו לחיצה על הכפתור)",
           "confirm_contacts": "לשלוח לאנשי הקשר הודעה שזוהתה סכנה אפשרית? ההודעה תציין שזה זיהוי AI שלא אומת.",
           "confirm_call": "לפתוח חיוג ל-{n}?", "why": "למה זו התראת שווא? (לא חובה)",
           "not_verified": "זיהוי של צליל בלבד - לא אישור לסכנה", "dir": "rtl",
           "not_connected": "לא מחובר לשרת", "sending": "שולח...", "approx": "משוער", "min_ago": "לפני {m} דק'",
           "no_contacts": "אין אנשי קשר מאושרים במעגל הביטחון - ההודעה לא נשלחה לאף אחד",
           "sent": "השרת שלח ל-{n} אנשי קשר. ממתין לאישור קבלה באפליקציות...",
           "delivered": "{ok} מתוך {n} אישרו קבלה באפליקציה", "send_failed": "❌ ההודעה לא נשלחה: {e}",
           "dial_opened": "נפתחה אפליקציית החיוג ל-{n} (אם מקושר טלפון). אם לא - חייגו מהטלפון.",
           "no_dialer": "אין במחשב הזה אפליקציית חיוג. חייגו {n} מהטלפון."},
    "en": {"title": "LifeGuard detected a potential danger", "question": "What would you like to do?",
           "sos": "🚨 Activate SOS", "safe_place": "📍 Find a safe place", "contacts": "👨‍👩‍👧 Alert contacts",
           "call": "📞 Call emergency", "false_alarm": "False alarm", "dismiss": "Dismiss", "severity": "Severity",
           "confidence": "Detection confidence", "location": "Location", "no_location": "Location unknown",
           "updated": "Updated - detected again",
           "sev": {"high": "High", "medium": "Medium", "low": "Low", "info": "Info"},
           "confirm_sos": "Start SOS now? (countdown with cancel, same as pressing the button)",
           "confirm_contacts": "Send your contacts a message that a potential danger was detected? It will say this "
                               "is an unverified AI detection.",
           "confirm_call": "Open the dialer for {n}?", "why": "Why is this a false alarm? (optional)",
           "not_verified": "Sound-only detection - not a confirmation of danger", "dir": "ltr",
           "not_connected": "not connected to server", "sending": "Sending...", "approx": "approximate",
           "min_ago": "{m} min ago", "no_contacts": "No approved contacts in your safety circle - nothing was sent",
           "sent": "The server sent it to {n} contacts. Waiting for their apps to confirm receipt...",
           "delivered": "{ok} of {n} confirmed receipt in the app", "send_failed": "❌ Not sent: {e}",
           "dial_opened": "Opened the dialer for {n} (if a phone is linked). Otherwise dial from your phone.",
           "no_dialer": "This computer has no dialer app. Dial {n} from your phone."},
}
LABELS_EN = {"gunshot": "Gunshot", "explosion": "Explosion", "fireworks": "Fireworks", "glass_break": "Glass breaking",
             "siren": "Siren", "alarm": "Alarm", "scream": "Scream", "impact": "Heavy impact",
             "distress_voice": "Distress voice (scream / shout / crying)"}
EXPLAIN_EN = {"gunshot": "A sound similar to a gunshot. Fireworks and drums can sound alike.",
              "explosion": "A sound similar to an explosion. Fireworks and thunder can sound alike.",
              "alarm": "An alarm sound. Car horns are sometimes detected as alarms.",
              "scream": "A sound similar to a scream. Happy shouting and play sound alike.",
              "distress_voice": "Sounds like screaming, shouting or crying. Happy shouting and play sound alike.",
              "glass_break": "A glass-breaking sound.", "impact": "A heavy impact."}
REASONS_EN = {"music_tv": "Music / TV", "fireworks": "Fireworks", "horn": "Vehicle horn",
              "construction": "Construction", "other": "Other"}


# ================================================================= העדפות
def load_prefs(username):
    try:
        import incident_store
        import storage
        raw = incident_store.get_setting(username, _KEY, None)
        if raw:
            d = json.loads(storage.unprotect_text(raw))
            return {**DEFAULTS, **{k: v for k, v in d.items() if k in DEFAULTS}}
    except Exception:  # noqa: BLE001 - העדפות לא זמינות = ברירות המחדל (חלונות מופעלים)
        pass
    return dict(DEFAULTS)


def save_prefs(username, prefs):
    import incident_store
    import storage
    clean = {**DEFAULTS, **{k: v for k, v in (prefs or {}).items() if k in DEFAULTS}}
    if clean["min_severity"] not in MIN_SEVERITY_OPTIONS:
        raise ValueError("רמת חומרה לא מוכרת")
    if clean["language"] not in STRINGS:
        raise ValueError("שפה לא נתמכת")
    clean["vibration"] = False                       # אין רטט ב-Windows - לא שומרים ערך שלא מתבצע
    incident_store.set_setting(username, _KEY, storage.protect_text(json.dumps(clean)))
    return clean


def severity_allowed(severity, prefs):
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK[prefs.get("min_severity", "medium")]


# ================================================================= ניהול חכם
class AlertManager:
    """מחליט לכל זיהוי: new / update / grouped / cooldown / rate_limited. בלי Tk - נבדק לבד."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.open = {}             # family -> alert
        self.cooldown = {}         # family -> (until, severity_rank)
        self.last_popup = 0.0
        self._lock = threading.Lock()

    def decide(self, det):
        """det: {category, severity, confidence, label_he, explanation, ...} -> (kind, alert)."""
        now = self.clock()
        fam = FAMILY.get(det["category"], det["category"])
        rank = SEVERITY_RANK.get(det["severity"], 0)
        with self._lock:
            a = self.open.get(fam)
            if a and now - a["last_ts"] <= EVENT_GAP_S:
                a["count"] += 1
                a["last_ts"] = now
                if rank > SEVERITY_RANK.get(a["severity"], 0):
                    a.update(category=det["category"], severity=det["severity"],
                             confidence=max(a["confidence"], det["confidence"]), label_he=det.get("label_he"),
                             explanation=det.get("explanation"))
                    return "update", a
                a["confidence"] = max(a["confidence"], det["confidence"])
                return "grouped", a
            cd = self.cooldown.get(fam)
            if cd and now < cd[0] and rank <= cd[1]:
                return "cooldown", None
            if now - self.last_popup < MIN_GAP_ANY_S and not any(
                    rank > SEVERITY_RANK.get(o["severity"], 0) for o in self.open.values()):
                return "rate_limited", None
            a = {"id": "hz-" + uuid.uuid4().hex[:12], "family": fam, "category": det["category"],
                 "severity": det["severity"], "confidence": det["confidence"], "label_he": det.get("label_he"),
                 "explanation": det.get("explanation"), "first_ts": now, "last_ts": now, "count": 1,
                 "response": None, "meta": det.get("meta") or {}}
            self.open[fam] = a
            self.last_popup = now
            return "new", a

    def close(self, alert, response):
        """תגובת המשתמש/ת -> סגירת האירוע ושקט לפי סוג התגובה."""
        now = self.clock()
        secs = {"false_alarm": COOLDOWN_FALSE_S, "dismiss": COOLDOWN_DISMISS_S}.get(response, COOLDOWN_ACTION_S)
        with self._lock:
            alert["response"] = response
            self.open.pop(alert["family"], None)
            self.cooldown[alert["family"]] = (now + secs, SEVERITY_RANK.get(alert["severity"], 0))


# ================================================================= תיעוד (פנקס)
def record_alert(owner, alert, shown, reason_not_shown=None):
    """רשומה לכל אירוע (לא לכל זיהוי). בלי אודיו ובלי מיקום."""
    import incident_store as S
    rec = {"category": alert["category"], "severity": alert["severity"], "confidence": alert["confidence"],
           "count": alert["count"], "shown": bool(shown), "not_shown_reason": reason_not_shown,
           "meta": alert.get("meta") or {}}
    with S._LOCK:
        con = S._conn()
        with con:
            con.execute("""INSERT OR REPLACE INTO hazard_alerts(id, owner, ts, category, severity, shown, response,
                           reason, label_strength, record) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (alert["id"], S._key(owner), alert["first_ts"], alert["category"], alert["severity"],
                         int(bool(shown)), alert.get("response"), alert.get("reason"),
                         "user_reported" if alert.get("response") == "false_alarm" else None, S._enc(rec)))


def record_response(owner, alert, response, reason=None):
    import incident_store as S
    if response not in RESPONSES:
        raise ValueError(response)
    if reason is not None and reason not in FALSE_REASONS:
        raise ValueError(reason)
    alert["response"], alert["reason"] = response, reason
    with S._LOCK:
        con = S._conn()
        with con:
            con.execute("""UPDATE hazard_alerts SET response=?, reason=?, label_strength=?, responded_at=?
                           WHERE id=? AND owner=?""",
                        (response, reason, "user_reported" if response == "false_alarm" else None, time.time(),
                         alert["id"], S._key(owner)))


def list_alerts(owner, limit=500):
    import incident_store as S
    with S._LOCK:
        rows = S._conn().execute("SELECT * FROM hazard_alerts WHERE owner=? ORDER BY ts DESC LIMIT ?",
                                 (S._key(owner), int(limit))).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["record"] = S._dec_json(d["record"]) or {}
        out.append(d)
    return out


def feedback_summary(owner):
    """לחוקר הטעויות: התראות שווא לפי קטגוריה וסיבה (משוב משתמש/ת לא מאומת)."""
    rows = list_alerts(owner)
    by = {}
    for r in rows:
        c = by.setdefault(r["category"], {"alerts": 0, "shown": 0, "false_alarm": 0, "actions": 0, "reasons": {}})
        c["alerts"] += 1
        c["shown"] += int(bool(r["shown"]))
        if r["response"] == "false_alarm":
            c["false_alarm"] += 1
            c["reasons"][r["reason"] or "unspecified"] = c["reasons"].get(r["reason"] or "unspecified", 0) + 1
        elif r["response"] in ("sos", "safe_place", "contacts", "call"):
            c["actions"] += 1
    return by


def contacts_message(alert, lang="he"):
    s = STRINGS[lang]
    label = alert.get("label_he") or alert["category"]
    when = time.strftime("%H:%M", time.localtime(alert["first_ts"]))
    if lang == "en":
        return (f"LifeGuard: a possible danger sound ({alert['category']}) was detected near me at {when}. "
                "This is an unverified AI detection. Please check on me.")
    return (f"LifeGuard: זוהה אצלי צליל שעשוי להעיד על סכנה ({label}) בשעה {when}. "
            f"{s['not_verified']}. כדאי לבדוק מה שלומי.")

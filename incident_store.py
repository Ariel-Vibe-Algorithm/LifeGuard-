# -*- coding: utf-8 -*-
"""
incident_store.py
היסטוריית אירועים מובנית (Timeline), תור פעולות לסנכרון מאוחר (Outbox), ותוכנית בטיחות
אישית - הכל ב-SQLite מקומי אחד (data/lifeguard.db) עם מיגרציות ממוספרות.

עקרונות:
- כל פונקציה מקבלת owner ומסננת לפיו. משתמש אחד לא יכול לקרוא/למחוק אירוע של משתמש אחר
  (גם אם ניחש את המזהה) - נבדק בבדיקות אוטומטיות.
- תוכן רגיש (הודעות ציר זמן, מיקומים, תוכנית בטיחות) מוצפן במנוחה עם DPAPI (ראה storage.py).
  מטא-דאטה (סוג אירוע, זמנים) נשמר גלוי כדי לאפשר רשימה/מחיקה לפי ותק.
- כל רשומה בציר הזמן מסומנת provenance:
    'confirmed'  - פעולה שהמשתמש ביצע בפועל (לחיצה, סיסמה, דירוג) או אישור אנושי.
    'automatic'  - נאסף אוטומטית ע"י המערכת (מיקום, מצב, דיווח ספק) - לא אומת ע"י אדם.
- כשל אחסון לעולם לא מפיל אירוע SOS: המנוע עוטף את הקריאות (ראה engine._record).
"""

import json
import os
import threading
import time
import uuid

import config
import storage

DB_PATH = os.path.join(config.DATA_DIR, "lifeguard.db")

PROVENANCE_CONFIRMED = "confirmed"
PROVENANCE_AUTOMATIC = "automatic"

CATEGORIES = ("state", "user_action", "location", "delivery", "acknowledgment",
              "media", "system", "guidance", "note")

DEFAULT_RETENTION_DAYS = 180

_MIGRATIONS = [
    # 1 - טבלאות ליבה
    """
    CREATE TABLE IF NOT EXISTS incidents(
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        kind TEXT NOT NULL,
        trigger_source TEXT NOT NULL DEFAULT '',
        started_at REAL NOT NULL,
        ended_at REAL,
        outcome TEXT,
        rating TEXT,
        is_confidential INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_incidents_owner ON incidents(owner, started_at);
    CREATE TABLE IF NOT EXISTS incident_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
        owner TEXT NOT NULL,
        ts REAL NOT NULL,
        category TEXT NOT NULL,
        provenance TEXT NOT NULL,
        message TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_events_incident ON incident_events(incident_id, ts);
    CREATE TABLE IF NOT EXISTS outbox(
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        incident_id TEXT,
        kind TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(owner, status);
    CREATE TABLE IF NOT EXISTS user_settings(
        owner TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY(owner, key)
    );
    """,
    # 2 - תוכנית בטיחות אישית + מצב צ'קליסטים
    """
    CREATE TABLE IF NOT EXISTS safety_plans(
        owner TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS checklist_state(
        owner TEXT NOT NULL,
        checklist_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        done INTEGER NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(owner, checklist_id, item_id)
    );
    """,
    # 3 - פנקס הערכות (assessment_ledger.py): כל הערכת סיכון, בלי אודיו. התוכן מוצפן; גלויים רק
    #     השדות שנדרשים לסינון ולמחיקה לפי ותק. התווית לא נשמרת כאן - היא נגזרת מהאירוע עצמו (דירוג/ביטול)
    """
    CREATE TABLE IF NOT EXISTS assessments(
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        incident_id TEXT,
        ts REAL NOT NULL,
        level TEXT NOT NULL,
        record TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_assessments_owner ON assessments(owner, ts);
    """,
    # 4 - התראות סכנה אינטראקטיביות (hazard_alerts.py): אירוע לכל קבוצת זיהויים, תגובת המשתמש/ת ומשוב שווא.
    #     בלי אודיו ובלי מיקום; התוכן מוצפן
    """
    CREATE TABLE IF NOT EXISTS hazard_alerts(
        id TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        ts REAL NOT NULL,
        category TEXT NOT NULL,
        severity TEXT NOT NULL,
        shown INTEGER NOT NULL,
        response TEXT,
        reason TEXT,
        label_strength TEXT,
        responded_at REAL,
        record TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_hazard_alerts_owner ON hazard_alerts(owner, ts);
    """,
]

_LOCK = threading.RLock()
_CON = None
_CON_PATH = None


def _key(owner):
    return (owner or "").strip().lower()


def _conn():
    """חיבור יחיד משותף (מוגן ב-_LOCK). נפתח מחדש אם DB_PATH השתנה (בדיקות)."""
    global _CON, _CON_PATH
    if _CON is None or _CON_PATH != DB_PATH:
        if _CON is not None:
            try:
                _CON.close()
            except Exception:
                pass
        _CON = storage.connect_sqlite(DB_PATH, _MIGRATIONS)
        _CON_PATH = DB_PATH
    return _CON


def close():
    global _CON, _CON_PATH
    with _LOCK:
        if _CON is not None:
            try:
                _CON.close()
            finally:
                _CON = None
                _CON_PATH = None


def _enc(obj):
    if obj is None or obj == "":
        return ""
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, default=str)
    return storage.protect_text(text)


def _dec_text(value):
    try:
        return storage.unprotect_text(value)
    except Exception:
        return "[לא ניתן לפענח - הנתון הוצפן בחשבון Windows אחר]"


def _dec_json(value):
    text = _dec_text(value)
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


# ==================== אירועים וציר זמן ====================

def create_incident(owner, kind, trigger_source="", incident_id=None, started_at=None,
                    confidential=False):
    incident_id = incident_id or f"inc-{uuid.uuid4().hex[:16]}"
    started_at = started_at or time.time()
    with _LOCK:
        con = _conn()
        with con:
            con.execute(
                "INSERT OR IGNORE INTO incidents(id, owner, kind, trigger_source, started_at, is_confidential)"
                " VALUES (?,?,?,?,?,?)",
                (incident_id, _key(owner), kind, trigger_source, started_at, int(bool(confidential))),
            )
    return incident_id


def update_incident(owner, incident_id, **fields):
    allowed = {"kind", "trigger_source", "ended_at", "outcome", "rating", "is_confidential"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return False
    sets = ", ".join(f"{k}=?" for k in fields)
    with _LOCK:
        con = _conn()
        with con:
            cur = con.execute(
                f"UPDATE incidents SET {sets} WHERE id=? AND owner=?",
                (*fields.values(), incident_id, _key(owner)),
            )
    return cur.rowcount > 0


def add_event(owner, incident_id, category, message, provenance=PROVENANCE_AUTOMATIC,
              data=None, ts=None):
    if category not in CATEGORIES:
        category = "system"
    if provenance not in (PROVENANCE_CONFIRMED, PROVENANCE_AUTOMATIC):
        provenance = PROVENANCE_AUTOMATIC
    with _LOCK:
        con = _conn()
        row = con.execute("SELECT 1 FROM incidents WHERE id=? AND owner=?",
                          (incident_id, _key(owner))).fetchone()
        if row is None:
            return False  # לא ניתן להוסיף אירוע לתיק של משתמש אחר / לתיק שלא קיים
        with con:
            con.execute(
                "INSERT INTO incident_events(incident_id, owner, ts, category, provenance, message, data)"
                " VALUES (?,?,?,?,?,?,?)",
                (incident_id, _key(owner), ts or time.time(), category, provenance,
                 _enc(message), _enc(data)),
            )
    return True


def list_incidents(owner, limit=200):
    with _LOCK:
        rows = _conn().execute(
            "SELECT i.*, (SELECT COUNT(*) FROM incident_events e WHERE e.incident_id=i.id) AS event_count"
            " FROM incidents i WHERE owner=? ORDER BY started_at DESC LIMIT ?",
            (_key(owner), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def get_incident(owner, incident_id):
    """מחזיר את האירוע וציר הזמן המפוענח, או None אם לא קיים / שייך למשתמש אחר."""
    with _LOCK:
        con = _conn()
        row = con.execute("SELECT * FROM incidents WHERE id=? AND owner=?",
                          (incident_id, _key(owner))).fetchone()
        if row is None:
            return None
        events = con.execute(
            "SELECT ts, category, provenance, message, data FROM incident_events"
            " WHERE incident_id=? AND owner=? ORDER BY ts, id",
            (incident_id, _key(owner)),
        ).fetchall()
    incident = dict(row)
    incident["events"] = [
        {"ts": e["ts"], "category": e["category"], "provenance": e["provenance"],
         "message": _dec_text(e["message"]), "data": _dec_json(e["data"])}
        for e in events
    ]
    return incident


def delete_incident(owner, incident_id, delete_media=False):
    """מוחק אירוע וציר זמן. delete_media=True מוחק גם הקלטות/תמונות שהאירוע מפנה אליהן."""
    incident = get_incident(owner, incident_id)
    if incident is None:
        return False, 0
    removed_files = 0
    if delete_media:
        for path in _media_paths(incident):
            removed_files += _safe_remove_media(path)
    with _LOCK:
        con = _conn()
        with con:
            con.execute("DELETE FROM incident_events WHERE incident_id=? AND owner=?",
                        (incident_id, _key(owner)))
            con.execute("DELETE FROM outbox WHERE incident_id=? AND owner=?",
                        (incident_id, _key(owner)))
            con.execute("DELETE FROM assessments WHERE incident_id=? AND owner=?",
                        (incident_id, _key(owner)))
            con.execute("DELETE FROM incidents WHERE id=? AND owner=?",
                        (incident_id, _key(owner)))
    return True, removed_files


def _media_paths(incident):
    paths = []
    for ev in incident.get("events", []):
        data = ev.get("data")
        if ev.get("category") == "media" and isinstance(data, dict) and data.get("path"):
            paths.append(data["path"])
    return paths


def _safe_remove_media(path):
    """מוחק רק קבצים שנמצאים בתוך תיקיית הנתונים של LifeGuard - לעולם לא נתיב שרירותי."""
    try:
        real = os.path.realpath(path)
        allowed_roots = [os.path.realpath(config.AUDIO_DIR), os.path.realpath(config.PHOTOS_DIR)]
        if not any(os.path.commonpath([real, root]) == root for root in allowed_roots):
            return 0
        if os.path.isfile(real):
            os.remove(real)
            return 1
    except (OSError, ValueError):
        pass
    return 0


# ==================== מדיניות שמירה (Retention) ====================

def get_setting(owner, key, default=None):
    with _LOCK:
        row = _conn().execute("SELECT value FROM user_settings WHERE owner=? AND key=?",
                              (_key(owner), key)).fetchone()
    return row["value"] if row else default


def set_setting(owner, key, value):
    with _LOCK:
        con = _conn()
        with con:
            con.execute("INSERT OR REPLACE INTO user_settings(owner, key, value) VALUES (?,?,?)",
                        (_key(owner), key, str(value)))


def get_retention_days(owner):
    try:
        return int(get_setting(owner, "retention_days", DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def set_retention_days(owner, days):
    days = int(days)
    if days < 0 or days > 3650:
        raise ValueError("retention must be between 0 (keep forever) and 3650 days")
    set_setting(owner, "retention_days", days)


def purge_expired(owner, now=None, delete_media=True):
    """מוחק אירועים שהסתיימו וישנים ממדיניות השמירה. 0 ימים = לשמור לתמיד. מחזיר כמות."""
    days = get_retention_days(owner)
    if days <= 0:
        return 0
    cutoff = (now or time.time()) - days * 86400
    with _LOCK:
        rows = _conn().execute(
            "SELECT id FROM incidents WHERE owner=? AND ended_at IS NOT NULL AND ended_at < ?",
            (_key(owner), cutoff),
        ).fetchall()
    for row in rows:
        delete_incident(owner, row["id"], delete_media=delete_media)
    with _LOCK:          # גם רשומות פנקס ההערכות (גם כאלה שהאירוע שלהן כבר לא קיים) כפופות למדיניות השמירה
        con = _conn()
        with con:
            con.execute("DELETE FROM assessments WHERE owner=? AND ts < ?", (_key(owner), cutoff))
            con.execute("DELETE FROM hazard_alerts WHERE owner=? AND ts < ?", (_key(owner), cutoff))
    return len(rows)


# ==================== ייצוא דוח ====================

_PROVENANCE_LABEL = {
    PROVENANCE_CONFIRMED: "מאושר (פעולת משתמש/אדם)",
    PROVENANCE_AUTOMATIC: "אוטומטי (נאסף ע\"י המערכת, לא אומת)",
}


def _fmt_ts(ts):
    if not ts:
        return "—"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def build_report_text(owner, incident_id):
    incident = get_incident(owner, incident_id)
    if incident is None:
        return None
    lines = [
        "LifeGuard - דוח אירוע",
        "=" * 40,
        f"מזהה: {incident['id']}",
        f"סוג: {incident['kind']}",
        f"מקור הפעלה: {incident['trigger_source'] or '—'}",
        f"התחלה: {_fmt_ts(incident['started_at'])}",
        f"סיום: {_fmt_ts(incident['ended_at'])}",
        f"תוצאה: {incident['outcome'] or 'לא הסתיים / לא ידוע'}",
        f"דירוג משתמש: {incident['rating'] or '—'}",
        "",
        "מקרא: [מאושר] = פעולה שבוצעה בפועל ע\"י המשתמש או אישור אנושי;",
        "      [אוטומטי] = נאסף ע\"י המערכת. 'התקבל אצל ספק' אינו הוכחה שהודעה נקראה.",
        "-" * 40,
    ]
    for ev in incident["events"]:
        tag = "מאושר" if ev["provenance"] == PROVENANCE_CONFIRMED else "אוטומטי"
        lines.append(f"{_fmt_ts(ev['ts'])}  [{tag}] [{ev['category']}] {ev['message']}")
    lines.append("-" * 40)
    lines.append(f"הופק: {_fmt_ts(time.time())}. הקובץ אינו מוצפן - שמור/י אותו במקום מאובטח.")
    return "\n".join(lines)


def export_incident(owner, incident_id, path, fmt="txt"):
    """מייצא דוח (txt או json). מחזיר (ok, msg). הקובץ המיוצא אינו מוצפן - הממשק מזהיר."""
    if fmt == "json":
        incident = get_incident(owner, incident_id)
        if incident is None:
            return False, "האירוע לא נמצא"
        incident["provenance_legend"] = _PROVENANCE_LABEL
        content = json.dumps(incident, ensure_ascii=False, indent=2, default=str)
    else:
        content = build_report_text(owner, incident_id)
        if content is None:
            return False, "האירוע לא נמצא"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as exc:
        return False, f"שמירת הקובץ נכשלה: {exc}"
    return True, f"הדוח נשמר: {path}"


# ==================== Outbox - פעולות לסנכרון מאוחר ====================

OUTBOX_PENDING = "pending"          # נשמר מקומית, טרם נשלח
OUTBOX_SENT = "provider_accepted"   # ספק קיבל (לא הוכחת קריאה)
OUTBOX_FAILED = "failed"            # נכשל סופית
OUTBOX_EXPIRED = "expired"          # האירוע הסתיים לפני שנשלח - לא יישלח באיחור
OUTBOX_UNDELIVERABLE = "no_channel" # אין ערוץ שליחה מוגדר לנמען - לעולם לא יישלח מעצמו


def enqueue(owner, kind, payload, idempotency_key, incident_id=None, status=OUTBOX_PENDING):
    """מוסיף פעולה לתור. idempotency_key ייחודי - הוספה כפולה לא יוצרת כפילות."""
    now = time.time()
    with _LOCK:
        con = _conn()
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO outbox(id, owner, incident_id, kind, idempotency_key, payload,"
                " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"ob-{uuid.uuid4().hex[:16]}", _key(owner), incident_id, kind, idempotency_key,
                 _enc(payload), status, now, now),
            )
    return cur.rowcount > 0


def pending_outbox(owner, incident_id=None):
    q = "SELECT * FROM outbox WHERE owner=? AND status=?"
    args = [_key(owner), OUTBOX_PENDING]
    if incident_id:
        q += " AND incident_id=?"
        args.append(incident_id)
    with _LOCK:
        rows = _conn().execute(q + " ORDER BY created_at", args).fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["payload"] = _dec_json(r["payload"])
        items.append(item)
    return items


def list_outbox(owner, limit=100):
    with _LOCK:
        rows = _conn().execute(
            "SELECT id, incident_id, kind, status, attempts, last_error, created_at, updated_at"
            " FROM outbox WHERE owner=? ORDER BY created_at DESC LIMIT ?",
            (_key(owner), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_outbox(owner, item_id, status, error="", count_attempt=True):
    with _LOCK:
        con = _conn()
        with con:
            con.execute(
                "UPDATE outbox SET status=?, last_error=?, updated_at=?,"
                " attempts = attempts + ? WHERE id=? AND owner=?",
                (status, error or "", time.time(), 1 if count_attempt else 0, item_id, _key(owner)),
            )


def expire_outbox_for_incident(owner, incident_id):
    """בסיום אירוע: פריטים שלא נשלחו מסומנים 'expired' - לא שולחים SOS ישן באיחור."""
    with _LOCK:
        con = _conn()
        with con:
            cur = con.execute(
                "UPDATE outbox SET status=?, updated_at=? WHERE owner=? AND incident_id=? AND status=?",
                (OUTBOX_EXPIRED, time.time(), _key(owner), incident_id, OUTBOX_PENDING),
            )
    return cur.rowcount


def outbox_counts(owner):
    with _LOCK:
        rows = _conn().execute("SELECT status, COUNT(*) AS n FROM outbox WHERE owner=? GROUP BY status",
                               (_key(owner),)).fetchall()
    return {r["status"]: r["n"] for r in rows}


# ==================== תוכנית בטיחות אישית + צ'קליסטים ====================

EMPTY_PLAN = {
    "meeting_points": [],          # [{"name":..., "address":..., "notes":...}]
    "fallback_destinations": [],   # [{"name":..., "address":..., "notes":...}]
    "important_instructions": "",  # טקסט חופשי קצר: תרופות, אלרגיות, מי לקחת מהגן...
    "medical_notes": "",           # מידע רפואי שהמשתמש בחר לשמור (מוצפן)
    "household_contacts": "",      # טלפונים/שמות כתובים מראש (למקרה שאין גישה לטלפון)
    "updated_at": None,
}


def get_safety_plan(owner):
    with _LOCK:
        row = _conn().execute("SELECT data, updated_at FROM safety_plans WHERE owner=?",
                              (_key(owner),)).fetchone()
    plan = json.loads(json.dumps(EMPTY_PLAN))
    if row is not None:
        stored = _dec_json(row["data"])
        if isinstance(stored, dict):
            plan.update({k: v for k, v in stored.items() if k in EMPTY_PLAN})
        plan["updated_at"] = row["updated_at"]
    return plan


def save_safety_plan(owner, plan):
    clean = {}
    for key in ("meeting_points", "fallback_destinations"):
        items = []
        for item in plan.get(key, []) or []:
            name = (item.get("name") or "").strip()[:120]
            if not name:
                continue
            items.append({"name": name,
                          "address": (item.get("address") or "").strip()[:250],
                          "notes": (item.get("notes") or "").strip()[:500]})
        clean[key] = items[:10]
    for key in ("important_instructions", "medical_notes", "household_contacts"):
        clean[key] = (plan.get(key) or "").strip()[:4000]
    now = time.time()
    with _LOCK:
        con = _conn()
        with con:
            con.execute("INSERT OR REPLACE INTO safety_plans(owner, data, updated_at) VALUES (?,?,?)",
                        (_key(owner), _enc(clean), now))
    return True


def set_checklist_item(owner, checklist_id, item_id, done):
    with _LOCK:
        con = _conn()
        with con:
            con.execute(
                "INSERT OR REPLACE INTO checklist_state(owner, checklist_id, item_id, done, updated_at)"
                " VALUES (?,?,?,?,?)",
                (_key(owner), checklist_id, item_id, int(bool(done)), time.time()),
            )


def get_checklist_state(owner, checklist_id):
    with _LOCK:
        rows = _conn().execute(
            "SELECT item_id, done, updated_at FROM checklist_state WHERE owner=? AND checklist_id=?",
            (_key(owner), checklist_id),
        ).fetchall()
    return {r["item_id"]: {"done": bool(r["done"]), "updated_at": r["updated_at"]} for r in rows}


def reset_checklist(owner, checklist_id):
    with _LOCK:
        con = _conn()
        with con:
            con.execute("DELETE FROM checklist_state WHERE owner=? AND checklist_id=?",
                        (_key(owner), checklist_id))

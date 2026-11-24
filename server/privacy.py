# -*- coding: utf-8 -*-
"""
privacy.py - הרשאות מיקום לכל איש קשר, יומן ביקורת עמיד לשינוי, הקלטות חירום, ופרופיל הורה-ילד.

1. מיקום לכל איש קשר (relationships.location_mode):
   - always: מיקום חי רציף (מוצפן בשרת, live_locations). "השהיה" עוצרת מיד את כל השיתוף הרציף.
   - emergency (ברירת מחדל = ההתנהגות הקיימת): רק באירוע חירום פעיל / SOS ידני.
   - off: איש הקשר עדיין מקבל התראת חירום, אבל *בלי* מיקום.
   מיקום ישן לעולם לא מוצג כחי: לכל מיקום captured_at/received_at, ו-stale אחרי 5 דקות.
2. יומן ביקורת (audit_events): append-only - טריגרים במסד חוסמים UPDATE/DELETE, ושרשרת hash מגלה
   שינוי בקובץ עצמו. כל אירוע: event_id, זמן שרת, זמן מכשיר (אם הגיע ממכשיר), actor/subject/recipient,
   action, result (completed/failed/pending). צפייה במיקום נרשמת כ"נמסר לאפליקציה של X" - לא "נצפה" (אין לנו
   הוכחה שמישהו הסתכל במסך). צפיות חוזרות מאוחדות למפגש (location_access_sessions: מתי התחיל/הסתיים).
3. הקלטות: רק באירוע מאושר (SOS ידני / ביטחון גבוה / אישור בעלים), רק אם המשתמש/ת אפשר/ה, מועלות
   מוצפנות ונגישות רק לאנשי קשר שקיבלו הרשאת הקלטות (ולהורה מאושר בפרופיל ילד). אין האזנה חיה, אין הפעלה
   מרחוק של מיקרופון - אין לזה API בכלל. כל השמעה נרשמת. שמירה 30 יום; מחיקה מאובטחת לבעלים.
4. הורה-ילד: קישור שדורש אישור של שני הצדדים; הקלטה דיסקרטית (בלי אינטראקציה של הילד באירוע מאושר)
   רק אם: הקישור פעיל, שני הצדדים הסכימו *להקלטה* בנפרד, וההורה אומת (guardian_verified - בדיקה ידנית ע"י
   מנהל/ת). כל צד יכול לבטל. ההקלטה מתבצעת במכשיר בכפוף להרשאות מערכת ההפעלה ולסמן המיקרופון שלה.
5. הפעלת חירום לפי ביטחון: SOS ידני - מיד ומלא. זיהוי אוטומטי: ביטחון < 0.4 נדחה; 0.4-0.7 "לא מאושר" -
   אנשי קשר מקבלים התראה, מתנדבים לא נשלחים עד אישור; >= 0.7 - זרימה מלאה.
"""

import base64
import hashlib
import json
import os
import secrets
import time

LOCATION_MODES = ("always", "emergency", "off")
STALE_SECONDS = 300
SESSION_GAP_SECONDS = 300
RECORDING_MAX_BYTES = 1_200_000
RECORDING_RETENTION_DAYS = 30
RECORDING_LATE_UPLOAD_HOURS = 48
AUTO_TRIGGERS = ("auto", "sensor", "wellness_no_response", "acoustic", "watch_fall", "wearable")
# לחיצה של אדם (ידני) - גם בשמות שלקוחות ישנים שולחים. כל השאר: אם יש ביטחון - זה זיהוי אוטומטי
MANUAL_TRIGGERS = ("manual", "button", "panic_button", "panic", "hotkey", "voice", "silent_voice")
CONF_REJECT, CONF_HIGH = 0.4, 0.7
CONFIRMED_ACTIVATIONS = ("manual", "confirmed_high", "confirmed_by_user", "server_timer")
DEVICE_ACTIONS = ("recording_started", "recording_stopped", "recording_stored_offline", "recording_upload_failed",
                  "sos_activated_offline", "false_alarm_cancelled", "live_location_paused_offline",
                  "evidence_exported", "auto_activation_rejected")
GENESIS = "0" * 64

MIGRATION = """
ALTER TABLE relationships ADD COLUMN location_mode TEXT NOT NULL DEFAULT 'emergency';
ALTER TABLE relationships ADD COLUMN recording_access INTEGER NOT NULL DEFAULT 0;
ALTER TABLE relationships ADD COLUMN perm_changed_at REAL;
ALTER TABLE incidents ADD COLUMN trigger_kind TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE incidents ADD COLUMN activation TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE users ADD COLUMN guardian_verified INTEGER NOT NULL DEFAULT 0;
CREATE TABLE live_locations(username TEXT PRIMARY KEY, enc TEXT NOT NULL, captured_at REAL, received_at REAL NOT NULL);
CREATE TABLE privacy_settings(username TEXT PRIMARY KEY, sharing_paused INTEGER NOT NULL DEFAULT 0,
    record_on_emergency INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE audit_events(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, server_ts REAL NOT NULL, device_ts REAL,
    actor TEXT, subject TEXT, recipient TEXT, action TEXT NOT NULL, result TEXT NOT NULL, via TEXT, permission TEXT,
    incident_id TEXT, details TEXT NOT NULL DEFAULT '{}', source TEXT NOT NULL DEFAULT 'server',
    prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_audit_subject ON audit_events(subject, server_ts);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_events(actor, server_ts);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TABLE location_access_sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, viewer TEXT NOT NULL, subject TEXT NOT NULL, via TEXT NOT NULL,
    incident_id TEXT, first_at REAL NOT NULL, last_at REAL NOT NULL, count INTEGER NOT NULL, audit_event_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_las_key ON location_access_sessions(viewer, subject, via, last_at);
CREATE TABLE guardian_links(
    id TEXT PRIMARY KEY, guardian TEXT NOT NULL, child TEXT NOT NULL, created_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','active','revoked')),
    guardian_approved_at REAL, child_approved_at REAL, child_age INTEGER,
    rec_guardian_consent_at REAL, rec_child_consent_at REAL, created_at REAL NOT NULL, revoked_at REAL, revoked_by TEXT);
CREATE TABLE recordings(
    id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, owner TEXT NOT NULL, client_id TEXT NOT NULL, path TEXT,
    sha256 TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT, device_started_at REAL, device_ended_at REAL,
    uploaded_at REAL NOT NULL, discreet INTEGER NOT NULL DEFAULT 0, deleted_at REAL, UNIQUE(owner, client_id));
"""


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def audit_hash(prev, row):
    fields = [prev, row["event_id"], f"{row['server_ts']:.6f}", "" if row.get("device_ts") is None else
              f"{float(row['device_ts']):.3f}", row.get("actor") or "", row.get("subject") or "",
              row.get("recipient") or "", row["action"], row["result"], row.get("via") or "",
              row.get("permission") or "", row.get("incident_id") or "", row.get("details") or "{}",
              row.get("source") or "server"]
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()


def verify_audit_rows(rows):
    """שרשרת מלאה (לפי seq): כל שורה מצביעה על הקודמת וה-hash תואם לתוכן."""
    prev = GENESIS
    for r in rows:
        if r["prev_hash"] != prev or r["hash"] != audit_hash(prev, r):
            return False, r["event_id"]
        prev = r["hash"]
    return True, None


class PrivacyMixin:
    # ================================================================ יומן ביקורת
    def audit(self, action, result="completed", actor=None, subject=None, recipient=None, via=None,
              permission=None, incident_id=None, details=None, device_ts=None, event_id=None, source="server"):
        event_id = event_id or ("ev_" + secrets.token_hex(12))
        with self._lock:
            if self._q("SELECT 1 FROM audit_events WHERE event_id=?", (event_id,), one=True):
                return event_id          # אירוע ממכשיר שנשלח שוב (סנכרון אחרי ניתוק) - לא כפול
            last = self._q("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1", one=True)
            row = {"event_id": event_id, "server_ts": round(time.time(), 6), "device_ts": device_ts, "actor": actor,
                   "subject": subject, "recipient": recipient, "action": action, "result": result, "via": via,
                   "permission": permission, "incident_id": incident_id,
                   "details": _canon(details or {}), "source": source}
            prev = last["hash"] if last else GENESIS
            row["prev_hash"], row["hash"] = prev, audit_hash(prev, row)
            cols = list(row)
            self._x(f"INSERT INTO audit_events({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    tuple(row[c] for c in cols))
        return event_id

    def verify_audit(self):
        rows = self._q("SELECT * FROM audit_events ORDER BY seq")
        ok, bad = verify_audit_rows(rows)
        return {"ok": ok, "events": len(rows), "first_bad_event": bad}

    def device_audit(self, user, events):
        """אירועים שנרשמו במכשיר (גם בלי קליטה) - זמן המכשיר נשמר בנפרד מזמן השרת."""
        if not isinstance(events, list) or len(events) > 200:
            raise self._err(400, "invalid", "רשימת אירועים לא תקינה")
        accepted = 0
        for e in events:
            if not isinstance(e, dict) or e.get("action") not in DEVICE_ACTIONS:
                continue
            eid = str(e.get("event_id") or "")
            if not (8 <= len(eid) <= 64) or not all(c.isalnum() or c in "-_" for c in eid):
                continue
            try:
                dts = float(e.get("device_ts"))
            except (TypeError, ValueError):
                dts = None
            result = e.get("result") if e.get("result") in ("completed", "failed", "pending") else "completed"
            det = e.get("details") if isinstance(e.get("details"), dict) else {}
            det = {k: str(v)[:200] for k, v in list(det.items())[:10]}
            self.audit(e["action"], result, actor=user, subject=user, incident_id=str(e.get("incident_id") or "")[:60]
                       or None, details=det, device_ts=dts, event_id=f"dev_{user}_{eid}"[:120], source="device")
            accepted += 1
        return {"accepted": accepted}

    # ================================================================ הרשאות מיקום
    def _rel(self, owner, contact):
        return self._q("SELECT * FROM relationships WHERE owner=? AND contact=? AND status='accepted'",
                       (owner, contact), one=True)

    def location_mode_for(self, owner, contact):
        r = self._rel(owner, contact)
        return r["location_mode"] if r else None

    def set_location_mode(self, owner, contact, mode):
        if mode not in LOCATION_MODES:
            raise self._err(400, "invalid_mode", "מצב שיתוף לא תקין")
        r = self._rel(owner, contact)
        if not r:
            raise self._err(404, "not_found", "איש הקשר לא נמצא במעגל")
        self._x("UPDATE relationships SET location_mode=?, perm_changed_at=? WHERE owner=? AND contact=?",
                (mode, time.time(), owner, contact))
        if mode != "always":
            self._end_sessions(contact, owner, "always")
        self.audit("permission_location_mode", actor=owner, subject=owner, recipient=contact,
                   details={"from": r["location_mode"], "to": mode})
        return self.privacy_permissions(owner)

    def set_recording_access(self, owner, contact, allowed):
        r = self._rel(owner, contact)
        if not r:
            raise self._err(404, "not_found", "איש הקשר לא נמצא במעגל")
        self._x("UPDATE relationships SET recording_access=?, perm_changed_at=? WHERE owner=? AND contact=?",
                (int(bool(allowed)), time.time(), owner, contact))
        self.audit("permission_recording_access", actor=owner, subject=owner, recipient=contact,
                   details={"from": bool(r["recording_access"]), "to": bool(allowed)})
        return self.privacy_permissions(owner)

    def _settings(self, user):
        r = self._q("SELECT * FROM privacy_settings WHERE username=?", (user,), one=True)
        return r or {"username": user, "sharing_paused": 0, "record_on_emergency": 0, "updated_at": None}

    def _set_setting(self, user, **kw):
        cur = self._settings(user)
        cur.update({k: int(bool(v)) for k, v in kw.items()})
        self._x("""INSERT INTO privacy_settings(username, sharing_paused, record_on_emergency, updated_at)
                   VALUES (?,?,?,?) ON CONFLICT(username) DO UPDATE SET sharing_paused=excluded.sharing_paused,
                   record_on_emergency=excluded.record_on_emergency, updated_at=excluded.updated_at""",
                (user, cur["sharing_paused"], cur["record_on_emergency"], time.time()))
        return cur

    def pause_sharing(self, user, paused):
        self._set_setting(user, sharing_paused=paused)
        if paused:
            self._x("DELETE FROM live_locations WHERE username=?", (user,))   # לא נשאר מיקום "חי" ישן
        self.audit("permission_sharing_paused" if paused else "permission_sharing_resumed", actor=user, subject=user)
        return self.privacy_permissions(user)

    def set_record_on_emergency(self, user, enabled):
        self._set_setting(user, record_on_emergency=enabled)
        self.audit("permission_recording", actor=user, subject=user, details={"record_on_emergency": bool(enabled)})
        return self.recording_policy(user)

    def put_live_location(self, user, loc):
        """מיקום רציף לאנשי קשר במצב always בלבד. מוצפן במסד. בהשהיה / בלי אף צופה - לא נשמר בכלל."""
        if self._settings(user)["sharing_paused"]:
            return {"stored": False, "reason": "paused"}
        if not self._q("SELECT 1 FROM relationships WHERE owner=? AND status='accepted' AND location_mode='always'",
                       (user,), one=True):
            return {"stored": False, "reason": "no_always_contacts"}
        lat, lon = self._valid_coords(loc.get("lat"), loc.get("lon"))
        try:
            acc = float(loc.get("accuracy_m")) if loc.get("accuracy_m") is not None else None
        except (TypeError, ValueError):
            acc = None
        try:
            cap = float(loc.get("captured_at")) if loc.get("captured_at") is not None else None
        except (TypeError, ValueError):
            cap = None
        now = time.time()
        if cap is not None and cap > now + 120:
            cap = None              # שעון מכשיר בעתיד - לא סומכים
        payload = {"lat": lat, "lon": lon, "accuracy_m": acc, "is_precise": bool(loc.get("is_precise"))}
        enc = self.crypto.encrypt(_canon(payload), aad=f"live:{user}".encode())
        self._x("INSERT INTO live_locations(username, enc, captured_at, received_at) VALUES (?,?,?,?) "
                "ON CONFLICT(username) DO UPDATE SET enc=excluded.enc, captured_at=excluded.captured_at, "
                "received_at=excluded.received_at", (user, enc, cap, now))
        return {"stored": True}

    def contact_location(self, viewer, owner):
        """מיקום חי של owner עבור viewer - רק במצב always, לא בהשהיה. stale מסומן במפורש."""
        mode = self.location_mode_for(owner, viewer)
        if mode != "always" or self._settings(owner)["sharing_paused"]:
            self.audit("location_access", "failed", actor=viewer, subject=owner, via="always",
                       permission=mode or "none", details={"reason": "not_permitted"})
            raise self._err(403, "not_permitted", "אין הרשאה למיקום הזה")
        row = self._q("SELECT * FROM live_locations WHERE username=?", (owner,), one=True)
        if not row:
            return {"available": False, "reason": "אין עדיין מיקום משותף מהמכשיר"}
        p = json.loads(self.crypto.decrypt(row["enc"], aad=f"live:{owner}".encode()))
        now = time.time()
        age = now - row["received_at"]
        self._note_location_access(viewer, owner, "always", None, "always")
        return {"available": True, "lat": p["lat"], "lon": p["lon"], "accuracy_m": p["accuracy_m"],
                "is_precise": p["is_precise"], "captured_at": row["captured_at"], "received_at": row["received_at"],
                "age_seconds": round(age), "stale": age > STALE_SECONDS,
                "label": "חי" if age <= STALE_SECONDS else f"מיקום ישן ({int(age // 60)} דק') - לא חי"}

    def _note_location_access(self, viewer, subject, via, incident_id, permission):
        """מסירת מיקום לאפליקציה של viewer. רצף בקשות (polling) = מפגש אחד עם התחלה/סוף."""
        now = time.time()
        s = self._q("""SELECT * FROM location_access_sessions WHERE viewer=? AND subject=? AND via=?
                       AND COALESCE(incident_id,'')=COALESCE(?, '') AND last_at>? ORDER BY id DESC LIMIT 1""",
                    (viewer, subject, via, incident_id, now - SESSION_GAP_SECONDS), one=True)
        if s:
            self._x("UPDATE location_access_sessions SET last_at=?, count=count+1 WHERE id=?", (now, s["id"]))
            return
        eid = self.audit("location_access", "completed", actor=viewer, subject=subject, recipient=viewer, via=via,
                         permission=permission, incident_id=incident_id,
                         details={"note": "המיקום נמסר לאפליקציה (לא מאשר שמישהו הסתכל במסך)"})
        self._x("INSERT INTO location_access_sessions(viewer, subject, via, incident_id, first_at, last_at, count, "
                "audit_event_id) VALUES (?,?,?,?,?,?,1,?)", (viewer, subject, via, incident_id, now, now, eid))

    def _end_sessions(self, viewer, subject, via):
        self._x("UPDATE location_access_sessions SET last_at=last_at WHERE viewer=? AND subject=? AND via=?",
                (viewer, subject, via))

    # ================================================================ הפעלת חירום לפי ביטחון
    def activation_for(self, trigger, confidence):
        """-> (trigger_kind, activation). זורק 422 על זיהוי אוטומטי בביטחון נמוך."""
        if trigger in AUTO_TRIGGERS or trigger == "server_timer":
            t = trigger
        elif not trigger or trigger in MANUAL_TRIGGERS:
            t = "manual"
        elif confidence is not None:
            # באג שנמצא (סוכן השעון): trigger לא מוכר הפך ל"ידני" = הפעלה מלאה בלי בדיקת ביטחון.
            # trigger לא מוכר *עם* ביטחון הוא זיהוי אוטומטי -> עובר את הספים כמו כל זיהוי אוטומטי.
            t = "auto"
        else:
            # בלי ביטחון: כנראה לחיצה מלקוח ישן. לא חוסמים SOS של אדם בגלל שם לא מוכר (חסימה מסוכנת יותר)
            t = "manual"
        if t not in AUTO_TRIGGERS:
            return t, ("server_timer" if t == "server_timer" else "manual")
        if confidence is None or confidence < CONF_REJECT:
            raise self._err(422, "low_confidence", "זיהוי אוטומטי בביטחון נמוך לא מפעיל חירום")
        return t, ("confirmed_high" if confidence >= CONF_HIGH else "unconfirmed")

    def confirm_incident(self, user, iid):
        inc, role, _ = self._access(user, iid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל/ת האירוע מאשר/ת")
        if inc["activation"] == "unconfirmed":
            self._x("UPDATE incidents SET activation='confirmed_by_user' WHERE id=?", (iid,))
            self._event(iid, "confirmed", "המשתמש/ת אישר/ה שזה חירום - מפעילים מתנדבים", user)
            self.audit("emergency_confirmed", actor=user, subject=user, incident_id=iid)
            self._wake_dispatch()
            self._dispatch_for(iid)
        return self._view_for(iid, user, include_medical=True)

    # ================================================================ הקלטות
    def recording_policy(self, user):
        s = self._settings(user)
        links = [g for g in self._guardian_rows(user) if g["child"] == user and self._discreet_active(g)]
        recips = self._recording_recipients(user)
        return {"record_on_emergency": bool(s["record_on_emergency"]) or bool(links),
                "discreet": bool(links), "discreet_guardians": [self._display(g["guardian"]) for g in links],
                "upload": bool(recips), "recipients": [self._display(r) for r in recips],
                "max_upload_bytes": RECORDING_MAX_BYTES, "retention_days": RECORDING_RETENTION_DAYS,
                "notes": ["הקלטה רק באירוע חירום מאושר או SOS ידני; אין האזנה חיה ואין הפעלה מרחוק",
                          "סמן המיקרופון של מערכת ההפעלה מוצג גם בהקלטה דיסקרטית"]}

    def _recording_recipients(self, owner):
        rs = [r["contact"] for r in self._q("SELECT contact FROM relationships WHERE owner=? AND status='accepted' "
                                            "AND recording_access=1", (owner,))]
        for g in self._guardian_rows(owner):
            if g["child"] == owner and self._discreet_active(g) and g["guardian"] not in rs:
                rs.append(g["guardian"])
        return rs

    def _rec_dir(self):
        d = os.path.join(os.path.dirname(os.path.abspath(self.db_path)), "recordings")
        os.makedirs(d, exist_ok=True)
        return d

    def upload_recording(self, user, iid, body):
        inc, role, _ = self._access(user, iid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל/ת האירוע מעלה הקלטות")
        if inc["activation"] not in CONFIRMED_ACTIVATIONS:
            raise self._err(409, "not_confirmed", "הקלטה מועלית רק באירוע חירום מאושר")
        pol = self.recording_policy(user)
        if not pol["record_on_emergency"]:
            raise self._err(403, "recording_disabled", "הקלטה בחירום לא אופשרה בהגדרות")
        if inc["status"] != "active" and time.time() - (inc["ended_at"] or 0) > RECORDING_LATE_UPLOAD_HOURS * 3600:
            raise self._err(409, "too_late", "האירוע הסתיים מזמן")
        client_id = str(body.get("client_id") or "")[:80]
        if len(client_id) < 4:
            raise self._err(400, "invalid", "client_id חסר")
        dup = self._q("SELECT id FROM recordings WHERE owner=? AND client_id=?", (user, client_id), one=True)
        if dup:
            return {"id": dup["id"], "duplicate": True}
        try:
            data = base64.b64decode(str(body.get("data_b64") or ""), validate=True)
        except (ValueError, TypeError):
            raise self._err(400, "invalid", "נתוני הקלטה לא תקינים")
        if not data or len(data) > RECORDING_MAX_BYTES:
            raise self._err(413, "too_large", "קטע הקלטה גדול מדי")
        digest = hashlib.sha256(data).hexdigest()
        if body.get("sha256") and body["sha256"] != digest:
            raise self._err(400, "hash_mismatch", "ה-hash לא תואם - ההעלאה נפגמה")
        rid = "rec_" + secrets.token_hex(12)
        path = os.path.join(self._rec_dir(), rid + ".enc")
        with open(path, "w", encoding="ascii") as fh:
            fh.write(self.crypto.encrypt(base64.b64encode(data).decode("ascii"), aad=rid.encode()))

        def num(k):
            try:
                return float(body.get(k)) if body.get(k) is not None else None
            except (TypeError, ValueError):
                return None
        self._x("""INSERT INTO recordings(id, incident_id, owner, client_id, path, sha256, size, mime, device_started_at,
                   device_ended_at, uploaded_at, discreet) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, iid, user, client_id, path, digest, len(data), str(body.get("mime") or "audio/mp4")[:40],
                 num("started_at"), num("ended_at"), time.time(), int(bool(pol["discreet"]))))
        self.audit("recording_uploaded", actor=user, subject=user, incident_id=iid,
                   details={"recording_id": rid, "bytes": len(data), "sha256": digest, "discreet": pol["discreet"],
                            "trigger": inc["trigger_kind"]}, device_ts=num("ended_at"))
        for r in self._recording_recipients(user):
            nid = self._notify(r, "incident_update", {"incident_id": iid, "event": "recording", "recording_id": rid,
                                                       "quiet": True}, iid)
            self.audit("recording_recipient_notified", "pending", actor=user, subject=user, recipient=r,
                       incident_id=iid, details={"recording_id": rid, "notification_id": nid})
        return {"id": rid, "sha256": digest, "recipients": [self._display(r) for r in self._recording_recipients(user)]}

    def _can_hear(self, user, rec):
        if rec["owner"] == user:
            return True
        return user in self._recording_recipients(rec["owner"])

    def list_recordings(self, user, iid):
        self._access(user, iid)
        rows = self._q("SELECT * FROM recordings WHERE incident_id=? AND deleted_at IS NULL ORDER BY uploaded_at", (iid,))
        return {"recordings": [{"id": r["id"], "size": r["size"], "sha256": r["sha256"], "mime": r["mime"],
                                "started_at": r["device_started_at"], "ended_at": r["device_ended_at"],
                                "uploaded_at": r["uploaded_at"]} for r in rows if self._can_hear(user, r)]}

    def get_recording(self, user, iid, rid):
        self._access(user, iid)
        rec = self._q("SELECT * FROM recordings WHERE id=? AND incident_id=? AND deleted_at IS NULL", (rid, iid), one=True)
        if not rec or not self._can_hear(user, rec):
            self.audit("recording_access", "failed", actor=user, subject=rec["owner"] if rec else None,
                       incident_id=iid, details={"recording_id": rid, "reason": "not_permitted"})
            raise self._err(404, "not_found", "ההקלטה לא נמצאה")
        with open(rec["path"], encoding="ascii") as fh:
            data = self.crypto.decrypt(fh.read(), aad=rid.encode())
        self.audit("recording_access", "completed", actor=user, subject=rec["owner"], recipient=user, incident_id=iid,
                   details={"recording_id": rid, "note": "ההקלטה נמסרה לאפליקציה (לא מאשר שהושמעה)"})
        return {"id": rid, "mime": rec["mime"], "sha256": rec["sha256"], "data_b64": data}

    def delete_recording(self, user, rid, reason="owner_request"):
        rec = self._q("SELECT * FROM recordings WHERE id=? AND owner=? AND deleted_at IS NULL", (rid, user), one=True)
        if not rec:
            raise self._err(404, "not_found", "ההקלטה לא נמצאה")
        try:
            size = os.path.getsize(rec["path"])
            with open(rec["path"], "r+b") as fh:
                fh.write(os.urandom(size))
            os.remove(rec["path"])
        except OSError:
            pass
        self._x("UPDATE recordings SET deleted_at=?, path=NULL WHERE id=?", (time.time(), rid))
        self.audit("recording_deleted", actor=user, subject=user, incident_id=rec["incident_id"],
                   details={"recording_id": rid, "reason": reason})
        return {"ok": True}

    def privacy_tick(self, now=None):
        now = now or time.time()
        for r in self._q("SELECT id, owner FROM recordings WHERE deleted_at IS NULL AND uploaded_at<?",
                         (now - RECORDING_RETENTION_DAYS * 86400,)):
            self.delete_recording(r["owner"], r["id"], reason="retention_expired")

    # ================================================================ הורה-ילד
    def _guardian_rows(self, user):
        return self._q("SELECT * FROM guardian_links WHERE (guardian=? OR child=?) AND status!='revoked'", (user, user))

    def _discreet_active(self, g):
        if g["status"] != "active" or not (g["rec_guardian_consent_at"] and g["rec_child_consent_at"]):
            return False
        u = self._q("SELECT guardian_verified FROM users WHERE username=?", (g["guardian"],), one=True)
        return bool(u and u["guardian_verified"])

    def _guardian_view(self, g, user):
        u = self._q("SELECT guardian_verified FROM users WHERE username=?", (g["guardian"],), one=True)
        return {"id": g["id"], "guardian": g["guardian"], "guardian_display": self._display(g["guardian"]),
                "child": g["child"], "child_display": self._display(g["child"]), "my_role":
                "guardian" if g["guardian"] == user else "child", "status": g["status"], "child_age": g["child_age"],
                "guardian_approved": bool(g["guardian_approved_at"]), "child_approved": bool(g["child_approved_at"]),
                "guardian_verified": bool(u and u["guardian_verified"]),
                "recording_consent": {"guardian": bool(g["rec_guardian_consent_at"]),
                                      "child": bool(g["rec_child_consent_at"])},
                "discreet_recording_active": self._discreet_active(g), "created_at": g["created_at"]}

    def guardian_invite(self, user, other, i_am, child_age=None):
        if i_am not in ("guardian", "child"):
            raise self._err(400, "invalid", "תפקיד לא תקין")
        other = str(other or "").lower()
        if other == user or not self._q("SELECT 1 FROM users WHERE username=?", (other,), one=True):
            raise self._err(404, "not_found", "המשתמש/ת לא נמצא/ה")
        guardian, child = (user, other) if i_am == "guardian" else (other, user)
        try:
            age = int(child_age) if child_age is not None else None
        except (TypeError, ValueError):
            age = None
        if age is not None and not 0 <= age <= 17:
            raise self._err(400, "invalid_age", "פרופיל ילד/ה מיועד לגילאי 0-17")
        if self._q("SELECT 1 FROM guardian_links WHERE guardian=? AND child=? AND status!='revoked'",
                   (guardian, child), one=True):
            raise self._err(409, "exists", "כבר קיים קישור")
        gid = "gl_" + secrets.token_hex(10)
        now = time.time()
        self._x("""INSERT INTO guardian_links(id, guardian, child, created_by, status, guardian_approved_at,
                   child_approved_at, child_age, created_at) VALUES (?,?,?,?, 'pending', ?, ?, ?, ?)""",
                (gid, guardian, child, user, now if i_am == "guardian" else None, now if i_am == "child" else None,
                 age, now))
        self._notify(other, "guardian_update", {"event": "invite", "link_id": gid, "from": self._display(user)})
        self.audit("guardian_link_requested", actor=user, subject=child, recipient=other,
                   details={"link_id": gid, "guardian": guardian, "child": child, "child_age": age})
        return self._guardian_view(self._q("SELECT * FROM guardian_links WHERE id=?", (gid,), one=True), user)

    def _guardian_link(self, user, gid):
        g = self._q("SELECT * FROM guardian_links WHERE id=? AND status!='revoked'", (gid,), one=True)
        if not g or user not in (g["guardian"], g["child"]):
            raise self._err(404, "not_found", "הקישור לא נמצא")
        return g

    def guardian_approve(self, user, gid):
        g = self._guardian_link(user, gid)
        col = "guardian_approved_at" if user == g["guardian"] else "child_approved_at"
        self._x(f"UPDATE guardian_links SET {col}=COALESCE({col}, ?) WHERE id=?", (time.time(), gid))
        g = self._q("SELECT * FROM guardian_links WHERE id=?", (gid,), one=True)
        if g["guardian_approved_at"] and g["child_approved_at"] and g["status"] == "pending":
            self._x("UPDATE guardian_links SET status='active' WHERE id=?", (gid,))
        self.audit("guardian_link_approved", actor=user, subject=g["child"], details={"link_id": gid, "by": col[:-12]})
        other = g["child"] if user == g["guardian"] else g["guardian"]
        self._notify(other, "guardian_update", {"event": "approved", "link_id": gid})
        return self._guardian_view(self._q("SELECT * FROM guardian_links WHERE id=?", (gid,), one=True), user)

    def guardian_recording_consent(self, user, gid, consent):
        g = self._guardian_link(user, gid)
        if g["status"] != "active":
            raise self._err(409, "not_active", "הקישור עוד לא אושר ע\"י שני הצדדים")
        col = "rec_guardian_consent_at" if user == g["guardian"] else "rec_child_consent_at"
        self._x(f"UPDATE guardian_links SET {col}=? WHERE id=?", (time.time() if consent else None, gid))
        self.audit("permission_discreet_recording", actor=user, subject=g["child"],
                   details={"link_id": gid, "party": col.split("_")[1], "consent": bool(consent)})
        other = g["child"] if user == g["guardian"] else g["guardian"]
        self._notify(other, "guardian_update", {"event": "recording_consent", "link_id": gid, "consent": bool(consent)})
        return self._guardian_view(self._q("SELECT * FROM guardian_links WHERE id=?", (gid,), one=True), user)

    def guardian_revoke(self, user, gid):
        g = self._guardian_link(user, gid)
        self._x("""UPDATE guardian_links SET status='revoked', revoked_at=?, revoked_by=?, rec_guardian_consent_at=NULL,
                   rec_child_consent_at=NULL WHERE id=?""", (time.time(), user, gid))
        self.audit("guardian_link_revoked", actor=user, subject=g["child"], details={"link_id": gid})
        other = g["child"] if user == g["guardian"] else g["guardian"]
        self._notify(other, "guardian_update", {"event": "revoked", "link_id": gid})
        return {"ok": True}

    def my_guardian_links(self, user):
        return {"links": [self._guardian_view(g, user) for g in self._guardian_rows(user)]}

    def admin_guardian_verify(self, username, verified=True):
        self._x("UPDATE users SET guardian_verified=? WHERE username=?", (int(bool(verified)), username))
        self.audit("guardian_verified" if verified else "guardian_unverified", actor="admin", subject=username)
        return {"username": username, "guardian_verified": bool(verified)}

    # ================================================================ לוח פרטיות
    def privacy_permissions(self, user):
        s = self._settings(user)
        contacts = []
        for r in self._q("SELECT * FROM relationships WHERE owner=? AND status='accepted' ORDER BY contact", (user,)):
            last = self._q("SELECT server_ts, actor FROM audit_events WHERE subject=? AND recipient=? AND action LIKE "
                           "'permission_%' ORDER BY seq DESC LIMIT 1", (user, r["contact"]), one=True)
            contacts.append({"username": r["contact"], "display_name": self._display(r["contact"]),
                             "location_mode": r["location_mode"], "recording_access": bool(r["recording_access"]),
                             "effective_now": ("paused" if s["sharing_paused"] and r["location_mode"] == "always"
                                               else r["location_mode"]),
                             "last_change": last})
        vols = self._q("""SELECT r.username, r.incident_id FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.owner=? AND i.status='active' AND r.role='volunteer' AND r.state='accepted'""", (user,))
        live = self._q("SELECT captured_at, received_at FROM live_locations WHERE username=?", (user,), one=True)
        return {"sharing_paused": bool(s["sharing_paused"]), "record_on_emergency": bool(s["record_on_emergency"]),
                "contacts": contacts,
                "who_can_see_location_now": [c["display_name"] for c in contacts if c["effective_now"] == "always"],
                "volunteers_with_access": [{"display_name": self._display(v["username"]), "username": v["username"],
                                            "incident_id": v["incident_id"]} for v in vols],
                "guardian_links": self.my_guardian_links(user)["links"],
                "recording_policy": self.recording_policy(user),
                "my_live_location": live}

    def privacy_history(self, user, since=None, until=None, contact=None, kind=None, q=None, limit=500):
        sql = "SELECT * FROM audit_events WHERE (subject=? OR actor=?)"
        args = [user, user]
        if since:
            sql += " AND server_ts>=?"; args.append(float(since))
        if until:
            sql += " AND server_ts<=?"; args.append(float(until))
        if contact:
            sql += " AND (actor=? OR recipient=?)"; args += [contact, contact]
        if kind:
            sql += " AND action LIKE ?"; args.append(f"{kind}%")
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(max(1, min(int(limit or 500), 2000)))
        out = []
        for r in self._q(sql, tuple(args)):
            d = json.loads(r["details"] or "{}")
            item = {k: r[k] for k in ("event_id", "server_ts", "device_ts", "actor", "subject", "recipient", "action",
                                      "result", "via", "permission", "incident_id", "source")}
            item["details"] = d
            if r["source"] == "device":   # המזהה שהמכשיר שלח (בשרת נשמר עם קידומת למניעת התנגשויות)
                item["client_event_id"] = r["event_id"].split("_", 2)[-1] if r["event_id"].startswith("dev_")                     else r["event_id"]
            item["actor_display"] = self._display(r["actor"]) if r["actor"] and r["actor"] != "admin" else r["actor"]
            item["recipient_display"] = self._display(r["recipient"]) if r["recipient"] else None
            if r["action"] == "location_access" and r["result"] == "completed":
                s = self._q("SELECT first_at, last_at, count FROM location_access_sessions WHERE audit_event_id=?",
                            (r["event_id"],), one=True)
                if s:
                    item["access_period"] = {"from": s["first_at"], "to": s["last_at"], "requests": s["count"],
                                             "seconds": round(s["last_at"] - s["first_at"])}
            if r["action"] == "recording_recipient_notified" and d.get("notification_id"):
                n = self._q("SELECT received_at FROM notifications WHERE id=?", (d["notification_id"],), one=True)
                item["delivery"] = ("נמסר לאפליקציה" if n and n["received_at"] else "ממתין לאישור קבלה")
                item["result_effective"] = "completed" if n and n["received_at"] else "pending"
            if q and q not in json.dumps(item, ensure_ascii=False):
                continue
            out.append(item)
        return {"events": out, "count": len(out)}

    def privacy_export(self, user):
        hist = self.privacy_history(user, limit=2000)
        perms = self.privacy_permissions(user)
        record = {"format": "lifeguard-privacy-report-v1", "generated_at": time.time(), "user": user,
                  "permissions": perms, "history": hist["events"],
                  "notes": ["server_ts = זמן שרת; device_ts = שעון המכשיר (לא מאומת)",
                            "location_access = המיקום נמסר לאפליקציה של הצד השני; אין הוכחה שהוצג על המסך"]}
        body = _canon(record).encode("utf-8")
        key = self._evidence_key()
        self.audit("privacy_report_exported", actor=user, subject=user)
        return {"record": record, "sha256": hashlib.sha256(body).hexdigest(), "signature": key.sign(body).hex(),
                "public_key": self.evidence_public_key()["public_key"], "algorithm": "Ed25519"}

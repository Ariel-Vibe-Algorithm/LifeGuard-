# -*- coding: utf-8 -*-
"""
features.py - יכולות שרת נוספות (mixin של LifeGuardService):

1. ליווי הביתה משותף (escorts): הטיימר נאכף *בשרת* - גם אם המחשב/הטלפון כבה או אבד, בתום הזמן
   ותקופת חסד השרת פותח אירוע חירום בעצמו עם המיקום האחרון.
2. אמון במתנדבים: בקשת אימות (ארגון + מספר תעודה) שמאושרת ידנית ע"י מנהל, דירוג ודיווח אחרי
   אירוע, השעיה אוטומטית אחרי דיווחים, ומגבלה למתנדבים לא מאומתים.
3. מידע רפואי לחירום: מוצפן במנוחה (AES-GCM), מוצג רק למי שיש לו גישה מלאה לאירוע פעיל,
   ורק אם המשתמש בחר לשתף.
4. גיבוי SMS: איש קשר שהאפליקציה שלו לא אישרה קבלה תוך זמן קצוב מקבל SMS עם קישור מיקום
   (רק למספר שאומת). אימות טלפון ב-SMS. הכל דרך ספק SMS שמוגדר בסביבה - בלעדיו: "לא מוגדר".
5. Web Push לאפליקציית הנייד (PWA).
6. בדיקת מוכנות: הודעת בדיקה לכל אנשי הקשר ומעקב מי אישר קבלה, נשמר בשרת.
"""

import hashlib
import json
import queue
import secrets
import sys
import threading
import time

from server import webpush

ESCORT_MAX_MINUTES = 24 * 60
ESCORT_GRACE_SECONDS = 90          # אחרי תום הזמן: הודעה למשתמש "הכל בסדר?" ואז אירוע
UNVERIFIED_MONTHLY_ACCEPTS = 3      # מתנדב לא מאומת: עד 3 קבלות אירוע ב-30 יום
REPORTS_TO_SUSPEND = 2              # דיווחים ממשתמשים שונים ב-90 יום -> השעיה עד בדיקה
REPORTER_MIN_AGE_DAYS = 3
MAX_PUSH_SUBS = 5                   # מכשירים עם Web Push למשתמש           # רק חשבונות ותיקים נספרים (חשבונות זמניים לא משעים מתנדבים)
SMS_FALLBACK_SECONDS = 60
LOCATION_LINK_TTL_SECONDS = 24 * 3600
MEDICAL_FIELDS = ("blood_type", "allergies", "medications", "conditions", "emergency_medical_contact", "notes")
PUSH_TYPES = {"incident_new", "relationship_invite", "escort_started", "escort_overdue", "escort_alarm", "test",
              "readiness_check", "incident_closed", "escort_check"}


def _now():
    return time.time()


def _h(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


MIGRATION_4 = """
CREATE TABLE escorts(
    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(username), destination TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL CHECK(mode IN ('loud','silent')),
    status TEXT NOT NULL CHECK(status IN ('active','arrived','cancelled','escalated')),
    started_at REAL NOT NULL, deadline REAL NOT NULL, ended_at REAL, overdue_notified INTEGER NOT NULL DEFAULT 0,
    incident_id TEXT, last_lat REAL, last_lon REAL, last_precise INTEGER, last_loc_at REAL);
CREATE TABLE escort_watchers(escort_id TEXT NOT NULL, username TEXT NOT NULL, PRIMARY KEY(escort_id, username));
ALTER TABLE volunteers ADD COLUMN verification TEXT NOT NULL DEFAULT 'unverified';
ALTER TABLE volunteers ADD COLUMN verification_org TEXT;
ALTER TABLE volunteers ADD COLUMN verification_ref TEXT;
ALTER TABLE volunteers ADD COLUMN verification_requested_at REAL;
ALTER TABLE volunteers ADD COLUMN verification_note TEXT;
ALTER TABLE volunteers ADD COLUMN verified_at REAL;
ALTER TABLE volunteers ADD COLUMN suspended INTEGER NOT NULL DEFAULT 0;
CREATE TABLE volunteer_feedback(
    incident_id TEXT NOT NULL, volunteer TEXT NOT NULL, rater TEXT NOT NULL,
    rating INTEGER CHECK(rating BETWEEN 1 AND 5), report TEXT, created_at REAL NOT NULL,
    PRIMARY KEY(incident_id, volunteer, rater));
CREATE TABLE medical_profiles(username TEXT PRIMARY KEY, data_enc TEXT NOT NULL, share INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL);
ALTER TABLE users ADD COLUMN phone_enc TEXT;
ALTER TABLE users ADD COLUMN phone_verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN sms_fallback INTEGER NOT NULL DEFAULT 1;
ALTER TABLE recipients ADD COLUMN sms_sent_at REAL;
ALTER TABLE recipients ADD COLUMN sms_status TEXT;
CREATE TABLE location_links(token_hash TEXT PRIMARY KEY, incident_id TEXT NOT NULL, username TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, opened_at REAL);
CREATE TABLE sms_log(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, incident_id TEXT, kind TEXT NOT NULL,
    status TEXT NOT NULL, provider_ref TEXT, error TEXT, created_at REAL NOT NULL);
CREATE TABLE push_subscriptions(endpoint TEXT PRIMARY KEY, username TEXT NOT NULL, p256dh TEXT NOT NULL,
    auth TEXT NOT NULL, created_at REAL NOT NULL, last_ok REAL, last_error TEXT);
CREATE TABLE readiness_checks(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, created_at REAL NOT NULL,
    notification_ids TEXT NOT NULL);
"""


class FeaturesMixin:
    # ================================================================ init / infra
    def _init_features(self, sms_provider, public_url, escort_grace, sms_fallback_seconds):
        from server.crypto_box import CryptoBox
        self.crypto = CryptoBox.for_db(self.db_path)
        self.sms = sms_provider
        self.public_url = (public_url or "").rstrip("/")
        self.escort_grace = float(escort_grace)
        self.sms_fallback_seconds = float(sms_fallback_seconds)
        self.vapid = self._load_vapid()
        self.push_sender = webpush.send  # ניתן להחלפה בבדיקות; ברירת מחדל - HTTP אמיתי לשרת ה-Push
        self._push_q = queue.Queue()
        threading.Thread(target=self._push_worker, name="LG-WebPush", daemon=True).start()

    def _load_vapid(self):
        # שירות הפוש של אפל (iOS) עלול לדחות sub מקומי - בייצור: LIFEGUARD_VAPID_SUBJECT=mailto:you@domain או URL https
        import os
        subject = os.environ.get("LIFEGUARD_VAPID_SUBJECT", "").strip() or self.public_url or "mailto:lifeguard@localhost"
        row = self._q("SELECT value FROM server_meta WHERE key='vapid_private'", one=True)
        if row:
            return webpush.Vapid.from_pem(self.crypto.decrypt(row["value"]), subject)
        v = webpush.Vapid.generate(subject)
        self._x("INSERT INTO server_meta(key, value) VALUES ('vapid_private', ?)", (self.crypto.encrypt(v.to_pem()),))
        return v

    FCM_NONURGENT_GAP_S = 10

    def _after_notify(self, username, ntype, payload):
        """נקרא מ-_notify אחרי שההתראה נשמרה: Web Push (סוגים נבחרים) + השכמת FCM לאנדרואיד (כל סוג,
        כי האפליקציה שולפת הכל מהשרת; עדכונים לא דחופים - לכל היותר השכמה אחת ל-10 שניות). ברקע."""
        if ntype in PUSH_TYPES or self._fcm().configured:
            self._push_q.put((username, ntype, payload))

    def _push_worker(self):
        while True:
            item = self._push_q.get()
            if item is None:          # close(): השירות נסגר - ה-thread מסתיים (לא דולף)
                return
            username, ntype, payload = item
            try:
                self._fcm_dispatch(username, ntype, payload)
            except Exception as e:  # noqa: BLE001 - FCM לא מפיל את Web Push
                sys.stderr.write(f"[fcm] {type(e).__name__}: {e}\n")
            if ntype not in PUSH_TYPES:
                self._push_q.task_done()
                continue
            try:
                subs = self._q("SELECT * FROM push_subscriptions WHERE username=?", (username,))
                data = _push_payload(ntype, payload)
                for s in subs:
                    code, err = self.push_sender(self.vapid, s, data,
                                                 urgency="high" if (ntype in ("incident_new", "escort_alarm")
                                                                    and payload.get("ring", True)) else "normal")
                    if code in (200, 201, 202):
                        self._x("UPDATE push_subscriptions SET last_ok=?, last_error=NULL WHERE endpoint=?",
                                (_now(), s["endpoint"]))
                    elif code in (404, 410):  # המנוי בוטל בדפדפן
                        self._x("DELETE FROM push_subscriptions WHERE endpoint=?", (s["endpoint"],))
                    else:
                        self._x("UPDATE push_subscriptions SET last_error=? WHERE endpoint=?",
                                (f"{code} {err or ''}"[:200], s["endpoint"]))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[webpush] {type(e).__name__}: {e}\n")
            finally:
                self._push_q.task_done()

    def _fcm_dispatch(self, username, ntype, payload):
        from server.fcm import URGENT_TYPES
        if not self._fcm().configured:
            return
        urgent = ntype in URGENT_TYPES
        last = getattr(self, "_fcm_last", None)
        if last is None:
            last = self._fcm_last = {}
        now = _now()
        if not urgent and now - last.get(username, 0) < self.FCM_NONURGENT_GAP_S:
            return
        last[username] = now
        self._fcm_wake(username, ntype, payload)

    def features_tick(self, now=None):
        now = now or _now()
        self._ride_tick(now)       # קודם: נסיעה שמתקדמת מקבלת הארכה לפני שהטיימר הכללי מסלים
        self._escort_tick(now)
        if now - getattr(self, "_privacy_tick_at", 0) > 60:   # שמירה/מחיקת הקלטות - פעם בדקה מספיק
            self._privacy_tick_at = now
            self.privacy_tick(now)
        self._sms_fallback_tick(now)

    # ================================================================ 5. Web Push
    def push_public_key(self):
        return {"public_key": self.vapid.public_key_b64}

    def push_subscribe(self, user, sub):
        sub = sub or {}
        endpoint = str(sub.get("endpoint") or "")
        keys = sub.get("keys") or {}
        if not webpush.endpoint_allowed(endpoint):
            raise self._err(400, "push_endpoint", "שרת Push לא מוכר")
        try:
            if len(webpush.b64u_dec(keys.get("p256dh", ""))) != 65 or len(webpush.b64u_dec(keys.get("auth", ""))) < 16:
                raise ValueError
        except Exception:
            raise self._err(400, "push_keys", "מפתחות מנוי לא תקינים")
        # עד MAX_PUSH_SUBS מנויים למשתמש (300 מנויים של משתמש אחד עיכבו SOS של אחרת ב-29 שנ' - סקירה 2)
        old = self._q("SELECT endpoint FROM push_subscriptions WHERE username=? AND endpoint!=? ORDER BY created_at DESC",
                      (user, endpoint))
        for r in old[MAX_PUSH_SUBS - 1:]:
            self._x("DELETE FROM push_subscriptions WHERE endpoint=?", (r["endpoint"],))
        self._x("""INSERT INTO push_subscriptions(endpoint, username, p256dh, auth, created_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(endpoint) DO UPDATE SET username=excluded.username, p256dh=excluded.p256dh,
                   auth=excluded.auth""", (endpoint, user, keys["p256dh"], keys["auth"], _now()))
        return {"ok": True}

    def push_unsubscribe(self, user, endpoint):
        self._x("DELETE FROM push_subscriptions WHERE endpoint=? AND username=?", (endpoint or "", user))
        return {"ok": True}

    def push_status(self, user):
        rows = self._q("SELECT created_at, last_ok, last_error FROM push_subscriptions WHERE username=?", (user,))
        return {"devices": len(rows), "subscriptions": rows}

    # ================================================================ 1. ליווי הביתה
    def start_escort(self, user, minutes, destination="", mode="loud", location=None, kind="walk",
                     ride_type=None, destination_point=None, vehicle=None):
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise self._err(400, "invalid_minutes", "מספר דקות לא תקין")
        if not 1 <= minutes <= ESCORT_MAX_MINUTES:
            raise self._err(400, "invalid_minutes", "בין דקה ל-24 שעות")
        if kind == "ride":   # בדיקה לפני יצירה - לא משאירים "נסיעה" חצי-נוצרת
            from server.rides import RIDE_TYPES
            if ride_type not in RIDE_TYPES:
                raise self._err(400, "invalid_ride_type", "סוג נסיעה לא תקין")
            if not isinstance(destination_point, dict):
                raise self._err(400, "destination_required", "בנסיעה בטוחה נדרש יעד (נקודה)")
            self._valid_coords(destination_point.get("lat"), destination_point.get("lon"))
        if self._q("SELECT 1 FROM escorts WHERE owner=? AND status='active'", (user,), one=True):
            raise self._err(409, "escort_active", "כבר יש ליווי פעיל")
        eid = "esc_" + secrets.token_hex(16)
        now = _now()
        lat = lon = None
        if location:
            lat, lon = self._valid_coords(location.get("lat"), location.get("lon"))
        # המיקום נשמר באותו INSERT - עוקב/ת לעולם לא רואה ליווי "בלי מיקום" לרגע (נמצא בבדיקה)
        self._x("""INSERT INTO escorts(id, owner, destination, mode, status, started_at, deadline, last_lat, last_lon,
                   last_precise, last_loc_at) VALUES (?,?,?,?,'active',?,?,?,?,?,?)""",
                (eid, user, str(destination or "")[:80], "silent" if mode == "silent" else "loud", now,
                 now + minutes * 60, lat, lon, int(bool(location and location.get("is_precise"))),
                 now if location else None))
        if kind == "ride":
            self._ride_init(eid, ride_type, destination_point, vehicle, location)
        watchers = [r["contact"] for r in self._q(
            "SELECT contact FROM relationships WHERE owner=? AND status='accepted'", (user,))]
        for w in watchers:
            self._x("INSERT OR IGNORE INTO escort_watchers VALUES (?,?)", (eid, w))
        for w in watchers:
            self._notify(w, "escort_started", self._escort_view(eid, w))
        return self._escort_view(eid, user)

    def _escort_row(self, eid):
        return self._q("SELECT * FROM escorts WHERE id=?", (str(eid or ""),), one=True)

    def _escort_access(self, user, eid):
        e = self._escort_row(eid)
        if not e:
            raise self._err(404, "not_found", "הליווי לא נמצא")
        if e["owner"] == user:
            return e, "owner"
        if self._q("SELECT 1 FROM escort_watchers WHERE escort_id=? AND username=?", (eid, user), one=True) \
                and self._q("SELECT 1 FROM relationships WHERE owner=? AND contact=? AND status='accepted'",
                            (e["owner"], user), one=True):
            return e, "watcher"
        raise self._err(404, "not_found", "הליווי לא נמצא")

    def _escort_view(self, eid, user):
        e = self._escort_row(eid)
        out = {k: e[k] for k in ("id", "owner", "destination", "mode", "status", "started_at", "deadline",
                                 "ended_at", "incident_id")}
        out["owner_display"] = self._display(e["owner"])
        out["remaining_seconds"] = max(0, e["deadline"] - _now()) if e["status"] == "active" else 0
        out["overdue"] = e["status"] == "active" and _now() > e["deadline"]
        out["signal_lost_since"] = e["signal_lost_at"]          # הטלפון הפסיק לדווח (אין קליטה?)
        show = e["owner"] == user or self.location_mode_for(e["owner"], user) != "off"
        if e["last_lat"] is not None and show:
            out["location"] = {"lat": e["last_lat"], "lon": e["last_lon"], "is_precise": bool(e["last_precise"]),
                               "updated_at": e["last_loc_at"],
                               "stale": _now() - (e["last_loc_at"] or 0) > 300}
            if e["owner"] != user and e["status"] == "active":
                self._note_location_access(user, e["owner"], "escort", None, self.location_mode_for(e["owner"], user))
        elif not show:
            out["location_withheld"] = True
        if e["owner"] == user:
            out["watchers"] = [{"username": w["username"], "display_name": self._display(w["username"])}
                               for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (eid,))]
        self._ride_view(e, out, user)
        return out

    def get_escort(self, user, eid):
        self._escort_access(user, eid)
        return self._escort_view(eid, user)

    def escorts_for(self, user):
        mine = self._q("SELECT id FROM escorts WHERE owner=? AND status='active'", (user,))
        watching = self._q("""SELECT e.id FROM escorts e JOIN escort_watchers w ON w.escort_id=e.id
                              JOIN relationships r ON r.owner=e.owner AND r.contact=w.username AND r.status='accepted'
                              WHERE w.username=? AND e.status='active' ORDER BY e.deadline""", (user,))
        return {"mine": [self._escort_view(r["id"], user) for r in mine],
                "watching": [self._escort_view(r["id"], user) for r in watching]}

    def escort_location(self, user, eid, loc):
        e, role = self._escort_access(user, eid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל הליווי מעדכן מיקום")
        if e["status"] != "active":
            raise self._err(409, "closed", "הליווי הסתיים")
        lat, lon = self._valid_coords(loc.get("lat"), loc.get("lon"))
        self._x("UPDATE escorts SET last_lat=?, last_lon=?, last_precise=?, last_loc_at=? WHERE id=?",
                (lat, lon, int(bool(loc.get("is_precise"))), _now(), eid))
        self.signal_back("escort", eid)
        if e["kind"] == "ride":
            self._ride_track(eid, {**loc, "lat": lat, "lon": lon})
            self._ride_evaluate(eid)
        for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (eid,)):
            self._notify(w["username"], "escort_update", {"escort_id": eid, "event": "location"})
        return {"ok": True}

    def escort_action(self, user, eid, action, minutes=None):
        e, role = self._escort_access(user, eid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל הליווי")
        if action == "extend":
            try:
                minutes = float(minutes)
            except (TypeError, ValueError):
                raise self._err(400, "invalid_minutes", "מספר דקות לא תקין")
            if not 1 <= minutes <= 240:
                raise self._err(400, "invalid_minutes", "הארכה בין דקה ל-4 שעות")
            cur = self._x("UPDATE escorts SET deadline=MAX(deadline, ?) + ?, overdue_notified=0 WHERE id=? AND status='active'",
                          (_now(), minutes * 60, eid))
            event = "extended"
        elif action in ("arrived", "cancelled"):
            cur = self._x("UPDATE escorts SET status=?, ended_at=? WHERE id=? AND status='active'", (action, _now(), eid))
            event = action
        else:
            raise self._err(400, "invalid_action", "פעולה לא חוקית")
        if not cur.rowcount:
            raise self._err(409, "closed", "הליווי כבר הסתיים")
        if event == "arrived":
            text = f"✅ {self._display(user)} הגיע/ה בשלום"
        elif event == "cancelled":
            text = f"{self._display(user)} ביטל/ה את הליווי"
        else:
            text = f"⏳ {self._display(user)} האריך/ה את הליווי ב-{minutes:.0f} דקות"
        for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (eid,)):
            self._notify(w["username"], "escort_update", {"escort_id": eid, "event": event, "text": text})
        return self._escort_view(eid, user)

    def link_escort_incident(self, owner, eid, incident_id):
        """המכשיר עצמו פתח SOS בתום הטיימר - השרת לא יפתח אירוע כפול."""
        self._x("UPDATE escorts SET status='escalated', ended_at=?, incident_id=? WHERE id=? AND owner=? AND status='active'",
                (_now(), incident_id, eid, owner))

    def _escort_tick(self, now):
        for e in self._q("SELECT * FROM escorts WHERE status='active' AND deadline<=?", (now,)):
            if not e["overdue_notified"]:
                self._x("UPDATE escorts SET overdue_notified=1 WHERE id=?", (e["id"],))
                # ליווי שקט: טקסט ניטרלי - מי שמחזיק את הטלפון לא אמור להבין שמדובר בחירום
                text = ("תזכורת מ-LifeGuard - פתח/י את האפליקציה" if e["mode"] == "silent" else
                        "זמן הליווי הסתיים. הכל בסדר? אשר/י הגעה או הארך/י - "
                        f"אחרת תיפתח קריאת חירום בעוד {int(self.escort_grace)} שניות")
                self._notify(e["owner"], "escort_overdue", {"escort_id": e["id"], "text": text,
                                                            "silent": e["mode"] == "silent"})
            if now < e["deadline"] + self.escort_grace:
                continue
            cur = self._x("UPDATE escorts SET status='escalated', ended_at=? WHERE id=? AND status='active'", (now, e["id"]))
            if not cur.rowcount:
                continue
            loc = None
            if e["last_lat"] is not None:
                loc = {"lat": e["last_lat"], "lon": e["last_lon"], "is_precise": bool(e["last_precise"]),
                       "source": "escort_last_known", "captured_at": e["last_loc_at"]}
            inc = self.create_incident(e["owner"], f"escort:{e['id']}", mode=e["mode"], emergency_type="unknown",
                                       severity="high", location=loc, profile={"display_name": self._display(e["owner"])},
                                       risk={"level": "MEDIUM", "score": None,
                                             "reasons": ["לא אושרה הגעה בתום ליווי הביתה ולא הייתה תגובה"]},
                                       trigger="server_timer")
            dest = f" ל{e['destination']}" if e["destination"] else ""
            self._event(inc["id"], "escort_expired",
                        f"ליווי הביתה{dest} הסתיים בלי אישור הגעה - השרת פתח קריאת חירום אוטומטית"
                        + ("" if loc else " (אין מיקום ידוע)"))
            self._x("UPDATE escorts SET incident_id=? WHERE id=?", (inc["id"], e["id"]))
            for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (e["id"],)):
                self._notify(w["username"], "escort_alarm", {"escort_id": e["id"], "incident_id": inc["id"],
                             "text": f"🚨 {self._display(e['owner'])} לא אישר/ה הגעה{dest} - נפתחה קריאת חירום"})
            if e["mode"] != "silent":   # בליווי שקט - שום התראת "חירום" על מסך הנעילה של הבעלים
                self._notify(e["owner"], "escort_alarm", {"escort_id": e["id"], "incident_id": inc["id"]})

    # ================================================================ 2. אמון במתנדבים
    def request_verification(self, user, org, ref):
        org, ref = str(org or "").strip()[:80], str(ref or "").strip()[:60]
        if len(org) < 2 or len(ref) < 2:
            raise self._err(400, "invalid", "יש לציין ארגון ומספר תעודה/חבר")
        if not self._q("SELECT 1 FROM volunteers WHERE username=?", (user,), one=True):
            self.set_volunteer(user, False)
        cur = self._q("SELECT verification FROM volunteers WHERE username=?", (user,), one=True)
        if cur["verification"] == "verified":
            raise self._err(409, "already_verified", "כבר מאומת/ת")
        self._x("""UPDATE volunteers SET verification='pending', verification_org=?, verification_ref=?,
                   verification_requested_at=?, verification_note=NULL WHERE username=?""", (org, ref, _now(), user))
        return self.me(user)["volunteer"]

    def admin_volunteers(self, status=None):
        q = "SELECT username, available, verification, verification_org, verification_ref, verification_requested_at, " \
            "verification_note, verified_at, suspended, review_flag FROM volunteers"
        rows = self._q(q + (" WHERE verification=?" if status else "") + " ORDER BY verification_requested_at",
                       (status,) if status else ())
        for r in rows:
            r["display_name"] = self._display(r["username"])
            r["feedback"] = self.volunteer_stats(r["username"])
        return rows

    def admin_verify(self, username, approve, note=""):
        v = self._q("SELECT verification FROM volunteers WHERE username=?", (username,), one=True)
        if not v:
            raise self._err(404, "not_found", "מתנדב לא נמצא")
        status = "verified" if approve else "rejected"
        self._x("UPDATE volunteers SET verification=?, verification_note=?, verified_at=? WHERE username=?",
                (status, str(note or "")[:200], _now() if approve else None, username))
        self._notify(username, "info", {"from": "LifeGuard", "from_display": "LifeGuard",
                                        "text": "✅ אומתת כמתנדב/ת" if approve else
                                        f"בקשת האימות נדחתה{': ' + note if note else ''}"})
        self._wake_dispatch()      # הזכאות של המתנדב השתנתה - אירועים ממתינים נבדקים מיד
        return {"username": username, "verification": status}

    def admin_set_suspended(self, username, suspended):
        cur = self._x("UPDATE volunteers SET suspended=? WHERE username=?", (int(bool(suspended)), username))
        if not cur.rowcount:
            raise self._err(404, "not_found", "מתנדב לא נמצא")
        self._wake_dispatch()      # הזכאות של המתנדב השתנתה - אירועים ממתינים נבדקים מיד
        return {"username": username, "suspended": bool(suspended)}

    def volunteer_stats(self, username):
        r = self._q("""SELECT COUNT(rating) AS n, AVG(rating) AS avg,
                       SUM(CASE WHEN report IS NOT NULL AND report!='' THEN 1 ELSE 0 END) AS reports
                       FROM volunteer_feedback WHERE volunteer=?""", (username,), one=True)
        return {"ratings": r["n"] or 0, "avg_rating": round(r["avg"], 2) if r["avg"] else None,
                "reports": r["reports"] or 0}

    def _volunteer_eligible(self, v, now=None):
        """לפני שליחת קריאה: מושעה - לא. לא מאומת - עד UNVERIFIED_MONTHLY_ACCEPTS קבלות ב-30 יום."""
        if v.get("suspended"):
            return False
        if not self.identity_ok(v["username"]):
            return False                         # אימות זהות חובה (server/volunteer_identity.py)
        if v.get("verification") == "verified":
            return True
        now = now or _now()
        # כל אירוע שהמתנדב קיבל (גם אם נסגר מאז ומצבו revoked) נספר פעם אחת
        n = self._q("""SELECT COUNT(*) AS n FROM recipients WHERE username=? AND role='volunteer'
                       AND response IN ('accept','on_my_way','arrived','handled') AND responded_at>?""",
                    (v["username"], now - 30 * 86400), one=True)["n"]
        return n < UNVERIFIED_MONTHLY_ACCEPTS

    def give_feedback(self, user, iid, volunteer, rating=None, report=None):
        inc = self._incident_row(iid)
        if not inc or inc["owner"] != user:
            raise self._err(404, "not_found", "האירוע לא נמצא")
        rec = self._q("""SELECT 1 FROM recipients WHERE incident_id=? AND username=? AND role='volunteer'
                         AND response IN ('accept','on_my_way','arrived','handled')""", (iid, volunteer), one=True)
        if not rec:
            raise self._err(400, "not_responder", "אפשר לדרג רק מתנדב שקיבל את האירוע")
        if rating is not None:
            try:
                rating = int(rating)
            except (TypeError, ValueError):
                rating = 0
            if not 1 <= rating <= 5:
                raise self._err(400, "invalid_rating", "דירוג בין 1 ל-5")
        report = str(report or "").strip()[:500] or None
        if rating is None and not report:
            raise self._err(400, "empty", "יש לדרג או לדווח")
        self._x("""INSERT INTO volunteer_feedback(incident_id, volunteer, rater, rating, report, created_at)
                   VALUES (?,?,?,?,?,?) ON CONFLICT(incident_id, volunteer, rater) DO UPDATE SET
                   rating=excluded.rating, report=excluded.report, created_at=excluded.created_at""",
                (iid, volunteer, user, rating, report, _now()))
        suspended = False
        if report:
            # דיווחים "אמינים" בלבד (סקירת אבטחה: שני חשבונות זמניים השעו מתנדב מאומת): חשבון בן
            # REPORTER_MIN_AGE_DAYS לפחות, ועל אירוע שלא בוטל ע"י המדווח עצמו
            raters = self._q("""SELECT COUNT(DISTINCT f.rater) AS n FROM volunteer_feedback f
                                JOIN users u ON u.username=f.rater JOIN incidents i ON i.id=f.incident_id
                                WHERE f.volunteer=? AND f.report IS NOT NULL AND f.report!='' AND f.created_at>?
                                AND u.created_at<? AND i.status!='cancelled'""",
                             (volunteer, _now() - 90 * 86400, _now() - REPORTER_MIN_AGE_DAYS * 86400), one=True)["n"]
            if raters >= REPORTS_TO_SUSPEND:
                v = self._q("SELECT verification FROM volunteers WHERE username=?", (volunteer,), one=True)
                if v and v["verification"] == "verified":
                    # מאומת - לא מושעה אוטומטית; מסומן לבדיקת מנהל
                    self._x("UPDATE volunteers SET review_flag=1 WHERE username=?", (volunteer,))
                else:
                    self._x("UPDATE volunteers SET suspended=1 WHERE username=?", (volunteer,))
                    suspended = True
        return {"ok": True, "volunteer_suspended": suspended}

    # ================================================================ 3. מידע רפואי
    def set_medical(self, user, data):
        data = data or {}
        clean = {k: str(data.get(k) or "").strip()[:500] for k in MEDICAL_FIELDS}
        share = 1 if data.get("share") else 0
        enc = self.crypto.encrypt(json.dumps(clean, ensure_ascii=False), aad=user.encode())
        self._x("""INSERT INTO medical_profiles(username, data_enc, share, updated_at) VALUES (?,?,?,?)
                   ON CONFLICT(username) DO UPDATE SET data_enc=excluded.data_enc, share=excluded.share,
                   updated_at=excluded.updated_at""", (user, enc, share, _now()))
        return self.get_medical(user)

    def get_medical(self, user):
        r = self._q("SELECT * FROM medical_profiles WHERE username=?", (user,), one=True)
        if not r:
            return {**{k: "" for k in MEDICAL_FIELDS}, "share": False, "updated_at": None}
        data = json.loads(self.crypto.decrypt(r["data_enc"], aad=user.encode()))
        return {**data, "share": bool(r["share"]), "updated_at": r["updated_at"]}

    def _medical_for_incident(self, owner):
        """רק אם המשתמש בחר לשתף ויש תוכן. הקורא (_view_for) מוודא גישה מלאה + אירוע פעיל."""
        m = self.get_medical(owner)
        if not m["share"]:
            return None
        fields = {k: m[k] for k in MEDICAL_FIELDS if m.get(k)}
        return fields or None

    # ================================================================ 4. SMS
    def sms_status(self):
        return {"configured": bool(self.sms.configured), "provider": self.sms.name,
                "can_verify": bool(getattr(self.sms, "can_verify", False))}

    def _log_sms(self, username, incident_id, kind, status, ref=None, error=None):
        self._x("INSERT INTO sms_log(username, incident_id, kind, status, provider_ref, error, created_at) "
                "VALUES (?,?,?,?,?,?,?)", (username, incident_id, kind, status, ref, error, _now()))

    def _user_phone(self, username):
        r = self._q("SELECT phone_enc, phone_verified, sms_fallback FROM users WHERE username=?", (username,), one=True)
        if not r or not r["phone_enc"]:
            return None, False, False
        return self.crypto.decrypt(r["phone_enc"], aad=username.encode()), bool(r["phone_verified"]), bool(r["sms_fallback"])

    def phone_verify_start(self, user):
        phone, verified, _ = self._user_phone(user)
        if not phone:
            raise self._err(400, "no_phone", "לא הוגדר מספר טלפון")
        if not getattr(self.sms, "can_verify", False):
            raise self._err(503, "sms_not_configured", "אימות SMS לא מוגדר בשרת הזה")
        from server.sms import SmsError
        try:
            self.sms.start_verification(phone)
        except SmsError as e:
            self._log_sms(user, None, "verify", "error", error=str(e))
            raise self._err(502, "sms_failed", f"שליחת קוד האימות נכשלה: {e}")
        self._log_sms(user, None, "verify", "sent")
        return {"sent": True, "phone_last4": phone[-4:]}

    def phone_verify_check(self, user, code):
        phone, _, _ = self._user_phone(user)
        if not phone:
            raise self._err(400, "no_phone", "לא הוגדר מספר טלפון")
        if not getattr(self.sms, "can_verify", False):
            raise self._err(503, "sms_not_configured", "אימות SMS לא מוגדר בשרת הזה")
        from server.sms import SmsError
        try:
            ok = self.sms.check_verification(phone, str(code or "").strip()[:10])
        except SmsError as e:
            raise self._err(502, "sms_failed", str(e))
        if not ok:
            raise self._err(400, "wrong_code", "קוד שגוי או שפג תוקפו")
        self._x("UPDATE users SET phone_verified=1 WHERE username=?", (user,))
        self._deliver_phone_invites(user)
        return {"phone_verified": True}

    def set_sms_fallback(self, user, enabled):
        self._x("UPDATE users SET sms_fallback=? WHERE username=?", (int(bool(enabled)), user))
        return {"sms_fallback": bool(enabled)}

    def send_invite_sms(self, inviter, e164, code, expires_at):
        if not self.sms.configured:
            return {"sms": "not_configured"}
        from server.sms import SmsError
        text = (f"LifeGuard: {self._display(inviter)} מזמין/ה אותך להיות איש קשר לחירום. "
                f"התקן/י את LifeGuard והזן/י את הקוד {code} (חד-פעמי, בתוקף 7 ימים)."
                + (f" {self.public_url}/app/" if self.public_url else ""))
        try:
            ref = self.sms.send(e164, text)
            self._log_sms(inviter, None, "invite", "sent", ref)
            return {"sms": "sent"}
        except SmsError as e:
            self._log_sms(inviter, None, "invite", "error", error=str(e))
            return {"sms": "error", "sms_error": str(e)}

    def _sms_fallback_tick(self, now):
        """איש קשר מהימן שהאפליקציה שלו לא אישרה קבלה תוך sms_fallback_seconds -> SMS עם קישור מיקום."""
        if not self.sms.configured:
            return
        rows = self._q("""SELECT r.incident_id, r.username, i.owner FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.status='active' AND r.role='trusted' AND r.state='notified'
                          AND r.sms_sent_at IS NULL AND r.notified_at<?""", (now - self.sms_fallback_seconds,))
        from server.sms import SmsError
        for r in rows:
            cur = self._x("""UPDATE recipients SET sms_sent_at=?, sms_status='pending' WHERE incident_id=? AND username=?
                             AND sms_sent_at IS NULL""", (now, r["incident_id"], r["username"]))
            if not cur.rowcount:
                continue
            phone, verified, enabled = self._user_phone(r["username"])
            if not phone or not verified or not enabled:
                why = "אין מספר מאומת" if not (phone and verified) else "גיבוי SMS כבוי אצל איש הקשר"
                self._x("UPDATE recipients SET sms_status='skipped' WHERE incident_id=? AND username=?",
                        (r["incident_id"], r["username"]))
                self._event(r["incident_id"], "sms_skipped", f"לא נשלח SMS ל{self._display(r['username'])}: {why}")
                continue
            token = secrets.token_urlsafe(24)
            self._x("INSERT INTO location_links VALUES (?,?,?,?,?,NULL)",
                    (_h(token), r["incident_id"], r["username"], now, now + LOCATION_LINK_TTL_SECONDS))
            link = f"{self.public_url}/l/{token}" if self.public_url else f"/l/{token}"
            text = f"LifeGuard: {self._display(r['owner'])} שלח/ה קריאת חירום. מיקום ופרטים: {link}"
            try:
                ref = self.sms.send(phone, text)
                self._x("UPDATE recipients SET sms_status='sent' WHERE incident_id=? AND username=?",
                        (r["incident_id"], r["username"]))
                self._log_sms(r["username"], r["incident_id"], "fallback", "sent", ref)
                self._event(r["incident_id"], "sms_sent", f"האפליקציה של {self._display(r['username'])} לא אישרה קבלה - "
                                                          "נשלח SMS עם קישור מיקום (נשלח לספק; אין אישור קריאה)")
            except SmsError as e:
                self._x("UPDATE recipients SET sms_status='error' WHERE incident_id=? AND username=?",
                        (r["incident_id"], r["username"]))
                self._log_sms(r["username"], r["incident_id"], "fallback", "error", error=str(e))
                self._event(r["incident_id"], "sms_failed", f"שליחת SMS ל{self._display(r['username'])} נכשלה")
            self._notify(r["owner"], "incident_update", {"incident_id": r["incident_id"], "event": "sms"}, r["incident_id"])

    def location_link(self, token):
        """דף מיקום ציבורי (טוקן סודי, חד-מטרתי). פג כשהאירוע נסגר או אחרי 24 שעות."""
        row = self._q("SELECT * FROM location_links WHERE token_hash=?", (_h(token or ""),), one=True)
        if not row or row["expires_at"] < _now():
            return None
        inc = self._incident_row(row["incident_id"])
        # הקישור תקף רק כל עוד הנמען עדיין מורשה: איש קשר שנותק / שהגישה שלו בוטלה לא רואה כלום
        rec = self._q("SELECT state FROM recipients WHERE incident_id=? AND username=?",
                      (row["incident_id"], row["username"]), one=True)
        if not inc or not rec or rec["state"] in ("revoked", "expired", "declined", "reassigned"):
            return None
        if inc["status"] != "active":
            return {"closed": True, "owner_display": self._display(inc["owner"])}
        if not row["opened_at"]:
            self._x("UPDATE location_links SET opened_at=? WHERE token_hash=?", (_now(), row["token_hash"]))
            cur = self._x("""UPDATE recipients SET state='delivered', delivered_at=? WHERE incident_id=? AND username=?
                             AND state='notified'""", (_now(), row["incident_id"], row["username"]))
            self._event(row["incident_id"], "sms_opened", f"{self._display(row['username'])} פתח/ה את קישור ה-SMS")
            if cur.rowcount:
                self._notify(inc["owner"], "incident_update", {"incident_id": inc["id"], "event": "delivered"}, inc["id"])
        loc = self._latest_location(inc["id"])
        # הרשאת המיקום של איש הקשר חלה גם על קישור ה-SMS; כל פתיחה נרשמת ביומן הביקורת
        if self.location_mode_for(inc["owner"], row["username"]) == "off":
            loc = None
        elif loc:
            self._note_location_access(row["username"], inc["owner"], "sms_link", inc["id"],
                                       self.location_mode_for(inc["owner"], row["username"]) or "sms_fallback")
        return {"closed": False, "owner_display": self._display(inc["owner"]), "mode": inc["mode"],
                "emergency_type": inc["emergency_type"], "created_at": inc["created_at"], "location": loc,
                "medical": self._medical_for_incident(inc["owner"])}

    # ================================================================ 6. בדיקת מוכנות
    def readiness_check(self, user):
        contacts = [r["contact"] for r in self._q(
            "SELECT contact FROM relationships WHERE owner=? AND status='accepted'", (user,))]
        if not contacts:
            raise self._err(400, "no_contacts", "אין אנשי קשר מחוברים לבדוק")
        ids = {}
        for c in contacts:
            ids[c] = self._notify(c, "readiness_check", {"from": user, "from_display": self._display(user),
                                                         "text": f"🧪 בדיקת מוכנות מ{self._display(user)} - "
                                                                 "זו לא קריאת חירום. אין צורך לעשות דבר."})
        cur = self._x("INSERT INTO readiness_checks(username, created_at, notification_ids) VALUES (?,?,?)",
                      (user, _now(), json.dumps(ids)))
        return self.readiness_result(user, cur.lastrowid)

    def readiness_result(self, user, check_id=None):
        row = (self._q("SELECT * FROM readiness_checks WHERE id=? AND username=?", (check_id, user), one=True)
               if check_id else self._q("SELECT * FROM readiness_checks WHERE username=? ORDER BY id DESC LIMIT 1",
                                        (user,), one=True))
        if not row:
            return {"id": None, "created_at": None, "results": []}
        results = []
        for username, nid in json.loads(row["notification_ids"]).items():
            n = self._q("SELECT received_at FROM notifications WHERE id=?", (nid,), one=True)
            results.append({"username": username, "display_name": self._display(username),
                            "received_at": n["received_at"] if n else None})
        return {"id": row["id"], "created_at": row["created_at"], "results": results,
                "all_received": all(r["received_at"] for r in results)}


def _push_payload(ntype, p):
    """תוכן ההתראה שמוצג במסך הנעילה. מינימלי בכוונה - בלי מיקום ובלי מידע רפואי."""
    title, body, url = "LifeGuard", p.get("text") or "", "/app/"
    if ntype == "incident_new":
        if p.get("ring", True):
            title = f"🚨 {p.get('owner_display', '')} צריך/ה עזרה"
            body = "פתח/י כדי לקבל או לדחות" + (f" · {p['distance_km']:.1f} ק\"מ" if p.get("distance_km") else "")
        else:   # עדכון שקט: איש קשר אחר כבר מטפל
            title = f"ℹ️ {p.get('owner_display', '')} באירוע חירום"
            body = "מישהו מהמשפחה כבר מטפל. פתח/י לפרטים ולצ'אט"
        url = f"/app/#incident/{p.get('id')}"
    elif ntype == "relationship_invite":
        title, url = "📨 הזמנה למעגל הביטחון", "/app/#circle"
    elif ntype == "escort_started":
        title = f"🚶 {p.get('owner_display', '')} בדרך"
        body = (f"ליווי הביתה{(' ל' + p['destination']) if p.get('destination') else ''} - "
                "תקבל/י התראה אם לא יאשר/תאשר הגעה")
        url = f"/app/#escort/{p.get('id')}"
    elif ntype == "escort_overdue":
        title = "LifeGuard" if p.get("silent") else "⏰ הליווי הסתיים - הכל בסדר?"
        url = "/app/#home"
    elif ntype == "escort_alarm":
        title, url = "🚨 לא אושרה הגעה", f"/app/#incident/{p.get('incident_id')}"
    elif ntype == "escort_check":   # נסיעה בטוחה: "הכל בסדר?" - בנסיעה שקטה כותרת ניטרלית
        title = "LifeGuard" if p.get("silent") else "🚕 הכל בסדר בנסיעה?"
        url = "/app/#home"
    elif ntype == "incident_closed":
        title, body = "ℹ️ האירוע הסתיים", p.get("text") or "האירוע הסתיים"
        url = "/app/#home"
    elif ntype in ("test", "readiness_check"):
        title = "🧪 בדיקת LifeGuard"
    return {"type": ntype, "title": title[:80], "body": body[:200], "url": url,
            "tag": str(p.get("id") or p.get("incident_id") or p.get("escort_id") or ntype)[:64]}

# -*- coding: utf-8 -*-
"""
volunteer_identity.py - אימות זהות חובה לכל מתנדב/ת (גם מי שכבר רשום/ה): צילום תעודת זהות + סלפי + מספר ת"ז.

הזרימה:
1. המתנדב/ת מעלה צילום ת"ז, סלפי ומספר ת"ז, ומאשר/ת במפורש עיבוד לצורך אימות (consent).
2. השרת בודק ספרת ביקורת של ת"ז ישראלית, סוג הקובץ (JPEG/PNG לפי bytes, לא לפי סיומת) וגודל.
3. התמונות נשמרות **מוצפנות** (AES-GCM, crypto_box) עד להחלטה. מספר הת"ז לא נשמר - רק HMAC עם מפתח
   סודי של השרת (כדי לחסום אותה ת"ז בשני חשבונות) ו-4 ספרות אחרונות לתצוגה.
4. מנהל/ת מאמת/ת ידנית: משווה סלפי לתמונה בת"ז ואת השם. **אין כאן זיהוי פנים אוטומטי** ואין "אימות מול
   משרד הפנים" - לא קיים חיבור כזה, ולא מדמים אותו.
5. אחרי החלטה (אישור או דחייה) **התמונות נמחקות**. בקשה שלא טופלה תוך PENDING_TTL_DAYS - נמחקת אוטומטית.
6. עד שהזהות אומתה - המתנדב/ת לא מקבל/ת קריאות (הגעה למקום, תמיכה מרחוק, קשר לרשויות) ולא מוצג/ת כזמין/ה.

הצפייה בתמונות אפשרית רק בטוקן הניהול של השרת (לא בסיסמת דף הניהול), ונרשמת ביומן הביקורת.
"""
import base64
import hashlib
import hmac
import time

MIGRATION = """
CREATE TABLE volunteer_identity(
    username TEXT PRIMARY KEY REFERENCES users(username),
    status TEXT NOT NULL CHECK(status IN ('pending','verified','rejected')),
    id_hash TEXT, id_last4 TEXT, id_doc_enc TEXT, selfie_enc TEXT,
    consent_at REAL, submitted_at REAL, decided_at REAL, decided_by TEXT, note TEXT);
CREATE UNIQUE INDEX idx_vid_verified_hash ON volunteer_identity(id_hash) WHERE status='verified';
"""

ID_REQUIRED = True                     # כל מתנדב/ת חייב/ת אימות זהות כדי לקבל קריאות
PENDING_TTL_DAYS = 30
MAX_IMAGE_BYTES = 3_000_000
MIN_IMAGE_BYTES = 10_000               # צילום ת"ז קריא לא יכול להיות זעיר
UPLOAD_BODY_LIMIT = 9 * 1024 * 1024    # 2 תמונות ב-base64 + שדות
STATUS_HE = {"none": "לא הועלה", "pending": "ממתין לבדיקה", "verified": "מאומת/ת", "rejected": "נדחה"}


def valid_israeli_id(value):
    """ספרת ביקורת של ת"ז ישראלית (9 ספרות, משלימים אפסים משמאל)."""
    s = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not s or len(s) > 9 or len(str(value).strip()) != len(s):
        return None
    s = s.zfill(9)
    if s == "000000000":
        return None
    total = 0
    for i, ch in enumerate(s):
        d = int(ch) * (1 if i % 2 == 0 else 2)
        total += d - 9 if d > 9 else d
    return s if total % 10 == 0 else None


def _image(b64, label, err):
    if not isinstance(b64, str) or not b64:
        raise err(400, "missing_image", f"חסר {label}")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, TypeError):
        raise err(400, "bad_image", f"{label}: קובץ לא תקין")
    if len(raw) > MAX_IMAGE_BYTES:
        raise err(413, "image_too_large", f"{label}: הקובץ גדול מדי (עד 3MB)")
    if len(raw) < MIN_IMAGE_BYTES:
        raise err(400, "image_too_small", f"{label}: התמונה קטנה מדי - צריך צילום ברור")
    if not (raw[:3] == b"\xff\xd8\xff" or raw[:8] == b"\x89PNG\r\n\x1a\n"):
        raise err(400, "bad_image_type", f"{label}: רק JPEG או PNG")
    return base64.b64encode(raw).decode("ascii")


class VolunteerIdentityMixin:
    def _id_hash(self, id9):
        return hmac.new(self._pepper(), ("teudat_zehut:" + id9).encode("utf-8"), hashlib.sha256).hexdigest()

    def _identity_row(self, user):
        return self._q("SELECT * FROM volunteer_identity WHERE username=?", (user,), one=True)

    def identity_status(self, user):
        self._purge_stale_identity()
        r = self._identity_row(user)
        status = r["status"] if r else "none"
        return {"status": status, "status_he": STATUS_HE[status], "required": ID_REQUIRED,
                "can_receive_calls": (not ID_REQUIRED) or status == "verified",
                "id_last4": r["id_last4"] if r else None, "submitted_at": r["submitted_at"] if r else None,
                "decided_at": r["decided_at"] if r else None,
                "note": r["note"] if r and status == "rejected" else None}

    def identity_verified_set(self):
        return {r["username"] for r in self._q("SELECT username FROM volunteer_identity WHERE status='verified'")}

    def identity_ok(self, user):
        if not ID_REQUIRED:
            return True
        r = self._q("SELECT status FROM volunteer_identity WHERE username=?", (user,), one=True)
        return bool(r and r["status"] == "verified")

    def submit_identity(self, user, id_number, id_image, selfie, consent):
        if consent is not True:
            raise self._err(400, "consent_required",
                            "צריך לאשר עיבוד של צילום הת\"ז והסלפי לצורך אימות זהות בלבד")
        id9 = valid_israeli_id(id_number)
        if not id9:
            raise self._err(400, "invalid_id_number", "מספר תעודת הזהות לא תקין (ספרת ביקורת)")
        doc = _image(id_image, "צילום תעודת הזהות", self._err)
        face = _image(selfie, "הסלפי", self._err)
        h = self._id_hash(id9)
        with self._lock:
            cur = self._identity_row(user)
            if cur and cur["status"] == "verified":
                raise self._err(409, "already_verified", "הזהות כבר אומתה")
            other = self._q("SELECT username FROM volunteer_identity WHERE id_hash=? AND status='verified' "
                            "AND username!=?", (h, user), one=True)
            if other:
                raise self._err(409, "id_in_use", "תעודת הזהות הזו כבר משויכת לחשבון מתנדב/ת אחר")
            if not self._q("SELECT 1 FROM volunteers WHERE username=?", (user,), one=True):
                self.set_volunteer(user, False)
            now = time.time()
            enc = lambda b64: self.crypto.encrypt(b64, aad=f"vid:{user}".encode())  # noqa: E731
            self._x("""INSERT INTO volunteer_identity(username, status, id_hash, id_last4, id_doc_enc, selfie_enc,
                           consent_at, submitted_at, decided_at, decided_by, note)
                       VALUES (?, 'pending', ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                       ON CONFLICT(username) DO UPDATE SET status='pending', id_hash=excluded.id_hash,
                           id_last4=excluded.id_last4, id_doc_enc=excluded.id_doc_enc, selfie_enc=excluded.selfie_enc,
                           consent_at=excluded.consent_at, submitted_at=excluded.submitted_at,
                           decided_at=NULL, decided_by=NULL, note=NULL""",
                    (user, h, id9[-4:], enc(doc), enc(face), now, now))
        self.audit("volunteer_identity_submitted", actor=user, subject=user)
        return self.identity_status(user)

    def withdraw_identity(self, user):
        """המתנדב/ת מבטל/ת בקשה ממתינה - התמונות נמחקות מיד."""
        cur = self._identity_row(user)
        if not cur or cur["status"] != "pending":
            raise self._err(409, "nothing_pending", "אין בקשת אימות ממתינה")
        self._x("DELETE FROM volunteer_identity WHERE username=?", (user,))
        self.audit("volunteer_identity_withdrawn", actor=user, subject=user)
        return self.identity_status(user)

    # ---------------- ניהול ----------------
    def admin_identity_queue(self):
        self._purge_stale_identity()
        rows = self._q("SELECT username, id_last4, submitted_at FROM volunteer_identity WHERE status='pending' "
                       "ORDER BY submitted_at")
        for r in rows:
            r["display_name"] = self._display(r["username"])
        return rows

    def admin_identity_view(self, username, actor):
        r = self._identity_row(username)
        if not r or r["status"] != "pending" or not r["id_doc_enc"]:
            raise self._err(404, "not_found", "אין בקשת אימות ממתינה עם תמונות")
        aad = f"vid:{username}".encode()
        self.audit("volunteer_identity_viewed", actor=actor, subject=username)
        return {"username": username, "display_name": self._display(username), "id_last4": r["id_last4"],
                "submitted_at": r["submitted_at"], "id_image": self.crypto.decrypt(r["id_doc_enc"], aad=aad),
                "selfie": self.crypto.decrypt(r["selfie_enc"], aad=aad)}

    def admin_identity_decide(self, username, approve, note="", actor="admin"):
        with self._lock:
            r = self._identity_row(username)
            if not r or r["status"] != "pending":
                raise self._err(404, "not_found", "אין בקשת אימות ממתינה")
            if approve and self._q("SELECT 1 FROM volunteer_identity WHERE id_hash=? AND status='verified' "
                                   "AND username!=?", (r["id_hash"], username), one=True):
                raise self._err(409, "id_in_use", "תעודת הזהות הזו כבר מאומתת בחשבון אחר")
            status = "verified" if approve else "rejected"
            # התמונות נמחקות בכל החלטה - נשארים רק סטטוס, 4 ספרות ו-HMAC
            self._x("""UPDATE volunteer_identity SET status=?, id_doc_enc=NULL, selfie_enc=NULL, decided_at=?,
                       decided_by=?, note=? WHERE username=?""",
                    (status, time.time(), str(actor)[:60], str(note or "")[:200] or None, username))
        self.audit("volunteer_identity_" + status, actor=actor, subject=username, details={"note": note or ""})
        self._notify(username, "info", {"from": "LifeGuard", "from_display": "LifeGuard",
                                        "text": "✅ הזהות שלך אומתה - אפשר לקבל קריאות כמתנדב/ת" if approve else
                                        "אימות הזהות נדחה" + (f": {note}" if note else "") +
                                        ". אפשר להעלות שוב צילום ברור של הת\"ז וסלפי"})
        self._wake_dispatch()
        return {"username": username, "status": status}

    def _purge_stale_identity(self, now=None):
        cutoff = (now or time.time()) - PENDING_TTL_DAYS * 86400
        stale = self._q("SELECT username FROM volunteer_identity WHERE status='pending' AND submitted_at<?", (cutoff,))
        for r in stale:
            self._x("""UPDATE volunteer_identity SET status='rejected', id_doc_enc=NULL, selfie_enc=NULL, decided_at=?,
                       decided_by='system', note=? WHERE username=? AND status='pending'""",
                    (time.time(), f"הבקשה לא נבדקה תוך {PENDING_TTL_DAYS} ימים והתמונות נמחקו - יש להעלות שוב",
                     r["username"]))
        return len(stale)

    def notify_identity_required_once(self):
        """פעם אחת אחרי העדכון: כל מתנדב/ת קיים/ת בלי זהות מאומתת מקבל/ת הודעה שנדרש אימות."""
        if not ID_REQUIRED:
            return 0
        with self._lock:
            if self._q("SELECT 1 FROM server_meta WHERE key='identity_notice_sent'", one=True):
                return 0
            self._x("INSERT INTO server_meta(key, value) VALUES ('identity_notice_sent', ?)", (str(time.time()),))
        n = 0
        for v in self._q("SELECT username FROM volunteers"):
            if not self.identity_ok(v["username"]):
                self._notify(v["username"], "info", {
                    "from": "LifeGuard", "from_display": "LifeGuard",
                    "text": "🪪 כדי להמשיך לקבל קריאות כמתנדב/ת צריך לאמת זהות: צילום ת\"ז וסלפי "
                            "(במסך המתנדבים). עד האימות לא יישלחו אליך קריאות."})
                n += 1
        return n

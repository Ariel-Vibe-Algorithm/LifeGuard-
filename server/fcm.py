# -*- coding: utf-8 -*-
"""
fcm.py - השכמת אפליקציית האנדרואיד דרך Firebase Cloud Messaging (FCM HTTP v1).

למה: האפליקציה מחזיקה חיבור פתוח לשרת (long-poll). יצרני טלפונים (Xiaomi/Samsung/Huawei) ו-Doze הורגים או
מקפיאים אותו - וקריאת חירום מאיש קשר לא מגיעה עד שהטלפון "מתעורר". הודעת FCM בעדיפות גבוהה מעירה את האפליקציה.

פרטיות: ההודעה היא **השכמה בלבד** - בלי שם, בלי מיקום, בלי תוכן. רק {"t": "wake", "k": סוג, "u": דחוף?}.
האפליקציה מתעוררת ושולפת את הפרטים מהשרת שלנו בחיבור המוצפן. Google רואה רק "יש משהו".

הגדרה (שרת): LIFEGUARD_FCM_SERVICE_ACCOUNT = נתיב לקובץ ה-JSON של חשבון שירות מפרויקט Firebase
(Project settings -> Service accounts -> Generate new private key). בלי זה: "not_configured" - ולא מדמים כלום.
"""

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
URGENT_TYPES = ("incident_new", "escort_alarm", "escort_check", "incident_closed")
TIMEOUT_S = 8


def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def make_jwt(sa, now=None):
    """JWT בחתימת RS256 של חשבון השירות (לבקשת access token מ-Google)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(now or time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": sa.get("private_key_id", "")}
    claims = {"iss": sa["client_email"], "scope": SCOPE, "aud": sa.get("token_uri") or TOKEN_URL,
              "iat": now, "exp": now + 3600}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}".encode()
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode() + "." + _b64url(sig)


def wake_message(token, ntype, urgent):
    """הודעת data בלבד - בלי notification (שהמערכת הייתה מציגה בעצמה) ובלי שום מידע אישי."""
    return {"message": {"token": token,
                        "data": {"t": "wake", "k": str(ntype)[:32], "u": "1" if urgent else "0"},
                        "android": {"priority": "high" if urgent else "normal", "ttl": "300s" if urgent else "3600s",
                                    "collapse_key": "lifeguard_wake"}}}


class FcmSender:
    def __init__(self, sa_path=None, opener=None):
        self.sa_path = sa_path if sa_path is not None else os.environ.get("LIFEGUARD_FCM_SERVICE_ACCOUNT", "")
        self.sa = None
        self.error = None
        self._token = (None, 0.0)
        self._lock = threading.Lock()
        self._open = opener or urllib.request.urlopen
        if self.sa_path:
            try:
                with open(self.sa_path, encoding="utf-8") as fh:
                    sa = json.load(fh)
                for k in ("client_email", "private_key", "project_id"):
                    if not sa.get(k):
                        raise ValueError(f"חסר {k} בקובץ חשבון השירות")
                self.sa = sa
            except (OSError, ValueError) as exc:
                self.error = str(exc)[:200]

    @property
    def configured(self):
        return self.sa is not None

    @property
    def status(self):
        if self.configured:
            return "active"
        return "misconfigured" if self.error else "not_configured"

    def _access_token(self):
        with self._lock:
            tok, exp = self._token
            if tok and time.time() < exp - 120:
                return tok
            body = ("grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion="
                    + make_jwt(self.sa)).encode()
            req = urllib.request.Request(self.sa.get("token_uri") or TOKEN_URL, data=body, method="POST",
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with self._open(req, timeout=TIMEOUT_S) as r:
                d = json.loads(r.read().decode("utf-8"))
            self._token = (d["access_token"], time.time() + float(d.get("expires_in", 3600)))
            return self._token[0]

    def send(self, token, ntype, urgent):
        """-> (code, error). 404/UNREGISTERED = הטוקן לא בתוקף (הקורא מוחק אותו)."""
        if not self.configured:
            return 0, "not_configured"
        try:
            at = self._access_token()
            req = urllib.request.Request(SEND_URL.format(project=self.sa["project_id"]),
                                         data=json.dumps(wake_message(token, ntype, urgent)).encode("utf-8"),
                                         method="POST", headers={"Authorization": f"Bearer {at}",
                                                                 "Content-Type": "application/json"})
            with self._open(req, timeout=TIMEOUT_S) as r:
                return r.status if hasattr(r, "status") else 200, None
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:  # noqa: BLE001
                pass
            if e.code == 401:
                self._token = (None, 0.0)          # access token פג/נדחה - יתחדש בניסיון הבא
            return e.code, detail
        except Exception as exc:  # noqa: BLE001 - רשת/DNS: לא מפיל את ה-worker
            return -1, str(exc)[:200]


MIGRATION = """
CREATE TABLE fcm_tokens(token TEXT PRIMARY KEY, username TEXT NOT NULL, device TEXT, created_at REAL NOT NULL,
    last_ok REAL, last_error TEXT);
CREATE INDEX IF NOT EXISTS idx_fcm_user ON fcm_tokens(username);
"""
MAX_TOKENS_PER_USER = 5


class FcmMixin:
    def _fcm(self):
        s = getattr(self, "_fcm_sender", None)
        if s is None:
            s = self._fcm_sender = FcmSender()
        return s

    def register_fcm(self, user, token, device=""):
        token = str(token or "").strip()
        if not (20 <= len(token) <= 4096) or any(c.isspace() for c in token):
            raise self._err(400, "invalid_token", "טוקן FCM לא תקין")
        self._x("INSERT INTO fcm_tokens(token, username, device, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(token) DO UPDATE SET username=excluded.username, device=excluded.device",
                (token, user, str(device or "")[:80], time.time()))
        extra = self._q("SELECT token FROM fcm_tokens WHERE username=? ORDER BY created_at DESC LIMIT -1 OFFSET ?",
                        (user, MAX_TOKENS_PER_USER))
        for r in extra:
            self._x("DELETE FROM fcm_tokens WHERE token=?", (r["token"],))
        return {"ok": True, "fcm": self._fcm().status}

    def unregister_fcm(self, user, token):
        self._x("DELETE FROM fcm_tokens WHERE token=? AND username=?", (str(token or ""), user))
        return {"ok": True}

    def _fcm_wake(self, username, ntype, payload):
        """נקרא מתור ה-push ברקע (לא מעכב את הבקשה)."""
        sender = self._fcm()
        if not sender.configured:
            return
        urgent = ntype in URGENT_TYPES and (payload or {}).get("ring", True) is not False
        for row in self._q("SELECT token FROM fcm_tokens WHERE username=?", (username,)):
            code, err = sender.send(row["token"], ntype, urgent)
            if code == 200:
                self._x("UPDATE fcm_tokens SET last_ok=?, last_error=NULL WHERE token=?", (time.time(), row["token"]))
            elif code == 404 or (err and "UNREGISTERED" in err):
                self._x("DELETE FROM fcm_tokens WHERE token=?", (row["token"],))
            else:
                self._x("UPDATE fcm_tokens SET last_error=? WHERE token=?", (f"{code} {err or ''}"[:200], row["token"]))

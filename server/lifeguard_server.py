# -*- coding: utf-8 -*-
"""
LifeGuard Backend - שרת התראות חירום, מיקום חי, הסלמה ומרכז קהילה.

ספרייה סטנדרטית בלבד (http.server + sqlite3) - אין תלויות חדשות.

עקרונות:
- כל התראה עוברת דרך השרת. אפליקציית הנמען מקבלת אותה בזרם בזמן אמת (SSE) או ב-polling,
  ו*מאשרת קבלה לשרת* (POST /received). רק אז מצב הנמען הופך ל-"delivered". השולח לעולם לא
  רואה "נמסר" בלי אישור כזה.
- אנשי קשר מהימנים (משפחה/חברים) מקבלים התראה רק אחרי שאישרו את הקשר (הזמנה + הסכמה).
- מתנדבים: נבחר המתנדב הזמין והמתאים הקרוב ביותר. אם לא אישר תוך ACK_TIMEOUT - מסומן
  "expired" ומוזעק הבא בתור (מדיניות ההסלמה הקיימת: ESCALATION_ACK_TIMEOUT_MINUTES).
- פרטיות: מתנדב רואה מרחק ואזור משוער עד שהוא מקבל את האירוע; מיקום מדויק ומידע אישי רק
  אחרי קבלה. עם סיום האירוע הגישה של מתנדבים מבוטלת. מצוקה שקטה -> רק אנשי קשר מהימנים.
- הרשאות: כל בקשה מאומתת ב-token (נשמר בשרת כ-hash בלבד), וכל אירוע נגיש רק לבעליו ולנמעניו.
"""

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage  # noqa: E402
from server import sms as sms_mod  # noqa: E402
from server.features import MIGRATION_4, FeaturesMixin  # noqa: E402
from server.signal_watch import MIGRATION as MIGRATION_8, SignalWatchMixin  # noqa: E402
from server.evidence import MIGRATION as MIGRATION_10, EvidenceMixin  # noqa: E402
from server.rides import MIGRATION as MIGRATION_11, RideMixin  # noqa: E402
from server import volunteer_match  # noqa: E402
from server.volunteer_match import VolunteerMatchMixin  # noqa: E402
from server import national, safety_intel  # noqa: E402
from server import privacy  # noqa: E402
from server import fcm  # noqa: E402
from server import helpers  # noqa: E402
from server import heatmap  # noqa: E402
from server import admin_dashboard  # noqa: E402
from server import volunteer_identity  # noqa: E402
from server import abuse, dispatch, ring  # noqa: E402
from server import routing as routing_mod  # noqa: E402
from server.routing import Router  # noqa: E402

API_VERSION = "v1"
TOKEN_TTL_SECONDS = 30 * 86400
MAX_LOGIN_FAILURES = 5
LOGIN_LOCK_SECONDS = 60
VOLUNTEER_LOCATION_MAX_AGE_SECONDS = 12 * 3600
ETA_BAND_SECONDS = 600               # רצועת זמן הליכה שבתוכה מתנדב מאומת עדיף
DISPATCH_RETRY_SECONDS = 5           # אירוע בלי מועמד נבדק מחדש כל 5 שנ' (או מיד כשמשהו משתנה)
DEFAULT_VOLUNTEER_RADIUS_KM = 5.0
MAX_BODY_BYTES = 64 * 1024
DRAIN_MAX_BYTES = 2 * 1024 * 1024     # גוף שלא נקרא (שגיאה לפני הקריאה) - עד כאן קוראים ומשליכים
USERNAME_RE = re.compile(r"^[\w֐-׿.\-]{3,32}$", re.UNICODE)

EMERGENCY_TYPES = {"unknown", "medical", "fire", "earthquake", "security", "physical_danger",
                   "road_accident", "lost", "missing_person", "flood_weather", "power_outage"}
SEVERITIES = {"unknown", "low", "medium", "high", "critical"}
RESPONSES = {"accept", "decline", "on_my_way", "arrived", "handled"}

# מעגל הביטחון
DEFAULT_INVITE_TTL_SECONDS = 7 * 86400
MAX_PENDING_INVITES = 20             # למשתמש - מונע הצפה
MAX_RESENDS = 5
RESEND_COOLDOWN_SECONDS = 30
DECLINE_COOLDOWN_SECONDS = 24 * 3600  # אחרי סירוב - אין הזמנה חוזרת לאותו אדם במשך יממה (מניעת הטרדה)
REDEEM_MAX_FAILURES = 10             # ניסיונות קוד שגויים בחלון זמן -> חסימה זמנית
REDEEM_WINDOW_SECONDS = 600
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # בלי 0/O/1/I/L - קל להקליד מהודעה
CODE_LENGTH = 12                     # ~59 ביט אקראיות
DEFAULT_COUNTRY_CODE = os.environ.get("LIFEGUARD_DEFAULT_COUNTRY_CODE", "972")

# ---------------------------------------------------------------- schema
_MIGRATIONS = [
    """
    CREATE TABLE users(
        username TEXT PRIMARY KEY, display_name TEXT NOT NULL,
        pw_salt TEXT NOT NULL, pw_hash TEXT NOT NULL,
        failed_logins INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL);
    CREATE TABLE sessions(
        token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES users(username),
        device TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, expires_at REAL NOT NULL);
    CREATE TABLE volunteers(
        username TEXT PRIMARY KEY REFERENCES users(username),
        available INTEGER NOT NULL DEFAULT 0, radius_km REAL NOT NULL DEFAULT 5,
        skills TEXT NOT NULL DEFAULT '[]', lat REAL, lon REAL, accuracy_m REAL,
        loc_source TEXT, is_precise INTEGER NOT NULL DEFAULT 0, loc_updated_at REAL,
        updated_at REAL NOT NULL);
    CREATE TABLE presence(
        username TEXT PRIMARY KEY REFERENCES users(username),
        lat REAL, lon REAL, is_precise INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
    CREATE TABLE relationships(
        owner TEXT NOT NULL REFERENCES users(username),
        contact TEXT NOT NULL REFERENCES users(username),
        status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined')),
        created_at REAL NOT NULL, responded_at REAL,
        PRIMARY KEY(owner, contact));
    CREATE TABLE incidents(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(username),
        client_id TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('loud','silent')),
        emergency_type TEXT NOT NULL DEFAULT 'unknown', severity TEXT NOT NULL DEFAULT 'unknown',
        status TEXT NOT NULL CHECK(status IN ('active','resolved','cancelled')),
        profile TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL, updated_at REAL NOT NULL, ended_at REAL,
        UNIQUE(owner, client_id));
    CREATE TABLE incident_locations(
        id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL REFERENCES incidents(id),
        lat REAL NOT NULL, lon REAL NOT NULL, accuracy_m REAL, source TEXT,
        is_precise INTEGER NOT NULL DEFAULT 0, captured_at REAL, received_at REAL NOT NULL);
    CREATE TABLE recipients(
        incident_id TEXT NOT NULL REFERENCES incidents(id), username TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('trusted','volunteer')),
        state TEXT NOT NULL, distance_km REAL, escalation_level INTEGER NOT NULL DEFAULT 0,
        notified_at REAL NOT NULL, delivered_at REAL, responded_at REAL, response TEXT,
        PRIMARY KEY(incident_id, username));
    CREATE TABLE notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, incident_id TEXT,
        type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL, received_at REAL);
    CREATE INDEX idx_notif_user ON notifications(username, id);
    CREATE TABLE incident_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, ts REAL NOT NULL,
        actor TEXT, kind TEXT NOT NULL, text TEXT NOT NULL);
    CREATE TABLE messages(
        id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, sender TEXT NOT NULL,
        text TEXT NOT NULL, created_at REAL NOT NULL);
    """,
    # 2 - תשתית Global Street Intelligence (נתונים בלבד; אין מנוע ניקוד ואין ציונים מומצאים)
    """
    CREATE TABLE street_segments(
        id TEXT PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT, name TEXT,
        geometry TEXT NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE street_observations(
        id TEXT PRIMARY KEY, segment_id TEXT REFERENCES street_segments(id),
        kind TEXT NOT NULL CHECK(kind IN ('user_video','user_image','satellite','aerial',
                                          'municipal','user_report')),
        submitted_by TEXT, lat REAL, lon REAL, observed_at REAL, received_at REAL NOT NULL,
        media_ref TEXT, attributes TEXT NOT NULL DEFAULT '{}',
        verification TEXT NOT NULL DEFAULT 'unverified'
            CHECK(verification IN ('unverified','verified','rejected')),
        consent TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE street_scores(
        segment_id TEXT PRIMARY KEY REFERENCES street_segments(id),
        score REAL, confidence REAL, risk_factors TEXT NOT NULL DEFAULT '[]',
        model_version TEXT NOT NULL, observation_count INTEGER NOT NULL,
        computed_at REAL NOT NULL);
    """,
    # 3 - מעגל הביטחון: הזמנות עם מחזור חיים, מזהי משתמש ייחודיים, טלפון (hash בלבד)
    lambda con: _migration_3_safety_circle(con),
    # 4 - ליווי הביתה, אמון במתנדבים, מידע רפואי מוצפן, גיבוי SMS, Web Push, בדיקת מוכנות
    MIGRATION_4,
    # 5 - רמת סכנה לאירוע (מוצגת רק למגיבים) + תור עדיפויות למתנדבים
    """
    ALTER TABLE incidents ADD COLUMN risk_level TEXT;
    ALTER TABLE incidents ADD COLUMN risk_score REAL;
    ALTER TABLE incidents ADD COLUMN risk_reasons TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE incidents ADD COLUMN dispatch_waiting INTEGER NOT NULL DEFAULT 0;
    """,
    # 6 - דיווחי מתנדבים על התראות שווא/ניצול + זמן הגעה בהליכה לכל מתנדב
    """
    CREATE TABLE incident_reports(
        incident_id TEXT NOT NULL REFERENCES incidents(id), reporter TEXT NOT NULL, owner TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('false_alarm','abusive')), note TEXT, created_at REAL NOT NULL,
        PRIMARY KEY(incident_id, reporter));
    CREATE INDEX idx_reports_owner ON incident_reports(owner, created_at);
    ALTER TABLE recipients ADD COLUMN eta_seconds REAL;
    ALTER TABLE recipients ADD COLUMN eta_source TEXT;
    ALTER TABLE incidents ADD COLUMN abuse_noted INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE volunteers ADD COLUMN review_flag INTEGER NOT NULL DEFAULT 0;
    """,
    # 7 - שרשרת צלצול לאנשי קשר (הקרוב ראשון) + צ'אט: הודעות מהירות ואישורי קריאה
    """
    ALTER TABLE recipients ADD COLUMN ring_status TEXT;
    ALTER TABLE recipients ADD COLUMN ring_order INTEGER;
    ALTER TABLE messages ADD COLUMN quick TEXT;
    CREATE TABLE message_reads(
        incident_id TEXT NOT NULL, username TEXT NOT NULL, up_to_id INTEGER NOT NULL, read_at REAL NOT NULL,
        PRIMARY KEY(incident_id, username));
    """,
    # 8 - "אין קליטה": זיהוי שתיקה של הטלפון בליווי/באירוע (server/signal_watch.py)
    MIGRATION_8,
    # 9 - אינדקסים (בדיקת עומס: tick ב-400 אלף שורות היסטוריה 1,577ms -> 25ms; get_incident 39ms -> 0.1ms)
    """
    CREATE INDEX IF NOT EXISTS idx_incloc_inc ON incident_locations(incident_id, id);
    CREATE INDEX IF NOT EXISTS idx_incevt_inc ON incident_events(incident_id, id);
    CREATE INDEX IF NOT EXISTS idx_msg_inc ON messages(incident_id, sender, created_at);
    CREATE INDEX IF NOT EXISTS idx_rcpt_user ON recipients(username, role, state);
    CREATE INDEX IF NOT EXISTS idx_rcpt_role_state ON recipients(role, state, notified_at);
    CREATE INDEX IF NOT EXISTS idx_inc_status ON incidents(status, created_at);
    CREATE INDEX IF NOT EXISTS idx_inc_owner ON incidents(owner, created_at);
    CREATE INDEX IF NOT EXISTS idx_inv_status ON invitations(status, expires_at);
    CREATE INDEX IF NOT EXISTS idx_inv_inviter ON invitations(inviter, status);
    CREATE INDEX IF NOT EXISTS idx_obs_user ON street_observations(submitted_by, kind);
    CREATE INDEX IF NOT EXISTS idx_escort_status ON escorts(status, deadline);
    CREATE INDEX IF NOT EXISTS idx_notif_recv ON notifications(username, received_at);
    CREATE INDEX IF NOT EXISTS idx_vol_avail ON volunteers(available, loc_updated_at);
    """,
    # 10 - רשומת ראיות חתומה + יומן גישה בשרשרת hash; ביטחון בזיהוי נפרד מחומרה (server/evidence.py)
    MIGRATION_10,
    # 11 - נסיעה בטוחה: יעד, סוג נסיעה, רכב, מסלול צפוי ובפועל, חריגות ובדיקות (server/rides.py)
    MIGRATION_11,
    # 12 - הכשרות מתנדבים מאומתות (server/volunteer_match.py)
    volunteer_match.MIGRATION,
    # 13 - דיווחי רחוב (מודיעין בטיחות רחובות, server/safety_intel.py)
    safety_intel.MIGRATION,
    # 14 - אזורי התרעה של המשתמש (מצב חירום לאומי, server/national.py)
    national.MIGRATION,
    # 15 - הרשאות מיקום לכל איש קשר, יומן ביקורת append-only, הקלטות, הורה-ילד, הפעלה לפי ביטחון
    privacy.MIGRATION,
    # 16 - השכמת אנדרואיד דרך FCM (server/fcm.py) - טוקנים של מכשירים
    fcm.MIGRATION,
    # 17 - מתנדבים בלי חובת מד"א: שיחת תמיכה מרחוק והזעקת רשויות (server/helpers.py)
    helpers.MIGRATION,
    # 18 - חשבונות ניהול (סיסמה מגובבת בלבד), כניסות ניהול ונעילה (server/admin_dashboard.py)
    admin_dashboard.MIGRATION,
    # 19 - אימות זהות חובה למתנדבים: צילום ת"ז + סלפי, מוצפנים עד להחלטה (server/volunteer_identity.py)
    volunteer_identity.MIGRATION,
]


def _migration_3_safety_circle(con):
    con.executescript("""
    ALTER TABLE users ADD COLUMN user_id TEXT;
    ALTER TABLE users ADD COLUMN phone_hash TEXT;
    ALTER TABLE users ADD COLUMN phone_last4 TEXT;
    CREATE INDEX idx_users_phone ON users(phone_hash);
    CREATE TABLE server_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE invitations(
        id TEXT PRIMARY KEY,
        inviter TEXT NOT NULL REFERENCES users(username), inviter_id TEXT NOT NULL,
        channel TEXT NOT NULL CHECK(channel IN ('username','phone','code')),
        target_username TEXT REFERENCES users(username), target_id TEXT,
        target_phone_hash TEXT, target_phone_last4 TEXT, target_label TEXT,
        token_hash TEXT UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('pending','accepted','declined','expired','cancelled')),
        accepted_by TEXT, accepted_by_id TEXT, awaiting_confirmation INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, last_sent_at REAL NOT NULL, expires_at REAL NOT NULL,
        responded_at REAL, resend_count INTEGER NOT NULL DEFAULT 0);
    CREATE UNIQUE INDEX uq_inv_pending_user ON invitations(inviter, target_username)
        WHERE status='pending' AND channel='username';
    CREATE UNIQUE INDEX uq_inv_pending_phone ON invitations(inviter, target_phone_hash)
        WHERE status='pending' AND channel='phone';
    CREATE INDEX idx_inv_target ON invitations(target_username, status);
    CREATE TABLE redeem_failures(username TEXT NOT NULL, ts REAL NOT NULL);
    ALTER TABLE relationships ADD COLUMN invitation_id TEXT;
    """)
    for row in con.execute("SELECT username FROM users").fetchall():
        con.execute("UPDATE users SET user_id=? WHERE username=?", ("usr_" + uuid.uuid4().hex, row[0]))
    con.execute("CREATE UNIQUE INDEX uq_users_user_id ON users(user_id)")
    # קשרים ישנים: 'pending'/'declined' עוברים לטבלת ההזמנות; ב-relationships נשארים רק קשרים מאושרים
    now = _now()
    for owner, contact, status, created in con.execute(
            "SELECT owner, contact, status, created_at FROM relationships WHERE status!='accepted'").fetchall():
        ids = dict(con.execute("SELECT username, user_id FROM users WHERE username IN (?,?)", (owner, contact)).fetchall())
        con.execute("""INSERT INTO invitations(id, inviter, inviter_id, channel, target_username, target_id, status,
                       created_at, last_sent_at, expires_at, responded_at) VALUES (?,?,?,'username',?,?,?,?,?,?,?)""",
                    ("inv_" + uuid.uuid4().hex, owner, ids.get(owner), contact, ids.get(contact), status, created,
                     created, now + DEFAULT_INVITE_TTL_SECONDS, now if status == "declined" else None))
    con.execute("DELETE FROM relationships WHERE status!='accepted'")


def _now():
    return time.time()


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _hash_pw(password, salt=None):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), dk.hex()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_phone(raw, country_code=DEFAULT_COUNTRY_CODE):
    """מספר טלפון -> E.164 ('+9725...'). None אם לא תקין. 05X... מקומי מקבל קידומת ברירת מחדל."""
    s = re.sub(r"[\s\-().]", "", str(raw or ""))
    if s.startswith("00"):
        s = "+" + s[2:]
    if s.startswith("+"):
        digits = s[1:]
    elif s.startswith("0"):
        digits = country_code + s[1:]
    else:
        digits = s
    if not digits.isdigit() or not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def new_invite_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def _code_hash(code):
    norm = re.sub(r"[^A-Z0-9]", "", str(code or "").upper())
    return hashlib.sha256(("lg-invite:" + norm).encode("utf-8")).hexdigest() if norm else None


def _approx(value, step=0.005):
    """עיגול קואורדינטה (~500 מ') - מה שמתנדב רואה לפני שקיבל את האירוע."""
    return None if value is None else round(round(value / step) * step, 4)


class ApiError(Exception):
    def __init__(self, status, code, message, extra=None):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra or {}


class _DispatchCtx:
    """מטמון לסבב שיבוץ אחד: כל נתון נשלף/מחושב פעם אחת (במקום לכל מועמד ולכל אירוע)."""

    def __init__(self, svc, now):
        self.svc, self.now = svc, now
        self._ab, self._prio = {}, {}
        self._busy = self._vols = self._accepts = self._id_ok = None
        self._quals = self._rel = None

    def quals(self, username):
        if self._quals is None:
            self._quals = self.svc._verified_quals_all()
        return self._quals.get(username, set())

    def reliability(self, username):
        """אמינות מהיסטוריית 180 הימים האחרונים (שאילתה אחת לסבב)."""
        if self._rel is None:
            stats = self.svc._offer_stats_all(self.now - 180 * 86400)
            self._rel = {u: volunteer_match.reliability(s)[0] for u, s in stats.items()}
        return self._rel.get(username, volunteer_match.UNKNOWN)

    def ab(self, owner):
        if owner not in self._ab:
            self._ab[owner] = self.svc.owner_abuse(owner, self.now)
        return self._ab[owner]

    def priority(self, inc):
        if inc["id"] not in self._prio:
            self._prio[inc["id"]] = self.svc._incident_priority(inc, self.now, self.ab(inc["owner"]))
        return self._prio[inc["id"]]

    def priority_of(self, iid):
        if iid not in self._prio:
            self.priority(self.svc._incident_row(iid))
        return self._prio[iid]

    def busy(self):
        """מתנדב -> (אירוע, מצב) בכל האירועים הפעילים (קיבל, או שממתינים לתשובתו)."""
        if self._busy is None:
            self._busy = {}
            for r in self.svc._q("""SELECT r.username, r.incident_id, r.state FROM recipients r
                                    JOIN incidents i ON i.id=r.incident_id WHERE i.status='active'
                                    AND r.role='volunteer' AND r.state IN ('notified','delivered','accepted')"""):
                if r["username"] not in self._busy or r["state"] == "accepted":
                    self._busy[r["username"]] = (r["incident_id"], r["state"])
        return self._busy

    def assigned(self, username, iid):
        self.busy()[username] = (iid, "notified")

    def volunteers(self):
        if self._vols is None:
            fresh = self.now - VOLUNTEER_LOCATION_MAX_AGE_SECONDS
            self._vols = self.svc._q("""SELECT username, radius_km, skills, lat, lon, verification, suspended
                                        FROM volunteers WHERE available=1 AND lat IS NOT NULL AND loc_updated_at>?
                                        AND roles LIKE '%"on_scene"%'""",   # רק מי שבחר/ה הגעה למקום (helpers.py)
                                     (fresh,))
        return self._vols

    def eligible(self, v):
        """זהה ל-_volunteer_eligible, עם ספירת קבלות אחת לכל המתנדבים (GROUP BY) במקום שאילתה לכל אחד."""
        from server.features import UNVERIFIED_MONTHLY_ACCEPTS
        if v.get("suspended"):
            return False
        if volunteer_identity.ID_REQUIRED:
            if getattr(self, "_id_ok", None) is None:
                self._id_ok = self.svc.identity_verified_set()
            if v["username"] not in self._id_ok:
                return False                     # אימות זהות (ת"ז + סלפי) חובה לכל מתנדב/ת
        if v.get("verification") == "verified":
            return True
        if self._accepts is None:
            self._accepts = {r["username"]: r["n"] for r in self.svc._q(
                """SELECT username, COUNT(*) AS n FROM recipients WHERE role='volunteer'
                   AND response IN ('accept','on_my_way','arrived','handled') AND responded_at>?
                   GROUP BY username""", (self.now - 30 * 86400,))}
        return self._accepts.get(v["username"], 0) < UNVERIFIED_MONTHLY_ACCEPTS


# ---------------------------------------------------------------- core service
class LifeGuardService(FeaturesMixin, SignalWatchMixin, EvidenceMixin, RideMixin, VolunteerMatchMixin,
                       safety_intel.SafetyIntelMixin, national.NationalMixin, privacy.PrivacyMixin,
                       fcm.FcmMixin, helpers.HelpersMixin, admin_dashboard.AdminDashboardMixin,
                       heatmap.HeatmapMixin, volunteer_identity.VolunteerIdentityMixin):
    _pw_hash = staticmethod(_hash_pw)          # חשבונות ניהול (server/admin_dashboard.py) - אותו PBKDF2

    def __init__(self, db_path, ack_timeout_seconds=120.0, default_radius_km=DEFAULT_VOLUNTEER_RADIUS_KM,
                 invite_ttl_seconds=DEFAULT_INVITE_TTL_SECONDS, sms_provider=None, public_url=None,
                 escort_grace_seconds=None, sms_fallback_seconds=None, contact_ring_seconds=None):
        from server import features
        self.db_path = db_path
        self.started_at = _now()
        self.ack_timeout = float(ack_timeout_seconds)
        self.invite_ttl = float(invite_ttl_seconds)
        self.default_radius_km = float(default_radius_km)
        self._lock = threading.RLock()
        self._dispatch_lock = threading.RLock()   # סבב תור אחד בכל פעם - מתנדב לא משובץ פעמיים
        self.router = Router()                    # OSRM עצמי בלבד (LIFEGUARD_OSRM_URL); אחרת הערכה
        self._retry_at = {}                       # incident_id -> מתי לנסות שוב לשבץ (אין מועמד כרגע)
        self._last_wake, self._wake_pending = 0.0, False
        self.ring_seconds = float(ring.RING_SECONDS if contact_ring_seconds is None else contact_ring_seconds)
        self._login_fails = {}
        self._register_hits = {}                  # כתובת -> זמני הרשמה (שעה אחרונה)
        self._streams = {}                        # משתמש -> מספר זרמים פתוחים (SSE / long-poll)
        self._health_cache = (0.0, None)                    # (משתמש, כתובת) / (כתובת) -> (כישלונות, נעול עד)
        self._con = storage.connect_sqlite(db_path, _MIGRATIONS)
        self._wakeups = {}
        self._wake_lock = threading.Lock()
        self._init_features(sms_provider or sms_mod.from_env(),
                            public_url if public_url is not None else os.environ.get("LIFEGUARD_PUBLIC_URL", ""),
                            features.ESCORT_GRACE_SECONDS if escort_grace_seconds is None else escort_grace_seconds,
                            features.SMS_FALLBACK_SECONDS if sms_fallback_seconds is None else sms_fallback_seconds)
        self.notify_identity_required_once()     # מתנדבים קיימים: הודעה אחת שנדרש אימות זהות

    @staticmethod
    def _err(status, code, message, extra=None):
        return ApiError(status, code, message, extra)

    @staticmethod
    def _valid_coords(lat, lon):
        return _valid_coords(lat, lon)

    # ---------- infra ----------
    def close(self):
        q = getattr(self, "_push_q", None)
        if q is not None:
            q.put(None)
        dc = getattr(self, "_data_cache", None)     # מטמון מקורות חיצוניים (server/data_sources.py)
        if dc is not None:
            dc.close()
        with self._lock:
            self._con.close()

    def _q(self, sql, args=(), one=False):
        with self._lock:
            cur = self._con.execute(sql, args)
            rows = cur.fetchall()
        rows = [dict(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    def _x(self, sql, args=()):
        with self._lock:
            with self._con:
                return self._con.execute(sql, args)

    def _cond(self, username):
        with self._wake_lock:
            c = self._wakeups.get(username)
            if c is None:
                c = self._wakeups[username] = threading.Condition()
            return c

    def wait_for_notifications(self, username, after_id, timeout):
        cond = self._cond(username)
        with cond:
            if not self._pending(username, after_id):
                cond.wait(timeout)
        return self._pending(username, after_id)

    def _pending(self, username, after_id):
        rows = self._q("SELECT * FROM notifications WHERE username=? AND id>? ORDER BY id LIMIT 100",
                       (username, int(after_id or 0)))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return rows

    def _notify(self, username, ntype, payload, incident_id=None):
        """תור הדחיפה: נשמר בשרת ומעיר את הזרם של הנמען. מסירה = אישור מהאפליקציה בלבד."""
        cur = self._x("INSERT INTO notifications(username, incident_id, type, payload, created_at) VALUES (?,?,?,?,?)",
                      (username, incident_id, ntype, json.dumps(payload, ensure_ascii=False), _now()))
        cond = self._cond(username)
        with cond:
            cond.notify_all()
        self._after_notify(username, ntype, payload)  # Web Push - רק אחרי שההתראה נשמרה
        return cur.lastrowid

    def _event(self, incident_id, kind, text, actor=None):
        self._x("INSERT INTO incident_events(incident_id, ts, actor, kind, text) VALUES (?,?,?,?,?)",
                (incident_id, _now(), actor, kind, text))

    # ---------- auth ----------
    REGISTER_PER_IP_HOUR = 5

    def register(self, username, password, display_name=None, ip=""):
        # ריבוי חשבונות מאותה כתובת: "תפיסת" מתנדבים ו-DoS של PBKDF2 (סקירה 2)
        now = _now()
        with self._lock:
            hits = [t for t in self._register_hits.get(ip, []) if t > now - 3600]
            # loopback = פיתוח/בדיקות מקומיות (בשרת אמיתי הכתובת היא של הלקוח, גם מאחורי Caddy דרך XFF)
            if ip and ip not in ("127.0.0.1", "::1") and len(hits) >= self.REGISTER_PER_IP_HOUR:
                raise ApiError(429, "too_many_accounts", "נוצרו יותר מדי חשבונות מהרשת הזו בשעה האחרונה")
            self._register_hits[ip] = hits + [now]
            if len(self._register_hits) > 50000:
                self._register_hits.clear()
        username = (username or "").strip().lower()
        if not USERNAME_RE.match(username):
            raise ApiError(400, "invalid_username", "שם משתמש: 3-32 תווים (אותיות, ספרות, . -)")
        if len(password or "") < 8:
            raise ApiError(400, "weak_password", "סיסמה חייבת להכיל לפחות 8 תווים")
        if self._q("SELECT 1 FROM users WHERE username=?", (username,), one=True):
            raise ApiError(409, "username_taken", "שם המשתמש כבר קיים בשרת")
        salt, h = _hash_pw(password)
        user_id = "usr_" + uuid.uuid4().hex
        self._x("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at, user_id) VALUES (?,?,?,?,?,?)",
                (username, (display_name or username).strip()[:60], salt, h, _now(), user_id))
        return {"username": username, "user_id": user_id}

    IP_FAIL_WINDOW = 600
    IP_FAIL_LIMIT = 30

    def _login_throttle(self, key, now):
        with self._lock:
            fails, until = self._login_fails.get(key, (0, 0.0))
            return until > now

    def _login_failed(self, keys, now):
        with self._lock:
            for key, limit, lock_s in keys:
                fails, until = self._login_fails.get(key, (0, 0.0))
                fails = fails + 1 if until < now else fails
                self._login_fails[key] = (0, now + lock_s) if fails >= limit else (fails, 0.0)
            if len(self._login_fails) > 50000:          # הגנת זיכרון: מפנים רק רשומות שאינן נעולות כעת
                for k in [k for k, (_, until) in self._login_fails.items() if until < now][:25000]:
                    del self._login_fails[k]

    def login(self, username, password, device="", ip=""):
        """נעילה לפי (משתמש, כתובת) ולא לחשבון כולו: אחרת כל אחד יכול לנעול את הקורבן מחוץ לחשבון
        שלו בכוונה (סקירת אבטחה). בנוסף - מגבלה כללית לכתובת על פני כל שמות המשתמש."""
        username = (username or "").strip().lower()
        now = _now()
        if self._login_throttle(("ip", ip), now) or self._login_throttle(("user", username, ip), now):
            raise ApiError(429, "locked", "יותר מדי ניסיונות - נסה/י שוב בעוד דקה")
        user = self._q("SELECT * FROM users WHERE username=?", (username,), one=True)
        ok = False
        if user:
            _, h = _hash_pw(password or "", bytes.fromhex(user["pw_salt"]))
            ok = hmac.compare_digest(h, user["pw_hash"])
        else:
            _hash_pw(password or "", b"\0" * 16)        # אותו זמן חישוב - לא חושפים קיום משתמש בתזמון
        if not ok:
            self._login_failed([(("user", username, ip), MAX_LOGIN_FAILURES, LOGIN_LOCK_SECONDS),
                                (("ip", ip), self.IP_FAIL_LIMIT, self.IP_FAIL_WINDOW)], now)
            raise ApiError(401, "bad_credentials", "שם משתמש או סיסמה שגויים")
        with self._lock:
            self._login_fails.pop(("user", username, ip), None)
        token = secrets.token_urlsafe(32)
        self._x("INSERT INTO sessions VALUES (?,?,?,?,?)",
                (_token_hash(token), username, (device or "")[:80], _now(), _now() + TOKEN_TTL_SECONDS))
        return {"token": token, "username": username, "display_name": user["display_name"],
                "expires_at": _now() + TOKEN_TTL_SECONDS}

    def authenticate(self, token):
        if not token:
            raise ApiError(401, "no_token", "נדרשת התחברות")
        row = self._q("SELECT username, expires_at FROM sessions WHERE token_hash=?", (_token_hash(token),), one=True)
        if not row or row["expires_at"] < _now():
            raise ApiError(401, "invalid_token", "ההתחברות פגה - יש להתחבר מחדש")
        return row["username"]

    def logout(self, token):
        self._x("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    # ---------- ניהול התחברויות (סקירת אבטחה: מי שהתחבר פעם לחשבון של הקורבן נשאר מחובר 30 יום) ----------
    def change_password(self, user, token, old_password, new_password):
        """החלפת סיסמה מנתקת את *כל* המכשירים האחרים מיד (כולל מכשיר של מישהו שגילה את הסיסמה)."""
        now = _now()
        if self._login_throttle(("pw", user), now):
            raise ApiError(429, "locked", "יותר מדי ניסיונות - נסה/י שוב בעוד כמה דקות")
        row = self._q("SELECT pw_salt, pw_hash FROM users WHERE username=?", (user,), one=True)
        _, h = _hash_pw(old_password or "", bytes.fromhex(row["pw_salt"]))
        if not hmac.compare_digest(h, row["pw_hash"]):
            # token גנוב לא יכול לנחש את הסיסמה הנוכחית בלי הגבלה (סקירה 2)
            self._login_failed([(("pw", user), MAX_LOGIN_FAILURES, 600)], now)
            raise ApiError(403, "bad_password", "הסיסמה הנוכחית שגויה")
        if len(new_password or "") < 8:
            raise ApiError(400, "weak_password", "סיסמה חייבת להכיל לפחות 8 תווים")
        salt, nh = _hash_pw(new_password)
        mine = _token_hash(token)

        def tx(con):
            con.execute("UPDATE users SET pw_salt=?, pw_hash=?, failed_logins=0, locked_until=0 WHERE username=?",
                        (salt, nh, user))
            return con.execute("DELETE FROM sessions WHERE username=? AND token_hash!=?", (user, mine)).rowcount
        return {"ok": True, "other_sessions_revoked": self._tx(tx)}

    def sessions(self, user, token):
        mine = _token_hash(token)
        rows = self._q("SELECT token_hash, device, created_at, expires_at FROM sessions WHERE username=? AND expires_at>? "
                       "ORDER BY created_at DESC", (user, _now()))
        return {"sessions": [{"id": r["token_hash"][:16], "device": r["device"], "created_at": r["created_at"],
                              "expires_at": r["expires_at"], "current": r["token_hash"] == mine} for r in rows]}

    def revoke_sessions(self, user, token, session_id=None):
        """session_id: מזהה מקוצר מהרשימה. בלי מזהה - כל המכשירים חוץ מהנוכחי."""
        mine = _token_hash(token)
        if session_id:
            sid = str(session_id)[:16]
            if len(sid) < 16 or mine.startswith(sid):
                raise ApiError(400, "invalid_session", "לא ניתן לנתק את המכשיר הנוכחי כך (השתמש/י ביציאה)")
            n = self._x("DELETE FROM sessions WHERE username=? AND substr(token_hash,1,16)=?", (user, sid)).rowcount
        else:
            n = self._x("DELETE FROM sessions WHERE username=? AND token_hash!=?", (user, mine)).rowcount
        return {"revoked": n}

    def me(self, user):
        u = self._q("""SELECT username, user_id, display_name, created_at, phone_last4, phone_verified, sms_fallback
                       FROM users WHERE username=?""", (user,), one=True)
        u["phone_verified"] = bool(u["phone_verified"])  # True רק אחרי אימות SMS אמיתי מול הספק
        u["sms_fallback"] = bool(u["sms_fallback"])
        v = self._q("""SELECT available, radius_km, skills, loc_updated_at, is_precise, verification, verification_org,
                       verification_note, verified_at, suspended FROM volunteers WHERE username=?""", (user,), one=True)
        if v:
            v["skills"] = json.loads(v["skills"])
            v["suspended"] = bool(v["suspended"])
            v.update(self.volunteer_stats(user))
            v["identity"] = self.identity_status(user)
        u["volunteer"] = v
        u["sms"] = self.sms_status()
        return u

    def setup_status(self, user):
        """מוכנות מצד השרת (לבדיקת המוכנות באפליקציות): אנשי קשר, מכשירים ל-Push, אימות טלפון, מתנדב,
        ותוצאת בדיקת המוכנות האחרונה (מי *באמת* קיבל). בלי פרטים מזהים של אנשי הקשר."""
        rel = {r["status"]: r["n"] for r in self._q(
            "SELECT status, COUNT(*) AS n FROM relationships WHERE owner=? GROUP BY status", (user,))}
        u = self._q("SELECT phone_verified FROM users WHERE username=?", (user,), one=True)
        v = self._q("SELECT available, verification FROM volunteers WHERE username=?", (user,), one=True)
        last = self.readiness_result(user)
        return {"contacts_accepted": rel.get("accepted", 0), "contacts_pending": rel.get("pending", 0),
                "push_devices": self._q("SELECT COUNT(*) AS n FROM push_subscriptions WHERE username=?", (user,),
                                        one=True)["n"],
                "fcm_devices": self._q("SELECT COUNT(*) AS n FROM fcm_tokens WHERE username=?", (user,),
                                       one=True)["n"],
                "fcm": self._fcm().status,
                "phone_verified": bool(u and u["phone_verified"]),
                "volunteer": {"available": bool(v["available"]), "verification": v["verification"],
                              "identity": self.identity_status(user)["status"]} if v else None,
                "last_readiness": ({"created_at": last["created_at"], "total": len(last["results"]),
                                    "confirmed": sum(1 for r in last["results"] if r["received_at"])}
                                   if last.get("id") else None),
                "sms": self.sms_status(), "server_time": _now()}

    def _display(self, username):
        r = self._q("SELECT display_name FROM users WHERE username=?", (username,), one=True)
        return r["display_name"] if r else username

    # ---------- presence / volunteers ----------
    def set_volunteer(self, user, available, radius_km=None, skills=None):
        radius = float(radius_km or self.default_radius_km)
        if not 0.2 <= radius <= 50:
            raise ApiError(400, "invalid_radius", "רדיוס בין 0.2 ל-50 ק\"מ")
        skills = [s for s in (skills or []) if isinstance(s, str)][:10]
        self._x("""INSERT INTO volunteers(username, available, radius_km, skills, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(username) DO UPDATE SET available=excluded.available, radius_km=excluded.radius_km,
                   skills=excluded.skills, updated_at=excluded.updated_at""",
                (user, int(bool(available)), radius, json.dumps(skills), _now()))
        self._wake_dispatch()
        return self.me(user)["volunteer"]

    def update_presence(self, user, lat, lon, is_precise=False, accuracy_m=None, source=None):
        lat, lon = _valid_coords(lat, lon)
        now = _now()
        self._x("""INSERT INTO presence(username, lat, lon, is_precise, updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(username) DO UPDATE SET lat=excluded.lat, lon=excluded.lon,
                   is_precise=excluded.is_precise, updated_at=excluded.updated_at""",
                (user, lat, lon, int(bool(is_precise)), now))
        cur = self._x("""UPDATE volunteers SET lat=?, lon=?, accuracy_m=?, loc_source=?, is_precise=?, loc_updated_at=?
                   WHERE username=?""", (lat, lon, accuracy_m, source, int(bool(is_precise)), now, user))
        if cur.rowcount:                 # רק מתנדב שזז משנה את מצב התור (לא כל משתמש שמעדכן נוכחות)
            self._wake_dispatch(throttled=True)
        return {"ok": True}

    SUMMARY_RADII = (1.0, 3.0, 5.0, 10.0)
    SUMMARY_GRID_DEG = 0.005            # ~500 מ'
    MIN_COUNT_TO_SHOW = 5               # k-אנונימיות לספירת משתמשים

    def community_summary(self, user, radius_km=5.0):
        """
        ספירות בלבד למשתמשים קרובים (פרטיות); מתנדבים - שם תצוגה + טווח מרחק מעוגל.
        נגד איתור (נמצא בסקירת אבטחה: חיפוש בינארי על הרדיוס/מיקום איתר משתמש ל-~1 מ'):
        המיקום שלי מעוגל לרשת של ~500 מ', הרדיוס רק מרשימה קבועה, ומספר משתמשים מוצג רק מ-5 ומעלה
        ומעוגל למטה לכפולה של 5.
        """
        try:
            radius_km = float(radius_km)
        except (TypeError, ValueError):
            radius_km = 5.0
        radius_km = min(self.SUMMARY_RADII, key=lambda r: abs(r - radius_km))
        me = self._q("SELECT lat, lon, is_precise, updated_at FROM presence WHERE username=?", (user,), one=True)
        trusted = self.relationships(user)
        out = {"location_known": bool(me), "location_precise": bool(me and me["is_precise"]),
               "radius_km": radius_km, "volunteers": [], "nearby_users_count": None, "trusted": trusted}
        if not me:
            return out
        g = self.SUMMARY_GRID_DEG
        me = {**me, "lat": (math.floor(me["lat"] / g) + 0.5) * g, "lon": (math.floor(me["lon"] / g) + 0.5) * g}
        fresh = _now() - VOLUNTEER_LOCATION_MAX_AGE_SECONDS
        vols = self._q("""SELECT v.username, v.radius_km, v.skills, v.lat, v.lon, v.is_precise, v.verification
                          FROM volunteers v WHERE v.available=1 AND v.suspended=0 AND v.lat IS NOT NULL
                          AND v.loc_updated_at>? AND v.username!=? AND v.roles LIKE '%"on_scene"%'""", (fresh, user))
        id_ok = self.identity_verified_set() if volunteer_identity.ID_REQUIRED else None
        for v in vols:
            if id_ok is not None and v["username"] not in id_ok:
                continue                         # בלי אימות זהות - לא מוצג/ת כמתנדב/ת זמין/ה
            d = haversine_km(me["lat"], me["lon"], v["lat"], v["lon"])
            if d <= radius_km:
                out["volunteers"].append({"display_name": self._display(v["username"]),
                                          "distance_band_km": _band(d),
                                          "approximate": not (v["is_precise"] and me["is_precise"]),
                                          "skills": json.loads(v["skills"]),
                                          "verified": v["verification"] == "verified"})
        out["volunteers"].sort(key=lambda x: x["distance_band_km"])
        others = self._q("SELECT lat, lon FROM presence WHERE username!=? AND updated_at>?", (user, _now() - 3600))
        n = sum(1 for o in others if haversine_km(me["lat"], me["lon"], o["lat"], o["lon"]) <= radius_km)
        out["nearby_users_count"] = (n // 5) * 5 if n >= self.MIN_COUNT_TO_SHOW else None
        out["nearby_users_few"] = n < self.MIN_COUNT_TO_SHOW
        return out

    # ---------- Safety Circle: הזמנות וקשרים (הסכמה הדדית) ----------
    # מודל: "owner" מזמין את "contact" להיות איש הקשר שלו לחירום. הקשר נוצר רק אחרי שהנמען אישר.
    # הזמנה: pending -> accepted (=connected) | declined | expired | cancelled. כל מעבר הוא UPDATE מותנה
    # (WHERE status='pending') בתוך טרנזקציה - שני מכשירים שמגיבים במקביל לא יוצרים מצב סותר.
    # התראה (notification) נוצרת רק אחרי שהמעבר נשמר במסד הנתונים.
    def _tx(self, fn):
        """טרנזקציה אטומית אחת. בתוך fn להשתמש רק ב-con.execute (לא ב-_x, שמבצע commit משלו)."""
        with self._lock:
            with self._con:
                return fn(self._con)

    def _pepper(self):
        with self._lock:
            row = self._con.execute("SELECT value FROM server_meta WHERE key='phone_pepper'").fetchone()
            if row:
                return bytes.fromhex(row[0])
            pepper = secrets.token_bytes(32)
            with self._con:
                self._con.execute("INSERT INTO server_meta(key, value) VALUES ('phone_pepper', ?)", (pepper.hex(),))
            return pepper

    def _phone_hash(self, e164):
        """מספרי טלפון לא נשמרים בשרת - רק HMAC עם מפתח סודי של השרת + 4 ספרות אחרונות לתצוגה."""
        return hmac.new(self._pepper(), e164.encode("utf-8"), hashlib.sha256).hexdigest()

    def _user_row(self, username):
        return self._q("SELECT username, user_id, phone_hash, phone_verified FROM users WHERE username=?",
                       (username,), one=True)

    def _connected(self, owner, contact):
        return bool(self._q("SELECT 1 FROM relationships WHERE owner=? AND contact=? AND status='accepted'",
                            (owner, contact), one=True))

    def set_phone(self, user, phone):
        """מספר הטלפון של המשתמש (לא מאומת - אין ספק SMS). משמש רק להתאמת הזמנות שנשלחו למספר."""
        if not phone:
            self._x("UPDATE users SET phone_hash=NULL, phone_last4=NULL, phone_enc=NULL, phone_verified=0 "
                    "WHERE username=?", (user,))
            return {"phone_last4": None, "phone_verified": False}
        e164 = normalize_phone(phone)
        if not e164:
            raise ApiError(400, "invalid_phone", "מספר טלפון לא תקין")
        h = self._phone_hash(e164)
        # המספר עצמו נשמר מוצפן (AES-GCM) - נדרש רק לשליחת SMS גיבוי בחירום. מספר חדש = לא מאומת.
        self._x("""UPDATE users SET phone_hash=?, phone_last4=?, phone_enc=?,
                   phone_verified=CASE WHEN phone_hash=? THEN phone_verified ELSE 0 END WHERE username=?""",
                (h, e164[-4:], self.crypto.encrypt(e164, aad=user.encode()), h, user))
        self._deliver_phone_invites(user)        # רק אם המספר (הזהה) כבר אומת
        verified = self._q("SELECT phone_verified FROM users WHERE username=?", (user,), one=True)["phone_verified"]
        return {"phone_last4": e164[-4:], "phone_verified": bool(verified)}

    def _inv_for_inviter(self, inv, code=None):
        out = {k: inv[k] for k in ("id", "channel", "status", "created_at", "expires_at", "last_sent_at",
                                   "resend_count", "responded_at")}
        out.update({"label": inv["target_label"], "target_username": inv["target_username"],
                    "target_display": self._display(inv["target_username"]) if inv["target_username"] else None,
                    "phone_last4": inv["target_phone_last4"],
                    "awaiting_confirmation": bool(inv["awaiting_confirmation"]),
                    "accepted_by": inv["accepted_by"],
                    "accepted_by_display": self._display(inv["accepted_by"]) if inv["accepted_by"] else None,
                    "resends_left": max(0, MAX_RESENDS - inv["resend_count"])})
        if code:
            out["code"] = code  # מוחזר רק ברגע היצירה/השליחה מחדש. בשרת נשמר hash בלבד.
        return out

    def _inv_for_target(self, inv):
        return {"id": inv["id"], "from": inv["inviter"], "from_display": self._display(inv["inviter"]),
                "channel": inv["channel"], "via_phone": inv["channel"] == "phone",
                "created_at": inv["created_at"], "expires_at": inv["expires_at"],
                "status": inv["status"], "awaiting_confirmation": bool(inv["awaiting_confirmation"]),
                "text": f"{self._display(inv['inviter'])} מבקש/ת להוסיף אותך כאיש קשר לחירום"}

    def _get_inv(self, inv_id):
        return self._q("SELECT * FROM invitations WHERE id=?", (str(inv_id or ""),), one=True)

    def _is_target(self, inv, user):
        """האם user הוא הנמען של ההזמנה. נבדק גם המזהה הייחודי (לא רק שם המשתמש)."""
        me = self._user_row(user)
        if not me:
            return False
        if inv["target_username"]:
            return inv["target_username"] == user and inv["target_id"] == me["user_id"]
        # הזמנה לפי טלפון מגיעה רק לחשבון שאימת את המספר ב-SMS. אחרת כל אחד יכול "לטעון" למספר של
        # מישהו אחר, לראות מי מזמין אותו (למשל עובדת מקלט) ולחטוף את ההזמנה. בלי אימות - מצטרפים בקוד.
        return bool(inv["channel"] == "phone" and me["phone_hash"] and me["phone_verified"]
                    and inv["target_phone_hash"] == me["phone_hash"])

    def _deliver_phone_invites(self, user):
        """אחרי אימות טלפון: הזמנות ממתינות למספר הזה מגיעות עכשיו לחשבון."""
        me = self._user_row(user)
        if not (me and me["phone_hash"] and me["phone_verified"]):
            return
        self._expire_invitations()
        for inv in self._q("""SELECT * FROM invitations WHERE channel='phone' AND status='pending'
                              AND awaiting_confirmation=0 AND target_phone_hash=? AND inviter!=?""",
                           (me["phone_hash"], user)):
            self._notify(user, "relationship_invite", self._inv_for_target(inv))

    def _notify_inv_parties(self, inv, status, extra=None):
        """עדכון סטטוס לשני הצדדים (כל המכשירים שלהם) - אחרי שהשינוי נשמר."""
        payload = {"invitation_id": inv["id"], "status": status}
        payload.update(extra or {})
        self._notify(inv["inviter"], "relationship_update", payload)
        for other in {inv["target_username"], inv["accepted_by"]} - {None, inv["inviter"]}:
            self._notify(other, "relationship_update", payload)

    def _expire_invitations(self, now=None):
        now = now or _now()
        for inv in self._q("SELECT * FROM invitations WHERE status='pending' AND expires_at<=?", (now,)):
            cur = self._x("UPDATE invitations SET status='expired' WHERE id=? AND status='pending' AND expires_at<=?",
                          (inv["id"], now))
            if cur.rowcount:
                self._notify_inv_parties(inv, "expired")

    def create_invitation(self, inviter, username=None, phone=None, label=None, as_code=False, send_sms=False):
        """הזמנה לפי שם משתמש (משתמש קיים), טלפון, או קוד חד-פעמי לשיתוף (גם למי שעוד לא התקין)."""
        self._expire_invitations()
        now = _now()
        me = self._user_row(inviter)
        label = (label or "").strip()[:60] or None
        pending = self._q("SELECT COUNT(*) AS n FROM invitations WHERE inviter=? AND status='pending'",
                          (inviter,), one=True)["n"]
        if pending >= MAX_PENDING_INVITES:
            raise ApiError(429, "too_many_invites", f"יש לך כבר {MAX_PENDING_INVITES} הזמנות ממתינות. בטל/י חלק מהן.")
        fields = {"target_username": None, "target_id": None, "target_phone_hash": None, "target_phone_last4": None}
        code = None
        if username:
            contact = str(username).strip().lower().lstrip("@")
            if contact == inviter:
                raise ApiError(400, "self_invite", "לא ניתן להזמין את עצמך")
            target = self._user_row(contact)
            if not target:
                raise ApiError(404, "no_such_user", "אין משתמש LifeGuard בשם הזה. אפשר להזמין לפי מספר טלפון "
                                                    "או לשלוח קוד הזמנה - הוא יוכל להצטרף אחרי ההתקנה.")
            if self._connected(inviter, contact):
                raise ApiError(409, "already_connected", "כבר מחוברים - הוא/היא כבר איש קשר שלך")
            dup = self._q("SELECT id FROM invitations WHERE inviter=? AND target_username=? AND status='pending'",
                          (inviter, contact), one=True)
            if dup:
                raise ApiError(409, "duplicate_invitation", "כבר נשלחה הזמנה שממתינה לאישור. אפשר לשלוח אותה שוב.",
                               {"invitation_id": dup["id"]})
            if self._q("""SELECT 1 FROM invitations WHERE inviter=? AND target_username=? AND status='declined'
                          AND responded_at>?""", (inviter, contact, now - DECLINE_COOLDOWN_SECONDS), one=True):
                raise ApiError(429, "declined_recently", "ההזמנה נדחתה לאחרונה. אפשר להזמין שוב רק בעוד יממה.")
            channel = "username"
            fields.update(target_username=contact, target_id=target["user_id"])
        elif phone:
            e164 = normalize_phone(phone)
            if not e164:
                raise ApiError(400, "invalid_phone", "מספר טלפון לא תקין")
            h = self._phone_hash(e164)
            if me["phone_hash"] == h:
                raise ApiError(400, "self_invite", "זה מספר הטלפון שלך")
            dup = self._q("SELECT id FROM invitations WHERE inviter=? AND target_phone_hash=? AND status='pending'",
                          (inviter, h), one=True)
            if dup:
                raise ApiError(409, "duplicate_invitation", "כבר נשלחה הזמנה למספר הזה. אפשר לשלוח אותה שוב.",
                               {"invitation_id": dup["id"]})
            channel, code = "phone", new_invite_code()
            fields.update(target_phone_hash=h, target_phone_last4=e164[-4:])
        elif as_code:
            channel, code = "code", new_invite_code()
        else:
            raise ApiError(400, "no_target", "יש לציין שם משתמש, מספר טלפון או בקשה לקוד הזמנה")
        inv_id = "inv_" + uuid.uuid4().hex
        try:
            self._x("""INSERT INTO invitations(id, inviter, inviter_id, channel, target_username, target_id,
                       target_phone_hash, target_phone_last4, target_label, token_hash, status, created_at,
                       last_sent_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)""",
                    (inv_id, inviter, me["user_id"], channel, fields["target_username"], fields["target_id"],
                     fields["target_phone_hash"], fields["target_phone_last4"], label,
                     _code_hash(code) if code else None, now, now, now + self.invite_ttl))
        except sqlite3.IntegrityError:  # שתי בקשות במקביל - האינדקס הייחודי מונע כפילות
            raise ApiError(409, "duplicate_invitation", "כבר נשלחה הזמנה שממתינה לאישור")
        inv = self._get_inv(inv_id)
        self._deliver_invitation(inv)
        out = self._inv_for_inviter(inv, code)
        if send_sms and channel == "phone":  # רק כשיש ספק SMS בשרת; אחרת המשתמש שולח את הקוד בעצמו
            out.update(self.send_invite_sms(inviter, e164, code, inv["expires_at"]))
        return out

    def _deliver_invitation(self, inv):
        if inv["target_username"]:
            self._notify(inv["target_username"], "relationship_invite", self._inv_for_target(inv))
        elif inv["channel"] == "phone":
            for u in self._q("SELECT username FROM users WHERE phone_hash=? AND username!=?",
                             (inv["target_phone_hash"], inv["inviter"])):
                self._notify(u["username"], "relationship_invite", self._inv_for_target(inv))

    def _establish(self, inv, acceptor, from_awaiting=False):
        """הזמנה -> קשר. אטומי: אם ההזמנה כבר לא ממתינה (בוטלה/פגה/אושרה במכשיר אחר) - 409."""
        if acceptor == inv["inviter"]:
            raise ApiError(400, "self_invite", "לא ניתן לאשר הזמנה של עצמך")
        acc = self._user_row(acceptor)
        now = _now()

        def tx(con):
            cur = con.execute("""UPDATE invitations SET status='accepted', accepted_by=?, accepted_by_id=?,
                                 awaiting_confirmation=0, responded_at=? WHERE id=? AND status='pending'
                                 AND awaiting_confirmation=? AND expires_at>?""",
                              (acceptor, acc["user_id"], now, inv["id"], 1 if from_awaiting else 0, now))
            if not cur.rowcount:
                return None
            con.execute("""INSERT OR IGNORE INTO relationships(owner, contact, status, created_at, responded_at,
                           invitation_id) VALUES (?,?,'accepted',?,?,?)""",
                        (inv["inviter"], acceptor, now, now, inv["id"]))
            # הזמנות כפולות אחרות מאותו מזמין לאותו אדם (לפי שם/טלפון) כבר לא רלוונטיות
            others = [r[0] for r in con.execute(
                """SELECT id FROM invitations WHERE inviter=? AND status='pending' AND id!=?
                   AND (target_username=? OR (target_phone_hash IS NOT NULL AND target_phone_hash=?))""",
                (inv["inviter"], inv["id"], acceptor, acc["phone_hash"] or "-")).fetchall()]
            for oid in others:
                con.execute("UPDATE invitations SET status='accepted', accepted_by=?, accepted_by_id=?, responded_at=? "
                            "WHERE id=?", (acceptor, acc["user_id"], now, oid))
            return others

        others = self._tx(tx)
        if others is None:
            self._raise_not_pending(inv["id"], acceptor)
        payload = {"contact": acceptor, "contact_display": self._display(acceptor), "owner": inv["inviter"],
                   "owner_display": self._display(inv["inviter"])}
        self._notify_inv_parties(self._get_inv(inv["id"]), "connected", payload)
        for oid in others:
            self._notify_inv_parties(self._get_inv(oid), "connected", payload)
        return {"invitation_id": inv["id"], "status": "connected", "owner": inv["inviter"], "contact": acceptor}

    _STATUS_HE = {"accepted": "אושרה", "declined": "נדחתה", "expired": "פגה", "cancelled": "בוטלה"}

    def _raise_not_pending(self, inv_id, user=None):
        cur = self._get_inv(inv_id)
        st = cur["status"] if cur else "missing"
        if st == "expired" or (cur and st == "pending" and cur["expires_at"] <= _now()):
            raise ApiError(410, "invitation_expired", "תוקף ההזמנה פג. בקש/י מהשולח לשלוח אותה שוב.", {"status": "expired"})
        if st == "pending" and cur["awaiting_confirmation"]:
            raise ApiError(409, "awaiting_confirmation", "ההזמנה כבר אושרה וממתינה לאישור השולח", {"status": "pending"})
        raise ApiError(409, "invitation_not_pending", f"ההזמנה כבר {self._STATUS_HE.get(st, 'לא בתוקף')}",
                       {"status": st})

    def respond_invitation(self, user, inv_id, accept):
        """הנמען מאשר/דוחה. הזמנה שהגיעה לפי טלפון (לא מאומת) דורשת גם אישור זהות מהשולח."""
        self._expire_invitations()
        inv = self._get_inv(inv_id)
        if not inv or not self._is_target(inv, user):
            raise ApiError(404, "no_invite", "אין הזמנה כזו")
        if inv["status"] == "accepted" and inv["accepted_by"] == user and accept:
            return {"invitation_id": inv["id"], "status": "connected", "owner": inv["inviter"], "contact": user}
        if inv["status"] != "pending" or inv["awaiting_confirmation"]:
            self._raise_not_pending(inv["id"], user)
        if not accept:
            return self._decline(inv, user)
        phone_verified = bool(self._q("SELECT phone_verified FROM users WHERE username=?", (user,), one=True)
                              ["phone_verified"])
        # טלפון שאומת ב-SMS = הוכחת בעלות על המספר -> אין צורך באישור זהות נוסף מהשולח
        if inv["channel"] == "phone" and not inv["target_username"] and not phone_verified:
            acc = self._user_row(user)
            cur = self._x("""UPDATE invitations SET awaiting_confirmation=1, accepted_by=?, accepted_by_id=?,
                             responded_at=? WHERE id=? AND status='pending' AND awaiting_confirmation=0""",
                          (user, acc["user_id"], _now(), inv["id"]))
            if not cur.rowcount:
                self._raise_not_pending(inv["id"], user)
            self._notify_inv_parties(self._get_inv(inv["id"]), "awaiting_confirmation",
                                     {"contact": user, "contact_display": self._display(user)})
            return {"invitation_id": inv["id"], "status": "awaiting_confirmation"}
        return self._establish(inv, user)

    def _decline(self, inv, user):
        cur = self._x("""UPDATE invitations SET status='declined', responded_at=?, accepted_by=NULL
                         WHERE id=? AND status='pending'""", (_now(), inv["id"]))
        if not cur.rowcount:
            self._raise_not_pending(inv["id"], user)
        self._notify_inv_parties(inv, "declined", {"contact_display": self._display(user)})
        return {"invitation_id": inv["id"], "status": "declined"}

    def confirm_invitation(self, user, inv_id, confirm):
        """השולח מאשר שהאדם שאישר הזמנת-טלפון הוא באמת איש הקשר שלו (הטלפון בשרת לא מאומת)."""
        inv = self._get_inv(inv_id)
        if not inv or inv["inviter"] != user:
            raise ApiError(404, "no_invite", "אין הזמנה כזו")
        if inv["status"] != "pending" or not inv["awaiting_confirmation"]:
            self._raise_not_pending(inv["id"], user)
        if confirm:
            return self._establish(inv, inv["accepted_by"], from_awaiting=True)
        cur = self._x("""UPDATE invitations SET status='cancelled', responded_at=? WHERE id=? AND status='pending'
                         AND awaiting_confirmation=1""", (_now(), inv["id"]))
        if not cur.rowcount:
            self._raise_not_pending(inv["id"], user)
        self._notify_inv_parties(inv, "cancelled", {"reason": "identity_not_confirmed"})
        return {"invitation_id": inv["id"], "status": "cancelled"}

    def _check_redeem_rate(self, user):
        since = _now() - REDEEM_WINDOW_SECONDS
        n = self._q("SELECT COUNT(*) AS n FROM redeem_failures WHERE username=? AND ts>?", (user, since), one=True)["n"]
        if n >= REDEEM_MAX_FAILURES:
            raise ApiError(429, "too_many_attempts", "יותר מדי קודים שגויים. נסה/י שוב בעוד כמה דקות.")

    def redeem_invitation(self, user, code, accept=True, preview=False):
        """קוד הזמנה חד-פעמי (נשלח ע"י המזמין בערוץ שלו - SMS/WhatsApp). מחזיק הקוד הוא הנמען."""
        self._check_redeem_rate(user)
        h = _code_hash(code)
        inv = self._q("SELECT * FROM invitations WHERE token_hash=?", (h,), one=True) if h else None
        if not inv:
            self._x("INSERT INTO redeem_failures(username, ts) VALUES (?,?)", (user, _now()))
            raise ApiError(404, "invalid_code", "קוד הזמנה לא תקין")
        if inv["inviter"] == user:
            raise ApiError(400, "self_invite", "זה קוד ההזמנה שאת/ה שלחת")
        self._expire_invitations()
        inv = self._get_inv(inv["id"])
        if inv["status"] == "accepted" and inv["accepted_by"] == user:
            return {"invitation_id": inv["id"], "status": "connected", "owner": inv["inviter"], "contact": user}
        if inv["status"] != "pending" or inv["awaiting_confirmation"]:
            self._raise_not_pending(inv["id"], user)
        if preview:
            return {"invitation_id": inv["id"], "from": inv["inviter"], "from_display": self._display(inv["inviter"]),
                    "expires_at": inv["expires_at"], "already_connected": self._connected(inv["inviter"], user)}
        if not accept:
            return self._decline(inv, user)
        return self._establish(inv, user)

    def resend_invitation(self, user, inv_id):
        """שליחה מחדש: מאריך תוקף, ובהזמנת טלפון/קוד מנפיק קוד חדש (הקוד הקודם מפסיק לעבוד)."""
        self._expire_invitations()
        inv = self._get_inv(inv_id)
        if not inv or inv["inviter"] != user:
            raise ApiError(404, "no_invite", "אין הזמנה כזו")
        if inv["status"] not in ("pending", "expired") or inv["awaiting_confirmation"]:
            self._raise_not_pending(inv["id"], user)
        if inv["resend_count"] >= MAX_RESENDS:
            raise ApiError(429, "too_many_resends", "הגעת למספר השליחות המרבי להזמנה הזו. בטל/י ושלח/י הזמנה חדשה.")
        if _now() - inv["last_sent_at"] < RESEND_COOLDOWN_SECONDS:
            raise ApiError(429, "resend_too_soon", f"אפשר לשלוח שוב בעוד {RESEND_COOLDOWN_SECONDS} שניות")
        if inv["target_username"] and self._connected(user, inv["target_username"]):
            raise ApiError(409, "already_connected", "כבר מחוברים")
        code = new_invite_code() if inv["channel"] != "username" else None
        now = _now()
        try:
            cur = self._x("""UPDATE invitations SET status='pending', token_hash=?, expires_at=?, last_sent_at=?,
                             resend_count=resend_count+1 WHERE id=? AND status IN ('pending','expired')
                             AND awaiting_confirmation=0""",
                          (_code_hash(code) if code else None, now + self.invite_ttl, now, inv["id"]))
        except sqlite3.IntegrityError:  # הזמנה ממתינה אחרת לאותו נמען כבר קיימת
            raise ApiError(409, "duplicate_invitation", "יש כבר הזמנה ממתינה אחרת לאותו אדם")
        if not cur.rowcount:
            self._raise_not_pending(inv["id"], user)
        inv = self._get_inv(inv["id"])
        self._deliver_invitation(inv)
        self._notify(user, "relationship_update", {"invitation_id": inv["id"], "status": "pending", "resent": True})
        return self._inv_for_inviter(inv, code)

    def cancel_invitation(self, user, inv_id):
        inv = self._get_inv(inv_id)
        if not inv or inv["inviter"] != user:
            raise ApiError(404, "no_invite", "אין הזמנה כזו")
        cur = self._x("UPDATE invitations SET status='cancelled', responded_at=? WHERE id=? AND status='pending'",
                      (_now(), inv["id"]))
        if not cur.rowcount:
            self._raise_not_pending(inv["id"], user)
        self._notify_inv_parties(inv, "cancelled")
        return {"invitation_id": inv["id"], "status": "cancelled"}

    def disconnect(self, user, other, scope="both"):
        """ניתוק קשר קיים (כל צד רשאי). מי שנותק מאבד מיד גישה לאירועים פעילים של הצד השני."""
        other = (other or "").strip().lower()
        pairs = []
        if scope in ("both", "my_contact"):
            pairs.append((user, other))
        if scope in ("both", "contact_for"):
            pairs.append((other, user))
        removed = []
        for owner, contact in pairs:
            cur = self._x("DELETE FROM relationships WHERE owner=? AND contact=?", (owner, contact))
            if cur.rowcount:
                removed.append((owner, contact))
        if not removed:
            raise ApiError(404, "not_connected", "אין קשר כזה")
        for owner, contact in removed:
            # כל האירועים - גם שהסתיימו: מי שנותק לא רואה יותר שום מיקום/הודעה/פרופיל של הצד השני
            for r in self._q("""SELECT r.incident_id, r.state, r.ring_status, i.status FROM recipients r
                                JOIN incidents i ON i.id=r.incident_id
                                WHERE i.owner=? AND r.username=? AND r.role='trusted' AND r.state!='revoked'""",
                             (owner, contact)):
                iid = r["incident_id"]
                self._x("UPDATE recipients SET state='revoked', ring_status=CASE WHEN ring_status IN "
                        "('queued','ringing') THEN 'rang' ELSE ring_status END WHERE incident_id=? AND username=?",
                        (iid, contact))
                if r["status"] != "active":
                    continue
                self._event(iid, "revoked", f"הגישה של {self._display(contact)} בוטלה (הקשר נותק)")
                if r["state"] != "queued":
                    # רק מי שידע על האירוע מקבל הודעה. מי שהיה בתור הצלצול לא ידע - ולא ילמד עכשיו (סקירה 2)
                    self._notify(contact, "incident_closed", {"incident_id": iid, "reason": "disconnected"}, iid)
                if r["ring_status"] == "ringing":
                    # צלצלו אליו ברגע הניתוק - ממשיכים מיד לבא בתור; אחרת השרשרת "נתקעת" ומצוקה שקטה
                    # לא מגיעה לאף אחד (סקירה 2, ממצא חמור)
                    self._ring_next(iid, reason="disconnected")
                elif self._needs_volunteers(iid):
                    self._wake_dispatch()
                    self._dispatch_for(iid)
            # עדכוני ליווי: מי שנותק לא ממשיך לעקוב ("הגיע/ה בשלום" וכו')
            self._x("""DELETE FROM escort_watchers WHERE username=? AND escort_id IN
                       (SELECT id FROM escorts WHERE owner=?)""", (contact, owner))
            # קישורי מיקום ב-SMS שנשלחו לאיש הקשר הזה - מבוטלים
            self._x("""DELETE FROM location_links WHERE username=? AND incident_id IN
                       (SELECT id FROM incidents WHERE owner=?)""", (contact, owner))
            payload = {"owner": owner, "contact": contact, "by": user, "by_display": self._display(user),
                       "owner_display": self._display(owner), "contact_display": self._display(contact)}
            self._notify(owner, "relationship_removed", payload)
            self._notify(contact, "relationship_removed", payload)
        return {"ok": True, "removed": [{"owner": o, "contact": c} for o, c in removed]}

    # תאימות לאחור (API קודם): הזמנה/תגובה לפי שם משתמש
    def invite(self, owner, contact):
        return self.create_invitation(owner, username=contact)

    def respond_invite(self, contact, owner, accept):
        inv = self._q("""SELECT id FROM invitations WHERE inviter=? AND target_username=? AND status='pending'
                         ORDER BY created_at DESC LIMIT 1""", (owner, contact), one=True)
        if not inv:
            raise ApiError(404, "no_invite", "אין הזמנה כזו")
        return self.respond_invitation(contact, inv["id"], accept)

    def remove_relationship(self, user, other):
        return self.disconnect(user, other, "both")

    def relationships(self, user):
        """מצב מעגל הביטחון מהשרת (מקור האמת) - זהה בכל מכשיר ואחרי התקנה מחדש."""
        self._expire_invitations()
        now = _now()
        mine = self._q("""SELECT contact AS username, responded_at AS connected_at FROM relationships
                          WHERE owner=? AND status='accepted' ORDER BY responded_at""", (user,))
        theirs = self._q("""SELECT owner AS username, responded_at AS connected_at FROM relationships
                            WHERE contact=? AND status='accepted' ORDER BY responded_at""", (user,))
        for r in mine + theirs:
            r["display_name"] = self._display(r["username"])
            r["status"] = "connected"
        for r in theirs:   # האם האדם הזה משתף איתי מיקום חי כרגע (מצב "תמיד" ולא מושהה) - בלי לחשוף את המיקום
            r["shares_location_live"] = (self.location_mode_for(r["username"], user) == "always"
                                         and not self._settings(r["username"])["sharing_paused"])
        sent = [self._inv_for_inviter(i) for i in self._q(
            """SELECT * FROM invitations WHERE inviter=? AND (status='pending'
               OR (status IN ('declined','expired') AND COALESCE(responded_at, expires_at) > ?))
               ORDER BY created_at DESC LIMIT 50""", (user, now - 30 * 86400))]
        me = self._user_row(user)
        received = []
        for i in self._q("""SELECT * FROM invitations WHERE status='pending' AND inviter!=? AND
                            (target_username=? OR (channel='phone' AND target_username IS NULL
                             AND target_phone_hash IS NOT NULL AND target_phone_hash=?))
                            ORDER BY created_at DESC""", (user, user, me["phone_hash"] or "-")):
            if not self._is_target(i, user):
                continue
            if i["awaiting_confirmation"] and i["accepted_by"] != user:
                continue  # מישהו אחר עם אותו מספר כבר אישר
            received.append(self._inv_for_target(i))
        return {"my_contacts": mine, "i_am_contact_for": theirs, "sent_invitations": sent,
                "received_invitations": received, "server_time": now}

    def notify_trusted(self, user, text, ntype="info"):
        contacts = [r["contact"] for r in self._q(
            "SELECT contact FROM relationships WHERE owner=? AND status='accepted'", (user,))]
        ids = {}
        for c in contacts:
            ids[c] = self._notify(c, ntype, {"from": user, "from_display": self._display(user), "text": text[:500]})
        return {"sent_to": contacts, "notification_ids": ids}

    def notification_status(self, user, ids):
        """לשולח: האם אפליקציית הנמען אישרה קבלה (למשל להודעת בדיקה)."""
        out = {}
        for username, nid in list((ids or {}).items())[:50]:
            try:
                nid = int(nid)
            except (TypeError, ValueError):
                continue
            r = self._q("SELECT received_at, payload FROM notifications WHERE id=? AND username=?",
                        (nid, username), one=True)
            # רק הודעות שהשולח עצמו יצר (notify_trusted). אחרת זה ערוץ צד לפעילות של כל משתמש -
            # כולל מצוקה שקטה (נמצא בסקירת אבטחה).
            mine = bool(r) and json.loads(r["payload"]).get("from") == user
            out[username] = bool(mine and r["received_at"])
        return out

    # ---------- incidents ----------
    def create_incident(self, owner, client_id, mode="loud", emergency_type="unknown", severity="unknown",
                        location=None, profile=None, escort_id=None, risk=None, trigger=None):
        if escort_id:  # המכשיר פתח SOS בעצמו בתום ליווי - השרת לא יפתח אירוע נוסף
            iid = self.create_incident(owner, client_id, mode, emergency_type, severity, location, profile,
                                       risk=risk, trigger=trigger)["id"]
            self.link_escort_incident(owner, escort_id, iid)
            return self.get_incident(owner, iid)
        if not client_id or len(client_id) > 100:
            raise ApiError(400, "invalid_client_id", "client_id חסר")
        mode = mode if mode in ("loud", "silent") else "loud"
        emergency_type = emergency_type if emergency_type in EMERGENCY_TYPES else "unknown"
        severity = severity if severity in SEVERITIES else "unknown"
        existing = self._q("SELECT id FROM incidents WHERE owner=? AND client_id=?", (owner, client_id), one=True)
        if existing:  # idempotency: ניסיון חוזר מה-outbox לא יוצר אירוע כפול
            return self.get_incident(owner, existing["id"])
        # SOS ידני - מיד ובמלואו. זיהוי אוטומטי: לפי ביטחון (server/privacy.py): נמוך נדחה, בינוני בלי מתנדבים
        trigger_kind, activation = self.activation_for(trigger, dispatch.clean_confidence(risk))
        iid = "inc_" + uuid.uuid4().hex
        now = _now()
        profile = profile if isinstance(profile, dict) else {}
        # medical_notes לא נשמר כאן: הפרופיל נשמר בגלוי ומועתק לתוכן ההתראות. מידע רפואי עובר רק
        # בערוץ המוצפן (/me/medical -> _medical_for_incident), ורק בזמן אירוע פעיל (סקירת אבטחה)
        profile = {k: str(v)[:1000] for k, v in profile.items() if k in ("display_name", "notes")}
        r = dispatch.clean_risk(risk)
        level, score, reasons = r if r else (None, None, [])
        # סבב התור (כל שנייה) לא ירוץ באמצע היצירה: אחרת הוא עלול לראות מצוקה שקטה *לפני* שצורפו
        # אנשי הקשר ("שקט בלי אנשי קשר") ולשלוח אותה למתנדבים. (באג אמיתי שנמצא בהרצה מלאה.)
        with self._dispatch_lock:
            self._x("""INSERT INTO incidents(id, owner, client_id, mode, emergency_type, severity, status, profile,
                       created_at, updated_at, risk_level, risk_score, risk_reasons, risk_confidence, risk_signals,
                       trigger_kind, activation)
                       VALUES (?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?)""",
                    (iid, owner, client_id, mode, emergency_type, severity, json.dumps(profile, ensure_ascii=False),
                     now, now, level, score, json.dumps(reasons, ensure_ascii=False),
                     dispatch.clean_confidence(risk) if r else None,
                     json.dumps(dispatch.clean_signals(risk) if r else [], ensure_ascii=False), trigger_kind, activation))
            self.audit("emergency_activated", actor=owner, subject=owner, incident_id=iid,
                       details={"trigger": trigger_kind, "activation": activation, "mode": mode,
                                "confidence": dispatch.clean_confidence(risk)})
            self._event(iid, "created", f"אירוע נפתח ({'שקט' if mode == 'silent' else 'גלוי'})", owner)
            if location:
                self._add_location(iid, location)
            # שרשרת צלצול: כל אנשי הקשר נרשמים "בתור" לפי קרבה, ורק הראשון מקבל צפצוף (server/ring.py)
            contacts = self._q("""SELECT r.contact AS username, r.responded_at AS connected_at, p.lat, p.lon, p.updated_at
                                  FROM relationships r LEFT JOIN presence p ON p.username=r.contact
                                  WHERE r.owner=? AND r.status='accepted'""", (owner,))
            loc_row = self._latest_location(iid)
            order = ring.order_contacts(contacts, (loc_row["lat"], loc_row["lon"]) if loc_row else None, now)
            for i, c in enumerate(order):
                self._add_recipient(iid, c, "trusted", queued=True, ring_order=i)
            if order:
                self._ring_next(iid)
        if self._needs_volunteers(iid):
            if mode == "silent":
                self._event(iid, "fallback", "אין אנשי קשר מחוברים - מצוקה שקטה נשלחת למתנדבים זמינים בקרבת מקום")
            if not location:
                self._event(iid, "no_volunteer", "לא נשלח למתנדבים: אין מיקום לאירוע")
            self._dispatch_for(iid)
        return self.get_incident(owner, iid)

    # ---------- שרשרת צלצול לאנשי קשר (server/ring.py) ----------
    def _ring_next(self, iid, reason=None):
        """מצלצל לאיש הקשר הבא בתור. מחזיר את שמו, או None כשהתור נגמר."""
        nxt = self._q("""SELECT username FROM recipients WHERE incident_id=? AND role='trusted' AND state='queued'
                         ORDER BY ring_order LIMIT 1""", (iid,), one=True)
        if not nxt:
            if reason:
                self._event(iid, "ring_exhausted", "כל אנשי הקשר קיבלו צלצול ואף אחד עוד לא ענה")
                if self._needs_volunteers(iid):
                    self._wake_dispatch()
                    self._dispatch_for(iid)
            return None
        cur = self._x("""UPDATE recipients SET state='notified', ring_status='ringing', notified_at=?
                         WHERE incident_id=? AND username=? AND state='queued'""", (_now(), iid, nxt["username"]))
        if not cur.rowcount:
            return None
        first = not self._q("""SELECT 1 FROM recipients WHERE incident_id=? AND role='trusted'
                               AND ring_status IN ('rang','answered','informed') LIMIT 1""", (iid,), one=True)
        self._event(iid, "ringing", f"מצלצל ל{self._display(nxt['username'])}"
                    + (" (איש הקשר הקרוב ביותר)" if first else " (הקודם לא ענה)"))
        self._notify(nxt["username"], "incident_new", {**self._view_for(iid, nxt["username"]), "ring": True}, iid)
        return nxt["username"]

    def _stop_ring(self, iid, username, status):
        cur = self._x("""UPDATE recipients SET ring_status=? WHERE incident_id=? AND username=?
                         AND ring_status='ringing'""", (status, iid, username))
        if cur.rowcount:
            self._notify(username, "incident_update", {"incident_id": iid, "event": "ring_stopped"}, iid)
        return bool(cur.rowcount)

    def _inform_queued(self, iid):
        """מישהו קיבל את האירוע: שאר אנשי הקשר בתור מקבלים עדכון שקט (בלי צפצוף)."""
        for r in self._q("SELECT username FROM recipients WHERE incident_id=? AND role='trusted' AND state='queued'",
                         (iid,)):
            cur = self._x("""UPDATE recipients SET state='notified', ring_status='informed', notified_at=?
                             WHERE incident_id=? AND username=? AND state='queued'""", (_now(), iid, r["username"]))
            if cur.rowcount:
                self._notify(r["username"], "incident_new", {**self._view_for(iid, r["username"]), "ring": False}, iid)

    def _ring_tick(self, now):
        rows = self._q("""SELECT r.incident_id, r.username FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.status='active' AND r.role='trusted' AND r.ring_status='ringing'
                          AND r.state IN ('notified','delivered') AND r.notified_at < ?""", (now - self.ring_seconds,))
        for r in rows:
            if self._stop_ring(r["incident_id"], r["username"], "rang"):
                self._event(r["incident_id"], "ring_timeout", f"{self._display(r['username'])} לא ענה/תה - עוברים לבא בתור")
                self._ring_next(r["incident_id"], reason="timeout")

    def _needs_volunteers(self, iid):
        """אירוע גלוי -> תמיד מתנדבים. מצוקה שקטה -> רק אנשי קשר, *אלא אם אין אף איש קשר מחובר*:
        אסור ש-SOS יגיע לאף אחד. זיהוי אוטומטי שלא אושר (ביטחון בינוני) -> בלי מתנדבים עד אישור."""
        inc = self._incident_row(iid)
        if inc["activation"] in ("unconfirmed", "support"):
            return False
        if inc["mode"] == "loud":
            return True
        # איש קשר שהגישה שלו בוטלה (נותק באמצע) לא נחשב - אחרת מצוקה שקטה לא מגיעה לאף אחד
        trusted = self._q("SELECT state, ring_status FROM recipients WHERE incident_id=? AND role='trusted' "
                          "AND state!='revoked'", (iid,))
        if not trusted:
            return True
        if any(t["state"] == "accepted" for t in trusted):
            return False
        # שרשרת הצלצול עדיין רצה (מישהו בתור או מצלצל עכשיו) - מחכים לאנשי הקשר.
        # כולם קיבלו צלצול ואף אחד לא ענה -> מתנדבים.
        return not any(t["ring_status"] in ("queued", "ringing") or t["state"] == "queued" for t in trusted)

    def _add_location(self, iid, loc):
        lat, lon = _valid_coords(loc.get("lat"), loc.get("lon"))
        self._x("""INSERT INTO incident_locations(incident_id, lat, lon, accuracy_m, source, is_precise, captured_at,
                   received_at) VALUES (?,?,?,?,?,?,?,?)""",
                (iid, lat, lon, loc.get("accuracy_m"), str(loc.get("source") or "")[:60],
                 int(bool(loc.get("is_precise"))), loc.get("captured_at") or _now(), _now()))

    def _incident_row(self, iid):
        return self._q("SELECT * FROM incidents WHERE id=?", (iid,), one=True)

    def _latest_location(self, iid):
        return self._q("SELECT lat, lon, accuracy_m, source, is_precise, captured_at, received_at FROM incident_locations "
                       "WHERE incident_id=? ORDER BY id DESC LIMIT 1", (iid,), one=True)

    def _add_recipient(self, iid, username, role, distance_km=None, level=0, queued=False, ring_order=None):
        if queued:
            # בתור הצלצול: עוד לא יודע על האירוע (אין התראה, אין גישה) עד שיגיע תורו
            self._x("""INSERT OR IGNORE INTO recipients(incident_id, username, role, state, ring_status, ring_order,
                       escalation_level, notified_at) VALUES (?,?,?,'queued','queued',?,0,?)""",
                    (iid, username, role, ring_order, _now()))
            return
        self._x("""INSERT OR IGNORE INTO recipients(incident_id, username, role, state, distance_km, escalation_level,
                   notified_at) VALUES (?,?,?,'notified',?,?,?)""", (iid, username, role, distance_km, level, _now()))
        # בלי מרחק בציר הזמן: בעל האירוע רואה אותו, ומרחק מדויק מאפשר לאתר מתנדבים (סקירת אבטחה)
        self._event(iid, "notified", f"התראה נשלחה ל{'מתנדב/ת' if role == 'volunteer' else 'איש קשר מהימן'} "
                                     f"{self._display(username)}")
        self._notify(username, "incident_new", self._view_for(iid, username), iid)

    # ---------- תור עדיפויות למתנדבים (server/dispatch.py) ----------
    def owner_abuse(self, owner, now=None):
        """הערכת ניצול לרעה של חשבון (server/abuse.py). לעולם לא חוסמת חירום - רק יתרונות עדיפות."""
        now = now or _now()
        since = now - abuse.WINDOW_SECONDS
        quick = self._q("""SELECT COUNT(*) AS n FROM incidents i WHERE i.owner=? AND i.status='cancelled'
                           AND i.created_at>? AND i.ended_at - i.created_at < ?
                           AND EXISTS(SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.role='volunteer')""",
                        (owner, since, abuse.QUICK_CANCEL_SECONDS), one=True)["n"]
        reporters = self._q("SELECT COUNT(DISTINCT reporter) AS n FROM incident_reports WHERE owner=? AND created_at>?",
                            (owner, since), one=True)["n"]
        last_hour = self._q("""SELECT COUNT(*) AS n FROM recipients r JOIN incidents i ON i.id=r.incident_id
                               WHERE i.owner=? AND r.role='volunteer' AND r.notified_at>?""",
                            (owner, now - 3600), one=True)["n"]
        created = self._q("SELECT COUNT(*) AS n FROM incidents WHERE owner=? AND created_at>?",
                          (owner, now - 3600), one=True)["n"]
        u = self._q("SELECT created_at FROM users WHERE username=?", (owner,), one=True)
        age_h = (now - u["created_at"]) / 3600 if u else 0
        return abuse.assess(quick, reporters, last_hour, created, account_age_hours=age_h)

    def _effective_level(self, inc, ab=None):
        ab = ab if ab is not None else self.owner_abuse(inc["owner"])
        return abuse.effective_level(inc["risk_level"], ab["limited"])

    def _incident_priority(self, inc, now=None, ab=None):
        has_contacts = bool(self._q("SELECT 1 FROM recipients WHERE incident_id=? AND role='trusted'",
                                    (inc["id"],), one=True))
        ab = ab if ab is not None else self.owner_abuse(inc["owner"], now)
        level = self._effective_level(inc, ab)
        score = None if ab["limited"] else inc["risk_score"]
        p = dispatch.priority(level, score, inc["emergency_type"], inc["severity"],
                              inc["mode"], has_contacts, (now or _now()) - inc["created_at"])
        if inc["risk_confidence"] is not None and not ab["limited"]:
            # ביטחון בזיהוי (ציר נפרד מהחומרה): +-CONFIDENCE_POINTS/2 בלבד - הזדקנות עדיין מבטיחה תור
            p += dispatch.CONFIDENCE_POINTS * (float(inc["risk_confidence"]) - 0.5)
        if inc["risk_level"] is None:
            # אירוע בלי הערכת סיכון מהמכשיר (אנדרואיד/PWA/ליווי שפג): השרת מוסיף בעצמו הקשר של לילה
            # ורחוב חשוך במיקום האירוע. אירוע מהמחשב כבר כולל את זה בניקוד - לא סופרים פעמיים.
            p += dispatch.CONTEXT_POINTS * self._incident_context(inc["id"], now)["score"]
        return p

    def _incident_context(self, iid, now=None):
        loc = self._latest_location(iid)
        empty = {"phase": None, "lighting": "unknown", "score": 0, "reasons": []}
        if not loc:
            return empty
        try:
            import context_risk
        except ImportError:
            return empty
        return context_risk.assess({"lat": loc["lat"], "lon": loc["lon"], "is_precise": bool(loc["is_precise"]),
                                    "timestamp": loc["received_at"]}, now=now or _now())

    def _note_abuse(self, inc, ab):
        """שקיפות: פעם אחת לאירוע נרשם בציר הזמן למה ההזעקה מוגבלת."""
        if (ab["limited"] or ab["capped"]) and not inc["abuse_noted"]:
            self._x("UPDATE incidents SET abuse_noted=1 WHERE id=?", (inc["id"],))
            self._event(inc["id"], "dispatch_limited", "ההזעקה למתנדבים מוגבלת זמנית (אנשי הקשר מקבלים כרגיל): "
                        + "; ".join(ab["reasons"]))

    def report_incident(self, user, iid, kind, note=None):
        """מתנדב שהוזעק מדווח: התראת שווא / ניצול. מתנדב אחד = דיווח אחד לאירוע."""
        if kind not in abuse.REPORT_KINDS:
            raise ApiError(400, "invalid_kind", "סוג דיווח לא חוקי")
        inc = self._incident_row(iid)
        rec = self._q("SELECT role FROM recipients WHERE incident_id=? AND username=?", (iid, user), one=True) if inc else None
        if not rec or rec["role"] != "volunteer":
            raise ApiError(404, "not_found", "האירוע לא נמצא")   # רק מתנדב שהוזעק לאירוע הזה
        self._x("""INSERT INTO incident_reports(incident_id, reporter, owner, kind, note, created_at) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(incident_id, reporter) DO UPDATE SET kind=excluded.kind, note=excluded.note""",
                (iid, user, inc["owner"], kind, (str(note or "").strip()[:300] or None), _now()))
        return {"ok": True}

    def _busy_volunteers(self, exclude_iid):
        """מתנדב -> (אירוע, מצב) לכל מי שכבר עסוק באירוע פעיל אחר (קיבל, או שממתינים לתשובתו)."""
        rows = self._q("""SELECT r.username, r.incident_id, r.state FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.status='active' AND r.role='volunteer' AND r.incident_id!=?
                          AND r.state IN ('notified','delivered','accepted')""", (exclude_iid,))
        busy = {}
        for r in rows:
            # accepted גובר על הצעה ממתינה
            if r["username"] not in busy or r["state"] == "accepted":
                busy[r["username"]] = (r["incident_id"], r["state"])
        return busy

    _WAITING_SQL = """SELECT i.* FROM incidents i WHERE i.status='active'
        AND i.activation NOT IN ('unconfirmed','support') {extra}
        AND EXISTS(SELECT 1 FROM incident_locations l WHERE l.incident_id=i.id)
        AND NOT EXISTS(SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.role='volunteer'
                       AND r.state='accepted')
        AND (i.mode='loud' OR (
            -- מצוקה שקטה: מתנדבים רק כשאף איש קשר לא קיבל *ו*שרשרת הצלצול הסתיימה (זהה ל-_needs_volunteers)
            NOT EXISTS(SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.role='trusted'
                       AND r.state='accepted')
            AND NOT EXISTS(SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.role='trusted'
                       AND r.state!='revoked' AND (r.state='queued' OR r.ring_status IN ('queued','ringing')))))
        ORDER BY i.created_at"""

    def _slots_for(self, inc, ctx):
        """כמה מתנדבים להזעיק עכשיו לאירוע (0 = לא כרגע). כל הנתונים מהמטמון של הסבב."""
        vols = self._q("SELECT state FROM recipients WHERE incident_id=? AND role='volunteer'", (inc["id"],))
        pending = sum(1 for v in vols if v["state"] in ("notified", "delivered"))
        ab = ctx.ab(inc["owner"])
        self._note_abuse(inc, ab)
        return abuse.allowed_slots(dispatch.fanout(self._effective_level(inc, ab)) - pending, len(vols), ab)

    def _owner_eligible_ids(self, owner):
        """לכל משתמש - רק MAX_ACTIVE_WITH_VOLUNTEERS האירועים הפעילים הוותיקים מקבלים מתנדבים."""
        return {r["id"] for r in self._q("SELECT id FROM incidents WHERE owner=? AND status='active' "
                                         "ORDER BY created_at LIMIT ?", (owner, abuse.MAX_ACTIVE_WITH_VOLUNTEERS))}

    def run_dispatch_queue(self, now=None):
        """
        עובר על כל האירועים שממתינים למתנדבים, מהדחוף ביותר, וממלא את מכסת הפנייה של כל אחד.
        בדיקת עומס (סערת 200 SOS): הגרסה הקודמת הריצה 55 אלף שאילתות לסבב (עדיפויות/ניצול/עסוקים
        חושבו מחדש לכל מועמד) והסבב לקח שניות. עכשיו כל נתון מחושב פעם אחת לסבב (_DispatchCtx).
        """
        now = now or _now()
        self._apply_pending_wake(now)
        with self._dispatch_lock:
            ctx = _DispatchCtx(self, now)
            waiting, per_owner = [], {}
            for inc in self._q(self._WAITING_SQL.format(extra="")):
                per_owner[inc["owner"]] = per_owner.get(inc["owner"], 0) + 1
                if per_owner[inc["owner"]] > abuse.MAX_ACTIVE_WITH_VOLUNTEERS:
                    continue
                if self._retry_at.get(inc["id"], 0) > now:
                    continue                  # חיכה בתור בלי מועמד - לא סורקים מחדש בכל שנייה
                slots = self._slots_for(inc, ctx)
                if slots > 0:
                    waiting.append((ctx.priority(inc), inc["id"], slots))
            for iid, slots in dispatch.plan(waiting):
                self._fill(iid, slots, now, ctx)

    def _dispatch_for(self, iid, now=None):
        """שיבוץ לאירוע אחד בלבד (בפתיחת SOS / מיקום ראשון / סירוב) - בלי להריץ את כל התור בתוך הבקשה.
        הסדר בין אירועים לפי דחיפות נשמר בסבב הרקע (כל שנייה)."""
        now = now or _now()
        with self._dispatch_lock:
            rows = self._q(self._WAITING_SQL.format(extra="AND i.id=?"), (iid,))
            if not rows or iid not in self._owner_eligible_ids(rows[0]["owner"]):
                return
            ctx = _DispatchCtx(self, now)
            slots = self._slots_for(rows[0], ctx)
            if slots > 0:
                self._fill(iid, slots, now, ctx)

    def _fill(self, iid, slots, now, ctx):
        for _ in range(slots):
            if not self._dispatch_next_volunteer(iid, now, ctx):
                self._retry_at[iid] = now + DISPATCH_RETRY_SECONDS
                return
        self._retry_at.pop(iid, None)

    WAKE_MIN_INTERVAL = 1.0

    def _wake_dispatch(self, throttled=False):
        """משהו שינה את הזמינות (מתנדב התפנה/סירב/הצטרף) - בודקים מחדש את האירועים הממתינים.
        throttled (עדכוני מיקום של מתנדבים - תכופים): לכל היותר פעם בשנייה; הם ביטלו את ההשהיה בין
        סבבים והאטו את התור פי 100 (בדיקת עומס). שחרור מתנדב (סגירה/סירוב/פקיעה) - מיד."""
        now = _now()
        if not throttled or now - self._last_wake >= self.WAKE_MIN_INTERVAL:
            self._last_wake = now
            self._retry_at.clear()
            self._wake_pending = False
        else:
            self._wake_pending = True

    def _apply_pending_wake(self, now):
        if self._wake_pending and now - self._last_wake >= self.WAKE_MIN_INTERVAL:
            self._last_wake = now
            self._wake_pending = False
            self._retry_at.clear()

    def _dispatch_next_volunteer(self, iid, now=None, ctx=None):
        inc = self._incident_row(iid)
        loc = self._latest_location(iid)
        if not loc:
            return None
        now = now or _now()
        ctx = ctx or _DispatchCtx(self, now)
        ab = ctx.ab(inc["owner"])
        my_prio = ctx.priority(inc)
        tried = {r["username"] for r in self._q("SELECT username FROM recipients WHERE incident_id=?", (iid,))}
        busy = ctx.busy()
        dlat = min(self.default_radius_km * 2, 50.0) / 111.0
        candidates = []
        for v in ctx.volunteers():
            if v["username"] == inc["owner"] or v["username"] in tried:
                continue
            if abs(v["lat"] - loc["lat"]) > dlat:   # סינון מהיר לפני חישוב מרחק
                continue
            d = haversine_km(loc["lat"], loc["lon"], v["lat"], v["lon"])
            if d > min(v["radius_km"], self.default_radius_km * 2):
                continue
            if not ctx.eligible(v):             # מושעה / לא מאומת שחרג מהמכסה החודשית
                continue
            preempt = None
            if v["username"] in busy and busy[v["username"]][0] != iid:
                other_iid, other_state = busy[v["username"]]
                if other_state == "accepted":
                    continue                     # מי שכבר מטפל באירוע לא נלקח ממנו לעולם
                if ab["limited"]:
                    continue                     # חשבון מוגבל לא "לוקח" מתנדב מאירוע של מישהו אחר
                if my_prio < ctx.priority_of(other_iid) + dispatch.PREEMPT_MARGIN:
                    continue                     # ממתין לתשובה באירוע דחוף לא פחות
                preempt = other_iid
            tier = volunteer_match.match_tier(inc["emergency_type"], ctx.quals(v["username"]),
                                              json.loads(v["skills"]))
            # מתנדב פנוי לגמרי עדיף על פני "גניבה" מאירוע אחר. הכשרה/אימות/אמינות - רק בתוך אותה
            # רצועת זמן הגעה (למטה): בחירום מהירות ההגעה קודמת (server/volunteer_match.py)
            candidates.append((1 if preempt else 0, d, tier, 0 if v["verification"] == "verified" else 1,
                               ctx.reliability(v["username"]), v["username"], preempt))
        if not candidates:
            if not inc["dispatch_waiting"]:      # מדווחים פעם אחת, לא בכל סבב של התור
                self._x("UPDATE incidents SET dispatch_waiting=1 WHERE id=?", (iid,))
                self._event(iid, "no_volunteer", "אין מתנדב זמין ומתאים נוסף בטווח - האירוע ממתין בתור")
                self._notify(inc["owner"], "incident_update", {"incident_id": iid, "event": "no_volunteer"}, iid)
            return None
        candidates.sort(key=lambda c: (c[0], c[1]))
        # זמן הגעה בהליכה למועמדים הקרובים (פנויים קודם) - לפי ניתוב אמיתי (OSRM עצמי) או הערכה.
        # 500 מ' מעבר לכביש מהיר אינם 500 מ' הליכה.
        top = [c for c in candidates if c[0] == candidates[0][0]][:routing_mod.MAX_DESTS]
        vpos = {v["username"]: (v["lat"], v["lon"]) for v in ctx.volunteers()}
        etas = self.router.walking_from((loc["lat"], loc["lon"]), [vpos[c[5]] for c in top])
        # רצועת זמן הליכה (10 דק') קודמת לכל; בתוכה: הכשרה מאומתת מתאימה > מוצהרת > בלי, מאומת/ת,
        # אמינות (עונה לקריאות / דירוג), ואז זמן ההגעה עצמו.
        best = min(range(len(top)), key=lambda i: (int(etas[i]["seconds"] // ETA_BAND_SECONDS), top[i][2],
                                                   top[i][3], top[i][4], etas[i]["seconds"], top[i][5]))
        d, username, preempt = top[best][1], top[best][5], top[best][6]
        eta = etas[best]
        if preempt:
            cur = self._x("""UPDATE recipients SET state='reassigned' WHERE incident_id=? AND username=?
                             AND state IN ('notified','delivered')""", (preempt, username))
            if not cur.rowcount:
                return None                      # בינתיים קיבל את האירוע הקודם - לא לוקחים ממנו
            self._notify(username, "incident_closed", {"incident_id": preempt, "reason": "reassigned"}, preempt)
            self._event(preempt, "reassigned", f"{self._display(username)} הופנה/תה לאירוע דחוף יותר - מחפשים מתנדב אחר")
            self._retry_at.pop(preempt, None)
        level = len([1 for r in self._q("SELECT role FROM recipients WHERE incident_id=?", (iid,)) if r["role"] == "volunteer"])
        self._x("UPDATE incidents SET dispatch_waiting=0 WHERE id=?", (iid,))
        self._add_recipient(iid, username, "volunteer", round(d, 2), level)
        self._x("UPDATE recipients SET eta_seconds=?, eta_source=? WHERE incident_id=? AND username=?",
                (round(eta["seconds"], 1), eta["source"], iid, username))
        ctx.assigned(username, iid)
        self._notify(inc["owner"], "incident_update", {"incident_id": iid, "event": "volunteer_notified"}, iid)
        return username

    def run_escalation_tick(self, now=None, dispatch_queue=True):
        """מתנדב שלא הגיב בזמן -> expired, ומוזעק הבא. נקרא כל שנייה ע"י thread ברקע."""
        now = now or _now()
        self._expire_invitations(now)
        # כל אירוע שיש בו מתנדבים מוסלם (גם מצוקה שקטה שנשלחה למתנדבים כי אין אנשי קשר מחוברים)
        rows = self._q("""SELECT r.incident_id, r.username FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE i.status='active' AND r.role='volunteer'
                          AND r.state IN ('notified','delivered') AND r.notified_at < ?""", (now - self.ack_timeout,))
        for r in rows:
            # מותנה: אם המתנדב קיבל ממש עכשיו - לא דורסים את ה-accepted (מרוץ שנמצא בסקירת אבטחה)
            cur = self._x("""UPDATE recipients SET state='expired' WHERE incident_id=? AND username=?
                             AND state IN ('notified','delivered')""", (r["incident_id"], r["username"]))
            if not cur.rowcount:
                continue
            self._notify(r["username"], "incident_closed", {"incident_id": r["incident_id"], "reason": "expired"},
                         r["incident_id"])
            self._event(r["incident_id"], "escalation", f"{self._display(r['username'])} לא הגיב/ה בזמן - הסלמה")
        # התור עובר בכל שנייה: ממלא מכסות אחרי הסלמה, ומשבץ מתנדבים שהתפנו לאירועים שממתינים
        self._ring_tick(now)             # איש קשר שלא ענה בזמן -> הבא בתור
        self.helpers_tick(now)           # מתנדבי תמיכה/הזעקה שלא ענו -> הבאים; הזעקה אוטומטית אחרי 90 שנ'
        self.signal_tick(now)            # טלפון שהפסיק לדווח (אין קליטה) -> עדכון למי שעוקב
        if rows:
            self._wake_dispatch()
        if dispatch_queue:               # בשרת עצמו התור רץ ב-thread נפרד (LG-Dispatch) - טיימרים לא מחכים לו
            self.run_dispatch_queue(now)
        self.features_tick(now)  # ליווי הביתה שפג + גיבוי SMS

    def _access(self, user, iid):
        inc = self._incident_row(iid)
        if not inc:
            raise ApiError(404, "not_found", "האירוע לא נמצא")
        if inc["owner"] == user:
            return inc, "owner", None
        rec = self._q("SELECT * FROM recipients WHERE incident_id=? AND username=?", (iid, user), one=True)
        if not rec and inc["status"] == "active" and self._helper_row(iid, user):
            return inc, "helper", None          # מתנדב/ת תמיכה / הזעקה (server/helpers.py)
        if not rec or rec["state"] in ("revoked", "expired", "declined", "reassigned", "queued"):
            raise ApiError(404, "not_found", "האירוע לא נמצא")  # לא חושפים קיום אירוע
        if rec["role"] == "volunteer" and inc["status"] != "active":
            raise ApiError(404, "not_found", "האירוע לא נמצא")
        return inc, rec["role"], rec

    def _view_for(self, iid, user, include_medical=False):
        inc = self._incident_row(iid)
        role = "owner" if inc["owner"] == user else None
        rec = None
        if role is None:
            rec = self._q("SELECT * FROM recipients WHERE incident_id=? AND username=?", (iid, user), one=True)
            role = rec["role"] if rec else None
            if role is None and self._helper_row(iid, user):
                return self._helper_view(iid, user)
        loc = self._latest_location(iid)
        full = role in ("owner", "trusted") or (rec and rec["state"] == "accepted")
        view = {
            "id": iid, "mode": inc["mode"], "emergency_type": inc["emergency_type"], "severity": inc["severity"],
            "status": inc["status"], "created_at": inc["created_at"], "updated_at": inc["updated_at"],
            "owner": inc["owner"], "owner_display": self._display(inc["owner"]), "my_role": role,
            "my_state": rec["state"] if rec else None, "full_access": bool(full),
            # האם מצלצלים אליי עכשיו (שרשרת הצלצול) - לאפליקציה שעלתה מחדש באמצע צלצול
            "my_ring_status": rec["ring_status"] if rec else None,
            # הטלפון הפסיק לדווח (אין קליטה?) - המיקום המוצג הוא האחרון הידוע, לא עדכני
            "signal_lost_since": inc["signal_lost_at"],
        }
        if role == "volunteer" and not full:
            # מתנדב שעוד לא קיבל: לא רואה מי במצוקה (שם/משתמש) - רק שמישהו בקרבת מקום צריך עזרה
            view["owner"], view["owner_display"] = None, "מישהו בקרבת מקום"
        # מד הסכנה: רק למגיבים (אנשי קשר ומתנדבים) - לא לאדם שבמצוקה, כדי לא להוסיף לחץ ברגע הקשה.
        # מתנדב לפני קבלה רואה רמה ועדיפות (כדי להחליט מהר); הנימוקים - רק בגישה מלאה.
        if role in ("trusted", "volunteer"):
            prio = self._incident_priority(inc)
            view["risk"] = {"level": inc["risk_level"], "score": inc["risk_score"],
                            "confidence": inc["risk_confidence"],
                            "scale": dispatch.SCORE_SCALE, "priority": prio,
                            "band": dispatch.band(prio)[0], "band_label": dispatch.band(prio)[1]}
            if full:
                view["risk"]["reasons"] = json.loads(inc["risk_reasons"] or "[]")
            ctx = self._incident_context(iid)
            view["risk"]["context"] = {"phase": ctx["phase"], "lighting": ctx["lighting"]}   # "לילה · רחוב חשוך"
        view["activation"] = inc["activation"]          # unconfirmed = זיהוי אוטומטי שעוד לא אושר
        if role == "owner":
            view["helpers"] = self._helpers_for_owner(iid)
            view["helper_requests"] = [r["kind"] for r in self._q(
                "SELECT kind FROM helper_requests WHERE incident_id=? ORDER BY requested_at", (iid,))]
            if inc["activation"] == "support":
                view["hotlines"], view["hotlines_note"] = helpers.HOTLINES, helpers.HOTLINES_NOTE
        if loc and role == "trusted" and self.location_mode_for(inc["owner"], user) == "off":
            loc = None                                  # הבעלים כיבה/תה מיקום לאיש הקשר הזה (מקבל/ת התראה בלבד)
            view["location_withheld"] = True
        if loc:
            if full:
                view["location"] = loc
            else:
                view["location"] = {"lat": _approx(loc["lat"]), "lon": _approx(loc["lon"]), "approximate": True,
                                    "received_at": loc["received_at"], "is_precise": False}
            view["location"]["stale"] = _now() - (loc["received_at"] or 0) > privacy.STALE_SECONDS
            if role != "owner":
                via = ("emergency" if role == "trusted" else
                       "volunteer_assigned" if full else "volunteer_nearby_approx")
                self._note_location_access(user, inc["owner"], via, iid,
                                           self.location_mode_for(inc["owner"], user) if role == "trusted"
                                           else "assigned_incident" if full else "nearby_alert")
            if rec and rec["distance_km"] is not None:
                view["distance_km"] = rec["distance_km"]
            if rec and rec["eta_seconds"] is not None:
                view["eta_minutes"] = max(1, round(rec["eta_seconds"] / 60))
                view["eta_source"] = rec["eta_source"]          # 'osrm' = מסלול אמיתי, 'estimate' = משוער
        if full:
            view["profile"] = json.loads(inc["profile"])
        # מידע רפואי: רק בבקשה ישירה (לא בתוכן התראה, שנשמר בטבלת notifications), רק בגישה מלאה,
        # רק באירוע פעיל, ורק אם הבעלים בחר/ה לשתף
        if include_medical and full and inc["status"] == "active":
            med = self._medical_for_incident(inc["owner"])
            if med:
                view["medical"] = med
        responders = self._q("SELECT username, role, state, response, responded_at, distance_km, escalation_level, "
                             "sms_status, eta_seconds, eta_source, ring_status FROM recipients WHERE incident_id=? "
                             "ORDER BY notified_at, rowid", (iid,))
        vstatus = {r["username"]: r for r in self._q("SELECT username, verification FROM volunteers")}
        for r in responders:
            r["display_name"] = self._display(r["username"])
            r["verified"] = r["role"] == "volunteer" and (vstatus.get(r["username"]) or {}).get("verification") == "verified"
        if role == "owner":
            for r in responders:
                if r["role"] == "volunteer":
                    # נגד איתור מתנדבים (אירועים מזויפים + מרחק מדויק = טרילטרציה): מרחק/זמן הגעה רק
                    # אחרי שהמתנדב קיבל, ורק כטווח מעוגל
                    accepted = r["state"] == "accepted"
                    r["distance_band_km"] = _band(r["distance_km"]) if accepted and r["distance_km"] is not None else None
                    r["eta_minutes"] = (max(1, round(r["eta_seconds"] / 60 / 5) * 5)
                                        if accepted and r["eta_seconds"] is not None else None)
                    r["distance_km"] = r["eta_seconds"] = None
            view["recipients"] = responders
            view["timeline"] = self._q("SELECT ts, actor, kind, text FROM incident_events WHERE incident_id=? ORDER BY id", (iid,))
        else:
            view["responders"] = [{"display_name": r["display_name"], "role": r["role"], "state": r["state"],
                                   "verified": r["verified"]}
                                  for r in responders if r["state"] in ("accepted",)]
        if full:
            view["messages"] = self._messages_view(iid, inc)
            view["quick_replies"] = self.QUICK_OWNER if role == "owner" else self.QUICK_RESPONDER
        return view

    def get_incident(self, user, iid):
        self._access(user, iid)
        return self._view_for(iid, user, include_medical=True)

    def _broadcast_update(self, iid, event, extra=None, exclude=None):
        inc = self._incident_row(iid)
        targets = [r["username"] for r in self._q(
            "SELECT username FROM recipients WHERE incident_id=? AND state NOT IN ('revoked','expired','declined',"
            "'reassigned','queued')", (iid,))]
        for u in targets + [inc["owner"]]:
            if u == exclude:
                continue
            payload = {"incident_id": iid, "event": event}
            payload.update(extra or {})
            self._notify(u, "incident_update", payload, iid)
        # מתנדבי תמיכה/הזעקה שקיבלו: רק "משהו השתנה" (בלי פרטים - מתנדב/ת שיחה לא אמור/ה לקבל שם/טקסט)
        for r in self._q("SELECT DISTINCT username FROM helpers WHERE incident_id=? AND state='accepted'", (iid,)):
            if r["username"] != exclude and r["username"] not in targets:
                self._notify(r["username"], "incident_update", {"incident_id": iid, "event": event}, iid)

    def update_location(self, user, iid, loc):
        inc, role, _ = self._access(user, iid)
        if role != "owner":
            raise ApiError(403, "forbidden", "רק בעל האירוע מעדכן מיקום")
        if inc["status"] != "active":
            raise ApiError(409, "closed", "האירוע הסתיים")
        had_location = self._latest_location(iid) is not None
        self._add_location(iid, loc)
        self.signal_back("incident", iid)
        self._x("UPDATE incidents SET updated_at=? WHERE id=?", (_now(), iid))
        self._broadcast_update(iid, "location", exclude=user)
        if not had_location and self._needs_volunteers(iid):
            self._dispatch_for(iid)
        return {"ok": True}

    def update_incident(self, user, iid, fields):
        inc, role, _ = self._access(user, iid)
        if role != "owner":
            raise ApiError(403, "forbidden", "רק בעל האירוע יכול לעדכן אותו")
        sets, args = [], []
        if fields.get("emergency_type") in EMERGENCY_TYPES:
            sets.append("emergency_type=?"); args.append(fields["emergency_type"])
        if fields.get("severity") in SEVERITIES:
            sets.append("severity=?"); args.append(fields["severity"])
        became_loud = fields.get("mode") == "loud" and inc["mode"] == "silent"
        if became_loud:
            sets.append("mode='loud'")
        risk = dispatch.clean_risk(fields.get("risk")) if "risk" in fields else None
        if risk:  # הערכת סכנה מעודכנת מהמכשיר (למשל אחרי שהתבררו ראיות נוספות)
            sets += ["risk_level=?", "risk_score=?", "risk_reasons=?", "risk_confidence=?", "risk_signals=?"]
            args += [risk[0], risk[1], json.dumps(risk[2], ensure_ascii=False),
                     dispatch.clean_confidence(fields.get("risk")),
                     json.dumps(dispatch.clean_signals(fields.get("risk")), ensure_ascii=False)]
        new_status = fields.get("status")
        if new_status in ("resolved", "cancelled") and inc["status"] == "active":
            self.audit("emergency_cancelled" if new_status == "cancelled" else "emergency_resolved", actor=user,
                       subject=inc["owner"], incident_id=iid)
            sets.append("status=?"); args.append(new_status)
            sets.append("ended_at=?"); args.append(_now())
        if not sets:
            return self._view_for(iid, user)
        sets.append("updated_at=?"); args.append(_now())
        self._x(f"UPDATE incidents SET {', '.join(sets)} WHERE id=?", (*args, iid))
        if became_loud:
            self._event(iid, "escalated", "האירוע הוסלם ממצוקה שקטה לאזעקה גלויה", user)
        if risk:
            self._event(iid, "risk", "הערכת הסכנה עודכנה ונשלחה למגיבים", user)   # בלי הרמה - בעל האירוע רואה את ציר הזמן
        if (became_loud or risk) and inc["status"] == "active" and new_status not in ("resolved", "cancelled"):
            self._dispatch_for(iid)
        if new_status in ("resolved", "cancelled") and inc["status"] == "active":
            self._wake_dispatch()
            label = "הסתיים" if new_status == "resolved" else "בוטל ע\"י המשתמש"
            self._event(iid, "closed", f"האירוע {label}", user)
            # רק מי שידע ועדיין מורשה. מי שבתור/נותק/פג - לא לומד שהיה אירוע ומתי נסגר (סקירה 2)
            for r in self._q("""SELECT username, role, state FROM recipients WHERE incident_id=?
                                AND state NOT IN ('queued','revoked','reassigned')""", (iid,)):
                self._stop_ring(iid, r["username"], "rang")
                if r["role"] == "volunteer" and r["state"] != "accepted":
                    # מתנדב שלא קיבל לא ידע מי במצוקה - גם לא עכשיו (ההודעה מוצגת במסך הנעילה)
                    text = "קריאת החירום שקיבלת הסתיימה. תודה על הזמינות."
                else:
                    text = f"האירוע של {self._display(user)} {label}. מומלץ לוודא שהוא/היא בטוח/ה."
                self._notify(r["username"], "incident_closed", {"incident_id": iid, "reason": new_status,
                             "text": text}, iid)
                if r["role"] == "volunteer":
                    self._x("UPDATE recipients SET state='revoked' WHERE incident_id=? AND username=? AND state!='expired'",
                            (iid, r["username"]))
        else:
            self._broadcast_update(iid, "details", exclude=user)
        return self._view_for(iid, user)

    def respond(self, user, iid, action):
        if action not in RESPONSES:
            raise ApiError(400, "invalid_action", "פעולה לא חוקית")
        inc, role, rec = self._access(user, iid)
        if role == "owner":
            raise ApiError(400, "owner", "בעל האירוע לא מגיב לעצמו")
        if inc["status"] != "active":
            raise ApiError(409, "closed", "האירוע הסתיים")
        state = {"accept": "accepted", "on_my_way": "accepted", "arrived": "accepted", "handled": "accepted",
                 "decline": "declined"}[action]
        cur = self._x("""UPDATE recipients SET state=?, response=?, responded_at=? WHERE incident_id=? AND username=?
                         AND state IN ('notified','delivered','accepted')""", (state, action, _now(), iid, user))
        if not cur.rowcount:   # בינתיים פג/הועבר/בוטל - לא "מחיים" גישה שנסגרה
            raise ApiError(404, "not_found", "האירוע לא נמצא")
        labels = {"accept": "קיבל/ה את האירוע", "on_my_way": "בדרך", "arrived": "הגיע/ה למקום",
                  "handled": "מטפל/ת", "decline": "לא זמין/ה"}
        self._event(iid, "response", f"{self._display(user)} {labels[action]}", user)
        self._broadcast_update(iid, "response", {"by": self._display(user), "action": action}, exclude=user)
        if role == "trusted":
            if state == "accepted":
                self._stop_ring(iid, user, "answered")
                self._x("UPDATE recipients SET ring_status='answered' WHERE incident_id=? AND username=?", (iid, user))
                self._inform_queued(iid)               # שאר המשפחה יודעת (בלי צפצוף)
            elif self._stop_ring(iid, user, "rang"):
                self._ring_next(iid, reason="declined")   # סירב - לא מחכים לזמן, עוברים מיד לבא
            elif self._needs_volunteers(iid):
                self._wake_dispatch()
                self._dispatch_for(iid)
        if action == "decline" and role == "volunteer":
            self._wake_dispatch()
            self._dispatch_for(iid)
        return (self._view_for(iid, user, include_medical=True) if state != "declined"
                else {"ok": True, "state": "declined"})

    # הודעות מהירות: מי שבמצוקה לא תמיד יכול/ה להקליד. הקוד נשמר, הטקסט נקבע בשרת (אחיד בכל הלקוחות).
    QUICK_OWNER = {"ok": "אני בסדר עכשיו", "cant_talk": "אני לא יכול/ה לדבר - רק הודעות",
                   "call_police": "תתקשרו למשטרה (100)", "hiding": "אני מסתתר/ת", "hurry": "בואו מהר",
                   "injured": "אני פצוע/ה"}
    QUICK_RESPONDER = {"on_my_way": "אני בדרך אליך", "near_2min": "אני במרחק כ-2 דקות",
                       "called_police": "התקשרתי למשטרה", "stay_hidden": "תישאר/י במקום מוסתר",
                       "cant_come": "אני לא יכול/ה להגיע, מעדכן/ת אחרים"}
    MESSAGES_PER_MINUTE = 20

    def add_message(self, user, iid, text=None, quick=None):
        inc, role, rec = self._access(user, iid)
        if role == "helper" and self._helper_row(iid, user)["state"] != "accepted":
            raise ApiError(403, "accept_first", "יש לקבל את הבקשה לפני שליחת הודעה")
        if role == "volunteer" and rec["state"] != "accepted":
            raise ApiError(403, "accept_first", "יש לקבל את האירוע לפני שליחת הודעה")
        if inc["status"] != "active":
            raise ApiError(409, "closed", "האירוע הסתיים")
        if quick:
            table = self.QUICK_OWNER if role == "owner" else self.QUICK_RESPONDER
            if quick not in table:
                raise ApiError(400, "invalid_quick", "הודעה מהירה לא מוכרת")
            text = table[quick]
        text = (text or "").strip()[:500]
        if not text:
            raise ApiError(400, "empty", "הודעה ריקה")
        recent = self._q("SELECT COUNT(*) AS n FROM messages WHERE incident_id=? AND sender=? AND created_at>?",
                         (iid, user, _now() - 60), one=True)["n"]
        if recent >= self.MESSAGES_PER_MINUTE:
            raise ApiError(429, "slow_down", "יותר מדי הודעות בדקה")
        cur = self._x("INSERT INTO messages(incident_id, sender, text, created_at, quick) VALUES (?,?,?,?,?)",
                      (iid, user, text, _now(), quick or None))
        mid = cur.lastrowid
        # השולח קרא את ההודעה של עצמו
        self._mark_read(iid, user, mid)
        silent_owner = inc["mode"] == "silent"
        for u in self._update_targets(iid, exclude=user):
            payload = {"incident_id": iid, "event": "message", "from": self._display(user), "message_id": mid}
            if u == inc["owner"] and silent_owner:
                payload["quiet"] = True   # במצוקה שקטה: שום צליל/רטט אצל בעל האירוע
            self._notify(u, "incident_update", payload, iid)
        return {"ok": True, "id": mid}

    def _update_targets(self, iid, exclude=None):
        inc = self._incident_row(iid)
        users = [r["username"] for r in self._q(
            "SELECT username FROM recipients WHERE incident_id=? AND state NOT IN ('revoked','expired','declined',"
            "'reassigned','queued')", (iid,))] + [inc["owner"]] + [r["username"] for r in self._q(
            "SELECT username FROM helpers WHERE incident_id=? AND state='accepted'", (iid,))]
        return [u for u in dict.fromkeys(users) if u != exclude]

    def _mark_read(self, iid, user, up_to_id):
        self._x("""INSERT INTO message_reads(incident_id, username, up_to_id, read_at) VALUES (?,?,?,?)
                   ON CONFLICT(incident_id, username) DO UPDATE SET up_to_id=MAX(up_to_id, excluded.up_to_id),
                   read_at=excluded.read_at""", (iid, user, int(up_to_id), _now()))

    def mark_messages_read(self, user, iid, up_to_id):
        inc, role, rec = self._access(user, iid)
        if role == "helper" and self._helper_row(iid, user)["state"] != "accepted":
            raise ApiError(403, "accept_first", "יש לקבל את הבקשה")
        if role == "volunteer" and rec["state"] != "accepted":
            raise ApiError(403, "accept_first", "יש לקבל את האירוע")
        try:
            up_to_id = int(up_to_id)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid", "up_to_id לא תקין")
        last = self._q("SELECT MAX(id) AS m FROM messages WHERE incident_id=?", (iid,), one=True)["m"] or 0
        self._mark_read(iid, user, min(up_to_id, last))
        for u in self._update_targets(iid, exclude=user):
            self._notify(u, "incident_update", {"incident_id": iid, "event": "read", "quiet": True}, iid)
        return {"ok": True}

    def _messages_view(self, iid, inc):
        roles = {r["username"]: r["role"] for r in self._q("SELECT username, role FROM recipients WHERE incident_id=?",
                                                           (iid,))}
        roles[inc["owner"]] = "owner"
        reads = self._q("SELECT username, up_to_id FROM message_reads WHERE incident_id=?", (iid,))
        names = {}
        out = []
        for m in self._q("SELECT id, sender, text, quick, created_at FROM messages WHERE incident_id=? ORDER BY id",
                         (iid,)):
            if m["sender"] not in names:
                names[m["sender"]] = self._display(m["sender"])
            read_by = []
            for r in reads:
                if r["username"] != m["sender"] and r["up_to_id"] >= m["id"]:
                    if r["username"] not in names:
                        names[r["username"]] = self._display(r["username"])
                    read_by.append(names[r["username"]])
            out.append({"id": m["id"], "sender": m["sender"], "sender_display": names[m["sender"]],
                        "role": roles.get(m["sender"], "helper"), "text": m["text"], "quick": m["quick"],
                        "created_at": m["created_at"], "read_by": read_by})
        return out

    def _messages_for_helper(self, iid, inc, kind):
        """מתנדב/ת תמיכה מרחוק לא רואה מי הפונה: שם ומזהה מוחלפים ב"הפונה" (גם ב"נקרא ע"י")."""
        msgs = self._messages_view(iid, inc)
        if kind != "remote_support":
            return msgs
        owner_name = self._display(inc["owner"])
        for m in msgs:
            if m["sender"] == inc["owner"]:
                m["sender"], m["sender_display"] = None, "הפונה"
            m["read_by"] = ["הפונה" if n == owner_name else n for n in m["read_by"]]
        return msgs

    def inbox(self, user):
        rows = self._q("""SELECT r.incident_id FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE r.username=? AND i.status='active' AND r.state IN ('notified','delivered','accepted')
                          ORDER BY i.created_at DESC""", (user,))
        out = [self._view_for(r["incident_id"], user) for r in rows]
        seen = {r["incident_id"] for r in rows}
        for h in self._q("""SELECT DISTINCT h.incident_id FROM helpers h JOIN incidents i ON i.id=h.incident_id
                            WHERE h.username=? AND i.status='active' AND h.state IN ('notified','accepted')""", (user,)):
            if h["incident_id"] not in seen:
                out.append(self._helper_view(h["incident_id"], user))
        return out

    def notification_cursor(self, user):
        """למכשיר חדש/התקנה מחדש: מאיפה להתחיל בזרם, ואילו התראות עוד לא התקבלו באף מכשיר.
        בלי זה מכשיר חדש היה "משחזר" את כל ההיסטוריה (כולל חלונות התראה לאירועים שהסתיימו)."""
        last = self._q("SELECT MAX(id) AS m FROM notifications WHERE username=?", (user,), one=True)["m"] or 0
        rows = self._q("""SELECT n.* FROM notifications n LEFT JOIN incidents i ON i.id=n.incident_id
                          WHERE n.username=? AND n.received_at IS NULL AND n.created_at>?
                          AND (n.incident_id IS NULL OR i.status='active' OR n.type='incident_closed')
                          ORDER BY n.id LIMIT 200""", (user, _now() - 7 * 86400))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
        return {"last_id": last, "unreceived": rows}

    def mark_received(self, user, nid):
        row = self._q("SELECT * FROM notifications WHERE id=? AND username=?", (int(nid), user), one=True)
        if not row:
            raise ApiError(404, "not_found", "התראה לא נמצאה")
        if row["received_at"] is None:
            self._x("UPDATE notifications SET received_at=? WHERE id=?", (_now(), nid))
            if row["type"] == "incident_new" and row["incident_id"]:
                cur = self._x("""UPDATE recipients SET state='delivered', delivered_at=? WHERE incident_id=? AND username=?
                                 AND state='notified'""", (_now(), row["incident_id"], user))
                if cur.rowcount:
                    self._event(row["incident_id"], "delivered", f"האפליקציה של {self._display(user)} אישרה קבלה")
                    inc = self._incident_row(row["incident_id"])
                    self._notify(inc["owner"], "incident_update",
                                 {"incident_id": row["incident_id"], "event": "delivered"}, row["incident_id"])
        return {"ok": True}

    # ---------- Street Intelligence (תשתית בלבד) ----------
    def street_intel_query(self, bbox):
        """אין מנוע ניקוד: מחזיר מפורשות שאין נתונים. לעולם לא מחזיר ציון שלא חושב מנתונים אמיתיים."""
        count = self._q("SELECT COUNT(*) AS n FROM street_scores", one=True)["n"]
        return {"status": "no_data" if count == 0 else "partial", "segments": [],
                "message": "עדיין אין נתוני בטיחות לרחובות. לא מוצגים ציונים משוערים.",
                "bbox": bbox, "model_version": None}

    STREET_ATTRS = ("lighting", "sidewalk", "activity", "maintenance", "visibility")
    MAX_USER_OBSERVATIONS = 50000        # מכסה למשתמש (מניעת ניפוח מסד הנתונים)
    MAX_WALK_OBSERVATIONS = 200          # לבקשה (גוף עד 64KB); הליכה ארוכה נשלחת במנות עם אותו walk_id

    def submit_walk(self, user, body):
        """
        תצפיות מסרטון מסלול שעובד *במחשב של המשתמש*: רק מספרים (מיקום, בהירות נמדדת, מאפייני
        מודל ותיוגי משתמש). השרת מסרב לכל דבר שנראה כמו מדיה, ודורש הסכמה מפורשת.
        """
        consent = body.get("consent") or {}
        if consent.get("accepted") is not True or not consent.get("version"):
            raise ApiError(400, "consent_required", "נדרשת הסכמה מפורשת לשימוש מחקרי בנתוני המסלול")
        obs = body.get("observations")
        if not isinstance(obs, list) or not obs or len(obs) > self.MAX_WALK_OBSERVATIONS:
            raise ApiError(400, "invalid", f"צריך בין 1 ל-{self.MAX_WALK_OBSERVATIONS} תצפיות")
        have = self._q("SELECT COUNT(*) AS n FROM street_observations WHERE submitted_by=?", (user,), one=True)["n"]
        if have + len(obs) > self.MAX_USER_OBSERVATIONS:
            raise ApiError(429, "quota", "הגעת למכסת התצפיות. אפשר למחוק הליכות ישנות ולשלוח שוב.")
        walk = body.get("walk_id")
        if walk:   # המשך של הליכה קיימת - רק של אותו משתמש
            if not self._q("""SELECT 1 FROM street_observations WHERE submitted_by=? AND kind='user_video'
                              AND json_extract(consent,'$.walk_id')=? LIMIT 1""", (user, str(walk)), one=True):
                raise ApiError(404, "not_found", "ההליכה לא נמצאה")
        else:
            walk = "walk_" + uuid.uuid4().hex
        model = str(body.get("model") or "")[:60]
        rows, now = [], _now()
        for o in obs:
            if not isinstance(o, dict):
                raise ApiError(400, "invalid", "תצפית לא תקינה")
            lat, lon = _valid_coords(o.get("lat"), o.get("lon"))
            attrs = {}
            try:
                for k in self.STREET_ATTRS:
                    v = (o.get("attributes") or {}).get(k)
                    if v is not None:
                        attrs[k] = max(0.0, min(1.0, float(v)))
                for k in ("luminance", "dark_fraction"):
                    if o.get(k) is not None:
                        float(o[k])
            except (TypeError, ValueError, AttributeError):
                raise ApiError(400, "invalid", "ערך לא תקין בתצפית")
            out = {"model": model, "model_scores": attrs, "measured": {}}
            for k in ("luminance", "dark_fraction"):
                if o.get(k) is not None:
                    out["measured"][k] = max(0.0, min(1.0, float(o[k])))
            labels = {k: int(bool(v)) for k, v in (o.get("labels") or {}).items()
                      if k in self.STREET_ATTRS + ("felt_safe",)}
            if labels:
                out["user_labels"] = labels
            observed = o.get("observed_at")
            observed = float(observed) if isinstance(observed, (int, float)) and 0 < observed <= now + 300 else None
            rows.append(("obs_" + uuid.uuid4().hex, "user_video", user, lat, lon, observed, now,
                         json.dumps(out, ensure_ascii=False),
                         json.dumps({"accepted": True, "version": str(consent["version"])[:20],
                                     "walk_id": walk, "media_stored": False}, ensure_ascii=False)))
        self._tx(lambda con: con.executemany(
            """INSERT INTO street_observations(id, kind, submitted_by, lat, lon, observed_at, received_at,
               attributes, consent) VALUES (?,?,?,?,?,?,?,?,?)""", rows))
        return {"walk_id": walk, "stored": len(rows), "media_stored": False}

    def my_walks(self, user):
        rows = self._q("""SELECT json_extract(consent,'$.walk_id') AS walk_id, COUNT(*) AS n, MIN(received_at) AS at
                          FROM street_observations WHERE submitted_by=? AND kind='user_video'
                          GROUP BY walk_id ORDER BY at DESC""", (user,))
        return {"walks": rows}

    def delete_my_walks(self, user, walk_id=None):
        """פרישה: מחיקת התצפיות של המשתמש (הליכה אחת או כולן)."""
        q, args = "DELETE FROM street_observations WHERE submitted_by=? AND kind='user_video'", [user]
        if walk_id:
            q += " AND json_extract(consent,'$.walk_id')=?"
            args.append(walk_id)
        n = self._tx(lambda con: con.execute(q, args).rowcount)
        return {"deleted": n}

    MAX_STREAMS_PER_USER = 6     # SSE + long-poll פתוחים לחשבון (token אחד פתח 300 - סקירה 2)

    def acquire_stream(self, user):
        with self._lock:
            n = self._streams.get(user, 0)
            if n >= self.MAX_STREAMS_PER_USER:
                return False
            self._streams[user] = n + 1
            return True

    def release_stream(self, user):
        with self._lock:
            n = self._streams.get(user, 0) - 1
            if n > 0:
                self._streams[user] = n
            else:
                self._streams.pop(user, None)

    HEALTH_CACHE_SECONDS = 60

    def health(self):
        # quick_check על כל ה-DB בכל קריאה (לא מאומתת) = DoS זול (581ms ב-75MB). מטמון לדקה.
        at, cached = self._health_cache
        if cached is not None and _now() - at < self.HEALTH_CACHE_SECONDS:
            ok, detail = cached
        else:
            ok, detail = storage.sqlite_health(self.db_path)
            self._health_cache = (_now(), (ok, detail))
        subs = self._q("SELECT COUNT(*) AS n FROM push_subscriptions", one=True)["n"]
        return {"status": "ok" if ok else "degraded", "db": detail, "time": _now(),
                "public_url": self.public_url or None,
                "push_providers": {"stream": "active", "web_push": "active", "web_push_devices": subs,
                                   "fcm": self._fcm().status,
                                   "fcm_devices": self._q("SELECT COUNT(*) AS n FROM fcm_tokens", one=True)["n"],
                                   "apns": "not_configured"},
                "sms": self.sms_status()}


def _valid_coords(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_location", "קואורדינטות לא תקינות")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(400, "invalid_location", "קואורדינטות מחוץ לטווח")
    return lat, lon


def _band(km):
    for b in (0.5, 1, 2, 3, 5, 10, 20):
        if km <= b:
            return b
    return 50


# ---------------------------------------------------------------- HTTP layer
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class Handler(BaseHTTPRequestHandler):
    server_version = "LifeGuard/1.0"
    protocol_version = "HTTP/1.1"
    timeout = 60     # קריאה מהלקוח (בקשה/keep-alive) - לקוח איטי/שותק לא מחזיק thread לנצח

    def log_message(self, fmt, *args):  # בלי הדפסת URLs/טוקנים ללוג
        pass

    @property
    def svc(self):
        return self.server.service

    def _send(self, status, body):
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_raw(self, status, data, content_type, cache="no-store", extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        # בלי סקריפטים inline ובלי מקורות חיצוניים (חוץ מאריחי מפה) - מקטין סיכון XSS לטוקן בדפדפן
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' https://*.tile.openstreetmap.org data:; "
                         "style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'self'")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _admin(self, allow_session=False):
        """נתיבי ניהול: LIFEGUARD_ADMIN_TOKEN (מוגדר בשרת בלבד). טוקן כניסת מנהל/ת מהדף (POST /v1/admin/login)
        מתקבל *רק* בנתיבים שמסומנים allow_session (לוח הבקרה - נתונים מצטברים בלבד): נתיבי הניהול האחרים
        מחזירים שמות משתמשים ומבצעים פעולות, וסיסמת דף שנוחשה לא תפתח אותם. -> מזהה ה-actor ליומן הביקורת."""
        expected = os.environ.get("LIFEGUARD_ADMIN_TOKEN") or getattr(self.server, "admin_token", None)
        auth = self.headers.get("Authorization", "")
        given = auth[7:] if auth.startswith("Bearer ") else ""
        if expected and hmac.compare_digest(given.encode(), expected.encode()):
            return "admin:env-token"
        if given.startswith(admin_dashboard.ADMIN_TOKEN_PREFIX) and allow_session:
            name = self.svc.admin_authenticate(given)
            if name:
                return "admin:" + name
            raise ApiError(401, "admin_session_expired", "ההתחברות לניהול פגה - יש להתחבר מחדש")
        raise ApiError(404, "not_found", "נתיב לא קיים")

    def _body(self, limit=None):
        limit = limit or MAX_BODY_BYTES
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "length_required", "נדרש Content-Length (ללא chunked)")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad_length", "Content-Length לא תקין")
        # שלילי -> rfile.read(-1) קורא עד סגירת החיבור ועוקף את המגבלה (נמצא בסקירת אבטחה)
        if length < 0 or length > limit:
            raise ApiError(413, "too_large", "גוף הבקשה גדול מדי")
        if not length:
            return {}
        self._body_consumed = True          # הגוף נקרא כאן - _drain_body לא יקרא שוב (היה נתקע)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError(400, "bad_json", "JSON לא תקין")
        if not isinstance(body, dict):
            raise ApiError(400, "bad_json", "נדרש אובייקט JSON")
        return body

    def _user(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        return self.svc.authenticate(token), token

    def _drain_body(self):
        """
        גוף בקשה שה-handler לא קרא נשאר בחיבור ה-keep-alive, והבקשה הבאה באותו חיבור נקראת כ-"{}POST"
        -> 501 (באג שנמצא באינטגרציה עם אפליקציית האנדרואיד). קוראים ומשליכים; אורך לא תקין -> סוגרים.
        """
        if getattr(self, "_body_consumed", False):
            return
        self._body_consumed = True
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.close_connection = True
            return
        if length < 0 or length > DRAIN_MAX_BYTES:
            self.close_connection = True
        elif length:
            self.rfile.read(length)

    def _dispatch(self, method):
        self._body_consumed = False
        try:
            self._route(method)
        finally:
            try:
                self._drain_body()
            except OSError:
                self.close_connection = True

    def _route(self, method):
        parsed = urlparse(self.path)
        for m, rx, fn in ROUTES:
            if m != method:
                continue
            match = rx.match(parsed.path)
            if match:
                try:
                    result = fn(self, *match.groups(), query=parse_qs(parsed.query))
                    if result is not None:
                        self._send(200, result)
                except ApiError as e:
                    self._send(e.status, {**e.extra, "error": e.code, "message": e.message})
                except Exception as e:  # לא חושפים stack trace ללקוח
                    self._send(500, {"error": "internal", "message": "שגיאת שרת"})
                    sys.stderr.write(f"[server error] {type(e).__name__}: {e}\n")
                return
        self._send(404, {"error": "not_found", "message": "נתיב לא קיים"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")


P = "/" + API_VERSION


@route("GET", P + "/health")
def _health(h, query):
    return h.svc.health()


@route("POST", P + "/auth/register")  # (ip נמסר ל-register - ראו _register)
def _register(h, query):
    b = h._body()
    return h.svc.register(b.get("username"), b.get("password"), b.get("display_name"), ip=_client_ip(h))


def _trusted_proxy(addr):
    """רק proxy בכתובת פרטית/loopback (Caddy באותה רשת docker) רשאי לקבוע X-Forwarded-For.
    אחרת, אם הפורט נגיש ישירות, תוקף מחליף XFF בכל בקשה ועוקף את נעילת הכניסה (סקירה 2)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    nets = os.environ.get("LIFEGUARD_TRUSTED_PROXIES", "")
    if nets:
        return any(ip in ipaddress.ip_network(n.strip(), strict=False) for n in nets.split(",") if n.strip())
    return ip.is_private or ip.is_loopback


def _client_ip(h):
    """מאחורי Caddy (LIFEGUARD_BEHIND_PROXY=1) - הכתובת שה-proxy הוסיף (הערך האחרון; הלקוח לא שולט בו)."""
    peer = h.client_address[0]
    if os.environ.get("LIFEGUARD_BEHIND_PROXY") == "1" and _trusted_proxy(peer):
        xff = h.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[-1].strip()[:64]
    return peer


@route("POST", P + "/auth/login")
def _login(h, query):
    b = h._body()
    return h.svc.login(b.get("username"), b.get("password"), b.get("device", ""), ip=_client_ip(h))


@route("POST", P + "/auth/logout")
def _logout(h, query):
    _, token = h._user()
    h.svc.logout(token)
    return {"ok": True}


@route("POST", P + "/me/password")
def _password(h, query):
    user, token = h._user()
    b = h._body()
    return h.svc.change_password(user, token, b.get("old_password"), b.get("new_password"))


@route("GET", P + "/me/sessions")
def _sessions(h, query):
    user, token = h._user()
    return h.svc.sessions(user, token)


@route("DELETE", P + "/me/sessions")
def _sessions_revoke(h, query):
    user, token = h._user()
    return h.svc.revoke_sessions(user, token, (query.get("id") or [None])[0])


@route("GET", P + "/me")
def _me(h, query):
    return h.svc.me(h._user()[0])


@route("GET", P + "/me/volunteer/roles")
def _vol_roles_get(h, query):
    return h.svc.volunteer_roles(h._user()[0])


@route("PUT", P + "/me/volunteer/roles")
def _vol_roles(h, query):
    b = h._body()
    return h.svc.set_volunteer_roles(h._user()[0], b.get("roles"), b.get("languages"))


@route("POST", P + r"/incidents/([\w]+)/request-helper")
def _req_helper(h, iid, query):
    b = h._body()
    return h.svc.request_helper(h._user()[0], iid, b.get("kind"), b.get("reason", ""))


@route("POST", P + r"/incidents/([\w]+)/helper-respond")
def _helper_resp(h, iid, query):
    return h.svc.helper_respond(h._user()[0], iid, h._body().get("action"))


@route("POST", P + r"/incidents/([\w]+)/helpers/([^/]+)/release")
def _helper_release(h, iid, helper, query):
    return h.svc.release_helper(h._user()[0], iid, helper.lower())


@route("POST", P + "/support-requests")
def _support_req(h, query):
    b = h._body()
    return h.svc.create_support_request(h._user()[0], b.get("note", ""), b.get("language", "he"))


@route("POST", P + "/me/devices/fcm")
def _fcm_register(h, query):
    b = h._body()
    return h.svc.register_fcm(h._user()[0], b.get("token"), b.get("device", ""))


@route("DELETE", P + "/me/devices/fcm")
def _fcm_unregister(h, query):
    return h.svc.unregister_fcm(h._user()[0], h._body().get("token"))


@route("GET", P + "/me/setup")
def _me_setup(h, query):
    return h.svc.setup_status(h._user()[0])


@route("GET", P + r"/incidents/([\w]+)/evidence")
def _evidence(h, iid, query):
    return h.svc.evidence_record(h._user()[0], iid)


@route("GET", P + r"/evidence/by-client/([\w\-]+)")
def _evidence_by_client(h, cid, query):
    return h.svc.evidence_by_client(h._user()[0], cid)


@route("GET", P + "/evidence/public-key")
def _evidence_pk(h, query):
    return h.svc.evidence_public_key()


@route("PUT", P + "/me/volunteer")
def _vol(h, query):
    b = h._body()
    return h.svc.set_volunteer(h._user()[0], b.get("available"), b.get("radius_km"), b.get("skills"))


@route("PUT", P + "/me/presence")
def _presence(h, query):
    b = h._body()
    return h.svc.update_presence(h._user()[0], b.get("lat"), b.get("lon"), b.get("is_precise"),
                                 b.get("accuracy_m"), b.get("source"))


@route("GET", P + "/community/summary")
def _community(h, query):
    radius = float((query.get("radius_km") or ["5"])[0])
    return h.svc.community_summary(h._user()[0], max(0.2, min(radius, 50)))


@route("PUT", P + "/me/phone")
def _phone(h, query):
    return h.svc.set_phone(h._user()[0], h._body().get("phone"))


@route("GET", P + "/relationships")
def _rels(h, query):
    return h.svc.relationships(h._user()[0])


@route("GET", P + "/circle")
def _circle(h, query):
    return h.svc.relationships(h._user()[0])


@route("POST", P + "/relationships")
def _invite(h, query):
    return h.svc.invite(h._user()[0], h._body().get("contact"))


@route("POST", P + r"/relationships/([^/]+)/respond")
def _respond_invite(h, owner, query):
    return h.svc.respond_invite(h._user()[0], owner.lower(), bool(h._body().get("accept")))


@route("DELETE", P + r"/relationships/([^/]+)")
def _remove_rel(h, other, query):
    return h.svc.remove_relationship(h._user()[0], other.lower())


@route("POST", P + "/invitations")
def _create_inv(h, query):
    b = h._body()
    return h.svc.create_invitation(h._user()[0], username=b.get("username"), phone=b.get("phone"),
                                   label=b.get("label"), as_code=bool(b.get("code")),
                                   send_sms=bool(b.get("send_sms")))


@route("POST", P + "/invitations/redeem")
def _redeem(h, query):
    b = h._body()
    return h.svc.redeem_invitation(h._user()[0], b.get("code"), accept=bool(b.get("accept", True)),
                                   preview=bool(b.get("preview")))


@route("POST", P + r"/invitations/(inv_[0-9a-f]+)/respond")
def _respond_inv(h, inv_id, query):
    return h.svc.respond_invitation(h._user()[0], inv_id, bool(h._body().get("accept")))


@route("POST", P + r"/invitations/(inv_[0-9a-f]+)/confirm")
def _confirm_inv(h, inv_id, query):
    return h.svc.confirm_invitation(h._user()[0], inv_id, bool(h._body().get("confirm")))


@route("POST", P + r"/invitations/(inv_[0-9a-f]+)/resend")
def _resend_inv(h, inv_id, query):
    return h.svc.resend_invitation(h._user()[0], inv_id)


@route("POST", P + r"/invitations/(inv_[0-9a-f]+)/cancel")
def _cancel_inv(h, inv_id, query):
    return h.svc.cancel_invitation(h._user()[0], inv_id)


@route("DELETE", P + r"/connections/([^/]+)")
def _disconnect(h, other, query):
    scope = (query.get("scope") or ["both"])[0]
    if scope not in ("both", "my_contact", "contact_for"):
        raise ApiError(400, "invalid_scope", "scope לא חוקי")
    return h.svc.disconnect(h._user()[0], other.lower(), scope)


@route("GET", P + "/notifications/cursor")
def _cursor(h, query):
    return h.svc.notification_cursor(h._user()[0])


@route("POST", P + "/trusted/notify")
def _notify_trusted(h, query):
    b = h._body()
    ntype = "test" if b.get("type") == "test" else "info"
    return h.svc.notify_trusted(h._user()[0], b.get("text", ""), ntype)


@route("POST", P + "/notifications/status")
def _notif_status(h, query):
    return h.svc.notification_status(h._user()[0], h._body().get("ids"))


@route("POST", P + "/incidents")
def _create(h, query):
    b = h._body()
    return h.svc.create_incident(h._user()[0], b.get("client_id"), b.get("mode", "loud"),
                                 b.get("emergency_type", "unknown"), b.get("severity", "unknown"),
                                 b.get("location"), b.get("profile"), b.get("escort_id"), b.get("risk"),
                                 b.get("trigger"))


@route("GET", P + "/incidents/inbox")
def _inbox(h, query):
    return {"incidents": h.svc.inbox(h._user()[0])}


@route("GET", P + r"/incidents/([\w]+)")
def _get(h, iid, query):
    return h.svc.get_incident(h._user()[0], iid)


@route("PATCH", P + r"/incidents/([\w]+)")
def _patch(h, iid, query):
    return h.svc.update_incident(h._user()[0], iid, h._body())


@route("POST", P + r"/incidents/([\w]+)/location")
def _loc(h, iid, query):
    return h.svc.update_location(h._user()[0], iid, h._body())


@route("GET", P + r"/incidents/by-client/([\w\-]+)")
def _inc_by_client(h, cid, query):
    user = h._user()[0]
    row = h.svc._q("SELECT id FROM incidents WHERE owner=? AND client_id=?", (user, cid[:100]), one=True)
    if not row:
        raise ApiError(404, "not_found", "האירוע לא הגיע לשרת")
    return {"id": row["id"]}


@route("POST", P + r"/incidents/([\w]+)/confirm")
def _confirm(h, iid, query):
    return h.svc.confirm_incident(h._user()[0], iid)


@route("POST", P + r"/incidents/([\w]+)/recordings")
def _rec_upload(h, iid, query):
    return h.svc.upload_recording(h._user()[0], iid, h._body(limit=privacy.RECORDING_MAX_BYTES * 4 // 3 + 4096))


@route("GET", P + r"/incidents/([\w]+)/recordings")
def _rec_list(h, iid, query):
    return h.svc.list_recordings(h._user()[0], iid)


@route("GET", P + r"/incidents/([\w]+)/recordings/(rec_[0-9a-f]+)")
def _rec_get(h, iid, rid, query):
    return h.svc.get_recording(h._user()[0], iid, rid)


@route("DELETE", P + r"/me/recordings/(rec_[0-9a-f]+)")
def _rec_delete(h, rid, query):
    return h.svc.delete_recording(h._user()[0], rid)


@route("PUT", P + r"/relationships/([^/]+)/location-mode")
def _loc_mode(h, contact, query):
    return h.svc.set_location_mode(h._user()[0], contact.lower(), h._body().get("mode"))


@route("PUT", P + r"/relationships/([^/]+)/recording-access")
def _rec_access(h, contact, query):
    return h.svc.set_recording_access(h._user()[0], contact.lower(), bool(h._body().get("allowed")))


@route("POST", P + "/me/location-sharing/pause")
def _pause(h, query):
    return h.svc.pause_sharing(h._user()[0], bool(h._body().get("paused", True)))


@route("PUT", P + "/me/recording")
def _rec_setting(h, query):
    return h.svc.set_record_on_emergency(h._user()[0], bool(h._body().get("record_on_emergency")))


@route("GET", P + "/me/recording-policy")
def _rec_policy(h, query):
    return h.svc.recording_policy(h._user()[0])


@route("PUT", P + "/me/live-location")
def _live_loc(h, query):
    return h.svc.put_live_location(h._user()[0], h._body())


@route("GET", P + r"/contacts/([^/]+)/location")
def _contact_loc(h, owner, query):
    return h.svc.contact_location(h._user()[0], owner.lower())


@route("GET", P + "/me/privacy/history")
def _priv_hist(h, query):
    g = lambda k: (query.get(k) or [None])[0]  # noqa: E731
    return h.svc.privacy_history(h._user()[0], g("from"), g("to"), (g("contact") or "").lower() or None, g("type"),
                                 g("q"), g("limit") or 500)


@route("GET", P + "/me/privacy/permissions")
def _priv_perms(h, query):
    return h.svc.privacy_permissions(h._user()[0])


@route("GET", P + "/me/privacy/export")
def _priv_export(h, query):
    return h.svc.privacy_export(h._user()[0])


@route("POST", P + "/me/audit")
def _dev_audit(h, query):
    return h.svc.device_audit(h._user()[0], h._body().get("events"))


@route("GET", P + "/me/guardian")
def _guardian_list(h, query):
    return h.svc.my_guardian_links(h._user()[0])


@route("POST", P + "/guardian/links")
def _guardian_new(h, query):
    b = h._body()
    return h.svc.guardian_invite(h._user()[0], b.get("other"), b.get("i_am"), b.get("child_age"))


@route("POST", P + r"/guardian/links/(gl_[0-9a-f]+)/approve")
def _guardian_ok(h, gid, query):
    return h.svc.guardian_approve(h._user()[0], gid)


@route("POST", P + r"/guardian/links/(gl_[0-9a-f]+)/recording-consent")
def _guardian_rec(h, gid, query):
    return h.svc.guardian_recording_consent(h._user()[0], gid, bool(h._body().get("consent")))


@route("POST", P + r"/guardian/links/(gl_[0-9a-f]+)/revoke")
def _guardian_revoke(h, gid, query):
    return h.svc.guardian_revoke(h._user()[0], gid)


@route("POST", P + r"/admin/guardians/([^/]+)/verify")
def _admin_guardian(h, username, query):
    h._admin()
    return h.svc.admin_guardian_verify(username.lower(), bool(h._body().get("verified", True)))


@route("POST", P + "/admin/login")
def _admin_login(h, query):
    b = h._body()
    return h.svc.admin_login(b.get("username"), b.get("password"), ip=_client_ip(h))


@route("POST", P + "/admin/logout")
def _admin_logout(h, query):
    auth = h.headers.get("Authorization", "")
    return h.svc.admin_logout(auth[7:] if auth.startswith("Bearer ") else "", ip=_client_ip(h))


@route("GET", P + "/admin/session")
def _admin_session(h, query):
    return {"ok": True, "actor": h._admin(allow_session=True)}


@route("GET", P + "/admin/dashboard")
def _admin_dash(h, query):
    h._admin(allow_session=True)
    return h.svc.admin_dashboard()


@route("GET", P + "/admin/audit/verify")
def _admin_audit(h, query):
    h._admin()
    return h.svc.verify_audit()


@route("POST", P + r"/incidents/([\w]+)/respond")
def _resp(h, iid, query):
    return h.svc.respond(h._user()[0], iid, h._body().get("action"))


@route("POST", P + r"/incidents/([\w]+)/report")
def _report(h, iid, query):
    b = h._body()
    return h.svc.report_incident(h._user()[0], iid, b.get("kind"), b.get("note"))


@route("POST", P + r"/incidents/([\w]+)/messages")
def _msg(h, iid, query):
    b = h._body()
    return h.svc.add_message(h._user()[0], iid, b.get("text"), b.get("quick"))


@route("POST", P + r"/incidents/([\w]+)/messages/read")
def _msg_read(h, iid, query):
    return h.svc.mark_messages_read(h._user()[0], iid, h._body().get("up_to_id"))


@route("GET", P + "/notifications")
def _poll(h, query):
    user, _ = h._user()
    after = int((query.get("after") or ["0"])[0])
    wait = min(float((query.get("wait") or ["0"])[0]), 25)
    if not wait:
        return {"notifications": h.svc._pending(user, after)}
    if not h.svc.acquire_stream(user):
        raise ApiError(429, "too_many_streams", "יותר מדי חיבורים פתוחים לחשבון")
    try:
        return {"notifications": h.svc.wait_for_notifications(user, after, wait)}
    finally:
        h.svc.release_stream(user)


@route("POST", P + r"/notifications/(\d+)/received")
def _received(h, nid, query):
    return h.svc.mark_received(h._user()[0], nid)


@route("GET", P + "/stream")
def _stream(h, query):
    """Server-Sent Events: הדחיפה בזמן אמת לאפליקציה מחוברת."""
    user, _ = h._user()
    after = int((query.get("after") or [h.headers.get("Last-Event-ID") or "0"])[0])
    if not h.svc.acquire_stream(user):
        raise ApiError(429, "too_many_streams", "יותר מדי חיבורים פתוחים לחשבון")
    try:
        return _stream_body(h, user, after)
    finally:
        h.svc.release_stream(user)


def _stream_body(h, user, after):
    h.send_response(200)
    h.send_header("Content-Type", "text/event-stream; charset=utf-8")
    h.send_header("Cache-Control", "no-store")
    h.send_header("Connection", "close")
    h.end_headers()
    h.close_connection = True
    deadline = _now() + 300  # הלקוח מתחבר מחדש כל 5 דקות
    try:
        h.wfile.write(b": connected\n\n")
        h.wfile.flush()
        while _now() < deadline and not h.server.stopping.is_set():
            rows = h.svc.wait_for_notifications(user, after, 15)
            if not rows:
                h.wfile.write(b": keepalive\n\n")
            for r in rows:
                after = r["id"]
                data = json.dumps(r, ensure_ascii=False, default=str)
                h.wfile.write(f"id: {r['id']}\nevent: {r['type']}\ndata: {data}\n\n".encode("utf-8"))
            h.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    return None


# ---------- ליווי הביתה ----------
@route("POST", P + "/escorts")
def _escort_start(h, query):
    b = h._body()
    return h.svc.start_escort(h._user()[0], b.get("minutes"), b.get("destination", ""), b.get("mode", "loud"),
                              b.get("location"), kind="ride" if b.get("kind") == "ride" else "walk",
                              ride_type=b.get("ride_type"), destination_point=b.get("destination_point"),
                              vehicle=b.get("vehicle"))


@route("GET", P + "/escorts")
def _escorts(h, query):
    return h.svc.escorts_for(h._user()[0])


@route("GET", P + r"/escorts/(esc_[0-9a-f]+)")
def _escort_get(h, eid, query):
    return h.svc.get_escort(h._user()[0], eid)


@route("POST", P + r"/escorts/(esc_[0-9a-f]+)/location")
def _escort_loc(h, eid, query):
    return h.svc.escort_location(h._user()[0], eid, h._body())


@route("POST", P + "/me/volunteer/qualifications")
def _vol_qual(h, query):
    b = h._body()
    return h.svc.request_qualification(h._user()[0], b.get("qual"), b.get("org"), b.get("ref"))


@route("GET", P + "/me/volunteer/history")
def _vol_history(h, query):
    return h.svc.volunteer_history(h._user()[0])


@route("POST", P + r"/escorts/(esc_[0-9a-f]+)/checkin")
def _escort_checkin(h, eid, query):
    return h.svc.escort_checkin(h._user()[0], eid, h._body().get("status"))


@route("POST", P + r"/escorts/(esc_[0-9a-f]+)/sos")
def _escort_sos(h, eid, query):
    return h.svc.escort_sos(h._user()[0], eid, h._body().get("mode"))


@route("POST", P + r"/escorts/(esc_[0-9a-f]+)/(arrived|cancelled|extend)")
def _escort_act(h, eid, action, query):
    return h.svc.escort_action(h._user()[0], eid, action, h._body().get("minutes"))


@route("POST", P + r"/incidents/([\w]+)/feedback")
def _feedback(h, iid, query):
    b = h._body()
    return h.svc.give_feedback(h._user()[0], iid, str(b.get("volunteer") or "").lower(), b.get("rating"),
                               b.get("report"))


# ---------- מתנדבים: אימות זהות (חובה) ----------
@route("GET", P + "/me/volunteer/identity")
def _identity_status(h, query):
    return h.svc.identity_status(h._user()[0])


@route("POST", P + "/me/volunteer/identity")
def _identity_submit(h, query):
    user = h._user()[0]
    b = h._body(limit=volunteer_identity.UPLOAD_BODY_LIMIT)
    return h.svc.submit_identity(user, b.get("id_number"), b.get("id_image"), b.get("selfie"), b.get("consent"))


@route("DELETE", P + "/me/volunteer/identity")
def _identity_withdraw(h, query):
    return h.svc.withdraw_identity(h._user()[0])


@route("GET", P + "/admin/identity")
def _admin_identity_queue(h, query):
    h._admin()                       # טוקן הניהול של השרת בלבד - לא סיסמת דף הניהול
    return {"pending": h.svc.admin_identity_queue()}


@route("GET", P + r"/admin/identity/([^/]+)")
def _admin_identity_view(h, username, query):
    actor = h._admin()
    return h.svc.admin_identity_view(username.lower(), actor)


@route("POST", P + r"/admin/identity/([^/]+)/decide")
def _admin_identity_decide(h, username, query):
    actor = h._admin()
    b = h._body()
    return h.svc.admin_identity_decide(username.lower(), bool(b.get("approve")), b.get("note", ""), actor)


# ---------- מתנדבים: אימות ארגון/הכשרה ----------
@route("POST", P + "/me/volunteer/verification")
def _verify_req(h, query):
    b = h._body()
    return h.svc.request_verification(h._user()[0], b.get("organization"), b.get("credential_ref"))


@route("GET", P + "/admin/volunteers")
def _admin_vols(h, query):
    h._admin()
    return {"volunteers": h.svc.admin_volunteers((query.get("status") or [None])[0])}


@route("POST", P + r"/admin/volunteers/([^/]+)/verify")
def _admin_verify(h, username, query):
    h._admin()
    b = h._body()
    return h.svc.admin_verify(username.lower(), bool(b.get("approve")), b.get("note", ""))


@route("POST", P + r"/admin/volunteers/([^/]+)/suspend")
def _admin_suspend(h, username, query):
    h._admin()
    return h.svc.admin_set_suspended(username.lower(), bool(h._body().get("suspended", True)))


@route("GET", P + "/admin/qualifications")
def _admin_quals(h, query):
    h._admin()
    return {"qualifications": h.svc.admin_qualifications((query.get("status") or ["pending"])[0])}


@route("POST", P + r"/admin/qualifications/([^/]+)/([a-z_]+)")
def _admin_qual(h, username, qual, query):
    h._admin()
    b = h._body()
    return h.svc.admin_decide_qualification(username.lower(), qual, bool(b.get("approve")), b.get("note", ""))


@route("POST", P + r"/admin/street-reports/(\d+)")
def _admin_report(h, rid, query):
    h._admin()
    b = h._body()
    return h.svc.admin_decide_report(int(rid), bool(b.get("approve")), b.get("note", ""))


@route("POST", P + "/admin/national")
def _admin_national(h, query):
    h._admin()
    b = h._body()
    return h.svc.admin_national_mode(bool(b.get("active")), b.get("title", ""), b.get("message", ""), b.get("areas"))


# ---------- מודיעין רחובות / בטיחות מסלול / חירום לאומי ----------
def _qf(query, key):
    try:
        return float((query.get(key) or [None])[0])
    except (TypeError, ValueError):
        return None


@route("GET", P + "/map/environment")
def _map_environment(h, query):
    """מפת סביבה: גורמי סביבה ברמת רחוב (server/heatmap.py) - לא ציון סכנה לשכונה."""
    return h.svc.map_environment(h._user()[0], (query.get("bbox") or [""])[0], _qf(query, "at"))


@route("GET", P + "/street-intel")
def _street_intel(h, query):
    return h.svc.street_intel(h._user()[0], _qf(query, "lat"), _qf(query, "lon"), _qf(query, "radius") or 150,
                              include_news=(query.get("news") or ["0"])[0] == "1")


@route("POST", P + "/street-intel/reports")
def _street_report(h, query):
    b = h._body()
    return h.svc.add_street_report(h._user()[0], b.get("kind"), b.get("lat"), b.get("lon"), b.get("note", ""))


@route("GET", P + "/street-intel/reports/mine")
def _street_reports_mine(h, query):
    return {"reports": h.svc.my_street_reports(h._user()[0])}


@route("DELETE", P + r"/street-intel/reports/(\d+)")
def _street_report_del(h, rid, query):
    return h.svc.delete_street_report(h._user()[0], int(rid))


@route("POST", P + "/route-safety")
def _route_safety(h, query):
    b = h._body()
    return h.svc.route_safety(h._user()[0], b.get("routes"), b.get("from"), b.get("to"), b.get("at"),
                              include_news=bool(b.get("include_news")))


@route("GET", P + "/national/status")
def _national_status(h, query):
    return h.svc.national_status(h._user()[0], _qf(query, "lat"), _qf(query, "lon"))


@route("PUT", P + "/me/national/areas")
def _national_areas(h, query):
    return h.svc.set_national_areas(h._user()[0], h._body().get("areas"))


# ---------- מקורות נתונים חיצוניים (server/data_sources.py) ----------
@route("GET", P + "/data-sources")
def _data_sources(h, query):
    """ציבורי, קריאה בלבד, בלי קריאות רשת: רישום המקורות + המצב האחרון שנמדד. אף מפתח לא נכלל."""
    from server import data_sources
    return data_sources.report(data_sources.cache_for(h.svc))


@route("POST", P + "/admin/data-sources/check")
def _admin_data_sources_check(h, query):
    h._admin()
    from server import data_sources
    cache = data_sources.cache_for(h.svc)
    return {"results": data_sources.check_all(cache), **data_sources.report(cache)}


@route("GET", P + "/data/mapillary")
def _data_mapillary(h, query):
    """תצפיות נגזרות מ-Mapillary (כיסוי + עמודי תאורה שזוהו) - הערכה בלבד, עם ייחוס. בלי token: not_configured."""
    h._user()
    from server import data_sources
    from server.mapillary_client import MapillaryClient
    lat, lon = _valid_coords(_qf(query, "lat"), _qf(query, "lon"))
    radius = min(max(_qf(query, "radius_m") or 150.0, 20.0), 300.0)
    return MapillaryClient(cache=data_sources.cache_for(h.svc)).observe(lat, lon, radius)


# ---------- מידע רפואי ----------
@route("GET", P + "/me/medical")
def _med_get(h, query):
    return h.svc.get_medical(h._user()[0])


@route("PUT", P + "/me/medical")
def _med_put(h, query):
    return h.svc.set_medical(h._user()[0], h._body())


# ---------- SMS / טלפון ----------
@route("POST", P + "/me/phone/verify/start")
def _pv_start(h, query):
    return h.svc.phone_verify_start(h._user()[0])


@route("POST", P + "/me/phone/verify/check")
def _pv_check(h, query):
    return h.svc.phone_verify_check(h._user()[0], h._body().get("code"))


@route("PUT", P + "/me/sms-fallback")
def _sms_fb(h, query):
    return h.svc.set_sms_fallback(h._user()[0], bool(h._body().get("enabled")))


# ---------- Web Push ----------
@route("GET", P + "/push/public-key")
def _push_key(h, query):
    return h.svc.push_public_key()


@route("POST", P + "/push/subscribe")
def _push_sub(h, query):
    return h.svc.push_subscribe(h._user()[0], h._body())


@route("POST", P + "/push/unsubscribe")
def _push_unsub(h, query):
    return h.svc.push_unsubscribe(h._user()[0], h._body().get("endpoint"))


@route("GET", P + "/push/status")
def _push_status(h, query):
    return h.svc.push_status(h._user()[0])


# ---------- בדיקת מוכנות ----------
@route("POST", P + "/me/readiness")
def _ready_run(h, query):
    return h.svc.readiness_check(h._user()[0])


@route("GET", P + "/me/readiness")
def _ready_get(h, query):
    cid = (query.get("id") or [None])[0]
    return h.svc.readiness_result(h._user()[0], int(cid) if cid and cid.isdigit() else None)


# ---------- דף מיקום מקישור SMS (ציבורי, טוקן סודי, ללא JavaScript) ----------
@route("GET", r"/l/([A-Za-z0-9_\-]{20,64})")
def _loc_page(h, token, query):
    import html
    data = h.svc.location_link(token)
    esc = html.escape
    if data is None:
        body = "<h1>הקישור לא בתוקף</h1><p>הקישור פג או אינו קיים.</p>"
    elif data["closed"]:
        body = f"<h1>האירוע הסתיים</h1><p>האירוע של {esc(data['owner_display'])} הסתיים. מומלץ לוודא שהוא/היא בטוח/ה.</p>"
    else:
        loc = data.get("location")
        body = f"<h1>🚨 {esc(data['owner_display'])} שלח/ה קריאת חירום</h1>"
        if data["mode"] == "silent":
            body += "<p class='warn'>מצוקה שקטה: ייתכן שהוא/היא לא יכול/ה לדבר. אל תתקשר/י אם זה עלול לסכן.</p>"
        if loc:
            lat, lon = float(loc["lat"]), float(loc["lon"])
            age = int(max(0, _now() - (loc.get("received_at") or _now())) // 60)
            body += (f"<p>מיקום אחרון ({'GPS' if loc.get('is_precise') else 'משוער'}, לפני {age} דק'): "
                     f"<b>{lat:.5f}, {lon:.5f}</b></p>"
                     f"<p><a class='btn' href='https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}'>"
                     f"פתח בניווט</a> <a href='https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=17/"
                     f"{lat:.6f}/{lon:.6f}'>OpenStreetMap</a></p>")
        else:
            body += "<p>אין מיקום ידוע לאירוע.</p>"
        med = data.get("medical")
        if med:
            labels = {"blood_type": "סוג דם", "allergies": "אלרגיות", "medications": "תרופות", "conditions": "מצבים רפואיים",
                      "emergency_medical_contact": "איש קשר רפואי", "notes": "הערות"}
            body += "<h2>מידע רפואי</h2><ul>" + "".join(
                f"<li><b>{labels.get(k, k)}:</b> {esc(v)}</li>" for k, v in med.items()) + "</ul>"
        body += "<p>בסכנת חיים: <b>חייג/י 100 / 101 / 112</b>. רענן/י את הדף למיקום עדכני.</p>"
    page = ("<!doctype html><html lang='he' dir='rtl'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'><title>LifeGuard</title>"
            "<link rel='stylesheet' href='/app/link.css'></head><body><main>" + body + "</main></body></html>")
    h._send_raw(200, page.encode("utf-8"), "text/html; charset=utf-8")
    return None


# ---------- אפליקציית הנייד (PWA) - קבצים סטטיים ----------
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/manifest+json", ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
         ".png": "image/png", ".ico": "image/x-icon",
         # AI hazard detector weights (server/web/models/yamnet.bin, tools/build_yamnet_web.py) - fetched by the PWA
         # only after the user turns listening on. Run in plain JS: no WebAssembly, so the CSP needs no change.
         ".bin": "application/octet-stream"}


@route("GET", r"/app/?([A-Za-z0-9_\-./]*)")
def _static(h, rel, query):
    rel = rel or "index.html"
    path = os.path.realpath(os.path.join(WEB_DIR, rel))
    ext = os.path.splitext(path)[1].lower()
    if (os.path.commonpath([path, os.path.realpath(WEB_DIR)]) != os.path.realpath(WEB_DIR) or ext not in _MIME
            or not os.path.isfile(path)):
        raise ApiError(404, "not_found", "קובץ לא נמצא")
    with open(path, "rb") as fh:
        data = fh.read()
    extra = {"Service-Worker-Allowed": "/app/"} if rel == "sw.js" else None
    h._send_raw(200, data, _MIME[ext], cache="no-cache", extra_headers=extra)
    return None


# ---------- דף הניהול (server/admin_web) - מחוץ ל-/app/ כדי שה-Service Worker של ה-PWA לא ישמור אותו ----------
ADMIN_WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "admin_web")
_ADMIN_FILES = {"": "admin.html", "admin.html": "admin.html", "admin.js": "admin.js", "admin.css": "admin.css",
                "identity.html": "identity.html", "identity.js": "identity.js"}


@route("GET", r"/admin")
def _admin_redirect(h, query):
    h.send_response(302)
    h.send_header("Location", "/admin/")
    h.send_header("Content-Length", "0")
    h.end_headers()
    return None


@route("GET", r"/admin/([A-Za-z0-9_.\-]*)")
def _admin_static(h, rel, query):
    name = _ADMIN_FILES.get(rel)
    path = os.path.join(ADMIN_WEB_DIR, name) if name else None
    if not path or not os.path.isfile(path):
        raise ApiError(404, "not_found", "קובץ לא נמצא")
    with open(path, "rb") as fh:
        data = fh.read()
    h._send_raw(200, data, _MIME[os.path.splitext(path)[1].lower()], cache="no-store")
    return None


@route("GET", r"/")
def _root(h, query):
    h.send_response(302)
    h.send_header("Location", "/app/")
    h.send_header("Content-Length", "0")
    h.end_headers()
    return None


@route("GET", P + "/street-intel/segments")
def _street(h, query):
    h._user()
    return h.svc.street_intel_query((query.get("bbox") or [""])[0])


@route("POST", P + "/street-intel/walks")
def _walk_submit(h, query):
    return h.svc.submit_walk(h._user()[0], h._body())


@route("GET", P + "/street-intel/walks")
def _walk_list(h, query):
    return h.svc.my_walks(h._user()[0])


@route("DELETE", P + "/street-intel/walks")
def _walk_delete(h, query):
    return h.svc.delete_my_walks(h._user()[0], (query.get("walk_id") or [None])[0])


# ---------------------------------------------------------------- server bootstrap
class LifeGuardHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 256      # ברירת המחדל (5) דחתה 3-4% מהבקשות בסערת SOS עוד לפני שהגיעו לשרת

    def __init__(self, addr, service, tick_seconds=1.0):
        super().__init__(addr, Handler)
        self.service = service
        self.stopping = threading.Event()
        self._tick = tick_seconds
        self._worker = threading.Thread(target=self._escalation_loop, name="LG-Escalation", daemon=True)
        self._worker.start()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, name="LG-Dispatch", daemon=True)
        self._dispatcher.start()
        self._conns = set()
        self._conns_lock = threading.Lock()

    ssl_context = None
    HANDSHAKE_TIMEOUT = 10

    def process_request(self, request, client_address):
        with self._conns_lock:
            self._conns.add(request)
        super().process_request(request, client_address)

    def finish_request(self, request, client_address):
        """רץ ב-thread של החיבור. TLS: ה-handshake קורה כאן ולא ב-accept של ה-thread הראשי -
        אחרת חיבור TCP אחד ששותק תוקע את כל השרת (נמצא בסקירת אבטחה)."""
        if self.ssl_context is not None:
            request.settimeout(self.HANDSHAKE_TIMEOUT)
            try:
                request = self.ssl_context.wrap_socket(request, server_side=True)
            except (ssl.SSLError, OSError):
                return
            request.settimeout(None)
        super().finish_request(request, client_address)

    def shutdown_request(self, request):
        with self._conns_lock:
            self._conns.discard(request)
        super().shutdown_request(request)

    def _dispatch_loop(self):
        """תור המתנדבים לפי דחיפות, ב-thread נפרד: סבב עמוס לא מעכב צלצול/הסלמה/ליווי (בדיקת עומס)."""
        while not self.stopping.wait(self._tick):
            try:
                self.service.run_dispatch_queue()
            except Exception as e:
                sys.stderr.write(f"[dispatch] {e}\n")

    def _escalation_loop(self):
        while not self.stopping.wait(self._tick):
            try:
                self.service.run_escalation_tick(dispatch_queue=False)   # התור - ב-LG-Dispatch
            except Exception as e:
                sys.stderr.write(f"[escalation] {e}\n")

    def shutdown(self):
        self.stopping.set()
        for cond in list(self.service._wakeups.values()):
            with cond:
                cond.notify_all()
        super().shutdown()
        # סוגרים גם חיבורי keep-alive פתוחים - אחרת לקוח ממשיך לדבר עם handler של שרת שכבר כבה
        import socket as _socket
        with self._conns_lock:
            conns, self._conns = list(self._conns), set()
        for c in conns:
            try:
                c.shutdown(_socket.SHUT_RDWR)
                c.close()
            except OSError:
                pass


def create_server(host="127.0.0.1", port=8765, db_path=None, ack_timeout_seconds=120.0, tick_seconds=1.0,
                  certfile=None, keyfile=None, invite_ttl_seconds=None, **service_kw):
    """service_kw: sms_provider, public_url, escort_grace_seconds, sms_fallback_seconds (ברירת מחדל מהסביבה)."""
    db_path = db_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_data", "lifeguard_server.db")
    ttl = invite_ttl_seconds or float(os.environ.get("LIFEGUARD_INVITE_TTL_SECONDS", DEFAULT_INVITE_TTL_SECONDS))
    service = LifeGuardService(db_path, ack_timeout_seconds, invite_ttl_seconds=ttl, **service_kw)
    httpd = LifeGuardHTTPServer((host, port), service, tick_seconds)
    if certfile and keyfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile, keyfile)
        httpd.ssl_context = ctx           # handshake לכל חיבור ב-thread שלו (finish_request)
    elif host not in ("127.0.0.1", "localhost", "::1") and os.environ.get("LIFEGUARD_BEHIND_PROXY") != "1":
        # LIFEGUARD_BEHIND_PROXY=1: TLS מסתיים ב-reverse proxy (Caddy בפריסת Docker) - התעבורה הפנימית מבודדת
        sys.stderr.write("⚠️ השרת מאזין לרשת ללא TLS. בפריסה אמיתית חובה להגדיר LIFEGUARD_TLS_CERT/KEY.\n")
    return httpd


def main():
    import argparse
    ap = argparse.ArgumentParser(description="LifeGuard backend")
    ap.add_argument("--host", default=os.environ.get("LIFEGUARD_SERVER_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("LIFEGUARD_SERVER_PORT", "8765")))
    ap.add_argument("--db", default=os.environ.get("LIFEGUARD_SERVER_DB"))
    ap.add_argument("--ack-timeout", type=float, default=float(os.environ.get("LIFEGUARD_ACK_TIMEOUT_SECONDS", "120")))
    ap.add_argument("--cert", default=os.environ.get("LIFEGUARD_TLS_CERT"))
    ap.add_argument("--key", default=os.environ.get("LIFEGUARD_TLS_KEY"))
    a = ap.parse_args()
    httpd = create_server(a.host, a.port, a.db, a.ack_timeout, certfile=a.cert, keyfile=a.key)
    scheme = "https" if a.cert else "http"
    print(f"LifeGuard backend: {scheme}://{a.host}:{a.port}/{API_VERSION}  (ack timeout {a.ack_timeout:.0f}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()

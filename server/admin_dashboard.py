# -*- coding: utf-8 -*-
"""
admin_dashboard.py - כניסת מנהל/ת (שם משתמש + סיסמה) ולוח ניהול: סטטיסטיקות, תקינות, מאגרי מידע ואיך המנוע עובד.

אבטחה:
- אין סיסמה בקוד/במאגר. החשבון נוצר בשרת בלבד:  python -m server.admin set-admin-password --user <שם>
  (getpass, או --password-stdin לאוטומציה). נשמר PBKDF2-SHA256 עם salt - אותו גיבוב של סיסמאות המשתמשים.
- POST /v1/admin/login -> טוקן ניהול אקראי ("lga_..."), נשמר בשרת כ-hash בלבד, תוקף ADMIN_SESSION_TTL_S, ניתן לביטול
  (POST /v1/admin/logout). אחרי ADMIN_MAX_FAILURES כישלונות ל-(שם, כתובת) בתוך ADMIN_FAIL_WINDOW_S -> 429 עם זמן המתנה.
  השוואה בזמן קבוע, אותו זמן חישוב גם לשם שלא קיים, הודעת שגיאה כללית. כל כניסה/יציאה/כישלון ביומן הביקורת.
- הלוח מציג **סיכומים מצטברים בלבד**: אין שמות משתמש, שמות תצוגה, הודעות, מיקומים או טלפונים - גם מי שניחש/ה
  את הסיסמה לא מקבל/ת מידע אישי מהלוח.
- כל מספר מחושב בזמן אמת ממסד הנתונים או מיובא מהקוד (לא מועתק). מדד שאי אפשר לחשב -> null + סיבה.
"""

import ast
import csv
import hashlib
import hmac
import json
import math
import os
import platform
import re
import secrets
import sys
import threading
import time
import unicodedata

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADMIN_SESSION_TTL_S = 8 * 3600
ADMIN_MAX_FAILURES = 5               # לכל (שם משתמש, כתובת IP)
ADMIN_IP_MAX_FAILURES = 20           # לכתובת IP על פני כל שמות המשתמש
ADMIN_FAIL_WINDOW_S = 15 * 60
ADMIN_TOKEN_PREFIX = "lga_"
ADMIN_MIN_PASSWORD = 8
ADMIN_USERNAME_RE = re.compile(r"^[\w֐-׿.\-]{3,32}$", re.UNICODE)
GENERIC_LOGIN_ERROR = "שם משתמש או סיסמה שגויים"
DECLINE_LABEL = "לא זמין/ה"          # סוף הטקסט של אירוע 'response' שהוא סירוב (lifeguard_server.respond)
CATALOG_PATH = os.path.join(HERE, "research", "data_catalog.csv")
ACOUSTIC_DOC = os.path.join(HERE, "research", "acoustic_benchmark_fsd50k.md")
SAFE_PLACES_PATH = os.path.join(HERE, "safe_places", "safe_places_il.json")
MAP_MODULE_PATH = os.path.join(HERE, "map_module.py")
PRIVACY_NOTE = ("הלוח מציג סיכומים מצטברים בלבד: אין בו שמות משתמש, שמות תצוגה, הודעות, מיקומים מדויקים או מספרי "
                "טלפון - גם מי שמשיג/ה את סיסמת הניהול לא רואה מידע אישי כאן.")

MIGRATION = """
CREATE TABLE admin_accounts(username TEXT PRIMARY KEY, pw_salt TEXT NOT NULL, pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE admin_sessions(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, created_at REAL NOT NULL,
    expires_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_admin_sess_user ON admin_sessions(username);
CREATE TABLE admin_login_failures(username TEXT NOT NULL, ip TEXT NOT NULL, ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_admin_fail_user ON admin_login_failures(username, ip, ts);
CREATE INDEX IF NOT EXISTS idx_admin_fail_ip ON admin_login_failures(ip, ts);
"""

_LOGIN_LOCK = threading.Lock()       # בדיקת נעילה + גיבוב + רישום כישלון - אטומי (בקשות מקבילות לא עוקפות את המגבלה)


def normalize_admin_username(raw):
    return unicodedata.normalize("NFC", str(raw or "")).strip().lower()


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _na(reason):
    """מדד שאי אפשר לחשב - לעולם לא מספר מומצא."""
    return {"value": None, "reason": reason}


def _jsonable(v):
    if isinstance(v, dict):
        return {("ללא הערכה" if k is None else str(k)): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        items = [_jsonable(x) for x in v]
        return sorted(items, key=str) if isinstance(v, (set, frozenset)) else items
    return v


def _pct(values, p):
    """אחוזון בשיטת nearest-rank."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(p / 100 * len(s)))
    return round(s[k - 1], 1)


def _module_constants(path, names):
    """ערכי קבועים מקובץ פייתון בלי להריץ אותו (ast.literal_eval). קובץ חסר -> None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            n = node.targets[0].id
            if n in names:
                try:
                    out[n] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return out


def git_commit():
    """הקומיט הנוכחי מתוך .git (בלי להריץ git). בפריסת Docker אין .git -> None."""
    gd = os.path.join(HERE, ".git")
    try:
        with open(os.path.join(gd, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
        if not head.startswith("ref: "):
            return {"commit": head[:12], "branch": None}
        ref = head[5:]
        p = os.path.join(gd, *ref.split("/"))
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                return {"commit": fh.read().strip()[:12], "branch": ref.rsplit("/", 1)[-1]}
        with open(os.path.join(gd, "packed-refs"), encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split(" ")
                if len(parts) == 2 and parts[1] == ref:
                    return {"commit": parts[0][:12], "branch": ref.rsplit("/", 1)[-1]}
    except OSError:
        return None
    return None


def parse_acoustic_benchmark(path=ACOUSTIC_DOC):
    """research/acoustic_benchmark_fsd50k.md -> [{detector, rows:[{budget, threshold, recall, ci, fa_per_h}]}]."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    summary = None
    m = re.search(r"^- קטעים: (.+)$", text, re.M)
    if m:
        summary = m.group(1).strip()
    detectors = []
    for sec in re.split(r"^## ", text, flags=re.M)[1:]:
        name = sec.splitlines()[0].strip()
        rows = []
        for line in sec.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 5 and re.match(r"^[\d.]+$", cells[0]):
                rows.append({"budget_fa_per_h": float(cells[0]), "threshold": cells[1], "recall": cells[2],
                             "ci95": cells[3], "actual_fa_per_h": cells[4]})
        if rows:
            detectors.append({"detector": name, "rows": rows})
    return {"summary": summary, "detectors": detectors} if detectors else None


def read_catalog(path=CATALOG_PATH, limit=1000):
    if not os.path.exists(path):
        return {"available": False, "note": "קטלוג הנתונים עוד לא נבנה", "path": "research/data_catalog.csv"}
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [dict(r) for _, r in zip(range(limit), reader)]
        cols = reader.fieldnames or []
    return {"available": True, "path": "research/data_catalog.csv", "columns": cols, "rows": rows,
            "built_at": os.path.getmtime(path)}


def _item(key, label, value, source, desc="", unit=""):
    return {"key": key, "label": label, "value": _jsonable(value), "unit": unit, "source": source, "desc": desc}


class AdminDashboardMixin:
    # ================================================================ חשבונות ניהול (CLI בלבד)
    def admin_set_password(self, username, password):
        u = normalize_admin_username(username)
        if not ADMIN_USERNAME_RE.match(u):
            raise ValueError("שם משתמש לא תקין (3-32 תווים: אותיות, ספרות, . -)")
        if len(password or "") < ADMIN_MIN_PASSWORD:
            raise ValueError(f"סיסמה חייבת להכיל לפחות {ADMIN_MIN_PASSWORD} תווים")
        salt, h = self._pw_hash(password)
        now = time.time()
        existed = bool(self._q("SELECT 1 FROM admin_accounts WHERE username=?", (u,), one=True))

        def tx(con):
            con.execute("INSERT INTO admin_accounts(username, pw_salt, pw_hash, created_at, updated_at) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(username) DO UPDATE SET pw_salt=excluded.pw_salt, pw_hash=excluded.pw_hash, "
                        "updated_at=excluded.updated_at", (u, salt, h, now, now))
            con.execute("DELETE FROM admin_sessions WHERE username=?", (u,))      # סיסמה חדשה מנתקת את כל הכניסות
        self._tx(tx)
        self.audit("admin_password_set", actor="admin:" + u, via="cli", details={"existed": existed})
        return {"username": u, "updated": existed}

    def admin_remove_account(self, username):
        u = normalize_admin_username(username)

        def tx(con):
            con.execute("DELETE FROM admin_sessions WHERE username=?", (u,))
            return con.execute("DELETE FROM admin_accounts WHERE username=?", (u,)).rowcount
        n = self._tx(tx)
        if n:
            self.audit("admin_account_removed", actor="admin:" + u, via="cli")
        return {"removed": bool(n)}

    def admin_list_accounts(self):
        return self._q("SELECT username, created_at, updated_at FROM admin_accounts ORDER BY username")

    # ================================================================ כניסה / יציאה
    def admin_login(self, username, password, ip=""):
        u = normalize_admin_username(username)
        valid = bool(ADMIN_USERNAME_RE.match(u))
        actor = "admin:" + (u if valid else "<invalid>")
        ip = str(ip or "")[:64]
        with _LOGIN_LOCK:
            now = time.time()
            since = now - ADMIN_FAIL_WINDOW_S
            self._x("DELETE FROM admin_login_failures WHERE ts<?", (since,))
            fu = self._q("SELECT COUNT(*) AS n, MIN(ts) AS t FROM admin_login_failures WHERE username=? AND ip=? AND ts>=?",
                         (u, ip, since), one=True)
            fi = self._q("SELECT COUNT(*) AS n, MIN(ts) AS t FROM admin_login_failures WHERE ip=? AND ts>=?",
                         (ip, since), one=True)
            if fu["n"] >= ADMIN_MAX_FAILURES or fi["n"] >= ADMIN_IP_MAX_FAILURES:
                first = fu["t"] if fu["n"] >= ADMIN_MAX_FAILURES else fi["t"]
                retry = max(1, int(math.ceil(first + ADMIN_FAIL_WINDOW_S - now)))
                raise self._err(429, "locked", f"יותר מדי ניסיונות כושלים - נסה/י שוב בעוד {math.ceil(retry / 60)} דקות",
                                {"retry_after_s": retry})
            row = self._q("SELECT pw_salt, pw_hash FROM admin_accounts WHERE username=?", (u,), one=True) if valid else None
            if row:
                _, h = self._pw_hash(password or "", bytes.fromhex(row["pw_salt"]))
                ok = hmac.compare_digest(h, row["pw_hash"])
            else:
                self._pw_hash(password or "", b"\0" * 16)            # אותו זמן חישוב - לא חושפים אם השם קיים
                ok = False
            if not ok:
                self._x("INSERT INTO admin_login_failures(username, ip, ts) VALUES (?,?,?)", (u, ip, now))
                locked = fu["n"] + 1 >= ADMIN_MAX_FAILURES
                self.audit("admin_login", result="failed", actor=actor, via="web",
                           details={"ip": ip, "reason": "bad_credentials", "locked_now": locked})
                raise self._err(401, "bad_credentials", GENERIC_LOGIN_ERROR)
            self._x("DELETE FROM admin_login_failures WHERE username=? AND ip=?", (u, ip))
        token = ADMIN_TOKEN_PREFIX + secrets.token_urlsafe(32)
        now = time.time()
        self._x("DELETE FROM admin_sessions WHERE expires_at<?", (now,))
        self._x("INSERT INTO admin_sessions(token_hash, username, created_at, expires_at) VALUES (?,?,?,?)",
                (_token_hash(token), u, now, now + ADMIN_SESSION_TTL_S))
        self.audit("admin_login", actor=actor, via="web", details={"ip": ip})
        return {"token": token, "username": u, "expires_at": now + ADMIN_SESSION_TTL_S, "ttl_s": ADMIN_SESSION_TTL_S}

    def admin_authenticate(self, token):
        """-> שם מנהל/ת, או None (טוקן לא קיים / פג / חשבון נמחק)."""
        if not token or not str(token).startswith(ADMIN_TOKEN_PREFIX):
            return None
        row = self._q("SELECT s.username, s.expires_at FROM admin_sessions s JOIN admin_accounts a "
                      "ON a.username=s.username WHERE s.token_hash=?", (_token_hash(token),), one=True)
        if not row or row["expires_at"] < time.time():
            return None
        return row["username"]

    def admin_logout(self, token, ip=""):
        u = self.admin_authenticate(token)
        self._x("DELETE FROM admin_sessions WHERE token_hash=?", (_token_hash(token or ""),))
        if u:
            self.audit("admin_logout", actor="admin:" + u, via="web", details={"ip": str(ip or "")[:64]})
        return {"ok": True}

    # ================================================================ הלוח
    def admin_dashboard(self):
        t0 = time.time()
        out = {"generated_at": t0, "privacy_note": PRIVACY_NOTE, "usage": self._dash_usage(t0),
               "health": self._dash_health(t0), "data": self._dash_data(), "engine": self._dash_engine()}
        from server import admin_improvements
        ac = next((g.get("benchmark") for g in out["engine"]["groups"] if g["id"] == "acoustic"), None)
        out["improvements"] = admin_improvements.build(self, out, t0, ac)
        out["compute_ms"] = round((time.time() - t0) * 1000, 1)
        return out

    # ---------- עזרי SQL ----------
    def _n(self, sql, args=()):
        return self._q(sql, args, one=True)["n"]

    def _groups(self, sql, args=()):
        return {("—" if r["k"] is None else str(r["k"])): r["n"] for r in self._q(sql, args)}

    # ---------- סטטיסטיקות שימוש ----------
    def _dash_usage(self, now):
        day = 86400
        users = {"total": self._n("SELECT COUNT(*) AS n FROM users"),
                 "new_7d": self._n("SELECT COUNT(*) AS n FROM users WHERE created_at>=?", (now - 7 * day,)),
                 "new_30d": self._n("SELECT COUNT(*) AS n FROM users WHERE created_at>=?", (now - 30 * day,)),
                 "active_sessions": self._n("SELECT COUNT(*) AS n FROM sessions WHERE expires_at>?", (now,)),
                 "phone_verified": self._n("SELECT COUNT(*) AS n FROM users WHERE phone_verified=1"),
                 "guardian_verified": self._n("SELECT COUNT(*) AS n FROM users WHERE guardian_verified=1")}

        def inc_block(where, args):
            base = f"FROM incidents WHERE {where}"
            return {"total": self._n(f"SELECT COUNT(*) AS n {base}", args),
                    "by_status": self._groups(f"SELECT status AS k, COUNT(*) AS n {base} GROUP BY status", args),
                    "by_mode": self._groups(f"SELECT mode AS k, COUNT(*) AS n {base} GROUP BY mode", args),
                    "by_trigger_kind": self._groups(f"SELECT trigger_kind AS k, COUNT(*) AS n {base} GROUP BY trigger_kind",
                                                    args),
                    "by_activation": self._groups(f"SELECT activation AS k, COUNT(*) AS n {base} GROUP BY activation",
                                                  args),
                    "by_emergency_type": self._groups(
                        f"SELECT emergency_type AS k, COUNT(*) AS n {base} GROUP BY emergency_type", args),
                    "by_risk_level": self._groups(f"SELECT risk_level AS k, COUNT(*) AS n {base} GROUP BY risk_level",
                                                  args)}

        incidents = {"all_time": inc_block("1=1", ()),
                     "active_now": self._n("SELECT COUNT(*) AS n FROM incidents WHERE status='active'"),
                     "active_now_waiting_for_volunteer": self._n(
                         "SELECT COUNT(*) AS n FROM incidents WHERE status='active' AND dispatch_waiting=1"),
                     "windows": {k: inc_block("created_at>=?", (now - s,))
                                 for k, s in (("24h", day), ("7d", 7 * day), ("30d", 30 * day))},
                     "false_alarm_reports": self._groups("SELECT kind AS k, COUNT(*) AS n FROM incident_reports GROUP BY kind")}
        # ביטול מהיר / משך עד סיום
        durs = [r["d"] for r in self._q("SELECT ended_at-created_at AS d FROM incidents WHERE ended_at IS NOT NULL "
                                        "AND activation!='support'") if r["d"] is not None and r["d"] >= 0]
        incidents["duration_s"] = ({"n": len(durs), "median": _pct(durs, 50), "p90": _pct(durs, 90)} if durs else
                                   _na("אין עדיין אירועים שהסתיימו"))
        out = {"users": users, "incidents": incidents, "response": self._dash_response(),
               "delivery": self._dash_delivery(now), "volunteers": self._dash_volunteers(),
               "helpers": self._dash_helpers(), "street_reports": self._dash_reports(now),
               "circle": self._dash_circle(now), "escorts": self._dash_escorts(now),
               "national_mode": self._dash_national(), "privacy_features": self._dash_privacy_counts()}
        return out

    def _dash_response(self):
        """זמן מפתיחת האירוע עד הקבלה הראשונה (accept/on_my_way/arrived/handled) - מאירועי 'response' בציר הזמן.
        (recipients.responded_at נדרס בכל פעולה מאוחרת יותר, לכן לא משמש כאן.)"""
        rows = self._q("""SELECT i.id AS iid, i.created_at AS c, e.ts AS ts, r.role AS role
                          FROM incident_events e JOIN incidents i ON i.id=e.incident_id
                          LEFT JOIN recipients r ON r.incident_id=e.incident_id AND r.username=e.actor
                          WHERE e.kind='response' AND e.text NOT LIKE ? AND i.activation!='support'""",
                       ("%" + DECLINE_LABEL,))
        first, first_role = {}, {"trusted": {}, "volunteer": {}}
        for r in rows:
            d = r["ts"] - r["c"]
            if d < 0:
                continue
            first[r["iid"]] = min(d, first.get(r["iid"], d))
            if r["role"] in first_role:
                fr = first_role[r["role"]]
                fr[r["iid"]] = min(d, fr.get(r["iid"], d))
        n_inc = self._n("SELECT COUNT(*) AS n FROM incidents WHERE activation!='support'")

        def stat(d):
            v = list(d.values())
            if not v:
                return _na("אין עדיין אירוע שמישהו קיבל")
            return {"n": len(v), "median_s": _pct(v, 50), "p90_s": _pct(v, 90)}
        return {"incidents_considered": n_inc, "incidents_with_acceptance": len(first),
                "first_acceptance_any": stat(first), "first_acceptance_trusted": stat(first_role["trusted"]),
                "first_acceptance_volunteer": stat(first_role["volunteer"]),
                "method": "ts של אירוע 'response' הראשון שאינו סירוב, פחות created_at של האירוע (לא כולל בקשות תמיכה)"}

    def _dash_delivery(self, now):
        def block(where, args):
            sent = self._n(f"SELECT COUNT(*) AS n FROM recipients WHERE state!='queued' {where}", args)
            dlv = self._n(f"SELECT COUNT(*) AS n FROM recipients WHERE state!='queued' AND delivered_at IS NOT NULL {where}",
                          args)
            return {"sent": sent, "delivered": dlv, "rate": round(dlv / sent, 3) if sent else None}
        out = {"recipients_all": block("", ()),
               "recipients_trusted": block("AND role='trusted'", ()),
               "recipients_volunteer": block("AND role='volunteer'", ()),
               "recipients_30d": block("AND notified_at>=?", (now - 30 * 86400,)),
               "recipient_states": self._groups("SELECT state AS k, COUNT(*) AS n FROM recipients GROUP BY state"),
               "notifications": {"created": self._n("SELECT COUNT(*) AS n FROM notifications"),
                                 "received_by_app": self._n("SELECT COUNT(*) AS n FROM notifications "
                                                            "WHERE received_at IS NOT NULL")},
               "sms": self._groups("SELECT status AS k, COUNT(*) AS n FROM sms_log GROUP BY status"),
               "definition": "נמסר = האפליקציה של הנמען אישרה קבלה לשרת (delivered_at). נשלח = נמען שהוזעק (לא בתור)."}
        n = out["notifications"]
        n["rate"] = round(n["received_by_app"] / n["created"], 3) if n["created"] else None
        return out

    def _dash_volunteers(self):
        vols = self._q("SELECT available, suspended, verification, review_flag, roles FROM volunteers")
        roles = {}
        for v in vols:
            try:
                rs = json.loads(v["roles"] or "[]")
            except ValueError:
                rs = []
            for r in rs:
                roles[r] = roles.get(r, 0) + 1
        return {"registered": len(vols),
                "available_now": sum(1 for v in vols if v["available"] and not v["suspended"]),
                "suspended": sum(1 for v in vols if v["suspended"]),
                "review_flag": sum(1 for v in vols if v["review_flag"]),
                "by_role": roles,
                "by_verification": self._groups("SELECT verification AS k, COUNT(*) AS n FROM volunteers GROUP BY verification"),
                "qualifications": self._groups("SELECT qual || ':' || status AS k, COUNT(*) AS n FROM volunteer_qualifications "
                                               "GROUP BY qual, status"),
                "feedback": self._q("SELECT COUNT(rating) AS ratings, ROUND(AVG(rating), 2) AS avg_rating, "
                                    "COUNT(report) AS reports FROM volunteer_feedback", one=True)}

    def _dash_helpers(self):
        reqs = self._q("""SELECT r.kind AS kind, r.offers AS offers,
                          EXISTS(SELECT 1 FROM helpers h WHERE h.incident_id=r.incident_id AND h.kind=r.kind
                                 AND h.state IN ('accepted','done')) AS acc
                          FROM helper_requests r""")
        by = {}
        for r in reqs:
            o = "accepted" if r["acc"] else ("offered_no_accept" if r["offers"] else "no_helper_available")
            d = by.setdefault(r["kind"], {"total": 0, "accepted": 0, "offered_no_accept": 0, "no_helper_available": 0})
            d["total"] += 1
            d[o] += 1
        return {"requests_by_kind": by,
                "support_requests_no_emergency": self._n("SELECT COUNT(*) AS n FROM incidents WHERE activation='support'"),
                "helper_offer_states": self._groups("SELECT kind || ':' || state AS k, COUNT(*) AS n FROM helpers "
                                                    "GROUP BY kind, state")}

    def _dash_reports(self, now):
        return {"total": self._n("SELECT COUNT(*) AS n FROM street_reports"),
                "active": self._n("SELECT COUNT(*) AS n FROM street_reports WHERE expires_at>? AND status!='rejected'",
                                  (now,)),
                "by_kind": self._groups("SELECT kind AS k, COUNT(*) AS n FROM street_reports GROUP BY kind"),
                "by_status": self._groups("SELECT status AS k, COUNT(*) AS n FROM street_reports GROUP BY status"),
                "walk_observations": self._n("SELECT COUNT(*) AS n FROM street_observations")}

    def _dash_circle(self, now):
        return {"relationships": self._n("SELECT COUNT(*) AS n FROM relationships WHERE status='accepted'"),
                "by_location_mode": self._groups("SELECT location_mode AS k, COUNT(*) AS n FROM relationships "
                                                 "WHERE status='accepted' GROUP BY location_mode"),
                "with_recording_access": self._n("SELECT COUNT(*) AS n FROM relationships WHERE status='accepted' "
                                                 "AND recording_access=1"),
                "invitations": self._groups("SELECT status AS k, COUNT(*) AS n FROM invitations GROUP BY status"),
                "guardian_links": self._groups("SELECT status AS k, COUNT(*) AS n FROM guardian_links GROUP BY status")}

    def _dash_escorts(self, now):
        return {"by_kind_status": self._groups("SELECT kind || ':' || status AS k, COUNT(*) AS n FROM escorts "
                                               "GROUP BY kind, status"),
                "active_now": self._n("SELECT COUNT(*) AS n FROM escorts WHERE status='active'"),
                "active_past_deadline": self._n("SELECT COUNT(*) AS n FROM escorts WHERE status='active' AND deadline<?",
                                                (now,)),
                "escalated_total": self._n("SELECT COUNT(*) AS n FROM escorts WHERE status='escalated'"),
                "rides_by_type": self._groups("SELECT ride_type AS k, COUNT(*) AS n FROM escorts WHERE kind!='walk' "
                                              "GROUP BY ride_type")}

    def _dash_national(self):
        r = self._q("SELECT value FROM server_meta WHERE key='national_mode'", one=True)
        if not r:
            return {"active": False}
        m = json.loads(r["value"])
        return {"active": bool(m.get("active")), "title": m.get("title"), "since": m.get("since"),
                "areas_count": len(m.get("areas") or [])}

    def _dash_privacy_counts(self):
        return {"recordings_stored": self._n("SELECT COUNT(*) AS n FROM recordings WHERE deleted_at IS NULL"),
                "recordings_deleted": self._n("SELECT COUNT(*) AS n FROM recordings WHERE deleted_at IS NOT NULL"),
                "medical_profiles": self._n("SELECT COUNT(*) AS n FROM medical_profiles"),
                "live_location_sharers": self._n("SELECT COUNT(*) AS n FROM live_locations"),
                "sharing_paused": self._n("SELECT COUNT(*) AS n FROM privacy_settings WHERE sharing_paused=1"),
                "web_push_devices": self._n("SELECT COUNT(*) AS n FROM push_subscriptions"),
                "fcm_devices": self._n("SELECT COUNT(*) AS n FROM fcm_tokens")}

    # ---------- תקינות ----------
    def _dash_health(self, now):
        mod = sys.modules[type(self).__module__]
        from server import data_sources
        h = self.health()
        db = {"path_name": os.path.basename(self.db_path), "integrity": h.get("db"), "status": h.get("status")}
        if self.db_path != ":memory:" and os.path.exists(self.db_path):
            db["size_bytes"] = os.path.getsize(self.db_path)
            wal = self.db_path + "-wal"
            db["wal_bytes"] = os.path.getsize(wal) if os.path.exists(wal) else 0
        ver = self._q("SELECT MAX(version) AS v FROM schema_version", one=True)["v"]
        expected = len(getattr(mod, "_MIGRATIONS", []) or []) or None
        cache = data_sources.cache_for(self)
        with cache._lock:
            crow = cache._con.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(value)),0) FROM cache").fetchone()
        cache_info = {"entries": crow[0], "value_bytes": crow[1], "file": None}
        if cache.path != ":memory:" and os.path.exists(cache.path):
            cache_info["file"] = os.path.basename(cache.path)
            cache_info["file_bytes"] = os.path.getsize(cache.path)
        started = getattr(self, "started_at", None)
        audit = self.verify_audit()
        return {
            "server": {"api_version": getattr(mod, "API_VERSION", None),
                       "server_header": getattr(getattr(mod, "Handler", None), "server_version", None),
                       "git": git_commit() or _na("אין תיקיית .git בשרת הזה (למשל בפריסת Docker)"),
                       "python": platform.python_version(),
                       "started_at": started, "uptime_s": round(now - started) if started else None,
                       "public_url": self.public_url or None,
                       "behind_proxy": os.environ.get("LIFEGUARD_BEHIND_PROXY") == "1",
                       "admin_env_token_configured": bool(os.environ.get("LIFEGUARD_ADMIN_TOKEN")),
                       "admin_accounts": self._n("SELECT COUNT(*) AS n FROM admin_accounts"),
                       "admin_sessions_active": self._n("SELECT COUNT(*) AS n FROM admin_sessions WHERE expires_at>?",
                                                        (now,))},
            "database": {**db, "schema_version": ver, "schema_expected": expected,
                         "schema_up_to_date": (ver == expected) if expected else None},
            "push": {"web_push_vapid": "configured" if getattr(self, "vapid", None) else "not_configured",
                     "vapid_subject_set": bool(os.environ.get("LIFEGUARD_VAPID_SUBJECT", "").strip()),
                     **h.get("push_providers", {})},
            "sms": h.get("sms"),
            "routing": {"osrm_configured": bool(os.environ.get("LIFEGUARD_OSRM_URL", "").strip())},
            "audit_chain": audit,
            "data_cache": cache_info,
        }

    # ---------- מאגרי מידע ----------
    def _dash_data(self):
        from server import data_sources
        rep = data_sources.report(data_sources.cache_for(self))
        sources = []
        for s in rep["sources"]:
            st = s.get("status") or {}
            sources.append({"id": s["id"], "name": s["name"], "kind": s["kind"], "kind_label": s.get("kind_label"),
                            "reliability": s.get("reliability"), "licence": s.get("licence"),
                            "terms_url": s.get("terms_url"), "terms_checked_at": s.get("terms_checked_at"),
                            "commercial_ok": s.get("commercial_ok"), "coverage": s.get("coverage"),
                            "used_by": s.get("used_by"), "key_env": s.get("key_env"),
                            "key_configured": bool(st.get("key_present")) if s.get("key_env") else None,
                            "state": st.get("state"), "state_detail": st.get("detail"),
                            "last_check": st.get("last_check"), "usable_in_israel": s.get("usable_in_israel")})
        return {"sources": sources, "kinds": rep.get("kinds"), "note": rep.get("note"), "catalog": read_catalog()}

    # ---------- איך המנוע עובד (ערכים חיים מהקוד) ----------
    def _dash_engine(self):
        import inspect
        from server import (abuse, dispatch, features, helpers, national, news_layer as NL, privacy, rides, ring,
                            risk_factors_layer as RF, safety_intel as SI, signal_watch, volunteer_match as VM,
                            weather as WX)
        mod = sys.modules[type(self).__module__]
        S = "server/lifeguard_server.py"
        groups = []

        groups.append({"id": "activation", "title": "הפעלת חירום וספי ביטחון",
                       "summary": "SOS ידני (לחיצה/קול) מפעיל מיד זרימה מלאה. זיהוי אוטומטי (חיישן, אקוסטי, נפילה בשעון...) "
                                  f"מגיע עם ציון ביטחון: מתחת ל-{privacy.CONF_REJECT} נדחה (422) ולא מפעיל כלום; "
                                  f"בין {privacy.CONF_REJECT} ל-{privacy.CONF_HIGH} - 'לא מאושר': אנשי הקשר מקבלים התראה "
                                  f"אבל מתנדבים לא נשלחים עד שהבעלים מאשר/ת; {privacy.CONF_HIGH} ומעלה - זרימה מלאה.",
                       "items": [
                           _item("privacy.CONF_REJECT", "סף דחייה לזיהוי אוטומטי", privacy.CONF_REJECT, "server/privacy.py"),
                           _item("privacy.CONF_HIGH", "סף ביטחון גבוה (זרימה מלאה)", privacy.CONF_HIGH, "server/privacy.py"),
                           _item("privacy.AUTO_TRIGGERS", "טריגרים אוטומטיים", privacy.AUTO_TRIGGERS, "server/privacy.py"),
                           _item("privacy.MANUAL_TRIGGERS", "טריגרים ידניים", privacy.MANUAL_TRIGGERS, "server/privacy.py"),
                           _item("privacy.CONFIRMED_ACTIVATIONS", "הפעלות שנחשבות מאושרות (מתירות הקלטה)",
                                 privacy.CONFIRMED_ACTIVATIONS, "server/privacy.py")]})

        groups.append({"id": "ring", "title": "אנשי קשר - שרשרת צלצול",
                       "summary": "בכל רגע מצלצל רק איש קשר אחד, לפי קרבה למיקום האירוע (מיקום עדכני עד FRESH_SECONDS); "
                                  "בלי מענה תוך זמן הצלצול עוברים לבא. מי שקיבל - השרשרת נעצרת ושאר המשפחה מקבלת עדכון "
                                  "שקט. במצוקה שקטה: אם כל השרשרת עברה בלי מענה (או שאין אנשי קשר) - עוברים למתנדבים.",
                       "items": [
                           _item("service.ring_seconds", "זמן צלצול לאיש קשר (בשרת הזה)", self.ring_seconds, S, unit="שנ'"),
                           _item("ring.RING_SECONDS", "ברירת מחדל בקוד", ring.RING_SECONDS, "server/ring.py", unit="שנ'"),
                           _item("ring.FRESH_SECONDS", "גיל מיקום מרבי לסידור לפי קרבה", ring.FRESH_SECONDS,
                                 "server/ring.py", unit="שנ'")]})

        groups.append({"id": "dispatch", "title": "מתנדבים - תור עדיפויות ושיבוץ",
                       "summary": "אירוע גלוי -> מתנדבים תמיד, במקביל לאנשי הקשר. מצוקה שקטה -> רק אחרי שהשרשרת מוצתה או "
                                  "כשאין אנשי קשר. 'לא מאושר' ובקשת תמיכה -> אף פעם. כל האירועים שממתינים מדורגים בציון "
                                  "עדיפות (רמת סכנה + סוג + חומרה + שקט + בלי אנשי קשר + הזדקנות), ולכל אירוע פונים "
                                  "למספר מתנדבים לפי רמת הסכנה (fan-out). מתנדב שלא אישר תוך זמן האישור - 'expired' "
                                  "ועוברים לבא. בחירת מתנדב: רצועת זמן הליכה, ובתוכה הכשרה מאומתת מתאימה > מאומת/ת > "
                                  "אמינות > זמן הגעה.",
                       "items": [
                           _item("service.ack_timeout", "זמן אישור למתנדב (בשרת הזה)", self.ack_timeout, S, unit="שנ'"),
                           _item("server.DEFAULT_VOLUNTEER_RADIUS_KM", "רדיוס ברירת מחדל",
                                 getattr(mod, "DEFAULT_VOLUNTEER_RADIUS_KM", None), S, unit='ק"מ'),
                           _item("server.VOLUNTEER_LOCATION_MAX_AGE_SECONDS", "גיל מיקום מתנדב מרבי",
                                 getattr(mod, "VOLUNTEER_LOCATION_MAX_AGE_SECONDS", None), S, unit="שנ'"),
                           _item("server.ETA_BAND_SECONDS", "רצועת זמן הליכה (בתוכה - הכשרה/אימות קובעים)",
                                 getattr(mod, "ETA_BAND_SECONDS", None), S, unit="שנ'"),
                           _item("server.DISPATCH_RETRY_SECONDS", "בדיקה חוזרת לאירוע בלי מועמד",
                                 getattr(mod, "DISPATCH_RETRY_SECONDS", None), S, unit="שנ'"),
                           _item("dispatch.LEVEL_BASE", "בסיס ציון לפי רמת סכנה", dispatch.LEVEL_BASE, "server/dispatch.py"),
                           _item("dispatch.TYPE_WEIGHT", "תוספת לפי סוג חירום", dispatch.TYPE_WEIGHT, "server/dispatch.py"),
                           _item("dispatch.SEVERITY_WEIGHT", "תוספת לפי חומרה", dispatch.SEVERITY_WEIGHT,
                                 "server/dispatch.py"),
                           _item("dispatch.SILENT_BONUS", "תוספת למצוקה שקטה", dispatch.SILENT_BONUS, "server/dispatch.py"),
                           _item("dispatch.NO_CONTACTS_BONUS", "תוספת כשאין אנשי קשר", dispatch.NO_CONTACTS_BONUS,
                                 "server/dispatch.py"),
                           _item("dispatch.AGING_SECONDS_PER_POINT", "הזדקנות: שניות לנקודה",
                                 dispatch.AGING_SECONDS_PER_POINT, "server/dispatch.py"),
                           _item("dispatch.AGING_MAX", "הזדקנות: מקסימום נקודות", dispatch.AGING_MAX, "server/dispatch.py"),
                           _item("dispatch.CONFIDENCE_POINTS", "טווח השפעת ביטחון הזיהוי (±חצי)",
                                 dispatch.CONFIDENCE_POINTS, "server/dispatch.py"),
                           _item("dispatch.CONTEXT_POINTS", "נקודות הקשר (לילה/רחוב חשוך) לאירוע בלי הערכה",
                                 dispatch.CONTEXT_POINTS, "server/dispatch.py"),
                           _item("dispatch.FANOUT", "מספר מתנדבים במקביל לפי רמה", dispatch.FANOUT, "server/dispatch.py"),
                           _item("dispatch.PREEMPT_MARGIN", "פער ציון להעברת מתנדב שעוד לא אישר",
                                 dispatch.PREEMPT_MARGIN, "server/dispatch.py"),
                           _item("dispatch.BANDS", "רצועות דחיפות (ציון מינימלי, רמה)",
                                 [[b[0], b[2]] for b in dispatch.BANDS], "server/dispatch.py"),
                           _item("volunteer_match.TYPE_QUALS", "הכשרות שמתאימות לכל סוג חירום", VM.TYPE_QUALS,
                                 "server/volunteer_match.py"),
                           _item("volunteer_match.MIN_OFFERS", "מינימום פניות לפני דירוג אמינות", VM.MIN_OFFERS,
                                 "server/volunteer_match.py"),
                           _item("features.UNVERIFIED_MONTHLY_ACCEPTS", "קבלות לחודש למתנדב לא מאומת",
                                 features.UNVERIFIED_MONTHLY_ACCEPTS, "server/features.py"),
                           _item("features.REPORTS_TO_SUSPEND", "דיווחים (ממשתמשים שונים, 90 יום) להשעיה",
                                 features.REPORTS_TO_SUSPEND, "server/features.py")]})

        groups.append({"id": "abuse", "title": "הגנה מניצול לרעה של מתנדבים",
                       "summary": "לעולם לא חוסמים חירום: אנשי הקשר תמיד מקבלים ותמיד נשלח לפחות מתנדב אחד. לחשבון עם "
                                  "דפוס חשוד (ביטולים מהירים / דיווחי שווא) מוגבלים רק היתרונות: עדיפות, fan-out, העברת מתנדב.",
                       "items": [
                           _item("abuse.WINDOW_SECONDS", "חלון בדיקה", abuse.WINDOW_SECONDS, "server/abuse.py", unit="שנ'"),
                           _item("abuse.QUICK_CANCEL_SECONDS", "ביטול 'מהיר' עד", abuse.QUICK_CANCEL_SECONDS,
                                 "server/abuse.py", unit="שנ'"),
                           _item("abuse.QUICK_CANCELS_TO_LIMIT", "ביטולים מהירים להגבלה", abuse.QUICK_CANCELS_TO_LIMIT,
                                 "server/abuse.py"),
                           _item("abuse.REPORTS_TO_LIMIT", "דיווחי שווא להגבלה", abuse.REPORTS_TO_LIMIT, "server/abuse.py"),
                           _item("abuse.VOLUNTEERS_PER_HOUR_CAP", "תקרת מתנדבים למשתמש בשעה",
                                 abuse.VOLUNTEERS_PER_HOUR_CAP, "server/abuse.py"),
                           _item("abuse.MAX_ACTIVE_WITH_VOLUNTEERS", "אירועים פעילים בו-זמנית שמקבלים מתנדבים",
                                 abuse.MAX_ACTIVE_WITH_VOLUNTEERS, "server/abuse.py")]})

        groups.append({"id": "helpers", "title": "מתנדבי תמיכה מרחוק והזעקת רשויות",
                       "summary": "remote_support - שיחה בצ'אט, לא רואה שם ולא מיקום; authority_liaison - מזעיק/ה 100/101/102 "
                                  "בשביל מי שלא יכול/ה, רואה שם ומיקום ולכן חייב/ת אימות. מתנדב/ת הזעקה נשלח/ת אוטומטית אם "
                                  f"באירוע מאושר אף אחד לא קיבל תוך {helpers.LIAISON_AUTO_AFTER_S} שנ'.",
                       "items": [
                           _item("helpers.VOL_ROLES", "תפקידי מתנדבים", [f"{r} ({helpers.ROLE_HE[r]})" for r in helpers.VOL_ROLES],
                                 "server/helpers.py"),
                           _item("helpers.FANOUT", "פניות במקביל", helpers.FANOUT, "server/helpers.py"),
                           _item("helpers.MAX_OFFERS", "מקסימום פניות לבקשה", helpers.MAX_OFFERS, "server/helpers.py"),
                           _item("helpers.LIAISON_AUTO_AFTER_S", "הזעקה אוטומטית אחרי", helpers.LIAISON_AUTO_AFTER_S,
                                 "server/helpers.py", unit="שנ'"),
                           _item("helpers.MIN_ACCOUNT_AGE_S", "גיל חשבון מינימלי למתנדב/ת תמיכה",
                                 helpers.MIN_ACCOUNT_AGE_S, "server/helpers.py", unit="שנ'")]})

        groups.append({"id": "fallback", "title": "גיבויים: SMS, אין קליטה, ליווי ונסיעה",
                       "summary": "איש קשר שלא אישר קבלה באפליקציה מקבל SMS עם קישור מיקום זמני. טלפון ששותק באירוע/ליווי "
                                  "מזוהה כ'אין קליטה'. ליווי שלא הסתיים בזמן -> שאלה 'הכל בסדר?' ואז אירוע.",
                       "items": [
                           _item("service.sms_fallback_seconds", "SMS לאיש קשר שלא אישר קבלה (בשרת הזה)",
                                 getattr(self, "sms_fallback_seconds", None), "server/features.py", unit="שנ'"),
                           _item("features.LOCATION_LINK_TTL_SECONDS", "תוקף קישור מיקום ב-SMS",
                                 features.LOCATION_LINK_TTL_SECONDS, "server/features.py", unit="שנ'"),
                           _item("signal_watch.SIGNAL_LOSS_SECONDS", "אין קליטה בליווי אחרי",
                                 signal_watch.SIGNAL_LOSS_SECONDS, "server/signal_watch.py", unit="שנ'"),
                           _item("signal_watch.INCIDENT_SIGNAL_LOSS_SECONDS", "אין קליטה באירוע אחרי",
                                 signal_watch.INCIDENT_SIGNAL_LOSS_SECONDS, "server/signal_watch.py", unit="שנ'"),
                           _item("service.escort_grace_seconds", "חסד אחרי תום זמן ליווי (בשרת הזה)",
                                 getattr(self, "escort_grace", None),
                                 "server/features.py", unit="שנ'"),
                           _item("features.ESCORT_MAX_MINUTES", "אורך ליווי מרבי", features.ESCORT_MAX_MINUTES,
                                 "server/features.py", unit="דק'"),
                           _item("rides.ANOMALY_RESPONSE_S", "נסיעה: זמן לענות על חריגה", rides.ANOMALY_RESPONSE_S,
                                 "server/rides.py", unit="שנ'"),
                           _item("rides.ALARM_KINDS", "נסיעה: סוגי חריגה", rides.ALARM_KINDS, "server/rides.py")]})

        layers_ok = os.path.exists(SI.LAYERS_PATH)
        rf_ok = os.path.exists(RF.GRID_PATH)
        groups.append({"id": "route", "title": "ניקוד בטיחות מסלול",
                       "summary": f"דגימה כל {SI.SAMPLE_M} מ' לאורך המסלול, ניקוד לכל מקטע {SI.SEGMENT_M} מ'. הנקודות "
                                  "מסוכמות ומחולקות בק\"מ (risk_per_km). OSRM נותן רק מרחק וזמן - לא ציון בטיחות. "
                                  "מצבים: fastest; safest = risk_per_km הנמוך מבין חלופות עד פי "
                                  f"{SI.SAFEST_MAX_RATIO} מזמן המהיר; balanced = דקות + {SI.BALANCED_MIN_PER_POINT} × נקודות. "
                                  "מזג אוויר מוצג כהקשר בלבד.",
                       "items": [
                           _item("safety_intel.W_DARK_SEGMENT", "רחוב חשוך בלילה (למקטע)", SI.W_DARK_SEGMENT,
                                 "server/safety_intel.py", unit="נק'"),
                           _item("safety_intel.W_TRAFFIC_HOTSPOT", "מוקד תאונות הולכי רגל (פי 2 עם 2+ קשות)",
                                 SI.W_TRAFFIC_HOTSPOT, "server/safety_intel.py", unit="נק'"),
                           _item("safety_intel.W_REPORT", "דיווחי משתמשים: נק' × min(משקל, תקרה)", SI.W_REPORT,
                                 "server/safety_intel.py", unit="נק'"),
                           _item("safety_intel.REPORT_WEIGHT_CAP", "תקרת משקל דיווחים למקטע", SI.REPORT_WEIGHT_CAP,
                                 "server/safety_intel.py"),
                           _item("safety_intel.STATUS_WEIGHT", "משקל דיווח לפי סטטוס", SI.STATUS_WEIGHT,
                                 "server/safety_intel.py"),
                           _item("risk_factors_layer.W_NIGHTLIFE", "אזור בילוי לילי (לילה)", RF.W_NIGHTLIFE,
                                 "server/risk_factors_layer.py", unit="נק'"),
                           _item("risk_factors_layer.W_NIGHTLIFE_WEEKEND", "אזור בילוי - לילה של סוף שבוע",
                                 RF.W_NIGHTLIFE_WEEKEND, "server/risk_factors_layer.py", unit="נק'"),
                           _item("risk_factors_layer.W_TRANSIT", "תחנת רכבת/רק\"ל בערב (ניסיוני, לא נספר ברמה)",
                                 RF.W_TRANSIT, "server/risk_factors_layer.py", unit="נק'"),
                           _item("risk_factors_layer.NIGHTLIFE_LEVEL_CAP_PER_KM", "תקרת בילוי ברמה המוצגת",
                                 RF.NIGHTLIFE_LEVEL_CAP_PER_KM, "server/risk_factors_layer.py", unit="נק'/ק\"מ"),
                           _item("news_layer.W_NEWS", "דיווח בתקשורת (לא מאומת)", NL.W_NEWS, "server/news_layer.py",
                                 unit="נק'"),
                           _item("news_layer.NEWS_SEGMENT_CAP", "תקרת חדשות למקטע", NL.NEWS_SEGMENT_CAP,
                                 "server/news_layer.py", unit="נק'"),
                           _item("news_layer.NEWS_LEVEL_CAP_PER_KM", "תקרת חדשות ברמה המוצגת", NL.NEWS_LEVEL_CAP_PER_KM,
                                 "server/news_layer.py", unit="נק'/ק\"מ"),
                           _item("news_layer.MAX_AGE_DAYS", "גיל מרבי לדיווח חדשות", NL.MAX_AGE_DAYS,
                                 "server/news_layer.py", unit="ימים"),
                           _item("safety_intel.LEVEL_HIGH_PER_KM", "סף רמה 'גבוה'", SI.LEVEL_HIGH_PER_KM,
                                 "server/safety_intel.py", unit="נק'/ק\"מ"),
                           _item("safety_intel.LEVEL_MEDIUM_PER_KM", "סף רמה 'בינוני'", SI.LEVEL_MEDIUM_PER_KM,
                                 "server/safety_intel.py", unit="נק'/ק\"מ"),
                           _item("safety_intel.SAFEST_MAX_RATIO", "safest/balanced: עד פי מזמן המהיר", SI.SAFEST_MAX_RATIO,
                                 "server/safety_intel.py"),
                           _item("safety_intel.BALANCED_MIN_PER_POINT", "balanced: דקות לכל נקודת סיכון",
                                 SI.BALANCED_MIN_PER_POINT, "server/safety_intel.py"),
                           _item("safety_intel.CORROBORATE_REPORTERS", "מדווחים שונים לאישוש דיווח",
                                 SI.CORROBORATE_REPORTERS, "server/safety_intel.py"),
                           _item("safety_intel.REPORT_RADIUS_M", "רדיוס דיווח", SI.REPORT_RADIUS_M,
                                 "server/safety_intel.py", unit="מ'"),
                           _item("safety_intel.REPORT_KINDS", "סוגי דיווח (תוקף בימים, משפיע על ניקוד)",
                                 {k: f"{v[0]} · {v[1]} ימים · {'משפיע' if v[2] else 'לא משפיע'}"
                                  for k, v in SI.REPORT_KINDS.items()}, "server/safety_intel.py"),
                           _item("layers.safety_layers", "שכבות רשמיות (תאונות/מקלטים) בשרת הזה",
                                 "נטען" if layers_ok else "לא נבנה בשרת הזה", "models/safety_layers.json.gz"),
                           _item("layers.risk_factors", "רשת גורמי סיכון (בילוי/תחנות) בשרת הזה",
                                 "נטען" if rf_ok else "לא נבנה בשרת הזה", "models/risk_factors_grid.json.gz")]})

        groups.append({"id": "weather", "title": "מזג אוויר (הקשר בלבד)",
                       "summary": f"{WX.SOURCE} ({WX.LICENSE}). {WX.LABEL_HE}.",
                       "items": [
                           _item("weather.AFFECTS_SCORE", "משפיע על ניקוד הסיכון", WX.AFFECTS_SCORE, "server/weather.py"),
                           _item("weather.TTL_S", "זמן מטמון", WX.TTL_S, "server/weather.py", unit="שנ'"),
                           _item("weather.HEAVY_RAIN_MM_H", "גשם חזק", WX.HEAVY_RAIN_MM_H, "server/weather.py", unit="מ\"מ/שעה"),
                           _item("weather.FOG_VISIBILITY_M", "ערפל - ראות מתחת", WX.FOG_VISIBILITY_M, "server/weather.py",
                                 unit="מ'"),
                           _item("weather.GALE_GUST_KMH", "משבי רוח סוערים", WX.GALE_GUST_KMH, "server/weather.py",
                                 unit='קמ"ש'),
                           _item("weather.HEAT_C", "עומס חום", WX.HEAT_C, "server/weather.py", unit="°C")]})

        groups.append(self._engine_safe_places())

        approx_step = inspect.signature(mod._approx).parameters["step"].default if hasattr(mod, "_approx") else None
        groups.append({"id": "privacy", "title": "כללי פרטיות",
                       "summary": "לכל איש קשר מצב מיקום: always (חי), emergency (רק בחירום - ברירת מחדל), off (התראה בלי "
                                  "מיקום). מתנדב רואה מרחק ואזור משוער עד שקיבל את האירוע; מיקום מדויק רק אחרי קבלה, והגישה "
                                  "נסגרת בסיום. הקלטות רק באירוע מאושר, רק אם המשתמש/ת אפשר/ה, מוצפנות, בלי האזנה חיה ובלי "
                                  "הפעלה מרחוק. יומן ביקורת append-only בשרשרת hash.",
                       "items": [
                           _item("privacy.LOCATION_MODES", "מצבי מיקום לאיש קשר", privacy.LOCATION_MODES, "server/privacy.py"),
                           _item("privacy.STALE_SECONDS", "מיקום מסומן ישן אחרי", privacy.STALE_SECONDS,
                                 "server/privacy.py", unit="שנ'"),
                           _item("server._approx.step", "עיגול מיקום למתנדב שלא קיבל (מעלות)", approx_step, S),
                           _item("privacy.RECORDING_RETENTION_DAYS", "שמירת הקלטות", privacy.RECORDING_RETENTION_DAYS,
                                 "server/privacy.py", unit="ימים"),
                           _item("privacy.RECORDING_MAX_BYTES", "גודל הקלטה מרבי", privacy.RECORDING_MAX_BYTES,
                                 "server/privacy.py", unit="בתים"),
                           _item("privacy.RECORDING_LATE_UPLOAD_HOURS", "העלאה מאוחרת של הקלטה עד",
                                 privacy.RECORDING_LATE_UPLOAD_HOURS, "server/privacy.py", unit="שעות"),
                           _item("national.OFFICIAL_NOTE", "חירום לאומי - מקור מחייב", national.OFFICIAL_NOTE,
                                 "server/national.py")]})

        ac = parse_acoustic_benchmark()
        groups.append({"id": "acoustic", "title": "זיהוי מצוקה קולית (אקוסטי)",
                       "summary": "הזיהוי רץ במכשיר (acoustic_ai_module.py באפליקציית Windows), לא בשרת. לשרת מגיע רק "
                                  "trigger='acoustic' עם ציון ביטחון, ועליו חלים ספי ההפעלה שלמעלה. התוצאות כאן - מבחן "
                                  "השוואתי על FSD50K (research/acoustic_benchmark_fsd50k.md).",
                       "items": [_item("acoustic.benchmark", "תוצאות המבחן", ac if ac else
                                       "המסמך לא נמצא או לא ניתן לפענוח - ראו research/acoustic_benchmark_fsd50k.md",
                                       "research/acoustic_benchmark_fsd50k.md")],
                       "benchmark": ac})
        return {"groups": groups, "note": "כל הערכים מיובאים מהקוד בזמן הבקשה (לא מועתקים). 'בשרת הזה' = הערך "
                                          "שבו השרת הנוכחי רץ בפועל (יכול להיות שונה מברירת המחדל בקוד)."}

    def _engine_safe_places(self):
        names = ("OFFICIAL_IDS", "OFFICIAL_PREFERRED_M", "PUBLIC_RADIUS_M", "SEARCH_DEADLINE_S", "WALK_FACTOR")
        c = _module_constants(MAP_MODULE_PATH, names)
        items = []
        if c is None:
            items.append(_item("map_module", "קבועי החיפוש", None, "map_module.py",
                               desc="map_module.py לא קיים בשרת הזה (בפריסת Docker רק server/ מועתק) - הערכים לא זמינים"))
        else:
            labels = {"OFFICIAL_IDS": ("סוגים רשמיים", ""), "OFFICIAL_PREFERRED_M": ("רשמי עדיף עד", "מ'"),
                      "PUBLIC_RADIUS_M": ("מקום ציבורי רלוונטי עד", "מ'"),
                      "SEARCH_DEADLINE_S": ("זמן חיפוש מרבי ב-SOS", "שנ'"),
                      "WALK_FACTOR": ("מקדם הליכה ביחס לקו אווירי (בלי ניתוב)", "")}
            for n in names:
                items.append(_item("map_module." + n, labels[n][0], c.get(n), "map_module.py", unit=labels[n][1]))
        if os.path.exists(SAFE_PLACES_PATH):
            with open(SAFE_PLACES_PATH, encoding="utf-8") as fh:
                sp = json.load(fh)
            places = sp.get("places") or []
            by_type = {}
            for p in places:
                by_type[p.get("type") or "—"] = by_type.get(p.get("type") or "—", 0) + 1
            items += [_item("safe_places.count", "מקומות ברשימה המובנית", len(places), "safe_places/safe_places_il.json"),
                      _item("safe_places.official", "מתוכם רשמיים", sum(1 for p in places if p.get("official")),
                            "safe_places/safe_places_il.json"),
                      _item("safe_places.by_type", "לפי סוג", by_type, "safe_places/safe_places_il.json"),
                      _item("safe_places.built_at", "נבנתה ב", sp.get("built_at"), "safe_places/safe_places_il.json"),
                      _item("safe_places.sources", "מקורות", [s.get("name") for s in sp.get("sources") or []],
                            "safe_places/safe_places_il.json")]
        else:
            items.append(_item("safe_places.count", "רשימה מובנית", None, "safe_places/safe_places_il.json",
                               desc="הקובץ לא קיים בשרת הזה"))
        return {"id": "safe_places", "title": "חיפוש מקום בטוח",
                "summary": "החיפוש רץ באפליקציה (map_module.py): קודם הרשימה המובנית (תחנות כיבוי, מנחתי בתי חולים, "
                           "OSM) ומטמון מקומי, ובמקביל Overpass/Nominatim עם תקרת זמן. מקום רשמי (משטרה/בית חולים/כבאות) "
                           "קרוב עדיף על כל מקום ציבורי; מקום ציבורי פתוח נחשב רק אם הוא באמת קרוב. בלי ניתוב אמיתי - "
                           "זמן הליכה מוערך מקו אווירי × מקדם.",
                "items": items}

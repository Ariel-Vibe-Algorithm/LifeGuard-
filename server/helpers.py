# -*- coding: utf-8 -*-
"""
helpers.py - מתנדבים שלא חייבים להיות מד"א: שיחת תמיכה מרחוק, ומי שמזעיק רשויות בשבילך.

שלושה תפקידים (volunteers.roles):
- on_scene: מגיע/ה פיזית (הקיים - server/lifeguard_server.py + volunteer_match.py). לא משתנה כאן.
- remote_support: שיחה בצ'אט - להרגיע, להישאר "על הקו", לעזור לחשוב. מכל מקום. **לא רואה שם ולא מיקום**
  של הפונה. דרישות: חשבון בן 3 ימים לפחות, לא מושעה.
- authority_liaison: מזעיק/ה משטרה/מד"א/כבאות בשביל מי שלא יכול/ה (מצוקה שקטה, שפה, הלם). מכל מקום.
  רואה שם ומיקום (צריך למסור לרשויות) - לכן **חייב/ת אימות** (verification=verified). האימות לא חייב להיות
  מארגון: מנהל/ת יכול/ה לאשר לפי בדיקת זהות ידנית ("אימות זהות"). כל גישה למיקום נרשמת ביומן הביקורת.

שני סוגי פנייה:
1. בתוך אירוע חירום: בעל/ת האירוע מבקש/ת "מישהו לדבר איתו" או "שמישהו יזעיק משטרה בשבילי".
   מתנדב/ת קישור נשלח/ת גם אוטומטית אם אחרי 90 שניות באירוע מאושר אף אחד (איש קשר/מתנדב) לא קיבל.
2. בלי חירום: "צריך/ה לדבר עם מישהו" - בקשת תמיכה (activation='support'): בלי מיקום, בלי אנשי קשר,
   בלי מתנדבים בשטח, בלי הקלטות; רק מתנדבי תמיכה + מספרי קווי סיוע רשמיים.

טבלה נפרדת (helpers) - לא משנה את תור המתנדבים בשטח ולא את הבדיקות הקיימות שלו.
"""

import json
import secrets
import time

VOL_ROLES = ("on_scene", "remote_support", "authority_liaison")
HELPER_KINDS = ("remote_support", "authority_liaison")
ROLE_HE = {"on_scene": "הגעה למקום", "remote_support": "שיחת תמיכה מרחוק", "authority_liaison": "הזעקת רשויות"}
LANGS = ("he", "ar", "en", "ru", "am", "fr", "es")
FANOUT = 2
MAX_OFFERS = 12
LIAISON_AUTO_AFTER_S = 90
MIN_ACCOUNT_AGE_S = 3 * 86400
LIAISON_ACTIONS = {"called_police": "הזעיק/ה משטרה (100)", "called_mda": "הזעיק/ה מד\"א (101)",
                   "called_fire": "הזעיק/ה כבאות (102)", "reported_other": "דיווח/ה לגורם רשמי אחר",
                   "could_not_reach": "לא הצליח/ה ליצור קשר עם הרשויות - ממשיך/ה לנסות"}
HOTLINES = [
    {"name": "ער\"ן - עזרה ראשונה נפשית", "number": "1201", "note": "24/7, גם בצ'אט באתר"},
    {"name": "סה\"ר - סיוע והקשבה ברשת", "number": None, "url": "https://sahar.org.il", "note": "צ'אט אנונימי"},
    {"name": "נט\"ל - טראומה על רקע לאומי", "number": "1-800-363-363", "note": None},
    {"name": "מרכזי סיוע לנפגעות ולנפגעי תקיפה מינית", "number": "1202 / 1203", "note": "נשים / גברים"},
    {"name": "מוקד 105 - הגנה על ילדים ברשת", "number": "105", "note": None},
    {"name": "משטרה", "number": "100", "note": "בסכנה מיידית"},
]
HOTLINES_NOTE = "מספרי הקווים יש לאמת לפני הפצה לציבור."
GUIDANCE = {
    "remote_support": ["הקשיבו ושקפו - בלי שיפוט ובלי להבטיח הבטחות", "אל תבקשו פרטים מזהים או מיקום",
                       "אם יש סכנה מיידית - הציעו לפנות ל-100/101 או לבקש 'מתנדב/ת הזעקה'",
                       "אינכם מטפלים מקצועיים: אפשר להפנות לקווי הסיוע (ער\"ן 1201 ועוד)"],
    "authority_liaison": ["התקשרו מיד ל-100 (משטרה) או 101 (מד\"א) לפי המצב", "מסרו: מיקום מדויק, מה ידוע, "
                          "שהפונה לא יכול/ה לדבר (אם במצוקה שקטה)", "עדכנו כאן בכפתורים מה עשיתם - הפונה ואנשי "
                          "הקשר שלו/ה רואים את זה", "אל תגיעו בעצמכם למקום אלא אם אתם גם מתנדבים בשטח"],
}

MIGRATION = """
ALTER TABLE volunteers ADD COLUMN roles TEXT NOT NULL DEFAULT '["on_scene"]';
ALTER TABLE volunteers ADD COLUMN languages TEXT NOT NULL DEFAULT '["he"]';
CREATE TABLE helpers(
    incident_id TEXT NOT NULL, username TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('remote_support','authority_liaison')),
    state TEXT NOT NULL CHECK(state IN ('notified','accepted','declined','expired','revoked','done')),
    notified_at REAL NOT NULL, responded_at REAL, last_action TEXT,
    PRIMARY KEY(incident_id, username, kind));
CREATE INDEX IF NOT EXISTS idx_helpers_user ON helpers(username, state);
CREATE TABLE helper_requests(
    incident_id TEXT NOT NULL, kind TEXT NOT NULL, requested_at REAL NOT NULL, requested_by TEXT,
    reason TEXT, offers INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(incident_id, kind));
"""


class HelpersMixin:
    # ---------------------------------------------------------------- הגדרות מתנדב
    def set_volunteer_roles(self, user, roles, languages=None):
        roles = [r for r in dict.fromkeys(roles or []) if r in VOL_ROLES]
        if not roles:
            raise self._err(400, "invalid_roles", "יש לבחור לפחות תפקיד אחד")
        langs = [l for l in dict.fromkeys(languages or ["he"]) if l in LANGS] or ["he"]
        if not self._q("SELECT 1 FROM volunteers WHERE username=?", (user,), one=True):
            self.set_volunteer(user, False)
        self._x("UPDATE volunteers SET roles=?, languages=? WHERE username=?",
                (json.dumps(roles), json.dumps(langs), user))
        self._wake_dispatch()
        return self.volunteer_roles(user)

    def volunteer_roles(self, user):
        v = self._q("SELECT * FROM volunteers WHERE username=?", (user,), one=True)
        if not v:
            return {"roles": [], "languages": ["he"], "effective": {}, "available": False}
        roles = json.loads(v["roles"] or '["on_scene"]')
        eff = {r: self._role_status(v, r) for r in roles}
        # available: תפקיד "פעיל" לא שווה כלום אם המתנדב/ת סימן/ה "לא זמין/ה" - הלקוח מציג זאת במפורש
        return {"roles": roles, "languages": json.loads(v["languages"] or '["he"]'), "effective": eff,
                "labels": ROLE_HE, "available": bool(v["available"])}

    def _role_status(self, v, role):
        """-> 'active' / סיבה בעברית למה התפקיד עוד לא פעיל."""
        if v["suspended"]:
            return "מושעה/ית"
        if role == "authority_liaison" and v["verification"] != "verified":
            return "ממתין לאימות (אימות זהות ע\"י מנהל/ת - לא חייב להיות ארגון)"
        if role == "remote_support":
            u = self._q("SELECT created_at FROM users WHERE username=?", (v["username"],), one=True)
            if u and time.time() - u["created_at"] < MIN_ACCOUNT_AGE_S:
                return "זמין 3 ימים אחרי פתיחת החשבון"
        return "active"

    # ---------------------------------------------------------------- בקשות
    def request_helper(self, user, iid, kind, reason="", auto=False):
        if kind not in HELPER_KINDS:
            raise self._err(400, "invalid_kind", "סוג עזרה לא מוכר")
        inc, role, _ = self._access(user, iid) if not auto else (self._incident_row(iid), "owner", None)
        if role != "owner":
            raise self._err(403, "forbidden", "רק מי שבמצוקה מבקש/ת עזרה כזו")
        if inc["status"] != "active":
            raise self._err(409, "closed", "האירוע הסתיים")
        if kind == "authority_liaison" and inc["activation"] == "support":
            raise self._err(409, "not_emergency", "בבקשת שיחה אין הזעקת רשויות - בסכנה לחצו SOS")
        if kind == "authority_liaison" and inc["activation"] == "unconfirmed" and not auto:
            self.confirm_incident(user, iid)      # בקשה מפורשת להזעיק רשויות = אישור שזה חירום
        now = time.time()
        self._x("INSERT OR IGNORE INTO helper_requests(incident_id, kind, requested_at, requested_by, reason) "
                "VALUES (?,?,?,?,?)", (iid, kind, now, "auto" if auto else user, str(reason or "")[:200]))
        self._event(iid, "helper_requested", ("נשלחה אוטומטית " if auto else "") +
                    f"בקשה ל{ROLE_HE[kind]}", None if auto else user)
        self.audit("helper_requested", actor="system" if auto else user, subject=inc["owner"], incident_id=iid,
                   details={"kind": kind, "auto": auto})
        self._dispatch_helpers(iid, kind)
        return self._view_for(iid, inc["owner"], include_medical=True) if not auto else None

    def create_support_request(self, user, note="", language="he"):
        """'צריך/ה לדבר עם מישהו' - לא חירום: בלי מיקום, בלי אנשי קשר, בלי מתנדבים בשטח."""
        open_req = self._q("SELECT id FROM incidents WHERE owner=? AND status='active' AND activation='support'",
                           (user,), one=True)
        if open_req:                       # בקשה אחת פתוחה: חוזרים אליה (למשל אחרי הפעלה מחדש של האפליקציה)
            if str(note or "").strip():    # מה שנכתב עכשיו לא הולך לאיבוד - נכנס לצ'אט של הבקשה הפתוחה
                self.add_message(user, open_req["id"], str(note).strip()[:500])
            view = self._view_for(open_req["id"], user, include_medical=False)
            view["hotlines"], view["hotlines_note"], view["resumed"] = HOTLINES, HOTLINES_NOTE, True
            return view
        iid = "inc_" + secrets.token_hex(16)
        now = time.time()
        self._x("""INSERT INTO incidents(id, owner, client_id, mode, emergency_type, severity, status, profile,
                   created_at, updated_at, trigger_kind, activation) VALUES (?,?,?,'silent','emotional_support','low',
                   'active','{}',?,?,'manual','support')""", (iid, user, "support:" + secrets.token_hex(6), now, now))
        self._event(iid, "created", "בקשת שיחה (לא חירום)", user)
        if str(note or "").strip():
            self._x("INSERT INTO messages(incident_id, sender, text, created_at) VALUES (?,?,?,?)",
                    (iid, user, str(note).strip()[:500], now))
        self.audit("support_requested", actor=user, subject=user, incident_id=iid, details={"language": language})
        self._x("INSERT INTO helper_requests(incident_id, kind, requested_at, requested_by, reason) VALUES (?,?,?,?,?)",
                (iid, "remote_support", now, user, str(language or "he")[:5]))
        self._dispatch_helpers(iid, "remote_support")
        view = self._view_for(iid, user, include_medical=False)
        view["hotlines"], view["hotlines_note"] = HOTLINES, HOTLINES_NOTE
        return view

    def _dispatch_helpers(self, iid, kind, n=FANOUT):
        req = self._q("SELECT * FROM helper_requests WHERE incident_id=? AND kind=?", (iid, kind), one=True)
        inc = self._incident_row(iid)
        if not req or inc["status"] != "active":
            return []
        if self._q("SELECT 1 FROM helpers WHERE incident_id=? AND kind=? AND state='accepted'", (iid, kind), one=True):
            return []
        pending = self._q("SELECT COUNT(*) AS n FROM helpers WHERE incident_id=? AND kind=? AND state='notified'",
                          (iid, kind), one=True)["n"]
        need = max(0, n - pending)
        if not need or req["offers"] >= MAX_OFFERS:
            return []
        tried = {r["username"] for r in self._q("SELECT username FROM helpers WHERE incident_id=?", (iid,))}
        tried |= {r["username"] for r in self._q("SELECT username FROM recipients WHERE incident_id=?", (iid,))}
        busy = {r["username"] for r in self._q(
            """SELECT h.username FROM helpers h JOIN incidents i ON i.id=h.incident_id
               WHERE i.status='active' AND h.state IN ('notified','accepted')""")}
        lang = req["reason"] if inc["activation"] == "support" else None
        cands = []
        for v in self._q("SELECT * FROM volunteers WHERE available=1 AND suspended=0"):
            u = v["username"]
            if u == inc["owner"] or u in tried or u in busy:
                continue
            if kind not in json.loads(v["roles"] or "[]") or self._role_status(v, kind) != "active":
                continue
            if not self.identity_ok(u):
                continue                         # גם תמיכה מרחוק / קשר לרשויות - רק אחרי אימות זהות
            langs = json.loads(v["languages"] or '["he"]')
            cands.append((0 if (not lang or lang in langs) else 1, 0 if v["verification"] == "verified" else 1,
                          secrets.randbelow(1000), u))
        cands.sort()
        chosen = [c[3] for c in cands[:need]]
        now = time.time()
        for u in chosen:
            self._x("INSERT OR IGNORE INTO helpers(incident_id, username, kind, state, notified_at) "
                    "VALUES (?,?,?,'notified',?)", (iid, u, kind, now))
            payload = self._helper_view(iid, u)
            payload["ring"] = kind == "authority_liaison"       # הזעקה - צלצול; שיחה - התראה רגילה
            self._notify(u, "incident_new", payload, iid)
        if chosen:
            self._x("UPDATE helper_requests SET offers=offers+? WHERE incident_id=? AND kind=?", (len(chosen), iid, kind))
            self._event(iid, "helper_notified", f"נשלח ל-{len(chosen)} מתנדבי {ROLE_HE[kind]}")
        elif not pending:
            self._event(iid, "helper_none", f"אין כרגע מתנדבי {ROLE_HE[kind]} זמינים")
        return chosen

    # ---------------------------------------------------------------- תגובות המתנדב/ת
    def _helper_row(self, iid, user):
        return self._q("SELECT * FROM helpers WHERE incident_id=? AND username=? AND state IN ('notified','accepted') "
                       "ORDER BY state='accepted' DESC LIMIT 1", (iid, user), one=True)

    def helper_respond(self, user, iid, action):
        h = self._helper_row(iid, user)
        inc = self._incident_row(iid)
        if not h or not inc or inc["status"] != "active":
            raise self._err(404, "not_found", "הבקשה לא נמצאה")
        kind, now = h["kind"], time.time()
        if action == "accept":
            cur = self._x("UPDATE helpers SET state='accepted', responded_at=? WHERE incident_id=? AND username=? "
                          "AND kind=? AND state='notified'", (now, iid, user, kind))
            if not cur.rowcount:
                raise self._err(409, "taken", "מישהו/י אחר/ת כבר לקח/ה את הבקשה")
            for o in self._q("SELECT username FROM helpers WHERE incident_id=? AND kind=? AND state='notified'",
                             (iid, kind)):
                self._x("UPDATE helpers SET state='revoked' WHERE incident_id=? AND username=? AND kind=?",
                        (iid, o["username"], kind))
                self._notify(o["username"], "incident_closed", {"incident_id": iid, "reason": "taken"}, iid)
            who = self._display(user) if kind == "authority_liaison" else "מתנדב/ת תמיכה"
            self._event(iid, "helper_accepted", f"{who} הצטרף/ה ({ROLE_HE[kind]})", user)
            self._broadcast_update(iid, "helper", {"kind": kind, "action": "accept"}, exclude=user)
            self.audit("helper_accepted", actor=user, subject=inc["owner"], incident_id=iid, details={"kind": kind})
        elif action == "decline":
            self._x("UPDATE helpers SET state='declined', responded_at=? WHERE incident_id=? AND username=? AND kind=?",
                    (now, iid, user, kind))
            self._dispatch_helpers(iid, kind)
        elif action == "done":
            self._x("UPDATE helpers SET state='done', responded_at=? WHERE incident_id=? AND username=? AND kind=?",
                    (now, iid, user, kind))
            self._event(iid, "helper_done", f"{ROLE_HE[kind]}: סיים/ה את ההשתתפות", user)
        elif action in LIAISON_ACTIONS:
            if kind != "authority_liaison" or h["state"] != "accepted":
                raise self._err(403, "forbidden", "רק מתנדב/ת הזעקה שקיבל/ה את הבקשה")
            self._x("UPDATE helpers SET last_action=? WHERE incident_id=? AND username=? AND kind=?",
                    (action, iid, user, kind))
            text = f"{self._display(user)} {LIAISON_ACTIONS[action]}"
            self._event(iid, "liaison_" + action, text, user)
            self._broadcast_update(iid, "liaison", {"action": action, "text": text}, exclude=user)
            self.audit("authority_contacted" if action != "could_not_reach" else "authority_contact_failed",
                       "completed" if action != "could_not_reach" else "failed", actor=user, subject=inc["owner"],
                       incident_id=iid, details={"action": action})
        else:
            raise self._err(400, "invalid_action", "פעולה לא חוקית")
        return self._helper_view(iid, user)

    def release_helper(self, owner, iid, helper):
        inc, role, _ = self._access(owner, iid)
        if role != "owner":
            raise self._err(403, "forbidden", "רק בעל/ת האירוע")
        self._x("UPDATE helpers SET state='revoked' WHERE incident_id=? AND username=? AND state IN ('notified','accepted')",
                (iid, helper))
        self._notify(helper, "incident_closed", {"incident_id": iid, "reason": "released"}, iid)
        self.audit("helper_released", actor=owner, subject=owner, recipient=helper, incident_id=iid)
        return self._view_for(iid, owner, include_medical=True)

    # ---------------------------------------------------------------- תצוגה
    def _helper_view(self, iid, user):
        inc = self._incident_row(iid)
        h = self._helper_row(iid, user)
        kind = h["kind"] if h else None
        accepted = bool(h and h["state"] == "accepted")
        support = inc["activation"] == "support"
        view = {"id": iid, "status": inc["status"], "created_at": inc["created_at"], "mode": inc["mode"],
                "emergency_type": inc["emergency_type"], "my_role": "helper", "helper_kind": kind,
                "helper_kind_label": ROLE_HE.get(kind), "my_state": h["state"] if h else None,
                "support_request": support, "guidance": GUIDANCE.get(kind, []), "hotlines": HOTLINES,
                "owner": None, "owner_display": "מישהו צריך לדבר" if support else "מישהו במצוקה"}
        if kind == "authority_liaison" and accepted:
            view["owner_display"] = self._display(inc["owner"])
            loc = self._latest_location(iid)
            if loc:
                view["location"] = dict(loc)
                view["location"]["stale"] = time.time() - (loc["received_at"] or 0) > 300
                self._note_location_access(user, inc["owner"], "authority_liaison", iid, "liaison_assigned")
            view["profile"] = json.loads(inc["profile"] or "{}")
            view["liaison_actions"] = LIAISON_ACTIONS
            view["last_action"] = h["last_action"]                 # מה דווח לאחרונה (גם אחרי טעינה מחדש)
            view["last_action_label"] = LIAISON_ACTIONS.get(h["last_action"])
        if accepted:
            view["messages"] = self._messages_for_helper(iid, inc, kind)
        return view

    def _helpers_for_owner(self, iid):
        return [{"username": r["username"], "display_name": self._display(r["username"]), "kind": r["kind"],
                 "kind_label": ROLE_HE[r["kind"]], "state": r["state"], "last_action": r["last_action"],
                 "last_action_label": LIAISON_ACTIONS.get(r["last_action"])}
                for r in self._q("SELECT * FROM helpers WHERE incident_id=? AND state IN ('notified','accepted','done') "
                                 "ORDER BY notified_at", (iid,))]

    # ---------------------------------------------------------------- טיימרים
    def helpers_tick(self, now=None):
        now = now or time.time()
        for r in self._q("""SELECT h.incident_id, h.username, h.kind FROM helpers h JOIN incidents i ON i.id=h.incident_id
                            WHERE i.status='active' AND h.state='notified' AND h.notified_at<?""",
                         (now - self.ack_timeout,)):
            cur = self._x("UPDATE helpers SET state='expired' WHERE incident_id=? AND username=? AND kind=? "
                          "AND state='notified'", (r["incident_id"], r["username"], r["kind"]))
            if cur.rowcount:
                self._notify(r["username"], "incident_closed", {"incident_id": r["incident_id"], "reason": "expired"},
                             r["incident_id"])
                self._dispatch_helpers(r["incident_id"], r["kind"])
        # הזעקה אוטומטית: אירוע מאושר, 90 שניות ואף אחד לא קיבל (לא איש קשר ולא מתנדב) -> מתנדב/ת הזעקה
        for inc in self._q("""SELECT i.id FROM incidents i WHERE i.status='active'
                              AND i.activation IN ('manual','confirmed_high','confirmed_by_user','server_timer')
                              AND i.created_at < ?
                              AND NOT EXISTS(SELECT 1 FROM recipients r WHERE r.incident_id=i.id AND r.state='accepted')
                              AND NOT EXISTS(SELECT 1 FROM helper_requests q WHERE q.incident_id=i.id
                                             AND q.kind='authority_liaison')""", (now - LIAISON_AUTO_AFTER_S,)):
            if self._q("SELECT 1 FROM volunteers WHERE available=1 AND suspended=0 AND roles LIKE "
                       "'%authority_liaison%' AND verification='verified'", one=True):
                self.request_helper(None, inc["id"], "authority_liaison", "אף אחד לא קיבל את הקריאה", auto=True)

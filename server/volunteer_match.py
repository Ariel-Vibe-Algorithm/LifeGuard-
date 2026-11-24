# -*- coding: utf-8 -*-
"""
volunteer_match.py - שיבוץ מתנדבים מתקדם: הכשרות מאומתות, אמינות מהיסטוריה, התאמה לסוג החירום.

סדר ההחלטה (בחירום מהירות ההגעה קודמת לכל):
  1. רצועת זמן הליכה (10 דק', כמו קודם) - מתנדב שמגיע בעוד 4 דק' לא יוחלף במומחה שמגיע בעוד 20.
  2. בתוך הרצועה: הכשרה *מאומתת* שמתאימה לסוג החירום (חובש/ת לאירוע רפואי, כבאי/ת לשריפה...) >
     כישור שהוצהר (לא מאומת) > בלי התאמה.
  3. מתנדב/ת מאומת/ת.
  4. אמינות: מי שבדרך כלל עונה ומגיע/ה, ודירוג טוב - לפני מי שמתעלם/ת מקריאות. חדש/ה = "לא ידוע",
     לא "גרוע" (לא מענישים מתנדבים חדשים).
  5. זמן ההגעה עצמו.

הכשרה מאומתת: המתנדב/ת מגיש/ה (ארגון + מספר תעודה), מנהל/ת בודק/ת מול הארגון ומאשר/ת
(python -m server.admin quals / qual-verify). בלי אישור - "מוצהר" בלבד, משקל נמוך יותר.
"""

import time

QUALS = {"first_aid": "עזרה ראשונה", "emt": "חובש/ת", "paramedic": "פרמדיק/ית", "doctor": "רופא/ה",
         "nurse": "אח/ות", "firefighter": "כבאי/ת", "security": "ביטחון / שיטור", "search_rescue": "חיפוש והצלה",
         "mental_health": "עזרה נפשית ראשונה"}
MEDICAL = {"first_aid", "emt", "paramedic", "doctor", "nurse"}
TYPE_QUALS = {"medical": MEDICAL, "road_accident": MEDICAL, "fire": {"firefighter"} | MEDICAL,
              "physical_danger": {"security"}, "security": {"security"}, "missing_person": {"search_rescue"},
              "lost": {"search_rescue"}, "earthquake": {"search_rescue"} | MEDICAL,
              "flood_weather": {"search_rescue"}}
RELIABLE, UNKNOWN, POOR = 0, 1, 2
MIN_OFFERS = 5

MIGRATION = """
CREATE TABLE volunteer_qualifications(
    username TEXT NOT NULL, qual TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','verified','rejected')),
    org TEXT, ref TEXT, requested_at REAL NOT NULL, decided_at REAL, note TEXT,
    PRIMARY KEY(username, qual));
"""


def match_tier(emergency_type, verified_quals, declared_skills):
    need = TYPE_QUALS.get(emergency_type, set())
    if need & set(verified_quals or ()):
        return 0
    if need & set(declared_skills or ()):
        return 1
    return 2


def reliability(stats):
    """stats: offers, accepted, arrived, expired, declined, ratings, avg_rating, reports -> (bucket, why)."""
    if stats.get("reports", 0) >= 2:
        return POOR, "דיווחים על התנהגות"
    if stats.get("ratings", 0) >= 3 and (stats.get("avg_rating") or 5) < 3:
        return POOR, "דירוג נמוך"
    offers = stats.get("offers", 0)
    if offers < MIN_OFFERS:
        return UNKNOWN, "מעט היסטוריה"
    answered = (stats.get("accepted", 0) + stats.get("declined", 0)) / offers
    if stats.get("accepted", 0) / offers < 0.2 and answered < 0.5:
        return POOR, "בדרך כלל לא עונה לקריאות"
    return RELIABLE, "עונה לקריאות"


class VolunteerMatchMixin:
    # ---------- הכשרות ----------
    def request_qualification(self, user, qual, org, ref):
        if qual not in QUALS:
            raise self._err(400, "invalid_qual", "הכשרה לא מוכרת")
        org, ref = str(org or "").strip()[:80], str(ref or "").strip()[:60]
        if len(org) < 2 or len(ref) < 2:
            raise self._err(400, "invalid", "יש לציין ארגון מנפיק ומספר תעודה")
        cur = self._q("SELECT status FROM volunteer_qualifications WHERE username=? AND qual=?", (user, qual),
                      one=True)
        if cur and cur["status"] == "verified":
            raise self._err(409, "already_verified", "ההכשרה כבר מאומתת")
        self._x("""INSERT INTO volunteer_qualifications(username, qual, status, org, ref, requested_at)
                   VALUES (?,?,'pending',?,?,?) ON CONFLICT(username, qual) DO UPDATE SET status='pending',
                   org=excluded.org, ref=excluded.ref, requested_at=excluded.requested_at, decided_at=NULL,
                   note=NULL""", (user, qual, org, ref, time.time()))
        return self.my_qualifications(user)

    def my_qualifications(self, user):
        return [{"qual": r["qual"], "label": QUALS.get(r["qual"], r["qual"]), "status": r["status"],
                 "org": r["org"], "requested_at": r["requested_at"], "decided_at": r["decided_at"],
                 "note": r["note"]}
                for r in self._q("SELECT * FROM volunteer_qualifications WHERE username=? ORDER BY qual", (user,))]

    def admin_qualifications(self, status="pending"):
        rows = self._q("SELECT * FROM volunteer_qualifications" + (" WHERE status=?" if status else "")
                       + " ORDER BY requested_at", (status,) if status else ())
        return [dict(r, label=QUALS.get(r["qual"], r["qual"])) for r in rows]

    def admin_decide_qualification(self, username, qual, approve, note=""):
        cur = self._x("UPDATE volunteer_qualifications SET status=?, decided_at=?, note=? WHERE username=? AND qual=?",
                      ("verified" if approve else "rejected", time.time(), str(note or "")[:200], username, qual))
        if not cur.rowcount:
            raise self._err(404, "not_found", "בקשה לא נמצאה")
        self._notify(username, "volunteer_update", {"event": "qualification", "qual": qual,
                                                    "status": "verified" if approve else "rejected"})
        self._wake_dispatch()
        return self.my_qualifications(username)

    def _verified_quals_all(self):
        out = {}
        for r in self._q("SELECT username, qual FROM volunteer_qualifications WHERE status='verified'"):
            out.setdefault(r["username"], set()).add(r["qual"])
        return out

    # ---------- היסטוריה ואמינות ----------
    def _offer_stats_all(self, since):
        rows = self._q("""SELECT username, COUNT(*) AS offers,
                   SUM(CASE WHEN response IN ('accept','on_my_way','arrived','handled') THEN 1 ELSE 0 END) AS accepted,
                   SUM(CASE WHEN response='arrived' THEN 1 ELSE 0 END) AS arrived,
                   SUM(CASE WHEN state='declined' THEN 1 ELSE 0 END) AS declined,
                   SUM(CASE WHEN state='expired' THEN 1 ELSE 0 END) AS expired,
                   AVG(CASE WHEN responded_at IS NOT NULL THEN responded_at - notified_at END) AS avg_response_s
                   FROM recipients WHERE role='volunteer' AND notified_at>? GROUP BY username""", (since,))
        fb = {r["volunteer"]: r for r in self._q(
            """SELECT volunteer, COUNT(rating) AS ratings, AVG(rating) AS avg_rating,
               SUM(CASE WHEN report IS NOT NULL AND report!='' THEN 1 ELSE 0 END) AS reports
               FROM volunteer_feedback GROUP BY volunteer""")}
        out = {}
        for r in rows:
            out[r["username"]] = dict(r)
        for u, f in fb.items():
            d = out.setdefault(u, {"username": u, "offers": 0, "accepted": 0, "arrived": 0, "declined": 0,
                                   "expired": 0, "avg_response_s": None})
            d.update(ratings=f["ratings"] or 0, avg_rating=f["avg_rating"], reports=f["reports"] or 0)
        return out

    def volunteer_history(self, user, limit=50):
        rows = self._q("""SELECT r.incident_id, r.state, r.response, r.notified_at, r.responded_at, r.distance_km,
                          r.eta_seconds, i.emergency_type, i.status AS incident_status
                          FROM recipients r JOIN incidents i ON i.id=r.incident_id
                          WHERE r.username=? AND r.role='volunteer' ORDER BY r.notified_at DESC LIMIT ?""",
                       (user, int(limit)))
        stats = self._offer_stats_all(time.time() - 365 * 86400).get(user) or {
            "offers": 0, "accepted": 0, "arrived": 0, "declined": 0, "expired": 0, "avg_response_s": None}
        stats.update({k: stats.get(k, 0) or 0 for k in ("ratings", "reports")})
        bucket, why = reliability(stats)
        return {"assignments": [{"at": r["notified_at"], "emergency_type": r["emergency_type"], "state": r["state"],
                                 "response": r["response"],
                                 "response_seconds": round(r["responded_at"] - r["notified_at"])
                                 if r["responded_at"] else None,
                                 "distance_km": r["distance_km"], "incident_status": r["incident_status"]}
                                for r in rows],
                "stats": {k: stats.get(k) for k in ("offers", "accepted", "arrived", "declined", "expired",
                                                     "avg_response_s", "ratings", "avg_rating", "reports")},
                "reliability": {"bucket": ("reliable", "unknown", "poor")[bucket], "reason": why},
                "qualifications": self.my_qualifications(user)}

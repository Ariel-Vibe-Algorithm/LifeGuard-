# -*- coding: utf-8 -*-
"""
signal_watch.py - "אין קליטה": השרת שם לב כשהטלפון מפסיק לדווח, ומעדכן את מי שצריך לדעת.

הרעיון: כשאין קליטה, הטלפון לא יכול לבקש עזרה - אבל השרת יכול לשים לב לשתיקה.
- ליווי הביתה פעיל: הטלפון שולח מיקום כל דקה. אין דיווח SIGNAL_LOSS_SECONDS ->
  העוקבים מקבלים "אבד הקשר עם הטלפון של X", עם המיקום האחרון הידוע ושעתו. חוזר דיווח -> "הקשר חזר".
  (זו *לא* קריאת חירום אוטומטית: הרבה פעמים זו מעלית או חניון. אם הזמן של הליווי נגמר בלי אישור
  הגעה - מנגנון הליווי הקיים פותח קריאת חירום לפי המיקום האחרון.)
- אירוע חירום פעיל: אין עדכון מיקום INCIDENT_SIGNAL_LOSS_SECONDS -> כל המגיבים מקבלים
  "המיקום לא מתעדכן - ייתכן שאין קליטה; המיקום המוצג הוא האחרון הידוע מ-HH:MM". לעולם לא
  מציגים מיקום ישן כאילו הוא עדכני.
"""

SIGNAL_LOSS_SECONDS = 5 * 60
INCIDENT_SIGNAL_LOSS_SECONDS = 3 * 60

MIGRATION = """
ALTER TABLE escorts ADD COLUMN signal_lost_at REAL;
ALTER TABLE incidents ADD COLUMN signal_lost_at REAL;
"""


class SignalWatchMixin:
    signal_loss_seconds = SIGNAL_LOSS_SECONDS
    incident_signal_loss_seconds = INCIDENT_SIGNAL_LOSS_SECONDS

    def signal_tick(self, now):
        import time as _t
        for e in self._q("""SELECT * FROM escorts WHERE status='active' AND signal_lost_at IS NULL
                            AND COALESCE(last_loc_at, started_at) < ?""", (now - self.signal_loss_seconds,)):
            cur = self._x("UPDATE escorts SET signal_lost_at=? WHERE id=? AND signal_lost_at IS NULL", (now, e["id"]))
            if not cur.rowcount:
                continue
            last = e["last_loc_at"] or e["started_at"]
            when = _t.strftime("%H:%M", _t.localtime(last))
            text = (f"📵 אבד הקשר עם הטלפון של {self._display(e['owner'])} (ייתכן שאין קליטה). "
                    f"מיקום אחרון ידוע מ-{when}. אם אפשר - נסו ליצור קשר.")
            for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (e["id"],)):
                self._notify(w["username"], "escort_update", {"escort_id": e["id"], "event": "signal_lost",
                                                              "last_seen": last, "text": text})
        for inc in self._q("""SELECT i.id, i.owner, i.created_at, MAX(l.received_at) AS last FROM incidents i
                              LEFT JOIN incident_locations l ON l.incident_id=i.id
                              WHERE i.status='active' AND i.signal_lost_at IS NULL GROUP BY i.id""", ()):
            last = inc["last"] or inc["created_at"]
            if last >= now - self.incident_signal_loss_seconds:
                continue
            cur = self._x("UPDATE incidents SET signal_lost_at=? WHERE id=? AND signal_lost_at IS NULL", (now, inc["id"]))
            if not cur.rowcount:
                continue
            when = _t.strftime("%H:%M", _t.localtime(last))
            self._event(inc["id"], "signal_lost", f"המיקום לא מתעדכן מאז {when} - ייתכן שאין קליטה")
            self._broadcast_update(inc["id"], "signal_lost", {"last_seen": last}, exclude=inc["owner"])

    def signal_back(self, kind, rid):
        """נקרא כשמגיע דיווח חדש (מיקום ליווי / מיקום אירוע) אחרי שתיקה."""
        if kind == "escort":
            e = self._q("SELECT id, owner, signal_lost_at FROM escorts WHERE id=?", (rid,), one=True)
            if e and e["signal_lost_at"]:
                self._x("UPDATE escorts SET signal_lost_at=NULL WHERE id=?", (rid,))
                for w in self._q("SELECT username FROM escort_watchers WHERE escort_id=?", (rid,)):
                    self._notify(w["username"], "escort_update", {"escort_id": rid, "event": "signal_back",
                                 "text": f"✅ הקשר עם הטלפון של {self._display(e['owner'])} חזר"})
        else:
            inc = self._q("SELECT id, owner, signal_lost_at FROM incidents WHERE id=?", (rid,), one=True)
            if inc and inc["signal_lost_at"]:
                self._x("UPDATE incidents SET signal_lost_at=NULL WHERE id=?", (rid,))
                self._event(rid, "signal_back", "המיקום מתעדכן שוב")
                self._broadcast_update(rid, "signal_back", exclude=inc["owner"])

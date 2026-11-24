# -*- coding: utf-8 -*-
"""
evidence.py - רשומת ראיות חתומה לאירוע (חבילת ראיות דיגיטלית, צד השרת).

מה השרת יודע ויכול להעיד עליו: מתי נפתח האירוע, כל המיקומים שהתקבלו (עם זמן הקליטה בשרת), מי קיבל
התראה ומתי, מי אישר קבלה / ענה / הגיע, הודעות הצ'אט, ציר הזמן, והערכת הסכנה שנשלחה מהמכשיר.

שלמות: הרשומה מסודרת כ-JSON קנוני, מחושב לה SHA-256, ונחתמת ב-Ed25519 במפתח פרטי של השרת (נשמר מוצפן
ב-server_meta). כל אחד יכול לאמת עם המפתח הציבורי (GET /v1/evidence/public-key) - גם בלי גישה לשרת,
ש"לא שינו שורה" מאז ההפקה. האימות: verify_record() (משמש גם את האפליקציה השולחנית).

יומן גישה: כל הפקה של הרשומה נרשמת בטבלה append-only עם שרשרת hash (כל שורה מכילה hash של הקודמת) -
מחיקה/שינוי של שורה באמצע שוברים את השרשרת. היומן עצמו נכלל ברשומה החתומה.

פרטיות: רק בעל/ת האירוע מפיק/ה. מתנדב שלא קיבל את האירוע מופיע כ"מתנדב/ת" בלי שם משתמש.
"""

import base64
import hashlib
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

MIGRATION = """
CREATE TABLE evidence_access(
    id INTEGER PRIMARY KEY AUTOINCREMENT, incident_id TEXT NOT NULL, username TEXT NOT NULL,
    action TEXT NOT NULL, at REAL NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_evacc_inc ON evidence_access(incident_id, id);
ALTER TABLE incidents ADD COLUMN risk_confidence REAL;
ALTER TABLE incidents ADD COLUMN risk_signals TEXT NOT NULL DEFAULT '[]';
"""

FORMAT = "lifeguard-evidence-v1"
GENESIS = "0" * 64


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _chain_hash(prev, incident_id, username, action, at):
    return hashlib.sha256(f"{prev}|{incident_id}|{username}|{action}|{at:.6f}".encode("utf-8")).hexdigest()


def verify_chain(rows):
    prev = GENESIS
    for r in rows:
        if r["prev_hash"] != prev or r["hash"] != _chain_hash(prev, r["incident_id"], r["username"], r["action"],
                                                                r["at"]):
            return False
        prev = r["hash"]
    return True


def verify_record(doc, public_key_hex=None):
    """-> (ok, reason). מאמת hash, חתימה, ושרשרת יומן הגישה שבתוך הרשומה."""
    try:
        rec = doc["record"]
        body = canonical(rec)
        if hashlib.sha256(body).hexdigest() != doc["sha256"]:
            return False, "ה-hash לא תואם לתוכן - הרשומה שונתה"
        pk = bytes.fromhex(public_key_hex or doc["public_key"])
        Ed25519PublicKey.from_public_bytes(pk).verify(bytes.fromhex(doc["signature"]), body)
        if not verify_chain(rec.get("access_log") or []):
            return False, "שרשרת יומן הגישה שבורה"
        return True, "תקין"
    except InvalidSignature:
        return False, "החתימה לא תקינה"
    except (KeyError, ValueError, TypeError) as exc:
        return False, f"מבנה לא תקין: {exc}"


class EvidenceMixin:
    def _evidence_key(self):
        key = getattr(self, "_ev_key", None)
        if key is not None:
            return key
        row = self._q("SELECT value FROM server_meta WHERE key='evidence_ed25519'", one=True)
        if row:
            raw = base64.b64decode(self.crypto.decrypt(row["value"]))
            key = Ed25519PrivateKey.from_private_bytes(raw)
        else:
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                    serialization.NoEncryption())
            self._x("INSERT OR IGNORE INTO server_meta(key, value) VALUES ('evidence_ed25519', ?)",
                    (self.crypto.encrypt(base64.b64encode(raw).decode()),))
            # שני threads במקביל: מי שהספיק לכתוב ראשון - המפתח שלו הוא המפתח
            row = self._q("SELECT value FROM server_meta WHERE key='evidence_ed25519'", one=True)
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(self.crypto.decrypt(row["value"])))
        self._ev_key = key
        return key

    def evidence_public_key(self):
        pub = self._evidence_key().public_key().public_bytes(serialization.Encoding.Raw,
                                                             serialization.PublicFormat.Raw)
        return {"algorithm": "Ed25519", "public_key": pub.hex(), "format": FORMAT}

    def _log_evidence_access(self, iid, user, action):
        with self._lock:
            last = self._q("SELECT hash FROM evidence_access WHERE incident_id=? ORDER BY id DESC LIMIT 1", (iid,),
                           one=True)
            prev = last["hash"] if last else GENESIS
            at = round(time.time(), 6)
            self._x("INSERT INTO evidence_access(incident_id, username, action, at, prev_hash, hash) "
                    "VALUES (?,?,?,?,?,?)", (iid, user, action, at, prev, _chain_hash(prev, iid, user, action, at)))

    def evidence_by_client(self, user, client_id):
        row = self._q("SELECT id FROM incidents WHERE owner=? AND client_id=?", (user, str(client_id or "")[:100]),
                      one=True)
        if not row:
            raise self._err(404, "not_found", "האירוע לא הגיע לשרת")
        return self.evidence_record(user, row["id"])

    def evidence_record(self, user, iid):
        inc, role, _ = self._access(user, iid)
        if role != "owner":
            raise self._err(404, "not_found", "האירוע לא נמצא")
        self._log_evidence_access(iid, user, "export")
        accepted = {r["username"] for r in self._q(
            "SELECT username FROM recipients WHERE incident_id=? AND state='accepted'", (iid,))}

        def who(u, r=None):
            role_ = r or ""
            if role_ == "volunteer" and u not in accepted:
                return "מתנדב/ת"
            return self._display(u) if u else None

        roles = {r["username"]: r["role"] for r in self._q(
            "SELECT username, role FROM recipients WHERE incident_id=?", (iid,))}
        record = {
            "format": FORMAT,
            "generated_at": time.time(),
            "generated_for": user,
            "incident": {k: inc[k] for k in ("id", "mode", "emergency_type", "severity", "status", "created_at",
                                             "ended_at")},
            "owner": {"username": inc["owner"], "display_name": self._display(inc["owner"])},
            "risk": {"level": inc["risk_level"], "score": inc["risk_score"],
                     "confidence": inc["risk_confidence"],
                     "reasons": json.loads(inc["risk_reasons"] or "[]"),
                     "signals": json.loads(inc["risk_signals"] or "[]")},
            "locations": [dict(r) for r in self._q(
                "SELECT lat, lon, accuracy_m, source, is_precise, captured_at, received_at FROM incident_locations "
                "WHERE incident_id=? ORDER BY id", (iid,))],
            "timeline": [{"ts": r["ts"], "kind": r["kind"], "text": r["text"],
                          "actor": who(r["actor"], roles.get(r["actor"])) if r["actor"] else None}
                         for r in self._q("SELECT ts, actor, kind, text FROM incident_events WHERE incident_id=? "
                                          "ORDER BY id", (iid,))],
            "recipients": [{"who": who(r["username"], r["role"]), "role": r["role"], "state": r["state"],
                            "notified_at": r["notified_at"], "delivered_at": r["delivered_at"],
                            "responded_at": r["responded_at"], "response": r["response"]}
                           for r in self._q("SELECT * FROM recipients WHERE incident_id=? ORDER BY notified_at",
                                            (iid,))],
            "messages": [{"from": who(r["sender"], roles.get(r["sender"])), "text": r["text"],
                          "at": r["created_at"]}
                         for r in self._q("SELECT sender, text, created_at FROM messages WHERE incident_id=? "
                                          "ORDER BY id", (iid,))],
            "access_log": [dict(r) for r in self._q(
                "SELECT incident_id, username, action, at, prev_hash, hash FROM evidence_access WHERE incident_id=? "
                "ORDER BY id", (iid,))],
            "notes": ["זמני received_at הם זמן הקליטה בשרת; captured_at - שעון המכשיר (לא מאומת)",
                      "הקלטות/תמונות נשמרות במכשיר בלבד ומאומתות שם (SHA-256 לכל קובץ)"],
        }
        body = canonical(record)
        key = self._evidence_key()
        pub = self.evidence_public_key()["public_key"]
        return {"record": record, "sha256": hashlib.sha256(body).hexdigest(),
                "signature": key.sign(body).hex(), "public_key": pub, "algorithm": "Ed25519"}

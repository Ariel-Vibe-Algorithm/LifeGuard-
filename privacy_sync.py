# -*- coding: utf-8 -*-
"""
privacy_sync.py - סנכרון מושהה (offline-first) של הקלטות חירום ואירועי ביקורת מהמחשב לשרת.

- הקלטות: כל קטע שנחתם בכספת (evidence_vault) נכנס לתור המקומי המוצפן (incident_store outbox). העלאה רק
  אם השרת מאשר: המשתמש/ת הפעיל/ה "העלאת הקלטות חירום" (או פרופיל ילד עם אישור כפול), והאירוע מאושר (SOS ידני /
  ביטחון גבוה). אין הרשאה -> ההקלטה נשארת מוצפנת במחשב בלבד ("לא הועלה - אין הרשאה"), לא "נכשל".
- אירועי ביקורת מהמכשיר (הפעלה בלי קליטה, ביטול כהתראת שווא, כשל העלאה) - עם event_id קבוע (לא כפול
  בשליחה חוזרת) וזמן המכשיר, שנשמר בשרת בנפרד מזמן השרת.
- פריטים בתור לא פגים בסיום האירוע (incident_id=None): העלאה מאוחרת מותרת עד 48 שעות (השרת אוכף).
"""

import base64
import hashlib
import time
import uuid

import incident_store

KIND_REC, KIND_AUDIT = "recording_upload", "device_audit"
UNDELIVERABLE = incident_store.OUTBOX_UNDELIVERABLE


def queue_recording(owner, event_id, item):
    """item: רשומה מה-manifest של הכספת (file, sealed, sha256, captured_at)."""
    return incident_store.enqueue(owner, KIND_REC, {"event_id": event_id, "item": item},
                                  idempotency_key=f"rec:{event_id}:{item['file']}")


def queue_audit(owner, action, result="completed", details=None, event_client_id=None):
    eid = uuid.uuid4().hex
    return incident_store.enqueue(owner, KIND_AUDIT, {"event_id": eid, "device_ts": time.time(), "action": action,
                                                      "result": result, "details": details or {},
                                                      "client_incident": event_client_id},
                                  idempotency_key=f"audit:{eid}")


def sync(client, vault, owner, log=None):
    """-> {"audit_sent", "uploaded", "kept_local", "pending"}. לא זורק על בעיות רשת - נשאר בתור."""
    from backend_client import BackendError, BackendUnavailable
    out = {"audit_sent": 0, "uploaded": 0, "kept_local": 0, "pending": 0}
    if client is None or not getattr(client, "token", None):
        return out
    items = incident_store.pending_outbox(owner)
    audits = [i for i in items if i["kind"] == KIND_AUDIT]
    if audits:
        try:
            events = []
            for a in audits:
                p = a["payload"]
                events.append({"event_id": p["event_id"], "device_ts": p["device_ts"], "action": p["action"],
                               "result": p["result"], "details": p.get("details") or {}})
            client.device_audit(events)
            for a in audits:
                incident_store.mark_outbox(owner, a["id"], incident_store.OUTBOX_SENT)
            out["audit_sent"] = len(audits)
        except BackendUnavailable:
            out["pending"] += len(audits)
            return out
    recs = [i for i in items if i["kind"] == KIND_REC]
    if not recs:
        return out
    try:
        policy = client.recording_policy()
    except BackendUnavailable:
        out["pending"] += len(recs)
        return out
    ids = {}
    for r in recs:
        p = r["payload"]
        if not policy.get("record_on_emergency"):
            incident_store.mark_outbox(owner, r["id"], UNDELIVERABLE, "העלאת הקלטות לא מאופשרת - נשמר במחשב בלבד")
            out["kept_local"] += 1
            continue
        try:
            if p["event_id"] not in ids:
                ids[p["event_id"]] = client.incident_by_client(p["event_id"])["id"]
            data = vault.open_item(p["event_id"], p["item"])
            client.upload_recording(ids[p["event_id"]], {
                "client_id": f"{p['event_id']}/{p['item']['file']}"[:80],
                "data_b64": base64.b64encode(data).decode("ascii"), "sha256": hashlib.sha256(data).hexdigest(),
                "mime": "audio/wav", "ended_at": p["item"].get("captured_at")})
            incident_store.mark_outbox(owner, r["id"], incident_store.OUTBOX_SENT)
            out["uploaded"] += 1
        except BackendUnavailable:
            out["pending"] += 1
            if r.get("attempts", 0) == 0:
                queue_audit(owner, "recording_upload_failed", "pending", {"file": p["item"]["file"],
                                                                          "reason": "אין חיבור - ינסה שוב"})
            incident_store.mark_outbox(owner, r["id"], incident_store.OUTBOX_PENDING, "אין חיבור", count_attempt=True)
        except BackendError as exc:
            if exc.status == 404:          # האירוע עוד לא הגיע לשרת - ננסה שוב
                out["pending"] += 1
                continue
            incident_store.mark_outbox(owner, r["id"], UNDELIVERABLE, f"{exc.code}: {exc.message}"[:200])
            out["kept_local"] += 1
        except (OSError, ValueError) as exc:   # קובץ הכספת נמחק (שווא) / פגום
            incident_store.mark_outbox(owner, r["id"], incident_store.OUTBOX_FAILED, str(exc)[:200])
    if log and out["uploaded"]:
        log(f"🔒 {out['uploaded']} קטעי הקלטה הועלו מוצפנים לאנשי הקשר המורשים")
    return out

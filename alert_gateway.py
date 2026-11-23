# -*- coding: utf-8 -*-
"""
alert_gateway.py
השכבה היחידה שדרכה המנוע שולח התראות חירום: שרת LifeGuard בלבד (אין Telegram/WhatsApp/SMS/אימייל).

אחריות:
- פתיחת אירוע בשרת, מיקום חי, הסלמה לגלוי, סוג חירום, סגירה.
- שרת לא זמין -> הפעולה נשמרת בתור המקומי (outbox) ונשלחת שוב כל עוד האירוע פעיל.
  לעולם לא מדווח "נשלח" על משהו שנשמר מקומית בלבד.
- מצב מסירה מחושב *רק* ממה שהשרת מאשר: notified (השרת שלח) / delivered (אפליקציית הנמען
  אישרה קבלה) / accepted (אדם קיבל את האירוע).
"""

import threading
import time

import incident_store
from backend_client import BackendError, BackendUnavailable

POLL_SECONDS = 3.0
LOCATION_MIN_INTERVAL_SECONDS = 5.0
ROLE_LABELS = {"trusted": "איש קשר מהימן", "volunteer": "מתנדב/ת"}
SEVERITY_FROM_RISK = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}


class AlertGateway:
    def __init__(self, username, client=None, on_status=None, log=None, poll_seconds=POLL_SECONDS):
        self.username = username
        self.client = client
        self.on_status = on_status or (lambda event_id, status: None)
        self.log = log or (lambda msg: None)
        self.poll_seconds = poll_seconds
        self._lock = threading.RLock()
        self._events = {}  # event_id -> state dict

    def set_client(self, client):
        self.client = client

    def connected(self):
        return self.client is not None and self.client.token is not None

    # ---------- פעולות מהמנוע (לא חוסמות) ----------
    def open_incident(self, event_id, mode="loud", emergency_type="unknown", severity="unknown",
                      location=None, profile=None, log=None, escort_id=None, risk=None):
        with self._lock:
            if event_id in self._events:
                return
            self._events[event_id] = {
                "server_id": None, "create": {"mode": mode, "emergency_type": emergency_type,
                                              "severity": severity, "profile": profile or {},
                                              "escort_id": escort_id, "risk": risk_payload(risk)},
                "location": _loc_payload(location), "location_sent_at": 0, "ops": [],
                "view": None, "error": None, "closed": False, "stop": threading.Event(),
                "log": log or self.log, "created_at": time.time(),
            }
        try:
            incident_store.enqueue(self.username, "server_create", {"mode": mode},
                                   idempotency_key=f"{event_id}:create", incident_id=event_id)
        except Exception:
            pass
        threading.Thread(target=self._worker, args=(event_id,), name="LG-Alert", daemon=True).start()

    def update_location(self, event_id, loc):
        payload = _loc_payload(loc)
        if not payload:
            return
        with self._lock:
            st = self._events.get(event_id)
            if st and not st["closed"]:
                st["location"] = payload  # רק האחרון חשוב; לא שולחים מיקומים ישנים באיחור

    def update_location_now(self, event_id, loc):
        """מיקום שהמשתמש/ת הזין/ה ידנית - נשלח מיד (לא מחכה למרווח של המיקום החי)."""
        payload = _loc_payload(loc)
        if not payload:
            return
        with self._lock:
            st = self._events.get(event_id)
            if st and not st["closed"]:
                st["location"], st["location_sent_at"] = payload, 0

    def send_message(self, event_id, text):
        """הודעת צ'אט מבעל/ת האירוע (למשל תיאור המיקום שנכתב). נשמרת בתור עד שהאירוע מגיע לשרת."""
        self._queue_op(event_id, {"_message": str(text)[:500]})

    def update_risk(self, event_id, risk):
        """הערכת סכנה שהסתיימה אחרי שה-SOS כבר נפתח (SOS ידני לא ממתין לה) - נשלחת כעדכון."""
        payload = risk_payload(risk)
        if payload:
            self._queue_op(event_id, {"risk": payload})

    def set_loud(self, event_id):
        self._queue_op(event_id, {"mode": "loud"})

    def set_emergency_type(self, event_id, emergency_type):
        self._queue_op(event_id, {"emergency_type": emergency_type})

    def close(self, event_id, status="resolved"):
        with self._lock:
            st = self._events.get(event_id)
            if not st:
                return
            st["ops"].append({"status": status})
            st["closing"] = True

    def status(self, event_id):
        with self._lock:
            st = self._events.get(event_id)
            return _describe(st, self.connected())

    def shutdown(self):
        with self._lock:
            for st in self._events.values():
                st["stop"].set()

    def _queue_op(self, event_id, op):
        with self._lock:
            st = self._events.get(event_id)
            if st and not st["closed"]:
                st["ops"].append(op)

    # ---------- worker לכל אירוע ----------
    def _worker(self, event_id):
        st = self._events[event_id]
        last_text = None
        while not st["stop"].is_set():
            try:
                self._step(event_id, st)
                st["error"] = None
            except BackendUnavailable as e:
                st["error"] = e.message
            except BackendError as e:
                st["error"] = e.message
                if e.status in (401, 403):
                    st["log"]("❗ ההתחברות לשרת LifeGuard פגה - התראות לא נשלחות. יש להתחבר מחדש.")
            status = _describe(st, self.connected())
            if status["text"] != last_text:
                last_text = status["text"]
                try:
                    self.on_status(event_id, status)
                except Exception:
                    pass
            if st["closed"]:
                return
            st["stop"].wait(self.poll_seconds)

    def _step(self, event_id, st):
        if not self.connected():
            if st.get("closing") and st["server_id"] is None:
                st["closed"] = True  # אירוע שמעולם לא הגיע לשרת ונסגר - אין מה לשלוח באיחור
                _expire(self.username, event_id)
            return
        c = self.client
        if st["server_id"] is None:
            if st.get("closing"):
                st["closed"] = True
                _expire(self.username, event_id)
                return
            args = st["create"]
            view = c.create_incident(event_id, args["mode"], args["emergency_type"], args["severity"],
                                     st["location"], args["profile"],
                                     escort_id=args.get("escort_id"),  # idempotent לפי event_id
                                     risk=args.get("risk"))
            st["server_id"], st["view"], st["location_sent_at"] = view["id"], view, time.time()
            _mark_sent(self.username, event_id)
            late = time.time() - st["created_at"] > 10
            st["log"]("📡 האירוע התקבל בשרת LifeGuard" + (" (באיחור - השרת לא היה זמין)" if late else ""))
        sid = st["server_id"]
        while st["ops"]:
            op = st["ops"][0]
            if "_message" in op:
                c.send_message(sid, text=op["_message"])
            else:
                c.update_incident(sid, **op)
            st["ops"].pop(0)
            if op.get("status"):
                st["closed"] = True
                st["view"] = c.get_incident(sid)
                return
        loc = st["location"]
        if loc and time.time() - st["location_sent_at"] >= LOCATION_MIN_INTERVAL_SECONDS:
            c.update_location(sid, loc)
            st["location_sent_at"] = time.time()
        st["view"] = c.get_incident(sid)

    # ---------- שליחת בדיקה ----------
    def test_alert(self, log, wait_seconds=6):
        """שולח התראת בדיקה לאנשי הקשר המאושרים ומדווח מי *אישר קבלה באפליקציה* (לא רק 'נשלח')."""
        if not self.connected():
            log("❌ בדיקה נכשלה: לא מחובר לשרת LifeGuard")
            return None
        try:
            res = self.client.notify_trusted("🧪 הודעת בדיקה מ-LifeGuard - אין מצב חירום.", test=True)
        except BackendError as e:
            log(f"❌ בדיקה נכשלה: {e.message}")
            return None
        if not res["sent_to"]:
            log("⚠️ אין אנשי קשר מהימנים שאישרו את ההזמנה - אף אחד לא יקבל התראת SOS")
            return {}
        log(f"🧪 נשלח ל-{len(res['sent_to'])} אנשי קשר. ממתין לאישור קבלה מהאפליקציות שלהם...")
        status = {}
        end = time.time() + wait_seconds
        while time.time() < end:
            time.sleep(1)
            try:
                status = self.client.notification_status(res["notification_ids"])
            except BackendError:
                continue
            if all(status.values()):
                break
        for user in res["sent_to"]:
            log(f"  {'✅ נמסר לאפליקציה של' if status.get(user) else '⚠️ טרם אושרה קבלה (האפליקציה לא מחוברת?) -'} {user}")
        return status


def risk_payload(risk):
    """רק מה שהמגיבים צריכים: רמה, ניקוד ונימוקים. (לא נתוני חיישנים גולמיים / רמת רעש.)"""
    if not isinstance(risk, dict) or not risk.get("level"):
        return None
    conf = risk.get("confidence") if isinstance(risk.get("confidence"), dict) else {}
    out = {"level": risk.get("level"), "score": risk.get("score"),
           "reasons": [str(r) for r in (risk.get("reasons") or [])][:8]}
    if conf.get("value") is not None:   # ביטחון בזיהוי - נפרד מהחומרה
        out["confidence"] = conf["value"]
        out["signals"] = [{"source": s} for s in (conf.get("sources") or [])][:8]
    return out


def _loc_payload(loc):
    if not loc or not loc.get("available", True) or loc.get("lat") is None:
        return None
    return {"lat": loc["lat"], "lon": loc["lon"], "is_precise": bool(loc.get("is_precise")),
            "accuracy_m": loc.get("accuracy_m_estimate") or loc.get("accuracy_m"),
            "source": loc.get("source"), "captured_at": loc.get("timestamp") or loc.get("captured_at")}


def _mark_sent(username, event_id):
    try:
        for item in incident_store.pending_outbox(username, event_id):
            if item["kind"] == "server_create":
                incident_store.mark_outbox(username, item["id"], incident_store.OUTBOX_SENT)
    except Exception:
        pass


def _expire(username, event_id):
    try:
        incident_store.expire_outbox_for_incident(username, event_id)
    except Exception:
        pass


def _describe(st, connected):
    """טקסט מצב אחד וברור, מבוסס אך ורק על אישורי השרת."""
    base = {"total": 0, "accepted": 0, "delivered": 0, "pending": 0, "no_channel": 0, "human_ack": False,
            "online": connected, "level": "pending", "recipients": [], "server_id": None}
    if st is None:
        return {**base, "text": "⏳ שליחת ההתראה בתהליך..."}
    base["server_id"] = st["server_id"]
    if st["server_id"] is None:
        if not connected:
            return {**base, "level": "critical", "pending": 1,
                    "text": "❗ לא מחובר לשרת LifeGuard - ההתראה לא נשלחה.\nהתקשר/י בעצמך למוקד החירום."}
        return {**base, "level": "critical", "pending": 1,
                "text": "❗ ההתראה עדיין לא הגיעה לשרת (" + (st["error"] or "מנסה") + ").\n"
                        "היא נשמרה ותישלח אוטומטית. בינתיים - התקשר/י בעצמך למוקד החירום."}
    recipients = (st["view"] or {}).get("recipients", [])
    queued = [r for r in recipients if r["state"] == "queued"]
    active = [r for r in recipients if r["state"] not in ("expired", "revoked", "declined", "reassigned", "queued")]
    accepted = [r for r in recipients if r["state"] == "accepted"]
    delivered = [r for r in recipients if r["state"] in ("delivered", "accepted")]
    base.update({"total": len(active), "accepted": len(accepted), "delivered": len(delivered),
                 "human_ack": bool(accepted), "recipients": recipients})
    lines = []
    if accepted:
        base["level"] = "ok"
        for r in accepted:
            lines.append(f"✅ {r['display_name']} ({ROLE_LABELS.get(r['role'], r['role'])}) קיבל/ה את האירוע"
                         + (f" · עד {r['distance_band_km']:g} ק\"מ" if r.get("distance_band_km") else "")
                         + (f" · ~{r['eta_minutes']} דק'" if r.get("eta_minutes") else ""))
    if delivered and not accepted:
        base["level"] = "warn"
        lines.append(f"📲 נמסר לאפליקציה של {len(delivered)} - ממתין שמישהו יקבל את האירוע")
    waiting = [r for r in active if r["state"] == "notified"]
    if waiting:
        if not lines:
            base["level"] = "warn"
        lines.append(f"⏳ {len(waiting)} טרם אישרו קבלה באפליקציה")
    ringing = [r for r in recipients if r.get("ring_status") == "ringing"]
    if ringing and not accepted:
        lines.append(f"📞 מצלצל עכשיו ל{ringing[0]['display_name']} (איש הקשר הקרוב ביותר שעוד לא ענה)")
    if queued and not accepted:
        lines.append(f"⏭️ עוד {len(queued)} אנשי קשר בתור - יקבלו צלצול אם הקודם לא יענה")
    if not active and not accepted and not queued:
        base["level"] = "critical"
        lines.append("❗ השרת קיבל את האירוע אך אין למי לשלוח: אין איש קשר מאושר\nואין מתנדב זמין בטווח. "
                     "התקשר/י בעצמך למוקד החירום.")
    if st["error"]:
        lines.append(f"⚠️ החיבור לשרת נקטע ({st['error']}) - מנסה שוב")
    if st.get("closed"):
        lines.append("האירוע נסגר בשרת")
    return {**base, "text": "\n".join(lines)}

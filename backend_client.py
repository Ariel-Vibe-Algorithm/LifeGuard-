# -*- coding: utf-8 -*-
"""
backend_client.py
לקוח לשרת LifeGuard: התחברות, אירועים, מיקום חי, קהילה, וקבלת התראות בזמן אמת.

קבלת התראות: זרם SSE (דחיפה מיידית מהשרת) עם התחברות מחדש אוטומטית, ו-polling כגיבוי.
כל התראה שהתקבלה מאושרת לשרת (POST /received) - זה מה שמאפשר לשולח לראות "נמסר"
על סמך אישור אמיתי ולא הנחה.
"""

import json
import os
import threading
import time
from urllib.parse import quote, urlparse

import requests

DEFAULT_URL = os.environ.get("LIFEGUARD_SERVER_URL", "http://127.0.0.1:8765")
TIMEOUT = 6


class BackendError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class BackendUnavailable(BackendError):
    def __init__(self, message):
        super().__init__(0, "unavailable", message)


def is_insecure(url):
    p = urlparse(url)
    return p.scheme != "https" and p.hostname not in ("127.0.0.1", "localhost", "::1")


class BackendClient:
    def __init__(self, base_url=None, token=None, username=None):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.token = token
        self.username = username
        self._session = requests.Session()
        self._stream_thread = None
        self._stream_stop = threading.Event()
        self._last_id = 0
        self._seen = set()
        self.stream_state = {"connected": False, "mode": None, "last_ok": None, "error": None}
        if is_insecure(self.base_url) and os.environ.get("LIFEGUARD_ALLOW_INSECURE") != "1":
            raise BackendError(0, "insecure_url", "כתובת שרת לא מוצפנת (http) מחוץ למחשב המקומי נחסמה. "
                                                  "השתמש/י ב-https.")

    # ---------- HTTP ----------
    def _req(self, method, path, body=None, timeout=TIMEOUT, auth=True, params=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self.token:
                raise BackendError(401, "no_token", "לא מחובר לשרת")
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = self._session.request(method, f"{self.base_url}/v1{path}", headers=headers, params=params,
                                      data=json.dumps(body) if body is not None else None, timeout=timeout)
        except requests.RequestException as exc:
            self._reset_connections()
            raise BackendUnavailable(f"השרת לא זמין ({type(exc).__name__})") from exc
        if r.status_code >= 500:
            # חיבור keep-alive לשרת שנפל/הופעל מחדש עלול להמשיך להחזיר שגיאות - פותחים חיבור חדש
            self._reset_connections()
        try:
            data = r.json()
        except ValueError:
            raise BackendError(r.status_code, "bad_response", "תשובה לא תקינה מהשרת")
        if r.status_code >= 400:
            err = BackendError(r.status_code, data.get("error", "error"), data.get("message", "שגיאה"))
            err.data = data
            raise err
        return data

    def _reset_connections(self):
        try:
            self._session.close()
        except Exception:
            pass
        self._session = requests.Session()

    def health(self):
        return self._req("GET", "/health", auth=False, timeout=3)

    # ---------- auth ----------
    def register(self, username, password, display_name=None):
        return self._req("POST", "/auth/register", {"username": username, "password": password,
                                                    "display_name": display_name}, auth=False)

    def login(self, username, password, device="LifeGuard Windows"):
        data = self._req("POST", "/auth/login", {"username": username, "password": password, "device": device},
                         auth=False)
        self.token, self.username = data["token"], data["username"]
        return data

    def login_or_register(self, username, password, display_name=None):
        """חשבון השרת נוצר אוטומטית בכניסה הראשונה עם אותם פרטים. מחזיר ('login'|'registered', data)."""
        try:
            return "login", self.login(username, password)
        except BackendError as e:
            if e.status != 401:
                raise
        try:
            self.register(username, password, display_name)
        except BackendError as e:
            if e.code == "username_taken":
                raise BackendError(409, "username_taken",
                                   "שם המשתמש תפוס בשרת ע\"י חשבון אחר (או שהסיסמה בשרת שונה)")
            raise
        return "registered", self.login(username, password)

    def logout(self):
        try:
            self._req("POST", "/auth/logout")
        finally:
            self.token = None

    def me(self):
        return self._req("GET", "/me")

    def setup(self):
        return self._req("GET", "/me/setup")

    def evidence(self, incident_id):
        return self._req("GET", f"/incidents/{incident_id}/evidence")

    def evidence_by_client(self, client_id):
        return self._req("GET", f"/evidence/by-client/{client_id}")

    def evidence_public_key(self):
        return self._req("GET", "/evidence/public-key", auth=False)

    # ---------- presence / community ----------
    def set_volunteer(self, available, radius_km=5, skills=None):
        return self._req("PUT", "/me/volunteer", {"available": available, "radius_km": radius_km,
                                                  "skills": skills or []})

    def update_presence(self, loc):
        return self._req("PUT", "/me/presence", {"lat": loc["lat"], "lon": loc["lon"],
                                                 "is_precise": bool(loc.get("is_precise")),
                                                 "accuracy_m": loc.get("accuracy_m_estimate"),
                                                 "source": loc.get("source")})

    def community_summary(self, radius_km=5):
        return self._req("GET", "/community/summary", params={"radius_km": radius_km})

    def set_phone(self, phone):
        return self._req("PUT", "/me/phone", {"phone": phone})

    # ---------- מעגל הביטחון (השרת הוא מקור האמת) ----------
    def relationships(self):
        return self._req("GET", "/circle")

    circle = relationships

    def invite(self, contact):
        """הזמנה לפי שם משתמש LifeGuard."""
        return self._req("POST", "/invitations", {"username": contact})

    def invite_phone(self, phone, label=None, send_sms=False):
        """הזמנה לפי טלפון. מחזיר 'code' חד-פעמי. send_sms=True: השרת שולח SMS (רק אם יש לו ספק מוגדר)."""
        return self._req("POST", "/invitations", {"phone": phone, "label": label, "send_sms": send_sms})

    def invite_code(self, label=None):
        """קוד הזמנה לשיתוף - גם למי שעדיין לא התקין LifeGuard."""
        return self._req("POST", "/invitations", {"code": True, "label": label})

    def respond_invitation(self, invitation_id, accept):
        return self._req("POST", f"/invitations/{invitation_id}/respond", {"accept": accept})

    def confirm_invitation(self, invitation_id, confirm):
        return self._req("POST", f"/invitations/{invitation_id}/confirm", {"confirm": confirm})

    def resend_invitation(self, invitation_id):
        return self._req("POST", f"/invitations/{invitation_id}/resend")

    def cancel_invitation(self, invitation_id):
        return self._req("POST", f"/invitations/{invitation_id}/cancel")

    def preview_code(self, code):
        return self._req("POST", "/invitations/redeem", {"code": code, "preview": True})

    def redeem_code(self, code, accept=True):
        return self._req("POST", "/invitations/redeem", {"code": code, "accept": accept})

    def disconnect(self, other, scope="both"):
        return self._req("DELETE", f"/connections/{quote(other, safe='')}", params={"scope": scope})

    def respond_invite(self, owner, accept):
        """תאימות: תגובה להזמנה הממתינה האחרונה מ-owner."""
        return self._req("POST", f"/relationships/{quote(owner, safe='')}/respond", {"accept": accept})

    def remove_relationship(self, other):
        return self.disconnect(other, "both")

    def notify_trusted(self, text, test=False):
        return self._req("POST", "/trusted/notify", {"text": text, "type": "test" if test else "info"})

    def notification_status(self, ids):
        return self._req("POST", "/notifications/status", {"ids": ids})

    # ---------- ליווי הביתה (נאכף בשרת) ----------
    def start_escort(self, minutes, destination="", mode="loud", location=None):
        return self._req("POST", "/escorts", {"minutes": minutes, "destination": destination, "mode": mode,
                                              "location": location})

    def escorts(self):
        return self._req("GET", "/escorts")

    def get_escort(self, escort_id):
        return self._req("GET", f"/escorts/{escort_id}")

    # ---------- פרטיות: הרשאות מיקום, הקלטות, ביקורת, הורה-ילד ----------
    def set_location_mode(self, contact, mode):
        return self._req("PUT", f"/relationships/{contact}/location-mode", {"mode": mode})

    def set_recording_access(self, contact, allowed):
        return self._req("PUT", f"/relationships/{contact}/recording-access", {"allowed": bool(allowed)})

    def pause_sharing(self, paused=True):
        return self._req("POST", "/me/location-sharing/pause", {"paused": bool(paused)})

    def set_record_on_emergency(self, enabled):
        return self._req("PUT", "/me/recording", {"record_on_emergency": bool(enabled)})

    def recording_policy(self):
        return self._req("GET", "/me/recording-policy")

    def put_live_location(self, loc):
        return self._req("PUT", "/me/live-location", loc)

    def contact_location(self, owner):
        return self._req("GET", f"/contacts/{owner}/location")

    def privacy_history(self, **filters):
        params = {k: v for k, v in filters.items() if v not in (None, "")}
        return self._req("GET", "/me/privacy/history", params=params)

    def privacy_permissions(self):
        return self._req("GET", "/me/privacy/permissions")

    def privacy_export(self):
        return self._req("GET", "/me/privacy/export", timeout=20)

    def device_audit(self, events):
        return self._req("POST", "/me/audit", {"events": events})

    def guardian_links(self):
        return self._req("GET", "/me/guardian")

    def guardian_invite(self, other, i_am, child_age=None):
        return self._req("POST", "/guardian/links", {"other": other, "i_am": i_am, "child_age": child_age})

    def guardian_approve(self, link_id):
        return self._req("POST", f"/guardian/links/{link_id}/approve", {})

    def guardian_consent(self, link_id, consent):
        return self._req("POST", f"/guardian/links/{link_id}/recording-consent", {"consent": bool(consent)})

    def guardian_revoke(self, link_id):
        return self._req("POST", f"/guardian/links/{link_id}/revoke", {})

    def incident_by_client(self, client_id):
        return self._req("GET", f"/incidents/by-client/{client_id}")

    def confirm_incident(self, incident_id):
        return self._req("POST", f"/incidents/{incident_id}/confirm", {})

    def upload_recording(self, incident_id, body):
        return self._req("POST", f"/incidents/{incident_id}/recordings", body, timeout=60)

    def list_recordings(self, incident_id):
        return self._req("GET", f"/incidents/{incident_id}/recordings")

    def get_recording(self, incident_id, recording_id):
        return self._req("GET", f"/incidents/{incident_id}/recordings/{recording_id}", timeout=60)

    def delete_recording(self, recording_id):
        return self._req("DELETE", f"/me/recordings/{recording_id}")

    # ---------- מתנדבי תמיכה / הזעקת רשויות (server/helpers.py) ----------
    def volunteer_roles(self):
        return self._req("GET", "/me/volunteer/roles")

    def set_volunteer_roles(self, roles, languages=None):
        return self._req("PUT", "/me/volunteer/roles", {"roles": roles, "languages": languages or ["he"]})

    def request_helper(self, incident_id, kind, reason=""):
        return self._req("POST", f"/incidents/{incident_id}/request-helper", {"kind": kind, "reason": reason})

    def helper_respond(self, incident_id, action):
        return self._req("POST", f"/incidents/{incident_id}/helper-respond", {"action": action})

    def release_helper(self, incident_id, helper):
        return self._req("POST", f"/incidents/{incident_id}/helpers/{helper}/release", {})

    def create_support_request(self, note="", language="he"):
        return self._req("POST", "/support-requests", {"note": note, "language": language})

    def street_intel_at(self, lat, lon, radius=150, include_news=False):
        return self._req("GET", "/street-intel", params={"lat": lat, "lon": lon, "radius": radius,
                                                         "news": "1" if include_news else "0"})

    def street_report(self, kind, lat, lon, note=""):
        return self._req("POST", "/street-intel/reports", {"kind": kind, "lat": lat, "lon": lon, "note": note})

    def route_safety(self, routes=None, origin=None, dest=None, at=None, include_news=False):
        """routes: [[lat, lon], ...] או [{"points": [[lat, lon], ...], "duration_s": .., "distance_m": ..}].
        התשובה כוללת modes (fastest / safest / balanced) לכל החלופות, ו-weather (הקשר בלבד)."""
        return self._req("POST", "/route-safety", {"routes": routes, "from": origin, "to": dest, "at": at,
                                                   "include_news": bool(include_news)}, timeout=15)

    def route_options(self, alternatives, at=None, include_news=False):
        """חלופות הליכה מ-OSRM ({points, duration_s, distance_m}) -> ניקוד + בחירת מצבים בשרת."""
        routes = [{"points": [[round(p[0], 6), round(p[1], 6)] for p in a["points"]][:4000],
                   "duration_s": a.get("duration_s"), "distance_m": a.get("distance_m")}
                  for a in list(alternatives)[:3]]
        return self.route_safety(routes=routes, at=at, include_news=include_news)

    def route_options_between(self, origin, dest, at=None, include_news=False):
        """השרת מביא את החלופות בעצמו (OSRM עצמי או FOSSGIS) ומחזיר גם geometry לכל מסלול."""
        return self.route_safety(origin={"lat": origin[0], "lon": origin[1]}, dest={"lat": dest[0], "lon": dest[1]},
                                 at=at, include_news=include_news)

    def national_status(self, lat=None, lon=None):
        params = {"lat": lat, "lon": lon} if lat is not None else None
        return self._req("GET", "/national/status", params=params)

    def set_national_areas(self, areas):
        return self._req("PUT", "/me/national/areas", {"areas": areas})

    def request_qualification(self, qual, org, ref):
        return self._req("POST", "/me/volunteer/qualifications", {"qual": qual, "org": org, "ref": ref})

    def volunteer_history(self):
        return self._req("GET", "/me/volunteer/history")

    def start_ride(self, minutes, ride_type, destination_point, destination="", mode="loud", location=None,
                   vehicle=None):
        return self._req("POST", "/escorts", {"minutes": minutes, "destination": destination, "mode": mode,
                                              "location": location, "kind": "ride", "ride_type": ride_type,
                                              "destination_point": destination_point, "vehicle": vehicle or {}})

    def escort_checkin(self, escort_id, status):
        return self._req("POST", f"/escorts/{escort_id}/checkin", {"status": status})

    def escort_sos(self, escort_id, mode="loud"):
        return self._req("POST", f"/escorts/{escort_id}/sos", {"mode": mode})

    def escort_location(self, escort_id, loc):
        return self._req("POST", f"/escorts/{escort_id}/location", loc)

    def escort_action(self, escort_id, action, minutes=None):
        return self._req("POST", f"/escorts/{escort_id}/{action}", {"minutes": minutes})

    # ---------- מתנדבים: אימות ודירוג ----------
    # ---------- אימות זהות חובה למתנדבים (server/volunteer_identity.py) ----------
    def identity_status(self):
        return self._req("GET", "/me/volunteer/identity")

    def submit_identity(self, id_number, id_image_bytes, selfie_bytes, consent):
        import base64
        return self._req("POST", "/me/volunteer/identity",
                         {"id_number": id_number, "consent": bool(consent),
                          "id_image": base64.b64encode(id_image_bytes).decode("ascii"),
                          "selfie": base64.b64encode(selfie_bytes).decode("ascii")}, timeout=60)

    def withdraw_identity(self):
        return self._req("DELETE", "/me/volunteer/identity")

    def request_verification(self, organization, credential_ref):
        return self._req("POST", "/me/volunteer/verification",
                         {"organization": organization, "credential_ref": credential_ref})

    def give_feedback(self, incident_id, volunteer, rating=None, report=None):
        return self._req("POST", f"/incidents/{incident_id}/feedback",
                         {"volunteer": volunteer, "rating": rating, "report": report})

    # ---------- מידע רפואי / SMS / מוכנות ----------
    def get_medical(self):
        return self._req("GET", "/me/medical")

    def set_medical(self, data):
        return self._req("PUT", "/me/medical", data)

    def phone_verify_start(self):
        return self._req("POST", "/me/phone/verify/start")

    def phone_verify_check(self, code):
        return self._req("POST", "/me/phone/verify/check", {"code": code})

    def set_sms_fallback(self, enabled):
        return self._req("PUT", "/me/sms-fallback", {"enabled": enabled})

    def run_readiness_check(self):
        return self._req("POST", "/me/readiness")

    def readiness(self, check_id=None):
        return self._req("GET", "/me/readiness", params={"id": check_id} if check_id else None)

    # ---------- incidents ----------
    def create_incident(self, client_id, mode="loud", emergency_type="unknown", severity="unknown",
                        location=None, profile=None, escort_id=None, risk=None):
        body = {"client_id": client_id, "mode": mode, "emergency_type": emergency_type, "severity": severity,
                "location": location, "profile": profile}
        if escort_id:
            body["escort_id"] = escort_id
        if risk:  # הערכת הסכנה מהמכשיר - השרת מציג אותה רק למגיבים ומשתמש בה לתור המתנדבים
            body["risk"] = risk
        return self._req("POST", "/incidents", body)

    def update_location(self, incident_id, loc):
        return self._req("POST", f"/incidents/{incident_id}/location", loc)

    def update_incident(self, incident_id, **fields):
        return self._req("PATCH", f"/incidents/{incident_id}", fields)

    def get_incident(self, incident_id):
        return self._req("GET", f"/incidents/{incident_id}")

    def change_password(self, old_password, new_password):
        """מנתק את כל המכשירים האחרים."""
        return self._req("POST", "/me/password", {"old_password": old_password, "new_password": new_password})

    def sessions(self):
        return self._req("GET", "/me/sessions")

    def revoke_sessions(self, session_id=None):
        return self._req("DELETE", "/me/sessions", params={"id": session_id} if session_id else None)

    def report_incident(self, incident_id, kind, note=None):
        """מתנדב: 'false_alarm' / 'abusive'."""
        return self._req("POST", f"/incidents/{incident_id}/report", {"kind": kind, "note": note})

    def respond(self, incident_id, action):
        return self._req("POST", f"/incidents/{incident_id}/respond", {"action": action})

    def send_message(self, incident_id, text=None, quick=None):
        """text חופשי, או quick = קוד הודעה מהירה (ok / cant_talk / on_my_way ...). השרת קובע את הנוסח."""
        body = {"quick": quick} if quick else {"text": text}
        return self._req("POST", f"/incidents/{incident_id}/messages", body)

    def mark_read(self, incident_id, up_to_id):
        return self._req("POST", f"/incidents/{incident_id}/messages/read", {"up_to_id": up_to_id})

    def inbox(self):
        return self._req("GET", "/incidents/inbox")["incidents"]

    def street_intel(self, bbox=""):
        return self._req("GET", "/street-intel/segments", params={"bbox": bbox})

    def submit_walk(self, observations, consent_version, model="", batch=150):
        """תצפיות מסרטון מסלול (מספרים בלבד) - במנות, תחת walk_id אחד."""
        walk_id, stored = None, 0
        for i in range(0, len(observations), batch):
            body = {"observations": observations[i:i + batch], "model": model,
                    "consent": {"accepted": True, "version": consent_version}}
            if walk_id:
                body["walk_id"] = walk_id
            r = self._req("POST", "/street-intel/walks", body)
            walk_id, stored = r["walk_id"], stored + r["stored"]
        return {"walk_id": walk_id, "stored": stored}

    def my_walks(self):
        return self._req("GET", "/street-intel/walks")

    def delete_walks(self, walk_id=None):
        return self._req("DELETE", "/street-intel/walks", params={"walk_id": walk_id} if walk_id else None)

    # ---------- real-time ----------
    def ack(self, notification_id):
        return self._req("POST", f"/notifications/{notification_id}/received")

    def poll(self, wait=0):
        rows = self._req("GET", "/notifications", params={"after": self._last_id, "wait": wait},
                         timeout=wait + TIMEOUT)["notifications"]
        return [r for r in rows if self._accept(r)]

    def _accept(self, row):
        self._last_id = max(self._last_id, int(row["id"]))
        if row["id"] in self._seen:
            return False
        self._seen.add(row["id"])
        return True

    def start_stream(self, on_notification, on_state=None):
        """thread רקע: SSE; אם נכשל - long-poll. כל התראה מאושרת לשרת לפני ה-callback."""
        if self._stream_thread and self._stream_thread.is_alive():
            return
        self._stream_stop.clear()
        self._stream_thread = threading.Thread(target=self._stream_loop, args=(on_notification, on_state),
                                               name="LG-Stream", daemon=True)
        self._stream_thread.start()

    def stop_stream(self):
        self._stream_stop.set()

    def _set_state(self, on_state, **kw):
        self.stream_state.update(kw)
        if on_state:
            try:
                on_state(dict(self.stream_state))
            except Exception:
                pass

    def _deliver(self, row, on_notification):
        try:
            self.ack(row["id"])
        except BackendError:
            pass  # נשלח שוב ב-poll הבא; השרת ממשיך להציג "לא נמסר" עד לאישור
        try:
            on_notification(row)
        except Exception:
            pass

    def sync_cursor(self, on_notification=None):
        """הפעלה ראשונה במכשיר (או אחרי התקנה מחדש): מתחילים מהסוף, ומקבלים רק התראות שלא התקבלו
        באף מכשיר - לא את כל ההיסטוריה."""
        data = self._req("GET", "/notifications/cursor")
        for row in data.get("unreceived", []):
            if self._accept(row) and on_notification:
                self._deliver(row, on_notification)
        self._last_id = max(self._last_id, int(data.get("last_id") or 0))

    def _stream_loop(self, on_notification, on_state):
        backoff = 1
        while not self._stream_stop.is_set():
            try:
                if self._last_id == 0:
                    self.sync_cursor(on_notification)
                for row in self.poll(0):  # השלמת פערים מהניתוק הקודם
                    self._deliver(row, on_notification)
                self._sse(on_notification, on_state)
                backoff = 1
            except BackendError as e:
                self._set_state(on_state, connected=False, error=e.message)
                if e.status == 401:
                    return
                self._stream_stop.wait(backoff)
                backoff = min(backoff * 2, 30)

    def _sse(self, on_notification, on_state):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"}
        try:
            with self._session.get(f"{self.base_url}/v1/stream", params={"after": self._last_id},
                                   headers=headers, stream=True, timeout=(TIMEOUT, 40)) as r:
                if r.status_code == 401:
                    raise BackendError(401, "invalid_token", "ההתחברות לשרת פגה")
                if r.status_code != 200:
                    raise BackendError(r.status_code, "stream", "הזרם לא זמין")
                self._set_state(on_state, connected=True, mode="stream", last_ok=time.time(), error=None)
                data_lines = []
                # chunk_size=1: ברירת המחדל (512) מחזיקה הודעות קטנות (עדכון מיקום) בבאפר עד
                # שמצטברים 512 בתים - עיכוב של עד 15 שניות בעדכון חי. נמצא בבדיקת קצה-לקצה.
                for raw in r.iter_lines(chunk_size=1, decode_unicode=True):
                    if self._stream_stop.is_set():
                        return
                    self.stream_state["last_ok"] = time.time()
                    if raw is None:
                        continue
                    if raw == "":
                        if data_lines:
                            row = json.loads("\n".join(data_lines))
                            data_lines = []
                            if self._accept(row):
                                self._deliver(row, on_notification)
                        continue
                    if raw.startswith("data: "):
                        data_lines.append(raw[6:])
        except requests.RequestException as exc:
            raise BackendUnavailable(f"הזרם נותק ({type(exc).__name__})") from exc

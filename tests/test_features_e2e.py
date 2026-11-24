# -*- coding: utf-8 -*-
"""
יכולות שרת: ליווי הביתה, אמון במתנדבים, מידע רפואי מוצפן, גיבוי SMS, Web Push, בדיקת מוכנות.

הכל מול שרת HTTP אמיתי + SQLite + thread הרקע. הגבולות החיצוניים בלבד מוחלפים, ובמפורש:
- ספק SMS (שירות בתשלום שדורש חשבון): RecordingSms רושם במקום לשלוח. שאר הלוגיקה - אמיתית.
- שליחת HTTP לשרת ה-Push של הדפדפן: service.push_sender רושם. ההצפנה עצמה נבדקת מול
  וקטור ה-RFC ב-test_webpush.py, והשליחה האמיתית מדפדפן נבדקת ב-test_pwa_e2e.py.
"""
import json
import sqlite3
import threading
import time
import urllib.request

import pytest

from backend_client import BackendClient, BackendError
from server import features as F
from server.lifeguard_server import create_server
from server.sms import SmsError

PW = "StrongPass!2026"
TLV = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "source": "GNSS"}


class RecordingSms:
    """במקום Twilio: רושם הודעות. קוד אימות נכון = 123456."""
    name, configured, can_verify = "recording", True, True

    def __init__(self):
        self.sent, self.verifications, self.fail = [], [], False

    def send(self, to, text):
        if self.fail:
            raise SmsError("provider down")
        self.sent.append((to, text))
        return f"SM{len(self.sent)}"

    def start_verification(self, to):
        self.verifications.append(to)
        return "pending"

    def check_verification(self, to, code):
        return code == "123456"


def _start(tmp_path, **kw):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=kw.pop("ack", 60),
                          tick_seconds=0.2, **kw)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path, escort_grace_seconds=0.6, public_url="https://lg.example")
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


@pytest.fixture
def sms_srv(tmp_path):
    sms = RecordingSms()
    httpd, url = _start(tmp_path, sms_provider=sms, sms_fallback_seconds=0.5, public_url="https://lg.example")
    yield httpd, url, sms
    httpd.shutdown()
    httpd.service.close()


def _c(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name.title())
    return c


def _connect(owner, contact):
    contact.respond_invitation(owner.invite(contact.username)["id"], True)


def _wait(pred, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


def _err(fn):
    with pytest.raises(BackendError) as e:
        fn()
    return e.value


def _types(client):
    return [(n["type"], n["payload"]) for n in client.poll()]


def _vol(url, name, pos, verified=False, httpd=None):
    v = _c(url, name)
    v.set_volunteer(True, 5)
    v.update_presence(pos)
    if verified:
        v.request_verification("מד\"א", "12345")
        httpd.service.admin_verify(name, True)
    return v


# ================================================================== 1. ליווי הביתה
def test_escort_shared_with_connected_contacts_only(srv):
    httpd, url = srv
    dana, mom, eve = _c(url, "dana"), _c(url, "mom"), _c(url, "eve")
    _connect(dana, mom)
    mom.poll()
    esc = dana.start_escort(20, "הביתה", location=TLV)
    assert esc["status"] == "active" and 1190 < esc["remaining_seconds"] <= 1200
    assert [w["username"] for w in esc["watchers"]] == ["mom"]
    started = [p for t, p in _types(mom) if t == "escort_started"]
    assert started and started[0]["destination"] == "הביתה"
    seen = mom.escorts()["watching"][0]
    assert seen["location"]["lat"] == TLV["lat"] and "watchers" not in seen
    assert _err(lambda: eve.get_escort(esc["id"])).status == 404
    assert _err(lambda: mom.escort_action(esc["id"], "arrived")).status == 403  # רק הבעלים
    assert _err(lambda: dana.start_escort(10)).code == "escort_active"
    dana.escort_location(esc["id"], {**TLV, "lat": 32.09})
    assert mom.get_escort(esc["id"])["location"]["lat"] == 32.09
    ext = dana.escort_action(esc["id"], "extend", 10)
    assert ext["remaining_seconds"] > 1700
    done = dana.escort_action(esc["id"], "arrived")
    assert done["status"] == "arrived"
    assert any(t == "escort_update" and p.get("event") == "arrived" for t, p in _types(mom))
    assert mom.escorts()["watching"] == []
    time.sleep(1)
    assert dana.inbox() == [] and not httpd.service._q("SELECT 1 FROM incidents", one=True)


def test_server_opens_incident_when_escort_expires_even_if_device_is_gone(srv):
    """הלב של הפיצ'ר: המכשיר 'מת' באמצע הדרך. השרת לבדו פותח אירוע עם המיקום האחרון."""
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    esc = dana.start_escort(15, "מהתחנה", location={**TLV, "lat": 32.0811})
    mom.poll()
    dana.poll()
    # הזמן עובר (קיצור: מזיזים את המועד במסד הנתונים; כל השאר - thread הרקע האמיתי)
    httpd.service._x("UPDATE escorts SET deadline=? WHERE id=?", (time.time() - 0.1, esc["id"]))
    assert _wait(lambda: any(t == "escort_overdue" for t, _ in _types(dana))), "המשתמש לא נשאל 'הכל בסדר?'"
    alarm = _wait(lambda: [p for t, p in _types(mom) if t == "escort_alarm"])
    assert alarm, "איש הקשר לא קיבל התראה"
    inc = mom.get_incident(alarm[0]["incident_id"])
    assert inc["location"]["lat"] == 32.0811 and inc["owner"] == "dana" and inc["severity"] == "high"
    owner_view = dana.get_incident(inc["id"])
    assert any(e["kind"] == "escort_expired" for e in owner_view["timeline"])
    assert dana.get_escort(esc["id"])["status"] == "escalated"


def test_device_sos_on_expiry_is_not_duplicated_by_server(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    esc = dana.start_escort(15, location=TLV)
    inc = dana.create_incident("dev-1", location=TLV, escort_id=esc["id"])
    assert dana.get_escort(esc["id"])["status"] == "escalated"
    httpd.service._x("UPDATE escorts SET deadline=? WHERE id=?", (time.time() - 5, esc["id"]))
    time.sleep(1.5)
    assert [r["id"] for r in httpd.service._q("SELECT id FROM incidents")] == [inc["id"]]


def test_arrival_inside_grace_period_cancels_alarm(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    esc = dana.start_escort(15, location=TLV)
    httpd.service._x("UPDATE escorts SET deadline=?, overdue_notified=1 WHERE id=?", (time.time() + 0.05, esc["id"]))
    dana.escort_action(esc["id"], "arrived")
    time.sleep(1.2)
    assert not httpd.service._q("SELECT 1 FROM incidents", one=True)


# ================================================================== 2. אמון במתנדבים
def test_verification_flow_and_admin_endpoints_are_protected(srv, monkeypatch):
    httpd, url = srv
    vol = _vol(url, "vol", {**TLV, "lat": 32.086})
    st = vol.request_verification("איחוד הצלה", "IH-778")
    assert st["verification"] == "pending" and st["verification_org"] == "איחוד הצלה"
    raw = urllib.request.Request(f"{url}/v1/admin/volunteers")
    with pytest.raises(urllib.error.HTTPError) as e:  # בלי טוקן ניהול מוגדר - הנתיב לא קיים
        urllib.request.urlopen(raw)
    assert e.value.code == 404
    monkeypatch.setenv("LIFEGUARD_ADMIN_TOKEN", "admin-secret-token-123")
    raw.add_header("Authorization", "Bearer wrong")
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(raw)
    ok = urllib.request.Request(f"{url}/v1/admin/volunteers?status=pending",
                                headers={"Authorization": "Bearer admin-secret-token-123"})
    listed = json.loads(urllib.request.urlopen(ok).read())["volunteers"]
    assert [v["username"] for v in listed] == ["vol"]
    req = urllib.request.Request(f"{url}/v1/admin/volunteers/vol/verify", method="POST",
                                 data=json.dumps({"approve": True}).encode(),
                                 headers={"Authorization": "Bearer admin-secret-token-123",
                                          "Content-Type": "application/json"})
    assert json.loads(urllib.request.urlopen(req).read())["verification"] == "verified"
    dana = _c(url, "dana")
    dana.update_presence(TLV)
    assert dana.community_summary(5)["volunteers"][0]["verified"] is True


def test_dispatch_prefers_verified_only_within_same_distance_band(srv):
    httpd, url = srv
    dana = _c(url, "dana")
    _vol(url, "unver_close", {**TLV, "lat": 32.0858})                       # ~50 מ'
    _vol(url, "ver_close", {**TLV, "lat": 32.0890}, True, httpd)              # ~400 מ' - אותו ק"מ
    assert [r["username"] for r in dana.create_incident("a", location=TLV)["recipients"]] == ["ver_close"]
    httpd.service.admin_set_suspended("ver_close", True)
    _vol(url, "ver_far", {**TLV, "lat": 32.1120}, True, httpd)                # ~3 ק"מ
    # המתנדב המאומת הרחוק לא עוקף מתנדב קרוב בהרבה - מהירות ההגעה קודמת
    assert [r["username"] for r in dana.create_incident("b", location=TLV)["recipients"]] == ["unver_close"]


def test_unverified_volunteer_monthly_limit(srv):
    httpd, url = srv
    vol = _vol(url, "vol", {**TLV, "lat": 32.086})
    for i in range(F.UNVERIFIED_MONTHLY_ACCEPTS):
        owner = _c(url, f"owner{i}")
        inc = owner.create_incident(f"x{i}", location=TLV)
        vol.respond(inc["id"], "accept")
        owner.update_incident(inc["id"], status="resolved")
    nxt = _c(url, "owner_next").create_incident("y", location=TLV)
    assert not any(r["role"] == "volunteer" for r in nxt["recipients"]), "מתנדב לא מאומת חרג מהמכסה"
    vol.request_verification("מד\"א", "MDA-1")
    httpd.service.admin_verify("vol", True)
    owner_next = _c(url, "owner_next")
    again = _c(url, "owner_after").create_incident("z", location=TLV)
    # תור העדיפויות: האירוע שחיכה בתור (אותה דחיפות, ותיק יותר) מקבל ראשון את המתנדב שהתפנה
    assert [r["username"] for r in owner_next.get_incident(nxt["id"])["recipients"] if r["role"] == "volunteer"] == ["vol"]
    assert not any(r["role"] == "volunteer" for r in again["recipients"])


def _feedback_setup(url, httpd, verified, age_days):
    vol = _vol(url, "vol", {**TLV, "lat": 32.086}, verified, httpd)
    incs = []
    for i in range(2):
        owner = _c(url, f"own{i}")
        httpd.service._x("UPDATE users SET created_at=? WHERE username=?", (time.time() - age_days * 86400, f"own{i}"))
        inc = owner.create_incident(f"f{i}", location=TLV)
        assert _err(lambda: owner.give_feedback(inc["id"], "vol", 5)).code == "not_responder"  # עוד לא קיבל
        vol.respond(inc["id"], "accept")
        owner.update_incident(inc["id"], status="resolved")   # מתנדב שמטפל באירוע לא מוזעק לאירוע נוסף במקביל
        incs.append((owner, inc))
    return vol, incs


def test_feedback_and_auto_suspension(srv):
    httpd, url = srv
    vol, incs = _feedback_setup(url, httpd, verified=False, age_days=10)
    stranger = _c(url, "stranger")
    assert _err(lambda: stranger.give_feedback(incs[0][1]["id"], "vol", 1)).status == 404
    assert _err(lambda: incs[0][0].give_feedback(incs[0][1]["id"], "vol", 9)).code == "invalid_rating"
    assert incs[0][0].give_feedback(incs[0][1]["id"], "vol", 2, "התנהגות לא הולמת")["volunteer_suspended"] is False
    assert incs[1][0].give_feedback(incs[1][1]["id"], "vol", 1, "ביקש כסף")["volunteer_suspended"] is True
    me = vol.me()["volunteer"]
    assert me["suspended"] is True and me["reports"] == 2 and me["avg_rating"] == 1.5
    later = _c(url, "later")
    later.update_presence(TLV)
    assert later.community_summary(5)["volunteers"] == []
    assert not any(r["role"] == "volunteer" for r in later.create_incident("g", location=TLV)["recipients"])


def test_throwaway_accounts_cannot_suspend_a_verified_volunteer(srv):
    """סקירת אבטחה: שני חשבונות זמניים השעו מתנדב מאומת. עכשיו: חשבונות חדשים לא נספרים, ומאומת
    לעולם לא מושעה אוטומטית - מסומן לבדיקת מנהל."""
    httpd, url = srv
    vol, incs = _feedback_setup(url, httpd, verified=True, age_days=0)          # חשבונות טריים
    for owner, inc in incs:
        assert owner.give_feedback(inc["id"], "vol", 1, "דיווח")["volunteer_suspended"] is False
    v = httpd.service._q("SELECT suspended, review_flag FROM volunteers WHERE username='vol'", one=True)
    assert (v["suspended"], v["review_flag"]) == (0, 0)
    for i in range(2):                                                         # אותם דיווחים מחשבונות ותיקים
        httpd.service._x("UPDATE users SET created_at=? WHERE username=?", (time.time() - 10 * 86400, f"own{i}"))
    assert incs[1][0].give_feedback(incs[1][1]["id"], "vol", 1, "דיווח")["volunteer_suspended"] is False
    v = httpd.service._q("SELECT suspended, review_flag FROM volunteers WHERE username='vol'", one=True)
    assert (v["suspended"], v["review_flag"]) == (0, 1)
    assert [r for r in httpd.service.admin_volunteers() if r["username"] == "vol"][0]["review_flag"] == 1


# ================================================================== 3. מידע רפואי
def test_medical_info_encrypted_and_shown_only_with_full_access_during_active_incident(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    vol = _vol(url, "vol", {**TLV, "lat": 32.086}, True, httpd)
    saved = dana.set_medical({"blood_type": "O-", "allergies": "פניצילין", "medications": "Ventolin",
                              "share": False, "evil": "ignored"})
    assert saved["blood_type"] == "O-" and "evil" not in saved and saved["share"] is False
    inc = dana.create_incident("m1", location=TLV)
    assert "medical" not in mom.get_incident(inc["id"])  # לא בחר/ה לשתף
    dana.update_incident(inc["id"], status="resolved")
    dana.set_medical({**saved, "share": True})
    inc = dana.create_incident("m2", location=TLV)
    assert mom.get_incident(inc["id"])["medical"]["allergies"] == "פניצילין"
    assert "medical" not in vol.get_incident(inc["id"])            # מתנדב לפני קבלה
    assert vol.respond(inc["id"], "accept")["medical"]["blood_type"] == "O-"
    dana.update_incident(inc["id"], status="resolved")
    assert "medical" not in mom.get_incident(inc["id"])            # אחרי סיום - לא
    con = sqlite3.connect(httpd.service.db_path)
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in ("פניצילין", "Ventolin", "O-\""):
        assert secret not in dump, "מידע רפואי נשמר כטקסט גלוי"


# ================================================================== 4. SMS
def test_sms_not_configured_is_reported_honestly(srv):
    httpd, url = srv
    dana = _c(url, "dana")
    dana.set_phone("050-1234567")
    assert dana.me()["sms"]["configured"] is False
    assert _err(dana.phone_verify_start).code == "sms_not_configured"
    assert httpd.service.health()["sms"]["configured"] is False
    assert dana.invite_phone("052-7654321", send_sms=True)["sms"] == "not_configured"


def test_phone_verification_removes_inviter_confirmation_step(sms_srv):
    httpd, url, sms = sms_srv
    dana, uncle = _c(url, "dana"), _c(url, "uncle")
    uncle.set_phone("054-555-0101")
    assert uncle.phone_verify_start()["sent"] and sms.verifications == ["+972545550101"]
    assert _err(lambda: uncle.phone_verify_check("000000")).code == "wrong_code"
    assert uncle.me()["phone_verified"] is False
    uncle.phone_verify_check("123456")
    assert uncle.me()["phone_verified"] is True
    inv = dana.invite_phone("054-555-0101", "Uncle", send_sms=True)
    assert inv["sms"] == "sent" and inv["code"] in sms.sent[-1][1] and sms.sent[-1][0] == "+972545550101"
    assert uncle.respond_invitation(inv["id"], True)["status"] == "connected"  # בלי אישור זהות נוסף
    uncle.set_phone("054-555-0102")  # מספר חדש -> לא מאומת עוד
    assert uncle.me()["phone_verified"] is False


def test_sms_fallback_with_location_link(sms_srv):
    httpd, url, sms = sms_srv
    dana, mom, dad = _c(url, "dana"), _c(url, "mom"), _c(url, "dad")
    for c, phone in ((mom, "050-111-2222"), (dad, "050-333-4444")):
        _connect(dana, c)
        c.set_phone(phone)
    mom.phone_verify_start()
    mom.phone_verify_check("123456")  # רק המספר של mom מאומת
    # שרשרת צלצול: mom קודם (חוברה ראשונה), dad אחרי שהצלצול אצלה נגמר. זמן צלצול קצר לבדיקה.
    httpd.service.ring_seconds = 0.4
    inc = dana.create_incident("s1", location=TLV)
    msg = _wait(lambda: [m for m in sms.sent if m[0] == "+972501112222"])
    assert msg, "לא נשלח SMS גיבוי"
    assert "Dana" in msg[0][1] and "https://lg.example/l/" in msg[0][1]
    view = _wait(lambda: (lambda v: v if {r["username"]: r["sms_status"] for r in v["recipients"]}.get("dad") else None)(
        dana.get_incident(inc["id"])))
    states = {r["username"]: (r["state"], r["sms_status"]) for r in view["recipients"]}
    assert states["mom"] == ("notified", "sent")      # SMS נשלח לספק != נמסר
    assert states["dad"] == ("notified", "skipped")   # מספר לא מאומת - לא שולחים מיקום
    assert not [m for m in sms.sent if m[0] == "+972503334444"]
    token = msg[0][1].split("/l/")[1].split()[0]
    page = urllib.request.urlopen(f"{url}/l/{token}").read().decode("utf-8")
    assert "32.08530" in page and "Dana" in page and "<script" not in page
    v = dana.get_incident(inc["id"])
    assert {r["username"]: r["state"] for r in v["recipients"]}["mom"] == "delivered"
    assert any(e["kind"] == "sms_opened" for e in v["timeline"])
    dana.update_incident(inc["id"], status="resolved")
    assert "32.08530" not in urllib.request.urlopen(f"{url}/l/{token}").read().decode("utf-8")
    assert "לא בתוקף" in urllib.request.urlopen(f"{url}/l/{'x' * 32}").read().decode("utf-8")


def test_no_sms_when_app_confirmed_receipt_and_provider_errors_are_recorded(sms_srv):
    httpd, url, sms = sms_srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    mom.set_phone("050-111-2222")
    mom.phone_verify_start()
    mom.phone_verify_check("123456")
    got = []
    mom.start_stream(got.append)
    # חלון של 0.5 שנ' לאישור קבלה היה קצר מדי תחת עומס (כשבדיקת הדפדפן רצה במקביל) - אז SMS נשלח
    # בצדק. כאן בודקים את ההחלטה, לא את מהירות המכונה: 3 שניות (בפועל: 60).
    httpd.service.sms_fallback_seconds = 3
    try:
        inc = dana.create_incident("ok1", location=TLV)
        assert _wait(lambda: any(n["type"] == "incident_new" for n in got))
        assert _wait(lambda: {r["username"]: r["state"] for r in dana.get_incident(inc["id"])["recipients"]}
                     .get("mom") == "delivered")
        time.sleep(3.5)
        assert sms.sent == [], "נשלח SMS למרות שהאפליקציה אישרה קבלה"
        dana.update_incident(inc["id"], status="resolved")
    finally:
        mom.stop_stream()
        httpd.service.sms_fallback_seconds = 0.5
    time.sleep(0.3)
    sms.fail = True
    inc2 = dana.create_incident("err1", location=TLV)
    # 'pending' הוא מצב ביניים (לפני שהספק ענה) - מחכים לתוצאה סופית
    view = _wait(lambda: (lambda v: v if v["recipients"][0]["sms_status"] in ("error", "sent", "skipped") else None)(
        dana.get_incident(inc2["id"])))
    assert view["recipients"][0]["sms_status"] == "error"
    assert any(e["kind"] == "sms_failed" for e in view["timeline"])


def test_location_page_escapes_html(sms_srv):
    httpd, url, sms = sms_srv
    evil = BackendClient(url)
    evil.login_or_register("evil", PW, "<script>alert(1)</script>")
    mom = _c(url, "mom")
    _connect(evil, mom)
    mom.set_phone("050-111-2222")
    mom.phone_verify_start()
    mom.phone_verify_check("123456")
    evil.create_incident("e", location=TLV)
    msg = _wait(lambda: sms.sent)
    token = msg[0][1].split("/l/")[1].split()[0]
    page = urllib.request.urlopen(f"{url}/l/{token}")
    html = page.read().decode("utf-8")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]


# ================================================================== 5. Web Push
def test_push_subscription_validation_and_delivery(srv):
    httpd, url = srv
    sent = []
    httpd.service.push_sender = lambda vapid, sub, data, urgency="normal": (sent.append((sub["endpoint"], data, urgency))
                                                                            or (201, None))
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    key = mom._req("GET", "/push/public-key")["public_key"]
    assert len(key) > 80
    from server import webpush
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    ua = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": webpush.b64u(ua),
                                                                                "auth": webpush.b64u(b"0123456789abcdef")}}
    assert _err(lambda: mom._req("POST", "/push/subscribe", {**good, "endpoint": "https://evil.example/x"})).code \
        == "push_endpoint"
    assert _err(lambda: mom._req("POST", "/push/subscribe", {**good, "keys": {"p256dh": "AAAA", "auth": "x"}})).code \
        == "push_keys"
    mom._req("POST", "/push/subscribe", good)
    assert mom._req("GET", "/push/status")["devices"] == 1
    inc = dana.create_incident("p1", location=TLV)
    assert _wait(lambda: any(d["type"] == "incident_new" for _, d, _u in sent))
    ep, data, urgency = next(s for s in sent if s[1]["type"] == "incident_new")
    assert urgency == "high" and "Dana" in data["title"] and data["url"].endswith(inc["id"])
    assert "32.08" not in json.dumps(data)  # אין מיקום במסך הנעילה
    # מנוי שבוטל בדפדפן (410) נמחק
    httpd.service.push_sender = lambda *a, **k: (410, "gone")
    httpd.service._notify("mom", "test", {"text": "x"})
    assert _wait(lambda: mom._req("GET", "/push/status")["devices"] == 0)


# ================================================================== 6. בדיקת מוכנות
def test_readiness_check_tracks_real_receipts(srv):
    httpd, url = srv
    dana, mom, dad = _c(url, "dana"), _c(url, "mom"), _c(url, "dad")
    assert _err(dana.run_readiness_check).code == "no_contacts"
    _connect(dana, mom)
    _connect(dana, dad)
    res = dana.run_readiness_check()
    assert {r["username"]: r["received_at"] for r in res["results"]} == {"mom": None, "dad": None}
    assert res["all_received"] is False
    got = []
    mom.start_stream(got.append)
    try:
        assert _wait(lambda: any(n["type"] == "readiness_check" for n in got))
        r2 = _wait(lambda: (lambda r: r if [x for x in r["results"] if x["username"] == "mom"][0]["received_at"] else None)(
            dana.readiness()))
        assert r2 and r2["id"] == res["id"] and r2["all_received"] is False   # dad עוד לא
    finally:
        mom.stop_stream()
    assert dana.readiness()["created_at"] == res["created_at"]  # נשמר בשרת - זמין מכל מכשיר


def test_static_files_block_path_traversal(srv):
    httpd, url = srv
    for bad in ("/app/../lifeguard_server.py", "/app/..%2Flifeguard_server.py", "/app/x.py", "/app/../../config.py"):
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url + bad)
        assert e.value.code == 404, bad

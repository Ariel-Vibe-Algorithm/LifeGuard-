# -*- coding: utf-8 -*-
"""
רגרסיות אבטחה: כל בדיקה כאן משחזרת התקפה שנמצאה בסקירת האבטחה (עם PoC) ומוודאת שהיא נחסמה.
שרת אמיתי (HTTP + SQLite), לקוחות אמיתיים. אם אחת נכשלת - פרצה חזרה.
"""
import datetime
import json
import os
import socket
import ssl
import threading
import time
import urllib.request

import pytest

from backend_client import BackendClient, BackendError
from server.lifeguard_server import create_server
from test_dispatch_queue import NEAR, _vols
from test_features_e2e import TLV, RecordingSms, _c, _connect, _err, _start, _vol

PW = "StrongPass!2026"


@pytest.fixture
def srv(tmp_path):
    httpd, url = _start(tmp_path, sms_provider=RecordingSms(), public_url="https://lg.example")
    yield httpd, url
    httpd.shutdown()
    httpd.service.close()


# ---------------------------------------------------------------- 1. איתור משתמשים/מתנדבים
def test_cannot_trilaterate_users_or_volunteers(srv):
    httpd, url = srv
    vol = _vol(url, "vol", {**TLV, "lat": 32.0861, "lon": 34.7822})
    target = _c(url, "target")
    target.update_presence({**TLV, "lat": 32.0905, "lon": 34.7790})
    spy = _c(url, "spy")
    # חיפוש בינארי על הרדיוס: מותרים רק רדיוסים קבועים
    spy.update_presence(TLV)
    radii = {spy.community_summary(r)["radius_km"] for r in (0.2, 0.37, 0.41, 0.9, 1.7, 4.4, 7.9, 50)}
    assert radii <= {1.0, 3.0, 5.0, 10.0}
    # הזזת "המיקום שלי" בצעדים קטנים לא משנה כלום בתוך תא רשת של ~500 מ'
    seen = set()
    for k in range(10):
        spy.update_presence({**TLV, "lat": 32.0851 + k * 0.0003, "lon": 34.7801})
        s = spy.community_summary(1)
        seen.add((tuple(v["distance_band_km"] for v in s["volunteers"]), s["nearby_users_count"], s["nearby_users_few"]))
    assert len(seen) <= 2                           # לכל היותר מעבר תא אחד - לא "מחוג" של מטרים
    # מעט משתמשים - אין מספר בכלל (k-אנונימיות)
    s = spy.community_summary(5)
    assert s["nearby_users_count"] is None and s["nearby_users_few"] is True
    # אירוע מזויף: בלי מרחק/זמן של מתנדב שלא קיבל, ובלי מרחק בציר הזמן
    inc = spy.create_incident("probe", location=TLV)
    view = spy.get_incident(inc["id"])
    v = next(r for r in view["recipients"] if r["role"] == "volunteer")
    assert v["distance_km"] is None and v["distance_band_km"] is None and v["eta_minutes"] is None
    assert not any("ק\"מ" in e["text"] for e in view["timeline"])
    # והמתנדב שעוד לא קיבל לא רואה מי במצוקה
    vv = vol.get_incident(inc["id"])
    assert vv["owner"] is None and vv["owner_display"] == "מישהו בקרבת מקום"


# ---------------------------------------------------------------- 2+3. איש קשר שנותק
def test_disconnected_contact_loses_closed_incidents_and_sms_link(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    mom.set_phone("+972541111111")
    mom.phone_verify_start()
    mom.phone_verify_check("123456")
    httpd.service.sms_fallback_seconds = 0.2
    inc = dana.create_incident("e1", location=TLV)
    dana.send_message(inc["id"], "אני במקלט, חדר 12")
    end = time.time() + 8
    while time.time() < end and not httpd.service.sms.sent:
        time.sleep(0.1)
    token = httpd.service.sms.sent[0][1].rsplit("/l/", 1)[1]
    page = lambda: urllib.request.urlopen(f"{url}/l/{token}").read().decode("utf-8")  # noqa: E731
    assert "32.085" in page()                                          # לפני הניתוק: מיקום חי
    old = dana.create_incident("e0", location=TLV)
    dana.update_incident(old["id"], status="resolved")                 # אירוע ישן שהסתיים
    dana.disconnect("mom", "my_contact")                               # ניתוק בזמן שהאירוע e1 עדיין פעיל
    dana.update_location(inc["id"], {**TLV, "lat": 32.2222})
    after = page()
    assert "לא בתוקף" in after and "32.2222" not in after and "32.085" not in after
    for iid in (inc["id"], old["id"]):
        assert _err(lambda: mom.get_incident(iid)).status == 404     # גם אירוע שכבר נסגר


# ---------------------------------------------------------------- 4. השתלטות על התור
def test_flooding_incidents_does_not_capture_volunteers_or_stall_the_tick(srv):
    httpd, url = srv
    for i in range(3):
        _vol(url, f"vol{i}", {**NEAR, "lat": 32.086 + i * 0.001})
    eve = _c(url, "eve")
    ids = [eve.create_incident(f"x{i}", location=TLV, risk={"level": "HIGH", "score": 8, "reasons": []})["id"]
           for i in range(8)]
    with_vols = [i for i in ids if _vols(eve.get_incident(i))]
    assert len(with_vols) <= 2                                       # רק 2 אירועים פעילים מקבלים מתנדבים
    # עוד מאות אירועים - סבב התור נשאר מהיר (סינון ב-SQL + המתנה לאירועים בלי מועמד)
    svc = httpd.service
    for i in range(300):
        svc.create_incident("eve", f"bulk{i}", location={"lat": 32.0853, "lon": 34.7818})
    t0 = time.time()
    svc.run_escalation_tick()
    assert time.time() - t0 < 0.5


# ---------------------------------------------------------------- 5. ניתוק מכשירים
def test_password_change_revokes_other_sessions(srv):
    httpd, url = srv
    victim = _c(url, "victim")
    abuser = BackendClient(url)
    abuser.login("victim", PW, "abuser laptop")
    assert len(victim.sessions()["sessions"]) == 2
    with pytest.raises(BackendError):
        victim.change_password("wrong", "NewPass!2027x")
    assert victim.change_password(PW, "NewPass!2027x")["other_sessions_revoked"] == 1
    with pytest.raises(BackendError) as e:
        abuser.circle()
    assert e.value.status == 401
    victim.circle()                                                   # המכשיר שלה ממשיך לעבוד
    third = BackendClient(url)
    third.login("victim", "NewPass!2027x", "x")
    sid = next(s["id"] for s in victim.sessions()["sessions"] if not s["current"])
    assert victim.revoke_sessions(sid)["revoked"] == 1
    with pytest.raises(BackendError):
        third.circle()


# ---------------------------------------------------------------- 6. ערוץ צד בסטטוס התראות
def test_notification_status_only_for_own_messages(srv):
    httpd, url = srv
    dana, mom, abuser = _c(url, "dana"), _c(url, "mom"), _c(url, "abuser")
    _connect(dana, mom)
    mine = dana.notify_trusted("בדיקה") if hasattr(dana, "notify_trusted") else None
    nid = httpd.service._notify("dana", "incident_new", {"x": 1})      # התראה שלא abuser יצר
    httpd.service._x("UPDATE notifications SET received_at=? WHERE id=?", (time.time(), nid))
    st = abuser._req("POST", "/notifications/status", {"ids": {"dana": nid}})
    assert st == {"dana": False}


# ---------------------------------------------------------------- 10. ליווי שקט
def test_silent_escort_never_alarms_on_owner_lock_screen(srv):
    httpd, url = srv
    httpd.service.escort_grace = 0.2
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    e = dana.start_escort(5, mode="silent", location=TLV)
    eid = e["id"] if "id" in e else e["escort"]["id"]
    httpd.service._x("UPDATE escorts SET deadline=? WHERE id=?", (time.time() - 1, eid))   # הזמן עבר
    end = time.time() + 10
    while time.time() < end:
        httpd.service.run_escalation_tick()
        if httpd.service._q("SELECT status FROM escorts WHERE id=?", (eid,), one=True)["status"] == "escalated":
            break
        time.sleep(0.1)
    rows = httpd.service._q("SELECT type, payload FROM notifications WHERE username='dana'")
    assert not any(r["type"] == "escort_alarm" for r in rows)
    over = [json.loads(r["payload"]) for r in rows if r["type"] == "escort_overdue"]
    assert over and all("חירום" not in o["text"] and "ליווי" not in o["text"] for o in over)
    assert any(r["type"] == "escort_alarm" for r in httpd.service._q("SELECT type FROM notifications WHERE username='mom'"))


# ---------------------------------------------------------------- 11+12. פרוטוקול
def test_negative_content_length_and_chunked_are_rejected(srv):
    httpd, url = srv
    host, port = url.replace("http://", "").split(":")
    for headers in ("Content-Length: -1\r\n", "Transfer-Encoding: chunked\r\n"):
        s = socket.create_connection((host, int(port)), timeout=5)
        body = json.dumps({"username": "bigbody", "password": "x" * 100}).encode()
        s.sendall(f"POST /v1/auth/register HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n{headers}"
                  f"Connection: close\r\n\r\n".encode() + body)
        s.shutdown(socket.SHUT_WR)
        resp = s.recv(200).decode(errors="replace")
        s.close()
        assert resp.split()[1] in ("411", "413"), resp
    assert not httpd.service._q("SELECT 1 FROM users WHERE username='bigbody'", one=True)


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
    c, k = str(tmp_path / "c.pem"), str(tmp_path / "k.pem")
    open(c, "wb").write(cert.public_bytes(serialization.Encoding.PEM))
    open(k, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return c, k


def test_idle_tls_connection_does_not_block_the_server(tmp_path):
    cert, key = _self_signed(tmp_path)
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "s.db"), certfile=cert, keyfile=key, tick_seconds=0.5)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    idle = socket.create_connection(("127.0.0.1", port))          # TCP בלי handshake - "תוקע" שרת פגיע
    try:
        time.sleep(0.3)
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        t0 = time.time()
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/v1/health", context=ctx, timeout=4) as r:
            assert r.status == 200
        assert time.time() - t0 < 3
    finally:
        idle.close()
        httpd.shutdown()
        httpd.service.close()


# ---------------------------------------------------------------- 13+15. מרוצים / מצב reassigned
def test_late_expiry_never_overwrites_an_acceptance(srv):
    httpd, url = srv
    vol = _vol(url, "vol", NEAR)
    dana = _c(url, "dana")
    inc = dana.create_incident("r", location=TLV)
    httpd.service._x("UPDATE recipients SET notified_at=0 WHERE incident_id=?", (inc["id"],))   # "פג" מבחינת הזמן
    vol.respond(inc["id"], "accept")                     # אבל קיבל לפני שהסבב רץ
    httpd.service.run_escalation_tick()
    assert vol.get_incident(inc["id"])["my_state"] == "accepted"
    st = httpd.service._q("SELECT state FROM recipients WHERE incident_id=? AND username='vol'", (inc["id"],), one=True)
    assert st["state"] == "accepted"


def test_reassigned_volunteer_stops_receiving_updates(srv):
    httpd, url = srv
    vol = _vol(url, "vol", NEAR)
    low_owner = _c(url, "low")
    low = low_owner.create_incident("l", location=TLV, risk={"level": "LOW", "score": 0, "reasons": []})
    _c(url, "urgent").create_incident("u", location=TLV, emergency_type="physical_danger", severity="critical",
                                      risk={"level": "HIGH", "score": 7, "reasons": []})
    before = httpd.service._q("SELECT MAX(id) AS m FROM notifications WHERE username='vol'", one=True)["m"]
    low_owner.send_message(low["id"], "סוד")
    low_owner.update_location(low["id"], {**TLV, "lat": 32.0866})
    leaked = httpd.service._q("SELECT type FROM notifications WHERE username='vol' AND id>? AND incident_id=?",
                              (before, low["id"]))
    assert leaked == []


# ---------------------------------------------------------------- נעילת חשבון בכוונה
def test_attacker_cannot_lock_victim_out_from_her_device(srv):
    httpd, url = srv
    _c(url, "victim")
    svc = httpd.service
    for _ in range(8):                                    # תוקף מכתובת אחרת
        with pytest.raises(Exception):
            svc.login("victim", "wrong", ip="203.0.113.9")
    with pytest.raises(Exception) as e:
        svc.login("victim", PW, ip="203.0.113.9")          # התוקף עצמו נעול
    assert getattr(e.value, "status", None) == 429
    assert svc.login("victim", PW, ip="198.51.100.7")["token"]   # הקורבן ממכשיר אחר - נכנסת


# ---------------------------------------------------------------- keep-alive: גוף שלא נקרא
def test_unread_request_body_does_not_poison_next_request_on_same_connection(srv):
    """באג שנמצא באינטגרציה עם אנדרואיד: POST עם גוף ל-handler שלא קורא אותו (אישור קבלה / יציאה)
    השאיר את הגוף בחיבור, והבקשה הבאה באותו חיבור פוענחה כ-"{}POST" -> 501."""
    import http.client
    httpd, url = srv
    c = _c(url, "dana")
    host, port = url.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=5)
    hdr = {"Authorization": f"Bearer {c.token}", "Content-Type": "application/json"}
    nid = httpd.service._notify("dana", "info", {"text": "x"})
    for _ in range(3):
        conn.request("POST", f"/v1/notifications/{nid}/received", body="{}", headers=hdr)
        r = conn.getresponse()
        r.read()
        assert r.status == 200
        conn.request("GET", "/v1/circle", headers=hdr)          # אותו חיבור
        r = conn.getresponse()
        r.read()
        assert r.status == 200, r.status
    conn.request("GET", "/v1/nope", body='{"a": 1}', headers=hdr)   # 404 עם גוף
    r = conn.getresponse()
    r.read()
    conn.request("GET", "/v1/circle", headers=hdr)
    r = conn.getresponse()
    r.read()
    assert r.status == 200
    conn.close()


# ---------------------------------------------------------------- סקירה 2
def test_ring_chain_continues_when_ringing_contact_disconnects(srv):
    """ממצא חמור: איש הקשר שמצלצלים אליו מתנתק -> מצוקה שקטה לא הגיעה לאף אחד. עכשיו: עוברים לבא."""
    httpd, url = srv
    httpd.service.ring_seconds = 600
    dana, sis, mom = _c(url, "dana"), _c(url, "sis"), _c(url, "mom")
    _connect(dana, sis)
    _connect(dana, mom)
    inc = dana.create_incident("s", mode="silent", location=TLV)
    st = lambda: {r["username"]: (r["state"], r["ring_status"]) for r in httpd.service._q(  # noqa: E731
        "SELECT username, state, ring_status FROM recipients WHERE incident_id=? AND role='trusted'", (inc["id"],))}
    ringing = [u for u, (_, rs) in st().items() if rs == "ringing"][0]
    other = "mom" if ringing == "sis" else "sis"
    (sis if ringing == "sis" else mom).disconnect("dana", "contact_for")
    assert st()[other][1] == "ringing"                                   # עבר מיד לבא בתור
    # הבא גם מתנתק -> אין עוד אנשי קשר -> מתנדבים
    vol = _vol(url, "vol", NEAR)
    (mom if other == "mom" else sis).disconnect("dana", "contact_for")
    assert _vols(dana.get_incident(inc["id"])) == ["vol"]
    assert vol.inbox()


def test_queued_contact_never_learns_about_the_incident(srv):
    httpd, url = srv
    httpd.service.ring_seconds = 600
    dana, sis, mom = _c(url, "dana"), _c(url, "sis"), _c(url, "mom")
    _connect(dana, sis)
    _connect(dana, mom)
    inc = dana.create_incident("q", mode="silent", location=TLV)
    queued = [r["username"] for r in httpd.service._q(
        "SELECT username FROM recipients WHERE incident_id=? AND state='queued'", (inc["id"],))][0]
    dana.disconnect(queued, "my_contact")
    dana.update_incident(inc["id"], status="resolved")
    seen = httpd.service._q("SELECT type FROM notifications WHERE username=? AND incident_id=?", (queued, inc["id"]))
    assert seen == []                                                    # לא "נסגר", לא "נותקת" - כלום


def test_unaccepted_volunteer_closure_text_has_no_owner_name(srv):
    httpd, url = srv
    _vol(url, "vol", NEAR)
    dana = _c(url, "dana")
    inc = dana.create_incident("v", location=TLV)
    dana.update_incident(inc["id"], status="resolved")
    p = json.loads(httpd.service._q("SELECT payload FROM notifications WHERE username='vol' AND type='incident_closed'",
                                    one=True)["payload"])
    assert "Dana" not in p["text"] and "dana" not in p["text"]


def test_disconnected_contact_stops_getting_escort_updates(srv):
    httpd, url = srv
    dana, mom = _c(url, "dana"), _c(url, "mom")
    _connect(dana, mom)
    e = dana.start_escort(30, location=TLV)
    eid = e["id"] if "id" in e else e["escort"]["id"]
    dana.disconnect("mom", "my_contact")
    before = httpd.service._q("SELECT COUNT(*) AS n FROM notifications WHERE username='mom'", one=True)["n"]
    dana.escort_action(eid, "arrived")
    after = httpd.service._q("SELECT COUNT(*) AS n FROM notifications WHERE username='mom' AND type LIKE 'escort%'",
                             one=True)["n"]
    assert httpd.service._q("SELECT COUNT(*) AS n FROM notifications WHERE username='mom'", one=True)["n"] == before
    assert after == 0 or after == httpd.service._q(
        "SELECT COUNT(*) AS n FROM notifications WHERE username='mom' AND type LIKE 'escort%'", one=True)["n"]


def test_password_guessing_is_rate_limited(srv):
    httpd, url = srv
    c = _c(url, "victim")
    codes = []
    for _ in range(8):
        try:
            c.change_password("wrong-guess", "NewPass!2027x")
        except BackendError as e:
            codes.append(e.status)
    assert 429 in codes and codes[:5] == [403] * 5


def test_registration_rate_limit_and_new_accounts_get_no_priority_privileges(srv, monkeypatch):
    from server import abuse
    httpd, url = srv
    svc = httpd.service
    for i in range(svc.REGISTER_PER_IP_HOUR):
        svc.register(f"bot{i}", PW, ip="203.0.113.7")
    with pytest.raises(Exception) as e:
        svc.register("bot_more", PW, ip="203.0.113.7")
    assert getattr(e.value, "status", None) == 429
    svc.register("someone_else", PW, ip="198.51.100.2")                  # רשת אחרת - לא נחסמת
    monkeypatch.setattr(abuse, "NEW_ACCOUNT_HOURS", 24)
    ab = svc.owner_abuse("bot0")
    assert ab["limited"] and any("חשבון חדש" in r for r in ab["reasons"])
    svc._x("UPDATE users SET created_at=? WHERE username='bot0'", (time.time() - 3 * 86400,))
    assert not svc.owner_abuse("bot0")["limited"]


def test_xff_trusted_only_from_private_proxy(monkeypatch):
    from server import lifeguard_server as LS
    monkeypatch.setenv("LIFEGUARD_BEHIND_PROXY", "1")
    h = type("H", (), {})()
    h.headers = {"X-Forwarded-For": "1.2.3.4, 9.9.9.9"}
    h.client_address = ("172.18.0.5", 1234)                              # Caddy ברשת docker
    assert LS._client_ip(h) == "9.9.9.9"
    h.client_address = ("8.8.8.8", 1234)                                 # ישירות מהאינטרנט - XFF מזויף
    assert LS._client_ip(h) == "8.8.8.8"


def test_stream_limit_per_user(srv):
    httpd, url = srv
    svc = httpd.service
    assert all(svc.acquire_stream("u") for _ in range(svc.MAX_STREAMS_PER_USER))
    assert not svc.acquire_stream("u")
    svc.release_stream("u")
    assert svc.acquire_stream("u")

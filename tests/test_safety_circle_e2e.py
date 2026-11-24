# -*- coding: utf-8 -*-
"""
מעגל הביטחון - בדיקות קצה-לקצה מול שרת LifeGuard אמיתי (HTTP + SSE + SQLite + thread התפוגה).
אין mocks: כל בדיקה עוברת דרך BackendClient -> HTTP -> LifeGuardService -> מסד הנתונים, וההתראות
מגיעות דרך זרם ה-SSE האמיתי ומאושרות לשרת.
"""
import sqlite3
import threading
import time

import pytest

import auth_module
from backend_client import BackendClient, BackendError
from server import lifeguard_server as srv
from server.lifeguard_server import create_server

PW = "StrongPass!2026"
TLV = {"lat": 32.0853, "lon": 34.7818, "is_precise": True, "source": "GNSS"}
NEAR = {"lat": 32.0880, "lon": 34.7830, "is_precise": True, "source": "GNSS"}


def _start(tmp_path, **kw):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=kw.pop("ack", 60),
                          tick_seconds=0.2, **kw)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture
def server(tmp_path):
    httpd, url = _start(tmp_path)
    yield url, httpd
    httpd.shutdown()
    httpd.service.close()


@pytest.fixture
def short_ttl_server(tmp_path):
    httpd, url = _start(tmp_path, invite_ttl_seconds=1.0)
    yield url, httpd
    httpd.shutdown()
    httpd.service.close()


@pytest.fixture
def no_cooldown(monkeypatch):
    monkeypatch.setattr(srv, "RESEND_COOLDOWN_SECONDS", 0)


def _client(url, name):
    c = BackendClient(url)
    c.login_or_register(name, PW, name.title())
    return c


def _wait(pred, timeout=15.0):   # מסתיים מיד כשהתנאי מתקיים; 5s לא הספיקו תחת עומס של כל הסוויטה
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return None


class Inbox:
    """זרם SSE אמיתי של משתמש - אוסף את ההתראות שהאפליקציה שלו קיבלה ואישרה."""

    def __init__(self, client):
        self.client, self.items = client, []
        client.start_stream(self.items.append)

    def wait(self, ntype, **match):
        def find():
            for n in self.items:
                p = n.get("payload") or {}
                if n["type"] == ntype and all(p.get(k) == v for k, v in match.items()):
                    return n
        return _wait(find)

    def stop(self):
        self.client.stop_stream()


def _err(fn):
    with pytest.raises(BackendError) as e:
        fn()
    return e.value


def _sent(client, inv_id):
    return next(i for i in client.circle()["sent_invitations"] if i["id"] == inv_id)


# ------------------------------------------------------------------ registration / SOS fallback
def test_registration_without_contacts_succeeds(temp_env):
    ok, msg = auth_module.register_user("solo", PW, [], voice_secret_word="red siren",
                                        silent_alarm_phrase="blue lantern", secret_cancel_phrase="green garden",
                                        terms_accepted=True)
    assert ok, msg
    assert auth_module.get_contacts("solo") == []


@pytest.mark.parametrize("mode", ["loud", "silent"])
def test_sos_without_connected_contacts_still_reaches_nearby_volunteer(server, mode):
    url, _ = server
    solo, vol = _client(url, "solo"), _client(url, "vol")
    vol.set_volunteer(True, 5)
    vol.update_presence(NEAR)
    inc = solo.create_incident(f"c-{mode}", mode=mode, location=TLV)
    vols = [r for r in inc["recipients"] if r["role"] == "volunteer"]
    assert [r["username"] for r in vols] == ["vol"], f"SOS ({mode}) בלי אנשי קשר לא הגיע למתנדב"
    assert vol.inbox()[0]["id"] == inc["id"]
    if mode == "silent":
        assert any(e["kind"] == "fallback" for e in inc["timeline"])


def test_silent_sos_with_connected_contact_does_not_go_to_volunteers(server):
    url, _ = server
    a, mom, vol = _client(url, "a_user"), _client(url, "mom"), _client(url, "vol")
    vol.set_volunteer(True, 5)
    vol.update_presence(NEAR)
    inv = a.invite("mom")
    mom.respond_invitation(inv["id"], True)
    inc = a.create_incident("s1", mode="silent", location=TLV)
    assert {r["role"] for r in inc["recipients"]} == {"trusted"}


def test_silent_fallback_escalates_to_next_volunteer(tmp_path):
    httpd, url = _start(tmp_path, ack=0.5)
    try:
        solo, v1, v2 = _client(url, "solo"), _client(url, "v_one"), _client(url, "v_two")
        for v, pos in ((v1, NEAR), (v2, {**NEAR, "lat": 32.095})):
            v.set_volunteer(True, 5)
            v.update_presence(pos)
        inc = solo.create_incident("sf", mode="silent", location=TLV)
        view = _wait(lambda: (lambda v: v if len([r for r in v["recipients"] if r["role"] == "volunteer"]) == 2
                              else None)(solo.get_incident(inc["id"])), 6)
        assert view, "מצוקה שקטה ללא אנשי קשר לא הוסלמה למתנדב הבא"
    finally:
        httpd.shutdown()
        httpd.service.close()


# ------------------------------------------------------------------ invite existing user
def test_invite_existing_user_accept_flow_with_real_push(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    mom_feed, dana_feed = Inbox(mom), Inbox(dana)
    try:
        inv = dana.invite("mom")
        assert inv["status"] == "pending" and inv["channel"] == "username" and "code" not in inv
        assert inv["expires_at"] > time.time() + 6 * 86400
        assert mom_feed.wait("relationship_invite", id=inv["id"]), "ההזמנה לא נדחפה לאפליקציה של הנמען"
        assert mom.circle()["received_invitations"][0]["from"] == "dana"
        assert dana.circle()["my_contacts"] == []  # עדיין לא מחובר - רק אחרי אישור
        res = mom.respond_invitation(inv["id"], True)
        assert res["status"] == "connected"
        assert dana_feed.wait("relationship_update", invitation_id=inv["id"], status="connected")
        d, m = dana.circle(), mom.circle()
        assert [c["username"] for c in d["my_contacts"]] == ["mom"] and d["my_contacts"][0]["status"] == "connected"
        assert [c["username"] for c in m["i_am_contact_for"]] == ["dana"]
        assert m["received_invitations"] == [] and d["sent_invitations"] == []
        # הקשר פעיל: SOS מגיע ל-mom
        inc = dana.create_incident("e1", location=TLV)
        assert any(r["username"] == "mom" and r["role"] == "trusted" for r in inc["recipients"])
    finally:
        mom_feed.stop()
        dana_feed.stop()


def test_decline_and_reinvite_cooldown(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite("mom")
    assert mom.respond_invitation(inv["id"], False)["status"] == "declined"
    assert _sent(dana, inv["id"])["status"] == "declined"
    assert dana.circle()["my_contacts"] == []
    assert _err(lambda: dana.invite("mom")).code == "declined_recently"  # אין הטרדה בהזמנות חוזרות
    assert _err(lambda: mom.respond_invitation(inv["id"], True)).status == 409  # אי אפשר "להחיות" אחרי סירוב


def test_expiration(short_ttl_server):
    url, _ = short_ttl_server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    dana_feed = Inbox(dana)
    try:
        inv = dana.invite("mom")
        assert dana_feed.wait("relationship_update", invitation_id=inv["id"], status="expired"), \
            "השולח לא קיבל עדכון על פקיעת ההזמנה"
        assert _sent(dana, inv["id"])["status"] == "expired"
        assert mom.circle()["received_invitations"] == []
        e = _err(lambda: mom.respond_invitation(inv["id"], True))
        assert e.status == 410 and e.code == "invitation_expired"
        assert dana.circle()["my_contacts"] == []
    finally:
        dana_feed.stop()


def test_resend_extends_expired_invitation_and_rotates_code(short_ttl_server, no_cooldown):
    url, httpd = short_ttl_server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite_phone("050-123-4567", "Mom")
    old_code = inv["code"]
    assert _wait(lambda: _sent(dana, inv["id"])["status"] == "expired")   # 4s לא הספיקו תחת עומס הסוויטה
    httpd.service.invite_ttl = 3600  # השליחה מחדש מקבלת תוקף רגיל
    again = dana.resend_invitation(inv["id"])
    assert again["status"] == "pending" and again["resend_count"] == 1 and again["code"] != old_code
    assert _err(lambda: mom.redeem_code(old_code)).code == "invalid_code"  # הקוד הקודם בוטל (חד-פעמי)
    assert mom.redeem_code(again["code"])["status"] == "connected"


def test_resend_limits(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite("mom")
    assert _err(lambda: dana.resend_invitation(inv["id"])).code == "resend_too_soon"
    srv_cool = srv.RESEND_COOLDOWN_SECONDS
    try:
        srv.RESEND_COOLDOWN_SECONDS = 0
        for _ in range(srv.MAX_RESENDS):
            dana.resend_invitation(inv["id"])
        assert _err(lambda: dana.resend_invitation(inv["id"])).code == "too_many_resends"
    finally:
        srv.RESEND_COOLDOWN_SECONDS = srv_cool
    assert len([n for n in mom.poll() if n["type"] == "relationship_invite"]) == 1 + srv.MAX_RESENDS


def test_cancel_pending_invitation(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    mom_feed = Inbox(mom)
    try:
        inv = dana.invite("mom")
        assert dana.cancel_invitation(inv["id"])["status"] == "cancelled"
        assert mom_feed.wait("relationship_update", invitation_id=inv["id"], status="cancelled")
        assert mom.circle()["received_invitations"] == []
        assert _err(lambda: mom.respond_invitation(inv["id"], True)).status == 409
        assert dana.circle()["my_contacts"] == []
        assert dana.invite("mom")["status"] == "pending"  # אחרי ביטול מותר להזמין מחדש
    finally:
        mom_feed.stop()


def test_disconnect_either_side_revokes_active_incident_access(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    mom.respond_invitation(dana.invite("mom")["id"], True)
    inc = dana.create_incident("e1", location=TLV)
    assert mom.get_incident(inc["id"])["full_access"]
    mom_feed = Inbox(mom)
    try:
        dana.disconnect("mom", "my_contact")
        assert mom_feed.wait("relationship_removed", owner="dana", contact="mom")
        assert mom_feed.wait("incident_closed", incident_id=inc["id"], reason="disconnected")
    finally:
        mom_feed.stop()
    assert _err(lambda: mom.get_incident(inc["id"])).status == 404  # גישה למיקום נחסמה מיד
    assert dana.circle()["my_contacts"] == [] and mom.circle()["i_am_contact_for"] == []
    assert not any(r["username"] == "mom" for r in dana.create_incident("e2", location=TLV)["recipients"])
    # ניתוק מהצד של איש הקשר
    mom.respond_invitation(dana.invite("mom")["id"], True)
    mom.disconnect("dana", "contact_for")
    assert dana.circle()["my_contacts"] == []
    assert _err(lambda: mom.disconnect("dana", "contact_for")).code == "not_connected"


# ------------------------------------------------------------------ validation / security
def test_duplicate_and_already_connected_are_rejected(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite("mom")
    e = _err(lambda: dana.invite("MOM"))
    assert e.code == "duplicate_invitation" and e.data["invitation_id"] == inv["id"]
    mom.respond_invitation(inv["id"], True)
    assert _err(lambda: dana.invite("mom")).code == "already_connected"
    assert _err(lambda: dana.invite("dana")).code == "self_invite"
    assert _err(lambda: dana.invite("nobody_here")).code == "no_such_user"
    # רק הזמנה אחת לנמען - אין התראות כפולות ממקרי הכישלון
    assert len([n for n in mom.poll() if n["type"] == "relationship_invite"]) == 1


def test_duplicate_phone_invitation(server):
    url, _ = server
    dana = _client(url, "dana")
    first = dana.invite_phone("+972 50-123-4567")
    e = _err(lambda: dana.invite_phone("050 1234567"))  # אותו מספר בפורמט אחר
    assert e.code == "duplicate_invitation" and e.data["invitation_id"] == first["id"]


def test_unauthorized_actions(server):
    url, _ = server
    dana, mom, eve = _client(url, "dana"), _client(url, "mom"), _client(url, "eve")
    inv = dana.invite("mom")
    assert _err(lambda: eve.respond_invitation(inv["id"], True)).status == 404   # לא הנמען
    assert _err(lambda: eve.cancel_invitation(inv["id"])).status == 404
    assert _err(lambda: eve.resend_invitation(inv["id"])).status == 404
    assert _err(lambda: mom.cancel_invitation(inv["id"])).status == 404          # רק השולח מבטל
    assert _err(lambda: dana.respond_invitation(inv["id"], True)).status == 404  # השולח לא מאשר לעצמו
    assert _err(lambda: eve.disconnect("dana")).status == 404
    assert _err(lambda: BackendClient(url).circle()).status == 401
    code_inv = dana.invite_code()
    assert _err(lambda: dana.redeem_code(code_inv["code"])).code == "self_invite"
    assert eve.circle()["received_invitations"] == []
    assert dana.circle()["my_contacts"] == []


def test_username_reuse_cannot_hijack_invitation(server):
    """ההזמנה קשורה למזהה הייחודי של המשתמש, לא רק לשם."""
    url, httpd = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite("mom")
    httpd.service._x("UPDATE users SET user_id='usr_someone_else' WHERE username='mom'")
    assert _err(lambda: mom.respond_invitation(inv["id"], True)).status == 404


def test_code_bruteforce_is_rate_limited(server):
    url, _ = server
    eve = _client(url, "eve")
    for i in range(srv.REDEEM_MAX_FAILURES):
        assert _err(lambda: eve.redeem_code(f"AAAA-BBBB-{i:04d}")).code == "invalid_code"
    assert _err(lambda: eve.redeem_code("AAAA-BBBB-CCCC")).code == "too_many_attempts"


def test_phone_numbers_and_codes_are_not_stored_in_plaintext(server, tmp_path):
    url, httpd = server
    dana, bob = _client(url, "dana"), _client(url, "bob")
    bob.set_phone("052-777-8899")
    inv = dana.invite_phone("052-777-8899")
    con = sqlite3.connect(httpd.service.db_path)
    try:
        dump = "\n".join(con.iterdump())
    finally:
        con.close()
    for secret in ("7778899", "527778899", inv["code"], inv["code"].replace("-", "")):
        assert secret not in dump
    assert _sent(dana, inv["id"])["phone_last4"] == "8899"
    assert "code" not in _sent(dana, inv["id"])  # הקוד מוצג רק ברגע היצירה


def test_concurrent_accept_and_cancel_have_single_outcome(server):
    url, _ = server
    for round_ in range(5):
        dana, mom = _client(url, f"dana{round_}"), _client(url, f"mom{round_}")
        inv = dana.invite(f"mom{round_}")
        results = {}
        barrier = threading.Barrier(2)

        def run(name, fn):
            barrier.wait()
            try:
                results[name] = fn()["status"]
            except BackendError as e:
                results[name] = e.code

        ts = [threading.Thread(target=run, args=("accept", lambda: mom.respond_invitation(inv["id"], True))),
              threading.Thread(target=run, args=("cancel", lambda: dana.cancel_invitation(inv["id"])))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        connected = [c["username"] for c in dana.circle()["my_contacts"]]
        if results["accept"] == "connected":
            assert results["cancel"] == "invitation_not_pending" and connected == [f"mom{round_}"]
        else:
            assert results["cancel"] == "cancelled" and connected == []


def test_two_devices_accepting_same_invitation_is_idempotent(server):
    url, _ = server
    dana = _client(url, "dana")
    _client(url, "mom")
    phone, laptop = BackendClient(url), BackendClient(url)
    phone.login("mom", PW, "phone")
    laptop.login("mom", PW, "laptop")
    inv = dana.invite("mom")
    assert phone.respond_invitation(inv["id"], True)["status"] == "connected"
    assert laptop.respond_invitation(inv["id"], True)["status"] == "connected"
    assert _err(lambda: laptop.respond_invitation(inv["id"], False)).status == 409
    assert len(dana.circle()["my_contacts"]) == 1


# ------------------------------------------------------------------ new users (not installed yet)
def test_new_user_joins_with_invite_code_after_installing(server):
    url, _ = server
    dana = _client(url, "dana")
    dana_feed = Inbox(dana)
    try:
        inv = dana.invite_code("Grandpa")
        code = inv["code"]
        assert len(code) == 14 and inv["status"] == "pending"
        # ...סבא מתקין LifeGuard ונרשם רק עכשיו
        grandpa = _client(url, "grandpa")
        preview = grandpa.preview_code(code.lower().replace("-", " "))  # הקלדה חופשית
        assert preview["from"] == "dana" and _sent(dana, inv["id"])["status"] == "pending"  # תצוגה לא צורכת
        assert grandpa.redeem_code(code)["status"] == "connected"
        assert dana_feed.wait("relationship_update", invitation_id=inv["id"], status="connected")
        assert [c["username"] for c in dana.circle()["my_contacts"]] == ["grandpa"]
        stranger = _client(url, "stranger")
        assert _err(lambda: stranger.redeem_code(code)).status == 409  # חד-פעמי
    finally:
        dana_feed.stop()


@pytest.fixture
def sms_server(tmp_path):
    from test_features_e2e import RecordingSms, _start as _fstart
    httpd, url = _fstart(tmp_path, sms_provider=RecordingSms())
    yield url, httpd
    httpd.shutdown()
    httpd.service.close()


def test_phone_invitation_reaches_only_a_verified_phone(sms_server):
    """הזמנה לטלפון של מי שעוד לא נרשם. אחרי הרשמה + טלפון *לא מאומת* - לא מגיע כלום (אחרת כל אחד
    יכול לטעון למספר ולגלות מי מזמין). אחרי אימות SMS - ההזמנה מגיעה והקשר נוצר ישירות."""
    url, _ = sms_server
    dana = _client(url, "dana")
    inv = dana.invite_phone("054-555-0101", "Uncle")
    uncle = _client(url, "uncle")
    uncle_feed = Inbox(uncle)
    try:
        uncle.set_phone("+972545550101")
        time.sleep(0.6)
        assert not any(n["type"] == "relationship_invite" for n in uncle_feed.items)
        assert uncle.circle()["received_invitations"] == []
        assert _err(lambda: uncle.respond_invitation(inv["id"], True)).status == 404
        uncle.phone_verify_start()
        uncle.phone_verify_check("123456")
        assert uncle_feed.wait("relationship_invite", id=inv["id"])
        rec = uncle.circle()["received_invitations"]
        assert rec[0]["from"] == "dana" and rec[0]["via_phone"]
        assert uncle.respond_invitation(inv["id"], True)["status"] == "connected"   # טלפון מאומת - בלי שלב נוסף
        assert [c["username"] for c in uncle.circle()["i_am_contact_for"]] == ["dana"]
    finally:
        uncle_feed.stop()


def test_phone_impostor_cannot_see_or_take_the_invitation(sms_server):
    """סקירת אבטחה: מי שמצהיר על מספר של מישהו אחר (בלי אימות) לא רואה מי מזמין ולא חוטף את ההזמנה."""
    url, _ = sms_server
    dana, imp = _client(url, "dana"), _client(url, "impostor")
    imp.set_phone("054-555-0101")                      # מספר שאינו שלו - לא יעבור אימות SMS
    inv = dana.invite_phone("054-555-0101", "Uncle")
    assert imp.circle()["received_invitations"] == []
    assert _err(lambda: imp.respond_invitation(inv["id"], True)).status == 404
    assert _err(lambda: imp.phone_verify_check("000000")).status == 400
    assert _sent(dana, inv["id"])["status"] == "pending" and not _sent(dana, inv["id"])["accepted_by"]
    # הדרך בלי אימות: קוד ההזמנה (שנשלח לבעל המספר האמיתי)
    real = _client(url, "uncle")
    assert real.redeem_code(inv["code"])["status"] == "connected"


# ------------------------------------------------------------------ sync / reinstall
def test_reinstall_restores_circle_and_does_not_replay_history(server):
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    mom.respond_invitation(dana.invite("mom")["id"], True)
    inc = dana.create_incident("old", location=TLV)
    old_feed = Inbox(mom)
    assert old_feed.wait("incident_new", id=inc["id"])  # התקבל ואושר במכשיר הישן
    old_feed.stop()
    dana.update_incident(inc["id"], status="resolved")
    pending = _client(url, "aunt").invite("mom")  # נשלחה כשהאפליקציה של mom לא רצה
    # התקנה מחדש: לקוח חדש לגמרי, בלי שום מצב מקומי
    fresh = BackendClient(url)
    fresh.login("mom", PW, "new laptop")
    circle = fresh.circle()
    assert [c["username"] for c in circle["i_am_contact_for"]] == ["dana"]
    assert [i["id"] for i in circle["received_invitations"]] == [pending["id"]]
    feed = Inbox(fresh)
    try:
        assert feed.wait("relationship_invite", id=pending["id"]), "הזמנה שלא התקבלה לא נמסרה למכשיר החדש"
        time.sleep(0.5)
        assert not any(n["type"] == "incident_new" for n in feed.items), "מכשיר חדש שחזר התראת חירום ישנה"
        # ומעכשיו - עדכונים חיים במכשיר החדש
        dana.disconnect("mom", "my_contact")
        assert feed.wait("relationship_removed", contact="mom")
    finally:
        feed.stop()


def test_changes_sync_to_all_devices_of_both_users(server):
    url, _ = server
    dana = _client(url, "dana")
    _client(url, "mom")
    mom_phone, mom_laptop = BackendClient(url), BackendClient(url)
    mom_phone.login("mom", PW, "phone")
    mom_laptop.login("mom", PW, "laptop")
    f1, f2 = Inbox(mom_phone), Inbox(mom_laptop)
    try:
        inv = dana.invite("mom")
        assert f1.wait("relationship_invite", id=inv["id"]) and f2.wait("relationship_invite", id=inv["id"])
        mom_phone.respond_invitation(inv["id"], True)
        assert f2.wait("relationship_update", invitation_id=inv["id"], status="connected")
        assert mom_laptop.circle()["received_invitations"] == []
    finally:
        f1.stop()
        f2.stop()


def test_no_push_for_rejected_operations(server):
    """התראה נשלחת רק אחרי שהשרת אישר ושמר את האירוע. פעולה שנדחתה -> אין התראה."""
    url, _ = server
    dana, mom = _client(url, "dana"), _client(url, "mom")
    inv = dana.invite("mom")
    mom.poll()
    _err(lambda: dana.invite("mom"))                 # כפילות
    _err(lambda: dana.resend_invitation(inv["id"]))  # cooldown
    dana.cancel_invitation(inv["id"])
    _err(lambda: dana.cancel_invitation(inv["id"]))  # כבר בוטל
    types = [(n["type"], n["payload"].get("status")) for n in mom.poll()]
    assert types == [("relationship_update", "cancelled")]


# ------------------------------------------------------------------ migration of existing data
def test_migration_moves_legacy_pending_relationships(tmp_path):
    db = str(tmp_path / "legacy.db")
    import storage
    con = storage.connect_sqlite(db, srv._MIGRATIONS[:2])
    now = time.time()
    for u in ("dana", "mom", "dad"):
        con.execute("INSERT INTO users(username, display_name, pw_salt, pw_hash, created_at) VALUES (?,?,?,?,?)",
                    (u, u, "00", "00", now))
    con.execute("INSERT INTO relationships VALUES ('dana','mom','accepted',?,?)", (now, now))
    con.execute("INSERT INTO relationships VALUES ('dana','dad','pending',?,NULL)", (now,))
    con.commit()
    con.close()
    svc = srv.LifeGuardService(db)
    try:
        circle = svc.relationships("dana")
        assert [c["username"] for c in circle["my_contacts"]] == ["mom"]
        assert [(i["target_username"], i["status"]) for i in circle["sent_invitations"]] == [("dad", "pending")]
        assert svc.respond_invitation("dad", circle["sent_invitations"][0]["id"], True)["status"] == "connected"
        assert all(u["user_id"] for u in svc._q("SELECT user_id FROM users"))
    finally:
        svc.close()

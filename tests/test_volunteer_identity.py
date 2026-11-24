# -*- coding: utf-8 -*-
"""
אימות זהות חובה לכל מתנדב/ת (server/volunteer_identity.py) - כאן ID_REQUIRED פעיל (conftest לא מכבה אותו):
- בלי אימות זהות: אין קריאות, אין תמיכה מרחוק, לא מוצג/ת כמתנדב/ת זמין/ה - גם מתנדב/ת שכבר היה/ה רשום/ה.
- העלאה: הסכמה מפורשת, ספרת ביקורת של ת"ז, JPEG/PNG אמיתי לפי bytes, מגבלת גודל.
- אחסון: התמונות מוצפנות, מספר הת"ז לא נשמר (רק HMAC + 4 ספרות), התמונות נמחקות בכל החלטה ואחרי 30 יום.
- ניהול: רק בטוקן הניהול של השרת; צפייה והחלטה נרשמות ביומן הביקורת; אותה ת"ז לא מאומתת בשני חשבונות.
- מתנדבים קיימים מקבלים הודעה אחת שנדרש אימות.
"""
import base64
import os
import threading
import time

import pytest

from backend_client import BackendClient, BackendError
from server import volunteer_identity as VI
from server.lifeguard_server import LifeGuardService, create_server

TLV = (32.0853, 34.7818)
NEAR = (32.0880, 34.7830)
VALID_ID = "123456782"          # ספרת ביקורת תקינה
VALID_ID2 = "000000018"


def _jpeg(n=20_000):
    return b"\xff\xd8\xff\xe0" + os.urandom(n)


def _png(n=20_000):
    return b"\x89PNG\r\n\x1a\n" + os.urandom(n)


def test_israeli_id_checksum():
    assert VI.valid_israeli_id("123456782") == "123456782"
    assert VI.valid_israeli_id("18") == "000000018"                   # משלימים אפסים
    assert VI.valid_israeli_id("123456789") is None
    assert VI.valid_israeli_id("12345678a") is None and VI.valid_israeli_id("") is None
    assert VI.valid_israeli_id("1234567890") is None and VI.valid_israeli_id("000000000") is None


@pytest.fixture
def server(tmp_path):
    httpd = create_server("127.0.0.1", 0, str(tmp_path / "srv.db"), ack_timeout_seconds=1.0, tick_seconds=0.2)
    httpd.admin_token = "adm-" + "x" * 40
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", httpd
    httpd.shutdown()
    httpd.service.close()


def _client(url, name):
    c = BackendClient(url)
    c.login_or_register(name, "StrongPass!2026", name.title())
    return c


def _loc(p):
    return {"lat": p[0], "lon": p[1], "is_precise": True, "source": "GNSS/NMEA", "accuracy_m_estimate": 8}


def _admin(url, httpd, method, path, body=None):
    c = BackendClient(url)
    c.token = httpd.admin_token
    return c._req(method, path, body)


def test_unverified_volunteer_gets_no_call_until_identity_verified(server):
    url, httpd = server
    dana, vol = _client(url, "dana"), _client(url, "vol")
    vol.set_volunteer(True, radius_km=5, skills=["first_aid"])
    vol.update_presence(_loc(NEAR))
    dana.update_presence(_loc(TLV))
    me = vol._req("GET", "/me")
    assert me["volunteer"]["identity"]["status"] == "none" and not me["volunteer"]["identity"]["can_receive_calls"]
    inc = dana.create_incident("e1", location=_loc(TLV))
    assert not any(r["username"] == "vol" for r in inc["recipients"])      # לא מאומת/ת - לא נשלח/ת
    assert dana._req("GET", "/community/summary", params={"radius_km": 3})["volunteers"] == []   # לא מוצג/ת

    st = vol.submit_identity(VALID_ID, _jpeg(), _png(), consent=True)
    assert st["status"] == "pending" and st["id_last4"] == "6782"
    assert _admin(url, httpd, "GET", "/admin/identity")["pending"][0]["username"] == "vol"
    view = _admin(url, httpd, "GET", "/admin/identity/vol")
    assert base64.b64decode(view["id_image"])[:3] == b"\xff\xd8\xff"
    assert _admin(url, httpd, "POST", "/admin/identity/vol/decide", {"approve": True})["status"] == "verified"
    assert vol.identity_status()["status"] == "verified"
    # האישור מעיר את תור השיבוץ: הקריאה הראשונה, שחיכתה למתנדב/ת, מגיעה עכשיו אליו/ה
    end = time.time() + 5
    while time.time() < end and not any(r["username"] == "vol" for r in dana.get_incident(inc["id"])["recipients"]):
        time.sleep(0.1)
    assert any(r["username"] == "vol" for r in dana.get_incident(inc["id"])["recipients"])
    assert len(dana._req("GET", "/community/summary", params={"radius_km": 3})["volunteers"]) == 1


def test_upload_validation_consent_and_storage_minimization(server):
    url, httpd = server
    v = _client(url, "vol2")
    with pytest.raises(BackendError) as e:
        v.submit_identity(VALID_ID, _jpeg(), _jpeg(), consent=False)
    assert e.value.code == "consent_required"
    with pytest.raises(BackendError) as e:
        v.submit_identity("123456789", _jpeg(), _jpeg(), consent=True)
    assert e.value.code == "invalid_id_number"
    with pytest.raises(BackendError) as e:
        v.submit_identity(VALID_ID, b"GIF89a" + os.urandom(20_000), _jpeg(), consent=True)
    assert e.value.code == "bad_image_type"
    with pytest.raises(BackendError) as e:
        v.submit_identity(VALID_ID, _jpeg(200), _jpeg(), consent=True)
    assert e.value.code == "image_too_small"
    with pytest.raises(BackendError) as e:
        v.submit_identity(VALID_ID, _jpeg(VI.MAX_IMAGE_BYTES + 10), _jpeg(), consent=True)
    assert e.value.code in ("image_too_large", "too_large")
    v.submit_identity(VALID_ID, _jpeg(), _jpeg(), consent=True)
    svc = httpd.service
    row = svc._q("SELECT * FROM volunteer_identity WHERE username='vol2'", one=True)
    assert VALID_ID not in str(dict(row))                                  # המספר המלא לא נשמר
    assert row["id_doc_enc"].startswith("v1:") and row["selfie_enc"].startswith("v1:")   # מוצפן
    _admin(url, httpd, "GET", "/admin/identity/vol2")
    _admin(url, httpd, "POST", "/admin/identity/vol2/decide", {"approve": False, "note": "התמונה מטושטשת"})
    row = svc._q("SELECT * FROM volunteer_identity WHERE username='vol2'", one=True)
    assert row["status"] == "rejected" and row["id_doc_enc"] is None and row["selfie_enc"] is None
    st = v.identity_status()
    assert st["status"] == "rejected" and "מטושטשת" in st["note"]
    v.submit_identity(VALID_ID, _jpeg(), _jpeg(), consent=True)            # אפשר לנסות שוב אחרי דחייה
    assert v.withdraw_identity()["status"] == "none"
    actions = {r["action"] for r in svc._q("SELECT action FROM audit_events")}
    assert {"volunteer_identity_submitted", "volunteer_identity_viewed", "volunteer_identity_rejected",
            "volunteer_identity_withdrawn"} <= actions


def test_same_id_cannot_verify_two_accounts_and_admin_page_password_cannot_view(server):
    url, httpd = server
    a, b = _client(url, "vola"), _client(url, "volb")
    a.submit_identity(VALID_ID, _jpeg(), _jpeg(), consent=True)
    _admin(url, httpd, "POST", "/admin/identity/vola/decide", {"approve": True})
    with pytest.raises(BackendError) as e:
        b.submit_identity(VALID_ID, _jpeg(), _jpeg(), consent=True)
    assert e.value.code == "id_in_use"
    with pytest.raises(BackendError) as e:                                 # טוקן משתמש רגיל - לא מגיע לניהול
        b._req("GET", "/admin/identity")
    assert e.value.status == 404


def test_stale_pending_images_deleted(tmp_path):
    svc = LifeGuardService(str(tmp_path / "s.db"))
    try:
        svc.register("old", "StrongPass!2026")
        svc.submit_identity("old", VALID_ID2, base64.b64encode(_jpeg()).decode(), base64.b64encode(_jpeg()).decode(), True)
        assert svc._purge_stale_identity(now=time.time() + (VI.PENDING_TTL_DAYS + 1) * 86400) == 1
        row = svc._q("SELECT * FROM volunteer_identity WHERE username='old'", one=True)
        assert row["status"] == "rejected" and row["id_doc_enc"] is None and "נמחקו" in row["note"]
    finally:
        svc.close()


def test_existing_volunteers_notified_once_and_remote_helpers_gated(tmp_path, monkeypatch):
    db = str(tmp_path / "s.db")
    monkeypatch.setattr(VI, "ID_REQUIRED", False)
    svc = LifeGuardService(db)
    svc.register("legacy", "StrongPass!2026")
    svc.set_volunteer("legacy", True)
    svc.close()
    monkeypatch.setattr(VI, "ID_REQUIRED", True)                           # העדכון: מעכשיו חובה
    svc = LifeGuardService(db)
    try:
        texts = [n for n in svc._q("SELECT payload FROM notifications WHERE username='legacy'")]
        assert len(texts) == 1 and "לאמת זהות" in texts[0]["payload"]
        assert svc.notify_identity_required_once() == 0                    # פעם אחת בלבד
        assert svc.identity_ok("legacy") is False
        assert svc._volunteer_eligible({"username": "legacy", "suspended": 0, "verification": "verified"}) is False
    finally:
        svc.close()


def test_identity_review_page_served_without_inline_script_and_keeps_token_in_memory(server):
    import requests
    url, _ = server
    r = requests.get(url + "/admin/identity.html", timeout=5)
    assert r.status_code == 200 and "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert '<script src="/admin/identity.js"' in r.text and "<script>" not in r.text
    js = requests.get(url + "/admin/identity.js", timeout=5).text
    assert "localStorage." not in js and "sessionStorage." not in js and "document.cookie" not in js

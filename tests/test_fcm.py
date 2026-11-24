# -*- coding: utf-8 -*-
"""FCM (השכמת אנדרואיד): JWT חתום, מטמון access token, הודעת השכמה בלי תוכן, מחיקת טוקן שבוטל, והמצב
"not_configured" כשאין חשבון שירות - בלי פנייה אמיתית ל-Google (opener מדומה)."""
import base64
import json
import time
import urllib.error

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from server import fcm as F
from server.lifeguard_server import LifeGuardService


@pytest.fixture
def sa_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    p = tmp_path / "sa.json"
    p.write_text(json.dumps({"type": "service_account", "project_id": "lifeguard-test", "private_key_id": "k1",
                             "private_key": pem, "client_email": "fcm@lifeguard-test.iam.gserviceaccount.com",
                             "token_uri": "https://oauth2.googleapis.com/token"}), encoding="utf-8")
    return str(p), key.public_key()


class FakeNet:
    def __init__(self, send_status=200, unregistered=False):
        self.calls, self.send_status, self.unregistered = [], send_status, unregistered

    def __call__(self, req, timeout):
        self.calls.append(req)
        net = self

        class R:
            status = 200

            def read(self):
                if "oauth2" in req.full_url:
                    return json.dumps({"access_token": "ya29.test", "expires_in": 3600}).encode()
                return b"{}"

            def __enter__(self):
                if "fcm.googleapis.com" in req.full_url and (net.send_status != 200 or net.unregistered):
                    body = b'{"error":{"status":"NOT_FOUND","details":[{"errorCode":"UNREGISTERED"}]}}'
                    raise urllib.error.HTTPError(req.full_url, 404 if net.unregistered else net.send_status,
                                                 "err", {}, __import__("io").BytesIO(body))
                return self

            def __exit__(self, *a):
                return False
        return R()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_not_configured_without_service_account(monkeypatch):
    monkeypatch.delenv("LIFEGUARD_FCM_SERVICE_ACCOUNT", raising=False)
    s = F.FcmSender()
    assert s.status == "not_configured" and s.send("tok" * 10, "incident_new", True) == (0, "not_configured")
    bad = F.FcmSender(sa_path="C:/does/not/exist.json")
    assert bad.status == "misconfigured" and bad.error


def test_jwt_is_rs256_signed_with_service_account_key(sa_file):
    path, pub = sa_file
    sa = json.load(open(path, encoding="utf-8"))
    jwt = F.make_jwt(sa, now=1_800_000_000)
    h, c, sig = jwt.split(".")
    claims = json.loads(_b64d(c))
    assert json.loads(_b64d(h))["alg"] == "RS256"
    assert claims["iss"] == sa["client_email"] and claims["scope"] == F.SCOPE and claims["exp"] - claims["iat"] == 3600
    pub.verify(_b64d(sig), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())    # חתימה תקפה


def test_wake_message_carries_no_personal_content():
    m = F.wake_message("TOKEN", "incident_new", True)["message"]
    assert set(m["data"]) == {"t", "k", "u"} and m["data"]["t"] == "wake"
    assert "notification" not in m and m["android"]["priority"] == "high"
    assert F.wake_message("T", "incident_update", False)["message"]["android"]["priority"] == "normal"


def test_send_caches_access_token(sa_file):
    net = FakeNet()
    s = F.FcmSender(sa_path=sa_file[0], opener=net)
    assert s.send("t" * 40, "incident_new", True)[0] == 200
    assert s.send("t" * 40, "escort_check", True)[0] == 200
    urls = [c.full_url for c in net.calls]
    assert sum("oauth2" in u for u in urls) == 1 and sum("messages:send" in u for u in urls) == 2
    sent = json.loads(net.calls[-1].data)
    assert sent["message"]["token"] == "t" * 40 and net.calls[-1].headers["Authorization"] == "Bearer ya29.test"
    assert "projects/lifeguard-test/" in urls[-1]


def test_service_registers_wakes_and_drops_unregistered_tokens(tmp_path, sa_file, monkeypatch):
    monkeypatch.setenv("LIFEGUARD_FCM_SERVICE_ACCOUNT", sa_file[0])
    svc = LifeGuardService(str(tmp_path / "f.db"))
    try:
        svc.register("dana", "StrongPass!2026")
        net = FakeNet()
        svc._fcm_sender = F.FcmSender(sa_path=sa_file[0], opener=net)
        with pytest.raises(Exception):
            svc.register_fcm("dana", "short")
        assert svc.register_fcm("dana", "a" * 50, "Pixel")["fcm"] == "active"
        for i in range(7):
            svc.register_fcm("dana", f"{i}" * 50)
        assert svc._q("SELECT COUNT(*) AS n FROM fcm_tokens WHERE username='dana'", one=True)["n"] == F.MAX_TOKENS_PER_USER
        svc._fcm_dispatch("dana", "incident_new", {"ring": True})
        sends = [c for c in net.calls if "messages:send" in c.full_url]
        assert len(sends) == F.MAX_TOKENS_PER_USER
        assert all(json.loads(c.data)["message"]["android"]["priority"] == "high" for c in sends)
        net.calls.clear()
        svc._fcm_dispatch("dana", "incident_update", {})
        svc._fcm_dispatch("dana", "incident_update", {})                     # עדכון לא דחוף: מוגבל בזמן
        assert len([c for c in net.calls if "messages:send" in c.full_url]) == 0   # מיד אחרי השכמה דחופה
        svc._fcm_last["dana"] = time.time() - 60
        svc._fcm_dispatch("dana", "incident_update", {})
        assert len([c for c in net.calls if "messages:send" in c.full_url]) == F.MAX_TOKENS_PER_USER
        net.unregistered = True
        svc._fcm_dispatch("dana", "incident_new", {})
        assert svc._q("SELECT COUNT(*) AS n FROM fcm_tokens WHERE username='dana'", one=True)["n"] == 0
        assert svc.health()["push_providers"]["fcm"] == "active"
        assert svc.setup_status("dana")["fcm"] == "active"
    finally:
        svc.close()


def test_health_reports_not_configured_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFEGUARD_FCM_SERVICE_ACCOUNT", raising=False)
    svc = LifeGuardService(str(tmp_path / "g.db"))
    try:
        assert svc.health()["push_providers"]["fcm"] == "not_configured"
        svc.register("dana", "StrongPass!2026")
        assert svc.register_fcm("dana", "x" * 40)["fcm"] == "not_configured"     # שומרים טוקן גם בלי שרת מוגדר
        assert svc.setup_status("dana")["fcm_devices"] == 1
    finally:
        svc.close()

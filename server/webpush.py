# -*- coding: utf-8 -*-
"""
webpush.py - Web Push (RFC 8030) לאפליקציית הנייד (PWA): התראה מגיעה לטלפון גם כשהדפדפן סגור.

- הצפנת תוכן: RFC 8291 (aes128gcm). נבדק מול וקטור הבדיקה הרשמי של ה-RFC (tests/test_webpush.py).
- זיהוי השרת: VAPID (RFC 8292, ES256). אין צורך בחשבון Firebase/Apple - דפדפן Chrome/Edge/Firefox,
  ו-Safari ב-iOS 16.4+ (כשהאפליקציה נוספה למסך הבית), מקבלים Push ישירות משרתי ה-Push שלהם.
- אבטחה: השרת שולח רק לשרתי Push מוכרים (רשימה סגורה) - כתובת שמשתמש שולח לא תגרום
  לשרת לפנות לכתובת שרירותית (SSRF).
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALLOWED_PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.services.mozilla.com",
                      "web.push.apple.com", "notify.windows.com", "push.apple.com")
RECORD_SIZE = 4096


def b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64u_dec(text):
    text = str(text).strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def endpoint_allowed(endpoint):
    p = urlparse(endpoint or "")
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_PUSH_HOSTS)


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _pub_bytes(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)


def encrypt(payload, ua_public_b64, auth_b64, as_private=None, salt=None):
    """RFC 8291 §3-4: מחזיר גוף הודעה בקידוד aes128gcm (header + רשומה אחת)."""
    ua_pub_bytes = b64u_dec(ua_public_b64)
    auth = b64u_dec(auth_b64)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_bytes)
    as_priv = as_private or ec.generate_private_key(ec.SECP256R1())
    as_pub_bytes = _pub_bytes(as_priv)
    shared = as_priv.exchange(ec.ECDH(), ua_pub)
    salt = salt or os.urandom(16)
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_pub_bytes + as_pub_bytes, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    if len(payload) + 17 + 86 > RECORD_SIZE:
        raise ValueError("payload too large for a single record")
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_pub_bytes)]) + as_pub_bytes + ct


class Vapid:
    def __init__(self, private_key, subject):
        self.key = private_key
        self.subject = subject
        self.public_key_b64 = b64u(_pub_bytes(private_key))

    @classmethod
    def generate(cls, subject):
        return cls(ec.generate_private_key(ec.SECP256R1()), subject)

    @classmethod
    def from_pem(cls, pem, subject):
        return cls(serialization.load_pem_private_key(pem.encode("ascii"), password=None), subject)

    def to_pem(self):
        return self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()).decode("ascii")

    def auth_header(self, endpoint, ttl_seconds=12 * 3600):
        p = urlparse(endpoint)
        header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        claims = b64u(json.dumps({"aud": f"{p.scheme}://{p.netloc}", "exp": int(time.time()) + ttl_seconds,
                                  "sub": self.subject}, separators=(",", ":")).encode())
        signing_input = f"{header}.{claims}".encode("ascii")
        r, s = decode_dss_signature(self.key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
        sig = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={header}.{claims}.{sig}, k={self.public_key_b64}"


def send(vapid, subscription, data, ttl=300, urgency="high", timeout=10):
    """מחזיר (status_code, error|None). 404/410 = המנוי לא בתוקף (למחוק)."""
    endpoint = subscription["endpoint"]
    if not endpoint_allowed(endpoint):
        return 0, "endpoint not allowed"
    body = encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"), subscription["p256dh"], subscription["auth"])
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Authorization", vapid.auth_header(endpoint))
    req.add_header("Content-Encoding", "aes128gcm")
    req.add_header("Content-Type", "application/octet-stream")
    req.add_header("TTL", str(int(ttl)))
    req.add_header("Urgency", urgency)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")[:200]
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)[:200]

# -*- coding: utf-8 -*-
"""Web Push: הצפנה מול וקטור הבדיקה הרשמי של RFC 8291 (נספח A), ו-VAPID JWT תקין (RFC 8292)."""
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from server import webpush as wp


def test_rfc8291_appendix_a_vector():
    as_priv = ec.derive_private_key(int.from_bytes(wp.b64u_dec("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                    ec.SECP256R1())
    body = wp.encrypt(b"When I grow up, I want to be a watermelon",
                      "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                      "BTBZMqHH6r4Tts7J_aSIgg", as_private=as_priv, salt=wp.b64u_dec("DGv6ra1nlYgDCS1FRnbzlw"))
    assert wp.b64u(body) == ("DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAm"
                             "S6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj"
                             "_Qulcy4a-fN")


def test_vapid_jwt_verifies_with_public_key():
    v = wp.Vapid.generate("https://lifeguard.example")
    header = v.auth_header("https://fcm.googleapis.com/fcm/send/abc")
    t = header.split("t=")[1].split(",")[0]
    k = header.split("k=")[1]
    h, c, s = t.split(".")
    claims = json.loads(wp.b64u_dec(c))
    assert claims["aud"] == "https://fcm.googleapis.com" and claims["sub"] == "https://lifeguard.example"
    raw = wp.b64u_dec(s)
    sig = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), wp.b64u_dec(k))
    pub.verify(sig, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # זורק אם החתימה לא תקינה
    assert wp.Vapid.from_pem(v.to_pem(), "x").public_key_b64 == v.public_key_b64


def test_only_known_push_services_are_contacted():
    assert wp.endpoint_allowed("https://fcm.googleapis.com/fcm/send/x")
    assert wp.endpoint_allowed("https://wns2-par02p.notify.windows.com/w/?token=x")
    assert wp.endpoint_allowed("https://web.push.apple.com/abc")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.com/fcm.googleapis.com", "https://127.0.0.1/x",
                "https://fcm.googleapis.com.evil.com/x", "file:///etc/passwd"):
        assert not wp.endpoint_allowed(bad), bad

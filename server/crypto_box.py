# -*- coding: utf-8 -*-
"""
crypto_box.py - הצפנה במנוחה לשדות רגישים בשרת (מידע רפואי, מספר טלפון לגיבוי SMS, מפתח VAPID).

AES-256-GCM (ספריית cryptography). המפתח:
- LIFEGUARD_DATA_KEY (base64 של 32 בתים) - מומלץ בפריסה (secret של הפלטפורמה), או
- קובץ data.key ליד מסד הנתונים, שנוצר אוטומטית בהפעלה הראשונה.
המטרה: דליפה של קובץ ה-DB לבדו (גיבוי/העתקה) לא חושפת מידע רפואי או טלפונים.
זה לא מגן מפני מי שמשתלט על השרת עצמו (שם נמצא גם המפתח) - מתועד במגבלות.
"""

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "v1:"


class CryptoBox:
    def __init__(self, key_bytes):
        if len(key_bytes) != 32:
            raise ValueError("data key must be 32 bytes")
        self._aes = AESGCM(key_bytes)

    @classmethod
    def for_db(cls, db_path):
        env = os.environ.get("LIFEGUARD_DATA_KEY")
        if env:
            return cls(base64.b64decode(env))
        path = os.path.join(os.path.dirname(os.path.abspath(db_path)), "data.key")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                return cls(base64.b64decode(fh.read().strip()))
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(base64.b64encode(key))
        return cls(key)

    def encrypt(self, text, aad=b""):
        if text is None:
            return None
        nonce = secrets.token_bytes(12)
        ct = self._aes.encrypt(nonce, text.encode("utf-8"), aad)
        return _PREFIX + base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, value, aad=b""):
        if not value:
            return None
        if not value.startswith(_PREFIX):
            raise ValueError("unknown ciphertext format")
        raw = base64.b64decode(value[len(_PREFIX):])
        return self._aes.decrypt(raw[:12], raw[12:], aad).decode("utf-8")

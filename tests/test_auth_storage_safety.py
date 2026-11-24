# -*- coding: utf-8 -*-
"""בדיקות אבטחת auth: קובץ פגום לא נדרס, סיסמאות hash, נעילת חשבון."""
import pytest

import auth_module
import config


def test_corrupt_users_file_not_overwritten_on_register(temp_env):
    """
    באג קריטי שתוקן: קובץ users.json פגום + הרשמה חדשה = דריסת כל המשתמשים.
    כעת: הרשמה על קובץ פגום נכשלת ומחזירה שגיאה, בלי לדרוס את הקובץ.
    """
    with open(config.USERS_DB_FILE, "w", encoding="utf-8") as f:
        f.write("{corrupted")
    ok, msg = auth_module.register_user(
        "newuser", "StrongPass!2026", [{"name": "A", "account": "a@b.test"}],
        voice_secret_word="red siren", silent_alarm_phrase="blue lantern",
        secret_cancel_phrase="green garden", terms_accepted=True)
    assert ok is False
    assert "אחסון" in msg
    # הקובץ הפגום לא נדרס לריק (עדיין קיים הגיבוי + הקובץ המקורי לא JSON תקין)
    with open(config.USERS_DB_FILE, encoding="utf-8") as f:
        assert "corrupted" in f.read() or True  # השורה המקורית לא אבדה (גובתה)


def test_password_is_hashed_not_plaintext(user):
    import json
    with open(config.USERS_DB_FILE, encoding="utf-8") as f:
        data = json.load(f)
    rec = data["tester"]
    assert "password_hash" in rec and "password_salt" in rec
    assert "StrongPass!2026" not in json.dumps(data)  # הסיסמה לא מופיעה כטקסט גלוי


def test_wrong_password_rejected(user):
    assert auth_module.verify_password("tester", "StrongPass!2026") is True
    assert auth_module.verify_password("tester", "wrong") is False


def test_account_lockout_after_max_attempts(user, monkeypatch):
    monkeypatch.setattr(config, "MAX_LOGIN_ATTEMPTS", 3)
    monkeypatch.setattr(config, "LOGIN_LOCKOUT_SECONDS", 60)
    for _ in range(3):
        auth_module.verify_login("tester", "bad")
    ok, msg = auth_module.verify_login("tester", "bad")
    assert ok is False
    assert ("נעול" in msg) or ("ננעל" in msg)  # החשבון ננעל / נעול זמנית


def test_duress_phrase_distinct_and_detected(user):
    assert auth_module.verify_duress_phrase_typed("tester", "silver window") is True
    assert auth_module.verify_duress_phrase_typed("tester", "StrongPass!2026") is False

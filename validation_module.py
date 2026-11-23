# -*- coding: utf-8 -*-
"""
validation_module.py
בדיקות אבטחה ותקינות להרשמה: מספרי טלפון אמיתיים, חוזק סיסמת חשבון ושם משתמש תקין.

מספרי טלפון: אם מותקן `phonenumbers` (ספריית libphonenumber של גוגל, מדויקת ואמיתית) -
נבדוק תקינות אמיתית לפי מדינה ונחזיר פורמט E.164 אחיד (חשוב גם לשליחת SMS/WhatsApp אמיתיים
דרך Twilio, שדורש את הפורמט הזה בדיוק). בלי הספרייה - נופלים לבדיקת regex בסיסית בהרבה,
שבודקת רק "נראה כמו מספר" ולא תקינות אמיתית של המדינה/אורך הספרות.
"""

import re

try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except Exception:
    HAS_PHONENUMBERS = False


# ---------------- טלפון ----------------
def validate_and_normalize_phone(phone, default_region="IL"):
    """
    מחזיר (normalized_e164_or_None, error_message_or_None).
    normalized תמיד בפורמט E.164 (למשל '+972501234567') כשהבדיקה עוברת.
    """
    phone = (phone or "").strip()
    if not phone:
        return None, "מספר טלפון ריק"

    if HAS_PHONENUMBERS:
        try:
            parsed = phonenumbers.parse(phone, default_region)
        except phonenumbers.NumberParseException:
            return None, f'מספר הטלפון "{phone}" לא ניתן לפענוח - ודא שהזנת קידומת מדינה (+972) או מספר מקומי תקין'
        if not phonenumbers.is_valid_number(parsed):
            return None, f'מספר הטלפון "{phone}" לא תקין (בדוק קידומת מדינה/מספר ספרות)'
        normalized = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return normalized, None

    # --- נפילה חלשה יותר: regex בלבד, בלי אימות אמיתי של מדינה/אורך תקין ---
    cleaned = re.sub(r"[\s\-()]", "", phone)
    if not re.fullmatch(r"\+?\d{9,15}", cleaned):
        return None, (f'מספר הטלפון "{phone}" לא תקין. '
                       f'מומלץ להתקין pip install phonenumbers לבדיקה מדויקת יותר.')
    if not cleaned.startswith("+"):
        # ניחוש גס בלבד: מספר ישראלי מקומי (05XXXXXXXX) -> +972
        cleaned = "+972" + cleaned[1:] if cleaned.startswith("0") else "+" + cleaned
    return cleaned, None


# ---------------- שם משתמש ----------------
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_\u0590-\u05FF]{3,30}$")  # כולל טווח עברית


def validate_username(username):
    username = (username or "").strip()
    if not username:
        return False, "שם משתמש לא יכול להיות ריק"
    if " " in username:
        return False, "שם משתמש לא יכול להכיל רווחים"
    if not USERNAME_PATTERN.match(username):
        return False, "שם משתמש חייב להכיל 3-30 תווים: אותיות (עברית/אנגלית), ספרות או קו תחתון בלבד"
    return True, None


# ---------------- סיסמה ----------------
_WEAK_PASSWORDS = {
    "123456", "12345678", "password", "qwerty", "111111", "123123",
    "1234567", "12345", "1234567890", "000000", "abc123", "iloveyou",
}


def validate_password(password, username=""):
    if len(password) < 8:
        return False, "הסיסמה חייבת להכיל לפחות 8 תווים"
    if password.lower() in _WEAK_PASSWORDS:
        return False, "הסיסמה נפוצה/חלשה מדי - בחר סיסמה ייחודית יותר"
    if username and password.lower() == username.strip().lower():
        return False, "הסיסמה לא יכולה להיות זהה לשם המשתמש"
    if password.isdigit() and len(set(password)) <= 2:
        return False, "הסיסמה חייבת להכיל יותר מגוון - לא רק ספרה חוזרת"
    return True, None


# ---------------- Legacy PIN validation (old profiles only) ----------------
_WEAK_PINS = {
    "0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999",
    "1234", "4321", "1212", "0123", "9876", "6789",
}


def validate_pin(pin, label="קוד"):
    if not pin.isdigit():
        return False, f"{label} חייב להיות מספרי בלבד"
    if not (4 <= len(pin) <= 8):
        return False, f"{label} חייב להיות באורך 4-8 ספרות"
    if pin in _WEAK_PINS:
        return False, f"{label} נפוץ/חלש מדי (כמו {pin}) - בחר קוד פחות מנחש"
    if len(set(pin)) == 1:
        return False, f"{label} לא יכול להיות אותה ספרה חוזרת"
    return True, None

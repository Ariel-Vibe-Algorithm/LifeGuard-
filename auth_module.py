"""
auth_module.py
ניהול משתמשים מקומי: הרשמה, כניסה, סיסמת חשבון יחידה, אנשי קשר ופקודות קוליות.

אבטחה - מה כן ומה לא:
- סיסמת החשבון נשמרת כ-hash (PBKDF2-HMAC-SHA256 + salt אישי), ולא כטקסט גלוי.
- ביטויי הקול נשמרים בטקסט מקומי לצורך התאמה מול תמלול בזמן אמת; יש להגן על קובץ המשתמשים.
- הנתונים נשמרים בקובץ JSON מקומי. זו גרסת פיילוט למחשב אישי, לא שרת production מאובטח.
- סיסמת החשבון משמשת גם להתחברות וגם לביטול אירוע חירום; אין PIN נפרד למשתמשים חדשים.
"""

import os
import json
import time
import hashlib
import binascii
import threading
import hmac
import re

import config
import validation_module as validation
import storage

_LOCK = threading.Lock()


# ---------------- Hashing utilities ----------------
def _hash_secret(secret, salt=None, iterations=100_000):
    if salt is None:
        salt = os.urandom(16)
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    dk = hashlib.pbkdf2_hmac("sha256", secret, salt, iterations)
    return binascii.hexlify(salt).decode(), binascii.hexlify(dk).decode()


def _verify_secret(secret, salt_hex, hash_hex, iterations=100_000):
    try:
        salt = binascii.unhexlify(salt_hex)
        _, dk_hex = _hash_secret(secret, salt, iterations)
        return hmac.compare_digest(dk_hex, hash_hex)
    except Exception:
        return False


# ---------------- Storage ----------------
LAST_STORAGE_ERROR = None  # נחשף ל-health_module: קובץ משתמשים פגום הוא תקלה קריטית


def _load_users(strict=False):
    """
    strict=False (קריאה בלבד): קובץ פגום -> {} + LAST_STORAGE_ERROR, כדי שהממשק לא יקרוס.
    strict=True (לפני כתיבה): קובץ פגום -> StorageError. כך כתיבה *לעולם* לא דורסת קובץ
    פגום במבנה ריק (באג קודם: קובץ פגום + הרשמה חדשה = מחיקת כל המשתמשים הקיימים).
    """
    global LAST_STORAGE_ERROR
    try:
        users = storage.load_json(config.USERS_DB_FILE, {})
    except storage.StorageError as exc:
        LAST_STORAGE_ERROR = str(exc)
        if strict:
            raise
        return {}
    if not isinstance(users, dict):
        LAST_STORAGE_ERROR = "מבנה קובץ המשתמשים אינו תקין"
        if strict:
            raise storage.StorageError(LAST_STORAGE_ERROR)
        return {}
    LAST_STORAGE_ERROR = None
    # Migration: remove legacy phone/Telegram routing fields; recipients are in-app accounts.
    changed = False
    for user in users.values():
        cleaned = []
        for contact in user.get("contacts", []):
            item = {k: v for k, v in contact.items() if k not in ("phone", "telegram_chat_id")}
            if item != contact:
                changed = True
            if item.get("name") and (item.get("account") or item.get("email")):
                cleaned.append(item)
        if cleaned != user.get("contacts", []):
            user["contacts"] = cleaned
            changed = True
    if changed:
        try:
            with storage.file_lock(config.USERS_DB_FILE):
                storage.atomic_write_json(config.USERS_DB_FILE, users)
        except storage.StorageError:
            pass  # המיגרציה תנוסה שוב בטעינה הבאה; הנתונים בזיכרון כבר נקיים
    return users


def _save_users(users):
    with storage.file_lock(config.USERS_DB_FILE):
        storage.atomic_write_json(config.USERS_DB_FILE, users)


def _storage_safe(fn):
    """פונקציות כתיבה מחזירות (False, הודעה) במקום לקרוס/לדרוס כשהאחסון פגום."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except storage.StorageError as exc:
            return False, f"שגיאת אחסון - השינוי לא נשמר: {exc}"
    return wrapper


def username_exists(username):
    return username.strip().lower() in _load_users()


# ---------------- Registration ----------------
def _normalize_phrase(value):
    """נרמול ביטויי קוד להשוואה: אותיות/ספרות בלבד, בלי תלות בפיסוק וברווחים."""
    value = (value or "").strip().lower()
    return " ".join(re.findall(r"[\w\u0590-\u05ff]+", value, flags=re.UNICODE))


def _phrases_overlap(first, second):
    first_norm = _normalize_phrase(first)
    second_norm = _normalize_phrase(second)
    return bool(first_norm and second_norm and (
        first_norm == second_norm
        or (" " + first_norm + " ") in (" " + second_norm + " ")
        or (" " + second_norm + " ") in (" " + first_norm + " ")
    ))


@_storage_safe
def register_user(username, password, contacts, security_question="", security_answer="",
                  voice_secret_word="", silent_alarm_phrase="", secret_cancel_phrase="",
                  terms_accepted=False, duress_phrase="", phone=""):
    """רישום חשבון עם סיסמה אחת, שלושה ביטויי פעולה וביטוי כפייה אופציונלי. אנשי קשר וטלפון - אופציונליים."""
    username_key = (username or "").strip().lower()
    if not username_key:
        return False, "שם משתמש לא יכול להיות ריק"

    with _LOCK:
        users = _load_users(strict=True)
        if username_key in users:
            return False, "שם המשתמש כבר קיים"

        ok, err = validation.validate_username(username)
        if not ok:
            return False, err

        ok, err = validation.validate_password(password, username=username)
        if not ok:
            return False, err

        # אנשי קשר אופציונליים בהרשמה: אפשר להוסיף אחר כך במעגל הביטחון. בלי אנשי קשר SOS עדיין
        # נשלח למתנדבים זמינים בקרבת מקום דרך השרת.
        contacts = contacts or []

        phrases = {
            "ביטוי האזעקה השקטה": (silent_alarm_phrase or "").strip(),
            "ביטוי האזעקה הרועשת": (voice_secret_word or "").strip(),
            "ביטוי הביטול הקולי": (secret_cancel_phrase or "").strip(),
        }
        if (duress_phrase or "").strip():
            phrases["ביטוי הכפייה הסודי"] = (duress_phrase or "").strip()
        normalized = {}
        for label, phrase in phrases.items():
            if len(phrase) < 3:
                return False, f"{label} חייב להכיל לפחות 3 תווים"
            normalized[label] = _normalize_phrase(phrase)
            if not normalized[label]:
                return False, f"{label} אינו תקין"

        labels = list(normalized)
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a, b = normalized[labels[i]], normalized[labels[j]]
                # מונעים גם ביטויים שאחד מהם כלול בתוך השני - אחרת הזיהוי עלול להיות עמום.
                if _phrases_overlap(a, b):
                    return False, f"{labels[i]} ו{labels[j]} חייבים להיות שונים וברורים זה מזה"

        # ביטוי קולי לא יכול להיות גם סיסמת החשבון/הביטול.
        password_norm = _normalize_phrase(password)
        if any(_phrases_overlap(password_norm, value) for value in normalized.values()):
            return False, "מילות הקוד חייבות להיות שונות מסיסמת החשבון"

        # אנשי קשר הם חשבונות LifeGuard / הזמנות פנימיות בלבד. אין איסוף מספרי טלפון.
        normalized_contacts = []
        for contact in contacts:
            name = (contact.get("name") or "").strip()
            recipient = (contact.get("account") or contact.get("email") or "").strip().lower()
            role = contact.get("role", "family")
            if name and recipient:
                normalized_contacts.append({"name": name, "account": recipient,
                                            "role": role if role in ("family", "volunteer") else "family"})

        pwd_salt, pwd_hash = _hash_secret(password)
        user_record = {
            "display_name": username.strip(),
            "password_salt": pwd_salt,
            "password_hash": pwd_hash,
            "contacts": normalized_contacts,
            # הטלפון של המשתמש עצמו - נשלח לשרת (שם נשמר רק כ-hash) כדי להתאים הזמנות שנשלחו אליו
            "phone": re.sub(r"[^\d+]", "", phone or "")[:20],
            # שומרים גם את שם השדה הישן כדי לתמוך בפרופילים/גרסאות קודמות.
            "loud_alarm_phrase": phrases["ביטוי האזעקה הרועשת"],
            "voice_secret_word": phrases["ביטוי האזעקה הרועשת"],
            "silent_alarm_phrase": phrases["ביטוי האזעקה השקטה"],
            "secret_cancel_phrase": phrases["ביטוי הביטול הקולי"],
            "duress_phrase": phrases.get("ביטוי הכפייה הסודי", ""),
            "created_at": time.time(),
            "terms_accepted_at": time.time() if terms_accepted else None,
            "failed_login_attempts": 0,
            "locked_until": 0,
        }

        users[username_key] = user_record
        _save_users(users)
        return True, "נרשמת בהצלחה!"


# ---------------- Login ----------------
@_storage_safe
def verify_login(username, password):
    """מחזיר (success: bool, message: str)"""
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "שם משתמש לא קיים"

        now = time.time()
        if user.get("locked_until", 0) > now:
            remaining = int(user["locked_until"] - now)
            return False, f"החשבון נעול זמנית ({remaining} שניות) עקב נסיונות שגויים מרובים"

        if _verify_secret(password, user["password_salt"], user["password_hash"]):
            user["failed_login_attempts"] = 0
            users[key] = user
            _save_users(users)
            return True, "התחברת בהצלחה"

        user["failed_login_attempts"] = user.get("failed_login_attempts", 0) + 1
        if user["failed_login_attempts"] >= config.MAX_LOGIN_ATTEMPTS:
            user["locked_until"] = now + config.LOGIN_LOCKOUT_SECONDS
            user["failed_login_attempts"] = 0
            users[key] = user
            _save_users(users)
            return False, f"יותר מדי נסיונות שגויים - החשבון ננעל ל-{config.LOGIN_LOCKOUT_SECONDS} שניות"

        users[key] = user
        _save_users(users)
        return False, "סיסמה שגויה"


# ---------------- Legacy PIN compatibility (old profiles only) ----------------
def verify_pin(username, pin, kind):
    """kind: 'cancel' או 'quit'"""
    users = _load_users()
    user = users.get(username.strip().lower())
    if not user:
        return False
    salt_key = f"{kind}_pin_salt"
    hash_key = f"{kind}_pin_hash"
    if salt_key not in user or hash_key not in user:
        return False
    return _verify_secret(pin, user[salt_key], user[hash_key], iterations=50_000)


@_storage_safe
def reset_pin_with_security_answer(username, security_answer, kind, new_pin):
    """שחזור PIN שנשכח דרך שאלת האבטחה. kind: 'cancel' או 'quit'"""
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        if not _verify_secret(
            security_answer.strip().lower(),
            user["security_answer_salt"], user["security_answer_hash"], iterations=50_000
        ):
            return False, "תשובת האבטחה שגויה"
        if not (new_pin.isdigit() and len(new_pin) >= 4):
            return False, "קוד חדש חייב להיות מספרי, לפחות 4 ספרות"
        ok, err = validation.validate_pin(new_pin, label="הקוד החדש")
        if not ok:
            return False, err

        salt, h = _hash_secret(new_pin, iterations=50_000)
        user[f"{kind}_pin_salt"] = salt
        user[f"{kind}_pin_hash"] = h
        users[key] = user
        _save_users(users)
        return True, "הקוד עודכן בהצלחה"


# ---------------- Data access ----------------
def get_contacts(username):
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("contacts", []) if user else []


def get_phone(username):
    user = _load_users().get(username.strip().lower())
    return (user or {}).get("phone", "")


@_storage_safe
def set_phone(username, phone):
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        if key not in users:
            return False, "משתמש לא קיים"
        users[key]["phone"] = re.sub(r"[^\d+]", "", phone or "")[:20]
        _save_users(users)
        return True, "מספר הטלפון נשמר"


def get_display_name(username):
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("display_name", username) if user else username


def get_security_question(username):
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("security_question", "") if user else ""


def get_voice_secret_word(username):
    users = _load_users()
    user = users.get(username.strip().lower())
    return (user.get("loud_alarm_phrase") or user.get("voice_secret_word", "")) if user else ""


def get_silent_alarm_phrase(username):
    """הביטוי (מוקלד+קולי, אותו טקסט) שמפעיל אזעקה שקטה."""
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("silent_alarm_phrase", "") if user else ""


def get_secret_cancel_phrase(username):
    """הביטוי (מוקלד+קולי) שמבטל מיד כל אזעקה פעילה - רועשת או שקטה."""
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("secret_cancel_phrase", "") if user else ""


def get_duress_phrase(username):
    """ביטוי כפייה: נראה כלפי חוץ כמו ביטול, אך מפעיל/משאיר מעקב שקט."""
    users = _load_users()
    user = users.get(username.strip().lower())
    return user.get("duress_phrase", "") if user else ""


def verify_silent_alarm_phrase_typed(username, entered_text):
    """השוואה מדויקת (לא substring) - להקלדה בשדה קוד הביטול, לא לתמלול קולי."""
    phrase = get_silent_alarm_phrase(username)
    entered_text = (entered_text or "").strip().lower()
    return bool(phrase) and entered_text == phrase.strip().lower()


def verify_secret_cancel_phrase_typed(username, entered_text):
    phrase = get_secret_cancel_phrase(username)
    entered_text = (entered_text or "").strip().lower()
    return bool(phrase) and entered_text == phrase.strip().lower()


def verify_duress_phrase_typed(username, entered_text):
    phrase = get_duress_phrase(username)
    entered_text = (entered_text or "").strip().lower()
    return bool(phrase) and entered_text == phrase.strip().lower()


def validate_new_password(username, new_password):
    """אותן בדיקות כמו update_password, בלי לשמור (כדי לבדוק לפני החלפה בשרת)."""
    ok, err = validation.validate_password(new_password, username=username)
    if not ok:
        return False, err
    user = _load_users().get((username or "").strip().lower()) or {}
    new_normalized = _normalize_phrase(new_password)
    for phrase in (user.get("loud_alarm_phrase") or user.get("voice_secret_word", ""),
                   user.get("silent_alarm_phrase", ""), user.get("secret_cancel_phrase", "")):
        if phrase and new_normalized and _phrases_overlap(new_normalized, _normalize_phrase(phrase)):
            return False, "סיסמת החשבון חייבת להיות שונה ממילות הקוד הקוליות"
    return True, ""


@_storage_safe
def update_password(username, new_password):
    """עדכון סיסמת החשבון - אותה סיסמה משמשת גם לאימות ביטול חירום."""
    ok, err = validation.validate_password(new_password, username=username)
    if not ok:
        return False, err
    with _LOCK:
        users = _load_users(strict=True)
        key = (username or "").strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        new_normalized = _normalize_phrase(new_password)
        existing_phrases = (
            user.get("loud_alarm_phrase") or user.get("voice_secret_word", ""),
            user.get("silent_alarm_phrase", ""),
            user.get("secret_cancel_phrase", ""),
        )
        if any(new_normalized and _phrases_overlap(new_normalized, _normalize_phrase(phrase))
               for phrase in existing_phrases if phrase):
            return False, "סיסמת החשבון חייבת להיות שונה ממילות הקוד הקוליות"
        salt, password_hash = _hash_secret(new_password)
        user["password_salt"] = salt
        user["password_hash"] = password_hash
        # סיסמה חדשה מאפסת ספירת ניסיונות כניסה קודמים.
        user["failed_login_attempts"] = 0
        user["locked_until"] = 0
        users[key] = user
        _save_users(users)
        return True, "סיסמת החשבון עודכנה. זו גם סיסמת ביטול האזעקה."


# ---------------- עדכון פרופיל (מסך הגדרות - המשתמש כבר מחובר) ----------------
def verify_password(username, password):
    """בדיקת סיסמת החשבון; לא ננעלת זמנית כדי לא לחסום ביטול אירוע חירום."""
    users = _load_users()
    user = users.get((username or "").strip().lower())
    if not user or not user.get("password_salt") or not user.get("password_hash"):
        return False
    return _verify_secret(password, user["password_salt"], user["password_hash"])


@_storage_safe
def update_pin(username, kind, new_pin):
    """
    kind: 'cancel'/'quit'. מחזיר (bool, msg). לשימוש ממסך הגדרות (המשתמש כבר מאומת).
    בודק שה-PIN החדש לא זהה ל-PIN האחר הקיים - אי אפשר להשוות ישירות (הוא מוצפן), אז
    משתמשים ב-verify_pin עצמו כדי לבדוק אם הערך החדש "עובר" בטעות כאילו הוא הקוד האחר.
    """
    ok, err = validation.validate_pin(new_pin, label="הקוד החדש")
    if not ok:
        return False, err

    other_kind = "quit" if kind == "cancel" else "cancel"
    if verify_pin(username, new_pin, other_kind):
        other_label = "קוד הכיבוי" if kind == "cancel" else "קוד הביטול"
        return False, f"הקוד החדש זהה ל{other_label} הקיים - הם חייבים להיות שונים"
    if verify_silent_alarm_phrase_typed(username, new_pin) or verify_secret_cancel_phrase_typed(username, new_pin):
        return False, "הקוד החדש זהה לביטוי אזעקה שקטה/ביטול סודי קיים - הם חייבים להיות שונים"

    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"

        salt, h = _hash_secret(new_pin, iterations=50_000)
        user[f"{kind}_pin_salt"] = salt
        user[f"{kind}_pin_hash"] = h
        users[key] = user
        _save_users(users)
        return True, "הקוד עודכן בהצלחה"


@_storage_safe
def update_contacts(username, new_contacts):
    """מחליף אנשי קשר באפליקציה; אין שימוש במספרי טלפון או WhatsApp."""
    normalized = []
    for contact in new_contacts:
        name = (contact.get("name") or "").strip()
        recipient = (contact.get("account") or contact.get("email") or "").strip().lower()
        role = contact.get("role", "family")
        if name and recipient:
            normalized.append({"name": name, "account": recipient,
                               "role": role if role in ("family", "volunteer") else "family"})
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        user["contacts"] = normalized
        users[key] = user
        _save_users(users)
        return True, "אנשי הקשר עודכנו בהצלחה"


def _check_phrase_conflict(username, new_phrase, exclude_field):
    """מוודא שביטוי קולי חדש אינו קצר מדי או מתנגש עם פקודה אחרת/סיסמת החשבון."""
    new_phrase = (new_phrase or "").strip()
    if not new_phrase:
        return "מילת הקוד אינה יכולה להיות ריקה - שלוש הפקודות חייבות להישאר מוגדרות"
    if len(new_phrase) < 3 or not _normalize_phrase(new_phrase):
        return "מילת הקוד חייבת להכיל לפחות 3 תווים"

    fields = {
        "voice_secret_word": ("מילת הקוד לאזעקה רועשת", get_voice_secret_word),
        "silent_alarm_phrase": ("מילת הקוד למצוקה שקטה", get_silent_alarm_phrase),
        "secret_cancel_phrase": ("מילת הקוד לביטול", get_secret_cancel_phrase),
        "duress_phrase": ("מילת הקוד לכפייה", get_duress_phrase),
    }
    for field_key, (label, getter) in fields.items():
        if field_key == exclude_field:
            continue
        existing = getter(username)
        if existing and _phrases_overlap(new_phrase, existing):
            return f'הביטוי מתנגש עם "{label}" - בחר/י צירוף שונה וברור יותר'

    if verify_pin(username, new_phrase, "cancel") or verify_pin(username, new_phrase, "quit"):
        return "הביטוי לא יכול להיות זהה לקוד ישן שמור"
    if verify_password(username, new_phrase):
        return "מילת הקוד חייבת להיות שונה מסיסמת החשבון"

    return None


@_storage_safe
def update_voice_secret_word(username, new_word):
    conflict = _check_phrase_conflict(username, new_word, exclude_field="voice_secret_word")
    if conflict:
        return False, conflict
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        user["loud_alarm_phrase"] = (new_word or "").strip()
        user["voice_secret_word"] = (new_word or "").strip()
        users[key] = user
        _save_users(users)
        return True, "מילת הקוד הקולית עודכנה"


@_storage_safe
def update_silent_alarm_phrase(username, new_phrase):
    conflict = _check_phrase_conflict(username, new_phrase, exclude_field="silent_alarm_phrase")
    if conflict:
        return False, conflict
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        user["silent_alarm_phrase"] = (new_phrase or "").strip()
        users[key] = user
        _save_users(users)
        return True, "ביטוי האזעקה השקטה עודכן"


@_storage_safe
def update_secret_cancel_phrase(username, new_phrase):
    conflict = _check_phrase_conflict(username, new_phrase, exclude_field="secret_cancel_phrase")
    if conflict:
        return False, conflict
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        user["secret_cancel_phrase"] = (new_phrase or "").strip()
        users[key] = user
        _save_users(users)
        return True, "קוד הביטול הסודי עודכן"


@_storage_safe
def update_duress_phrase(username, new_phrase):
    """מעדכן ביטוי כפייה; ניתן להשאיר ריק כדי לכבות את התכונה."""
    new_phrase = (new_phrase or "").strip()
    if new_phrase:
        conflict = _check_phrase_conflict(username, new_phrase, exclude_field="duress_phrase")
        if conflict:
            return False, conflict
    with _LOCK:
        users = _load_users(strict=True)
        key = username.strip().lower()
        user = users.get(key)
        if not user:
            return False, "משתמש לא קיים"
        user["duress_phrase"] = new_phrase
        users[key] = user
        _save_users(users)
        return True, ("ביטוי הכפייה עודכן" if new_phrase else "מצב הכפייה הושבת")

# -*- coding: utf-8 -*-
"""
storage.py
שכבת אחסון משותפת ובטוחה לכל המודולים:

1. כתיבת JSON אטומית (קובץ זמני + os.replace) - קריסה באמצע כתיבה לא משאירה קובץ חצי-כתוב.
2. קריאת JSON "בטוחה": קובץ פגום *לא* מוחלף בשקט במבנה ריק. הקובץ הפגום מגובה לצד המקור
   ונזרקת StorageError, כדי שהקורא יחליט (ולא ימחק בטעות את כל המשתמשים/האירועים).
3. חיבור SQLite עם מיגרציות ממוספרות (טבלת schema_version) ו-WAL.
4. הצפנת שדות רגישים במנוחה באמצעות Windows DPAPI (CryptProtectData) כשזמין.
   DPAPI קושר את ההצפנה לחשבון Windows של המשתמש: מגן מפני העתקת קבצים/גיבוי שדלף או
   משתמש Windows אחר - *לא* מפני מי שכבר מחובר לאותו חשבון Windows.
"""

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import time

try:
    import win32crypt  # pywin32
    HAS_DPAPI = True
except Exception:  # לא Windows / pywin32 לא מותקן
    win32crypt = None
    HAS_DPAPI = False

_ENC_PREFIX = "dpapi:"
_JSON_LOCKS = {}
_JSON_LOCKS_GUARD = threading.Lock()


class StorageError(Exception):
    """שגיאת אחסון שאסור להתעלם ממנה בשקט (קובץ פגום, אין הרשאת כתיבה וכו')."""


def file_lock(path):
    """נעילה לכל קובץ - מונעת מצב שבו שני threads כותבים לאותו JSON במקביל."""
    path = os.path.abspath(path)
    with _JSON_LOCKS_GUARD:
        lock = _JSON_LOCKS.get(path)
        if lock is None:
            lock = threading.RLock()
            _JSON_LOCKS[path] = lock
        return lock


def atomic_write_json(path, data):
    """כותב JSON לקובץ זמני באותה תיקייה ומחליף אטומית. זורק StorageError בכשל."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise
    except Exception as exc:
        raise StorageError(f"כתיבה ל-{os.path.basename(path)} נכשלה: {exc}") from exc


def load_json(path, default):
    """
    טוען JSON. קובץ חסר -> default. קובץ פגום -> מגבה אותו (`.corrupt-<ts>`) וזורק StorageError.
    לעולם לא מחזיר default עבור קובץ קיים ופגום - זה מה שגרם בעבר לדריסת כל המשתמשים.
    """
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        backup = f"{path}.corrupt-{int(time.time())}"
        try:
            shutil.copy2(path, backup)
        except Exception:
            backup = "(גיבוי נכשל)"
        raise StorageError(
            f"הקובץ {os.path.basename(path)} פגום ולא נטען ({exc}). עותק נשמר ב-{backup}"
        ) from exc


# ==================== SQLite + מיגרציות ====================

def connect_sqlite(path, migrations):
    """
    פותח חיבור SQLite ומריץ מיגרציות חסרות. migrations: רשימת מחרוזות SQL (או callables
    שמקבלים connection) - אינדקס i+1 הוא מספר הגרסה. מיגרציה שכבר רצה לא תרוץ שוב.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("PRAGMA journal_mode=WAL")
        # WAL + NORMAL: אין fsync על כל commit (רק בנקודות checkpoint). עמיד לקריסת תהליך; בנפילת חשמל
        # עלולה לאבד רק הטרנזקציה האחרונה. בבדיקת עומס: פי 4-20 מהר יותר בכתיבות (fsync היה צוואר הבקבוק)
        con.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.DatabaseError:
        pass
    con.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
    row = con.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    current = row["v"] or 0
    for index, migration in enumerate(migrations, start=1):
        if index <= current:
            continue
        with con:
            if callable(migration):
                migration(con)
            else:
                con.executescript(migration)
            con.execute("INSERT INTO schema_version(version) VALUES (?)", (index,))
    con.commit()
    return con


def sqlite_health(path):
    """(ok: bool, detail: str) - בדיקת שלמות אמיתית (PRAGMA quick_check), לא רק קיום קובץ."""
    if not os.path.exists(path):
        return False, "קובץ מסד הנתונים עדיין לא נוצר"
    try:
        con = sqlite3.connect(path, timeout=3)
        try:
            result = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        return result == "ok", ("תקין" if result == "ok" else f"בדיקת שלמות נכשלה: {result}")
    except Exception as exc:
        return False, f"לא ניתן לפתוח: {exc}"


# ==================== הצפנה במנוחה (DPAPI) ====================

def protect_text(text):
    """מצפין טקסט עם DPAPI (אם זמין). ריק נשאר ריק. ללא DPAPI - מוחזר כמו שהוא (מתועד)."""
    if not text:
        return text or ""
    if not HAS_DPAPI:
        return text
    blob = win32crypt.CryptProtectData(text.encode("utf-8"), "LifeGuard", None, None, None, 0)
    return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")


def unprotect_text(value):
    """מפענח ערך שהוצפן ב-protect_text. ערך לא מוצפן (legacy) מוחזר כמו שהוא."""
    if not value or not isinstance(value, str) or not value.startswith(_ENC_PREFIX):
        return value or ""
    if not HAS_DPAPI:
        raise StorageError("הנתון מוצפן ב-DPAPI אך pywin32 אינו זמין לפענוח")
    blob = base64.b64decode(value[len(_ENC_PREFIX):])
    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data.decode("utf-8")


def is_protected(value):
    return isinstance(value, str) and value.startswith(_ENC_PREFIX)


def free_disk_bytes(path):
    try:
        return shutil.disk_usage(os.path.abspath(path)).free
    except Exception:
        return None

# -*- coding: utf-8 -*-
"""
agent_bridge.py
עזרים משותפים לאפליקציה הראשית (main.py) ולסוכן הרקע (lifeguard_agent.py):

- provision/load_session/clear_session: לסוכן יש סשן שרת *נפרד* (device="LifeGuard Windows Agent"),
  כך שאפשר לבטל אותו בנפרד מהאפליקציה. הטוקן נשמר מוצפן ב-DPAPI (storage.protect_text) בקובץ
  <DATA_DIR>/agent_session.json. הסיסמה לעולם לא נשמרת.
- mark_app_running/is_app_running: mutex בשם Local\\LifeGuard.App שהאפליקציה מחזיקה כל עוד היא רצה.
  הסוכן בודק אותו (OpenMutex) ושותק כשהאפליקציה פתוחה - כולל במצב "מעקב שקט"/כפייה, שבו האפליקציה
  רצה ומחזיקה את ה-mutex, ולכן הסוכן לא מציג דבר.
- install_autostart/uninstall_autostart/is_autostart_installed: HKCU\\...\\Run (בלי הרשאות מנהל).
- register_protocol/parse_open_arg: פרוטוקול lifeguard:// (HKCU\\Software\\Classes) - לחיצה על ההודעה
  פותחת את האפליקציה על האירוע. הקלט של --open נשלט ע"י כל דף אינטרנט שמקשר ל-lifeguard://,
  ולכן מאומת בקפדנות (רק inc_/esc_ + 32 תווי hex, פרמטר אחד בלבד).
"""

import os
import re
import sys
import time
from urllib.parse import parse_qsl, urlsplit

import config
import storage

AGENT_DEVICE = "LifeGuard Windows Agent"
SESSION_FILE = "agent_session.json"
STATE_FILE = "agent_state.json"
APP_MUTEX = r"Local\LifeGuard.App"
AGENT_MUTEX = r"Local\LifeGuard.Agent"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LifeGuardAgent"
CLASSES_KEY = r"Software\Classes"
PROTOCOL = "lifeguard"

_HERE = os.path.dirname(os.path.abspath(__file__))
_app_mutex = None  # handle שמוחזק לכל חיי התהליך


# ==================== סשן הסוכן ====================
def _path(name, data_dir=None):
    return os.path.join(data_dir or config.DATA_DIR, name)


def session_path(data_dir=None):
    return _path(SESSION_FILE, data_dir)


def state_path(data_dir=None):
    return _path(STATE_FILE, data_dir)


def provision(base_url, username, password, data_dir=None, revoke_previous=True):
    """מתחבר עם סשן מכשיר נפרד לסוכן ושומר אותו (טוקן מוצפן). מחזיר True/False. לא זורק."""
    from backend_client import BackendClient
    try:
        client = BackendClient(base_url)
        data = client.login(username, password, device=AGENT_DEVICE)
    except Exception:
        return False
    old = load_session(data_dir)
    try:
        storage.atomic_write_json(session_path(data_dir), {
            "base_url": client.base_url, "username": data["username"],
            "token": storage.protect_text(data["token"]), "created_at": time.time()})
    except Exception:
        _revoke(client.base_url, data["token"])
        return False
    # סשן קודם של הסוכן (אם היה) מבוטל בשרת - לא מצטברים טוקנים פעילים
    if old and old.get("token") and old["token"] != data["token"] and revoke_previous:
        _revoke(old["base_url"], old["token"])
    return True


def load_session(data_dir=None):
    """{base_url, username, token (מפוענח), created_at} או None."""
    try:
        raw = storage.load_json(session_path(data_dir), None)
    except storage.StorageError:
        return None
    if not isinstance(raw, dict) or not raw.get("token") or not raw.get("base_url"):
        return None
    try:
        token = storage.unprotect_text(raw["token"])
    except Exception:
        return None
    if not token:
        return None
    return {"base_url": raw["base_url"], "username": raw.get("username"), "token": token,
            "created_at": raw.get("created_at")}


def clear_session(data_dir=None, revoke=False):
    """מוחק את סשן הסוכן (ובקשה: גם מבטל את הטוקן בשרת - להתנתקות)."""
    if revoke:
        s = load_session(data_dir)
        if s:
            _revoke(s["base_url"], s["token"])
    for name in (SESSION_FILE, STATE_FILE):
        try:
            os.remove(_path(name, data_dir))
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _revoke(base_url, token):
    try:
        from backend_client import BackendClient
        BackendClient(base_url, token=token).logout()
    except Exception:
        pass


# ==================== האם האפליקציה רצה (mutex בין תהליכים) ====================
def mark_app_running(name=APP_MUTEX):
    """נקרא פעם אחת בעליית האפליקציה. ה-handle נשמר ברמת המודול ולכן ה-mutex קיים עד סוף התהליך."""
    global _app_mutex
    if _app_mutex is not None or sys.platform != "win32":
        return _app_mutex is not None
    try:
        import win32event
        _app_mutex = win32event.CreateMutex(None, False, name)
        return True
    except Exception:
        return False


def release_app_running():
    """לבדיקות בלבד / יציאה מסודרת."""
    global _app_mutex
    if _app_mutex is not None:
        try:
            _app_mutex.Close()
        except Exception:
            pass
        _app_mutex = None


def mutex_exists(name):
    if sys.platform != "win32":
        return False
    import pywintypes
    import win32event
    SYNCHRONIZE = 0x00100000
    try:
        h = win32event.OpenMutex(SYNCHRONIZE, False, name)
    except pywintypes.error:
        return False
    h.Close()
    return True


def is_app_running(name=APP_MUTEX):
    return mutex_exists(name)


def acquire_single_instance(name=AGENT_MUTEX):
    """מחזיר handle אם זה המופע היחיד, או None אם מופע אחר כבר מחזיק את ה-mutex."""
    if sys.platform != "win32":
        return object()
    import win32api
    import win32event
    import winerror
    h = win32event.CreateMutex(None, True, name)
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        h.Close()
        return None
    return h


# ==================== הפעלה אוטומטית בכניסה ל-Windows ====================
def pythonw_path():
    exe = sys.executable
    d, base = os.path.split(exe)
    if base.lower() == "python.exe" and os.path.exists(os.path.join(d, "pythonw.exe")):
        return os.path.join(d, "pythonw.exe")
    return exe


def agent_command():
    if getattr(sys, "frozen", False):  # EXE: ה-EXE צריך לטפל ב---agent (ראה הדוח)
        return f'"{sys.executable}" --agent'
    return f'"{pythonw_path()}" "{os.path.join(_HERE, "lifeguard_agent.py")}"'


def install_autostart(value_name=RUN_VALUE, key_path=RUN_KEY, command=None):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, value_name, 0, winreg.REG_SZ, command or agent_command())
    return True


def uninstall_autostart(value_name=RUN_VALUE, key_path=RUN_KEY):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, value_name)
        return True
    except FileNotFoundError:
        return False


def is_autostart_installed(value_name=RUN_VALUE, key_path=RUN_KEY):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
            winreg.QueryValueEx(k, value_name)
        return True
    except FileNotFoundError:
        return False


def ensure_agent_running(mutex_name=AGENT_MUTEX):
    """מפעיל את הסוכן מיד (בלי לחכות לכניסה הבאה ל-Windows) אם הוא לא רץ. הסוכן שותק כל עוד
    האפליקציה פתוחה, ומתחיל לקבל התראות רק כשהיא נסגרת."""
    if sys.platform != "win32" or mutex_exists(mutex_name):
        return False
    import subprocess
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
             | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    cmd = ([sys.executable, "--agent"] if getattr(sys, "frozen", False)
           else [pythonw_path(), os.path.join(_HERE, "lifeguard_agent.py")])
    try:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=_HERE)
        return True
    except OSError:
        return False


# ==================== פרוטוקול lifeguard:// ====================
def protocol_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --open "%1"'
    return f'"{pythonw_path()}" "{os.path.join(_HERE, "main.py")}" --open "%1"'


def register_protocol(scheme=PROTOCOL, classes_key=CLASSES_KEY, command=None):
    import winreg
    base = rf"{classes_key}\{scheme}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:LifeGuard")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, command or protocol_command())
    return True


def unregister_protocol(scheme=PROTOCOL, classes_key=CLASSES_KEY):
    import winreg
    base = rf"{classes_key}\{scheme}"
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + sub)
        except FileNotFoundError:
            pass


_ID_RE = {"incident": re.compile(r"inc_[0-9a-f]{32}"), "escort": re.compile(r"esc_[0-9a-f]{32}")}


def open_uri(kind=None, ident=None):
    """URI ל-launch של הודעה. מזהה לא תקין -> פשוט פותחים את האפליקציה."""
    if kind in _ID_RE and isinstance(ident, str) and _ID_RE[kind].fullmatch(ident):
        return f"{PROTOCOL}://open?{kind}={ident}"
    return f"{PROTOCOL}://open"


def parse_open_arg(argv):
    """argv -> {"incident": id} / {"escort": id} / {}. קלט עוין: כל חריגה -> {}."""
    argv = list(argv or [])
    values = []
    for i, a in enumerate(argv):
        if a == "--open":
            values.append(argv[i + 1] if i + 1 < len(argv) else "")
        elif isinstance(a, str) and a.startswith("--open="):
            values.append(a[len("--open="):])
    if len(values) != 1:
        return {}
    uri = values[0]
    if not isinstance(uri, str) or len(uri) > 200 or any(ord(c) < 0x21 or ord(c) > 0x7e for c in uri):
        return {}
    try:
        p = urlsplit(uri)
    except ValueError:
        return {}
    if (p.scheme.lower() != PROTOCOL or p.netloc.lower() != "open" or p.path not in ("", "/")
            or p.fragment or "@" in uri or "%" in uri):
        return {}
    if not p.query:
        return {}
    try:
        pairs = parse_qsl(p.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return {}
    if len(pairs) != 1:
        return {}
    key, value = pairs[0]
    rx = _ID_RE.get(key)
    if rx is None or not rx.fullmatch(value):
        return {}
    return {key: value}

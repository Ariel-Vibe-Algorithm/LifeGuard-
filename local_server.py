# -*- coding: utf-8 -*-
"""
local_server.py - הפעלה אוטומטית של שרת LifeGuard המקומי.

הבעיה: במצב מקומי (בלי server_url.txt) האפליקציה מתחברת ל-127.0.0.1:8765, אבל השרת רץ רק אם מישהו
הפעיל את RUN_SERVER_WINDOWS.bat. מי שפתח רק את האפליקציה קיבל "לא מחובר לשרת" בלי הסבר.

הפתרון: לפני ההתחברות - אם הכתובת מקומית והשרת לא עונה, מפעילים אותו ברקע (בלי חלון, תהליך נפרד
שממשיך לרוץ גם אחרי סגירת האפליקציה - סוכן הרקע צריך אותו), ממתינים עד שהוא עונה, ורק אז מתחברים.
שרת מרוחק (https://...) לא מופעל מכאן לעולם. הפלט של השרת נכתב ל-server/server_data/server.log.
"""

import os
import subprocess
import sys
import time
import urllib.parse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
START_TIMEOUT_S = 15


def is_local(url):
    return (urllib.parse.urlparse(url).hostname or "") in LOCAL_HOSTS


def is_up(url, timeout=1.5):
    try:
        r = requests.get(url.rstrip("/") + "/v1/health", timeout=timeout)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except (requests.RequestException, ValueError):
        return False


def _python_for_server():
    """pythonw (בלי חלון קונסול) מאותה סביבה; בקובץ exe ארוז אין שרת להפעיל."""
    if getattr(sys, "frozen", False):
        return None
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def log_path():
    d = os.path.join(HERE, "server", "server_data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "server.log")


def ensure_running(url, log=None, timeout=START_TIMEOUT_S, popen=subprocess.Popen, up=is_up, sleep=time.sleep):
    """
    -> (ok, message). ok=True אם השרת עונה (כבר רץ או הופעל עכשיו).
    לא עושה כלום לשרת מרוחק או כשאין קוד שרת (exe ארוז) - מחזיר (up(url), הסבר).
    """
    if up(url):
        return True, "השרת פועל"
    if not is_local(url):
        return False, "השרת המרוחק לא עונה (בדקו חיבור לאינטרנט / כתובת ב-server_url.txt)"
    py = _python_for_server()
    if py is None or not os.path.exists(os.path.join(HERE, "server", "lifeguard_server.py")):
        return False, "השרת המקומי לא פועל - הפעילו RUN_SERVER_WINDOWS.bat"
    port = urllib.parse.urlparse(url).port or 8765
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) \
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    if log:
        log("🖥️ השרת המקומי לא פעל - מפעיל אותו ברקע...")
    try:
        out = open(log_path(), "ab")
        popen([py, "-m", "server.lifeguard_server", "--port", str(port)], cwd=HERE, stdout=out,
              stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError as exc:
        return False, f"הפעלת השרת המקומי נכשלה: {exc}"
    end = time.time() + timeout
    while time.time() < end:
        if up(url):
            return True, "השרת המקומי הופעל"
        sleep(0.5)
    return False, f"השרת המקומי הופעל אבל לא עונה - פרטים ב-{log_path()}"

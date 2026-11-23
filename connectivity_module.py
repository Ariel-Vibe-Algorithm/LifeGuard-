# -*- coding: utf-8 -*-
"""
connectivity_module.py
מצב קישוריות אמיתי (לא ניחוש): ניסיון חיבור TCP קצר לכמה יעדים ציבוריים בלתי תלויים.
"online" = לפחות יעד אחד ענה. התוצאה נשמרת עם חותמת זמן, כדי שהממשק יציג גם *מתי* נבדק.

ניטור רקע (start_monitor) מעדכן כל CONNECTIVITY_CHECK_INTERVAL_SECONDS ומודיע למאזינים
רק כשהמצב משתנה - כך המנוע יודע לנסות לשלוח שוב פריטים שממתינים בתור (outbox).
"""

import socket
import threading
import time

PROBES = (
    ("1.1.1.1", 443),        # Cloudflare
    ("8.8.8.8", 53),         # Google DNS
    ("9.9.9.9", 53),         # Quad9
)
PROBE_TIMEOUT_SECONDS = 2.0
CHECK_INTERVAL_SECONDS = 15

_LOCK = threading.Lock()
_STATE = {"online": None, "checked_at": None, "detail": "טרם נבדק"}
_LISTENERS = []
_MONITOR_THREAD = None
_MONITOR_STOP = threading.Event()


def _tcp_reachable(host, port, timeout=PROBE_TIMEOUT_SECONDS):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_now():
    """בודק עכשיו ומחזיר dict מצב. לא זורק חריגות."""
    reached = next(([f"{h}:{p}"] for h, p in PROBES if _tcp_reachable(h, p)), [])  # עוצר בהצלחה הראשונה
    online = bool(reached)
    detail = (f"מחובר (הגיב: {reached[0]})" if online
              else "אין חיבור לאינטרנט - אף שרת בדיקה לא הגיב")
    changed = False
    with _LOCK:
        changed = _STATE["online"] is not None and _STATE["online"] != online
        _STATE.update({"online": online, "checked_at": time.time(), "detail": detail})
        snapshot = dict(_STATE)
        listeners = list(_LISTENERS)
    if changed:
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                pass
    return snapshot


def status():
    """המצב האחרון הידוע (ללא בדיקת רשת). online=None פירושו 'לא נבדק עדיין'."""
    with _LOCK:
        snap = dict(_STATE)
    snap["age_seconds"] = (time.time() - snap["checked_at"]) if snap["checked_at"] else None
    return snap


def add_listener(callback):
    with _LOCK:
        if callback not in _LISTENERS:
            _LISTENERS.append(callback)


def remove_listener(callback):
    with _LOCK:
        if callback in _LISTENERS:
            _LISTENERS.remove(callback)


def _monitor_loop(interval):
    while not _MONITOR_STOP.is_set():
        check_now()
        _MONITOR_STOP.wait(interval)


def start_monitor(interval=CHECK_INTERVAL_SECONDS):
    global _MONITOR_THREAD
    if _MONITOR_THREAD and _MONITOR_THREAD.is_alive():
        return
    _MONITOR_STOP.clear()
    _MONITOR_THREAD = threading.Thread(target=_monitor_loop, args=(interval,),
                                       name="LifeGuard-Connectivity", daemon=True)
    _MONITOR_THREAD.start()


def stop_monitor():
    _MONITOR_STOP.set()


def is_monitor_running():
    return bool(_MONITOR_THREAD and _MONITOR_THREAD.is_alive())


def _set_state_for_tests(online):
    with _LOCK:
        _STATE.update({"online": online, "checked_at": time.time(),
                       "detail": "מצב בדיקה" if online else "מצב בדיקה - לא מקוון"})

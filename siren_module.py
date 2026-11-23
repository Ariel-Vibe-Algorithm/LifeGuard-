# -*- coding: utf-8 -*-
"""
siren_module.py
מפעיל צליל סירנה אמיתי דרך הרמקולים (עובד out-of-the-box בווינדוס עם winsound).
מנסה גם להעלות את ווליום המערכת למקסימום (עוקף מצב שקט) - דורש pycaw (אופציונלי).
"""

import platform
import threading
import time

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    import winsound

try:
    from ctypes import cast, POINTER
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    HAS_PYCAW = True
except Exception:
    HAS_PYCAW = False


def force_max_volume():
    """
    מנסה להעלות את ווליום המערכת למקסימום (עוקף מצב Silent/Vibrate ברמת התוכנה).
    עובד רק בווינדוס ורק אם pycaw מותקן (pip install pycaw comtypes).
    אם לא זמין - מדלג בשקט ומחזיר False.
    """
    if not (IS_WINDOWS and HAS_PYCAW):
        return False
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        volume.SetMasterVolumeLevelScalar(1.0, None)  # 100%
        return True
    except Exception:
        return False


def play_siren(stop_event: threading.Event, log_callback=None):
    """
    מנגן סירנה בלולאה (צליל עולה-יורד) עד ש-stop_event מופעל.
    רץ ב-thread נפרד. אמור לרוץ בפועל בווינדוס בלי תלויות חיצוניות.
    """
    force_max_volume()
    if log_callback:
        log_callback("🔊 סירנה הופעלה")

    if not IS_WINDOWS:
        # פלטפורמה אחרת - סימולציה בלוג בלבד
        while not stop_event.is_set():
            if log_callback:
                log_callback("[SIMULATED SIREN BEEP]")
            time.sleep(0.7)
        return

    freqs = [1500, 2200]
    idx = 0
    while not stop_event.is_set():
        try:
            winsound.Beep(freqs[idx % 2], 350)
        except RuntimeError:
            # winsound.Beep יכול להיכשל בסביבות מסוימות (למשל ללא כרטיס קול)
            time.sleep(0.35)
        idx += 1

    if log_callback:
        log_callback("🔇 סירנה הופסקה")

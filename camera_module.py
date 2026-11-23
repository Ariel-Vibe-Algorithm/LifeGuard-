# -*- coding: utf-8 -*-
"""
camera_module.py
צילום אוטומטי מהמצלמה בזמן אירוע SOS.

היגיון:
- פריים חשוך מדי או "שטוח" (טקסטורה אחידה - למשל בד כיס צמוד לעדשה) -> לא מצלם כלל.
- פריים בהיר עם פנים ברורות שזוהו -> מצלם ומסמן כ"face" (עדות פוטנציאלית בעדיפות גבוהה).
- פריים בהיר בלי פנים אבל עם תוכן/גיוון (רואים סביבה) -> מצלם ומסמן כ"environment".

** מגבלות חשובות **
- זיהוי פנים הוא Haar Cascade קלאסי (מובנה ב-OpenCV, לא דורש אינטרנט) - מהיר אך לא
  state-of-the-art; עלול לפספס זווית קשה/תאורה חלשה, ולעיתים נדירות "לראות" פנים שאינן קיימות.
- הקריטריון ל"חושך/כיסוי" מבוסס בהירות + סטיית תקן של הפריים - היוריסטי, כמו מד הרעש,
  ומומלץ לכייל (CAMERA_DARK_BRIGHTNESS_THRESHOLD, CAMERA_FLAT_TEXTURE_THRESHOLD ב-config.py).
- על מחשב שולחני זו מצלמת ה-Webcam; בפריסה למובייל יש להחליף בגישה למצלמה האחורית של הטלפון.
"""

import os
import time
import threading
import platform

import config

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except Exception:
    HAS_CV2 = False

IS_WINDOWS = platform.system() == "Windows"


def _load_face_cascade():
    if not HAS_CV2:
        return None
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        return None if cascade.empty() else cascade
    except Exception:
        return None


def _analyze_frame(frame, cascade):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(np.mean(gray))
    texture_std = float(np.std(gray))

    is_dark_or_covered = (
        brightness < config.CAMERA_DARK_BRIGHTNESS_THRESHOLD
        or texture_std < config.CAMERA_FLAT_TEXTURE_THRESHOLD
    )

    faces_found = 0
    if not is_dark_or_covered and cascade is not None:
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        faces_found = len(faces)

    return {
        "is_dark_or_covered": is_dark_or_covered,
        "brightness": brightness,
        "texture_std": texture_std,
        "faces_found": faces_found,
    }


def capture_loop(stop_event: threading.Event, out_dir: str, on_photo_captured=None, log_callback=None):
    """
    לולאת צילום רציפה בזמן אירוע SOS - רץ ב-thread נפרד עד ש-stop_event מופעל.
    on_photo_captured(filepath, kind) נקרא בכל תמונה שנשמרה בפועל (kind: "face" / "environment").
    """
    os.makedirs(out_dir, exist_ok=True)

    if not config.CAMERA_ENABLED:
        if log_callback:
            log_callback("📷 צילום אוטומטי מושבת בהגדרות (CAMERA_ENABLED=False)")
        return

    if not HAS_CV2:
        if log_callback:
            log_callback("⚠️ OpenCV לא מותקן - צילום אוטומטי מושבת (pip install opencv-python)")
        return

    cascade = _load_face_cascade()
    cap = None
    try:
        # ב-Windows, backend DSHOW נפתח ונסגר משמעותית מהר יותר מברירת המחדל (MSMF) -
        # זה בדיוק מה שמקצר את זמן "התכבות" המצלמה שדווח כבעיה.
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if IS_WINDOWS else cv2.VideoCapture(0)
        if not cap.isOpened():
            if log_callback:
                log_callback('❌ לא ניתן לגשת למצלמה (תפוסה ע"י תוכנה אחרת? חסרה הרשאה?)')
            return
    except Exception as e:
        if log_callback:
            log_callback(f"❌ שגיאת פתיחת מצלמה: {e}")
        return

    if log_callback:
        log_callback("📷 צילום אוטומטי הופעל")

    photo_count = 0
    try:
        while not stop_event.is_set() and photo_count < config.CAMERA_MAX_PHOTOS_PER_EVENT:
            ret, frame = cap.read()
            if not ret:
                stop_event.wait(config.CAMERA_CAPTURE_INTERVAL_SECONDS)
                continue

            analysis = _analyze_frame(frame, cascade)

            if analysis["is_dark_or_covered"]:
                if log_callback:
                    log_callback(
                        f"🌑 מצלמה חשוכה/מכוסה (בהירות={analysis['brightness']:.0f}, "
                        f"טקסטורה={analysis['texture_std']:.0f}) - לא נשמרה תמונה"
                    )
            else:
                kind = "face" if analysis["faces_found"] > 0 else "environment"
                ts = int(time.time())
                fname = os.path.join(out_dir, f"{kind}_{ts}_{photo_count}.jpg")
                cv2.imwrite(fname, frame)
                photo_count += 1

                if log_callback:
                    if kind == "face":
                        log_callback(f"👤 זוהו פנים ברורות ({analysis['faces_found']}) - צולם: {os.path.basename(fname)}")
                    else:
                        log_callback(f"🌳 סביבה נראית - צולמה: {os.path.basename(fname)}")

                if on_photo_captured:
                    on_photo_captured(fname, kind)

            stop_event.wait(config.CAMERA_CAPTURE_INTERVAL_SECONDS)
    finally:
        if cap is not None:
            cap.release()
        if log_callback:
            log_callback("📷 צילום אוטומטי הופסק")

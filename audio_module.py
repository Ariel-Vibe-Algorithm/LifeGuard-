# -*- coding: utf-8 -*-
"""
audio_module.py
הקלטת שמע אמיתית מהמיקרופון בלולאות (chunks) - נשמר כקבצי WAV מקומיים.
"העלאה לענן" מסומנת כסימולציה מפורשת (TODO) - כדי לחבר Cloud Sync אמיתי
יש להחליף את upload_to_cloud() בקריאה אמיתית ל-S3 / Firebase / כל שירות אחר.
"""

import os
import time
import threading
import wave

try:
    import sounddevice as sd
    import numpy as np
    HAS_AUDIO_LIBS = True
except Exception:
    HAS_AUDIO_LIBS = False

SAMPLE_RATE = 44100
CHANNELS = 1


def _save_wav(filepath, audio_data, sample_rate):
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())


def upload_to_cloud(filepath, log_callback=None):
    """
    אין כרגע אחסון ענן מחובר. בעבר הפונקציה רשמה "[SIMULATED UPLOAD] -> Cloud Sync" בזמן
    אירוע אמיתי - כלומר הציגה העלאה שלא קרתה. כעת היא מחזירה False ביושר; הקובץ נשמר
    מקומית בלבד, וההודעה למשתמש מציינת זאת פעם אחת (ראה record_loop).
    TODO: לחבר שירות אחסון פרטי ומוצפן (ראה COMMUNITY_BACKEND_SPEC_HE.md - Media Service).
    """
    return False


def record_loop(stop_event: threading.Event, out_dir: str, chunk_seconds: int,
                 log_callback=None, on_chunk_recorded=None):
    """
    מקליט שמע אמיתי בלולאות של chunk_seconds שניות, שומר כ-WAV, ו"מעלה" כל צ'אנק.
    on_chunk_recorded(filepath) נקרא בכל צ'אנק *אמיתי* שנשמר בהצלחה (לא בסימולציה) -
    משמש לשליחה מיידית של ההקלטה לאנשי קשר (למשל דרך Telegram).
    אם sounddevice לא מותקן - עובר לסימולציה (יוצר קבצים ריקים + לוג, בלי לשלוח כלום).
    רץ ב-thread נפרד עד ש-stop_event מופעל.
    """
    os.makedirs(out_dir, exist_ok=True)
    chunk_index = 0
    if log_callback:
        log_callback("💾 ההקלטות נשמרות במחשב זה בלבד - אין העלאה לענן בגרסה זו")

    if not HAS_AUDIO_LIBS:
        # בעבר נוצרו כאן קבצי "הקלטה" מזויפים (.wav.sim) - הוסר: אין הקלטה, ולכן אין קובץ.
        if log_callback:
            log_callback("❌ אין הקלטת שמע באירוע זה: sounddevice/numpy לא מותקנים "
                         "(pip install sounddevice numpy)")
        return

    while not stop_event.is_set():
        chunk_index += 1
        try:
            frames = int(chunk_seconds * SAMPLE_RATE)
            recording = sd.rec(frames, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
            # מאפשר עצירה מוקדמת תוך כדי הקלטה
            elapsed = 0.0
            step = 0.2
            while elapsed < chunk_seconds and not stop_event.is_set():
                time.sleep(step)
                elapsed += step
            sd.stop()
            sd.wait()

            fname = os.path.join(out_dir, f"chunk_{int(time.time())}_{chunk_index}.wav")
            _save_wav(fname, recording, SAMPLE_RATE)
            if log_callback:
                log_callback(f"🎙️ הוקלט צ'אנק אמיתי #{chunk_index} ({fname})")
            if on_chunk_recorded:
                on_chunk_recorded(fname)
        except Exception as e:
            if log_callback:
                log_callback(f"❌ שגיאת הקלטה: {e}")
            stop_event.wait(1)

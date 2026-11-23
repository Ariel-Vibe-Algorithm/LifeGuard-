# -*- coding: utf-8 -*-
"""
voice_input.py - הכתבה קולית (לחיצה מתחילה, לחיצה נוספת מסיימת), זיהוי דיבור **מקומי** בעברית
(speech_local / sherpa-onnx). בלי ענן: מה שנאמר לא יוצא מהמחשב - רק הטקסט שהמשתמש/ת בוחר/ת לשלוח.

- עד MAX_SECONDS שניות (עצירה אוטומטית).
- בזמן SOS המיקרופון כבר מקליט ראיות - Windows מאפשר כמה זרמי קלט במקביל (shared mode); אם לא -
  מחזירים שגיאה ברורה, וההקלטה הקיימת לא נפגעת.
"""

import threading
import time

SAMPLE_RATE = 16000
MAX_SECONDS = 15


def available():
    """רק אם יש מודל עברי מקומי + sounddevice. אחרת לא מציגים כפתור (לא מבטיחים מה שלא עובד)."""
    try:
        import sounddevice  # noqa: F401
        import speech_local
        return bool(speech_local.is_available("he"))
    except Exception:  # noqa: BLE001
        return False


class Dictation:
    """start() -> stop() -> on_done(text, error) נקרא מה-thread של הזיהוי (הקורא מעביר ל-Tk בעצמו)."""

    def __init__(self, on_done, lang="he", max_seconds=MAX_SECONDS, recorder=None, transcriber=None):
        self.on_done, self.lang, self.max_seconds = on_done, lang, max_seconds
        self._recorder, self._transcriber = recorder, transcriber
        self._frames, self._stream = [], None
        self._stop = threading.Event()
        self.active = False
        self.started_at = None

    def start(self):
        if self.active:
            return
        self.active, self.started_at = True, time.time()
        self._stop.clear()
        self._frames = []
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def elapsed(self):
        return 0 if not self.started_at else time.time() - self.started_at

    def _run(self):
        text, err = "", None
        try:
            audio = (self._recorder or self._record)()
            if audio is not None and len(audio):
                text = (self._transcriber or self._transcribe)(audio) or ""
        except Exception as exc:  # noqa: BLE001 - מוצג למשתמש/ת
            err = str(exc) or type(exc).__name__
        finally:
            self.active = False
        self.on_done(text.strip(), err)

    def _record(self):
        import numpy as np
        import sounddevice as sd

        def cb(indata, frames, t, status):  # noqa: ARG001
            self._frames.append(indata.copy())
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=cb):
            self._stop.wait(self.max_seconds)
        if not self._frames:
            return None
        return np.concatenate(self._frames).reshape(-1)

    def _transcribe(self, audio):
        import speech_local
        return speech_local.transcribe(audio, SAMPLE_RATE, self.lang)

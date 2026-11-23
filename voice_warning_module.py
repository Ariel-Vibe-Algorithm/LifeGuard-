# -*- coding: utf-8 -*-
"""
voice_warning_module.py
משמיע הודעת אזהרה קולית חוזרת (עברית, אנגלית, ערבית) - "תתרחק, המצב מתועד ועזרה בדרך".
מיועד להרתיע תוקף פוטנציאלי ולהבהיר בקול שהמצב מתועד ושיש דיווח יוצא.

שתי שכבות סינתזת דיבור, עם נפילה חכמה:
1. gTTS (Google Text-to-Speech) - איכות טובה ותמיכה אמינה בשלוש השפות, אך דורש אינטרנט.
   הקבצים נוצרים פעם אחת בלבד ונשמרים כ-cache מקומי - לא נוצרים מחדש בכל השמעה.
2. נפילה: pyttsx3 (מקומי/אופליין, מבוסס קולות המערכת של Windows - SAPI5) - עובד תמיד,
   אך תלוי אילו קולות/שפות מותקנים בפועל בווינדוס (בד"כ יש אנגלית; עברית/ערבית לא תמיד
   מותקנות כברירת מחדל, ועלולות להישמע במבטא שגוי או להיכשל).

** מגבלה חשובה **: זו הודעה קבועה מוקלטת מראש, לא תגובה חכמה למצב בשטח - היא מושמעת
בלולאה קבועה כל עוד האירוע מוגדר "מסוכן", ולא מגיבה לנסיבות בזמן אמת.
"""

import os
import threading

import config

try:
    from gtts import gTTS
    HAS_GTTS = True
except Exception:
    HAS_GTTS = False

try:
    import pyttsx3
    HAS_PYTTSX3 = True
except Exception:
    HAS_PYTTSX3 = False

try:
    from playsound import playsound
    HAS_PLAYSOUND = True
except Exception:
    HAS_PLAYSOUND = False


WARNING_TEXTS = {
    "he": "תתרחק, המצב מתועד ועזרה בדרך",
    "en": "Back away, this situation is being recorded and help is on the way",
    "ar": "ابتعد، الوضع موثق والمساعدة قادمة",
}

_cache_dir_cache = None


def _get_cache_dir():
    global _cache_dir_cache
    if _cache_dir_cache is None:
        _cache_dir_cache = os.path.join(config.DATA_DIR, "voice_warning_cache")
        os.makedirs(_cache_dir_cache, exist_ok=True)
    return _cache_dir_cache


def _generate_gtts_files(log_callback=None):
    """יוצר (פעם אחת, עם cache על הדיסק) קובץ mp3 לכל שפה. מחזיר dict {lang: filepath}."""
    cache_dir = _get_cache_dir()
    files = {}
    for lang, text in WARNING_TEXTS.items():
        fpath = os.path.join(cache_dir, f"warning_{lang}.mp3")
        if os.path.exists(fpath) and os.path.getsize(fpath) > 0:
            files[lang] = fpath
            continue
        try:
            tts = gTTS(text=text, lang=lang)
            tts.save(fpath)
            files[lang] = fpath
            if log_callback:
                log_callback(f"🗣️ נוצר קובץ אזהרה קולית חדש ({lang})")
        except Exception as e:
            if log_callback:
                log_callback(f"⚠️ נכשל ביצירת אזהרה קולית בשפה '{lang}': {e}")
    return files


def _play_file(filepath, log_callback=None):
    if not HAS_PLAYSOUND:
        if log_callback:
            log_callback("⚠️ playsound לא מותקן - לא ניתן להשמיע את קובץ האזהרה (pip install playsound==1.2.2)")
        return False
    try:
        playsound(filepath)
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"⚠️ שגיאת השמעת אזהרה קולית: {e}")
        return False


def _play_with_pyttsx3(log_callback=None):
    """נפילה חכמה: מנוע דיבור מקומי (SAPI5 בווינדוס) - לרוב רק אנגלית זמינה כברירת מחדל."""
    if not HAS_PYTTSX3:
        if log_callback:
            log_callback("⚠️ אין gTTS ואין pyttsx3 - אין אפשרות להשמיע אזהרה קולית כלל "
                         "(pip install gtts playsound==1.2.2  או  pip install pyttsx3)")
        return False
    try:
        engine = pyttsx3.init()
        for text in WARNING_TEXTS.values():
            engine.say(text)
        engine.runAndWait()
        return True
    except Exception as e:
        if log_callback:
            log_callback(f"⚠️ שגיאת מנוע דיבור מקומי (pyttsx3): {e}")
        return False


def warning_loop(stop_event: threading.Event, log_callback=None):
    """
    משמיע את הודעת האזהרה (שלוש השפות ברצף) כל config.VOICE_WARNING_INTERVAL_SECONDS,
    עד ש-stop_event מופעל. רץ ב-thread נפרד - מתאים גם לשימוש בתוך אירוע SOS וגם
    כמצב הרתעה עצמאי (בלי שאר רכיבי ה-SOS).
    """
    if not config.VOICE_WARNING_ENABLED:
        if log_callback:
            log_callback("📢 אזהרה קולית מושבתת בהגדרות (VOICE_WARNING_ENABLED=False)")
        return

    if log_callback:
        log_callback("📢 מצב הרתעה קולית הופעל (עברית/אנגלית/ערבית)")

    files = _generate_gtts_files(log_callback) if HAS_GTTS else {}

    while not stop_event.is_set():
        if files:
            for lang in ("he", "en", "ar"):
                if stop_event.is_set():
                    break
                fpath = files.get(lang)
                if fpath:
                    _play_file(fpath, log_callback)
        else:
            _play_with_pyttsx3(log_callback)

        stop_event.wait(config.VOICE_WARNING_INTERVAL_SECONDS)

    if log_callback:
        log_callback("📢 מצב הרתעה קולית הופסק")

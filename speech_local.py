# -*- coding: utf-8 -*-
"""
speech_local.py - זיהוי דיבור *במכשיר* (on-device ASR) לפקודות הקוליות ולמילות המצוקה.

למה: עד עכשיו כל קטע דיבור (כולל מילות הקוד הסודיות - ביטול/שקט/כפייה) נשלח ל-Google לתמלול.
כאן התמלול נעשה מקומית עם sherpa-onnx (Apache-2.0) ומודלי Zipformer streaming ב-int8:

  שפה  | מודל                                             | רישיון      | גודל
  he   | ivrit-ai/zipformer2-66m-streaming-he (Yakov Kolani) | CC-BY-4.0   | כ-72 MB
  en   | sherpa-onnx-streaming-zipformer-en-2023-06-26      | Apache-2.0  | כ-73 MB
  ru   | alphacep/vosk-model-small-streaming-ru (Vosk 0.54)  | Apache-2.0  | כ-28 MB
  ar   | אין מודל קטן עם רישיון ברור - לא נתמך מקומית (ראה ARABIC_NOTE)

עקרונות:
- ברירת מחדל: אודיו לעולם לא יוצא מהמכשיר. Google משמש *רק* אם config.CLOUD_SPEECH_FALLBACK=True
  (הסכמה מפורשת של המשתמש), ורק כשאין מודל מקומי לאף אחת מהשפות הנדרשות.
- אין מודל ואין הסכמה -> אין תוצאה, והודעה ברורה בעברית נרשמת פעם אחת. לא "ממציאים" זיהוי.
- תמלולים וביטויים סודיים לעולם לא נרשמים ליומן מכאן. המודול לא כותב שום דבר מלבד הודעות זמינות.
- המודלים לא במאגר הקוד: research/models/asr/<name>/ (או models/asr/<name>/ ליד ה-EXE).
  הורדה עם אימות checksum: python speech_local.py --download he en ru

הרישיון של המודל העברי (CC-BY-4.0) מחייב ייחוס: "zipformer2-66m-streaming-he by Yakov Kolani,
mirrored by ivrit.ai" - נשמר גם בקובץ SOURCE.txt בתיקיית המודל.
"""

import hashlib
import os
import re
import sys
import threading
import unicodedata
import urllib.request

import config

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

try:
    import sherpa_onnx
    HAS_ENGINE = True
except Exception:  # noqa: BLE001 - אין מנוע => אין זיהוי מקומי (לא קריסה)
    sherpa_onnx = None
    HAS_ENGINE = False


SAMPLE_RATE = 16000
USER_AGENT = "LifeGuard-Research/1.0"

# ---------------------------------------------------------------------------------------------
# קטלוג מודלים - revision מקובע + checksum שהמקור מפרסם (sha256 של LFS, או git-blob sha1 לקבצים קטנים)
# ---------------------------------------------------------------------------------------------
CATALOG = {
    "he": {
        "name": "zipformer2-66m-streaming-he",
        "repo": "ivrit-ai/zipformer2-66m-streaming-he",
        "revision": "ee769a2a13b887f91572eec349458bfb0fe79e7a",
        "license": "CC-BY-4.0",
        "attribution": "zipformer2-66m-streaming-he by Yakov Kolani (thewh1teagle), mirrored by ivrit.ai; "
                       "base model k2-fsa/icefall (Apache-2.0)",
        "model_type": "zipformer2",
        "files": {
            # local name: (path in repo, size, sha256 or None, git blob sha1)
            "encoder.onnx": ("sherpa-onnx/encoder.int8.onnx", 70478964,
                             "6c6febcb74a09435315a1d872a6539f6e7fc2c333533f14c74edc0e85c49ffcd", None),
            "decoder.onnx": ("sherpa-onnx/decoder.int8.onnx", 1049105,
                             "feb03e7c8aa761593a9284a6fa1e91259be3702de75f630062256c1a59c5a906", None),
            "joiner.onnx": ("sherpa-onnx/joiner.int8.onnx", 771805,
                            "1d2c6d8e6dabc5eb52de52e95718e9bdfbffbfe7ead3808fb5ffd74e83bd9cae", None),
            "tokens.txt": ("sherpa-onnx/tokens.txt", 17086, None, "97675cfca093bb75694b0f8e5a115b3fecf51d86"),
        },
    },
    "en": {
        # הערה: המודל הקטן יותר (sherpa-onnx-streaming-zipformer-en-20M-2023-02-17, ‏44MB) נבדק ונפסל:
        # החזיר תמלול ריק ל-9 מתוך 10 משפטי מצוקה קצרים ("help me please", "stop it right now"...),
        # גם ב-fp32. המודל הזה (66M) זיהה את כולם.
        "name": "sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "repo": "csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
        "revision": "672fbf1b30579d6585301139bb363f42a0ad4a24",
        "license": "Apache-2.0",
        "attribution": "Zengwei/icefall-asr-librispeech-streaming-zipformer-2023-05-17 (k2-fsa/icefall, LibriSpeech), "
                       "exported for sherpa-onnx by csukuangfj",
        "model_type": "zipformer2",
        "files": {
            "encoder.onnx": ("encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx", 71083163,
                             "563fde436d16cf7607cf408cd6b30909819d03162652ef389c2450ced3f45ac1", None),
            "decoder.onnx": ("decoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx", 1307236,
                             "98da299f471e38bb4e1a8df579b8cc9122d6039576a77e357b3c60f17dd83b02", None),
            "joiner.onnx": ("joiner-epoch-99-avg-1-chunk-16-left-128.int8.onnx", 259335,
                            "d944208d660d67c8d72cd2acaeac971fa5ceb8c80e76c1968148846fedd6e297", None),
            "tokens.txt": ("tokens.txt", 5048, None, "b4d1bf82e68de1354f2a3f5fd51094d61d65c77c"),
        },
    },
    "ru": {
        "name": "vosk-model-small-streaming-ru",
        "repo": "alphacep/vosk-model-small-streaming-ru",
        "revision": "e18123ee13f694036a1eea82eb43f9895387cb59",
        "license": "Apache-2.0",
        "attribution": "Vosk small streaming Russian 0.54 by Alpha Cephei (k2-fsa/icefall Zipformer2)",
        "model_type": "zipformer2",
        "files": {
            # אותם קבצים בדיוק כמו החבילה הרשמית sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16
            "encoder.onnx": ("am-onnx/encoder.int8.onnx", 26214060,
                             "e0db705e94ec35d803b1df4f40cda23d064e1142977c80ab288430b109777a9d", None),
            "decoder.onnx": ("am-onnx/decoder.onnx", 2093080,
                             "89b3088a9e20e1ef7f2e85ce1a3478afe6a9c4ac57369cabcc4beb8e95328ea0", None),
            "joiner.onnx": ("am-onnx/joiner.int8.onnx", 259417,
                            "b55784b071ab7512eab4c7c44e4f5478284ef33c83562cc6a249b972515a31e5", None),
            "tokens.txt": ("lang/tokens.txt", 6388, None, "eb7996c3921124c9c05a109fbf83ab1fac440dca"),
        },
    },
}

ARABIC_NOTE = (
    "Arabic: no small on-device model with a clear license was found (checked 2026-11). "
    "Vosk vosk-model-ar-mgb2-0.4 is Apache-2.0 but 318 MB (Kaldi, broadcast MSA); "
    "PengChengStarling multilingual zipformer (ar+7) ships without a license; "
    "moonshine-base-ar is 'license: other' with no stated terms."
)

LANGUAGE_NAMES_HE = {"he": "עברית", "en": "אנגלית", "ru": "רוסית", "ar": "ערבית"}


def language_code(tag):
    """'he-IL' -> 'he'; 'iw' (קוד ישן) -> 'he'."""
    code = (tag or "").split("-")[0].split("_")[0].lower()
    return "he" if code == "iw" else code


def _model_roots():
    base = getattr(config, "BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(base, "models", "asr"), os.path.join(base, "research", "models", "asr")]


def default_download_root():
    return _model_roots()[1]


def model_dir(lang):
    """תיקיית המודל אם כל הקבצים קיימים בגודל הנכון, אחרת None (לא מאמתים hash בכל הפעלה)."""
    spec = CATALOG.get(language_code(lang))
    if spec is None:
        return None
    for root in _model_roots():
        path = os.path.join(root, spec["name"])
        try:
            if all(os.path.getsize(os.path.join(path, local)) == meta[1] for local, meta in spec["files"].items()):
                return path
        except OSError:
            continue
    return None


def is_available(lang):
    return bool(HAS_ENGINE and np is not None and model_dir(lang))


def available_languages():
    return [lang for lang in CATALOG if is_available(lang)]


# ---------------------------------------------------------------------------------------------
# טעינה (פעם אחת לכל שפה, thread-safe) ותמלול
# ---------------------------------------------------------------------------------------------
_LOCKS_GUARD = threading.Lock()
_LOAD_LOCKS = {}       # lang -> Lock (טעינה מקבילה של שפות שונות: he ‏~3.3 שנ', en ‏~2.6 שנ')
_RECOGNIZERS = {}      # lang -> (recognizer, decode_lock)
_LOAD_ERRORS = {}      # lang -> str


def _load(lang):
    lang = language_code(lang)
    with _LOCKS_GUARD:
        lock = _LOAD_LOCKS.setdefault(lang, threading.Lock())
    with lock:
        if lang in _RECOGNIZERS:
            return _RECOGNIZERS[lang]
        if lang in _LOAD_ERRORS or not is_available(lang):
            return None
        spec, path = CATALOG[lang], model_dir(lang)
        try:
            rec = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=os.path.join(path, "tokens.txt"),
                encoder=os.path.join(path, "encoder.onnx"),
                decoder=os.path.join(path, "decoder.onnx"),
                joiner=os.path.join(path, "joiner.onnx"),
                num_threads=int(getattr(config, "LOCAL_SPEECH_NUM_THREADS", 2)),
                sample_rate=SAMPLE_RATE,
                feature_dim=80,
                decoding_method="greedy_search",
                model_type=spec["model_type"],
            )
        except Exception as exc:  # noqa: BLE001 - מודל פגום => לא זמין, לא קריסה
            _LOAD_ERRORS[lang] = str(exc)
            return None
        _RECOGNIZERS[lang] = (rec, threading.Lock())
        return _RECOGNIZERS[lang]


def preload(languages, wait=False):
    """טוען מודלים מראש (ברקע, שפה לכל thread) כדי שהזיהוי הראשון לא ימתין לטעינה."""
    threads = [threading.Thread(target=_load, args=(lang,), name="LifeGuard-ASRLoad", daemon=True)
               for lang in languages if language_code(lang) not in _RECOGNIZERS]
    for thread in threads:
        thread.start()
    if wait:
        for thread in threads:
            thread.join()


def transcribe(samples, sample_rate, lang):
    """
    מתמלל מקטע אודיו שלם (float32 מונו בטווח [-1,1]) במודל המקומי של lang.
    מחזיר str (ייתכן ריק = לא זוהה דיבור), או None אם אין מודל מקומי זמין לשפה.
    """
    loaded = _load(lang)
    if loaded is None:
        return None
    rec, decode_lock = loaded
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    with decode_lock:
        stream = rec.create_stream()
        # ריפוד שקט בהתחלה: בלעדיו מודל streaming "בולע" מילים שמתחילות ממש בדגימה הראשונה
        # (נמדד: המודל האנגלי השמיט "AFTER EARLY NIGHTFALL" בקליפ בדיקה בלי הריפוד)
        stream.accept_waveform(int(sample_rate), np.zeros(int(0.5 * sample_rate), dtype=np.float32))
        stream.accept_waveform(int(sample_rate), samples)
        # ריפוד בשקט בסוף כדי שה-chunk האחרון (1.28 שנ' במודל העברי) יפוענח עד הסוף
        stream.accept_waveform(int(sample_rate), np.zeros(int(1.5 * sample_rate), dtype=np.float32))
        stream.input_finished()
        while rec.is_ready(stream):
            rec.decode_stream(stream)
        result = rec.get_result(stream)
    text = result if isinstance(result, str) else getattr(result, "text", "")
    return (text or "").strip()


def audio_data_to_float(audio_data):
    """speech_recognition.AudioData -> (float32 16kHz מונו, 16000). מקבל גם bytes של PCM16 ב-16kHz,
    או מערך numpy של float32 שכבר ב-16kHz."""
    if np is None:
        raise RuntimeError("numpy missing")
    if hasattr(audio_data, "get_raw_data"):
        raw = audio_data.get_raw_data(convert_rate=SAMPLE_RATE, convert_width=2)
    elif isinstance(audio_data, (bytes, bytearray)):
        raw = bytes(audio_data)
    else:
        return np.asarray(audio_data, dtype=np.float32).reshape(-1), SAMPLE_RATE
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0, SAMPLE_RATE


# ---------------------------------------------------------------------------------------------
# מדיניות: מקומי -> (רק בהסכמה מפורשת) ענן -> כלום
# ---------------------------------------------------------------------------------------------
_WARNED = set()
_WARN_LOCK = threading.Lock()

UNAVAILABLE_MESSAGE = ("⚠️ זיהוי דיבור אינו זמין: מודל זיהוי הדיבור המקומי לא הותקן, ושליחת אודיו "
                       "לענן כבויה (לשמירה על פרטיות מילות הקוד). להתקנה: "
                       ".venv\\Scripts\\python speech_local.py --download he")


def cloud_allowed():
    return bool(getattr(config, "CLOUD_SPEECH_FALLBACK", False))


# ------------------------------------------------------------------------------------------------
# חיסכון בחשמל + דיוק בזמן סכנה: ברגיל רק המנוע המקומי (sherpa-onnx, בלי רשת). רק בזמן סכנה (הערכת
# סיכון, ספירה לאחור, SOS פעיל, מעקב שקט) מצרפים *במקביל* גם את זיהוי הדיבור של Google לדיוק נוסף -
# המקומי לא מחכה לו, ואם אין רשת / נכשל - ממשיכים עם המקומי בלבד. ניתן לכיבוי בהגדרות.
# הערה: recognize_google של SpeechRecognition משתמש במפתח הציבורי ל"בדיקות" של Google Web Speech -
# לא שירות מורשה לייצור. לייצור: Google Cloud Speech-to-Text (בתשלום, מפתח בשרת) או ספק אחר.
CLOUD_ASSIST_TIMEOUT_S = 4.0


def cloud_assist_enabled(username=None):
    """הגדרה גלובלית (config.CLOUD_ASSIST_IN_DANGER) + העדפה אישית cloud_speech_in_danger (ברירת מחדל: פעיל)."""
    if not getattr(config, "CLOUD_ASSIST_IN_DANGER", True):
        return False
    if username:
        try:
            import incident_store
            return incident_store.get_setting(username, "cloud_speech_in_danger", "1") == "1"
        except Exception:  # noqa: BLE001
            return True
    return True


def _google_async(recognizer, audio_data, language):
    """מריץ את Google ב-thread; מחזיר (thread, box). box["text"] מתמלא אם הצליח."""
    box = {}

    def run():
        try:
            box["text"] = recognizer.recognize_google(audio_data, language=language) or ""
        except Exception:  # noqa: BLE001 - שקט / אין רשת / מגבלת קצב
            box["text"] = ""
    t = threading.Thread(target=run, name="LifeGuard-CloudASR", daemon=True)
    t.start()
    return t, box


def languages_for_phrases(phrases, primary=None):
    """אילו מודלים נדרשים לפי הכתב שבביטויים (עברית/לטינית/קירילית/ערבית) + שפת ברירת המחדל."""
    langs = []
    if primary:
        langs.append(language_code(primary))
    for phrase in phrases:
        for ch in phrase or "":
            if "֐" <= ch <= "׿":
                lang = "he"
            elif "؀" <= ch <= "ۿ":
                lang = "ar"
            elif "Ѐ" <= ch <= "ӿ":
                lang = "ru"
            elif ch.isascii() and ch.isalpha():
                lang = "en"
            else:
                continue
            if lang not in langs:
                langs.append(lang)
    return langs


def can_recognize(languages):
    return any(is_available(lang) for lang in languages) or cloud_allowed()


def _warn_once(key, message, log_callback):
    if not log_callback:
        return          # לא "שורפים" את ההודעה כשאין לאן לכתוב אותה
    with _WARN_LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    try:
        log_callback(message)
    except Exception:  # noqa: BLE001
        pass


def warn_unavailable(log_callback, languages=None):
    """הודעה (פעם אחת לתהליך) כשאין זיהוי בכלל, או כשחסר מודל לחלק מהשפות. בלי לחשוף ביטויים."""
    languages = list(languages or [])
    if not any(is_available(lang) for lang in languages) and not cloud_allowed():
        _warn_once("none", UNAVAILABLE_MESSAGE, log_callback)
        return
    if cloud_allowed():
        return
    for lang in languages:
        if is_available(lang):
            continue
        name = LANGUAGE_NAMES_HE.get(lang, lang)
        if lang in CATALOG:
            hint = f"להתקנה: .venv\\Scripts\\python speech_local.py --download {lang}"
        else:
            hint = "אין כרגע מודל מקומי לשפה זו"
        _warn_once(f"lang:{lang}", f"⚠️ אין מודל זיהוי דיבור מקומי ל{name} - ביטויים ב{name} לא יזוהו ({hint})",
                   log_callback)


def _reset_warnings():
    """לבדיקות בלבד."""
    with _WARN_LOCK:
        _WARNED.clear()


def recognize_speech(audio_data, languages, recognizer=None, cloud_language=None, log_callback=None,
                     cloud_assist=False):
    """
    מחזיר רשימת תמלולים (אחד לכל מודל מקומי שרץ). [] = לא זוהה / לא זמין.
    Google נקרא אם CLOUD_SPEECH_FALLBACK=True וגם אין מודל מקומי - או (cloud_assist=True) בזמן סכנה,
    במקביל למקומי, כתמלול נוסף: לא מחליף את המקומי, וכישלון/איטיות של הענן מעכבים לכל היותר
    CLOUD_ASSIST_TIMEOUT_S.
    """
    cloud = None
    if cloud_assist and recognizer is not None and hasattr(recognizer, "recognize_google"):
        cloud = _google_async(recognizer, audio_data, cloud_language or config.VOICE_TRIGGER_LANGUAGE)
    out = _recognize_local_or_fallback(audio_data, languages, recognizer, cloud_language, log_callback,
                                       skip_cloud_fallback=cloud is not None)
    if cloud is not None:
        cloud[0].join(CLOUD_ASSIST_TIMEOUT_S)
        text = cloud[1].get("text")
        if text and text not in out:
            out = list(out) + [text]
    return out


def _recognize_local_or_fallback(audio_data, languages, recognizer, cloud_language, log_callback,
                                 skip_cloud_fallback=False):
    local = [lang for lang in languages if is_available(lang)]
    if local:
        if len(local) < len(languages):
            warn_unavailable(log_callback, languages)     # חסר מודל לחלק מהשפות - פעם אחת לכל שפה
        try:
            samples, rate = audio_data_to_float(audio_data)
        except Exception:  # noqa: BLE001 - אודיו פגום/ריק
            return []
        results = {}

        def _run(lang):
            try:
                results[lang] = transcribe(samples, rate, lang)
            except Exception:  # noqa: BLE001
                results[lang] = None

        # sherpa-onnx משחרר את ה-GIL בפענוח - כמה שפות במקביל (נמדד: 0.43 שנ' במקום 0.7-0.9 ל-he+en)
        workers = [threading.Thread(target=_run, args=(lang,), daemon=True) for lang in local[1:]]
        for worker in workers:
            worker.start()
        _run(local[0])
        for worker in workers:
            worker.join()
        return [results[lang] for lang in local if results.get(lang)]
    if skip_cloud_fallback:
        return []                                 # הענן כבר רץ במקביל (cloud_assist) - לא פעמיים
    if cloud_allowed() and recognizer is not None and hasattr(recognizer, "recognize_google"):
        try:
            text = recognizer.recognize_google(audio_data, language=cloud_language or config.VOICE_TRIGGER_LANGUAGE)
        except Exception:  # noqa: BLE001 - שקט/אין רשת
            return []
        return [text] if text else []
    warn_unavailable(log_callback, languages)
    return []


# ---------------------------------------------------------------------------------------------
# נרמול טקסט והתאמת ביטויים (סובלנית לשגיאות ASR קטנות, שמרנית)
# ---------------------------------------------------------------------------------------------
_HEB_FINALS = str.maketrans("ךםןףץ", "כמנפצ")
_BIDI_AND_QUOTES = re.compile("[‎‏‪-‮⁦-⁩﻿'‘’׳״\"`´]")
_HEB_PREFIXES = "והשבלמכ"
_FINAL_VOWEL_LETTERS = "היוא"
# מרחק עריכה מנורמל מקסימלי למילה: עד 5 אותיות - התאמה מדויקת בלבד (או כתיב מלא/חסר); 6-11: שגיאה אחת;
# 12+: שתיים. נמדד על FLEURS-he: סף 0.2 (שגיאה אחת כבר ב-5 אותיות) התאים בטעות "למטרה" ו"המשטר" ל"משטרה".
MAX_WORD_EDIT_RATIO = 1 / 6


def _is_hebrew(token):
    return any("א" <= ch <= "ת" for ch in token)


def normalize_tokens(text):
    """
    -> רשימת מילים מנורמלות: אותיות קטנות, בלי ניקוד/טעמים/סימני כיווניות, גרש/גרשיים/אפוסטרוף
    נמחקים (don't -> dont), מקף עברי ופיסוק -> רווח, אותיות סופיות -> רגילות, וו/יוד כפולות -> יחידות.
    """
    value = unicodedata.normalize("NFKD", (text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = _BIDI_AND_QUOTES.sub("", value.replace("־", " "))
    value = unicodedata.normalize("NFC", value).translate(_HEB_FINALS)
    tokens = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    return [re.sub(r"([וי])\1+", r"\1", tok) for tok in tokens]


def _skeleton(token):
    """עברית: ללא אמות קריאה ו/י באמצע/סוף מילה (כתיב מלא/חסר: תיגע~תגע, מישהו~משהו)."""
    if not _is_hebrew(token) or len(token) < 2:
        return token
    return token[0] + re.sub("[וי]", "", token[1:])


def _levenshtein(a, b, limit):
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _word_close(heard, expected):
    if heard == expected:
        return True
    skel = _skeleton(expected)
    if _is_hebrew(expected) and len(skel) >= 2 and _skeleton(heard) == skel:
        return True
    # עברית: החלפת אם קריאה בסוף מילה (נמדד: "תעזבי" תומלל "תעזבה"). רק החלפה ולא השמטה - "משטר"≠"משטרה"
    if (_is_hebrew(expected) and len(expected) >= 4 and len(heard) == len(expected)
            and heard[:-1] == expected[:-1] and heard[-1] in _FINAL_VOWEL_LETTERS
            and expected[-1] in _FINAL_VOWEL_LETTERS):
        return True
    allowed = int(len(expected) * MAX_WORD_EDIT_RATIO + 1e-6)
    return allowed > 0 and _levenshtein(heard, expected, allowed) <= allowed


def _word_match(heard, expected, allow_prefix=False):
    if _word_close(heard, expected):
        return True
    if allow_prefix and _is_hebrew(heard):
        # תחיליות עבריות צמודות (ו/ה/ש/ב/ל/מ/כ, עד שתיים): "למשטרה", "והמשטרה"
        for cut in (1, 2):
            rest = heard[cut:]
            if len(rest) >= 2 and all(c in _HEB_PREFIXES for c in heard[:cut]) and _word_close(rest, expected):
                return True
    return False


def phrase_in_transcript(transcript, phrase, allow_prefixes=False):
    """
    האם הביטוי נאמר כרצף מילים שלם בתמלול (לא תת-מחרוזת בתוך מילה).
    סובלנות: נרמול עברי, שגיאת ASR קטנה בכל מילה (MAX_WORD_EDIT_RATIO), כתיב מלא/חסר, ואיחוד/פיצול
    מילים ע"י ה-ASR (רק כשהאותיות זהות). allow_prefixes - לתחיליות עבריות במילה הראשונה (למילות מצוקה,
    לא לביטויים סודיים).
    """
    heard, want = normalize_tokens(transcript), normalize_tokens(phrase)
    if not want or not heard:
        return False
    n = len(want)
    for i in range(len(heard) - n + 1):
        if all(_word_match(h, w, allow_prefix=allow_prefixes and k == 0)
               for k, (h, w) in enumerate(zip(heard[i:i + n], want))):
            return True
    joined_want = _skeleton("".join(want))
    for width in (n - 1, n + 1):
        if width < 1 or width > len(heard):
            continue
        for i in range(len(heard) - width + 1):
            if _skeleton("".join(heard[i:i + width])) == joined_want:
                return True
    return False


def find_keywords(transcripts, keywords):
    """מילות המצוקה (בניסוח המקורי מ-config) שנאמרו באחד התמלולים."""
    if isinstance(transcripts, str):
        transcripts = [transcripts]
    return [kw for kw in keywords
            if any(phrase_in_transcript(text, kw, allow_prefixes=True) for text in transcripts if text)]


# ---------------------------------------------------------------------------------------------
# הורדת מודלים עם אימות checksum
# ---------------------------------------------------------------------------------------------
def _hash_file(path):
    sha256, sha1 = hashlib.sha256(), hashlib.sha1()
    size = os.path.getsize(path)
    sha1.update(f"blob {size}\0".encode())   # git blob id = sha1("blob <size>\0" + content)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha256.update(chunk)
            sha1.update(chunk)
    return size, sha256.hexdigest(), sha1.hexdigest()


def verify_file(path, size, sha256=None, blob_sha1=None):
    try:
        got_size, got_sha256, got_sha1 = _hash_file(path)
    except OSError:
        return False
    if got_size != size:
        return False
    if sha256 and got_sha256 != sha256:
        return False
    if blob_sha1 and got_sha1 != blob_sha1:
        return False
    return True


def download_model(lang, dest_root=None, log=print):
    """מוריד את קבצי המודל (revision מקובע) ומאמת כל קובץ מול ה-checksum שהמקור מפרסם."""
    lang = language_code(lang)
    if lang not in CATALOG:
        raise ValueError(f"no on-device model for '{lang}'. {ARABIC_NOTE if lang == 'ar' else ''}".strip())
    spec = CATALOG[lang]
    target = os.path.join(dest_root or default_download_root(), spec["name"])
    os.makedirs(target, exist_ok=True)
    for local, (remote, size, sha256, blob_sha1) in spec["files"].items():
        path = os.path.join(target, local)
        if os.path.exists(path) and verify_file(path, size, sha256, blob_sha1):
            log(f"[{lang}] {local}: already present, checksum OK")
            continue
        url = f"https://huggingface.co/{spec['repo']}/resolve/{spec['revision']}/{remote}"
        log(f"[{lang}] downloading {url} ({size / 1e6:.1f} MB)")
        tmp = path + ".part"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as resp, open(tmp, "wb") as out:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                out.write(chunk)
        if not verify_file(tmp, size, sha256, blob_sha1):
            os.remove(tmp)
            raise RuntimeError(f"checksum mismatch for {remote} - file discarded")
        os.replace(tmp, path)
        log(f"[{lang}] {local}: checksum OK ({'sha256' if sha256 else 'git-blob-sha1'})")
    with open(os.path.join(target, "SOURCE.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"source: https://huggingface.co/{spec['repo']} @ {spec['revision']}\n"
                 f"license: {spec['license']}\nattribution: {spec['attribution']}\n")
    return target


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "--download":
        print("usage: python speech_local.py --download he [en ru]")
        print("available on-device models:", ", ".join(f"{k} ({v['license']})" for k, v in CATALOG.items()))
        print(ARABIC_NOTE)
        print("installed:", ", ".join(available_languages()) or "none",
              "| engine (sherpa-onnx):", "yes" if HAS_ENGINE else "no")
        return 0
    for lang in argv[1:] or ["he"]:
        print("->", download_model(lang))
    return 0


if __name__ == "__main__":
    sys.exit(main())

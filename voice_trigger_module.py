# -*- coding: utf-8 -*-
"""
voice_trigger_module.py
האזנה רציפה ברקע לשלושת ביטויי הקוד שהוגדרו בהרשמה:
1. מילת קוד לאזעקה רועשת - כשנאמרת, מפעילה SOS מלא מיד, ללא ספירה לאחור.
2. מילת קוד למצוקה שקטה - כשנאמרת, מפעילה/ממירה ישירות למעקב שקט (בלי מסך/סירנה) - אותו ביטוי
   בדיוק עובד גם מוקלד (ראה auth_module.verify_silent_alarm_phrase_typed).
3. מילת קוד לביטול - כשנאמרת, מבטלת *מיד* כל אזעקה פעילה, רועשת או שקטה.

שלושת הביטויים מוגדרים כחובה בהרשמה ונשארים נפרדים זה מזה; המשתמש בוחר את הניסוח בעצמו.
בדיקת המיקרופון בזמן ההרשמה היא אופציונלית בלבד ואינה תנאי ליצירת החשבון.

** מגבלות חשובות - קרא לפני הסתמכות בשטח **
- התמלול נעשה *במכשיר* (speech_local.py: sherpa-onnx + מודל Zipformer streaming; עברית: ivrit.ai,
  CC-BY-4.0). האודיו ומילות הקוד הסודיות לא נשלחים לשום שרת. Google משמש רק אם המשתמש הפעיל במפורש
  config.CLOUD_SPEECH_FALLBACK וגם אין מודל מקומי. אין מודל ואין הסכמה -> ההאזנה לא מופעלת והודעה
  ברורה נרשמת פעם אחת.
- זו לא זיהוי wake-word ייעודי בעוצמת חשמל נמוכה - זו האזנה כמעט-רציפה (VAD לפי עוצמה של
  SpeechRecognition) ותמלול מקומי של כל קטע דיבור (RTF≈0.12 על CPU, כלומר ~0.5 שנ' לקטע של 4 שנ').
- עלולה לפספס את הביטוי (רעש רקע, דיבור מהיר/לא ברור) או לפרש שגוי ביטוי דומה - יש לבחור
  ניסוח ייחודי שלא נאמר בשיחה רגילה כדי למנוע הפעלות שווא. ההתאמה סובלת שגיאת ASR קטנה בלבד
  (speech_local.phrase_in_transcript). WER של המודל העברי על FLEURS-he נמדד ~22%.
- ביטויים באנגלית/רוסית דורשים את המודל של אותה שפה (python speech_local.py --download en ru).
  ערבית: אין כרגע מודל מקומי קטן עם רישיון ברור.
- משתמשת במיקרופון באופן רציף - ייתכן ניגוד גישה (device busy) עם הקלטת האודיו של האירוע
  עצמו על מערכות מסוימות; זהו טרייד-אוף מכוון לטובת זמינות הביטויים הקוליים בכל שלב.
- תמלולים וביטויים לעולם לא נרשמים ליומן (גלוי או שקט).
"""

import threading

import config
import speech_local

try:
    import speech_recognition as sr
    HAS_SR = True
except Exception:
    HAS_SR = False


def test_phrase_recognition(expected_phrase, duration=None, language=None, log_callback=None):
    """
    בדיקת מיקרופון חד-פעמית (אופציונלית, לא חובה) - מקליטה duration שניות, מתמללת, ובודקת
    אם expected_phrase מופיע בתמלול. משמשת כפתור "בדוק בקול" בהרשמה, כדי לתת למשתמש משוב
    אמיתי על הביטוי שבחר - אך אינה תנאי סף לרישום.
    מחזיר (matched: bool, recognized_text: str|None, error: str|None).
    """
    duration = duration or config.VOICE_TEST_RECORD_SECONDS
    language = language or config.VOICE_TRIGGER_LANGUAGE

    if not HAS_SR:
        return False, None, "speech_recognition/pyaudio לא מותקנים (pip install SpeechRecognition pyaudio)"

    languages = speech_local.languages_for_phrases([expected_phrase], primary=language)
    if not speech_local.can_recognize(languages):
        return False, None, speech_local.UNAVAILABLE_MESSAGE.replace("⚠️ ", "")
    speech_local.preload(languages)

    try:
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.4)
            if log_callback:
                log_callback(f"🎙️ מקליט {duration:.0f} שניות - אמור/אמרי עכשיו את הביטוי...")
            audio = recognizer.record(source, duration=duration)
    except Exception as e:
        return False, None, str(e)

    texts = speech_local.recognize_speech(audio, languages, recognizer=recognizer, cloud_language=language)
    if not texts:
        return False, "", None
    matched = any(_phrase_was_spoken(text, expected_phrase) for text in texts)
    # התמלול מוחזר רק לתווית בדיקה במסך של המשתמש עצמו - לא נרשם ליומן
    return matched, " / ".join(texts), None


def _normalize_spoken_text(value):
    """נרמול תמלול/ביטוי כדי שפיסוק, ניקוד ואותיות סופיות לא ישברו זיהוי (speech_local.normalize_tokens)."""
    return speech_local.normalize_tokens(value)


def _phrase_was_spoken(transcript, phrase):
    """רצף מילים שלם (לא תת-מחרוזת בתוך מילה), סובלני לשגיאת ASR קטנה, ללא תחיליות (ביטוי סודי)."""
    return speech_local.phrase_in_transcript(transcript, phrase, allow_prefixes=False)


class VoiceTriggerListener:
    """
    עוטף sr.Recognizer().listen_in_background (לכידת מיקרופון + VAD בלבד; התמלול מקומי ב-speech_local)
    - מאזין ברקע לשלושה ביטויים אפשריים:
    secret_word (אזעקה רועשת מיידית), silent_phrase (מעקב שקט), cancel_phrase (ביטול מיידי אוניברסלי).

    קריטי: כשמזוהה silent_phrase או cancel_phrase, *אסור* לרשום שום דבר ליומן הגלוי
    (log_callback) - הקריאה ל-on_silent_triggered/on_cancel_triggered לבדה אחראית על
    התיעוד (engine._log_silent או log רגיל, לפי המצב בזמן הקריאה בפועל). אחרת כל הרעיון
    של "שקט" נשבר ברגע שהביטוי נאמר.
    """

    def __init__(self, secret_word="", silent_phrase="", cancel_phrase="", duress_phrase="",
                 on_triggered=None, on_silent_triggered=None, on_cancel_triggered=None,
                 on_duress_triggered=None, log_callback=None, language="he-IL", username=None,
                 fake_call_phrase="", on_fake_call=None, danger_fn=None, on_audio=None):
        self.secret_word = (secret_word or "").strip().lower()
        self.silent_phrase = (silent_phrase or "").strip().lower()
        self.cancel_phrase = (cancel_phrase or "").strip().lower()
        self.duress_phrase = (duress_phrase or "").strip().lower()
        # שיחה מזויפת (fake_call.py): לא חירום - נבדק אחרון, רק אם אף ביטוי SOS לא נאמר
        self.fake_call_phrase = (fake_call_phrase or "").strip().lower()
        self.on_fake_call = on_fake_call or (lambda: None)
        # danger_fn() -> True בזמן סכנה: מצרפים את Google לדיוק (speech_local cloud_assist); ברגיל - מקומי בלבד
        self.danger_fn = danger_fn or (lambda: False)
        # on_audio(audio): זיהוי סכנה ברקע (hazard_monitor) על אותו מקטע - לא חוסם, לא פותח מיקרופון נוסף
        self.on_audio = on_audio
        self.on_triggered = on_triggered or (lambda: None)
        self.on_silent_triggered = on_silent_triggered or (lambda: None)
        self.on_cancel_triggered = on_cancel_triggered or (lambda: None)
        self.on_duress_triggered = on_duress_triggered or (lambda: None)
        self.log_callback = log_callback or (lambda m: None)
        self.language = language
        # אילו מודלים מקומיים נדרשים (לפי הכתב של הביטויים) - בזיכרון בלבד, לא נרשם
        self._languages = speech_local.languages_for_phrases(
            [self.secret_word, self.silent_phrase, self.cancel_phrase, self.duress_phrase, self.fake_call_phrase],
            primary=language)
        # מזהה אישי (personal_kws) - מודלים שאומנו מהקלטות המשתמש/ת עצמו/ה; מתאם *נוסף* לתמלול
        self.username = username
        self._personal = None          # PersonalDetector או None
        self._recognizer = None
        self._mic = None
        self._stop_listening_fn = None
        self._running = False
        self._lock = threading.Lock()

    def set_log_callback(self, callback):
        """מחליף יעד תיעוד בלי לפתוח מחדש את המיקרופון (מעבר למעקב שקט וחזרה)."""
        self.log_callback = callback or (lambda m: None)

    def start(self):
        """
        מחזיר מיד. פתיחת המיקרופון קורית ב-thread רקע, *אחרי* שהזרם הקודם שוחרר במלואו.
        באג קריסה שתוקן: עצירה+הפעלה מיידית פתחה זרם PortAudio חדש בזמן שהקודם עוד נסגר
        ב-thread אחר -> access violation (קריסת התוכנה כולה) ברגע הפעלת מצב כפייה/שקט.
        """
        if not HAS_SR:
            self.log_callback("⚠️ speech_recognition/pyaudio לא מותקנים - זיהוי מילת קוד קולית מושבת "
                               "(pip install SpeechRecognition pyaudio)")
            return False
        if not self.secret_word or not self.silent_phrase or not self.cancel_phrase:
            self.log_callback("⚠️ חסרה אחת מפקודות הקול בפרופיל - יש להגדיר את שלושת הביטויים בהגדרות")
            return False
        # אין מודל מקומי, אין מודל אישי ואין הסכמה מפורשת לענן -> לא פותחים מיקרופון בכלל
        # (ולא שולחים אודיו לשום מקום)
        self._personal = self._load_personal_detector()
        speech_local.warn_unavailable(self.log_callback, self._languages)
        if not speech_local.can_recognize(self._languages) and self._personal is None:
            return False
        with self._lock:
            if self._running:
                return True
            self._running = True
        threading.Thread(target=self._open_microphone, name="LifeGuard-VoiceOpen", daemon=True).start()
        return True

    def _open_microphone(self):
        # טוענים את המודלים לפני פתיחת המיקרופון, כדי שהביטוי הראשון לא ימתין לטעינה (~1-4 שנ')
        speech_local.preload(self._languages, wait=True)
        # גם המודלים של מילות המצוקה (עברית+אנגלית) נטענים עכשיו ברקע - אחרת הערכת הסיכון הראשונה
        # (3 שנ' הקלטה) הייתה ממתינה לטעינה (נמדד: עד ~7 שנ' תחת עומס) ומפספסת את חלון ההערכה
        speech_local.preload(speech_local.languages_for_phrases(config.DANGEROUS_KEYWORDS, primary=self.language))
        if not _MIC_RELEASED.wait(MIC_RELEASE_TIMEOUT_SECONDS):
            self.log_callback("⚠️ המיקרופון עדיין תפוס ע\"י ההאזנה הקודמת - מנסה בכל זאת")
        with self._lock:
            if not self._running:
                return  # stop() נקרא בינתיים
            try:
                self._recognizer = sr.Recognizer()
                self._mic = sr.Microphone()
                with self._mic as source:
                    self._recognizer.adjust_for_ambient_noise(source, duration=0.5)
                self._stop_listening_fn = self._recognizer.listen_in_background(
                    self._mic, self._on_audio_chunk, phrase_time_limit=4
                )
                _MIC_RELEASED.clear()  # המיקרופון בשימוש עד ש-stop ישחרר אותו במלואו
                parts = []
                if self.secret_word:
                    parts.append("מילת קוד לאזעקה רועשת")
                if self.silent_phrase:
                    parts.append("מילת קוד למצוקה שקטה")
                if self.cancel_phrase:
                    parts.append("מילת קוד לביטול")
                if self.duress_phrase:
                    parts.append("מילת קוד כפייה")
                self.log_callback(f'🎙️ האזנה קולית פעילה ({" + ".join(parts)})')
            except Exception as e:
                self._running = False
                _MIC_RELEASED.set()
                self.log_callback(f"❌ שגיאת הפעלת האזנה קולית: {e}")

    def is_running(self):
        return bool(self._running and self._stop_listening_fn is not None)

    def _load_personal_detector(self):
        """PersonalDetector אם המשתמש/ת אימן/ה מודל אישי בפרופיל; אחרת None. לעולם לא חוסם את ההאזנה."""
        if not self.username:
            return None
        try:
            import personal_kws
            store = personal_kws.store_for(self.username)
            if "models" not in store.names():
                return None
            return personal_kws.PersonalDetector(store)
        except Exception:  # noqa: BLE001 - חסר onnxruntime/מודל embedding/מפתח: ממשיכים עם התמלול בלבד
            self.log_callback("⚠️ המודל הקולי האישי לא נטען - הזיהוי ממשיך בתמלול המקומי בלבד")
            return None

    def _personal_hits(self, audio):
        """
        מזהי ביטויים (loud/silent/cancel) שהמודל האישי זיהה בקטע. המודל רואה חלון של 2 שנ' שבו הביטוי
        בסופו, ובקטע של SpeechRecognition יש שקט אחרי הדיבור - לכן מחליקים חלון אחורה בצעדי 0.25 שנ'.
        """
        if self._personal is None:
            return set()
        try:
            import personal_kws
            samples, rate = speech_local.audio_data_to_float(audio)
            win, step = int(personal_kws.CLIP_SECONDS * rate), int(0.25 * rate)
            hits = set()
            for end in range(len(samples), min(win, len(samples)) - 1, -step):
                hits.update(pid for pid, _ in self._personal.detect(samples[max(0, end - win):end], rate))
            return hits & PERSONAL_PHRASE_IDS
        except Exception:  # noqa: BLE001
            return set()

    def _on_audio_chunk(self, recognizer, audio):
        if not self._running:
            return
        if self.on_audio is not None:
            try:
                self.on_audio(audio)
            except Exception:  # noqa: BLE001 - זיהוי סכנה ברקע לעולם לא עוצר את זיהוי מילות הקוד
                pass
        # תמלול במכשיר (speech_local); ענן רק בהסכמה מפורשת ורק כשאין מודל מקומי. שקט/לא ברור -> []
        try:
            assist = bool(self.danger_fn())
        except Exception:  # noqa: BLE001
            assist = False
        texts = speech_local.recognize_speech(audio, self._languages, recognizer=recognizer,
                                              cloud_language=self.language, log_callback=self.log_callback,
                                              cloud_assist=assist)
        personal = self._personal_hits(audio)
        if texts or personal:
            self._handle_transcripts(texts, personal)

    def _handle_transcripts(self, texts, personal_hits=()):
        """
        texts - תמלול אחד לכל מודל שפה שרץ על אותו קטע; personal_hits - מזהי ביטויים מהמודל האישי
        (loud/silent/cancel; לביטוי כפייה אין מודל אישי). התמלול והביטויים לעולם לא נרשמים ליומן.
        """
        personal_hits = set(personal_hits or ())

        def spoken(phrase, personal_id=None):
            if not phrase:
                return False
            return personal_id in personal_hits or any(_phrase_was_spoken(t, phrase) for t in texts)

        # סדר הבדיקה חשוב: קוד הביטול הקולי תמיד ראשון, כדי שלא יופעל טריגר נוסף
        # באותו תמלול. ההתאמה היא לפי רצף מילים שלם ולא תת-מחרוזת בתוך מילה.
        # קוד כפייה נבדק לפני קוד הביטול: אסור שתמלול שמכיל את שניהם יבטל בטעות.
        if spoken(self.duress_phrase):
            try:
                self.on_duress_triggered()
            except Exception:
                pass
            return

        if spoken(self.cancel_phrase, "cancel"):
            try:
                self.on_cancel_triggered()
            except Exception:
                pass
            return

        if spoken(self.silent_phrase, "silent"):
            try:
                self.on_silent_triggered()
            except Exception:
                pass
            return

        if spoken(self.secret_word, "loud"):
            # בלי התמלול/הביטוי עצמו: היומן נשמר לדיסק ומוצג על המסך
            self.log_callback("🚨 מילת הקוד הקולית זוהתה!")
            try:
                self.on_triggered()
            except Exception as e:
                self.log_callback(f"❌ שגיאה בהפעלת הטריגר מהזיהוי הקולי: {e}")
            return

        # שיחה מזויפת - אחרונה: ביטוי חירום באותו תמלול תמיד גובר. בלי רישום ליומן.
        if spoken(self.fake_call_phrase):
            try:
                self.on_fake_call()
            except Exception:
                pass

    def stop(self, suppress_log=False):
        """
        לא חוסם את הקורא (UI/callback). השחרור המלא (join ל-thread ההאזנה וסגירת זרם
        PortAudio) קורה ב-thread רקע; עד שהוא מסתיים _MIC_RELEASED לא מסומן, ולכן start()
        הבא ימתין ולא יפתח זרם מקביל.
        """
        with self._lock:
            if not self._running:
                return
            self._running = False
            stopper, self._stop_listening_fn = self._stop_listening_fn, None
            if not suppress_log:
                self.log_callback("🎙️ האזנה קולית הופסקה")
        if stopper is None:
            return

        def _release():
            try:
                stopper(wait_for_stop=True)
            except Exception:
                pass
            finally:
                _MIC_RELEASED.set()
        threading.Thread(target=_release, name="LifeGuard-VoiceRelease", daemon=True).start()


# מזהי הביטויים של personal_kws שהמאזין מגיב להם ("help" משמש את הערכת הסיכון בלבד)
PERSONAL_PHRASE_IDS = frozenset({"loud", "silent", "cancel"})

# האם המיקרופון שוחרר במלואו ע"י המאזין האחרון. משותף לכל המאזינים - יש מיקרופון אחד.
_MIC_RELEASED = threading.Event()
_MIC_RELEASED.set()
MIC_RELEASE_TIMEOUT_SECONDS = 8


def wait_for_mic_release(timeout=3.0):
    """לשימוש ע"י רכיבים אחרים שפותחים את המיקרופון (הערכת סיכון, הקלטה)."""
    return _MIC_RELEASED.wait(timeout)

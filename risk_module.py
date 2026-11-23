# -*- coding: utf-8 -*-
"""Multi-signal risk assessment (heuristic safety aid, not a certified detector).

A loud sound alone is NEVER enough for automatic SOS. The engine distinguishes
LOW/MEDIUM/HIGH/UNKNOWN. MEDIUM and UNKNOWN retain a user confirmation window;
only a combination of independent evidence sources may skip the countdown.
Manual PANIC and explicit loud-alarm code always bypass this assessor.
"""

import threading
import config
import sensor_module

try:
    import sounddevice as sd
    import numpy as np
    HAS_SD = True
except Exception:
    sd = None
    np = None
    HAS_SD = False

try:
    import speech_recognition as sr
    HAS_SR = True
except Exception:
    sr = None
    HAS_SR = False


MIC_SR = 16000   # קצב הדגימה של YAMNet - חוסך resample; מספיק גם ל-RMS


def record_audio(duration, log_callback=None):
    """הקלטה אחת קצרה (לא נשמרת) -> (audio float32 מונו, sr) או (None, None)."""
    if not HAS_SD:
        if log_callback:
            log_callback("⚠️ sounddevice לא מותקן — אין מדידת רעש אמיתית")
        return None, None
    try:
        frames = max(1, int(duration * MIC_SR))
        recording = sd.rec(frames, samplerate=MIC_SR, channels=1, dtype="float32")
        sd.wait()
        if recording is None or len(recording) == 0:
            return None, None
        return recording.reshape(-1), MIC_SR
    except Exception as exc:
        if log_callback:
            log_callback(f"⚠️ מדידת רעש נכשלה: {exc}")
        return None, None


def measure_noise_level(duration, log_callback=None):
    """Returns RMS float, or None when the microphone cannot be measured."""
    audio, _ = record_audio(duration, log_callback)
    return None if audio is None else float(np.sqrt(np.mean(np.square(audio))))


def classify_distress_audio(audio, sr, log_callback=None):
    """מודל AI במכשיר (YAMNet + ראש מסווג). None אם לא זמין או שנכשל - לעולם לא ממציא."""
    if audio is None:
        return None
    try:
        import acoustic_ai_module
        return acoustic_ai_module.analyze(audio, sr)
    except Exception as exc:  # noqa: BLE001
        if log_callback:
            log_callback(f"⚠️ מודל זיהוי המצוקה הקולי נכשל: {exc}")
        return None


def analyze_audio_events(audio, sr, log_callback=None):
    """audio_intel (ירי/פיצוץ/שבירה/סירנה/אזעקה/צרחה + איכות קלט). None אם לא זמין - לעולם לא ממציא."""
    if audio is None:
        return None
    try:
        import audio_intel
        return audio_intel.analyze(audio, sr, log_callback)
    except Exception as exc:  # noqa: BLE001
        if log_callback:
            log_callback(f"⚠️ מנוע אירועי הסכנה הקוליים נכשל: {exc}")
        return None


def detect_dangerous_keywords(duration, log_callback=None, cloud_assist=False):
    """On-device speech-to-text keyword detection (speech_local). [] means no keyword or unavailable.

    האודיו מתומלל במכשיר; Google רק אם config.CLOUD_SPEECH_FALLBACK=True ואין מודל מקומי.
    התמלול עצמו לא נרשם ליומן (עלול להכיל מילות קוד סודיות שנאמרו בזמן ההערכה) - רק המילים שזוהו,
    והן מופיעות בנימוקי ההערכה.
    """
    import speech_local
    languages = speech_local.languages_for_phrases(config.DANGEROUS_KEYWORDS, primary=config.VOICE_TRIGGER_LANGUAGE)
    if not speech_local.can_recognize(languages):
        speech_local.warn_unavailable(log_callback, languages)
        return []
    if not HAS_SR:
        if log_callback:
            log_callback("⚠️ speech_recognition לא מותקן — זיהוי מילות מצוקה אינו זמין")
        return []
    speech_local.preload(languages)   # טעינת המודל במקביל להקלטה (פעם ראשונה בלבד)
    try:
        recognizer = sr.Recognizer()
        with sr.Microphone() as source:
            audio = recognizer.record(source, duration=duration)
    except Exception:
        return []
    texts = speech_local.recognize_speech(audio, languages, recognizer=recognizer,
                                          cloud_language=config.VOICE_TRIGGER_LANGUAGE, log_callback=log_callback,
                                          cloud_assist=cloud_assist)   # הערכת סיכון = רגע של סכנה
    return speech_local.find_keywords(texts, config.DANGEROUS_KEYWORDS)


def _sensor_evidence(signals):
    by_name = {}
    for item in signals:
        name = item.get("signal")
        by_name[name] = max(by_name.get(name, 0.0), float(item.get("confidence", 0.0)))
    return by_name


_PERSONAL = {}


def personal_keywords(audio, sr, username, log_callback=None):
    """זיהוי "הצילו" בקול של המשתמש/ת (personal_kws - אומן בפרופיל). [] אם אין מודל אישי."""
    if audio is None or not username:
        return []
    try:
        det = _PERSONAL.get(username)
        if det is None:
            import personal_kws
            store = personal_kws.store_for(username)
            if "models" not in store.names():
                return []
            det = _PERSONAL[username] = personal_kws.PersonalDetector(store)
        return [pid for pid, _ in det.detect(audio, sr)]
    except Exception as exc:  # noqa: BLE001
        if log_callback:
            log_callback(f"⚠️ הזיהוי הקולי האישי נכשל: {exc}")
        return []


def keyword_points(found):
    """נקודות למילות מצוקה: תחינה מפורשת 2, מילה כללית 1, תקרה 4. מילה שמוכלת בביטוי שזוהה ("help" בתוך
    "help me") לא נספרת פעמיים. מחזיר (נקודות, המילים הכלליות שנספרו)."""
    found = set(found)
    found = {k for k in found if not any(k != o and k.lower() in o.lower() for o in found)}
    generic = {k for k in found if k in config.DANGEROUS_KEYWORDS_GENERIC}
    pts = config.RISK_SCORE_PER_GENERIC_KEYWORD * len(generic) + config.RISK_SCORE_PER_KEYWORD * len(found - generic)
    return min(pts, config.RISK_SCORE_KEYWORDS_CAP), generic


def assess_risk(is_rapid_retrigger=False, log_callback=None, sensor_signals=None, username=None, location=None,
                now=None, trigger=None):
    """Collects evidence in parallel and returns transparent, auditable assessment."""
    results = {}

    def _noise_task():
        audio, sr_ = record_audio(config.RISK_ASSESS_NOISE_SECONDS, log_callback)
        results["noise"] = None if audio is None else float(np.sqrt(np.mean(np.square(audio))))
        # מנוע אירועי הסכנה (audio_intel) על האודיו המקורי; מאותו מעבר YAMNet גם ראש המצוקה
        report = analyze_audio_events(audio, sr_, log_callback)
        results["audio_intel"] = report
        if report is not None and report.get("quality", {}).get("status") == "unavailable" and audio is not None:
            results["noise"] = None          # שקט דיגיטלי = מיקרופון לא עובד, לא "שקט ובטוח"
        shared = (report or {}).get("distress")      # ראש המצוקה כבר רץ מאותו מעבר YAMNet
        results["acoustic_ai"] = shared if shared is not None else classify_distress_audio(audio, sr_, log_callback)
        results["personal"] = personal_keywords(audio, sr_, username, log_callback)

    def _keywords_task():
        import speech_local
        results["keywords"] = detect_dangerous_keywords(config.RISK_ASSESS_SPEECH_SECONDS, log_callback,
                                                        cloud_assist=speech_local.cloud_assist_enabled(username))

    tasks = [threading.Thread(target=_noise_task, daemon=True),
             threading.Thread(target=_keywords_task, daemon=True)]
    for task in tasks:
        task.start()
    for task in tasks:
        task.join(timeout=config.RISK_ASSESS_MAX_WAIT_SECONDS)

    noise_level = results.get("noise")
    audio_report = results.get("audio_intel")
    keywords_found = list(results.get("keywords", []))
    if "help" in (results.get("personal") or []):
        # מודל אישי (בקול של המשתמש/ת) זיהה "הצילו" - ראיית דיבור, מקור נפרד מעוצמת הרעש
        keywords_found.append("הצילו (זוהה בקול שלך)")
    signals = sensor_signals if sensor_signals is not None else sensor_module.get_recent_sensor_signals(
        config.SENSOR_EVIDENCE_WINDOW_SECONDS
    )
    sensor_by_name = _sensor_evidence(signals)

    score = 0
    reasons = []
    independent_sources = set()

    if noise_level is not None:
        if noise_level >= config.LOUD_NOISE_RMS_THRESHOLD:
            score += config.RISK_SCORE_LOUD_NOISE
            independent_sources.add("acoustic_anomaly")
            reasons.append(f"נמדדה עוצמת רעש חריגה (RMS={noise_level:.3f}); לא מספיקה לבדה להפעלת SOS")
    else:
        reasons.append("מדידת מיקרופון אינה זמינה")

    import audio_intel
    reasons.extend(audio_intel.reasons(audio_report))
    ai = results.get("acoustic_ai")
    hazard = audio_intel.hazard_points(audio_report)
    if hazard and not (ai and ai.get("detected")):
        # אותו מיקרופון כמו מודל המצוקה -> אותו מקור ראיה, ולוקחים את המקסימום (לא סוכמים): צליל לבדו
        # מגיע לכל היותר ל-MEDIUM (חלון אימות), לעולם לא ל-SOS אוטומטי
        score += hazard
        independent_sources.add("acoustic_anomaly")
    if ai and ai.get("detected"):
        score += config.RISK_SCORE_ACOUSTIC_AI
        # אותו מיקרופון כמו מדידת הרעש -> אותו מקור ראיה. כך צליל לבדו (גם אם המודל בטוח) לעולם לא
        # מספיק ל-SOS אוטומטי; צריך מקור בלתי תלוי נוסף (מילות מצוקה / חיישן / טריגר חוזר).
        independent_sources.add("acoustic_anomaly")
        # research/multilingual_audio_eval.md המלצה 10: לא "גלאי מצוקה" - צעקות שמחה ומשחק נשמעות דומה (AUC 0.54)
        reasons.append(f"מודל AI זיהה צליל שדומה לצרחה/צעקה/בכי (ציון {ai['probability']:.0%}); "
                       "צעקות שמחה ומשחק נשמעות דומה - זה לא אישור למצוקה")

    if keywords_found:
        kw_points, generic = keyword_points(keywords_found)
        score += kw_points
        independent_sources.add("speech_keyword")
        reasons.append(f"מילות מצוקה זוהו: {', '.join(sorted(set(keywords_found)))}"
                       + (f" ({', '.join(sorted(generic))} - מילה כללית, משקל נמוך)" if generic else ""))

    for name, confidence in sensor_by_name.items():
        weight = config.SENSOR_SIGNAL_WEIGHTS.get(name, 0)
        if confidence >= config.SENSOR_MIN_CONFIDENCE and weight:
            score += weight
            independent_sources.add("motion_sensor" if name != "manual_wearable_sos" else "wearable")
            reasons.append(f"חיישן תנועה: {name} (confidence={confidence:.2f})")

    if is_rapid_retrigger:
        score += config.RISK_SCORE_RAPID_RETRIGGER
        independent_sources.add("retrigger")
        reasons.append("הופעל טריגר נוסף בתוך חלון קצר")

    # הקשר: לילה (לפי גובה השמש במיקום) ורחוב חשוך (לפי עמודי התאורה). מעלה ניקוד ועדיפות -
    # אבל *לא* נספר כמקור ראיה בלתי תלוי, ולכן לבדו לעולם לא מאפשר SOS אוטומטי (context_risk.py)
    context = None
    try:
        import context_risk
        context = context_risk.assess(location, now=now)
        score += context["score"]
        reasons.extend(context["reasons"])
    except Exception as exc:  # noqa: BLE001 - הקשר הוא תוספת; כשל לא עוצר הערכת סיכון
        if log_callback:
            log_callback(f"⚠️ הערכת זמן/תאורה נכשלה: {exc}")

    # A fall/impact followed by immobility is a stronger, independent combination.
    motion_pair = (
        sensor_by_name.get("immobility", 0) >= config.SENSOR_MIN_CONFIDENCE
        and max(sensor_by_name.get("fall", 0), sensor_by_name.get("impact", 0)) >= config.SENSOR_MIN_CONFIDENCE
    )
    multi_source_high = (
        len(independent_sources) >= config.RISK_HIGH_MIN_INDEPENDENT_SOURCES
        and score >= config.RISK_THRESHOLD_HIGH
    )
    explicit_wearable_panic = sensor_by_name.get("manual_wearable_sos", 0) >= 0.9

    if explicit_wearable_panic or motion_pair or multi_source_high:
        level = "HIGH"
        reasons.append("שילוב של מספר אינדיקציות בלתי תלויות — הופעלה הסלמה מיידית")
    elif score >= config.RISK_THRESHOLD_MEDIUM:
        level = "MEDIUM"
    elif noise_level is None and not keywords_found and not sensor_by_name:
        level = "UNKNOWN"
        reasons.append("אין מספיק נתוני חיישנים להערכת סיכון")
    else:
        level = "LOW"

    result = {
        "level": level,
        "score": score,
        "reasons": reasons,
        "noise_level": noise_level,
        "keywords_found": sorted(set(keywords_found)),
        "acoustic_ai": ai,
        "audio_intel": audio_intel.compact(audio_report),
        "context": context,
        "sensor_signals": signals,
        "independent_sources": sorted(independent_sources),
        "assessment_kind": "heuristic_multi_signal_v1",
        # עקיבות: אילו גרסאות מודל תרמו להערכה (בסיס להשוואת champion/challenger ולחקירת טעויות)
        "models": {"audio_intel": (audio_report or {}).get("model_version"),
                   "distress_head": (ai or {}).get("variant") if isinstance(ai, dict) else None},
        "automatic_sos_eligible": level == "HIGH",
        "timestamp": __import__("time").time(),
    }
    # ביטחון בזיהוי - ציר נפרד מהחומרה (risk_feedback.py): לא משנה את הרמה, רק מוצג ומשפיע על העדיפות
    try:
        import risk_feedback
        result["confidence"] = risk_feedback.confidence(result, trigger=trigger, username=username)
    except Exception as exc:  # noqa: BLE001
        result["confidence"] = None
        if log_callback:
            log_callback(f"⚠️ חישוב ביטחון נכשל: {exc}")
    return result

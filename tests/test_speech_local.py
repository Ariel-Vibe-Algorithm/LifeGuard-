# -*- coding: utf-8 -*-
"""
זיהוי דיבור במכשיר (speech_local): נרמול עברי, התאמת ביטויים סובלנית-אך-שמרנית, מדיניות "אף פעם לא לענן
בלי הסכמה", ובדיקה עם המודל העברי האמיתי על הקלטה אמיתית (FLEURS he_il, CC-BY-4.0).

המודל וההקלטה לא במאגר הקוד. להפעלת הבדיקות האמיתיות:
    .venv\\Scripts\\python speech_local.py --download he
    (הקלטה: research_tools/local_asr_eval.py --export-test-clip, אחרי הורדת FLEURS he_il dev)
"""
import os
import time
import wave

import numpy as np
import pytest

import config
import risk_module
import speech_local
import voice_trigger_module

sr = pytest.importorskip("speech_recognition")

CLIP = os.path.join(config.BASE_DIR, "research", "samples", "asr_he", "fleurs_he_dev_police.wav")
CLIP_REF = "לפי המשטרה, כנראה לא יוגש כתב אישום נגד נהג הרכב שפגע בצלם."
HAS_HE = speech_local.is_available("he")
needs_model = pytest.mark.skipif(
    not HAS_HE, reason="Hebrew on-device model not downloaded (python speech_local.py --download he)")
needs_clip = pytest.mark.skipif(
    not os.path.exists(CLIP), reason="FLEURS he test clip missing (research_tools/local_asr_eval.py --export-test-clip)")


def _clip_audio_data():
    with wave.open(CLIP) as w:
        return sr.AudioData(w.readframes(w.getnframes()), w.getframerate(), w.getsampwidth())


def _silence_audio_data(seconds=1.0):
    return sr.AudioData(b"\x00\x00" * int(16000 * seconds), 16000, 2)


@pytest.fixture(autouse=True)
def fresh_warnings():
    speech_local._reset_warnings()
    yield
    speech_local._reset_warnings()


@pytest.fixture
def cloud_forbidden(monkeypatch):
    """כל ניסיון לשלוח אודיו ל-Google מפיל את הבדיקה."""
    calls = []

    def _boom(self, *a, **k):
        calls.append(1)
        raise AssertionError("cloud recognizer must not be called with default config")
    monkeypatch.setattr(sr.Recognizer, "recognize_google", _boom)
    return calls


@pytest.fixture
def no_local_models(monkeypatch):
    monkeypatch.setattr(speech_local, "model_dir", lambda lang: None)


# ------------------------------------------------------------------ נרמול
def test_normalize_strips_niqqud_finals_punctuation_and_bidi():
    assert speech_local.normalize_tokens("הַצִּילוּ! מִשְׁטָרָה...") == ["הצילו", "משטרה"]
    assert speech_local.normalize_tokens("‏שלום‎, עולם") == ["שלומ", "עולמ"]   # סופיות -> רגילות
    assert speech_local.normalize_tokens("בית־ספר") == ["בית", "ספר"]                       # מקף עברי
    assert speech_local.normalize_tokens('צה"ל ג\'ירפה') == ["צהל", "גירפה"]                # גרשיים/גרש
    assert speech_local.normalize_tokens("Don't TOUCH me!") == ["dont", "touch", "me"]
    assert speech_local.normalize_tokens("") == []


def test_normalize_collapses_double_vav_yod():
    assert speech_local.normalize_tokens("סיירת וורד") == ["סירת", "ורד"]


# ------------------------------------------------------------------ התאמה
def test_phrase_match_is_whole_words_not_substring():
    assert speech_local.phrase_in_transcript("אני אומרת כחול לבן עכשיו", "כחול לבן")
    assert not speech_local.phrase_in_transcript("כחולים לבנים", "כחול לבן")
    assert not speech_local.phrase_in_transcript("cancelled now", "cancel now")


def test_phrase_match_tolerates_small_asr_error_in_long_words_only():
    assert speech_local.phrase_in_transcript("תתקשרו למספר הטלפון", "הטלפוןן")     # 7 אותיות -> שגיאה אחת
    assert not speech_local.phrase_in_transcript("למטרה", "משטרה")                   # 5 אותיות -> מדויק בלבד
    assert not speech_local.phrase_in_transcript("המשטר", "משטרה")
    assert not speech_local.phrase_in_transcript("bed siren", "red siren")           # מילה קצרה


def test_phrase_match_ktiv_male_haser_and_word_merge_split():
    assert speech_local.phrase_in_transcript("אל תגע בי", "אל תיגע בי")             # כתיב חסר/מלא
    assert speech_local.phrase_in_transcript("גינה ירוקה", "גינה ירוקה")
    assert speech_local.phrase_in_transcript("red siren", "redsiren")                 # ASR פיצל מילה
    assert speech_local.phrase_in_transcript("אלתיגע בי", "אל תיגע בי")             # ASR איחד מילים


def test_final_vowel_letter_substitution_but_not_deletion():
    assert speech_local.phrase_in_transcript("תעזבה אותי", "תעזבי אותי")            # נמדד ב-ASR אמיתי
    assert not speech_local.phrase_in_transcript("נגד המשטר", "משטרה", allow_prefixes=True)
    assert not speech_local.phrase_in_transcript("לא", "לו")                         # מילה קצרה מדי


def test_prefixes_only_for_keywords_not_for_secret_phrases():
    assert speech_local.phrase_in_transcript("לפי המשטרה", "משטרה", allow_prefixes=True)
    assert speech_local.phrase_in_transcript("מהמשטרה נמסר", "משטרה", allow_prefixes=True)
    assert not speech_local.phrase_in_transcript("לפי המשטרה", "משטרה", allow_prefixes=False)
    assert not voice_trigger_module._phrase_was_spoken("והכחול לבן", "כחול לבן")


def test_find_keywords_returns_config_spelling():
    found = speech_local.find_keywords(["תעזבי אותי! ומשטרה", "HELP ME PLEASE"], config.DANGEROUS_KEYWORDS)
    # תעזבי/תעזוב שונות רק באמות קריאה (ו/י) - שתיהן מילות מצוקה ושתיהן מדווחות
    assert set(found) == {"תעזבי אותי", "תעזוב אותי", "משטרה", "help", "help me"}
    assert speech_local.find_keywords(["מזג האוויר נאה היום"], config.DANGEROUS_KEYWORDS) == []


def test_languages_follow_phrase_scripts():
    assert speech_local.languages_for_phrases(["כחול לבן"], primary="he-IL") == ["he"]
    assert speech_local.languages_for_phrases(["red siren", "синий"], primary="he-IL") == ["he", "en", "ru"]
    assert "en" in speech_local.languages_for_phrases(config.DANGEROUS_KEYWORDS, primary="he-IL")


# ------------------------------------------------------------------ מדיניות פרטיות: אף פעם לא לענן בלי הסכמה
def test_default_config_never_calls_cloud_even_without_local_model(cloud_forbidden, no_local_models):
    assert config.CLOUD_SPEECH_FALLBACK is False
    logs = []
    for _ in range(3):
        texts = speech_local.recognize_speech(_silence_audio_data(), ["he"], recognizer=sr.Recognizer(),
                                              log_callback=logs.append)
        assert texts == []
    assert cloud_forbidden == []
    assert len(logs) == 1 and "זיהוי דיבור אינו זמין" in logs[0]          # הודעה ברורה, פעם אחת


def test_voice_listener_does_not_start_or_use_cloud_without_model(cloud_forbidden, no_local_models, monkeypatch):
    monkeypatch.setattr(voice_trigger_module, "HAS_SR", True)     # המיקרופון עצמו לא ייפתח - start נעצר קודם
    logs = []
    listener = voice_trigger_module.VoiceTriggerListener(
        secret_word="אדום חזק", silent_phrase="כחול שקט", cancel_phrase="ירוק בטל", log_callback=logs.append)
    assert listener.start() is False
    assert not listener._running
    listener._running = True
    listener._on_audio_chunk(sr.Recognizer(), _silence_audio_data())
    assert cloud_forbidden == []
    assert logs and all("ירוק" not in m and "כחול" not in m and "אדום" not in m for m in logs)


def test_risk_keywords_without_model_do_not_open_mic_or_cloud(cloud_forbidden, no_local_models, monkeypatch):
    monkeypatch.setattr(risk_module.sr, "Microphone",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("mic must not open")))
    logs = []
    assert risk_module.detect_dangerous_keywords(3.0, logs.append) == []
    assert risk_module.detect_dangerous_keywords(3.0, logs.append) == []
    assert cloud_forbidden == [] and len(logs) == 1


def test_cloud_used_only_with_explicit_opt_in_and_no_local_model(monkeypatch, no_local_models):
    class FakeCloud:
        calls = []

        def recognize_google(self, audio, language="he-IL"):
            self.calls.append(language)
            return "שלום"
    monkeypatch.setattr(config, "CLOUD_SPEECH_FALLBACK", True)
    assert speech_local.recognize_speech(_silence_audio_data(), ["he"], recognizer=FakeCloud(),
                                         cloud_language="he-IL") == ["שלום"]
    assert FakeCloud.calls == ["he-IL"]


@needs_model
def test_opt_in_still_prefers_local_model(monkeypatch, cloud_forbidden):
    monkeypatch.setattr(config, "CLOUD_SPEECH_FALLBACK", True)
    speech_local.recognize_speech(_silence_audio_data(0.5), ["he"], recognizer=sr.Recognizer())
    assert cloud_forbidden == []


# ------------------------------------------------------------------ מודל אמיתי על הקלטה עברית אמיתית
@needs_model
@needs_clip
def test_real_hebrew_clip_recognized_on_device(cloud_forbidden):
    speech_local.preload(["he"], wait=True)
    audio, rate = speech_local.audio_data_to_float(_clip_audio_data())
    started = time.perf_counter()
    text = speech_local.transcribe(audio, rate, "he")
    elapsed = time.perf_counter() - started
    ref, hyp = "".join(speech_local.normalize_tokens(CLIP_REF)), "".join(speech_local.normalize_tokens(text))
    cer = speech_local._levenshtein(hyp, ref, len(ref)) / len(ref)
    assert cer < 0.25, text
    assert speech_local.find_keywords([text], config.DANGEROUS_KEYWORDS) == ["משטרה"]
    assert elapsed < len(audio) / rate                     # מהיר מזמן אמת
    assert cloud_forbidden == []


@needs_model
@needs_clip
def test_real_clip_through_voice_listener_triggers_cancel_without_logging_phrase(cloud_forbidden):
    calls, logs = [], []
    listener = voice_trigger_module.VoiceTriggerListener(
        secret_word="אדום חזק", silent_phrase="כחול שקט", cancel_phrase="כתב אישום",
        on_cancel_triggered=lambda: calls.append("cancel"), on_triggered=lambda: calls.append("loud"),
        log_callback=logs.append, language="he-IL")
    listener._running = True
    listener._on_audio_chunk(sr.Recognizer(), _clip_audio_data())
    assert calls == ["cancel"]
    assert logs == []                                        # ביטול קולי: אף מילה ליומן
    assert cloud_forbidden == []


@needs_model
@needs_clip
def test_real_clip_through_risk_keyword_detection(cloud_forbidden, monkeypatch):
    class FakeMic:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(risk_module.sr, "Microphone", FakeMic)
    monkeypatch.setattr(risk_module.sr.Recognizer, "record", lambda self, source, duration=None: _clip_audio_data())
    logs = []
    assert risk_module.detect_dangerous_keywords(3.0, logs.append) == ["משטרה"]
    assert not any("כתב אישום" in m for m in logs)          # התמלול עצמו לא נרשם
    assert cloud_forbidden == []

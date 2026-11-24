# -*- coding: utf-8 -*-
"""
מילות מצוקה אחרי המלצות 3-5 ו-10 של research/multilingual_audio_eval.md:
- "הצילו" / "תצילו אותי" נמצאות ומזוהות בתמלול; "stop" כמילה בודדת כבר לא מפעיל ("stop it" כן).
- תחינה מפורשת = 2 נקודות, מילה כללית = 1, תקרה 4; "help" בתוך "help me" לא נספר פעמיים.
- מילה כללית לבדה (או שתיים) לא מגיעה ל-MEDIUM; תחינה + מילה כללית כן.
- הנימוק של מודל הצליל לא טוען "מצוקה" (צעקות שמחה נשמעות דומה).
"""
from unittest import mock

import numpy as np

import config
import risk_module
import sensor_module
import speech_local


def test_new_pleas_found_and_bare_stop_removed():
    assert {"הצילו", "תצילו אותי"} <= set(config.DANGEROUS_KEYWORDS)
    assert "stop" not in config.DANGEROUS_KEYWORDS and "stop it" in config.DANGEROUS_KEYWORDS
    assert config.DANGEROUS_KEYWORDS_GENERIC <= set(config.DANGEROUS_KEYWORDS)
    assert speech_local.find_keywords(["הצילו! מישהו"], config.DANGEROUS_KEYWORDS) == ["הצילו"]
    assert speech_local.find_keywords(["stop the car here"], config.DANGEROUS_KEYWORDS) == []
    assert "stop it" in speech_local.find_keywords(["stop it now"], config.DANGEROUS_KEYWORDS)


def test_keyword_points_weights_cap_and_no_substring_double_count():
    kp = risk_module.keyword_points
    assert kp(["help"]) == (1, {"help"})
    assert kp(["help", "help me"]) == (2, set())                    # "help" הוא חלק מ-"help me"
    assert kp(["משטרה", "police"])[0] == 2
    assert kp(["הצילו"])[0] == 2 and kp(["הצילו", "משטרה"])[0] == 3
    assert kp(["הצילו", "תעזוב אותי", "let me go"])[0] == config.RISK_SCORE_KEYWORDS_CAP
    assert kp(["הצילו", "הצילו (זוהה בקול שלך)"])[0] == 2           # אותה אמירה - מודל אישי + תמלול


def _assess(keywords, rms=0.01, ai=None):
    sensor_module.clear_sensor_signals()
    with mock.patch.object(risk_module, "record_audio", lambda *a, **k: (np.full(32000, rms, np.float32), 16000)), \
         mock.patch.object(risk_module, "analyze_audio_events", return_value=None), \
         mock.patch.object(risk_module, "classify_distress_audio", return_value=ai), \
         mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=list(keywords)):
        return risk_module.assess_risk(sensor_signals=[])


def test_generic_words_alone_stay_low_plea_reaches_medium():
    assert _assess(["משטרה", "help"])["level"] == "LOW"            # היה MEDIUM (4 נקודות) - מילים מדיבור רגיל
    r = _assess(["הצילו", "משטרה"])
    assert r["level"] == "MEDIUM" and "מילה כללית" in " ".join(r["reasons"])
    assert _assess(["תעזוב אותי"])["score"] == 2


def test_acoustic_reason_does_not_claim_distress():
    ai = {"score": 9.0, "detected": True, "probability": 0.97, "at_seconds": 0.0}
    text = " ".join(_assess([], ai=ai)["reasons"])
    assert "שדומה לצרחה" in text and "לא אישור למצוקה" in text and "זיהה צליל מצוקה" not in text

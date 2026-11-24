import unittest
from unittest import mock

import numpy as np

import config
import risk_module
import sensor_module


def _audio(rms):
    """הקלטה סינתטית במקום מיקרופון: אות קבוע בעוצמה נתונה (None = אין מיקרופון)."""
    if rms is None:
        return lambda *a, **k: (None, None)
    return lambda *a, **k: (np.full(16000, rms, dtype=np.float32), 16000)


AI_YES = {"score": 9.0, "detected": True, "probability": 0.9999, "at_seconds": 0.5}
AI_NO = {"score": -3.0, "detected": False, "probability": 0.05, "at_seconds": 0.0}


class RiskTests(unittest.TestCase):
    def setUp(self):
        sensor_module.clear_sensor_signals()

    def _assess(self, rms, keywords=(), ai=None, signals=()):
        with mock.patch.object(risk_module, "record_audio", _audio(rms)), \
             mock.patch.object(risk_module, "classify_distress_audio", return_value=ai), \
             mock.patch.object(risk_module, "analyze_audio_events", return_value=None), \
             mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=list(keywords)):
            return risk_module.assess_risk(sensor_signals=list(signals))

    def test_loud_noise_alone_never_auto_sos(self):
        result = self._assess(0.9)
        self.assertNotEqual(result["level"], "HIGH")
        self.assertFalse(result["automatic_sos_eligible"])

    def test_missing_sensors_is_unknown_not_low_or_high(self):
        result = self._assess(None)
        self.assertEqual(result["level"], "UNKNOWN")
        self.assertFalse(result["automatic_sos_eligible"])

    def test_fall_plus_immobility_is_high(self):
        signals = [{"signal": "fall", "confidence": 0.91}, {"signal": "immobility", "confidence": 0.92}]
        result = self._assess(0.01, signals=signals)
        self.assertEqual(result["level"], "HIGH")
        self.assertTrue(result["automatic_sos_eligible"])

    def test_single_keyword_is_not_automatic_high(self):
        self.assertNotEqual(self._assess(0.01, keywords=["help"])["level"], "HIGH")

    # ------------------------------------------------------------ מודל ה-AI הקולי
    def test_ai_scream_alone_raises_to_medium_but_never_auto_sos(self):
        r = self._assess(0.9, ai=AI_YES)                      # רעש + המודל בטוח שזו צרחה
        self.assertEqual(r["level"], "MEDIUM")               # חלון אימות, לא SOS אוטומטי
        self.assertFalse(r["automatic_sos_eligible"])
        self.assertEqual(r["independent_sources"], ["acoustic_anomaly"])   # אותו מיקרופון = מקור אחד
        self.assertTrue(any("מודל AI" in x for x in r["reasons"]))

    def test_ai_plus_independent_keyword_can_reach_high(self):
        r = self._assess(0.9, keywords=["help", "הצילו"], ai=AI_YES)
        self.assertEqual(r["level"], "HIGH")
        self.assertEqual(r["independent_sources"], ["acoustic_anomaly", "speech_keyword"])

    def test_ai_negative_or_unavailable_changes_nothing(self):
        base = self._assess(0.9)
        for ai in (AI_NO, None):
            r = self._assess(0.9, ai=ai)
            self.assertEqual((r["level"], r["score"]), (base["level"], base["score"]))
            self.assertFalse(any("מודל AI" in x for x in r["reasons"]))

    def test_one_recording_feeds_both_noise_and_model(self):
        calls = []
        rec = lambda *a, **k: calls.append(1) or (np.full(16000, 0.5, np.float32), 16000)  # noqa: E731
        seen = []
        with mock.patch.object(risk_module, "record_audio", rec), \
             mock.patch.object(risk_module, "classify_distress_audio",
                               lambda audio, sr, log=None: seen.append((len(audio), sr)) or AI_NO), \
             mock.patch.object(risk_module, "analyze_audio_events",
                               lambda audio, sr, log=None: seen.append((len(audio), sr)) or None), \
             mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=[]):
            r = risk_module.assess_risk(sensor_signals=[])
        self.assertEqual(calls, [1])                          # המיקרופון נפתח פעם אחת
        self.assertEqual(seen, [(16000, 16000)] * 2)         # אותה הקלטה גם למנוע האירועים וגם לראש המצוקה
        self.assertAlmostEqual(r["noise_level"], 0.5, places=5)

    def test_score_weights_are_consistent(self):
        # רעש + AI לבדם חייבים להישאר מתחת לסף HIGH, כדי שמקור אחד לא יספיק
        self.assertLess(config.RISK_SCORE_LOUD_NOISE + config.RISK_SCORE_ACOUSTIC_AI, config.RISK_THRESHOLD_HIGH)


if __name__ == "__main__":
    unittest.main()


class PersonalVoiceTests(unittest.TestCase):
    def test_personal_help_detection_counts_as_speech_evidence(self):
        with mock.patch.object(risk_module, "record_audio", _audio(0.5)), \
             mock.patch.object(risk_module, "classify_distress_audio", return_value=AI_YES), \
             mock.patch.object(risk_module, "personal_keywords", return_value=["help"]), \
             mock.patch.object(risk_module, "detect_dangerous_keywords", return_value=["help"]):
            r = risk_module.assess_risk(sensor_signals=[], username="u")
        self.assertIn("הצילו (זוהה בקול שלך)", r["keywords_found"])
        self.assertIn("speech_keyword", r["independent_sources"])

    def test_no_personal_model_means_no_effect(self):
        self.assertEqual(risk_module.personal_keywords(np.zeros(16000, np.float32), 16000, None), [])

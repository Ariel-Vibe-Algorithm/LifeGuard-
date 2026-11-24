import unittest
from unittest import mock

import speech_local
import voice_trigger_module

TRANSCRIPT = "please use the duress phrase and cancel now"


class FakeRecognizer:
    """אם הקוד ינסה לתמלל בענן - הבדיקה תיכשל (ברירת המחדל היא זיהוי במכשיר בלבד)."""
    def recognize_google(self, audio, language="he-IL"):
        raise AssertionError("cloud recognizer must not be called")


def _listener(calls, logs=None):
    return voice_trigger_module.VoiceTriggerListener(
        secret_word="loud alarm",
        silent_phrase="quiet alert",
        cancel_phrase="cancel now",
        duress_phrase="duress phrase",
        on_triggered=lambda: calls.append("loud"),
        on_silent_triggered=lambda: calls.append("silent"),
        on_cancel_triggered=lambda: calls.append("cancel"),
        on_duress_triggered=lambda: calls.append("duress"),
        log_callback=(logs.append if logs is not None else None),
    )


class VoiceTriggerTests(unittest.TestCase):
    def test_duress_phrase_has_priority_over_cancel_phrase(self):
        calls = []
        listener = _listener(calls)
        listener._running = True
        # התמלול המקומי (speech_local) מוחלף כאן בתמלול קבוע; המודל האמיתי נבדק ב-test_speech_local.py
        with mock.patch.object(speech_local, "recognize_speech", return_value=[TRANSCRIPT]):
            listener._on_audio_chunk(FakeRecognizer(), b"audio")
        self.assertEqual(calls, ["duress"])

    def test_priority_holds_across_transcripts_of_different_language_models(self):
        calls = []
        listener = _listener(calls)
        listener._handle_transcripts(["cancel now", "duress phrase"])   # he-model + en-model לאותו קטע
        self.assertEqual(calls, ["duress"])

    def test_loud_secret_word_log_never_contains_transcript_or_phrase(self):
        calls, logs = [], []
        listener = _listener(calls, logs)
        listener._handle_transcripts(["ok loud alarm right now"])
        self.assertEqual(calls, ["loud"])
        self.assertTrue(logs)
        self.assertFalse(any("loud alarm" in m or "right now" in m for m in logs))

    # ------------------------------------------------ מודל קולי אישי (personal_kws) כמתאם נוסף
    def _with_personal(self, hits, transcripts=()):
        class StubDetector:      # PersonalDetector.detect -> [(phrase_id, score)]
            def detect(self, audio, sr=16000):
                return [(pid, 9.0) for pid in hits]
        calls, logs = [], []
        listener = _listener(calls, logs)
        listener._personal = StubDetector()
        listener._running = True
        silence = b"\x00\x00" * 16000 * 3
        with mock.patch.object(speech_local, "recognize_speech", return_value=list(transcripts)):
            listener._on_audio_chunk(FakeRecognizer(), silence)
        return calls, logs

    def test_personal_model_alone_triggers_cancel_silently(self):
        self.assertEqual(self._with_personal({"cancel"}), (["cancel"], []))

    def test_personal_help_id_is_not_a_listener_command(self):
        self.assertEqual(self._with_personal({"help"}), ([], []))

    def test_duress_from_transcript_beats_personal_cancel(self):
        calls, _ = self._with_personal({"cancel"}, transcripts=["duress phrase"])
        self.assertEqual(calls, ["duress"])

    def test_personal_loud_hit_logs_without_phrase(self):
        calls, logs = self._with_personal({"loud"})
        self.assertEqual(calls, ["loud"])
        self.assertFalse(any("loud alarm" in m for m in logs))

    def test_no_personal_model_for_user_means_none(self):
        import tempfile
        import config
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(config, "DATA_DIR", tmp):
            listener = voice_trigger_module.VoiceTriggerListener(
                secret_word="a b", silent_phrase="c d", cancel_phrase="e f", username="nobody")
            self.assertIsNone(listener._load_personal_detector())

    def test_silent_and_cancel_phrases_log_nothing(self):
        for transcript, expected in (("quiet alert", "silent"), ("cancel now", "cancel")):
            calls, logs = [], []
            _listener(calls, logs)._handle_transcripts([transcript])
            self.assertEqual((calls, logs), ([expected], []))


if __name__ == "__main__":
    unittest.main()

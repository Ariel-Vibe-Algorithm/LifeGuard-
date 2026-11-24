# -*- coding: utf-8 -*-
"""
אימון קולי אישי: הקלטות מוצפנות, העשרה לאלפי וריאציות, מסווג לכל ביטוי, בדיקה על הקלטות שלא נראו.
"קולות" אמיתיים: מנוע הדיבור של Windows (SAPI) מייצר את הביטויים בכמה קולות ומהירויות - כך שהבדיקה
רצה על דיבור אמיתי ולא על אותות מלאכותיים. (העברית ב-SAPI לא מותקנת כאן - הביטויים באנגלית.)
"""
import os
import subprocess
import sys
import wave

import numpy as np
import pytest

pk = pytest.importorskip("personal_kws")
HAVE_MODEL = any(os.path.exists(os.path.join(d, "embedding_model.onnx")) for d in pk.OWW_DIRS)
pytestmark = pytest.mark.skipif(not (HAVE_MODEL and sys.platform == "win32"), reason="דרוש Windows SAPI + מודל embedding")

PHRASES = {"help": "help me", "silent": "blue lantern"}
FREE = ("The weather today is mild and the market on the corner opens at nine. We talked about the bus schedule, "
        "the new library hours, a recipe for lentil soup, and whether the train will be late again tomorrow morning. "
        "My cousin is visiting next week, so we are planning a short walk by the sea and a dinner with friends.")


def _tts(text, path, voice, rate):
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SelectVoice('{voice}');$s.Rate={rate};"
        "$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,"
        "[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono);"
        f"$s.SetOutputToWaveFile('{path}',$f);$s.Speak('{text}');$s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True, timeout=60)
    with wave.open(path, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0


@pytest.fixture(scope="module")
def voices(tmp_path_factory):
    d = tmp_path_factory.mktemp("tts")
    takes = {}
    combos = [(v, r) for v in ("Microsoft Zira Desktop", "Microsoft David Desktop") for r in (-3, -1, 1, 3)]
    for pid, text in PHRASES.items():
        takes[pid] = [_tts(text, str(d / f"{pid}_{i}.wav"), v, r) for i, (v, r) in enumerate(combos)]
    free = _tts(FREE, str(d / "free.wav"), "Microsoft Zira Desktop", 0)
    free2 = _tts(FREE, str(d / "free2.wav"), "Microsoft David Desktop", 2)
    return takes, np.concatenate([free, free2])


def test_secure_store_encrypts_takes(tmp_path):
    st = pk.SecureStore(str(tmp_path / "v"))
    a = np.linspace(-0.5, 0.5, 16000).astype(np.float32)
    pk.save_take(st, "help", 0, a)
    raw = open(os.path.join(str(tmp_path / "v"), "take_help_00.enc"), "rb").read()
    assert a.tobytes()[:400] not in raw
    assert np.array_equal(pk.load_takes(st)["help"][0], a)
    st.delete_all()
    assert not [f for f in os.listdir(str(tmp_path / "v")) if f.endswith(".enc")]


def test_augmentation_produces_varied_realistic_clips():
    rng = np.random.default_rng(0)
    take = (0.3 * np.sin(2 * np.pi * 220 * np.arange(8000) / pk.SR)).astype(np.float32)
    bgs = pk.synthetic_backgrounds(rng, n=8)
    clips = [pk.augment(take, bgs, rng) for _ in range(30)]
    assert all(len(c) == int(pk.CLIP_SECONDS * pk.SR) and np.max(np.abs(c)) <= 1.0 for c in clips)
    assert len({round(float(np.std(c)), 4) for c in clips}) > 20          # באמת שונים זה מזה


def test_train_and_detect_on_real_speech(voices, tmp_path):
    takes, free = voices
    models, report = pk.train(takes, free, variations_per_take=60, log=lambda m: None)
    assert report["variations_total"] == 2 * 6 * 60
    for pid, r in report["phrases"].items():
        assert r["false_positives"] == 0                       # בהגדרה (הסף מעל כל שלילי בבדיקה)
        det, n = map(int, r["heldout_clean_detected"].split("/"))
        assert n == pk.HOLDOUT_TAKES and det >= 1, (pid, r)   # הקלטות שהמודל לא ראה - מזוהות
    st = pk.SecureStore(str(tmp_path / "v"))
    pk.save_models(st, models, report)
    detector = pk.PersonalDetector(st)
    held = takes["help"][-1]
    hits = detector.detect(held)
    assert hits and hits[0][0] == "help"
    assert detector.detect(free[-int(2 * pk.SR):]) == []      # דיבור רגיל - לא מזוהה כביטוי

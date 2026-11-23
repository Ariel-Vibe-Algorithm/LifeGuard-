# -*- coding: utf-8 -*-
"""
speech_enhance.py - הפחתת רעשים לזרם הדיבור בלבד (תמלול מילות מצוקה). מנוע אירועי הסכנה ממשיך לעבוד על
האודיו המקורי: הפחתת רעשים מוחקת בדיוק את מה שהוא צריך לזהות (ירי, שבירה).

שיטה: spectral gating - פרופיל רעש מ-20% המסגרות השקטות בהקלטה, ומסכת Wiener רכה עם רצפה (לא מאפסים תדרים,
כדי לא ליצור "רעש מוזיקלי" שמבלבל את ה-ASR). numpy + scipy בלבד, ~5 ms לשניית אודיו.

מופעל באפליקציה רק אם research/speech_enhance_eval.md הראה שיפור ב-WER ברעש בלי פגיעה בשקט (config.SPEECH_ENHANCE).
"""
import numpy as np

N_FFT = 512
HOP = 128
NOISE_PCT = 20
FLOOR = 0.15          # הנחתה מקסימלית ~16dB
OVERSUB = 1.5


def enhance(x, sr=16000):
    x = np.asarray(x, np.float32).reshape(-1)
    if len(x) < N_FFT * 2:
        return x
    from scipy.signal import istft, stft
    _, _, Z = stft(x, fs=sr, nperseg=N_FFT, noverlap=N_FFT - HOP, boundary="even")
    P = np.abs(Z) ** 2
    frame_e = P.sum(axis=0)
    quiet = frame_e <= np.percentile(frame_e, NOISE_PCT)
    noise = P[:, quiet].mean(axis=1, keepdims=True) if quiet.any() else P.min(axis=1, keepdims=True)
    gain = np.maximum(1.0 - OVERSUB * noise / (P + 1e-12), FLOOR)
    # החלקה בזמן (3 מסגרות) - פחות רעש מוזיקלי
    k = np.ones(3) / 3
    gain = np.apply_along_axis(lambda g: np.convolve(g, k, mode="same"), 1, gain)
    _, y = istft(Z * gain, fs=sr, nperseg=N_FFT, noverlap=N_FFT - HOP, boundary=True)
    y = y[:len(x)].astype(np.float32)
    if len(y) < len(x):
        y = np.pad(y, (0, len(x) - len(y)))
    return y

# -*- coding: utf-8 -*-
"""
hazard_features.py - מאפייני חלון למנוע הסכנה v2. **אותו קוד** באימון (research_tools/hv2_train.py) ובזמן ריצה
(audio_intel.py) - כדי שהמדידה תייצג את מה שרץ באפליקציה.

חלון = 1.5 שניות אודיו ב-16kHz (אורך ההקלטה אחרי טריגר). YAMNet רץ על החלון עצמו.

שלב 1 (מועמד, רגיש): ממוצע + מקסימום של embeddings של YAMNet בחלון (2048).
שלב 2 (אימות הקשרי): הסתברויות YAMNet למחלקות דומות-מבלבלות (ירי / זיקוקים / תופים / מוזיקה / טלוויזיה / צופר /
סירנה / צעקה / צחוק...) + מאפיינים ספקטרליים בשלוש רזולוציות (16 / 64 / 256 ms): עד כמה האירוע אימפולסיבי, דעיכה,
מחזוריות (מוזיקה ותופים חוזרים בקצב - ירי לא), יציבות טון (צופר) ואפנון (אזעקה מצפצפת, סירנה גולשת),
ופיזור אנרגיה בתדרים (מרחק מנמיך תדרים גבוהים).
numpy + scipy בלבד.
"""
import numpy as np

SR = 16000
WINDOW_S = 1.5
WINDOW = int(SR * WINDOW_S)

# מחלקות YAMNet (שמות AudioSet) שמבדילות בין סכנה לבין דומים לא מסוכנים
CURATED = (
    "Gunshot, gunfire", "Machine gun", "Fusillade", "Artillery fire", "Cap gun", "Fireworks", "Firecracker",
    "Burst, pop", "Explosion", "Boom", "Bang", "Eruption", "Drum", "Drum kit", "Snare drum", "Bass drum", "Timpani",
    "Music", "Television", "Radio", "Speech", "Narration, monologue", "Vehicle horn, car horn, honking", "Air horn, truck horn",
    "Siren", "Police car (siren)", "Ambulance (siren)", "Fire engine, fire truck (siren)", "Civil defense siren",
    "Alarm", "Car alarm", "Smoke detector, smoke alarm", "Fire alarm", "Beep, bleep", "Ringtone", "Telephone bell ringing",
    "Screaming", "Shout", "Yell", "Children shouting", "Crying, sobbing", "Wail, moan", "Laughter", "Cheering",
    "Crowd", "Children playing", "Shatter", "Glass", "Slam", "Knock", "Thump, thud", "Hammer", "Jackhammer",
    "Traffic noise, roadway noise", "Wind noise (microphone)", "Clapping",
)
_IDX = {"idx": None}


def curated_idx():
    if _IDX["idx"] is None:
        from research_tools import yamnet_detector as Y
        names = Y.class_names()
        _IDX["idx"] = np.array([names.index(c) for c in CURATED if c in names])
    return _IDX["idx"]


def stage1_vector(emb):
    """שלב 1: ממוצע + מקסימום של embeddings של החלון (מסגרות של 0.96 ש', קפיצה 0.48)."""
    e = np.asarray(emb, np.float32)
    return np.concatenate([e.mean(axis=0), e.max(axis=0)])


def _stft_mag(x, n_fft):
    hop = n_fft // 4
    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    n = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx] * _hann(n_fft)[None, :]
    from scipy.fft import rfft
    return np.abs(rfft(fr, axis=1)).astype(np.float32), hop


_HANN = {}


def _hann(n):
    if n not in _HANN:
        _HANN[n] = np.hanning(n).astype(np.float32)
    return _HANN[n]


def spectral_features(x):
    """מאפיינים ספקטרליים רב-רזולוציה (27). x: חלון ב-16kHz."""
    x = np.asarray(x, np.float32)
    x = x - x.mean() if len(x) else x
    out = []
    eps = 1e-8
    nyq = SR / 2
    flux_1024 = S1024 = None
    for n_fft in (256, 1024, 4096):
        S, hop = _stft_mag(x, n_fft)
        if n_fft == 1024:
            S1024 = S
        P = S ** 2
        freqs = np.linspace(0, nyq, S.shape[1])
        tot = P.sum(axis=1) + eps
        flux = np.maximum(np.diff(np.log1p(S), axis=0), 0).sum(axis=1) if len(S) > 1 else np.zeros(1)
        if n_fft == 1024:
            flux_1024 = flux
        cent = (P * freqs[None, :]).sum(axis=1) / tot / nyq
        flat = np.exp(np.mean(np.log(P + eps), axis=1)) / (np.mean(P, axis=1) + eps)
        loud = tot >= np.percentile(tot, 50)
        out += [float(np.log1p(flux.max())), float(flux.max() / (np.median(flux) + eps) if len(flux) else 0.0),
                float(P[:, freqs >= 4000].sum() / tot.sum()), float(P[:, freqs < 300].sum() / tot.sum()),
                float(cent[loud].mean()), float(cent[loud].std()), float(flat[loud].mean())]
    # זמן: חדות (crest), קורטוזיס של המעטפת, דעיכה אחרי השיא
    env_hop = 160
    env = np.sqrt(np.convolve(x ** 2, np.ones(env_hop) / env_hop, mode="same")[::env_hop] + eps)
    rms = float(np.sqrt(np.mean(x ** 2) + eps))
    crest = float(20 * np.log10((np.abs(x).max() + eps) / rms))
    z = (env - env.mean()) / (env.std() + eps)
    kurt = float(np.mean(z ** 4))
    i = int(np.argmax(env))
    after = env[i:]
    below = np.nonzero(after < env[i] * 0.1)[0]                  # -20dB
    decay = float(below[0] * env_hop / SR) if len(below) else float(len(after) * env_hop / SR)
    # מחזוריות של אונסטים (מוזיקה/תופים) בטווח 0.25-1.0 ש'
    per = 0.0
    if flux_1024 is not None and len(flux_1024) > 8:
        f = flux_1024 - flux_1024.mean()
        ac = np.correlate(f, f, mode="full")[len(f) - 1:]
        ac = ac / (ac[0] + eps)
        lo, hi = int(0.25 * SR / 256), min(len(ac) - 1, int(1.0 * SR / 256))
        per = float(ac[lo:hi + 1].max()) if hi > lo else 0.0
    # אפנון 2-20Hz של המעטפת (צפצוף, סירנה) ויציבות טון (צופר)
    em = env - env.mean()
    spec = np.abs(np.fft.rfft(em)) ** 2
    mf = np.fft.rfftfreq(len(em), d=env_hop / SR)
    mod = float(spec[(mf >= 2) & (mf <= 20)].sum() / (spec[mf > 0.5].sum() + eps))
    S = S1024
    freqs = np.linspace(0, nyq, S.shape[1])
    band = (freqs >= 300) & (freqs <= 3000)
    e_fr = (S ** 2).sum(axis=1)
    loud = e_fr >= np.percentile(e_fr, 60)
    peaks = freqs[band][np.argmax(S[:, band], axis=1)]
    pitch_std = float(np.std(peaks[loud]) / 1000.0) if loud.any() else 0.0
    srt = np.sort(S[:, band] ** 2, axis=1)
    tonal = float(np.mean(srt[loud, -2:].sum(axis=1) / (srt[loud].sum(axis=1) + eps))) if loud.any() else 0.0
    level = float(20 * np.log10(rms))
    out += [crest, np.log1p(kurt), decay, per, mod, pitch_std, tonal]
    return np.array(out + [level / 60.0], np.float32)          # 3*7 + 7 + 1 = 29


def verifier_vector(stage1_logit, scores, x):
    """שלב 2: [ציון שלב 1, YAMNet למחלקות מבלבלות (מקס + ממוצע), מאפיינים ספקטרליים]."""
    sc = np.asarray(scores, np.float32)[:, curated_idx()]
    return np.concatenate([[np.float32(stage1_logit)], sc.max(axis=0), sc.mean(axis=0), spectral_features(x)])


def windows(x, hop_s=0.75, max_windows=None, rng=None):
    """חלונות של 1.5 ש' (האחרון מרופד). max_windows: דגימה אקראית קבועה (rng) אם יש יותר."""
    x = np.asarray(x, np.float32)
    if len(x) <= WINDOW:
        return [(0, np.pad(x, (0, WINDOW - len(x))))]
    hop = int(hop_s * SR)
    starts = list(range(0, len(x) - WINDOW + 1, hop))
    if starts[-1] + WINDOW < len(x):
        starts.append(len(x) - WINDOW)
    if max_windows and len(starts) > max_windows and rng is not None:
        starts = sorted(rng.choice(starts, size=max_windows, replace=False).tolist())
    return [(s, x[s:s + WINDOW]) for s in starts]

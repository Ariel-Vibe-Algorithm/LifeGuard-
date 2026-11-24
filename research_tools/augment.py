# -*- coding: utf-8 -*-
"""
augment.py - "תנאי טלפון ברחוב" לאודיו: לאימון עמיד יותר ולבדיקה רועשת.

FSD50K מוקלט ברובו בתנאים נקיים. טלפון אמיתי שומע צרחה מרחוק, מעל תנועה ורוח, דרך מיקרופון זול
ולפעמים מתוך כיס. כאן מדמים את זה:
- ערבוב עם רקע רחוב (תנועה, רכבים, רוח, גשם, צעדים) ביחס אות/רעש (SNR) אקראי
- ערוץ טלפון: מסנן מעביר-פס (מיקרופון זול) + דחיסה רכה
- "כיס": מסנן מעביר-נמוכים חד (בד מעמעם תדרים גבוהים) + הנחתה
חשוב: אותה העשרה מופעלת גם על שליליים, אחרת המודל לומד "רעש רקע = צרחה".
"""

import numpy as np

SR = 16000
STREET_LABELS = ("Traffic_noise_and_roadway_noise", "Vehicle", "Car_passing_by", "Bus", "Motor_vehicle_(road)",
                 "Motorcycle", "Truck", "Wind", "Rain", "Walk_and_footsteps", "Engine")


def rms(x):
    return float(np.sqrt(np.mean(np.square(x)) + 1e-12))


def mix_snr(signal, background, snr_db, rng):
    """רקע באורך האות (חזרה/חיתוך מאקראי), מוגבר כך ש-SNR = snr_db."""
    if len(background) < len(signal):
        background = np.tile(background, int(np.ceil(len(signal) / max(1, len(background)))) + 1)
    start = int(rng.integers(0, len(background) - len(signal) + 1))
    bg = background[start:start + len(signal)]
    gain = rms(signal) / (rms(bg) * 10 ** (snr_db / 20.0))
    out = signal + gain * bg
    peak = np.max(np.abs(out))
    return (out / peak * 0.98 if peak > 0.98 else out).astype(np.float32)


def phone_channel(x, rng, sr=SR):
    from scipy.signal import butter, sosfilt
    lo, hi = rng.uniform(100, 300), rng.uniform(3800, 7000)
    y = sosfilt(butter(4, [lo, hi], btype="band", fs=sr, output="sos"), x)
    k = rng.uniform(1.5, 4.0)                                   # דחיסה רכה (AGC של טלפון)
    y = np.tanh(k * y) / np.tanh(k)
    return y.astype(np.float32)


def pocket(x, rng, sr=SR):
    from scipy.signal import butter, sosfilt
    fc = rng.uniform(1200, 2500)
    y = sosfilt(butter(6, fc, btype="low", fs=sr, output="sos"), x) * rng.uniform(0.3, 0.7)
    return y.astype(np.float32)


def street_phone(x, backgrounds, rng, snr_range=(0, 15), p_pocket=0.3):
    """שרשרת אקראית אחת: רקע רחוב -> ערוץ טלפון -> (לפעמים) כיס. מחזיר (אודיו, תיאור)."""
    bg = backgrounds[int(rng.integers(0, len(backgrounds)))]
    snr = float(rng.uniform(*snr_range))
    y = mix_snr(np.asarray(x, np.float32), bg, snr, rng)
    y = phone_channel(y, rng)
    in_pocket = bool(rng.random() < p_pocket)
    if in_pocket:
        y = pocket(y, rng)
    return y, {"snr_db": round(snr, 1), "pocket": in_pocket}

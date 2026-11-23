# -*- coding: utf-8 -*-
"""
personal_kws.py - זיהוי "הצילו" ומילות הקוד *בקול של המשתמש/ת*, במכשיר בלבד.

תהליך:
1. הקלטה בפרופיל: כל ביטוי TAKES_PER_PHRASE פעמים (רגיל, בשקט, בצעקה, מהר...) + 20 שניות דיבור
   חופשי (דוגמאות "לא-ביטוי"). ההקלטות מוצפנות (AES-GCM, מפתח מוגן ב-DPAPI) - הן מכילות את מילות
   הקוד הסודיות - ולעולם לא נשלחות לשום מקום.
2. העשרה: כל הקלטה הופכת למאות וריאציות - רעשי רקע (רחוב/המון/לבן/ורוד) ביחסי אות-רעש שונים,
   מהירות דיבור, עוצמה, הדהוד חדר, ערוץ טלפון, "בכיס", הזזה בזמן. סה"כ אלפי דוגמאות לביטוי.
3. תכונות: embedding דיבור כללי (Google speech_embedding בגרסת ONNX של openWakeWord; קוד Apache-2.0,
   קובצי המודל CC BY-NC-SA - שימוש מחקרי לא מסחרי) -> מסווג קטן לכל ביטוי.
4. בדיקה כנה: 2 הקלטות מכל ביטוי *לא* נכנסות לאימון/להעשרה - עליהן מודדים זיהוי, ועל דיבור חופשי
   שלא שימש לאימון - התראות שווא. הסף נקבע כך שאין התראות שווא על דוגמאות הבדיקה השליליות.
"""

import json
import os
import time

import numpy as np

SR = 16000
CLIP_SECONDS = 2.0
TAKES_PER_PHRASE = 8
HOLDOUT_TAKES = 2
FREE_SPEECH_SECONDS = 20
EMB_FRAMES = 16                       # ~1.3 שניות של embeddings (צעד 80ms)
HERE = os.path.dirname(os.path.abspath(__file__))
OWW_DIRS = [os.path.join(HERE, "models", "oww"), os.path.join(HERE, "research", "models", "oww")]
TAKE_STYLES = ["רגיל", "רגיל", "בשקט/לחישה", "בשקט/לחישה", "בצעקה", "בצעקה", "מהר ובלחץ", "רחוק מהמיקרופון"]


# ===================================================================== אחסון מוצפן
class SecureStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _key(self):
        from evidence_vault import _dpapi_protect, _dpapi_unprotect
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        kp = os.path.join(self.root, "key.bin")
        if os.path.exists(kp):
            with open(kp, "rb") as fh:
                return _dpapi_unprotect(fh.read())
        k = AESGCM.generate_key(bit_length=256)
        with open(kp, "wb") as fh:
            fh.write(_dpapi_protect(k))
        return k

    def put(self, name, data: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = os.urandom(12)
        with open(os.path.join(self.root, name + ".enc"), "wb") as fh:
            fh.write(nonce + AESGCM(self._key()).encrypt(nonce, data, name.encode()))

    def get(self, name):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        with open(os.path.join(self.root, name + ".enc"), "rb") as fh:
            blob = fh.read()
        return AESGCM(self._key()).decrypt(blob[:12], blob[12:], name.encode())

    def names(self):
        return sorted(f[:-4] for f in os.listdir(self.root) if f.endswith(".enc"))

    def delete_all(self):
        from evidence_vault import _wipe
        for f in os.listdir(self.root):
            _wipe(os.path.join(self.root, f))


def store_for(username):
    import config
    return SecureStore(os.path.join(config.DATA_DIR, "voice", username))


def save_take(store, phrase_id, idx, audio):
    store.put(f"take_{phrase_id}_{idx:02d}", np.asarray(audio, np.float32).tobytes())


def load_takes(store):
    out = {}
    for n in store.names():
        if n.startswith("take_"):
            pid, idx = n[5:].rsplit("_", 1)
            out.setdefault(pid, []).append((int(idx), np.frombuffer(store.get(n), np.float32)))
    return {k: [a for _, a in sorted(v)] for k, v in out.items()}


# ===================================================================== תכונות דיבור
class SpeechEmbedder:
    def __init__(self, model_dir=None):
        import onnxruntime as ort
        d = model_dir or next((p for p in OWW_DIRS if os.path.exists(os.path.join(p, "embedding_model.onnx"))), None)
        if not d:
            raise FileNotFoundError("מודל embedding הדיבור לא נמצא (models/oww)")
        o = ort.SessionOptions()
        o.log_severity_level = 3
        self.mel = ort.InferenceSession(os.path.join(d, "melspectrogram.onnx"), o, providers=["CPUExecutionProvider"])
        self.emb = ort.InferenceSession(os.path.join(d, "embedding_model.onnx"), o, providers=["CPUExecutionProvider"])

    def embeddings(self, audio):
        """audio float [-1,1] 16kHz -> [N,96] (צעד 80ms)."""
        x = (np.asarray(audio, np.float32) * 32767.0)[None, :]
        spec = self.mel.run(None, {"input": x})[0].squeeze() / 10.0 + 2.0          # [T,32]
        if spec.ndim == 1 or len(spec) < 76:
            spec = np.pad(spec.reshape(-1, 32), ((0, 76 - len(spec.reshape(-1, 32))), (0, 0)), mode="edge")
        wins = np.stack([spec[i:i + 76] for i in range(0, len(spec) - 76 + 1, 8)])[..., None].astype(np.float32)
        return self.emb.run(None, {"input_1": wins})[0].reshape(len(wins), 96)

    def clip_feature(self, audio):
        e = self.embeddings(audio)
        if len(e) < EMB_FRAMES:
            e = np.pad(e, ((EMB_FRAMES - len(e), 0), (0, 0)))
        return e[-EMB_FRAMES:].reshape(-1)


# ===================================================================== העשרה
def _resample_speed(x, rate):
    n = max(1, int(round(len(x) / rate)))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def _reverb(x, rng):
    t60 = rng.uniform(0.15, 0.6)
    n = int(t60 * SR)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0
    y = np.convolve(x, ir * rng.uniform(0.1, 0.4))[:len(x)]
    return (y / (np.max(np.abs(y)) + 1e-6) * np.max(np.abs(x))).astype(np.float32)


def synthetic_backgrounds(rng, n=12, seconds=4.0, speech_like=None):
    """רעשי רקע בלי נתונים חיצוניים: לבן, ורוד, חום ("תנועה"), ו"המון" מדיבור חופשי מעורבב."""
    m = int(seconds * SR)
    out = []
    for i in range(n):
        w = rng.standard_normal(m).astype(np.float32)
        kind = i % 4
        if kind == 1:                                              # ורוד ~ 1/f
            f = np.fft.rfft(w)
            f /= np.sqrt(np.arange(1, len(f) + 1))
            w = np.fft.irfft(f, m).astype(np.float32)
        elif kind == 2:                                            # חום ~ 1/f^2 (רעש תנועה עמום)
            w = np.cumsum(w).astype(np.float32)
            w -= np.convolve(w, np.ones(400) / 400, mode="same")
        elif kind == 3 and speech_like is not None and len(speech_like) > m:
            parts = [speech_like[s:s + m] for s in rng.integers(0, len(speech_like) - m, 4)]
            w = np.sum(parts, axis=0).astype(np.float32)          # "בבל" - כמה דוברים
        out.append((w / (np.max(np.abs(w)) + 1e-6)).astype(np.float32))
    return out


def augment(take, backgrounds, rng):
    """וריאציה אקראית אחת בתוך חלון של CLIP_SECONDS."""
    from research_tools import augment as AU
    x = _resample_speed(take, rng.uniform(0.85, 1.15))
    if rng.random() < 0.5:
        x = _reverb(x, rng)
    x = x * rng.uniform(0.3, 1.6)
    n = int(CLIP_SECONDS * SR)
    canvas = np.zeros(n, np.float32)
    x = x[:n]
    end_slack = n - len(x)
    off = int(rng.integers(max(0, end_slack - int(0.4 * SR)), end_slack + 1)) if end_slack > 0 else 0
    canvas[off:off + len(x)] = x                                   # הביטוי בסוף החלון (כמו בזמן אמת)
    if rng.random() < 0.85:
        canvas = AU.mix_snr(canvas + 1e-4 * rng.standard_normal(n).astype(np.float32),
                            backgrounds[int(rng.integers(0, len(backgrounds)))], float(rng.uniform(0, 20)), rng)
    if rng.random() < 0.6:
        canvas = AU.phone_channel(canvas, rng)
    if rng.random() < 0.2:
        canvas = AU.pocket(canvas, rng)
    return np.clip(canvas, -1, 1).astype(np.float32)


def negative_windows(audio_list, rng, count):
    n = int(CLIP_SECONDS * SR)
    pool = [a for a in audio_list if len(a) >= n // 2]
    out = []
    for _ in range(count):
        a = pool[int(rng.integers(0, len(pool)))]
        if len(a) < n:
            a = np.pad(a, (n - len(a), 0))
        s = int(rng.integers(0, len(a) - n + 1))
        w = a[s:s + n]
        if rng.random() < 0.3:                                      # הפוך בזמן: נשמע כמו דיבור, לא הביטוי
            w = w[::-1].copy()
        out.append(w.astype(np.float32))
    return out


# ===================================================================== אימון
def train(takes, free_speech, embedder=None, variations_per_take=300, seed=0, log=print):
    """
    takes: {phrase_id: [audio,...]}; free_speech: audio (דיבור חופשי - שליליים).
    -> (models {phrase_id: {"model", "threshold"}}, report)
    """
    from research_tools.yamnet_detector import LogisticModel
    rng = np.random.default_rng(seed)
    emb = embedder or SpeechEmbedder()
    split = int(len(free_speech) * 0.7)
    neg_train_src, neg_test_src = [free_speech[:split]], [free_speech[split:]]
    bgs = synthetic_backgrounds(rng, speech_like=free_speech[:split])
    report, models = {"phrases": {}, "variations_total": 0}, {}
    for pid, clips in takes.items():
        if len(clips) < HOLDOUT_TAKES + 2:
            raise ValueError(f"צריך לפחות {HOLDOUT_TAKES + 2} הקלטות לביטוי {pid}")
        test_clips, train_clips = clips[-HOLDOUT_TAKES:], clips[:-HOLDOUT_TAKES]
        others_train = [c for q, cs in takes.items() if q != pid for c in cs[:-HOLDOUT_TAKES]]
        t0 = time.time()
        Xp = [emb.clip_feature(augment(c, bgs, rng)) for c in train_clips for _ in range(variations_per_take)]
        neg_audio = negative_windows(neg_train_src + others_train, rng, len(Xp))
        Xn = [emb.clip_feature(augment(w, bgs, rng) if rng.random() < 0.5 else w) for w in neg_audio]
        X = np.stack(Xp + Xn)
        y = np.r_[np.ones(len(Xp)), np.zeros(len(Xn))]
        m = LogisticModel(l2=1e-2, epochs=300).fit(X, y)
        # בדיקה: הקלטות שלא נראו (גם עם רעש) מול דיבור חופשי שלא נראה + ביטויים אחרים שלא נראו
        others_test = [c for q, cs in takes.items() if q != pid for c in cs[-HOLDOUT_TAKES:]]
        pos_test = [emb.clip_feature(augment(c, bgs, rng)) for c in test_clips for _ in range(20)]
        pos_clean = [emb.clip_feature(_place(c)) for c in test_clips]
        neg_test = [emb.clip_feature(w) for w in negative_windows(neg_test_src, rng, 200)] + \
                   [emb.clip_feature(_place(c)) for c in others_test]
        sp, sc, sn = (m.decision_function(np.stack(a)) for a in (pos_test, pos_clean, neg_test))
        thr = float(np.max(sn)) + 0.5                             # אפס התראות שווא על סט הבדיקה השלילי
        models[pid] = {"model": m, "threshold": thr}
        report["phrases"][pid] = {"train_takes": len(train_clips), "variations": len(Xp),
                                  "heldout_clean_detected": f"{int(np.sum(sc >= thr))}/{len(sc)}",
                                  "heldout_noisy_recall": round(float(np.mean(sp >= thr)), 3),
                                  "negatives_tested": len(sn), "false_positives": int(np.sum(sn >= thr)),
                                  "seconds": round(time.time() - t0, 1)}
        report["variations_total"] += len(Xp)
        log(f"{pid}: {report['phrases'][pid]}")
    return models, report


def _place(take):
    n = int(CLIP_SECONDS * SR)
    c = np.zeros(n, np.float32)
    t = take[:n]
    c[n - len(t):] = t
    return c


def save_models(store, models, report):
    blob = {pid: {"w": m["model"].w.tolist(), "b": float(m["model"].b), "mean": m["model"].mean.tolist(),
                  "std": m["model"].std.tolist(), "threshold": m["threshold"]} for pid, m in models.items()}
    store.put("models", json.dumps({"models": blob, "report": report, "trained_at": time.time()}).encode())


class PersonalDetector:
    """זיהוי בזמן אמת על חלון אודיו (2 שניות אחרונות)."""

    def __init__(self, store, embedder=None):
        d = json.loads(store.get("models").decode())
        self.models = {pid: (np.array(m["w"], np.float32), m["b"], np.array(m["mean"], np.float32),
                             np.array(m["std"], np.float32), m["threshold"]) for pid, m in d["models"].items()}
        self.report = d["report"]
        self.emb = embedder or SpeechEmbedder()

    def detect(self, audio, sr=SR):
        if sr != SR:
            from research_tools.yamnet_detector import to_16k
            audio = to_16k(audio, sr)
        n = int(CLIP_SECONDS * SR)
        a = np.asarray(audio, np.float32)
        a = np.pad(a, (max(0, n - len(a)), 0))[-n:]
        f = self.emb.clip_feature(a)
        hits = []
        for pid, (w, b, mean, std, thr) in self.models.items():
            s = float(((f - mean) / std) @ w + b)
            if s >= thr:
                hits.append((pid, s))
        return sorted(hits, key=lambda h: -h[1])

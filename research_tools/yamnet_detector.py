# -*- coding: utf-8 -*-
"""
yamnet_detector.py - גלאי מצוקה מבוססי YAMNet, למדידה מול המנגנון הקיים (עוצמת רעש).

שני גלאים:
1. YamnetClassDetector ("zero-shot"): הציון למסגרת = ההסתברות המקסימלית ש-YAMNet נותן למחלקות
   מצוקה קיימות ב-AudioSet (Screaming, Shout, Yell, Crying/sobbing, Wail/moan, Whimper).
   בלי אימון בכלל - מה YAMNet יודע "מהקופסה".
2. EmbeddingClassifierDetector: רגרסיה לוגיסטית קטנה מעל embeddings של YAMNet (1024 מימדים),
   מאומנת על הדאטה שלנו - כולל "שליליים קשים" (צחוק, עידוד, ילדים) שמהם רוצים להבדיל.
   המודל קטן מאוד (1025 משקלים) ומתאים לטלפון.

פרטי YAMNet (נבדקו מול המודל עצמו, ראו tests/test_yamnet_detector.py):
- קלט waveform מונו 16kHz float32. מסגרת = 0.96 שניות, צעד 0.48 שניות.
- מספר מסגרות = 1 + ceil((max(n, 15600) - 15600) / 7680) (קלט קצר מרופד; 15600 = 0.96s + 0.015s של STFT).
- באודיו ארוך מעבדים בחלקים: רק המסגרת האחרונה בכל חלק מושפעת מהריפוד, ולכן כל חלק נחתך
  לפני המסגרת האחרונה וממשיך ממנה - התוצאה זהה לעיבוד בבת אחת.
"""

import csv
import hashlib
import json
import math
import os

import numpy as np

from research_tools.acoustic_eval import TARGET_SR, Detector

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(os.path.dirname(HERE), "research", "models", "yamnet")
EMB_MODEL = os.path.join(MODEL_DIR, "yamnet_float_embeddings.tflite")
CLASS_MAP = os.path.join(MODEL_DIR, "yamnet_class_map.csv")

FRAME_WIN = 15600        # 0.96 s + חלון STFT (0.025) - צעד STFT (0.010) - האורך המינימלי ש-YAMNet צורך
FRAME_HOP = 7680         # 0.48 s
HOP_SECONDS = FRAME_HOP / TARGET_SR

DISTRESS_CLASSES = ("Screaming", "Shout", "Yell", "Crying, sobbing", "Wail, moan", "Whimper")


def n_frames(n_samples):
    return 1 + max(0, math.ceil((n_samples - FRAME_WIN) / FRAME_HOP))


def to_16k(x, sr):
    """resample איכותי (polyphase) - לגלאי נוירוני דגימה לינארית מעוותת תדרים גבוהים."""
    x = np.asarray(x, dtype=np.float32)
    if sr == TARGET_SR or len(x) == 0:
        return x
    try:
        from scipy.signal import resample_poly
        g = math.gcd(int(sr), TARGET_SR)
        return resample_poly(x, TARGET_SR // g, int(sr) // g).astype(np.float32)
    except ImportError:                                   # pragma: no cover
        from research_tools.acoustic_eval import resample
        return resample(x, sr)


def class_names(path=CLASS_MAP):
    with open(path, encoding="utf-8") as fh:
        return [r["display_name"] for r in csv.DictReader(fh)]


class Yamnet:
    """עוטף את מודל ה-TFLite. analyze(x, sr) -> (scores[N,521], embeddings[N,1024])."""

    def __init__(self, model_path=EMB_MODEL, chunk_frames=600):
        from ai_edge_litert.interpreter import Interpreter
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"מודל YAMNet לא נמצא: {model_path}")
        self.it = Interpreter(model_path=model_path)
        self.inp = self.it.get_input_details()[0]["index"]
        outs = self.it.get_output_details()
        self.out_scores, self.out_emb = outs[0]["index"], outs[1]["index"]
        self.chunk_frames = max(2, int(chunk_frames))
        self._len = None

    def _run(self, x):
        if self._len != len(x):
            self.it.resize_tensor_input(self.inp, [len(x)])
            self.it.allocate_tensors()
            self._len = len(x)
        self.it.set_tensor(self.inp, x)
        self.it.invoke()
        return self.it.get_tensor(self.out_scores).copy(), self.it.get_tensor(self.out_emb).copy()

    def analyze(self, x, sr=TARGET_SR):
        x = to_16k(x, sr)
        total = n_frames(len(x))
        if total <= self.chunk_frames:
            return self._run(x)
        scores, embs, f = [], [], 0
        while f < total:
            start = f * FRAME_HOP
            # מספיק דגימות ל-chunk_frames מסגרות + אחת נוספת שנזרקת (מושפעת מריפוד)
            seg = x[start:start + FRAME_WIN + self.chunk_frames * FRAME_HOP]
            s, e = self._run(seg)
            keep = len(s) if start + len(seg) >= len(x) else self.chunk_frames
            scores.append(s[:keep])
            embs.append(e[:keep])
            f += keep
        return np.concatenate(scores)[:total], np.concatenate(embs)[:total]


_SHARED = {}


def shared_yamnet():
    if "m" not in _SHARED:
        _SHARED["m"] = Yamnet()
    return _SHARED["m"]


# ===================================================================== גלאים
class YamnetClassDetector(Detector):
    name = "yamnet_zero_shot"
    hop_seconds = HOP_SECONDS

    def __init__(self, model=None, classes=DISTRESS_CLASSES):
        self.model = model
        names = class_names()
        missing = [c for c in classes if c not in names]
        if missing:
            raise ValueError(f"מחלקות לא קיימות ב-YAMNet: {missing}")
        self.idx = np.array([names.index(c) for c in classes])

    def from_scores(self, scores):
        return scores[:, self.idx].max(axis=1)

    def frame_scores(self, x, sr):
        s, _ = (self.model or shared_yamnet()).analyze(x, sr)
        return np.arange(len(s)) * self.hop_seconds, self.from_scores(s)


class LogisticModel:
    """רגרסיה לוגיסטית עם L2 ואיזון מחלקות, ב-numpy בלבד (אין צורך ב-sklearn; קל להעביר לטלפון)."""

    def __init__(self, l2=1e-3, lr=0.5, epochs=300):
        self.l2, self.lr, self.epochs = l2, lr, epochs
        self.mean = self.std = self.w = None
        self.b = 0.0
        self.info = {}

    def fit(self, X, y, sample_groups=None):
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        if y.min() == y.max():
            raise ValueError("צריך גם דוגמאות חיוביות וגם שליליות")
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-6
        Z = (X - self.mean) / self.std
        # משקל לכל דוגמה: מחלקות מאוזנות (אחרת המודל ילמד "תמיד לא")
        pos = y.sum()
        wts = np.where(y > 0, len(y) / (2 * pos), len(y) / (2 * (len(y) - pos))).astype(np.float32)
        wts /= wts.sum()
        w = np.zeros(Z.shape[1], dtype=np.float32)
        b = 0.0
        # Adam - יציב בלי כיול ידני של קצב הלמידה
        m_w = np.zeros_like(w); v_w = np.zeros_like(w); m_b = v_b = 0.0
        b1, b2, eps = 0.9, 0.999, 1e-8
        for t in range(1, self.epochs + 1):
            p = _sigmoid(Z @ w + b)
            g = wts * (p - y)
            gw = Z.T @ g + self.l2 * w
            gb = float(g.sum())
            m_w = b1 * m_w + (1 - b1) * gw; v_w = b2 * v_w + (1 - b2) * gw * gw
            m_b = b1 * m_b + (1 - b1) * gb; v_b = b2 * v_b + (1 - b2) * gb * gb
            w -= self.lr * 0.02 * (m_w / (1 - b1 ** t)) / (np.sqrt(v_w / (1 - b2 ** t)) + eps)
            b -= self.lr * 0.02 * (m_b / (1 - b1 ** t)) / (math.sqrt(v_b / (1 - b2 ** t)) + eps)
        self.w, self.b = w, b
        self.info = {"n": int(len(y)), "n_pos": int(pos), "l2": self.l2, "epochs": self.epochs}
        return self

    def decision_function(self, X):
        """logit - לדירוג ולספים. (הסתברות "נדבקת" ל-1.0 וכל השיאים נראים זהים - אבד הדירוג.)"""
        Z = (np.asarray(X, dtype=np.float32) - self.mean) / self.std
        return Z @ self.w + self.b

    def predict_proba(self, X):
        return _sigmoid(self.decision_function(X))

    def save(self, path):
        np.savez(path, mean=self.mean, std=self.std, w=self.w, b=np.array([self.b]),
                 info=np.array([json.dumps(self.info, ensure_ascii=False)]))

    @classmethod
    def load(cls, path):
        d = np.load(path, allow_pickle=False)
        m = cls()
        m.mean, m.std, m.w, m.b = d["mean"], d["std"], d["w"], float(d["b"][0])
        m.info = json.loads(str(d["info"][0]))
        return m


def _sigmoid(z):
    z = np.clip(np.asarray(z, dtype=np.float64), -60, 60)   # בלי overflow
    return 1.0 / (1.0 + np.exp(-z))


class MLPModel:
    """
    רשת קטנה (שכבה נסתרת אחת, ReLU) מעל ה-embeddings - numpy בלבד. ~260K משקלים (~1MB): עדיין
    מתאים לטלפון. משקל לכל דוגמה (sample_weight) מאפשר להעניש יותר טעויות על "שליליים קשים".
    """

    def __init__(self, hidden=256, epochs=25, batch=512, lr=1e-3, l2=1e-4, seed=0):
        self.hidden, self.epochs, self.batch, self.lr, self.l2, self.seed = hidden, epochs, batch, lr, l2, seed
        self.info = {}

    def fit(self, X, y, sample_weight=None):
        rng = np.random.default_rng(self.seed)
        X = np.asarray(X, dtype=np.float32)
        y = np.asarray(y, dtype=np.float32)
        if y.min() == y.max():
            raise ValueError("צריך גם דוגמאות חיוביות וגם שליליות")
        self.mean = X.mean(axis=0)
        self.std = X.std(axis=0) + 1e-6
        Z = (X - self.mean) / self.std
        pos = y.sum()
        w = np.where(y > 0, len(y) / (2 * pos), len(y) / (2 * (len(y) - pos))).astype(np.float32)
        if sample_weight is not None:
            w = w * np.asarray(sample_weight, dtype=np.float32)
        d = Z.shape[1]
        self.W1 = (rng.standard_normal((d, self.hidden)) * math.sqrt(2.0 / d)).astype(np.float32)
        self.b1 = np.zeros(self.hidden, np.float32)
        self.W2 = (rng.standard_normal(self.hidden) * math.sqrt(1.0 / self.hidden)).astype(np.float32)
        self.b2 = np.float32(0.0)
        params = [self.W1, self.b1, self.W2]
        m = [np.zeros_like(p) for p in params] + [0.0]
        v = [np.zeros_like(p) for p in params] + [0.0]
        b1_, b2_, eps, t = 0.9, 0.999, 1e-8, 0
        for _ in range(self.epochs):
            order = rng.permutation(len(y))
            for s in range(0, len(y), self.batch):
                idx = order[s:s + self.batch]
                xb, yb, wb = Z[idx], y[idx], w[idx]
                h = xb @ self.W1 + self.b1
                a = np.maximum(h, 0)
                p = _sigmoid(a @ self.W2 + self.b2)
                g = (wb * (p - yb)) / wb.sum()
                gW2 = a.T @ g + self.l2 * self.W2
                gb2 = float(g.sum())
                ga = np.outer(g, self.W2) * (h > 0)
                gW1 = xb.T @ ga + self.l2 * self.W1
                gb1 = ga.sum(axis=0)
                t += 1
                for i, (pp, gg) in enumerate(((self.W1, gW1), (self.b1, gb1), (self.W2, gW2))):
                    m[i] = b1_ * m[i] + (1 - b1_) * gg
                    v[i] = b2_ * v[i] + (1 - b2_) * gg * gg
                    pp -= self.lr * (m[i] / (1 - b1_ ** t)) / (np.sqrt(v[i] / (1 - b2_ ** t)) + eps)
                m[3] = b1_ * m[3] + (1 - b1_) * gb2
                v[3] = b2_ * v[3] + (1 - b2_) * gb2 * gb2
                self.b2 = np.float32(self.b2 - self.lr * (m[3] / (1 - b1_ ** t)) / (math.sqrt(v[3] / (1 - b2_ ** t)) + eps))
        self.info = {"n": int(len(y)), "n_pos": int(pos), "hidden": self.hidden, "epochs": self.epochs, "kind": "mlp"}
        return self

    def decision_function(self, X):
        Z = (np.asarray(X, dtype=np.float32) - self.mean) / self.std
        return np.maximum(Z @ self.W1 + self.b1, 0) @ self.W2 + self.b2

    def predict_proba(self, X):
        return _sigmoid(self.decision_function(X))

    def save(self, path):
        np.savez(path, mean=self.mean, std=self.std, W1=self.W1, b1=self.b1, W2=self.W2, b2=np.array([self.b2]),
                 info=np.array([json.dumps(self.info, ensure_ascii=False)]))

    @classmethod
    def load(cls, path):
        d = np.load(path, allow_pickle=False)
        m = cls()
        m.mean, m.std, m.W1, m.b1, m.W2 = d["mean"], d["std"], d["W1"], d["b1"], d["W2"]
        m.b2 = np.float32(d["b2"][0])
        m.info = json.loads(str(d["info"][0]))
        return m


def context_features(emb):
    """הקשר זמני: לכל מסגרת - היא עצמה + ממוצע ומקסימום של שכנותיה (±1 מסגרת, ~1.5 שניות סה"כ).
    צרחה היא אירוע מתמשך; מחיאת כף בודדת או צפצוף - לא."""
    e = np.asarray(emb, dtype=np.float32)
    pad = np.concatenate([e[:1], e, e[-1:]])
    win = np.stack([pad[:-2], pad[1:-1], pad[2:]])
    return np.concatenate([e, win.mean(axis=0), win.max(axis=0)], axis=1)


def smooth_scores(s, width=2):
    """ממוצע נע קדימה: התראה רק כשהציון גבוה לאורך width מסגרות רצופות (~0.5 שנ' לכל מסגרת)."""
    s = np.asarray(s, dtype=np.float64)
    if width <= 1 or len(s) < 2:
        return s
    k = np.ones(width) / width
    return np.convolve(np.pad(s, (0, width - 1), mode="edge"), k, mode="valid")


def event_frames(audio16k, n, rel=0.25):
    """
    תיוג חלש -> מסגרות: קטע שמתויג "צרחה" כולל לרוב גם שקט לפני ואחרי. מסגרות חיוביות לאימון =
    מסגרות שהעוצמה בהן לפחות rel מהמסגרת החזקה בקטע. (תלוי רק בעוצמה ולא ב-YAMNet, כדי לא
    להטות את המודל לטובת מה ש-YAMNet כבר יודע.)
    """
    x = np.asarray(audio16k, dtype=np.float32)
    rms = np.empty(n, dtype=np.float32)
    for i in range(n):
        seg = x[i * FRAME_HOP:i * FRAME_HOP + FRAME_WIN]
        rms[i] = math.sqrt(float(np.mean(seg * seg))) if len(seg) else 0.0
    return rms >= rel * max(float(rms.max()), 1e-9)


class EmbeddingClassifierDetector(Detector):
    name = "yamnet_embed_lr"
    hop_seconds = HOP_SECONDS

    def __init__(self, clf, model=None):
        self.clf, self.model = clf, model

    def from_embeddings(self, emb):
        return self.clf.predict_proba(emb)

    def frame_scores(self, x, sr):
        _, e = (self.model or shared_yamnet()).analyze(x, sr)
        return np.arange(len(e)) * self.hop_seconds, self.from_embeddings(e)


# ===================================================================== מטמון
class FeatureCache:
    """
    YAMNet על עשרות שעות אודיו לוקח דקות - שומרים פעם אחת לכל קטע (לפי hash של האודיו):
    ציוני מחלקות המצוקה, embeddings (float16), ועוצמה לכל מסגרת לתיוג.
    """

    def __init__(self, cache_dir, model=None):
        self.dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.model = model
        self._zs = None

    def _path(self, key):
        return os.path.join(self.dir, key[:2], key + ".npz")

    @staticmethod
    def key(audio16k):
        return hashlib.sha1(np.ascontiguousarray(audio16k, dtype=np.float32).tobytes()).hexdigest()

    def get(self, audio, sr):
        x = to_16k(audio, sr)
        k = self.key(x)
        p = self._path(k)
        if os.path.exists(p):
            d = np.load(p)
            return {n: d[n] for n in d.files}
        if self._zs is None:
            self._zs = YamnetClassDetector(model=self.model)
        s, e = (self.model or shared_yamnet()).analyze(x, TARGET_SR)
        feats = {"zero_shot": self._zs.from_scores(s).astype(np.float32),
                 "emb": e.astype(np.float16),
                 "event": event_frames(x, len(e))}
        os.makedirs(os.path.dirname(p), exist_ok=True)
        np.savez(p, **feats)
        return feats

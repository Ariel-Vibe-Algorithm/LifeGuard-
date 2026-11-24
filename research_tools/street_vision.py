# -*- coding: utf-8 -*-
"""
street_vision.py - סיווג רחובות מסרטון של מסלול הליכה (מחקר).

הצנרת:
  סרטון + מסלול GPS  ->  דגימת פריים כל ~2 שניות  ->  טשטוש פנים (YuNet) *לפני* כל עיבוד
  ->  embedding של CLIP (512 מימדים)  ->  סיווג zero-shot של מאפייני בטיחות
  ->  שיוך כל פריים למיקום (אינטרפולציה במסלול)  ->  צבירה לתאי רחוב של ~25 מ'
  ->  (בהמשך) מסווג קטן מאומן על תיוגים של משתמשים, מעל אותם embeddings.

פרטיות (חלק מהתכנון, לא תוספת):
- הפריימים לעולם לא נשמרים לדיסק ולא נשלחים לשום מקום. נשמרים רק מספרים: מאפיינים, בהירות
  נמדדת ו-embedding. פנים מטושטשות לפני חישוב ה-embedding.
- אין זיהוי אנשים, אין ספירת פנים לצורך מעקב; מספר הפנים שטושטשו נרשם רק כבקרת איכות.
- הכול רץ במחשב של המשתמש. הסרטון המקורי נשאר אצלו ונמחק לפי בחירתו.

מאפיינים (zero-shot, כיוון חיובי = בטוח יותר): תאורה, מדרכה, פעילות אנשים, תחזוקה, שדה ראייה.
אלה *השערות של מודל* - הן מסומנות ככאלה ולא מוצגות כציון בטיחות עד שיאומתו (ראו street_intel).
בהירות (luminance) היא מדידה פיזיקלית ישירה מהפריים.
"""

import json
import math
import os
import xml.etree.ElementTree as ET

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(os.path.dirname(HERE), "research", "models")
CLIP_DIR = os.path.join(MODELS, "clip")
YUNET = os.path.join(MODELS, "yunet", "face_detection_yunet_2023mar.onnx")

ATTRIBUTES = {
    "lighting": (["a well-lit street at night with bright street lamps", "a bright street in daylight"],
                 ["a dark unlit street at night", "a dark alley with no lights"]),
    "sidewalk": (["a street with a wide sidewalk for pedestrians", "a pedestrian sidewalk next to the road"],
                 ["a road with no sidewalk", "a narrow road shoulder with no pavement for walking"]),
    "activity": (["a busy street with many people walking", "people and open shops along the street"],
                 ["an empty deserted street with nobody around", "a desolate empty area"]),
    "maintenance": (["a clean well-maintained street", "a tidy neighborhood street"],
                    ["a neglected street with graffiti and litter", "abandoned buildings and broken fences"]),
    "visibility": (["an open wide street with good visibility"],
                   ["a narrow alley with hidden corners", "an overgrown path with bushes blocking the view",
                    "a dark underpass or tunnel"]),
}
LABELS_HE = {"lighting": "תאורה", "sidewalk": "מדרכה", "activity": "אנשים בסביבה", "maintenance": "תחזוקה",
             "visibility": "שדה ראייה"}
MAX_GPX_BYTES = 20 * 1024 * 1024


# ===================================================================== CLIP (ONNX)
class Clip:
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

    def __init__(self, model_dir=CLIP_DIR):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.vision = ort.InferenceSession(os.path.join(model_dir, "vision_model_quantized.onnx"), opts,
                                           providers=["CPUExecutionProvider"])
        self.text = ort.InferenceSession(os.path.join(model_dir, "text_model_quantized.onnx"), opts,
                                         providers=["CPUExecutionProvider"])
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self._text_cache = {}

    @classmethod
    def preprocess(cls, bgr):
        import cv2
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        s = 224 / min(h, w)
        rgb = cv2.resize(rgb, (max(224, round(w * s)), max(224, round(h * s))), interpolation=cv2.INTER_CUBIC)
        h, w = rgb.shape[:2]
        y, x = (h - 224) // 2, (w - 224) // 2
        rgb = rgb[y:y + 224, x:x + 224].astype(np.float32) / 255.0
        return ((rgb - cls.MEAN) / cls.STD).transpose(2, 0, 1)

    def embed_images(self, frames):
        if not frames:
            return np.zeros((0, 512), dtype=np.float32)
        x = np.stack([self.preprocess(f) for f in frames]).astype(np.float32)
        e = self.vision.run(None, {"pixel_values": x})[0]
        return e / np.linalg.norm(e, axis=1, keepdims=True)

    def embed_text(self, text):
        if text not in self._text_cache:
            ids = np.array([self.tok.encode(text).ids[:77]], dtype=np.int64)
            e = self.text.run(None, {"input_ids": ids})[0][0]
            self._text_cache[text] = e / np.linalg.norm(e)
        return self._text_cache[text]

    def zero_shot(self, emb, attributes=ATTRIBUTES):
        """emb[N,512] -> {attr: [N]} - ההסתברות (softmax על כל הניסוחים) שהתמונה בצד ה"בטוח"."""
        out = {}
        for attr, (pos, neg) in attributes.items():
            T = np.stack([self.embed_text(t) for t in pos + neg])
            logits = 100.0 * emb @ T.T
            logits -= logits.max(axis=1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(axis=1, keepdims=True)
            out[attr] = p[:, :len(pos)].sum(axis=1)
        return out


# ===================================================================== פרטיות
class FaceBlur:
    def __init__(self, model=YUNET, score_threshold=0.6):
        import cv2
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(model, "", (320, 320), score_threshold, 0.3, 5000)

    def __call__(self, bgr):
        """מחזיר (עותק מטושטש, מספר פנים). טשטוש חזק עם שוליים, כך שאי אפשר לזהות."""
        cv2 = self.cv2
        img = bgr.copy()
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, faces = self.det.detect(img)
        n = 0 if faces is None else len(faces)
        for f in (faces if faces is not None else []):
            x, y, fw, fh = f[:4]
            pad = 0.25
            x0, y0 = max(0, int(x - pad * fw)), max(0, int(y - pad * fh))
            x1, y1 = min(w, int(x + (1 + pad) * fw)), min(h, int(y + (1 + pad) * fh))
            if x1 > x0 and y1 > y0:
                # פיקסליזציה ל-6x6 ואז טשטוש: לא ניתן לשחזר פנים (בניגוד לטשטוש קל)
                rw, rh = x1 - x0, y1 - y0
                small = cv2.resize(img[y0:y1, x0:x1], (6, 6), interpolation=cv2.INTER_AREA)
                block = cv2.resize(small, (rw, rh), interpolation=cv2.INTER_NEAREST)
                k = max(3, (max(rw, rh) // 4) | 1)
                img[y0:y1, x0:x1] = cv2.GaussianBlur(block, (k, k), 0)
        return img, n


def luminance(bgr):
    """בהירות נמדדת: ממוצע ערוץ V (0..1) ואחוז הפיקסלים הכהים (V<0.15)."""
    import cv2
    v = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[:, :, 2].astype(np.float32) / 255.0
    return float(v.mean()), float((v < 0.15).mean())


# ===================================================================== מסלול GPS
def parse_gpx(path):
    """GPX -> [(epoch, lat, lon)] ממוין. (קובץ מהטלפון; גודל מוגבל.)"""
    import calendar
    import time as _t
    if os.path.getsize(path) > MAX_GPX_BYTES:
        raise ValueError("קובץ GPX גדול מדי")
    root = ET.parse(path).getroot()
    pts = []
    for el in root.iter():
        if el.tag.endswith("trkpt") or el.tag.endswith("rtept"):
            ts = next((c.text for c in el if c.tag.endswith("time") and c.text), None)
            if ts is None:
                continue
            ts = ts.strip().replace("Z", "")
            frac = 0.0
            if "." in ts:
                ts, f = ts.split(".", 1)
                frac = float("0." + "".join(ch for ch in f if ch.isdigit()) or 0)
            ts = ts.split("+")[0]
            epoch = calendar.timegm(_t.strptime(ts, "%Y-%m-%dT%H:%M:%S")) + frac
            pts.append((epoch, float(el.get("lat")), float(el.get("lon"))))
    pts.sort()
    return pts


def position_at(track, t):
    """אינטרפולציה לינארית; מחוץ למסלול (יותר מ-30 שנ') -> None. אסור להמציא מיקום."""
    if not track or t < track[0][0] - 30 or t > track[-1][0] + 30:
        return None
    times = [p[0] for p in track]
    i = int(np.searchsorted(times, t))
    if i <= 0:
        return track[0][1:]
    if i >= len(track):
        return track[-1][1:]
    (t0, a0, o0), (t1, a1, o1) = track[i - 1], track[i]
    if t1 - t0 > 60:            # חור גדול במסלול - לא ממציאים מה היה באמצע
        return None
    r = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    return a0 + r * (a1 - a0), o0 + r * (o1 - o0)


def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# ===================================================================== סרטון -> תצפיות
def sample_video(path, every_s=2.0, max_frames=2000):
    """[(שנייה בסרטון, פריים BGR)] - קורא בלי לשמור שום פריים לדיסק."""
    import cv2
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("לא ניתן לפתוח את הסרטון")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, int(round(every_s * fps)))
    out = []
    try:
        idx = 0
        while len(out) < max_frames:
            if total:
                if idx >= total:
                    break
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            if max(h, w) > 960:            # מספיק לסיווג; חוסך זמן וזיכרון
                s = 960 / max(h, w)
                frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            out.append((idx / fps, frame))
            idx += step
    finally:
        cap.release()
    return out


class StreetAnalyzer:
    def __init__(self, clip=None, blur=None):
        self.clip = clip or Clip()
        self.blur = blur or FaceBlur()

    def analyze_frames(self, frames, batch=16):
        """frames: [BGR] -> (embeddings[N,512], zero_shot{attr:[N]}, luminance[N,2], faces_blurred[N])"""
        embs, lum, faces = [], [], []
        for i in range(0, len(frames), batch):
            chunk = []
            for f in frames[i:i + batch]:
                safe, n = self.blur(f)          # טשטוש לפני כל שימוש
                chunk.append(safe)
                faces.append(n)
                lum.append(luminance(f))        # בהירות - מהפריים המקורי (לא מושפע מטשטוש)
            embs.append(self.clip.embed_images(chunk))
        emb = np.concatenate(embs) if embs else np.zeros((0, 512), dtype=np.float32)
        return emb, self.clip.zero_shot(emb), np.array(lum).reshape(-1, 2), np.array(faces, dtype=int)

    def analyze_walk(self, video_path, track, video_start=None, every_s=2.0, walk_id=None, log=print,
                     thumbs=None):
        """סרטון + מסלול -> רשימת תצפיות (רק מספרים; אין תמונות).
        thumbs: רשימה (אופציונלית) שתתמלא בתמונות ממוזערות *מטושטשות* - בזיכרון בלבד, לתיוג במסך."""
        samples = sample_video(video_path, every_s)
        log(f"נדגמו {len(samples)} פריימים")
        start = video_start if video_start is not None else (track[0][0] if track else None)
        if thumbs is not None:
            import cv2
            for _, f in samples:
                safe, _ = self.blur(f)
                h, w = safe.shape[:2]
                thumbs.append(cv2.resize(safe, (200, max(1, int(h * 200 / w))), interpolation=cv2.INTER_AREA))
        emb, zs, lum, faces = self.analyze_frames([f for _, f in samples])
        obs = []
        for i, (t, _) in enumerate(samples):
            pos = position_at(track, start + t) if start is not None else None
            obs.append({"walk_id": walk_id, "t": round(t, 2),
                        "observed_at": round(start + t, 1) if start is not None else None,
                        "lat": pos[0] if pos else None,
                        "lon": pos[1] if pos else None,
                        "attributes": {k: round(float(v[i]), 4) for k, v in zs.items()},
                        "luminance": round(lum[i][0], 4), "dark_fraction": round(lum[i][1], 4),
                        "faces_blurred": int(faces[i]), "embedding": emb[i].astype(np.float16)})
        return obs


# ===================================================================== צבירה לתאי רחוב
def cell_of(lat, lon, cell_m=25.0):
    dlat = cell_m / 111320.0
    dlon = cell_m / (111320.0 * max(0.1, math.cos(math.radians(lat))))
    return int(math.floor(lat / dlat)), int(math.floor(lon / dlon))


def aggregate(observations, cell_m=25.0):
    """תצפיות -> תאי רחוב: ממוצע מאפיינים, בהירות, מספר תצפיות ומספר הליכות שונות."""
    cells = {}
    for o in observations:
        if o.get("lat") is None:
            continue
        key = cell_of(o["lat"], o["lon"], cell_m)
        c = cells.setdefault(key, {"lat": [], "lon": [], "attrs": {}, "lum": [], "walks": set()})
        c["lat"].append(o["lat"]); c["lon"].append(o["lon"]); c["lum"].append(o["luminance"])
        c["walks"].add(o.get("walk_id"))
        for k, v in o["attributes"].items():
            c["attrs"].setdefault(k, []).append(v)
    out = []
    for key, c in cells.items():
        out.append({"cell": f"{key[0]}:{key[1]}", "lat": float(np.mean(c["lat"])), "lon": float(np.mean(c["lon"])),
                    "observations": len(c["lat"]), "walks": len(c["walks"]),
                    "luminance": round(float(np.mean(c["lum"])), 4),
                    "attributes": {k: round(float(np.mean(v)), 4) for k, v in c["attrs"].items()}})
    return sorted(out, key=lambda c: c["cell"])


# ===================================================================== אימון על תיוגים
def train_attribute(observations, labels, l2=1e-2):
    """
    labels: {index: 0/1} - תיוג משתמש לפריים (למשל "חשוך" = 0 בתאורה). מאמן רגרסיה לוגיסטית על
    ה-embeddings (אותו מודל קטן כמו בזיהוי הקולי), עם הערכה צולבת לפי הליכה: פריימים מאותה הליכה
    דומים מאוד, ולכן אסור שיהיו גם באימון וגם בבדיקה.
    """
    from research_tools.yamnet_detector import LogisticModel
    idx = sorted(labels)
    X = np.stack([np.asarray(observations[i]["embedding"], dtype=np.float32) for i in idx])
    y = np.array([labels[i] for i in idx], dtype=np.float32)
    groups = np.array([str(observations[i].get("walk_id")) for i in idx])
    uniq = sorted(set(groups))
    oof = np.full(len(idx), np.nan)
    if len(uniq) >= 2:
        for g in uniq:
            te = groups == g
            tr = ~te
            if len(set(y[tr])) < 2:
                continue
            m = LogisticModel(l2=l2, epochs=300).fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])
    done = ~np.isnan(oof)
    acc = float(np.mean((oof[done] >= 0.5) == (y[done] >= 0.5))) if done.any() else None
    final = LogisticModel(l2=l2, epochs=300).fit(X, y)
    final.info.update({"cv_accuracy_by_walk": acc, "cv_evaluated": int(done.sum()), "walks": len(uniq)})
    return final


def save_observations(observations, path):
    """JSON lines בלי embeddings (קריא) + npz עם embeddings. לעולם לא תמונות."""
    with open(path, "w", encoding="utf-8") as fh:
        for o in observations:
            fh.write(json.dumps({k: v for k, v in o.items() if k != "embedding"}, ensure_ascii=False) + "\n")
    np.savez_compressed(os.path.splitext(path)[0] + "_emb.npz",
                        emb=np.stack([o["embedding"] for o in observations]) if observations else np.zeros((0, 512)))

# -*- coding: utf-8 -*-
"""
build_yamnet_web.py - YAMNet (Google, Apache-2.0) מקובץ ה-TFLite למשקלים שה-PWA מריץ ב-JavaScript טהור.

למה JavaScript טהור ולא onnxruntime-web / tfjs-tflite: שניהם WebAssembly (קבצי wasm של 10MB+, וב-CSP צריך
'wasm-unsafe-eval'). YAMNet הוא MobileNet-v1 קטן (13 שכבות depthwise-separable) - מימוש ישיר ב-JS הוא ~300
שורות, רץ ב-Web Worker, לא דורש שום שינוי ב-CSP (script-src 'self' בלבד) ועובד בכל דפדפן מודרני כולל Safari ב-iOS.

מה הסקריפט עושה:
1. קורא את research/models/yamnet/yamnet_float_embeddings.tflite (אותו קובץ שהאפליקציה ב-Windows מריצה).
2. מוצא לפי מבנה הגרף (לא לפי מספרי טנזורים): חלון Hann, מטריצת mel + offset, 27 שכבות קונבולוציה, ו-Dense 521.
3. **מאמת** מימוש numpy עצמאי (אותו אלגוריתם שה-JS מממש: FFT במקום מכפלת DFT, SAME padding, ReLU) מול
   מפרש ה-TFLite על כמה אורכי קלט - אם הסטייה גדולה, לא כותב כלום.
4. כותב server/web/models/yamnet.bin (float32 little-endian) + yamnet.json (מניפסט: היסטים, צורות, פרמטרי frontend).
5. כותב tests/web_e2e/fixtures/yamnet_parity.json: גלי קול סינתטיים קבועים -> הפלט של TFLite (scores/embeddings/log-mel),
   כדי שבדיקת ה-JS תשווה את המימוש שלה לפלט של Python/TFLite.

הרצה (סביבת המחקר - לא .venv של האפליקציה):
    research_data\\venv_models\\Scripts\\python.exe tools\\build_yamnet_web.py
דורש: numpy, ai_edge_litert.
"""
import base64
import hashlib
import json
import math
import os
import sys
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "research", "models", "yamnet", "yamnet_float_embeddings.tflite")
OUT_DIR = os.path.join(ROOT, "server", "web", "models")
FIX_DIR = os.path.join(ROOT, "tests", "web_e2e", "fixtures")

SR = 16000
WIN, HOP, NFFT = 400, 160, 512
MIN_SAMPLES, HOP_SAMPLES = 15600, 7680       # 0.96+0.025-0.010 s ; 0.48 s
PATCH, PATCH_HOP = 96, 48


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a, dtype="<f4").tobytes()).decode("ascii")


def interpreter(n):
    warnings.filterwarnings("ignore")
    from ai_edge_litert.interpreter import Interpreter, OpResolverType
    it = Interpreter(model_path=SRC, experimental_preserve_all_tensors=True,
                     experimental_op_resolver_type=OpResolverType.BUILTIN_WITHOUT_DEFAULT_DELEGATES)
    it.resize_tensor_input(it.get_input_details()[0]["index"], [n])
    it.allocate_tensors()
    return it


def run_tflite(x):
    it = interpreter(len(x))
    it.set_tensor(it.get_input_details()[0]["index"], np.asarray(x, np.float32))
    it.invoke()
    o = it.get_output_details()
    return it.get_tensor(o[0]["index"]).copy(), it.get_tensor(o[1]["index"]).copy(), it.get_tensor(o[2]["index"]).copy()


def extract():
    """-> dict of numpy weights + layer list, found by graph structure."""
    it = interpreter(24000)
    it.set_tensor(0, np.zeros(24000, np.float32))
    it.invoke()
    ops = it._get_ops_details()
    produced = {i for o in ops for i in o["outputs"]}
    const = lambda i: i >= 0 and i not in produced and i != 0  # noqa: E731
    T = it.get_tensor
    hann = mel_w = mel_b = cls_w = cls_b = None
    layers = []
    for o in ops:
        name, ins = o["op_name"], o["inputs"]
        if name == "MUL":
            c = [i for i in ins if const(i) and tuple(T(i).shape) == (1, WIN)]
            if c:
                hann = T(c[0]).reshape(-1)
        elif name == "FULLY_CONNECTED" and const(ins[1]):
            w = T(ins[1])
            if w.shape == (64, NFFT // 2 + 1):
                mel_w, mel_b = w, T(ins[2])
            elif w.shape == (521, 1024):
                cls_w, cls_b = w, T(ins[2])
        elif name in ("CONV_2D", "DEPTHWISE_CONV_2D"):
            x_in, x_out = T(ins[0]), T(o["outputs"][0])
            stride = x_in.shape[1] // x_out.shape[1]
            w, b = T(ins[1]), T(ins[2])
            if name == "DEPTHWISE_CONV_2D":
                kind = "dw"
            elif w.shape[1] == 1 and w.shape[2] == 1:
                kind = "pw"
            else:
                kind = "conv"
            layers.append({"op": kind, "stride": int(stride), "w": w.copy(), "b": b.copy()})
    assert hann is not None and mel_w is not None and cls_w is not None, "graph structure changed"
    assert len(layers) == 27 and layers[0]["op"] == "conv", [l["op"] for l in layers]
    return {"hann": hann.astype(np.float32), "mel_w": mel_w.astype(np.float32), "mel_b": mel_b.astype(np.float32),
            "cls_w": cls_w.astype(np.float32), "cls_b": cls_b.astype(np.float32), "layers": layers}


# ----------------------------------------------------------------- reference implementation (same algorithm as JS)
def pad_waveform(x):
    n = len(x)
    pad = max(0, MIN_SAMPLES - n)
    n = max(n, MIN_SAMPLES)
    after = n - MIN_SAMPLES
    hops = math.ceil(after / HOP_SAMPLES)
    pad += HOP_SAMPLES * hops - after
    return np.pad(np.asarray(x, np.float32), (0, pad))


def log_mel(x, W):
    xp = pad_waveform(x)
    nf = 1 + (len(xp) - WIN) // HOP
    fr = np.stack([xp[i * HOP:i * HOP + WIN] for i in range(nf)]) * W["hann"]
    mag = np.abs(np.fft.rfft(fr, NFFT, axis=1)).astype(np.float32)
    return np.log(mag @ W["mel_w"].T + W["mel_b"])


def _same(a, s):
    H, Wd, _ = a.shape
    oh, ow = -(-H // s), -(-Wd // s)
    ph, pw = max((oh - 1) * s + 3 - H, 0), max((ow - 1) * s + 3 - Wd, 0)
    return np.pad(a, ((ph // 2, ph - ph // 2), (pw // 2, pw - pw // 2), (0, 0))), oh, ow


def network(patch, W):
    a = patch[:, :, None].astype(np.float32)
    for L in W["layers"]:
        w, b, s = L["w"], L["b"], L["stride"]
        if L["op"] == "pw":
            a = a @ w[:, 0, 0, :].T + b
        else:
            ap, oh, ow = _same(a, s)
            out = np.zeros((oh, ow, w.shape[0] if L["op"] == "conv" else a.shape[2]), np.float32)
            for ky in range(3):
                for kx in range(3):
                    sl = ap[ky:ky + s * oh:s, kx:kx + s * ow:s, :]
                    out += sl @ w[:, ky, kx, :].T if L["op"] == "conv" else sl * w[0, ky, kx, :]
            a = out + b
        a = np.maximum(a, 0)
    return a.mean(axis=(0, 1))


def reference(x, W):
    lm = log_mel(x, W)
    n = 1 + (len(lm) - PATCH) // PATCH_HOP
    emb = np.stack([network(lm[i * PATCH_HOP:i * PATCH_HOP + PATCH], W) for i in range(n)])
    scores = 1.0 / (1.0 + np.exp(-(emb @ W["cls_w"].T + W["cls_b"])))
    return scores, emb, lm


def waves():
    """גלים סינתטיים קבועים (נשמרים בקובץ - ה-JS לא צריך לשחזר את ה-RNG)."""
    rng = np.random.default_rng(20261115)
    t = np.arange(24000) / SR
    tone = 0.25 * np.sin(2 * np.pi * 880 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 6 * t))
    clicks = np.zeros(24000)
    for c in (3000, 11000, 19000):
        clicks[c:c + 800] += np.exp(-np.arange(800) / 120.0) * rng.standard_normal(800) * 0.8
    mix = (tone + clicks + rng.standard_normal(24000) * 0.02).astype(np.float32)
    sweep = (0.3 * np.sin(2 * np.pi * (500 * t[:16000] + 900 * t[:16000] ** 2))).astype(np.float32)
    short = (rng.standard_normal(8000) * 0.1).astype(np.float32)
    return {"mix_1p5s": mix, "sweep_1s": sweep, "noise_0p5s": short}


def main():
    if not os.path.isfile(SRC):
        sys.exit(f"missing {SRC}")
    W = extract()
    # 1) the reference must equal TFLite before anything is written
    worst = 0.0
    for n in (8000, 16000, 24000, 40000):
        x = (np.random.default_rng(n).standard_normal(n) * 0.1).astype(np.float32)
        s_t, e_t, lm_t = run_tflite(x)
        s_r, e_r, lm_r = reference(x, W)
        d = max(float(np.abs(s_t - s_r).max()), float(np.abs(e_t - e_r).max() / max(1e-6, np.abs(e_t).max())),
                float(np.abs(lm_t - lm_r).max()))
        worst = max(worst, d)
        print(f"  verify n={n}: frames={len(e_t)} max diff {d:.2e}")
    if worst > 1e-4:
        sys.exit(f"reference != TFLite (max diff {worst}) - nothing written")

    # 2) weights file + manifest
    os.makedirs(OUT_DIR, exist_ok=True)
    chunks, off = [], 0

    def put(a):
        nonlocal off
        a = np.ascontiguousarray(a, dtype="<f4").reshape(-1)
        chunks.append(a)
        rec = [off, int(a.size)]
        off += int(a.size)
        return rec
    man = {"model": "YAMNet (MobileNet-v1, AudioSet 521 classes)", "license": "Apache-2.0 (Google, tensorflow/models)",
           "source": os.path.relpath(SRC, ROOT).replace("\\", "/"),
           "source_sha256": hashlib.sha256(open(SRC, "rb").read()).hexdigest(),
           "generated_by": "tools/build_yamnet_web.py",
           "verified_max_diff_vs_tflite": worst,
           "frontend": {"sample_rate": SR, "win": WIN, "hop": HOP, "nfft": NFFT, "mel_bands": 64,
                        "min_samples": MIN_SAMPLES, "hop_samples": HOP_SAMPLES, "patch": PATCH, "patch_hop": PATCH_HOP},
           "hann": put(W["hann"]), "mel_w": put(W["mel_w"]), "mel_b": put(W["mel_b"]), "layers": []}
    for L in W["layers"]:
        w = L["w"]
        if L["op"] == "pw":
            w = w[:, 0, 0, :]                   # [out, in]
        elif L["op"] == "dw":
            w = w[0]                            # [3, 3, C]
        man["layers"].append({"op": L["op"], "stride": L["stride"], "shape": list(w.shape), "w": put(w), "b": put(L["b"])})
    man["cls_w"], man["cls_b"] = put(W["cls_w"]), put(W["cls_b"])
    blob = np.concatenate(chunks).astype("<f4").tobytes()
    man["bin_bytes"] = len(blob)
    man["bin_sha256"] = hashlib.sha256(blob).hexdigest()
    with open(os.path.join(OUT_DIR, "yamnet.bin"), "wb") as fh:
        fh.write(blob)
    with open(os.path.join(OUT_DIR, "yamnet.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, ensure_ascii=False, indent=1)
    print(f"wrote yamnet.bin ({len(blob) / 1e6:.2f} MB) + yamnet.json")

    # 3) parity fixture: TFLite outputs for fixed synthetic waves
    os.makedirs(FIX_DIR, exist_ok=True)
    fx = {"generated_by": "tools/build_yamnet_web.py", "source_sha256": man["source_sha256"], "cases": []}
    for name, x in waves().items():
        s, e, lm = run_tflite(x)
        fx["cases"].append({"name": name, "n": len(x), "wave": b64(x), "frames": int(len(e)),
                            "scores": b64(s), "emb": b64(e), "logmel": b64(lm), "logmel_frames": int(len(lm))})
    with open(os.path.join(FIX_DIR, "yamnet_parity.json"), "w", encoding="utf-8") as fh:
        json.dump(fx, fh)
    print("wrote tests/web_e2e/fixtures/yamnet_parity.json")


if __name__ == "__main__":
    main()

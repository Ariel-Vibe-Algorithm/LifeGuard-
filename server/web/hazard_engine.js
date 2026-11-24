// hazard_engine.js - the on-device audio hazard detector of the PWA, in plain JavaScript (no WebAssembly, so the
// strict CSP stays `script-src 'self'`). A port of the Windows engine - same models, same math:
//   research_tools/yamnet_detector.py (to_16k = scipy resample_poly, framing 0.96 s / hop 0.48 s, context_features,
//   smooth_scores), YAMNet itself (MobileNet-v1, weights from tools/build_yamnet_web.py), hazard_features.py
//   (stage-1 vector, spectral_features, curated classes), audio_intel.py (analyze, _category_frames,
//   _v2_window_scores, quality), acoustic_ai_module.py (distress head).
// Parity with Python is asserted by tests/test_pwa_hazard_parity.py (vectors from tools/build_hazard_web.py and
// tools/build_yamnet_web.py). Runs in a Web Worker in the app and directly in Node for the parity test.
// Nothing here stores or sends audio: it returns detection metadata only.

export const VERSION = 'audio_intel_v1';           // audio_intel.VERSION
export const SR = 16000;
export const WINDOW_S = 1.5;                        // hazard_features.WINDOW_S / hazard_monitor.WINDOW_S
export const WINDOW = SR * WINDOW_S;
const NEAR_THRESHOLD_FRACTION = 0.1;
const DIGITAL_SILENCE_PEAK = 1e-6;
const CLIP_LEVEL = 0.999;
const CLIP_RATIO_DEGRADED = 0.01;
const MIN_SECONDS = 0.96;
const HOP_SECONDS = 7680 / SR;                      // yamnet_detector.HOP_SECONDS
const DANGER_REASON = 'לא ניתן לאמוד הסתברות לסכנה מצליל בלבד: אין נתוני שטח מאומתים, '
  + 'וירי / זיקוקים / סרט נשמעים דומה. זיהוי צליל ≠ סכנה.';

// ------------------------------------------------------------------ small numeric helpers
export function b64f32(s) {
  const bin = atob(s);
  const u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return new Float32Array(u8.buffer);
}

/** Python round(x, n) for display values (ties are practically never exact in binary floats). */
export function pyRound(x, n) {
  if (x == null || !Number.isFinite(x)) return x;
  return Number(x.toFixed(n));
}

export function sigmoid(z) {
  z = Math.max(-60, Math.min(60, Number(z)));
  return 1 / (1 + Math.exp(-z));
}

function argmax(a, from = 0, to = a.length) {
  let bi = from, bv = a[from];
  for (let i = from + 1; i < to; i++) if (a[i] > bv) { bv = a[i]; bi = i; }
  return bi;
}

function mean(a) { let s = 0; for (let i = 0; i < a.length; i++) s += a[i]; return a.length ? s / a.length : NaN; }
function std(a) { const m = mean(a); let s = 0; for (let i = 0; i < a.length; i++) s += (a[i] - m) ** 2; return Math.sqrt(s / a.length); }

/** numpy.percentile (linear). */
function percentile(a, q) {
  const s = Float64Array.from(a).sort();
  const pos = (q / 100) * (s.length - 1);
  const lo = Math.floor(pos), hi = Math.ceil(pos);
  return s[lo] + (s[hi] - s[lo]) * (pos - lo);
}
const median = (a) => percentile(a, 50);

// ------------------------------------------------------------------ FFT (radix-2) + plain DFT for other sizes
const FFT_CACHE = new Map();
function fftTables(n) {
  let t = FFT_CACHE.get(n);
  if (!t) {
    const rev = new Uint32Array(n);
    const bits = Math.log2(n);
    for (let i = 0; i < n; i++) { let r = 0; for (let b = 0; b < bits; b++) r |= ((i >> b) & 1) << (bits - 1 - b); rev[i] = r; }
    const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos(-2 * Math.PI * i / n); sin[i] = Math.sin(-2 * Math.PI * i / n); }
    t = { rev, cos, sin, re: new Float64Array(n), im: new Float64Array(n) };
    FFT_CACHE.set(n, t);
  }
  return t;
}

/** |rfft(frame, n)| -> out[0..n/2] (frame shorter than n is zero-padded at the end). n: power of two. */
function rfftMag(frame, n, out) {
  const { rev, cos, sin, re, im } = fftTables(n);
  const L = Math.min(frame.length, n);
  for (let i = 0; i < n; i++) { const j = rev[i]; re[j] = i < L ? frame[i] : 0; im[j] = 0; }
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1, step = n / size;
    for (let s = 0; s < n; s += size) {
      for (let k = 0; k < half; k++) {
        const wr = cos[k * step], wi = sin[k * step];
        const a = s + k, b = a + half;
        const tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
      }
    }
  }
  for (let k = 0; k <= n / 2; k++) out[k] = Math.hypot(re[k], im[k]);
  return out;
}

/** |rfft(x)|^2 for any length (numpy.fft.rfft): direct DFT - only used on short envelopes. */
function rfftPowerAny(x) {
  const n = x.length, nb = Math.floor(n / 2) + 1;
  const out = new Float64Array(nb);
  for (let k = 0; k < nb; k++) {
    let r = 0, i = 0;
    for (let t = 0; t < n; t++) { const ang = -2 * Math.PI * ((k * t) % n) / n; r += x[t] * Math.cos(ang); i += x[t] * Math.sin(ang); }
    out[k] = r * r + i * i;
  }
  return out;
}

// ------------------------------------------------------------------ resampling = scipy.signal.resample_poly
function gcd(a, b) { while (b) [a, b] = [b, a % b]; return a; }

function besselI0(x) {
  let sum = 1, term = 1;
  for (let k = 1; k < 200; k++) { term *= (x / (2 * k)) ** 2; sum += term; if (term < sum * 1e-17) break; }
  return sum;
}

const RESAMPLE_CACHE = new Map();
function resampleFilter(up, down) {
  const key = up + '/' + down;
  let f = RESAMPLE_CACHE.get(key);
  if (f) return f;
  const maxRate = Math.max(up, down);
  const fc = 1 / maxRate, halfLen = 10 * maxRate, numtaps = 2 * halfLen + 1;
  // firwin(numtaps, fc, window=('kaiser', 5.0)) - float64, then cast to the input dtype (float32), then *= up
  const alpha = 0.5 * (numtaps - 1), beta = 5.0, i0b = besselI0(beta);
  const h = new Float64Array(numtaps);
  let s = 0;
  for (let i = 0; i < numtaps; i++) {
    const m = i - alpha;
    const x = fc * m;
    const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
    const r = (i - alpha) / alpha;
    const win = besselI0(beta * Math.sqrt(Math.max(0, 1 - r * r))) / i0b;
    h[i] = fc * sinc * win;
    s += h[i];
  }
  for (let i = 0; i < numtaps; i++) h[i] = Math.fround(Math.fround(h[i] / s) * up);
  const nPrePad = down - (halfLen % down);
  const nPreRemove = Math.floor((halfLen + nPrePad) / down);
  f = { h, halfLen, nPrePad, nPreRemove };
  RESAMPLE_CACHE.set(key, f);
  return f;
}

function outputLen(lenH, inLen, up, down) {
  const inLenCopy = inLen + Math.floor((lenH + (((-lenH % up) + up) % up)) / up) - 1;
  const nt = inLenCopy * up;
  return Math.floor(nt / down) + (nt % down > 0 ? 1 : 0);
}

/** Polyphase resampling to 16 kHz exactly like research_tools/yamnet_detector.to_16k (scipy resample_poly). */
export function to16k(x, sr) {
  sr = Math.round(sr);
  if (sr === SR || x.length === 0) return Float32Array.from(x);
  const g = gcd(sr, SR);
  const up = SR / g, down = sr / g;
  if (up === 1 && down === 1) return Float32Array.from(x);
  const nIn = x.length;
  const nOut = Math.floor(nIn * up / down) + ((nIn * up) % down ? 1 : 0);
  const { h: h0, nPrePad, nPreRemove } = resampleFilter(up, down);
  let nPostPad = 0;
  while (outputLen(h0.length + nPrePad + nPostPad, nIn, up, down) < nOut + nPreRemove) nPostPad += 1;
  const lenH = nPrePad + h0.length + nPostPad;
  const h = new Float64Array(lenH);
  h.set(h0, nPrePad);
  const y = new Float32Array(nOut);
  for (let k = 0; k < nOut; k++) {
    const m0 = (k + nPreRemove) * down;         // index in the upsampled signal
    let acc = 0;
    // taps j with (m0 - j) % up == 0 and 0 <= (m0 - j) / up < nIn
    let j = m0 % up;
    let xi = (m0 - j) / up;
    if (xi >= nIn) { const skip = xi - (nIn - 1); j += skip * up; xi -= skip; }
    for (; j < lenH && xi >= 0; j += up, xi--) acc += h[j] * x[xi];
    y[k] = acc;
  }
  return y;
}

// ------------------------------------------------------------------ input quality (audio_intel.quality)
export function quality(audio, sr) {
  if (!audio || !sr) return { status: 'unavailable', reason: 'אין הקלטה מהמיקרופון' };
  const n = audio.length;
  if (!n) return { status: 'unavailable', reason: 'ההקלטה ריקה' };
  let peak = 0, ss = 0, clipN = 0;
  for (let i = 0; i < n; i++) {
    const a = Math.abs(audio[i]);
    if (a > peak) peak = a;
    ss += audio[i] * audio[i];
    if (a >= CLIP_LEVEL) clipN++;
  }
  const rms = Math.sqrt(ss / n), seconds = n / sr, clip = clipN / n;
  const q = { seconds: pyRound(seconds, 2), rms: pyRound(rms, 5), peak: pyRound(peak, 5), clip_ratio: pyRound(clip, 4) };
  if (peak <= DIGITAL_SILENCE_PEAK) Object.assign(q, { status: 'unavailable', reason: 'המיקרופון החזיר שקט דיגיטלי מוחלט - כנראה מושתק, חסום או תקול' });
  else if (seconds < MIN_SECONDS) Object.assign(q, { status: 'degraded', reason: `הקלטה קצרה מדי (${seconds.toFixed(2)} ש') לזיהוי אירועים` });
  else if (clip > CLIP_RATIO_DEGRADED) Object.assign(q, { status: 'degraded', reason: `רוויה במיקרופון (${(clip * 100).toFixed(1)}% מהדגימות בתקרה) - צליל חזק מאוד; הסיווג פחות אמין` });
  else Object.assign(q, { status: 'ok', reason: '' });
  return q;
}

// ------------------------------------------------------------------ YAMNet (MobileNet-v1) forward pass
export class Yamnet {
  /** man: models/yamnet.json, buf: Float32Array of models/yamnet.bin */
  constructor(man, buf) {
    if (buf.length * 4 !== man.bin_bytes) throw new Error('yamnet.bin size mismatch');
    const t = ([off, n]) => buf.subarray(off, off + n);
    this.f = man.frontend;
    this.hann = t(man.hann); this.melW = t(man.mel_w); this.melB = t(man.mel_b);
    this.layers = man.layers.map((L) => ({ op: L.op, stride: L.stride, shape: L.shape, w: t(L.w), b: t(L.b) }));
    this.clsW = t(man.cls_w); this.clsB = t(man.cls_b);
    this.nClasses = this.clsB.length;
    this.embDim = this.layers[this.layers.length - 1].b.length;
  }

  static nFrames(n) { return 1 + Math.max(0, Math.ceil((n - 15600) / 7680)); }

  /** log-mel spectrogram (yamnet features.py, tflite_compatible): returns {data: Float32Array(frames*64), frames} */
  logMel(x) {
    const { min_samples: MINS, hop_samples: HOPS, win, hop, nfft, mel_bands: MB } = this.f;
    let n = x.length, pad = Math.max(0, MINS - n);
    n = Math.max(n, MINS);
    const after = n - MINS;
    pad += HOPS * Math.ceil(after / HOPS) - after;
    const total = x.length + pad;
    const nf = 1 + Math.floor((total - win) / hop);
    const nb = nfft / 2 + 1;
    const frame = new Float64Array(win), mag = new Float64Array(nb);
    const out = new Float32Array(nf * MB);
    for (let f = 0; f < nf; f++) {
      const s0 = f * hop;
      for (let i = 0; i < win; i++) { const k = s0 + i; frame[i] = (k < x.length ? x[k] : 0) * this.hann[i]; }
      rfftMag(frame, nfft, mag);
      for (let m = 0; m < MB; m++) {
        let acc = this.melB[m];
        const row = m * nb;
        for (let k = 0; k < nb; k++) acc += mag[k] * this.melW[row + k];
        out[f * MB + m] = Math.log(acc);
      }
    }
    return { data: out, frames: nf };
  }

  /** x16: Float32Array at 16 kHz -> {n, scores: Float32Array(n*521), emb: Float32Array(n*1024)} */
  analyze(x16) {
    const lm = this.logMel(x16);
    const { patch: P, patch_hop: PH, mel_bands: MB } = this.f;
    const n = 1 + Math.floor((lm.frames - P) / PH);
    const scores = new Float32Array(n * this.nClasses), emb = new Float32Array(n * this.embDim);
    for (let p = 0; p < n; p++) {
      const e = this.forward(lm.data.subarray(p * PH * MB, (p * PH + P) * MB), P, MB);
      emb.set(e, p * this.embDim);
      for (let c = 0; c < this.nClasses; c++) {
        let acc = this.clsB[c];
        const row = c * this.embDim;
        for (let i = 0; i < this.embDim; i++) acc += e[i] * this.clsW[row + i];
        scores[p * this.nClasses + c] = 1 / (1 + Math.exp(-acc));
      }
    }
    return { n, scores, emb };
  }

  forward(patch, H0, W0) {
    let a = patch, H = H0, W = W0, C = 1;
    for (const L of this.layers) {
      if (L.op === 'pw') { a = pointwise(a, H * W, C, L.w, L.b); C = L.b.length; continue; }
      const s = L.stride;
      const oh = Math.ceil(H / s), ow = Math.ceil(W / s);
      const pt = Math.floor(Math.max((oh - 1) * s + 3 - H, 0) / 2), pl = Math.floor(Math.max((ow - 1) * s + 3 - W, 0) / 2);
      a = L.op === 'conv' ? conv3(a, H, W, C, L.w, L.b, s, oh, ow, pt, pl) : depthwise(a, H, W, C, L.w, L.b, s, oh, ow, pt, pl);
      if (L.op === 'conv') C = L.b.length;
      H = oh; W = ow;
    }
    const out = new Float32Array(C);
    const px = H * W;
    for (let c = 0; c < C; c++) { let s = 0; for (let p = 0; p < px; p++) s += a[p * C + c]; out[c] = s / px; }
    return out;
  }
}

function conv3(a, H, W, C, w, b, s, oh, ow, pt, pl) {       // full 3x3 conv, weights [O,3,3,C], ReLU
  const O = b.length, out = new Float32Array(oh * ow * O);
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      const o0 = (y * ow + x) * O;
      for (let o = 0; o < O; o++) {
        let acc = b[o];
        for (let ky = 0; ky < 3; ky++) {
          const iy = y * s + ky - pt;
          if (iy < 0 || iy >= H) continue;
          for (let kx = 0; kx < 3; kx++) {
            const ix = x * s + kx - pl;
            if (ix < 0 || ix >= W) continue;
            const ai = (iy * W + ix) * C, wi = ((o * 3 + ky) * 3 + kx) * C;
            for (let c = 0; c < C; c++) acc += a[ai + c] * w[wi + c];
          }
        }
        out[o0 + o] = acc > 0 ? acc : 0;
      }
    }
  }
  return out;
}

function depthwise(a, H, W, C, w, b, s, oh, ow, pt, pl) {    // weights [3,3,C], ReLU
  const out = new Float32Array(oh * ow * C);
  const acc = new Float32Array(C);
  for (let y = 0; y < oh; y++) {
    for (let x = 0; x < ow; x++) {
      acc.set(b);
      for (let ky = 0; ky < 3; ky++) {
        const iy = y * s + ky - pt;
        if (iy < 0 || iy >= H) continue;
        for (let kx = 0; kx < 3; kx++) {
          const ix = x * s + kx - pl;
          if (ix < 0 || ix >= W) continue;
          const ai = (iy * W + ix) * C, wi = (ky * 3 + kx) * C;
          for (let c = 0; c < C; c++) acc[c] += a[ai + c] * w[wi + c];
        }
      }
      const o0 = (y * ow + x) * C;
      for (let c = 0; c < C; c++) out[o0 + c] = acc[c] > 0 ? acc[c] : 0;
    }
  }
  return out;
}

function pointwise(a, P, I, w, b) {                         // 1x1 conv, weights [O,I], ReLU
  // the hot loop (~95% of the work): 4 pixels share every weight load (register blocking)
  const O = b.length, out = new Float32Array(P * O);
  let p = 0;
  for (; p + 3 < P; p += 4) {
    const a0 = p * I, a1 = a0 + I, a2 = a1 + I, a3 = a2 + I;
    for (let o = 0; o < O; o++) {
      const wi = o * I;
      let s0 = 0, s1 = 0, s2 = 0, s3 = 0;
      for (let i = 0; i < I; i++) {
        const wv = w[wi + i];
        s0 += a[a0 + i] * wv; s1 += a[a1 + i] * wv; s2 += a[a2 + i] * wv; s3 += a[a3 + i] * wv;
      }
      const bo = b[o];
      let v = bo + s0; out[p * O + o] = v > 0 ? v : 0;
      v = bo + s1; out[(p + 1) * O + o] = v > 0 ? v : 0;
      v = bo + s2; out[(p + 2) * O + o] = v > 0 ? v : 0;
      v = bo + s3; out[(p + 3) * O + o] = v > 0 ? v : 0;
    }
  }
  for (; p < P; p++) {
    const ai = p * I;
    for (let o = 0; o < O; o++) {
      const wi = o * I;
      let s = 0;
      for (let i = 0; i < I; i++) s += a[ai + i] * w[wi + i];
      const v = b[o] + s;
      out[p * O + o] = v > 0 ? v : 0;
    }
  }
  return out;
}

// ------------------------------------------------------------------ hazard_features.py
const HANN_SYM = new Map();
function hanning(n) {                                        // numpy.hanning (symmetric), float32
  let w = HANN_SYM.get(n);
  if (!w) { w = new Float32Array(n); for (let i = 0; i < n; i++) w[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (n - 1)); HANN_SYM.set(n, w); }
  return w;
}

function stftMag(x, nfft) {                                  // -> {S: Float64Array[frames*nb], frames, nb}
  const hop = nfft / 4;
  const len = Math.max(x.length, nfft);
  const frames = 1 + Math.floor((len - nfft) / hop);
  const nb = nfft / 2 + 1, w = hanning(nfft);
  const S = new Float64Array(frames * nb), fr = new Float64Array(nfft), mag = new Float64Array(nb);
  for (let f = 0; f < frames; f++) {
    for (let i = 0; i < nfft; i++) { const k = f * hop + i; fr[i] = (k < x.length ? x[k] : 0) * w[i]; }
    rfftMag(fr, nfft, mag);
    for (let k = 0; k < nb; k++) S[f * nb + k] = Math.fround(mag[k]);
  }
  return { S, frames, nb };
}

/** hazard_features.spectral_features: 29 multi-resolution features of a 16 kHz window. */
export function spectralFeatures(x0) {
  const n = x0.length;
  const m0 = n ? mean(x0) : 0;
  const x = new Float64Array(n);
  for (let i = 0; i < n; i++) x[i] = Math.fround(x0[i] - m0);
  const out = [];
  const eps = 1e-8, nyq = SR / 2;
  let flux1024 = null, S1024 = null;
  for (const nfft of [256, 1024, 4096]) {
    const { S, frames, nb } = stftMag(x, nfft);
    if (nfft === 1024) S1024 = { S, frames, nb };
    const freqs = new Float64Array(nb);
    for (let k = 0; k < nb; k++) freqs[k] = nyq * k / (nb - 1);
    const tot = new Float64Array(frames), cent = new Float64Array(frames), flat = new Float64Array(frames);
    let hiE = 0, loE = 0, totSum = 0;
    for (let f = 0; f < frames; f++) {
      let t = 0, c = 0, lg = 0, mp = 0;
      for (let k = 0; k < nb; k++) {
        const P = S[f * nb + k] ** 2;
        t += P; c += P * freqs[k]; lg += Math.log(P + eps); mp += P;
        if (freqs[k] >= 4000) hiE += P;
        if (freqs[k] < 300) loE += P;
      }
      tot[f] = t + eps;
      totSum += tot[f];
      cent[f] = c / tot[f] / nyq;
      flat[f] = Math.exp(lg / nb) / (mp / nb + eps);
    }
    let flux;
    if (frames > 1) {
      flux = new Float64Array(frames - 1);
      for (let f = 1; f < frames; f++) {
        let s = 0;
        for (let k = 0; k < nb; k++) { const d = Math.log1p(S[f * nb + k]) - Math.log1p(S[(f - 1) * nb + k]); if (d > 0) s += d; }
        flux[f - 1] = s;
      }
    } else flux = new Float64Array(1);
    if (nfft === 1024) flux1024 = flux;
    const thr = percentile(tot, 50);
    const cl = [], fl = [];
    for (let f = 0; f < frames; f++) if (tot[f] >= thr) { cl.push(cent[f]); fl.push(flat[f]); }
    let fmax = -Infinity;
    for (const v of flux) if (v > fmax) fmax = v;
    out.push(Math.log1p(fmax), flux.length ? fmax / (median(flux) + eps) : 0, hiE / totSum, loE / totSum, mean(cl), std(cl), mean(fl));
  }
  // time: crest, envelope kurtosis, decay after the peak
  const envHop = 160;
  let ss = 0, pk = 0;
  const x2 = new Float64Array(n);
  for (let i = 0; i < n; i++) { x2[i] = x[i] * x[i]; ss += x2[i]; const a = Math.abs(x[i]); if (a > pk) pk = a; }
  // np.convolve(x**2, ones(160)/160, 'same')[::160]: 'same' starts at offset (M-1)//2 = 79 of the full convolution
  const nEnv = Math.ceil(n / envHop);
  const env = new Float64Array(nEnv);
  for (let e = 0; e < nEnv; e++) {
    const i = e * envHop;
    const lo = Math.max(0, i - 80), hi = Math.min(n - 1, i + 79);
    let s = 0;
    for (let k = lo; k <= hi; k++) s += x2[k] / 160;
    env[e] = Math.sqrt(s + eps);
  }
  const rms = Math.sqrt(ss / n + eps);
  const crest = 20 * Math.log10((pk + eps) / rms);
  const em = mean(env), es = std(env);
  let kurt = 0;
  for (let i = 0; i < nEnv; i++) kurt += ((env[i] - em) / (es + eps)) ** 4;
  kurt /= nEnv;
  const ip = argmax(env);
  let decay = (nEnv - ip) * envHop / SR;
  for (let k = ip; k < nEnv; k++) if (env[k] < env[ip] * 0.1) { decay = (k - ip) * envHop / SR; break; }
  // periodicity of onsets (music / drums) at 0.25-1.0 s
  let per = 0;
  if (flux1024 && flux1024.length > 8) {
    const L = flux1024.length, fm = mean(flux1024);
    const f = Float64Array.from(flux1024, (v) => v - fm);
    const ac = new Float64Array(L);
    for (let k = 0; k < L; k++) { let s = 0; for (let i = 0; i + k < L; i++) s += f[i + k] * f[i]; ac[k] = s; }
    const a0 = ac[0] + eps;
    const lo = Math.floor(0.25 * SR / 256), hi = Math.min(L - 1, Math.floor(1.0 * SR / 256));
    if (hi > lo) { per = -Infinity; for (let k = lo; k <= hi; k++) per = Math.max(per, ac[k] / a0); }
  }
  // 2-20 Hz envelope modulation (beeping alarm, siren) and tone stability (horn)
  const emc = Float64Array.from(env, (v) => v - em);
  const spec = rfftPowerAny(emc);
  let num = 0, den = 0;
  for (let k = 0; k < spec.length; k++) {
    const mf = k / (nEnv * (envHop / SR));
    if (mf >= 2 && mf <= 20) num += spec[k];
    if (mf > 0.5) den += spec[k];
  }
  const mod = num / (den + eps);
  const { S, frames, nb } = S1024;
  const band = [];
  for (let k = 0; k < nb; k++) { const fr = nyq * k / (nb - 1); if (fr >= 300 && fr <= 3000) band.push(k); }
  const eFr = new Float64Array(frames);
  for (let f = 0; f < frames; f++) { let s = 0; for (let k = 0; k < nb; k++) s += S[f * nb + k] ** 2; eFr[f] = s; }
  const lthr = percentile(eFr, 60);
  const peaks = [], tonals = [];
  for (let f = 0; f < frames; f++) {
    if (!(eFr[f] >= lthr)) continue;
    let bi = 0, bv = -Infinity;
    const p2 = [];
    for (let j = 0; j < band.length; j++) {
      const v = S[f * nb + band[j]];
      if (v > bv) { bv = v; bi = j; }
      p2.push(v * v);
    }
    peaks.push(nyq * band[bi] / (nb - 1));
    p2.sort((a, b) => a - b);
    let tot = 0;
    for (const v of p2) tot += v;
    tonals.push((p2[p2.length - 1] + p2[p2.length - 2]) / (tot + eps));
  }
  const pitchStd = peaks.length ? std(peaks) / 1000 : 0;
  const tonal = tonals.length ? mean(tonals) : 0;
  const level = 20 * Math.log10(rms);
  out.push(crest, Math.log1p(kurt), decay, per, mod, pitchStd, tonal, level / 60);
  return Float32Array.from(out);
}

/** hazard_features.windows: 1.5 s windows, hop 0.75 s; a short input is zero-padded to one window. */
export function windows16(x16) {
  if (x16.length <= WINDOW) {
    if (x16.length === WINDOW) return [x16];
    const w = new Float32Array(WINDOW);
    w.set(x16);
    return [w];
  }
  const hop = Math.floor(0.75 * SR);
  const starts = [];
  for (let s = 0; s <= x16.length - WINDOW; s += hop) starts.push(s);
  if (starts[starts.length - 1] + WINDOW < x16.length) starts.push(x16.length - WINDOW);
  return starts.map((s) => x16.subarray(s, s + WINDOW));
}

// ------------------------------------------------------------------ heads (models/hazard_heads.json)
function arr(o) { return o && o.b64 != null ? b64f32(o.b64) : null; }

function linHead(h) { return { mean: arr(h.mean), std: arr(h.std), w: arr(h.w), b: h.b }; }

/** ((X - mean) / std) @ w + b  (float32 standardisation as numpy does it). */
function headLogit(h, X, off = 0) {
  let acc = 0;
  const n = h.w.length;
  for (let i = 0; i < n; i++) acc += Math.fround(Math.fround(X[off + i] - h.mean[i]) / h.std[i]) * h.w[i];
  return acc + h.b;
}

export function loadHeads(json) {
  const v1 = json.v1, v2 = json.v2, d = json.distress;
  const H = { json, v1: { ...v1, heads: {} }, v2: v2 ? { ...v2, heads: {} } : null, distress: null, sequence: json.sequence || null };
  for (const [cat, c] of Object.entries(v1.categories)) if (c.head) H.v1.heads[cat] = linHead(c.head);
  if (v2) for (const [cat, h] of Object.entries(v2.heads)) H.v2.heads[cat] = { s1: linHead(h.s1), s2: h.s2 ? linHead(h.s2) : null };
  if (d) H.distress = { ...d, head: linHead(d.head) };
  H.curated = v2 ? Int32Array.from(v2.curated_idx) : null;
  return H;
}

/** hazard_features.stage1_vector: [mean, max] of the window's embeddings (2048). */
export function stage1Vector(emb, n, D) {
  const v = new Float32Array(2 * D);
  for (let d = 0; d < D; d++) {
    let s = 0, m = -Infinity;
    for (let f = 0; f < n; f++) { const e = emb[f * D + d]; s += e; if (e > m) m = e; }
    v[d] = Math.fround(s / n); v[D + d] = m;
  }
  return v;
}

/** hazard_features.verifier_vector: [stage-1 logit, curated YAMNet max, curated mean, spectral features]. */
export function verifierVector(l1, scores, n, C, curated, x16, spec) {
  const K = curated.length;
  const v = new Float32Array(1 + 2 * K + 29);
  v[0] = l1;
  for (let j = 0; j < K; j++) {
    let s = 0, m = -Infinity;
    for (let f = 0; f < n; f++) { const x = scores[f * C + curated[j]]; s += x; if (x > m) m = x; }
    v[1 + j] = m; v[1 + K + j] = Math.fround(s / n);
  }
  v.set(spec || spectralFeatures(x16), 1 + 2 * K);
  return v;
}

/** audio_intel._v2_window_scores -> [final, threshold, detected, confidence] (+ logits for the parity test). */
export function v2WindowScores(H, cat, scores, emb, n, x16, spec) {
  const c = H.v2.categories[cat], hd = H.v2.heads[cat];
  const D = emb.length / n, C = scores.length / n;
  const l1 = headLogit(hd.s1, stage1Vector(emb, n, D));
  let final = l1, thr = c.t1, det = l1 >= c.t1, l2 = null;
  if (c.two_stage) {
    l2 = headLogit(hd.s2, verifierVector(l1, scores, n, C, H.curated, x16, spec));
    final = l2; thr = c.t2;
    det = det && l2 >= c.t2;
  }
  const [a, b] = c.platt;
  return { final, thr, det: !!det, conf: sigmoid(a * final + b), l1, l2 };
}

/** audio_intel._category_frames: per-frame score of a v1 category. */
export function categoryFrames(H, cat, scores, emb, n) {
  const c = H.v1.categories[cat];
  const out = new Float64Array(n);
  if (c.champion === 'head') {
    const h = H.v1.heads[cat], D = emb.length / n;
    for (let f = 0; f < n; f++) out[f] = headLogit(h, emb, f * D);
  } else {
    const C = scores.length / n;
    for (let f = 0; f < n; f++) { let m = -Infinity; for (const i of c.yam_idx) m = Math.max(m, scores[f * C + i]); out[f] = m; }
  }
  return out;
}

/** yamnet_detector.context_features: [frame, mean(+-1), max(+-1)] for every frame. */
export function contextFeatures(emb, n, D) {
  const out = new Float32Array(n * 3 * D);
  for (let f = 0; f < n; f++) {
    const p = Math.max(0, f - 1), q = Math.min(n - 1, f + 1);
    const o = f * 3 * D;
    for (let d = 0; d < D; d++) {
      const a = emb[p * D + d], b = emb[f * D + d], c = emb[q * D + d];
      out[o + d] = b;
      out[o + D + d] = Math.fround(Math.fround(Math.fround(a + b) + c) / 3);
      out[o + 2 * D + d] = Math.max(a, b, c);
    }
  }
  return out;
}

/** yamnet_detector.smooth_scores (forward moving average, edge padded). */
export function smoothScores(s, width = 2) {
  if (width <= 1 || s.length < 2) return Float64Array.from(s);
  const n = s.length, out = new Float64Array(n);
  for (let i = 0; i < n; i++) { let acc = 0; for (let k = 0; k < width; k++) acc += s[Math.min(n - 1, i + k)] / width; out[i] = acc; }
  return out;
}

/** acoustic_ai_module.analyze_embeddings: distress voice (scream / shout / crying) head. */
export function distressFromEmbeddings(H, emb, n) {
  const d = H.distress;
  if (!d || !n) return null;
  const D = emb.length / n;
  const X = d.context ? contextFeatures(emb, n, D) : emb;
  const dim = X.length / n;
  const raw = new Float64Array(n);
  for (let f = 0; f < n; f++) raw[f] = headLogit(d.head, X, f * dim);
  const s = smoothScores(raw, d.smooth || 1);
  const i = argmax(s);
  const score = s[i];
  return { score: pyRound(score, 3), detected: score >= d.threshold_logit, probability: pyRound(sigmoid(score), 4), at_seconds: pyRound(i * HOP_SECONDS, 2) };
}

function faAt(table, score) {
  let rate = null;
  for (const [thr, fa] of table) { if (score >= thr) rate = fa; else break; }
  return rate;
}

/**
 * audio_intel.analyze for one window, given YAMNet's output. audio/sr: the raw window (quality is measured on it);
 * yam: (x16) -> {n, scores, emb}. Returns the same report dict as Python (no embeddings, no audio).
 */
export function analyze(H, audio, sr, yam) {
  const q = quality(audio, sr);
  const v1 = H.v1, v2 = H.v2;
  const report = {
    engine: VERSION, model_version: v1.version, quality: q, events: [], suspected_unvalidated: [], top: [], distress: null,
    danger_probability: null, danger_probability_reason: DANGER_REASON,
  };
  if (q.status === 'unavailable') return report;
  const x16 = to16k(audio, sr);
  const { n, scores, emb } = yam(x16);
  if (!n) return report;
  report.distress = distressFromEmbeddings(H, emb, n);
  const v2s = {};
  if (v2) {
    // v2 works on 1.5 s windows (as in training). One window (the runtime case): YAMNet's output is reused;
    // longer input: every window separately, the strongest one counts
    const wins = windows16(x16);
    const perWin = wins.map((w) => {
      const y = wins.length === 1 ? { n, scores, emb } : yam(w);
      return { ...y, w, spec: spectralFeatures(w) };
    });
    for (const cat of Object.keys(v2.categories)) {
      let best = null;
      for (const p of perWin) {
        const r = v2WindowScores(H, cat, p.scores, p.emb, p.n, p.w, p.spec);
        if (!best || r.final - r.thr > best.final - best.thr) best = r;
      }
      v2s[cat] = best;
    }
    if (v2s.distress_voice) {
      const r = v2s.distress_voice;
      report.distress = { score: pyRound(r.final, 3), detected: r.det, probability: pyRound(r.conf, 4), at_seconds: 0.0, variant: 'hazard_v2' };
    }
    report.model_version = `${v1.version}+${v2.version}`;
  }
  const hop = v1.frame_hop_s || HOP_SECONDS;
  const rows = [];
  for (const [cat, c] of Object.entries(v1.categories)) {
    const r2 = v2s[cat];
    if (r2) {
      rows.push({
        category: cat, label_he: c.label_he, score: pyRound(r2.final, 4), threshold: pyRound(r2.thr, 4), detected: r2.det,
        at_seconds: 0.0, detection_confidence: pyRound(r2.conf, 4),
        confidence_note: 'מכויל על validation של v2 (FSD50K + SONYC + ירי אמיתי)', fa_per_hour_at_score: null,
        severity_prior: c.severity_prior, margin: pyRound(r2.final - r2.thr, 4), near_threshold: Math.abs(r2.final - r2.thr) <= 0.5,
        model: 'v2_' + (v2.categories[cat].two_stage ? 'two_stage' : 'stage1'), validated_on: 'research/hazard_v2_benchmark.md',
      });
      continue;
    }
    const frames = categoryFrames(H, cat, scores, emb, n);
    const i = argmax(frames);
    const s = frames[i], thr = c.threshold;
    const topThr = c.fa_table && c.fa_table.length ? c.fa_table[c.fa_table.length - 1][0] : thr;
    const span = Math.max(1e-6, Math.abs(topThr - thr));
    const [a, b] = c.platt;
    rows.push({
      category: cat, label_he: c.label_he, score: pyRound(s, 4), threshold: pyRound(thr, 4), detected: s >= thr,
      at_seconds: pyRound(i * hop, 2), detection_confidence: pyRound(sigmoid(a * s + b), 4),
      confidence_note: 'מכויל על FSD50K (שכיחות שם, לא בעולם)', fa_per_hour_at_score: faAt(c.fa_table || [], s),
      severity_prior: c.severity_prior, margin: pyRound(s - thr, 4), near_threshold: Math.abs(s - thr) <= NEAR_THRESHOLD_FRACTION * span,
      model: c.champion, validated_on: 'FSD50K eval',
    });
  }
  rows.sort((p, q2) => q2.margin - p.margin);
  report.category_scores = Object.fromEntries(rows.map((r) => [r.category, r.score]));
  report.events = rows.filter((r) => r.detected);
  report.top = rows.slice(0, 3);
  report.anomaly = null;          // models/audio_anomaly.npz is not shipped (did not pass its acceptance criteria)
  const C = scores.length / n;
  for (const [cat, u] of Object.entries(v1.unvalidated || {})) {
    let s = -Infinity;
    for (let f = 0; f < n; f++) for (const i of u.yam_idx) s = Math.max(s, scores[f * C + i]);
    if (s >= u.threshold) report.suspected_unvalidated.push({ category: cat, label_he: u.label_he, score: pyRound(s, 4), note: u.note });
  }
  return report;
}

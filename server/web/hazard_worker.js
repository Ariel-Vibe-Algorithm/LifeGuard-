// hazard_worker.js - module Web Worker: runs YAMNet + the hazard heads on 1.5 s windows off the main thread.
// The page fetches the model files (Cache Storage, after the user enabled listening) and transfers them here.
// In: {type:'init', yamnet: <manifest>, bin: ArrayBuffer, heads: <json>} | {type:'window', id, audio: Float32Array, sr}
//     | {type:'yamnet', id, audio: Float32Array} (test hook: raw YAMNet output)
// Out: {type:'ready'} | {type:'error', message} | {type:'report', id, report, ms} | {type:'yamnet', id, n, scores, emb}
// No audio leaves this worker; it keeps nothing between windows.
import { Yamnet, loadHeads, analyze } from './hazard_engine.js';

let yam = null;
let heads = null;

self.onmessage = (ev) => {
  const m = ev.data || {};
  try {
    if (m.type === 'init') {
      yam = new Yamnet(m.yamnet, new Float32Array(m.bin));
      heads = loadHeads(m.heads);
      self.postMessage({ type: 'ready', model_version: `${heads.v1.version}+${heads.v2 ? heads.v2.version : ''}` });
    } else if (m.type === 'window') {
      if (!yam) throw new Error('model not loaded');
      const t0 = performance.now();
      const report = analyze(heads, m.audio, m.sr, (x16) => yam.analyze(x16));
      self.postMessage({ type: 'report', id: m.id, report, ms: Math.round(performance.now() - t0) });
    } else if (m.type === 'yamnet') {
      if (!yam) throw new Error('model not loaded');
      const r = yam.analyze(m.audio);
      self.postMessage({ type: 'yamnet', id: m.id, n: r.n, scores: r.scores, emb: r.emb }, [r.scores.buffer, r.emb.buffer]);
    }
  } catch (e) {
    self.postMessage({ type: m.type === 'init' ? 'error' : 'report', id: m.id, error: String((e && e.message) || e), message: String((e && e.message) || e) });
  }
};

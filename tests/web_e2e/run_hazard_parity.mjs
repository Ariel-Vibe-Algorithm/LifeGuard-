// JS == Python parity for the PWA's AI hazard detector (server/web/hazard_engine.js + hazard_core.js).
// Vectors: fixtures/hazard_parity.json (tools/build_hazard_web.py - the real Python code: resample_poly,
// hazard_features.spectral_features, audio_intel.analyze, hazard_monitor.process, AlertManager, contacts_message)
// and fixtures/yamnet_parity.json (tools/build_yamnet_web.py - the TFLite interpreter's YAMNet output).
// Usage: node run_hazard_parity.mjs   -> prints "PARITY_SUMMARY {json}" on the last line; exit 0 = all equal.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const WEB = path.join(ROOT, 'server', 'web');
const E = await import(pathToFileURL(path.join(WEB, 'hazard_engine.js')).href);
const C = await import(pathToFileURL(path.join(WEB, 'hazard_core.js')).href);
const fx = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'hazard_parity.json'), 'utf8'));
const yfx = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'yamnet_parity.json'), 'utf8'));
const heads = E.loadHeads(JSON.parse(fs.readFileSync(path.join(WEB, 'models', 'hazard_heads.json'), 'utf8')));

const checks = [];
let failed = false;
function check(name, fn) {
  try {
    const info = fn();
    checks.push({ name, ok: true, ...(info ? { info } : {}) });
  } catch (e) {
    failed = true;
    checks.push({ name, ok: false, error: String(e && e.message || e) });
  }
}
const arr = (o) => E.b64f32(o.ref ? fx.blobs[o.ref] : o.b64);

function maxDiff(a, b) {
  if (a.length !== b.length) throw new Error(`length ${a.length} != ${b.length}`);
  let d = 0;
  for (let i = 0; i < a.length; i++) d = Math.max(d, Math.abs(a[i] - b[i]));
  return d;
}

/** deep compare: numbers within abs+rel tolerance, everything else exact. skip(path, py, js) -> true to ignore. */
function deepEq(py, js, where, { abs = 2e-4, rel = 1e-4, skip } = {}) {
  if (skip && skip(where, py, js)) return;
  if (typeof py === 'number' && typeof js === 'number') {
    if (Math.abs(py - js) > abs + rel * Math.abs(py)) throw new Error(`${where}: py ${py} != js ${js}`);
    return;
  }
  if (py === null || js === null || typeof py !== 'object' || typeof js !== 'object') {
    if (py !== js) throw new Error(`${where}: py ${JSON.stringify(py)} != js ${JSON.stringify(js)}`);
    return;
  }
  if (Array.isArray(py) !== Array.isArray(js)) throw new Error(`${where}: array vs object`);
  const keys = new Set([...Object.keys(py), ...Object.keys(js)]);
  for (const k of keys) {
    if (!(k in js)) throw new Error(`${where}.${k}: missing in JS`);
    if (!(k in py)) throw new Error(`${where}.${k}: extra in JS`);
    deepEq(py[k], js[k], `${where}.${k}`, { abs, rel, skip });
  }
}

// ---------------------------------------------------------------- constants and strings
check('constants: grouping / cooldown / escalation / rate limit / ranks / families / prefs defaults', () => {
  const k = fx.constants;
  deepEq(k.EVENT_GAP_S, C.EVENT_GAP_S, 'EVENT_GAP_S'); deepEq(k.COOLDOWN_DISMISS_S, C.COOLDOWN_DISMISS_S, 'COOLDOWN_DISMISS_S');
  deepEq(k.COOLDOWN_FALSE_S, C.COOLDOWN_FALSE_S, 'COOLDOWN_FALSE_S'); deepEq(k.COOLDOWN_ACTION_S, C.COOLDOWN_ACTION_S, 'COOLDOWN_ACTION_S');
  deepEq(k.MIN_GAP_ANY_S, C.MIN_GAP_ANY_S, 'MIN_GAP_ANY_S');
  for (const n of ['SEVERITY_RANK', 'MIN_SEVERITY_OPTIONS', 'FAMILY', 'FALSE_REASONS', 'RESPONSES', 'DEFAULTS', 'LABELS_EN', 'EXPLAIN_EN', 'REASONS_EN']) deepEq(k[n], C[n], n);
  return { event_gap_s: C.EVENT_GAP_S, cooldowns: [C.COOLDOWN_DISMISS_S, C.COOLDOWN_FALSE_S, C.COOLDOWN_ACTION_S], min_gap_s: C.MIN_GAP_ANY_S };
});

check('UI strings he/en identical to hazard_alerts.STRINGS', () => {
  let n = 0;
  for (const [lang, d] of Object.entries(fx.constants.STRINGS)) {
    for (const [key, v] of Object.entries(d)) { deepEq(v, C.STRINGS[lang][key], `STRINGS.${lang}.${key}`); n++; }
  }
  return { strings: n };
});

check('contacts message (hazard_alerts.contacts_message) he/en', () => {
  const m = fx.contacts_message;
  deepEq(m.he, C.contactsMessage(m.alert, 'he'), 'he');
  deepEq(m.en, C.contactsMessage(m.alert, 'en'), 'en');
});

// ---------------------------------------------------------------- DSP
check('resampling to 16 kHz == scipy resample_poly (48k / 44.1k / 22.05k)', () => {
  const out = {};
  for (const c of fx.resample) {
    const d = maxDiff(arr(c.y), E.to16k(arr(c.x), c.sr));
    if (d > 1e-5) throw new Error(`sr ${c.sr}: max diff ${d}`);
    out[c.sr] = d;
  }
  return out;
});

check('hazard_features.spectral_features (29 features) on 8 synthetic windows', () => {
  let worst = 0;
  for (const c of fx.spectral) {
    const js = E.spectralFeatures(arr(c.x));
    if (js.length !== c.features.length) throw new Error(`${c.name}: ${js.length} features`);
    c.features.forEach((p, i) => {
      const d = Math.abs(p - js[i]) / Math.max(1, Math.abs(p));
      worst = Math.max(worst, d);
      if (d > 1e-4) throw new Error(`${c.name}[${i}]: py ${p} js ${js[i]}`);
    });
  }
  return { worst_rel_diff: worst };
});

// ---------------------------------------------------------------- YAMNet (JS forward pass vs TFLite)
const yman = JSON.parse(fs.readFileSync(path.join(WEB, 'models', 'yamnet.json'), 'utf8'));
const ybin = fs.readFileSync(path.join(WEB, 'models', 'yamnet.bin'));
const yam = new E.Yamnet(yman, new Float32Array(ybin.buffer.slice(ybin.byteOffset, ybin.byteOffset + ybin.byteLength)));
check('YAMNet in JS == TFLite interpreter (scores, embeddings, log-mel)', () => {
  const out = {};
  for (const c of yfx.cases) {
    const x = E.b64f32(c.wave);
    const t0 = performance.now();
    const r = yam.analyze(x);
    const ms = Math.round(performance.now() - t0);
    if (r.n !== c.frames) throw new Error(`${c.name}: frames ${r.n} != ${c.frames}`);
    const ds = maxDiff(E.b64f32(c.scores), r.scores);
    const de = maxDiff(E.b64f32(c.emb), r.emb);
    if (ds > 1e-4 || de > 1e-3) throw new Error(`${c.name}: scores ${ds} emb ${de}`);
    out[c.name] = { frames: r.n, scores: +ds.toExponential(2), emb: +de.toExponential(2), ms };
  }
  return out;
});

// ---------------------------------------------------------------- heads + analyze (post-YAMNet pipeline)
// v2 / some v1 Platt slopes are ~1e7: a 1e-6 difference in the logit flips the "confidence" between 0 and 1 only
// when the logit sits on the knee - compare confidence only when it is not on the knee.
function onKnee(cat, score) {
  const c2 = heads.v2 && heads.v2.categories[cat];
  const c1 = heads.v1.categories[cat];
  const [a, b] = c2 ? c2.platt : c1 ? c1.platt : [1, 0];
  return Math.abs(a) > 1e3 && Math.abs(a * score + b) < Math.abs(a) * 1e-3;
}

check('audio_intel.analyze: v1 heads / zero-shot, v2 two-stage, distress head, quality, report (18 cases)', () => {
  const det = {};
  for (const c of fx.analyze) {
    const audio = arr(c.audio), scores = arr(c.scores), emb = arr(c.emb);
    const js = E.analyze(heads, audio, c.sr, () => ({ n: c.n, scores, emb }));
    const skip = (where, py, j) => {
      const m = where.match(/\.(events|top)\.(\d+)\.detection_confidence$/);
      if (m) { const row = c.report[m[1]][Number(m[2])]; return onKnee(row.category, row.score); }
      return false;
    };
    deepEq(c.report, js, c.name, { skip });
    if (c.report.quality.status !== 'unavailable') {
      for (const [cat, fr] of Object.entries(c.internals.v1_frames)) {
        const jf = E.categoryFrames(heads, cat, scores, emb, c.n);
        fr.forEach((v, i) => { if (Math.abs(v - jf[i]) > 1e-3 + 1e-5 * Math.abs(v)) throw new Error(`${c.name} v1 ${cat}[${i}] py ${v} js ${jf[i]}`); });
      }
      const w = E.windows16(E.to16k(audio, c.sr))[0];
      for (const [cat, v] of Object.entries(c.internals.v2)) {
        const r = E.v2WindowScores(heads, cat, scores, emb, c.n, w);
        if (Math.abs(v.l1 - r.l1) > 1e-3) throw new Error(`${c.name} v2 ${cat} stage1 py ${v.l1} js ${r.l1}`);
        if (v.l2 != null && Math.abs(v.l2 - r.l2) > 2e-3) throw new Error(`${c.name} v2 ${cat} stage2 py ${v.l2} js ${r.l2}`);
      }
    }
    det[c.name] = (js.events || []).map((e) => e.category).concat(js.distress && js.distress.detected ? ['distress'] : []);
  }
  return det;
});

// ---------------------------------------------------------------- monitor (detections -> alerts) on Python's reports
check('hazard_monitor.process: grouping, cooldown, rate limit, severity filter, SOS/silent, shout sequence', () => {
  const summary = {};
  for (const [name, sc] of Object.entries(fx.monitor)) {
    let now = 0;
    const mon = new C.MonitorCore({ sequenceRule: sc.rule, canShow: () => sc.can, prefsFn: () => ({ ...sc.prefs }), clock: () => now });
    sc.steps.forEach((st, i) => {
      now = st.now;
      const py = sc.results[i];
      if (st.close) {
        const a = mon.manager.open[st.close.family];
        if (a) mon.manager.close(a, st.close.response);
        deepEq(py, { closed: !!a }, `${name}[${i}]`);
        return;
      }
      const res = mon.process(st.reports, st.now).map(({ kind, alert, show, why }) => ({
        kind, show, why, family: alert.family, category: alert.category, severity: alert.severity, confidence: alert.confidence,
        count: alert.count, label_he: alert.label_he, explanation: alert.explanation, meta: alert.meta,
      }));
      deepEq(py, res, `${name}[${i}]`);
    });
    summary[name] = sc.results.map((r) => (Array.isArray(r) ? r.map((x) => `${x.kind}:${x.category}${x.show ? '' : '(' + x.why + ')'}`).join(',') || '-' : 'close'));
  }
  return summary;
});

check('AlertManager step by step (new / update / grouped / cooldown / rate_limited)', () => {
  let now = fx.manager.start;
  const m = new C.AlertManager(() => now);
  const kinds = [];
  for (const st of fx.manager.steps) {
    now += st.dt;
    if (st.op === 'close') {
      const a = m.open[st.family];
      if (a) m.close(a, st.response);
      if (!!a !== st.closed) throw new Error(`close ${st.family}`);
      continue;
    }
    const [kind, a] = m.decide(st.det);
    deepEq(st.kind, kind, `decide ${st.det.category} @+${st.dt}`);
    deepEq(st.alert, a ? { family: a.family, category: a.category, severity: a.severity, confidence: a.confidence, count: a.count } : null, `alert ${st.det.category}`);
    kinds.push(kind);
  }
  return { kinds: kinds.join(',') };
});

const summary = { ok: !failed, checks };
for (const c of checks) console.log(c.ok ? 'PASS' : 'FAIL', c.name, c.ok ? JSON.stringify(c.info || '') : c.error);
console.log('PARITY_SUMMARY ' + JSON.stringify(summary));
process.exit(failed ? 1 : 0);

// AI hazard alerts in the PWA (server/web/hazard.js) - real Chrome, real LifeGuard server.
// Usage: node run_hazard_e2e.mjs <base-url>   (users from LG_HAZARD_USERS, password LG_E2E_PW; A has B as an accepted contact)
// Detections are injected through a test hook (window.__lgHazardTest, enabled by localStorage lg.e2eHooks=1) - not
// the microphone - except one step that runs the real capture path on Chrome's fake microphone device.
// Prints "E2E_SUMMARY {json}" on the last line; exit code 0 = all required steps passed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const BASE = (process.argv[2] || '').replace(/\/$/, '');
if (!BASE) { console.error('usage: node run_hazard_e2e.mjs <base-url>'); process.exit(2); }
const CHROME = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const HEADLESS = !process.env.LG_E2E_HEADFUL;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.join(HERE, 'artifacts');
fs.mkdirSync(ART, { recursive: true });
const USERS = JSON.parse(process.env.LG_HAZARD_USERS || '{}');
const PW = process.env.LG_E2E_PW;
const A = USERS.A, B = USERS.B;
const YFX = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures', 'yamnet_parity.json'), 'utf8'));

const steps = [];
const pageErrors = [];
let failed = false;
const pages = {};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log('[hz-e2e]', ...a);

async function waitFor(fn, timeout, label) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(200);
  }
  throw new Error(`timeout (${timeout} ms) waiting for: ${label}${last instanceof Error ? ' - last error: ' + last.message : ''}`);
}

async function step(name, fn, { required = true } = {}) {
  const t0 = Date.now();
  try {
    const info = await fn();
    steps.push({ name, ok: true, ms: Date.now() - t0, ...(info ? { info } : {}) });
    log('PASS', name, info ? JSON.stringify(info) : '');
  } catch (e) {
    steps.push({ name, ok: false, required, ms: Date.now() - t0, error: String(e && e.message || e) });
    log(required ? 'FAIL' : 'WARN', name, '-', e && e.message);
    if (required) failed = true;
    for (const [who, p] of Object.entries(pages)) {
      try { await p.screenshot({ path: path.join(ART, `hazard_${name.replace(/[^\w]+/g, '_')}_${who}.png`), fullPage: true }); } catch { /* ignore */ }
    }
  }
}

async function api(token, method, p, body) {
  const res = await fetch(BASE + '/v1' + p, {
    method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(`${method} ${p} -> ${res.status} ${data && data.error}`); e.status = res.status; throw e; }
  return data;
}

async function launch(who, { hooks = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `lg-hz-${who}-`));
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: HEADLESS, userDataDir: dir,
    args: ['--no-first-run', '--no-default-browser-check', '--lang=he-IL', '--window-size=420,900',
      '--disable-features=Translate,OptimizationHints', '--disable-background-timer-throttling',
      '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
      '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
    defaultViewport: { width: 412, height: 880, isMobile: false, hasTouch: false },
  });
  const ctx = browser.defaultBrowserContext();
  await ctx.overridePermissions(BASE, ['geolocation', 'notifications', 'microphone']);
  const page = (await browser.pages())[0] || await browser.newPage();
  if (hooks) await page.evaluateOnNewDocument(() => { try { localStorage.setItem('lg.e2eHooks', '1'); } catch { /* ignore */ } });
  page.on('pageerror', (e) => pageErrors.push(`[${who}] pageerror: ${e.message}`));
  page.on('console', (m) => {
    const t = m.text();
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) pageErrors.push(`[${who}] console: ${t}`);
  });
  pages[who] = page;
  return { browser, page, dir, cdp: await page.createCDPSession() };
}

async function fill(page, sel, value) {
  await page.waitForSelector(sel, { visible: true, timeout: 10000 });
  await page.$eval(sel, (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, value);
}

async function click(page, sel, timeout = 10000) {
  await page.waitForSelector(sel, { visible: true, timeout });
  const hit = await page.$eval(sel, (el) => {
    el.scrollIntoView({ block: 'center' });
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && (top === el || el.contains(top));
  });
  if (!hit) throw new Error(`control ${sel} is covered by another element`);
  await page.click(sel);
}

const textOf = (page, sel) => page.$eval(sel, (el) => el.textContent).catch(() => null);
async function waitText(page, sel, re, timeout, label) {
  return waitFor(async () => { const t = await textOf(page, sel); return t && re.test(t) ? t : null; }, timeout, label || `${sel} ~ ${re}`);
}
async function go(page, hash) { await page.evaluate((h) => { location.hash = h; }, hash); await sleep(200); }
async function login(page, u) {
  await page.goto(BASE + '/app/', { waitUntil: 'load' });
  await fill(page, '#f-user', u.username);
  await fill(page, '#f-pass', PW);
  await click(page, '[data-test=login-submit]');
  await page.waitForSelector('#tabs:not([hidden])', { timeout: 15000 });
}
const T = (page, fn, ...args) => page.evaluate(fn, ...args);
const hz = (page, name, ...args) => page.evaluate((n, a) => window.__lgHazardTest[n](...a), name, args);
const modals = (page) => page.$$eval('[data-test=hz-modal]', (els) => els.length);
async function rec(page, id) { return (await hz(page, 'log')).find((r) => r.id === id); }
// the sheet closes at once; the local log write follows a moment later
const recWith = (page, id, response) => waitFor(async () => { const r = await rec(page, id); return r && r.response === response ? r : null; }, 5000, `log ${id} = ${response}`);
async function fresh(page) { await hz(page, 'advance', 3600); }   // well past every cooldown / event gap

let ua, ub;
try {
  ua = await launch('A', { hooks: true });
  ub = await launch('B');
  const pa = ua.page, pb = ub.page;
  const modelReqs = [];
  pa.on('request', (r) => { if (r.url().includes('/app/models/')) modelReqs.push(r.url()); });
  let tokA, tokB, incId;

  await step('login A and B; first load downloads no model', async () => {
    await login(pa, A);
    await login(pb, B);
    tokA = (await api(null, 'POST', '/auth/login', { username: A.username, password: PW, device: 'e2e-verifier' })).token;
    tokB = (await api(null, 'POST', '/auth/login', { username: B.username, password: PW, device: 'e2e-verifier' })).token;
    await go(pa, '#profile');
    await pa.waitForSelector('[data-test=hz-settings]', { timeout: 10000 });
    await go(pa, '#home');
    await sleep(500);
    if (modelReqs.length) throw new Error('model fetched on first load: ' + modelReqs.join(', '));
    const caches = await T(pa, async () => {
      await navigator.serviceWorker.ready;
      const keys = await caches.keys();
      const shell = await caches.open(keys.find((k) => k.startsWith('lg-shell')));
      return { keys, shell: (await shell.keys()).map((r) => new URL(r.url).pathname) };
    });
    if (caches.keys.includes('lg-hazard-models-v1')) throw new Error('model cache exists before opt-in');
    if (!caches.shell.includes('/app/hazard.js') || caches.shell.some((p) => p.startsWith('/app/models/'))) throw new Error('shell cache ' + caches.shell.join(','));
    const swc = (await (await fetch(BASE + '/app/sw.js')).text()).match(/const CACHE = '([^']+)'/)[1];
    if (swc === 'lg-shell-v7') throw new Error('SW cache version not bumped');
    const csp = (await fetch(BASE + '/app/')).headers.get('content-security-policy');
    return { model_requests: 0, caches: caches.keys, sw_cache: swc, csp };
  });

  await step('settings: defaults, persistence across reload, always-on text', async () => {
    await go(pa, '#profile');
    await pa.waitForSelector('[data-test=hz-settings]');
    const d = await T(pa, () => {
      const q = (t) => document.querySelector(`[data-test=${t}]`);
      return { listen: q('hz-listen').checked, popups: q('hz-popups').checked, sound: q('hz-sound').checked, vib: q('hz-vibration').checked,
        sev: q('hz-min-sev').value, lang: q('hz-lang').value, always: q('hz-always').textContent, note: q('hz-listen-note').textContent };
    });
    if (d.listen || !d.popups || !d.sound || d.sev !== 'medium' || d.lang !== 'he') throw new Error('defaults ' + JSON.stringify(d));
    if (!/נשארות פעילות תמיד/.test(d.always) || !/רק כשהאפליקציה פתוחה/.test(d.note)) throw new Error('honest texts missing');
    await pa.select('[data-test=hz-min-sev]', 'high');
    await pa.select('[data-test=hz-lang]', 'en');
    await click(pa, '[data-test=hz-popups]');
    await waitText(pa, '[data-test=hz-saved]', /נשמר/, 3000);
    await pa.reload({ waitUntil: 'load' });
    await go(pa, '#profile');
    await pa.waitForSelector('[data-test=hz-settings]');
    const p = await T(pa, () => ({ sev: document.querySelector('[data-test=hz-min-sev]').value, lang: document.querySelector('[data-test=hz-lang]').value,
      popups: document.querySelector('[data-test=hz-popups]').checked }));
    if (p.sev !== 'high' || p.lang !== 'en' || p.popups) throw new Error('not persisted ' + JSON.stringify(p));
    const stored = await T(pa, (u) => JSON.parse(localStorage.getItem('lg.hazardPrefs.' + u)), A.username);
    if (stored.min_severity !== 'high' || stored.language !== 'en' || stored.popups !== false || stored.listen !== false) throw new Error('storage ' + JSON.stringify(stored));
    await pa.select('[data-test=hz-min-sev]', 'medium');
    await pa.select('[data-test=hz-lang]', 'he');
    await click(pa, '[data-test=hz-popups]');
    return { stored };
  });

  await step('alert sheet: severity chip, confidence, explanation, sound-only note, location, six actions', async () => {
    await go(pa, '#home');
    const r = await hz(pa, 'event', 'gunshot', 'high', 0.87, { label_he: 'ירי' });
    if (r[0].kind !== 'new' || !r[0].show) throw new Error(JSON.stringify(r));
    await pa.waitForSelector('[data-test=hz-modal]', { visible: true, timeout: 5000 });
    const v = await T(pa, () => {
      const m = document.querySelector('[data-test=hz-modal]');
      const q = (t) => m.querySelector(`[data-test=${t}]`);
      return { dir: m.getAttribute('dir'), kind: q('hz-kind').textContent, sev: q('hz-sev').textContent, conf: q('hz-conf').textContent,
        explain: q('hz-explain').textContent, note: q('hz-not-verified').textContent, loc: q('hz-loc').textContent,
        actions: ['hz-sos', 'hz-safe', 'hz-contacts', 'hz-call', 'hz-false', 'hz-dismiss'].filter((t) => q(t)).length };
    });
    if (v.dir !== 'rtl' || v.kind !== 'ירי' || v.sev !== 'חומרה: גבוהה' || !/87%/.test(v.conf)) throw new Error(JSON.stringify(v));
    if (!/זיקוקים ותופים/.test(v.explain) || v.note !== 'זיהוי של צליל בלבד - לא אישור לסכנה' || !/מיקום/.test(v.loc) || v.actions !== 6) throw new Error(JSON.stringify(v));
    return v;
  });

  await step('SOS from the alert only after confirmation -> the PWA SOS flow', async () => {
    const id = (await hz(pa, 'modals'))[0];
    await click(pa, '[data-test=hz-sos]');
    await pa.waitForSelector('[data-test=hz-confirm-yes]', { visible: true });
    await sleep(800);
    const before = await T(pa, (u) => [localStorage.getItem('lg.myIncident.' + u), localStorage.getItem('lg.sosOutbox.' + u)], A.username);
    if (before.some((x) => x && x !== 'null')) throw new Error('SOS started before confirmation');
    await click(pa, '[data-test=hz-confirm-no]');
    await pa.waitForSelector('[data-test=hz-sos]', { visible: true });
    await click(pa, '[data-test=hz-sos]');
    await click(pa, '[data-test=hz-confirm-yes]');
    const mine = await waitFor(async () => {
      const v = await T(pa, (u) => localStorage.getItem('lg.myIncident.' + u), A.username);
      return v && v !== 'null' ? JSON.parse(v) : null;
    }, 20000, 'incident created');
    incId = mine.id;
    const inc = await api(tokA, 'GET', `/incidents/${incId}`);
    if (inc.status !== 'active' || inc.mode !== 'loud') throw new Error('incident ' + inc.status);
    if (await modals(pa)) throw new Error('sheet still open');
    await recWith(pa, id, 'sos');
    return { incident: incId };
  });

  await step('no popup during an active SOS (recorded as not shown: event_active)', async () => {
    await hz(pa, 'advance', 30);                       // past MIN_GAP_ANY_S, so only the active SOS can block it
    const r = await hz(pa, 'event', 'alarm', 'medium', 0.9);
    if (!r.length) throw new Error('no decision');
    await sleep(300);
    if (await modals(pa)) throw new Error('popup during SOS');
    if (r[0].show || r[0].why !== 'event_active') throw new Error(JSON.stringify(r));
    const row = await rec(pa, r[0].id);
    if (row.shown || row.not_shown_reason !== 'event_active') throw new Error(JSON.stringify(row));
    await go(pa, '#incident/' + incId);
    await click(pa, '[data-test=incident-resolve]');
    await click(pa, '[data-test=confirm-yes]');
    await pa.waitForSelector('[data-test=incident-closed]', { timeout: 10000 });
    await waitFor(async () => !(await T(pa, (u) => { const v = localStorage.getItem('lg.myIncident.' + u); return v && v !== 'null'; }, A.username)), 10000, 'my incident cleared');
    return { why: r[0].why };
  });

  await step('grouping / escalation / rate limit / cooldown (same constants as Windows)', async () => {
    await go(pa, '#home');
    await fresh(pa);
    const k = [];
    k.push((await hz(pa, 'event', 'glass_break', 'medium'))[0].kind);
    await hz(pa, 'advance', 5);
    k.push(JSON.stringify(await hz(pa, 'event', 'alarm', 'medium')));          // other family within 20 s -> rate limited
    await hz(pa, 'advance', 25);
    k.push((await hz(pa, 'event', 'impact', 'low'))[0].kind);                  // same family, same event
    await hz(pa, 'advance', 10);
    k.push((await hz(pa, 'event', 'glass_break', 'high'))[0].kind);            // severity went up -> update
    if (await modals(pa) !== 1) throw new Error('modals ' + await modals(pa));
    const upd = await textOf(pa, '[data-test=hz-modal] [data-test=hz-updated]');
    const sev = await textOf(pa, '[data-test=hz-modal] [data-test=hz-sev]');
    if (!/עודכן - זוהה שוב \(×3\)/.test(upd) || sev !== 'חומרה: גבוהה') throw new Error(`${upd} / ${sev}`);
    await click(pa, '[data-test=hz-dismiss]');
    await hz(pa, 'advance', 60);
    k.push(JSON.stringify(await hz(pa, 'event', 'glass_break', 'medium')));    // cooldown after dismiss (5 min)
    await hz(pa, 'advance', 5 * 60);
    k.push((await hz(pa, 'event', 'glass_break', 'medium'))[0].kind);         // after the cooldown -> new event
    if (k.join('|') !== 'new|[]|grouped|update|[]|new') throw new Error(k.join('|'));
    await click(pa, '[data-test=hz-dismiss]');
    return { kinds: k.join('|') };
  });

  await step('alert contacts: confirm -> POST /trusted/notify -> real sent_to and delivery ACK', async () => {
    await fresh(pa);
    const id = (await hz(pa, 'event', 'gunshot', 'high', 0.9, { label_he: 'ירי' }))[0].id;
    await click(pa, '[data-test=hz-contacts]');
    await click(pa, '[data-test=hz-confirm-yes]');
    await waitText(pa, '[data-test=hz-modal] [data-test=hz-status]', /השרת שלח ל-1 אנשי קשר/, 10000);
    const t = await waitText(pa, '[data-test=hz-modal] [data-test=hz-status]', /1 מתוך 1 אישרו קבלה/, 30000, 'B app ACK');
    const notes = await api(tokB, 'GET', '/notifications?after=0&wait=0');
    const n = (notes.notifications || []).find((x) => x.type === 'info' && /LifeGuard: זוהה אצלי צליל/.test((x.payload || {}).text || ''));
    if (!n || !/לא אישור לסכנה/.test(n.payload.text)) throw new Error('B did not get the message');
    if ((await rec(pa, id)).response !== 'contacts') throw new Error('not recorded');
    await click(pa, '[data-test=hz-dismiss]');
    return { status: t, text: n.payload.text };
  });

  await step('alert contacts offline: real error, nothing recorded as sent', async () => {
    await fresh(pa);
    const id = (await hz(pa, 'event', 'explosion', 'high', 0.9))[0].id;
    await click(pa, '[data-test=hz-contacts]');
    await pa.waitForSelector('[data-test=hz-confirm-yes]', { visible: true });
    await pa.setOfflineMode(true);
    await click(pa, '[data-test=hz-confirm-yes]');
    const t = await waitText(pa, '[data-test=hz-modal] [data-test=hz-status]', /ההודעה לא נשלחה/, 10000);
    if ((await rec(pa, id)).response != null) throw new Error('recorded despite failure');
    await click(pa, '[data-test=hz-dismiss]');
    // a sheet opened while offline does not offer contacts as available
    await fresh(pa);
    await hz(pa, 'event', 'alarm', 'medium');
    await pa.waitForSelector('[data-test=hz-modal]', { visible: true });
    const off = await T(pa, () => { const b = document.querySelector('[data-test=hz-modal] [data-test=hz-contacts]'); return [b.disabled, b.textContent]; });
    await pa.setOfflineMode(false);
    if (!off[0] || !/לא מחובר לשרת/.test(off[1])) throw new Error('offline button ' + off);
    await click(pa, '[data-test=hz-dismiss]');
    return { error: t, offline_button: off[1] };
  });

  await step('call emergency: confirm, then a tel: link with the PWA emergency number', async () => {
    await fresh(pa);
    const id = (await hz(pa, 'event', 'alarm', 'medium'))[0].id;
    await click(pa, '[data-test=hz-call]');
    await pa.waitForSelector('[data-test=hz-confirm-yes]', { visible: true });
    const href = await T(pa, () => document.querySelector('[data-test=hz-confirm-yes]').getAttribute('href'));
    if (href !== 'tel:100') throw new Error('href ' + href);
    if ((await rec(pa, id)).response != null) throw new Error('recorded before tapping');
    await T(pa, () => { const a = document.querySelector('[data-test=hz-confirm-yes]'); a.addEventListener('click', (e) => e.preventDefault()); a.click(); });
    await waitFor(async () => (await rec(pa, id)).response === 'call', 5000, 'call recorded');
    const tel = await T(pa, () => [...document.querySelectorAll('[data-test=hz-modal] .tel-row a')].map((a) => a.getAttribute('href')));
    await click(pa, '[data-test=hz-dismiss]');
    return { href, tel_row: tel };
  });

  await step('false alarm with a reason -> local log (user_reported, no audio / location)', async () => {
    await fresh(pa);
    const id = (await hz(pa, 'event', 'scream', 'medium', 0.7))[0].id;
    await click(pa, '[data-test=hz-false]');
    await click(pa, '[data-test=hz-reason-fireworks]');
    await click(pa, '[data-test=hz-false-confirm]');
    await waitFor(async () => !(await modals(pa)), 5000, 'sheet closed');
    const r = await recWith(pa, id, 'false_alarm');
    if (r.response !== 'false_alarm' || r.reason !== 'fireworks' || r.label_strength !== 'user_reported') throw new Error(JSON.stringify(r));
    const flat = JSON.stringify(r);
    if (/"(lat|lon|location|audio|samples)"/.test(flat)) throw new Error('sensitive field in log: ' + flat);
    return { record: r };
  });

  await step('find safe place: honest (no safe-place search in the PWA) -> environment map', async () => {
    await fresh(pa);
    const id = (await hz(pa, 'event', 'glass_break', 'medium'))[0].id;
    await click(pa, '[data-test=hz-safe]');
    await waitFor(async () => (await T(pa, () => location.hash)) === '#map', 5000, '#map');
    const t = await waitText(pa, '#notices', /חיפוש מקום בטוח לא זמין/, 5000);
    await recWith(pa, id, 'safe_place');
    return { notice: t.slice(0, 80) };
  });

  await step('popups off / below min severity: detection logged, no sheet', async () => {
    await go(pa, '#profile');
    await click(pa, '[data-test=hz-popups]');
    await fresh(pa);
    const r1 = await hz(pa, 'event', 'gunshot', 'high');
    await click(pa, '[data-test=hz-popups]');
    await fresh(pa);
    const r2 = await hz(pa, 'event', 'impact', 'low');
    await sleep(200);
    if (await modals(pa)) throw new Error('sheet shown');
    if (r1[0].why !== 'popups_disabled' || r2[0].why !== 'below_min_severity') throw new Error(JSON.stringify([r1, r2]));
    if ((await rec(pa, r1[0].id)).not_shown_reason !== 'popups_disabled') throw new Error('not logged');
    return { reasons: [r1[0].why, r2[0].why] };
  });

  await step('English (LTR) sheet per preference', async () => {
    await pa.select('[data-test=hz-lang]', 'en');
    await fresh(pa);
    await hz(pa, 'event', 'gunshot', 'high', 0.8);
    await pa.waitForSelector('[data-test=hz-modal]', { visible: true });
    const v = await T(pa, () => { const m = document.querySelector('[data-test=hz-modal]'); return [m.getAttribute('dir'), m.querySelector('[data-test=hz-sos]').textContent, m.querySelector('[data-test=hz-kind]').textContent, m.querySelector('[data-test=hz-not-verified]').textContent]; });
    await click(pa, '[data-test=hz-dismiss]');
    await pa.select('[data-test=hz-lang]', 'he');
    if (v[0] !== 'ltr' || !/Activate SOS/.test(v[1]) || v[2] !== 'Gunshot' || !/Sound-only detection/.test(v[3])) throw new Error(JSON.stringify(v));
    return { sheet: v };
  });

  await step('in-browser YAMNet (Web Worker, models from the server) == TFLite', async () => {
    const out = {};
    for (const c of YFX.cases) {
      const r = await hz(pa, 'yamnet', c.wave);
      if (r.error) throw new Error(r.error);
      const f32 = (b64) => { const b = Buffer.from(b64, 'base64'); return new Float32Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); };
      const S = f32(c.scores), E = f32(c.emb);
      if (r.scores.length !== S.length || r.emb.length !== E.length) throw new Error(`${c.name}: sizes ${r.scores.length}/${S.length}`);
      let ds = 0, de = 0;
      S.forEach((v, i) => { ds = Math.max(ds, Math.abs(v - r.scores[i])); });
      E.forEach((v, i) => { de = Math.max(de, Math.abs(v - r.emb[i])); });
      if (r.n !== c.frames || !(ds <= 1e-4) || !(de <= 1e-3)) throw new Error(`${c.name}: n ${r.n} scores ${ds} emb ${de}`);
      out[c.name] = { scores: +ds.toExponential(1), emb: +de.toExponential(1) };
    }
    const cached = await T(pa, async () => (await (await caches.open('lg-hazard-models-v1')).keys()).map((r) => new URL(r.url).pathname));
    if (!cached.includes('/app/models/yamnet.bin')) throw new Error('model not cached after opt-in use: ' + cached);
    return { ...out, model_cache: cached };
  });

  await step('foreground listening on Chrome\'s fake microphone: capture -> worker -> windows analysed; off stops the mic', async () => {
    await hz(pa, 'stopWorker');
    await go(pa, '#profile');
    await click(pa, '[data-test=hz-listen]');
    await waitText(pa, '[data-test=hz-listen-status]', /מאזין עכשיו/, 30000, 'listening');
    const st = await waitFor(async () => { const s = await hz(pa, 'listen'); return s.windows >= 2 ? s : null; }, 30000, '2 windows analysed');
    // a detection on the fake tone (unlikely) would open a sheet - close it so the toggle is reachable
    for (let i = 0; i < 3 && await modals(pa); i++) await click(pa, '[data-test=hz-dismiss]');
    await go(pa, '#home');
    await waitText(pa, '[data-test=hz-home-status]', /מאזין עכשיו/, 5000);
    await go(pa, '#profile');
    await click(pa, '[data-test=hz-listen]');
    const off = await waitFor(async () => { const s = await hz(pa, 'listen'); return !s.capturing && s.state === 'off' ? s : null; }, 5000, 'stopped');
    const pref = (await hz(pa, 'prefs')).listen;
    if (pref) throw new Error('listen pref still on');
    return { windows: st.windows, sample_rate: st.sampleRate, last_ms: st.lastMs, skipped: st.skipped, after_off: off.state };
  });

  await step('page hidden + notification permission: Web Notification is shown', async () => {
    await go(pa, '#home');
    await fresh(pa);
    await T(pa, () => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); });
    const id = (await hz(pa, 'event', 'gunshot', 'high'))[0].id;
    const tags = await waitFor(async () => {
      const t = await T(pa, async () => (await (await navigator.serviceWorker.getRegistration('/app/')).getNotifications()).map((n) => n.tag));
      return t.includes('hazard-' + id) ? t : null;
    }, 5000, 'notification');
    await T(pa, () => { delete document.visibilityState; });
    await click(pa, '[data-test=hz-dismiss]');
    return { tags };
  }, { required: false });

  await step('no CSP violations / script errors', async () => {
    if (pageErrors.length) throw new Error(pageErrors.slice(0, 5).join(' | '));
    return { errors: 0 };
  });
} catch (e) {
  steps.push({ name: 'harness', ok: false, required: true, error: String(e && e.stack || e) });
  failed = true;
} finally {
  for (const u of [ua, ub]) {
    if (!u) continue;
    try { await u.browser.close(); } catch { /* ignore */ }
    try { fs.rmSync(u.dir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

console.log('E2E_SUMMARY ' + JSON.stringify({ ok: !failed, base: BASE, headless: HEADLESS, steps, page_errors: pageErrors }));
process.exit(failed ? 1 : 0);

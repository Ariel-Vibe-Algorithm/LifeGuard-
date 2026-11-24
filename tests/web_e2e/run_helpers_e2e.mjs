// End-to-end test of the PWA "volunteers beyond on-scene" feature (server/helpers.py) in the installed Chrome,
// against a REAL LifeGuard server. Driven by tests/test_pwa_helpers_e2e.py, which creates the accounts
// (account age + admin verification have no PWA UI) and passes them in LG_HELPERS_USERS.
// Usage: node run_helpers_e2e.mjs <base-url>
// Three separate Chrome instances (= three phones): A asks for help, B = remote support, C = authority liaison.
// The UI is driven like a user; results are verified through the real HTTP API. Last line = JSON summary.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const BASE = (process.argv[2] || process.env.LG_BASE_URL || '').replace(/\/$/, '');
if (!BASE) { console.error('usage: node run_helpers_e2e.mjs <base-url>'); process.exit(2); }
const CHROME = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const HEADLESS = !process.env.LG_E2E_HEADFUL;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.join(HERE, 'artifacts');
fs.mkdirSync(ART, { recursive: true });

const USERS = JSON.parse(process.env.LG_HELPERS_USERS || '{}');
const PW = process.env.LG_E2E_PW || 'E2e-Pass-2026!';
const A = USERS.A, B = USERS.B, C = USERS.C;
if (!A || !B || !C) { console.error('LG_HELPERS_USERS missing'); process.exit(2); }
const TLV = { latitude: 32.0853, longitude: 34.7818, accuracy: 8 };

const steps = [];
const pageErrors = [];
let failed = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log('[e2e-helpers]', ...a);

async function waitFor(fn, timeout, label) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(250);
  }
  throw new Error(`timeout (${timeout} ms) waiting for: ${label}${last instanceof Error ? ' - last error: ' + last.message : ''}`);
}

const pages = {};
async function step(name, fn) {
  const t0 = Date.now();
  try {
    const info = await fn();
    steps.push({ name, ok: true, ms: Date.now() - t0, ...(info ? { info } : {}) });
    log('PASS', name, info ? JSON.stringify(info) : '');
  } catch (e) {
    steps.push({ name, ok: false, required: true, ms: Date.now() - t0, error: String(e && e.message || e) });
    log('FAIL', name, '-', e && e.message);
    failed = true;
    for (const [who, p] of Object.entries(pages)) {
      try { await p.screenshot({ path: path.join(ART, `helpers_${name.replace(/[^\w]+/g, '_')}_${who}.png`), fullPage: true }); } catch { /* ignore */ }
    }
    throw e;
  }
}

async function api(token, method, p, body) {
  const res = await fetch(BASE + '/v1' + p, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) { const e = new Error(`${method} ${p} -> ${res.status} ${data && data.error}`); e.status = res.status; throw e; }
  return data;
}

async function launch(who) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `lg-e2e-h-${who}-`));
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: HEADLESS,
    userDataDir: dir,
    args: ['--no-first-run', '--no-default-browser-check', '--lang=he-IL', '--window-size=420,900',
      '--disable-features=Translate,OptimizationHints', '--disable-background-timer-throttling',
      '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'],
    defaultViewport: { width: 412, height: 880, isMobile: false, hasTouch: false },
  });
  const ctx = browser.defaultBrowserContext();
  await ctx.overridePermissions(BASE, ['geolocation', 'notifications']);
  const page = (await browser.pages())[0] || await browser.newPage();
  page.on('pageerror', (e) => pageErrors.push(`[${who}] pageerror: ${e.message}`));
  page.on('console', (m) => {
    const t = m.text();
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) pageErrors.push(`[${who}] console: ${t}`);
  });
  const cdp = await page.createCDPSession();
  pages[who] = page;
  return { browser, page, cdp, dir };
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

async function setChecked(page, sel, on) {
  await page.waitForSelector(sel, { visible: true, timeout: 10000 });
  const cur = await page.$eval(sel, (el) => el.checked);
  if (cur !== on) await click(page, sel);
}

const textOf = (page, sel) => page.$$eval(sel, (els) => els.map((e) => e.textContent).join('\n')).catch(() => '');

async function waitText(page, sel, re, timeout, label) {
  return waitFor(async () => {
    const t = await textOf(page, sel);
    return re.test(t) ? t : null;
  }, timeout, label || `${sel} ~ ${re}`);
}

async function go(page, hash) {
  await page.evaluate((h) => { location.hash = h; }, hash);
  await sleep(150);
}

async function login(page, u) {
  await page.goto(BASE + '/app/', { waitUntil: 'load' });
  await click(page, '[data-test=mode-login]');
  await fill(page, '#f-user', u.username);
  await fill(page, '#f-pass', PW);
  await click(page, '[data-test=login-submit]');
  await page.waitForSelector('#tabs:not([hidden])', { timeout: 15000 });
}

async function chooseRoles(page, wanted) {
  await go(page, '#profile');
  await page.waitForSelector('[data-test=vol-roles-card]', { visible: true, timeout: 10000 });
  for (const r of ['on_scene', 'remote_support', 'authority_liaison']) await setChecked(page, `[data-test=role-${r}]`, wanted.includes(r));
  await click(page, '[data-test=vol-roles-save]');
  await waitText(page, '[data-test=vol-roles-msg]', /נשמר בשרת/, 10000);
  // available for requests (the existing volunteer switch) - roles first, so no on-scene presence is ever sent
  await setChecked(page, '[data-test=vol-available]', true);
  await click(page, '[data-test=vol-save]');
}

const esc = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');

// ======================================================================
let ua, ub, uc;
try {
  ua = await launch('A');
  ub = await launch('B');
  uc = await launch('C');
  const pa = ua.page, pb = ub.page, pc = uc.page;
  let tokA, tokB, tokC, supId, sosId;

  await step('login three users through the UI', async () => {
    await login(pa, A); await login(pb, B); await login(pc, C);
    tokA = (await api(null, 'POST', '/auth/login', { username: A.username, password: PW, device: 'e2e-verifier' })).token;
    tokB = (await api(null, 'POST', '/auth/login', { username: B.username, password: PW, device: 'e2e-verifier' })).token;
    tokC = (await api(null, 'POST', '/auth/login', { username: C.username, password: PW, device: 'e2e-verifier' })).token;
    return { a: A.username, b: B.username, c: C.username };
  });

  await step('roles B: at least one role required; remote_support saved, server says active', async () => {
    await go(pb, '#profile');
    await pb.waitForSelector('[data-test=vol-roles-card]', { visible: true, timeout: 10000 });
    for (const r of ['on_scene', 'remote_support', 'authority_liaison']) await setChecked(pb, `[data-test=role-${r}]`, false);
    await click(pb, '[data-test=vol-roles-save]');
    await waitText(pb, '[data-test=vol-roles-msg]', /לפחות דרך אחת/, 5000);
    await chooseRoles(pb, ['remote_support']);
    await waitText(pb, '[data-test=role-status-remote_support]', /✓ פעיל/, 10000);
    const r = await waitFor(async () => {
      const x = await api(tokB, 'GET', '/me/volunteer/roles');
      const me = await api(tokB, 'GET', '/me');
      return me.volunteer && me.volunteer.available && x.roles.join() === 'remote_support' ? x : null;
    }, 10000, 'B roles + available on server');
    return { roles: r.roles, effective: r.effective };
  });

  await step('roles C: authority_liaison (admin-verified identity) shows active', async () => {
    await chooseRoles(pc, ['authority_liaison']);
    await waitText(pc, '[data-test=role-status-authority_liaison]', /✓ פעיל/, 10000);
    const r = await waitFor(async () => {
      const x = await api(tokC, 'GET', '/me/volunteer/roles');
      const me = await api(tokC, 'GET', '/me');
      return me.volunteer && me.volunteer.available && x.effective.authority_liaison === 'active' ? x : null;
    }, 10000, 'C roles + available on server');
    return { roles: r.roles };
  });

  await step('support request from A: home card -> hotlines -> request -> waiting status from server', async () => {
    await go(pa, '#home');
    await click(pa, '[data-test=support-open]');
    await waitText(pa, '[data-test=hotlines]', /1201/, 5000, 'hotlines shown');
    await fill(pa, '[data-test=support-note]', 'קשה לי הערב');
    await click(pa, '[data-test=support-start]');
    const st = await waitText(pa, '[data-test=support-status]', /נשלחה ל-1 מתנדבים/, 10000);
    if (/הצטרף/.test(st)) throw new Error('claims a helper joined before acceptance');
    const reqs = await api(tokA, 'GET', '/incidents/inbox').catch(() => null);
    supId = await pa.evaluate(() => { for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k.startsWith('lg.mySupport.')) return JSON.parse(localStorage.getItem(k)).id; } return null; });
    const inc = await api(tokA, 'GET', `/incidents/${supId}`);
    if (inc.activation !== 'support' || inc.status !== 'active') throw new Error('bad support incident ' + JSON.stringify(inc));
    if (!inc.helpers.length || inc.helpers[0].username !== B.username || inc.helpers[0].state !== 'notified') throw new Error('helpers ' + JSON.stringify(inc.helpers));
    if (inc.location) throw new Error('support request has a location');
    return { incident: supId, inbox_ok: !!reqs };
  });

  await step('B gets the offer: guidance, no name/location, no chat before accept', async () => {
    await waitText(pb, '[data-test=helper-title]', /מישהו צריך לדבר/, 20000, 'offer in B');
    await waitText(pb, '[data-test=helper-privacy]', /לשמירה על הפרטיות לא מוצגים שם ומיקום של הפונה/, 5000);
    await waitText(pb, '[data-test=helper-guidance]', /הקשיבו/, 5000);
    const all = await textOf(pb, '#view');
    if (all.includes(A.display) || all.includes(A.username)) throw new Error('remote helper sees the requester identity');
    if (await pb.$('[data-test=msg-input]') && await pb.$eval('[data-test=chat-card]', (el) => !el.hidden)) throw new Error('chat visible before accept');
    return { ok: true };
  });

  await step('B accepts and chats; messages from A show as "הפונה"; A sees the helper status', async () => {
    await click(pb, '[data-test=helper-accept]');
    await pb.waitForSelector('[data-test=helper-accepted]', { timeout: 10000 });
    await waitText(pb, '[data-test=messages]', /הפונה[\s\S]*קשה לי הערב/, 10000);
    await fill(pb, '[data-test=msg-input]', 'אני כאן איתך');
    await click(pb, '[data-test=msg-send]');
    await waitText(pa, '[data-test=messages]', /אני כאן איתך/, 15000);
    await waitText(pa, '[data-test=support-status]', /מתנדב\/ת הצטרף\/ה לשיחה/, 15000);
    await fill(pa, '[data-test=msg-input]', 'תודה שאת/ה כאן');
    await click(pa, '[data-test=msg-send]');
    await waitText(pb, '[data-test=messages]', /תודה שאת\/ה כאן/, 15000);
    const all = await textOf(pb, '#view');
    if (all.includes(A.display) || all.includes(A.username)) throw new Error('remote helper sees the requester identity after accept');
    const inc = await api(tokA, 'GET', `/incidents/${supId}`);
    if (inc.helpers[0].state !== 'accepted') throw new Error('server helper state ' + inc.helpers[0].state);
    return { messages: inc.messages.length };
  });

  await step('A reloads the app: the open conversation is restored (server says resumed)', async () => {
    await pa.reload({ waitUntil: 'load' });
    await go(pa, '#home');
    await waitText(pa, '[data-test=support-card]', /שיחה פתוחה/, 10000);
    await click(pa, '[data-test=support-open]');
    await waitText(pa, '[data-test=messages]', /אני כאן איתך/, 10000);
    const again = await api(tokA, 'POST', '/support-requests', { note: '', language: 'he' });
    if (again.id !== supId || !again.resumed) throw new Error('server did not resume the open request');
    return { resumed: true };
  });

  await step('A ends the conversation (PATCH resolved); B loses access', async () => {
    await click(pa, '[data-test=support-end]');
    await click(pa, '[data-test=confirm-yes]');
    await waitText(pa, '[data-test=support-status]', /השיחה הסתיימה/, 10000);
    const inc = await api(tokA, 'GET', `/incidents/${supId}`);
    if (inc.status !== 'resolved') throw new Error('status ' + inc.status);
    await waitFor(async () => (await pb.$('[data-test=incident-gone]')) || /הסתיים/.test(await textOf(pb, '#notices')), 15000, 'B sees it ended');
    await pa.waitForSelector('[data-test=support-start]', { visible: true, timeout: 5000 });
    return { status: inc.status };
  });

  await step('liaison: A in SOS asks "someone call the police"; C accepts, sees name + location, reports; A sees it', async () => {
    await ua.cdp.send('Emulation.setGeolocationOverride', TLV);
    await go(pa, '#home');
    await click(pa, '[data-test=sos-button]');
    await pa.waitForSelector('[data-test=sos-confirm-panel]', { timeout: 5000 });
    await click(pa, '[data-test=sos-confirm]');
    await pa.waitForSelector('[data-test=incident-owner]', { timeout: 15000 });
    sosId = await pa.evaluate(() => location.hash.split('/')[1]);
    await click(pa, '[data-test=req-liaison]');
    await waitText(pa, '[data-test=helpers-status]', /הזעקת רשויות: נשלח למתנדבים/, 10000);
    // C: offer rings; before accept - no name and no location
    await waitText(pc, '[data-test=helper-title]', /בקשה להזעיק רשויות/, 20000, 'liaison offer in C');
    await waitText(pc, '[data-test=helper-identity]', /יוצגו רק אחרי שתקבל\/י/, 5000);
    if ((await textOf(pc, '#view')).includes(A.display)) throw new Error('liaison sees the name before accepting');
    if (/איתך/.test(await textOf(pa, '[data-test=helpers-status]'))) throw new Error('owner told someone is with them before acceptance');
    await click(pc, '[data-test=helper-accept]');
    await waitText(pc, '[data-test=helper-owner-name]', new RegExp(esc(A.display)), 10000);
    await waitText(pc, '[data-test=helper-location]', /32\.08530, 34\.78180/, 10000);
    if (await pc.$('[data-test=helper-loc-stale]')) throw new Error('fresh location marked stale');
    await click(pc, '[data-test=liaison-called_police]');
    await waitText(pc, '[data-test=liaison-feedback]', /נשמר בשרת: הזעיק\/ה משטרה/, 10000);
    await waitText(pa, '[data-test=helpers-status]', new RegExp(`הזעקת רשויות: ${esc(C.display)} איתך[\\s\\S]*הזעיק/ה משטרה`), 15000);
    const inc = await api(tokA, 'GET', `/incidents/${sosId}`);
    const hc = inc.helpers.find((x) => x.username === C.username);
    if (!hc || hc.state !== 'accepted' || hc.last_action !== 'called_police') throw new Error('server helpers ' + JSON.stringify(inc.helpers));
    return { incident: sosId };
  });

  await step('remote support inside SOS: B accepts, A sees status, A releases B', async () => {
    await click(pa, '[data-test=req-support]');
    await waitText(pb, '[data-test=helper-title]', /מישהו במצוקה צריך שיחה/, 20000, 'support offer in B');
    await waitText(pb, '[data-test=helper-privacy]', /לא מוצגים שם ומיקום/, 5000);
    await click(pb, '[data-test=helper-accept]');
    await pb.waitForSelector('[data-test=helper-accepted]', { timeout: 10000 });
    await waitText(pa, '[data-test=helpers-status]', /שיחת תמיכה מרחוק: מתנדב\/ת איתך/, 15000);
    if ((await textOf(pa, '[data-test=helpers-status]')).includes(B.display)) throw new Error('remote helper name shown in summary');
    const sel = `[data-test=owner-helper][data-user=${B.username}][data-state=accepted] [data-test=helper-release]`;
    await click(pa, sel, 10000);
    await click(pa, `[data-test=owner-helper][data-user=${B.username}] [data-test=confirm-yes]`);
    await waitFor(async () => !(await pa.$(`[data-test=owner-helper][data-user=${B.username}]`)), 10000, 'released helper removed from list');
    const inc = await api(tokA, 'GET', `/incidents/${sosId}`);
    if (inc.helpers.some((x) => x.username === B.username)) throw new Error('B still listed ' + JSON.stringify(inc.helpers));
    let status = 0;
    try { await api(tokB, 'GET', `/incidents/${sosId}`); } catch (e) { status = e.status; }
    if (status !== 404) throw new Error('B still has access after release');
    await waitFor(async () => (await pb.$('[data-test=incident-gone]')) || /העזרה שלך הסתיימה/.test(await textOf(pb, '#notices')), 15000, 'B sees release');
    return { released: true };
  });

  await step('liaison done: C finishes; A sees "finished"; A resolves the SOS', async () => {
    await click(pc, '[data-test=helper-done]');
    await click(pc, '[data-test=confirm-yes]');
    await pc.waitForSelector('[data-test=helper-finished]', { timeout: 10000 });
    await waitText(pa, '[data-test=helpers-status]', /הזעקת רשויות: המתנדב\/ת סיים\/ה/, 15000);
    await click(pa, '[data-test=incident-resolve]');
    await click(pa, '[data-test=confirm-yes]');
    await pa.waitForSelector('[data-test=incident-closed]', { timeout: 10000 });
    const inc = await api(tokA, 'GET', `/incidents/${sosId}`);
    if (inc.status !== 'resolved') throw new Error('status ' + inc.status);
    return { status: inc.status };
  });

  await step('no CSP violations / script errors in any browser', async () => {
    if (pageErrors.length) throw new Error(pageErrors.slice(0, 5).join(' | '));
    return { errors: 0 };
  });
} catch (e) {
  if (!steps.length || steps[steps.length - 1].ok) {
    steps.push({ name: 'harness', ok: false, required: true, error: String(e && e.stack || e) });
    failed = true;
  }
} finally {
  for (const u of [ua, ub, uc]) {
    if (!u) continue;
    try { await u.browser.close(); } catch { /* ignore */ }
    try { fs.rmSync(u.dir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

const summary = { ok: !failed, base: BASE, headless: HEADLESS, steps, page_errors: pageErrors };
console.log('E2E_SUMMARY ' + JSON.stringify(summary));
process.exit(failed ? 1 : 0);

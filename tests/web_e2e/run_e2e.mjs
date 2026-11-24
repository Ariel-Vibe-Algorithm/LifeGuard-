// End-to-end test of the LifeGuard PWA (server/web) in the installed Chrome, against a REAL LifeGuard server.
// Usage: node run_e2e.mjs <base-url>      e.g. node run_e2e.mjs http://127.0.0.1:8765
// Env:   CHROME_PATH (default: C:\Program Files\Google\Chrome\Application\chrome.exe)
//        LG_E2E_HEADFUL=1   run with visible windows
//        LG_E2E_SKIP_OVERDUE=1  skip the ~70s walk-home overdue check
// Two separate Chrome instances (= two phones). The UI is driven like a user; results are verified through
// the real HTTP API with Node fetch. Prints a JSON summary on the last line; exit code 0 = all required steps passed.
// Web Push is attempted for real and reported as observed (informational: headless Chrome may not support it).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const BASE = (process.argv[2] || process.env.LG_BASE_URL || '').replace(/\/$/, '');
if (!BASE) { console.error('usage: node run_e2e.mjs <base-url>'); process.exit(2); }
const CHROME = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const HEADLESS = !process.env.LG_E2E_HEADFUL;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.join(HERE, 'artifacts');
fs.mkdirSync(ART, { recursive: true });

const RUN = Date.now().toString(36).slice(-5);
const PW = 'E2e-Pass-2026!';
const A = { username: `alice_${RUN}`, display: 'אליס בדיקה' };
const B = { username: `bob_${RUN}`, display: 'בוב בדיקה' };
const TLV = { latitude: 32.0853, longitude: 34.7818, accuracy: 8 };
const TLV2 = { latitude: 32.0921, longitude: 34.7896, accuracy: 6 };

const steps = [];
const pushReport = {};
const pageErrors = [];
let failed = false;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log('[e2e]', ...a);

async function waitFor(fn, timeout, label) {
  const end = Date.now() + timeout;
  let last;
  while (Date.now() < end) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(250);
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
      try { await p.screenshot({ path: path.join(ART, `${name.replace(/[^\w]+/g, '_')}_${who}.png`), fullPage: true }); } catch { /* ignore */ }
    }
    if (required) throw e;
  }
}

// ---------------- API (verification only) ----------------
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

// ---------------- browser helpers ----------------
const pages = {};

async function launch(who) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `lg-e2e-${who}-`));
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
    // CSP violations and script errors are bugs. Failed HTTP statuses (expected 4xx in some flows) are not.
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) pageErrors.push(`[${who}] console: ${t}`);
  });
  const cdp = await page.createCDPSession();
  pages[who] = page;
  return { browser, page, cdp, dir };
}

async function setGeo(cdp, pos) {
  await cdp.send('Emulation.setGeolocationOverride', pos);
}

async function fill(page, sel, value) {
  await page.waitForSelector(sel, { visible: true, timeout: 10000 });
  await page.$eval(sel, (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, value);
}

async function click(page, sel, timeout = 10000) {
  await page.waitForSelector(sel, { visible: true, timeout });
  // like a user: bring the control to the middle of the screen (the bottom nav is fixed), then make sure
  // the control itself - not an overlay - is what is under the finger
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
  return waitFor(async () => {
    const t = await page.$$eval(sel, (els) => els.map((e) => e.textContent).join('\n')).catch(() => '');
    return re.test(t) ? t : null;
  }, timeout, label || `${sel} ~ ${re}`);
}

async function go(page, hash) {
  await page.evaluate((h) => { location.hash = h; }, hash);
  await sleep(150);
}

async function register(page, u) {
  await page.goto(BASE + '/app/', { waitUntil: 'load' });
  await click(page, '[data-test=mode-register]');
  await fill(page, '#f-display', u.display);
  await fill(page, '#f-user', u.username);
  await fill(page, '#f-pass', PW);
  await click(page, '[data-test=login-submit]');
  await page.waitForSelector('#tabs:not([hidden])', { timeout: 15000 });
}

// ======================================================================
let ua, ub;
try {
  ua = await launch('A');
  ub = await launch('B');
  const pa = ua.page, pb = ub.page;
  let tokA, tokB, incId, escId;

  await step('app shell served with CSP, manifest and service worker', async () => {
    const res = await fetch(BASE + '/app/');
    const csp = res.headers.get('content-security-policy') || '';
    if (!csp.includes("script-src 'self'")) throw new Error('CSP header missing');
    const man = await (await fetch(BASE + '/app/manifest.webmanifest')).json();
    if (man.dir !== 'rtl' || man.display !== 'standalone') throw new Error('bad manifest');
    for (const f of ['icon-192.png', 'icon-512.png', 'icon.svg', 'link.css', 'sw.js']) {
      const r = await fetch(BASE + '/app/' + f);
      if (!r.ok) throw new Error(f + ' -> ' + r.status);
    }
    await pa.goto(BASE + '/app/', { waitUntil: 'load' });
    await pa.waitForSelector('[data-test=mode-register]', { timeout: 10000 });
    const sw = await pa.evaluate(async () => {
      const reg = await Promise.race([navigator.serviceWorker.ready, new Promise((r) => setTimeout(() => r(null), 10000))]);
      return reg ? reg.scope : null;
    });
    if (!sw || !sw.endsWith('/app/')) throw new Error('service worker not active: ' + sw);
    return { sw_scope: sw };
  });

  await step('register two users through the UI', async () => {
    await register(pa, A);
    await register(pb, B);
    tokA = (await api(null, 'POST', '/auth/login', { username: A.username, password: PW, device: 'e2e-verifier' })).token;
    tokB = (await api(null, 'POST', '/auth/login', { username: B.username, password: PW, device: 'e2e-verifier' })).token;
    const me = await api(tokA, 'GET', '/me');
    if (me.display_name !== A.display) throw new Error('display name not stored');
    return { a: A.username, b: B.username };
  });

  await step('redesign: environment map (legend + honest disclaimer) and the bar SOS opens the confirm, nothing sent', async () => {
    await setGeo(ua.cdp, TLV);
    await go(pa, '#map');
    await pa.waitForSelector('[data-test=env-layers] button[data-test=env-layer-lighting]', { timeout: 10000 });
    const legend = await waitFor(async () => {
      const t = await textOf(pa, '[data-test=env-legend]');
      return t && !/טוען/.test(t) ? t : null;
    }, 20000, 'environment legend loaded');
    if (!/לא ציון סכנה לשכונה/.test(legend)) throw new Error('environment disclaimer missing: ' + legend.slice(0, 200));
    if (/מסוכנ/.test(await textOf(pa, '#view'))) throw new Error('"dangerous" wording on the map screen');
    if (!(await pa.$('[data-test=route-compare]'))) throw new Error('route planner missing');
    // the bar SOS: present on ordinary screens, opens the same two-step confirm on home; cancelling sends nothing
    await click(pa, '[data-test=sos-mini]');
    await pa.waitForSelector('[data-test=sos-confirm-panel]', { timeout: 5000 });
    if (await pa.$('#sos-mini:not([hidden])')) throw new Error('bar SOS still shown on home');
    await click(pa, '[data-test=sos-cancel]');
    await pa.waitForSelector('[data-test=sos-button]', { visible: true, timeout: 5000 });
    const pending = await pa.evaluate(() => Object.keys(localStorage).filter((k) => /sosOutbox|myIncident/.test(k) && localStorage.getItem(k) && localStorage.getItem(k) !== 'null'));
    if (pending.length) throw new Error('something was queued: ' + pending.join(','));
    return { legend: legend.slice(0, 60) };
  });

  await step('medical info saved (encrypted on server) via profile UI', async () => {
    await go(pa, '#profile');
    await fill(pa, '[data-test=med-blood_type]', 'A+');
    await fill(pa, '[data-test=med-allergies]', 'פניצילין');
    await pa.$eval('[data-test=med-share]', (el) => { el.checked = true; });
    await click(pa, '[data-test=med-save]');
    await waitText(pa, '[data-test=medical-card]', /נשמר בשרת\./, 10000);
    const m = await api(tokA, 'GET', '/me/medical');
    if (m.blood_type !== 'A+' || m.allergies !== 'פניצילין' || m.share !== true) throw new Error('medical mismatch ' + JSON.stringify(m));
    return { updated_at: m.updated_at };
  });

  await step('web push: real subscription attempt in Chrome (B)', async () => {
    await go(pb, '#profile');
    await click(pb, '[data-test=push-enable]');
    const msg = await waitFor(async () => {
      const t = await textOf(pb, '[data-test=push-msg]');
      return t && t !== 'נרשם...' ? t : null;
    }, 30000, 'push result message');
    const st = await api(tokB, 'GET', '/push/status');
    const sub = await pb.evaluate(async () => {
      const reg = await navigator.serviceWorker.getRegistration('/app/');
      const s = reg && await reg.pushManager.getSubscription();
      return s ? new URL(s.endpoint).host : null;
    });
    Object.assign(pushReport, { headless: HEADLESS, ui_message: msg, browser_subscription_host: sub, server_devices: st.devices });
    if (!sub || st.devices < 1) throw new Error('no push subscription: ' + msg);
    return pushReport;
  }, { required: false });

  await step('safety circle: invite by username, accept in the other browser', async () => {
    await go(pa, '#circle');
    await fill(pa, '[data-test=invite-username]', B.username);
    await click(pa, '[data-test=invite-username-send]');
    await pa.waitForSelector('[data-test=sent-invite][data-status=pending]', { timeout: 10000 });
    await waitText(pa, '[data-test=sent-invite]', /ממתין לאישור/, 5000);
    // B gets the invitation through the long-poll stream (notice), then accepts in the circle screen
    await waitText(pb, '[data-test=notice]', /הזמנה למעגל הביטחון/, 15000, 'invite notice in B');
    await go(pb, '#circle');
    await click(pb, `[data-test=received-invite][data-from=${A.username}] [data-test=invite-accept]`, 15000);
    await pa.waitForSelector(`[data-test=contact][data-user=${B.username}]`, { timeout: 20000 });
    const c = await api(tokA, 'GET', '/circle');
    if (!c.my_contacts.some((x) => x.username === B.username)) throw new Error('B not in A.my_contacts');
    await waitText(pb, '[data-test=contact-for]', new RegExp(A.display), 15000);
    return { connected: true };
  });

  await step('safety circle: invite by phone shows one-time code', async () => {
    await fill(pa, '[data-test=invite-phone]', '052-123-4567');
    await fill(pa, '[data-test=invite-label]', 'אמא');
    await click(pa, '[data-test=invite-phone-send]');
    const code = (await waitText(pa, '[data-test=invite-code]', /^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/, 10000)).trim();
    const c = await api(tokA, 'GET', '/circle');
    const inv = c.sent_invitations.find((i) => i.channel === 'phone' && i.status === 'pending' && i.phone_last4 === '4567');
    if (!inv) throw new Error('phone invitation not on server');
    if (JSON.stringify(c).includes(code)) throw new Error('code is returned by the server again (should be shown once only)');
    const shareVisible = await pa.$('[data-test=share-code]');
    return { code_format_ok: true, label: inv.label, share_button: !!shareVisible };
  });

  await step('safety circle: share code created by B, redeemed by A (preview + accept)', async () => {
    await click(pb, '[data-test=invite-code-create]');
    const code = (await waitText(pb, '[data-test=invite-code]', /^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/, 10000)).trim();
    await fill(pa, '[data-test=redeem-code]', code);
    await click(pa, '[data-test=redeem-preview]');
    await waitText(pa, '#view', new RegExp(`${B.display}.*מבקש/ת להוסיף אותך`), 10000);
    await click(pa, '[data-test=redeem-accept]');
    await waitText(pa, '#view', /מחובר\/ת!/, 10000);
    const c = await api(tokB, 'GET', '/circle');
    if (!c.my_contacts.some((x) => x.username === A.username)) throw new Error('A not in B.my_contacts');
    return { connected_both_ways: true };
  });

  await step('readiness check: B app ACKs, A sees "received"', async () => {
    await go(pa, '#home');
    await click(pa, '[data-test=readiness-run]');
    await waitText(pa, '[data-test=readiness-results]', /✓ התקבל/, 20000, 'B received the readiness check');
    const r = await api(tokA, 'GET', '/me/readiness');
    if (!r.all_received) throw new Error('server says not all received');
    return { all_received: r.all_received };
  });

  await step('web push: did a push actually arrive (FCM -> Chrome -> service worker)?', async () => {
    if (!pushReport.browser_subscription_host) throw new Error('skipped: no subscription');
    const st = await waitFor(async () => {
      const s = await api(tokB, 'GET', '/push/status');
      const x = (s.subscriptions || [])[0];
      return x && (x.last_ok || x.last_error) ? x : null;
    }, 25000, 'push service response recorded by server');
    pushReport.push_service_accepted = !!st.last_ok;
    pushReport.push_service_error = st.last_error || null;
    const got = await waitFor(() => pb.evaluate(() => localStorage.getItem('lg.lastPush')), 30000, 'service worker received push')
      .catch(() => null);
    pushReport.push_arrived_in_browser = !!got;
    if (!got) throw new Error('push accepted by service: ' + pushReport.push_service_accepted + ', but not received in browser');
    return pushReport;
  }, { required: false });

  await step('SOS from the PWA (tap + confirm) with emulated GPS: incident on server with location', async () => {
    await setGeo(ua.cdp, TLV);
    await go(pa, '#home');
    await click(pa, '[data-test=sos-button]');
    await pa.waitForSelector('[data-test=sos-confirm-panel]', { timeout: 5000 });
    await pa.select('[data-test=sos-type]', 'medical');
    await click(pa, '[data-test=sos-confirm]');
    await pa.waitForSelector('[data-test=incident-owner]', { timeout: 15000 });
    incId = await pa.evaluate(() => location.hash.split('/')[1]);
    const inc = await api(tokA, 'GET', `/incidents/${incId}`);
    if (inc.status !== 'active' || inc.mode !== 'loud' || inc.emergency_type !== 'medical') throw new Error('bad incident ' + JSON.stringify(inc));
    if (!inc.location || Math.abs(inc.location.lat - TLV.latitude) > 1e-4 || Math.abs(inc.location.lon - TLV.longitude) > 1e-4) throw new Error('location missing/wrong ' + JSON.stringify(inc.location));
    if (!inc.location.is_precise || inc.location.source !== 'browser_gps') throw new Error('location metadata ' + JSON.stringify(inc.location));
    if (!inc.recipients.some((r) => r.username === B.username)) throw new Error('B not a recipient');
    return { incident: incId, location: [inc.location.lat, inc.location.lon, inc.location.accuracy_m] };
  });

  await step('B gets the alert in the PWA (long-poll), sees medical info; server marks delivered', async () => {
    await waitText(pb, '[data-test=alert-title]', new RegExp(A.display), 20000, 'alert screen in B');
    await waitText(pb, '[data-test=medical]', /A\+/, 10000, 'medical shown to trusted contact');
    await waitText(pb, '[data-test=risk]', /עדיפות בתור/, 10000, 'risk meter shown to responder');
    if (await pa.$('[data-test=risk]')) throw new Error('risk meter shown to the person in distress');
    const st = await waitFor(async () => {
      const inc = await api(tokA, 'GET', `/incidents/${incId}`);
      const r = inc.recipients.find((x) => x.username === B.username);
      return r && r.state === 'delivered' ? r.state : null;
    }, 15000, 'recipient state delivered');
    await pa.waitForSelector(`[data-test=recipient][data-user=${B.username}][data-state=delivered]`, { timeout: 10000 });
    await waitText(pa, `[data-test=recipient][data-user=${B.username}]`, /נמסר לאפליקציה/, 5000);
    const nav = await pb.$eval('[data-test=navigate]', (a) => a.href);
    if (!nav.startsWith('https://www.google.com/maps/search/?api=1&query=32.085300,34.781800')) throw new Error('navigate link ' + nav);
    return { state: st, navigate: nav };
  });

  await step('B accepts; A UI shows "קיבל/ה"', async () => {
    await click(pb, '[data-test=resp-accept]');
    await pb.waitForSelector('[data-test=my-response]', { timeout: 10000 });
    await pa.waitForSelector(`[data-test=recipient][data-user=${B.username}][data-state=accepted]`, { timeout: 15000 });
    await waitText(pa, `[data-test=recipient][data-user=${B.username}]`, /קיבל\/ה/, 5000);
    await waitText(pa, '[data-test=delivery-summary]', /קיבלו: 1/, 5000);
    return { accepted: true };
  });

  await step('live location: moving the emulated GPS updates the incident on the server', async () => {
    await setGeo(ua.cdp, TLV2);
    const loc = await waitFor(async () => {
      const inc = await api(tokA, 'GET', `/incidents/${incId}`);
      return Math.abs(inc.location.lat - TLV2.latitude) < 1e-4 ? inc.location : null;
    }, 25000, 'new location on server');
    await waitText(pa, '[data-test=track-status]', /המיקום נשלח לשרת/, 5000);
    return { lat: loc.lat, lon: loc.lon };
  });

  await step('incident messages both ways + on_my_way', async () => {
    await fill(pb, '[data-test=msg-input]', 'אני בדרך אלייך');
    await click(pb, '[data-test=msg-send]');
    await waitText(pa, '[data-test=messages]', /אני בדרך אלייך/, 15000);
    await fill(pa, '[data-test=msg-input]', 'תודה, אני בכניסה');
    await click(pa, '[data-test=msg-send]');
    await waitText(pb, '[data-test=messages]', /תודה, אני בכניסה/, 15000);
    // הודעה מהירה בלחיצה אחת (של בעלת האירוע) + תפקיד השולח + אישור קריאה
    await pa.waitForSelector('[data-test=quick-replies] button[data-quick=hiding]', { timeout: 10000 });
    await click(pa, '[data-test=quick-replies] button[data-quick=hiding]');
    await waitText(pb, '[data-test=messages]', /אני מסתתר\/ת/, 15000);
    await waitText(pb, '[data-test=messages]', /\(במצוקה\)/, 5000);
    await waitText(pa, '[data-test=messages]', /נקרא ע"י/, 15000);
    await click(pb, '[data-test=resp-on_my_way]');
    await waitText(pa, `[data-test=recipient][data-user=${B.username}]`, /בדרך/, 15000);
    return { ok: true };
  });

  await step('"I\'m safe": owner resolves, B sees it ended', async () => {
    await click(pa, '[data-test=incident-resolve]');
    await click(pa, '[data-test=confirm-yes]');
    await pa.waitForSelector('[data-test=incident-closed]', { timeout: 10000 });
    const inc = await api(tokA, 'GET', `/incidents/${incId}`);
    if (inc.status !== 'resolved') throw new Error('status ' + inc.status);
    await waitFor(async () => (await pb.$('[data-test=incident-closed]')) || (await pb.$('[data-test=incident-gone]'))
      || /האירוע הסתיים/.test(await textOf(pb, '#notices') || ''), 15000, 'B sees ended');
    return { status: inc.status };
  });

  await step('silent distress: nothing alarming on A, B sees "don\'t call" warning', async () => {
    await go(pa, '#home');
    await click(pa, '[data-test=silent-button]');
    await click(pa, '[data-test=sos-confirm]');
    await pa.waitForSelector('[data-test=incident-owner-silent]', { timeout: 15000 });
    const sid = await pa.evaluate(() => location.hash.split('/')[1]);
    const alarmEls = await pa.$$('.alarm, .alarm-head, .card.danger');
    if (alarmEls.length) throw new Error(`alarming elements on silent screen: ${alarmEls.length}`);
    const inc = await api(tokA, 'GET', `/incidents/${sid}`);
    if (inc.mode !== 'silent') throw new Error('mode ' + inc.mode);
    await pb.waitForSelector('[data-test=silent-warning]', { timeout: 20000 });
    await click(pa, '[data-test=incident-resolve]');
    await click(pa, '[data-test=confirm-yes]');
    await pa.waitForSelector('[data-test=incident-closed]', { timeout: 10000 });
    return { incident: sid };
  });

  await step('walk-home escort: start in UI, watcher sees it with location, arrive', async () => {
    await setGeo(ua.cdp, TLV);
    await go(pa, '#home');
    await pa.waitForSelector('[data-test=escort-start]', { visible: true, timeout: 10000 });
    await fill(pa, '[data-test=escort-dest]', 'הבית');
    await pa.select('[data-test=escort-minutes]', '15');
    await click(pa, '[data-test=escort-start]');
    await pa.waitForSelector('[data-test=escort-countdown]', { timeout: 15000 });
    const e = await api(tokA, 'GET', '/escorts');
    const mine = e.mine[0];
    if (!mine || mine.destination !== 'הבית' || !mine.location) throw new Error('escort on server ' + JSON.stringify(e));
    escId = mine.id;
    if (Math.abs(mine.remaining_seconds - 900) > 30) throw new Error('remaining ' + mine.remaining_seconds);
    await waitText(pb, '[data-test=notice]', /התחיל\/ה ליווי הביתה/, 15000, 'escort_started notice in B');
    await go(pb, '#community');
    await waitText(pb, '[data-test=watching-item]', new RegExp(A.display), 15000);
    await waitText(pb, '[data-test=watching-item] [data-test=loc-line]', /עודכן לפני/, 5000);
    await click(pa, '[data-test=escort-arrived]');
    await pa.waitForSelector('[data-test=escort-start]', { visible: true, timeout: 10000 });
    const g = await api(tokA, 'GET', `/escorts/${escId}`);
    if (g.status !== 'arrived') throw new Error('status ' + g.status);
    await waitText(pb, '[data-test=notice]', /הגיע\/ה בשלום/, 15000, 'arrived notice in B');
    return { escort: escId, status: g.status };
  });

  if (!process.env.LG_E2E_SKIP_OVERDUE) {
    await step('walk-home overdue: server escort_overdue -> prominent prompt -> extend', async () => {
      // the UI offers >=10 minutes; a 1-minute escort is started through the API (same user) to keep the test short
      const e = await api(tokA, 'POST', '/escorts', { minutes: 1, destination: 'בדיקה', mode: 'loud', location: { lat: TLV.latitude, lon: TLV.longitude, is_precise: true } });
      await go(pa, '#home');
      await pa.waitForSelector('[data-test=escort-overdue]', { visible: true, timeout: 95000 });
      await click(pa, '[data-test=escort-overdue] [data-test=overdue-extend]');
      await waitFor(async () => !(await pa.$('[data-test=escort-overdue]')), 10000, 'prompt dismissed');
      const g = await api(tokA, 'GET', `/escorts/${e.id}`);
      if (g.status !== 'active' || g.remaining_seconds < 800) throw new Error('not extended ' + JSON.stringify(g));
      await api(tokA, 'POST', `/escorts/${e.id}/cancelled`, {});
      return { extended_remaining_s: Math.round(g.remaining_seconds) };
    });
  }

  await step('logout revokes the PWA session token', async () => {
    const pwaToken = await pa.evaluate(() => localStorage.getItem('lg.token'));
    await go(pa, '#profile');
    await click(pa, '[data-test=logout]');
    await pa.waitForSelector('[data-test=mode-register]', { timeout: 10000 });
    let status = 0;
    try { await api(pwaToken, 'GET', '/me'); } catch (e) { status = e.status; }
    if (status !== 401) throw new Error('token still valid after logout');
    return { revoked: true };
  });

  await step('no CSP violations / script errors in either browser', async () => {
    if (pageErrors.length) throw new Error(pageErrors.slice(0, 5).join(' | '));
    return { errors: 0 };
  });
} catch (e) {
  if (!steps.length || steps[steps.length - 1].ok) {
    steps.push({ name: 'harness', ok: false, required: true, error: String(e && e.stack || e) });
    failed = true;
  }
} finally {
  for (const u of [ua, ub]) {
    if (!u) continue;
    try { await u.browser.close(); } catch { /* ignore */ }
    try { fs.rmSync(u.dir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

const summary = { ok: !failed, base: BASE, headless: HEADLESS, steps, push: pushReport, page_errors: pageErrors };
console.log('E2E_SUMMARY ' + JSON.stringify(summary));
process.exit(failed ? 1 : 0);

// iPhone emulation run of the LifeGuard PWA (server/web) in the installed Chrome, against a REAL LifeGuard server.
// Usage: node run_ios_e2e.mjs <base-url>
// Env:   CHROME_PATH, LG_E2E_HEADFUL=1
//
// IMPORTANT: this is Chrome (Blink) pretending to be an iPhone - iPhone viewport (390x844 @3x), touch, an iOS Safari
// user agent, navigator.vibrate removed (iOS has none), and "home screen app" emulated by overriding
// navigator.standalone. It checks OUR code paths for iOS (install card, push gating, safe-area CSS, layout,
// offline shell). It does NOT prove anything about real Safari/WebKit behaviour - that needs a real iPhone.
// Prints a JSON summary on the last line; exit code 0 = all required steps passed.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const BASE = (process.argv[2] || process.env.LG_BASE_URL || '').replace(/\/$/, '');
if (!BASE) { console.error('usage: node run_ios_e2e.mjs <base-url>'); process.exit(2); }
const CHROME = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const HEADLESS = !process.env.LG_E2E_HEADFUL;
const HERE = path.dirname(fileURLToPath(import.meta.url));
const ART = path.join(HERE, 'artifacts');
fs.mkdirSync(ART, { recursive: true });

const IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1';
const IPHONE_VIEWPORT = { width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true };
const RUN = Date.now().toString(36).slice(-5);
const PW = 'E2e-Pass-2026!';
const TLV = { latitude: 32.0853, longitude: 34.7818, accuracy: 8 };

const steps = [];
const pageErrors = [];
const pages = {};
let failed = false;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (...a) => console.log('[ios-e2e]', ...a);

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
      try { await p.screenshot({ path: path.join(ART, `ios_${name.replace(/[^\w]+/g, '_')}_${who}.png`), fullPage: true }); } catch { /* ignore */ }
    }
  }
}

// ---------------- an "iPhone" = a separate Chrome with its own profile (storage, service worker) ----------------
// (not an incognito context: Chrome has no Push API in incognito, and logs an error when it is called)
const browsers = [];
/** standalone: emulate the home-screen app (navigator.standalone === true, as iOS sets it).
 *  oldIos: additionally remove the Push API, as on iOS < 16.4. */
async function iphone(who, { standalone = false, oldIos = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `lg-ios-e2e-${who}-`));
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: HEADLESS,
    userDataDir: dir,
    args: ['--no-first-run', '--no-default-browser-check', '--lang=he-IL', '--window-size=420,900',
      '--disable-features=Translate,OptimizationHints', '--disable-background-timer-throttling',
      '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'],
  });
  browsers.push({ browser, dir });
  const ctx = browser.defaultBrowserContext();
  await ctx.overridePermissions(BASE, ['geolocation', 'notifications']);
  const page = (await browser.pages())[0] || await browser.newPage();
  await page.emulate({ viewport: IPHONE_VIEWPORT, userAgent: IPHONE_UA });
  await page.evaluateOnNewDocument((sa, old) => {
    // iOS WebKit has no Vibration API: make sure nothing in the app assumes it
    try { delete Navigator.prototype.vibrate; } catch { /* ignore */ }
    if (sa) Object.defineProperty(Navigator.prototype, 'standalone', { get: () => true, configurable: true });
    if (old) { try { delete window.PushManager; } catch { /* ignore */ } }
  }, standalone, oldIos);
  page.on('pageerror', (e) => pageErrors.push(`[${who}] pageerror: ${e.message}`));
  page.on('console', (m) => {
    const t = m.text();
    if (m.type() === 'error' && !/Failed to load resource/.test(t)) pageErrors.push(`[${who}] console: ${t}`);
  });
  const cdp = await page.createCDPSession();
  await cdp.send('Emulation.setGeolocationOverride', TLV);
  pages[who] = page;
  return { browser, page, cdp };
}

async function tap(page, sel, timeout = 10000) {
  await page.waitForSelector(sel, { visible: true, timeout });
  const hit = await page.$eval(sel, (el) => {
    el.scrollIntoView({ block: 'center' });
    const r = el.getBoundingClientRect();
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!top && (top === el || el.contains(top));
  });
  if (!hit) throw new Error(`control ${sel} is covered by another element`);
  await page.tap(sel);
}

async function fill(page, sel, value) {
  await page.waitForSelector(sel, { visible: true, timeout: 10000 });
  await page.$eval(sel, (el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }, value);
}

async function register(page, u) {
  await page.goto(BASE + '/app/', { waitUntil: 'load' });
  await tap(page, '[data-test=mode-register]');
  await fill(page, '#f-display', u.display);
  await fill(page, '#f-user', u.username);
  await fill(page, '#f-pass', PW);
  await tap(page, '[data-test=login-submit]');
  await page.waitForSelector('#tabs:not([hidden])', { timeout: 15000 });
}

async function go(page, hash) {
  await page.evaluate((h) => { location.hash = h; }, hash);
  await sleep(400);
}

const visible = (page, sel) => page.$eval(sel, (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)).catch(() => false);

/** Layout checks of the current screen at iPhone width: no horizontal scroll, no element sticking out
 *  sideways, every text field >= 16px (smaller makes iOS Safari zoom in on focus). */
async function layoutProblems(page, label) {
  return page.evaluate((lbl) => {
    const out = [];
    const W = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > W + 1) out.push(`${lbl}: page scrollWidth ${document.documentElement.scrollWidth} > ${W}`);
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      if (r.right > W + 1 || r.left < -1) {
        out.push(`${lbl}: <${el.tagName.toLowerCase()} class="${el.className}" data-test="${el.dataset.test || ''}"> spans ${Math.round(r.left)}..${Math.round(r.right)} (width ${W})`);
        if (out.length > 6) break;
      }
    }
    for (const el of document.querySelectorAll('input, select, textarea')) {
      if (['checkbox', 'radio', 'range', 'hidden', 'file', 'color'].includes(el.type)) continue;
      if (!(el.offsetWidth || el.offsetHeight)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 16) out.push(`${lbl}: ${el.tagName.toLowerCase()}#${el.id || el.dataset.test || '?'} font-size ${fs}px < 16px`);
    }
    return out;
  }, label);
}

function pngSize(buf) {
  if (buf.slice(1, 4).toString('ascii') !== 'PNG') return null;
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
}

// ======================================================================
const U1 = { username: `ios_tab_${RUN}`, display: 'אייפון בדפדפן' };
const U2 = { username: `ios_home_${RUN}`, display: 'אייפון ממסך הבית' };
try {
  await step('static: apple meta tags, touch icons, manifest', async () => {
    const html = await (await fetch(BASE + '/app/')).text();
    const need = ['viewport-fit=cover', 'name="apple-mobile-web-app-capable" content="yes"',
      'name="apple-mobile-web-app-status-bar-style"', 'name="apple-mobile-web-app-title" content="LifeGuard"',
      'name="theme-color"', 'rel="manifest"', 'name="format-detection" content="telephone=no"'];
    for (const n of need) if (!html.includes(n)) throw new Error('index.html missing ' + n);
    if (/user-scalable=no|maximum-scale=1/.test(html)) throw new Error('viewport blocks zoom (accessibility)');
    const icons = [...html.matchAll(/<link rel="apple-touch-icon"(?: sizes="(\d+)x\d+")? href="([^"]+)"/g)];
    const sizes = {};
    for (const [, size, href] of icons) {
      const r = await fetch(BASE + href);
      if (!r.ok || !(r.headers.get('content-type') || '').startsWith('image/png')) throw new Error(`${href} -> ${r.status}`);
      const dim = pngSize(Buffer.from(await r.arrayBuffer()));
      if (!dim || dim[0] !== dim[1] || (size && dim[0] !== Number(size))) throw new Error(`${href} has size ${dim}`);
      sizes[href] = dim[0];
    }
    if (!Object.values(sizes).includes(180)) throw new Error('no 180x180 apple-touch-icon');
    const man = await (await fetch(BASE + '/app/manifest.webmanifest')).json();
    for (const k of ['name', 'short_name', 'start_url', 'scope', 'display', 'theme_color', 'background_color', 'icons']) {
      if (!man[k]) throw new Error('manifest missing ' + k);
    }
    if (man.display !== 'standalone' || man.dir !== 'rtl' || man.lang !== 'he') throw new Error('manifest display/dir/lang');
    if (!man.icons.some((i) => i.sizes === '512x512' && i.type === 'image/png')) throw new Error('manifest has no 512 png');
    const sw = await (await fetch(BASE + '/app/sw.js')).text();
    const cacheName = (sw.match(/const CACHE = '([^']+)'/) || [])[1];
    if (!cacheName || cacheName === 'lg-shell-v2') throw new Error('service worker cache version not bumped: ' + cacheName);
    return { touch_icons: sizes, cache: cacheName };
  });

  // ---------------- 1) iPhone, Safari tab (not installed) ----------------
  const tab = await iphone('tab');
  await step('iPhone Safari tab: install card on login screen, layout ok at 390px', async () => {
    await tab.page.goto(BASE + '/app/', { waitUntil: 'load' });
    await tab.page.waitForSelector('[data-test=mode-register]', { timeout: 10000 });
    if (!(await visible(tab.page, '[data-test=ios-install-card]'))) throw new Error('install card not shown in a Safari tab');
    const txt = await tab.page.$eval('[data-test=ios-install-card]', (el) => el.textContent);
    for (const want of ['הוסיפו את LifeGuard למסך הבית', 'הוספה למסך הבית', 'iOS 16.4']) {
      if (!txt.includes(want)) throw new Error('install card text missing: ' + want);
    }
    const dir = await tab.page.evaluate(() => getComputedStyle(document.body).direction);
    if (dir !== 'rtl') throw new Error('body direction ' + dir);
    const probs = await layoutProblems(tab.page, 'login');
    if (probs.length) throw new Error(probs.join(' | '));
    return { width: await tab.page.evaluate(() => innerWidth) };
  });

  await step('iPhone Safari tab: register by touch; home shows install card + "what works on iPhone"', async () => {
    await register(tab.page, U1);
    await tab.page.waitForSelector('[data-test=sos-button]', { visible: true, timeout: 10000 });
    if (!(await visible(tab.page, '[data-test=ios-install-card]'))) throw new Error('install card missing on home');
    const caps = await tab.page.$eval('[data-test=ios-capabilities]', (el) => el.textContent);
    for (const want of ['מעקב מיקום ברקע', 'זיהוי נפילה', 'ליווי הביתה: הטיימר רץ בשרת', 'רטט - לא נתמך']) {
      if (!caps.includes(want)) throw new Error('capabilities text missing: ' + want);
    }
    const tel = await tab.page.$$eval('a[href^="tel:"]', (as) => as.map((a) => a.getAttribute('href')));
    for (const n of ['tel:100', 'tel:101']) if (!tel.includes(n)) throw new Error('missing ' + n);
    return { tel };
  });

  await step('iPhone Safari tab: push button disabled and explained (needs home screen)', async () => {
    await go(tab.page, '#profile');
    await tab.page.waitForSelector('[data-test=push-unsupported]', { timeout: 10000 });
    const reason = await tab.page.$eval('[data-test=push-unsupported]', (el) => el.dataset.reason);
    if (reason !== 'ios_browser') throw new Error('reason ' + reason);
    const disabled = await tab.page.$eval('[data-test=push-enable]', (b) => b.disabled);
    if (!disabled) throw new Error('push button enabled in a Safari tab');
    const geoOk = await (async () => {
      await tap(tab.page, '[data-test=ios-capabilities] [data-test=geo-check]');
      return waitFor(async () => {
        const t = await tab.page.$eval('[data-test=ios-capabilities] [data-test=geo-check-msg]', (el) => el.textContent);
        return /המיקום זמין/.test(t) ? t : null;
      }, 10000, 'location check from the tap');
    })();
    return { reason, geo_check: geoOk.slice(0, 40) };
  });

  await step('iPhone: no horizontal scroll and text fields >= 16px on every screen', async () => {
    const all = [];
    for (const hash of ['#home', '#community', '#circle', '#profile', '#support']) {
      await go(tab.page, hash);
      await sleep(600);
      all.push(...await layoutProblems(tab.page, hash));
    }
    if (all.length) throw new Error(all.slice(0, 8).join(' | '));
    return { screens: 5 };
  });

  await step('iPhone Safari tab: "not now" hides the install card for this session', async () => {
    await go(tab.page, '#home');
    await tap(tab.page, '[data-test=ios-install-later]');
    await go(tab.page, '#community');
    await go(tab.page, '#home');
    if (await tab.page.$('[data-test=ios-install-card]')) throw new Error('install card came back');
    return { hidden: true };
  });

  await step('safe-area CSS present and applied (notch / home indicator)', async () => {
    const css = await tab.page.evaluate(() => [...document.styleSheets].flatMap((ss) => {
      try { return [...ss.cssRules].map((r) => r.cssText); } catch { return []; }
    }).join('\n'));
    for (const side of ['top', 'bottom', 'left', 'right']) {
      if (!css.includes('safe-area-inset-' + side)) throw new Error('no env(safe-area-inset-' + side + ') in CSS');
    }
    if (!/100dvh/.test(css)) throw new Error('no 100dvh');
    let override = 'unsupported';
    try {
      await tab.cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: { top: 47, bottom: 34, left: 0, right: 0 } });
      await sleep(300);
      const m = await tab.page.evaluate(() => ({
        top: parseFloat(getComputedStyle(document.querySelector('.topbar')).paddingTop),
        navPad: parseFloat(getComputedStyle(document.querySelector('#tabs')).paddingBottom),
      }));
      if (m.top < 47 || m.navPad < 34) throw new Error('insets not applied ' + JSON.stringify(m));
      override = m;
      await tab.cdp.send('Emulation.setSafeAreaInsetsOverride', { insets: {} });
    } catch (e) {
      if (!/wasn't found|not found|Unknown/i.test(String(e.message))) throw e;
    }
    return { inset_override: override };
  });

  await step('offline: app shell opens from the service worker cache with emergency numbers', async () => {
    const { page } = tab;
    const cached = await page.evaluate(async () => {
      await navigator.serviceWorker.ready;
      const keys = await caches.keys();
      const c = await caches.open(keys.find((k) => k.startsWith('lg-shell')));
      return { keys, urls: (await c.keys()).map((r) => new URL(r.url).pathname) };
    });
    for (const f of ['/app/', '/app/app.js', '/app/app.css', '/app/api.js', '/app/apple-touch-icon-180.png']) {
      if (!cached.urls.includes(f)) throw new Error('not precached: ' + f);
    }
    // offline for the page AND for the service worker (it fetches on the page's behalf)
    await page.setOfflineMode(true);
    const swTarget = await tab.browser.waitForTarget((t) => t.type() === 'service_worker' && t.url().startsWith(BASE), { timeout: 5000 });
    const swSession = await swTarget.createCDPSession();
    await swSession.send('Network.enable');
    await swSession.send('Network.emulateNetworkConditions', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    try {
      const res = await page.reload({ waitUntil: 'load', timeout: 20000 });
      if (!res || !res.fromServiceWorker()) throw new Error('page was not served by the service worker');
      await page.waitForSelector('[data-test=offline-banner]', { visible: true, timeout: 10000 });
      const banner = await page.$eval('#conn', (el) => el.textContent);
      const onLine = await page.evaluate(() => navigator.onLine);
      // navigator.onLine false -> "no internet"; otherwise the app notices the server is unreachable
      if (!(onLine ? /אין חיבור לשרת/ : /אין אינטרנט/).test(banner)) throw new Error(`banner (onLine=${onLine}): ${banner}`);
      if (/null/.test(banner)) throw new Error('banner renders "null": ' + banner);
      const tel = await page.$$eval('#conn a[href^="tel:"]', (as) => as.map((a) => a.getAttribute('href')));
      if (!tel.includes('tel:100') || !tel.includes('tel:101')) throw new Error('no emergency numbers offline: ' + tel);
      const apiReachable = await page.evaluate(() => fetch('/v1/health').then(() => true, () => false));
      if (apiReachable) throw new Error('API answered while offline - offline emulation did not apply');
      // the device-offline wording (navigator.onLine === false + 'offline' event, as iOS reports airplane mode)
      const offlineText = await page.evaluate(() => {
        Object.defineProperty(Navigator.prototype, 'onLine', { get: () => false, configurable: true });
        window.dispatchEvent(new Event('offline'));
        return document.querySelector('#conn').textContent;
      });
      if (!/אין אינטרנט/.test(offlineText) || !/שיחת טלפון לא צריכה אינטרנט/.test(offlineText)) throw new Error('offline text: ' + offlineText);
      return { cache: cached.keys, served_by_sw: true, navigator_onLine: onLine, offline_text: true };
    } finally {
      await swSession.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
      await page.setOfflineMode(false);
    }
  });

  // ---------------- 2) iPhone, home-screen app (navigator.standalone) ----------------
  const home = await iphone('home', { standalone: true });
  await step('home-screen app: no install card, push button enabled', async () => {
    await register(home.page, U2);
    await home.page.waitForSelector('[data-test=sos-button]', { visible: true, timeout: 10000 });
    if (await home.page.$('[data-test=ios-install-card]')) throw new Error('install card shown in standalone mode');
    await go(home.page, '#profile');
    await home.page.waitForSelector('[data-test=push-enable]', { timeout: 10000 });
    await sleep(500);
    if (await home.page.$('[data-test=push-unsupported]')) throw new Error('push marked unsupported in standalone');
    const disabled = await home.page.$eval('[data-test=push-enable]', (b) => b.disabled);
    if (disabled) throw new Error('push button disabled in standalone');
    const st = await home.page.$eval('[data-test=ios-standalone-state]', (el) => el.textContent);
    if (!/מהמסך הבית/.test(st)) throw new Error('standalone state: ' + st);
    return { standalone: true };
  });

  await step('home-screen app: SOS by touch (no vibrate API), wake-lock / iOS location note', async () => {
    const { page } = home;
    await go(page, '#home');
    await tap(page, '[data-test=sos-button]');                // short tap -> confirmation
    await tap(page, '[data-test=sos-confirm]');
    await page.waitForSelector('[data-test=incident-owner]', { timeout: 20000 });
    const line = await waitFor(() => page.$eval('[data-test=wake-line]', (el) => el.textContent).catch(() => null), 10000, 'wake line');
    if (!/באייפון: אם המסך ננעל/.test(line)) throw new Error('wake line: ' + line);
    const ok = await waitFor(() => page.$eval('[data-test=track-status]', (el) => el.textContent).then((t) => (/נשלח לשרת/.test(t) ? t : null)), 15000, 'location sent');
    const probs = await layoutProblems(page, 'incident');
    // end the test incident through the UI
    await tap(page, '[data-test=incident-resolve]');
    await page.waitForFunction(() => [...document.querySelectorAll('button')].some((b) => b.textContent.includes('כן, אני בטוח/ה')), { timeout: 5000 });
    await page.evaluate(() => [...document.querySelectorAll('button')].find((b) => b.textContent.includes('כן, אני בטוח/ה')).click());
    await page.waitForSelector('[data-test=incident-closed]', { timeout: 10000 });
    if (probs.length) throw new Error(probs.join(' | '));
    return { wake: line.slice(0, 50), track: ok.slice(0, 40) };
  });

  await step('home-screen app: enabling push (informational - headless Chrome, not Safari)', async () => {
    const { page } = home;
    await go(page, '#profile');
    await tap(page, '[data-test=push-enable]');
    const msg = await waitFor(() => page.$eval('[data-test=push-msg]', (el) => el.textContent).then((t) => (t && t !== 'נרשם...' ? t : null)), 30000, 'push result');
    if (!/נרשם בשרת/.test(msg)) throw new Error(msg);
    return { msg };
  }, { required: false });

  // ---------------- 3) old iOS (< 16.4): home-screen app without the Push API ----------------
  const old = await iphone('oldios', { standalone: true, oldIos: true });
  await step('old iOS home-screen app: push disabled with an honest "needs iOS 16.4" message', async () => {
    await old.page.goto(BASE + '/app/', { waitUntil: 'load' });
    await old.page.waitForSelector('[data-test=mode-login]', { timeout: 10000 });
    await fill(old.page, '#f-user', U2.username);
    await fill(old.page, '#f-pass', PW);
    await tap(old.page, '[data-test=login-submit]');
    await old.page.waitForSelector('#tabs:not([hidden])', { timeout: 15000 });
    await go(old.page, '#profile');
    await old.page.waitForSelector('[data-test=push-unsupported]', { timeout: 10000 });
    const r = await old.page.$eval('[data-test=push-unsupported]', (el) => ({ reason: el.dataset.reason, text: el.textContent }));
    if (r.reason !== 'ios_old' || !/16\.4/.test(r.text)) throw new Error(JSON.stringify(r));
    if (!(await old.page.$eval('[data-test=push-enable]', (b) => b.disabled))) throw new Error('push button enabled without Push API');
    return { reason: r.reason };
  });

  await step('zero console / CSP errors in all emulated iPhones', async () => {
    if (pageErrors.length) throw new Error(pageErrors.slice(0, 6).join(' | '));
    return { errors: 0 };
  });
} catch (e) {
  steps.push({ name: 'harness', ok: false, required: true, error: String(e && e.stack || e) });
  failed = true;
} finally {
  for (const b of browsers) {
    try { await b.browser.close(); } catch { /* ignore */ }
    try { fs.rmSync(b.dir, { recursive: true, force: true }); } catch { /* ignore */ }
  }
}

const summary = { ok: !failed, base: BASE, headless: HEADLESS, emulated: 'Chrome with iPhone UA/viewport/touch - NOT Safari', steps, page_errors: pageErrors };
console.log('E2E_SUMMARY ' + JSON.stringify(summary));
process.exit(failed ? 1 : 0);

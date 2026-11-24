// hazard.js - "התראות סכנה מ-AI" in the PWA: settings, the alert sheet with its six actions, foreground listening,
// and the on-device feedback log. Mirrors the Windows app (hazard_alerts.py / hazard_monitor.py / hazard_alert_ui.py).
//
// Honest about the browser: a web page can use the microphone ONLY while it is open on the screen. iOS Safari (and
// other browsers) suspend it in the background - so listening is an opt-in mode, OFF by default, and the status line
// always says what is really happening. Audio is analysed in memory on the device (Web Worker) and dropped: nothing is
// recorded, stored or sent. A detection NEVER starts SOS, contacts anyone or dials - it only offers, and every
// significant action needs an explicit confirmation.
import {
  STRINGS, LABELS_EN, EXPLAIN_EN, FALSE_REASONS, REASONS_EN, MIN_SEVERITY_OPTIONS, MIN_SEVERITY_OPTIONS_EN, RESPONSES,
  WINDOW_S, cleanPrefs, MonitorCore, contactsMessage, fmt,
} from './hazard_core.js';

export const MODEL_CACHE = 'lg-hazard-models-v1';          // also in sw.js (kept on activate, served offline)
const M_YAMNET = '/app/models/yamnet.json';
const M_BIN = '/app/models/yamnet.bin';
const M_HEADS = '/app/models/hazard_heads.json';
const LOG_DB = 'lg-hazard';
const LOG_STORE = 'alerts';
const LOG_MAX = 300;                                        // per user, newest kept
const LOG_DAYS = 90;
const SEV_CLASS = { high: 'sev-high', medium: 'sev-medium', low: 'sev-low', info: 'sev-info' };
// extra PWA wording (not in hazard_alerts.STRINGS: the browser has no SOS countdown and no desktop dialer)
const PWA_STR = {
  he: { confirm_sos: 'לשלוח עכשיו קריאת חירום? (כמו לחיצה על כפתור ה-SOS ואישור - לאנשי הקשר ולמתנדב/ת קרוב/ה, עם המיקום)', yes_sos: '🚨 כן, לשלוח', yes_contacts: 'כן, לשלוח', yes_dial: '📞 כן, חיוג {n}' },
  en: { confirm_sos: 'Send an emergency call now? (same as pressing the SOS button and confirming - to your contacts and a nearby volunteer, with your location)', yes_sos: '🚨 Yes, send', yes_contacts: 'Yes, send', yes_dial: '📞 Yes, dial {n}' },
};
const LISTEN_HE = {
  off: 'כבוי',
  starting: 'מבקש גישה למיקרופון...',
  loading: 'טוען את מודל הזיהוי...',
  running: '🎙️ מאזין עכשיו - הניתוח במכשיר בלבד, שום דבר לא מוקלט, לא נשמר ולא נשלח',
  paused: '⏸️ מושהה: האפליקציה לא על המסך. הדפדפן (ובמיוחד באייפון) עוצר את המיקרופון ברקע - אין זיהוי עד שחוזרים לאפליקציה.',
  needs_tap: 'ההאזנה לא פעילה כרגע - צריך להקיש "הפעלה" (הדפדפן מאפשר מיקרופון רק אחרי הקשה, ובכל פתיחה מחדש של האפליקציה).',
  error: 'ההאזנה לא פעילה',
};

const UA = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
const IS_IOS = /iPhone|iPad|iPod/.test(UA) || (/Macintosh/.test(UA) && typeof navigator !== 'undefined' && navigator.maxTouchPoints > 1);
const CAN_VIBRATE = !IS_IOS && typeof navigator !== 'undefined' && typeof navigator.vibrate === 'function';

/**
 * ctx (from app.js): { h, icon, secHead, pill, store, userKey, username, api, errText, triggerSOS, canShow, geoLast,
 *   serverNow, connected, emergency: [[num, name]...], telRow, addNotice, go, beep, onCleanup }
 */
export function createHazard(ctx) {
  const { h } = ctx;
  const S = {
    user: null, prefs: cleanPrefs({}), monitor: null, clockOffset: 0, modals: new Map(), seqRule: null,
    listen: { state: 'off', detail: '', windows: 0, skipped: 0, lastMs: 0, quality: null, progress: null },
    cap: null, worker: null, workerReady: null, busy: false, pending: null, reqId: 0, waits: new Map(),
  };
  const subs = new Set();
  const notify = () => { for (const f of [...subs]) { try { f(); } catch { /* a closed view */ } } };
  const now = () => Date.now() / 1000 + S.clockOffset;
  const strings = () => ({ ...STRINGS[S.prefs.language] || STRINGS.he, ...PWA_STR[S.prefs.language] || PWA_STR.he });

  // ---------------------------------------------------------------- prefs (localStorage, per user; nothing secret)
  const prefsKey = () => ctx.userKey('hazardPrefs');
  function loadPrefs() { S.prefs = cleanPrefs(ctx.store.getJSON(prefsKey(), {}) || {}, { canVibrate: CAN_VIBRATE }); return S.prefs; }
  function savePrefs(patch) {
    S.prefs = cleanPrefs({ ...S.prefs, ...patch }, { canVibrate: CAN_VIBRATE });
    ctx.store.setJSON(prefsKey(), S.prefs);
    notify();
    return S.prefs;
  }

  // ---------------------------------------------------------------- feedback log (IndexedDB, per user, no audio/location)
  let dbP = null;
  function db() {
    if (!dbP) {
      dbP = new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no IndexedDB')); return; }
        const r = indexedDB.open(LOG_DB, 1);
        r.onupgradeneeded = () => { r.result.createObjectStore(LOG_STORE, { keyPath: 'id' }).createIndex('owner', 'owner'); };
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      });
      dbP.catch(() => { dbP = null; });
    }
    return dbP;
  }
  async function tx(mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const t = d.transaction(LOG_STORE, mode);
      const st = t.objectStore(LOG_STORE);
      let out;
      Promise.resolve(fn(st)).then((v) => { out = v; });
      t.oncomplete = () => resolve(out);
      t.onerror = () => reject(t.error);
      t.onabort = () => reject(t.error);
    });
  }
  const req = (r) => new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(r.error); });
  async function logList(owner = S.user) {
    if (!owner) return [];
    const rows = await tx('readonly', (st) => req(st.index('owner').getAll(owner)));
    return (rows || []).sort((a, b) => b.ts - a.ts);
  }
  async function logPrune(owner) {
    const rows = await logList(owner);
    const cutoff = now() - LOG_DAYS * 86400;
    const drop = rows.filter((r, i) => i >= LOG_MAX || r.ts < cutoff);
    if (drop.length) await tx('readwrite', (st) => { for (const r of drop) st.delete(r.id); });
  }
  /** hazard_alerts.record_alert: one record per event (not per detection). */
  async function logRecord(alert, shown, why) {
    if (!S.user) return;
    const m = alert.meta || {};
    const rec = {
      id: alert.id, owner: S.user, ts: alert.first_ts, category: alert.category, severity: alert.severity,
      confidence: alert.confidence, count: alert.count, model_version: m.model_version || null,
      shown: !!shown, not_shown_reason: why || null, response: alert.response || null, reason: alert.reason || null,
      label_strength: alert.response === 'false_alarm' ? 'user_reported' : null, responded_at: null,
      meta: { score: m.score ?? null, threshold: m.threshold ?? null, model: m.model ?? null, quality: m.quality ?? null,
        near_threshold: m.near_threshold ?? null, rule: m.rule ?? null },
    };
    try { await tx('readwrite', (st) => st.put(rec)); await logPrune(S.user); } catch { /* the log never blocks an alert */ }
    notify();
  }
  /** hazard_alerts.record_response + AlertManager.close (engine.hazard_respond). Never changes thresholds. */
  async function respond(alert, response, reason = null) {
    if (!RESPONSES.includes(response)) throw new Error(response);
    if (reason != null && !(reason in FALSE_REASONS)) throw new Error(reason);
    alert.response = response; alert.reason = reason;
    if (S.monitor) S.monitor.manager.close(alert, response);
    try {
      await tx('readwrite', async (st) => {
        const r = await req(st.get(alert.id));
        if (!r) return;
        Object.assign(r, { response, reason, label_strength: response === 'false_alarm' ? 'user_reported' : null, responded_at: now() });
        st.put(r);
      });
    } catch { /* recorded best effort */ }
    notify();
  }

  // ---------------------------------------------------------------- the alert sheet
  function sevLabel(s, sev) { return `${s.severity}: ${s.sev[sev] || sev}`; }

  function locationText(s) {
    const g = ctx.geoLast();
    if (!g || g.lat == null) return `📍 ${s.no_location}`;
    const age = ctx.serverNow() - Number(g.captured_at || ctx.serverNow());
    const approx = g.accuracy_m > 0 && g.accuracy_m <= 150 ? '' : ` (${s.approx})`;
    const when = age > 120 ? ' · ' + fmt(s.min_ago, { m: Math.floor(age / 60) }) : '';
    return `📍 ${s.location}: ${Number(g.lat).toFixed(4)}, ${Number(g.lon).toFixed(4)}${approx}${when}`;
  }

  function attention(alert) {
    const p = S.prefs;
    if (p.sound) { try { ctx.beep(); } catch { /* audio blocked */ } }
    if (p.vibration && CAN_VIBRATE) { try { navigator.vibrate([300, 150, 300]); } catch { /* ignore */ } }
    if (typeof document !== 'undefined' && document.visibilityState === 'hidden') systemNotification(alert);
  }

  async function systemNotification(alert) {
    try {
      if (!('Notification' in window) || Notification.permission !== 'granted' || !('serviceWorker' in navigator)) return;
      const reg = await navigator.serviceWorker.getRegistration('/app/');
      if (!reg) return;
      const s = strings();
      const label = s.dir === 'rtl' ? alert.label_he || alert.category : LABELS_EN[alert.category] || alert.category;
      await reg.showNotification(s.title, {
        body: fmt(s.notif_body, { label, sev: s.sev[alert.severity] || alert.severity }), tag: 'hazard-' + alert.id,
        dir: s.dir, lang: S.prefs.language, icon: '/app/icon-192.png', badge: '/app/icon-192.png', data: { url: '/app/#home', type: 'hazard' },
      });
    } catch { /* the in-app sheet is still there when the user comes back */ }
  }

  function showAlert(kind, alert) {
    const open = S.modals.get(alert.id);
    if (open) { open.refresh(alert, kind === 'update'); if (kind === 'update') attention(alert); return; }
    const m = buildModal(alert);
    S.modals.set(alert.id, m);
    document.body.append(m.el);
    document.body.classList.add('hz-open');
    m.focus();
    attention(alert);
    notify();
  }

  function closeModal(id) {
    const m = S.modals.get(id);
    if (!m) return;
    m.closed = true;
    clearTimeout(m.pollTimer);
    m.el.remove();
    S.modals.delete(id);
    if (!S.modals.size) document.body.classList.remove('hz-open');
    notify();
  }

  function buildModal(alert0) {
    const s = strings();
    const rtl = s.dir === 'rtl';
    const lang = S.prefs.language;
    const m = { alert: alert0, closed: false, pollTimer: null };
    const titleId = 'hz-t-' + alert0.id;
    const kindEl = h('h2', { class: 'hz-kind', id: titleId, 'data-test': 'hz-kind' });
    const when = h('p', { class: 'hz-when', 'data-test': 'hz-updated' });
    const head = h('header', { class: 'hz-head' }, h('p', { class: 'hz-title' }, '⚠️ ' + s.title), kindEl, when);
    const sevChip = h('span', { class: 'hz-chip hz-sev', 'data-test': 'hz-sev' });
    const confChip = h('span', { class: 'hz-chip', 'data-test': 'hz-conf' });
    const explain = h('p', { class: 'hz-explain', 'data-test': 'hz-explain' });
    const loc = h('p', { class: 'small muted', 'data-test': 'hz-loc' });
    const status = h('p', { class: 'hz-status', 'data-test': 'hz-status', role: 'status', 'aria-live': 'polite' });
    const setStatus = (text, cls = '') => { status.className = 'hz-status ' + cls; status.textContent = text || ''; };
    const btn = (key, cls, onclick, label) => h('button', { type: 'button', class: 'hz-act ' + cls, 'data-test': 'hz-' + key, onclick }, label || s[key]);
    const online = ctx.connected();
    const b = {
      sos: btn('sos', 'red', () => doSos()),
      safe_place: btn('safe_place', '', () => doSafe()),
      contacts: btn('contacts', online ? '' : 'off', () => doContacts(), online ? s.contacts : `${s.contacts} (${s.not_connected})`),
      call: btn('call', 'navy', () => doCall()),
      false_alarm: btn('false_alarm', 'ghost', () => showReasons()),
      dismiss: btn('dismiss', 'ghost', () => doDismiss()),
    };
    b.safe_place.dataset.test = 'hz-safe';
    b.false_alarm.dataset.test = 'hz-false';
    if (!online) b.contacts.disabled = true;      // like Windows: not shown as available without a server connection
    const main = h('div', { class: 'hz-main', 'data-test': 'hz-main' },
      h('p', { class: 'hz-q' }, s.question),
      h('div', { class: 'hz-grid' }, b.sos, b.safe_place, b.contacts, b.call),
      h('div', { class: 'hz-low' }, b.false_alarm, b.dismiss));
    const extra = h('div', { class: 'hz-extra', 'data-test': 'hz-extra' });
    const sheet = h('div', { class: 'hz-sheet' }, head,
      h('div', { class: 'hz-body' }, h('div', { class: 'hz-chips' }, sevChip, confChip), explain,
        h('p', { class: 'hz-note', 'data-test': 'hz-not-verified' }, s.not_verified), loc, main, extra, status));
    m.el = h('div', {
      class: 'hz-overlay', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': titleId, 'data-test': 'hz-modal',
      'data-alert': alert0.id, 'data-category': alert0.category, dir: s.dir, lang,
    }, sheet);
    m.el.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') doDismiss(); });
    m.focus = () => { try { kindEl.tabIndex = -1; kindEl.focus({ preventScroll: true }); } catch { /* ignore */ } };

    m.refresh = (alert, updated = false) => {
      m.alert = alert;
      const sev = alert.severity;
      sheet.className = 'hz-sheet ' + (SEV_CLASS[sev] || 'sev-low');
      sevChip.textContent = sevLabel(s, sev);
      confChip.textContent = `${s.confidence}: ${Math.round(alert.confidence * 100)}%`;
      kindEl.textContent = rtl ? alert.label_he || alert.category : LABELS_EN[alert.category] || alert.category;
      explain.textContent = rtl ? alert.explanation || '' : EXPLAIN_EN[alert.category] || '';
      const t = new Date(alert.first_ts * 1000 - S.clockOffset * 1000);
      when.textContent = updated || alert.count > 1 ? `${s.updated} (×${alert.count})` : t.toLocaleTimeString(rtl ? 'he-IL' : 'en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      loc.textContent = locationText(s);
    };
    m.refresh(alert0);

    // inline two-step confirmation (no browser dialogs): replaces the action buttons until answered
    function confirm(question, yes, onYes) {
      main.hidden = true;
      const no = h('button', { type: 'button', class: 'ghost', 'data-test': 'hz-confirm-no', onclick: () => { extra.replaceChildren(); main.hidden = false; } }, s.no);
      yes.dataset.test = 'hz-confirm-yes';
      yes.addEventListener('click', (ev) => onYes(ev));
      extra.replaceChildren(h('div', { class: 'hz-confirm', role: 'group' }, h('p', { class: 'big' }, question), h('div', { class: 'li-actions' }, yes, no)));
      try { yes.focus({ preventScroll: true }); } catch { /* ignore */ }
    }
    const back = () => { extra.replaceChildren(); main.hidden = false; };

    function doSos() {
      confirm(s.confirm_sos, h('button', { type: 'button', class: 'red' }, s.yes_sos), async () => {
        respond(m.alert, 'sos');                     // cooldown applied at once; the log write follows
        closeModal(m.alert.id);
        ctx.triggerSOS('loud', 'unknown');           // the PWA's own SOS flow (outbox, retries, incident screen)
      });
    }

    function doSafe() {
      respond(m.alert, 'safe_place');
      closeModal(m.alert.id);
      // the PWA has no safe-place search: say so, and open what it has (the environment map) - nothing faked
      ctx.go('#map');
      ctx.addNotice({ id: 'hz-safe', title: s.safe_place, text: s.safe_na });
    }

    function doContacts() {
      confirm(s.confirm_contacts, h('button', { type: 'button', class: 'secondary' }, s.yes_contacts), async (ev) => {
        ev.currentTarget.disabled = true;
        back();
        b.contacts.disabled = true;
        setStatus(s.sending, 'muted');
        try {
          const res = await ctx.api('POST', '/trusted/notify', { text: contactsMessage(m.alert, lang), type: 'info' });
          await respond(m.alert, 'contacts');
          const to = res.sent_to || [];
          if (!to.length) { setStatus(s.no_contacts, 'error'); return; }
          setStatus(fmt(s.sent, { n: to.length }));
          pollDelivery(res.notification_ids || {}, to.length, 0);
        } catch (e) {
          b.contacts.disabled = !ctx.connected();
          setStatus(fmt(s.send_failed, { e: e && e.offline ? s.offline : ctx.errText(e) }), 'error');
        }
      });
    }

    // "delivered" only when the contact's app ACKed (server: notifications.received_at) - not just "sent"
    function pollDelivery(ids, n, round) {
      if (m.closed || round > 20 || !Object.keys(ids).length) return;
      m.pollTimer = setTimeout(async () => {
        try {
          const st = await ctx.api('POST', '/notifications/status', { ids });
          const ok = Object.values(st || {}).filter(Boolean).length;
          if (m.closed) return;
          setStatus(fmt(s.delivered, { ok, n }), ok ? 'okmsg' : 'wait');
          if (ok >= n) return;
        } catch { /* keep the last real status */ }
        pollDelivery(ids, n, round + 1);
      }, round === 0 ? 2000 : 3000);
    }

    function doCall() {
      const [num] = ctx.emergency.find(([, name]) => name === 'משטרה') || ctx.emergency[0];
      // the confirmation's "yes" IS the tel: link: one tap after confirming, and the phone itself asks before calling
      const yes = h('a', { class: 'btn red', href: 'tel:' + num, 'data-num': num }, fmt(s.yes_dial, { n: num }));
      confirm(fmt(s.confirm_call, { n: num }), yes, async () => {
        if (m.alert.response !== 'call') await respond(m.alert, 'call');
        setStatus(fmt(s.dial_hint, { n: num }));
        extra.replaceChildren(ctx.telRow());
        main.hidden = false;
      });
    }

    function showReasons() {
      main.hidden = true;
      const names = rtl ? FALSE_REASONS : REASONS_EN;
      const group = 'hz-r-' + m.alert.id;
      const radios = Object.entries(names).map(([key, label]) => h('label', { class: 'inline hz-reason' },
        h('input', { type: 'radio', name: group, value: key, 'data-test': 'hz-reason-' + key }), label));
      const send = h('button', { type: 'button', class: 'secondary', 'data-test': 'hz-false-confirm' }, s.send_false);
      send.addEventListener('click', async () => {
        const sel = extra.querySelector(`input[name="${group}"]:checked`);
        respond(m.alert, 'false_alarm', sel ? sel.value : null);
        closeModal(m.alert.id);
        ctx.addNotice({ id: 'hz-false', title: s.false_alarm, text: s.false_saved });
      });
      extra.replaceChildren(h('div', { class: 'hz-reasons', role: 'radiogroup', 'aria-label': s.why }, h('p', null, s.why), ...radios,
        h('div', { class: 'li-actions' }, send, h('button', { type: 'button', class: 'ghost', onclick: back }, s.no))));
    }

    function doDismiss() {
      if (m.closed) return;
      if (m.alert.response == null) respond(m.alert, 'dismiss');   // sheet closes at once; the log write follows
      closeModal(m.alert.id);
    }
    return m;
  }

  // ---------------------------------------------------------------- monitor (detections -> alerts)
  function newMonitor() {
    S.monitor = new MonitorCore({
      sequenceRule: S.seqRule, canShow: () => ctx.canShow(), prefsFn: () => S.prefs, clock: now,
      record: (alert, shown, why) => { logRecord(alert, shown, why); },
      onAlert: (kind, alert) => showAlert(kind, alert),
    });
  }

  function onReport(rep) {
    S.listen.windows += 1;
    S.listen.quality = rep && rep.quality ? rep.quality.status : null;
    if (S.monitor && rep) S.monitor.process([rep], now());
    notify();
  }

  // ---------------------------------------------------------------- models (downloaded once, after opt-in)
  async function modelCache() {
    try { return 'caches' in self ? await caches.open(MODEL_CACHE) : null; } catch { return null; }
  }

  async function getJSON(cache, url) {
    try {
      const r = await fetch(url, { cache: 'no-store' });
      if (!r.ok) throw new Error(`${url} -> ${r.status}`);
      if (cache) await cache.put(url, r.clone());
      return await r.json();
    } catch (e) {
      const c = cache && await cache.match(url);
      if (c) return c.json();
      throw e;
    }
  }

  async function sha256hex(buf) {
    if (!(self.crypto && crypto.subtle)) return null;
    const d = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
    return [...d].map((v) => v.toString(16).padStart(2, '0')).join('');
  }

  async function verified(buf, man) {
    if (!buf || buf.byteLength !== man.bin_bytes) return false;
    const hx = await sha256hex(buf);
    return hx == null || hx === man.bin_sha256;
  }

  async function download(url, total, onProgress) {
    const r = await fetch(url, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    if (!r.body || !r.body.getReader) return r.arrayBuffer();
    const reader = r.body.getReader();
    const out = new Uint8Array(total);
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (got + value.length > total) throw new Error('model file larger than its manifest');
      out.set(value, got);
      got += value.length;
      onProgress(got, total);
    }
    return got === total ? out.buffer : out.buffer.slice(0, got);
  }

  async function loadModels(onProgress) {
    const cache = await modelCache();
    const man = await getJSON(cache, M_YAMNET);
    const heads = await getJSON(cache, M_HEADS);
    let bin = null;
    const hit = cache && await cache.match(M_BIN);
    if (hit) {
      bin = await hit.arrayBuffer();
      if (!(await verified(bin, man))) { bin = null; await cache.delete(M_BIN); }
    }
    if (!bin) {
      bin = await download(M_BIN, man.bin_bytes, onProgress);
      if (!(await verified(bin, man))) throw new Error('קובץ המודל שהורד פגום - נסו שוב');
      if (cache) { try { await cache.put(M_BIN, new Response(bin.slice(0), { headers: { 'Content-Type': 'application/octet-stream' } })); } catch { /* storage full: works this time */ } }
    }
    return { man, bin, heads };
  }

  function ensureWorker() {
    if (S.workerReady) return S.workerReady;
    S.workerReady = (async () => {
      setListen(S.listen.state === 'off' ? 'off' : 'loading');
      const { man, bin, heads } = await loadModels((got, total) => {
        S.listen.progress = [got, total];
        notify();
      });
      S.listen.progress = null;
      S.seqRule = heads.sequence && ['k', 't_low', 's_low', 'span_s'].every((k) => k in heads.sequence) ? heads.sequence : null;
      if (S.monitor) S.monitor.sequenceRule = S.seqRule;
      const w = new Worker('/app/hazard_worker.js', { type: 'module' });
      await new Promise((resolve, reject) => {
        const t = setTimeout(() => reject(new Error('הטעינה של מנוע הזיהוי לא הסתיימה')), 60000);
        w.onmessage = (ev) => {
          const d = ev.data || {};
          if (d.type === 'ready') { clearTimeout(t); resolve(); } else if (d.type === 'error') { clearTimeout(t); reject(new Error(d.message)); }
        };
        w.onerror = (e) => { clearTimeout(t); reject(new Error((e && e.message) || 'Web Worker לא נטען')); };
        w.postMessage({ type: 'init', yamnet: man, bin, heads }, [bin]);
      });
      w.onmessage = (ev) => onWorker(ev.data || {});
      S.worker = w;
      notify();
      return w;
    })();
    S.workerReady.catch(() => { S.workerReady = null; });
    return S.workerReady;
  }

  function onWorker(d) {
    const wait = d.id != null ? S.waits.get(d.id) : null;
    if (wait) { S.waits.delete(d.id); wait(d); return; }
    if (d.type === 'report') {
      S.busy = false;
      if (d.ms) S.listen.lastMs = d.ms;
      if (!d.error) onReport(d.report);
      if (S.pending) { const p = S.pending; S.pending = null; sendWindow(p.audio, p.sr); }
    }
  }

  function sendWindow(audio, sr) {
    if (!S.worker) return;
    if (S.busy) { if (S.pending) S.listen.skipped += 1; S.pending = { audio, sr }; return; }   // queue of one, like Windows
    S.busy = true;
    S.worker.postMessage({ type: 'window', id: null, audio, sr }, [audio.buffer]);
  }

  async function workerCall(msg, transfer) {
    const w = await ensureWorker();
    const id = ++S.reqId;
    return new Promise((resolve) => { S.waits.set(id, resolve); w.postMessage({ ...msg, id }, transfer || []); });
  }

  // ---------------------------------------------------------------- microphone capture (foreground only)
  function setListen(state, detail = '') {
    S.listen.state = state;
    S.listen.detail = detail;
    notify();
  }

  function micDeniedText() {
    return IS_IOS
      ? 'אין הרשאה למיקרופון. באייפון: הגדרות > אפליקציות > Safari > מיקרופון (או "אאפשר" כשהדפדפן שואל), ואז הקישו שוב "הפעלה".'
      : 'הגישה למיקרופון נחסמה - אפשרו מיקרופון לאתר הזה בהגדרות הדפדפן, ואז הקישו שוב "הפעלה".';
  }

  /** Must run inside a tap (iOS: the AudioContext and the permission prompt need a user gesture). */
  async function startListening() {
    if (S.cap || ['starting', 'loading'].includes(S.listen.state)) return true;
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C || !(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
      setListen('error', 'הדפדפן הזה לא מאפשר גישה למיקרופון לאפליקציית רשת.');
      return false;
    }
    let actx = null, stream = null;
    try {
      actx = new C();
      const resumed = actx.resume ? actx.resume().catch(() => {}) : null;   // inside the gesture
      setListen('starting');
      // raw audio, like Windows (no noise suppression: it would erase gunshots / breaking glass)
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
      await resumed;
      setListen('loading');
      await ensureWorker();
      const src = actx.createMediaStreamSource(stream);
      let node;
      const winLen = Math.round(WINDOW_S * actx.sampleRate);
      const cap = { actx, stream, src, node: null, sink: null, buf: new Float32Array(winLen), n: 0, winLen, sr: actx.sampleRate };
      const onBlock = (block) => {
        let i = 0;
        while (i < block.length) {
          const k = Math.min(block.length - i, cap.winLen - cap.n);
          cap.buf.set(block.subarray(i, i + k), cap.n);
          cap.n += k; i += k;
          if (cap.n === cap.winLen) {
            const w = cap.buf;
            cap.buf = new Float32Array(cap.winLen);
            cap.n = 0;
            sendWindow(w, cap.sr);
          }
        }
      };
      if (actx.audioWorklet && typeof AudioWorkletNode !== 'undefined') {
        await actx.audioWorklet.addModule('/app/hazard_worklet.js');
        node = new AudioWorkletNode(actx, 'lg-hazard-capture', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        node.port.onmessage = (ev) => onBlock(ev.data);
      } else {
        node = actx.createScriptProcessor(4096, 1, 1);           // older browsers
        node.onaudioprocess = (ev) => onBlock(new Float32Array(ev.inputBuffer.getChannelData(0)));
      }
      const sink = actx.createGain();
      sink.gain.value = 0;                                        // nothing is played back
      src.connect(node); node.connect(sink); sink.connect(actx.destination);
      cap.node = node; cap.sink = sink;
      for (const t of stream.getAudioTracks()) t.addEventListener('ended', () => interrupted('המיקרופון נעצר על ידי המערכת'));
      S.cap = cap;
      S.listen.windows = 0; S.listen.skipped = 0;
      setListen(document.visibilityState === 'hidden' ? 'paused' : 'running');
      return true;
    } catch (e) {
      try { if (stream) for (const t of stream.getTracks()) t.stop(); } catch { /* ignore */ }
      try { if (actx) actx.close(); } catch { /* ignore */ }
      const denied = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError');
      const noMic = e && (e.name === 'NotFoundError' || e.name === 'OverconstrainedError');
      setListen('error', denied ? micDeniedText() : noMic ? 'לא נמצא מיקרופון במכשיר.' : `לא ניתן להפעיל: ${(e && e.message) || e}`);
      return false;
    }
  }

  function teardownCapture() {
    const c = S.cap;
    S.cap = null;
    if (!c) return;
    try { for (const t of c.stream.getTracks()) t.stop(); } catch { /* ignore */ }
    try { c.src.disconnect(); c.node.disconnect(); c.sink.disconnect(); } catch { /* ignore */ }
    try { if (c.node.port) c.node.port.onmessage = null; } catch { /* ignore */ }
    try { c.actx.close(); } catch { /* ignore */ }
  }

  function interrupted(why) {
    if (!S.cap) return;
    teardownCapture();
    setListen('needs_tap', why);
  }

  function stopListening() {
    teardownCapture();
    S.pending = null;
    if (S.worker) { S.worker.terminate(); S.worker = null; }
    S.workerReady = null;
    S.busy = false;
    setListen('off');
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', () => {
      if (!S.cap) return;
      if (document.visibilityState === 'hidden') { setListen('paused'); return; }
      const live = S.cap.stream.getAudioTracks().some((t) => t.readyState === 'live');
      if (!live) { interrupted('המיקרופון נעצר כשהאפליקציה עברה לרקע'); return; }
      S.cap.actx.resume().then(() => setListen(S.cap && S.cap.actx.state === 'running' ? 'running' : 'needs_tap',
        S.cap && S.cap.actx.state === 'running' ? '' : 'הדפדפן השהה את השמע')).catch(() => interrupted('הדפדפן השהה את השמע'));
    });
  }

  function listenText() {
    const L = S.listen;
    let t = LISTEN_HE[L.state] || L.state;
    if (L.state === 'loading' && L.progress) t = `מוריד את מודל הזיהוי (פעם אחת בלבד): ${(L.progress[0] / 1e6).toFixed(1)} / ${(L.progress[1] / 1e6).toFixed(1)} MB`;
    if (L.state === 'running') {
      t += ` · נותחו ${L.windows} קטעים`;
      if (L.quality === 'unavailable') t += ' · ⚠️ המיקרופון מחזיר שקט מוחלט - כנראה מושתק או חסום';
    }
    if (L.detail) t += (L.state === 'error' ? ': ' : ' - ') + L.detail;
    return t;
  }

  // ---------------------------------------------------------------- views
  function sub(fn) { subs.add(fn); ctx.onCleanup(() => subs.delete(fn)); }

  /** Profile: "התראות סכנה מ-AI" */
  function settingsCard() {
    const p = S.prefs;
    const sw = (id, test, checked, label, disabled) => {
      const cb = h('input', { type: 'checkbox', class: 'switch', id, 'data-test': test, checked: !!checked, disabled: !!disabled });
      return [cb, h('label', { class: 'inline', for: id }, cb, label)];
    };
    const [listen, listenRow] = sw('hz-listen', 'hz-listen', p.listen && S.listen.state !== 'error', 'האזנה לסכנה כשהאפליקציה פתוחה');
    const listenStatus = h('p', { class: 'small', 'data-test': 'hz-listen-status', role: 'status', 'aria-live': 'polite' });
    const [popups, popupsRow] = sw('hz-popups', 'hz-popups', p.popups, 'חלונות קופצים על סכנה שזוהתה');
    const [sound, soundRow] = sw('hz-sound', 'hz-sound', p.sound, 'צליל התראה');
    const [vib, vibRow] = sw('hz-vibration', 'hz-vibration', p.vibration && CAN_VIBRATE, 'רטט', !CAN_VIBRATE);
    const sevSel = h('select', { id: 'hz-min-sev', 'data-test': 'hz-min-sev' },
      Object.entries(MIN_SEVERITY_OPTIONS).map(([k, v]) => h('option', { value: k }, v)));
    sevSel.value = p.min_severity;
    const langSel = h('select', { id: 'hz-lang', 'data-test': 'hz-lang' }, h('option', { value: 'he' }, 'עברית'), h('option', { value: 'en' }, 'English'));
    langSel.value = p.language;
    const saved = h('p', { class: 'small', 'data-test': 'hz-saved', role: 'status' });
    const ok = () => { saved.className = 'okmsg small'; saved.textContent = '✅ נשמר במכשיר הזה'; };
    for (const [el, key] of [[popups, 'popups'], [sound, 'sound'], [vib, 'vibration']]) el.addEventListener('change', () => { savePrefs({ [key]: el.checked }); ok(); });
    sevSel.addEventListener('change', () => { savePrefs({ min_severity: sevSel.value }); ok(); });
    langSel.addEventListener('change', () => { savePrefs({ language: langSel.value }); ok(); });
    listen.addEventListener('change', async () => {
      if (listen.checked) {
        savePrefs({ listen: true });
        const started = await startListening();
        if (!started) { listen.checked = false; savePrefs({ listen: false }); }
      } else {
        stopListening();
        savePrefs({ listen: false });
      }
    });
    const logLine = h('p', { class: 'small muted', 'data-test': 'hz-log-line' });
    const clearLog = h('button', { type: 'button', class: 'link', 'data-test': 'hz-log-clear' }, 'מחיקת היומן');
    clearLog.addEventListener('click', async () => {
      try { const rows = await logList(); await tx('readwrite', (st) => { for (const r of rows) st.delete(r.id); }); } catch { /* ignore */ }
      drawLog();
    });
    const modelLine = h('p', { class: 'small muted', 'data-test': 'hz-model-line' });
    const delModel = h('button', { type: 'button', class: 'link', 'data-test': 'hz-model-delete' }, 'מחיקת המודל מהמכשיר');
    delModel.addEventListener('click', async () => {
      stopListening(); savePrefs({ listen: false }); listen.checked = false;
      try { await caches.delete(MODEL_CACHE); } catch { /* ignore */ }
      drawModel();
    });
    const drawLog = async () => {
      let rows = [];
      try { rows = await logList(); } catch { rows = []; }
      const fa = rows.filter((r) => r.response === 'false_alarm').length;
      logLine.textContent = rows.length
        ? `יומן ההתראות במכשיר הזה: ${rows.length} אירועים (${fa} סומנו כהתראת שווא). נשמרים רק סוג, חומרה, ביטחון, גרסת מודל ותגובה - בלי אודיו ובלי מיקום, עד ${LOG_DAYS} יום. התראת שווא היא משוב שלא אומת - היא לא משנה את הספים.`
        : 'יומן ההתראות במכשיר הזה ריק.';
      clearLog.hidden = !rows.length;
    };
    const drawModel = async () => {
      let has = false;
      // caches.has first: caches.open() would create an (empty) model cache just by looking
      try { has = (await caches.has(MODEL_CACHE)) && !!(await (await caches.open(MODEL_CACHE)).match(M_BIN)); } catch { has = false; }
      modelLine.textContent = has ? 'מודל הזיהוי (YAMNet + ראשי הזיהוי, ~15MB) שמור במכשיר הזה.' : 'מודל הזיהוי עוד לא הורד. הוא יורד פעם אחת (~15MB) רק כשמפעילים את ההאזנה.';
      delModel.hidden = !has;
    };
    const draw = () => {
      listenStatus.textContent = listenText();
      listenStatus.className = 'small ' + (S.listen.state === 'error' ? 'error' : S.listen.state === 'running' ? 'okmsg' : 'muted');
      if (S.listen.state === 'error' || S.listen.state === 'off') listen.checked = S.listen.state !== 'error' && S.prefs.listen;
    };
    draw(); drawLog(); drawModel();
    sub(draw); sub(drawLog); sub(drawModel);
    return h('div', { class: 'card', 'data-test': 'hz-settings' },
      ctx.secHead('h2', 'mic', 'red', 'התראות סכנה מ-AI'),
      h('p', { class: 'help' }, 'כש-LifeGuard מזהה צליל שעשוי להעיד על סכנה (ירי, פיצוץ, שבירת זכוכית, צרחה...) מופיע חלון עם אפשרויות: SOS, מקום בטוח, התראה לאנשי הקשר, חיוג לחירום, התראת שווא. ה-AI לעולם לא מפעיל SOS ולא פונה לאיש בעצמו.'),
      listenRow,
      h('p', { class: 'help', 'data-test': 'hz-listen-note' }, 'עובד רק כשהאפליקציה פתוחה על המסך והמסך דולק. דפדפנים (ובמיוחד באייפון) לא מאפשרים לאפליקציית רשת להאזין ברקע או כשהמסך נעול - ואז אין זיהוי. ההפעלה הראשונה מורידה מודל זיהוי (~15MB). הניתוח נעשה במכשיר; שום דבר לא מוקלט, לא נשמר ולא נשלח.'),
      listenStatus,
      h('hr'),
      popupsRow, soundRow, vibRow,
      CAN_VIBRATE ? null : h('p', { class: 'help', 'data-test': 'hz-vibration-note' }, IS_IOS ? 'באייפון אין רטט לאפליקציות רשת (Safari לא תומך ב-Vibration API).' : 'הדפדפן הזה לא תומך ברטט.'),
      h('label', { for: 'hz-min-sev' }, 'להציג מ:'), sevSel,
      h('label', { for: 'hz-lang' }, 'שפת החלון'), langSel,
      saved,
      h('p', { class: 'help', 'data-test': 'hz-always' }, 'כשהחלונות כבויים: הזיהוי ממשיך לרוץ ונרשם (בלי חלון). נשארות פעילות תמיד: קריאות חירום מאנשי הקשר, התראות דחיפה מהשרת, ליווי הביתה ו"הכל בסדר?" בסיום הליווי, וכפתור ה-SOS. בזמן קריאת חירום פעילה או מצוקה שקטה לא קופץ שום חלון.'),
      logLine, clearLog, modelLine, delModel);
  }

  /** Home: only when listening was turned on - what the microphone is doing right now, and start/stop. */
  function homeCard() {
    if (!S.prefs.listen) return null;
    const status = h('p', { class: 'small', 'data-test': 'hz-home-status', role: 'status', 'aria-live': 'polite' });
    const btn = h('button', { type: 'button', class: 'secondary wide', 'data-test': 'hz-home-toggle' });
    btn.addEventListener('click', async () => {
      if (S.cap) { stopListening(); savePrefs({ listen: false }); return; }
      await startListening();
    });
    const draw = () => {
      status.textContent = listenText();
      status.className = 'small ' + (S.listen.state === 'error' ? 'error' : S.listen.state === 'running' ? 'okmsg' : 'muted');
      btn.textContent = S.cap ? 'הפסקת ההאזנה' : 'הפעלה';
      btn.disabled = ['starting', 'loading'].includes(S.listen.state);
    };
    draw();
    sub(draw);
    return h('div', { class: 'card', 'data-test': 'hz-home' }, ctx.secHead('h2', 'mic', 'red', 'האזנה לסכנה'), status, btn,
      h('p', { class: 'help' }, 'רק כשהאפליקציה פתוחה והמסך דולק.'));
  }

  // ---------------------------------------------------------------- session
  function sessionStart() {
    S.user = ctx.username();
    loadPrefs();
    S.clockOffset = 0;
    newMonitor();
    if (S.prefs.listen && !S.cap) setListen('needs_tap');
  }

  function sessionEnd() {
    stopListening();
    for (const id of [...S.modals.keys()]) closeModal(id);
    S.user = null;
    S.monitor = null;
    S.prefs = cleanPrefs({});
  }

  // ---------------------------------------------------------------- test hook (only when localStorage lg.e2eHooks = 1)
  try {
    if (typeof window !== 'undefined' && localStorage.getItem('lg.e2eHooks') === '1') {
      const b64 = (s) => { const bin = atob(s); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return new Float32Array(u.buffer); };
      const sum = (out) => out.map(({ kind, alert, show, why }) => ({ kind, show, why, id: alert.id, category: alert.category, severity: alert.severity, count: alert.count }));
      window.__lgHazardTest = {
        report: (rep) => sum(S.monitor ? S.monitor.process([rep], now()) : []),
        event: (category, severity, conf = 0.87, extra = {}) => sum(S.monitor.process([{
          model_version: 'hazard_audio_v1+hazard_audio_v2.1', quality: { status: 'ok' }, distress: null, category_scores: {},
          events: [{ category, severity_prior: severity, detection_confidence: conf, label_he: extra.label_he || null, score: 5, threshold: 4,
            model: 'head', near_threshold: false, margin: 1 }],
        }], now())),
        advance: (sec) => { S.clockOffset += sec; return now(); },
        log: () => logList(),
        prefs: () => ({ ...S.prefs }),
        modals: () => [...S.modals.keys()],
        listen: () => ({ ...S.listen, capturing: !!S.cap, sampleRate: S.cap ? S.cap.sr : null }),
        yamnet: async (wave) => { const d = await workerCall({ type: 'yamnet', audio: b64(wave) }); return { n: d.n, scores: Array.from(d.scores || []), emb: Array.from(d.emb || []), error: d.error }; },
        analyze: async (wave, sr) => { const d = await workerCall({ type: 'window', audio: b64(wave), sr }); return d.error ? { error: d.error } : d.report; },
        stopWorker: () => stopListening(),
      };
    }
  } catch { /* storage blocked: no hook */ }

  return { settingsCard, homeCard, sessionStart, sessionEnd, stopListening, state: S };
}

// LifeGuard PWA - vanilla JS, no build step, no inline scripts/styles (strict CSP).
// Honesty rules: a status is shown only as the server reports it. "נמסר" only after the recipient's
// app ACKed (server state "delivered"). A failed/offline request is shown as NOT sent and retried.
import {
  api, ApiError, onUnauthorized, getToken, getUser, setSession, clearSession, serverNow, store,
  volunteerRoles, setVolunteerRoles, requestHelper, helperRespond, releaseHelper, createSupportRequest,
} from './api.js';
import { icon } from './icons.js';
import { EnvMap, lightCap } from './envmap.js';
import { createHazard } from './hazard.js';

// ------------------------------------------------------------------ labels
const STATE_HE = {
  notified: 'נשלח', delivered: 'נמסר לאפליקציה', accepted: 'קיבל/ה',
  declined: 'דחה/תה', expired: 'לא הגיב/ה בזמן', revoked: 'הגישה בוטלה',
};
const STATE_CLS = { notified: 'wait', delivered: 'info', accepted: 'ok', declined: 'bad', expired: 'bad', revoked: '' };
const RESP_HE = { accept: 'קיבל/ה את האירוע', on_my_way: 'בדרך', arrived: 'הגיע/ה למקום', handled: 'מטפל/ת', decline: 'לא זמין/ה' };
const INV_HE = { pending: 'ממתין לאישור', connected: 'מחובר', accepted: 'מחובר', declined: 'נדחה', expired: 'פג תוקף', cancelled: 'בוטל' };
const INV_CLS = { pending: 'wait', connected: 'ok', accepted: 'ok', declined: 'bad', expired: 'bad', cancelled: '' };
const TYPE_HE = {
  unknown: 'לא צוין', medical: 'רפואי', fire: 'שריפה', earthquake: 'רעידת אדמה', security: 'ביטחוני',
  physical_danger: 'סכנה פיזית', road_accident: 'תאונת דרכים', lost: 'הלכתי לאיבוד', missing_person: 'נעדר/ת',
  flood_weather: 'הצפה / מזג אוויר', power_outage: 'הפסקת חשמל',
};
const SMS_HE = { pending: 'SMS בשליחה', sent: 'SMS נשלח לספק (אין אישור קריאה)', skipped: 'SMS לא נשלח', error: 'שליחת SMS נכשלה' };
const MED_LABELS = {
  blood_type: 'סוג דם', allergies: 'אלרגיות', medications: 'תרופות קבועות', conditions: 'מצבים רפואיים',
  emergency_medical_contact: 'איש קשר רפואי', notes: 'הערות',
};
const VERIF_HE = { unverified: 'לא מאומת/ת', pending: 'בקשה ממתינה לבדיקה', verified: 'מאומת/ת', rejected: 'הבקשה נדחתה' };
const VERIF_CLS = { unverified: '', pending: 'wait', verified: 'ok', rejected: 'bad' };
const SKILLS = { first_aid: 'עזרה ראשונה', cpr: 'החייאה (CPR)', medic: 'חובש/ת / פראמדיק/ית', nurse: 'אח/ות', doctor: 'רופא/ה', firefighter: 'כבאות והצלה', security: 'אבטחה' };
const EMERGENCY_NUMBERS = [['100', 'משטרה'], ['101', 'מד"א'], ['102', 'כבאות'], ['112', 'חירום']];
const HOLD_MS = 3000;
// מתנדבים שלא חייבים להיות מד"א (server/helpers.py). הנוסח כמו באפליקציית Windows (features_ui.py).
const VOL_ROLE_ROWS = [
  ['on_scene', '🚶 הגעה למקום', 'קריאות בקרבתך - כמו היום'],
  ['remote_support', '💬 שיחת תמיכה מרחוק', 'צ\'אט עם מי שצריך/ה להרגיש שמישהו איתו/ה. מכל מקום, בלי לראות שם ומיקום. חשבון בן 3 ימים לפחות.'],
  ['authority_liaison', '👮 הזעקת רשויות', 'מתקשר/ת ל-100/101/102 בשביל מי שלא יכול/ה לדבר. רואה שם ומיקום - לכן דורש אימות (גם אימות זהות ידני, בלי ארגון).'],
];
const VOL_LANGS = [['he', 'עברית'], ['ar', 'ערבית'], ['en', 'אנגלית'], ['ru', 'רוסית'], ['am', 'אמהרית'], ['fr', 'צרפתית'], ['es', 'ספרדית']];
// קווי הסיוע - עותק מקומי של server/helpers.py HOTLINES, כדי שיוצגו גם בלי שרת ובלי אינטרנט
const HOTLINES = [
  { name: 'ער"ן - עזרה ראשונה נפשית', number: '1201', note: '24/7, גם בצ\'אט באתר' },
  { name: 'סה"ר - סיוע והקשבה ברשת', number: null, url: 'https://sahar.org.il', note: 'צ\'אט אנונימי' },
  { name: 'נט"ל - טראומה על רקע לאומי', number: '1-800-363-363', note: null },
  { name: 'מרכזי סיוע לנפגעות ולנפגעי תקיפה מינית', number: '1202 / 1203', note: 'נשים / גברים' },
  { name: 'מוקד 105 - הגנה על ילדים ברשת', number: '105', note: null },
  { name: 'משטרה', number: '100', note: 'בסכנה מיידית' },
];
const HOTLINES_NOTE = 'מספרי הקווים יש לאמת לפני הפצה לציבור.';
const HELPER_KIND_HE = { remote_support: 'שיחת תמיכה מרחוק', authority_liaison: 'הזעקת רשויות' };

// ------------------------------------------------------------------ DOM helpers
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  let value;
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k === 'value') value = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (typeof v === 'boolean' && k in el) el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  if (value !== undefined) el.value = value;
  return el;
}

const pill = (text, cls = '') => h('span', { class: 'pill ' + cls }, text);

/** Section title with a tinted vector icon badge (Apple Health / Settings style). Text content = the title only. */
function secHead(tag, name, tone, text, attrs = {}) {
  return h(tag, { class: 'sec', ...attrs }, h('span', { class: 'ibadge ' + (tone || ''), 'aria-hidden': 'true' }, icon(name)), h('span', null, text));
}

function ago(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round(serverNow() - ts));
  if (s < 60) return `לפני ${s} שניות`;
  const m = Math.round(s / 60);
  if (m < 60) return `לפני ${m} דק'`;
  const hr = Math.round(m / 60);
  if (hr < 48) return `לפני ${hr} שעות`;
  return `לפני ${Math.round(hr / 24)} ימים`;
}

function clock(ts, withDate = false) {
  if (!ts) return '';
  const opts = withDate ? { day: 'numeric', month: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }
    : { hour: '2-digit', minute: '2-digit', second: '2-digit' };
  return new Date(ts * 1000).toLocaleString('he-IL', opts);
}

function mmss(sec) {
  sec = Math.max(0, Math.round(sec));
  const m = Math.floor(sec / 60), s = sec % 60;
  return (m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}` : String(m)) + ':' + String(s).padStart(2, '0');
}

function haversineKm(a, b) {
  const r = 6371, rad = (x) => x * Math.PI / 180;
  const dp = rad(b.lat - a.lat), dl = rad(b.lon - a.lon);
  const x = Math.sin(dp / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dl / 2) ** 2;
  return 2 * r * Math.asin(Math.sqrt(x));
}

// ------------------------------------------------------------------ platform (iPhone / iPad / Mac Safari)
// The PWA is the Apple solution (no native iOS app). Detection only adapts wording and links - capabilities
// are always feature-detected, never assumed from the user agent.
const UA = navigator.userAgent || '';
// iPadOS 13+ presents itself as a Mac ("desktop-class browsing"); the touch screen gives it away
const IS_IOS = /iPhone|iPad|iPod/.test(UA) || (/Macintosh/.test(UA) && navigator.maxTouchPoints > 1);
const IS_MAC = !IS_IOS && /Macintosh/.test(UA);
const IS_APPLE = IS_IOS || IS_MAC;
// every iOS browser is WebKit, but "Add to Home Screen" is reliably offered by Safari only
const IS_IOS_SAFARI = IS_IOS && /Safari\//.test(UA) && !/CriOS|FxiOS|EdgiOS|OPiOS|YaBrowser|GSA\//.test(UA);

/** Running as an installed app (iOS home screen: navigator.standalone; elsewhere: display-mode). */
function isStandalone() {
  try {
    if (navigator.standalone === true) return true;
    return ['standalone', 'fullscreen', 'minimal-ui'].some((m) => matchMedia(`(display-mode: ${m})`).matches);
  } catch { return false; }
}

function vibrate(pattern) {
  try { if (typeof navigator.vibrate === 'function') navigator.vibrate(pattern); } catch { /* iOS: no Vibration API */ }
}

const GEO_DENIED_HE = IS_IOS
  ? 'אין הרשאת מיקום. באייפון: הגדרות > פרטיות ואבטחה > שירותי מיקום - ודאו שהם פעילים, ותחת "אתרי Safari" בחרו "בזמן השימוש ביישום". אחר כך פתחו מחדש את LifeGuard.'
  : 'אין הרשאת מיקום - אפשר/י מיקום בהגדרות הדפדפן';

const fmtLL = (lat, lon) => `${Number(lat).toFixed(6)},${Number(lon).toFixed(6)}`;
const googleMapsUrl = (lat, lon) => `https://www.google.com/maps/search/?api=1&query=${fmtLL(lat, lon)}`;
/** Navigation link: Apple Maps walking directions on iPhone/iPad/Mac (opens the Maps app), Google Maps elsewhere. */
function mapsUrl(lat, lon) {
  return IS_APPLE ? `https://maps.apple.com/?daddr=${fmtLL(lat, lon)}&dirflg=w` : googleMapsUrl(lat, lon);
}

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
  const x = [...b].map((v) => v.toString(16).padStart(2, '0')).join('');
  return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const errText = (e) => (e instanceof ApiError ? e.message : 'שגיאה לא צפויה');

function telRow() {
  return h('div', { class: 'tel-row' },
    EMERGENCY_NUMBERS.map(([num, name]) => h('a', { class: 'btn', href: 'tel:' + num, 'aria-label': `${name} ${num}` },
      h('span', { class: 'num' }, icon('phone'), num), h('span', { class: 'nm' }, name))));
}

/** Inline two-step confirmation (no browser dialogs). Replaces `holder`'s content until answered. */
function confirmIn(holder, question, yesLabel, yesCls, onYes) {
  const prev = [...holder.childNodes];
  const msg = h('p', { class: 'error' });
  const yes = h('button', { class: yesCls, 'data-test': 'confirm-yes' }, yesLabel);
  const no = h('button', { class: 'ghost', 'data-test': 'confirm-no', onclick: () => holder.replaceChildren(...prev) }, 'לא, חזרה');
  yes.addEventListener('click', async () => {
    yes.disabled = true; no.disabled = true;
    try { await onYes(); } catch (e) { msg.textContent = errText(e); yes.disabled = false; no.disabled = false; }
  });
  holder.replaceChildren(h('div', { class: 'card warn' }, h('p', { class: 'big' }, question), h('div', { class: 'li-actions' }, yes, no), msg));
}

const hasOpenConfirm = (el) => !!el.querySelector('[data-test="confirm-yes"]');

// ------------------------------------------------------------------ helpers (remote support / authority liaison)
/** מה קורה עם בקשות העזרה - רק לפי רשימת ה-helpers שהשרת החזיר (main.py helpers_summary).
 *  "איתך" רק אחרי state=accepted אמיתי בשרת. requested = סוגים שביקשת (כדי לומר "אין זמינים"). */
function helpersSummary(helpers, requested = new Set()) {
  const lines = [];
  const seen = new Set();
  for (const x of helpers || []) {
    seen.add(x.kind);
    if (x.state === 'accepted') {
      const who = x.kind === 'authority_liaison' ? x.display_name : 'מתנדב/ת';
      lines.push(`✅ ${x.kind_label}: ${who} איתך` + (x.last_action_label ? `\n   ${x.last_action_label}` : ''));
    }
  }
  for (const kind of ['authority_liaison', 'remote_support']) {
    const mine = (helpers || []).filter((x) => x.kind === kind);
    if (mine.some((x) => x.state === 'accepted')) continue;
    if (mine.some((x) => x.state === 'notified')) lines.push(`⏳ ${mine[0].kind_label}: נשלח למתנדבים, ממתינים שמישהו יקבל`);
    else if (mine.some((x) => x.state === 'done')) lines.push(`✔️ ${mine[0].kind_label}: המתנדב/ת סיים/ה`);
    else if (requested.has(kind) && !seen.has(kind)) lines.push('⚠️ אין כרגע מתנדבים זמינים לזה. בסכנה מיידית: 100 / 101');
  }
  if (!lines.length) return { text: '', cls: 'muted' };
  const bad = lines.some((l) => l.startsWith('⚠️'));
  return { text: lines.join('\n'), cls: bad ? 'error' : (lines[0].startsWith('✅') ? 'okmsg' : 'muted') };
}

/** מצב בקשת שיחה (לא חירום) - רק לפי נתוני השרת (support_ui.py support_status). */
function supportStatus(view) {
  if (!view) return { text: '', cls: 'muted' };
  if (view.status !== 'active') return { text: 'השיחה הסתיימה. אפשר תמיד לפתוח שיחה חדשה או לפנות לקווי הסיוע.', cls: 'muted' };
  const hs = view.helpers || [];
  if (hs.some((x) => x.state === 'accepted')) return { text: '✅ מתנדב/ת הצטרף/ה לשיחה - אפשר לכתוב. המתנדב/ת לא רואה את שמך ואת מיקומך.', cls: 'okmsg' };
  const n = hs.filter((x) => x.state === 'notified').length;
  if (n) return { text: `⏳ הבקשה נשלחה ל-${n} מתנדבים. ממתינים שמישהו יקבל - בינתיים אפשר לכתוב כאן.`, cls: 'muted' };
  if (hs.some((x) => x.state === 'done')) return { text: 'המתנדב/ת סיים/ה את השיחה. אפשר לסגור ולפתוח בקשה חדשה.', cls: 'muted' };
  return { text: '⚠️ אין כרגע מתנדבי שיחה זמינים. הבקשה נשארת פתוחה וננסה שוב - ובינתיים אפשר לפנות לקווי הסיוע למטה.', cls: 'error' };
}

/** "1202 / 1203" -> שני קישורי חיוג */
function hotlineLinks(x) {
  if (x.number) {
    const nums = String(x.number).split('/').map((s) => s.trim()).filter(Boolean);
    return nums.flatMap((n, i) => [i ? ' / ' : null, h('a', { href: 'tel:' + n.replace(/[^\d+]/g, ''), dir: 'ltr' }, n)]);
  }
  if (x.url) return [h('a', { href: x.url, target: '_blank', rel: 'noopener', dir: 'ltr' }, x.url.replace(/^https?:\/\//, ''))];
  return [];
}

function hotlinesCard(list, note) {
  return h('div', { class: 'card', 'data-test': 'hotlines' },
    secHead('h3', 'phone', 'green', 'קווי סיוע (זמינים תמיד, גם בלי האפליקציה)'),
    h('ul', { class: 'list hotlines' }, (list && list.length ? list : HOTLINES).map((x) => h('li', null,
      '☎️ ', h('strong', null, x.name), ': ', ...hotlineLinks(x), x.note ? h('span', { class: 'muted small' }, ` (${x.note})`) : null))),
    (note || HOTLINES_NOTE) ? h('p', { class: 'muted small' }, note || HOTLINES_NOTE) : null,
    h('p', { class: 'error small' }, 'בסכנה מיידית לחצו SOS או התקשרו 100 / 101 - שיחה עם מתנדב/ת אינה שירות חירום.'));
}

/** כותרת להתראה נכנסת: קריאת חירום רגילה, או בקשה למתנדב/ת תמיכה / הזעקה (my_role=helper - בלי שם). */
function alertTitle(a) {
  if (a.my_role === 'helper') {
    if (a.helper_kind === 'authority_liaison') return '👮 בקשה להזעיק רשויות';
    return a.support_request ? '💬 מישהו צריך לדבר' : '💬 מישהו במצוקה צריך שיחה';
  }
  return `🚨 ${a.owner_display || ''} צריך/ה עזרה`;
}

function alertSubtitle(a) {
  if (a.my_role === 'helper') return `${a.helper_kind_label || ''} · ${a.support_request ? 'לא חירום - שיחה בלבד' : 'בתוך אירוע חירום'}`;
  return a.mode === 'silent' ? 'מצוקה שקטה - אל תתקשר/י' : `סוג: ${TYPE_HE[a.emergency_type] || TYPE_HE.unknown}`;
}

const MSG_ROLE_HE = { owner: 'במצוקה', trusted: 'איש קשר', volunteer: 'מתנדב/ת', helper: 'מתנדב/ת' };

/** צ'אט של אירוע (בעלים / מגיב / מתנדב/ת תמיכה). מוצג רק כשהשרת מחזיר messages (גישה מלאה / אחרי קבלה). */
function makeChat(iid, reload, { quick = true, title = 'הודעות' } = {}) {
  const card = h('div', { class: 'card', hidden: true, 'data-test': 'chat-card' });
  const list = h('ul', { class: 'msgs', 'data-test': 'messages' });
  const quickRow = h('div', { class: 'quick-row', 'data-test': 'quick-replies' });
  const err = h('p', { class: 'error' });
  let built = false, quickKeys = '', lastReadSent = 0;
  const build = () => {
    if (built) return;
    built = true;
    const input = h('input', { type: 'text', maxlength: '500', placeholder: 'כתיבת הודעה...', 'data-test': 'msg-input', 'aria-label': 'הודעה' });
    const send = h('button', { 'data-test': 'msg-send' }, 'שליחה');
    const form = h('form', { class: 'row' }, input, send);
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      send.disabled = true; err.textContent = '';
      try { await api('POST', `/incidents/${iid}/messages`, { text }); input.value = ''; await reload(); } catch (e) { err.textContent = e.offline ? 'אין חיבור - ההודעה לא נשלחה.' : errText(e); } finally { send.disabled = false; }
    });
    // הודעות מהירות: לחיצה אחת (מי שבמצוקה לא תמיד יכול/ה להקליד). הנוסח מהשרת.
    quickRow.addEventListener('click', async (ev) => {
      const b = ev.target.closest('button[data-quick]');
      if (!b) return;
      b.disabled = true; err.textContent = '';
      try { await api('POST', `/incidents/${iid}/messages`, { quick: b.dataset.quick }); await reload(); }
      catch (e) { err.textContent = e.offline ? 'אין חיבור - ההודעה לא נשלחה.' : errText(e); }
      finally { b.disabled = false; }
    });
    card.append(h('h3', null, title), list, quickRow, form, err);
  };
  const markRead = (msgs) => {
    const others = msgs.filter((m) => !(state.user && m.sender === state.user.username)).map((m) => m.id);
    if (!others.length || document.hidden) return;              // "נקרא" רק כשהמסך באמת מוצג
    const upTo = Math.max(...others);
    if (upTo <= lastReadSent) return;
    lastReadSent = upTo;
    api('POST', `/incidents/${iid}/messages/read`, { up_to_id: upTo }).catch(() => { lastReadSent = 0; });
  };
  /** roleHe: תוויות תפקיד לשולחים (null = בלי תווית). nameOf: שם לפי משתמש כשאין sender_display. */
  const render = (inc, { nameOf = (u) => u, roleHe = MSG_ROLE_HE } = {}) => {
    if (!inc || !inc.messages) { card.hidden = true; return; }
    card.hidden = false;
    build();
    list.replaceChildren(...(inc.messages.length ? inc.messages.map((m) => {
      const mine = !!(state.user && m.sender && m.sender === state.user.username);
      const receipt = mine ? ((m.read_by || []).length ? ` · ✓✓ נקרא ע"י ${m.read_by.join(', ')}` : ' · ✓ נשלח') : '';
      const roleLabel = !mine && m.role && roleHe ? roleHe[m.role] : null;
      return h('li', { class: mine ? 'mine' : '', 'data-test': 'message', 'data-role': m.role || '' },
        h('div', { class: 'who' }, `${mine ? 'אני' : (m.sender_display || nameOf(m.sender))}${roleLabel ? ` (${roleLabel})` : ''} · ${clock(m.created_at)}${receipt}`),
        h('div', null, m.text));
    }) : [h('li', { class: 'muted' }, 'אין הודעות עדיין.')]));
    const q = quick ? (inc.quick_replies || {}) : {};
    const keys = Object.keys(q).join(',');
    if (keys !== quickKeys) {
      quickKeys = keys;
      quickRow.replaceChildren(...Object.entries(q).map(([code, text]) =>
        h('button', { type: 'button', class: 'chip', 'data-quick': code }, text)));
    }
    markRead(inc.messages);
  };
  return { card, render };
}

// ------------------------------------------------------------------ state
const bus = new EventTarget();
const emit = (name) => bus.dispatchEvent(new Event(name));

const state = {
  user: null,        // {username, display_name}
  me: null,          // GET /me
  volRoles: null,    // GET /me/volunteer/roles (only when I am a volunteer)
  alerts: new Map(), // incident_id -> incident view summary (incoming alerts)
  notices: [],       // [{id, cls, title, text, hash, urgent}]
  escorts: { mine: [], watching: [], fetchedAt: 0 },
  overdue: null,     // escort_overdue payload
  loopGen: 0,
  connOk: true,
  viewing: null,     // incident id currently on screen
};
let viewCleanups = [];
const onCleanup = (fn) => viewCleanups.push(fn);

function every(ms, fn) {
  let stopped = false, timer = null;
  const tick = async () => { if (stopped) return; try { await fn(); } catch { /* shown by fn */ } if (!stopped) timer = setTimeout(tick, ms); };
  timer = setTimeout(tick, ms);
  const stop = () => { stopped = true; clearTimeout(timer); };
  onCleanup(stop);
  return stop;
}

function listen(name, fn) {
  bus.addEventListener(name, fn);
  onCleanup(() => bus.removeEventListener(name, fn));
}

const userKey = (k) => `lg.${k}.${state.user ? state.user.username : '_'}`;

// ------------------------------------------------------------------ geolocation
const geo = { watchId: null, last: null, error: null, users: new Set() };

function geoUse(who) {
  geo.users.add(who);
  if (geo.watchId != null) return;
  if (!('geolocation' in navigator)) { geo.error = 'המכשיר/הדפדפן לא תומך במיקום'; return; }
  geo.watchId = navigator.geolocation.watchPosition((p) => {
    geo.last = {
      lat: p.coords.latitude, lon: p.coords.longitude,
      accuracy_m: Math.round(p.coords.accuracy || 0),
      captured_at: (p.timestamp || Date.now()) / 1000 + (serverNow() - Date.now() / 1000),
    };
    geo.error = null;
    emit('geo');
  }, (err) => {
    geo.error = err.code === 1 ? GEO_DENIED_HE : 'המיקום לא זמין כרגע';
    emit('geo');
  }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
}

function geoRelease(who) {
  geo.users.delete(who);
  if (!geo.users.size && geo.watchId != null) {
    navigator.geolocation.clearWatch(geo.watchId);
    geo.watchId = null;
  }
}

/** Fresh fix (<=60s) or wait for the first one up to maxWaitMs. Never blocks SOS longer than that. */
function geoFix(maxWaitMs) {
  const fresh = () => geo.last && serverNow() - geo.last.captured_at < 60;
  if (fresh()) return Promise.resolve(geo.last);
  return new Promise((resolve) => {
    const done = () => { bus.removeEventListener('geo', onGeo); clearTimeout(t); resolve(fresh() ? geo.last : null); };
    const onGeo = () => { if (fresh() || geo.error) done(); };
    const t = setTimeout(done, maxWaitMs);
    bus.addEventListener('geo', onGeo);
  });
}

function locPayload(fix) {
  return {
    lat: fix.lat, lon: fix.lon, accuracy_m: fix.accuracy_m,
    // GPS-grade only when the device reports good accuracy; a coarse network fix is not "precise"
    is_precise: fix.accuracy_m > 0 && fix.accuracy_m <= 150,
    source: 'browser_gps', captured_at: fix.captured_at,
  };
}

function geoStatusText(sentAt, sendError) {
  if (geo.error) return { cls: 'error', text: '⚠️ ' + geo.error + ' - המיקום לא נשלח' };
  if (!geo.last) return { cls: 'muted', text: 'ממתין למיקום מהמכשיר...' };
  if (sendError) return { cls: 'error', text: '⚠️ שליחת המיקום לשרת נכשלה - מנסה שוב' };
  if (!sentAt) return { cls: 'muted', text: `מיקום במכשיר (דיוק ${geo.last.accuracy_m} מ') - עוד לא נשלח` };
  // e.g. iOS suspended the app (screen locked): the device has produced no new fix since - say so
  if (serverNow() - geo.last.captured_at > 120) return { cls: 'error', text: `⚠️ אין מיקום חדש מהמכשיר - המיקום האחרון נמדד ${ago(geo.last.captured_at)}` };
  return { cls: 'okmsg', text: `המיקום נשלח לשרת ${ago(sentAt)} (דיוק ${geo.last.accuracy_m} מ')` };
}

// ------------------------------------------------------------------ my active incident (location tracking)
const track = { iid: null, mode: null, sentAt: 0, sentFix: null, error: null, busy: false, timer: null };

function myIncident() { return store.getJSON(userKey('myIncident')); }

function setMyIncident(id, mode) {
  store.setJSON(userKey('myIncident'), id ? { id, mode } : null);
  if (id) startTracking(id, mode); else stopTracking();
  syncSosMini();
}

// בקשת שיחה (לא חירום): אין מעקב מיקום - רק זוכרים איזו בקשה פתוחה כדי לחזור אליה
function mySupport() { return store.getJSON(userKey('mySupport')); }
function setMySupport(id) { store.setJSON(userKey('mySupport'), id ? { id } : null); }

function startTracking(iid, mode) {
  if (track.iid === iid) { track.mode = mode; syncWake(); return; }   // e.g. silent -> loud
  stopTracking();
  Object.assign(track, { iid, mode, sentAt: 0, sentFix: null, error: null });
  geoUse('incident');
  bus.addEventListener('geo', sendTrackLocation);
  track.timer = setInterval(() => sendTrackLocation(), 15000);
  sendTrackLocation();
  syncWake();
}

function stopTracking() {
  if (!track.iid) return;
  bus.removeEventListener('geo', sendTrackLocation);
  clearInterval(track.timer);
  track.iid = null;
  geoRelease('incident');
  syncWake();
}

// ------------------------------------------------------------------ screen wake lock (active loud SOS only)
// A web app gets no background execution on iOS: once the screen locks, location stops. Keeping the screen on
// during an active SOS keeps the location flowing. Not in silent mode (a lit screen could give the user away).
const wake = { sentinel: null, wanted: false, pending: false, failed: null };
const wakeSupported = () => 'wakeLock' in navigator && !!navigator.wakeLock && typeof navigator.wakeLock.request === 'function';

function syncWake() {
  if (track.iid && track.mode !== 'silent') wakeAcquire(); else wakeRelease();
}

async function wakeAcquire() {
  wake.wanted = true;
  if (!wakeSupported() || wake.sentinel || wake.pending || document.visibilityState !== 'visible') return;
  wake.pending = true;
  try {
    const s = await navigator.wakeLock.request('screen');
    if (!wake.wanted) { s.release().catch(() => {}); return; }
    wake.sentinel = s; wake.failed = null;
    s.addEventListener('release', () => { if (wake.sentinel === s) { wake.sentinel = null; emit('wake'); } });
  } catch (e) {
    wake.failed = (e && e.name) || 'error';
  } finally {
    wake.pending = false;
    emit('wake');
  }
}

function wakeRelease() {
  const had = wake.wanted || wake.sentinel;
  wake.wanted = false;
  const s = wake.sentinel;
  wake.sentinel = null;
  if (s) s.release().catch(() => {});
  if (had) emit('wake');
}

document.addEventListener('visibilitychange', () => {
  // the system drops the lock whenever the page is hidden; take it again on return
  if (wake.wanted && document.visibilityState === 'visible') wakeAcquire();
});

function wakeLine(silent) {
  let text = '';
  if (!silent) {
    text = wake.sentinel ? '🔆 המסך יישאר דלוק כל עוד LifeGuard פתוח - כך המיקום ממשיך להישלח.'
      : '⚠️ השאירו את LifeGuard פתוח והמסך דולק' + (wakeSupported() ? '.' : ' (המכשיר לא מאפשר לאפליקציה להשאיר אותו דלוק).');
  }
  if (IS_IOS) text += (text ? ' ' : '') + 'באייפון: אם המסך ננעל או שעוברים לאפליקציה אחרת - המיקום מפסיק להישלח, ואנשי הקשר רואים את המיקום האחרון עם השעה שלו.';
  return h('p', { class: wake.sentinel || silent ? 'small muted' : 'small', 'data-test': 'wake-line' }, text);
}

async function sendTrackLocation() {
  const fix = geo.last;
  if (!track.iid || !fix || track.busy) return;
  const moved = !track.sentFix || haversineKm(track.sentFix, fix) > 0.015 || fix.accuracy_m < track.sentFix.accuracy_m - 10;
  const age = serverNow() - track.sentAt;
  if (!track.error && !(moved && age >= 4) && age < 30) return;
  track.busy = true;
  const iid = track.iid;
  try {
    await api('POST', `/incidents/${iid}/location`, locPayload(fix));
    track.sentAt = serverNow(); track.sentFix = fix; track.error = null;
  } catch (e) {
    track.error = e;
    if (e.status === 409 || e.status === 404) setMyIncident(null); // incident ended
  } finally {
    track.busy = false;
    emit('track');
  }
}

// ------------------------------------------------------------------ SOS outbox (idempotent retries)
const sos = { sending: false, timer: null };

function sosOutbox() { return store.getJSON(userKey('sosOutbox')); }
function setSosOutbox(v) { store.setJSON(userKey('sosOutbox'), v); emit('sos'); }

async function triggerSOS(mode, emergencyType) {
  if (sosOutbox()) return flushSOS();
  geoUse('sos');
  setSosOutbox({ client_id: uuid(), mode, emergency_type: emergencyType || 'unknown', severity: 'high', created: Date.now(), attempts: 0 });
  if (mode === 'loud') vibrate(200);
  const fix = await geoFix(3000);
  const out = sosOutbox();
  if (out && fix && !out.location) { out.location = locPayload(fix); setSosOutbox(out); }
  await flushSOS();
}

async function flushSOS() {
  const out = sosOutbox();
  if (!out || sos.sending || !getToken()) return;
  sos.sending = true;
  clearTimeout(sos.timer);
  try {
    const body = { client_id: out.client_id, mode: out.mode, emergency_type: out.emergency_type, severity: out.severity };
    if (out.location) body.location = out.location;
    const inc = await api('POST', '/incidents', body, { timeout: 12000 });
    setSosOutbox(null);
    geoRelease('sos');
    setMyIncident(inc.id, inc.mode);
    location.hash = '#incident/' + inc.id;
  } catch (e) {
    const cur = sosOutbox();
    if (cur) {
      cur.attempts = (cur.attempts || 0) + 1;
      cur.lastError = e.offline ? 'אין חיבור לשרת' : errText(e);
      if (e.code === 'invalid_location') delete cur.location; // never let a bad fix block the alert
      setSosOutbox(cur);
      if (e.status !== 401) sos.timer = setTimeout(flushSOS, 3000);
    }
  } finally {
    sos.sending = false;
  }
}

// ------------------------------------------------------------------ notifications (long-poll + ACK)
const ackQueue = new Set();

async function flushAcks() {
  for (const id of [...ackQueue]) {
    try { await api('POST', `/notifications/${id}/received`); ackQueue.delete(id); } catch (e) {
      if (e.status === 404) ackQueue.delete(id); else return;
    }
  }
}

function setConn(ok) {
  if (state.connOk === ok) return;
  state.connOk = ok;
  renderConn();
}

function renderConn() {
  const el = $('#conn');
  const offline = navigator.onLine === false;
  if ((!getToken() || state.connOk) && !offline) { el.hidden = true; el.replaceChildren(); return; }
  el.hidden = false;
  el.className = 'conn bad offline-card';
  // e.g. opened offline (the app shell came from the service worker cache): nothing can reach the server,
  // so say exactly that and put the emergency numbers - phone calls need no internet - right here
  el.replaceChildren(
    h('p', { 'data-test': 'offline-banner' }, offline
      ? '⚠️ אין אינטרנט. LifeGuard לא יכול לשלוח קריאה או מיקום ולא לקבל התראות עד שהחיבור יחזור.'
      : '⚠️ אין חיבור לשרת - קריאות והתראות לא נשלחות ולא מתקבלות כרגע. מנסה להתחבר מחדש...'),
    h('p', { class: 'small' }, 'בחירום חייגו עכשיו - שיחת טלפון לא צריכה אינטרנט:'),
    telRow());
}

async function notificationLoop(gen) {
  let after = null;
  while (gen === state.loopGen && after === null) {
    try {
      const stored = Number(store.get(userKey('cursor')));
      if (stored > 0) {
        after = stored;
      } else {
        // first start on this device: begin at the end, handle only what no device has received yet
        const cur = await api('GET', '/notifications/cursor');
        for (const n of cur.unreceived || []) await processNotification(n);
        after = cur.last_id || 0;
        store.set(userKey('cursor'), String(after));
      }
      setConn(true);
    } catch (e) {
      if (gen !== state.loopGen) return;
      setConn(false);
      await sleep(3000);
    }
  }
  while (gen === state.loopGen) {
    try {
      const r = await api('GET', `/notifications?after=${after}&wait=25`, undefined, { timeout: 40000 });
      if (gen !== state.loopGen) return;
      setConn(true);
      for (const n of r.notifications || []) {
        after = Math.max(after, n.id);
        await processNotification(n);
        store.set(userKey('cursor'), String(after));
      }
      flushAcks();
    } catch (e) {
      if (gen !== state.loopGen) return;
      setConn(false);
      await sleep(3000);
    }
  }
}

async function processNotification(n) {
  try { handleNotification(n); } catch (e) { /* never let one bad payload stop the stream */ }
  ackQueue.add(n.id); // "received by the app" - this is what turns the sender's view into "נמסר"
  await flushAcks();
}

function addNotice(nt) {
  nt.id = nt.id || 'n' + Date.now() + Math.random();
  state.notices = state.notices.filter((x) => x.id !== nt.id);
  state.notices.unshift(nt);
  state.notices = state.notices.slice(0, 6);
  renderNotices();
}

// ------------------------------------------------------------------ audio (iOS: unlocked by the first tap)
// iOS/Safari keeps Web Audio silent until it is started inside a user gesture. The first tap anywhere unlocks a
// shared AudioContext, so an incoming "ring" can be heard while the app is open. A closed app gets only the
// system notification - there is no way around that for a web app.
const audio = {
  ctx: null,
  get() {
    if (!this.ctx) {
      const C = window.AudioContext || window.webkitAudioContext;
      if (C) { try { this.ctx = new C(); } catch { this.ctx = null; } }
    }
    return this.ctx;
  },
  unlock() {
    const ctx = this.get();
    if (!ctx) return true;
    try {
      if (ctx.state !== 'running') ctx.resume().catch(() => {});
      const src = ctx.createBufferSource();      // a silent sample played inside the gesture unlocks iOS
      src.buffer = ctx.createBuffer(1, 1, 22050);
      src.connect(ctx.destination);
      src.start(0);
    } catch { /* keep trying on the next tap */ }
    return ctx.state === 'running';
  },
};

function installAudioUnlock() {
  const evs = ['pointerdown', 'touchend', 'keydown'];
  const onGesture = () => {
    if (audio.unlock()) for (const e of evs) window.removeEventListener(e, onGesture, true);
  };
  for (const e of evs) window.addEventListener(e, onGesture, { capture: true, passive: true });
}

// "צלצול": איש הקשר הקרוב ביותר שמצלצלים אליו עכשיו - צפצוף חוזר + רטט עד מענה,
// עד שהשרת עובר לבא בתור (ring_stopped), או עד שהאירוע נסגר. מקסימום 3 דקות.
const ringer = {
  iid: null, timer: null, started: 0,
  start(iid) {
    if (this.iid === iid) return;
    this.stop();
    this.iid = iid; this.started = Date.now();
    const tick = () => {
      if (!this.iid || Date.now() - this.started > 180000) { this.stop(); return; }
      try {
        const ctx = audio.get();
        if (ctx) {
          if (ctx.state !== 'running') ctx.resume().catch(() => {});
          const o = ctx.createOscillator(), g = ctx.createGain();
          o.type = 'square'; o.frequency.value = 880; g.gain.value = 0.2;
          o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.6);
        }
      } catch (e) { /* דפדפן חסם אודיו לפני אינטראקציה - נשאר הרטט וההתראה */ }
      vibrate([400, 200, 400]);
    };
    tick();
    this.timer = setInterval(tick, 2000);
    document.body.classList.add('ringing');
  },
  stop(iid) {
    if (iid && iid !== this.iid) return;
    clearInterval(this.timer); this.timer = null; this.iid = null;
    vibrate(0);
    document.body.classList.remove('ringing');
  },
};

function handleNotification(n) {
  const p = n.payload || {};
  switch (n.type) {
    case 'incident_new': {
      state.alerts.set(p.id, p);
      const mine = myIncident();
      renderNotices();
      if (p.ring !== false) ringer.start(p.id);   // ring=false: עדכון שקט - מישהו אחר כבר מטפל
      if (!(mine && state.viewing === mine.id) && state.viewing !== p.id) location.hash = '#incident/' + p.id;
      break;
    }
    case 'incident_update':
      if (p.event === 'ring_stopped' && ringer.iid === p.incident_id) {
        ringer.stop();
        addNotice({ id: 'ring' + p.incident_id, title: 'הצלצול עבר לאיש הקשר הבא', text: 'עדיין אפשר לענות ולקבל את הקריאה.' });
      }
      emit('incident:' + p.incident_id);
      break;
    case 'incident_closed': {
      ringer.stop(p.incident_id);
      const had = state.alerts.get(p.incident_id);
      state.alerts.delete(p.incident_id);
      if (p.reason === 'resolved' || p.reason === 'cancelled') {
        addNotice({ id: 'closed' + p.incident_id, title: 'ℹ️ האירוע הסתיים', text: p.text || 'האירוע הסתיים' });
      } else if (had && p.reason === 'expired') {
        addNotice({ id: 'closed' + p.incident_id, title: 'הקריאה הועברה הלאה', text: 'לא הגבת בזמן - הקריאה הועברה למתנדב/ת אחר/ת.' });
      } else if (had && p.reason === 'taken') {
        addNotice({ id: 'closed' + p.incident_id, title: 'הבקשה כבר נלקחה', text: 'מתנדב/ת אחר/ת כבר קיבל/ה את הבקשה. תודה על הזמינות!' });
      } else if (p.reason === 'released') {
        addNotice({ id: 'closed' + p.incident_id, title: 'ℹ️ העזרה שלך הסתיימה', text: 'הפונה סיים/ה את ההשתתפות שלך בבקשה. תודה!' });
      }
      renderNotices();
      emit('incident:' + p.incident_id);
      break;
    }
    case 'relationship_invite':
      addNotice({ id: 'inv' + p.id, title: '📨 הזמנה למעגל הביטחון', text: p.text || '', hash: '#circle', action: 'לצפייה ואישור' });
      emit('circle');
      break;
    case 'relationship_update':
      if (p.status === 'connected') {
        const other = state.user && p.owner === state.user.username ? p.contact_display : p.owner_display;
        addNotice({ id: 'rel' + p.invitation_id, title: '✅ מעגל הביטחון', text: `החיבור עם ${other || ''} אושר`, hash: '#circle' });
      } else if (p.status === 'awaiting_confirmation' && state.user && p.contact !== state.user.username) {
        addNotice({ id: 'rel' + p.invitation_id, title: 'נדרש אישור שלך', text: `${p.contact_display || ''} אישר/ה הזמנה שנשלחה למספר טלפון. אשר/י שזה האדם הנכון.`, hash: '#circle', action: 'לאישור' });
      }
      emit('circle');
      break;
    case 'relationship_removed':
      emit('circle');
      break;
    case 'escort_started':
      addNotice({ id: 'esc' + p.id, title: '🚶 ליווי הביתה', text: `${p.owner_display || ''} התחיל/ה ליווי הביתה${p.destination ? ' ל' + p.destination : ''}`, hash: '#escort/' + p.id, action: 'לצפייה' });
      emit('escorts');
      break;
    case 'escort_update':
      if (p.text) addNotice({ id: 'esc' + p.escort_id, title: '🚶 ליווי הביתה', text: p.text });
      emit('escorts');
      break;
    case 'escort_overdue':
      state.overdue = p;
      renderNotices();
      emit('escorts');
      break;
    case 'escort_alarm': {
      const mineEsc = state.escorts.mine.some((e) => e.id === p.escort_id) || !p.text;
      if (mineEsc && p.incident_id) {
        state.overdue = null;
        setMyIncident(p.incident_id, null);
        addNotice({ id: 'alarm' + p.escort_id, urgent: true, title: '🚨 נפתחה קריאת חירום', text: 'לא אישרת הגעה בזמן - השרת פתח קריאת חירום לאנשי הקשר שלך. אם את/ה בסדר, סיים/י את האירוע.', hash: '#incident/' + p.incident_id, action: 'לאירוע' });
        location.hash = '#incident/' + p.incident_id;
      } else {
        addNotice({ id: 'alarm' + p.escort_id, urgent: true, title: '🚨 לא אושרה הגעה', text: p.text || '', hash: p.incident_id ? '#incident/' + p.incident_id : '#home', action: 'לאירוע' });
      }
      emit('escorts');
      break;
    }
    case 'readiness_check':
    case 'test':
      addNotice({ id: 'rd' + n.id, title: '🧪 בדיקת מוכנות', text: p.text || `הודעת בדיקה מ${p.from_display || ''}. זו לא קריאת חירום.` });
      break;
    case 'info':
      addNotice({ id: 'info' + n.id, title: p.from_display || 'LifeGuard', text: p.text || '' });
      break;
    default:
      break;
  }
}

async function loadInbox() {
  try {
    const r = await api('GET', '/incidents/inbox');
    state.alerts.clear();
    for (const inc of r.incidents || []) state.alerts.set(inc.id, inc);
    renderNotices();
  } catch { /* connection banner covers it */ }
}

function renderNotices() {
  const box = $('#notices');
  box.replaceChildren();
  if (!getToken()) return;
  for (const [id, a] of state.alerts) {
    if (id === state.viewing) continue;
    const support = a.my_role === 'helper' && a.helper_kind !== 'authority_liaison';
    box.append(h('div', { class: support ? 'card notice' : 'card notice urgent', 'data-test': 'alert-notice' },
      h('div', { class: 'big' }, alertTitle(a)),
      h('p', null, alertSubtitle(a)),
      h('button', { class: support ? 'wide' : 'red wide', onclick: () => { location.hash = '#incident/' + id; } }, a.my_role === 'helper' ? 'פתח/י את הבקשה' : 'פתח/י את הקריאה')));
  }
  const esc = state.escorts.mine.find((e) => e.overdue);
  if (state.overdue || esc) {
    const eid = (state.overdue && state.overdue.escort_id) || esc.id;
    if (state.escorts.mine.length === 0 || state.escorts.mine.some((e) => e.id === eid)) box.append(overduePrompt(eid));
  }
  for (const nt of state.notices) {
    box.append(h('div', { class: 'card notice' + (nt.urgent ? ' urgent' : ''), 'data-test': 'notice' },
      h('div', { class: 'li-head' }, h('strong', null, nt.title || ''),
        h('button', { class: 'link', onclick: () => { state.notices = state.notices.filter((x) => x.id !== nt.id); renderNotices(); } }, 'סגירה')),
      nt.text ? h('p', null, nt.text) : null,
      nt.hash ? h('button', { class: 'secondary', onclick: () => { state.notices = state.notices.filter((x) => x.id !== nt.id); renderNotices(); location.hash = nt.hash; } }, nt.action || 'פתיחה') : null));
  }
}

function overduePrompt(eid) {
  const msg = h('p', { class: 'error' });
  const act = async (path, body) => {
    try {
      await api('POST', `/escorts/${eid}/${path}`, body);
      state.overdue = null;
      await refreshEscorts();
      renderNotices();
    } catch (e) { msg.textContent = errText(e) + (e.offline ? ' - הפעולה לא נשמרה בשרת!' : ''); }
  };
  return h('div', { class: 'card danger', 'data-test': 'escort-overdue' },
    h('h2', null, '⏰ זמן הליווי הסתיים - הכל בסדר?'),
    h('p', null, (state.overdue && state.overdue.text) || 'אשר/י הגעה או הארך/י את הזמן - אחרת תיפתח קריאת חירום אוטומטית.'),
    h('div', { class: 'li-actions' },
      h('button', { class: 'green', 'data-test': 'overdue-arrived', onclick: () => act('arrived') }, '✅ הגעתי בשלום'),
      h('button', { class: 'secondary', 'data-test': 'overdue-extend', onclick: () => act('extend', { minutes: 15 }) }, 'הארכה ב-15 דק\'')),
    h('button', { class: 'red wide', onclick: () => triggerSOS('loud', 'unknown') }, '🚨 אני צריך/ה עזרה'),
    msg);
}

// ------------------------------------------------------------------ escorts (shared state)
async function refreshEscorts() {
  const r = await api('GET', '/escorts');
  state.escorts = { mine: r.mine || [], watching: r.watching || [], fetchedAt: Date.now() };
  for (const e of [...state.escorts.mine, ...state.escorts.watching]) e._deadlineLocal = Date.now() / 1000 + (e.remaining_seconds || 0);
  const active = state.escorts.mine[0];
  if (active) startEscortTracking(active.id); else stopEscortTracking();
  if (state.overdue && !state.escorts.mine.some((e) => e.id === state.overdue.escort_id)) state.overdue = null;
  emit('escorts-loaded');
  renderNotices();
  return state.escorts;
}

const etrack = { eid: null, sentAt: 0, sentFix: null, error: null, busy: false, timer: null };

function startEscortTracking(eid) {
  if (etrack.eid === eid) return;
  stopEscortTracking();
  Object.assign(etrack, { eid, sentAt: 0, sentFix: null, error: null });
  geoUse('escort');
  bus.addEventListener('geo', sendEscortLocation);
  etrack.timer = setInterval(sendEscortLocation, 20000);
  sendEscortLocation();
}

function stopEscortTracking() {
  if (!etrack.eid) return;
  bus.removeEventListener('geo', sendEscortLocation);
  clearInterval(etrack.timer);
  etrack.eid = null;
  geoRelease('escort');
}

async function sendEscortLocation() {
  const fix = geo.last;
  if (!etrack.eid || !fix || etrack.busy) return;
  const moved = !etrack.sentFix || haversineKm(etrack.sentFix, fix) > 0.03;
  const age = serverNow() - etrack.sentAt;
  if (!etrack.error && !(moved && age >= 10) && age < 60) return;
  etrack.busy = true;
  try {
    await api('POST', `/escorts/${etrack.eid}/location`, locPayload(fix));
    etrack.sentAt = serverNow(); etrack.sentFix = fix; etrack.error = null;
  } catch (e) {
    etrack.error = e;
    if (e.status === 409 || e.status === 404) stopEscortTracking();
  } finally {
    etrack.busy = false;
    emit('etrack');
  }
}

// ------------------------------------------------------------------ volunteer presence
let presenceTimer = null;

function syncPresence() {
  const v = state.me && state.me.volunteer;
  // מיקום נוכחות נדרש רק להגעה למקום. מי שבחר/ה רק שיחה מרחוק / הזעקת רשויות - המכשיר לא שולח מיקום.
  const roles = state.volRoles && state.volRoles.roles && state.volRoles.roles.length ? state.volRoles.roles : null;
  const onScene = !roles || roles.includes('on_scene');
  if (v && v.available && onScene) {
    geoUse('presence');
    if (!presenceTimer) {
      const push = async () => {
        if (!geo.last) return;
        try {
          await api('PUT', '/me/presence', { lat: geo.last.lat, lon: geo.last.lon, accuracy_m: geo.last.accuracy_m, is_precise: locPayload(geo.last).is_precise, source: 'browser_gps' });
          store.set(userKey('presenceAt'), String(serverNow()));
        } catch { /* retried next tick */ }
      };
      presenceTimer = setInterval(push, 5 * 60 * 1000);
      geoFix(15000).then(push);
    }
  } else {
    clearInterval(presenceTimer); presenceTimer = null;
    geoRelease('presence');
  }
}

async function loadMe() {
  try {
    state.me = await api('GET', '/me');
    if (state.me && state.me.volunteer) { try { state.volRoles = await volunteerRoles(); } catch { /* keep last */ } }
    syncPresence(); emit('me');
  } catch { /* banner */ }
  return state.me;
}

// ------------------------------------------------------------------ router
const TABS = ['home', 'map', 'community', 'circle', 'profile'];

function parseHash() {
  const raw = (location.hash || '#home').slice(1);
  const [name, arg] = raw.split('/');
  return [name || 'home', arg ? decodeURIComponent(arg) : null];
}

function render() {
  for (const fn of viewCleanups.splice(0)) { try { fn(); } catch { /* ignore */ } }
  state.viewing = null;
  const main = $('#view');
  main.replaceChildren();
  if (!getToken()) { renderLogin(main); renderNotices(); renderConn(); return; }
  document.body.classList.remove('no-nav');
  $('#tabs').hidden = false;
  renderWhoami();
  const [name, arg] = parseHash();
  const mine = myIncident();
  const tab = TABS.includes(name) ? name : (name === 'incident' && mine && mine.id === arg ? 'home' : name === 'incident' ? 'community' : 'home');
  for (const b of document.querySelectorAll('#tabs button')) b.setAttribute('aria-current', b.dataset.tab === tab ? 'page' : 'false');
  // soft entrance for ordinary screens only - never on an emergency (incident) screen, never on the SOS area
  if (name === 'incident') delete main.dataset.anim; else main.dataset.anim = '1';
  main.dataset.screen = name;
  if (name === 'incident' && arg) { state.viewing = arg; viewIncident(main, arg); } else if (name === 'escort' && arg) viewEscort(main, arg);
  else if (name === 'support') viewSupport(main);
  else if (name === 'map') viewMap(main);
  else if (name === 'community') viewCommunity(main);
  else if (name === 'circle') viewCircle(main);
  else if (name === 'profile') viewProfile(main);
  else viewHome(main, arg);
  syncSosMini();
  renderNotices();
  renderConn();
  window.scrollTo(0, 0);
}

/** avatar initial + display name in the navigation bar */
function renderWhoami() {
  const el = $('#whoami');
  const name = state.user ? state.user.display_name || state.user.username : '';
  if (!name) { el.replaceChildren(); return; }
  el.replaceChildren(h('span', { class: 'avatar', 'aria-hidden': 'true' }, [...name.trim()][0] || '?'), h('span', { class: 'name' }, name));
}

/** The compact SOS in the bar: on every ordinary screen. Hidden on home (the big button is there), on incident
 *  screens (they carry their own actions), while an SOS is being sent, and during my silent distress (discreet). */
function syncSosMini() {
  const b = $('#sos-mini');
  if (!b) return;
  const [name] = parseHash();
  const mine = myIncident();
  const hide = !getToken() || name === 'home' || name === 'incident' || !!sosOutbox() || (mine && mine.mode === 'silent');
  b.hidden = !!hide;
  b.setAttribute('aria-label', mine ? 'קריאת החירום הפעילה שלך' : 'קריאת חירום SOS - פותח אישור');
}

// ------------------------------------------------------------------ login / register
function renderLogin(main) {
  document.body.classList.add('no-nav');
  $('#tabs').hidden = true;
  $('#whoami').replaceChildren();
  syncSosMini();
  let mode = 'login';
  const msg = h('p', { class: 'error', role: 'alert', 'data-test': 'login-error' });
  const name = h('input', { type: 'text', id: 'f-display', autocomplete: 'name', maxlength: '60' });
  const user = h('input', { type: 'text', id: 'f-user', autocomplete: 'username', autocapitalize: 'none', spellcheck: 'false', dir: 'ltr', required: true });
  const pass = h('input', { type: 'password', id: 'f-pass', autocomplete: 'current-password', dir: 'ltr', required: true });
  const nameRow = h('div', null, h('label', { for: 'f-display' }, 'שם לתצוגה (מה שאנשי הקשר שלך יראו)'), name);
  const submit = h('button', { type: 'submit', class: 'wide', 'data-test': 'login-submit' }, 'כניסה');
  const bLogin = h('button', { type: 'button', 'aria-pressed': 'true', 'data-test': 'mode-login' }, 'כניסה');
  const bReg = h('button', { type: 'button', 'aria-pressed': 'false', 'data-test': 'mode-register' }, 'הרשמה');
  const setMode = (m) => {
    mode = m;
    bLogin.setAttribute('aria-pressed', String(m === 'login'));
    bReg.setAttribute('aria-pressed', String(m === 'register'));
    nameRow.hidden = m === 'login';
    pass.autocomplete = m === 'login' ? 'current-password' : 'new-password';
    submit.textContent = m === 'login' ? 'כניסה' : 'הרשמה וכניסה';
    msg.textContent = '';
  };
  bLogin.addEventListener('click', () => setMode('login'));
  bReg.addEventListener('click', () => setMode('register'));
  const form = h('form', { class: 'card', novalidate: true },
    h('div', { class: 'seg' }, bLogin, bReg),
    nameRow,
    h('label', { for: 'f-user' }, 'שם משתמש'), user,
    h('label', { for: 'f-pass' }, 'סיסמה'), pass,
    h('p', { class: 'help' }, 'בהרשמה: שם משתמש 3-32 תווים, סיסמה לפחות 8 תווים.'),
    h('div', { class: 'spacer' }), submit, msg);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    submit.disabled = true;
    msg.textContent = '';
    try {
      if (mode === 'register') {
        await api('POST', '/auth/register', { username: user.value.trim(), password: pass.value, display_name: name.value.trim() || user.value.trim() });
      }
      const r = await api('POST', '/auth/login', { username: user.value.trim(), password: pass.value, device: 'LifeGuard PWA' });
      setSession(r.token, { username: r.username, display_name: r.display_name });
      startSession();
    } catch (e) {
      msg.textContent = e.offline ? 'אין חיבור לשרת. בדוק/י את החיבור ונסה/י שוב.' : errText(e);
    } finally {
      submit.disabled = false;
    }
  });
  setMode('login');
  const pendingWarn = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith('lg.sosOutbox.') && localStorage.getItem(k) && localStorage.getItem(k) !== 'null') pendingWarn.push(k);
  }
  const mark = document.querySelector('.brand .mark');
  main.append(h('div', { class: 'login' },
    iosInstallCard(),
    pendingWarn.length ? h('div', { class: 'card danger', 'data-test': 'sos-pending-login' },
      h('h2', null, '⚠️ קריאת חירום ממתינה ולא נשלחה'),
      h('p', null, 'ההתחברות פגה לפני שהקריאה הגיעה לשרת. התחבר/י כדי לשלוח אותה - או חייג/י עכשיו:'), telRow()) : null,
    h('div', { class: 'login-hero' }, mark ? mark.cloneNode(true) : null,
      h('h1', null, 'ברוכים הבאים ', h('span', { class: 'nobr' }, 'ל-LifeGuard')),
      h('p', { class: 'muted' }, 'קריאת חירום לאנשי הקשר שלך ולמתנדבים קרובים, ליווי הביתה ומעגל ביטחון.')),
    form,
    h('p', { class: 'muted small' }, 'בסכנת חיים מיידית - חייג/י קודם כל 100 / 101 / 112.')));
}

function startSession() {
  state.user = getUser();
  state.loopGen += 1;
  state.alerts.clear();
  state.notices = [];
  state.overdue = null;
  state.connOk = true;
  notificationLoop(state.loopGen);
  loadMe();
  loadInbox();
  refreshEscorts().catch(() => {});
  const mine = myIncident();
  if (mine) {
    api('GET', `/incidents/${mine.id}`).then((inc) => { if (inc.status === 'active') startTracking(inc.id, inc.mode); else setMyIncident(null); })
      .catch((e) => { if (e.status === 404) setMyIncident(null); else startTracking(mine.id, mine.mode); });
  }
  if (sosOutbox()) { geoUse('sos'); flushSOS(); }
  resyncPush();
  hazard.sessionStart();
  render();
}

function endSession() {
  state.loopGen += 1;
  stopTracking();
  stopEscortTracking();
  clearInterval(presenceTimer); presenceTimer = null;
  for (const who of [...geo.users]) geoRelease(who);
  clearSession();
  state.user = null; state.me = null; state.volRoles = null;
  state.alerts.clear(); state.notices = []; state.overdue = null;
  state.escorts = { mine: [], watching: [], fetchedAt: 0 };
  hazard.sessionEnd();
  location.hash = '';
  render();
}

// ------------------------------------------------------------------ iPhone / iPad: install card + honest capabilities
const INSTALL_HIDE_KEY = 'lg.iosInstallHidden';

/** "הוסיפו למסך הבית" - only on iPhone/iPad when not running from the home screen. Push depends on it. */
function iosInstallCard() {
  if (!IS_IOS || isStandalone()) return null;
  try { if (sessionStorage.getItem(INSTALL_HIDE_KEY)) return null; } catch { /* storage blocked: show it */ }
  const card = h('div', { class: 'card warn ios-card', 'data-test': 'ios-install-card', role: 'region', 'aria-label': 'הוספה למסך הבית' },
    secHead('h2', 'iphone', 'navy', 'הוסיפו את LifeGuard למסך הבית'),
    h('p', null, h('strong', null, 'למה? '), 'באייפון ובאייפד, התראות על קריאות חירום מגיעות רק לאפליקציה שנוספה למסך הבית (iOS 16.4 ומעלה) - לא ללשונית בספארי. בלי זה תראו קריאות רק כשהדף פתוח מולכם.'),
    h('ol', null,
      IS_IOS_SAFARI ? null : h('li', null, 'פתחו את הכתובת הזו ב-Safari. בדפדפנים אחרים באייפון האפשרות לא תמיד קיימת.'),
      h('li', null, 'הקישו על כפתור השיתוף ', h('span', { class: 'share-ico', 'aria-hidden': 'true' }, '⬆'),
        ' (ריבוע עם חץ למעלה) - בסרגל התחתון באייפון, למעלה באייפד. לא רואים אותו? הקישו קודם על "⋯" ליד שורת הכתובת.'),
      h('li', null, 'גללו ובחרו ', h('strong', null, '"הוספה למסך הבית"'), ' (Add to Home Screen).'),
      h('li', null, 'אם מופיע "פתיחה כיישום אינטרנט" (Open as Web App) - השאירו אותו מסומן, והקישו ', h('strong', null, '"הוספה"'), ' (Add).'),
      h('li', null, 'פתחו את LifeGuard מהאייקון החדש במסך הבית, התחברו, ובלשונית "פרופיל" הפעילו "התראות דחיפה".')),
    h('p', { class: 'small muted' }, 'האפליקציה שבמסך הבית שומרת נתונים בנפרד מספארי, לכן צריך להתחבר בה שוב (אותו חשבון).'));
  card.append(h('button', { class: 'ghost wide', 'data-test': 'ios-install-later', onclick: () => {
    try { sessionStorage.setItem(INSTALL_HIDE_KEY, '1'); } catch { /* hide for now anyway */ }
    card.remove();
  } }, 'לא עכשיו'));
  return card;
}

/** "מה עובד באייפון" - what a web app can and cannot do on iOS. Device statuses are checked live, not assumed. */
function iosCapabilitiesCard(open) {
  const av = pushAvailability();
  const perm = 'Notification' in window ? Notification.permission : 'unsupported';
  const pushLine = av.ok
    ? (perm === 'granted' ? 'במכשיר הזה ההרשאה ניתנה - בפרופיל רואים אם המכשיר רשום בשרת.' : 'במכשיר הזה עוד לא הופעלו - הפעילו בפרופיל.')
    : PUSH_UNAVAILABLE_HE[av.reason];
  const geoMsg = h('p', { class: 'small', 'data-test': 'geo-check-msg', role: 'status' });
  const geoBtn = h('button', { class: 'secondary wide', 'data-test': 'geo-check' }, icon('locate'), 'בדיקת הרשאת מיקום');
  geoBtn.addEventListener('click', () => {
    // asked from the tap (iOS); this check sends nothing to anyone
    if (!('geolocation' in navigator)) { geoMsg.className = 'small error'; geoMsg.textContent = 'המכשיר/הדפדפן לא תומך במיקום.'; return; }
    geoBtn.disabled = true; geoMsg.className = 'small muted'; geoMsg.textContent = 'בודק מיקום...';
    navigator.geolocation.getCurrentPosition((p) => {
      geoBtn.disabled = false; geoMsg.className = 'small okmsg';
      geoMsg.textContent = `✅ המיקום זמין (דיוק ${Math.round(p.coords.accuracy || 0)} מ'). הבדיקה הזו לא שלחה את המיקום לאף אחד.`;
    }, (err) => {
      geoBtn.disabled = false; geoMsg.className = 'small error';
      geoMsg.textContent = err.code === 1 ? GEO_DENIED_HE : 'המיקום לא זמין כרגע (אין קליטה?). נסו שוב ליד חלון או בחוץ.';
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  });
  const li = (cls, text) => h('li', { class: cls }, text);
  return h('details', { class: 'card', open: !!open, 'data-test': 'ios-capabilities' },
    h('summary', null, h('span', { class: 'ibadge grey', 'aria-hidden': 'true' }, icon('iphone')), 'מה עובד באייפון'),
    h('p', { class: 'muted small' }, 'באייפון ובאייפד LifeGuard רץ כאפליקציית רשת (דרך Safari). כך זה עובד בפועל:'),
    h('ul', { class: 'caps' },
      li('yes', 'קריאת SOS ומצוקה שקטה - כשהאפליקציה פתוחה ויש אינטרנט. "נמסר" מוצג רק כשהשרת מאשר.'),
      li('yes', 'מיקום בזמן שהאפליקציה פתוחה ועל המסך (אחרי שאישרתם הרשאת מיקום).'),
      li('yes', 'חיוג ישיר ל-100 / 101 / 112, וניווט למקום קריאה במפות Apple.'),
      li('yes', 'ליווי הביתה: הטיימר רץ בשרת ולא בטלפון. אם לא תאשרו הגעה בזמן - השרת פותח קריאת חירום לאנשי הקשר עם המיקום האחרון שנשלח, גם אם האפליקציה סגורה, המסך נעול או שהסוללה נגמרה.'),
      li(av.ok ? 'partly' : 'no', 'התראות דחיפה על קריאות: רק מהאפליקציה שבמסך הבית, iOS 16.4 ומעלה, ואחרי הפעלה בפרופיל. הן לא עוקפות מצב שקט או מצב ריכוז. ' + pushLine),
      li('no', 'מעקב מיקום ברקע: כשהמסך ננעל או שעוברים לאפליקציה אחרת, iOS עוצר את האפליקציה והמיקום מפסיק להישלח. אנשי הקשר רואים את המיקום האחרון עם השעה שבה נשלח - לא כמיקום חי.'),
      li('no', 'זיהוי נפילה, זיהוי מצוקה אוטומטי והפעלה כשהאפליקציה סגורה - לא אפשריים באפליקציית רשת באייפון.'),
      li('partly', 'צליל התראה: רק כשהאפליקציה פתוחה ואחרי נגיעה אחת במסך, וייתכן שלא יישמע במצב שקט. רטט - לא נתמך באייפון.'),
      li(wakeSupported() ? 'partly' : 'no', wakeSupported()
        ? 'מסך דלוק בזמן קריאת חירום פעילה: LifeGuard מבקש מהמכשיר להשאיר את המסך דלוק כל עוד הוא פתוח.'
        : 'מסך דלוק בזמן קריאה: המכשיר הזה לא מאפשר לאפליקציה להשאיר את המסך דלוק - השאירו אותו פתוח בעצמכם.')),
    h('p', { class: 'small' }, h('strong', null, 'הולכים לבד? '), 'הפעילו "ליווי הביתה" לפני היציאה - זו ההגנה שעובדת באייפון גם כשהטלפון בכיס והמסך נעול.'),
    h('p', { class: 'small muted', 'data-test': 'ios-standalone-state' }, isStandalone() ? 'LifeGuard פתוח מהמסך הבית ✓' : 'LifeGuard פתוח כרגע בדפדפן, לא מהמסך הבית.'),
    geoBtn, geoMsg);
}

// ------------------------------------------------------------------ HOME
function viewHome(main, arg) {
  const sosSlot = h('div', { class: 'no-anim' });
  const escortSlot = h('div', { class: 'card', 'data-test': 'escort-card', id: 'escort-card' });
  const watchSlot = h('div');
  const readySlot = h('div', { class: 'card', 'data-test': 'readiness-card' });
  const supportSlot = h('div', { class: 'card', 'data-test': 'support-card' });
  const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const tiles = h('div', { class: 'tiles' },
    h('a', { class: 'tile', href: '#map', 'data-test': 'home-map-tile' }, h('span', { class: 'ibadge navy', 'aria-hidden': 'true' }, icon('map')),
      h('span', null, 'סביבה ומסלול', h('small', null, 'תאורה, תאונות ומסלול הליכה'))),
    h('a', { class: 'tile', href: '#home', 'data-test': 'home-escort-tile', onclick: (ev) => { ev.preventDefault(); escortSlot.scrollIntoView({ block: 'start', behavior: reduce() ? 'auto' : 'smooth' }); } },
      h('span', { class: 'ibadge teal', 'aria-hidden': 'true' }, icon('walk')),
      h('span', null, 'ליווי הביתה', h('small', null, 'טיימר בשרת, גם כשהמסך נעול'))));
  main.append(...[iosInstallCard(), homeHero(), sosSlot, hazard.homeCard(), tiles, supportSlot, escortSlot, watchSlot, readySlot,
    IS_IOS ? iosCapabilitiesCard(false) : null].filter(Boolean));
  if (arg === 'sos') history.replaceState(null, '', '#home');   // opened from the bar's SOS: straight to the confirm

  // --- "צריך/ה לדבר עם מישהו?" (לא חירום) - support_ui.py SupportCard
  const drawSupportCard = () => {
    const open = mySupport();
    supportSlot.replaceChildren(secHead('h2', 'chat', 'indigo', 'צריך/ה לדבר עם מישהו?'),
      open ? h('p', null, 'יש לך שיחה פתוחה עם מתנדבי התמיכה.')
        : h('p', { class: 'muted' }, 'לא חייב להיות חירום. מתנדב/ת יקשיב/תקשיב בצ\'אט - בלי לראות את שמך ואת מיקומך.'),
      h('button', { class: open ? 'wide' : 'secondary wide', 'data-test': 'support-open', onclick: () => { location.hash = '#support'; } },
        icon('chat'), open ? 'חזרה לשיחה' : 'פתח/י שיחה'),
      h('p', { class: 'muted small' }, 'ער"ן 1201 · בסכנה מיידית 100'));
  };
  drawSupportCard();

  // --- SOS / pending / active
  const drawSos = () => {
    const out = sosOutbox();
    const mine = myIncident();
    sosSlot.replaceChildren();
    if (out) {
      sosSlot.append(h('div', { class: out.mode === 'silent' ? 'card warn' : 'card danger', 'data-test': 'sos-pending' },
        h('h2', null, out.attempts ? '⚠️ הקריאה עדיין לא נשלחה' : 'שולח קריאה לשרת...'),
        out.attempts ? h('p', null, `הקריאה לא הגיעה לשרת (${out.lastError || 'שגיאה'}). אף אחד עדיין לא קיבל אותה. מנסה שוב כל 3 שניות - ניסיון ${out.attempts}.`)
          : h('p', null, out.location ? 'כולל מיקום.' : 'מאתר מיקום (עד 3 שניות)...'),
        out.attempts ? h('div', null, h('p', { class: 'big' }, 'חייג/י בינתיים:'), telRow()) : null,
        h('div', { class: 'li-actions' },
          h('button', { class: 'secondary', onclick: () => flushSOS() }, 'נסה/י שוב עכשיו'),
          h('button', { class: 'ghost', onclick: () => { setSosOutbox(null); geoRelease('sos'); } }, 'ביטול השליחה'))));
      return;
    }
    if (mine) {
      sosSlot.append(h('div', { class: mine.mode === 'silent' ? 'card calm' : 'card alarm', 'data-test': 'my-incident-link' },
        h('p', { class: 'big' }, mine.mode === 'silent' ? 'יש לך פעילות פתוחה' : '🚨 יש לך קריאת חירום פעילה'),
        h('button', { class: mine.mode === 'silent' ? 'secondary wide' : 'red wide', onclick: () => { location.hash = '#incident/' + mine.id; } }, 'למצב הקריאה')));
      return;
    }
    sosSlot.append(sosPanel(arg === 'sos'));
    arg = null;
  };
  drawSos();
  listen('sos', drawSos);

  // --- escort
  let tick = null;
  let drawnFor = null;
  const drawEscort = () => {
    const cur = state.escorts.mine[0];
    const sig = cur ? `${cur.id}:${Math.round(cur._deadlineLocal)}:${cur.overdue}:${(cur.watchers || []).length}` : 'none';
    // same escort & deadline (or still no escort): keep the DOM, so an open "cancel?" confirmation
    // or a half-typed destination survives the periodic refresh
    if (sig === drawnFor) return;
    drawnFor = sig;
    clearInterval(tick);
    escortSlot.replaceChildren(secHead('h2', 'walk', 'teal', 'ליווי הביתה'));
    const mine = state.escorts.mine[0];
    if (mine) {
      const cd = h('div', { class: 'countdown', 'data-test': 'escort-countdown' });
      const geoLine = h('p', { class: 'small' });
      const upd = () => {
        const left = mine._deadlineLocal - Date.now() / 1000;
        cd.textContent = left > 0 ? mmss(left) : '00:00';
        const g = geoStatusText(etrack.sentAt, etrack.error);
        geoLine.className = 'small ' + g.cls; geoLine.textContent = g.text;
      };
      upd();
      tick = setInterval(upd, 1000);
      const msg = h('p', { class: 'error' });
      const act = async (path, body) => {
        try { await api('POST', `/escorts/${mine.id}/${path}`, body); state.overdue = null; await refreshEscorts(); } catch (e) { msg.textContent = errText(e) + (e.offline ? ' - הפעולה לא נשמרה בשרת!' : ''); }
      };
      const actions = h('div', { class: 'li-actions' },
        h('button', { class: 'green', 'data-test': 'escort-arrived', onclick: () => act('arrived') }, '✅ הגעתי בשלום'),
        h('button', { class: 'secondary', 'data-test': 'escort-extend', onclick: () => act('extend', { minutes: 15 }) }, 'הארכה ב-15 דק\''));
      const cancelHolder = h('div', null, h('button', { class: 'ghost wide', onclick: () => confirmIn(cancelHolder, 'לבטל את הליווי?', 'כן, בטל', 'red', () => act('cancelled')) }, 'ביטול הליווי'));
      escortSlot.append(
        h('p', null, mine.destination ? `בדרך ל: ${mine.destination}` : 'ליווי פעיל', ' ', pill(mine.mode === 'silent' ? 'שקט' : 'גלוי')),
        h('p', { class: 'muted small' }, 'זמן שנותר:'), cd,
        mine.overdue ? h('p', { class: 'error' }, 'הזמן עבר - אשר/י הגעה או הארך/י, אחרת תיפתח קריאת חירום.') : null,
        (mine.watchers || []).length
          ? h('p', null, 'רואים את הליווי: ', mine.watchers.map((w) => w.display_name).join(', '))
          : h('p', { class: 'error' }, 'אין אנשי קשר מחוברים - אף איש קשר לא יראה את הליווי.'),
        geoLine, IS_IOS ? h('p', { class: 'small muted', 'data-test': 'escort-ios-note' }, 'באייפון: כשהמסך נעול המיקום לא מתעדכן - אבל הטיימר רץ בשרת. אם לא תאשרו הגעה, הקריאה תיפתח עם המיקום האחרון שנשלח.') : null, actions, cancelHolder, msg);
      return;
    }
    const minutes = h('select', { id: 'esc-min', 'data-test': 'escort-minutes', value: '20' },
      [10, 15, 20, 30, 45, 60, 90, 120].map((m) => h('option', { value: String(m) }, `${m} דקות`)));
    minutes.value = '20';
    const dest = h('input', { type: 'text', id: 'esc-dest', maxlength: '80', placeholder: 'למשל: הבית', 'data-test': 'escort-dest' });
    const silent = h('input', { type: 'checkbox', id: 'esc-silent', class: 'switch' });
    const msg = h('p', { class: 'error' });
    const start = h('button', { class: 'wide', 'data-test': 'escort-start' }, 'התחל ליווי');
    start.addEventListener('click', async () => {
      start.disabled = true; msg.textContent = '';
      try {
        geoUse('escort-start');
        const fix = await geoFix(4000);
        const body = { minutes: Number(minutes.value), destination: dest.value.trim(), mode: silent.checked ? 'silent' : 'loud' };
        if (fix) body.location = locPayload(fix);
        await api('POST', '/escorts', body);
        await refreshEscorts();
      } catch (e) {
        msg.textContent = (e.offline ? 'אין חיבור לשרת - הליווי לא התחיל.' : errText(e));
      } finally {
        geoRelease('escort-start');
        start.disabled = false;
      }
    });
    escortSlot.append(
      h('p', { class: 'small' }, h('a', { href: '#map', 'data-test': 'escort-route-link' }, 'בדיקת מסלול ההליכה לפני היציאה ›')),
      h('p', { class: 'muted' }, 'אנשי הקשר שלך יראו שאת/ה בדרך ואת המיקום שלך. אם לא תאשר/י הגעה עד תום הזמן, השרת ישאל אותך - ואם אין תשובה, תיפתח קריאת חירום אוטומטית עם המיקום האחרון (גם אם הטלפון כבוי).'),
      h('label', { for: 'esc-min' }, 'כמה זמן הדרך?'), minutes,
      h('label', { for: 'esc-dest' }, 'יעד (לא חובה)'), dest,
      h('label', { class: 'inline', for: 'esc-silent' }, silent, 'אם לא אאשר הגעה - מצוקה שקטה (רק לאנשי הקשר)'),
      IS_IOS ? h('p', { class: 'small muted', 'data-test': 'escort-ios-note' }, 'באייפון: כשהמסך נעול המיקום לא מתעדכן - אבל הטיימר רץ בשרת. אם לא תאשרו הגעה, הקריאה תיפתח עם המיקום האחרון שנשלח.') : null,
      start, msg);
  };
  onCleanup(() => clearInterval(tick));

  const drawWatching = () => {
    watchSlot.replaceChildren();
    if (state.escorts.watching.length) watchSlot.append(watchingCard(state.escorts.watching));
  };
  const loadEsc = async () => { try { await refreshEscorts(); } catch { /* banner */ } };
  drawEscort(); drawWatching();
  listen('escorts-loaded', () => { drawEscort(); drawWatching(); });
  listen('escorts', loadEsc);
  listen('etrack', () => { /* text updated by ticker */ });
  loadEsc();
  every(20000, loadEsc);

  // --- readiness
  let ready = null;
  const drawReady = () => {
    readySlot.replaceChildren(secHead('h2', 'checklist', 'green', 'בדיקת מוכנות'));
    const msg = h('p', { class: 'error' });
    const run = h('button', { class: 'secondary wide', 'data-test': 'readiness-run' }, 'שלח/י הודעת בדיקה לאנשי הקשר');
    run.addEventListener('click', async () => {
      run.disabled = true; msg.textContent = '';
      try { ready = await api('POST', '/me/readiness'); drawReady(); } catch (e) { msg.textContent = e.offline ? 'אין חיבור לשרת - הבדיקה לא נשלחה.' : errText(e); run.disabled = false; }
    });
    readySlot.append(h('p', { class: 'muted' }, 'שולחת לאנשי הקשר הודעה שמסומנת בבירור "בדיקה" ומראה מי מהם האפליקציה שלו קיבלה אותה בפועל.'));
    if (!ready || !ready.created_at) {
      readySlot.append(h('p', { class: 'error' }, 'עוד לא בוצעה בדיקת מוכנות. מומלץ לבדוק שאנשי הקשר שלך באמת יקבלו קריאה.'));
    } else {
      const days = (serverNow() - ready.created_at) / 86400;
      if (days > 30) readySlot.append(h('p', { class: 'error', 'data-test': 'readiness-remind' }, `הבדיקה האחרונה הייתה לפני ${Math.floor(days)} ימים - מומלץ לבדוק שוב.`));
      readySlot.append(h('p', null, `בדיקה אחרונה: ${clock(ready.created_at, true)}`),
        h('ul', { class: 'list', 'data-test': 'readiness-results' }, ready.results.map((r) => h('li', { class: 'li-head' },
          h('span', null, r.display_name),
          r.received_at ? pill(`✓ התקבל ${clock(r.received_at)}`, 'ok') : pill('עדיין לא התקבל באפליקציה', 'wait')))),
        ready.all_received ? h('p', { class: 'okmsg' }, 'כל אנשי הקשר קיבלו את הודעת הבדיקה.') : null);
    }
    readySlot.append(run, msg);
  };
  const loadReady = async () => { try { ready = await api('GET', '/me/readiness'); drawReady(); } catch { /* keep last */ } };
  drawReady();
  loadReady();
  every(5000, async () => { if (ready && ready.created_at && !ready.all_received && serverNow() - ready.created_at < 900) await loadReady(); });
}

/** Hold-progress ring around the SOS button (vector; feedback only - the 3 s timer sends, not the animation). */
function sosRing() {
  const NS = 'http://www.w3.org/2000/svg';
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('class', 'sos-ring'); s.setAttribute('viewBox', '0 0 100 100'); s.setAttribute('aria-hidden', 'true');
  const c = document.createElementNS(NS, 'circle');
  for (const [k, v] of Object.entries({ cx: 50, cy: 50, r: 48, pathLength: 100 })) c.setAttribute(k, String(v));
  s.append(c);
  return s;
}

/** Home: a calm identity header - who is signed in and what is ready (honest: only what the device/server says). */
function homeHero() {
  const name = state.user ? state.user.display_name || state.user.username : '';
  const chips = h('div', { class: 'chips', 'data-test': 'home-status' });
  const chip = (ico, text, off) => h('span', { class: 'chip-s' + (off ? ' off' : '') }, icon(ico), text);
  const hero = h('section', { class: 'hero', 'aria-label': 'מצב' },
    h('h1', null, name ? `שלום, ${name}` : 'LifeGuard'), chips);
  const st = { circle: null, push: null, geo: null };
  const draw = () => {
    const kids = [];
    if (st.circle != null) kids.push(chip('people', st.circle ? `אנשי קשר: ${st.circle}` : 'אין עדיין אנשי קשר', !st.circle));
    if (st.geo) kids.push(chip('pin', st.geo === 'granted' ? 'מיקום מאושר' : st.geo === 'denied' ? 'מיקום חסום' : 'מיקום: יתבקש בשליחה', st.geo === 'denied'));
    if (st.push != null) kids.push(chip('bell', st.push ? 'התראות פעילות' : 'התראות כבויות', !st.push));
    chips.replaceChildren(...kids);
  };
  api('GET', '/circle').then((c) => { st.circle = (c.my_contacts || []).length; draw(); }).catch(() => {});
  currentSubscription().then((sub) => { st.push = !!sub; draw(); }).catch(() => {});
  try {
    navigator.permissions.query({ name: 'geolocation' }).then((p) => { st.geo = p.state; draw(); }).catch(() => {});
  } catch { /* Permissions API missing (older Safari): no chip */ }
  return hero;
}

function sosPanel(autoConfirm = false) {
  const wrap = h('div', { class: 'card sos-card' });
  const hint = h('p', { class: 'sos-hint', 'data-test': 'sos-hint' }, 'לחיצה ארוכה 3 שניות שולחת מיד. הקשה קצרה פותחת אישור.');
  const btn = h('button', { class: 'sos-btn', 'data-test': 'sos-button', 'aria-label': 'קריאת חירום SOS - לחיצה ארוכה 3 שניות או הקשה ואישור' },
    h('span', null, 'SOS'), h('span', { class: 'sos-sub' }, 'קריאת חירום'));
  const stage = h('div', { class: 'sos-stage' }, btn, sosRing());
  let holdStart = 0, holdTimer = null, countTimer = null;
  const resetHold = () => { clearTimeout(holdTimer); clearInterval(countTimer); holdStart = 0; btn.classList.remove('holding'); stage.classList.remove('holding'); geoRelease('sos-arm'); };
  btn.addEventListener('contextmenu', (e) => e.preventDefault());
  btn.addEventListener('pointerdown', (e) => {
    if (e.button !== undefined && e.button !== 0) return;
    holdStart = Date.now();
    geoUse('sos-arm');   // location permission is asked inside the tap (iOS); the GPS warms up meanwhile
    btn.classList.add('holding');
    stage.classList.add('holding');
    hint.textContent = 'ממשיך/ה ללחוץ... 3';
    countTimer = setInterval(() => { const left = Math.ceil((HOLD_MS - (Date.now() - holdStart)) / 1000); if (left > 0) hint.textContent = `ממשיך/ה ללחוץ... ${left}`; }, 250);
    holdTimer = setTimeout(() => { geoUse('sos'); resetHold(); hint.textContent = 'שולח...'; triggerSOS('loud', 'unknown'); }, HOLD_MS);
  });
  // A short tap opens the confirm from the tap's own `click`, never from pointerup: on touch screens the click that
  // follows touchend is dispatched to whatever is under the finger at that moment - opening the panel on pointerup
  // put the red "send now" button there, and the same tap sent the SOS (ghost click). Fallback if no click comes.
  let tapTimer = null;
  const HINT = 'לחיצה ארוכה 3 שניות שולחת מיד. הקשה קצרה פותחת אישור.';
  const openFromTap = () => { clearTimeout(tapTimer); tapTimer = null; hint.textContent = HINT; showConfirm('loud'); };
  const release = () => {
    if (!holdStart) return;
    const held = Date.now() - holdStart;
    resetHold();
    if (held < 700) { clearTimeout(tapTimer); tapTimer = setTimeout(openFromTap, 350); }
    else hint.textContent = 'שחררת לפני 3 שניות - לא נשלח. הקשה קצרה פותחת אישור.';
  };
  btn.addEventListener('pointerup', release);
  btn.addEventListener('pointercancel', () => { resetHold(); hint.textContent = 'לא נשלח.'; });
  btn.addEventListener('pointerleave', () => { if (holdStart) { resetHold(); hint.textContent = 'לא נשלח (האצבע זזה מהכפתור).'; } });
  btn.addEventListener('click', (e) => { if (tapTimer || e.detail === 0) openFromTap(); }); // the tap's click; keyboard (detail 0)
  const silentBtn = h('button', { class: 'silent-btn wide', 'data-test': 'silent-button', onclick: () => showConfirm('silent') }, 'מצוקה שקטה');
  const area = h('div', { class: 'sos-wrap' }, stage, hint);
  const confirmSlot = h('div');
  function showConfirm(mode) {
    const type = h('select', { id: 'sos-type', 'data-test': 'sos-type' }, Object.entries(TYPE_HE).map(([k, v]) => h('option', { value: k }, v)));
    const send = h('button', { class: mode === 'silent' ? 'wide' : 'red wide', 'data-test': 'sos-confirm' }, mode === 'silent' ? 'שליחה' : '🚨 שלח/י קריאת חירום עכשיו');
    // defence in depth: a click counts only if the press started on this button (or comes from the keyboard) -
    // a stray click left over from the gesture that opened the panel can never send. After 600 ms any click counts,
    // so assistive tech (VoiceOver activates without pointerdown) can always send.
    let pressed = false;
    const openedAt = Date.now();
    send.addEventListener('pointerdown', () => { pressed = true; });
    send.addEventListener('click', (e) => {
      if (!pressed && e.detail !== 0 && Date.now() - openedAt < 600) return;
      send.disabled = true; triggerSOS(mode, type.value);
    });
    const close = () => { confirmSlot.replaceChildren(); area.hidden = false; silentBtn.hidden = false; };
    // the confirmation replaces the big button (same place on screen - no hunting for it under stress)
    area.hidden = true; silentBtn.hidden = true;
    confirmSlot.replaceChildren(h('div', { class: mode === 'silent' ? 'card calm' : 'card alarm', 'data-test': 'sos-confirm-panel' },
      h('p', { class: 'big' }, mode === 'silent' ? 'לשלוח בשקט לאנשי הקשר?' : 'לשלוח קריאת חירום?'),
      h('p', { class: 'muted' }, mode === 'silent'
        ? 'נשלח רק לאנשי הקשר שלך (למתנדבים רק אם אין אף איש קשר מחובר). אין צליל, ומסך זה יישאר רגיל.'
        : 'הקריאה תישלח לאנשי הקשר שלך ולמתנדב/ת קרוב/ה, עם המיקום שלך.'),
      mode === 'silent' ? null : h('div', null, h('label', { for: 'sos-type' }, 'סוג (לא חובה)'), type),
      h('div', { class: 'spacer' }), send,
      h('button', { class: 'ghost wide', 'data-test': 'sos-cancel', onclick: close }, 'ביטול')));
    wrap.scrollIntoView({ block: 'start' });
  }
  wrap.append(h('p', { class: 'sos-label' }, 'במצב חירום'), area, confirmSlot, silentBtn, h('div', { class: 'spacer' }), telRow());
  if (autoConfirm) queueMicrotask(() => showConfirm('loud'));
  return wrap;
}

// נסיעה בטוחה (לצופים): סוג, רכב, התקדמות, חריגה פתוחה
function rideLines(e) {
  if (e.kind !== 'ride') return [];
  const v = e.vehicle || {};
  const car = [v.plate, v.description, v.driver ? `נהג/ת: ${v.driver}` : null].filter(Boolean).join(' · ');
  const out = [h('p', { 'data-test': 'ride-info' }, `🚕 ${e.ride_label || 'נסיעה'}${car ? ' · ' + car : ''}`)];
  const p = e.progress || {};
  if (p.remaining_m != null) out.push(h('p', { class: 'small muted' }, `נותרו כ-${(p.remaining_m / 1000).toFixed(1)} ק"מ` + (p.eta_minutes ? ` · הגעה משוערת בעוד ${p.eta_minutes} דק'` : '')));
  if (e.anomaly) out.push(h('p', { class: 'small', 'data-test': 'ride-anomaly' }, '⚠️ ' + (e.anomaly.text || 'חריגה בנסיעה')));
  return out;
}

function watchingCard(list) {
  return h('div', { class: 'card', 'data-test': 'watching-card' },
    secHead('h2', 'eye', 'teal', 'ליווי של אנשים שלך'),
    h('ul', { class: 'list' }, list.map((e) => {
      const left = e._deadlineLocal - Date.now() / 1000;
      return h('li', { 'data-test': 'watching-item' },
        h('div', { class: 'li-head' }, h('strong', null, e.owner_display), e.overdue ? pill('לא אישר/ה הגעה בזמן', 'bad') : pill(`נותרו ${mmss(left)}`, 'info')),
        e.destination ? h('p', null, `בדרך ל: ${e.destination}`) : null,
        ...rideLines(e),
        locLine(e.location, 'updated_at'),
        e.location ? h('a', { class: 'btn secondary', href: mapsUrl(e.location.lat, e.location.lon), target: '_blank', rel: 'noopener' }, 'ניווט למיקום האחרון') : null);
    })));
}

const RISK_HE = { HIGH: 'גבוהה', MEDIUM: 'בינונית', LOW: 'נמוכה', UNKNOWN: 'לא ידועה' };
function riskCard(risk) {
  const level = risk.level || 'UNKNOWN';
  const scale = risk.scale || 8;
  const fallback = { HIGH: 7, MEDIUM: 4, LOW: 1 }[level] || 0;
  const pct = Math.round(100 * Math.min(1, (risk.score != null ? risk.score : fallback) / scale));
  const cls = level === 'HIGH' ? 'danger' : level === 'MEDIUM' ? 'warn' : '';
  const card = h('div', { class: `card ${cls}`, 'data-test': 'risk' },
    h('h3', null, `🎚️ רמת סכנה: ${RISK_HE[level] || level}`),
    h('progress', { class: `risk-meter ${cls}`, max: '100', value: String(pct), 'aria-label': 'מד רמת סכנה' }),
    h('p', { class: 'muted small' }, `עדיפות בתור המתנדבים: ${risk.band_label || ''}`),
    h('p', { class: 'small', 'data-test': 'risk-confidence' },
      typeof risk.confidence === 'number' ? `ביטחון בזיהוי: ${Math.round(risk.confidence * 100)}%` : 'ביטחון בזיהוי: לא ידוע'));
  const ctx = risk.context || {};
  const tags = [{ night: '🌙 לילה', twilight: '🌆 דמדומים' }[ctx.phase], { dark: 'רחוב חשוך (אין עמודי תאורה)', lit: 'רחוב מואר' }[ctx.lighting]].filter(Boolean);
  if (tags.length) card.append(h('p', { class: 'small', 'data-test': 'risk-context' }, tags.join(' · ')));
  if (risk.reasons && risk.reasons.length) card.append(h('ul', null, risk.reasons.slice(0, 4).map((r) => h('li', null, r))));
  else if (!risk.reasons) card.append(h('p', { class: 'muted small' }, 'פירוט הסיבות יוצג אחרי קבלת הקריאה.'));
  return card;
}

function locLine(loc, tsKey = 'received_at') {
  if (!loc) return h('p', { class: 'muted' }, 'אין מיקום ידוע.');
  const ts = loc[tsKey] || loc.received_at || loc.captured_at;
  const old = ts && serverNow() - ts > 120;
  return h('div', { 'data-test': 'loc-line' },
    h('p', null, loc.approximate ? 'מיקום משוער: ' : 'מיקום אחרון: ', h('span', { class: 'mono' }, `${Number(loc.lat).toFixed(5)}, ${Number(loc.lon).toFixed(5)}`),
      loc.accuracy_m ? ` (דיוק ${Math.round(loc.accuracy_m)} מ')` : '',
      String(loc.source || '').startsWith('manual') ? ' · הוזן ידנית' : (loc.is_precise ? ' · GPS' : (loc.approximate ? '' : ' · לא מדויק'))),
    h('p', { class: old ? 'error' : 'muted small' }, ts ? `${old ? '⚠️ מיקום ישן - ' : ''}עודכן ${ago(ts)} (${clock(ts)})` : ''));
}

// ------------------------------------------------------------------ INCIDENT (owner + responder)
function viewIncident(main, iid) {
  const head = h('div');
  const body = h('div');
  const actionsSlot = h('div');
  const helpersCard = h('div', { class: 'card', 'data-test': 'owner-helpers' });   // נבנה פעם אחת, מוזז בין רינדורים
  const chat = makeChat(iid, () => load());
  main.append(head, body, actionsSlot, chat.card);
  let inc = null, loadedAt = 0, failed = null, myResp = null, timelineOpen = false;
  const staleLine = h('p', { class: 'error', hidden: true });

  const nameOf = (u) => {
    if (state.user && u === state.user.username) return 'אני';
    if (inc && u === inc.owner) return inc.owner_display;
    const r = inc && (inc.recipients || []).find((x) => x.username === u);
    return r ? r.display_name : u;
  };

  const draw = () => {
    if (!inc) return;
    staleLine.hidden = !failed;
    staleLine.textContent = failed ? `⚠️ לא ניתן לרענן (${failed}). המצב המוצג עודכן ${ago(loadedAt)} ועלול להיות לא עדכני.` : '';
    if (inc.my_role === 'owner') drawOwner(); else if (inc.my_role === 'helper') drawHelper(); else drawResponder();
    // מתנדב/ת תמיכה בבקשת שיחה: לפונה אין תווית "במצוקה"; בתוך חירום - כרגיל
    const roleHe = inc.my_role === 'helper' && inc.support_request ? { ...MSG_ROLE_HE, owner: null } : MSG_ROLE_HE;
    chat.render(inc, { nameOf, roleHe });
  };

  const drawOwner = () => {
    if (inc.activation === 'support') {             // בקשת שיחה (לא חירום) - מסך השיחה, בלי מעקב מיקום
      if (inc.status === 'active') setMySupport(iid);
      stopPoll();
      location.replace('#support');
      return;
    }
    const silent = inc.mode === 'silent';
    const active = inc.status === 'active';
    const recips = inc.recipients || [];
    const delivered = recips.filter((r) => r.state === 'delivered' || r.state === 'accepted').length;
    const accepted = recips.filter((r) => r.state === 'accepted').length;
    if (!active) {
      if (myIncident() && myIncident().id === iid) setMyIncident(null);
      head.replaceChildren(h('div', { class: 'card good', 'data-test': 'incident-closed' },
        h('h2', null, inc.status === 'resolved' ? '✅ האירוע הסתיים' : 'האירוע בוטל'),
        h('p', null, 'אנשי הקשר קיבלו הודעה שהאירוע הסתיים. הגישה של מתנדבים בוטלה.'),
        h('button', { class: 'wide', onclick: () => { location.hash = '#home'; } }, 'חזרה לבית')));
    } else if (silent) {
      head.replaceChildren(h('div', { class: 'card calm plain', 'data-test': 'incident-owner-silent' },
        h('h2', null, 'פעילות פתוחה'),
        h('p', { class: 'muted' }, `נפתחה ${clock(inc.created_at)}`), staleLine));
    } else {
      head.replaceChildren(h('div', { class: 'card alarm-head alarm', 'data-test': 'incident-owner' },
        h('h1', null, '🚨 קריאת חירום פעילה'),
        h('p', null, `התקבלה בשרת ${clock(inc.created_at)} · ${TYPE_HE[inc.emergency_type] || TYPE_HE.unknown}`), staleLine));
    }
    const summary = recips.length === 0
      ? h('div', { class: silent ? 'card calm' : 'card danger', 'data-test': 'no-recipients' },
        h('p', { class: 'big' }, 'אף אחד לא קיבל את הקריאה'),
        h('p', null, 'אין אנשי קשר מחוברים ואין מתנדב זמין בטווח (או שאין עדיין מיקום). חייג/י:'), telRow())
      : h('p', { class: 'big', 'data-test': 'delivery-summary' }, `נשלח ל-${recips.length} · נמסר לאפליקציה: ${delivered} · קיבלו: ${accepted}`);
    const list = h('ul', { class: 'list', 'data-test': 'recipients' }, recips.map((r) => h('li', { 'data-test': 'recipient', 'data-user': r.username, 'data-state': r.state },
      h('div', { class: 'li-head' },
        h('strong', null, r.display_name),
        h('span', null, pill(r.role === 'volunteer' ? (r.verified ? 'מתנדב/ת מאומת/ת' : 'מתנדב/ת') : 'איש קשר'), ' ',
          pill(STATE_HE[r.state] || r.state, silent ? '' : STATE_CLS[r.state] || ''))),
      r.response && r.state === 'accepted' && r.response !== 'accept' ? h('p', null, RESP_HE[r.response] || r.response) : null,
      r.state === 'notified' ? h('p', { class: 'muted small' }, 'השרת שלח - האפליקציה שלו/ה עוד לא אישרה קבלה.') : null,
      r.distance_band_km != null ? h('p', { class: 'muted small' }, `מרחק: עד ${r.distance_band_km} ק"מ` + (r.eta_minutes ? ` · ~${r.eta_minutes} דק' הליכה` : '')) : null,
      r.sms_status ? h('p', { class: 'muted small' }, SMS_HE[r.sms_status] || r.sms_status) : null)));
    const g = geoStatusText(track.iid === iid ? track.sentAt : 0, track.iid === iid ? track.error : null);
    const parts = [];
    if (active) {
      parts.push(h('div', { class: 'card' + (silent ? ' calm plain' : '') }, h('h3', null, 'מי קיבל'), summary, list));
      parts.push(h('div', { class: 'card' + (silent ? ' calm plain' : '') }, h('h3', null, 'מיקום'),
        h('p', { class: g.cls, 'data-test': 'track-status' }, g.text),
        inc.location ? locLine(inc.location) : h('p', { class: 'error' }, 'לשרת אין עדיין מיקום לאירוע.'),
        track.iid === iid && (!silent || IS_IOS) ? wakeLine(silent) : null));
      drawOwnerHelpers();
      parts.push(helpersCard);
    }
    if ((inc.timeline || []).length) {
      parts.push(h('details', { class: 'card', open: timelineOpen }, h('summary', null, 'יומן האירוע'),
        h('ul', { class: 'list small' }, inc.timeline.map((t) => h('li', null, h('span', { class: 'muted' }, clock(t.ts) + ' · '), t.text)))));
      parts[parts.length - 1].addEventListener('toggle', (ev) => { timelineOpen = ev.currentTarget.open; });
    }
    if (!active && recips.length) parts.push(h('div', { class: 'card' }, h('h3', null, 'מי קיבל'), list));
    body.replaceChildren(...parts);
    // action buttons are built once per mode, so a 3s refresh never wipes an open confirmation
    const key = active ? inc.mode : 'closed';
    if (actionsSlot.dataset.key === key) return;
    actionsSlot.dataset.key = key;
    actionsSlot.replaceChildren();
    if (active) {
      const actions = h('div', { class: 'card' + (silent ? ' calm plain' : '') });
      const close = (status) => async () => {
        inc = await api('PATCH', `/incidents/${iid}`, { status });
        setMyIncident(null);
        draw();
      };
      const resolveHolder = h('div', null, h('button', { class: 'green wide', 'data-test': 'incident-resolve', onclick: () => confirmIn(resolveHolder, silent ? 'לסיים?' : 'לסמן שאת/ה בטוח/ה ולסיים את הקריאה?', silent ? 'כן, סיום' : 'כן, אני בטוח/ה', 'green', close('resolved')) }, silent ? 'סיום' : '✅ אני בטוח/ה - סיום'));
      const cancelHolder = h('div', null, h('button', { class: 'ghost wide', 'data-test': 'incident-cancel', onclick: () => confirmIn(cancelHolder, 'לבטל את הקריאה (הופעלה בטעות)?', 'כן, בטל', 'red', close('cancelled')) }, 'ביטול - הופעל בטעות'));
      actions.append(resolveHolder, h('div', { class: 'spacer' }), cancelHolder);
      if (silent) {
        const escHolder = h('div', null, h('button', { class: 'secondary wide', 'data-test': 'incident-escalate', onclick: () => confirmIn(escHolder, 'להפוך לקריאת חירום גלויה (גם למתנדבים)?', 'כן, הסלם', 'red', async () => { inc = await api('PATCH', `/incidents/${iid}`, { mode: 'loud' }); setMyIncident(iid, 'loud'); draw(); }) }, 'הפוך לקריאה גלויה'));
        actions.append(h('div', { class: 'spacer' }), escHolder);
      } else {
        actions.append(h('div', { class: 'spacer' }), telRow());
      }
      actionsSlot.append(actions);
    }
  };

  const drawResponder = () => {
    state.alerts.set(iid, inc);
    const active = inc.status === 'active';
    if (!active) {
      state.alerts.delete(iid);
      head.replaceChildren(h('div', { class: 'card good', 'data-test': 'incident-closed' },
        h('h2', null, 'הקריאה הסתיימה'),
        h('p', null, `האירוע של ${inc.owner_display} ${inc.status === 'resolved' ? 'הסתיים' : 'בוטל'}. מומלץ לוודא שהוא/היא בטוח/ה.`)));
      body.replaceChildren();
      return;
    }
    head.replaceChildren(h('div', { class: 'card alarm-head alarm', 'data-test': 'incident-alert' },
      h('h1', { 'data-test': 'alert-title' }, `🚨 ${inc.owner_display} צריך/ה עזרה`),
      h('p', null, `${TYPE_HE[inc.emergency_type] || TYPE_HE.unknown} · נפתחה ${clock(inc.created_at)} (${ago(inc.created_at)})`),
      h('p', null, inc.my_role === 'volunteer' ? 'את/ה מקבל/ת את זה כמתנדב/ת קרוב/ה' : 'את/ה איש קשר לחירום'), staleLine));
    const parts = [];
    if (inc.mode === 'silent') {
      parts.push(h('div', { class: 'card danger', 'data-test': 'silent-warning' },
        h('h2', null, '🤫 מצוקה שקטה - אל תתקשר/י!'),
        h('p', null, 'ייתכן שהוא/היא לא יכול/ה לדבר בבטחה, וצלצול עלול לסכן. השתמש/י בהודעות או פנה/י למשטרה (100).')));
    }
    if (inc.risk) parts.push(riskCard(inc.risk));   // מד הסכנה - השרת שולח אותו רק למגיבים
    const loc = inc.location;
    const where = h('div', { class: 'card' }, secHead('h3', 'pin', 'navy', 'איפה'));
    if (!loc) where.append(h('p', { class: 'error' }, 'אין עדיין מיקום לאירוע.'));
    else {
      if (loc.approximate) where.append(h('p', { class: 'error', 'data-test': 'approx-note' }, 'מיקום משוער בלבד (~500 מ\'). המיקום המדויק יוצג רק אחרי שתקבל/י את הקריאה.'));
      where.append(locLine(loc));
      const myPos = geo.last;
      if (inc.distance_km != null) where.append(h('p', { class: 'big' }, `מרחק: ~${Number(inc.distance_km).toFixed(1)} ק"מ`));
      if (inc.eta_minutes) where.append(h('p', { 'data-test': 'eta' }, `~${inc.eta_minutes} דק' הליכה ${inc.eta_source === 'osrm' ? '(לפי מסלול)' : '(הערכה)'}`));
      else if (myPos) where.append(h('p', { class: 'big' }, `מרחק ממך: ~${haversineKm(myPos, loc).toFixed(1)} ק"מ`));
      where.append(h('a', { class: 'btn secondary wide', href: mapsUrl(loc.lat, loc.lon), target: '_blank', rel: 'noopener', 'data-test': 'navigate' }, icon('route'), loc.approximate ? 'ניווט לאזור המשוער' : 'ניווט למקום'));
      if (IS_APPLE) where.append(h('p', { class: 'small' }, h('a', { href: googleMapsUrl(loc.lat, loc.lon), target: '_blank', rel: 'noopener', 'data-test': 'navigate-google' }, 'או לפתוח ב-Google Maps')));
    }
    parts.push(where);
    if (inc.medical) {
      parts.push(h('div', { class: 'card warn', 'data-test': 'medical' }, secHead('h3', 'medical', 'red', 'מידע רפואי ששותף'),
        h('dl', { class: 'kv' }, Object.entries(inc.medical).map(([k, v]) => [h('dt', null, MED_LABELS[k] || k), h('dd', null, v)]))));
    }
    const st = inc.my_state;
    const msg = h('p', { class: 'error' });
    const respond = async (action) => {
      msg.textContent = '';
      ringer.stop(iid);
      try {
        const r = await api('POST', `/incidents/${iid}/respond`, { action });
        if (action === 'decline') {
          state.alerts.delete(iid);
          head.replaceChildren(h('div', { class: 'card calm', 'data-test': 'declined' }, h('h2', null, 'סימנת שאינך זמין/ה'), h('p', null, 'השולח/ת יראה זאת. אם יש מתנדב נוסף בטווח - הקריאה תועבר אליו.')));
          body.replaceChildren(); chat.card.hidden = true;
          stopPoll();
          return;
        }
        myResp = action;
        inc = r; loadedAt = Date.now() / 1000; failed = null; draw();
      } catch (e) { msg.textContent = e.offline ? 'אין חיבור - התגובה לא נשלחה! נסה/י שוב.' : errText(e); }
    };
    const actions = h('div', { class: 'card' }, h('h3', null, 'התגובה שלך'));
    if (st === 'accepted') {
      actions.append(h('p', { class: 'okmsg', 'data-test': 'my-response' }, 'קיבלת את הקריאה ✓ - השולח/ת רואה שאת/ה בטיפול.'),
        myResp && myResp !== 'accept' ? h('p', { class: 'okmsg' }, `עדכנת: ${RESP_HE[myResp]}`) : null,
        h('div', { class: 'li-actions' },
          h('button', { class: 'secondary', 'data-test': 'resp-on_my_way', onclick: () => respond('on_my_way') }, '🚗 אני בדרך'),
          h('button', { class: 'green', 'data-test': 'resp-arrived', onclick: () => respond('arrived') }, '📍 הגעתי')));
    } else {
      actions.append(h('p', { class: 'muted' }, `מצב: ${STATE_HE[st] || st || ''}`),
        h('div', { class: 'li-actions' },
          h('button', { class: 'green', 'data-test': 'resp-accept', onclick: () => respond('accept') }, '✋ אני מגיע/ה - קבלה'),
          h('button', { class: 'ghost', 'data-test': 'resp-decline', onclick: () => respond('decline') }, 'לא יכול/ה')));
    }
    actions.append(msg);
    parts.push(actions);
    const resp = inc.responders || [];
    parts.push(h('div', { class: 'card' }, h('h3', null, 'מי כבר בטיפול'),
      resp.length ? h('ul', { class: 'list' }, resp.map((r) => h('li', { class: 'li-head' }, h('span', null, r.display_name), pill(r.role === 'volunteer' ? 'מתנדב/ת' : 'איש קשר')))) : h('p', { class: 'muted' }, 'עוד אף אחד לא קיבל.')));
    if (!inc.messages && inc.my_role === 'volunteer') parts.push(h('p', { class: 'muted small' }, 'הודעות ופרטים מלאים זמינים אחרי קבלת הקריאה.'));
    parts.push(h('div', { class: 'card' }, h('p', null, 'בסכנת חיים - חייג/י:'), telRow()));
    body.replaceChildren(...parts);
  };

  // ---- בעל/ת האירוע: "שמישהו יזעיק משטרה בשבילי" / "מישהו לדבר איתו" (main.py SosWindow)
  const requested = new Set();          // סוגים שביקשתי (גם מיומן האירוע בשרת - שורד רענון)
  let sendingKind = null;
  const helpersStatus = h('p', { class: 'muted pre-line', 'data-test': 'helpers-status', role: 'status', 'aria-live': 'polite' });
  const helpersList = h('ul', { class: 'list', 'data-test': 'helpers-list' });
  const helpersMsg = h('p', { class: 'error', 'data-test': 'helpers-error' });
  const ask = (kind) => async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true; sendingKind = kind; helpersMsg.textContent = '';
    drawOwnerHelpers();
    try {
      const r = await requestHelper(iid, kind, '');
      requested.add(kind);
      inc = r; loadedAt = Date.now() / 1000; failed = null;
      sendingKind = null;
      draw();
    } catch (e) {
      sendingKind = null;
      helpersMsg.textContent = (e.offline ? 'אין חיבור לשרת - הבקשה לא נשלחה!' : '❌ ' + errText(e)) + ' בסכנה מיידית: 100 / 101';
      drawOwnerHelpers();
    } finally {
      btn.disabled = false;
    }
  };
  helpersCard.append(secHead('h3', 'hand', 'indigo', 'עוד סוגי עזרה'),
    h('p', { class: 'muted small' }, 'מתנדב/ת מאומת/ת יכול/ה להזעיק משטרה / מד"א בשבילך כשאי אפשר לדבר בטלפון, או מתנדב/ת לשיחה - בלי לראות את שמך ואת מיקומך.'),
    h('button', { class: 'wide', 'data-test': 'req-liaison', onclick: ask('authority_liaison') }, '👮 שמישהו יזעיק משטרה בשבילי'),
    h('div', { class: 'spacer' }),
    h('button', { class: 'secondary wide', 'data-test': 'req-support', onclick: ask('remote_support') }, '💬 מישהו לדבר איתו עכשיו'),
    helpersStatus, helpersList, helpersMsg);
  const drawOwnerHelpers = () => {
    helpersCard.className = 'card' + (inc && inc.mode === 'silent' ? ' calm plain' : '');
    for (const t of (inc && inc.timeline) || []) {
      if (t.kind !== 'helper_requested') continue;
      for (const [k, label] of Object.entries(HELPER_KIND_HE)) if (String(t.text || '').includes(label)) requested.add(k);
    }
    const hs = (inc && inc.helpers) || [];
    const sum = helpersSummary(hs, requested);
    if (sendingKind) {
      helpersStatus.className = 'muted pre-line';
      helpersStatus.textContent = `⏳ שולח בקשה ל${HELPER_KIND_HE[sendingKind]}...` + (sum.text ? '\n' + sum.text : '');
    } else {
      helpersStatus.className = sum.cls + ' pre-line';
      helpersStatus.textContent = sum.text;
    }
    if (hasOpenConfirm(helpersList)) return;          // אל תמחק/י אישור "לשחרר?" פתוח ברענון
    helpersList.replaceChildren(...hs.filter((x) => x.state === 'notified' || x.state === 'accepted').map((x) => {
      const who = x.kind === 'authority_liaison' && x.state === 'accepted' ? x.display_name : 'מתנדב/ת';
      const holder = h('div', { class: 'li-actions' });
      holder.append(h('button', { class: 'ghost', 'data-test': 'helper-release', onclick: () => confirmIn(holder,
        x.state === 'accepted' ? `לשחרר את ${who} מ${x.kind_label}? הגישה לבקשה תיסגר.` : `לבטל את ההצעה ל${x.kind_label}?`,
        'כן, שחרור', 'red', async () => { inc = await releaseHelper(iid, x.username); loadedAt = Date.now() / 1000; failed = null; helpersList.replaceChildren(); draw(); }) },
      x.state === 'accepted' ? 'שחרור' : 'ביטול ההצעה'));
      return h('li', { 'data-test': 'owner-helper', 'data-user': x.username, 'data-state': x.state, 'data-kind': x.kind },
        h('div', { class: 'li-head' }, h('strong', null, `${x.kind_label}: ${who}`),
          x.state === 'accepted' ? pill('איתך', 'ok') : pill('נשלח - ממתין לתשובה', 'wait')),
        x.last_action_label ? h('p', { class: 'small' }, x.last_action_label) : null,
        holder);
    }));
  };

  // ---- מתנדב/ת תמיכה מרחוק / הזעקת רשויות (alert_window.py _apply_helper)
  const liaisonFeedback = h('p', { class: 'okmsg', 'data-test': 'liaison-feedback' });
  const helperFinished = (action) => {
    state.alerts.delete(iid);
    ringer.stop(iid);
    stopPoll();
    head.replaceChildren(h('div', { class: 'card calm', 'data-test': 'helper-finished' },
      h('h2', null, action === 'decline' ? 'סימנת שאינך זמין/ה' : 'סיימת - תודה!'),
      h('p', null, action === 'decline' ? 'הבקשה תועבר למתנדב/ת אחר/ת אם יש.' : 'הפונה רואה שסיימת את ההשתתפות.'),
      h('button', { class: 'wide', onclick: () => { location.hash = '#community'; } }, 'חזרה')));
    body.replaceChildren(); actionsSlot.replaceChildren(); actionsSlot.dataset.key = '';
    chat.card.hidden = true;
  };
  const helperAct = async (action, msgEl, btn) => {
    msgEl.textContent = '';
    if (btn) btn.disabled = true;
    ringer.stop(iid);
    try {
      const r = await helperRespond(iid, action);
      if (action === 'decline' || action === 'done') { helperFinished(action); return; }
      if (inc.liaison_actions && inc.liaison_actions[action]) {
        liaisonFeedback.textContent = `✓ נשמר בשרת: ${inc.liaison_actions[action]} (${clock(serverNow())}). הפונה ואנשי הקשר רואים את זה.`;
      }
      inc = r; loadedAt = Date.now() / 1000; failed = null;
      draw();
    } catch (e) {
      msgEl.textContent = e.offline ? 'אין חיבור - הפעולה לא נשלחה! נסה/י שוב.' : errText(e);
      if (e.status === 404 || e.status === 409) load();
    } finally {
      if (btn) btn.disabled = false;
    }
  };
  const drawHelper = () => {
    const liaison = inc.helper_kind === 'authority_liaison';
    const accepted = inc.my_state === 'accepted';
    if (inc.status !== 'active') {
      state.alerts.delete(iid);
      head.replaceChildren(h('div', { class: 'card good', 'data-test': 'incident-closed' }, h('h2', null, 'הבקשה הסתיימה')));
      body.replaceChildren(); actionsSlot.replaceChildren(); actionsSlot.dataset.key = '';
      return;
    }
    state.alerts.set(iid, inc);
    if (accepted) ringer.stop(iid);
    const title = liaison ? '👮 בקשה להזעיק רשויות' : (inc.support_request ? '💬 מישהו צריך לדבר' : '💬 מישהו במצוקה צריך שיחה');
    head.replaceChildren(h('div', { class: liaison ? 'card alarm-head alarm' : 'card calm', 'data-test': 'helper-head' },
      h('h1', { 'data-test': 'helper-title' }, title),
      h('p', null, `את/ה: ${inc.helper_kind_label || ''} · ${inc.support_request ? 'לא חירום - שיחה בלבד' : 'בתוך אירוע חירום'} · נפתח ${clock(inc.created_at)} (${ago(inc.created_at)})`),
      staleLine));
    const parts = [];
    if (liaison && inc.mode === 'silent') {
      parts.push(h('div', { class: 'card danger', 'data-test': 'silent-warning' },
        h('h2', null, '🤫 מצוקה שקטה - אל תתקשר/י אל הפונה!'),
        h('p', null, 'ייתכן שהוא/היא לא יכול/ה לדבר בבטחה. התקשר/י לרשויות ומסור/י שהפונה לא יכול/ה לדבר.')));
    }
    if ((inc.guidance || []).length) {
      parts.push(h('div', { class: 'card', 'data-test': 'helper-guidance' }, h('h3', null, 'הנחיות'),
        h('ul', null, inc.guidance.map((g) => h('li', null, g)))));
    }
    const ident = h('div', { class: 'card', 'data-test': 'helper-identity' });
    if (!liaison) {
      ident.append(h('p', { 'data-test': 'helper-privacy' }, '🔒 לשמירה על הפרטיות לא מוצגים שם ומיקום של הפונה'));
      const hot = (inc.hotlines || []).filter((x) => x.number);
      if (hot.length) ident.append(h('p', { class: 'muted small' }, 'קווי סיוע שאפשר להפנות אליהם: ', hot.map((x) => `${x.name} ${x.number}`).join(' · ')));
    } else if (!accepted) {
      ident.append(h('p', { class: 'muted' }, '📍 השם והמיקום יוצגו רק אחרי שתקבל/י את הבקשה'));
    } else {
      ident.append(h('h3', null, 'פרטים למסירה לרשויות'),
        h('dl', { class: 'kv' }, h('dt', null, 'שם'), h('dd', { 'data-test': 'helper-owner-name' }, inc.owner_display || '')));
      const loc = inc.location;
      if (!loc) {
        ident.append(h('p', { class: 'error', 'data-test': 'helper-no-location' }, 'לשרת אין מיקום לאירוע - מסור/י לרשויות את מה שידוע, ושאל/י בצ\'אט אם אפשר.'));
      } else {
        if (loc.stale) ident.append(h('p', { class: 'error', 'data-test': 'helper-loc-stale' }, '⚠️ המיקום לא התעדכן מעל 5 דקות - זה המיקום האחרון הידוע, לא מיקום חי'));
        ident.append(h('div', { 'data-test': 'helper-location' }, locLine(loc)),
          h('a', { class: 'btn secondary wide', href: mapsUrl(loc.lat, loc.lon), target: '_blank', rel: 'noopener' }, loc.stale ? 'פתיחה במפה (מיקום אחרון ידוע)' : 'פתיחה במפה'));
      }
    }
    parts.push(ident);
    if (!liaison) parts.push(h('div', { class: 'card' }, h('p', { class: 'muted small' }, 'בסכנה מיידית - הציעו לפנות ל-100 / 101:'), telRow()));
    body.replaceChildren(...parts);
    const key = `helper:${inc.helper_kind}:${inc.my_state}`;
    if (actionsSlot.dataset.key === key) return;
    actionsSlot.dataset.key = key;
    const msg = h('p', { class: 'error' });
    const card = h('div', { class: 'card', 'data-test': 'helper-actions' }, h('h3', null, 'התגובה שלך'));
    if (!accepted) {
      card.append(h('p', { class: 'muted' }, 'הצ\'אט ייפתח אחרי שתקבל/י את הבקשה.'),
        h('div', { class: 'li-actions' },
          h('button', { class: 'green', 'data-test': 'helper-accept', onclick: (ev) => helperAct('accept', msg, ev.currentTarget) }, '✋ אני לוקח/ת את הבקשה'),
          h('button', { class: 'ghost', 'data-test': 'helper-decline', onclick: (ev) => helperAct('decline', msg, ev.currentTarget) }, 'לא יכול/ה')));
    } else {
      card.append(h('p', { class: 'okmsg', 'data-test': 'helper-accepted' }, liaison ? '✅ הבקשה אצלך - עדכן/י כאן מה עשית.' : '✅ הבקשה אצלך - אפשר לכתוב בצ\'אט למטה.'));
      if (liaison) {
        card.append(h('div', { class: 'stack' }, Object.entries(inc.liaison_actions || {}).map(([a, label]) =>
          h('button', { class: a.startsWith('called') ? 'wide' : 'secondary wide', 'data-test': 'liaison-' + a, onclick: (ev) => helperAct(a, msg, ev.currentTarget) }, label))),
        liaisonFeedback);
      }
      const doneHolder = h('div', null, h('div', { class: 'spacer' }), h('button', { class: 'ghost wide', 'data-test': 'helper-done', onclick: () => confirmIn(doneHolder,
        'לסיים את ההשתתפות שלך בבקשה?', 'כן, סיימתי', 'green', async () => { await helperRespond(iid, 'done'); helperFinished('done'); }) }, 'סיימתי'));
      card.append(doneHolder);
    }
    card.append(msg);
    actionsSlot.replaceChildren(card);
  };

  let stopped = false;
  const stopPoll = () => { stopped = true; };
  onCleanup(stopPoll);
  const load = async () => {
    if (stopped) return;
    try {
      inc = await api('GET', `/incidents/${iid}`);
      loadedAt = Date.now() / 1000; failed = null;
      if (inc.my_role !== 'owner' && inc.my_role !== 'helper' && inc.status === 'active') geoUse('responder');
      draw();
    } catch (e) {
      if (e.status === 404) {
        state.alerts.delete(iid);
        if (myIncident() && myIncident().id === iid) setMyIncident(null);
        head.replaceChildren(h('div', { class: 'card calm', 'data-test': 'incident-gone' }, h('h2', null, 'הקריאה לא זמינה'),
          h('p', null, 'האירוע הסתיים, הועבר למתנדב/ת אחר/ת, או שהגישה אליו בוטלה.'),
          h('button', { class: 'wide', onclick: () => { location.hash = '#home'; } }, 'חזרה לבית')));
        body.replaceChildren(); chat.card.hidden = true;
        stopPoll();
        return;
      }
      failed = e.offline ? 'אין חיבור לשרת' : errText(e);
      if (inc) draw(); else head.replaceChildren(h('div', { class: 'card warn' }, h('p', null, `טוען... (${failed})`)));
    }
  };
  onCleanup(() => geoRelease('responder'));
  head.append(h('div', { class: 'card' }, h('p', null, 'טוען את הקריאה...')));
  load();
  every(3000, load);
  listen('incident:' + iid, load);
  listen('track', () => { if (inc && inc.my_role === 'owner') draw(); });
  listen('wake', () => { if (inc && inc.my_role === 'owner') draw(); });
  listen('geo', () => { if (inc && inc.my_role !== 'owner' && !inc.distance_km) draw(); });
}

// ------------------------------------------------------------------ MAP ("סביבה"): environment around me + walking routes
// Street-level environment factors (server/heatmap.py) - never a "dangerous neighbourhood" score, never red zones -
// and the three walking-route modes from POST /v1/route-safety (server/safety_intel.py choose_modes).
const ENV_LAYERS = [
  ['lighting', 'תאורה', 'bulb'],
  ['accidents', 'תאונות', 'crossing'],
  ['nightlife', 'בילוי', 'moon'],
  ['reports', 'דיווחים', 'flag'],
];
const ENV_KIND_HE = { official: 'נתון רשמי', 'official+estimate': 'רשמי + הערכה', estimate: 'הערכה', user_report: 'דיווחי משתמשים' };
const ENV_ENDS = {
  lighting: ['אין עמודים רשומים', '6+ עמודים רשומים'],
  accidents: ['מעט תאונות (≠ בטוח)', 'הכי הרבה תאונות (2.5% מהשטח)'],
  nightlife: ['לא אזור בילוי', 'אזור בילוי פעיל'],
  reports: ['דיווח מאומת אחד', 'כמה דיווחים'],
};
const ROUTE_MODES = [['fastest', 'המהיר'], ['balanced', 'מאוזן'], ['safest', 'בטוח יותר']];
const ROUTE_LEVEL_HE = { low: 'נמוך', medium: 'בינוני', high: 'גבוה' };
const RELIAB_HE = { high: 'גבוהה', partial: 'חלקית', low: 'נמוכה' };

function fmtUpdated(v) {
  if (v == null || v === '') return '';
  if (typeof v === 'number') return new Date(v * 1000).toLocaleDateString('he-IL');
  return String(v).slice(0, 10);
}

/** "32.08, 34.78" or a maps link with coordinates (…@lat,lon / ?q=lat,lon / ll=lat,lon) -> {lat, lon} or null */
function parseLatLon(text) {
  const m = String(text || '').match(/(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)/);
  if (!m) return null;
  const lat = Number(m[1]), lon = Number(m[2]);
  return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { lat, lon } : null;
}

function viewMap(main) {
  let env = null, envErr = null, layer = store.get('lg.mapLayer') || 'lighting';
  if (!ENV_LAYERS.some(([k]) => k === layer)) layer = 'lighting';
  let dest = null, routeRes = null, mode = 'fastest', centered = false, envSeq = 0;
  const map = new EnvMap({ onTap: (pt) => onTap(pt), onViewChange: (bb) => loadEnv(bb) });
  map.layer = layer;
  onCleanup(() => map.destroy());
  geoUse('map');
  onCleanup(() => geoRelease('map'));

  // ---- map chrome: zoom, locate
  const zin = h('button', { type: 'button', class: 'mapbtn', 'aria-label': 'התקרבות', 'data-test': 'map-zoom-in' }, icon('plus'));
  const zout = h('button', { type: 'button', class: 'mapbtn', 'aria-label': 'התרחקות', 'data-test': 'map-zoom-out' }, icon('minus'));
  const syncZoom = () => { zin.disabled = !map.canZoom(-1); zout.disabled = !map.canZoom(1); };
  zin.addEventListener('click', () => { map.zoomBy(-1); syncZoom(); });
  zout.addEventListener('click', () => { map.zoomBy(1); syncZoom(); });
  const locate = h('button', { type: 'button', class: 'map-float mapbtn', 'aria-label': 'מרכוז על המיקום שלי', 'data-test': 'map-locate' }, icon('locate'));
  locate.addEventListener('click', () => {
    if (geo.last) { map.setCenter(geo.last.lat, geo.last.lon); loadEnv(map.fetchBbox()); } else map.status.textContent = geo.error || 'ממתין למיקום מהמכשיר...';
  });
  map.frame.append(h('div', { class: 'map-ctrls' }, zin, zout), locate);
  syncZoom();

  // ---- layer picker + legend
  const layerSeg = h('div', { class: 'seg layer-seg', role: 'group', 'aria-label': 'שכבת סביבה', 'data-test': 'env-layers' });
  const legend = h('div', { class: 'legend', 'data-test': 'env-legend', 'aria-live': 'polite' });
  const here = h('div', { class: 'env-here', 'data-test': 'env-here' });
  const hereTitle = h('p', { class: 'small muted' });
  const drawLayers = () => {
    layerSeg.replaceChildren(...ENV_LAYERS.map(([k, label, ico]) => {
      const L = env && env.layers && env.layers[k];
      const b = h('button', { type: 'button', 'aria-pressed': String(k === layer), 'data-test': 'env-layer-' + k, 'data-available': L ? String(!!L.available) : '' }, icon(ico), label);
      b.addEventListener('click', () => { layer = k; map.layer = k; store.set('lg.mapLayer', k); drawLayers(); drawLegend(); map.draw(); });
      return b;
    }));
  };
  const drawLegend = () => {
    const kids = [];
    if (envErr) kids.push(h('p', { class: 'error small', 'data-test': 'env-error' }, envErr));
    else if (!env) kids.push(h('p', { class: 'muted small' }, 'טוען את שכבות הסביבה...'));
    else {
      const L = (env.layers || {})[layer] || {};
      if (!L.available) {
        kids.push(h('p', { class: 'small', 'data-test': 'env-unavailable' }, h('span', { class: 'swatch nodata', 'aria-hidden': 'true' }),
          L.note && layer === 'nightlife' ? L.note : 'אין נתונים לשכבה הזו באזור המוצג.'));
      } else {
        const [lo, hi] = layer === 'lighting' ? [`${lightCap(env)}+ עמודים רשומים`, 'אין עמודים רשומים'] : ENV_ENDS[layer];
        kids.push(h('div', { class: 'legend-bar', 'aria-hidden': 'true' }), h('div', { class: 'legend-ends' }, h('span', null, lo), h('span', null, hi)));
      }
      kids.push(h('p', { class: 'legend-src', 'data-test': 'env-source' }, h('strong', null, L.label || ''),
        L.source ? ` · מקור: ${L.source}` : '', L.kind ? ` · ${ENV_KIND_HE[L.kind] || L.kind}` : '',
        fmtUpdated(L.updated) ? ` · עודכן ${fmtUpdated(L.updated)}` : ''));
      if (L.note && !(layer === 'nightlife' && !L.available)) kids.push(h('p', { class: 'legend-src' }, L.note));
      if (L.attribution) kids.push(h('p', { class: 'legend-src' }, L.attribution));
      kids.push(h('p', { class: 'legend-src' }, h('span', { class: 'swatch nodata', 'aria-hidden': 'true' }), 'תא ריק = אין נתונים, לא "בטוח".'));
      if (env.disclaimer) kids.push(h('p', { class: 'fineprint', 'data-test': 'env-disclaimer' }, env.disclaimer));
    }
    legend.replaceChildren(...kids);
    drawHere();
  };
  const drawHere = () => {
    if (!env || !(env.cells || []).length) { here.replaceChildren(); hereTitle.textContent = ''; return; }
    const ref = geo.last || map.center;
    let best = null, bd = Infinity;
    for (const c of env.cells) { const d = (c.lat - ref.lat) ** 2 + (c.lon - ref.lon) ** 2; if (d < bd) { bd = d; best = c; } }
    if (!best || bd > (env.cell_deg || 0.001) ** 2 * 2) { here.replaceChildren(); hereTitle.textContent = ''; return; }
    hereTitle.textContent = geo.last ? 'סביב המיקום שלך (תא של כ-100 מ\'):' : 'סביב מרכז המפה (תא של כ-100 מ\'):';
    const stat = (k, v) => h('div', { class: 'env-stat' }, h('div', { class: 'v' }, v), h('div', { class: 'k' }, k));
    const L = env.layers || {};
    here.replaceChildren(
      stat('עמודי תאורה רשומים', best.lighting == null ? '—' : String(best.lighting)),
      stat('תאונות הולכי רגל (5 שנים)', best.accidents == null ? '—' : String(best.accidents) + (best.accident_hotspot ? ' · מוקד' : '')),
      stat('אזור בילוי (לילה)', best.nightlife == null ? (L.nightlife && L.nightlife.note ? 'ביום לא מוצג' : '—') : (best.nightlife ? 'כן' : 'לא')),
      stat('דיווחים מאומתים', String(best.reports || 0)));
  };
  const loadEnv = async (bb) => {
    const seq = ++envSeq;
    map.status.textContent = 'טוען שכבת סביבה...';
    try {
      const r = await api('GET', '/map/environment?bbox=' + bb.join(','));
      if (seq !== envSeq) return;
      env = r; envErr = null;
      map.env = r;
      map.status.textContent = '';
    } catch (e) {
      if (seq !== envSeq) return;
      envErr = e.offline ? 'אין חיבור לשרת - שכבת הסביבה לא נטענה.' : errText(e);
      map.status.textContent = 'שכבת הסביבה לא זמינה';
    }
    map.canvas.setAttribute('aria-label', env ? `מפת סביבה: ${(env.cells || []).length} תאים, שכבה: ${(ENV_LAYERS.find(([k]) => k === layer) || [])[1]}` : 'מפת סביבה');
    drawLayers(); drawLegend(); map.draw();
  };

  // ---- route planner
  const fromTxt = h('div', { class: 'txt' }, 'המיקום שלי', h('small', { 'data-test': 'route-from-state' }, ''));
  const toTxt = h('div', { class: 'txt', 'data-test': 'route-to' }, 'יעד', h('small', null, 'לא נבחר עדיין'));
  const pickBtn = h('button', { type: 'button', class: 'secondary wide', 'data-test': 'route-pick' }, icon('pin'), 'בחירת יעד במפה');
  const coords = h('input', { type: 'text', id: 'route-coords', dir: 'ltr', inputmode: 'decimal', autocomplete: 'off', spellcheck: 'false', placeholder: '32.0853, 34.7818', 'data-test': 'route-coords' });
  const coordsMsg = h('p', { class: 'help' }, 'אפשר להדביק קואורדינטות או קישור ממפות Apple / Google שיש בו קואורדינטות.');
  const compare = h('button', { type: 'button', class: 'wide', 'data-test': 'route-compare', disabled: true }, icon('route'), 'השוואת מסלולים');
  const result = h('div', { 'data-test': 'route-result', 'aria-live': 'polite' });
  const origin = () => (geo.last ? { lat: geo.last.lat, lon: geo.last.lon, mine: true } : null);
  const drawEnds = () => {
    const o = origin();
    fromTxt.lastChild.textContent = o ? `דיוק ${geo.last.accuracy_m} מ' · ${ago(geo.last.captured_at)}` : (geo.error || 'ממתין למיקום מהמכשיר...');
    fromTxt.lastChild.className = o ? '' : (geo.error ? 'error' : '');
    toTxt.lastChild.textContent = dest ? `${dest.lat.toFixed(5)}, ${dest.lon.toFixed(5)}` : 'לא נבחר עדיין';
    compare.disabled = !(o && dest);
  };
  const setDest = (pt) => {
    dest = { lat: pt.lat, lon: pt.lon };
    map.dest = dest; map.picking = false;
    pickBtn.setAttribute('aria-pressed', 'false');
    routeRes = null; map.routes = []; result.replaceChildren(); map.attr.textContent = '';
    map.status.textContent = '';
    drawEnds(); map.draw();
  };
  function onTap(pt) { if (map.picking) setDest(pt); }
  pickBtn.addEventListener('click', () => {
    map.picking = !map.picking;
    pickBtn.setAttribute('aria-pressed', String(map.picking));
    map.status.textContent = map.picking ? 'הקישו על המפה כדי לבחור יעד' : '';
    map.draw();
    if (map.picking) map.frame.scrollIntoView({ block: 'center', behavior: 'auto' });
  });
  coords.addEventListener('change', () => {
    const pt = parseLatLon(coords.value);
    if (!pt) { coordsMsg.className = 'help error'; coordsMsg.textContent = 'לא נמצאו קואורדינטות (למשל 32.0853, 34.7818).'; return; }
    coordsMsg.className = 'help'; coordsMsg.textContent = 'היעד הוצב במפה.';
    setDest(pt);
    map.fit([[pt.lat, pt.lon], ...(geo.last ? [[geo.last.lat, geo.last.lon]] : [])]); syncZoom();
    loadEnv(map.fetchBbox());
  });

  const drawRoute = () => {
    const r = routeRes;
    if (!r) return;
    const modes = r.modes || {};
    const sel = modes[mode] || modes.fastest;
    const selIdx = sel ? sel.index : 0;
    map.routes = (r.routes || []).filter((x) => Array.isArray(x.geometry)).map((x) => ({ points: x.geometry, selected: x.index === selIdx }));
    map.attr.textContent = r.routing_attribution || '';
    map.draw();
    const group = h('div', { class: 'modes', role: 'radiogroup', 'aria-label': 'סוג מסלול', 'data-test': 'route-modes' }, ROUTE_MODES.map(([k, label]) => {
      const m = modes[k];
      if (!m) return null;
      const b = h('button', { type: 'button', class: 'mode-card', role: 'radio', 'aria-checked': String(k === mode), 'data-test': 'route-mode-' + k },
        h('span', { class: 'm-name' }, label),
        h('span', { class: 'm-min' }, String(m.minutes), h('small', null, ' דק\'')),
        h('span', { class: 'm-risk lvl-' + m.level }, `סיכון ${ROUTE_LEVEL_HE[m.level] || m.level}`));
      b.addEventListener('click', () => { mode = k; drawRoute(); });
      return b;
    }));
    const route = (r.routes || []).find((x) => x.index === selIdx) || {};
    const reasons = [...new Set((route.segments || []).flatMap((sg) => sg.reasons || []))].slice(0, 4);
    const wx = r.weather && r.weather.status === 'ok' ? r.weather : null;
    const sameAsFastest = mode !== 'fastest' && modes.fastest && sel && sel.index === modes.fastest.index;
    result.replaceChildren(...[
      group,
      sel ? h('p', { class: 'big', 'data-test': 'route-summary' }, `${sel.minutes} דק' · ${(sel.length_m / 1000).toFixed(1)} ק"מ`,
        sel.extra_minutes > 0 ? h('span', { class: 'muted small' }, ` · +${sel.extra_minutes} דק' מהמהיר`) : null) : null,
      sel ? h('p', { 'data-test': 'route-why' }, sel.text) : null,
      sameAsFastest ? h('p', { class: 'small muted' }, 'זה אותו מסלול כמו המהיר.') : null,
      sel && sel.reliability ? h('p', { class: 'small muted' }, `מהימנות הנתונים במסלול: ${RELIAB_HE[sel.reliability] || sel.reliability}`) : null,
      reasons.length ? h('div', null, h('p', { class: 'small', 'data-test': 'route-reasons-title' }, 'מה נלקח בחשבון במסלול הזה:'), h('ul', { class: 'why' }, reasons.map((x) => h('li', null, x))))
        : h('p', { class: 'small muted' }, 'לא נמצאו במסלול הזה גורמי סביבה מסומנים לפי הנתונים.'),
      (r.caveats || []).length ? h('div', { 'data-test': 'route-caveats' }, (r.caveats || []).map((c) => h('p', { class: 'small', }, h('span', { class: 'pill wait' }, 'הסתייגות'), ' ', c))) : null,
      wx ? h('p', { class: 'small', 'data-test': 'route-weather' }, `${wx.label || 'מזג אוויר'}: ${wx.text}`) : null,
      h('a', { class: 'btn secondary wide', href: mapsUrl(dest.lat, dest.lon), target: '_blank', rel: 'noopener', 'data-test': 'route-open-maps' }, icon('route'), IS_APPLE ? 'ניווט במפות Apple' : 'ניווט ב-Google Maps'),
      h('p', { class: 'fineprint', 'data-test': 'route-disclaimer' }, r.disclaimer || ''),
      h('p', { class: 'fineprint', 'data-test': 'route-attribution' }, [r.routing_attribution, r.attribution, wx && wx.attribution].filter(Boolean).join(' · ')),
    ].filter(Boolean));
  };
  compare.addEventListener('click', async () => {
    const o = origin();
    if (!o || !dest) return;
    compare.disabled = true;
    result.replaceChildren(h('p', { class: 'muted', role: 'status' }, 'מחשב מסלולים ובודק את גורמי הסביבה...'));
    try {
      routeRes = await api('POST', '/route-safety', { from: { lat: o.lat, lon: o.lon }, to: dest }, { timeout: 30000 });
      mode = 'fastest';
      const pts = (routeRes.routes || []).flatMap((x) => x.geometry || []);
      if (pts.length) { map.fit(pts); syncZoom(); loadEnv(map.fetchBbox()); }
      drawRoute();
    } catch (e) {
      result.replaceChildren(h('p', { class: 'error', 'data-test': 'route-error' }, e.offline ? 'אין חיבור לשרת - המסלולים לא נבדקו.' : errText(e)));
    } finally {
      compare.disabled = !(origin() && dest);
    }
  });

  main.append(
    h('h1', null, 'סביבה'),
    h('p', { class: 'page-sub' }, 'גורמי סביבה ברמת רחוב ומסלולי הליכה. לא ציון סכנה לשכונה.'),
    h('div', { class: 'card map-card', 'data-test': 'env-map-card' }, map.frame,
      h('div', { class: 'map-body' }, layerSeg, legend, hereTitle, here)),
    h('div', { class: 'card', 'data-test': 'route-card' }, secHead('h2', 'route', 'navy', 'מסלול הליכה'),
      h('div', { class: 'route-ends' },
        h('div', { class: 'route-end' }, h('span', { class: 'dot', 'aria-hidden': 'true' }), fromTxt),
        h('div', { class: 'route-end' }, h('span', { class: 'dot dest', 'aria-hidden': 'true' }), toTxt)),
      pickBtn, h('label', { for: 'route-coords' }, 'או יעד לפי קואורדינטות'), coords, coordsMsg,
      h('div', { class: 'spacer' }), compare, result));

  // first view: my position when it arrives (else the last known area), then follow the dot without jumping
  const onGeo = () => {
    if (geo.last) {
      map.me = geo.last;
      if (!centered) { centered = true; map.setCenter(geo.last.lat, geo.last.lon); loadEnv(map.fetchBbox()); }
    }
    drawEnds(); drawHere(); map.draw();
  };
  listen('geo', onGeo);
  drawLayers(); drawLegend(); drawEnds();
  requestAnimationFrame(() => {
    if (geo.last) onGeo();
    else {
      map.draw();
      // no fix yet: show the area anyway after a short wait (desktop without GPS), and keep waiting for the dot
      geoFix(2500).then(() => {
        if (centered) return;
        centered = true;
        if (geo.last) { onGeo(); return; }
        loadEnv(map.fetchBbox()).then(() => { if (!geo.last) map.status.textContent = 'אין מיקום מהמכשיר - מוצג אזור ברירת מחדל'; });
      });
    }
  });
}

// ------------------------------------------------------------------ SUPPORT ("צריך/ה לדבר עם מישהו?" - לא חירום)
// support_ui.py SupportWindow: בלי מיקום, בלי אנשי קשר, בלי מתנדבים בשטח. הסטטוס רק לפי השרת.
function viewSupport(main) {
  const status = h('p', { class: 'muted pre-line', 'data-test': 'support-status', role: 'status', 'aria-live': 'polite' });
  const stale = h('p', { class: 'error', hidden: true, 'data-test': 'support-stale' });
  const resumed = h('p', { class: 'muted small', hidden: true, 'data-test': 'support-resumed' }, 'חזרת לשיחה הפתוחה שלך (יש רק בקשה פתוחה אחת בכל פעם).');
  const note = h('textarea', { id: 'support-note', maxlength: '500', 'data-test': 'support-note' });
  const lang = h('select', { id: 'support-lang', 'data-test': 'support-lang' }, VOL_LANGS.map(([k, v]) => h('option', { value: k }, v)));
  lang.value = 'he';
  const start = h('button', { class: 'wide', 'data-test': 'support-start' }, icon('chat'), 'חפש/י מתנדב/ת לשיחה');
  const startForm = h('div', null,
    h('label', { for: 'support-note' }, 'מה עובר עליך? (לא חובה - אפשר גם להתחיל בלי לכתוב)'), note,
    h('label', { for: 'support-lang' }, 'שפת השיחה'), lang,
    h('div', { class: 'spacer' }), start);
  const endHolder = h('div', { hidden: true });
  const chatHolder = h('div');
  const hotSlot = h('div');
  hotSlot.append(hotlinesCard(HOTLINES, HOTLINES_NOTE));       // מקומי - מוצג גם בלי שרת
  main.append(
    h('div', { class: 'card calm', 'data-test': 'support-head' }, secHead('h1', 'chat', 'indigo', 'מישהו לדבר איתו'),
      h('p', null, 'לא חירום · אנונימי למתנדב/ת · אפשר לסגור בכל רגע')),
    h('div', { class: 'card' }, startForm, status, resumed, stale, endHolder),
    chatHolder, hotSlot);

  let iid = null, view = null, chat = null, endKey = '', serverHotlines = false;
  const listened = new Set();
  const useIncident = (id) => {
    if (id === iid && chat) return;
    iid = id;
    chat = makeChat(id, () => load(), { quick: false, title: 'השיחה' });
    chatHolder.replaceChildren(chat.card);
    if (!listened.has(id)) { listened.add(id); listen('incident:' + id, () => { if (iid === id) load(); }); }
  };
  const apply = (v) => {
    view = v;
    useIncident(v.id);
    const active = v.status === 'active';
    if (active) setMySupport(v.id); else if (mySupport() && mySupport().id === v.id) setMySupport(null);
    const s = supportStatus(v);
    status.className = s.cls + ' pre-line'; status.textContent = s.text;
    stale.hidden = true;
    chat.render(v, { roleHe: { helper: 'מתנדב/ת תמיכה', volunteer: 'מתנדב/ת', trusted: 'איש קשר' } });
    startForm.hidden = active;
    start.disabled = false;
    if (v.hotlines && v.hotlines.length && !serverHotlines) {
      serverHotlines = true;
      hotSlot.replaceChildren(hotlinesCard(v.hotlines, v.hotlines_note));
    }
    const key = active ? 'active:' + v.id : 'closed';
    if (key !== endKey && !hasOpenConfirm(endHolder)) {
      endKey = key;
      endHolder.hidden = !active;
      endHolder.replaceChildren();
      if (active) {
        endHolder.append(h('div', { class: 'spacer' }), h('button', { class: 'ghost wide', 'data-test': 'support-end', onclick: () => confirmIn(endHolder, 'לסיים את השיחה?', 'כן, סיום', 'green', async () => {
          const r = await api('PATCH', `/incidents/${v.id}`, { status: 'resolved' });
          endKey = '';
          apply(r);
        }) }, 'סיום השיחה'));
      }
    }
  };
  const load = async () => {
    if (!iid) return;
    try {
      apply(await api('GET', `/incidents/${iid}`));
    } catch (e) {
      if (e.status === 404) {
        if (mySupport() && mySupport().id === iid) setMySupport(null);
        status.className = 'muted'; status.textContent = 'השיחה הקודמת כבר לא זמינה. אפשר לפתוח שיחה חדשה.';
        startForm.hidden = false; endHolder.hidden = true; endKey = '';
        iid = null; view = null; chat = null; chatHolder.replaceChildren();
        return;
      }
      stale.hidden = false;
      stale.textContent = `⚠️ לא ניתן לרענן (${e.offline ? 'אין חיבור לשרת' : errText(e)}). המצב המוצג עלול להיות לא עדכני.`;
    }
  };
  start.addEventListener('click', async () => {
    start.disabled = true; resumed.hidden = true; stale.hidden = true;
    status.className = 'muted'; status.textContent = '⏳ שולח בקשה לשרת...';
    try {
      const r = await createSupportRequest(note.value.trim(), lang.value);
      note.value = '';
      apply(r);
      resumed.hidden = !r.resumed;
    } catch (e) {
      start.disabled = false;
      status.className = 'error';
      status.textContent = '❌ ' + (e.offline ? 'אין חיבור לשרת - הבקשה לא נשלחה. קווי הסיוע למטה זמינים תמיד.' : errText(e));
    }
  });
  const open = mySupport();
  if (open) {
    startForm.hidden = true;
    status.textContent = 'טוען את השיחה הפתוחה...';
    useIncident(open.id);
    load();
  }
  every(4000, async () => { if (iid && (!view || view.status === 'active')) await load(); });
}

// ------------------------------------------------------------------ ESCORT (watcher deep link)
function viewEscort(main, eid) {
  const slot = h('div');
  main.append(slot);
  const load = async () => {
    try {
      const e = await api('GET', `/escorts/${eid}`);
      e._deadlineLocal = Date.now() / 1000 + (e.remaining_seconds || 0);
      if (state.user && e.owner === state.user.username) { location.hash = '#home'; return; }
      const done = e.status !== 'active';
      slot.replaceChildren(done
        ? h('div', { class: e.status === 'arrived' ? 'card good' : (e.status === 'escalated' ? 'card danger' : 'card') },
          h('h2', null, `ליווי של ${e.owner_display}`),
          h('p', { class: 'big' }, { arrived: '✅ הגיע/ה בשלום', cancelled: 'הליווי בוטל', escalated: '🚨 לא אושרה הגעה - נפתחה קריאת חירום' }[e.status] || e.status),
          e.incident_id ? h('button', { class: 'red wide', onclick: () => { location.hash = '#incident/' + e.incident_id; } }, 'לקריאה') : null)
        : watchingCard([e]));
    } catch (err) {
      slot.replaceChildren(h('div', { class: 'card warn' }, h('p', null, err.status === 404 ? 'הליווי לא נמצא או שאין לך גישה אליו.' : `לא ניתן לטעון (${errText(err)}).`)));
    }
  };
  load();
  every(10000, load);
  listen('escorts', load);
}

// ------------------------------------------------------------------ COMMUNITY
function viewCommunity(main) {
  const alertsSlot = h('div');
  const watchSlot = h('div');
  const nearSlot = h('div', { class: 'card' });
  main.append(h('h1', null, 'קהילה'), h('p', { class: 'page-sub' }, 'קריאות אליך, ליווי של אנשים שלך ומתנדבים בסביבה.'), alertsSlot, watchSlot, nearSlot);
  const drawAlerts = () => {
    alertsSlot.replaceChildren(h('div', { class: 'card', 'data-test': 'inbox' }, secHead('h2', 'bell', 'red', 'קריאות פעילות אליך'),
      state.alerts.size ? h('ul', { class: 'list' }, [...state.alerts.values()].map((a) => h('li', null,
        h('div', { class: 'li-head' }, h('strong', null, a.my_role === 'helper' ? alertTitle(a) : `🚨 ${a.owner_display}`), pill(STATE_HE[a.my_state] || '', STATE_CLS[a.my_state] || '')),
        h('p', null, a.my_role === 'helper' ? alertSubtitle(a) : (a.mode === 'silent' ? 'מצוקה שקטה - אל תתקשר/י' : TYPE_HE[a.emergency_type] || '')),
        h('button', { class: 'red wide', onclick: () => { location.hash = '#incident/' + a.id; } }, 'פתיחה'))))
        : h('p', { class: 'muted' }, 'אין קריאות פעילות כרגע.')));
  };
  const drawWatch = () => {
    watchSlot.replaceChildren();
    if (state.escorts.watching.length) watchSlot.append(watchingCard(state.escorts.watching));
  };
  let summary = null, sumErr = null, intel = null;
  const drawNear = () => {
    nearSlot.replaceChildren(secHead('h2', 'people', '', 'מתנדבים בסביבה'));
    if (sumErr) nearSlot.append(h('p', { class: 'error' }, sumErr));
    if (summary) {
      if (!summary.location_known) nearSlot.append(h('p', { class: 'muted' }, 'המיקום שלך לא ידוע לשרת - עדכן/י מיקום כדי לראות מתנדבים בטווח.'));
      else {
        nearSlot.append(h('p', null, `מתנדבים זמינים ב-${summary.radius_km} ק"מ: `, h('strong', null, String(summary.volunteers.length))),
          summary.nearby_users_count != null ? h('p', { class: 'muted small' }, `משתמשי LifeGuard פעילים בטווח (בשעה האחרונה): ${summary.nearby_users_count}+`) : null,
          h('ul', { class: 'list' }, summary.volunteers.map((v) => h('li', { class: 'li-head' },
            h('span', null, v.display_name, ' ', v.verified ? pill('מאומת/ת', 'ok') : pill('לא מאומת/ת')),
            h('span', { class: 'muted' }, `עד ${v.distance_band_km} ק"מ${v.approximate ? ' (משוער)' : ''}`)))));
      }
    }
    const msg = h('p', { class: 'muted small' });
    const upd = h('button', { class: 'secondary wide', 'data-test': 'presence-update' }, icon('locate'), 'עדכון המיקום שלי');
    upd.addEventListener('click', async () => {
      upd.disabled = true; msg.textContent = 'מאתר מיקום...';
      geoUse('presence-once');
      const fix = await geoFix(10000);
      geoRelease('presence-once');
      if (!fix) { msg.textContent = geo.error || 'לא התקבל מיקום מהמכשיר.'; upd.disabled = false; return; }
      try {
        await api('PUT', '/me/presence', { lat: fix.lat, lon: fix.lon, accuracy_m: fix.accuracy_m, is_precise: locPayload(fix).is_precise, source: 'browser_gps' });
        await loadSum();
      } catch (e) { msg.textContent = errText(e); upd.disabled = false; }
    });
    nearSlot.append(upd, msg, h('p', { class: 'muted small' }, 'לפרטיות: מוצגים רק שמות מתנדבים וטווח מרחק מעוגל, ומספר משתמשים - בלי מיקומים.'));
    if (intel) nearSlot.append(h('p', { class: 'muted small' }, `בטיחות רחובות: ${intel.message}`));
  };
  const loadSum = async () => {
    try { summary = await api('GET', '/community/summary?radius_km=5'); sumErr = null; } catch (e) { sumErr = errText(e); }
    drawNear();
  };
  drawAlerts(); drawWatch(); drawNear();
  loadInbox().then(drawAlerts);
  refreshEscorts().then(drawWatch).catch(() => {});
  loadSum();
  api('GET', '/street-intel/segments').then((r) => { intel = r; drawNear(); }).catch(() => {});
  listen('escorts-loaded', drawWatch);
  listen('escorts', () => refreshEscorts().catch(() => {}));
  every(5000, async () => { await loadInbox(); drawAlerts(); });
}

// ------------------------------------------------------------------ SAFETY CIRCLE
function shareCode(code, label) {
  const url = `${location.origin}/app/`;
  const text = `LifeGuard: אני מזמין/ה אותך להיות איש/אשת קשר לחירום שלי. פתח/י את LifeGuard (${url}), היכנס/י ל"מעגל הביטחון" והזן/י את הקוד ${code}. הקוד חד-פעמי ובתוקף 7 ימים.`;
  const status = h('p', { class: 'muted small', 'data-test': 'share-status' });
  const btn = h('button', { class: 'wide', 'data-test': 'share-code' }, navigator.share ? 'שיתוף הקוד' : 'העתקת ההודעה');
  btn.addEventListener('click', async () => {
    status.textContent = '';
    if (navigator.share) {
      try { await navigator.share({ title: 'הזמנה ל-LifeGuard', text }); status.textContent = 'נפתח חלון השיתוף.'; return; } catch (e) { if (e && e.name === 'AbortError') return; }
    }
    try { await navigator.clipboard.writeText(text); status.textContent = 'ההודעה הועתקה - הדבק/י אותה בהודעה לאיש הקשר.'; } catch { status.textContent = 'לא ניתן להעתיק אוטומטית - סמן/י את הקוד והעתק/י ידנית.'; }
  });
  return h('div', { class: 'card good', 'data-test': 'invite-code-card' },
    h('h3', null, label || 'קוד הזמנה'),
    h('div', { class: 'code-box', 'data-test': 'invite-code' }, code),
    h('p', { class: 'error' }, 'הקוד מוצג פעם אחת בלבד. השרת שומר רק טביעה מוצפנת שלו ולא שולח אותו בעצמו - שלח/י אותו לאיש הקשר (וואטסאפ / SMS).'),
    btn, status);
}

function viewCircle(main) {
  const listSlot = h('div');
  const codeSlot = h('div');
  const inviteCard = h('div', { class: 'card' });
  const redeemCard = h('div', { class: 'card' });
  main.append(h('h1', null, 'מעגל הביטחון'), h('p', { class: 'page-sub' }, 'אנשי קשר לחירום מקבלים את הקריאות שלך רק אחרי שאישרו את ההזמנה.'), codeSlot, listSlot, inviteCard, redeemCard);
  let circle = null, loadErr = null;

  const act = (fn) => async (ev) => {
    const btn = ev.currentTarget; btn.disabled = true;
    try { await fn(); await load(); } catch (e) { btn.disabled = false; alertLine(btn, errText(e)); }
  };
  const alertLine = (btn, text) => {
    const li = btn.closest('li') || btn.parentElement;
    let p = li.querySelector('.row-err');
    if (!p) { p = h('p', { class: 'error row-err' }); li.append(p); }
    p.textContent = text;
  };

  const draw = () => {
    if (hasOpenConfirm(listSlot)) return;
    listSlot.replaceChildren();
    if (loadErr) listSlot.append(h('div', { class: 'card warn' }, h('p', null, `לא ניתן לטעון את מעגל הביטחון מהשרת (${loadErr}).`)));
    if (!circle) return;
    const c = circle;
    if (c.received_invitations.length) {
      listSlot.append(h('div', { class: 'card notice', 'data-test': 'received-invitations' }, secHead('h2', 'bell', 'orange', 'הזמנות שקיבלת'),
        h('ul', { class: 'list' }, c.received_invitations.map((i) => h('li', { 'data-test': 'received-invite', 'data-from': i.from },
          h('p', { class: 'big' }, i.text),
          h('p', { class: 'muted small' }, `מ-@${i.from}${i.via_phone ? ' · נשלחה למספר הטלפון שלך' : ''} · בתוקף עד ${clock(i.expires_at, true)}`),
          i.awaiting_confirmation ? h('p', null, pill('אישרת - ממתין לאישור השולח', 'wait'))
            : h('div', { class: 'li-actions' },
              h('button', { class: 'green', 'data-test': 'invite-accept', onclick: act(() => api('POST', `/invitations/${i.id}/respond`, { accept: true })) }, 'אישור'),
              h('button', { class: 'ghost', 'data-test': 'invite-decline', onclick: act(() => api('POST', `/invitations/${i.id}/respond`, { accept: false })) }, 'דחייה')))))));
    }
    const disconnectBtn = (u, scope, label) => {
      const holder = h('div', { class: 'li-actions' });
      const b = h('button', { class: 'ghost', 'data-test': 'disconnect' }, label);
      b.addEventListener('click', () => confirmIn(holder, scope === 'my_contact' ? `להסיר את ${u.display_name} מאנשי הקשר שלך? הוא/היא לא יקבל/ו יותר קריאות ממך.` : `להפסיק להיות איש קשר של ${u.display_name}? לא תקבל/י יותר קריאות ממנו/ה.`, 'כן, נתק', 'red',
        async () => { await api('DELETE', `/connections/${encodeURIComponent(u.username)}?scope=${scope}`); await load(); }));
      holder.append(b);
      return holder;
    };
    listSlot.append(h('div', { class: 'card', 'data-test': 'my-contacts' }, secHead('h2', 'shield-check', 'navy', `אנשי הקשר שלי לחירום (${c.my_contacts.length})`),
      c.my_contacts.length ? h('ul', { class: 'list' }, c.my_contacts.map((u) => h('li', { 'data-test': 'contact', 'data-user': u.username },
        h('div', { class: 'li-head' }, h('strong', null, u.display_name), pill(INV_HE.connected, 'ok')),
        h('p', { class: 'muted small' }, `@${u.username} · מחובר/ת מ-${clock(u.connected_at, true)}`),
        disconnectBtn(u, 'my_contact', 'הסרה'))))
        : h('p', { class: 'error' }, 'אין לך עדיין אנשי קשר מחוברים - קריאת חירום תגיע רק למתנדבים (אם יש בטווח). הזמן/י מישהו למטה.')));
    if (c.i_am_contact_for.length) {
      listSlot.append(h('div', { class: 'card', 'data-test': 'contact-for' }, secHead('h2', 'people', 'teal', 'אני איש קשר עבור'),
        h('ul', { class: 'list' }, c.i_am_contact_for.map((u) => h('li', null,
          h('div', { class: 'li-head' }, h('strong', null, u.display_name), pill(INV_HE.connected, 'ok')),
          disconnectBtn(u, 'contact_for', 'הפסקה'))))));
    }
    if (c.sent_invitations.length) {
      listSlot.append(h('div', { class: 'card', 'data-test': 'sent-invitations' }, secHead('h2', 'clock', 'grey', 'הזמנות ששלחתי'),
        h('ul', { class: 'list' }, c.sent_invitations.map((i) => {
          const who = i.target_display || i.label || (i.phone_last4 ? `טלפון ...${i.phone_last4}` : (i.channel === 'code' ? 'קוד שיתוף' : ''));
          const li = h('li', { 'data-test': 'sent-invite', 'data-status': i.status },
            h('div', { class: 'li-head' }, h('strong', null, who), pill(i.awaiting_confirmation ? 'ממתין לאישור שלך' : (INV_HE[i.status] || i.status), i.awaiting_confirmation ? 'wait' : INV_CLS[i.status] || '')),
            h('p', { class: 'muted small' }, `נשלחה ${clock(i.created_at, true)}${i.status === 'pending' ? ` · בתוקף עד ${clock(i.expires_at, true)}` : ''}`));
          if (i.awaiting_confirmation) {
            li.append(h('p', null, `${i.accepted_by_display} (@${i.accepted_by}) אישר/ה את ההזמנה שנשלחה למספר. האם זה באמת האדם שהזמנת?`),
              h('div', { class: 'li-actions' },
                h('button', { class: 'green', 'data-test': 'invite-confirm', onclick: act(() => api('POST', `/invitations/${i.id}/confirm`, { confirm: true })) }, 'כן, זה הוא/היא'),
                h('button', { class: 'ghost', onclick: act(() => api('POST', `/invitations/${i.id}/confirm`, { confirm: false })) }, 'לא מכיר/ה')));
          } else if (i.status === 'pending' || i.status === 'expired') {
            const acts = h('div', { class: 'li-actions' });
            if (i.resends_left > 0) {
              acts.append(h('button', { class: 'secondary', onclick: act(async () => { const r = await api('POST', `/invitations/${i.id}/resend`); if (r.code) codeSlot.replaceChildren(shareCode(r.code, 'קוד חדש (הקוד הקודם כבר לא עובד)')); }) }, 'שליחה מחדש'));
            }
            if (i.status === 'pending') acts.append(h('button', { class: 'ghost', onclick: act(() => api('POST', `/invitations/${i.id}/cancel`)) }, 'ביטול'));
            li.append(acts);
          }
          return li;
        }))));
    }
  };

  // invite form
  const showErr = (el, e) => { el.textContent = e.offline ? 'אין חיבור לשרת - ההזמנה לא נשלחה.' : errText(e); };
  const uname = h('input', { type: 'text', id: 'inv-user', dir: 'ltr', autocapitalize: 'none', spellcheck: 'false', placeholder: 'username', 'data-test': 'invite-username' });
  const uMsg = h('p', { class: 'error' });
  const uBtn = h('button', { class: 'wide', 'data-test': 'invite-username-send' }, 'שליחת הזמנה');
  uBtn.addEventListener('click', async () => {
    uMsg.textContent = ''; uBtn.disabled = true;
    try { await api('POST', '/invitations', { username: uname.value.trim() }); uname.value = ''; uMsg.className = 'okmsg'; uMsg.textContent = 'ההזמנה נשמרה בשרת והועברה לאפליקציה של הנמען - ממתין לאישור.'; await load(); } catch (e) { uMsg.className = 'error'; showErr(uMsg, e); } finally { uBtn.disabled = false; }
  });
  const phone = h('input', { type: 'tel', id: 'inv-phone', dir: 'ltr', placeholder: '050-0000000', autocomplete: 'tel', 'data-test': 'invite-phone' });
  const plabel = h('input', { type: 'text', id: 'inv-label', maxlength: '60', placeholder: 'למשל: אמא', 'data-test': 'invite-label' });
  const pMsg = h('p', { class: 'error' });
  const pBtn = h('button', { class: 'wide', 'data-test': 'invite-phone-send' }, 'יצירת הזמנה למספר');
  pBtn.addEventListener('click', async () => {
    pMsg.textContent = ''; pBtn.disabled = true;
    try {
      const r = await api('POST', '/invitations', { phone: phone.value.trim(), label: plabel.value.trim() || undefined });
      codeSlot.replaceChildren(shareCode(r.code, `קוד הזמנה עבור ${r.label || '...' + r.phone_last4}`));
      phone.value = ''; plabel.value = '';
      await load();
      window.scrollTo(0, 0);
    } catch (e) { showErr(pMsg, e); } finally { pBtn.disabled = false; }
  });
  let pickBtn = null;
  if ('contacts' in navigator && navigator.contacts && typeof navigator.contacts.select === 'function') {
    pickBtn = h('button', { class: 'secondary wide', 'data-test': 'contact-picker' }, icon('person'), 'בחירה מאנשי הקשר בטלפון');
    pickBtn.addEventListener('click', async () => {
      try {
        const [c] = await navigator.contacts.select(['name', 'tel'], { multiple: false });
        if (!c) return;
        if (c.tel && c.tel[0]) phone.value = c.tel[0];
        if (c.name && c.name[0] && !plabel.value) plabel.value = c.name[0].slice(0, 60);
      } catch (e) { pMsg.textContent = 'לא ניתן לפתוח את אנשי הקשר.'; }
    });
  }
  const cMsg = h('p', { class: 'error' });
  const cBtn = h('button', { class: 'secondary wide', 'data-test': 'invite-code-create' }, 'יצירת קוד הזמנה לשיתוף');
  cBtn.addEventListener('click', async () => {
    cMsg.textContent = ''; cBtn.disabled = true;
    try { const r = await api('POST', '/invitations', { code: true }); codeSlot.replaceChildren(shareCode(r.code, 'קוד הזמנה לשיתוף')); await load(); window.scrollTo(0, 0); } catch (e) { showErr(cMsg, e); } finally { cBtn.disabled = false; }
  });
  inviteCard.append(secHead('h2', 'plus', '', 'הזמנת איש קשר'),
    h('label', { for: 'inv-user' }, 'לפי שם משתמש ב-LifeGuard'), uname, h('div', { class: 'spacer' }), uBtn, uMsg,
    h('hr'),
    h('label', { for: 'inv-phone' }, 'לפי מספר טלפון'), pickBtn, phone,
    h('label', { for: 'inv-label' }, 'כינוי (רק את/ה רואה)'), plabel,
    h('p', { class: 'help' }, 'המספר לא נשמר בשרת (רק טביעה מוצפנת). תקבל/י קוד חד-פעמי לשלוח לאיש הקשר.'),
    pBtn, pMsg, h('hr'),
    h('p', { class: 'muted' }, 'או קוד שאפשר לשלוח לכל אחד (גם למי שעוד לא התקין):'), cBtn, cMsg);

  // redeem
  const code = h('input', { type: 'text', id: 'redeem-code', dir: 'ltr', autocapitalize: 'characters', spellcheck: 'false', placeholder: 'XXXX-XXXX-XXXX', 'data-test': 'redeem-code' });
  const rOut = h('div');
  const rBtn = h('button', { class: 'secondary wide', 'data-test': 'redeem-preview' }, 'בדיקת הקוד');
  rBtn.addEventListener('click', async () => {
    rOut.replaceChildren(); rBtn.disabled = true;
    try {
      const pv = await api('POST', '/invitations/redeem', { code: code.value.trim(), preview: true });
      const decide = (accept) => async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          const r = await api('POST', '/invitations/redeem', { code: code.value.trim(), accept });
          rOut.replaceChildren(h('p', { class: 'okmsg' }, accept ? `מחובר/ת! את/ה עכשיו איש קשר לחירום של ${pv.from_display}.` : 'ההזמנה נדחתה.'));
          code.value = '';
          if (r) await load();
        } catch (e) { rOut.append(h('p', { class: 'error' }, errText(e))); }
      };
      rOut.replaceChildren(h('div', { class: 'card notice' },
        h('p', { class: 'big' }, `${pv.from_display} (@${pv.from}) מבקש/ת להוסיף אותך כאיש קשר לחירום`),
        pv.already_connected ? h('p', { class: 'muted' }, 'כבר מחוברים.') : null,
        h('p', { class: 'muted small' }, `בתוקף עד ${clock(pv.expires_at, true)}`),
        h('div', { class: 'li-actions' }, h('button', { class: 'green', 'data-test': 'redeem-accept', onclick: decide(true) }, 'אישור'), h('button', { class: 'ghost', onclick: decide(false) }, 'דחייה'))));
    } catch (e) { rOut.replaceChildren(h('p', { class: 'error' }, e.offline ? 'אין חיבור לשרת.' : errText(e))); } finally { rBtn.disabled = false; }
  });
  redeemCard.append(secHead('h2', 'lock', 'indigo', 'קיבלת קוד הזמנה?'), h('label', { for: 'redeem-code' }, 'קוד'), code, h('div', { class: 'spacer' }), rBtn, rOut);

  const load = async () => {
    try { circle = await api('GET', '/circle'); loadErr = null; } catch (e) { loadErr = e.offline ? 'אין חיבור' : errText(e); }
    draw();
  };
  load();
  listen('circle', load);
  every(15000, load);
}

// ------------------------------------------------------------------ PROFILE
function b64uToBytes(s) {
  const pad = '='.repeat((4 - (s.length % 4)) % 4);
  const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** {ok} or {ok:false, reason}: 'ios_browser' (iOS tab - push needs the home-screen app), 'ios_old' (< iOS 16.4),
 *  'unsupported' (any other browser without the Push API). */
function pushAvailability() {
  if (IS_IOS && !isStandalone()) return { ok: false, reason: 'ios_browser' };
  if (!('serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window)) {
    return { ok: false, reason: IS_IOS ? 'ios_old' : 'unsupported' };
  }
  return { ok: true };
}
const pushSupported = () => pushAvailability().ok;
const PUSH_UNAVAILABLE_HE = {
  ios_browser: 'באייפון ובאייפד התראות דחיפה עובדות רק כש-LifeGuard נפתח מהאייקון במסך הבית (iOS 16.4 ומעלה) - לא מלשונית בספארי. הוסיפו את LifeGuard למסך הבית (ההוראות בדף הבית), פתחו משם, התחברו והפעילו כאן.',
  ios_old: 'גרסת ה-iOS במכשיר הזה לא תומכת בהתראות דחיפה לאפליקציות רשת. נדרש iOS 16.4 ומעלה (הגדרות > כללי > עדכון תוכנה). עד אז קריאות יופיעו רק כשהאפליקציה פתוחה על המסך.',
  unsupported: 'הדפדפן הזה לא תומך בהתראות דחיפה. קריאות יופיעו רק כשהאפליקציה פתוחה.',
};
const PUSH_DENIED_HE = IS_IOS
  ? 'ההרשאה להתראות נחסמה. באייפון: הגדרות > עדכונים (התראות) > LifeGuard > "אפשר עדכונים", ואז חזרו לכאן.'
  : 'ההרשאה להתראות חסומה בדפדפן - אפשר לשנות בהגדרות האתר.';

// the VAPID key is fetched before the tap, so that after the permission prompt the subscribe call follows
// at once (Safari ties push permission / subscription to the user gesture)
let pushKeyPromise = null;
function pushKey() {
  if (!pushKeyPromise) pushKeyPromise = api('GET', '/push/public-key').catch((e) => { pushKeyPromise = null; throw e; });
  return pushKeyPromise;
}

async function currentSubscription() {
  if (!pushSupported()) return null;
  const reg = await navigator.serviceWorker.getRegistration('/app/');
  return reg ? reg.pushManager.getSubscription() : null;
}

async function enablePush() {
  const av = pushAvailability();
  if (!av.ok) throw new ApiError(-1, 'unsupported', PUSH_UNAVAILABLE_HE[av.reason]);
  const keyP = pushKey();
  keyP.catch(() => {});
  // must be the first await after the tap: iOS shows the permission prompt only inside a user gesture
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') throw new ApiError(-1, 'denied', perm === 'denied' ? PUSH_DENIED_HE : 'לא ניתנה הרשאה להתראות.');
  const [reg, { public_key: key }] = await Promise.all([navigator.serviceWorker.ready, keyP]);
  const appKey = b64uToBytes(key);
  let sub = await reg.pushManager.getSubscription();
  if (sub) {
    const cur = sub.options && sub.options.applicationServerKey ? new Uint8Array(sub.options.applicationServerKey) : null;
    if (cur && (cur.length !== appKey.length || cur.some((b, i) => b !== appKey[i]))) { await sub.unsubscribe(); sub = null; }
  }
  if (!sub) {
    try { sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey }); } catch (e) {
      throw new ApiError(-1, 'subscribe_failed', `הדפדפן לא הצליח להירשם לשירות הדחיפה (${e && e.name ? e.name : 'שגיאה'}: ${e && e.message ? e.message : ''})`);
    }
  }
  await api('POST', '/push/subscribe', sub.toJSON());
  return sub;
}

async function disablePush() {
  const sub = await currentSubscription();
  if (sub) {
    try { await api('POST', '/push/unsubscribe', { endpoint: sub.endpoint }); } finally { await sub.unsubscribe(); }
  }
}

async function resyncPush() {
  try {
    if (!pushSupported() || Notification.permission !== 'granted') return;
    const sub = await currentSubscription();
    if (sub) await api('POST', '/push/subscribe', sub.toJSON());
  } catch { /* shown on the profile screen */ }
}

function viewProfile(main) {
  const acct = h('div', { class: 'card' });
  const phoneCard = h('div', { class: 'card' });
  const medCard = h('div', { class: 'card', 'data-test': 'medical-card' });
  const volCard = h('div', { class: 'card' });
  const rolesCard = h('div', { class: 'card', 'data-test': 'vol-roles-card' });
  const pushCard = h('div', { class: 'card', 'data-test': 'push-card' });
  const out = h('div', { class: 'card' });
  main.append(...[h('h1', null, 'פרופיל'), h('p', { class: 'page-sub' }, 'החשבון, מידע לחירום, התנדבות והתראות.'), acct, phoneCard, medCard, volCard, rolesCard, pushCard,
    hazard.settingsCard(), IS_IOS ? iosCapabilitiesCard(true) : null, out].filter(Boolean));

  // ---- איך תרצה/י לעזור? (features_ui.py VolunteerTrustSection: roles + languages). הסטטוס - מהשרת בלבד.
  const roleBoxes = {}, roleStatus = {}, langBoxes = {};
  const rolesMsg = h('p', { 'data-test': 'vol-roles-msg' });
  const rolesSave = h('button', { class: 'wide', 'data-test': 'vol-roles-save' }, 'שמירת דרכי העזרה');
  const availNote = h('p', { class: 'help', 'data-test': 'vol-roles-avail' });
  rolesCard.append(secHead('h2', 'hand', 'indigo', 'איך תרצה/י לעזור?'), h('p', { class: 'muted' }, 'לא חייבים להיות מד"א. אפשר לבחור יותר מדרך אחת.'));
  for (const [role, label, sub] of VOL_ROLE_ROWS) {
    const cb = h('input', { type: 'checkbox', id: 'role-' + role, 'data-test': 'role-' + role, checked: role === 'on_scene' });
    roleBoxes[role] = cb;
    roleStatus[role] = h('span', { class: 'small', 'data-test': 'role-status-' + role });
    rolesCard.append(h('div', { class: 'li-head' }, h('label', { class: 'inline', for: 'role-' + role }, cb, label), roleStatus[role]),
      h('p', { class: 'help' }, sub));
  }
  rolesCard.append(h('p', { class: 'muted' }, 'שפות לשיחה'));
  for (const [code, label] of VOL_LANGS) {
    const cb = h('input', { type: 'checkbox', id: 'lang-' + code, 'data-test': 'lang-' + code, checked: code === 'he' });
    langBoxes[code] = cb;
    rolesCard.append(h('label', { class: 'inline', for: 'lang-' + code }, cb, label));
  }
  rolesCard.append(availNote, rolesSave, rolesMsg);
  const applyRoles = (r) => {
    const roles = (r && r.roles) || [];
    const eff = (r && r.effective) || {};
    if (roles.length) for (const [role, cb] of Object.entries(roleBoxes)) cb.checked = roles.includes(role);
    const langs = (r && r.languages) || ['he'];
    for (const [code, cb] of Object.entries(langBoxes)) cb.checked = langs.includes(code);
    for (const [role, el] of Object.entries(roleStatus)) {
      const st = eff[role];
      el.className = 'small ' + (st === 'active' ? 'okmsg' : 'muted');
      el.textContent = st == null ? '' : (st === 'active' ? '✓ פעיל' : `⏳ ${st}`);
    }
  };
  const drawAvail = () => {
    const v = state.me && state.me.volunteer;
    availNote.className = v && v.available ? 'help' : 'error';
    availNote.textContent = v && v.available
      ? 'בקשות לשיחה ולהזעקה מגיעות מכל מקום - בלי מיקום. מיקום נשלח לשרת רק אם בחרת "הגעה למקום".'
      : 'כדי לקבל בקשות יש לסמן למעלה "זמין/ה לקבל קריאות" ולשמור.';
  };
  rolesSave.addEventListener('click', async () => {
    rolesMsg.className = ''; rolesMsg.textContent = '';
    const roles = Object.entries(roleBoxes).filter(([, cb]) => cb.checked).map(([k]) => k);
    if (!roles.length) { rolesMsg.className = 'error'; rolesMsg.textContent = 'יש לבחור לפחות דרך אחת לעזור'; return; }
    const langs = Object.entries(langBoxes).filter(([, cb]) => cb.checked).map(([k]) => k);
    rolesSave.disabled = true;
    try {
      const r = await setVolunteerRoles(roles, langs.length ? langs : ['he']);
      state.volRoles = r;
      applyRoles(r);
      rolesMsg.className = 'okmsg'; rolesMsg.textContent = '✅ נשמר בשרת';
      await loadMe();
    } catch (e) {
      rolesMsg.className = 'error'; rolesMsg.textContent = e.offline ? 'אין חיבור לשרת - ההגדרה לא נשמרה!' : '❌ ' + errText(e);
    } finally { rolesSave.disabled = false; }
  });
  drawAvail();
  volunteerRoles().then((r) => { state.volRoles = r; applyRoles(r); })
    .catch((e) => { rolesMsg.className = 'error'; rolesMsg.textContent = `לא ניתן לטעון (${errText(e)})`; });

  const drawMe = () => {
    const me = state.me;
    acct.replaceChildren(secHead('h2', 'person', 'navy', 'החשבון שלי'),
      me ? h('dl', { class: 'kv' }, h('dt', null, 'שם לתצוגה'), h('dd', { 'data-test': 'display-name' }, me.display_name),
        h('dt', null, 'שם משתמש'), h('dd', { class: 'ltr' }, '@' + me.username)) : h('p', { class: 'muted' }, 'טוען...'));
    // phone
    const ph = h('input', { type: 'tel', id: 'my-phone', dir: 'ltr', autocomplete: 'tel', placeholder: me && me.phone_last4 ? `...${me.phone_last4}` : '050-0000000', 'data-test': 'my-phone' });
    const pmsg = h('p');
    const save = h('button', { class: 'wide', 'data-test': 'my-phone-save' }, 'שמירת מספר');
    save.addEventListener('click', async () => {
      save.disabled = true; pmsg.className = ''; pmsg.textContent = '';
      try { const r = await api('PUT', '/me/phone', { phone: ph.value.trim() }); pmsg.className = 'okmsg'; pmsg.textContent = r.phone_last4 ? `נשמר: ...${r.phone_last4}` : 'המספר הוסר'; ph.value = ''; await loadMe(); } catch (e) { pmsg.className = 'error'; pmsg.textContent = errText(e); } finally { save.disabled = false; }
    });
    phoneCard.replaceChildren(secHead('h2', 'phone', 'green', 'מספר הטלפון שלי'),
      h('p', null, me && me.phone_last4 ? `מספר שמור: ...${me.phone_last4} ` : 'לא הוגדר מספר. ', me && me.phone_last4 ? pill(me.phone_verified ? 'מאומת ב-SMS' : 'לא מאומת', me.phone_verified ? 'ok' : 'wait') : null),
      h('p', { class: 'help' }, 'משמש להתאמת הזמנות שנשלחו למספר שלך. בשרת נשמרת טביעה + המספר מוצפן (לגיבוי SMS, רק אם הוגדר ספק).'),
      h('label', { for: 'my-phone' }, 'מספר חדש'), ph, h('div', { class: 'spacer' }), save, pmsg);
    // volunteer
    const v = (me && me.volunteer) || { available: false, radius_km: 5, skills: [], verification: 'unverified' };
    const avail = h('input', { type: 'checkbox', class: 'switch', id: 'vol-av', checked: !!v.available, 'data-test': 'vol-available' });
    const radius = h('input', { type: 'number', id: 'vol-r', min: '0.2', max: '50', step: '0.5', value: String(v.radius_km || 5) });
    const skillBoxes = Object.entries(SKILLS).map(([k, label]) => {
      const cb = h('input', { type: 'checkbox', id: 'sk-' + k, checked: (v.skills || []).includes(k) });
      cb.dataset.skill = k;
      return h('label', { class: 'inline', for: 'sk-' + k }, cb, label);
    });
    const vmsg = h('p');
    const vsave = h('button', { class: 'wide', 'data-test': 'vol-save' }, 'שמירה');
    vsave.addEventListener('click', async () => {
      vsave.disabled = true; vmsg.textContent = '';
      try {
        const skills = skillBoxes.map((l) => l.querySelector('input')).filter((c) => c.checked).map((c) => c.dataset.skill);
        await api('PUT', '/me/volunteer', { available: avail.checked, radius_km: Number(radius.value) || 5, skills });
        await loadMe();
      } catch (e) { vmsg.className = 'error'; vmsg.textContent = errText(e); vsave.disabled = false; }
    });
    const org = h('input', { type: 'text', id: 'vol-org', maxlength: '80', placeholder: 'למשל: מד"א / איחוד הצלה' });
    const ref = h('input', { type: 'text', id: 'vol-ref', maxlength: '60', dir: 'ltr' });
    const rmsg = h('p');
    const rbtn = h('button', { class: 'secondary wide', 'data-test': 'vol-verify' }, 'שליחת בקשת אימות');
    rbtn.addEventListener('click', async () => {
      rbtn.disabled = true; rmsg.textContent = '';
      try { await api('POST', '/me/volunteer/verification', { organization: org.value.trim(), credential_ref: ref.value.trim() }); await loadMe(); } catch (e) { rmsg.className = 'error'; rmsg.textContent = errText(e); rbtn.disabled = false; }
    });
    const verif = v.verification || 'unverified';
    const idCard = identityBlock((me && me.volunteer && me.volunteer.identity) || { status: 'none', required: true });
    const presAt = Number(store.get(userKey('presenceAt')));
    // replaceChildren לא משטח מערכים ולא מדלג על null - הוצג "null" ו-"[object HTMLLabelElement]" במקום הכישורים
    const kids = (...xs) => xs.flat().filter((x) => x !== null && x !== undefined && x !== false);
    volCard.replaceChildren(...kids(secHead('h2', 'shield', 'teal', 'מתנדב/ת'),
      idCard,
      h('p', null, 'אימות ארגון / הכשרה: ', pill(VERIF_HE[verif] || verif, VERIF_CLS[verif] || ''), v.suspended ? pill('מושעה - בבדיקה', 'bad') : null),
      v.verification_note ? h('p', { class: 'muted small' }, v.verification_note) : null,
      h('label', { class: 'inline', for: 'vol-av' }, avail, 'זמין/ה לקבל קריאות ממשתמשים קרובים'),
      h('label', { for: 'vol-r' }, 'רדיוס (ק"מ)'), radius,
      h('p', { class: 'muted' }, 'כישורים'), skillBoxes,
      h('p', { class: 'help' }, 'קריאות מגיעות רק כשהמיקום שלך עודכן לשרת ב-12 השעות האחרונות. המיקום מתעדכן כשהאפליקציה פתוחה.'),
      v.available ? h('p', { class: presAt ? 'muted small' : 'error' }, presAt ? `המיקום שלך עודכן לשרת ${ago(presAt)}` : 'המיקום שלך עוד לא עודכן לשרת מהמכשיר הזה.') : null,
      vsave, vmsg,
      verif === 'verified' || verif === 'pending' ? null : h('div', null, h('hr'), h('h3', null, 'בקשת אימות'),
        h('p', { class: 'help' }, 'מנהל/ת המערכת בודק/ת ידנית מול הארגון (מד"א, איחוד הצלה...). זה בנוסף לאימות הזהות - מי שמאומת/ת גם בארגון מקבל/ת עדיפות.'),
        h('label', { for: 'vol-org' }, 'ארגון'), org, h('label', { for: 'vol-ref' }, 'מספר תעודה / חבר'), ref, h('div', { class: 'spacer' }), rbtn, rmsg)));
  };

  // אימות זהות חובה לכל מתנדב/ת (server/volunteer_identity.py): צילום ת"ז + סלפי + מספר ת"ז
  const ID_STATUS_CLS = { none: 'bad', pending: 'wait', verified: 'ok', rejected: 'bad' };
  // מקטין ל-1600px ומקודד מחדש ל-JPEG: נכנס במגבלת הגודל, ו-EXIF (כולל מיקום הצילום) לא נשלח לשרת
  const toJpegB64 = (file) => new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
      const c = document.createElement('canvas');
      c.width = Math.round(img.naturalWidth * k); c.height = Math.round(img.naturalHeight * k);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.88).split(',')[1]);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('לא ניתן לקרוא את התמונה')); };
    img.src = url;
  });
  const idValid = (s) => {
    if (!/^\d{1,9}$/.test(s)) return false;
    const d = s.padStart(9, '0');
    let t = 0;
    for (let i = 0; i < 9; i++) { let x = Number(d[i]) * (i % 2 ? 2 : 1); t += x > 9 ? x - 9 : x; }
    return t % 10 === 0 && d !== '000000000';
  };
  function identityBlock(st) {
    const status = st.status || 'none';
    const head = h('p', null, 'אימות זהות: ', pill(st.status_he || status, ID_STATUS_CLS[status] || ''),
      status === 'verified' ? null : pill('לא יישלחו אליך קריאות עד האימות', 'bad'));
    if (status === 'verified') return h('div', { 'data-test': 'id-verified' }, head);
    if (status === 'pending') {
      const wbtn = h('button', { class: 'secondary wide', 'data-test': 'id-withdraw' }, 'ביטול הבקשה ומחיקת התמונות');
      wbtn.addEventListener('click', async () => { wbtn.disabled = true; try { await api('DELETE', '/me/volunteer/identity'); await loadMe(); } catch (e) { wbtn.disabled = false; } });
      return h('div', { 'data-test': 'id-pending' }, head, h('p', { class: 'help' }, 'הבקשה ממתינה לבדיקה ידנית. אחרי ההחלטה התמונות נמחקות מהשרת.'), wbtn);
    }
    const num = h('input', { type: 'text', inputmode: 'numeric', id: 'id-num', maxlength: '9', dir: 'ltr', autocomplete: 'off', 'data-test': 'id-number' });
    const doc = h('input', { type: 'file', id: 'id-doc', accept: 'image/jpeg,image/png', capture: 'environment', 'data-test': 'id-doc' });
    const selfie = h('input', { type: 'file', id: 'id-selfie', accept: 'image/jpeg,image/png', capture: 'user', 'data-test': 'id-selfie' });
    const consent = h('input', { type: 'checkbox', id: 'id-consent', 'data-test': 'id-consent' });
    const msg = h('p');
    const send = h('button', { class: 'wide', 'data-test': 'id-submit' }, 'שליחה לאימות');
    send.addEventListener('click', async () => {
      msg.className = 'error'; msg.textContent = '';
      const n = num.value.trim();
      if (!idValid(n)) { msg.textContent = 'מספר תעודת הזהות לא תקין'; return; }
      if (!doc.files[0] || !selfie.files[0]) { msg.textContent = 'צריך צילום של תעודת הזהות וגם סלפי'; return; }
      if (!consent.checked) { msg.textContent = 'צריך לאשר את השימוש בתמונות לאימות'; return; }
      send.disabled = true; msg.className = 'muted'; msg.textContent = 'שולח...';
      try {
        await api('POST', '/me/volunteer/identity', { id_number: n, id_image: await toJpegB64(doc.files[0]), selfie: await toJpegB64(selfie.files[0]), consent: true });
        await loadMe();
      } catch (e) { msg.className = 'error'; msg.textContent = errText(e); send.disabled = false; }
    });
    return h('div', { 'data-test': 'id-form' }, head,
      st.note ? h('p', { class: 'error small' }, 'סיבת הדחייה: ' + st.note) : null,
      h('p', { class: 'help' }, 'כל מתנדב/ת - גם מי שכבר רשום/ה - צריך/ה לאמת זהות כדי לקבל קריאות: מספר ת"ז, צילום של התעודה וסלפי. ' +
        'מנהל/ת בודק/ת ידנית. התמונות נשמרות בשרת מוצפנות ונמחקות מיד אחרי ההחלטה (או אחרי 30 יום). מספר הת"ז עצמו לא נשמר - רק 4 ספרות אחרונות.'),
      h('label', { for: 'id-num' }, 'מספר תעודת זהות (9 ספרות)'), num,
      h('label', { for: 'id-doc' }, 'צילום תעודת הזהות (הצד עם התמונה)'), doc,
      h('label', { for: 'id-selfie' }, 'סלפי (פנים גלויות, בלי משקפי שמש)'), selfie,
      h('label', { class: 'inline', for: 'id-consent' }, consent, 'אני מאשר/ת שימוש בתמונות ובמספר הת"ז לאימות זהותי כמתנדב/ת בלבד'),
      h('div', { class: 'spacer' }), send, msg, h('hr'));
  }

  // medical
  const medInputs = {};
  const medMsg = h('p');
  const share = h('input', { type: 'checkbox', class: 'switch', id: 'med-share', 'data-test': 'med-share' });
  const medUpdated = h('p', { class: 'muted small' });
  const medSave = h('button', { class: 'wide', 'data-test': 'med-save' }, 'שמירת המידע הרפואי');
  medCard.append(secHead('h2', 'medical', 'red', 'מידע רפואי לחירום'),
    h('p', { class: 'help' }, 'המידע נשמר בשרת מוצפן. הוא מוצג רק למי שיש לו גישה מלאה לקריאת חירום פעילה שלך (אנשי הקשר שלך, ומתנדב/ת רק אחרי שקיבל/ה את הקריאה) - ורק אם סימנת "לשתף". עם סיום האירוע הגישה נסגרת.'));
  for (const [k, label] of Object.entries(MED_LABELS)) {
    const el = k === 'notes' || k === 'conditions' || k === 'medications' || k === 'allergies'
      ? h('textarea', { id: 'med-' + k, maxlength: '500', 'data-test': 'med-' + k }) : h('input', { type: 'text', id: 'med-' + k, maxlength: '500', 'data-test': 'med-' + k });
    medInputs[k] = el;
    medCard.append(h('label', { for: 'med-' + k }, label), el);
  }
  medCard.append(h('label', { class: 'inline', for: 'med-share' }, share, 'לשתף בזמן קריאת חירום'), medUpdated, medSave, medMsg);
  const fillMed = (m) => {
    for (const k of Object.keys(MED_LABELS)) medInputs[k].value = m[k] || '';
    share.checked = !!m.share;
    medUpdated.textContent = m.updated_at ? `נשמר בשרת: ${clock(m.updated_at, true)}` : 'עוד לא נשמר מידע.';
  };
  medSave.addEventListener('click', async () => {
    medSave.disabled = true; medMsg.textContent = '';
    try {
      const body = { share: share.checked };
      for (const k of Object.keys(MED_LABELS)) body[k] = medInputs[k].value;
      const m = await api('PUT', '/me/medical', body);
      fillMed(m);
      medMsg.className = 'okmsg'; medMsg.textContent = 'נשמר בשרת.';
    } catch (e) { medMsg.className = 'error'; medMsg.textContent = e.offline ? 'אין חיבור - המידע לא נשמר!' : errText(e); } finally { medSave.disabled = false; }
  });
  api('GET', '/me/medical').then(fillMed).catch((e) => { medMsg.className = 'error'; medMsg.textContent = `לא ניתן לטעון (${errText(e)})`; });

  // push
  const drawPush = async () => {
    const msg = h('p', { 'data-test': 'push-msg' });
    const lines = [];
    let sub = null, srv = null;
    try { sub = await currentSubscription(); } catch { sub = null; }
    try { srv = await api('GET', '/push/status'); } catch { srv = null; }
    const perm = 'Notification' in window ? Notification.permission : 'unsupported';
    const av = pushAvailability();
    if (!av.ok) lines.push(h('p', { class: 'error', 'data-test': 'push-unsupported', 'data-reason': av.reason }, PUSH_UNAVAILABLE_HE[av.reason]));
    else if (perm === 'denied') lines.push(h('p', { class: 'error', 'data-test': 'push-denied' }, PUSH_DENIED_HE));
    else pushKey().catch(() => { /* fetched again on tap */ });
    lines.push(h('p', { 'data-test': 'push-state' }, sub ? pill('פעיל במכשיר הזה', 'ok') : pill('לא פעיל במכשיר הזה', 'wait')));
    if (srv) {
      lines.push(h('p', { class: 'muted small' }, `מכשירים רשומים בשרת: ${srv.devices}`));
      for (const s of srv.subscriptions || []) {
        if (s.last_ok) lines.push(h('p', { class: 'muted small', 'data-test': 'push-last-ok' }, `שירות הדחיפה קיבל התראה ${ago(s.last_ok)}`));
        if (s.last_error) lines.push(h('p', { class: 'error small' }, `שגיאה אחרונה בשליחה: ${s.last_error}`));
      }
    }
    const lastPush = Number(store.get('lg.lastPush'));
    if (lastPush) lines.push(h('p', { class: 'okmsg small', 'data-test': 'push-last-received' }, `התראת דחיפה אחרונה הגיעה למכשיר הזה ${ago(lastPush)}`));
    const on = h('button', { class: 'wide', 'data-test': 'push-enable', disabled: !av.ok || perm === 'denied' }, sub ? 'רישום מחדש' : 'הפעלת התראות דחיפה');
    on.addEventListener('click', async () => {
      on.disabled = true; msg.className = ''; msg.textContent = 'נרשם...';
      try { await enablePush(); msg.className = 'okmsg'; msg.textContent = 'נרשם בשרת.'; setTimeout(drawPush, 300); } catch (e) { msg.className = 'error'; msg.textContent = errText(e); on.disabled = false; }
    });
    const off = sub ? h('button', { class: 'ghost wide', 'data-test': 'push-disable' }, 'כיבוי התראות במכשיר הזה') : null;
    if (off) off.addEventListener('click', async () => { off.disabled = true; try { await disablePush(); } catch (e) { msg.className = 'error'; msg.textContent = errText(e); } drawPush(); });
    pushCard.replaceChildren(secHead('h2', 'bell', 'orange', 'התראות דחיפה'),
      h('p', { class: 'help' }, 'כדי לקבל קריאות גם כשהאפליקציה סגורה. ההתראה במסך הנעילה לא כוללת מיקום או מידע רפואי. "נמסר" מסומן לשולח רק כשהאפליקציה עצמה מאשרת קבלה.'),
      ...lines, on, ...(off ? [off] : []), msg);   // replaceChildren() would print a null as "null"
  };
  drawPush();
  listen('push', drawPush);

  const lo = h('button', { class: 'ghost wide', 'data-test': 'logout' }, 'התנתקות');
  lo.addEventListener('click', async () => {
    lo.disabled = true;
    try { await disablePush(); } catch { /* best effort */ }
    try { await api('POST', '/auth/logout'); } catch { /* token dropped locally anyway */ }
    endSession();
  });
  out.append(h('p', { class: 'muted small' }, 'בהתנתקות המכשיר הזה מפסיק לקבל קריאות.'), lo);

  drawMe();
  listen('me', drawMe);
  listen('me', drawAvail);
  loadMe();
}

// ------------------------------------------------------------------ AI hazard alerts (hazard.js)
// Opt-in foreground listening + the alert sheet. A detection only OFFERS actions: SOS goes through triggerSOS
// (the same flow as the SOS button) and only after the user confirms. No popup during my SOS / silent distress.
const hazard = createHazard({
  h, icon, secHead, pill, store, userKey, serverNow, api, errText, telRow, addNotice,
  emergency: EMERGENCY_NUMBERS,
  username: () => (state.user ? state.user.username : null),
  triggerSOS: (mode, type) => triggerSOS(mode, type),
  canShow: () => {
    const mine = myIncident();
    if (mine && mine.mode === 'silent') return [false, 'silent_tracking'];
    if (mine || sosOutbox()) return [false, 'event_active'];
    return [true, null];
  },
  geoLast: () => geo.last,
  connected: () => state.connOk && navigator.onLine !== false && !!getToken(),
  go: (hash) => { location.hash = hash; },
  onCleanup: (fn) => onCleanup(fn),
  beep: () => {
    const ctx = audio.get();
    if (!ctx) return;
    if (ctx.state !== 'running') ctx.resume().catch(() => {});
    [[660, 0], [880, 0.22]].forEach(([f, t]) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = f; g.gain.value = 0.18;
      o.connect(g); g.connect(ctx.destination);
      o.start(ctx.currentTime + t); o.stop(ctx.currentTime + t + 0.18);
    });
  },
});

// ------------------------------------------------------------------ boot
function boot() {
  for (const b of document.querySelectorAll('#tabs button')) {
    b.addEventListener('click', () => { location.hash = '#' + b.dataset.tab; });
  }
  window.addEventListener('hashchange', render);
  $('#sos-mini').addEventListener('click', () => {
    const mine = myIncident();
    location.hash = mine ? '#incident/' + mine.id : '#home/sos';   // the same tap + confirm as on home - no extra step
  });
  bus.addEventListener('sos', syncSosMini);
  window.addEventListener('online', () => { flushSOS(); flushAcks(); renderConn(); });
  window.addEventListener('offline', renderConn);
  installAudioUnlock();
  onUnauthorized(() => { if (getToken()) endSession(); });
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => { /* app still works online */ });
    navigator.serviceWorker.addEventListener('message', (ev) => {
      const d = ev.data || {};
      if (d.type === 'push') { store.set('lg.lastPush', String(serverNow())); emit('push'); }
      if (d.type === 'navigate' && d.url) {
        const i = d.url.indexOf('#');
        if (i >= 0) location.hash = d.url.slice(i);
      }
    });
  }
  if (getToken()) startSession(); else render();
}

boot();
